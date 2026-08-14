"""Clearly labelled signal source used only for UI and pipeline tests."""

from __future__ import annotations

import threading
import time
from typing import Optional

import numpy as np

from .frame_protocol import BridgeFrame
from . import ErrorCallback, FrameCallback


class SyntheticCapture:
    """Generate deterministic dual-channel signals without claiming hardware use."""

    def __init__(
        self,
        sample_rate_hz: float,
        frame_samples: int,
        callback: FrameCallback,
        *,
        error_callback: Optional[ErrorCallback] = None,
    ) -> None:
        if not np.isfinite(sample_rate_hz) or sample_rate_hz <= 0:
            raise ValueError("synthetic sample_rate_hz must be finite and positive")
        if not isinstance(frame_samples, int) or isinstance(frame_samples, bool) or frame_samples <= 0:
            raise ValueError("synthetic frame_samples must be a positive integer")
        self.sample_rate_hz = float(sample_rate_hz)
        self.frame_samples = int(frame_samples)
        self.callback = callback
        self.error_callback = error_callback
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("synthetic acquisition is already running")
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="case5-simulation", daemon=True)
            self._thread.start()

    def stop(self) -> bool:
        self._stop.set()
        return self.wait_stopped(timeout=2.0)

    def wait_stopped(self, timeout: float | None = None) -> bool:
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout=timeout)
        stopped = thread is None or not thread.is_alive()
        if stopped:
            with self._lock:
                if self._thread is thread:
                    self._thread = None
        return stopped

    def _run(self) -> None:
        sequence = 0
        phase = 0
        period = self.frame_samples / self.sample_rate_hz
        while not self._stop.is_set():
            # Float64 keeps the deterministic simulation phase-resolved after
            # long runs; float32 sample counters lose individual ticks quickly
            # at the board's megasample rates.
            sample_index = np.arange(self.frame_samples, dtype=np.float64) + phase
            time_axis = sample_index / self.sample_rate_hz
            voltage = 1.2 * np.sin(2.0 * np.pi * 1_000.0 * time_axis)
            current = 0.08 * np.sin(2.0 * np.pi * 1_000.0 * time_axis - 0.35)
            current += 0.01 * np.sin(2.0 * np.pi * 40_000.0 * time_axis)
            try:
                self.callback(
                    BridgeFrame(
                        sequence=sequence,
                        host_receive_ns=time.monotonic_ns(),
                        sample_rate_hz=self.sample_rate_hz,
                        flags=0,
                        samples=np.column_stack((voltage, current)),
                    )
                )
            except Exception as exc:
                self._stop.set()
                self._report_error(f"Synthetic capture callback failed: {type(exc).__name__}: {exc}")
                return
            sequence += 1
            phase += self.frame_samples
            self._stop.wait(max(period, 0.02))

    def _report_error(self, message: str) -> None:
        if self.error_callback is None:
            return
        try:
            self.error_callback(message)
        except Exception:
            # Capture threads must always unwind even if a presentation-layer
            # callback fails while reporting the original error.
            pass
