"""Lazy local speech and loopback-chat adapters for the voice agent.

``sherpa_onnx`` is imported only by readiness checks or the first ASR/TTS call.
The code never downloads, installs, unpacks, or mutates a speech artifact. A
configured backend stays unavailable until both its wheel and model files are
present, so configuration cannot be mistaken for board validation.
"""

from __future__ import annotations

import asyncio
import importlib.util
import io
import logging
import shutil
import struct
import subprocess
import wave
from array import array
from pathlib import Path
from typing import Any

import httpx

from .config import VoiceSettings
from .protocol import SpeechRecognizer, SpeechSynthesizer, SynthesizedAudio


LOGGER = logging.getLogger("ascend_edge_chat.voice.adapters")


class SpeechAdapterError(RuntimeError):
    """A lazy speech or loopback adapter is unavailable or returned invalid data."""


class UnavailableRecognizer:
    """Explicit ASR placeholder when no admitted local backend was selected."""

    reason = "ASR backend is unavailable; no speech model was loaded"

    def readiness(self) -> tuple[bool, str]:
        return False, self.reason

    async def transcribe(self, pcm: bytes, *, sample_rate_hz: int) -> str:
        del pcm, sample_rate_hz
        raise SpeechAdapterError(self.reason)


class UnavailableSynthesizer:
    """Explicit TTS placeholder when no admitted local backend was selected."""

    reason = "TTS backend is unavailable; no speech model was loaded"

    def readiness(self) -> tuple[bool, str]:
        return False, self.reason

    async def synthesize(self, text: str) -> SynthesizedAudio:
        del text
        raise SpeechAdapterError(self.reason)


def adapter_readiness(adapter: object) -> tuple[bool, str]:
    """Read optional adapter readiness without forcing a model load."""

    check = getattr(adapter, "readiness", None)
    if callable(check):
        result = check()
        if isinstance(result, tuple) and len(result) == 2:
            return bool(result[0]), str(result[1])
    return True, "adapter injected"


def _pcm16_to_float(pcm: bytes) -> list[float]:
    if not pcm or len(pcm) % 2:
        raise SpeechAdapterError("ASR received empty or unaligned signed-16-bit PCM")
    values = array("h")
    values.frombytes(pcm)
    if struct.pack("=h", 1) != struct.pack("<h", 1):
        values.byteswap()
    return [value / 32768.0 for value in values]


class SherpaOnnxRecognizer:
    """Lazy Zipformer transducer recognizer for an admitted local sherpa bundle."""

    def __init__(self, settings: VoiceSettings):
        self._settings = settings
        self._recognizer: Any = None

    def _paths(self) -> tuple[Path, Path, Path, Path]:
        configured = (
            self._settings.sherpa_asr_encoder,
            self._settings.sherpa_asr_decoder,
            self._settings.sherpa_asr_joiner,
            self._settings.sherpa_asr_tokens,
        )
        defaults = (
            "encoder-epoch-99-avg-1.int8.onnx",
            "decoder-epoch-99-avg-1.int8.onnx",
            "joiner-epoch-99-avg-1.int8.onnx",
            "tokens.txt",
        )
        root = Path(self._settings.sherpa_asr_model_dir) if self._settings.sherpa_asr_model_dir else None
        if root is None and not all(configured):
            raise SpeechAdapterError(
                "sherpa ASR requires SHERPA_ASR_MODEL_DIR or encoder/decoder/joiner/tokens paths"
            )
        paths = tuple(
            Path(name) if Path(name).is_absolute() else (root / name if root is not None else Path(name))
            for name in tuple(value or default for value, default in zip(configured, defaults))
        )
        missing = [path for path in paths if not path.is_file()]
        if missing:
            LOGGER.warning("sherpa ASR model files are missing: %s", ", ".join(map(str, missing)))
            raise SpeechAdapterError("sherpa ASR model files are not ready")
        return paths  # type: ignore[return-value]

    def readiness(self) -> tuple[bool, str]:
        if importlib.util.find_spec("sherpa_onnx") is None:
            return False, "sherpa_onnx is not installed in the selected Python environment"
        try:
            self._paths()
        except SpeechAdapterError as exc:
            return False, str(exc)
        return True, "sherpa-onnx ASR package and configured model files are present; model not loaded"

    def _load(self) -> Any:
        if self._recognizer is not None:
            return self._recognizer
        ready, reason = self.readiness()
        if not ready:
            raise SpeechAdapterError(reason)
        import sherpa_onnx  # type: ignore

        encoder, decoder, joiner, tokens = self._paths()
        try:
            factory = getattr(sherpa_onnx.OnlineRecognizer, "from_transducer", None)
            if not callable(factory):
                raise SpeechAdapterError("installed sherpa_onnx lacks OnlineRecognizer.from_transducer")
            self._recognizer = factory(
                tokens=str(tokens), encoder=str(encoder), decoder=str(decoder), joiner=str(joiner),
                num_threads=self._settings.sherpa_num_threads, sample_rate=16_000,
                feature_dim=80, decoding_method="greedy_search", provider="cpu",
            )
        except SpeechAdapterError:
            raise
        except Exception as exc:
            raise SpeechAdapterError(f"could not initialize sherpa ASR: {exc}") from exc
        return self._recognizer

    def _transcribe(self, pcm: bytes, sample_rate_hz: int) -> str:
        if sample_rate_hz != 16_000:
            raise SpeechAdapterError("sherpa ASR requires 16000 Hz PCM")
        recognizer = self._load()
        stream = recognizer.create_stream()
        stream.accept_waveform(sample_rate_hz, _pcm16_to_float(pcm))
        stream.input_finished()
        while recognizer.is_ready(stream):
            recognizer.decode_stream(stream)
        getter = getattr(recognizer, "get_result_all", None) or getattr(recognizer, "get_result", None)
        if not callable(getter):
            raise SpeechAdapterError("installed sherpa_onnx lacks a recognizer result method")
        result = getter(stream)
        return str(getattr(result, "text", result) or "").strip()

    async def transcribe(self, pcm: bytes, *, sample_rate_hz: int) -> str:
        return await asyncio.to_thread(self._transcribe, pcm, sample_rate_hz)


