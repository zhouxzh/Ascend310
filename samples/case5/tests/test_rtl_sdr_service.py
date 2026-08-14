from __future__ import annotations

import hashlib
import json
from pathlib import Path
import threading
import time

import numpy as np
import pytest

from time_frequency_dashboard.model.candidate_catalog import IQ_SAMPLING_CONVENTION
from time_frequency_dashboard.model.preprocessing_contract import iq_preprocessing_contract
from time_frequency_dashboard.npu import NpuStatus
from time_frequency_dashboard.rtl_sdr_service import (
    CaptureArchiveError,
    CapturedIqBatch,
    IqSourceContext,
    RtlSdrDisplayFrame,
    RtlSdrRunConfig,
    RtlSdrService,
    discover_accepted_models,
    estimate_capture_bytes,
    estimate_live_capture_bytes,
    plan_live_capture,
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(
    directory: Path,
    *,
    model_id: str = "service-model",
    npu_p95_ms: float = 0.1,
    npu_speedup_over_cpu: float = 1.5,
    batch_size: int = 1,
    window_samples: int = 4,
) -> Path:
    onnx_path = directory / f"{model_id}.onnx"
    om_path = directory / f"{model_id}.om"
    onnx_path.write_bytes(b"onnx")
    om_path.write_bytes(b"om")
    manifest_path = directory / f"{model_id}.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "model_id": model_id,
                "task": "iq_classification",
                "source": {
                    "url": "https://example.invalid/model",
                    "revision": "revision",
                    "license": "test-only",
                    "upstream_weight_sha256": "0" * 64,
                },
                "input": {
                    "name": "iq",
                    "shape": [batch_size, 2, window_samples],
                    "dtype": "float32",
                    "normalization": "per_channel_zscore",
                    "sample_rate_hz": 8_000.0,
                    "sampling_convention": IQ_SAMPLING_CONVENTION,
                    "preprocessing": iq_preprocessing_contract(
                        [batch_size, 2, window_samples]
                    ),
                },
                "output": {
                    "names": ["logits"],
                    "shape": [batch_size, 2],
                    "class_names": ["a", "b"],
                },
                "artifacts": {
                    "onnx_path": onnx_path.name,
                    "om_path": om_path.name,
                    "onnx_sha256": _digest(onnx_path),
                    "om_sha256": _digest(om_path),
                },
                "conversion": {
                    "atc_command": ["atc", "--model=test.onnx"],
                    "cann_version": "test",
                },
                "admission": {
                    "status": "accepted",
                    "numerical_passed": True,
                    "source_contract_verified": True,
                    "npu_p95_meets_window_budget": True,
                    "live_demo_eligible": True,
                    "npu_p95_ms": npu_p95_ms,
                    "npu_speedup_over_cpu": npu_speedup_over_cpu,
                },
            }
        ),
        encoding="utf-8",
    )
    return manifest_path


class _FakeRunner:
    def __init__(self, _path: Path) -> None:
        self.status = NpuStatus("NPU (Ascend 310B)", False, "not initialized")
        self.closed = False

    def initialize(self) -> NpuStatus:
        self.status = NpuStatus("NPU (Ascend 310B)", True, "ready")
        return self.status

    def run(self, values: np.ndarray) -> np.ndarray:
        self.status = NpuStatus(
            "NPU (Ascend 310B)", True, "active", last_latency_ms=0.25
        )
        return np.tile(np.asarray([[0.1, 0.9]], dtype=np.float32), (values.shape[0], 1))

    def close(self) -> None:
        self.closed = True


class _UnavailableRunner(_FakeRunner):
    def initialize(self) -> NpuStatus:
        self.status = NpuStatus("NPU unavailable", False, "ACL runtime unavailable")
        return self.status


class _LiveShapeMismatchRunner(_FakeRunner):
    def initialize(self) -> NpuStatus:
        self.status = NpuStatus(
            "NPU (Ascend 310B)",
            True,
            "ready",
            input_shape=(1, 2, 8),
            output_shape=(1, 2),
        )
        return self.status


