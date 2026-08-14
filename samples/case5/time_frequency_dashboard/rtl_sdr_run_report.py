"""Summarize a completed RTL-SDR NPU JSONL run and its CU8 capture.

This is a read-only post-run quality-control tool.  It reports the timing
distribution recorded by ``rtl_sdr_npu_inference`` and basic byte-level IQ
statistics, such as DC offset and ADC clipping.  It does not establish RF
signal labels, receiver calibration, or model accuracy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import numpy as np


TIMING_FIELDS = (
    "capture_acquisition_ms",
    "archive_write_ms",
    "decode_ms",
    "queue_wait_ms",
    "preprocessing_ms",
    "npu_inference_ms",
    "postprocessing_ms",
    "post_capture_pipeline_ms",
    "end_to_end_ms",
)
# These are the latency fields needed to interpret a completed NPU run.  The
# remaining timing fields are useful breakdowns, but older reports may omit
# them without making the run's primary latency evidence ambiguous.
CORE_TIMING_FIELDS = {
    "npu_inference_ms",
    "post_capture_pipeline_ms",
    "end_to_end_ms",
}
EXPECTED_NPU_BACKEND = "NPU (Ascend 310B)"
CU8_HISTOGRAM_BINS = np.arange(256, dtype=np.float64)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inference-jsonl", type=Path)
    parser.add_argument(
        "--capture-cu8",
        type=Path,
        help="capture path for --capture-only, or an override for a moved JSONL capture",
    )
    parser.add_argument(
        "--capture-only",
        action="store_true",
        help="report byte-level CU8 quality only; do not combine it with another run's timings",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="optional JSON summary path; stdout is always written",
    )
    return parser.parse_args()


def _reject_nonstandard_json_constant(value: str) -> None:
    raise ValueError(f"JSONL contains unsupported non-standard constant: {value}")


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Construct a JSON object while rejecting ambiguous duplicate keys."""
    record: dict[str, Any] = {}
    for key, value in pairs:
        if key in record:
            raise ValueError(f"JSONL object contains duplicate key: {key}")
        record[key] = value
    return record


def _load_completed_report(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                raise ValueError(f"JSONL report has an empty record at line {line_number}")
            try:
                record = json.loads(
                    line,
                    object_pairs_hook=_reject_duplicate_json_keys,
                    parse_constant=_reject_nonstandard_json_constant,
                )
            except (json.JSONDecodeError, ValueError) as exc:
                raise ValueError(f"invalid JSONL at {path}:{line_number}: {exc}") from exc
            if not isinstance(record, dict):
                raise ValueError(f"JSONL record {line_number} must be an object")
            records.append(record)
    if len(records) < 3:
        raise ValueError("JSONL report must contain at least one inference_batch record")
    header, footer = records[0], records[-1]
    if header.get("record_type") != "run_metadata":
        raise ValueError("JSONL report must begin with run_metadata")
    if footer.get("record_type") != "run_summary":
        raise ValueError("JSONL report must end with run_summary")
    if footer.get("completion_status") != "completed":
        raise ValueError(
            "JSONL report was not normally completed (not a completed run) and cannot be used for QC evidence"
        )
    batches = records[1:-1]
    if not batches:
        raise ValueError("JSONL report must contain at least one inference_batch record")
    if any(record.get("record_type") != "inference_batch" for record in batches):
        raise ValueError("JSONL report contains an unexpected record type")
    return header, batches, footer


def _timing_summary(rows: Iterable[dict[str, Any]], field: str) -> dict[str, float | int] | None:
    row_list = list(rows)
    missing = [index for index, row in enumerate(row_list) if field not in row]
    if field in CORE_TIMING_FIELDS and missing:
        raise ValueError(f"inference_batch records are missing required {field}")
    values_list: list[float] = []
    for index, row in enumerate(row_list):
        if field not in row:
            continue
        value = row[field]
        if isinstance(value, bool):
            raise ValueError(f"inference_batch[{index}].{field} must be a finite non-negative number")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"inference_batch[{index}].{field} must be a finite non-negative number"
            ) from exc
        values_list.append(numeric)
    values = np.asarray(values_list, dtype=np.float64)
    if not values.size:
        return None
    if not np.all(np.isfinite(values)) or np.any(values < 0.0):
        raise ValueError(f"{field} contains a non-finite or negative value")
    return {
        "samples": int(values.size),
        "missing_rows": len(missing),
        "mean_ms": float(values.mean()),
        "p50_ms": float(np.percentile(values, 50.0)),
        "p95_ms": float(np.percentile(values, 95.0)),
        "max_ms": float(values.max()),
    }


