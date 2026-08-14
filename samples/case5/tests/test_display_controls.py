from __future__ import annotations

from collections import deque
from dataclasses import replace
from pathlib import Path
import tempfile

import numpy as np
import numpy.testing as npt
import pytest

from time_frequency_dashboard.config import Case5Config
from time_frequency_dashboard.controller import Case5Controller
from time_frequency_dashboard.display import AutoColorScale, band_energy_to_db
from time_frequency_dashboard.model.npu_spectrum_numpy_reference import hann_periodogram_power


def test_band_energy_to_db_uses_one_volt_squared_reference_without_mutating_input():
    energy = np.asarray([1.0, 100.0, 1.0e-20], dtype=np.float32)
    original = energy.copy()

    actual = band_energy_to_db(energy)

    npt.assert_allclose(actual, [0.0, 20.0, -120.0], atol=1.0e-5)
    npt.assert_array_equal(energy, original)


@pytest.mark.parametrize(
    ("energy", "reference", "floor"),
    [
        (np.asarray([np.nan], dtype=np.float32), 1.0, -120.0),
        (np.asarray([1.0], dtype=np.float32), float("nan"), -120.0),
        (np.asarray([1.0], dtype=np.float32), 1.0, float("inf")),
        (np.asarray([1.0], dtype=np.float32), True, -120.0),
        (np.asarray([1.0], dtype=np.float32), 1.0, 10_000.0),
        (np.asarray([1.0], dtype=np.float32), 1.0, -10_000.0),
    ],
)
def test_band_energy_to_db_rejects_nonfinite_or_boolean_inputs(energy, reference, floor):
    with pytest.raises(ValueError, match="finite|unusable"):
        band_energy_to_db(
            energy,
            reference_energy_v_squared=reference,
            floor_db=floor,
        )


@pytest.mark.parametrize(
    ("calibration_rows", "floor_db", "minimum_span_db"),
    [
        (True, -120.0, 40.0),
        (20, float("nan"), 40.0),
        (20, -120.0, float("inf")),
        (20, -120.0, True),
    ],
)
def test_auto_color_scale_rejects_invalid_constructor_values(
    calibration_rows, floor_db, minimum_span_db
):
    with pytest.raises(ValueError, match="positive|finite"):
        AutoColorScale(
            calibration_rows=calibration_rows,
            floor_db=floor_db,
            minimum_span_db=minimum_span_db,
        )


def test_npu_spectrum_reference_resolves_cal_square_wave_fundamental_and_harmonics():
    sample_rate_hz = 1_000_000.0
    samples = 10_000
    time = np.arange(samples, dtype=np.float32) / sample_rate_hz
    square = np.where(np.sin(2.0 * np.pi * 1_000.0 * time) >= 0.0, 1.0, -1.0)
    waveforms = np.stack((square, square))[None, :, :]

    spectrum_power = hann_periodogram_power(
        waveforms - waveforms.mean(axis=2, keepdims=True),
        sample_rate_hz=sample_rate_hz,
        max_frequency_hz=20_000.0,
    )
    values = spectrum_power[0, 0, :, 0]
    axis_hz = np.arange(values.size, dtype=np.float32) * sample_rate_hz / samples

    fundamental = int(np.argmax(values[1:])) + 1
    assert axis_hz[fundamental] == pytest.approx(1_000.0)
    assert values[np.searchsorted(axis_hz, 3_000.0)] > values[np.searchsorted(axis_hz, 2_000.0)]


