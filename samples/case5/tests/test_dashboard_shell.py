from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest


pytestmark = pytest.mark.skipif(
    __import__("importlib").util.find_spec("PySide6") is None,
    reason="PySide6 is a board-side UI dependency",
)


@dataclass
class _HantekSnapshot:
    acquisition_state: str = "STOPPED"
    message: str = "Ready"


class _FakeAnalysis:
    def close(self) -> None:
        return None


class _FakeHantek:
    def __init__(self) -> None:
        self.analysis = _FakeAnalysis()
        self.config = type(
            "Config",
            (), {"sample_rate_hz": 1_000_000.0},
        )()
        self._snapshot = _HantekSnapshot()
        self.initialize_calls = 0

    def initialize_npu(self):
        self.initialize_calls += 1
        return None

    def start_hardware(self, _bridge: Path, **_settings) -> None:
        self._snapshot = _HantekSnapshot("RUNNING", "active")

    def start_simulation(self) -> None:
        self._snapshot = _HantekSnapshot("RUNNING", "simulated")

    def stop(self) -> None:
        self._snapshot = _HantekSnapshot()

    def close(self) -> None:
        self.stop()

    def snapshot(self):
        # The Hantek workspace only needs the fields below before a run.  This
        # fake stays intentionally minimal so construction proves no auto-start.
        from time_frequency_dashboard.controller import DashboardSnapshot
        from time_frequency_dashboard.display import ColorScaleState
        from time_frequency_dashboard.npu import NpuStatus
        import numpy as np

        return DashboardSnapshot(
            source="DISCONNECTED",
            acquisition_state=self._snapshot.acquisition_state,
            message=self._snapshot.message,
            npu_status=NpuStatus("NPU unavailable", False, "not initialized"),
            waveforms=None,
            statistics=None,
            voltage_waterfall=np.empty((0, 201), dtype=np.float32),
            current_waterfall=np.empty((0, 201), dtype=np.float32),
            frames_received=0,
            usb_blocks_received=0,
            capture_interval_ms=None,
            analysis_completed=0,
            analysis_dropped=0,
            storage_dropped=0,
            session_path=None,
            voltage_color_scale=ColorScaleState(-120.0, -80.0, True, False, 0),
            current_color_scale=ColorScaleState(-120.0, -80.0, True, False, 0),
            waterfall_history_rows=20,
            spectrum_axis_hz=np.empty(201, dtype=np.float32),
            spectrum_values=np.empty(201, dtype=np.float32),
        )


class _FakeRtl:
    def __init__(self) -> None:
        from time_frequency_dashboard.rtl_sdr_service import RtlSdrSnapshot

        self._snapshot = RtlSdrSnapshot()
        self.start_calls = 0
        self.latest_frame_calls = 0
        self.frame = None

    def start(self, _config):
        from time_frequency_dashboard.rtl_sdr_service import RtlSdrSnapshot

        self.start_calls += 1
        self._snapshot = RtlSdrSnapshot(state="running", message="active")
        return self._snapshot

    def request_stop(self):
        from time_frequency_dashboard.rtl_sdr_service import RtlSdrSnapshot

        self._snapshot = RtlSdrSnapshot(state="idle", message="stopped")

    def wait_stopped(self, timeout=None):
        del timeout
        return True

    def close(self):
        self.request_stop()

    def snapshot(self):
        return self._snapshot

    def latest_frame(self):
        self.latest_frame_calls += 1
        return self.frame


