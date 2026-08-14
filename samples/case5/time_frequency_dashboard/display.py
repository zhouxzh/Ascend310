"""Display-only spectral conversions and color-scale state.

The OM model returns linear mean-square band energy.  This module deliberately
keeps its dB conversion outside the model and outside the session's raw result
record so both the numerical output and the display convention stay explicit.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import numpy as np


DB_REFERENCE_ENERGY_VOLT_SQUARED = 1.0
DB_FLOOR = -120.0
AUTO_SCALE_ROWS = 20
AUTO_SCALE_MIN_SPAN_DB = 40.0


def _finite_float(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not np.isfinite(numeric):
        raise ValueError(f"{field} must be a finite number")
    return numeric


def band_energy_to_db(
    energy: np.ndarray,
    *,
    reference_energy_v_squared: float = DB_REFERENCE_ENERGY_VOLT_SQUARED,
    floor_db: float = DB_FLOOR,
) -> np.ndarray:
    """Convert linear band energy to dB re ``reference_energy_v_squared``.

    The returned array is a fresh ``float32`` value.  It never changes the raw
    NPU output that is persisted in ``analysis.jsonl``.
    """

    reference = _finite_float(
        reference_energy_v_squared, "reference_energy_v_squared"
    )
    if reference <= 0:
        raise ValueError("reference_energy_v_squared must be positive")
    floor = _finite_float(floor_db, "floor_db")
    values = np.asarray(energy, dtype=np.float32)
    if not np.all(np.isfinite(values)):
        raise ValueError("energy must be finite")
    try:
        floor_energy = reference * math.pow(10.0, floor / 10.0)
    except OverflowError as exc:
        raise ValueError("floor_db produces an unusable energy floor") from exc
    if (
        not math.isfinite(floor_energy)
        or floor_energy <= 0.0
        or floor_energy > float(np.finfo(np.float32).max)
    ):
        raise ValueError("floor_db produces an unusable energy floor")
    # Work in float64 for the ratio so a valid, very small positive reference
    # cannot turn an otherwise finite float32 energy into an infinite ratio.
    clipped = np.maximum(values.astype(np.float64), floor_energy)
    result = 10.0 * np.log10(clipped / reference)
    if not np.all(np.isfinite(result)):
        raise ValueError("energy/reference conversion produced non-finite dB values")
    return result.astype(np.float32, copy=False)


@dataclass(frozen=True)
class ColorScaleState:
    """The user-visible state of one channel's waterfall color scale."""

    low_db: float
    high_db: float
    auto_enabled: bool
    locked: bool
    observed_rows: int


class AutoColorScale:
    """Estimate initial waterfall levels, then keep them stable for comparison."""

    def __init__(
        self,
        *,
        calibration_rows: int = AUTO_SCALE_ROWS,
        floor_db: float = DB_FLOOR,
        minimum_span_db: float = AUTO_SCALE_MIN_SPAN_DB,
    ) -> None:
        if (
            isinstance(calibration_rows, bool)
            or not isinstance(calibration_rows, int)
            or calibration_rows <= 0
        ):
            raise ValueError("calibration_rows must be positive")
        floor = _finite_float(floor_db, "floor_db")
        minimum_span = _finite_float(minimum_span_db, "minimum_span_db")
        if minimum_span <= 0:
            raise ValueError("minimum_span_db must be positive")
        self.calibration_rows = int(calibration_rows)
        self.floor_db = floor
        self.minimum_span_db = minimum_span
        self._rows: list[np.ndarray] = []
        self._low_db = self.floor_db
        self._high_db = self.floor_db + self.minimum_span_db
        self._auto_enabled = True
        self._locked = False

    @property
    def state(self) -> ColorScaleState:
        return ColorScaleState(
            low_db=self._low_db,
            high_db=self._high_db,
            auto_enabled=self._auto_enabled,
            locked=self._locked,
            observed_rows=len(self._rows),
        )

    def reset_auto(self) -> ColorScaleState:
        self._rows.clear()
        self._low_db = self.floor_db
        self._high_db = self.floor_db + self.minimum_span_db
        self._auto_enabled = True
        self._locked = False
        return self.state

    def set_manual(self, low_db: float, high_db: float) -> ColorScaleState:
        low = _finite_float(low_db, "color-scale low_db")
        high = _finite_float(high_db, "color-scale high_db")
        if high <= low:
            raise ValueError("color-scale high_db must be greater than low_db")
        self._low_db = low
        self._high_db = high
        self._auto_enabled = False
        self._locked = True
        return self.state

    def observe(self, row_db: np.ndarray) -> ColorScaleState:
        """Update the provisional range until the configured row count is reached."""

        if not self._auto_enabled or self._locked:
            return self.state
        row = np.asarray(row_db, dtype=np.float32).reshape(-1)
        finite = row[np.isfinite(row)]
        if finite.size:
            self._rows.append(finite.copy())
        else:
            self._rows.append(np.empty(0, dtype=np.float32))
        combined = np.concatenate([item for item in self._rows if item.size]) if any(
            item.size for item in self._rows
        ) else np.asarray([self.floor_db], dtype=np.float32)
        low, high = np.percentile(combined, [2.0, 98.0])
        high = max(float(high), self.floor_db + self.minimum_span_db)
        low = max(self.floor_db, min(float(low), high - self.minimum_span_db))
        self._low_db, self._high_db = low, high
        if len(self._rows) >= self.calibration_rows:
            self._locked = True
        return self.state
