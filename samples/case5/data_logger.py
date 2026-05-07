"""
Case 5: CSV data logger for motor monitoring data.

Records timestamped sensor readings and fault classification results
for offline analysis.
"""

import csv
import os
import time

from config import CSV_LOG_PATH, MOTOR_NAMES, NUM_MOTORS


class DataLogger:
    """Append motor data rows to a CSV file."""

    def __init__(self):
        os.makedirs(os.path.dirname(CSV_LOG_PATH), exist_ok=True)
        self._file = None
        self._writer = None
        self._header_written = os.path.exists(CSV_LOG_PATH)
        self._open()

    def _open(self):
        self._file = open(CSV_LOG_PATH, "a", newline="")
        self._writer = csv.writer(self._file)
        if not self._header_written:
            self._write_header()
            self._header_written = True

    def _write_header(self):
        header = ["timestamp", "motor_id", "motor_name",
                  "temperature", "current", "rpm",
                  "fault_class_id", "fault_label_cn",
                  "fault_confidence"]
        self._writer.writerow(header)
        self._file.flush()

    # ------------------------------------------------------------------
    # Log
    # ------------------------------------------------------------------

    def log(self, motors, fault_results=None):
        """Write one frame of motor data to CSV.

        Args:
            motors: list of motor dicts from SensorReader
            fault_results: optional dict {motor_id: fault_classification}
        """
        ts = time.strftime("%Y-%m-%d %H:%M:%S")

        for m in motors:
            mid = m["motor_id"]
            row = [
                ts,
                mid,
                m.get("name", MOTOR_NAMES[mid]),
                m.get("temperature", ""),
                m.get("current", ""),
                m.get("rpm", ""),
            ]

            if fault_results and mid in fault_results:
                fr = fault_results[mid]
                row.extend([
                    fr.get("class_id", ""),
                    fr.get("label_cn", ""),
                    round(fr.get("confidence", 0), 4),
                ])
            else:
                row.extend(["", "", ""])

            self._writer.writerow(row)

        self._file.flush()

    # ------------------------------------------------------------------
    # Read back
    # ------------------------------------------------------------------

    def get_recent(self, n=100):
        """Return the most recent n rows as list of dicts."""
        if not os.path.exists(CSV_LOG_PATH):
            return []

        rows = []
        with open(CSV_LOG_PATH, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                rows.append(row)

        return rows[-n:]

    def get_stats(self):
        """Return basic log statistics."""
        if not os.path.exists(CSV_LOG_PATH):
            return {"total_rows": 0, "file_size_mb": 0}

        size_mb = os.path.getsize(CSV_LOG_PATH) / (1024 * 1024)
        with open(CSV_LOG_PATH, "r") as f:
            total = sum(1 for _ in f) - 1  # exclude header

        return {
            "total_rows": max(0, total),
            "file_size_mb": round(size_mb, 2),
            "path": CSV_LOG_PATH,
        }

    def close(self):
        if self._file:
            self._file.close()
