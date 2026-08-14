"""CPU-side deterministic preprocessing and bounded real-time queues."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math
import queue
import threading
from typing import Deque, List, Optional, Tuple

import numpy as np

from .acquisition.frame_protocol import BridgeFrame
from .config import Case5Config


@dataclass(frozen=True)
class SignalStatistics:
    mean: float
    rms: float
    peak_to_peak: float


@dataclass(frozen=True)
class AnalysisWindow:
    first_sequence: int
    last_sequence: int
    start_host_ns: int
    end_host_ns: int
    sample_rate_hz: float
    waveforms: np.ndarray

    def __post_init__(self) -> None:
        values = np.asarray(self.waveforms, dtype=np.float32)
        if values.ndim != 2 or values.shape[0] != 2 or values.shape[1] == 0:
            raise ValueError("analysis waveforms must have shape [2, samples]")
        if not np.isfinite(values).all():
            raise ValueError("analysis waveforms must be finite")
        if not math.isfinite(float(self.sample_rate_hz)) or self.sample_rate_hz <= 0:
            raise ValueError("analysis sample_rate_hz must be finite and positive")
        if self.first_sequence < 0 or self.last_sequence < self.first_sequence:
            raise ValueError("analysis window sequence range is invalid")
        if self.start_host_ns < 0 or self.end_host_ns < self.start_host_ns:
            raise ValueError("analysis window time range is invalid")
        object.__setattr__(self, "waveforms", np.ascontiguousarray(values))


class LatestQueue:
    """Thread-safe queue that discards stale work rather than adding latency."""

    def __init__(self, capacity: int) -> None:
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity <= 0:
            raise ValueError("LatestQueue capacity must be a positive integer")
        self._queue: "queue.Queue[object]" = queue.Queue(maxsize=capacity)
        self._dropped = 0
        self._lock = threading.Lock()

    @property
    def dropped(self) -> int:
        with self._lock:
            return self._dropped

    def put_latest(self, item: object) -> None:
        with self._lock:
            if self._queue.full():
                try:
                    self._queue.get_nowait()
                    self._dropped += 1
                except queue.Empty:
                    pass
            self._queue.put_nowait(item)

    def get(self, timeout: Optional[float] = None):
        return self._queue.get(timeout=timeout)

    def get_nowait(self):
        return self._queue.get_nowait()

    def clear(self) -> int:
        """Discard pending work when an instrument generation is retired."""
        removed = 0
        with self._lock:
            while True:
                try:
                    self._queue.get_nowait()
                    removed += 1
                except queue.Empty:
                    return removed


class WindowAssembler:
    """Build fixed analysis windows from raw bridge blocks."""

    def __init__(self, samples_per_window: int, *, require_complete_frame: bool = False) -> None:
        if (
            not isinstance(samples_per_window, int)
            or isinstance(samples_per_window, bool)
            or samples_per_window <= 0
        ):
            raise ValueError("samples_per_window must be a positive integer")
        self.samples_per_window = samples_per_window
        self.require_complete_frame = bool(require_complete_frame)
        self._pending = np.empty((0, 2), dtype=np.float32)
        self._first_sequence: Optional[int] = None
        self._last_sequence: Optional[int] = None
        self._sample_rate_hz: Optional[float] = None
        self._last_host_receive_ns: Optional[int] = None

    def push(self, frame: BridgeFrame) -> List[AnalysisWindow]:
        if self.require_complete_frame:
            if frame.sample_count != self.samples_per_window:
                self.reset()
                return []
            duration_ns = int(1_000_000_000 * self.samples_per_window / frame.sample_rate_hz)
            start_host_ns = max(0, frame.host_receive_ns - duration_ns)
            return [
                AnalysisWindow(
                    first_sequence=frame.sequence,
                    last_sequence=frame.sequence,
                    start_host_ns=start_host_ns,
                    end_host_ns=frame.host_receive_ns,
                    sample_rate_hz=frame.sample_rate_hz,
                    waveforms=frame.samples.T,
                )
            ]
        if self._last_sequence is not None and frame.sequence != self._last_sequence + 1:
            # Never combine samples separated by a bridge sequence gap.  The
            # next frame starts a fresh NPU window, preserving traceability.
            self.reset()
        if self._sample_rate_hz is not None and frame.host_receive_ns < self._last_host_receive_ns:
            # Monotonic receive timestamps must not move backwards within one
            # assembled window; otherwise latency and time ranges are invalid.
            self.reset()
        if self._sample_rate_hz is not None and not np.isclose(
            self._sample_rate_hz, frame.sample_rate_hz, rtol=0.0, atol=0.0
        ):
            self.reset()
        if self._first_sequence is None:
            self._first_sequence = frame.sequence
            self._sample_rate_hz = frame.sample_rate_hz
        self._last_sequence = frame.sequence
        self._last_host_receive_ns = frame.host_receive_ns
        self._pending = np.concatenate((self._pending, frame.samples), axis=0)
        windows: List[AnalysisWindow] = []
        while self._pending.shape[0] >= self.samples_per_window:
            raw_window = self._pending[: self.samples_per_window]
            self._pending = self._pending[self.samples_per_window :]
            assert self._first_sequence is not None
            assert self._last_sequence is not None
            assert self._sample_rate_hz is not None
            duration_ns = int(1_000_000_000 * self.samples_per_window / self._sample_rate_hz)
            # The latest libsigrok callback timestamp is the host-side latency
            # reference. libsigrok does not expose device FIFO gap metadata.
            end_host_ns = frame.host_receive_ns
            windows.append(
                AnalysisWindow(
                    first_sequence=self._first_sequence,
                    last_sequence=self._last_sequence,
                    start_host_ns=max(0, end_host_ns - duration_ns),
                    end_host_ns=end_host_ns,
                    sample_rate_hz=self._sample_rate_hz,
                    waveforms=raw_window.T,
                )
            )
            self._first_sequence = frame.sequence if self._pending.shape[0] else None
            if self._first_sequence is None:
                self._last_sequence = None
                self._sample_rate_hz = None
        return windows

    def reset(self) -> None:
        self._pending = np.empty((0, 2), dtype=np.float32)
        self._first_sequence = None
        self._last_sequence = None
        self._sample_rate_hz = None
        self._last_host_receive_ns: Optional[int] = None


def signal_statistics(values: np.ndarray) -> SignalStatistics:
    array = np.asarray(values, dtype=np.float64)
    return SignalStatistics(
        mean=float(array.mean()),
        rms=float(np.sqrt(np.mean(np.square(array)))),
        peak_to_peak=float(array.max() - array.min()),
    )


class CaptureProcessor:
    """Convert raw frames into display units and deterministic NPU windows."""

    def __init__(self, config: Case5Config) -> None:
        config.validate()
        self.config = config
        self.windows = WindowAssembler(config.analysis_samples)
        self.latest_waveforms: Optional[np.ndarray] = None
        self.latest_statistics: Optional[Tuple[SignalStatistics, SignalStatistics]] = None
        self.frames_received = 0
        self.usb_blocks_received = 0
        self.capture_interval_ms: Optional[float] = None
        self._last_frame_host_ns: Optional[int] = None
        self._last_frame_sequence: Optional[int] = None

    def reset_stream(self) -> None:
        """Reset per-acquisition framing state before a new source generation."""
        self.windows.reset()
        self.latest_waveforms = None
        self.latest_statistics = None
        self.frames_received = 0
        self.usb_blocks_received = 0
        self.capture_interval_ms = None
        self._last_frame_host_ns = None
        self._last_frame_sequence = None

    def process(self, frame: BridgeFrame) -> List[AnalysisWindow]:
        if (
            self._last_frame_sequence is not None
            and frame.sequence <= self._last_frame_sequence
        ):
            raise ValueError(
                f"capture sequence must increase: previous {self._last_frame_sequence}, "
                f"received {frame.sequence}"
            )
        self.frames_received += 1
        if self._last_frame_host_ns is None:
            self.usb_blocks_received += 1
        elif frame.host_receive_ns > self._last_frame_host_ns:
            self.capture_interval_ms = (frame.host_receive_ns - self._last_frame_host_ns) / 1_000_000.0
            self.usb_blocks_received += 1
        self._last_frame_host_ns = max(self._last_frame_host_ns or frame.host_receive_ns, frame.host_receive_ns)
        self._last_frame_sequence = frame.sequence
        current = self.config.current_conversion.to_current(frame.samples[:, 1])
        converted = np.column_stack((frame.samples[:, 0], current)).astype(np.float32, copy=False)
        self.latest_waveforms = converted.T.copy()
        self.latest_statistics = (signal_statistics(converted[:, 0]), signal_statistics(converted[:, 1]))
        converted_frame = BridgeFrame(
            sequence=frame.sequence,
            host_receive_ns=frame.host_receive_ns,
            sample_rate_hz=frame.sample_rate_hz,
            flags=frame.flags,
            samples=converted,
        )
        windows = self.windows.push(converted_frame)
        return [self._preprocess(window) for window in windows]

    def _preprocess(self, window: AnalysisWindow) -> AnalysisWindow:
        waveforms = window.waveforms.astype(np.float32, copy=True)
        waveforms -= waveforms.mean(axis=1, keepdims=True, dtype=np.float32)
        return AnalysisWindow(
            first_sequence=window.first_sequence,
            last_sequence=window.last_sequence,
            start_host_ns=window.start_host_ns,
            end_host_ns=window.end_host_ns,
            sample_rate_hz=window.sample_rate_hz,
            waveforms=waveforms,
        )