class _FakeSource:
    source_name = "synthetic"

    def __init__(self, context: IqSourceContext, batches: int = 2, pause: float = 0.0) -> None:
        self.context = context
        self.batches = batches
        self.pause = pause

    def iter_batches(self, stop: threading.Event):
        # Injectable sources receive the archive path as part of their small
        # contract.  Keep this fixture realistic by leaving the same private
        # CU8 artifact that a built-in source would leave.
        with self.context.raw_path.open("wb") as archive:
            for sequence in range(self.batches):
                if stop.is_set():
                    break
                now = time.monotonic_ns()
                sample_index = np.arange(self.context.required_samples, dtype=np.float32)
                samples = np.ascontiguousarray(
                    (sample_index + np.float32(1.0))
                    / np.float32(self.context.required_samples)
                    + 1j
                    * (sample_index + np.float32(1.0))
                    / np.float32(self.context.required_samples),
                    dtype=np.complex64,
                )
                archive.write(np.full(self.context.required_samples * 2, 128, dtype=np.uint8).tobytes())
                archive.flush()
                yield CapturedIqBatch(
                    sequence=sequence,
                    source_sample_offset=sequence * self.context.required_samples,
                    host_receive_ns=time.time_ns(),
                    capture_started_monotonic_ns=now,
                    raw_complete_monotonic_ns=now,
                    ready_for_queue_monotonic_ns=now,
                    archive_write_ms=0.0,
                    decode_ms=0.0,
                    samples=samples,
                )
                if self.pause:
                    stop.wait(self.pause)


def test_discover_accepted_models_filters_invalid_or_modified_artifacts(tmp_path):
    accepted = _write_manifest(tmp_path)
    rejected = _write_manifest(tmp_path, model_id="modified")
    rejected.with_name("modified.om").write_bytes(b"different")

    options = discover_accepted_models(tmp_path)

    assert [option.manifest_path for option in options] == [accepted]
    assert options[0].task == "iq_classification"
    assert options[0].sample_rate_hz == pytest.approx(8_000.0)


def test_discover_accepted_models_uses_documented_default_order(tmp_path):
    lower_gain = _write_manifest(
        tmp_path,
        model_id="lower-gain",
        npu_p95_ms=0.01,
        npu_speedup_over_cpu=1.8,
    )
    faster_p95 = _write_manifest(
        tmp_path,
        model_id="faster-p95",
        npu_p95_ms=0.02,
        npu_speedup_over_cpu=2.0,
    )
    slower_p95 = _write_manifest(
        tmp_path,
        model_id="slower-p95",
        npu_p95_ms=0.04,
        npu_speedup_over_cpu=2.0,
    )

    options = discover_accepted_models(tmp_path)

    assert [option.manifest_path for option in options] == [
        faster_p95,
        slower_p95,
        lower_gain,
    ]


def test_display_frame_metadata_is_immutable_for_ui_consumers():
    frame = RtlSdrDisplayFrame(
        generation=1,
        sequence=2,
        source_sample_offset=0,
        samples=np.asarray([0.25 + 0.5j], dtype=np.complex64),
        model_input=np.asarray([[[0.25], [0.5]]], dtype=np.float32),
        model_iq=np.asarray([[[0.25], [0.5]]], dtype=np.float32),
        spectrogram_image=None,
        top_k=({"label": "QPSK", "confidence": 0.9},),
        detections=(),
        sample_rate_hz=8_000.0,
        center_frequency_hz=100_000_000.0,
        batch_duration_ms=1.0,
        completed_monotonic_ns=1,
    )

    with pytest.raises(TypeError):
        frame.top_k[0]["label"] = "changed"


def test_service_runs_injected_npu_source_and_exposes_latest_frame(tmp_path):
    manifest = _write_manifest(tmp_path)
    service = RtlSdrService(
        runner_factory=_FakeRunner,
        source_factory=lambda context: _FakeSource(context),
        disk_safety_bytes=0,
    )
    try:
        service.start(
            RtlSdrRunConfig(
                source="synthetic",
                manifest_path=manifest,
                output_dir=tmp_path / "runs",
                duration_seconds=1.0,
                max_batches=2,
            )
        )
        assert service.wait_stopped(timeout=5.0)
        snapshot = service.snapshot()
        assert snapshot.state == "idle"
        assert snapshot.completion_status == "completed"
        assert "completed" in snapshot.message
        assert snapshot.completed_batches == 2
        assert snapshot.npu_status.ready is True
        frame = service.latest_frame()
        assert frame is not None
        assert frame.sequence == 1
        assert frame.samples.dtype == np.complex64
        assert np.any(frame.samples.imag != 0.0)
        assert frame.samples.flags.writeable is False
        assert frame.model_iq is not None
        assert frame.spectrogram_image is None
        assert frame.top_k[0]["label"] == "b"
        records = [json.loads(line) for line in snapshot.result_path.read_text(encoding="utf-8").splitlines()]
        assert records[0]["inference_backend"] == "NPU (Ascend 310B)"
        assert records[-1]["completion_status"] == "completed"
        assert records[-1]["produced_batches"] == 2
        assert snapshot.capture_path is not None
        assert snapshot.capture_path.stat().st_size == 16
        assert records[-1]["capture_bytes"] == 16
    finally:
        service.close()