class SherpaOnnxSynthesizer:
    """Lazy VITS TTS adapter returning finite in-memory signed-16-bit PCM."""

    def __init__(self, settings: VoiceSettings):
        self._settings = settings
        self._tts: Any = None

    def _paths(self) -> tuple[Path, Path, Path]:
        if not self._settings.sherpa_tts_model_dir:
            raise SpeechAdapterError("sherpa TTS requires SHERPA_TTS_MODEL_DIR")
        root = Path(self._settings.sherpa_tts_model_dir)
        model = root / self._settings.sherpa_tts_model
        tokens = root / self._settings.sherpa_tts_tokens
        data = root / self._settings.sherpa_tts_data_dir
        missing = [path for path in (model, tokens, data) if not path.exists()]
        if missing:
            LOGGER.warning("sherpa TTS model files are missing: %s", ", ".join(map(str, missing)))
            raise SpeechAdapterError("sherpa TTS model files are not ready")
        return model, tokens, data

    def readiness(self) -> tuple[bool, str]:
        if importlib.util.find_spec("sherpa_onnx") is None:
            return False, "sherpa_onnx is not installed in the selected Python environment"
        try:
            self._paths()
        except SpeechAdapterError as exc:
            return False, str(exc)
        return True, "sherpa-onnx TTS package and configured model files are present; model not loaded"

    def _load(self) -> Any:
        if self._tts is not None:
            return self._tts
        ready, reason = self.readiness()
        if not ready:
            raise SpeechAdapterError(reason)
        import sherpa_onnx  # type: ignore

        model, tokens, data = self._paths()
        try:
            config = sherpa_onnx.OfflineTtsConfig(
                model=sherpa_onnx.OfflineTtsModelConfig(
                    vits=sherpa_onnx.OfflineTtsVitsModelConfig(
                        model=str(model), lexicon="", tokens=str(tokens), data_dir=str(data)
                    ),
                    num_threads=self._settings.sherpa_num_threads,
                    provider="cpu",
                ),
                rule_fsts="",
                max_num_sentences=1,
            )
            if not config.validate():
                raise SpeechAdapterError("sherpa_onnx rejected the configured TTS model")
            self._tts = sherpa_onnx.OfflineTts(config)
        except SpeechAdapterError:
            raise
        except Exception as exc:
            raise SpeechAdapterError(f"could not initialize sherpa TTS: {exc}") from exc
        return self._tts

    @staticmethod
    def _pcm16(samples: Any) -> bytes:
        values = array("h")
        for sample in samples:
            values.append(max(-32768, min(32767, int(round(float(sample) * 32767.0)))))
        if struct.pack("=h", 1) != struct.pack("<h", 1):
            values.byteswap()
        return values.tobytes()

    def _synthesize(self, text: str) -> SynthesizedAudio:
        generated = self._load().generate(text)
        rate = int(getattr(generated, "sample_rate", self._settings.playback_sample_rate_hz))
        if not 8_000 <= rate <= 48_000:
            raise SpeechAdapterError("sherpa TTS returned an unsupported sample rate")
        pcm = self._pcm16(getattr(generated, "samples", ()))
        if not pcm:
            raise SpeechAdapterError("sherpa TTS returned empty PCM")
        return SynthesizedAudio(pcm=pcm, sample_rate_hz=rate)

    async def synthesize(self, text: str) -> SynthesizedAudio:
        if not text.strip():
            raise SpeechAdapterError("TTS text must not be empty")
        return await asyncio.to_thread(self._synthesize, text.strip())


