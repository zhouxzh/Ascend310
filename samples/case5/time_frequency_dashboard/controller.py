"""Application controller shared by the Qt UI and acquisition threads."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
import json
from pathlib import Path
import threading
from typing import Deque, Dict, List, Optional, Tuple

import numpy as np

from .acquisition import SigrokCapture, SyntheticCapture
from .acquisition.frame_protocol import BridgeFrame
from .config import Case5Config
from .display import AutoColorScale, ColorScaleState, band_energy_to_db
from .model.npu_spectrum_numpy_reference import spectrum_axis_hz
from .npu import AnalysisResult, AnalysisService, AscendOmRunner, NpuStatus
from .processing import CaptureProcessor, SignalStatistics
from .session import SessionWriter


@dataclass(frozen=True)
class DashboardSnapshot:
    source: str
    acquisition_state: str
    message: str
    npu_status: NpuStatus
    waveforms: Optional[np.ndarray]
    statistics: Optional[Tuple[SignalStatistics, SignalStatistics]]
    voltage_waterfall: np.ndarray
    current_waterfall: np.ndarray
    frames_received: int
    usb_blocks_received: int
    capture_interval_ms: Optional[float]
    analysis_completed: int
    analysis_dropped: int
    storage_dropped: int
    session_path: Optional[Path]
    voltage_color_scale: ColorScaleState
    current_color_scale: ColorScaleState
    waterfall_history_rows: int
    spectrum_axis_hz: np.ndarray
    spectrum_values: np.ndarray
    analysis_latency_ms: Optional[float] = None


class Case5Controller:
    """Coordinates a source, session writer, CPU preprocessing and NPU worker."""

    def __init__(self, config: Case5Config, om_path: Path) -> None:
        self.config = config
        self.processor = CaptureProcessor(config)
        self.runner = AscendOmRunner(om_path)
        self.analysis = AnalysisService(
            self.runner, config.analysis_queue_capacity, config.result_queue_capacity
        )
        self._lock = threading.RLock()
        self._source = None
        self._source_name = "DISCONNECTED"
        self._acquisition_state = "STOPPED"
        self._message = "Ready"
        self._session: Optional[SessionWriter] = None
        self._retired_sessions: List[SessionWriter] = []
        self._voltage_rows: Deque[np.ndarray] = deque(maxlen=config.waterfall_rows)
        self._current_rows: Deque[np.ndarray] = deque(maxlen=config.waterfall_rows)
        self._waterfall_history_rows = config.waterfall_rows
        self._voltage_color_scale = AutoColorScale()
        self._current_color_scale = AutoColorScale()
        self._analysis_completed = 0
        self._analysis_latency_ms: Optional[float] = None

    def initialize_npu(self) -> NpuStatus:
        status = self.analysis.start()
        with self._lock:
            self._message = status.message
        return status

    def start_hardware(
        self,
        bridge_path: Path,
        *,
        ch1_volts_per_division: Optional[float] = None,
        ch1_probe_ratio: Optional[float] = None,
    ) -> None:
        """Start continuous libsigrok capture with fixed-shape OM settings."""

        capture_config = replace(
            self.config,
            ch1_volts_per_division=(
                self.config.ch1_volts_per_division
                if ch1_volts_per_division is None
                else float(ch1_volts_per_division)
            ),
            ch1_probe_ratio=(
                self.config.ch1_probe_ratio if ch1_probe_ratio is None else float(ch1_probe_ratio)
            ),
        )
        capture_config.validate()
        source = SigrokCapture(
            sample_rate_hz=capture_config.sample_rate_hz,
            frame_samples=capture_config.analysis_samples,
            callback_msec=capture_config.sigrok_callback_msec,
            ch1_volts_per_division=capture_config.ch1_volts_per_division,
            ch2_volts_per_division=capture_config.ch2_volts_per_division,
            ch1_probe_ratio=capture_config.ch1_probe_ratio,
            ch2_probe_ratio=capture_config.ch2_probe_ratio,
            bridge_path=bridge_path,
            frame_callback=self.on_frame,
            error_callback=self.on_error,
        )
        self._start_source_transaction(
            source=source,
            source_name="SIGROK",
            session_source="sigrok-hantek-6022be",
            running_message="Acquisition active; CH2 conversion uses declared probe sensitivity",
            extra_metadata={
                "sigrok_capture_settings": {
                    "bridge_path": str(bridge_path),
                    "sample_rate_hz": capture_config.sample_rate_hz,
                    "frame_samples": capture_config.analysis_samples,
                    "callback_msec": capture_config.sigrok_callback_msec,
                    "ch1_volts_per_division": capture_config.ch1_volts_per_division,
                    "ch1_probe_ratio": capture_config.ch1_probe_ratio,
                }
            },
        )

    def start_simulation(self) -> None:
        source = SyntheticCapture(
            self.config.sample_rate_hz,
            self.config.analysis_samples,
            self.on_frame,
            error_callback=self.on_error,
        )
        self._start_source_transaction(
            source=source,
            source_name="SIMULATED",
            session_source="SIMULATION",
            running_message="Simulation active; not hardware validation",
        )

    def stop(self) -> bool:
        """Stop acquisition and report whether its producer has exited."""
        with self._lock:
            source = self._source
        source_stopped = True
        if source is not None:
            stopped = source.stop()
            wait_stopped = getattr(source, "wait_stopped", None)
            if callable(wait_stopped):
                source_stopped = bool(wait_stopped(timeout=0.0))
            elif stopped is not None:
                source_stopped = bool(stopped)
        session_to_close: Optional[SessionWriter] = None
        with self._lock:
            if source_stopped:
                if self._source is source:
                    self._source = None
                    self._source_name = "DISCONNECTED"
                    self._acquisition_state = "STOPPED"
                    self._message = "Stopped"
                if self._session is not None:
                    session_to_close = self._session
                    self._session = None
            else:
                self._acquisition_state = "STOPPING"
                self._message = "Stop requested; acquisition thread is still exiting"
        session_closed = True
        if session_to_close is not None:
            session_closed = session_to_close.close()
            if not session_closed:
                self._remember_retired_session(session_to_close)
        return (
            source_stopped
            and session_closed
            and self._retired_sessions_closed(timeout=0.0)
        )

    def wait_stopped(self, timeout: float | None = None) -> bool:
        """Wait for the capture source; retain it until no thread is alive."""
        with self._lock:
            source = self._source
        if source is None:
            return self._retired_sessions_closed(timeout=timeout)
        wait_stopped = getattr(source, "wait_stopped", None)
        if callable(wait_stopped):
            stopped = bool(wait_stopped(timeout=timeout))
        else:
            stopped = False
        session: Optional[SessionWriter] = None
        if stopped:
            with self._lock:
                if self._source is source:
                    self._source = None
                    self._source_name = "DISCONNECTED"
                    self._acquisition_state = "STOPPED"
                    self._message = "Stopped"
                    if self._session is not None:
                        session = self._session
                        self._session = None
            if session is not None:
                if not session.close(timeout=timeout):
                    self._remember_retired_session(session)
                    stopped = False
        return stopped and self._retired_sessions_closed(timeout=timeout)

    def close(self) -> bool:
        capture_stopped = self.stop()
        if not capture_stopped:
            # A capture callback can still submit windows while its producer is
            # alive.  Leave the analysis worker owned until a later successful
            # stop rather than closing it concurrently under that callback.
            return False
        return self.analysis.close()

    def on_frame(self, frame: BridgeFrame) -> None:
        with self._lock:
            if self._acquisition_state not in {"STARTING", "RUNNING"}:
                # A source can have one callback already in flight when Stop
                # or a fatal error wins.  Do not let that stale frame create
                # another analysis window or write into a retired session.
                return
            session = self._session
            if session is not None:
                stored = session.record_frame(frame)
                if session.writer_error is not None:
                    self._acquisition_state = "FAILED"
                    self._message = f"Session storage failed: {session.writer_error}"
                    session.request_close()
                    self._remember_retired_session(session)
                    self._session = None
                    return
                if not stored:
                    # Queue pressure is recorded as a bounded storage drop and
                    # does not invalidate the live NPU analysis path.
                    pass
            try:
                windows = self.processor.process(frame)
            except Exception as exc:
                self._acquisition_state = "FAILED"
                self._message = f"Capture processing failed: {type(exc).__name__}: {exc}"
                if session is not None:
                    session.request_close()
                    self._remember_retired_session(session)
                    self._session = None
                return
        for window in windows:
            self.analysis.submit(window)

    def on_error(self, message: str) -> None:
        with self._lock:
            # This callback runs on the capture thread before its ``finally``
            # block returns.  Keep the source reference until wait_stopped()
            # can prove the old worker is gone; otherwise a source switch may
            # race a still-live libsigrok thread.
            self._acquisition_state = "FAILED"
            self._message = message
            if self._session is not None:
                # Error callbacks execute on the acquisition worker.  Closing
                # a full storage queue synchronously here can keep that worker
                # alive for seconds; request close and let SessionWriter
                # finalize itself after its bounded queue drains.
                self._session.request_close()
                self._remember_retired_session(self._session)
                self._session = None

    def poll(self) -> None:
        for result in self.analysis.drain_results():
            self._accept_result(result)

    def snapshot(self) -> DashboardSnapshot:
        self.poll()
        with self._lock:
            session = self._session
            return DashboardSnapshot(
                source=self._source_name,
                acquisition_state=self._acquisition_state,
                message=self._message,
                npu_status=self.runner.status,
                waveforms=None if self.processor.latest_waveforms is None else self.processor.latest_waveforms.copy(),
                statistics=self.processor.latest_statistics,
                voltage_waterfall=self._waterfall_array(self._voltage_rows),
                current_waterfall=self._waterfall_array(self._current_rows),
                frames_received=self.processor.frames_received,
                usb_blocks_received=self.processor.usb_blocks_received,
                capture_interval_ms=self.processor.capture_interval_ms,
                analysis_completed=self._analysis_completed,
                analysis_dropped=self.analysis.input.dropped,
                storage_dropped=0 if session is None else session.storage_dropped_frames,
                session_path=None if session is None else session.path,
                voltage_color_scale=self._voltage_color_scale.state,
                current_color_scale=self._current_color_scale.state,
                waterfall_history_rows=self._waterfall_history_rows,
                spectrum_axis_hz=self._spectrum_axis_hz(),
                spectrum_values=self._spectrum_values(),
                analysis_latency_ms=self._analysis_latency_ms,
            )

    def _start_session(self, source: str, extra_metadata: Optional[Dict[str, object]] = None) -> None:
        with self._lock:
            if self._source is not None or self._session is not None:
                raise RuntimeError("acquisition is already running")
            metadata = self._model_metadata()
            metadata["source"] = source
            metadata["spectral_display"] = {
                "transform": "10*log10(max(E, 1e-12) / 1 V^2)",
                "unit": "dB re 1 V^2 (uncalibrated)",
                "floor_db": -120.0,
            }
            if extra_metadata:
                metadata.update(extra_metadata)
            self.processor.reset_stream()
            self._session = SessionWriter(self.config, metadata)

    def _start_source_transaction(
        self,
        *,
        source: object,
        source_name: str,
        session_source: str,
        running_message: str,
        extra_metadata: Optional[Dict[str, object]] = None,
    ) -> None:
        """Open a session only for a source whose start has completed safely."""

        self._start_session(session_source, extra_metadata)
        with self._lock:
            # Publish ownership before source.start().  Both SigrokCapture and
            # SyntheticCapture launch a worker inside start(), so Stop and an
            # early callback must see a real source/session rather than an
            # apparently idle controller.
            self._source = source
            self._source_name = source_name
            self._acquisition_state = "STARTING"
            self._message = f"Starting {source_name}"
        try:
            source.start()
        except Exception as exc:
            cleanup_error = self._stop_failed_start_source(source)
            with self._lock:
                session = self._session
                self._session = None
                message = f"Failed to start {source_name}: {type(exc).__name__}: {exc}"
                if cleanup_error is not None:
                    message = f"{message}; startup cleanup: {cleanup_error}"
                    # start() may have launched a worker before it raised.
                    # Retain the source until a later wait_stopped() proves
                    # it cannot still emit into this controller.
                    self._source = source
                    self._source_name = source_name
                    self._acquisition_state = "FAILED"
                else:
                    if self._source is source:
                        self._source = None
                        self._source_name = "DISCONNECTED"
                        self._acquisition_state = "STOPPED"
                self._message = message
            if session is not None:
                # No source worker remains after a synchronous start error,
                # so finish the evidence before returning the exception.
                if not session.close():
                    self._remember_retired_session(session)
            raise

        with self._lock:
            # A source may fail from its worker before start() returns.  Keep
            # it owned in FAILED state so a later wait_stopped() proves it is
            # gone; never overwrite that callback with a stale RUNNING state.
            if self._source is source and self._acquisition_state == "STARTING":
                self._acquisition_state = "RUNNING"
                self._message = running_message

    @staticmethod
    def _stop_failed_start_source(source: object) -> Optional[str]:
        """Best-effort cleanup for a source that raised from start()."""

        try:
            stop = getattr(source, "stop", None)
            stop_result = None
            if callable(stop):
                stop_result = stop()
            wait_stopped = getattr(source, "wait_stopped", None)
            if callable(wait_stopped):
                if not bool(wait_stopped(timeout=2.0)):
                    return "source did not stop after failed start"
            elif stop_result is False:
                return "source did not stop after failed start"
        except Exception as exc:  # pragma: no cover - source-specific failure path
            return f"{type(exc).__name__}: {exc}"
        return None

    def _model_metadata(self) -> Dict[str, object]:
        sidecar = self.runner.om_path.with_suffix(self.runner.om_path.suffix + ".json")
        if sidecar.is_file():
            try:
                return json.loads(sidecar.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {
            "om_path": str(self.runner.om_path),
            "backend": self.runner.status.backend,
            "input_shape": [1, 2, self.config.analysis_samples],
            "output_shape": [1, 2, self.config.spectrum_bins, 1],
        }

    def _retired_sessions_closed(self, timeout: float | None) -> bool:
        """Finish sessions detached by asynchronous source-error callbacks."""
        with self._lock:
            pending = tuple(self._retired_sessions)
        all_closed = True
        # The writer wakes from its bounded queue wait at most every 100 ms.
        # A zero capture wait is useful for source liveness, but using it for
        # file finalization creates a needless race with the writer thread.
        session_timeout = 1.0 if timeout == 0 or timeout == 0.0 else timeout
        for session in pending:
            if not session.wait_closed(timeout=session_timeout):
                all_closed = False
        if all_closed:
            with self._lock:
                self._retired_sessions = [
                    session for session in self._retired_sessions if not session.closed
                ]
        return all_closed

    def _remember_retired_session(self, session: SessionWriter) -> None:
        with self._lock:
            if session not in self._retired_sessions:
                self._retired_sessions.append(session)

    def _accept_result(self, result: AnalysisResult) -> None:
        with self._lock:
            self._analysis_completed += 1
            self._analysis_latency_ms = max(
                0.0,
                (result.completed_ns - result.window.end_host_ns) / 1_000_000.0,
            )
            if not result.status.ready:
                # The capture source may still be unwinding on another
                # thread.  Retain it for wait_stopped(), but never present a
                # live acquisition as healthy once its mandatory OM path has
                # failed and AnalysisService will drop subsequent windows.
                self._acquisition_state = "FAILED"
                self._message = result.status.message
            if result.spectrum_power is not None:
                values = np.asarray(result.spectrum_power, dtype=np.float32)
                if values.shape != (1, 2, self.config.spectrum_bins, 1):
                    self._acquisition_state = "FAILED"
                    self._message = f"NPU output shape mismatch: {values.shape}"
                    return
                # Keep raw NPU spectrum power in the session.  dB conversion
                # belongs only to display, outside the OM's FP16 path.
                display_values = band_energy_to_db(values)
                self._voltage_rows.append(display_values[0, 0, :, 0].copy())
                self._current_rows.append(display_values[0, 1, :, 0].copy())
                self._voltage_color_scale.observe(display_values[0, 0, :, 0])
                self._current_color_scale.observe(display_values[0, 1, :, 0])
                self._message = result.status.message
            else:
                self._message = result.status.message
            if self._session is not None:
                persisted = self._session.record_analysis(
                    {
                        "first_sequence": result.window.first_sequence,
                        "last_sequence": result.window.last_sequence,
                        "start_host_ns": result.window.start_host_ns,
                        "end_host_ns": result.window.end_host_ns,
                        "sample_rate_hz": result.window.sample_rate_hz,
                        "backend": result.status.backend,
                        "npu_ready": result.status.ready,
                        "npu_message": result.status.message,
                        "npu_latency_ms": result.status.last_latency_ms,
                        "completed_ns": result.completed_ns,
                        "spectrum_power_shape": (
                            None if result.spectrum_power is None else list(result.spectrum_power.shape)
                        ),
                        "spectrum_power": (
                            None
                            if result.spectrum_power is None
                            else np.asarray(result.spectrum_power, dtype=np.float32)
                            .reshape(2, self.config.spectrum_bins)
                            .tolist()
                        ),
                    }
                )
                if not persisted and self._session.writer_error is not None:
                    self._acquisition_state = "FAILED"
                    self._message = f"Session storage failed: {self._session.writer_error}"

    def _waterfall_array(self, rows: Deque[np.ndarray]) -> np.ndarray:
        if not rows:
            return np.empty((0, self.config.spectrum_bins), dtype=np.float32)
        return np.stack(tuple(rows), axis=0)

    def set_waterfall_history_rows(self, rows: int) -> int:
        """Resize bounded display histories without touching capture or NPU work."""

        count = int(rows)
        if not 20 <= count <= 500:
            raise ValueError("waterfall history must be between 20 and 500 rows")
        with self._lock:
            self._voltage_rows = deque(self._voltage_rows, maxlen=count)
            self._current_rows = deque(self._current_rows, maxlen=count)
            self._waterfall_history_rows = count
        return count

    def reset_auto_color_scale(self, channel: int) -> ColorScaleState:
        with self._lock:
            return self._color_scale_for(channel).reset_auto()

    def set_manual_color_scale(self, channel: int, low_db: float, high_db: float) -> ColorScaleState:
        with self._lock:
            return self._color_scale_for(channel).set_manual(low_db, high_db)

    def _color_scale_for(self, channel: int) -> AutoColorScale:
        if channel == 0:
            return self._voltage_color_scale
        if channel == 1:
            return self._current_color_scale
        raise ValueError("channel must be 0 (CH1) or 1 (CH2)")

    def _spectrum_axis_hz(self) -> np.ndarray:
        return spectrum_axis_hz(
            sample_rate_hz=self.config.sample_rate_hz,
            samples=self.config.analysis_samples,
            max_frequency_hz=self.config.spectrum_max_frequency_hz,
        )

    def _spectrum_values(self) -> np.ndarray:
        if not self._voltage_rows:
            return np.empty(0, dtype=np.float32)
        return np.asarray(self._voltage_rows[-1], dtype=np.float32).copy()
