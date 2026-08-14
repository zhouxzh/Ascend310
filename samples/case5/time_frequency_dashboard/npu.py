"""Explicit Ascend OM inference adapter used by the Case 5 analysis thread."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import queue
import threading
import time
from typing import List, Optional, Sequence

import numpy as np

from .processing import AnalysisWindow, LatestQueue


@dataclass(frozen=True)
class NpuStatus:
    backend: str
    ready: bool
    message: str
    input_shape: Optional[Sequence[int]] = None
    output_shape: Optional[Sequence[int]] = None
    last_latency_ms: Optional[float] = None


@dataclass(frozen=True)
class AnalysisResult:
    window: AnalysisWindow
    spectrum_power: Optional[np.ndarray]
    status: NpuStatus
    completed_ns: int


class AscendOmRunner:
    """Small adapter around the board-provided ``aclruntime`` binding.

    There is deliberately no implicit CPU implementation here.  The caller
    receives an unavailable status when CANN, the OM artifact, or execution is
    not usable, instead of a result that could be mistaken for NPU output.
    """

    def __init__(self, om_path: Path, device_id: int = 0) -> None:
        self.om_path = Path(om_path)
        if not isinstance(device_id, int) or isinstance(device_id, bool) or device_id < 0:
            raise ValueError("device_id must be a non-negative integer")
        self.device_id = device_id
        self._runtime = None
        self._session = None
        self._output_names: List[str] = []
        self._status = NpuStatus("NPU unavailable", False, "not initialized")

    @property
    def status(self) -> NpuStatus:
        return self._status

    def initialize(self) -> NpuStatus:
        # ``AnalysisService`` owns initialization on one worker, but closing
        # a prior partially-created session here also makes direct diagnostic
        # reuse safe.
        self.close()
        if not self.om_path.is_file():
            self._status = NpuStatus("NPU unavailable", False, f"OM model not found: {self.om_path}")
            return self._status
        session = None
        runtime = None
        try:
            import aclruntime  # type: ignore

            options = aclruntime.session_options()
            session = aclruntime.InferenceSession(str(self.om_path), self.device_id, options)
            runtime = aclruntime
            inputs = session.get_inputs()
            outputs = session.get_outputs()
            input_shape = tuple(int(v) for v in getattr(inputs[0], "shape", ())) if inputs else None
            output_shape = tuple(int(v) for v in getattr(outputs[0], "shape", ())) if outputs else None
            self._output_names = [str(getattr(item, "name", "")) for item in outputs]
            if not self._output_names or any(not item for item in self._output_names):
                raise RuntimeError("aclruntime did not expose OM output names")
            self._session = session
            self._runtime = runtime
            self._status = NpuStatus(
                "NPU (Ascend 310B)",
                True,
                "OM loaded; waiting for first inference",
                input_shape=input_shape,
                output_shape=output_shape,
            )
        except Exception as exc:
            if session is not None:
                try:
                    session.finalize()
                except Exception:
                    pass
            self._session = None
            self._runtime = None
            self._output_names = []
            self._status = NpuStatus("NPU unavailable", False, f"{type(exc).__name__}: {exc}")
        return self._status

    def run_all(self, waveforms: np.ndarray) -> List[np.ndarray]:
        """Execute the OM and copy every declared output back to host memory."""
        if self._session is None or self._runtime is None:
            raise RuntimeError(self._status.message)
        values = np.ascontiguousarray(waveforms, dtype=np.float32)
        if values.ndim < 2 or values.shape[0] < 1:
            raise ValueError("OM input must be a fixed-shape batch with batch >= 1")
        if not np.isfinite(values).all():
            raise ValueError("OM input contains NaN or infinite values")
        started = time.perf_counter_ns()
        tensor = self._runtime.Tensor(values)
        outputs = self._session.run(self._output_names, [tensor])
        if not outputs:
            raise RuntimeError("OM returned no output tensors")
        results: List[np.ndarray] = []
        for output in outputs:
            try:
                output.to_host()
            except AttributeError:
                pass
            value = np.asarray(output, dtype=np.float32).copy()
            if not np.isfinite(value).all():
                raise RuntimeError("OM returned NaN or infinite values")
            results.append(value)
        # The reported NPU boundary includes input Tensor/H2D, OM execution,
        # D2H and copying the result into independent host-owned memory.
        elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
        self._status = NpuStatus(
            "NPU (Ascend 310B)",
            True,
            "NPU inference active",
            input_shape=tuple(values.shape),
            output_shape=tuple(results[0].shape),
            last_latency_ms=elapsed_ms,
        )
        return results

    def run(self, waveforms: np.ndarray) -> np.ndarray:
        """Execute the OM and return its first output for existing single-output paths."""
        return self.run_all(waveforms)[0]

    def close(self) -> None:
        if self._session is not None:
            try:
                self._session.finalize()
            except Exception:
                pass
        self._session = None
        self._runtime = None
        self._output_names = []
        if self._status.ready:
            self._status = NpuStatus("NPU unavailable", False, "OM session closed")

    def mark_unavailable(self, message: str) -> NpuStatus:
        self._status = NpuStatus("NPU unavailable", False, message)
        return self._status


class AnalysisService:
    """Dedicated NPU worker with bounded latency-first input and result queues."""

    def __init__(self, runner: AscendOmRunner, input_capacity: int, result_capacity: int) -> None:
        if not isinstance(runner, AscendOmRunner) and not all(
            hasattr(runner, attribute)
            for attribute in ("status", "initialize", "run", "close", "mark_unavailable")
        ):
            raise TypeError("runner does not satisfy the analysis runner contract")
        self.runner = runner
        self.input = LatestQueue(input_capacity)
        self.results = LatestQueue(result_capacity)
        self._stop = threading.Event()
        self._startup_event = threading.Event()
        self._startup_cancelled = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.RLock()

    def start(self) -> NpuStatus:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return self.runner.status
            # A stopped acquisition may leave latest-first work that belongs to a
            # retired instrument generation.  It must never execute after restart.
            self.input.clear()
            self.results.clear()
            self._stop.clear()
            self._startup_event.clear()
            self._startup_cancelled.clear()
            thread = threading.Thread(target=self._run, name="case5-npu-worker", daemon=True)
            self._thread = thread
            thread.start()
        if not self._startup_event.wait(timeout=15.0):
            self._stop.set()
            self._startup_cancelled.set()
            status = self.runner.mark_unavailable("NPU worker initialization timed out")
            # Do not leave queued windows for a delayed initialize() call to
            # consume after the caller has already rejected this start.
            self.input.clear()
            return status
        return self.runner.status

    def submit(self, window: AnalysisWindow) -> None:
        with self._lock:
            thread = self._thread
            active = (
                thread is not None
                and thread.is_alive()
                and not self._stop.is_set()
                and self.runner.status.ready
            )
        if active:
            self.input.put_latest(window)

    def drain_results(self) -> List[AnalysisResult]:
        drained: List[AnalysisResult] = []
        while True:
            try:
                drained.append(self.results.get_nowait())
            except Exception:
                return drained

    def close(self) -> bool:
        """Stop the worker and report whether its thread actually exited."""
        self._stop.set()
        stopped = self.wait_stopped(timeout=3.0)
        if stopped:
            self.input.clear()
            self.results.clear()
        return stopped

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
        try:
            status = self.runner.initialize()
            self._startup_event.set()
            if self._startup_cancelled.is_set():
                self.runner.mark_unavailable("NPU worker initialization timed out")
                return
            if not status.ready:
                return
            while not self._stop.is_set():
                try:
                    window = self.input.get(timeout=0.2)
                except queue.Empty:
                    continue
                try:
                    output = self.runner.run(window.waveforms[None, :, :])
                    result = AnalysisResult(window, output, self.runner.status, time.monotonic_ns())
                except Exception as exc:
                    status = self.runner.mark_unavailable(f"{type(exc).__name__}: {exc}")
                    result = AnalysisResult(window, None, status, time.monotonic_ns())
                    self.results.put_latest(result)
                    return
                self.results.put_latest(result)
        except Exception as exc:
            self.runner.mark_unavailable(f"{type(exc).__name__}: {exc}")
            self._startup_event.set()
        finally:
            self.runner.close()