class EspeakNgSynthesizer:
    """System eSpeak NG synthesizer returning finite in-memory mono WAV PCM."""

    def __init__(self, settings: VoiceSettings):
        self._settings = settings

    def _executable(self) -> str:
        resolved = shutil.which(self._settings.espeak_ng_binary)
        if resolved is None:
            raise SpeechAdapterError(
                f"eSpeak NG executable was not found: {self._settings.espeak_ng_binary}"
            )
        return resolved

    def readiness(self) -> tuple[bool, str]:
        try:
            executable = self._executable()
            completed = subprocess.run(
                [executable, "--version"],
                capture_output=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired, SpeechAdapterError) as exc:
            return False, f"eSpeak NG is unavailable: {exc}"
        if completed.returncode != 0:
            return False, "eSpeak NG version probe failed"
        version = completed.stdout.decode("utf-8", errors="replace").strip().splitlines()
        return True, version[0] if version else "eSpeak NG is installed"

    def _synthesize(self, text: str) -> SynthesizedAudio:
        ready, reason = self.readiness()
        if not ready:
            raise SpeechAdapterError(reason)
        command = [
            self._executable(),
            "--stdout",
            "-v",
            self._settings.espeak_ng_voice,
            "-s",
            str(self._settings.espeak_ng_speed),
            text,
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SpeechAdapterError(f"eSpeak NG synthesis failed: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.decode("utf-8", errors="replace").strip()[:240]
            raise SpeechAdapterError(
                "eSpeak NG synthesis failed" + (f": {detail}" if detail else "")
            )
        if not completed.stdout or len(completed.stdout) > 8 * 1_024 * 1_024:
            raise SpeechAdapterError("eSpeak NG returned empty or oversized WAV data")
        try:
            with wave.open(io.BytesIO(completed.stdout), "rb") as source:
                channels = source.getnchannels()
                sample_width = source.getsampwidth()
                sample_rate = source.getframerate()
                pcm = source.readframes(source.getnframes())
        except (wave.Error, EOFError) as exc:
            raise SpeechAdapterError("eSpeak NG returned an invalid WAV stream") from exc
        if channels != 1 or sample_width != 2 or not 8_000 <= sample_rate <= 48_000:
            raise SpeechAdapterError("eSpeak NG WAV does not match the mono S16 contract")
        if not pcm:
            raise SpeechAdapterError("eSpeak NG returned empty PCM")
        return SynthesizedAudio(pcm=pcm, sample_rate_hz=sample_rate)

    async def synthesize(self, text: str) -> SynthesizedAudio:
        if not text.strip():
            raise SpeechAdapterError("TTS text must not be empty")
        return await asyncio.to_thread(self._synthesize, text.strip())


class LoopbackGatewayChat:
    """Bounded, fixed-model client for the existing loopback text gateway."""

    def __init__(self, settings: VoiceSettings):
        self._settings = settings
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(300.0), follow_redirects=False, trust_env=False)

    @property
    def configured(self) -> bool:
        return bool(self._settings.gateway_api_key)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(self, text: str) -> str:
        if not self.configured:
            raise SpeechAdapterError("loopback gateway API key is not configured")
        try:
            response = await self._client.post(
                self._settings.gateway_url + "/chat/completions",
                headers={"Authorization": "Bearer " + self._settings.gateway_api_key},
                json={"model": self._settings.public_model_id, "stream": False, "messages": [{"role": "user", "content": text}]},
            )
        except httpx.TimeoutException as exc:
            raise SpeechAdapterError("loopback gateway timed out") from exc
        except httpx.RequestError as exc:
            raise SpeechAdapterError("loopback gateway is unavailable") from exc
        if response.status_code != 200:
            raise SpeechAdapterError(f"loopback gateway returned HTTP {response.status_code}")
        try:
            content = response.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise SpeechAdapterError("loopback gateway returned an invalid completion") from exc
        if not isinstance(content, str) or not content.strip():
            raise SpeechAdapterError("loopback gateway returned an empty completion")
        return content.strip()


def build_speech_adapters(settings: VoiceSettings) -> tuple[SpeechRecognizer, SpeechSynthesizer]:
    """Select lazy speech backends without installing or downloading anything."""

    recognizer: SpeechRecognizer = (
        SherpaOnnxRecognizer(settings)
        if settings.speech_backend == "sherpa-onnx"
        else UnavailableRecognizer()
    )
    if settings.tts_backend == "sherpa-onnx":
        synthesizer: SpeechSynthesizer = SherpaOnnxSynthesizer(settings)
    elif settings.tts_backend == "espeak-ng":
        synthesizer = EspeakNgSynthesizer(settings)
    elif settings.tts_backend == "unavailable":
        synthesizer = UnavailableSynthesizer()
    elif settings.sherpa_tts_model_dir:
        synthesizer = SherpaOnnxSynthesizer(settings)
    elif shutil.which(settings.espeak_ng_binary) is not None:
        synthesizer = EspeakNgSynthesizer(settings)
    else:
        synthesizer = UnavailableSynthesizer()
    return recognizer, synthesizer


__all__ = [
    "EspeakNgSynthesizer", "LoopbackGatewayChat", "SherpaOnnxRecognizer", "SherpaOnnxSynthesizer",
    "SpeechAdapterError", "UnavailableRecognizer", "UnavailableSynthesizer",
    "adapter_readiness", "build_speech_adapters",
]
