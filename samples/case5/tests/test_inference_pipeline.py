from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import threading

import numpy as np
import numpy.testing as npt
import pytest

from time_frequency_dashboard.inference_processing import (
    complex_to_model_iq,
    decode_yolo_detections,
    normalize_iq_batch,
    softmax_topk,
)
from time_frequency_dashboard.model.inference_manifest import (
    ensure_live_deployment_ready,
    load_inference_manifest,
    model_contract_sha256,
    select_default_manifest,
    verify_artifact_hashes,
)
from time_frequency_dashboard.model.attach_pipeline_realtime_evidence import (
    attach_pipeline_realtime_evidence,
    main as attach_pipeline_evidence_main,
    pipeline_evidence_from_report,
    summarize_continuous_pipeline_realtime,
    verify_attached_pipeline_realtime_evidence,
)
from time_frequency_dashboard.model.candidate_catalog import IQ_SAMPLING_CONVENTION
from time_frequency_dashboard.model.preprocessing_contract import (
    iq_preprocessing_contract,
    spectrogram_preprocessing_contract,
)
from time_frequency_dashboard.model.model_admission import compare_model_outputs
from time_frequency_dashboard.rtl_sdr_npu_inference import (
    _produce_cu8,
    prepare_model_input,
    resolve_sample_rate,
    summarize_pipeline_realtime,
    validate_model_output,
    validate_live_budget,
)
from time_frequency_dashboard.processing import LatestQueue
from time_frequency_dashboard.spectrogram import wideband_spectrogram_numpy


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_manifest(
    directory: Path,
    *,
    model_id: str = "test-iq-model",
    status: str = "accepted",
    speedup: float = 1.5,
    p95_ms: float = 1.0,
) -> Path:
    onnx_path = directory / f"{model_id}.onnx"
    om_path = directory / f"{model_id}.om"
    onnx_path.write_bytes(b"onnx-test")
    om_path.write_bytes(b"om-test")
    manifest_path = directory / f"{model_id}.manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "model_id": model_id,
                "task": "iq_classification",
                "source": {
                    "url": "https://example.invalid/model",
                    "revision": "0123456789abcdef",
                    "license": "test-only",
                    "upstream_weight_sha256": "0" * 64,
                },
                "input": {
                    "name": "iq",
                    "shape": [2, 2, 8],
                    "dtype": "float32",
                    "normalization": "per_channel_zscore",
                    "sample_rate_hz": 8_000.0,
                    "sampling_convention": IQ_SAMPLING_CONVENTION,
                    "preprocessing": iq_preprocessing_contract([2, 2, 8]),
                },
                "output": {
                    "names": ["logits"],
                    "shape": [2, 3],
                    "class_names": ["bpsk", "qpsk", "fm"],
                },
                "artifacts": {
                    "onnx_path": onnx_path.name,
                    "om_path": om_path.name,
                    "onnx_sha256": _digest(onnx_path),
                    "om_sha256": _digest(om_path),
                },
                "conversion": {
                    "atc_command": ["atc", "--framework=5"],
                    "cann_version": "test",
                    "cann_version_provenance": "test evidence",
                },
                "admission": {
                    "status": status,
                    "numerical_passed": True,
                    "source_contract_verified": True,
                    "npu_p95_meets_window_budget": True,
                    "p95_meets_real_time": True,
                    "live_demo_eligible": True,
                    "npu_speedup_over_cpu": speedup,
                    "npu_p95_ms": p95_ms,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return manifest_path


def _write_rtl_report(
    directory: Path,
    manifest_path: Path,
    *,
    post_capture_ms: list[float] | None = None,
    manifest_sha256: str | None = None,
    footer_override: dict | None = None,
    batch_duration_ms: float = 2.0,
) -> Path:
    manifest = load_inference_manifest(manifest_path)
    post_capture_ms = post_capture_ms or [0.5, 1.0, 1.5]
    capture_bytes = len(post_capture_ms) * manifest.batch_size * manifest.window_samples * 2
    capture_path = directory / "capture.cu8"
    capture_path.write_bytes(bytes(index % 256 for index in range(capture_bytes)))
    summary = summarize_pipeline_realtime(
        produced_batches=len(post_capture_ms),
        completed_batches=len(post_capture_ms),
        dropped_batches=0,
        post_capture_pipeline_ms=post_capture_ms,
        batch_duration_ms=batch_duration_ms,
    )
    summary.update(
        {
            "observation_duration_ms": 10.0,
            "evidence_scope": "short_run_pipeline_window_check",
            "continuous_pipeline_soak_verified": False,
        }
    )
    if footer_override:
        summary.update(footer_override)
    header = {
        "record_type": "run_metadata",
        "source": "rtl",
        "inference_backend": "NPU (Ascend 310B)",
        "rtl_device": "0",
        "rf_input_context": "disconnected",
        "capture_file": str(capture_path.resolve()),
        "model_id": manifest.model_id,
        "model_task": manifest.task,
        "model_manifest_sha256": manifest_sha256 or _digest(manifest_path),
        "model_onnx_sha256": manifest.onnx_sha256,
        "model_om_sha256": manifest.om_sha256,
        "model_upstream_weight_sha256": manifest.upstream_weight_sha256,
        "model_input_shape": list(manifest.input_shape),
        "model_preprocessing": dict(manifest.preprocessing),
        "model_input_normalization": manifest.normalization,
        "model_sampling_convention": manifest.sampling_convention,
        "model_source_revision": manifest.source_revision,
        "model_cann_version": manifest.cann_version,
        "sample_rate_hz": manifest.sample_rate_hz,
        "center_frequency_hz": 100_000_000.0,
        "batch_duration_ms": batch_duration_ms,
    }
    records = [header]
    records.extend(
        {
            "record_type": "inference_batch",
            "sequence": index,
            "source_sample_offset": index * manifest.batch_size * manifest.window_samples,
            "input_shape": list(manifest.input_shape),
            "output_shape": list(manifest.output_shape),
            "backend": "NPU (Ascend 310B)",
            "npu_inference_ms": 0.25,
            "post_capture_pipeline_ms": elapsed,
            "end_to_end_ms": elapsed + 0.25,
            "queue_dropped_batches": 0,
        }
        for index, elapsed in enumerate(post_capture_ms)
    )
    records.append(
        {
            "record_type": "run_summary",
            "completion_status": "completed",
            "completed_batches": len(post_capture_ms),
            "produced_batches": len(post_capture_ms),
            "queue_dropped_batches": 0,
            "archive_failed_batches": 0,
            "pipeline_realtime": summary,
            "wall_time_ms": 10.0,
            "inference_backend": "NPU (Ascend 310B)",
            "capture_sha256": _digest(capture_path),
            "capture_bytes": capture_bytes,
        }
    )
    report = directory / "rtl_inference.jsonl"
    report.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    return report


def test_manifest_validates_and_resolves_relative_artifacts(tmp_path):
    path = _write_manifest(tmp_path)
    manifest = load_inference_manifest(path)

    assert manifest.model_id == "test-iq-model"
    assert manifest.input_shape == (2, 2, 8)
    assert manifest.upstream_weight_sha256 == "0" * 64
    assert manifest.conversion_metadata["cann_version_provenance"] == "test evidence"
    assert manifest.onnx_path == tmp_path / "test-iq-model.onnx"
    assert manifest.om_path == tmp_path / "test-iq-model.om"
    verify_artifact_hashes(manifest)
    ensure_live_deployment_ready(manifest)


def test_manifest_rejects_tampered_artifact_and_missing_live_gate(tmp_path):
    path = _write_manifest(tmp_path)
    manifest = load_inference_manifest(path)
    manifest.om_path.write_bytes(b"tampered")
    with pytest.raises(RuntimeError, match="OM SHA256 mismatch"):
        verify_artifact_hashes(manifest)

    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["admission"].pop("numerical_passed")
    path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="numerical_passed"):
        ensure_live_deployment_ready(load_inference_manifest(path))


