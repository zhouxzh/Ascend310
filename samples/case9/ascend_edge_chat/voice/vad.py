"""Deterministic energy VAD for the first board-side half-duplex agent.

This is intentionally an energy gate, not a claimed speech model.  It converts
fixed-size signed-16-bit PCM frames into a small state machine: wait for several
voiced frames, retain the utterance, then end after configured silence.  A later
VAD model may implement the same ``feed`` contract without changing agent APIs.
"""

from __future__ import annotations

import math
import sys
from array import array
from dataclasses import dataclass
from enum import Enum


class VadState(str, Enum):
    """Internal state of one VAD-bounded capture."""

    WAITING = "waiting"
    SPEECH = "speech"
    COMPLETE = "complete"


@dataclass(frozen=True)
class VadSettings:
    """PCM and timing constraints for deterministic VAD."""

    sample_rate_hz: int = 16_000
    frame_ms: int = 20
    speech_rms_threshold: float = 180.0
    start_speech_frames: int = 3
    stop_silence_ms: int = 800
    adaptive: bool = False
    noise_floor_initial_rms: float = 80.0
    noise_floor_multiplier: float = 3.0
    noise_floor_decay: float = 0.95
    noise_floor_calibration_frames: int = 10
    max_duration_seconds: float = 30.0

    def __post_init__(self) -> None:
        if self.sample_rate_hz != 16_000:
            raise ValueError("the initial voice VAD contract uses 16000 Hz PCM")
        if self.frame_ms not in {10, 20, 30}:
            raise ValueError("frame_ms must be 10, 20, or 30")
        if self.speech_rms_threshold <= 0:
            raise ValueError("speech_rms_threshold must be positive")
        if self.start_speech_frames < 1:
            raise ValueError("start_speech_frames must be positive")
        if self.stop_silence_ms < self.frame_ms:
            raise ValueError("stop_silence_ms must be at least one frame")
        if not 1.0 <= self.noise_floor_initial_rms <= 2_000.0:
            raise ValueError("noise_floor_initial_rms must be between 1 and 2000")
        if not 1.2 <= self.noise_floor_multiplier <= 10.0:
            raise ValueError("noise_floor_multiplier must be between 1.2 and 10")
        if not 0.5 <= self.noise_floor_decay <= 0.999:
            raise ValueError("noise_floor_decay must be between 0.5 and 0.999")
        if not 1 <= self.noise_floor_calibration_frames <= 100:
            raise ValueError("noise_floor_calibration_frames must be between 1 and 100")
        if not 1.0 <= self.max_duration_seconds <= 60.0:
            raise ValueError("max_duration_seconds must be between 1 and 60")

    @property
    def frame_bytes(self) -> int:
        return self.sample_rate_hz * self.frame_ms // 1_000 * 2

    @property
    def stop_silence_frames(self) -> int:
        return max(1, math.ceil(self.stop_silence_ms / self.frame_ms))

    @property
    def max_frames(self) -> int:
        return math.ceil(self.max_duration_seconds * 1_000 / self.frame_ms)


@dataclass(frozen=True)
class VadDecision:
    """Result from one PCM frame, consumed by the capture adapter."""

    state: VadState
    rms: float
    voiced: bool
    started: bool
    complete: bool
    retain: bool
    threshold: float
    noise_floor: float


def pcm_rms(frame: bytes) -> float:
    """Return RMS amplitude for little-endian signed-16-bit PCM bytes."""

    if not frame or len(frame) % 2:
        raise ValueError("PCM frame must contain a non-empty whole number of signed-16-bit samples")
    values = array("h")
    values.frombytes(frame)
    if sys.byteorder != "little":
        values.byteswap()
    return math.sqrt(sum(sample * sample for sample in values) / len(values))


class EnergyVad:
    """Adaptive frame-energy VAD with speech onset, silence and duration bounds."""

    def __init__(self, settings: VadSettings | None = None):
        self.settings = settings or VadSettings()
        self.reset()

    @property
    def state(self) -> VadState:
        """Return the current VAD state."""

        return self._state

    def reset(self) -> None:
        """Forget the current utterance without retaining prior frames."""

        self._state = VadState.WAITING
        self._total_frames = 0
        self._voiced_frames = 0
        self._silent_frames = 0
        self._noise_floor = self.settings.noise_floor_initial_rms
        self._calibration_sum = 0.0
        self._calibration_count = 0

    def feed(self, frame: bytes) -> VadDecision:
        """Consume exactly one configured PCM frame and return a transition."""

        if len(frame) != self.settings.frame_bytes:
            raise ValueError(
                f"PCM frame must be exactly {self.settings.frame_bytes} bytes, got {len(frame)}"
            )
        if self._state == VadState.COMPLETE:
            raise RuntimeError("reset VAD before feeding a completed utterance")

        self._total_frames += 1
        rms = pcm_rms(frame)
        threshold = self.settings.speech_rms_threshold
        calibrating = False
        if self.settings.adaptive:
            if self._state == VadState.WAITING and (
                self._calibration_count < self.settings.noise_floor_calibration_frames
            ):
                calibrating = True
                self._calibration_count += 1
                self._calibration_sum += rms
                self._noise_floor = self._calibration_sum / self._calibration_count
            threshold = max(
                self.settings.speech_rms_threshold,
                self._noise_floor * self.settings.noise_floor_multiplier,
            )
            if self._state == VadState.SPEECH:
                # Keep short pauses inside one utterance, but make the release
                # threshold lower than onset so normal speech decay is retained.
                threshold = max(
                    self.settings.speech_rms_threshold * 0.6,
                    self._noise_floor * self.settings.noise_floor_multiplier * 0.55,
                )
        voiced = False if calibrating else rms >= threshold
        started = False

        if self._state == VadState.WAITING:
            if not voiced and self.settings.adaptive and not calibrating:
                decay = self.settings.noise_floor_decay
                self._noise_floor = decay * self._noise_floor + (1.0 - decay) * rms
                threshold = max(
                    self.settings.speech_rms_threshold,
                    self._noise_floor * self.settings.noise_floor_multiplier,
                )
            self._voiced_frames = self._voiced_frames + 1 if voiced else 0
            if self._voiced_frames >= self.settings.start_speech_frames:
                self._state = VadState.SPEECH
                self._silent_frames = 0
                started = True
        else:
            self._silent_frames = 0 if voiced else self._silent_frames + 1
            if (
                self._silent_frames >= self.settings.stop_silence_frames
                or self._total_frames >= self.settings.max_frames
            ):
                self._state = VadState.COMPLETE

        # Silence is also bounded. The capture adapter reports an empty utterance
        # to its caller, then releases the physical device instead of recording
        # forever while no-one speaks.
        if self._total_frames >= self.settings.max_frames:
            self._state = VadState.COMPLETE
        return VadDecision(
            state=self._state,
            rms=rms,
            voiced=voiced,
            started=started,
            complete=self._state == VadState.COMPLETE,
            retain=self._state in {VadState.SPEECH, VadState.COMPLETE},
            threshold=threshold,
            noise_floor=self._noise_floor,
        )


__all__ = ["EnergyVad", "VadDecision", "VadSettings", "VadState", "pcm_rms"]