def test_iq_display_frame_matches_the_final_classified_subwindow(tmp_path):
    manifest = _write_manifest(tmp_path, batch_size=2, window_samples=4)
    service = RtlSdrService(
        runner_factory=_FakeRunner,
        source_factory=lambda context: _FakeSource(context, batches=1),
        disk_safety_bytes=0,
    )
    try:
        service.start(
            RtlSdrRunConfig(
                source="synthetic",
                manifest_path=manifest,
                output_dir=tmp_path / "runs",
                duration_seconds=1.0,
                max_batches=1,
            )
        )
        assert service.wait_stopped(timeout=5.0)
        frame = service.latest_frame()
        assert frame is not None
        assert frame.source_sample_offset == 4
        assert frame.source_sample_count == 4
        assert frame.samples.size == 4
        assert frame.model_iq is not None
        assert frame.model_iq.shape == (1, 2, 4)
        assert frame.batch_duration_ms == pytest.approx(0.5)
        assert frame.top_k[0]["label"] == "b"
    finally:
        service.close()


def test_service_rejects_incorrect_source_window_before_npu_inference(tmp_path):
    manifest = _write_manifest(tmp_path)

    class _WrongWindowSource:
        source_name = "synthetic"

        def __init__(self, context: IqSourceContext) -> None:
            self.context = context

        def iter_batches(self, _stop: threading.Event):
            with self.context.raw_path.open("wb") as archive:
                archive.write(bytes([128, 128]) * self.context.required_samples)
                now = time.monotonic_ns()
                yield CapturedIqBatch(
                    sequence=0,
                    source_sample_offset=0,
                    host_receive_ns=time.time_ns(),
                    capture_started_monotonic_ns=now,
                    raw_complete_monotonic_ns=now,
                    ready_for_queue_monotonic_ns=now,
                    archive_write_ms=0.0,
                    decode_ms=0.0,
                    samples=np.ones(self.context.required_samples - 1, dtype=np.complex64),
                )

    service = RtlSdrService(
        runner_factory=_FakeRunner,
        source_factory=lambda context: _WrongWindowSource(context),
        disk_safety_bytes=0,
    )
    try:
        service.start(
            RtlSdrRunConfig(
                source="synthetic",
                manifest_path=manifest,
                output_dir=tmp_path / "runs",
                max_batches=1,
            )
        )
        assert service.wait_stopped(timeout=5.0)
        snapshot = service.snapshot()
        assert snapshot.state == "failed"
        assert "unexpected fixed-window sample count" in (snapshot.error or "")
    finally:
        service.close()


def test_live_service_rejects_om_shape_before_it_constructs_the_rtl_source(tmp_path):
    manifest = _write_manifest(tmp_path)
    source_created = []
    service = RtlSdrService(
        runner_factory=_LiveShapeMismatchRunner,
        source_factory=lambda _context: source_created.append(True),
        disk_safety_bytes=0,
    )
    try:
        service.start(
            RtlSdrRunConfig(
                source="rtl",
                manifest_path=manifest,
                output_dir=tmp_path / "runs",
                duration_seconds=1.0,
            )
        )
        assert service.wait_stopped(timeout=5.0)
        snapshot = service.snapshot()
        assert snapshot.state == "failed"
        assert "OM input shape" in (snapshot.error or "")
        assert source_created == []
    finally:
        service.close()


def test_wait_stopped_keeps_a_live_producer_as_a_resource_owner():
    service = RtlSdrService(runner_factory=_FakeRunner)
    release = threading.Event()
    producer = threading.Thread(target=release.wait, daemon=True)
    producer.start()
    try:
        service._producer_thread = producer
        assert not service.wait_stopped(timeout=0.0)
    finally:
        release.set()
        assert service.wait_stopped(timeout=1.0)


def test_service_refuses_double_start_and_writes_stopped_run_marker(tmp_path):
    manifest = _write_manifest(tmp_path)
    service = RtlSdrService(
        runner_factory=_FakeRunner,
        source_factory=lambda context: _FakeSource(context, batches=50, pause=0.05),
    )
    try:
        config = RtlSdrRunConfig(
            source="synthetic",
            manifest_path=manifest,
            output_dir=tmp_path / "runs",
            duration_seconds=2.0,
            max_batches=50,
        )
        service.start(config)
        with pytest.raises(RuntimeError, match="already running"):
            service.start(config)
        time.sleep(0.02)
        service.request_stop()
        assert service.wait_stopped(timeout=5.0)
        snapshot = service.snapshot()
        assert snapshot.state == "idle"
        assert snapshot.completion_status == "stopped"
        assert "not eligible for QC" in snapshot.message
        records = [json.loads(line) for line in snapshot.result_path.read_text(encoding="utf-8").splitlines()]
        assert records[-1]["completion_status"] == "stopped"
    finally:
        service.close()