def test_manifest_rejects_candidate_for_live_inference(tmp_path):
    path = _write_manifest(tmp_path, status="candidate")
    with pytest.raises(ValueError, match="not 'accepted'"):
        load_inference_manifest(path)
    assert load_inference_manifest(path, require_accepted=False).admission["status"] == "candidate"


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        ("root", "schema_version", True),
        ("input", "shape", [True, 2, 8]),
        ("input", "shape", [1, 2, 8.5]),
        ("output", "shape", [0]),
    ],
)
def test_manifest_rejects_non_integer_or_non_positive_dimensions(
    tmp_path, section, field, value
):
    path = _write_manifest(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if section == "root":
        raw[field] = value
    else:
        raw[section][field] = value
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="positive integer"):
        load_inference_manifest(path)


def test_detection_manifest_requires_the_fixed_fftw_preprocessing_contract(tmp_path):
    path = _write_manifest(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["task"] = "spectrogram_detection"
    raw["input"].update(
        {
            "shape": [1, 3, 8, 8],
            "normalization": "none",
            "sampling_convention": "human-readable detail is not executable",
        }
    )
    raw["input"].pop("preprocessing")
    raw["output"]["class_names"] = ["signal"]
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="input.preprocessing"):
        load_inference_manifest(path)


def test_detection_manifest_rejects_a_near_miss_preprocessing_contract(tmp_path):
    path = _write_manifest(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["task"] = "spectrogram_detection"
    raw["input"].update(
        {
            "shape": [1, 3, 8, 8],
            "normalization": "none",
            "sampling_convention": "human-readable detail is not executable",
            "preprocessing": spectrogram_preprocessing_contract([1, 3, 8, 8]),
        }
    )
    raw["input"]["preprocessing"]["complex_dc_removal"] = True
    raw["output"]["class_names"] = ["signal"]
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="must exactly match"):
        load_inference_manifest(path)


