from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import asyncio

import numpy as np
import pytest

from midi_ddsp_webui import ddsp_vst_effect as effect
from midi_ddsp_webui import app as web_app
from midi_ddsp_webui.core import ResourceBusyError, ResourceCoordinator
from fastapi import HTTPException
from pydantic import ValidationError
from realtime_ddsp import ModelControls


class FakeFeature:
    backend_name = "acl/om"

    def __init__(self) -> None:
        self.windows: list[np.ndarray] = []
        self.closed = False

    def predict(self, audio: np.ndarray) -> effect.FeatureValues:
        self.windows.append(np.asarray(audio).copy())
        return effect.FeatureValues(0.5, 0.6, 440.0, -24.0)

    def close(self) -> None:
        self.closed = True


class FakeControls:
    backend_name = "om-pyacl"

    def __init__(self) -> None:
        self.calls: list[tuple[float, float]] = []
        self.closed = False

    def predict_from_state(self, state, f0_scaled, pw_scaled):
        self.calls.append((float(f0_scaled), float(pw_scaled)))
        return (
            ModelControls(
                amplitude=0.1,
                harmonics=np.full(60, 1.0 / 60.0, dtype=np.float32),
                noise_amps=np.zeros(65, dtype=np.float32),
            ),
            np.asarray(state, dtype=np.float32) + 1.0,
        )

    def close(self) -> None:
        self.closed = True


def make_processor(parameters=None):
    feature = FakeFeature()
    controls = FakeControls()
    processor = effect.DdspVstEffectProcessor(feature, controls, parameters)
    processor.input_resampler = SimpleNamespace(
        process=lambda block: np.asarray(block, dtype=np.float32)[::3][:320],
        algorithmic_latency_seconds=0.0,
    )
    processor.output_resampler = SimpleNamespace(
        process=lambda block: np.zeros(960, dtype=np.float32),
        algorithmic_latency_seconds=0.0,
    )
    processor.harmonic = SimpleNamespace(
        render=lambda *_args: np.zeros(320, dtype=np.float32)
    )
    processor.noise = SimpleNamespace(
        render=lambda *_args: np.zeros(320, dtype=np.float32)
    )
    processor.reverb = SimpleNamespace(
        update=lambda _settings: None,
        process=lambda block: np.repeat(np.asarray(block)[:, None], 2, axis=1),
    )
    return processor, feature, controls


def test_parameter_boundaries_are_explicit() -> None:
    assert effect.validate_parameters({"transpose": -24, "output_gain_db": 6}) == {
        "transpose": -24.0,
        "output_gain_db": 6.0,
    }
    with pytest.raises(ValueError, match="transpose"):
        effect.validate_parameters({"transpose": 25})
    with pytest.raises(ValueError, match="Unknown"):
        effect.validate_parameters({"backend": "onnx"})


def test_runtime_model_validation_rejects_non_om_and_bad_hash(tmp_path: Path) -> None:
    feature = tmp_path / "feature.om"
    control = tmp_path / "Violin_mixed_float16.om"
    feature.write_bytes(b"feature")
    control.write_bytes(b"control")
    manifest = tmp_path / "SHA256SUMS.txt"
    manifest.write_text(
        f"{effect.sha256_file(control)}  om/{control.name}\n", encoding="utf-8"
    )
    hashes = effect.validate_runtime_models(
        feature,
        control,
        feature_sha256=effect.sha256_file(feature),
        manifest_path=manifest,
    )
    assert hashes == {
        "feature": effect.sha256_file(feature),
        "control": effect.sha256_file(control),
    }
    with pytest.raises(ValueError, match="SHA256"):
        effect.validate_runtime_models(
            feature, control, feature_sha256="0" * 64, manifest_path=manifest
        )
    with pytest.raises(ValueError, match="OM model"):
        effect.validate_runtime_models(
            feature.with_suffix(".onnx"),
            control,
            feature_sha256="0" * 64,
            manifest_path=manifest,
        )