def test_dashboard_has_two_workspaces_and_never_autostarts(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from time_frequency_dashboard.instrument_coordinator import InstrumentCoordinator
    from time_frequency_dashboard.ui.main_window import DashboardWindow

    app = QApplication.instance() or QApplication([])
    settings = QSettings("Ascend310", "Case5")
    settings.setValue(DashboardWindow.SETTINGS_KEY, DashboardWindow.SDR_INDEX)
    hantek = _FakeHantek()
    rtl = _FakeRtl()
    window = DashboardWindow(
        hantek,
        tmp_path / "sigrok_bridge",
        False,
        rtl_sdr_service=rtl,
        coordinator=InstrumentCoordinator(hantek, rtl),
        sdr_models_dir=tmp_path / "models",
        sdr_output_root=tmp_path / "runs",
    )
    try:
        assert window.workspace_tabs.count() == 2
        assert window.workspace_tabs.tabText(0) == "Hantek"
        assert window.workspace_tabs.tabText(1) == "RTL-SDR"
        assert window.workspace_tabs.currentIndex() == DashboardWindow.SDR_INDEX
        assert hantek.initialize_calls == 0
        assert rtl.start_calls == 0
    finally:
        window.close()
        app.processEvents()


def test_dashboard_only_acknowledges_a_frame_after_visible_unpaused_render(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    from time_frequency_dashboard.instrument_coordinator import InstrumentCoordinator
    from time_frequency_dashboard.rtl_sdr_service import RtlSdrDisplayFrame, RtlSdrSnapshot
    from time_frequency_dashboard.ui.main_window import DashboardWindow

    app = QApplication.instance() or QApplication([])
    settings = QSettings("Ascend310", "Case5")
    settings.setValue(DashboardWindow.SETTINGS_KEY, DashboardWindow.HANTEK_INDEX)
    hantek = _FakeHantek()
    rtl = _FakeRtl()
    rtl._snapshot = RtlSdrSnapshot(
        state="running",
        generation=7,
        source="rtl",
        model_id="test-classifier",
        model_task="iq_classification",
    )

    def display_frame(sequence: int) -> RtlSdrDisplayFrame:
        return RtlSdrDisplayFrame(
            generation=7,
            sequence=sequence,
            source_sample_offset=0,
            samples=np.asarray([0.5 + 0.25j, -0.25 - 0.5j], dtype=np.complex64),
            model_input=np.asarray([[[0.5, -0.25], [0.25, -0.5]]], dtype=np.float32),
            model_iq=np.asarray([[[0.5, -0.25], [0.25, -0.5]]], dtype=np.float32),
            spectrogram_image=None,
            top_k=({"label": "QPSK", "confidence": 0.9, "class_index": 0},),
            detections=(),
            sample_rate_hz=2_048_000.0,
            center_frequency_hz=100_000_000.0,
            batch_duration_ms=1.0,
            completed_monotonic_ns=0,
        )

    window = DashboardWindow(
        hantek,
        tmp_path / "sigrok_bridge",
        False,
        rtl_sdr_service=rtl,
        coordinator=InstrumentCoordinator(hantek, rtl),
        sdr_models_dir=tmp_path / "models",
        sdr_output_root=tmp_path / "runs",
    )
    try:
        rtl.frame = display_frame(1)
        rtl.latest_frame_calls = 0
        window.refresh()
        assert rtl.latest_frame_calls == 0

        # A fake service without the acknowledgement method remains supported.
        window.workspace_tabs.setCurrentIndex(DashboardWindow.SDR_INDEX)
        window.refresh()
        assert rtl.latest_frame_calls == 1

        acknowledgements = []
        rtl.acknowledge_display_frame = lambda generation, sequence: acknowledgements.append(
            (generation, sequence)
        )
        rtl.frame = display_frame(2)
        window.refresh()
        assert acknowledgements == [(7, 2)]

        window.sdr_workspace.set_display_paused(True)
        rtl.frame = display_frame(3)
        window.refresh()
        assert acknowledgements == [(7, 2)]
    finally:
        window.close()
        app.processEvents()


def test_dashboard_rejects_developer_sdr_source_without_explicit_switch(monkeypatch, tmp_path):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from time_frequency_dashboard.instrument_coordinator import InstrumentCoordinator
    from time_frequency_dashboard.ui.main_window import DashboardWindow
    from time_frequency_dashboard.ui.sdr_controls import SdrUiRunRequest

    app = QApplication.instance() or QApplication([])
    hantek = _FakeHantek()
    rtl = _FakeRtl()
    window = DashboardWindow(
        hantek,
        tmp_path / "sigrok_bridge",
        False,
        rtl_sdr_service=rtl,
        coordinator=InstrumentCoordinator(hantek, rtl),
        sdr_models_dir=tmp_path / "models",
        sdr_output_root=tmp_path / "runs",
    )
    try:
        # Do not display a modal dialog in the offscreen test; only assert the
        # UI boundary does not reserve or start a non-live source.
        window._show_start_error = lambda *_args: None
        window._start_sdr(
            SdrUiRunRequest(
                model_id="test",
                manifest_path=tmp_path / "missing.manifest.json",
                source="synthetic",
                device="0",
                center_frequency_hz=100_000_000.0,
                sample_rate_hz=8_000.0,
                gain_db=None,
                ppm_error=0,
                rf_input_context="unknown",
                duration_seconds=10.0,
            )
        )
        assert rtl.start_calls == 0
        assert window.coordinator.snapshot().active_source is None
    finally:
        window.close()
        app.processEvents()
