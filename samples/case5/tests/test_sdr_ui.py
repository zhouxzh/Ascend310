from __future__ import annotations

from pathlib import Path

import numpy as np
import numpy.testing as npt
import pytest


pytestmark = pytest.mark.skipif(
    __import__("importlib").util.find_spec("PySide6") is None,
    reason="PySide6 is a board-side UI dependency",
)


def test_iq_display_sampling_and_constellation_preprocessing_are_bounded():
    from time_frequency_dashboard.ui.sdr_views import (
        MAX_DISPLAY_SAMPLES,
        constellation_samples_from_model_input,
        sample_iq_for_display,
    )

    source = np.arange(10_000, dtype=np.float32).astype(np.complex64) * (1.0 + 1.0j)
    drawn = sample_iq_for_display(source)
    assert drawn.size == MAX_DISPLAY_SAMPLES
    assert drawn[0] == source[0]
    assert drawn[-1] == source[-1]

    model_input = np.asarray([[[1.0, 2.0], [-2.0, -3.0]]], dtype=np.float32)
    constellation = constellation_samples_from_model_input(model_input, source)
    npt.assert_allclose(constellation, np.asarray([1.0 - 2.0j, 2.0 - 3.0j]))


def test_spectrogram_detection_mapping_clips_image_coordinates():
    from time_frequency_dashboard.ui.sdr_views import map_detection_box

    mapped = map_detection_box(
        {"label": "FM", "confidence": 0.75, "box_xyxy": [-20.0, 100.0, 1200.0, 900.0]},
        image_width=1024,
        image_height=1024,
        batch_duration_ms=512.0,
        center_frequency_hz=100_000_000.0,
        sample_rate_hz=2_048_000.0,
    )

    assert mapped is not None
    assert mapped.time_start_s == pytest.approx(0.0)
    assert mapped.time_end_s == pytest.approx(0.512)
    assert mapped.frequency_low_hz == pytest.approx(99_224_000.0)
    assert mapped.frequency_high_hz == pytest.approx(100_824_000.0)