def _capture_file_reference(header: dict[str, Any]) -> str:
    value = header.get("capture_file")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("RTL-SDR report metadata must bind a non-empty capture_file")
    return value


def _resolve_capture_path(header: dict[str, Any], report_path: Path, capture_path: Path | None) -> Path:
    # An override is only for a capture that has been moved after the run.  It
    # cannot make an otherwise unbound JSONL report eligible for QC.
    capture_reference = _capture_file_reference(header)
    if capture_path is not None:
        resolved = capture_path
    else:
        candidate = Path(capture_reference)
        resolved = candidate if candidate.is_absolute() else report_path.parent / candidate
    resolved = resolved.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(f"CU8 capture does not exist: {resolved}")
    return resolved


def _capture_binding(footer: dict[str, Any]) -> tuple[int, str]:
    if "capture_bytes" not in footer or "capture_sha256" not in footer:
        raise ValueError("JSONL footer must bind capture_bytes and capture_sha256")
    capture_bytes = footer["capture_bytes"]
    if isinstance(capture_bytes, bool) or not isinstance(capture_bytes, int) or capture_bytes < 0:
        raise ValueError("JSONL footer capture_bytes must be a non-negative integer")
    if capture_bytes % 2:
        raise ValueError("JSONL footer capture_bytes must be even for interleaved CU8 IQ")
    capture_sha256 = footer["capture_sha256"]
    if not isinstance(capture_sha256, str):
        raise ValueError("JSONL footer capture_sha256 must be a SHA256 hex digest")
    normalized = capture_sha256.lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError("JSONL footer capture_sha256 must be a SHA256 hex digest")
    return capture_bytes, normalized


