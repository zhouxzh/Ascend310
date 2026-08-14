"""Hantek-only presentation workspace used by the top-level dashboard shell."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import numpy as np
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..controller import Case5Controller, DashboardSnapshot
from .controls import HantekControls
from .plot_views import SpectrumPlot, WaterfallPlot, WaveformPlot
from .theme import CURRENT, ERROR, GOOD, MUTED, VOLTAGE


class HantekWorkspace(QWidget):
    """Render the existing sigrok/DFT path without owning its runtime."""

    start_requested = Signal(object)
    simulation_requested = Signal()
    stop_requested = Signal()

    def __init__(
        self,
        controller: Case5Controller,
        sigrok_bridge: Path,
        allow_simulation: bool,
        parent=None,
        *,
        on_start: Callable[[dict[str, float]], None] | None = None,
        on_simulation: Callable[[], None] | None = None,
        on_stop: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self.controller = controller
        self.sigrok_bridge = Path(sigrok_bridge)
        self.allow_simulation = bool(allow_simulation)
        self._display_paused = False
        self._analysis_channel = 0
        self._ch2_visible = False
        self._rendered_frame_count = -1
        self._rendered_analysis_count = -1
        self._available = True
        self._availability_reason = ""
        self._build_ui()
        if on_start is not None:
            self.start_requested.connect(on_start)
        if on_simulation is not None:
            self.simulation_requested.connect(on_simulation)
        if on_stop is not None:
            self.stop_requested.connect(on_stop)

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        top_bar = QFrame()
        top_bar.setObjectName("topBar")
        top = QHBoxLayout(top_bar)
        top.setContentsMargins(10, 6, 10, 6)
        self.device_label = QLabel("Device: Hantek 6022BE (sigrok)")
        self.device_label.setObjectName("badge")
        self.npu_label = QLabel("NPU unavailable")
        self.npu_label.setObjectName("badge")
        self.status_label = QLabel("Ready")
        self.status_label.setWordWrap(True)
        self.connect_button = QPushButton("Connect")
        self.connect_button.clicked.connect(self._emit_hardware_start)
        self.pause_button = QPushButton("Pause display")
        self.pause_button.clicked.connect(self.toggle_display_pause)
        self.stop_button = QPushButton("Stop")
        self.stop_button.clicked.connect(self.stop_requested)
        top.addWidget(self.device_label)
        top.addWidget(self.npu_label)
        top.addWidget(self.status_label, 1)
        top.addWidget(self.connect_button)
        top.addWidget(self.pause_button)
        top.addWidget(self.stop_button)
        if self.allow_simulation:
            self.simulation_button = QPushButton("Simulation")
            self.simulation_button.clicked.connect(self.simulation_requested)
            top.addWidget(self.simulation_button)
        else:
            self.simulation_button = None
        layout.addWidget(top_bar)

        body = QHBoxLayout()
        body.setSpacing(8)
        self.controls = HantekControls()
        self.controls.setObjectName("controlRail")
        self.controls.setFixedWidth(308)
        self.controls.ch2_display_changed.connect(self.set_ch2_visible)
        self.controls.analysis_channel_changed.connect(self.set_analysis_channel)
        self.controls.auto_scale_requested.connect(self.reset_auto_scale)
        self.controls.color_levels_changed.connect(self.set_manual_color_levels)
        self.controls.history_rows_changed.connect(self.set_waterfall_history_rows)
        body.addWidget(self.controls)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self._build_views()
        self.controls.peak_hold_changed.connect(self.spectrum_plot.set_peak_hold)
        self.controls.spectrum_reset_requested.connect(self.spectrum_plot.reset_view)
        body.addWidget(self.tabs, 1)
        layout.addLayout(body, 1)

        status_bar = QFrame()
        status_bar.setObjectName("statusBar")
        status_layout = QHBoxLayout(status_bar)
        status_layout.setContentsMargins(10, 5, 10, 5)
        self.pipeline_label = QLabel("Frames: 0 | dropped: 0")
        self.latency_label = QLabel("NPU: -- | end-to-end: --")
        self.session_label = QLabel("Session: --")
        self.session_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        status_layout.addWidget(self.pipeline_label)
        status_layout.addWidget(self.latency_label)
        status_layout.addWidget(self.session_label, 1)
        layout.addWidget(status_bar)

    def _build_views(self) -> None:
        waveform_page = QWidget()
        waveform_layout = QVBoxLayout(waveform_page)
        waveform_layout.setContentsMargins(4, 4, 4, 4)
        self.voltage_wave = WaveformPlot("CH1 voltage", VOLTAGE, "V")
        self.current_wave = WaveformPlot("CH2 current", CURRENT, "A")
        waveform_layout.addWidget(self.voltage_wave, 1)
        waveform_layout.addWidget(self.current_wave, 1)
        self.current_wave.setVisible(False)
        self.tabs.addTab(waveform_page, "Waveforms")

        analysis_page = QWidget()
        analysis_layout = QVBoxLayout(analysis_page)
        analysis_layout.setContentsMargins(4, 4, 4, 4)
        header = QHBoxLayout()
        self.analysis_title = QLabel("CH1 voltage | NPU DFT spectrum power")
        self.analysis_title.setObjectName("sectionTitle")
        self.analysis_hint = QLabel("NPU DFT spectrum and waterfall; dB re 1 V^2 (uncalibrated)")
        self.analysis_hint.setStyleSheet(f"color: {MUTED}; padding: 2px 6px;")
        header.addWidget(self.analysis_title)
        header.addWidget(self.analysis_hint, 1)
        analysis_layout.addLayout(header)
        splitter = QSplitter(Qt.Vertical)
        self.spectrum_plot = SpectrumPlot()
        self.analysis_waterfall = WaterfallPlot()
        self.analysis_waterfall.levels_changed.connect(self.set_manual_color_levels)
        splitter.addWidget(self.spectrum_plot)
        splitter.addWidget(self.analysis_waterfall)
        splitter.setStretchFactor(0, 35)
        splitter.setStretchFactor(1, 65)
        splitter.setSizes([330, 610])
        analysis_layout.addWidget(splitter, 1)
        self.tabs.addTab(analysis_page, "Spectrum and waterfall")

    def set_available(self, available: bool, reason: str = "") -> None:
        self._available = bool(available)
        self._availability_reason = str(reason)
        self._apply_running_state(self.controls is not None and self.stop_button.isEnabled())
        if not self._available and reason:
            self.status_label.setText(reason)

    def render(
        self,
        snapshot: DashboardSnapshot,
        *,
        coordinator_state: str = "IDLE",
        coordinator_active: bool = False,
    ) -> None:
        # Capture may have failed after the coordinator acquired ownership.
        # Keep Stop available in that state so the owner can finish cleanup
        # before another physical source is allowed to start.
        running = snapshot.acquisition_state == "RUNNING" or coordinator_active
        self._apply_running_state(running)
        source_title = {
            "SIGROK": "Device: Hantek 6022BE (sigrok)",
            "SIMULATED": "Device: simulated data",
            "DISCONNECTED": "Device: Hantek 6022BE (sigrok)",
        }.get(snapshot.source, f"Device: {snapshot.source}")
        self.device_label.setText(source_title)
        self.npu_label.setText(snapshot.npu_status.backend)
        self.npu_label.setStyleSheet(
            f"background: {GOOD if snapshot.npu_status.ready else ERROR}; padding: 7px 10px;"
        )
        message = snapshot.message
        if coordinator_state not in {"IDLE", "RUNNING"}:
            message = f"{coordinator_state}: {message}"
        self.status_label.setText(message)
        drops = snapshot.analysis_dropped + snapshot.storage_dropped
        self.pipeline_label.setText(
            f"Frames: {snapshot.frames_received} | USB blocks: {snapshot.usb_blocks_received} | dropped: {drops}"
        )
        latency = snapshot.npu_status.last_latency_ms
        end_to_end = snapshot.analysis_latency_ms
        if latency is None or end_to_end is None:
            self.latency_label.setText("NPU: -- | end-to-end: --")
        else:
            self.latency_label.setText(f"NPU: {latency:.2f} ms | end-to-end: {end_to_end:.2f} ms")
        self.session_label.setText("Session: --" if snapshot.session_path is None else f"Session: {snapshot.session_path}")
        if self._display_paused:
            return
        if snapshot.frames_received != self._rendered_frame_count and snapshot.waveforms is not None:
            self.voltage_wave.set_values(snapshot.waveforms[0], self.controller.config.sample_rate_hz)
            if self._ch2_visible:
                self.current_wave.set_values(snapshot.waveforms[1], self.controller.config.sample_rate_hz)
            self._rendered_frame_count = snapshot.frames_received
        if snapshot.analysis_completed != self._rendered_analysis_count:
            self._render_analysis(snapshot)
            self._rendered_analysis_count = snapshot.analysis_completed

    def toggle_display_pause(self) -> None:
        self._display_paused = not self._display_paused
        self.pause_button.setText("Resume display" if self._display_paused else "Pause display")

    def reset_views(self) -> None:
        self.voltage_wave.reset_view()
        self.current_wave.reset_view()
        self.spectrum_plot.reset_view()
        self.analysis_waterfall.reset_view()

    def set_ch2_visible(self, visible: bool) -> None:
        self._ch2_visible = bool(visible)
        self.current_wave.setVisible(self._ch2_visible)
        if not self._ch2_visible and self._analysis_channel == 1:
            self.set_analysis_channel(0)
        self._rendered_frame_count = -1

    def set_analysis_channel(self, channel: int) -> None:
        self._analysis_channel = 1 if int(channel) == 1 and self._ch2_visible else 0
        self.analysis_title.setText(
            "CH2 current | NPU DFT spectrum power"
            if self._analysis_channel
            else "CH1 voltage | NPU DFT spectrum power"
        )
        self.spectrum_plot.clear_peak_hold()
        self._rendered_analysis_count = -1

    def reset_auto_scale(self) -> None:
        state = self.controller.reset_auto_color_scale(self._analysis_channel)
        self.controls.set_color_scale_state(state)
        self._rendered_analysis_count = -1

    def set_manual_color_levels(self, low_db: float, high_db: float) -> None:
        try:
            state = self.controller.set_manual_color_scale(self._analysis_channel, low_db, high_db)
        except ValueError:
            return
        self.controls.set_color_scale_state(state)

    def set_waterfall_history_rows(self, rows: int) -> None:
        try:
            self.controller.set_waterfall_history_rows(rows)
        except ValueError as exc:
            self.status_label.setText(str(exc))
            return
        self._rendered_analysis_count = -1

    def _emit_hardware_start(self) -> None:
        if not self._available:
            QMessageBox.warning(self, "Hantek 6022BE", self._availability_reason or "Another instrument is active")
            return
        self.start_requested.emit(self.controls.sigrok_capture_settings())

    def _apply_running_state(self, running: bool) -> None:
        self.controls.set_running(running)
        self.connect_button.setEnabled(not running and self._available)
        if self.simulation_button is not None:
            self.simulation_button.setEnabled(not running and self._available)
        self.stop_button.setEnabled(running)

    def _render_analysis(self, snapshot: DashboardSnapshot) -> None:
        if self._analysis_channel == 0:
            rows, scale = snapshot.voltage_waterfall, snapshot.voltage_color_scale
        else:
            rows, scale = snapshot.current_waterfall, snapshot.current_color_scale
        self.analysis_waterfall.set_rows(rows, snapshot.spectrum_axis_hz, (scale.low_db, scale.high_db))
        latest = rows[-1] if rows.size else np.empty(0, dtype=np.float32)
        self.spectrum_plot.set_data(snapshot.spectrum_axis_hz, latest)
        self.controls.set_color_scale_state(scale)