def test_default_manifest_uses_speedup_then_p95(tmp_path):
    slower = _write_manifest(tmp_path, model_id="slower", speedup=1.1, p95_ms=0.5)
    nested = tmp_path / "candidates"
    nested.mkdir()
    faster = _write_manifest(nested, model_id="faster", speedup=1.8, p95_ms=2.0)
    assert slower.is_file()
    assert select_default_manifest(tmp_path) == faster


def test_pipeline_evidence_binds_a_real_rtl_report_to_the_model_contract(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    report_path = _write_rtl_report(tmp_path, manifest_path)
    evidence = pipeline_evidence_from_report(
        manifest_path=manifest_path, report_path=report_path
    )

    manifest = load_inference_manifest(manifest_path)
    assert evidence["source"] == "rtl"
    assert evidence["rf_input_context"] == "disconnected"
    assert evidence["pipeline_real_time_passed"] is True
    assert evidence["model_contract_sha256"] == model_contract_sha256(manifest)
    assert evidence["report_sha256"] == _digest(report_path)


def test_pipeline_evidence_rejects_wrong_manifest_or_forged_footer(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    wrong_manifest_report = _write_rtl_report(
        tmp_path,
        manifest_path,
        manifest_sha256="f" * 64,
    )
    with pytest.raises(ValueError, match="exact source manifest"):
        pipeline_evidence_from_report(
            manifest_path=manifest_path, report_path=wrong_manifest_report
        )

    forged_report = _write_rtl_report(
        tmp_path,
        manifest_path,
        footer_override={"pipeline_real_time_passed": False},
    )
    with pytest.raises(ValueError, match="does not match recomputed result"):
        pipeline_evidence_from_report(
            manifest_path=manifest_path, report_path=forged_report
        )


def test_pipeline_evidence_rejects_mismatched_preprocessing_contract(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    report_path = _write_rtl_report(tmp_path, manifest_path)
    records = [json.loads(line) for line in report_path.read_text(encoding="utf-8").splitlines()]
    records[0]["model_preprocessing"]["complex_dc_removal"] = "wrong"
    report_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="model_preprocessing"):
        pipeline_evidence_from_report(
            manifest_path=manifest_path, report_path=report_path
        )


def test_pipeline_evidence_rejects_inflated_reported_window_budget(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    report_path = _write_rtl_report(tmp_path, manifest_path, batch_duration_ms=5.0)
    with pytest.raises(ValueError, match="fixed input window"):
        pipeline_evidence_from_report(
            manifest_path=manifest_path, report_path=report_path
        )


def test_pipeline_evidence_reuses_qc_window_to_cu8_binding(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    report_path = _write_rtl_report(tmp_path, manifest_path)
    records = [json.loads(line) for line in report_path.read_text(encoding="utf-8").splitlines()]
    records[1]["source_sample_offset"] = 1
    report_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="equal sequence times"):
        pipeline_evidence_from_report(
            manifest_path=manifest_path, report_path=report_path
        )


def test_continuous_pipeline_requires_ten_minutes_and_connected_rf_context():
    short_run = {"observation_duration_ms": 600_000.0, "pipeline_real_time_passed": True}
    assert summarize_continuous_pipeline_realtime(
        short_run, rf_input_context="antenna_connected"
    )["continuous_pipeline_realtime_passed"] is True
    assert summarize_continuous_pipeline_realtime(
        {"observation_duration_ms": 599_999.0, "pipeline_real_time_passed": True},
        rf_input_context="antenna_connected",
    )["continuous_pipeline_realtime_passed"] is False
    assert summarize_continuous_pipeline_realtime(
        short_run, rf_input_context="disconnected"
    )["continuous_pipeline_realtime_passed"] is False


def test_manifest_validates_and_exposes_passing_pipeline_evidence(tmp_path):
    source_manifest_path = _write_manifest(tmp_path)
    report_path = _write_rtl_report(tmp_path, source_manifest_path)
    raw = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    raw["schema_version"] = 4
    raw["admission"]["pipeline_realtime"] = pipeline_evidence_from_report(
        manifest_path=source_manifest_path, report_path=report_path
    )
    attached = tmp_path / "attached.manifest.json"
    attached.write_text(json.dumps(raw), encoding="utf-8")

    manifest = load_inference_manifest(attached)
    assert manifest.admission["pipeline_realtime"]["verified"] is True
    assert manifest.admission["pipeline_realtime"]["pipeline_real_time_passed"] is True


def test_attached_pipeline_evidence_revalidates_report_and_capture(tmp_path):
    source_manifest_path = _write_manifest(tmp_path)
    report_path = _write_rtl_report(tmp_path, source_manifest_path)
    attached_path = attach_pipeline_realtime_evidence(
        source_manifest=source_manifest_path,
        report_path=report_path,
        output=tmp_path / "attached.pipeline.manifest.json",
    )
    evidence = verify_attached_pipeline_realtime_evidence(attached_path)
    assert evidence["pipeline_real_time_passed"] is True

    report_path.write_text(report_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="report SHA256"):
        verify_attached_pipeline_realtime_evidence(attached_path)


@pytest.mark.parametrize("target", ["report", "capture", "existing"])
def test_attach_pipeline_evidence_refuses_to_overwrite_evidence_or_existing_file(tmp_path, target):
    source_manifest_path = _write_manifest(tmp_path)
    report_path = _write_rtl_report(tmp_path, source_manifest_path)
    if target == "report":
        output = report_path
    elif target == "capture":
        output = tmp_path / "capture.cu8"
    else:
        output = tmp_path / "existing.pipeline.manifest.json"
        output.write_text("preserve", encoding="utf-8")
    before = output.read_bytes()

    with pytest.raises((ValueError, FileExistsError), match="overwrite|existing"):
        attach_pipeline_realtime_evidence(
            source_manifest=source_manifest_path,
            report_path=report_path,
            output=output,
        )
    assert output.read_bytes() == before


def test_attached_manifest_rejects_inconsistent_embedded_verdict(tmp_path):
    source_manifest_path = _write_manifest(tmp_path)
    report_path = _write_rtl_report(tmp_path, source_manifest_path)
    attached_path = attach_pipeline_realtime_evidence(
        source_manifest=source_manifest_path,
        report_path=report_path,
        output=tmp_path / "attached.pipeline.manifest.json",
    )
    raw = json.loads(attached_path.read_text(encoding="utf-8"))
    raw["admission"]["pipeline_realtime"]["post_capture_pipeline_max_ms"] = 99.0
    attached_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="passing verdict"):
        load_inference_manifest(attached_path)


@pytest.mark.parametrize("field", ["continuous_pipeline_realtime", "report_path"])
def test_attached_manifest_requires_complete_structured_evidence(tmp_path, field):
    source_manifest_path = _write_manifest(tmp_path)
    report_path = _write_rtl_report(tmp_path, source_manifest_path)
    attached_path = attach_pipeline_realtime_evidence(
        source_manifest=source_manifest_path,
        report_path=report_path,
        output=tmp_path / "attached.pipeline.manifest.json",
    )
    raw = json.loads(attached_path.read_text(encoding="utf-8"))
    raw["admission"]["pipeline_realtime"].pop(field)
    attached_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match=field):
        load_inference_manifest(attached_path)


@pytest.mark.parametrize("field", ["produced_batches", "capture_bytes"])
def test_attached_manifest_rejects_boolean_integer_fields(tmp_path, field):
    source_manifest_path = _write_manifest(tmp_path)
    report_path = _write_rtl_report(tmp_path, source_manifest_path)
    attached_path = attach_pipeline_realtime_evidence(
        source_manifest=source_manifest_path,
        report_path=report_path,
        output=tmp_path / "attached.pipeline.manifest.json",
    )
    raw = json.loads(attached_path.read_text(encoding="utf-8"))
    raw["admission"]["pipeline_realtime"][field] = True
    attached_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="non-negative integer"):
        load_inference_manifest(attached_path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("short_run_pipeline_real_time_passed", False, "short-run verdict"),
        ("rf_input_context", "antenna_connected", "RF input context"),
        ("observation_duration_ms", "nan", "finite non-negative number"),
    ],
)
def test_attached_manifest_rejects_inconsistent_continuous_evidence(
    tmp_path, field, value, message
):
    source_manifest_path = _write_manifest(tmp_path)
    report_path = _write_rtl_report(tmp_path, source_manifest_path)
    attached_path = attach_pipeline_realtime_evidence(
        source_manifest=source_manifest_path,
        report_path=report_path,
        output=tmp_path / "attached.pipeline.manifest.json",
    )
    raw = json.loads(attached_path.read_text(encoding="utf-8"))
    raw["admission"]["pipeline_realtime"]["continuous_pipeline_realtime"][field] = value
    attached_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match=message):
        load_inference_manifest(attached_path)


