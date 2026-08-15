"""RTL-SDR controls shared by the dashboard workspace and offscreen tests.

The widgets deliberately know nothing about ACL, ``rtl_sdr``, or a controller.
They only turn the operator's selections into an immutable request; the owner
of the workspace must validate the manifest again before a device is opened.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..rtl_sdr_service import estimate_live_capture_bytes, plan_live_capture
from .controls import ControlSection


RF_INPUT_CONTEXT_LABELS = {
    "unknown": "RF 输入：未知",
    "disconnected": "RF 输入：未连接",
    "antenna_connected": "RF 输入：天线已连接",
    "lab_cabled": "RF 输入：实验室线缆",
}


@dataclass(frozen=True)
class SdrModelOption:
    """Minimal reviewed-model description required by the control rail."""

    model_id: str
    manifest_path: Path
    task: str
    input_shape: tuple[int, ...]
    sample_rate_hz: float | None
    display_name: str | None = None

    @property
    def label(self) -> str:
        task = "IQ 分类" if self.task == "iq_classification" else "时频检测"
        rate = "自定义采样率" if self.sample_rate_hz is None else f"{self.sample_rate_hz / 1e6:g} MS/s"
        return self.display_name or f"{self.model_id} · {task} · {rate}"


@dataclass(frozen=True)
class SdrUiRunRequest:
    """Presentation-layer request.  It is not proof that a run is admitted."""

    model_id: str
    manifest_path: Path
    source: str
    device: str
    center_frequency_hz: float
    sample_rate_hz: float | None
    gain_db: float | None
    ppm_error: int
    rf_input_context: str
    duration_seconds: float
    input_cu8: Path | None = None


def _fixed_window_samples(option: SdrModelOption) -> int | None:
    """Derive the raw-IQ sample count that one fixed model invocation consumes."""
    shape = option.input_shape
    if option.task == "iq_classification":
        dimensions = (0, 2)
        expected_rank = 3
    elif option.task == "spectrogram_detection":
        dimensions = (0, 2, 3)
        expected_rank = 4
    else:
        return None
    if len(shape) != expected_rank or any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in shape
    ):
        return None
    samples = 1
    for index in dimensions:
        samples *= shape[index]
    return samples


class SdrControls(QWidget):
    """Compact control rail for the reviewed RTL-SDR live path."""

    start_requested = Signal(object)
    stop_requested = Signal()
    qc_requested = Signal()
    model_changed = Signal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._models: list[SdrModelOption] = []
        self._available = True
        self._availability_reason = ""
        self._developer_sources = False
        self._running = False
        self._qc_available = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        model_section = ControlSection("RTL-SDR / 已准入 OM")
        self.model_combo = model_section.add(QComboBox())
        self.model_combo.setObjectName("sdrModelCombo")
        self.model_combo.currentIndexChanged.connect(self._on_model_changed)
        self.model_info = model_section.add(QLabel("没有可用的已准入模型"))
        self.model_info.setObjectName("secondary")
        self.model_info.setProperty("textRole", "secondary")
        self.model_info.setWordWrap(True)
        self.model_info.setToolTip("仅显示通过来源、哈希、数值和实时预算检查的模型")
        layout.addWidget(model_section)

        capture_section = ControlSection("采集参数")
        self.source_combo = capture_section.add(QComboBox())
        self.source_combo.setObjectName("sdrSourceCombo")
        self.source_combo.addItem("RTL-SDR 实时采集", "rtl")
        self.source_combo.setVisible(False)
        self.source_combo.currentIndexChanged.connect(self._on_source_changed)
        self.cu8_input_row = QWidget()
        cu8_layout = QHBoxLayout(self.cu8_input_row)
        cu8_layout.setContentsMargins(0, 0, 0, 0)
        cu8_layout.setSpacing(4)
        self.cu8_input = QLineEdit()
        self.cu8_input.setObjectName("sdrCu8Input")
        self.cu8_input.setPlaceholderText("CU8 文件")
        self.cu8_browse_button = QPushButton("选择")
        self.cu8_browse_button.setObjectName("sdrCu8Browse")
        self.cu8_browse_button.clicked.connect(self._select_cu8_input)
        cu8_layout.addWidget(self.cu8_input, 1)
        cu8_layout.addWidget(self.cu8_browse_button)
        capture_section.add(self.cu8_input_row)
        self.cu8_input_row.setVisible(False)
        self.device_spin = capture_section.add(QSpinBox())
        self.device_spin.setObjectName("sdrDeviceSpin")
        self.device_spin.setToolTip("RTL-SDR 设备编号；通常为 0")
        self.device_spin.setRange(0, 99)
        self.device_spin.setPrefix("设备 ")
        self.center_frequency = capture_section.add(QDoubleSpinBox())
        self.center_frequency.setObjectName("sdrCenterFrequency")
        self.center_frequency.setRange(1.0, 10_000_000_000.0)
        self.center_frequency.setDecimals(0)
        self.center_frequency.setSingleStep(100_000.0)
        self.center_frequency.setValue(100_000_000.0)
        self.center_frequency.setSuffix(" Hz")
        self.center_frequency.setToolTip("接收机中心频率；不是射频校准结果")
        self.sample_rate = capture_section.add(QDoubleSpinBox())
        self.sample_rate.setObjectName("sdrSampleRate")
        self.sample_rate.setRange(1_000.0, 10_000_000.0)
        self.sample_rate.setDecimals(0)
        self.sample_rate.setSingleStep(128_000.0)
        self.sample_rate.setValue(2_048_000.0)
        self.sample_rate.setSuffix(" S/s")
        self.sample_rate.setToolTip("采样率由已准入模型窗口合同约束")
        self.ppm_error = capture_section.add(QSpinBox())
        self.ppm_error.setObjectName("sdrPpmError")
        self.ppm_error.setRange(-500, 500)
        self.ppm_error.setPrefix("PPM ")
        self.ppm_error.setToolTip("接收机晶振修正值")
        self.rf_input_context = capture_section.add(QComboBox())
        self.rf_input_context.setObjectName("sdrRfInputContext")
        for value, label in RF_INPUT_CONTEXT_LABELS.items():
            self.rf_input_context.addItem(label, value)
        layout.addWidget(capture_section)

        gain_section = ControlSection("增益与时长")
        self.auto_gain = gain_section.add(QCheckBox("自动增益"))
        self.auto_gain.setObjectName("sdrAutoGain")
        self.auto_gain.setChecked(True)
        self.auto_gain.toggled.connect(self._on_auto_gain_changed)
        self.gain_db = gain_section.add(QDoubleSpinBox())
        self.gain_db.setObjectName("sdrGainDb")
        self.gain_db.setRange(0.0, 100.0)
        self.gain_db.setDecimals(1)
        self.gain_db.setSingleStep(0.1)
        self.gain_db.setValue(40.2)
        self.gain_db.setSuffix(" dB")
        self.gain_db.setEnabled(False)
        self.gain_db.setToolTip("手动增益；当前值只适用于本次接收条件")
        self.duration_preset = gain_section.add(QComboBox())
        self.duration_preset.setObjectName("sdrDurationPreset")
        for seconds in (10, 60, 600):
            self.duration_preset.addItem(f"{seconds} s", float(seconds))
        self.duration_preset.addItem("自定义", None)
        self.duration_preset.currentIndexChanged.connect(self._on_duration_preset_changed)
        self.duration_seconds = gain_section.add(QSpinBox())
        self.duration_seconds.setObjectName("sdrDurationSeconds")
        self.duration_seconds.setRange(1, 600)
        self.duration_seconds.setValue(10)
        self.duration_seconds.setSuffix(" s")
        self.duration_seconds.setEnabled(False)
        self.capture_estimate = gain_section.add(QLabel())
        self.capture_estimate.setObjectName("secondary")
        self.capture_estimate.setProperty("textRole", "secondary")
        self.capture_estimate.setWordWrap(True)
        layout.addWidget(gain_section)

        actions_section = ControlSection("运行")
        self.start_button = actions_section.add(QPushButton("开始 RTL-SDR"))
        self.start_button.setObjectName("sdrStartButton")
        self.start_button.clicked.connect(self._emit_start)
        self.stop_button = actions_section.add(QPushButton("停止"))
        self.stop_button.setObjectName("sdrStopButton")
        self.stop_button.clicked.connect(self.stop_requested)
        self.stop_button.setEnabled(False)
        self.qc_button = actions_section.add(QPushButton("检查本次记录"))
        self.qc_button.setObjectName("sdrQcButton")
        self.qc_button.clicked.connect(self.qc_requested)
        self.qc_button.setEnabled(False)
        self.availability_label = actions_section.add(QLabel())
        self.availability_label.setObjectName("secondary")
        self.availability_label.setProperty("textRole", "secondary")
        self.availability_label.setWordWrap(True)
        layout.addWidget(actions_section)
        layout.addStretch(1)

        for widget in (self.sample_rate, self.center_frequency, self.duration_seconds):
            widget.valueChanged.connect(self._update_capture_estimate)
        self._update_capture_estimate()
        self._on_source_changed(self.source_combo.currentIndex())
        self._refresh_enabled_state()

    @property
    def selected_model(self) -> SdrModelOption | None:
        index = self.model_combo.currentIndex()
        return self._models[index] if 0 <= index < len(self._models) else None

    def set_models(
        self,
        models: Iterable[SdrModelOption],
        selected_model_id: str | None = None,
    ) -> None:
        previous = selected_model_id
        if previous is None and self.selected_model is not None:
            previous = self.selected_model.model_id
        self._models = list(models)
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for option in self._models:
            self.model_combo.addItem(option.label, option.model_id)
        selected_index = next(
            (index for index, option in enumerate(self._models) if option.model_id == previous),
            0,
        )
        if self._models:
            self.model_combo.setCurrentIndex(selected_index)
        self.model_combo.blockSignals(False)
        self._on_model_changed(self.model_combo.currentIndex())

    def set_developer_sources(self, enabled: bool) -> None:
        self._developer_sources = bool(enabled)
        current_source = self.source_combo.currentData()
        self.source_combo.blockSignals(True)
        self.source_combo.clear()
        self.source_combo.addItem("RTL-SDR 实时采集", "rtl")
        if self._developer_sources:
            self.source_combo.addItem("CU8 回放（开发）", "cu8")
            self.source_combo.addItem("合成 IQ（开发）", "synthetic")
        index = self.source_combo.findData(current_source)
        self.source_combo.setCurrentIndex(max(index, 0))
        self.source_combo.blockSignals(False)
        self.source_combo.setVisible(self._developer_sources)
        self._on_source_changed(self.source_combo.currentIndex())

    def set_available(self, available: bool, reason: str = "") -> None:
        self._available = bool(available)
        self._availability_reason = str(reason)
        self.availability_label.setText("" if self._available else self._availability_reason)
        self._refresh_enabled_state()

    def set_running(self, running: bool) -> None:
        self._running = bool(running)
        self._apply_input_enabled_state()
        self._refresh_action_enabled_state()

    def set_qc_available(self, available: bool) -> None:
        self._qc_available = bool(available)
        self._refresh_action_enabled_state()

    def run_request(self) -> SdrUiRunRequest:
        model = self.selected_model
        if model is None:
            raise ValueError("没有可启动的已准入 OM 模型")
        source = str(self.source_combo.currentData() or "rtl")
        if source != "rtl" and not self._developer_sources:
            raise ValueError("开发输入未启用")
        if source == "cu8" and not self.cu8_input.text().strip():
            raise ValueError("请选择 CU8 回放文件")
        return SdrUiRunRequest(
            model_id=model.model_id,
            manifest_path=model.manifest_path,
            source=source,
            device=str(self.device_spin.value()),
            center_frequency_hz=float(self.center_frequency.value()),
            sample_rate_hz=(
                model.sample_rate_hz if model.sample_rate_hz is not None else float(self.sample_rate.value())
            ),
            gain_db=None if self.auto_gain.isChecked() else float(self.gain_db.value()),
            ppm_error=int(self.ppm_error.value()),
            rf_input_context=str(self.rf_input_context.currentData()),
            duration_seconds=float(self._selected_duration_seconds()),
            input_cu8=(
                Path(self.cu8_input.text()).expanduser()
                if source == "cu8" and self.cu8_input.text().strip()
                else None
            ),
        )

    def _on_model_changed(self, _index: int) -> None:
        model = self.selected_model
        if model is None:
            self.model_info.setText("没有可用的已准入模型")
            self.sample_rate.setEnabled(False)
        else:
            shape = " x ".join(str(value) for value in model.input_shape)
            if model.sample_rate_hz is None:
                rate = "采样率由运行配置决定"
                self.sample_rate.setEnabled(True)
            else:
                rate = f"模型固定 {model.sample_rate_hz / 1e6:g} MS/s"
                self.sample_rate.setValue(model.sample_rate_hz)
                self.sample_rate.setEnabled(False)
            task = "IQ 分类" if model.task == "iq_classification" else "时频检测"
            self.model_info.setText(f"{task} · 输入 [{shape}] · {rate}")
        self._update_capture_estimate()
        self._refresh_enabled_state()
        self.model_changed.emit(model)

    def _on_auto_gain_changed(self, enabled: bool) -> None:
        self.gain_db.setEnabled(not enabled and not self._running)

    def _on_duration_preset_changed(self, _index: int) -> None:
        is_custom = self.duration_preset.currentData() is None
        self.duration_seconds.setEnabled(is_custom and not self._running)
        if not is_custom:
            self.duration_seconds.setValue(int(float(self.duration_preset.currentData())))
        self._update_capture_estimate()

    def _on_source_changed(self, _index: int) -> None:
        is_cu8 = self._developer_sources and self.source_combo.currentData() == "cu8"
        self.cu8_input_row.setVisible(is_cu8)
        if hasattr(self, "capture_estimate"):
            self._update_capture_estimate()

    def _select_cu8_input(self) -> None:
        selected, _filter = QFileDialog.getOpenFileName(
            self,
            "选择 CU8 回放文件",
            self.cu8_input.text(),
            "CU8 files (*.cu8);;All files (*)",
        )
        if selected:
            self.cu8_input.setText(selected)

    def _selected_duration_seconds(self) -> float:
        preset = self.duration_preset.currentData()
        return float(self.duration_seconds.value() if preset is None else preset)

    def _update_capture_estimate(self, *_args) -> None:
        model = self.selected_model
        rate = model.sample_rate_hz if model and model.sample_rate_hz else float(self.sample_rate.value())
        duration_seconds = self._selected_duration_seconds()
        window_samples = None if model is None else _fixed_window_samples(model)
        source = str(self.source_combo.currentData() or "rtl")
        if source == "rtl" and window_samples is not None:
            try:
                capture_plan = plan_live_capture(rate, duration_seconds, window_samples)
            except (OverflowError, ValueError):
                bytes_estimate = int(2.0 * rate * duration_seconds)
                plan_suffix = "；模型窗口计划将在启动校验"
            else:
                bytes_estimate = estimate_live_capture_bytes(
                    rate, duration_seconds, window_samples
                )
                plan_suffix = f"；整窗计划 {capture_plan.planned_capture_duration_seconds:.3f} s"
        else:
            bytes_estimate = int(2.0 * rate * duration_seconds)
            plan_suffix = "；选择有效模型后按整窗计划计算"
        if bytes_estimate >= 1024 * 1024:
            display = f"预计 CU8：{bytes_estimate / (1024 * 1024):.1f} MiB"
        else:
            display = f"预计 CU8：{bytes_estimate / 1024:.1f} KiB"
        # The service repeats this complete-window calculation with its safety
        # margin during preflight before opening rtl_sdr.
        self.capture_estimate.setText(f"{display}{plan_suffix}；启动前检查磁盘安全余量")

    def _emit_start(self) -> None:
        try:
            request = self.run_request()
        except ValueError as exc:
            self.availability_label.setText(str(exc))
            return
        self.start_requested.emit(request)

    def _refresh_enabled_state(self) -> None:
        self._apply_input_enabled_state()
        self._refresh_action_enabled_state()

    def _refresh_action_enabled_state(self) -> None:
        self.start_button.setEnabled(
            self._available and self.selected_model is not None and not self._running
        )
        self.stop_button.setEnabled(self._running)
        self.qc_button.setEnabled(
            self._available and self._qc_available and not self._running
        )

    def _apply_input_enabled_state(self) -> None:
        editable = not self._running
        for widget in (
            self.model_combo,
            self.source_combo,
            self.device_spin,
            self.center_frequency,
            self.ppm_error,
            self.rf_input_context,
            self.auto_gain,
            self.duration_preset,
        ):
            widget.setEnabled(editable)
        self.cu8_input.setEnabled(editable and self._developer_sources)
        self.cu8_browse_button.setEnabled(editable and self._developer_sources)
        model = self.selected_model
        self.sample_rate.setEnabled(editable and (model is None or model.sample_rate_hz is None))
        self.gain_db.setEnabled(editable and not self.auto_gain.isChecked())
        self.duration_seconds.setEnabled(
            editable and self.duration_preset.currentData() is None
        )
