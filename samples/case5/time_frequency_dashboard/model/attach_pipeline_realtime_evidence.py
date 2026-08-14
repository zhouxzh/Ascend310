"""Attach a structurally validated board-side SDR pipeline report as immutable evidence.

This utility is intentionally a post-run operation.  It reads a completed
``rtl_sdr_npu_inference`` JSONL report and an admitted source manifest, verifies
their hashes and contracts, then writes a *new* manifest revision.  It never
rewrites the source manifest because the JSONL contains that source manifest's
SHA256.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import numpy as np

from .inference_manifest import (
    load_inference_manifest,
    model_contract_sha256,
    sha256_file,
    verify_artifact_hashes,
)
from ..rtl_sdr_npu_inference import (
    RF_INPUT_CONTEXTS,
    required_complex_samples,
    summarize_pipeline_realtime,
)
from ..rtl_sdr_run_report import summarize_rtl_sdr_run

CONTINUOUS_PIPELINE_MINIMUM_OBSERVATION_MS = 600_000.0


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Reject ambiguous JSONL records instead of accepting the last duplicate value."""
    record: dict[str, Any] = {}
    for key, value in pairs:
        if key in record:
            raise ValueError(f"JSONL object contains duplicate key: {key}")
        record[key] = value
    return record


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--inference-jsonl", type=Path)
    parser.add_argument(
        "--verify-only",
        action="store_true",
        help="validate a report against a manifest without writing a new manifest",
    )
    parser.add_argument(
        "--verify-attached",
        action="store_true",
        help="revalidate an already attached sibling manifest, its source manifest, JSONL, and CU8 capture",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="new manifest path; defaults to an .pipeline-verified.manifest.json sibling",
    )
    return parser.parse_args()