@pytest.mark.parametrize(
    "field",
    ["sample_rate_hz", "batch_duration_ms", "post_capture_pipeline_p95_ms"],
)
def test_attached_manifest_rejects_boolean_numeric_evidence_fields(tmp_path, field):
    source_manifest_path = _write_manifest(tmp_path)
    report_path = _write_rtl_report(tmp_path, source_manifest_path)
    attached_path = attach_pipeline_realtime_evidence(
        source_manifest=source_manifest_path,
        report_path=report_path,
        output=tmp_path / "attached.pipeline.manifest.json",
    )
    raw = json.loads(attached_path.read_text(encoding="utf-8"))
    raw["admission"]["pipeline_realtime"][field] = True
    attached_path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="finite non-negative number"):
        load_inference_manifest(attached_path)


def test_manifest_rejects_nonstandard_json_constants(tmp_path):
    path = _write_manifest(tmp_path)
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            '"sample_rate_hz": 8000.0', '"sample_rate_hz": NaN', 1
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="non-standard constant"):
        load_inference_manifest(path)


def test_manifest_rejects_duplicate_json_keys(tmp_path):
    path = _write_manifest(tmp_path)
    payload = path.read_text(encoding="utf-8")
    payload = payload.removesuffix("\n")
    # A duplicate root key would otherwise let a later occurrence silently
    # replace the reviewed model identifier during parsing.
    path.write_text(
        payload.removesuffix("}") + ', "model_id": "shadowed-model"}',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate key"):
        load_inference_manifest(path)


@pytest.mark.parametrize("field", ["npu_speedup_over_cpu", "npu_p95_ms"])
@pytest.mark.parametrize("value", ["not-a-number", float("nan"), float("inf"), True])
def test_live_manifest_rejects_invalid_performance_metrics(tmp_path, field, value):
    path = _write_manifest(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["admission"][field] = value
    path.write_text(json.dumps(raw, allow_nan=True), encoding="utf-8")

    with pytest.raises(ValueError, match=f"{field}|non-standard constant"):
        ensure_live_deployment_ready(load_inference_manifest(path))
    with pytest.raises(FileNotFoundError):
        select_default_manifest(tmp_path)


def test_classification_manifest_requires_output_shape_to_match_classes(tmp_path):
    path = _write_manifest(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["output"]["shape"] = [2, 2]
    path.write_text(json.dumps(raw), encoding="utf-8")

    with pytest.raises(ValueError, match="class_count"):
        load_inference_manifest(path)


def test_attached_pipeline_verifier_rechecks_model_artifact_hashes(tmp_path):
    source_manifest_path = _write_manifest(tmp_path)
    report_path = _write_rtl_report(tmp_path, source_manifest_path)
    attached_path = attach_pipeline_realtime_evidence(
        source_manifest=source_manifest_path,
        report_path=report_path,
        output=tmp_path / "attached.pipeline.manifest.json",
    )
    load_inference_manifest(source_manifest_path).om_path.write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="OM SHA256 mismatch"):
        verify_attached_pipeline_realtime_evidence(attached_path)


def test_manifest_serialization_preserves_conversion_provenance(tmp_path):
    manifest = load_inference_manifest(_write_manifest(tmp_path))
    assert manifest.to_dict()["conversion"]["cann_version_provenance"] == "test evidence"


def test_pipeline_evidence_verify_only_does_not_rewrite_manifest(monkeypatch, tmp_path):
    manifest_path = _write_manifest(tmp_path)
    report_path = _write_rtl_report(tmp_path, manifest_path)
    before = manifest_path.read_bytes()
    monkeypatch.setattr(
        "sys.argv",
        [
            "attach_pipeline_realtime_evidence",
            "--manifest",
            str(manifest_path),
            "--inference-jsonl",
            str(report_path),
            "--verify-only",
        ],
    )
    assert attach_pipeline_evidence_main() == 0
    assert manifest_path.read_bytes() == before


def test_live_sample_rate_must_match_admitted_rate_and_budget(tmp_path):
    manifest = load_inference_manifest(_write_manifest(tmp_path))
    assert resolve_sample_rate(manifest, None) == pytest.approx(8_000.0)
    with pytest.raises(ValueError, match="does not match"):
        resolve_sample_rate(manifest, 9_000.0)
    assert validate_live_budget(manifest, sample_rate_hz=8_000.0) == pytest.approx(2.0)
    changed = dict(manifest.admission)
    changed["npu_p95_ms"] = 3.0
    too_slow = manifest.__class__(**{**manifest.__dict__, "admission": changed})
    with pytest.raises(ValueError, match="exceeds"):
        validate_live_budget(too_slow, sample_rate_hz=8_000.0)


def test_live_deployment_rejects_multi_output_manifest_without_rejecting_parse(tmp_path):
    path = _write_manifest(tmp_path)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["output"]["names"] = ["logits", "auxiliary"]
    path.write_text(json.dumps(raw), encoding="utf-8")

    manifest = load_inference_manifest(path)
    with pytest.raises(ValueError, match="exactly_one_primary_output"):
        ensure_live_deployment_ready(manifest)


def test_cu8_replay_streams_fixed_batches_and_discards_stale_work(tmp_path):
    source = tmp_path / "capture.cu8"
    source.write_bytes(bytes(range(8)))
    args = SimpleNamespace(
        input_cu8=source,
        max_batches=2,
        duration_seconds=1.0,
    )
    queue = LatestQueue(1)
    done = threading.Event()
    errors: list[str] = []

    _produce_cu8(
        args,
        queue,
        done,
        threading.Event(),
        errors,
        required_samples=2,
        sample_rate_hz=1.0e9,
    )

    assert done.is_set()
    assert errors == []
    assert queue.dropped == 1
    batch = queue.get_nowait()
    assert batch.sequence == 1
    assert batch.source_sample_offset == 2
    # Replay now records the time spent copying its private diagnostic CU8
    # archive, just like the live source.  The exact duration is host-dependent.
    assert batch.archive_write_ms >= 0.0
    assert batch.ready_for_queue_monotonic_ns >= batch.raw_complete_monotonic_ns


def test_pipeline_realtime_requires_delivery_samples_and_budget():
    accepted = summarize_pipeline_realtime(
        produced_batches=3,
        completed_batches=3,
        dropped_batches=0,
        post_capture_pipeline_ms=[2.0, 3.0, 4.0],
        batch_duration_ms=5.0,
    )
    assert accepted["pipeline_real_time_passed"] is True

    slow = summarize_pipeline_realtime(
        produced_batches=2,
        completed_batches=2,
        dropped_batches=0,
        post_capture_pipeline_ms=[2.0, 6.0],
        batch_duration_ms=5.0,
    )
    assert slow["pipeline_real_time_passed"] is False

    dropped = summarize_pipeline_realtime(
        produced_batches=3,
        completed_batches=2,
        dropped_batches=1,
        post_capture_pipeline_ms=[2.0, 3.0],
        batch_duration_ms=5.0,
    )
    assert dropped["pipeline_real_time_passed"] is False

    short = summarize_pipeline_realtime(
        produced_batches=1,
        completed_batches=1,
        dropped_batches=0,
        post_capture_pipeline_ms=[2.0],
        batch_duration_ms=5.0,
    )
    assert short["pipeline_real_time_passed"] is False


def test_model_output_validation_rejects_shape_mismatch_and_nonfinite_values(tmp_path):
    manifest = load_inference_manifest(_write_manifest(tmp_path))
    expected = np.zeros((2, 3), dtype=np.float32)
    npt.assert_array_equal(validate_model_output(manifest, expected), expected)

    with pytest.raises(RuntimeError, match="output shape"):
        validate_model_output(manifest, np.zeros((1, 3), dtype=np.float32))
    with pytest.raises(RuntimeError, match="NaN or Inf"):
        validate_model_output(manifest, np.full((2, 3), np.nan, dtype=np.float32))


def test_iq_preprocessing_is_fixed_shape_and_normalized():
    samples = np.asarray([1 + 2j, 2 + 4j, 3 + 6j, 4 + 8j] * 2, dtype=np.complex64)
    actual = complex_to_model_iq(
        samples,
        batch_size=2,
        window_samples=4,
        normalization="per_channel_zscore",
    )

    assert actual.shape == (2, 2, 4)
    npt.assert_allclose(actual.mean(axis=2), 0.0, atol=1.0e-6)
    npt.assert_allclose(actual.std(axis=2), 1.0, atol=1.0e-6)


def test_iq_preprocessing_removes_per_window_complex_dc():
    samples = np.asarray([3 + 5j, 4 + 7j, 5 + 9j, 6 + 11j], dtype=np.complex64)
    actual = complex_to_model_iq(
        samples,
        batch_size=1,
        window_samples=4,
        normalization="none",
    )
    npt.assert_allclose(actual.mean(axis=2), 0.0, atol=1.0e-6)


def test_infinity_norm_and_topk_are_deterministic():
    values = np.asarray([[[1.0, -2.0], [4.0, -1.0]]], dtype=np.float32)
    normalized = normalize_iq_batch(values, "infinity_norm")
    assert float(np.max(np.abs(normalized))) == pytest.approx(1.0)

    result = softmax_topk(np.asarray([[0.1, 2.0, 1.0]], dtype=np.float32), ["a", "b", "c"], 2)
    assert [entry["label"] for entry in result[0]] == ["b", "c"]
    assert sum(entry["confidence"] for entry in result[0]) < 1.0


def test_prepare_model_input_uses_manifest_normalization(tmp_path):
    manifest = load_inference_manifest(_write_manifest(tmp_path))
    samples = np.arange(16, dtype=np.float32).astype(np.complex64)
    actual = prepare_model_input(manifest, samples, None)
    assert actual.shape == manifest.input_shape
    npt.assert_allclose(actual.mean(axis=2), 0.0, atol=1.0e-6)


def test_wideband_spectrogram_matches_black_hot_contract():
    nfft = 8
    tone = np.exp(1j * 2.0 * np.pi * np.arange(nfft * nfft) / nfft).astype(np.complex64)
    image = wideband_spectrogram_numpy(tone, nfft)

    assert image.shape == (1, 3, nfft, nfft)
    assert np.all(np.isfinite(image))
    assert float(image.min()) >= 0.0
    assert float(image.max()) <= 1.0
    npt.assert_array_equal(image[:, 0], image[:, 1])
    npt.assert_array_equal(image[:, 1], image[:, 2])


def test_wideband_spectrogram_batches_are_independently_normalized():
    nfft = 8
    first = np.ones(nfft * nfft, dtype=np.complex64)
    second = np.exp(1j * 2.0 * np.pi * np.arange(nfft * nfft) / nfft).astype(
        np.complex64
    )
    batched = wideband_spectrogram_numpy(np.concatenate((first, second)), nfft)

    assert batched.shape == (2, 3, nfft, nfft)
    npt.assert_array_equal(batched[0], wideband_spectrogram_numpy(first, nfft)[0])
    npt.assert_array_equal(batched[1], wideband_spectrogram_numpy(second, nfft)[0])


def test_wideband_spectrogram_preserves_the_upstream_dc_treatment():
    nfft = 8
    samples = np.exp(1j * 2.0 * np.pi * np.arange(nfft * nfft) / nfft).astype(
        np.complex64
    )
    offset = samples + np.complex64(3.0 + 2.0j)
    assert not np.allclose(
        wideband_spectrogram_numpy(samples, nfft),
        wideband_spectrogram_numpy(offset, nfft),
    )


def test_yolo_decoder_applies_confidence_and_class_aware_nms():
    # [x, y, width, height, class0, class1]
    output = np.asarray(
        [
            [10.0, 10.0, 4.0, 4.0, 0.9, 0.1],
            [10.2, 10.1, 4.0, 4.0, 0.8, 0.2],
            [30.0, 30.0, 2.0, 2.0, 0.1, 0.95],
        ],
        dtype=np.float32,
    )
    detections = decode_yolo_detections(
        output,
        ["signal", "tone"],
        confidence_threshold=0.25,
        iou_threshold=0.5,
        max_detections=10,
    )

    assert len(detections) == 2
    assert [item["label"] for item in detections] == ["tone", "signal"]


def test_model_admission_requires_numeric_and_top1_agreement():
    reference = np.asarray([[0.1, 2.0, 0.4], [3.0, 0.2, 0.1]], dtype=np.float32)
    close = reference + np.float32(1.0e-5)
    passed = compare_model_outputs(reference, close, task="iq_classification")
    assert passed["passed"]
    assert passed["top1_agreement"] == pytest.approx(1.0)

    changed = reference[:, ::-1]
    failed = compare_model_outputs(reference, changed, task="iq_classification")
    assert not failed["passed"]


@pytest.mark.parametrize(
    ("reference", "candidate"),
    [
        (np.asarray([[np.nan, 1.0]], dtype=np.float32), np.asarray([[0.0, 1.0]], dtype=np.float32)),
        (np.asarray([[0.0, 1.0]], dtype=np.float32), np.asarray([[np.inf, 1.0]], dtype=np.float32)),
    ],
)
def test_model_admission_rejects_nonfinite_reference_or_candidate(reference, candidate):
    metrics = compare_model_outputs(reference, candidate, task="iq_classification")
    assert metrics["passed"] is False
    assert metrics["reason"] == "nonfinite_values"


@pytest.mark.parametrize(
    "values",
    [np.asarray([], dtype=np.float32), np.asarray(1.0, dtype=np.float32), np.asarray([1.0], dtype=np.float32)],
)
def test_model_admission_rejects_outputs_without_a_nonempty_batch_axis(values):
    metrics = compare_model_outputs(values, values, task="iq_classification")
    assert metrics["passed"] is False
    assert metrics["reason"] == "invalid_output_shape"
