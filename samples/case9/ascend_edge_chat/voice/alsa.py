"""Safe ALSA enumeration and board-owned PCM capture/playback.

No shell is used. The only subprocesses are fixed ``arecord`` and ``aplay``
argument lists with agent-enumerated device IDs. Captured PCM stays in memory
until ASR consumes it and is never exposed by the HTTP service.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import shutil
import signal
import subprocess
import sys
from array import array
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

from .audio_processing import AgcSettings, apply_pcm16_agc
from .config import VoiceSettings
from .protocol import SynthesizedAudio, VoiceDevice
from .vad import EnergyVad, VadSettings, VadState

LOGGER = logging.getLogger("ascend_edge_chat.voice.alsa")


_PCM_LINE = re.compile(
    r"^(?P<card>\d+)-(?P<device>\d+):\s*(?P<name>.*?)\s*:\s*.*?\s*:\s*(?P<direction>playback|capture)\s+\d+\s*$"
)


class AudioDeviceError(RuntimeError):
    """A local ALSA device, command, or PCM contract failed."""


class NoSpeechDetected(AudioDeviceError):
    """A bounded listen window ended without any retained speech."""


@dataclass(frozen=True)
class AlsaPcmDevice:
    """One PCM node enriched with its parent ALSA card model."""

    card: int
    device: int
    name: str
    direction: Literal["capture", "playback"]
    card_model: str = ""

    @property
    def hw_name(self) -> str:
        return f"hw:{self.card},{self.device}"

    @property
    def public_id(self) -> str:
        return f"alsa-{self.direction}-{self.card}-{self.device}"

    def as_voice_device(self) -> VoiceDevice:
        model = self.card_model.strip()
        if model and model.lower() not in self.name.lower():
            label = f"{model} / {self.name} ({self.hw_name})"
        elif model:
            label = f"{model} ({self.hw_name})"
        else:
            label = f"{self.name} ({self.hw_name})"
        return VoiceDevice(
            id=self.public_id,
            label=label,
            direction=self.direction,
            backend_id=self.hw_name,
            model=model,
        )


_CARD_LINE = re.compile(
    r"^\s*(?P<card>\d+)\s+\[(?P<id>[^\]]+)\]:\s+"
    r"(?P<driver>.*?)\s+-\s+(?P<model>.+?)\s*$"
)
_PULSE_SOURCE_BLOCK = re.compile(r"^Source #\d+\s*$", re.MULTILINE)
_PULSE_SOURCE_NAME = re.compile(r"^\s*Name:\s*(?P<value>\S+)\s*$", re.MULTILINE)
_PULSE_ALSA_CARD = re.compile(r'^\s*alsa\.card\s*=\s*"(?P<value>\d+)"\s*$', re.MULTILINE)
_PULSE_ALSA_DEVICE = re.compile(r'^\s*alsa\.device\s*=\s*"(?P<value>\d+)"\s*$', re.MULTILINE)


def _card_models(proc_root: Path) -> dict[int, str]:
    """Read card model names from ``/proc/asound/cards`` when available."""

    try:
        lines = (proc_root / "cards").read_text(
            encoding="utf-8", errors="replace"
        ).splitlines()
    except OSError:
        return {}
    models: dict[int, str] = {}
    for line in lines:
        match = _CARD_LINE.match(line)
        if match is None:
            continue
        model = match.group("model").strip()
        if model:
            models[int(match.group("card"))] = model[:160]
    return models


def enumerate_alsa_devices(proc_root: Path = Path("/proc/asound")) -> list[AlsaPcmDevice]:
    """Parse available PCM devices and enrich them with card model names."""

    try:
        lines = (proc_root / "pcm").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        raise AudioDeviceError(f"cannot read {proc_root / 'pcm'}") from exc
    models = _card_models(proc_root)
    devices: list[AlsaPcmDevice] = []
    for line in lines:
        match = _PCM_LINE.match(line.strip())
        if match is None:
            continue
        card = int(match.group("card"))
        devices.append(
            AlsaPcmDevice(
                card=card,
                device=int(match.group("device")),
                name=match.group("name").strip() or "unnamed",
                direction=match.group("direction"),
                card_model=models.get(card, ""),
            )
        )
    return devices


def discover_voice_devices(proc_root: Path = Path("/proc/asound")) -> list[VoiceDevice]:
    """Return public descriptors, never exposing a caller-selected ALSA path."""

    return [device.as_voice_device() for device in enumerate_alsa_devices(proc_root)]


def _pulse_source_for_alsa_device(pactl_output: str, *, card: int, device: int) -> Optional[str]:
    """Resolve one Pulse source by its ALSA card/device properties."""

    for block in _PULSE_SOURCE_BLOCK.split(pactl_output):
        name = _PULSE_SOURCE_NAME.search(block)
        source_card = _PULSE_ALSA_CARD.search(block)
        source_device = _PULSE_ALSA_DEVICE.search(block)
        if (
            name is not None
            and source_card is not None
            and source_device is not None
            and int(source_card.group("value")) == card
            and int(source_device.group("value")) == device
        ):
            return name.group("value")
    return None


def downmix_pcm16le(pcm: bytes, channels: int) -> bytes:
    """Return mono S16_LE PCM without retaining the source recording.

    The initial ASR/VAD contract is mono, while USB microphones commonly expose
    only stereo capture.  Downmixing happens frame-by-frame in memory.
    """

    if channels not in {1, 2} or not pcm or len(pcm) % (2 * channels):
        raise AudioDeviceError("capture PCM does not match its declared channel count")
    if channels == 1:
        return pcm
    values = array("h")
    values.frombytes(pcm)
    if sys.byteorder != "little":
        values.byteswap()
    mono = array("h")
    for index in range(0, len(values), 2):
        mixed = (int(values[index]) + int(values[index + 1])) // 2
        mono.append(max(-32768, min(32767, mixed)))
    if sys.byteorder != "little":
        mono.byteswap()
    return mono.tobytes()


async def _stop_process(process: asyncio.subprocess.Process) -> None:
    """Close a capture stream gracefully before escalating process signals."""

    if process.returncode is not None:
        return
    try:
        process.send_signal(signal.SIGINT)
    except ProcessLookupError:
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=3.0)
        return
    except asyncio.TimeoutError:
        pass
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=3.0)
        return
    except asyncio.TimeoutError:
        pass
    with contextlib.suppress(ProcessLookupError):
        process.kill()
    try:
        await asyncio.wait_for(process.wait(), timeout=1.5)
    except asyncio.TimeoutError as exc:
        raise AudioDeviceError("arecord did not exit after graceful shutdown") from exc


class PushToTalkRecorder:
    """In-memory hold-to-record capture that can be replayed to the selected sink."""

    def __init__(self, audio: "AlsaAudioIO", *, max_duration_seconds: float = 10.0):
        self._audio = audio
        self._max_duration_seconds = max_duration_seconds
        self._channels = audio.settings.capture_device_channels or 2
        self._process: Optional[asyncio.subprocess.Process] = None
        self._read_task: Optional[asyncio.Task[None]] = None
        self._pcm = bytearray()
        self._read_error: Optional[BaseException] = None

    async def start(self) -> None:
        if self._process is not None:
            raise AudioDeviceError("push-to-talk capture is already active")
        self._audio.require_capture_binary()
        self._audio.enable_hardware_auto_gain()
        self._process = await asyncio.create_subprocess_exec(
            *self._audio.capture_command(self._channels),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._read_task = asyncio.create_task(self._read_stdout())

    async def _read_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            raise AudioDeviceError("arecord stdout was not available")
        max_bytes = int(
            self._audio.settings.capture_sample_rate_hz
            * self._channels
            * self._audio.settings.sample_width_bytes
            * self._max_duration_seconds
        )
        try:
            while len(self._pcm) < max_bytes:
                chunk = await process.stdout.read(min(8192, max_bytes - len(self._pcm)))
                if not chunk:
                    break
                self._pcm.extend(chunk)
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            self._read_error = exc
        finally:
            if len(self._pcm) >= max_bytes:
                with contextlib.suppress(ProcessLookupError):
                    await _stop_process(process)

    async def stop(self) -> SynthesizedAudio:
        process = self._process
        task = self._read_task
        if process is None or task is None:
            raise AudioDeviceError("push-to-talk capture has not started")
        with contextlib.suppress(ProcessLookupError):
            await _stop_process(process)
        with contextlib.suppress(asyncio.CancelledError):
            await task
        self._process = None
        self._read_task = None
        if self._read_error is not None:
            raise AudioDeviceError("push-to-talk capture failed") from self._read_error
        detail = ""
        if process.stderr is not None:
            detail = (await process.stderr.read()).decode("utf-8", errors="replace").strip()
        frame_bytes = self._audio.settings.sample_width_bytes * self._channels
        retained = bytes(self._pcm[: len(self._pcm) - (len(self._pcm) % frame_bytes)])
        minimum_bytes = int(
            self._audio.settings.capture_sample_rate_hz
            * self._audio.settings.sample_width_bytes
            * self._channels
            * 0.2
        )
        if len(retained) < minimum_bytes:
            suffix = ": " + detail.split("\n")[0] if detail else ""
            raise AudioDeviceError("hold the button for at least 0.2 seconds" + suffix)
        mono = downmix_pcm16le(retained, self._channels)
        conditioned = apply_pcm16_agc(
            mono,
            AgcSettings(
                enabled=self._audio.settings.auto_gain_enabled,
                target_rms=self._audio.settings.auto_gain_target_rms,
                max_gain=self._audio.settings.auto_gain_max_gain,
                frame_ms=self._audio.settings.frame_ms,
                sample_rate_hz=self._audio.settings.capture_sample_rate_hz,
            ),
        )
        LOGGER.info(
            "push-to-talk conditioned input_rms=%.1f active_rms=%.1f gain=%.2f output_rms=%.1f",
            conditioned.input_rms,
            conditioned.active_rms,
            conditioned.gain,
            conditioned.output_rms,
        )
        return SynthesizedAudio(
            pcm=conditioned.pcm,
            sample_rate_hz=self._audio.settings.capture_sample_rate_hz,
        )

    async def cancel(self) -> None:
        process = self._process
        task = self._read_task
        self._process = None
        self._read_task = None
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        if process is not None:
            with contextlib.suppress(ProcessLookupError):
                await _stop_process(process)
        self._pcm.clear()


class AlsaAudioIO:
    """One half-duplex ALSA endpoint with explicit agent-selected devices."""

    def __init__(self, settings: VoiceSettings, *, capture_device: str, playback_device: str):
        self.settings = settings
        self.capture_device = capture_device
        self.playback_device = playback_device
        self._operation_lock: Optional[asyncio.Lock] = None

    def _lock(self) -> asyncio.Lock:
        """Create the async operation lock only inside a running event loop."""

        if self._operation_lock is None:
            self._operation_lock = asyncio.Lock()
        return self._operation_lock

    def capture_command(self, channels: int = 1) -> tuple[str, ...]:
        if channels not in {1, 2}:
            raise AudioDeviceError("capture channels must be mono or stereo")
        if self.settings.capture_plugin == "pulse":
            pulse_source = self._pulse_source_for_capture_device()
            return (
                "parec",
                f"--device={pulse_source}",
                "--format=s16le",
                "--latency-msec=20",
                f"--rate={self.settings.capture_sample_rate_hz}",
                f"--channels={channels}",
                "--raw",
            )
        capture_device = (
            self.settings.capture_plugin + ":" + self.capture_device[3:]
            if self.capture_device.startswith("hw:")
            else self.capture_device
        )
        return (
            "arecord", "--quiet", "--format=S16_LE",
            f"--rate={self.settings.capture_sample_rate_hz}",
            f"--channels={channels}", "--file-type=raw",
            f"--device={capture_device}",
        )

    def playback_command(self, sample_rate_hz: int) -> tuple[str, ...]:
        playback_device = (
            "plughw:" + self.playback_device[3:]
            if self.playback_device.startswith("hw:")
            else self.playback_device
        )
        return (
            "aplay", "--quiet", "--format=S16_LE",
            f"--rate={sample_rate_hz}", f"--channels={self.settings.channels}",
            "--file-type=raw", f"--device={playback_device}",
        )

    def pulse_playback_command(self, sample_rate_hz: int) -> tuple[str, ...]:
        """Final fallback route through the desktop PulseAudio server."""

        return (
            "aplay", "--quiet", "--format=S16_LE",
            f"--rate={sample_rate_hz}", f"--channels={self.settings.channels}",
            "--file-type=raw", "--device=pulse",
        )

    def suspended_playback_command(self, sample_rate_hz: int) -> tuple[str, ...]:
        """Temporarily suspend PulseAudio and play directly to the selected PCM."""

        return ("pasuspender", "--", *self.playback_command(sample_rate_hz))

    def begin_push_to_talk(self, *, max_duration_seconds: float = 10.0) -> PushToTalkRecorder:
        """Create one bounded in-memory microphone replay operation."""

        if not 0.2 <= max_duration_seconds <= 30.0:
            raise AudioDeviceError("push-to-talk duration is outside the configured bound")
        return PushToTalkRecorder(
            self,
            max_duration_seconds=max_duration_seconds,
        )

    @staticmethod
    def _require_binary(name: str) -> None:
        if shutil.which(name) is None:
            raise AudioDeviceError(f"required audio command is unavailable: {name}")

    def require_capture_binary(self) -> None:
        if self.settings.capture_plugin == "pulse":
            self._require_binary("parec")
            self._require_binary("pactl")
            return
        self._require_binary("arecord")

    def _pulse_source_for_capture_device(self) -> str:
        match = re.fullmatch(r"hw:(\d+),(\d+)", self.capture_device)
        if match is None:
            raise AudioDeviceError("Pulse capture requires an enumerated ALSA hardware device")
        self._require_binary("pactl")
        try:
            completed = subprocess.run(
                ["pactl", "list", "sources"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=3.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise AudioDeviceError("cannot enumerate PulseAudio capture sources") from exc
        if completed.returncode != 0:
            raise AudioDeviceError("cannot enumerate PulseAudio capture sources")
        source = _pulse_source_for_alsa_device(
            completed.stdout.decode("utf-8", errors="replace"),
            card=int(match.group(1)),
            device=int(match.group(2)),
        )
        if source is None:
            raise AudioDeviceError("selected microphone is not available through PulseAudio")
        return source

    def enable_hardware_auto_gain(self) -> None:
        """Best-effort enable a device-advertised ALSA capture AGC control."""

        if not self.settings.auto_gain_enabled:
            return
        match = re.fullmatch(r"hw:(\d+),\d+", self.capture_device)
        if match is None or shutil.which("amixer") is None:
            return
        try:
            completed = subprocess.run(
                [
                    "amixer",
                    "-c",
                    match.group(1),
                    "cset",
                    "name=Auto Gain Control",
                    "on",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return
        if completed.returncode == 0:
            LOGGER.info("hardware capture AGC enabled device=%s", self.capture_device)

    async def capture_utterance(self, *, max_duration_seconds: float) -> bytes:
        """Capture one VAD-bounded signed-16-bit mono utterance in memory."""

        if not 1.0 <= max_duration_seconds <= self.settings.max_capture_seconds:
            raise AudioDeviceError("capture duration exceeds the configured voice-agent bound")
        self.require_capture_binary()
        self.enable_hardware_auto_gain()
        # Most UVC microphones expose only two channels. Users may force mono
        # or stereo after inspecting their device; auto defaults to stereo and
        # immediately downmixes to the fixed mono ASR contract.
        capture_channels = self.settings.capture_device_channels or 2
        async with self._lock():
            process = await asyncio.create_subprocess_exec(
                *self.capture_command(capture_channels), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            try:
                if process.stdout is None:
                    raise AudioDeviceError("arecord stdout was not available")
                vad = EnergyVad(VadSettings(
                    sample_rate_hz=self.settings.capture_sample_rate_hz,
                    frame_ms=self.settings.frame_ms,
                    speech_rms_threshold=self.settings.speech_rms_threshold,
                    start_speech_frames=self.settings.start_speech_frames,
                    stop_silence_ms=self.settings.stop_silence_ms,
                    adaptive=self.settings.adaptive_vad_enabled,
                    noise_floor_initial_rms=self.settings.noise_floor_initial_rms,
                    noise_floor_multiplier=self.settings.noise_floor_multiplier,
                    noise_floor_decay=self.settings.noise_floor_decay,
                    noise_floor_calibration_frames=self.settings.noise_floor_calibration_frames,
                    max_duration_seconds=max_duration_seconds,
                ))
                pre_roll: list[bytes] = []
                utterance = bytearray()
                deadline = asyncio.get_running_loop().time() + max_duration_seconds
                while True:
                    remaining = deadline - asyncio.get_running_loop().time()
                    if remaining <= 0:
                        if not utterance:
                            raise NoSpeechDetected(
                                "no speech detected before listen window ended"
                            )
                        raise AudioDeviceError("arecord capture exceeded its time limit")
                    try:
                        raw_frame = await asyncio.wait_for(
                            process.stdout.readexactly(
                                vad.settings.frame_bytes * capture_channels
                            ),
                            timeout=remaining,
                        )
                    except asyncio.TimeoutError as exc:
                        if not utterance:
                            raise NoSpeechDetected(
                                "no speech detected before listen window ended"
                            ) from exc
                        raise AudioDeviceError("arecord capture timed out") from exc
                    frame = downmix_pcm16le(raw_frame, capture_channels)
                    decision = vad.feed(frame)
                    if decision.state == VadState.WAITING:
                        pre_roll.append(frame)
                        if len(pre_roll) > self.settings.start_speech_frames:
                            del pre_roll[0]
                    elif decision.started:
                        utterance.extend(b"".join(pre_roll))
                        pre_roll.clear()
                    else:
                        utterance.extend(frame)
                    if len(utterance) > self.settings.max_capture_bytes:
                        raise AudioDeviceError("captured PCM exceeded the configured memory bound")
                    if decision.complete:
                        if not utterance:
                            raise NoSpeechDetected("no speech detected before listen window ended")
                        conditioned = apply_pcm16_agc(
                            bytes(utterance),
                            AgcSettings(
                                enabled=self.settings.auto_gain_enabled,
                                target_rms=self.settings.auto_gain_target_rms,
                                max_gain=self.settings.auto_gain_max_gain,
                                frame_ms=self.settings.frame_ms,
                                sample_rate_hz=self.settings.capture_sample_rate_hz,
                            ),
                        )
                        LOGGER.info(
                            "capture conditioned input_rms=%.1f active_rms=%.1f gain=%.2f output_rms=%.1f",
                            conditioned.input_rms,
                            conditioned.active_rms,
                            conditioned.gain,
                            conditioned.output_rms,
                        )
                        return conditioned.pcm
            except asyncio.IncompleteReadError as exc:
                detail = ""
                if process.stderr is not None:
                    detail = (await process.stderr.read()).decode("utf-8", errors="replace").strip().split("\n")[0]
                raise AudioDeviceError(f"arecord ended before a complete VAD frame{': ' + detail if detail else ''}") from exc
            finally:
                with contextlib.suppress(ProcessLookupError):
                    await _stop_process(process)

    async def play_pcm(self, audio: SynthesizedAudio) -> None:
        """Play finite signed-16-bit mono PCM and wait for local completion."""

        if audio.channels != self.settings.channels or audio.sample_width_bytes != self.settings.sample_width_bytes:
            raise AudioDeviceError("TTS PCM does not match the mono signed-16-bit playback contract")
        if not 8_000 <= audio.sample_rate_hz <= 48_000 or not audio.pcm or len(audio.pcm) % 2:
            raise AudioDeviceError("TTS PCM violates the playback contract")
        if len(audio.pcm) > self.settings.max_capture_bytes * 4:
            raise AudioDeviceError("TTS PCM exceeded the configured playback memory bound")
        self._require_binary("aplay")

        async def run(command: tuple[str, ...]) -> tuple[int, str]:
            try:
                process = await asyncio.create_subprocess_exec(
                    *command,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.PIPE,
                )
            except OSError as exc:
                return 1, str(exc)
            timeout = max(10.0, len(audio.pcm) / (audio.sample_rate_hz * 2) + 8.0)
            try:
                _, stderr = await asyncio.wait_for(process.communicate(audio.pcm), timeout=timeout)
            except asyncio.CancelledError:
                with contextlib.suppress(ProcessLookupError, AudioDeviceError):
                    await _stop_process(process)
                raise
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError, AudioDeviceError):
                    await _stop_process(process)
                return 1, "playback timed out"
            detail = (stderr or b"").decode("utf-8", errors="replace").strip().split("\n")[0]
            return process.returncode or 0, detail

        async with self._lock():
            returncode, detail = await run(self.playback_command(audio.sample_rate_hz))
            if returncode != 0 and "busy" in detail.lower():
                if shutil.which("pasuspender") is not None:
                    returncode, detail = await run(self.suspended_playback_command(audio.sample_rate_hz))
                if returncode != 0:
                    returncode, detail = await run(self.pulse_playback_command(audio.sample_rate_hz))
            if returncode != 0:
                raise AudioDeviceError(
                    f"aplay exited with status {returncode}{': ' + detail if detail else ''}"
                )


__all__ = ["AlsaAudioIO", "AlsaPcmDevice", "AudioDeviceError", "discover_voice_devices", "enumerate_alsa_devices"]