def test_processor_uses_1024_window_with_320_sample_hop() -> None:
    processor, feature, controls = make_processor()
    first = np.ones((960, 2), dtype=np.float32)
    second = np.full((960, 2), 2.0, dtype=np.float32)

    processor.process_capture_block(first)
    processor.process_capture_block(second)

    np.testing.assert_array_equal(feature.windows[0][:-320], 0.0)
    np.testing.assert_array_equal(feature.windows[0][-320:], 1.0)
    np.testing.assert_array_equal(feature.windows[1][:-640], 0.0)
    np.testing.assert_array_equal(feature.windows[1][-640:-320], 1.0)
    np.testing.assert_array_equal(feature.windows[1][-320:], 2.0)
    assert controls.calls[0][0] == pytest.approx(69.0 / 127.0)
    assert controls.calls[0][1] == pytest.approx(0.6)


def test_quiet_input_is_gated_and_a_real_signal_opens_smoothly() -> None:
    processor, _feature, _controls = make_processor()
    processor.output_resampler = SimpleNamespace(
        process=lambda _block: np.ones(960, dtype=np.float32),
        algorithmic_latency_seconds=0.0,
    )

    quiet = np.full((960, 2), 0.001, dtype=np.float32)
    loud = np.full((960, 2), 0.1, dtype=np.float32)
    quiet_output = processor.process_capture_block(quiet)
    loud_output = processor.process_capture_block(loud)

    assert np.count_nonzero(quiet_output) == 0
    assert np.count_nonzero(loud_output) > 0
    assert processor.metrics()["gate_open"] is True
    assert 0.0 < processor.metrics()["gate_gain"] <= 1.0


def test_input_calibration_sets_threshold_above_measured_noise() -> None:
    processor, _feature, _controls = make_processor()
    processor.begin_calibration()
    noise_amplitude = 10.0 ** (-50.0 / 20.0)
    noise = np.full((960, 2), noise_amplitude, dtype=np.float32)

    for _ in range(effect.GATE_CALIBRATION_FRAMES):
        output = processor.process_capture_block(noise)

    metrics = processor.metrics()
    assert np.count_nonzero(output) == 0
    assert metrics["calibrating"] is False
    assert metrics["calibration_progress"] == 1.0
    assert metrics["noise_floor_dbfs"] == pytest.approx(-50.0, abs=0.1)
    assert processor.parameters["gate_threshold_dbfs"] == pytest.approx(-42.0, abs=0.1)


def test_sustained_overload_triggers_safety_mute() -> None:
    processor, _feature, _controls = make_processor({"output_gain_db": 0.0})
    processor.output_resampler = SimpleNamespace(
        process=lambda _block: np.full(960, 2.0, dtype=np.float32),
        algorithmic_latency_seconds=0.0,
    )
    block = np.full((960, 2), 0.1, dtype=np.float32)
    output = None
    for _ in range(effect.SAFETY_OVERLOAD_FRAMES):
        output = processor.process_capture_block(block)

    assert output is not None
    assert np.count_nonzero(output) == 0
    assert processor.metrics()["safety_muted"] is True
    assert processor.metrics()["overload_frames"] == effect.SAFETY_OVERLOAD_FRAMES


class FakeProcessor:
    def __init__(self, feature_model, controls_model, parameters) -> None:
        self.feature_model = feature_model
        self.controls_model = controls_model
        self.parameters = dict(parameters)
        self.closed = False
        self.calibration_count = 0

    def begin_calibration(self) -> None:
        self.calibration_count += 1

    def update_parameters(self, values) -> None:
        self.parameters.update(values)

    def close(self) -> None:
        self.closed = True
        self.feature_model.close()
        self.controls_model.close()


class FakeSession:
    def __init__(self, processor, terminal_callback, **_kwargs) -> None:
        self.processor = processor
        self.terminal_callback = terminal_callback
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.terminal_callback(None)

    def fail_device(self) -> None:
        self.terminal_callback(RuntimeError("capture disconnected"))

    def metrics(self):
        return {"safety_muted": False, "frames": 1}


