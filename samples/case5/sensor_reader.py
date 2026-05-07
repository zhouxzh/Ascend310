"""
Case 5: STM32 UART sensor reader with simulation fallback.

Reads multi-motor sensor data (temperature, current, RPM) from STM32
over UART. Falls back to synthetic data when no STM32 is connected.
"""

import os
import random
import time

import numpy as np

from config import (
    CURRENT_WARN_THRESHOLD,
    MOTOR_NAMES,
    NUM_MOTORS,
    RPM_MAX,
    RPM_MIN,
    TEMP_WARN_THRESHOLD,
    UART_BAUDRATE,
    UART_PORT,
    UART_TIMEOUT,
)

# UART data frame format (STM32 → Ascend 310B):
#   M1:T=42.5,C=1.25,R=3200|M2:T=38.2,C=0.85,R=2950|...\r\n


class SensorReader:
    """Read multi-motor sensor data from STM32 UART or simulate."""

    def __init__(self):
        self._serial = None
        self.use_hardware = False
        self._sim_state = None
        self._init()

    def _init(self):
        if os.path.exists(UART_PORT):
            try:
                import serial
                self._serial = serial.Serial(
                    port=UART_PORT,
                    baudrate=UART_BAUDRATE,
                    timeout=UART_TIMEOUT,
                )
                self.use_hardware = True
                print(f"[SensorReader] Connected to STM32 on {UART_PORT}")
                return
            except Exception as exc:
                print(f"[SensorReader] UART open failed ({exc}), "
                      f"using simulation")
        else:
            print("[SensorReader] No STM32 detected, using simulation mode")

        self._init_simulation()

    def _init_simulation(self):
        """Initialize synthetic motor states for simulation."""
        self._sim_state = []
        for i in range(NUM_MOTORS):
            self._sim_state.append({
                "temp": random.uniform(30, 45),
                "current": random.uniform(0.5, 1.2),
                "rpm": random.uniform(2000, 3500),
                "temp_drift": random.uniform(-0.1, 0.1),
                "current_drift": random.uniform(-0.01, 0.01),
                "rpm_drift": random.uniform(-20, 20),
            })

    # ------------------------------------------------------------------
    # Read one frame
    # ------------------------------------------------------------------

    def read(self):
        """Read one sensor frame.

        Returns:
            list of dicts, one per motor:
                [{motor_id, name, temperature, current, rpm, timestamp}, ...]
        """
        if self.use_hardware:
            return self._read_uart()
        else:
            return self._simulate()

    def _read_uart(self):
        """Parse a UART line from STM32."""
        try:
            line = self._serial.readline()
            line = line.decode("ascii", errors="ignore").strip()
            return self._parse_frame(line)
        except Exception as exc:
            print(f"[SensorReader] UART read error: {exc}")
            return []

    def _parse_frame(self, line):
        """Parse STM32 data frame.

        Format: M1:T=42.5,C=1.25,R=3200|M2:T=38.2,C=0.85,R=2950|...
        """
        motors = []
        ts = time.time()

        for segment in line.split("|"):
            segment = segment.strip()
            if not segment:
                continue

            motor_data = {"timestamp": ts}
            try:
                # Parse "M1:T=42.5,C=1.25,R=3200"
                parts = segment.split(":")
                if len(parts) < 2:
                    continue

                motor_id_str = parts[0]  # "M1"
                motor_data["motor_id"] = int(motor_id_str[1:]) - 1

                values = {}
                for kv in parts[1:]:
                    kv = kv.rstrip(",")
                    if "=" in kv:
                        k, v = kv.split("=")
                        values[k.strip()] = float(v)

                motor_data["temperature"] = values.get("T", 0.0)
                motor_data["current"] = values.get("C", 0.0)
                motor_data["rpm"] = values.get("R", 0.0)
            except (ValueError, IndexError) as exc:
                print(f"[SensorReader] Parse error: {exc} in '{segment}'")
                continue

            motor_data["name"] = MOTOR_NAMES[motor_data["motor_id"]]
            motors.append(motor_data)

        return motors

    def _simulate(self):
        """Generate synthetic motor data with random walk."""
        motors = []
        ts = time.time()

        for i, state in enumerate(self._sim_state):
            # Random walk
            state["temp"] += state["temp_drift"] + random.gauss(0, 0.05)
            state["current"] += (state["current_drift"]
                                 + random.gauss(0, 0.005))
            state["rpm"] += state["rpm_drift"] + random.gauss(0, 5)

            # Bounds
            state["temp"] = max(20, min(90, state["temp"]))
            state["current"] = max(0.1, min(5.0, state["current"]))
            state["rpm"] = max(0, min(7000, state["rpm"]))

            # Occasionally inject glitch (1% chance)
            if random.random() < 0.01:
                state["temp"] += random.uniform(10, 20)
            if random.random() < 0.01:
                state["current"] += random.uniform(0.5, 1.5)

            motors.append({
                "motor_id": i,
                "name": MOTOR_NAMES[i],
                "temperature": round(state["temp"], 1),
                "current": round(state["current"], 2),
                "rpm": round(state["rpm"], 0),
                "timestamp": ts,
            })

        return motors

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def get_temperature_alerts(self, motors):
        """Check temperature against thresholds, return alert list."""
        alerts = []
        for m in motors:
            if m["temperature"] >= TEMP_WARN_THRESHOLD:
                level = "critical" if m["temperature"] >= 80 else "warning"
                alerts.append({
                    "motor_id": m["motor_id"],
                    "name": m["name"],
                    "parameter": "temperature",
                    "value": m["temperature"],
                    "threshold": TEMP_WARN_THRESHOLD,
                    "level": level,
                })
        return alerts

    def get_current_alerts(self, motors):
        """Check current against thresholds, return alert list."""
        alerts = []
        for m in motors:
            if m["current"] >= CURRENT_WARN_THRESHOLD:
                level = "critical" if m["current"] >= 3.5 else "warning"
                alerts.append({
                    "motor_id": m["motor_id"],
                    "name": m["name"],
                    "parameter": "current",
                    "value": m["current"],
                    "threshold": CURRENT_WARN_THRESHOLD,
                    "level": level,
                })
        return alerts

    def close(self):
        if self._serial:
            self._serial.close()