def _load_jsonl_report(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Load exactly one metadata header, zero or more batches, and one footer."""
    header: dict[str, Any] | None = None
    batches: list[dict[str, Any]] = []
    footer: dict[str, Any] | None = None
    state = "header"
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                raise ValueError(f"JSONL report has an empty record at line {line_number}")
            try:
                record = json.loads(
                    line,
                    object_pairs_hook=_reject_duplicate_json_keys,
                    parse_constant=lambda value: (_ for _ in ()).throw(
                        ValueError(f"unsupported non-standard JSON constant: {value}")
                    ),
                )
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"JSONL record {line_number} must be an object")
            record_type = record.get("record_type")
            if record_type == "run_metadata":
                if state != "header":
                    raise ValueError("JSONL run_metadata must be the first and only header")
                header = record
                state = "batches"
            elif record_type == "inference_batch":
                if state != "batches":
                    raise ValueError("JSONL inference_batch records must follow run_metadata")
                batches.append(record)
            elif record_type == "run_summary":
                if state != "batches":
                    raise ValueError("JSONL run_summary must be the final and only footer")
                footer = record
                state = "done"
            else:
                raise ValueError(f"JSONL record {line_number} has unsupported record_type")
    if header is None or footer is None:
        raise ValueError("JSONL report must contain run_metadata and run_summary records")
    if footer.get("completion_status") != "completed":
        raise ValueError("pipeline evidence requires a normally completed RTL-SDR run")
    return header, batches, footer


def _finite_nonnegative(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite non-negative number")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite non-negative number") from exc
    if not np.isfinite(numeric) or numeric < 0.0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return numeric


def _finite_positive(value: object, field: str) -> float:
    numeric = _finite_nonnegative(value, field)
    if numeric == 0.0:
        raise ValueError(f"{field} must be positive")
    return numeric


def _nonnegative_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _relative_or_absolute(path: Path, parent: Path) -> str:
    try:
        return str(path.relative_to(parent))
    except ValueError:
        return str(path)


def _assert_same_float(actual: object, expected: float, field: str) -> None:
    value = _finite_nonnegative(actual, field)
    if not np.isclose(value, expected, rtol=0.0, atol=1.0e-6):
        raise ValueError(f"{field} does not match recomputed pipeline evidence")


def _assert_same_json(actual: object, expected: object, field: str) -> None:
    if actual != expected:
        raise ValueError(f"JSONL {field} does not match the source manifest")


def _resolve_reference_path(value: object, *, base: Path, field: str) -> Path:
    path = Path(str(value))
    resolved = path if path.is_absolute() else base / path
    if not resolved.is_file():
        raise FileNotFoundError(f"{field} does not exist: {resolved}")
    return resolved.resolve()


def _expected_batch_duration_ms(manifest: Any, sample_rate_hz: float) -> float:
    return 1_000.0 * required_complex_samples(manifest) / sample_rate_hz


def summarize_continuous_pipeline_realtime(
    short_run: Mapping[str, Any], *, rf_input_context: str
) -> dict[str, Any]:
    """Derive the 10-minute antenna-connected verdict from validated run data."""
    observed_ms = _finite_positive(
        short_run.get("observation_duration_ms"), "pipeline observation_duration_ms"
    )
    short_run_passed = short_run.get("pipeline_real_time_passed") is True
    continuous_passed = bool(
        rf_input_context in {"antenna_connected", "lab_cabled"}
        and observed_ms >= CONTINUOUS_PIPELINE_MINIMUM_OBSERVATION_MS
        and short_run_passed
    )
    return {
        "minimum_observation_ms": CONTINUOUS_PIPELINE_MINIMUM_OBSERVATION_MS,
        "observation_duration_ms": observed_ms,
        "rf_input_context": rf_input_context,
        "short_run_pipeline_real_time_passed": short_run_passed,
        "continuous_pipeline_realtime_passed": continuous_passed,
    }


def pipeline_evidence_from_report(
    *, manifest_path: Path, report_path: Path
) -> dict[str, Any]:
    """Validate a completed JSONL report and return its immutable evidence object."""
    manifest_path = manifest_path.resolve()
    report_path = report_path.resolve()
    manifest = load_inference_manifest(manifest_path, require_accepted=True)
    verify_artifact_hashes(manifest)
    header, batches, footer = _load_jsonl_report(report_path)

    # Evidence attachment has a stronger provenance requirement than a timing
    # summary: every JSONL window must be bound to a non-overlapping range in
    # the archived CU8 file.  Reuse the read-only QC validator instead of
    # maintaining a second, weaker interpretation of the run artifact.
    summarize_rtl_sdr_run(report_path)

    if header.get("source") != "rtl":
        raise ValueError("pipeline evidence requires an RTL-SDR source report")
    if header.get("inference_backend") != "NPU (Ascend 310B)":
        raise ValueError("pipeline evidence requires an Ascend 310B NPU backend record")
    if not str(header.get("rtl_device", "")).strip():
        raise ValueError("pipeline evidence requires an RTL-SDR device record")
    if header.get("capture_file") is None:
        raise ValueError("pipeline evidence requires an RTL-SDR capture file record")
    if not batches:
        raise ValueError("pipeline evidence requires at least one inference batch")
    rf_input_context = str(header.get("rf_input_context", "unknown"))
    if rf_input_context not in RF_INPUT_CONTEXTS:
        raise ValueError("JSONL rf_input_context is unsupported")
    _assert_same_json(header.get("model_id"), manifest.model_id, "model_id")
    _assert_same_json(header.get("model_task"), manifest.task, "model_task")
    _assert_same_json(
        header.get("model_input_shape"), list(manifest.input_shape), "model_input_shape"
    )
    _assert_same_json(
        header.get("model_preprocessing"), dict(manifest.preprocessing), "model_preprocessing"
    )
    _assert_same_json(
        header.get("model_input_normalization"),
        manifest.normalization,
        "model_input_normalization",
    )
    _assert_same_json(
        header.get("model_sampling_convention"),
        manifest.sampling_convention,
        "model_sampling_convention",
    )
    _assert_same_json(
        header.get("model_source_revision"), manifest.source_revision, "model_source_revision"
    )
    _assert_same_json(header.get("model_cann_version"), manifest.cann_version, "model_cann_version")
    if header.get("model_manifest_sha256") != sha256_file(manifest_path):
        raise ValueError("JSONL report was not generated by this exact source manifest")
    for field, expected in (
        ("model_onnx_sha256", manifest.onnx_sha256),
        ("model_om_sha256", manifest.om_sha256),
        ("model_upstream_weight_sha256", manifest.upstream_weight_sha256),
    ):
        if str(header.get(field, "")).lower() != expected:
            raise ValueError(f"JSONL {field} does not match the source manifest")
    sample_rate_hz = _finite_positive(header.get("sample_rate_hz"), "sample_rate_hz")
    if manifest.sample_rate_hz is not None and not np.isclose(
        sample_rate_hz, manifest.sample_rate_hz, rtol=0.0, atol=1.0e-6
    ):
        raise ValueError("JSONL sample_rate_hz does not match the source manifest")
    batch_duration_ms = _finite_positive(
        header.get("batch_duration_ms"), "batch_duration_ms"
    )
    expected_batch_duration_ms = _expected_batch_duration_ms(manifest, sample_rate_hz)
    if not np.isclose(
        batch_duration_ms, expected_batch_duration_ms, rtol=0.0, atol=1.0e-6
    ):
        raise ValueError(
            "JSONL batch_duration_ms does not match the admitted fixed input window"
        )
    pipeline = footer.get("pipeline_realtime")
    if not isinstance(pipeline, Mapping):
        raise ValueError("JSONL run_summary.pipeline_realtime must be an object")
    produced_batches = _nonnegative_int(
        pipeline.get("produced_batches"), "pipeline_realtime.produced_batches"
    )
    completed_batches = _nonnegative_int(
        pipeline.get("completed_batches"), "pipeline_realtime.completed_batches"
    )
    dropped_batches = _nonnegative_int(
        pipeline.get("dropped_batches"), "pipeline_realtime.dropped_batches"
    )
    if completed_batches != len(batches):
        raise ValueError("JSONL completed batch count does not match inference_batch records")
    footer_completed_batches = _nonnegative_int(
        footer.get("completed_batches"), "run_summary.completed_batches"
    )
    if footer_completed_batches != len(batches):
        raise ValueError("JSONL run_summary completed_batches does not match batch records")
    footer_produced_batches = _nonnegative_int(
        footer.get("produced_batches"), "run_summary.produced_batches"
    )
    if footer_produced_batches != produced_batches:
        raise ValueError("JSONL run_summary produced_batches does not match pipeline summary")
    footer_dropped_batches = _nonnegative_int(
        footer.get("queue_dropped_batches"), "run_summary.queue_dropped_batches"
    )
    if footer_dropped_batches != dropped_batches:
        raise ValueError("JSONL footer queue_dropped_batches does not match pipeline summary")
    if footer.get("inference_backend") != "NPU (Ascend 310B)":
        raise ValueError("JSONL footer does not record an Ascend 310B NPU backend")
    if _nonnegative_int(footer.get("archive_failed_batches"), "run_summary.archive_failed_batches") != 0:
        raise ValueError("pipeline evidence cannot be attached to a run with archive failures")
    capture_path = _resolve_reference_path(
        header["capture_file"], base=report_path.parent, field="JSONL capture_file"
    )
    capture_sha256 = str(footer.get("capture_sha256", "")).lower()
    if capture_sha256 != sha256_file(capture_path):
        raise ValueError("JSONL capture SHA256 does not match the captured CU8 file")
    capture_bytes = _nonnegative_int(footer.get("capture_bytes"), "capture_bytes")
    if capture_bytes != capture_path.stat().st_size:
        raise ValueError("JSONL capture byte count does not match the captured CU8 file")
    expected_capture_bytes = produced_batches * required_complex_samples(manifest) * 2
    if capture_bytes != expected_capture_bytes:
        raise ValueError("captured CU8 byte count does not match the produced model batches")
    _assert_same_float(pipeline.get("batch_duration_ms"), batch_duration_ms, "pipeline batch duration")
    if not np.isclose(
        float(pipeline["batch_duration_ms"]), expected_batch_duration_ms, rtol=0.0, atol=1.0e-6
    ):
        raise ValueError(
            "JSONL pipeline_realtime.batch_duration_ms does not match the admitted fixed input window"
        )
    if pipeline.get("evidence_scope") != "short_run_pipeline_window_check":
        raise ValueError("JSONL pipeline_realtime must declare the short-run evidence scope")
    if pipeline.get("continuous_pipeline_soak_verified") is not False:
        raise ValueError("JSONL pipeline_realtime must not claim a continuous pipeline soak")
    observation_duration_ms = _finite_positive(
        pipeline.get("observation_duration_ms"), "pipeline_realtime.observation_duration_ms"
    )
    _assert_same_float(footer.get("wall_time_ms"), observation_duration_ms, "wall_time_ms")
    post_capture_samples: list[float] = []
    for index, batch in enumerate(batches):
        _assert_same_json(
            batch.get("input_shape"), list(manifest.input_shape), f"inference_batch[{index}].input_shape"
        )
        if batch.get("backend") != "NPU (Ascend 310B)":
            raise ValueError(f"inference_batch[{index}] does not record the Ascend 310B NPU backend")
        if manifest.output_shape is not None:
            _assert_same_json(
                batch.get("output_shape"),
                list(manifest.output_shape),
                f"inference_batch[{index}].output_shape",
            )
        _finite_nonnegative(
            batch.get("npu_inference_ms"), f"inference_batch[{index}].npu_inference_ms"
        )
        post_capture_samples.append(_finite_nonnegative(
            batch.get("post_capture_pipeline_ms"),
            f"inference_batch[{index}].post_capture_pipeline_ms",
        ))
    recomputed = summarize_pipeline_realtime(
        produced_batches=produced_batches,
        completed_batches=completed_batches,
        dropped_batches=dropped_batches,
        post_capture_pipeline_ms=post_capture_samples,
        batch_duration_ms=batch_duration_ms,
    )
    for field in (
        "minimum_batches",
        "produced_batches",
        "completed_batches",
        "dropped_batches",
        "sufficient_samples",
        "complete_delivery",
        "latency_meets_window_budget",
        "pipeline_real_time_passed",
    ):
        if pipeline.get(field) != recomputed[field]:
            raise ValueError(f"JSONL pipeline_realtime.{field} does not match recomputed result")
    for field in (
        "post_capture_pipeline_p50_ms",
        "post_capture_pipeline_p95_ms",
        "post_capture_pipeline_max_ms",
        "batch_duration_ms",
    ):
        _assert_same_float(
            pipeline.get(field), float(recomputed[field]), f"pipeline_realtime.{field}"
        )
    # A passing report must prove that every produced sequence arrived.  This
    # makes the zero-drop verdict independently checkable from the JSONL rows.
    if recomputed["pipeline_real_time_passed"]:
        sequences = [batch.get("sequence") for batch in batches]
        if sequences != list(range(completed_batches)):
            raise ValueError("passing pipeline evidence must contain contiguous batch sequences")
    continuous = summarize_continuous_pipeline_realtime(
        {
            "observation_duration_ms": observation_duration_ms,
            "pipeline_real_time_passed": bool(recomputed["pipeline_real_time_passed"]),
        },
        rf_input_context=rf_input_context,
    )

    return {
        "schema_version": 1,
        "validation_level": "structurally_validated_self_report",
        "verified": True,
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": str(header["source"]),
        "rf_input_context": rf_input_context,
        "report_path": str(report_path),
        "report_sha256": sha256_file(report_path),
        "capture_path": str(capture_path),
        "capture_sha256": capture_sha256,
        "capture_bytes": capture_bytes,
        "source_manifest_path": str(manifest_path),
        "source_run_manifest_sha256": sha256_file(manifest_path),
        "model_contract_sha256": model_contract_sha256(manifest),
        "sample_rate_hz": sample_rate_hz,
        "batch_duration_ms": batch_duration_ms,
        "minimum_batches": int(recomputed["minimum_batches"]),
        "produced_batches": int(recomputed["produced_batches"]),
        "completed_batches": int(recomputed["completed_batches"]),
        "dropped_batches": int(recomputed["dropped_batches"]),
        "post_capture_pipeline_p50_ms": float(recomputed["post_capture_pipeline_p50_ms"]),
        "post_capture_pipeline_p95_ms": float(recomputed["post_capture_pipeline_p95_ms"]),
        "post_capture_pipeline_max_ms": float(recomputed["post_capture_pipeline_max_ms"]),
        "pipeline_real_time_passed": bool(recomputed["pipeline_real_time_passed"]),
        "observation_duration_ms": observation_duration_ms,
        "evidence_scope": "short_run_pipeline_window_check",
        "continuous_pipeline_soak_verified": False,
        "continuous_pipeline_realtime": continuous,
    }


def _assert_evidence_matches_recomputed(
    stored: Mapping[str, Any], recomputed: Mapping[str, Any]
) -> None:
    exact_fields = (
        "schema_version",
        "validation_level",
        "verified",
        "source",
        "report_sha256",
        "capture_sha256",
        "capture_bytes",
        "source_run_manifest_sha256",
        "model_contract_sha256",
        "minimum_batches",
        "produced_batches",
        "completed_batches",
        "dropped_batches",
        "pipeline_real_time_passed",
        "evidence_scope",
        "continuous_pipeline_soak_verified",
    )
    for field in exact_fields:
        if stored.get(field) != recomputed.get(field):
            raise ValueError(f"attached pipeline evidence {field} does not match the report")
    if stored.get("continuous_pipeline_realtime") != recomputed.get(
        "continuous_pipeline_realtime"
    ):
        raise ValueError("attached continuous pipeline evidence does not match the report")
    # v4 manifests emitted before RF-input context existed remain independently
    # checkable.  Newer ones bind the operator-declared context as well.
    if "rf_input_context" in stored and (
        stored.get("rf_input_context") != recomputed.get("rf_input_context")
    ):
        raise ValueError("attached pipeline evidence rf_input_context does not match the report")
    for field in (
        "sample_rate_hz",
        "batch_duration_ms",
        "post_capture_pipeline_p50_ms",
        "post_capture_pipeline_p95_ms",
        "post_capture_pipeline_max_ms",
        "observation_duration_ms",
    ):
        _assert_same_float(stored.get(field), float(recomputed[field]), field)


def verify_attached_pipeline_realtime_evidence(manifest_path: Path) -> dict[str, Any]:
    """Reopen all evidence inputs and recompute an attached short-run verdict.

    Loading a manifest deliberately does not invoke this operation: deployment
    must not become dependent on an archived report path.  Call this explicit
    verifier when making a claim about the attached run.
    """
    attached_path = manifest_path.resolve()
    attached = load_inference_manifest(attached_path, require_accepted=True)
    verify_artifact_hashes(attached)
    evidence = attached.admission.get("pipeline_realtime")
    if not isinstance(evidence, Mapping):
        raise ValueError("manifest has no attached pipeline realtime evidence")
    source_manifest_path = _resolve_reference_path(
        evidence.get("source_manifest_path"),
        base=attached_path.parent,
        field="attached source_manifest_path",
    )
    if sha256_file(source_manifest_path) != evidence.get("source_run_manifest_sha256"):
        raise ValueError("attached source manifest SHA256 does not match the evidence")
    source_manifest = load_inference_manifest(source_manifest_path, require_accepted=True)
    if "pipeline_realtime" in source_manifest.admission:
        raise ValueError("attached evidence source manifest must not itself contain pipeline evidence")
    if model_contract_sha256(source_manifest) != model_contract_sha256(attached):
        raise ValueError("attached source manifest has a different model contract")
    report_path = _resolve_reference_path(
        evidence.get("report_path"), base=attached_path.parent, field="attached report_path"
    )
    if sha256_file(report_path) != evidence.get("report_sha256"):
        raise ValueError("attached JSONL report SHA256 does not match the evidence")
    capture_path = _resolve_reference_path(
        evidence.get("capture_path"), base=attached_path.parent, field="attached capture_path"
    )
    if sha256_file(capture_path) != evidence.get("capture_sha256"):
        raise ValueError("attached CU8 capture SHA256 does not match the evidence")
    recomputed = pipeline_evidence_from_report(
        manifest_path=source_manifest_path, report_path=report_path
    )
    _assert_evidence_matches_recomputed(evidence, recomputed)
    return recomputed


def attach_pipeline_realtime_evidence(
    *, source_manifest: Path, report_path: Path, output: Path | None = None
) -> Path:
    """Attach structurally validated short-run evidence to a new sibling manifest."""
    source_manifest = source_manifest.resolve()
    report_path = report_path.resolve()
    evidence = pipeline_evidence_from_report(
        manifest_path=source_manifest, report_path=report_path
    )
    default_output = source_manifest.with_name(
        source_manifest.name.removesuffix(".manifest.json")
        + ".pipeline-verified.manifest.json"
    )
    output = (output or default_output).resolve()
    if output == source_manifest:
        raise ValueError("--output must be a new manifest path; the source manifest is evidence-bound")
    if output.parent != source_manifest.parent:
        raise ValueError(
            "--output must remain beside the source manifest so relative artifacts remain bound"
        )
    if output in {report_path, Path(str(evidence["capture_path"])).resolve()}:
        raise ValueError("--output must not overwrite an evidence input")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite an existing attached manifest: {output}")
    # Take the exact source bytes after report validation.  If it changed in
    # between, reject rather than combining evidence from two source revisions.
    source_bytes = source_manifest.read_bytes()
    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    if source_sha256 != evidence["source_run_manifest_sha256"]:
        raise RuntimeError("source manifest changed while pipeline evidence was being attached")
    source_raw = json.loads(source_bytes.decode("utf-8"))
    if not isinstance(source_raw, dict):
        raise ValueError("manifest root must be an object")
    admission = source_raw.get("admission")
    if not isinstance(admission, dict):
        raise ValueError("manifest admission must be an object")
    if "pipeline_realtime" in admission:
        raise ValueError("source manifest already has pipeline realtime evidence; use it as source")
    admission["pipeline_realtime"] = evidence
    source_raw["schema_version"] = max(int(source_raw.get("schema_version", 1)), 4)
    output.parent.mkdir(parents=True, exist_ok=True)
    source_raw["admission"]["pipeline_realtime"]["report_path"] = _relative_or_absolute(
        report_path, output.parent
    )
    source_raw["admission"]["pipeline_realtime"]["capture_path"] = _relative_or_absolute(
        Path(str(evidence["capture_path"])), output.parent
    )
    source_raw["admission"]["pipeline_realtime"]["source_manifest_path"] = _relative_or_absolute(
        source_manifest, output.parent
    )
    serialized = json.dumps(source_raw, indent=2, ensure_ascii=True, allow_nan=False)
    with output.open("x", encoding="utf-8") as handle:
        handle.write(serialized + "\n")
    # Reload the result to ensure the stored evidence's contract binding is valid.
    load_inference_manifest(output, require_accepted=True)
    verify_attached_pipeline_realtime_evidence(output)
    return output


def main() -> int:
    args = parse_args()
    if args.verify_attached:
        if args.inference_jsonl is not None or args.verify_only or args.output is not None:
            raise ValueError("--verify-attached only accepts --manifest")
        evidence = verify_attached_pipeline_realtime_evidence(args.manifest)
        print(json.dumps(evidence, indent=2, ensure_ascii=True, allow_nan=False))
        return 0
    if args.inference_jsonl is None:
        raise ValueError("--inference-jsonl is required unless --verify-attached is used")
    if args.verify_only:
        if args.output is not None:
            raise ValueError("--output cannot be used with --verify-only")
        evidence = pipeline_evidence_from_report(
            manifest_path=args.manifest, report_path=args.inference_jsonl
        )
        print(json.dumps(evidence, indent=2, ensure_ascii=True, allow_nan=False))
        return 0
    output = attach_pipeline_realtime_evidence(
        source_manifest=args.manifest,
        report_path=args.inference_jsonl,
        output=args.output,
    )
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
