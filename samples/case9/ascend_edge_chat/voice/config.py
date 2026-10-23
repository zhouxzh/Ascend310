"""Validated configuration for the independent board voice agent.

Configuration is process-owned.  HTTP callers cannot choose ALSA paths, model
paths, gateway hosts, shell commands, or audio-size limits.  Device selection is
performed later through IDs enumerated by the agent itself.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Mapping, Optional
from urllib.parse import urlparse


VOICE_ENV_PREFIX = "ASCEND_EDGE_CHAT_VOICE_"
_LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class VoiceConfigurationError(ValueError):
    """Raised when a voice-agent process would start with unsafe settings."""


def _integer(source: Mapping[str, str], name: str, default: int, minimum: int, maximum: int) -> int:
    raw = source.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise VoiceConfigurationError(f"{VOICE_ENV_PREFIX}{name} must be an integer") from exc
    if not minimum <= value <= maximum:
        raise VoiceConfigurationError(
            f"{VOICE_ENV_PREFIX}{name} must be between {minimum} and {maximum}"
        )
    return value


def _decimal(source: Mapping[str, str], name: str, default: float, minimum: float, maximum: float) -> float:
    raw = source.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise VoiceConfigurationError(f"{VOICE_ENV_PREFIX}{name} must be a number") from exc
    if not math.isfinite(value) or not minimum <= value <= maximum:
        raise VoiceConfigurationError(
            f"{VOICE_ENV_PREFIX}{name} must be finite and between {minimum} and {maximum}"
        )
    return value


def _loopback_url(source: Mapping[str, str]) -> str:
    value = source.get("GATEWAY_URL", "http://127.0.0.1:7861/v1").strip().rstrip("/")
    try:
        parsed = urlparse(value)
        port = parsed.port
    except ValueError as exc:
        raise VoiceConfigurationError(f"{VOICE_ENV_PREFIX}GATEWAY_URL is malformed") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in _LOOPBACK_HOSTS
        or parsed.path.rstrip("/") != "/v1"
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or port is None
    ):
        raise VoiceConfigurationError(
            f"{VOICE_ENV_PREFIX}GATEWAY_URL must be a loopback http URL ending in /v1"
        )
    return value


@dataclass(frozen=True)
class VoiceSettings:
    """Static process limits for one loopback-only voice agent."""

    host: str = "127.0.0.1"
    port: int = 7862
    initial_mode: str = "disabled"
    capture_sample_rate_hz: int = 16_000
    # 0 uses stereo capture, the most common UVC microphone contract. VAD/ASR
    # still consume downmixed mono PCM; set 1 only after device enumeration.
    capture_device_channels: int = 0
    playback_sample_rate_hz: int = 22_050
    channels: int = 1
    sample_width_bytes: int = 2
    frame_ms: int = 20
    speech_rms_threshold: float = 450.0
    start_speech_frames: int = 3
    stop_silence_ms: int = 800
    max_capture_seconds: float = 30.0
    max_text_characters: int = 1_200
    max_browser_audio_bytes: int = 1_048_576
    speech_backend: str = "unavailable"
    tts_backend: str = "auto"
    sherpa_asr_model_dir: str = ""
    sherpa_asr_encoder: str = ""
    sherpa_asr_decoder: str = ""
    sherpa_asr_joiner: str = ""
    sherpa_asr_tokens: str = ""
    sherpa_tts_model_dir: str = ""
    sherpa_tts_model: str = "zh_CN-huayan-medium.onnx"
    sherpa_tts_tokens: str = "tokens.txt"
    sherpa_tts_data_dir: str = "espeak-ng-data"
    sherpa_num_threads: int = 2
    espeak_ng_binary: str = "espeak-ng"
    espeak_ng_voice: str = "cmn"
    espeak_ng_speed: int = 160
    gateway_url: str = "http://127.0.0.1:7861/v1"
    gateway_api_key: str = ""
    public_model_id: str = "ascend-edge-chat"

    def __post_init__(self) -> None:
        if self.host not in _LOOPBACK_HOSTS:
            raise VoiceConfigurationError("voice-agent host must be loopback")
        if not 1 <= self.port <= 65_535:
            raise VoiceConfigurationError("voice-agent port must be between 1 and 65535")
        if self.initial_mode not in {"disabled", "browser", "board"}:
            raise VoiceConfigurationError("initial_mode must be disabled, browser, or board")
        if self.capture_sample_rate_hz != 16_000:
            raise VoiceConfigurationError("capture_sample_rate_hz is fixed at 16000 for the initial ASR contract")
        if self.capture_device_channels not in {0, 1, 2}:
            raise VoiceConfigurationError("capture_device_channels must be 0 (auto), 1, or 2")
        if not 8_000 <= self.playback_sample_rate_hz <= 48_000:
            raise VoiceConfigurationError("playback_sample_rate_hz must be between 8000 and 48000")
        if self.channels != 1 or self.sample_width_bytes != 2:
            raise VoiceConfigurationError("the initial agent only accepts mono signed-16-bit PCM")
        if self.frame_ms not in {10, 20, 30}:
            raise VoiceConfigurationError("frame_ms must be 10, 20, or 30")
        if self.speech_rms_threshold <= 0:
            raise VoiceConfigurationError("speech_rms_threshold must be positive")
        if self.start_speech_frames < 1:
            raise VoiceConfigurationError("start_speech_frames must be positive")
        if not 200 <= self.stop_silence_ms <= 5_000:
            raise VoiceConfigurationError("stop_silence_ms must be between 200 and 5000")
        if not 1.0 <= self.max_capture_seconds <= 60.0:
            raise VoiceConfigurationError("max_capture_seconds must be between 1 and 60")
        if not 1 <= self.max_text_characters <= 4_000:
            raise VoiceConfigurationError("max_text_characters must be between 1 and 4000")
        if not 32_000 <= self.max_browser_audio_bytes <= 8 * 1_024 * 1_024:
            raise VoiceConfigurationError("max_browser_audio_bytes must be between 32000 and 8388608")
        if self.speech_backend not in {"unavailable", "sherpa-onnx"}:
            raise VoiceConfigurationError("speech_backend must be unavailable or sherpa-onnx")
        if self.tts_backend not in {"auto", "unavailable", "sherpa-onnx", "espeak-ng"}:
            raise VoiceConfigurationError("tts_backend must be auto, unavailable, sherpa-onnx, or espeak-ng")
        if self.sherpa_num_threads < 1 or self.sherpa_num_threads > 16:
            raise VoiceConfigurationError("sherpa_num_threads must be between 1 and 16")
        if not 80 <= self.espeak_ng_speed <= 300:
            raise VoiceConfigurationError("espeak_ng_speed must be between 80 and 300")
        if not self.espeak_ng_binary.strip():
            raise VoiceConfigurationError("espeak_ng_binary must not be empty")
        if not self.espeak_ng_voice.strip():
            raise VoiceConfigurationError("espeak_ng_voice must not be empty")
        if self.public_model_id != "ascend-edge-chat":
            raise VoiceConfigurationError("public_model_id must remain ascend-edge-chat")
        _loopback_url({"GATEWAY_URL": self.gateway_url})

    @property
    def capture_frame_bytes(self) -> int:
        """Exact PCM byte count consumed by one VAD frame."""

        return self.capture_sample_rate_hz * self.channels * self.sample_width_bytes * self.frame_ms // 1_000

    @property
    def max_capture_bytes(self) -> int:
        """Upper bound retained in memory for one board-side utterance."""

        return int(self.capture_sample_rate_hz * self.channels * self.sample_width_bytes * self.max_capture_seconds)

    @classmethod
    def from_environ(cls, environ: Optional[Mapping[str, str]] = None) -> "VoiceSettings":
        """Read only the voice namespace; values remain process-owned."""

        source = os.environ if environ is None else environ
        values = {
            key[len(VOICE_ENV_PREFIX):]: value
            for key, value in source.items()
            if key.startswith(VOICE_ENV_PREFIX)
        }
        return cls(
            host=values.get("HOST", "127.0.0.1").strip(),
            port=_integer(values, "PORT", 7862, 1, 65_535),
            initial_mode=values.get("MODE", "disabled").strip().lower() or "disabled",
            capture_sample_rate_hz=_integer(values, "CAPTURE_SAMPLE_RATE_HZ", 16_000, 8_000, 48_000),
            capture_device_channels=_integer(values, "CAPTURE_DEVICE_CHANNELS", 0, 0, 2),
            playback_sample_rate_hz=_integer(values, "PLAYBACK_SAMPLE_RATE_HZ", 22_050, 8_000, 48_000),
            frame_ms=_integer(values, "FRAME_MS", 20, 10, 30),
            speech_rms_threshold=_decimal(values, "SPEECH_RMS_THRESHOLD", 450.0, 1.0, 32_767.0),
            start_speech_frames=_integer(values, "START_SPEECH_FRAMES", 3, 1, 50),
            stop_silence_ms=_integer(values, "STOP_SILENCE_MS", 800, 200, 5_000),
            max_capture_seconds=_decimal(values, "MAX_CAPTURE_SECONDS", 30.0, 1.0, 60.0),
            max_text_characters=_integer(values, "MAX_TEXT_CHARACTERS", 1_200, 1, 4_000),
            max_browser_audio_bytes=_integer(values, "MAX_BROWSER_AUDIO_BYTES", 1_048_576, 32_000, 8 * 1_024 * 1_024),
            speech_backend=values.get("SPEECH_BACKEND", "unavailable").strip().lower(),
            tts_backend=values.get("TTS_BACKEND", "auto").strip().lower(),
            sherpa_asr_model_dir=values.get("SHERPA_ASR_MODEL_DIR", "").strip(),
            sherpa_asr_encoder=values.get("SHERPA_ASR_ENCODER", "").strip(),
            sherpa_asr_decoder=values.get("SHERPA_ASR_DECODER", "").strip(),
            sherpa_asr_joiner=values.get("SHERPA_ASR_JOINER", "").strip(),
            sherpa_asr_tokens=values.get("SHERPA_ASR_TOKENS", "").strip(),
            sherpa_tts_model_dir=values.get("SHERPA_TTS_MODEL_DIR", "").strip(),
            sherpa_tts_model=values.get("SHERPA_TTS_MODEL", "zh_CN-huayan-medium.onnx").strip(),
            sherpa_tts_tokens=values.get("SHERPA_TTS_TOKENS", "tokens.txt").strip(),
            sherpa_tts_data_dir=values.get("SHERPA_TTS_DATA_DIR", "espeak-ng-data").strip(),
            sherpa_num_threads=_integer(values, "SHERPA_NUM_THREADS", 2, 1, 16),
            espeak_ng_binary=values.get("ESPEAK_NG_BINARY", "espeak-ng").strip() or "espeak-ng",
            espeak_ng_voice=values.get("ESPEAK_NG_VOICE", "cmn").strip(),
            espeak_ng_speed=_integer(values, "ESPEAK_NG_SPEED", 160, 80, 300),
            gateway_url=_loopback_url(values),
            gateway_api_key=values.get("GATEWAY_API_KEY", "").strip(),
            public_model_id=values.get("PUBLIC_MODEL_ID", "ascend-edge-chat").strip(),
        )


__all__ = ["VOICE_ENV_PREFIX", "VoiceConfigurationError", "VoiceSettings"]
