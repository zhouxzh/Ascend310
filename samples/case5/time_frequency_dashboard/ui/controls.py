"""Hantek 6022BE controls for the current sigrok dashboard backend."""

from __future__ import annotations

from ..display import ColorScaleState

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QLabel,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class ControlSection(QWidget):
    """Flat labelled section; the rail intentionally avoids nested cards."""

    def __init__(self, title: str, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)
        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        layout.addWidget(title_label)
        self.body = QVBoxLayout()
        self.body.setSpacing(5)
        layout.addLayout(self.body)

    def add(self, widget: QWidget) -> QWidget:
        self.body.addWidget(widget)
        return widget


class HantekControls(QWidget):
    ch2_display_changed = Signal(bool)
    analysis_channel_changed = Signal(int)
    auto_scale_requested = Signal()
    color_levels_changed = Signal(float, float)
    history_rows_changed = Signal(int)
    peak_hold_changed = Signal(bool)
    spectrum_reset_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        scope_section = ControlSection("Hantek 6022BE / sigrok")
        self.scope_info = scope_section.add(
            QLabel("连续 sigrok：1 MS/s · NPU 窗口 10,000 点 · DFT 0--20 kHz · 201 点")
        )
        self.scope_info.setWordWrap(True)
        self.ch1_info = scope_section.add(QLabel("CH1 电压始终采集并显示"))
        self.ch2_visible = scope_section.add(QCheckBox("显示 CH2 电流"))
        self.ch2_visible.setChecked(False)
        self.ch2_visible.toggled.connect(self._on_ch2_visibility_changed)
        self.analysis_channel = scope_section.add(QComboBox())
        self.analysis_channel.addItem("分析通道：CH1 电压", 0)
        self.analysis_channel.currentIndexChanged.connect(
            lambda _index: self.analysis_channel_changed.emit(self.selected_analysis_channel())
        )
        self.scope_info = scope_section.add(
            QLabel("单次连续 session，无硬件触发；CH2 未显示时仍完成单位换算和 NPU 分析")
        )
        self.scope_info.setWordWrap(True)
        layout.addWidget(scope_section)

        capture_section = ControlSection("开始前采集参数")
        self.scope_volts_div = self._scale_combo()
        capture_section.add(self.scope_volts_div)
        self.scope_probe_ratio = QComboBox()
        for ratio in (1.0, 10.0, 100.0):
            self.scope_probe_ratio.addItem(f"CH1 探头倍率：{ratio:g}x", ratio)
        capture_section.add(self.scope_probe_ratio)
        capture_note = capture_section.add(
            QLabel("采样率和 10 ms 分析窗口由 OM 固定；sigrok 实际返回值不匹配时拒绝分析。")
        )
        capture_note.setWordWrap(True)
        layout.addWidget(capture_section)

        display_section = ControlSection("频带显示")
        self.waterfall_history = QSpinBox()
        self.waterfall_history.setRange(20, 500)
        self.waterfall_history.setValue(180)
        self.waterfall_history.setSuffix(" 行瀑布历史")
        self.waterfall_history.valueChanged.connect(self.history_rows_changed)
        display_section.add(self.waterfall_history)
        self.peak_hold = display_section.add(QCheckBox("峰值保持"))
        self.peak_hold.toggled.connect(self.peak_hold_changed)
        self.full_spectrum_button = display_section.add(QPushButton("恢复全频段"))
        self.full_spectrum_button.clicked.connect(self.spectrum_reset_requested)
        self.auto_scale_button = display_section.add(QPushButton("自动 dB 色标"))
        self.auto_scale_button.clicked.connect(self.auto_scale_requested)
        self.color_status = display_section.add(QLabel("自动：等待 0/20 行"))
        self.color_low = display_section.add(QDoubleSpinBox())
        self.color_low.setRange(-160.0, 80.0)
        self.color_low.setDecimals(1)
        self.color_low.setValue(-120.0)
        self.color_low.setSuffix(" dB 下限")
        self.color_high = display_section.add(QDoubleSpinBox())
        self.color_high.setRange(-160.0, 80.0)
        self.color_high.setDecimals(1)
        self.color_high.setValue(-80.0)
        self.color_high.setSuffix(" dB 上限")
        self.color_low.valueChanged.connect(self._emit_color_levels)
        self.color_high.valueChanged.connect(self._emit_color_levels)
        display_note = display_section.add(QLabel("dB 相对 1 V²，未校准；不是 dBV、dBFS 或 dBm。"))
        display_note.setWordWrap(True)
        layout.addWidget(display_section)

        layout.addStretch(1)

    @staticmethod
    def _scale_combo() -> QComboBox:
        combo = QComboBox()
        for volts_per_division in (0.1, 0.25, 0.5, 1.0):
            combo.addItem(f"CH1 量程：{volts_per_division:g} V/div", volts_per_division)
        combo.setCurrentIndex(3)
        return combo

    def set_running(self, running: bool) -> None:
        self.scope_volts_div.setEnabled(not running)
        self.scope_probe_ratio.setEnabled(not running)

    def sigrok_capture_settings(self) -> dict[str, float]:
        return {
            "ch1_volts_per_division": float(self.scope_volts_div.currentData()),
            "ch1_probe_ratio": float(self.scope_probe_ratio.currentData()),
        }

    def selected_analysis_channel(self) -> int:
        return int(self.analysis_channel.currentData())

    def set_color_scale_state(self, state: ColorScaleState) -> None:
        self.color_low.blockSignals(True)
        self.color_high.blockSignals(True)
        self.color_low.setValue(state.low_db)
        self.color_high.setValue(state.high_db)
        self.color_low.blockSignals(False)
        self.color_high.blockSignals(False)
        if not state.auto_enabled:
            text = "手动色标"
        elif state.locked:
            text = f"自动：已锁定（{state.observed_rows}/20 行）"
        else:
            text = f"自动：估计中（{state.observed_rows}/20 行）"
        self.color_status.setText(text)

    def _on_ch2_visibility_changed(self, visible: bool) -> None:
        current_channel = self.selected_analysis_channel()
        self.analysis_channel.blockSignals(True)
        self.analysis_channel.clear()
        self.analysis_channel.addItem("分析通道：CH1 电压", 0)
        if visible:
            self.analysis_channel.addItem("分析通道：CH2 电流", 1)
        self.analysis_channel.setCurrentIndex(1 if visible and current_channel == 1 else 0)
        self.analysis_channel.blockSignals(False)
        self.ch2_display_changed.emit(visible)
        self.analysis_channel_changed.emit(self.selected_analysis_channel())

    def _emit_color_levels(self, _value: float) -> None:
        low, high = self.color_low.value(), self.color_high.value()
        if high > low:
            self.color_levels_changed.emit(low, high)
