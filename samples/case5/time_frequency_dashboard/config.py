"""Stable configuration for the first Case 5 hardware milestone."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from pathlib import Path
from typing import Dict


@dataclass(frozen=True)
class LittleBeeConversion:
    """Declared Little Bee B1 output-to-current conversion parameters."""

    mode: str = "cyan"
    sensitivity_volts_per_amp: float = 1.0
    turns: int = 1
    bandwidth_hz: float = 1_000_000.0
    conversion_version: str = "declared-cyan-1.0V-per-A-1turn"

    def to_current(self, voltage):
        self.validate()
        return voltage / (self.sensitivity_volts_per_amp * self.turns)

    def validate(self) -> None:
        if not isinstance(self.turns, int) or isinstance(self.turns, bool) or self.turns <= 0:
            raise ValueError("Little Bee turns must be a positive integer")
        if not math.isfinite(float(self.sensitivity_volts_per_amp)) or self.sensitivity_volts_per_amp <= 0:
            raise ValueError("Little Bee sensitivity must be finite and positive")
        if not math.isfinite(float(self.bandwidth_hz)) or self.bandwidth_hz <= 0:
            raise ValueError("Little Bee bandwidth must be finite and positive")

    def metadata(self) -> Dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class Case5Config:
    """Fixed-shape first release configuration.

    libsigrok produces a continuous two-channel stream. The acquisition bridge
    emits bounded frames and the processing layer assembles fixed NPU windows.
    """

    sample_rate_hz: float = 1_000_000.0
    analysis_samples: int = 10_000
    channels: int = 2
    spectrum_max_frequency_hz: float = 20_000.0
    analysis_queue_capacity: int = 2
    result_queue_capacity: int = 8
    sigrok_callback_msec: int = 40
    raw_chunk_bytes: int = 64 * 1024 * 1024
    max_session_bytes: int = 1 * 1024 * 1024 * 1024
    waterfall_rows: int = 180
    session_root: Path = Path.home() / "case5_sessions"
    current_conversion: LittleBeeConversion = LittleBeeConversion()
    ch1_volts_per_division: float = 1.0
    ch2_volts_per_division: float = 0.25
    ch1_probe_ratio: float = 1.0
    ch2_probe_ratio: float = 1.0

    @property
    def spectrum_resolution_hz(self) -> float:
        return self.sample_rate_hz / self.analysis_samples

    @property
    def spectrum_bins(self) -> int:
        return int(self.spectrum_max_frequency_hz // self.spectrum_resolution_hz) + 1

    def validate(self) -> None:
        if not math.isfinite(float(self.sample_rate_hz)) or self.sample_rate_hz <= 0:
            raise ValueError("sample_rate_hz must be finite and positive")
        if (
            not isinstance(self.analysis_samples, int)
            or isinstance(self.analysis_samples, bool)
            or self.analysis_samples <= 1
        ):
            raise ValueError("analysis_samples must be an integer greater than one")
        if not isinstance(self.channels, int) or isinstance(self.channels, bool) or self.channels != 2:
            raise ValueError("Case 5 first release requires two channels")
        if (
            not math.isfinite(float(self.spectrum_max_frequency_hz))
            or not 0.0 < self.spectrum_max_frequency_hz <= self.sample_rate_hz / 2.0
        ):
            raise ValueError("spectrum_max_frequency_hz must be in (0, Nyquist]")
        for name, value in (
            ("analysis_queue_capacity", self.analysis_queue_capacity),
            ("result_queue_capacity", self.result_queue_capacity),
            ("sigrok_callback_msec", self.sigrok_callback_msec),
            ("raw_chunk_bytes", self.raw_chunk_bytes),
            ("max_session_bytes", self.max_session_bytes),
            ("waterfall_rows", self.waterfall_rows),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if not 20 <= self.waterfall_rows <= 500:
            raise ValueError("waterfall_rows must be between 20 and 500")
        if self.raw_chunk_bytes < 64:
            raise ValueError("raw_chunk_bytes must be at least 64 bytes")
        if self.max_session_bytes < self.raw_chunk_bytes:
            raise ValueError("max_session_bytes must be at least raw_chunk_bytes")
        supported_scales = {0.1, 0.25, 0.5, 1.0}
        for channel, scale in enumerate((self.ch1_volts_per_division, self.ch2_volts_per_division), start=1):
            if not math.isfinite(float(scale)) or scale not in supported_scales:
                raise ValueError(f"CH{channel} volts/div must be one of {sorted(supported_scales)}")
        if (
            not math.isfinite(float(self.ch1_probe_ratio))
            or not math.isfinite(float(self.ch2_probe_ratio))
            or self.ch1_probe_ratio <= 0
            or self.ch2_probe_ratio <= 0
        ):
            raise ValueError("probe ratios must be finite and positive")
        if not 10 <= self.sigrok_callback_msec <= 1000:
            raise ValueError("sigrok_callback_msec must be between 10 and 1000")
        self.current_conversion.validate()
