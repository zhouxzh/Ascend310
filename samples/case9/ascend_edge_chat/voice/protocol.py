"""Stable internal contract for the Ascend Edge Chat voice agent.

The service accepts three modes only: ``disabled``, ``browser``, and ``board``.
Board mode owns a directly attached microphone/speaker. Browser mode never
opens board audio and only processes bounded in-memory 16 kHz WAV uploads.
Neither mode accepts model paths, ALSA strings, gateway URLs, shell commands,
or raw board PCM through HTTP.

UI contract:

* ``GET /health`` -> readiness/mode/state.
* ``GET /api/config`` -> modes, agent-enumerated input/output descriptors and
  selected IDs; descriptors contain stable IDs, not raw ALSA command strings.
* ``PUT /api/config`` -> only ``mode``, ``input_id``, ``output_id`` and
  ``auto_listen``.  In board mode, ``auto_listen`` controls VAD capture.
* ``GET /api/status`` -> active/latest sanitized turn.
* ``POST /v1/voice/browser/transcribe`` -> bounded 16 kHz mono WAV only in
  browser mode; returns recognized text and never writes a file.
* ``POST /v1/voice/browser/synthesize`` -> finite WAV response only in browser
  mode; board mode always rejects it and never exports board PCM.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


class VoiceMode(str, Enum):
    """Mutually exclusive audio ownership modes."""

    DISABLED = "disabled"
    BROWSER = "browser"
    BOARD = "board"


class VoiceAgentState(str, Enum):
    """Service-level readiness state independent of a single voice turn."""

    IDLE = "idle"
    BUSY = "busy"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"


class VoiceTurnState(str, Enum):
    """A monotonic state for a board-side voice turn."""

    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_TERMINAL_STATES = {VoiceTurnState.COMPLETED, VoiceTurnState.FAILED, VoiceTurnState.CANCELLED}


@dataclass(frozen=True)
class VoiceDevice:
    """Public device descriptor with a stable agent-generated ID.

    ``backend_id`` is internal-only and must never be included in HTTP output.
    """

    id: str
    label: str
    direction: str
    backend_id: str

    def as_dict(self) -> dict[str, str]:
        return {"id": self.id, "label": self.label, "direction": self.direction}


@dataclass
class VoiceTurn:
    """Sanitized state visible to an internal status client."""

    id: str
    state: VoiceTurnState
    started_at_ms: int
    updated_at_ms: int
    transcript: str = ""
    reply: str = ""
    error: str = ""

    @property
    def terminal(self) -> bool:
        return self.state in _TERMINAL_STATES

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "id": self.id,
            "state": self.state.value,
            "terminal": self.terminal,
            "started_at_ms": self.started_at_ms,
            "updated_at_ms": self.updated_at_ms,
        }
        if self.transcript:
            payload["transcript"] = self.transcript
        if self.reply:
            payload["reply"] = self.reply
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass(frozen=True)
class SynthesizedAudio:
    """PCM returned from a TTS adapter for local playback or WAV encoding."""

    pcm: bytes
    sample_rate_hz: int
    channels: int = 1
    sample_width_bytes: int = 2


class VoiceCapture(Protocol):
    async def capture_utterance(self, *, max_duration_seconds: float) -> bytes:
        """Capture one signed-16-bit mono board-owned utterance."""


class VoicePlayback(Protocol):
    async def play_pcm(self, audio: SynthesizedAudio) -> None:
        """Play one finite PCM payload through the selected board output."""


class SpeechRecognizer(Protocol):
    async def transcribe(self, pcm: bytes, *, sample_rate_hz: int) -> str:
        """Return normalized user text for one finished utterance."""


class SpeechSynthesizer(Protocol):
    async def synthesize(self, text: str) -> SynthesizedAudio:
        """Return finite PCM for one assistant reply."""


class LoopbackChat(Protocol):
    async def complete(self, text: str) -> str:
        """Return one bounded assistant reply for recognized text."""


__all__ = [
    "LoopbackChat",
    "SpeechRecognizer",
    "SpeechSynthesizer",
    "SynthesizedAudio",
    "VoiceAgentState",
    "VoiceCapture",
    "VoiceDevice",
    "VoiceMode",
    "VoicePlayback",
    "VoiceTurn",
    "VoiceTurnState",
]
