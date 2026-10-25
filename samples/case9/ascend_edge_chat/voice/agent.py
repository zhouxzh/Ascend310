"""Loopback-only board voice agent.

The public text UI stays independent. This process owns one of three explicit
modes: ``disabled``, ``browser`` (bounded WAV conversion only), or ``board``
(agent-owned ALSA/VAD capture plus local playback). Importing this module opens
no device, loads no ASR/TTS model, and performs no network request.

Stable UI contract:

* ``GET /health`` -> ``status``, ``mode``, ``state``, ``enabled``.
* ``GET /api/config`` -> allowed modes, enumerated input/output IDs, selection,
  auto_listen and non-secret PCM/VAD contract.
* ``PUT``/``POST /api/config`` -> only ``mode``, ``input_id``, ``output_id``,
  ``auto_listen``. Board audio starts only when mode is ``board`` and
  ``auto_listen`` is true.
* ``GET /api/status`` -> sanitized active/latest turn.
* Browser-only conversion: ``POST /v1/voice/browser/transcribe`` (WAV body),
  ``POST /v1/voice/browser/synthesize`` (JSON text -> finite WAV response).

All endpoints bind loopback. Board PCM is never returned over HTTP; the
teaching example intentionally has no separate voice operator token.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import logging
import threading
import time
import uuid
import wave
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from .adapters import (
    LoopbackGatewayChat,
    SpeechAdapterError,
    adapter_readiness,
    build_speech_adapters,
)
from .alsa import AlsaAudioIO, AudioDeviceError, discover_voice_devices
from .config import VoiceConfigurationError, VoiceSettings
from .protocol import (
    LoopbackChat, SpeechRecognizer, SpeechSynthesizer, SynthesizedAudio,
    VoiceAgentState, VoiceDevice, VoiceMode, VoicePlayback, VoiceTurn, VoiceTurnState,
)

LOGGER = logging.getLogger("ascend_edge_chat.voice_agent")
_LOOPBACK_CLIENTS = {"127.0.0.1", "::1", "localhost", "testclient"}
_MAX_RETAINED_TURNS = 32
_AUTO_LISTEN_MAX_CONSECUTIVE_FAILURES = 3
_AUTO_LISTEN_RETRY_BASE_SECONDS = 1.0
_AUTO_LISTEN_RETRY_MAX_SECONDS = 8.0
_AUTO_LISTEN_IDLE_SECONDS = 0.15


class VoiceAgentError(RuntimeError):
    """A sanitized voice-agent state transition or dependency error."""


class VoiceConfigRequest(BaseModel):
    """Safe mutable state; no path, command, gateway or model fields exist."""

    model_config = ConfigDict(extra="forbid")
    mode: Optional[VoiceMode] = None
    input_id: Optional[str] = Field(default=None, min_length=1, max_length=96)
    output_id: Optional[str] = Field(default=None, min_length=1, max_length=96)
    auto_listen: Optional[bool] = None


class BrowserSynthesizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4_000)


@dataclass
class _Adapters:
    recognizer: SpeechRecognizer
    synthesizer: SpeechSynthesizer
    chat: LoopbackChat


class VoiceAgent:
    """Serialized half-duplex agent with mode-gated audio ownership."""

    def __init__(
        self,
        settings: VoiceSettings,
        *,
        recognizer: SpeechRecognizer,
        synthesizer: SpeechSynthesizer,
        chat: LoopbackChat,
        devices: Optional[list[VoiceDevice]] = None,
    ):
        self.settings = settings
        self._adapters = _Adapters(recognizer, synthesizer, chat)
        self._devices = list(devices) if devices is not None else []
        self._mode = VoiceMode(settings.initial_mode)
        self._input_id: Optional[str] = None
        self._output_id: Optional[str] = None
        self._auto_listen = False
        self._auto_listen_failures = 0
        self._turns: OrderedDict[str, VoiceTurn] = OrderedDict()
        self._active_turn_id: Optional[str] = None
        self._active_task: Optional[asyncio.Task[None]] = None
        self._supervisor_task: Optional[asyncio.Task[None]] = None
        self._state_lock = threading.Lock()

    def speech_readiness(self) -> dict[str, object]:
        """Report configured speech backend readiness without loading model weights."""

        asr_ready, asr_reason = adapter_readiness(self._adapters.recognizer)
        tts_ready, tts_reason = adapter_readiness(self._adapters.synthesizer)
        return {
            "backend": self.settings.speech_backend,
            "asr_ready": asr_ready,
            "tts_ready": tts_ready,
            "asr_reason": asr_reason,
            "tts_reason": tts_reason,
        }

    @property
    def ready(self) -> bool:
        speech = self.speech_readiness()
        return bool(speech["asr_ready"] and speech["tts_ready"] and self.settings.gateway_api_key)

    def _now_ms(self) -> int:
        return int(time.time() * 1000)

    def _state_for(self) -> VoiceAgentState:
        if not self.ready:
            return VoiceAgentState.UNAVAILABLE
        if self._mode == VoiceMode.DISABLED:
            return VoiceAgentState.DISABLED
        if self._active_task is not None and not self._active_task.done():
            return VoiceAgentState.BUSY
        return VoiceAgentState.IDLE

    def _device(self, device_id: Optional[str], direction: str) -> VoiceDevice:
        if not device_id:
            raise VoiceAgentError(f"no {direction} device is selected")
        for device in self._devices:
            if device.id == device_id and device.direction == direction:
                return device
        raise VoiceAgentError(f"selected {direction} device is not in the agent allowlist")

    def _refresh_devices(self) -> None:
        if not self._devices:
            try:
                self._devices = discover_voice_devices()
            except AudioDeviceError:
                self._devices = []

    def _selected_audio(self) -> AlsaAudioIO:
        capture = self._device(self._input_id, "capture")
        playback = self._device(self._output_id, "playback")
        return AlsaAudioIO(self.settings, capture_device=capture.backend_id, playback_device=playback.backend_id)

    def _new_turn(self) -> VoiceTurn:
        self._trim_turn_history()
        now = self._now_ms()
        turn = VoiceTurn(uuid.uuid4().hex, VoiceTurnState.LISTENING, now, now)
        self._turns[turn.id] = turn
        self._active_turn_id = turn.id
        return turn

    def _trim_turn_history(self) -> None:
        """Keep completed/cancelled voice transcripts in a fixed memory budget."""

        while len(self._turns) >= _MAX_RETAINED_TURNS:
            oldest_id, oldest_turn = next(iter(self._turns.items()))
            if oldest_id == self._active_turn_id or not oldest_turn.terminal:
                return
            self._turns.popitem(last=False)

    def _record_auto_listen_failure(self) -> Optional[float]:
        """Back off repeated board-audio failures and eventually fail closed."""

        with self._state_lock:
            if self._mode != VoiceMode.BOARD or not self._auto_listen:
                return None
            self._auto_listen_failures += 1
            failures = self._auto_listen_failures
            if failures >= _AUTO_LISTEN_MAX_CONSECUTIVE_FAILURES:
                self._auto_listen = False
                LOGGER.warning(
                    "stopped board auto-listen after %d consecutive voice-turn failures",
                    failures,
                )
                return None
        delay = min(
            _AUTO_LISTEN_RETRY_BASE_SECONDS * (2 ** (failures - 1)),
            _AUTO_LISTEN_RETRY_MAX_SECONDS,
        )
        LOGGER.warning(
            "board auto-listen turn failed; retrying in %.1f seconds (failure %d/%d)",
            delay,
            failures,
            _AUTO_LISTEN_MAX_CONSECUTIVE_FAILURES,
        )
        return delay

    def _reset_auto_listen_failures(self) -> None:
        with self._state_lock:
            self._auto_listen_failures = 0

    def _update(self, turn: VoiceTurn, state: VoiceTurnState, error: str = "") -> None:
        if turn.terminal:
            return
        turn.state = state
        turn.updated_at_ms = self._now_ms()
        if error:
            turn.error = error[:240]

    async def apply_config(self, request: VoiceConfigRequest) -> dict[str, object]:
        """Atomically validate an allowlisted mode/device update."""

        with self._state_lock:
            mode = request.mode if request.mode is not None else self._mode
            input_id = request.input_id if request.input_id is not None else self._input_id
            output_id = request.output_id if request.output_id is not None else self._output_id
            auto_listen = request.auto_listen if request.auto_listen is not None else self._auto_listen
            self._refresh_devices()
            if request.input_id is not None:
                self._device(input_id, "capture")
            if request.output_id is not None:
                self._device(output_id, "playback")
            if mode == VoiceMode.BOARD:
                self._device(input_id, "capture")
                self._device(output_id, "playback")
                if not self.ready:
                    raise VoiceAgentError("ASR, TTS, or loopback gateway credentials are not configured")
            if mode != VoiceMode.BOARD:
                auto_listen = False
            self._mode, self._input_id, self._output_id, self._auto_listen = mode, input_id, output_id, auto_listen
            self._auto_listen_failures = 0
        if mode == VoiceMode.BOARD and auto_listen:
            await self._ensure_supervisor()
        else:
            await self._stop_board_tasks()
        return self.public_config()

    async def _ensure_supervisor(self) -> None:
        with self._state_lock:
            if self._supervisor_task is None or self._supervisor_task.done():
                self._supervisor_task = asyncio.create_task(self._run_forever(), name="voice-agent-loop")

    async def _stop_board_tasks(self) -> None:
        tasks = [task for task in (self._supervisor_task, self._active_task) if task is not None and not task.done()]
        current = asyncio.current_task()
        for task in tasks:
            if task is not current:
                task.cancel()
        for task in tasks:
            if task is not current:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    async def start_board_turn(self) -> VoiceTurn:
        if self._mode != VoiceMode.BOARD:
            raise VoiceAgentError("board capture is only available in board mode")
        if not self.ready:
            raise VoiceAgentError("voice agent is unavailable: ASR, TTS, or gateway credentials are not configured")
        with self._state_lock:
            if self._active_task is not None and not self._active_task.done():
                raise VoiceAgentError("a board-side voice turn is already active")
            audio = self._selected_audio()
            turn = self._new_turn()
            self._active_task = asyncio.create_task(self._run_board_turn(turn, audio), name=f"voice-turn-{turn.id}")
            return turn

    async def _run_board_turn(self, turn: VoiceTurn, audio: AlsaAudioIO) -> None:
        try:
            pcm = await audio.capture_utterance(max_duration_seconds=self.settings.max_capture_seconds)
            await self._run_pipeline(turn, pcm, audio)
        except asyncio.CancelledError:
            self._update(turn, VoiceTurnState.CANCELLED, "voice turn cancelled")
            raise
        except (AudioDeviceError, SpeechAdapterError, VoiceAgentError, ValueError) as exc:
            self._update(turn, VoiceTurnState.FAILED, str(exc))
        except Exception:  # pragma: no cover
            LOGGER.exception("voice turn failed")
            self._update(turn, VoiceTurnState.FAILED, "voice turn failed")
        finally:
            with self._state_lock:
                if self._active_turn_id == turn.id:
                    self._active_turn_id = None

    async def _run_pipeline(self, turn: VoiceTurn, pcm: bytes, playback: Optional[VoicePlayback]) -> SynthesizedAudio:
        self._update(turn, VoiceTurnState.TRANSCRIBING)
        transcript = await self._adapters.recognizer.transcribe(pcm, sample_rate_hz=self.settings.capture_sample_rate_hz)
        if not isinstance(transcript, str) or not transcript.strip():
            raise VoiceAgentError("ASR returned no recognized speech")
        turn.transcript = transcript.strip()[: self.settings.max_text_characters]
        self._update(turn, VoiceTurnState.THINKING)
        reply = await self._adapters.chat.complete(turn.transcript)
        if not isinstance(reply, str) or not reply.strip():
            raise VoiceAgentError("loopback LLM returned an empty reply")
        turn.reply = reply.strip()[: self.settings.max_text_characters]
        self._update(turn, VoiceTurnState.SPEAKING)
        audio = await self._adapters.synthesizer.synthesize(turn.reply)
        if playback is not None:
            await playback.play_pcm(audio)
            self._update(turn, VoiceTurnState.COMPLETED)
        return audio

    async def _run_forever(self) -> None:
        try:
            while self._mode == VoiceMode.BOARD and self._auto_listen:
                retry_delay: Optional[float] = None
                try:
                    turn = await self.start_board_turn()
                    task = self._active_task
                    if task is not None:
                        await task
                    if turn.state == VoiceTurnState.FAILED:
                        retry_delay = self._record_auto_listen_failure()
                    else:
                        self._reset_auto_listen_failures()
                except VoiceAgentError:
                    retry_delay = self._record_auto_listen_failure()

                if retry_delay is not None:
                    await asyncio.sleep(retry_delay)
                    continue
                if self._mode != VoiceMode.BOARD or not self._auto_listen:
                    return
                await asyncio.sleep(_AUTO_LISTEN_IDLE_SECONDS)
        except asyncio.CancelledError:
            raise

    async def browser_transcribe(self, wav_bytes: bytes) -> dict[str, object]:
        if self._mode != VoiceMode.BROWSER:
            raise VoiceAgentError("browser transcription is only available in browser mode")
        pcm = _decode_browser_wav(wav_bytes, self.settings)
        transcript = await self._adapters.recognizer.transcribe(pcm, sample_rate_hz=self.settings.capture_sample_rate_hz)
        if not isinstance(transcript, str) or not transcript.strip():
            raise VoiceAgentError("ASR returned no recognized speech")
        return {"transcript": transcript.strip()[: self.settings.max_text_characters]}

    async def browser_synthesize(self, text: str) -> bytes:
        if self._mode != VoiceMode.BROWSER:
            raise VoiceAgentError("browser synthesis is only available in browser mode")
        if not text.strip():
            raise VoiceAgentError("text must not be empty")
        audio = await self._adapters.synthesizer.synthesize(text.strip()[: self.settings.max_text_characters])
        return _encode_wav(audio, self.settings.max_browser_audio_bytes)

    async def cancel_turn(self, turn_id: Optional[str] = None) -> VoiceTurn:
        requested = turn_id or self._active_turn_id
        if not requested or requested != self._active_turn_id or self._active_task is None:
            raise VoiceAgentError("no matching active voice turn")
        self._active_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._active_task
        return self._turns[requested]

    def public_config(self) -> dict[str, object]:
        self._refresh_devices()
        inputs = [device.as_dict() for device in self._devices if device.direction == "capture"]
        outputs = [device.as_dict() for device in self._devices if device.direction == "playback"]
        return {
            "mode": self._mode.value,
            "modes": [mode.value for mode in VoiceMode],
            "inputs": inputs,
            "outputs": outputs,
            "input_id": self._input_id,
            "output_id": self._output_id,
            "auto_listen": self._auto_listen,
            "enabled": self._auto_listen,  # compatibility alias for the initial thin UI
            "ready": self.ready,
            "capture": {"sample_rate_hz": 16000, "channels": 1, "sample_width_bytes": 2, "frame_ms": self.settings.frame_ms},
            "playback": {"sample_rate_hz": self.settings.playback_sample_rate_hz},
            "vad": {"speech_rms_threshold": self.settings.speech_rms_threshold, "start_speech_frames": self.settings.start_speech_frames, "stop_silence_ms": self.settings.stop_silence_ms},
            "gateway": {"loopback": True, "configured": bool(self.settings.gateway_api_key)},
            "speech": self.speech_readiness(),
        }

    def status(self) -> dict[str, object]:
        active = self._turns.get(self._active_turn_id) if self._active_turn_id else None
        latest = next(reversed(self._turns.values()), None)
        return {"mode": self._mode.value, "enabled": self._auto_listen, "auto_listen": self._auto_listen, "ready": self.ready, "state": self._state_for().value, "active_turn": active.as_dict() if active else None, "latest_turn": latest.as_dict() if latest else None, "speech": self.speech_readiness()}

    def health(self) -> dict[str, object]:
        return {"status": "ready" if self.ready else "unavailable", "mode": self._mode.value, "state": self._state_for().value, "enabled": self._auto_listen, "speech": self.speech_readiness()}

    async def aclose(self) -> None:
        await self.apply_config(VoiceConfigRequest(mode=VoiceMode.DISABLED, auto_listen=False))
        for adapter in (self._adapters.chat, self._adapters.recognizer, self._adapters.synthesizer):
            close = getattr(adapter, "aclose", None)
            if close is not None:
                result = close()
                if asyncio.iscoroutine(result):
                    await result


def _decode_browser_wav(payload: bytes, settings: VoiceSettings) -> bytes:
    if not payload or len(payload) > settings.max_browser_audio_bytes:
        raise VoiceAgentError("browser WAV is empty or exceeds its size limit")
    try:
        with wave.open(io.BytesIO(payload), "rb") as source:
            if source.getnchannels() != 1 or source.getsampwidth() != 2 or source.getframerate() != 16000 or source.getcomptype() != "NONE":
                raise VoiceAgentError("browser WAV must be 16 kHz mono signed-16-bit PCM")
            frames = source.readframes(source.getnframes())
    except (wave.Error, EOFError) as exc:
        raise VoiceAgentError("browser audio is not a valid WAV file") from exc
    if not frames or len(frames) > settings.max_browser_audio_bytes:
        raise VoiceAgentError("browser WAV PCM violates the configured size limit")
    return frames


def _encode_wav(audio: SynthesizedAudio, maximum_bytes: int) -> bytes:
    if audio.channels != 1 or audio.sample_width_bytes != 2 or not audio.pcm or len(audio.pcm) % 2:
        raise VoiceAgentError("TTS audio violates the mono signed-16-bit PCM contract")
    if len(audio.pcm) > maximum_bytes:
        raise VoiceAgentError("TTS audio exceeds the browser response size limit")
    output = io.BytesIO()
    with wave.open(output, "wb") as sink:
        sink.setnchannels(1)
        sink.setsampwidth(2)
        sink.setframerate(audio.sample_rate_hz)
        sink.writeframes(audio.pcm)
    return output.getvalue()


def create_default_agent(settings: VoiceSettings) -> VoiceAgent:
    """Wire only an explicitly selected lazy speech backend; never install it."""

    recognizer, synthesizer = build_speech_adapters(settings)
    return VoiceAgent(
        settings,
        recognizer=recognizer,
        synthesizer=synthesizer,
        chat=LoopbackGatewayChat(settings),
    )


def _require_loopback(request: Request) -> None:
    """Reject non-loopback callers; no separate voice operator token is used."""

    host = request.client.host if request.client else ""
    if host not in _LOOPBACK_CLIENTS:
        raise HTTPException(status_code=403, detail="voice agent only accepts loopback clients")


def create_voice_agent_app(settings: Optional[VoiceSettings] = None, *, agent: Optional[VoiceAgent] = None) -> FastAPI:
    runtime_settings = settings or VoiceSettings.from_environ()
    runtime_agent = agent or create_default_agent(runtime_settings)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            await runtime_agent.aclose()

    app = FastAPI(
        title="Ascend Edge Chat board voice agent",
        version="0.1.0",
        lifespan=lifespan,
    )

    @app.get("/health")
    async def health() -> dict[str, object]:
        return runtime_agent.health()

    @app.get("/api/config")
    async def config(request: Request) -> dict[str, object]:
        _require_loopback(request)
        return runtime_agent.public_config()

    @app.get("/api/status")
    async def status(request: Request) -> dict[str, object]:
        _require_loopback(request)
        return runtime_agent.status()

    async def configure(request: Request, update: VoiceConfigRequest) -> dict[str, object]:
        _require_loopback(request)
        try:
            return await runtime_agent.apply_config(update)
        except VoiceAgentError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    app.put("/api/config")(configure)
    app.post("/api/config")(configure)

    @app.post("/v1/voice/browser/transcribe")
    async def browser_transcribe(request: Request) -> dict[str, object]:
        _require_loopback(request)
        content_type = request.headers.get("content-type", "").lower()
        if not content_type.startswith("audio/wav") and not content_type.startswith("audio/x-wav"):
            raise HTTPException(status_code=415, detail="browser transcription requires audio/wav")
        body = await request.body()
        try:
            return await runtime_agent.browser_transcribe(body)
        except (VoiceAgentError, SpeechAdapterError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post("/v1/voice/browser/synthesize")
    async def browser_synthesize(request: Request, body: BrowserSynthesizeRequest) -> Response:
        _require_loopback(request)
        try:
            wav = await runtime_agent.browser_synthesize(body.text)
        except (VoiceAgentError, SpeechAdapterError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return Response(content=wav, media_type="audio/wav", headers={"cache-control": "no-store"})

    @app.get("/v1/voice/status")
    async def internal_status(request: Request) -> dict[str, object]:
        _require_loopback(request)
        return runtime_agent.status()

    @app.post("/v1/voice/turns")
    async def start_turn(request: Request) -> dict[str, object]:
        _require_loopback(request)
        try:
            return (await runtime_agent.start_board_turn()).as_dict()
        except VoiceAgentError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    return app


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run the Ascend Edge Chat loopback voice agent")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--check-config", action="store_true")
    args = parser.parse_args(argv)
    settings = VoiceSettings.from_environ()
    if args.host is not None or args.port is not None:
        settings = replace(settings, host=args.host if args.host is not None else settings.host, port=args.port if args.port is not None else settings.port)
    if args.check_config:
        print("voice-agent configuration is valid; device/model readiness is separate")
        return 0
    uvicorn.run(create_voice_agent_app(settings), host=settings.host, port=settings.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["VoiceAgent", "VoiceAgentError", "VoiceConfigRequest", "create_default_agent", "create_voice_agent_app", "main"]
