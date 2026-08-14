"""Continuous Hantek capture through the project libsigrok bridge."""

from __future__ import annotations

from collections import deque
from pathlib import Path
import os
import math
import signal
import subprocess
import threading
from typing import Callable, Deque, Optional

import numpy as np

from .frame_protocol import BridgeFrame, FrameStreamDecoder


class SigrokCapture:
    """Own one libsigrok bridge process and emit validated two-channel frames."""

    def __init__(
        self,
        *,
        sample_rate_hz: float,
        frame_samples: int,
        callback_msec: int,
        ch1_volts_per_division: float,
        ch2_volts_per_division: float,
        ch1_probe_ratio: float,
        ch2_probe_ratio: float,
        bridge_path: Path,
        frame_callback: Callable[[BridgeFrame], None],
        error_callback: Callable[[str], None],
        process_factory=subprocess.Popen,
    ) -> None:
        self.sample_rate_hz = float(sample_rate_hz)
        self.frame_samples = int(frame_samples)
        self.callback_msec = int(callback_msec)
        self.ch1_volts_per_division = float(ch1_volts_per_division)
        self.ch2_volts_per_division = float(ch2_volts_per_division)
        self.ch1_probe_ratio = float(ch1_probe_ratio)
        self.ch2_probe_ratio = float(ch2_probe_ratio)
        self.bridge_path = Path(bridge_path)
        self.frame_callback = frame_callback
        self.error_callback = error_callback
        self._process_factory = process_factory
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._process: Optional[subprocess.Popen] = None
        self._stderr_lines: Deque[str] = deque(maxlen=20)
        self._stderr_thread: Optional[threading.Thread] = None
        self._lock = threading.RLock()
        self._validate()

    def _validate(self) -> None:
        if not math.isfinite(self.sample_rate_hz) or self.sample_rate_hz <= 0:
            raise ValueError("sigrok sample rate must be finite and positive")
        if self.frame_samples <= 0:
            raise ValueError("sigrok frame size must be positive")
        if not 10 <= self.callback_msec <= 1000:
            raise ValueError("sigrok callback interval must be between 10 and 1000 ms")
        for name, value in (
            ("CH1 volts/div", self.ch1_volts_per_division),
            ("CH2 volts/div", self.ch2_volts_per_division),
            ("CH1 probe ratio", self.ch1_probe_ratio),
            ("CH2 probe ratio", self.ch2_probe_ratio),
        ):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")

    @staticmethod
    def _sigrok_vdiv(value: float) -> tuple[int, int]:
        # hantek-6xxx compares the tuple exactly rather than reducing the
        # rational value, so these must match its published VDIV_VALUES table.
        supported = {
            1.0: (1, 1),
            0.5: (500, 1000),
            0.25: (250, 1000),
            0.1: (100, 1000),
        }
        try:
            return supported[float(value)]
        except KeyError as exc:
            raise ValueError(
                f"sigrok hantek-6xxx does not support {value:g} V/div; "
                f"choose one of {tuple(supported)}"
            ) from exc

    def command(self) -> list[str]:
        ch1_num, ch1_den = self._sigrok_vdiv(self.ch1_volts_per_division)
        ch2_num, ch2_den = self._sigrok_vdiv(self.ch2_volts_per_division)
        return [
            str(self.bridge_path),
            str(int(self.sample_rate_hz)),
            str(self.frame_samples),
            str(self.callback_msec),
            str(ch1_num),
            str(ch1_den),
            str(ch2_num),
            str(ch2_den),
        ]

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("sigrok acquisition is already running")
            if not self.bridge_path.is_file():
                raise RuntimeError(
                    f"sigrok capture bridge not found: {self.bridge_path}; "
                    "run bash scripts/build_sigrok_capture_bridge.sh"
                )
            if os.name != "nt" and not os.access(self.bridge_path, os.X_OK):
                raise RuntimeError(f"sigrok capture bridge is not executable: {self.bridge_path}")
            self._stop.clear()
            self._stderr_lines.clear()
            self._thread = threading.Thread(target=self._run, name="case5-sigrok", daemon=True)
            self._thread.start()

    def stop(self) -> bool:
        """Request termination and retain liveness references until it completes."""
        self._stop.set()
        with self._lock:
            process = self._process
        if process is not None and process.poll() is None:
            try:
                process.send_signal(signal.SIGINT)
            except (OSError, ProcessLookupError):
                pass
        with self._lock:
            thread = self._thread
        if (
            thread is not None
            and thread is not threading.current_thread()
            and thread.is_alive()
        ):
            thread.join(timeout=3.0)
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except (OSError, ProcessLookupError):
                pass
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                try:
                    process.kill()
                except (OSError, ProcessLookupError):
                    pass
                try:
                    process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    # Keep the process reference so the coordinator continues
                    # to own this source until a later wait observes exit.
                    pass
        # A killed bridge normally unblocks stdout, but do not discard its
        # thread reference merely because a bounded join elapsed.  The
        # coordinator must be able to keep ownership until every old worker
        # really exits before activating another instrument.
        if (
            thread is not None
            and thread is not threading.current_thread()
            and thread.is_alive()
        ):
            thread.join(timeout=2.0)
        with self._lock:
            stderr_thread = self._stderr_thread
        if (
            stderr_thread is not None
            and stderr_thread is not threading.current_thread()
            and stderr_thread.is_alive()
        ):
            stderr_thread.join(timeout=1.0)
        return self.wait_stopped(timeout=0.0)

    def wait_stopped(self, timeout: float | None = None) -> bool:
        """Return true only when bridge, capture and stderr workers are gone."""
        with self._lock:
            thread = self._thread
        if thread is not None and thread is not threading.current_thread() and thread.is_alive():
            thread.join(timeout=timeout)
        with self._lock:
            stderr_thread = self._stderr_thread
        if (
            stderr_thread is not None
            and stderr_thread is not threading.current_thread()
            and stderr_thread.is_alive()
        ):
            stderr_thread.join(timeout=timeout)
        with self._lock:
            process = self._process
        process_alive = process is not None and process.poll() is None
        stopped = not any(
            candidate is not None and candidate.is_alive()
            for candidate in (thread, stderr_thread)
        ) and not process_alive
        if stopped:
            with self._lock:
                if self._thread is thread:
                    self._thread = None
                if self._stderr_thread is stderr_thread:
                    self._stderr_thread = None
                if self._process is process:
                    self._process = None
        return stopped

    def _run(self) -> None:
        frames_received = 0
        expected_sequence = 0
        process: Optional[subprocess.Popen] = None
        try:
            # Do not hold the lifecycle lock across Popen: USB/device startup
            # can block, and Stop must remain able to observe cancellation.
            if self._stop.is_set():
                return
            process = self._process_factory(
                self.command(),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=0,
            )
            with self._lock:
                self._process = process
                cancelled_before_ownership = self._stop.is_set()
            if cancelled_before_ownership:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                return
            assert process.stdout is not None
            assert process.stderr is not None
            stderr_thread = threading.Thread(
                target=self._drain_stderr,
                args=(process.stderr,),
                name="case5-sigrok-stderr",
                daemon=True,
            )
            with self._lock:
                self._stderr_thread = stderr_thread
            stderr_thread.start()
            decoder = FrameStreamDecoder()
            while not self._stop.is_set():
                chunk = process.stdout.read(1024 * 1024)
                if not chunk:
                    break
                for frame in decoder.feed(chunk):
                    if frame.sequence != expected_sequence:
                        raise RuntimeError(
                            f"sigrok bridge sequence discontinuity: expected {expected_sequence}, "
                            f"received {frame.sequence}"
                        )
                    expected_sequence += 1
                    if not np.isclose(
                        frame.sample_rate_hz,
                        self.sample_rate_hz,
                        rtol=0.0,
                        atol=0.0,
                    ):
                        raise RuntimeError(
                            f"sigrok selected {frame.sample_rate_hz:.0f} S/s, but the fixed OM "
                            f"expects {self.sample_rate_hz:.0f} S/s"
                        )
                    self.frame_callback(self._apply_probe_ratios(frame))
                    frames_received += 1
            if not self._stop.is_set() and decoder.pending_bytes:
                raise RuntimeError(
                    f"sigrok bridge ended with {decoder.pending_bytes} truncated frame bytes"
                )
            return_code = process.wait(timeout=3.0)
            # The bridge is a continuous acquisition process.  EOF is an
            # unexpected loss of capture even when the child exits cleanly and
            # even if it yielded earlier frames; otherwise the controller
            # would remain RUNNING forever while no source can produce data.
            if not self._stop.is_set():
                detail = self._diagnostic_tail()
                raise RuntimeError(
                    f"sigrok bridge ended unexpectedly with code {return_code} after "
                    f"{frames_received} frames"
                    + (f": {detail}" if detail else "")
                )
        except Exception as exc:
            if not self._stop.is_set():
                detail = self._diagnostic_tail()
                message = f"sigrok: {type(exc).__name__}: {exc}"
                if detail and detail not in message:
                    message += f"; diagnostics: {detail}"
                self._report_error(message)
        finally:
            if process is not None and process.poll() is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
            if (
                process is not None
                and process is self._process
                and process.poll() is not None
            ):
                with self._lock:
                    if process is self._process:
                        self._process = None

    def _apply_probe_ratios(self, frame: BridgeFrame) -> BridgeFrame:
        samples = frame.samples.copy()
        samples[:, 0] *= self.ch1_probe_ratio
        samples[:, 1] *= self.ch2_probe_ratio
        return BridgeFrame(
            sequence=frame.sequence,
            host_receive_ns=frame.host_receive_ns,
            sample_rate_hz=frame.sample_rate_hz,
            flags=frame.flags,
            samples=samples,
        )

    def _drain_stderr(self, stream) -> None:
        for raw_line in iter(stream.readline, b""):
            line = raw_line.decode("utf-8", errors="replace").strip()
            if line:
                self._stderr_lines.append(line)

    def _diagnostic_tail(self) -> str:
        return " | ".join(self._stderr_lines)

    def _report_error(self, message: str) -> None:
        try:
            self.error_callback(message)
        except Exception:
            # The original bridge error must not turn into an unhandled worker
            # exception because a UI callback is already tearing down.
            pass