def controller_config(tmp_path: Path) -> dict[str, object]:
    return {
        "feature_model_path": str(tmp_path / "feature.om"),
        "control_model_path": str(tmp_path / "control.om"),
        "model_id": "violin",
        "audio_input_id": "camera",
        "audio_output_id": "edifier",
        "pulse_source": "camera-source",
        "pulse_sink": "edifier-sink",
        "input_device_name": "UGREEN Camera",
        "output_device_name": "EDIFIER M16 Pro",
        "parameters": {},
    }


def make_controller(monkeypatch, coordinator: ResourceCoordinator):
    monkeypatch.setattr(
        effect,
        "validate_runtime_models",
        lambda *_args, **_kwargs: {"feature": "a" * 64, "control": "b" * 64},
    )
    return effect.DdspVstEffectController(
        coordinator,
        feature_model_factory=lambda *_args, **_kwargs: FakeFeature(),
        controls_model_factory=lambda *_args, **_kwargs: FakeControls(),
        processor_factory=FakeProcessor,
        session_factory=FakeSession,
    )


def test_controller_releases_resource_after_device_disconnect(monkeypatch, tmp_path: Path) -> None:
    coordinator = ResourceCoordinator()
    controller = make_controller(monkeypatch, coordinator)
    status = controller.start(controller_config(tmp_path))
    assert status["backend"] == "acl/om"
    assert coordinator.owner == effect.DdspVstEffectController.OWNER

    session = controller._session
    assert isinstance(session, FakeSession)
    session.fail_device()

    assert controller.status()["state"] == "failed"
    assert "capture disconnected" in str(controller.status()["error"])
    assert coordinator.owner is None


def test_controller_honors_exclusive_resource_lock(monkeypatch, tmp_path: Path) -> None:
    coordinator = ResourceCoordinator()
    coordinator.acquire("realtime")
    controller = make_controller(monkeypatch, coordinator)
    with pytest.raises(ResourceBusyError):
        controller.start(controller_config(tmp_path))
    assert coordinator.owner == "realtime"


def test_controller_stop_closes_models_and_worker(monkeypatch, tmp_path: Path) -> None:
    coordinator = ResourceCoordinator()
    controller = make_controller(monkeypatch, coordinator)
    controller.start(controller_config(tmp_path))
    processor = controller._processor

    status = controller.stop()

    assert status["state"] == "stopped"
    assert processor.closed is True
    assert coordinator.owner is None


def test_controller_calibrates_on_start_and_on_request(monkeypatch, tmp_path: Path) -> None:
    coordinator = ResourceCoordinator()
    controller = make_controller(monkeypatch, coordinator)
    controller.start(controller_config(tmp_path))
    processor = controller._processor

    assert processor.calibration_count == 1
    controller.calibrate()
    assert processor.calibration_count == 2
    controller.stop()


def test_controller_reports_and_retains_calibrated_threshold(monkeypatch, tmp_path: Path) -> None:
    coordinator = ResourceCoordinator()
    controller = make_controller(monkeypatch, coordinator)
    controller.start(controller_config(tmp_path))
    processor = controller._processor
    processor.parameters["gate_threshold_dbfs"] = -43.0

    assert controller.status()["parameters"]["gate_threshold_dbfs"] == -43.0
    assert controller.stop()["parameters"]["gate_threshold_dbfs"] == -43.0


def test_effect_runtime_source_has_no_onnx_fallback() -> None:
    source = Path(effect.__file__).read_text(encoding="utf-8").lower()
    assert "onnxruntime" not in source
    assert "tflite" not in source


