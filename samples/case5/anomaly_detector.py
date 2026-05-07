"""
Case 5: Statistical anomaly detection on motor sensor streams.

Sliding-window 3-sigma outlier detection + linear trend forecasting
for temperature, current, and RPM. Pure NumPy — no model needed.
"""

from collections import deque

import numpy as np

from config import (
    CURRENT_WARN_THRESHOLD,
    MOTOR_NAMES,
    NUM_MOTORS,
    RPM_MAX,
    RPM_MIN,
    SIGMA_THRESHOLD,
    TEMP_WARN_THRESHOLD,
    WINDOW_SIZE,
)


class AnomalyDetector:
    """Per-motor anomaly detection on temperature, current, RPM."""

    def __init__(self):
        # One deque per motor per parameter
        self._history = {}
        for i in range(NUM_MOTORS):
            self._history[i] = {
                "temperature": deque(maxlen=WINDOW_SIZE),
                "current": deque(maxlen=WINDOW_SIZE),
                "rpm": deque(maxlen=WINDOW_SIZE),
            }
        print(f"[AnomalyDetector] Window size: {WINDOW_SIZE}, "
              f"sigma threshold: {SIGMA_THRESHOLD}")

    # ------------------------------------------------------------------
    # Feed data
    # ------------------------------------------------------------------

    def update(self, motors):
        """Feed a new motor data frame into the detector.

        Args:
            motors: list of motor dicts from SensorReader.read()

        Returns:
            list of anomaly dicts: [{motor_id, name, parameter, value,
                                     mean, std, z_score, level}, ...]
        """
        anomalies = []

        for m in motors:
            mid = m["motor_id"]
            if mid not in self._history:
                continue

            # Check each parameter
            for param in ("temperature", "current", "rpm"):
                value = m[param]
                hist = self._history[mid][param]

                # Need enough data for statistics
                if len(hist) >= 10:
                    arr = np.array(hist)
                    mean = arr.mean()
                    std = arr.std()

                    if std > 1e-8:
                        z_score = abs(value - mean) / std
                        if z_score > SIGMA_THRESHOLD:
                            anomalies.append({
                                "motor_id": mid,
                                "name": m["name"],
                                "parameter": param,
                                "value": value,
                                "mean": round(mean, 2),
                                "std": round(std, 2),
                                "z_score": round(z_score, 2),
                                "level": ("critical" if z_score > 5.0
                                          else "warning"),
                            })

                hist.append(value)

        return anomalies

    # ------------------------------------------------------------------
    # Trend forecast
    # ------------------------------------------------------------------

    def predict_trend(self, motor_id, parameter, steps_ahead=10):
        """Simple linear regression forecast.

        Returns:
            dict with current, predicted, slope, will_exceed_threshold, ...
            or None if insufficient data
        """
        hist = list(self._history[motor_id][parameter])
        if len(hist) < 10:
            return None

        x = np.arange(len(hist), dtype=np.float32)
        y = np.array(hist, dtype=np.float32)

        # Linear regression: y = ax + b
        a = (np.mean(x * y) - np.mean(x) * np.mean(y)) / (
            np.mean(x * x) - np.mean(x) ** 2 + 1e-8
        )
        b = np.mean(y) - a * np.mean(x)

        current = y[-1]
        predicted = a * (len(hist) + steps_ahead) + b

        # Threshold for this parameter
        if parameter == "temperature":
            threshold = TEMP_WARN_THRESHOLD
            will_exceed = predicted > threshold
        elif parameter == "current":
            threshold = CURRENT_WARN_THRESHOLD
            will_exceed = predicted > threshold
        else:  # rpm
            threshold = RPM_MIN
            will_exceed = predicted < threshold

        return {
            "motor_id": motor_id,
            "name": MOTOR_NAMES[motor_id],
            "parameter": parameter,
            "current": round(current, 2),
            "predicted": round(predicted, 2),
            "slope": round(a, 4),
            "steps_ahead": steps_ahead,
            "will_exceed_threshold": will_exceed,
            "threshold": threshold,
        }

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------

    def get_stats(self, motor_id):
        """Return current statistics for a motor."""
        stats = {}
        for param in ("temperature", "current", "rpm"):
            hist = list(self._history[motor_id][param])
            if len(hist) >= 3:
                arr = np.array(hist)
                stats[param] = {
                    "mean": round(float(arr.mean()), 2),
                    "std": round(float(arr.std()), 2),
                    "min": round(float(arr.min()), 2),
                    "max": round(float(arr.max()), 2),
                    "latest": round(float(arr[-1]), 2),
                    "samples": len(hist),
                }
            else:
                stats[param] = {"samples": len(hist)}
        return stats
