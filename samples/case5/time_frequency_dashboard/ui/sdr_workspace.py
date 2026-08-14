"""Standalone RTL-SDR dashboard workspace.

The workspace is intentionally presentation-only.  It emits start/stop/QC
requests and renders immutable service snapshots; resource arbitration,
manifest verification, acquisition and OM inference remain outside Qt.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable, Mapping

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .sdr_controls import SdrControls, SdrModelOption, SdrUiRunRequest
from .sdr_views import (
    ConstellationPlot,
    IqTimePlot,
    SdrInferenceResults,
    SpectrogramDetectionPlot,
    constellation_samples_from_model_input,
)
from .theme import ERROR, GOOD


def _field(value: Any, name: str, default: Any = None) -> Any:
    """Read an attribute or mapping key without constraining service dataclasses."""
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _nested_field(value: Any, name: str, child: str, default: Any = None) -> Any:
    nested = _field(value, name)
    return _field(nested, child, default) if nested is not None else default


def _format_ms(value: Any) -> str:
    try:
        return f"{float(value):.2f} ms"
    except (TypeError, ValueError):
        return "--"


class SdrWorkspace(QWidget):
    """RTL-SDR workspace with no ownership of a device or NPU runner.

    The callbacks are convenience wiring only.  Callers may instead connect
    to the corresponding signals, which makes the widget straightforward to
    exercise in Qt offscreen tests.
    """

    start_requested = Signal(object)
    stop_requested = Signal()
    qc_requested = Signal()

    def __init__(
        self,
        parent=None,
        *,
        on_start: Callable[[SdrUiRunRequest], None] | None = None,
        on_stop: Callable[[], None] | None = None,
        on_qc: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._display_paused = False
        self._rendered_frame_key: tuple[Any, Any] | None = None
        self._rendered_generation: Any = None
        self._last_snapshot: Any = None
        self._build_ui()
        self.controls.start_requested.connect(self.start_requested.emit)
        self.controls.stop_requested.connect(self.stop_requested.emit)
        self.controls.qc_requested.connect(self.qc_requested.emit)
        if on_start is not None:
            self.start_requested.connect(on_start)
        if on_stop is not None:
            self.stop_requested.connect(on_stop)
        if on_qc is not None:
            self.qc_requested.connect(on_qc)

    def _build_ui(self) -> None:
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.controls = SdrControls()
        self.controls.setObjectName("sdrControlRail")
        self.controls.setFixedWidth(310)
        layout.addWidget(self.controls)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(6)
        self.developer_banner = QLabel("开发输入，不构成硬件验收")
        self.developer_banner.setObjectName("sdrDeveloperBanner")
        self.developer_banner.setStyleSheet(f"color: {ERROR}; padding: 3px 8px;")
        self.developer_banner.setVisible(False)
        content_layout.addWidget(self.developer_banner)
        content_layout.addWidget(self._build_status_strip())

        self.tabs = QTabWidget()
        self.tabs.setObjectName("sdrViewTabs")
        self.tabs.setDocumentMode(True)
        self._build_iq_page()
        self._build_detection_page()
        content_layout.addWidget(self.tabs, 1)
        layout.addWidget(content, 1)

        results_frame = QFrame()
        results_frame.setObjectName("sdrResultsRail")
        results_layout = QVBoxLayout(results_frame)
        results_layout.setContentsMargins(8, 8, 8, 8)
        self.results = SdrInferenceResults()
        results_layout.addWidget(self.results, 1)
        results_frame.setFixedWidth(350)
        layout.addWidget(results_frame)

    def _build_iq_page(self) -> None:
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(4, 4, 4, 4)
        splitter = QSplitter(Qt.Vertical)
        self.iq_time_plot = IqTimePlot()
        self.constellation_plot = ConstellationPlot()
        splitter.addWidget(self.iq_time_plot)
        splitter.addWidget(self.constellation_plot)
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([400, 400])
        page_layout.addWidget(splitter, 1)
        self.iq_context_label = QLabel("星座：等待模型输入")
        self.iq_context_label.setObjectName("sdrIqContext")
        self.iq_context_label.setStyleSheet(f"color: {GOOD}; padding: 2px 6px;")
        page_layout.addWidget(self.iq_context_label)
        self.tabs.addTab(page, "I/Q 与星座")

    def _build_detection_page(self) -> None:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(4, 4, 4, 4)
        self.spectrogram_plot = SpectrogramDetectionPlot()
        layout.addWidget(self.spectrogram_plot, 1)
        self.tabs.addTab(page, "时频检测")

    def set_models(
        self,
        models: Iterable[SdrModelOption],
        selected_model_id: str | None = None,
    ) -> None:
        self.controls.set_models(models, selected_model_id)

    def set_developer_sources(self, enabled: bool) -> None:
        self.controls.set_developer_sources(enabled)
        self.developer_banner.setVisible(bool(enabled))

    def set_available(self, available: bool, reason: str = "") -> None:
        self.controls.set_available(available, reason)

    def set_display_paused(self, paused: bool) -> None:
        self._display_paused = bool(paused)

    def reset_views(self) -> None:
        self.iq_time_plot.reset_view()
        self.constellation_plot.reset_view()
        self.spectrogram_plot.reset_view()

    def run_request(self) -> SdrUiRunRequest:
        return self.controls.run_request()

    def render(
        self,
        snapshot: Any,
        frame: Any = None,
        *,
        coordinator_state: str = "IDLE",
        coordinator_active: bool = False,
        coordinator_message: str = "",
    ) -> bool:
        """Render status and return whether a newer display frame was drawn."""
        self._last_snapshot = snapshot
        snapshot_generation = _field(snapshot, "generation")
        if snapshot_generation != self._rendered_generation:
            self._reset_generation_display(snapshot_generation)
        state = str(_field(snapshot, "state", "idle"))
        is_active = (
            state.lower() in {"starting", "running", "stopping"}
            or coordinator_active
        )
        self.controls.set_running(is_active)
        self._render_status(
            snapshot,
            coordinator_state=coordinator_state,
            coordinator_active=coordinator_active,
            coordinator_message=coordinator_message,
        )
        if self._display_paused or frame is None:
            return False
        frame_generation = _field(frame, "generation")
        if (
            snapshot_generation is not None
            and frame_generation is not None
            and frame_generation != snapshot_generation
        ):
            # The service and UI snapshots are read independently.  Never draw
            # a frame that raced with a new generation becoming active.
            return False
        key = (_field(frame, "generation"), _field(frame, "sequence"))
        if key == self._rendered_frame_key:
            return False
        self._render_frame(snapshot, frame)
        self._rendered_frame_key = key
        return True

    def _render_status(
        self,
        snapshot: Any,
        *,
        coordinator_state: str = "IDLE",
        coordinator_active: bool = False,
        coordinator_message: str = "",
    ) -> None:
        state = str(_field(snapshot, "state", "IDLE"))
        model_id = _field(snapshot, "model_id", "-")
        task = _field(snapshot, "model_task", "")
        task_label = {"iq_classification": "IQ 分类", "spectrogram_detection": "时频检测"}.get(
            task, str(task or "")
        )
        npu_status = _field(snapshot, "npu_status")
        backend = _field(npu_status, "backend", _field(snapshot, "backend", "NPU 未初始化"))
        ready = bool(_field(npu_status, "ready", False))
        error = _field(snapshot, "error")
        message = str(error or _field(snapshot, "message", "准备就绪"))
        if coordinator_active and state.lower() in {"idle", "stopped"}:
            state = str(coordinator_state)
            message = str(coordinator_message or "Starting RTL-SDR")
        self.status_label.setText(f"{state} · {message}")
        self.model_label.setText(f"模型：{model_id} {task_label}".strip())
        self.npu_label.setText(str(backend))
        self.npu_label.setStyleSheet(
            f"background: {GOOD if ready else ERROR}; padding: 7px 10px;"
        )
        produced = _field(snapshot, "produced_batches", 0)
        completed = _field(snapshot, "completed_batches", 0)
        inference_drops = _field(snapshot, "queue_dropped_batches", _field(snapshot, "inference_dropped_batches", 0))
        display_drops = _field(snapshot, "display_dropped_frames", 0)
        archive_failures = _field(snapshot, "archive_failed_batches", 0)
        self.pipeline_label.setText(
            f"批次：{produced}/{completed} · 推理丢批：{inference_drops} · "
            f"显示覆盖：{display_drops} · 归档失败：{archive_failures}"
        )
        npu_ms = _field(snapshot, "last_npu_inference_ms", _nested_field(snapshot, "npu_status", "last_latency_ms"))
        pipeline_ms = _field(snapshot, "last_post_capture_pipeline_ms")
        end_to_end_ms = _field(snapshot, "last_end_to_end_ms")
        self.latency_label.setText(
            f"NPU：{_format_ms(npu_ms)} · 采集后：{_format_ms(pipeline_ms)} · 端到端：{_format_ms(end_to_end_ms)}"
        )
        run_dir = _field(snapshot, "run_dir")
        result_path = _field(snapshot, "result_path")
        location = result_path or run_dir
        self.run_label.setText("记录：-" if location is None else f"记录：{location}")
        self.controls.set_qc_available(
            state.lower() == "idle"
            and _field(snapshot, "source") == "rtl"
            and _field(snapshot, "completion_status") == "completed"
            and result_path is not None
            and _field(snapshot, "capture_path") is not None
        )

        self._set_iq_context(str(task or ""))

    def _render_frame(self, snapshot: Any, frame: Any) -> None:
        samples = _field(frame, "iq_samples", _field(frame, "samples"))
        sample_rate_hz = _field(frame, "sample_rate_hz", 1.0)
        if samples is not None:
            self.iq_time_plot.set_samples(
                samples,
                sample_rate_hz,
                _field(frame, "source_sample_count"),
            )
            model_iq = constellation_samples_from_model_input(
                _field(frame, "model_iq", _field(frame, "model_input")), samples
            )
            self.constellation_plot.set_samples(model_iq)
        task = str(_field(frame, "task", _field(snapshot, "model_task", "")))
        self._set_iq_context(task)
        spectrogram = _field(frame, "spectrogram_image")
        if task == "spectrogram_detection" and spectrogram is not None:
            center_frequency_hz = _field(frame, "center_frequency_hz", 0.0)
            try:
                center_frequency_hz = float(center_frequency_hz)
            except (TypeError, ValueError):
                center_frequency_hz = 0.0
            try:
                boxes = self.spectrogram_plot.set_spectrogram(
                    spectrogram,
                    detections=_field(frame, "detections", ()) or (),
                    batch_duration_ms=float(_field(frame, "batch_duration_ms", 0.0)),
                    center_frequency_hz=center_frequency_hz,
                    sample_rate_hz=float(sample_rate_hz),
                    batch_sequence=_field(frame, "sequence"),
                    source_sample_offset=_field(frame, "source_sample_offset"),
                )
            except (TypeError, ValueError) as exc:
                # Service frames are validated before publication. Keep a
                # malformed development/test frame from breaking Qt's timer
                # loop while making the missing preview unmistakable.
                self.spectrogram_plot.clear(f"时频预览数据无效：{exc}")
                self.results.show_empty("时频模型预览数据无效")
                return
            self.results.set_detections(boxes)
        elif task == "iq_classification":
            self.spectrogram_plot.clear("当前模型不使用时频输入")
            self.results.set_classification(_field(frame, "top_k", ()) or ())
        else:
            self.spectrogram_plot.clear("等待时频模型输入")
            self.results.show_empty("等待 NPU 结果")

    def _reset_generation_display(self, generation: Any) -> None:
        """Prevent a finished run's samples/results appearing in a new run."""
        self._rendered_generation = generation
        self._rendered_frame_key = None
        self.iq_time_plot.clear()
        self.constellation_plot.clear()
        self.spectrogram_plot.clear("等待时频模型输入")
        self.results.show_empty("等待 NPU 结果")

    def _set_iq_context(self, task: str) -> None:
        if task == "iq_classification":
            text = "星座：模型前处理 I/Q（分类输入）"
        elif task == "spectrogram_detection":
            text = "星座：原始捕获 I/Q（检测模型输入见“时频检测”）"
        else:
            text = "星座：等待模型输入"
        self.iq_context_label.setText(text)

    def _build_status_strip(self) -> QWidget:
        status_bar = QFrame()
        status_bar.setObjectName("sdrStatusBar")
        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(10, 5, 10, 5)
        self.status_label = QLabel("IDLE · 准备就绪")
        self.model_label = QLabel("模型：-")
        self.npu_label = QLabel("NPU 未初始化")
        self.npu_label.setObjectName("badge")
        self.pipeline_label = QLabel("批次：0/0")
        self.latency_label = QLabel("NPU：-- · 采集后：-- · 端到端：--")
        self.run_label = QLabel("记录：-")
        self.run_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        for widget in (
            self.status_label,
            self.model_label,
            self.npu_label,
            self.pipeline_label,
            self.latency_label,
        ):
            status_layout.addWidget(widget)
        status_layout.addWidget(self.run_label, 1)
        return status_bar