def test_run_config_and_capture_estimate_validate_operator_inputs(tmp_path):
    with pytest.raises(ValueError, match="input_cu8"):
        RtlSdrRunConfig(source="cu8")
    with pytest.raises(ValueError, match="RF input"):
        RtlSdrRunConfig(rf_input_context="made_up")
    assert estimate_capture_bytes(2_048_000.0, 10.0) == 40_960_000
    yolo_plan = plan_live_capture(2_048_000.0, 10.0, 1_048_576)
    assert yolo_plan.requested_capture_samples == 20_480_000
    assert yolo_plan.planned_capture_batches == 20
    assert yolo_plan.planned_capture_samples == 20 * 1_048_576
    assert yolo_plan.planned_capture_duration_seconds == pytest.approx(10.24)
    assert estimate_live_capture_bytes(2_048_000.0, 10.0, 1_048_576) == 41_943_040
    with pytest.raises(ValueError, match="positive"):
        estimate_capture_bytes(0.0, 1.0)
    with pytest.raises(ValueError, match="max_batches"):
        RtlSdrRunConfig(source="rtl", max_batches=1)
    with pytest.raises(ValueError, match="whole number"):
        RtlSdrRunConfig(sample_rate_hz=8_000.5)
    with pytest.raises(ValueError, match="whole number"):
        RtlSdrRunConfig(center_frequency_hz=100_000_000.5)


def test_service_can_stop_before_first_batch_and_marks_non_qc_artifact(tmp_path):
    manifest = _write_manifest(tmp_path)

    class _DelayedSource:
        source_name = "synthetic"

        def iter_batches(self, stop: threading.Event):
            stop.wait(2.0)
            if False:  # pragma: no cover - make this a generator for the protocol
                yield None

    service = RtlSdrService(runner_factory=_FakeRunner, source_factory=lambda _context: _DelayedSource())
    try:
        service.start(
            RtlSdrRunConfig(
                source="synthetic",
                manifest_path=manifest,
                output_dir=tmp_path / "runs",
            )
        )
        service.request_stop()
        assert service.wait_stopped(timeout=5.0)
        snapshot = service.snapshot()
        assert snapshot.state == "idle"
        assert snapshot.completion_status == "stopped"
        records = [json.loads(line) for line in snapshot.result_path.read_text(encoding="utf-8").splitlines()]
        assert records[-1]["completion_status"] == "stopped"
        assert records[-1]["pipeline_realtime"] is None
        assert snapshot.capture_path is not None
        assert snapshot.capture_path.is_file()
    finally:
        service.close()


def test_failed_npu_preflight_keeps_explicit_non_qc_jsonl_and_cu8(tmp_path):
    manifest = _write_manifest(tmp_path)
    service = RtlSdrService(
        runner_factory=_UnavailableRunner,
        source_factory=lambda context: _FakeSource(context),
    )
    try:
        service.start(
            RtlSdrRunConfig(
                source="synthetic",
                manifest_path=manifest,
                output_dir=tmp_path / "runs",
                max_batches=1,
            )
        )
        assert service.wait_stopped(timeout=5.0)
        snapshot = service.snapshot()
        assert snapshot.state == "failed"
        assert snapshot.completion_status == "failed"
        assert snapshot.result_path is not None
        assert snapshot.capture_path is not None
        assert snapshot.capture_path.is_file()
        records = [
            json.loads(line)
            for line in snapshot.result_path.read_text(encoding="utf-8").splitlines()
        ]
        assert records[0]["record_type"] == "run_metadata"
        assert records[0]["inference_backend"] == "NPU unavailable"
        assert records[-1]["completion_status"] == "failed"
        assert records[-1]["capture_bytes"] == 0
    finally:
        service.close()


def test_archive_write_failure_is_counted_and_marks_the_run_failed(tmp_path):
    manifest = _write_manifest(tmp_path)

    class _ArchiveFailureSource:
        source_name = "synthetic"

        def iter_batches(self, _stop: threading.Event):
            raise CaptureArchiveError("simulated full filesystem")
            yield None  # pragma: no cover - establishes generator protocol

    service = RtlSdrService(
        runner_factory=_FakeRunner,
        source_factory=lambda _context: _ArchiveFailureSource(),
    )
    try:
        service.start(
            RtlSdrRunConfig(
                source="synthetic",
                manifest_path=manifest,
                output_dir=tmp_path / "runs",
            )
        )
        assert service.wait_stopped(timeout=5.0)
        snapshot = service.snapshot()
        assert snapshot.state == "failed"
        assert snapshot.archive_failed_batches == 1
        assert snapshot.result_path is not None
        records = [
            json.loads(line)
            for line in snapshot.result_path.read_text(encoding="utf-8").splitlines()
        ]
        assert records[-1]["completion_status"] == "failed"
        assert records[-1]["archive_failed_batches"] == 1
    finally:
        service.close()