def _nonnegative_integer(value: Any, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _finite_positive(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite positive number")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite positive number") from exc
    if not np.isfinite(numeric) or numeric <= 0.0:
        raise ValueError(f"{field} must be a finite positive number")
    return numeric


def _positive_shape(value: Any, field: str) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{field} must be a JSON array of positive integer dimensions")
    shape = tuple(_nonnegative_integer(item, f"{field}[{index}]") for index, item in enumerate(value))
    if any(item == 0 for item in shape):
        raise ValueError(f"{field} must contain only positive integer dimensions")
    return shape


def _required_complex_samples_from_shape(
    task: str,
    shape_value: Any,
    field: str,
) -> tuple[tuple[int, ...], int]:
    """Validate a fixed model input shape and derive its raw-IQ window size."""
    shape = _positive_shape(shape_value, field)
    if task == "iq_classification":
        if len(shape) != 3 or shape[1] != 2:
            raise ValueError(f"{field} must have IQ-classifier shape [B, 2, N]")
        return shape, shape[0] * shape[2]
    if task == "spectrogram_detection":
        if len(shape) != 4:
            raise ValueError(f"{field} must have spectrogram-detector shape [B, C, H, W]")
        return shape, shape[0] * shape[2] * shape[3]
    raise ValueError(
        "RTL-SDR report metadata model_task must be iq_classification or "
        "spectrogram_detection"
    )


def _validate_capture_provenance(
    header: dict[str, Any],
    rows: Iterable[dict[str, Any]],
    *,
    capture_complex_samples: int,
) -> None:
    """Tie each recorded inference window to a non-overlapping CU8 range."""
    task = header.get("model_task")
    if not isinstance(task, str):
        raise ValueError("RTL-SDR report metadata must include a string model_task")

    metadata_shape = header.get("model_input_shape")
    metadata_shape_error: ValueError | None = None
    metadata_required: int | None = None
    try:
        _, metadata_required = _required_complex_samples_from_shape(
            task,
            metadata_shape,
            "run_metadata.model_input_shape",
        )
    except ValueError as exc:
        metadata_shape_error = exc

    previous_sequence: int | None = None
    previous_offset: int | None = None
    previous_end = 0
    fallback_shape: tuple[int, ...] | None = None
    for index, row in enumerate(rows):
        sequence = _nonnegative_integer(row.get("sequence"), f"inference_batch[{index}].sequence")
        offset = _nonnegative_integer(
            row.get("source_sample_offset"),
            f"inference_batch[{index}].source_sample_offset",
        )
        if previous_sequence is not None and sequence <= previous_sequence:
            raise ValueError("inference_batch sequence values must be strictly increasing")
        if previous_offset is not None and offset <= previous_offset:
            raise ValueError("inference_batch source_sample_offset values must be strictly increasing")

        batch_required = metadata_required
        if "input_shape" in row:
            batch_shape, batch_required_from_shape = _required_complex_samples_from_shape(
                task,
                row["input_shape"],
                f"inference_batch[{index}].input_shape",
            )
            if metadata_required is not None:
                metadata_shape_tuple, _ = _required_complex_samples_from_shape(
                    task,
                    metadata_shape,
                    "run_metadata.model_input_shape",
                )
                if batch_shape != metadata_shape_tuple:
                    raise ValueError(
                        f"inference_batch[{index}].input_shape does not match "
                        "run_metadata.model_input_shape"
                    )
            elif fallback_shape is None:
                fallback_shape = batch_shape
            elif batch_shape != fallback_shape:
                raise ValueError(
                    "inference_batch input_shape values must match when metadata shape is unavailable"
                )
            batch_required = batch_required_from_shape
        elif batch_required is None:
            assert metadata_shape_error is not None
            raise ValueError(
                f"{metadata_shape_error}; inference_batch[{index}].input_shape is required "
                "when metadata shape is unusable"
            )

        assert batch_required is not None
        if offset != sequence * batch_required:
            raise ValueError(
                "inference_batch source_sample_offset must equal sequence times its fixed input window"
            )
        if offset < previous_end:
            raise ValueError("inference_batch source_sample_offset windows overlap")
        if offset + batch_required > capture_complex_samples:
            raise ValueError("inference_batch IQ window extends beyond the CU8 capture")
        previous_sequence = sequence
        previous_offset = offset
        previous_end = offset + batch_required


def _validate_npu_backends(
    header: dict[str, Any], rows: Iterable[dict[str, Any]], footer: dict[str, Any]
) -> None:
    if header.get("inference_backend") != EXPECTED_NPU_BACKEND:
        raise ValueError("RTL-SDR report header does not record an Ascend 310B NPU backend")
    if footer.get("inference_backend") != EXPECTED_NPU_BACKEND:
        raise ValueError("RTL-SDR report footer does not record an Ascend 310B NPU backend")
    for index, row in enumerate(rows):
        if row.get("backend") != EXPECTED_NPU_BACKEND:
            raise ValueError(
                f"inference_batch[{index}] does not record an Ascend 310B NPU backend"
            )


def _validate_completed_counts(
    rows: list[dict[str, Any]], footer: dict[str, Any]
) -> None:
    """Verify footer counters are reconcilable with every accepted batch row."""
    completed = _nonnegative_integer(footer.get("completed_batches"), "run_summary.completed_batches")
    produced = _nonnegative_integer(footer.get("produced_batches"), "run_summary.produced_batches")
    dropped = _nonnegative_integer(
        footer.get("queue_dropped_batches"), "run_summary.queue_dropped_batches"
    )
    archive_failures = _nonnegative_integer(
        footer.get("archive_failed_batches"), "run_summary.archive_failed_batches"
    )
    if completed != len(rows):
        raise ValueError("run_summary completed_batches does not match inference_batch records")
    if produced != completed + dropped:
        raise ValueError(
            "run_summary produced_batches must equal completed_batches plus queue_dropped_batches"
        )
    if archive_failures != 0:
        raise ValueError("normally completed RTL-SDR QC runs cannot contain archive failures")

    previous_dropped = 0
    for index, row in enumerate(rows):
        row_dropped = _nonnegative_integer(
            row.get("queue_dropped_batches", 0),
            f"inference_batch[{index}].queue_dropped_batches",
        )
        if row_dropped < previous_dropped or row_dropped > dropped:
            raise ValueError(
                "inference_batch queue_dropped_batches must be non-decreasing and no greater than the summary"
            )
        previous_dropped = row_dropped


def _validate_completed_capture_coverage(
    header: dict[str, Any],
    rows: list[dict[str, Any]],
    footer: dict[str, Any],
    *,
    capture_complex_samples: int,
) -> None:
    """Ensure a completed archive contains exactly the recorded fixed windows."""
    task = header.get("model_task")
    if not isinstance(task, str):
        raise ValueError("RTL-SDR report metadata must include a string model_task")
    metadata_shape = header.get("model_input_shape")
    try:
        _, fixed_window_samples = _required_complex_samples_from_shape(
            task,
            metadata_shape,
            "run_metadata.model_input_shape",
        )
    except ValueError as metadata_error:
        # Older reports may omit the metadata shape but still record and
        # validate an identical fixed input shape in every batch.  Preserve
        # that compatibility without weakening the archive-coverage proof.
        row_shapes: list[tuple[int, ...]] = []
        for index, row in enumerate(rows):
            if "input_shape" not in row:
                raise ValueError(
                    f"{metadata_error}; inference_batch[{index}].input_shape is required "
                    "when metadata shape is unavailable"
                ) from metadata_error
            shape, _ = _required_complex_samples_from_shape(
                task,
                row["input_shape"],
                f"inference_batch[{index}].input_shape",
            )
            row_shapes.append(shape)
        if not row_shapes or any(shape != row_shapes[0] for shape in row_shapes[1:]):
            raise ValueError(
                "inference_batch input_shape values must be identical when metadata shape is unavailable"
            ) from metadata_error
        _, fixed_window_samples = _required_complex_samples_from_shape(
            task,
            list(row_shapes[0]),
            "inference_batch[0].input_shape",
        )
    produced = _nonnegative_integer(footer.get("produced_batches"), "run_summary.produced_batches")
    completed = _nonnegative_integer(footer.get("completed_batches"), "run_summary.completed_batches")
    dropped = _nonnegative_integer(
        footer.get("queue_dropped_batches"), "run_summary.queue_dropped_batches"
    )
    if capture_complex_samples != produced * fixed_window_samples:
        raise ValueError(
            "completed CU8 capture length does not equal produced fixed-size inference windows"
        )
    sequences = [
        _nonnegative_integer(row.get("sequence"), f"inference_batch[{index}].sequence")
        for index, row in enumerate(rows)
    ]
    if len(set(sequences)) != len(sequences):
        raise ValueError("inference_batch sequence values must be unique")
    expected_sequences = set(range(produced))
    observed_sequences = set(sequences)
    missing = expected_sequences - observed_sequences
    if observed_sequences - expected_sequences or len(sequences) != completed:
        raise ValueError("inference_batch sequences are inconsistent with the completed summary")
    if len(missing) != dropped:
        raise ValueError(
            "inference_batch sequence gaps do not match run_summary.queue_dropped_batches"
        )
    _validate_complete_window_capture_plan(
        header,
        footer,
        fixed_window_samples=fixed_window_samples,
        capture_complex_samples=capture_complex_samples,
    )


def _validated_plan_float(value: Any, field: str) -> float:
    return _finite_positive(value, field)


def _validate_complete_window_capture_plan(
    header: dict[str, Any],
    footer: dict[str, Any],
    *,
    fixed_window_samples: int,
    capture_complex_samples: int,
) -> None:
    """Validate the complete-window policy recorded by current live runs.

    JSONL reports pre-dating this policy intentionally remain readable.  A
    report that declares the policy, however, must bind its requested duration
    to the whole-window plan and to the archived CU8 sample count.
    """
    policy = header.get("capture_plan_policy")
    if policy is None:
        return
    if header.get("source") != "rtl":
        raise ValueError("only live RTL-SDR reports may declare a capture plan policy")
    if policy != "ceil_requested_duration_to_complete_fixed_windows_v1":
        raise ValueError("RTL-SDR report has an unsupported capture plan policy")
    if footer.get("capture_plan_policy") != policy:
        raise ValueError("run_summary capture plan policy does not match run_metadata")

    sample_rate = _finite_positive(header.get("sample_rate_hz"), "run_metadata.sample_rate_hz")
    requested_duration = _validated_plan_float(
        header.get("requested_duration_seconds"),
        "run_metadata.requested_duration_seconds",
    )
    requested_samples = _nonnegative_integer(
        header.get("requested_capture_samples"),
        "run_metadata.requested_capture_samples",
    )
    planned_duration = _validated_plan_float(
        header.get("planned_capture_duration_seconds"),
        "run_metadata.planned_capture_duration_seconds",
    )
    planned_samples = _nonnegative_integer(
        header.get("planned_capture_samples"),
        "run_metadata.planned_capture_samples",
    )
    planned_batches = _nonnegative_integer(
        header.get("planned_capture_batches"),
        "run_metadata.planned_capture_batches",
    )
    if requested_samples <= 0 or planned_samples <= 0 or planned_batches <= 0:
        raise ValueError("RTL-SDR capture plan counts must be positive")
    requested_exact = sample_rate * requested_duration
    nearest_requested_samples = round(requested_exact)
    expected_requested_samples = (
        int(nearest_requested_samples)
        if np.isclose(requested_exact, nearest_requested_samples, rtol=0.0, atol=1.0e-6)
        else int(np.ceil(requested_exact))
    )
    if requested_samples != expected_requested_samples:
        raise ValueError("requested RTL-SDR capture samples do not match duration and sample rate")
    if planned_samples != planned_batches * fixed_window_samples:
        raise ValueError("planned RTL-SDR capture samples do not match complete fixed windows")
    if not requested_samples <= planned_samples < requested_samples + fixed_window_samples:
        raise ValueError("planned RTL-SDR capture samples are not the minimal complete-window plan")
    if not np.isclose(
        planned_duration,
        planned_samples / sample_rate,
        rtol=0.0,
        atol=1.0e-9,
    ):
        raise ValueError("planned RTL-SDR capture duration does not match samples and sample rate")
    if capture_complex_samples != planned_samples:
        raise ValueError("CU8 capture length does not match the declared RTL-SDR capture plan")

    for field, expected in (
        ("requested_duration_seconds", requested_duration),
        ("planned_capture_duration_seconds", planned_duration),
        ("planned_capture_samples", planned_samples),
        ("planned_capture_batches", planned_batches),
    ):
        value = footer.get(field)
        if isinstance(expected, float):
            numeric = _validated_plan_float(value, f"run_summary.{field}")
            if not np.isclose(numeric, expected, rtol=0.0, atol=1.0e-9):
                raise ValueError(f"run_summary.{field} does not match run_metadata")
        elif _nonnegative_integer(value, f"run_summary.{field}") != expected:
            raise ValueError(f"run_summary.{field} does not match run_metadata")

    produced = _nonnegative_integer(footer.get("produced_batches"), "run_summary.produced_batches")
    if produced != planned_batches:
        raise ValueError("run_summary produced_batches does not match the RTL-SDR capture plan")


def _validate_completed_metadata(header: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    """Validate the physical and model-window metadata used to interpret QC."""
    _finite_positive(header.get("sample_rate_hz"), "run_metadata.sample_rate_hz")
    _finite_positive(header.get("center_frequency_hz"), "run_metadata.center_frequency_hz")
    _finite_positive(header.get("batch_duration_ms"), "run_metadata.batch_duration_ms")


def _safe_output_path(output: Path, protected_paths: Iterable[Path]) -> Path:
    resolved = output.resolve()
    if any(resolved == protected.resolve() for protected in protected_paths):
        raise ValueError("--output must not overwrite an input JSONL or CU8 file")
    if resolved.exists():
        raise FileExistsError(f"refusing to overwrite an existing output: {resolved}")
    return resolved


def cu8_channel_statistics(path: Path, *, chunk_bytes: int = 8 * 1024 * 1024) -> dict[str, Any]:
    """Stream an interleaved CU8 capture and return exact byte-level statistics."""
    if chunk_bytes <= 0 or chunk_bytes % 2:
        raise ValueError("chunk_bytes must be a positive even number")
    i_histogram = np.zeros(256, dtype=np.uint64)
    q_histogram = np.zeros(256, dtype=np.uint64)
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            raw = handle.read(chunk_bytes)
            if not raw:
                break
            if len(raw) % 2:
                raise ValueError("CU8 capture has an odd byte count")
            digest.update(raw)
            samples = np.frombuffer(raw, dtype=np.uint8)
            i_histogram += np.bincount(samples[0::2], minlength=256).astype(np.uint64)
            q_histogram += np.bincount(samples[1::2], minlength=256).astype(np.uint64)

    sample_count = int(i_histogram.sum())
    if sample_count == 0:
        raise ValueError("CU8 capture is empty")

    def channel(histogram: np.ndarray) -> dict[str, float]:
        mean = float(np.dot(histogram, CU8_HISTOGRAM_BINS) / sample_count)
        second_moment = float(
            np.dot(histogram, CU8_HISTOGRAM_BINS * CU8_HISTOGRAM_BINS) / sample_count
        )
        return {
            "mean_cu8": mean,
            "stddev_cu8": float(max(second_moment - mean * mean, 0.0) ** 0.5),
            "dc_offset_from_127_5": mean - 127.5,
            "clip_fraction": float((histogram[0] + histogram[255]) / sample_count),
        }

    return {
        "capture_bytes": path.stat().st_size,
        "capture_sha256": digest.hexdigest(),
        "complex_samples": sample_count,
        "i": channel(i_histogram),
        "q": channel(q_histogram),
    }


def summarize_rtl_sdr_run(
    report_path: Path, *, capture_path: Path | None = None
) -> dict[str, Any]:
    """Return a portable summary for a completed RTL-SDR NPU run."""
    report_path = report_path.resolve()
    header, rows, footer = _load_completed_report(report_path)
    if header.get("source") != "rtl":
        raise ValueError("this summary tool only accepts RTL-SDR source reports")
    # A stopped or failed acquisition is still useful for diagnosis, but it is
    # not a completed run and must never be presented as a QC acceptance record.
    if footer.get("completion_status") != "completed":
        raise ValueError("RTL-SDR QC requires a normally completed run")
    _validate_npu_backends(header, rows, footer)
    _validate_completed_metadata(header, rows)
    _validate_completed_counts(rows, footer)
    resolved_capture = _resolve_capture_path(header, report_path, capture_path)
    capture_qc = cu8_channel_statistics(resolved_capture)
    expected_capture_bytes, expected_capture_sha256 = _capture_binding(footer)
    if expected_capture_bytes != capture_qc["capture_bytes"]:
        raise ValueError("CU8 capture byte count does not match the JSONL footer")
    if expected_capture_sha256 != capture_qc["capture_sha256"]:
        raise ValueError("CU8 capture SHA256 does not match the JSONL footer")
    _validate_capture_provenance(
        header,
        rows,
        capture_complex_samples=int(capture_qc["complex_samples"]),
    )
    _validate_completed_capture_coverage(
        header,
        rows,
        footer,
        capture_complex_samples=int(capture_qc["complex_samples"]),
    )
    timing = {
        field: summary
        for field in TIMING_FIELDS
        if (summary := _timing_summary(rows, field)) is not None
    }
    labels: dict[str, int] = {}
    for index, row in enumerate(rows):
        detections = row.get("detections", [])
        if not isinstance(detections, list):
            raise ValueError(f"inference_batch[{index}].detections must be a list")
        for detection in detections:
            if not isinstance(detection, dict):
                raise ValueError(f"inference_batch[{index}] detection must be an object")
            label = detection.get("label")
            if not isinstance(label, str) or not label:
                raise ValueError(f"inference_batch[{index}] detection label must be non-empty")
            labels[label] = labels.get(label, 0) + 1
    return {
        "schema_version": 1,
        "scope": "byte_level_capture_qc_and_recorded_host_timings_not_rf_accuracy",
        "report_path": str(report_path),
        "capture_path": str(resolved_capture),
        "source": header.get("source"),
        "rf_input_context": header.get("rf_input_context"),
        "center_frequency_hz": header.get("center_frequency_hz"),
        "sample_rate_hz": header.get("sample_rate_hz"),
        "inference_backend": footer.get("inference_backend"),
        "batches": len(rows),
        "timing": timing,
        "detection_label_counts": labels,
        "capture_qc": capture_qc,
    }


def main() -> int:
    args = parse_args()
    if args.capture_only:
        if args.inference_jsonl is not None or args.capture_cu8 is None:
            raise ValueError("--capture-only requires --capture-cu8 and no --inference-jsonl")
        capture_path = args.capture_cu8.resolve()
        if not capture_path.is_file():
            raise FileNotFoundError(f"CU8 capture does not exist: {capture_path}")
        summary = {
            "schema_version": 1,
            "scope": "byte_level_capture_qc_not_rf_accuracy",
            "capture_path": str(capture_path),
            "capture_qc": cu8_channel_statistics(capture_path),
        }
        protected_paths = (capture_path,)
    else:
        if args.inference_jsonl is None:
            raise ValueError("--inference-jsonl is required unless --capture-only is used")
        summary = summarize_rtl_sdr_run(args.inference_jsonl, capture_path=args.capture_cu8)
        protected_paths = (Path(summary["report_path"]), Path(summary["capture_path"]))
    output_path = (
        None if args.output is None else _safe_output_path(args.output, protected_paths)
    )
    serialized = json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=True, allow_nan=False)
    print(serialized)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("x", encoding="utf-8") as handle:
            handle.write(serialized + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
