"""Bounded PCM16 audio conditioning before speech recognition."""

from __future__ import annotations

import math
import statistics
import sys
from array import array
from dataclasses import dataclass


@dataclass(frozen=True)
class AgcSettings:
    """Automatic gain limits for one captured utterance."""

    enabled: bool = True
    target_rms: float = 2_400.0
    min_gain: float = 0.5
    max_gain: float = 16.0
    frame_ms: int = 20
    sample_rate_hz: int = 16_000

    def __post_init__(self) -> None:
        if not 100.0 <= self.target_rms <= 12_000.0:
            raise ValueError("target_rms must be between 100 and 12000")
        if not 0.1 <= self.min_gain <= 1.0:
            raise ValueError("min_gain must be between 0.1 and 1")
        if not 1.0 <= self.max_gain <= 32.0:
            raise ValueError("max_gain must be between 1 and 32")
        if self.frame_ms not in {10, 20, 30}:
            raise ValueError("frame_ms must be 10, 20, or 30")
        if self.sample_rate_hz != 16_000:
            raise ValueError("the AGC contract uses 16000 Hz PCM")


@dataclass(frozen=True)
class AgcResult:
    """Conditioned PCM and the measured level decisions that produced it."""

    pcm: bytes
    input_rms: float
    active_rms: float
    gain: float
    output_rms: float


def _rms(values: array[int]) -> float:
    if not values:
        return 0.0
    return math.sqrt(sum(sample * sample for sample in values) / len(values))


def apply_pcm16_agc(pcm: bytes, settings: AgcSettings = AgcSettings()) -> AgcResult:
    """Apply bounded whole-utterance AGC while preserving signed-16-bit PCM.

    Gain is estimated from active frames above an adaptive noise floor. Silence
    is never amplified merely because the whole recording is quiet.
    """

    if not pcm or len(pcm) % 2:
        raise ValueError("PCM must contain a non-empty whole number of int16 samples")
    values = array("h")
    values.frombytes(pcm)
    if sys.byteorder != "little":
        values.byteswap()
    overall_rms = _rms(values)
    if not settings.enabled or overall_rms < 1.0:
        return AgcResult(pcm, overall_rms, overall_rms, 1.0, overall_rms)

    frame_samples = settings.sample_rate_hz * settings.frame_ms // 1000
    frame_levels = [
        _rms(values[index : index + frame_samples])
        for index in range(0, len(values), frame_samples)
        if values[index : index + frame_samples]
    ]
    ordered = sorted(frame_levels)
    noise_floor = ordered[max(0, int(len(ordered) * 0.2) - 1)]
    active_threshold = max(60.0, noise_floor * 2.0)
    active_levels = [level for level in frame_levels if level >= active_threshold]
    active_rms = statistics.median(active_levels) if active_levels else overall_rms
    if active_rms < 1.0:
        return AgcResult(pcm, overall_rms, active_rms, 1.0, overall_rms)
    gain = max(settings.min_gain, min(settings.max_gain, settings.target_rms / active_rms))

    conditioned = array("h")
    for sample in values:
        conditioned.append(max(-32768, min(32767, int(round(sample * gain)))))
    if sys.byteorder != "little":
        conditioned.byteswap()
    output = conditioned.tobytes()
    output_values = array("h")
    output_values.frombytes(output)
    if sys.byteorder != "little":
        output_values.byteswap()
    return AgcResult(
        pcm=output,
        input_rms=overall_rms,
        active_rms=active_rms,
        gain=gain,
        output_rms=_rms(output_values),
    )


__all__ = ["AgcResult", "AgcSettings", "apply_pcm16_agc"]