def test_detection_overlay_captions_are_bounded_and_spaced(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("pyqtgraph")
    from PySide6.QtWidgets import QApplication

    from time_frequency_dashboard.ui.sdr_views import (
        MAX_DETECTION_CAPTIONS,
        PhysicalDetectionBox,
        SpectrogramDetectionPlot,
    )

    app = QApplication.instance() or QApplication([])
    view = SpectrogramDetectionPlot()
    view._boxes = [
        PhysicalDetectionBox(
            label="candidate",
            confidence=1.0 - index / 20.0,
            image_box_xyxy=(0.0, 0.0, 1.0, 1.0),
            time_start_s=index * 0.01,
            time_end_s=index * 0.01 + 0.02,
            frequency_low_hz=99_900_000.0,
            frequency_high_hz=100_000_000.0,
        )
        for index in range(16)
    ]

    captions = view._caption_boxes()

    assert len(captions) <= MAX_DETECTION_CAPTIONS
    assert captions[0].confidence == pytest.approx(1.0)
    assert all(
        later.time_start_s - earlier.time_start_s >= 0.025
        for earlier, later in zip(captions, captions[1:])
    )
    view.close()
    app.processEvents()


@pytest.mark.parametrize("center_frequency_hz", (float("nan"), 0.0, -1.0))
def test_spectrogram_mapping_rejects_invalid_rf_axis_values(center_frequency_hz):
    from time_frequency_dashboard.ui.sdr_views import map_detection_box

    with pytest.raises(ValueError, match="finite and positive"):
        map_detection_box(
            {"label": "FM", "confidence": 0.75, "box_xyxy": [1.0, 1.0, 4.0, 4.0]},
            image_width=8,
            image_height=8,
            batch_duration_ms=10.0,
            center_frequency_hz=center_frequency_hz,
            sample_rate_hz=2_048_000.0,
        )


def test_sdr_controls_lock_manifest_sample_rate_and_emit_typed_request(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from time_frequency_dashboard.ui.sdr_controls import SdrControls, SdrModelOption

    app = QApplication.instance() or QApplication([])
    controls = SdrControls()
    controls.set_models(
        [
            SdrModelOption(
                model_id="detector",
                manifest_path=Path("models/detector.manifest.json"),
                task="spectrogram_detection",
                input_shape=(1, 3, 1024, 1024),
                sample_rate_hz=2_048_000.0,
            )
        ]
    )
    seen = []
    controls.start_requested.connect(seen.append)
    assert not controls.sample_rate.isEnabled()
    controls.start_button.click()
    app.processEvents()

    assert len(seen) == 1
    request = seen[0]
    assert request.model_id == "detector"
    assert request.sample_rate_hz == pytest.approx(2_048_000.0)
    assert request.gain_db is None
    assert request.duration_seconds == pytest.approx(10.0)
    assert "10.240 s" in controls.capture_estimate.text()
    assert "40.0 MiB" in controls.capture_estimate.text()
    assert "磁盘安全余量" in controls.capture_estimate.text()
    assert not controls.qc_button.isEnabled()

    controls.set_qc_available(True)
    assert controls.qc_button.isEnabled()
    controls.set_running(True)
    assert not controls.qc_button.isEnabled()
    assert controls.stop_button.isEnabled()
    controls.set_running(False)
    assert controls.qc_button.isEnabled()
    controls.set_available(False, "Hantek is running")
    assert not controls.qc_button.isEnabled()


def test_sdr_workspace_renders_classification_and_detection_frames(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("pyqtgraph")
    from PySide6.QtWidgets import QApplication

    from time_frequency_dashboard.ui.sdr_workspace import SdrWorkspace

    app = QApplication.instance() or QApplication([])
    workspace = SdrWorkspace()
    ready_status = {"backend": "NPU (test)", "ready": True, "last_latency_ms": 1.0}
    snapshot = {
        "state": "RUNNING",
        "generation": 2,
        "model_id": "classifier",
        "model_task": "iq_classification",
        "npu_status": ready_status,
        "produced_batches": 1,
        "completed_batches": 1,
        "result_path": None,
    }
    classification_frame = {
        "generation": 2,
        "sequence": 1,
        "iq_samples": np.asarray([1.0 + 2.0j, -1.0 - 2.0j], dtype=np.complex64),
        "model_iq": np.asarray([[[0.5, -0.5], [0.25, -0.25]]], dtype=np.float32),
        "sample_rate_hz": 2_048_000.0,
        "task": "iq_classification",
        "top_k": [[{"label": "QPSK", "confidence": 0.9, "class_index": 3}]],
    }
    assert workspace.render(snapshot, classification_frame) is True
    assert workspace.results.table.rowCount() == 1
    assert workspace.results.table.item(0, 0).text() == "QPSK"
    assert "模型前处理" in workspace.iq_context_label.text()
    assert workspace.render(snapshot, classification_frame) is False

    detection_frame = {
        "generation": 2,
        "sequence": 2,
        "iq_samples": np.ones(16, dtype=np.complex64),
        "sample_rate_hz": 2_048_000.0,
        "center_frequency_hz": 100_000_000.0,
        "batch_duration_ms": 512.0,
        "task": "spectrogram_detection",
        "spectrogram_image": np.ones((3, 8, 8), dtype=np.float32),
        "detections": [{"label": "signal", "confidence": 0.8, "box_xyxy": [1.0, 2.0, 7.0, 6.0]}],
    }
    assert workspace.render(
        {**snapshot, "model_task": "spectrogram_detection"}, detection_frame
    ) is True
    assert len(workspace.spectrogram_plot.physical_boxes) == 1
    assert workspace.results.table.rowCount() == 1
    assert workspace.results.recent_detections[0].batch_sequence == 2
    assert "原始捕获" in workspace.iq_context_label.text()

    invalid_detection_frame = {**detection_frame, "sequence": 3, "center_frequency_hz": 0.0}
    assert workspace.render(
        {**snapshot, "model_task": "spectrogram_detection"}, invalid_detection_frame
    ) is True
    assert workspace.results.table.rowCount() == 0
    assert "无效" in workspace.spectrogram_plot.note_label.text()

    later_detection_frame = {
        **detection_frame,
        "sequence": 4,
        "detections": [{"label": "later", "confidence": 0.7, "box_xyxy": [2.0, 2.0, 6.0, 6.0]}],
    }
    assert workspace.render(
        {**snapshot, "model_task": "spectrogram_detection"}, later_detection_frame
    ) is True
    # An invalid detector frame explicitly clears the old preview and result
    # history, so the subsequent valid frame must not revive stale boxes.
    assert [box.label for box in workspace.results.recent_detections] == ["later"]

    workspace.render(
        {**snapshot, "generation": 3, "state": "starting", "model_task": "iq_classification"}
    )
    assert workspace.results.table.rowCount() == 0
    assert workspace.spectrogram_plot.physical_boxes == ()
    app.processEvents()