def test_auto_color_scale_collects_twenty_rows_then_locks_and_manual_can_reset():
    scale = AutoColorScale()
    first = scale.observe(np.asarray([-100.0, -95.0], dtype=np.float32))
    for value in range(2, 20):
        provisional = scale.observe(np.asarray([-100.0 + value, -95.0 + value], dtype=np.float32))
    locked = scale.observe(np.asarray([-60.0, -50.0], dtype=np.float32))

    assert first.auto_enabled and not first.locked and first.observed_rows == 1
    assert provisional.auto_enabled and not provisional.locked and provisional.observed_rows == 19
    assert locked.auto_enabled and locked.locked and locked.observed_rows == 20
    assert locked.high_db - locked.low_db >= 40.0
    assert locked.low_db >= -120.0

    manual = scale.set_manual(-90.0, -30.0)
    assert not manual.auto_enabled and manual.locked
    assert manual.low_db == pytest.approx(-90.0)
    reset = scale.reset_auto()
    assert reset.auto_enabled and not reset.locked and reset.observed_rows == 0


def test_auto_color_scale_uses_floor_and_minimum_span_for_silent_rows():
    scale = AutoColorScale(calibration_rows=1)
    state = scale.observe(np.full(32, -120.0, dtype=np.float32))
    assert state.locked
    assert state.low_db == pytest.approx(-120.0)
    assert state.high_db == pytest.approx(-80.0)


def test_controller_resizes_only_bounded_display_history():
    controller = Case5Controller(Case5Config(), Path("missing.om"))
    controller._voltage_rows = deque((np.asarray([value], dtype=np.float32) for value in range(25)), maxlen=180)
    controller._current_rows = deque((np.asarray([value], dtype=np.float32) for value in range(25)), maxlen=180)

    assert controller.set_waterfall_history_rows(20) == 20
    snapshot = controller.snapshot()
    assert snapshot.waterfall_history_rows == 20
    assert snapshot.voltage_waterfall.shape == (20, 1)
    npt.assert_array_equal(snapshot.voltage_waterfall[:, 0], np.arange(5.0, 25.0))
    controller.close()


def test_controller_passes_preconnection_sigrok_settings_to_capture(monkeypatch):
    created = {}

    class FakeCapture:
        def __init__(self, **kwargs):
            created.update(kwargs)

        def start(self):
            return None

        def stop(self):
            return None

    monkeypatch.setattr("time_frequency_dashboard.controller.SigrokCapture", FakeCapture)
    with tempfile.TemporaryDirectory() as directory:
        config = replace(Case5Config(), session_root=Path(directory))
        controller = Case5Controller(config, Path("missing.om"))
        controller.start_hardware(
            Path("build/sigrok_capture_bridge"),
            ch1_volts_per_division=0.5,
            ch1_probe_ratio=10.0,
        )
        assert created["bridge_path"] == Path("build/sigrok_capture_bridge")
        assert created["callback_msec"] == 40
        assert created["ch1_volts_per_division"] == pytest.approx(0.5)
        assert created["ch1_probe_ratio"] == pytest.approx(10.0)
        controller.close()


@pytest.mark.skipif(
    __import__("importlib").util.find_spec("PySide6") is None
    or __import__("importlib").util.find_spec("pyqtgraph") is None,
    reason="PySide6/pyqtgraph are board-side UI dependencies",
)
def test_qt_analysis_controls_and_histogram_are_available(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from time_frequency_dashboard.ui.controls import HantekControls
    from time_frequency_dashboard.ui.plot_views import SpectrumPlot, WaterfallPlot

    application = QApplication.instance() or QApplication([])
    controls = HantekControls()
    assert controls.selected_analysis_channel() == 0
    controls.ch2_visible.setChecked(True)
    assert controls.analysis_channel.count() == 2

    waterfall = WaterfallPlot()
    waterfall.set_rows(
        np.asarray([[-80.0, -60.0], [-70.0, -50.0]], dtype=np.float32),
        np.asarray([10_000.0, 30_000.0], dtype=np.float32),
        (-100.0, -20.0),
    )
    assert waterfall.levels == pytest.approx((-100.0, -20.0))
    spectrum = SpectrumPlot()
    spectrum.set_peak_hold(True)
    spectrum.set_data(np.asarray([10_000.0, 30_000.0]), np.asarray([-70.0, -50.0]))
    assert spectrum._peak_values is not None
    application.processEvents()