def test_effect_api_routes_are_public() -> None:
    paths = {route.path for route in web_app.app.routes}
    assert {
        "/api/v1/ddsp-vst-effect/catalog",
        "/api/v1/ddsp-vst-effect/catalog/refresh",
        "/api/v1/ddsp-vst-effect/status",
        "/api/v1/ddsp-vst-effect/start",
        "/api/v1/ddsp-vst-effect/parameters",
        "/api/v1/ddsp-vst-effect/stop",
        "/api/v1/ddsp-vst-effect/calibrate",
        "/api/v1/ddsp-vst-effect/events",
    }.issubset(paths)


def test_effect_catalog_uses_cached_global_catalog(monkeypatch) -> None:
    calls = []

    def cached_catalog(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ddsp_vst_models": []}

    monkeypatch.setattr(web_app, "catalog", cached_catalog)
    assert web_app._effect_control_models() == []
    assert calls == [((), {})]


def test_effect_start_request_rejects_paths_and_backend_selection() -> None:
    values = {
        "model_id": "violin",
        "audio_input_id": "camera",
        "audio_output_id": "edifier",
    }
    with pytest.raises(ValidationError):
        web_app.DdspVstEffectStartRequest(
            **values, feature_model_path="model.onnx"
        )
    with pytest.raises(ValidationError):
        web_app.DdspVstEffectStartRequest(**values, backend="onnx")


def test_effect_start_rejects_monitor_input(monkeypatch) -> None:
    payload = web_app.DdspVstEffectStartRequest(
        model_id="violin",
        audio_input_id="monitor",
        audio_output_id="edifier",
    )
    monkeypatch.setattr(web_app, "_require_board", lambda: None)
    monkeypatch.setattr(
        web_app,
        "_effect_control_models",
        lambda: [{"id": "violin", "path": "Violin.om"}],
    )
    monkeypatch.setattr(
        web_app,
        "query_audio_inputs",
        lambda: [
            {
                "id": "monitor",
                "backend": "pulse",
                "type": "monitor",
                "available": False,
                "source_name": "sink.monitor",
                "name": "Monitor",
            }
        ],
    )
    monkeypatch.setattr(
        web_app,
        "query_ddsp_vst_audio_outputs",
        lambda _query: [
            {
                "id": "edifier",
                "backend": "pulse",
                "sink_name": "edifier-sink",
                "name": "EDIFIER",
            }
        ],
    )
    with pytest.raises(HTTPException) as raised:
        asyncio.run(web_app.start_ddsp_vst_effect(payload))
    assert raised.value.status_code == 409


def test_effect_start_resolves_ids_to_server_owned_values(monkeypatch) -> None:
    captured = {}
    payload = web_app.DdspVstEffectStartRequest(
        model_id="violin",
        audio_input_id="camera",
        audio_output_id="edifier",
        parameters={"output_gain_db": -18},
    )
    monkeypatch.setattr(web_app, "_require_board", lambda: None)
    monkeypatch.setattr(
        web_app,
        "_effect_control_models",
        lambda: [{"id": "violin", "path": "/server/Violin.om"}],
    )
    monkeypatch.setattr(
        web_app,
        "query_audio_inputs",
        lambda: [
            {
                "id": "camera",
                "backend": "pulse",
                "type": "capture",
                "available": True,
                "source_name": "camera-source",
                "name": "UGREEN Camera",
            }
        ],
    )
    monkeypatch.setattr(
        web_app,
        "query_ddsp_vst_audio_outputs",
        lambda _query: [
            {
                "id": "edifier",
                "backend": "pulse",
                "sink_name": "edifier-sink",
                "name": "EDIFIER M16 Pro",
            }
        ],
    )

    def start(config):
        captured.update(config)
        return {"state": "running"}

    monkeypatch.setattr(web_app.ddsp_vst_effect, "start", start)
    assert asyncio.run(web_app.start_ddsp_vst_effect(payload)) == {"state": "running"}
    assert captured["control_model_path"] == "/server/Violin.om"
    assert captured["pulse_source"] == "camera-source"
    assert captured["pulse_sink"] == "edifier-sink"
    assert "backend" not in captured
