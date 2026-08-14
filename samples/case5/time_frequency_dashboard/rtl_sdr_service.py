"""Threaded, NPU-only RTL-SDR inference service shared by CLI and Qt clients.

The module deliberately has no Qt dependency.  It owns the live capture
subprocess, bounded producer/inference pipeline, OM invocation, and the
append-only CU8/JSONL run artifacts.  A caller may inspect immutable snapshots
and the latest display frame without ever treating a CPU result as NPU output.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import queue
import select
import shutil
import subprocess
import threading
import time
from types import MappingProxyType
from typing import Any, Mapping, Protocol

import numpy as np

from .inference_processing import (
    complex_to_model_iq,
    decode_yolo_detections,
    generate_qpsk_samples,
    softmax_topk,
)
from .model.inference_manifest import (
    InferenceModelManifest,
    ensure_live_deployment_ready,
    load_inference_manifest,
    select_default_manifest,
    sha256_file,
    verify_artifact_hashes,
)
from .npu import AscendOmRunner, NpuStatus
from .processing import LatestQueue
from .rtl_sdr_npu_demo import decode_rtl_sdr_cu8
from .spectrogram import FftwSpectrogram


RF_INPUT_CONTEXTS = ("unknown", "disconnected", "antenna_connected", "lab_cabled")
SOURCE_NAMES = ("rtl", "cu8", "synthetic")
SERVICE_STATES = ("idle", "starting", "running", "stopping", "failed")
EXPECTED_NPU_BACKEND = "NPU (Ascend 310B)"


def _nonnegative_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _positive_int(value: object, *, field: str) -> int:
    result = _nonnegative_int(value, field=field)
    if result <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return result


def _finite_nonnegative_float(value: object, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite non-negative number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite non-negative number") from exc
    if not math.isfinite(result) or result < 0.0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return result


def _finite_float(value: object, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def _integral_hz(value: object, *, field: str) -> float:
    """Validate a hardware tuning value and retain one canonical Hz value."""
    result = _finite_nonnegative_float(value, field=field)
    if result <= 0.0 or not result.is_integer():
        raise ValueError(f"{field} must be a positive whole number of hertz")
    return result


def _freeze_display_value(value: Any) -> Any:
    """Copy nested display metadata so a caller cannot mutate a snapshot in place."""
    if isinstance(value, Mapping):
        return MappingProxyType(
            {str(key): _freeze_display_value(item) for key, item in value.items()}
        )
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_display_value(item) for item in value)
    if isinstance(value, np.generic):
        return value.item()
    return value


@dataclass(frozen=True)
class CapturedIqBatch:
    """A complete fixed-size complex-IQ inference batch with capture timings."""

    sequence: int
    source_sample_offset: int
    host_receive_ns: int
    capture_started_monotonic_ns: int
    raw_complete_monotonic_ns: int
    ready_for_queue_monotonic_ns: int
    archive_write_ms: float
    decode_ms: float
    samples: np.ndarray

    def __post_init__(self) -> None:
        _nonnegative_int(self.sequence, field="captured I/Q sequence")
        _nonnegative_int(self.source_sample_offset, field="captured I/Q source sample offset")
        _nonnegative_int(self.host_receive_ns, field="captured I/Q host receive timestamp")
        capture_started = _nonnegative_int(
            self.capture_started_monotonic_ns,
            field="captured I/Q capture-start timestamp",
        )
        raw_complete = _nonnegative_int(
            self.raw_complete_monotonic_ns,
            field="captured I/Q raw-complete timestamp",
        )
        ready = _nonnegative_int(
            self.ready_for_queue_monotonic_ns,
            field="captured I/Q queue-ready timestamp",
        )
        if raw_complete < capture_started or ready < raw_complete:
            raise ValueError("captured I/Q timestamps must be ordered from capture through queueing")
        _finite_nonnegative_float(self.archive_write_ms, field="captured I/Q archive write time")
        _finite_nonnegative_float(self.decode_ms, field="captured I/Q decode time")
        values = np.array(self.samples, dtype=np.complex64, order="C", copy=True)
        if values.ndim != 1 or not values.size:
            raise ValueError("captured IQ batch samples must be a non-empty 1-D array")
        if not np.all(np.isfinite(values)):
            raise ValueError("captured IQ batch samples must be finite")
        values.setflags(write=False)
        object.__setattr__(self, "samples", values)


@dataclass
class ProducerStats:
    produced_batches: int = 0


@dataclass(frozen=True)
class RtlSdrRunConfig:
    """All operator-selected values required by one SDR inference run."""

    source: str = "rtl"
    manifest_path: Path | None = None
    models_dir: Path = Path("models/generated/inference")
    input_cu8: Path | None = None
    sample_rate_hz: float | None = None
    center_frequency_hz: float = 100_000_000.0
    device: str = "0"
    gain_db: float | None = None
    ppm_error: int = 0
    rf_input_context: str = "unknown"
    duration_seconds: float = 10.0
    max_batches: int = 0
    queue_capacity: int = 4
    capture_timeout_seconds: float = 5.0
    top_k: int = 5
    confidence: float = 0.25
    iou: float = 0.7
    max_detections: int = 300
    output_dir: Path = Path("data/rtl_sdr_npu_inference")

    def __post_init__(self) -> None:
        if self.source not in SOURCE_NAMES:
            raise ValueError(f"unsupported SDR source: {self.source}")
        if self.source == "rtl" and self.max_batches != 0:
            raise ValueError(
                "max_batches is a developer-only limit and cannot truncate a live RTL-SDR run"
            )
        if self.source == "cu8" and self.input_cu8 is None:
            raise ValueError("input_cu8 is required when source is cu8")
        if self.rf_input_context not in RF_INPUT_CONTEXTS:
            raise ValueError(f"unsupported RF input context: {self.rf_input_context}")
        duration_seconds = _finite_nonnegative_float(
            self.duration_seconds, field="duration"
        )
        capture_timeout_seconds = _finite_nonnegative_float(
            self.capture_timeout_seconds, field="capture timeout"
        )
        if duration_seconds <= 0.0 or capture_timeout_seconds <= 0.0:
            raise ValueError("duration and capture timeout must be positive")
        object.__setattr__(self, "duration_seconds", duration_seconds)
        object.__setattr__(self, "capture_timeout_seconds", capture_timeout_seconds)
        _nonnegative_int(self.max_batches, field="max batches")
        _positive_int(self.queue_capacity, field="queue capacity")
        _positive_int(self.top_k, field="top_k")
        _positive_int(self.max_detections, field="max detections")
        if isinstance(self.ppm_error, bool) or not isinstance(self.ppm_error, int):
            raise ValueError("PPM error must be an integer")
        if not str(self.device).strip():
            raise ValueError("RTL-SDR device must be non-empty")
        confidence = _finite_nonnegative_float(self.confidence, field="confidence")
        iou = _finite_nonnegative_float(self.iou, field="IoU")
        if confidence > 1.0 or iou > 1.0:
            raise ValueError("confidence and IoU thresholds must be between zero and one")
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "iou", iou)
        if self.sample_rate_hz is not None:
            sample_rate_hz = _integral_hz(self.sample_rate_hz, field="sample rate")
            object.__setattr__(self, "sample_rate_hz", sample_rate_hz)
        center_frequency_hz = _integral_hz(
            self.center_frequency_hz, field="center frequency"
        )
        object.__setattr__(self, "center_frequency_hz", center_frequency_hz)
        if self.gain_db is not None:
            gain_db = _finite_float(self.gain_db, field="gain")
            object.__setattr__(self, "gain_db", gain_db)
        for field_name in ("manifest_path", "models_dir", "input_cu8", "output_dir"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, Path(value))


@dataclass(frozen=True)
class RtlSdrModelOption:
    """A UI-safe description of a manifest that passed local admission checks."""

    manifest_path: Path
    model_id: str
    task: str
    input_shape: tuple[int, ...]
    output_shape: tuple[int, ...] | None
    sample_rate_hz: float | None
    class_names: tuple[str, ...]
    om_path: Path
    npu_p95_ms: float
    npu_speedup_over_cpu: float


@dataclass(frozen=True)
class RtlSdrDisplayFrame:
    """The latest host-owned rendering data for one successfully inferred batch."""

    generation: int
    sequence: int
    source_sample_offset: int
    samples: np.ndarray
    model_input: np.ndarray
    model_iq: np.ndarray | None
    spectrogram_image: np.ndarray | None
    top_k: tuple[Mapping[str, Any], ...]
    detections: tuple[Mapping[str, Any], ...]
    sample_rate_hz: float
    center_frequency_hz: float | None
    batch_duration_ms: float
    completed_monotonic_ns: int
    source_sample_count: int | None = None

    def __post_init__(self) -> None:
        _nonnegative_int(self.generation, field="display generation")
        _nonnegative_int(self.sequence, field="display sequence")
        _nonnegative_int(self.source_sample_offset, field="display source sample offset")
        _nonnegative_int(self.completed_monotonic_ns, field="display completion timestamp")
        sample_rate_hz = _finite_nonnegative_float(
            self.sample_rate_hz, field="display sample rate"
        )
        if sample_rate_hz <= 0.0:
            raise ValueError("display sample rate must be finite and positive")
        object.__setattr__(self, "sample_rate_hz", sample_rate_hz)
        if self.center_frequency_hz is not None:
            center_frequency_hz = _finite_nonnegative_float(
                self.center_frequency_hz, field="display center frequency"
            )
            if center_frequency_hz <= 0.0:
                raise ValueError("display center frequency must be finite and positive when available")
            object.__setattr__(self, "center_frequency_hz", center_frequency_hz)
        batch_duration_ms = _finite_nonnegative_float(
            self.batch_duration_ms, field="display batch duration"
        )
        if batch_duration_ms <= 0.0:
            raise ValueError("display batch duration must be finite and positive")
        object.__setattr__(self, "batch_duration_ms", batch_duration_ms)
        samples = np.array(self.samples, dtype=np.complex64, order="C", copy=True)
        if samples.ndim != 1 or not samples.size or not np.all(np.isfinite(samples)):
            raise ValueError("display I/Q samples must be finite and non-empty")
        samples.setflags(write=False)
        object.__setattr__(self, "samples", samples)
        source_sample_count = (
            samples.size
            if self.source_sample_count is None
            else _positive_int(self.source_sample_count, field="display source sample count")
        )
        if source_sample_count < samples.size:
            raise ValueError("display source sample count cannot be smaller than drawn samples")
        object.__setattr__(self, "source_sample_count", source_sample_count)
        for name in ("model_input", "model_iq", "spectrogram_image"):
            value = getattr(self, name)
            if value is None:
                continue
            source = np.asarray(value)
            if (
                not np.issubdtype(source.dtype, np.number)
                or np.issubdtype(source.dtype, np.complexfloating)
                or source.dtype == np.bool_
            ):
                raise ValueError(f"display {name} must contain real numeric values")
            array = np.array(source, dtype=np.float32, order="C", copy=True)
            if not array.size or not np.all(np.isfinite(array)):
                raise ValueError(f"display {name} must be non-empty and contain no NaN or Inf")
            array.setflags(write=False)
            object.__setattr__(self, name, array)
        if any(not isinstance(item, Mapping) for item in self.top_k):
            raise ValueError("display top_k entries must be mappings")
        if any(not isinstance(item, Mapping) for item in self.detections):
            raise ValueError("display detection entries must be mappings")
        object.__setattr__(
            self,
            "top_k",
            tuple(_freeze_display_value(item) for item in self.top_k),
        )
        object.__setattr__(
            self,
            "detections",
            tuple(_freeze_display_value(item) for item in self.detections),
        )


@dataclass(frozen=True)
class RtlSdrSnapshot:
    """Thread-safe service status returned to the CLI and GUI."""

    state: str = "idle"
    generation: int = 0
    source: str | None = None
    model_id: str | None = None
    model_task: str | None = None
    manifest_path: Path | None = None
    sample_rate_hz: float | None = None
    center_frequency_hz: float | None = None
    batch_duration_ms: float | None = None
    npu_status: NpuStatus = field(
        default_factory=lambda: NpuStatus("NPU unavailable", False, "not initialized")
    )
    produced_batches: int = 0
    completed_batches: int = 0
    queue_dropped_batches: int = 0
    display_dropped_frames: int = 0
    archive_failed_batches: int = 0
    last_npu_inference_ms: float | None = None
    last_post_capture_pipeline_ms: float | None = None
    last_end_to_end_ms: float | None = None
    run_dir: Path | None = None
    result_path: Path | None = None
    capture_path: Path | None = None
    completion_status: str | None = None
    error: str | None = None
    message: str = "Idle; no SDR capture is running"


@dataclass(frozen=True)
class IqSourceContext:
    config: RtlSdrRunConfig
    required_samples: int
    sample_rate_hz: float
    raw_path: Path
    stderr_path: Path
    live_capture_plan: "LiveCapturePlan | None" = None


class IqBatchSource(Protocol):
    """Injectable fixed-batch IQ source used by the service producer thread."""

    source_name: str

    def iter_batches(self, stop: threading.Event) -> Iterator[CapturedIqBatch]:
        """Yield complete decoded batches until natural completion or stop."""


class CaptureArchiveError(RuntimeError):
    """A CU8 batch could not be durably appended to its run archive."""


def _append_capture_bytes(raw_output: Any, raw: bytes) -> float:
    """Write one batch or make the archive failure explicit to the service."""
    archive_started = time.perf_counter_ns()
    try:
        raw_output.write(raw)
        raw_output.flush()
    except (OSError, ValueError) as exc:
        raise CaptureArchiveError(f"could not write CU8 archive batch: {exc}") from exc
    return (time.perf_counter_ns() - archive_started) / 1_000_000.0


def percentile_ms(values: list[float], percentile: float) -> float:
    if isinstance(percentile, bool):
        raise ValueError("percentile must be a finite value between zero and one hundred")
    try:
        percentile_value = float(percentile)
    except (TypeError, ValueError) as exc:
        raise ValueError("percentile must be a finite value between zero and one hundred") from exc
    if not math.isfinite(percentile_value) or not 0.0 <= percentile_value <= 100.0:
        raise ValueError("percentile must be a finite value between zero and one hundred")
    if not values:
        return float("nan")
    array = np.asarray(values, dtype=np.float64)
    if not np.all(np.isfinite(array)) or np.any(array < 0.0):
        raise ValueError("latency samples must be finite and non-negative")
    return float(np.percentile(array, percentile_value))


def summarize_pipeline_realtime(
    *,
    produced_batches: int,
    completed_batches: int,
    dropped_batches: int,
    post_capture_pipeline_ms: list[float],
    batch_duration_ms: float,
    minimum_batches: int = 2,
) -> dict[str, Any]:
    """Report a short-run pipeline window check, separate from OM P95."""
    _nonnegative_int(produced_batches, field="produced batches")
    _nonnegative_int(completed_batches, field="completed batches")
    _nonnegative_int(dropped_batches, field="dropped batches")
    _positive_int(minimum_batches, field="minimum batches")
    _finite_nonnegative_float(batch_duration_ms, field="batch duration")
    if completed_batches > produced_batches:
        raise ValueError("completed batches cannot exceed produced batches")
    if produced_batches != completed_batches + dropped_batches:
        raise ValueError("produced batches must equal completed batches plus dropped batches")
    if len(post_capture_pipeline_ms) != completed_batches:
        raise ValueError("post-capture latency sample count must equal completed batches")
    p50 = percentile_ms(post_capture_pipeline_ms, 50.0)
    p95 = percentile_ms(post_capture_pipeline_ms, 95.0)
    maximum = max(post_capture_pipeline_ms, default=float("nan"))
    sufficient_samples = completed_batches >= minimum_batches
    complete_delivery = produced_batches == completed_batches
    latency_meets_budget = bool(math.isfinite(maximum) and maximum <= batch_duration_ms)
    passed = bool(
        sufficient_samples
        and complete_delivery
        and dropped_batches == 0
        and latency_meets_budget
    )
    return {
        "minimum_batches": minimum_batches,
        "produced_batches": produced_batches,
        "completed_batches": completed_batches,
        "dropped_batches": dropped_batches,
        "post_capture_pipeline_p50_ms": p50,
        "post_capture_pipeline_p95_ms": p95,
        "post_capture_pipeline_max_ms": maximum,
        "batch_duration_ms": batch_duration_ms,
        "sufficient_samples": sufficient_samples,
        "complete_delivery": complete_delivery,
        "latency_meets_window_budget": latency_meets_budget,
        "pipeline_real_time_passed": passed,
    }


def required_complex_samples(manifest: InferenceModelManifest) -> int:
    if manifest.task == "iq_classification":
        return manifest.input_shape[0] * manifest.input_shape[2]
    return manifest.input_shape[0] * manifest.input_shape[2] * manifest.input_shape[3]


def resolve_sample_rate(
    manifest: InferenceModelManifest, requested_sample_rate_hz: float | None
) -> float:
    """Resolve a live sample rate without invalidating a model's admission budget."""
    declared = manifest.sample_rate_hz
    if requested_sample_rate_hz is not None:
        requested_sample_rate_hz = _integral_hz(
            requested_sample_rate_hz, field="sample rate"
        )
    if (
        declared is not None
        and requested_sample_rate_hz is not None
        and not np.isclose(requested_sample_rate_hz, declared, rtol=0.0, atol=1.0e-6)
    ):
        raise ValueError(
            f"--sample-rate {requested_sample_rate_hz:g} does not match the admitted "
            f"model rate {declared:g}; create and admit a model for the new rate"
        )
    resolved = requested_sample_rate_hz or declared or 2_048_000.0
    return _integral_hz(resolved, field="sample rate")


def validate_live_budget(
    manifest: InferenceModelManifest, *, sample_rate_hz: float
) -> float:
    """Validate the recorded NPU P95 against the live fixed input window."""
    sample_rate_hz = _finite_nonnegative_float(sample_rate_hz, field="sample rate")
    if sample_rate_hz <= 0.0:
        raise ValueError("sample rate must be finite and positive")
    batch_duration_ms = 1_000.0 * required_complex_samples(manifest) / sample_rate_hz
    recorded_p95 = _finite_nonnegative_float(
        manifest.admission.get("npu_p95_ms", float("inf")),
        field="admitted NPU P95",
    )
    if not np.isfinite(recorded_p95) or recorded_p95 > batch_duration_ms:
        raise ValueError(
            f"admitted NPU P95 {recorded_p95:g} ms exceeds this live batch budget "
            f"{batch_duration_ms:g} ms"
        )
    return batch_duration_ms


def _require_real_npu_status(status: NpuStatus) -> None:
    """Reject a test/dummy runner that could otherwise be recorded as a live NPU run."""
    if not status.ready:
        raise RuntimeError(status.message)
    if status.backend != EXPECTED_NPU_BACKEND:
        raise RuntimeError(
            f"OM runner reported {status.backend!r}, not the required {EXPECTED_NPU_BACKEND!r}"
        )


def _require_npu_latency(status: NpuStatus) -> float:
    """Return a finite NPU boundary measurement for an accepted batch row."""
    if status.backend != EXPECTED_NPU_BACKEND or not status.ready:
        raise RuntimeError("OM runner stopped reporting the required Ascend 310B NPU backend")
    latency = _finite_nonnegative_float(status.last_latency_ms, field="NPU inference latency")
    return latency


def estimate_capture_bytes(sample_rate_hz: float, duration_seconds: float) -> int:
    """Return the nominal CU8 byte count for an RTL-SDR timed recording."""
    sample_rate = _finite_nonnegative_float(sample_rate_hz, field="sample rate")
    duration = _finite_nonnegative_float(duration_seconds, field="duration")
    if sample_rate <= 0.0 or duration <= 0.0:
        raise ValueError("sample rate and duration must be positive")
    estimated = 2.0 * sample_rate * duration
    if not math.isfinite(estimated) or estimated > float(np.iinfo(np.intp).max):
        raise ValueError("capture size estimate is too large")
    return int(math.ceil(estimated))


@dataclass(frozen=True)
class LiveCapturePlan:
    """A complete-window capture plan for a live RTL-SDR recording.

    The requested duration is an operator-facing lower bound.  The receiver
    records the minimum whole number of fixed model windows that covers it,
    rather than silently dropping a partial trailing window.
    """

    requested_duration_seconds: float
    requested_capture_samples: int
    planned_capture_samples: int
    planned_capture_batches: int
    planned_capture_duration_seconds: float


def plan_live_capture(
    sample_rate_hz: float,
    duration_seconds: float,
    required_samples: int,
) -> LiveCapturePlan:
    """Plan a live capture that contains only complete fixed model windows."""
    sample_rate = _integral_hz(sample_rate_hz, field="sample rate")
    duration = _finite_nonnegative_float(duration_seconds, field="duration")
    window_samples = _positive_int(required_samples, field="required model samples")
    if duration <= 0.0:
        raise ValueError("duration must be positive")
    requested_exact = sample_rate * duration
    maximum_samples = int(np.iinfo(np.intp).max)
    if not math.isfinite(requested_exact) or requested_exact > maximum_samples:
        raise ValueError("live RTL-SDR capture length is too large")
    # Decimal CLI durations can land infinitesimally above an integer product
    # in binary floating point.  Treat that representation noise as the exact
    # requested sample count while still rounding genuine fractional samples up.
    nearest = round(requested_exact)
    requested_samples = max(
        1,
        int(nearest)
        if math.isclose(requested_exact, nearest, rel_tol=0.0, abs_tol=1.0e-6)
        else int(math.ceil(requested_exact)),
    )
    planned_batches = int(math.ceil(requested_samples / window_samples))
    planned_samples = planned_batches * window_samples
    if planned_samples > maximum_samples:
        raise ValueError("live RTL-SDR capture length is too large")
    return LiveCapturePlan(
        requested_duration_seconds=duration,
        requested_capture_samples=requested_samples,
        planned_capture_samples=planned_samples,
        planned_capture_batches=planned_batches,
        planned_capture_duration_seconds=planned_samples / sample_rate,
    )


def estimate_live_capture_bytes(
    sample_rate_hz: float,
    duration_seconds: float,
    required_samples: int,
) -> int:
    """Return the CU8 archive capacity for a complete-window live recording."""
    plan = plan_live_capture(sample_rate_hz, duration_seconds, required_samples)
    return plan.planned_capture_samples * 2


def discover_accepted_models(models_dir: Path) -> tuple[RtlSdrModelOption, ...]:
    """Return only accepted, hash-verified, live-eligible deployment manifests.

    This intentionally does not import ``aclruntime`` or initialize an OM.
    A selected model is revalidated by :meth:`RtlSdrService.start` before the
    RTL-SDR process is allowed to open the device.
    """
    options: list[RtlSdrModelOption] = []
    for path in sorted(Path(models_dir).rglob("*.manifest.json")):
        try:
            manifest = load_inference_manifest(path, require_accepted=True)
            ensure_live_deployment_ready(manifest)
            verify_artifact_hashes(manifest)
            validate_live_budget(
                manifest,
                sample_rate_hz=manifest.sample_rate_hz or 2_048_000.0,
            )
            npu_p95 = float(manifest.admission.get("npu_p95_ms", float("inf")))
            speedup = float(manifest.admission.get("npu_speedup_over_cpu", 0.0))
            if not math.isfinite(npu_p95) or not math.isfinite(speedup):
                continue
        except (OSError, ValueError, RuntimeError, TypeError, OverflowError):
            continue
        options.append(
            RtlSdrModelOption(
                manifest_path=path,
                model_id=manifest.model_id,
                task=manifest.task,
                input_shape=manifest.input_shape,
                output_shape=manifest.output_shape,
                sample_rate_hz=manifest.sample_rate_hz,
                class_names=manifest.class_names,
                om_path=manifest.om_path,
                npu_p95_ms=npu_p95,
                npu_speedup_over_cpu=speedup,
            )
        )
    # The default UI model follows the documented fixed decision rule: largest
    # admitted NPU/CPU throughput gain first, then the lower NPU P95, then a
    # deterministic path tie-breaker.
    options.sort(
        key=lambda option: (
            -option.npu_speedup_over_cpu,
            option.npu_p95_ms,
            str(option.manifest_path),
        )
    )
    return tuple(options)


def prepare_model_input(
    manifest: InferenceModelManifest,
    samples: np.ndarray,
    fftw: FftwSpectrogram | None,
) -> np.ndarray:
    if manifest.task == "iq_classification":
        return complex_to_model_iq(
            samples,
            batch_size=manifest.input_shape[0],
            window_samples=manifest.input_shape[2],
            normalization=manifest.normalization,
        )
    if fftw is None:
        raise RuntimeError("FFTW spectrogram plan is unavailable")
    return fftw.compute(samples)


def validate_model_output(
    manifest: InferenceModelManifest, model_output: np.ndarray
) -> np.ndarray:
    """Reject malformed or non-finite NPU output before CPU decoding."""
    source = np.asarray(model_output)
    if (
        not np.issubdtype(source.dtype, np.number)
        or np.issubdtype(source.dtype, np.complexfloating)
        or source.dtype == np.bool_
    ):
        raise RuntimeError("OM output must contain real numeric values")
    try:
        output = np.ascontiguousarray(source, dtype=np.float32)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("OM output must contain real numeric values") from exc
    if not output.size:
        raise RuntimeError("OM returned an empty output tensor")
    if manifest.output_shape is not None and tuple(output.shape) != manifest.output_shape:
        raise RuntimeError(
            f"OM output shape {output.shape} does not match {manifest.output_shape}"
        )
    if not np.all(np.isfinite(output)):
        raise RuntimeError("OM returned NaN or Inf; live NPU inference is stopped")
    return output


def _validate_om_contract(
    manifest: InferenceModelManifest,
    status: NpuStatus,
    *,
    require_declared_shapes: bool,
) -> None:
    """Fail before live capture when an OM does not match its admitted manifest.

    ``aclruntime`` exposes fixed IO shapes at session construction.  A live
    receiver must not be opened until those shapes agree with the reviewed
    ONNX/OM manifest.  Test-only replay and synthetic runners may omit shape
    metadata, but any shape they do expose is still checked exactly.
    """

    def checked_shape(value: object, label: str) -> tuple[int, ...] | None:
        if value is None:
            if require_declared_shapes:
                raise RuntimeError(f"OM {label} shape is unavailable during live RTL-SDR preflight")
            return None
        try:
            items = tuple(value)  # type: ignore[arg-type]
        except TypeError as exc:
            raise RuntimeError(f"OM {label} shape is invalid: {value!r}") from exc
        if any(isinstance(item, bool) or not isinstance(item, (int, np.integer)) for item in items):
            raise RuntimeError(f"OM {label} shape is invalid: {value!r}")
        shape = tuple(int(item) for item in items)
        if not shape or any(item <= 0 for item in shape):
            raise RuntimeError(f"OM {label} shape is invalid: {value!r}")
        return shape

    input_shape = checked_shape(status.input_shape, "input")
    if input_shape is not None and input_shape != manifest.input_shape:
        raise RuntimeError(
            f"OM input shape {input_shape} does not match admitted manifest "
            f"{manifest.input_shape}"
        )
    if manifest.output_shape is None:
        if require_declared_shapes:
            raise RuntimeError("admitted live RTL-SDR manifests must declare a fixed OM output shape")
        return
    output_shape = checked_shape(status.output_shape, "output")
    if output_shape is not None and output_shape != manifest.output_shape:
        raise RuntimeError(
            f"OM output shape {output_shape} does not match admitted manifest "
            f"{manifest.output_shape}"
        )


def _capture_command(config: RtlSdrRunConfig, sample_rate_hz: float) -> list[str]:
    executable = shutil.which("rtl_sdr")
    if executable is None:
        raise RuntimeError("rtl_sdr is not on PATH; install the RTL-SDR tools package manually")
    command = [
        executable,
        "-f",
        str(int(config.center_frequency_hz)),
        "-s",
        str(int(sample_rate_hz)),
        "-d",
        str(config.device),
        "-p",
        str(config.ppm_error),
    ]
    if config.gain_db is not None:
        command.extend(("-g", str(config.gain_db)))
    command.append("-")
    return command


def _decode_batch(raw: bytes, required_samples: int) -> tuple[np.ndarray, float]:
    started = time.perf_counter_ns()
    decoded = decode_rtl_sdr_cu8(raw, complex_samples=required_samples)
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
    return decoded, elapsed_ms


def _encode_cu8_iq(samples: np.ndarray) -> bytes:
    """Encode synthetic normalized IQ for a private diagnostic CU8 artifact."""
    values = np.asarray(samples, dtype=np.complex64).reshape(-1)
    if not values.size or not np.all(np.isfinite(values)):
        raise ValueError("synthetic I/Q samples must be finite and non-empty")
    interleaved = np.empty((values.size, 2), dtype=np.float32)
    interleaved[:, 0] = values.real * 127.5 + 127.5
    interleaved[:, 1] = values.imag * 127.5 + 127.5
    return np.clip(np.rint(interleaved), 0.0, 255.0).astype(np.uint8).tobytes()


def _captured_from_raw(
    *,
    sequence: int,
    sample_offset: int,
    raw: bytes,
    required_samples: int,
    capture_started_monotonic_ns: int,
    raw_complete_monotonic_ns: int,
    archive_write_ms: float,
) -> CapturedIqBatch:
    samples, decode_ms = _decode_batch(raw, required_samples)
    return CapturedIqBatch(
        sequence=sequence,
        source_sample_offset=sample_offset,
        host_receive_ns=time.time_ns(),
        capture_started_monotonic_ns=capture_started_monotonic_ns,
        raw_complete_monotonic_ns=raw_complete_monotonic_ns,
        ready_for_queue_monotonic_ns=time.monotonic_ns(),
        archive_write_ms=archive_write_ms,
        decode_ms=decode_ms,
        samples=samples,
    )


@dataclass(frozen=True)
class _StreamRead:
    data: bytes | None
    deadline_reached: bool = False


def _read_exact_stream(
    stdout: Any,
    required_bytes: int,
    deadline: float,
    timeout_seconds: float,
    stop: threading.Event,
) -> _StreamRead:
    collected = bytearray()
    stalled_deadline = time.monotonic() + timeout_seconds
    while len(collected) < required_bytes and not stop.is_set():
        remaining_run = deadline - time.monotonic()
        if remaining_run <= 0:
            return _StreamRead(None, deadline_reached=True)
        wait_seconds = min(0.1, remaining_run, stalled_deadline - time.monotonic())
        if wait_seconds <= 0:
            raise TimeoutError(
                f"rtl_sdr did not provide a complete batch within {timeout_seconds:g} seconds"
            )
        ready, _, _ = select.select([stdout], [], [], wait_seconds)
        if not ready:
            if time.monotonic() >= deadline:
                return _StreamRead(None, deadline_reached=True)
            if time.monotonic() >= stalled_deadline:
                raise TimeoutError(
                    f"rtl_sdr did not provide a complete batch within {timeout_seconds:g} seconds"
                )
            continue
        chunk = os.read(stdout.fileno(), required_bytes - len(collected))
        if not chunk:
            raise RuntimeError("rtl_sdr stream ended before a complete inference batch")
        collected.extend(chunk)
        stalled_deadline = time.monotonic() + timeout_seconds
    return _StreamRead(None if stop.is_set() else bytes(collected))


class RtlSdrSubprocessSource:
    source_name = "rtl"

    def __init__(self, context: IqSourceContext) -> None:
        self.context = context

    def iter_batches(self, stop: threading.Event) -> Iterator[CapturedIqBatch]:
        context = self.context
        config = context.config
        if stop.is_set():
            return
        process: subprocess.Popen[bytes] | None = None
        command = _capture_command(config, context.sample_rate_hz)
        plan = context.live_capture_plan
        if plan is None:
            raise RuntimeError("live RTL-SDR capture requires a complete-window capture plan")
        # Startup/tuning does not consume requested capture time.  The target
        # is a number of complete fixed windows; this deadline is only a
        # bounded grace period for a stalled receiver process.
        try:
            with context.raw_path.open("wb") as raw_output, context.stderr_path.open(
                "wb"
            ) as stderr_output:
                if stop.is_set():
                    return
                process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr_output)
                if process.stdout is None:
                    raise RuntimeError("rtl_sdr stdout pipe was not created")
                deadline = (
                    time.monotonic()
                    + plan.planned_capture_duration_seconds
                    + config.capture_timeout_seconds
                )
                sequence = 0
                timed_out_mid_window = False
                while not stop.is_set() and sequence < plan.planned_capture_batches:
                    capture_started = time.monotonic_ns()
                    read = _read_exact_stream(
                        process.stdout,
                        context.required_samples * 2,
                        deadline,
                        config.capture_timeout_seconds,
                        stop,
                    )
                    if read.data is None:
                        timed_out_mid_window = read.deadline_reached
                        break
                    raw = read.data
                    raw_complete = time.monotonic_ns()
                    archive_write_ms = _append_capture_bytes(raw_output, raw)
                    yield _captured_from_raw(
                        sequence=sequence,
                        sample_offset=sequence * context.required_samples,
                        raw=raw,
                        required_samples=context.required_samples,
                        capture_started_monotonic_ns=capture_started,
                        raw_complete_monotonic_ns=raw_complete,
                        archive_write_ms=archive_write_ms,
                    )
                    sequence += 1
                if timed_out_mid_window:
                    raise RuntimeError(
                        "RTL-SDR did not provide every planned complete fixed-size inference window; "
                        "the incomplete capture is not eligible for completed-run QC"
                    )
                if not stop.is_set() and sequence != plan.planned_capture_batches:
                    raise RuntimeError(
                        "RTL-SDR ended before the planned complete-window capture was delivered"
                    )
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3.0)


class Cu8ReplaySource:
    source_name = "cu8"

    def __init__(self, context: IqSourceContext) -> None:
        self.context = context

    def iter_batches(self, stop: threading.Event) -> Iterator[CapturedIqBatch]:
        context = self.context
        config = context.config
        assert config.input_cu8 is not None
        bytes_per_batch = context.required_samples * 2
        file_size = config.input_cu8.stat().st_size
        if file_size % bytes_per_batch:
            raise ValueError(
                f"CU8 file size {file_size} is not an exact multiple of {bytes_per_batch} bytes"
            )
        total_batches = file_size // bytes_per_batch
        if config.max_batches > 0:
            total_batches = min(total_batches, config.max_batches)
        else:
            batch_seconds = context.required_samples / context.sample_rate_hz
            total_batches = min(
                total_batches, max(1, int(np.ceil(config.duration_seconds / batch_seconds)))
            )
        batch_seconds = context.required_samples / context.sample_rate_hz
        # Keep a private replay capture alongside the JSONL.  This makes a
        # developer run diagnosable without ever claiming that it was a live
        # RTL-SDR acquisition or making it eligible for RTL QC.
        with config.input_cu8.open("rb") as input_file, context.raw_path.open(
            "wb"
        ) as raw_output:
            for sequence in range(total_batches):
                if stop.is_set():
                    break
                capture_started = time.monotonic_ns()
                raw = input_file.read(bytes_per_batch)
                raw_complete = time.monotonic_ns()
                if len(raw) != bytes_per_batch:
                    raise RuntimeError("CU8 file ended before a complete inference batch")
                archive_write_ms = _append_capture_bytes(raw_output, raw)
                yield _captured_from_raw(
                    sequence=sequence,
                    sample_offset=sequence * context.required_samples,
                    raw=raw,
                    required_samples=context.required_samples,
                    capture_started_monotonic_ns=capture_started,
                    raw_complete_monotonic_ns=raw_complete,
                    archive_write_ms=archive_write_ms,
                )
                elapsed = (time.monotonic_ns() - capture_started) / 1_000_000_000.0
                if batch_seconds > elapsed:
                    stop.wait(batch_seconds - elapsed)


class SyntheticIqSource:
    source_name = "synthetic"

    def __init__(self, context: IqSourceContext) -> None:
        self.context = context

    def iter_batches(self, stop: threading.Event) -> Iterator[CapturedIqBatch]:
        context = self.context
        config = context.config
        batch_seconds = context.required_samples / context.sample_rate_hz
        total_batches = config.max_batches
        if total_batches <= 0:
            total_batches = max(1, int(np.ceil(config.duration_seconds / batch_seconds)))
        with context.raw_path.open("wb") as raw_output:
            for sequence in range(total_batches):
                if stop.is_set():
                    break
                capture_started = time.monotonic_ns()
                samples = generate_qpsk_samples(context.required_samples, seed=310_005 + sequence)
                raw_complete = time.monotonic_ns()
                raw = _encode_cu8_iq(samples)
                archive_write_ms = _append_capture_bytes(raw_output, raw)
                yield CapturedIqBatch(
                    sequence=sequence,
                    source_sample_offset=sequence * context.required_samples,
                    host_receive_ns=time.time_ns(),
                    capture_started_monotonic_ns=capture_started,
                    raw_complete_monotonic_ns=raw_complete,
                    ready_for_queue_monotonic_ns=time.monotonic_ns(),
                    archive_write_ms=archive_write_ms,
                    decode_ms=0.0,
                    samples=samples,
                )
                remaining = batch_seconds - (
                    time.monotonic_ns() - capture_started
                ) / 1_000_000_000.0
                if remaining > 0:
                    stop.wait(remaining)


def create_iq_source(context: IqSourceContext) -> IqBatchSource:
    if context.config.source == "rtl":
        return RtlSdrSubprocessSource(context)
    if context.config.source == "cu8":
        return Cu8ReplaySource(context)
    return SyntheticIqSource(context)


RunnerFactory = Callable[[Path], AscendOmRunner]
SourceFactory = Callable[[IqSourceContext], IqBatchSource]


class RtlSdrService:
    """Own a single strictly NPU-backed RTL-SDR inference run at a time."""

    def __init__(
        self,
        *,
        runner_factory: RunnerFactory = AscendOmRunner,
        source_factory: SourceFactory = create_iq_source,
        display_samples: int = 4096,
        disk_safety_bytes: int = 16 * 1024 * 1024,
    ) -> None:
        if display_samples <= 0:
            raise ValueError("display_samples must be positive")
        if disk_safety_bytes < 0:
            raise ValueError("disk_safety_bytes must be non-negative")
        self._runner_factory = runner_factory
        self._source_factory = source_factory
        self._display_samples = display_samples
        self._disk_safety_bytes = disk_safety_bytes
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # The producer owns the live rtl_sdr process or its injected source.
        # It is tracked independently because the inference worker can finish
        # its own cleanup while a blocking producer is still alive.
        self._producer_thread: threading.Thread | None = None
        self._latest_frame: RtlSdrDisplayFrame | None = None
        self._latest_frame_read_key: tuple[int, int] | None = None
        self._snapshot = RtlSdrSnapshot()

    def start(self, config: RtlSdrRunConfig) -> RtlSdrSnapshot:
        """Validate a run and start its worker; this never silently uses CPU inference."""
        with self._lock:
            if any(
                worker is not None and worker.is_alive()
                for worker in (self._thread, self._producer_thread)
            ):
                raise RuntimeError("RTL-SDR service is already running or stopping")
            prepared = self._prepare_run(config)
            generation = self._snapshot.generation + 1
            self._stop.clear()
            self._producer_thread = None
            self._latest_frame = None
            self._latest_frame_read_key = None
            self._snapshot = RtlSdrSnapshot(
                state="starting",
                generation=generation,
                source=config.source,
                model_id=prepared.manifest.model_id,
                model_task=prepared.manifest.task,
                manifest_path=prepared.manifest.manifest_path,
                sample_rate_hz=prepared.sample_rate_hz,
                # A developer replay has no receiver-calibrated RF axis, but
                # retaining the operator's nominal center keeps its display
                # contract identical to a live run.  Its source remains
                # explicit in the JSONL and it is never eligible for RTL QC.
                center_frequency_hz=config.center_frequency_hz,
                batch_duration_ms=prepared.batch_duration_ms,
                run_dir=prepared.run_dir,
                result_path=prepared.result_path,
                capture_path=prepared.raw_path,
                message="Validating the accepted model and preparing the NPU worker",
            )
            self._thread = threading.Thread(
                target=self._run,
                args=(generation, config, prepared),
                name="case5-rtl-sdr-service",
                daemon=True,
            )
            try:
                self._thread.start()
            except Exception as exc:
                self._thread = None
                self._producer_thread = None
                self._replace_snapshot(
                    state="failed",
                    completion_status="failed",
                    error=f"{type(exc).__name__}: {exc}",
                    message=f"RTL-SDR service worker could not start: {type(exc).__name__}: {exc}",
                )
                raise
            return self._snapshot

    def request_stop(self) -> RtlSdrSnapshot:
        """Request capture stop; existing queued batches are drained before cleanup."""
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                return self._snapshot
            self._stop.set()
            self._replace_snapshot(
                state="stopping",
                message="Stopping SDR capture and draining queued NPU work",
            )
            return self._snapshot

    def snapshot(self) -> RtlSdrSnapshot:
        with self._lock:
            return self._snapshot

    def latest_frame(self) -> RtlSdrDisplayFrame | None:
        """Return the latest frame without claiming it was rendered.

        The Qt workspace acknowledges a frame only after it actually renders
        it.  This keeps display-overwrite accounting separate from periodic
        polling on an inactive or paused tab.
        """
        with self._lock:
            return self._latest_frame

    def acknowledge_display_frame(self, generation: int, sequence: int) -> None:
        """Mark one frame as rendered by a display consumer."""
        with self._lock:
            frame = self._latest_frame
            if frame is not None and (frame.generation, frame.sequence) == (
                int(generation),
                int(sequence),
            ):
                self._latest_frame_read_key = (frame.generation, frame.sequence)

    def wait_stopped(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + max(float(timeout), 0.0)
        with self._lock:
            service_thread = self._thread
            producer = self._producer_thread
        for worker in (service_thread, producer):
            if worker is None or worker is threading.current_thread() or not worker.is_alive():
                continue
            remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
            worker.join(timeout=remaining)
        with self._lock:
            if self._producer_thread is producer and (
                producer is None or not producer.is_alive()
            ):
                self._producer_thread = None
            return not any(
                worker is not None and worker.is_alive()
                for worker in (self._thread, self._producer_thread)
            )

    def close(self) -> None:
        self.request_stop()
        if not self.wait_stopped(timeout=5.0):
            raise RuntimeError("RTL-SDR service did not stop within five seconds")

    def _prepare_run(self, config: RtlSdrRunConfig) -> "_PreparedRun":
        manifest_path = config.manifest_path or select_default_manifest(config.models_dir)
        manifest = load_inference_manifest(manifest_path, require_accepted=True)
        ensure_live_deployment_ready(manifest)
        verify_artifact_hashes(manifest)
        manifest_sha256 = sha256_file(manifest.manifest_path)
        onnx_sha256 = sha256_file(manifest.onnx_path)
        om_sha256 = sha256_file(manifest.om_path)
        if onnx_sha256 != manifest.onnx_sha256 or om_sha256 != manifest.om_sha256:
            raise RuntimeError("model artifact changed while SDR preflight was reading it")
        # A manifest can be atomically replaced between parsing and this
        # snapshot.  First reject a byte change, then reparse the exact
        # snapshot and verify that its artifact contract remains identical.
        if sha256_file(manifest.manifest_path) != manifest_sha256:
            raise RuntimeError("model manifest changed while SDR preflight was reading it")
        snapshot_manifest = load_inference_manifest(manifest.manifest_path, require_accepted=True)
        if (
            snapshot_manifest.to_dict() != manifest.to_dict()
            or snapshot_manifest.onnx_sha256 != onnx_sha256
            or snapshot_manifest.om_sha256 != om_sha256
        ):
            raise RuntimeError("model manifest contract changed while SDR preflight was reading it")
        if manifest.task == "spectrogram_detection" and manifest.input_shape[0] != 1:
            raise ValueError(
                "live spectrogram detection requires a batch-one OM so its image and detection "
                "coordinates remain unambiguous"
            )
        sample_rate_hz = resolve_sample_rate(manifest, config.sample_rate_hz)
        batch_duration_ms = validate_live_budget(manifest, sample_rate_hz=sample_rate_hz)
        required_samples = required_complex_samples(manifest)
        live_capture_plan = (
            plan_live_capture(sample_rate_hz, config.duration_seconds, required_samples)
            if config.source == "rtl"
            else None
        )
        run_dir = _create_run_directory(config.output_dir)
        result_path = run_dir / "inference.jsonl"
        raw_path = run_dir / "capture.cu8"
        stderr_path = run_dir / "rtl_sdr.log"
        if config.source == "rtl":
            assert live_capture_plan is not None
            required_bytes = live_capture_plan.planned_capture_samples * 2
            try:
                available = shutil.disk_usage(run_dir).free
            except OSError as exc:
                try:
                    run_dir.rmdir()
                except OSError:
                    pass
                raise RuntimeError(f"could not determine free space for RTL-SDR capture: {exc}") from exc
            if available < required_bytes + self._disk_safety_bytes:
                try:
                    run_dir.rmdir()
                except OSError:
                    pass
                raise RuntimeError(
                    f"insufficient disk space for RTL-SDR capture: need at least "
                    f"{required_bytes + self._disk_safety_bytes} bytes, have {available}"
                )
        return _PreparedRun(
            manifest=manifest,
            manifest_sha256=manifest_sha256,
            onnx_sha256=onnx_sha256,
            om_sha256=om_sha256,
            sample_rate_hz=sample_rate_hz,
            required_samples=required_samples,
            batch_duration_ms=batch_duration_ms,
            live_capture_plan=live_capture_plan,
            run_dir=run_dir,
            result_path=result_path,
            raw_path=raw_path,
            stderr_path=stderr_path,
        )

    def _run(self, generation: int, config: RtlSdrRunConfig, prepared: "_PreparedRun") -> None:
        runner: AscendOmRunner | None = None
        fftw: FftwSpectrogram | None = None
        producer: threading.Thread | None = None
        producer_done = threading.Event()
        producer_errors: list[str] = []
        producer_stats = ProducerStats()
        input_queue = LatestQueue(config.queue_capacity)
        completed_batches = 0
        post_capture_pipeline_samples_ms: list[float] = []
        process_started = time.process_time_ns()
        run_started = time.monotonic_ns()
        footer_written = False
        error: str | None = None
        completion_status = "failed"
        try:
            # Sources own their raw writes so an RTL process can archive bytes
            # before decode.  Create the artifact up front as well: an
            # operator-stopped startup still retains a diagnosable, explicitly
            # non-QC CU8 path even when no complete batch arrived.
            prepared.raw_path.touch(exist_ok=False)
            # Hashes and the parsed manifest were snapshotted during preflight.
            # Check them again at the last possible point before the OM opens;
            # otherwise a replacement between UI selection and runner creation
            # could be misreported as the reviewed deployment artifact.
            if sha256_file(prepared.manifest.manifest_path) != prepared.manifest_sha256:
                raise RuntimeError("model manifest changed after SDR preflight")
            if sha256_file(prepared.manifest.onnx_path) != prepared.onnx_sha256:
                raise RuntimeError("ONNX artifact changed after SDR preflight")
            if sha256_file(prepared.manifest.om_path) != prepared.om_sha256:
                raise RuntimeError("OM artifact changed after SDR preflight")
            runner = self._runner_factory(prepared.manifest.om_path)
            status = runner.initialize()
            self._replace_snapshot_for_generation(
                generation,
                npu_status=status,
                message=status.message,
            )
            _require_real_npu_status(status)
            _validate_om_contract(
                prepared.manifest,
                status,
                require_declared_shapes=config.source == "rtl",
            )
            if self._stop.is_set():
                completion_status = "stopped"
                return
            fftw = (
                FftwSpectrogram(prepared.manifest.input_shape[2])
                if prepared.manifest.task == "spectrogram_detection"
                else None
            )
            if self._stop.is_set():
                completion_status = "stopped"
                return
            source = self._source_factory(
                IqSourceContext(
                    config=config,
                    required_samples=prepared.required_samples,
                    sample_rate_hz=prepared.sample_rate_hz,
                    raw_path=prepared.raw_path,
                    stderr_path=prepared.stderr_path,
                    live_capture_plan=prepared.live_capture_plan,
                )
            )
            source_name = getattr(source, "source_name", None)
            if source_name != config.source:
                raise RuntimeError(
                    f"I/Q source identity {source_name!r} does not match requested source {config.source!r}"
                )
            if self._stop.is_set():
                completion_status = "stopped"
                return
            self._replace_snapshot_for_generation(
                generation,
                state="running",
                message="NPU ready; waiting for fixed-size I/Q batches",
            )
            producer = threading.Thread(
                target=self._produce,
                args=(
                    generation,
                    source,
                    input_queue,
                    producer_done,
                    producer_errors,
                    producer_stats,
                    prepared.required_samples,
                ),
                name="case5-rtl-sdr-producer",
                daemon=True,
            )
            with self._lock:
                if self._snapshot.generation != generation:
                    raise RuntimeError("RTL-SDR run generation became stale before capture start")
                self._producer_thread = producer
            with prepared.result_path.open("x", encoding="utf-8") as output:
                output.write(
                    json.dumps(
                        self._header(config, prepared, status),
                        ensure_ascii=True,
                        sort_keys=True,
                        allow_nan=False,
                    )
                    + "\n"
                )
                output.flush()
                if self._stop.is_set():
                    completion_status = "stopped"
                    return
                producer.start()
                while True:
                    try:
                        captured = input_queue.get(timeout=0.2)
                    except queue.Empty:
                        if producer_done.is_set():
                            break
                        continue
                    if not isinstance(captured, CapturedIqBatch):
                        raise TypeError("inference queue contained an unexpected item")
                    row, display = self._infer_batch(
                        generation,
                        config,
                        prepared,
                        runner,
                        fftw,
                        captured,
                        input_queue.dropped,
                    )
                    output.write(
                        json.dumps(row, ensure_ascii=True, sort_keys=True, allow_nan=False) + "\n"
                    )
                    output.flush()
                    completed_batches += 1
                    post_capture_pipeline_samples_ms.append(float(row["post_capture_pipeline_ms"]))
                    self._publish_frame(display)
                    self._replace_snapshot_for_generation(
                        generation,
                        completed_batches=completed_batches,
                        queue_dropped_batches=input_queue.dropped,
                        last_npu_inference_ms=_optional_float(row["npu_inference_ms"]),
                        last_post_capture_pipeline_ms=float(row["post_capture_pipeline_ms"]),
                        last_end_to_end_ms=float(row["end_to_end_ms"]),
                        npu_status=runner.status,
                    )
                if producer is not None:
                    producer.join(timeout=3.0)
                if producer is not None and producer.is_alive():
                    raise RuntimeError("capture producer did not stop within three seconds")
                if producer_errors:
                    raise RuntimeError("; ".join(producer_errors))
                if completed_batches == 0 and not self._stop.is_set():
                    raise RuntimeError("the SDR inference run completed without an inference batch")
                if (
                    config.source == "rtl"
                    and not self._stop.is_set()
                    and (
                        prepared.live_capture_plan is None
                        or producer_stats.produced_batches
                        != prepared.live_capture_plan.planned_capture_batches
                    )
                ):
                    expected_batches = (
                        None
                        if prepared.live_capture_plan is None
                        else prepared.live_capture_plan.planned_capture_batches
                    )
                    raise RuntimeError(
                        "live RTL-SDR capture did not deliver every planned fixed window: "
                        f"expected {expected_batches}, got {producer_stats.produced_batches}"
                    )
                completion_status = "stopped" if self._stop.is_set() else "completed"
                pipeline_realtime: dict[str, Any] | None = None
                if completed_batches:
                    pipeline_realtime = summarize_pipeline_realtime(
                        produced_batches=producer_stats.produced_batches,
                        completed_batches=completed_batches,
                        dropped_batches=input_queue.dropped,
                        post_capture_pipeline_ms=post_capture_pipeline_samples_ms,
                        batch_duration_ms=prepared.batch_duration_ms,
                    )
                wall_time_ms = (time.monotonic_ns() - run_started) / 1_000_000.0
                if pipeline_realtime is not None:
                    pipeline_realtime["observation_duration_ms"] = wall_time_ms
                    pipeline_realtime["evidence_scope"] = "short_run_pipeline_window_check"
                    pipeline_realtime["continuous_pipeline_soak_verified"] = False
                if not prepared.raw_path.is_file():
                    raise RuntimeError("SDR capture archive is missing after producer completion")
                capture_sha256 = sha256_file(prepared.raw_path)
                capture_bytes = prepared.raw_path.stat().st_size
                expected_capture_bytes = producer_stats.produced_batches * prepared.required_samples * 2
                if capture_bytes != expected_capture_bytes:
                    raise RuntimeError(
                        "SDR capture archive byte count does not match produced fixed-size batches: "
                        f"expected {expected_capture_bytes}, got {capture_bytes}"
                    )
                footer = {
                    "record_type": "run_summary",
                    "completion_status": completion_status,
                    "completed_batches": completed_batches,
                    "produced_batches": producer_stats.produced_batches,
                    "queue_dropped_batches": input_queue.dropped,
                    "display_dropped_frames": self._snapshot.display_dropped_frames,
                    "archive_failed_batches": self._snapshot.archive_failed_batches,
                    "pipeline_realtime": pipeline_realtime,
                    "wall_time_ms": wall_time_ms,
                    "process_cpu_ms": (time.process_time_ns() - process_started) / 1_000_000.0,
                    "result_path": str(prepared.result_path),
                    "inference_backend": runner.status.backend,
                    "capture_sha256": capture_sha256,
                    "capture_bytes": capture_bytes,
                    "requested_duration_seconds": config.duration_seconds,
                    "planned_capture_duration_seconds": (
                        None
                        if prepared.live_capture_plan is None
                        else prepared.live_capture_plan.planned_capture_duration_seconds
                    ),
                    "planned_capture_samples": (
                        None
                        if prepared.live_capture_plan is None
                        else prepared.live_capture_plan.planned_capture_samples
                    ),
                    "planned_capture_batches": (
                        None
                        if prepared.live_capture_plan is None
                        else prepared.live_capture_plan.planned_capture_batches
                    ),
                    "capture_plan_policy": (
                        None
                        if prepared.live_capture_plan is None
                        else "ceil_requested_duration_to_complete_fixed_windows_v1"
                    ),
                }
                output.write(
                    json.dumps(footer, ensure_ascii=True, sort_keys=True, allow_nan=False) + "\n"
                )
                output.flush()
                footer_written = True
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            self._stop.set()
            if producer is not None and producer.is_alive():
                producer.join(timeout=3.0)
        finally:
            self._stop.set()
            if producer is not None and producer.is_alive():
                producer.join(timeout=3.0)
            if producer is not None and producer.is_alive():
                liveness_error = "capture producer did not exit during RTL-SDR service cleanup"
                error = liveness_error if error is None else f"{error}; {liveness_error}"
            elif producer is not None:
                with self._lock:
                    if self._producer_thread is producer:
                        self._producer_thread = None
            if fftw is not None:
                fftw.close()
            if runner is not None:
                runner.close()
            if not prepared.result_path.is_file():
                self._write_aborted_metadata(
                    config,
                    prepared,
                    runner.status
                    if runner is not None
                    else NpuStatus("NPU unavailable", False, "not initialized"),
                )
            if prepared.result_path.is_file() and not footer_written:
                self._append_aborted_footer(
                    config,
                    prepared,
                    error=error,
                    produced_batches=producer_stats.produced_batches,
                    completed_batches=completed_batches,
                    queue_dropped_batches=input_queue.dropped,
                    process_started=process_started,
                    run_started=run_started,
                )
            self._finish_run(
                generation,
                completion_status=completion_status if error is None else "failed",
                error=error,
                produced_batches=producer_stats.produced_batches,
                completed_batches=completed_batches,
                queue_dropped_batches=input_queue.dropped,
            )

    def _write_aborted_metadata(
        self,
        config: RtlSdrRunConfig,
        prepared: "_PreparedRun",
        status: NpuStatus,
    ) -> None:
        """Record an NPU/FFTW startup failure without changing successful metadata."""
        try:
            with prepared.result_path.open("x", encoding="utf-8") as output:
                output.write(
                    json.dumps(
                        self._header(config, prepared, status),
                        ensure_ascii=True,
                        sort_keys=True,
                        allow_nan=False,
                    )
                    + "\n"
                )
        except OSError:
            # A filesystem failure is already visible in the terminal service
            # state.  Do not mask it with a cleanup exception.
            pass

    def _append_aborted_footer(
        self,
        config: RtlSdrRunConfig,
        prepared: "_PreparedRun",
        *,
        error: str | None,
        produced_batches: int,
        completed_batches: int,
        queue_dropped_batches: int,
        process_started: int,
        run_started: int,
    ) -> None:
        """Leave an explicit non-QC terminal marker after stop or failure."""
        capture_sha256 = None
        capture_bytes = None
        if prepared.raw_path.is_file():
            try:
                capture_sha256 = sha256_file(prepared.raw_path)
                capture_bytes = prepared.raw_path.stat().st_size
            except OSError:
                pass
        footer = {
            "record_type": "run_summary",
            "completion_status": "failed" if error is not None else "stopped",
            "error": error,
            "completed_batches": completed_batches,
            "produced_batches": produced_batches,
            "queue_dropped_batches": queue_dropped_batches,
            "display_dropped_frames": self.snapshot().display_dropped_frames,
            "archive_failed_batches": self.snapshot().archive_failed_batches,
            "pipeline_realtime": None,
            "wall_time_ms": (time.monotonic_ns() - run_started) / 1_000_000.0,
            "process_cpu_ms": (time.process_time_ns() - process_started) / 1_000_000.0,
            "result_path": str(prepared.result_path),
            "inference_backend": self.snapshot().npu_status.backend,
            "capture_sha256": capture_sha256,
            "capture_bytes": capture_bytes,
            "requested_duration_seconds": config.duration_seconds,
            "planned_capture_duration_seconds": (
                None
                if prepared.live_capture_plan is None
                else prepared.live_capture_plan.planned_capture_duration_seconds
            ),
            "planned_capture_samples": (
                None
                if prepared.live_capture_plan is None
                else prepared.live_capture_plan.planned_capture_samples
            ),
            "planned_capture_batches": (
                None
                if prepared.live_capture_plan is None
                else prepared.live_capture_plan.planned_capture_batches
            ),
            "capture_plan_policy": (
                None
                if prepared.live_capture_plan is None
                else "ceil_requested_duration_to_complete_fixed_windows_v1"
            ),
        }
        try:
            with prepared.result_path.open("a", encoding="utf-8") as output:
                output.write(
                    json.dumps(footer, ensure_ascii=True, sort_keys=True, allow_nan=False)
                    + "\n"
                )
        except OSError:
            # A filesystem fault is already represented by the service error.
            # Do not hide it behind another exception from cleanup.
            pass

    def _produce(
        self,
        generation: int,
        source: IqBatchSource,
        target: LatestQueue,
        done: threading.Event,
        errors: list[str],
        stats: ProducerStats,
        required_samples: int,
    ) -> None:
        expected_sequence = 0
        expected_offset = 0
        try:
            for captured in source.iter_batches(self._stop):
                if self._stop.is_set():
                    break
                if not isinstance(captured, CapturedIqBatch):
                    raise TypeError("I/Q source yielded an unexpected batch object")
                if captured.sequence != expected_sequence:
                    raise RuntimeError(
                        "I/Q source sequence must be contiguous from zero; "
                        f"expected {expected_sequence}, got {captured.sequence}"
                    )
                if captured.source_sample_offset != expected_offset:
                    raise RuntimeError(
                        "I/Q source sample offsets must be contiguous fixed windows; "
                        f"expected {expected_offset}, got {captured.source_sample_offset}"
                    )
                if captured.samples.size != required_samples:
                    raise RuntimeError(
                        "I/Q source yielded a batch with an unexpected fixed-window sample count; "
                        f"expected {required_samples}, got {captured.samples.size}"
                    )
                expected_sequence += 1
                expected_offset += captured.samples.size
                target.put_latest(captured)
                stats.produced_batches += 1
                self._replace_snapshot_for_generation(
                    generation,
                    produced_batches=stats.produced_batches,
                    queue_dropped_batches=target.dropped,
                )
        except CaptureArchiveError as exc:
            self._replace_snapshot_for_generation(
                generation,
                archive_failed_batches=self._increment_archive_failures(generation),
            )
            errors.append(f"{type(exc).__name__}: {exc}")
        except Exception as exc:
            errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            done.set()

    def _infer_batch(
        self,
        generation: int,
        config: RtlSdrRunConfig,
        prepared: "_PreparedRun",
        runner: AscendOmRunner,
        fftw: FftwSpectrogram | None,
        captured: CapturedIqBatch,
        dropped_batches: int,
    ) -> tuple[dict[str, Any], RtlSdrDisplayFrame]:
        preprocessing_started_monotonic_ns = time.monotonic_ns()
        preprocessing_started = time.perf_counter_ns()
        model_input = prepare_model_input(prepared.manifest, captured.samples, fftw)
        preprocessing_ms = (time.perf_counter_ns() - preprocessing_started) / 1_000_000.0
        if tuple(model_input.shape) != prepared.manifest.input_shape:
            raise RuntimeError(
                f"preprocessed input shape {model_input.shape} does not match "
                f"{prepared.manifest.input_shape}"
            )
        raw_model_output = runner.run(model_input)
        npu_inference_ms = _require_npu_latency(runner.status)
        postprocessing_started = time.perf_counter_ns()
        model_output = validate_model_output(prepared.manifest, raw_model_output)
        row: dict[str, Any] = {
            "record_type": "inference_batch",
            "sequence": captured.sequence,
            "source_sample_offset": captured.source_sample_offset,
            "host_receive_ns": captured.host_receive_ns,
            "input_shape": list(model_input.shape),
            "output_shape": list(model_output.shape),
            "backend": runner.status.backend,
            "capture_acquisition_ms": (
                captured.raw_complete_monotonic_ns - captured.capture_started_monotonic_ns
            )
            / 1_000_000.0,
            "archive_write_ms": captured.archive_write_ms,
            "queue_wait_ms": (
                preprocessing_started_monotonic_ns - captured.ready_for_queue_monotonic_ns
            )
            / 1_000_000.0,
            "decode_ms": captured.decode_ms,
            "preprocessing_ms": preprocessing_ms,
            "npu_inference_ms": npu_inference_ms,
            "queue_dropped_batches": dropped_batches,
        }
        top_k: tuple[dict[str, Any], ...] = ()
        detections: tuple[dict[str, Any], ...] = ()
        if prepared.manifest.task == "iq_classification":
            per_window_top_k = softmax_topk(
                model_output,
                prepared.manifest.class_names,
                config.top_k,
            )
            # A fixed OM batch can contain several consecutive IQ windows.
            # The display and its "latest window" result table intentionally
            # show the final sub-window rather than flattening unrelated
            # Top-K rows together.  JSONL keeps every sub-window explicitly.
            top_k = tuple(dict(entry) for entry in per_window_top_k[-1])
            row["top_k_by_window"] = [
                [dict(entry) for entry in window]
                for window in per_window_top_k
            ]
            row["top_k"] = [dict(entry) for entry in top_k]
        else:
            detections = tuple(
                decode_yolo_detections(
                    model_output,
                    prepared.manifest.class_names,
                    confidence_threshold=config.confidence,
                    iou_threshold=config.iou,
                    max_detections=config.max_detections,
                )
            )
            row["detections"] = [dict(entry) for entry in detections]
        completed_ns = time.monotonic_ns()
        row["postprocessing_ms"] = (
            time.perf_counter_ns() - postprocessing_started
        ) / 1_000_000.0
        row["end_to_end_ms"] = (
            completed_ns - captured.capture_started_monotonic_ns
        ) / 1_000_000.0
        row["post_capture_pipeline_ms"] = (
            completed_ns - captured.raw_complete_monotonic_ns
        ) / 1_000_000.0
        display_offset = captured.source_sample_offset
        display_sample_count = int(captured.samples.size)
        display_duration_ms = prepared.batch_duration_ms
        if prepared.manifest.task == "iq_classification":
            # Top-K is decoded from the final fixed sub-window in an OM batch.
            # Render that exact raw/preprocessed window rather than a mixture
            # of several independent classifications.
            window_samples = prepared.manifest.input_shape[2]
            display_samples_source = captured.samples[-window_samples:]
            display_offset += (prepared.manifest.input_shape[0] - 1) * window_samples
            display_sample_count = window_samples
            display_duration_ms = 1_000.0 * window_samples / prepared.sample_rate_hz
            model_iq = model_input[-1:, :, :]
        else:
            display_samples_source = captured.samples
            model_iq = None
        display_samples = _downsample_complex(display_samples_source, self._display_samples)
        spectrogram = model_input[0] if prepared.manifest.task == "spectrogram_detection" else None
        display = RtlSdrDisplayFrame(
            generation=generation,
            sequence=captured.sequence,
            source_sample_offset=display_offset,
            samples=display_samples,
            model_input=model_input,
            model_iq=model_iq,
            spectrogram_image=spectrogram,
            top_k=top_k,
            detections=detections,
            sample_rate_hz=prepared.sample_rate_hz,
            center_frequency_hz=config.center_frequency_hz,
            batch_duration_ms=display_duration_ms,
            completed_monotonic_ns=completed_ns,
            source_sample_count=display_sample_count,
        )
        return row, display

    def _header(
        self,
        config: RtlSdrRunConfig,
        prepared: "_PreparedRun",
        status: NpuStatus,
    ) -> dict[str, Any]:
        manifest = prepared.manifest
        return {
            "record_type": "run_metadata",
            "created_utc": datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
            "source": config.source,
            "input_cu8": None if config.input_cu8 is None else str(config.input_cu8),
            "capture_file": str(prepared.raw_path.resolve()),
            "center_frequency_hz": config.center_frequency_hz,
            "sample_rate_hz": prepared.sample_rate_hz,
            "batch_duration_ms": prepared.batch_duration_ms,
            "requested_duration_seconds": config.duration_seconds,
            "requested_capture_samples": (
                None
                if prepared.live_capture_plan is None
                else prepared.live_capture_plan.requested_capture_samples
            ),
            "planned_capture_duration_seconds": (
                None
                if prepared.live_capture_plan is None
                else prepared.live_capture_plan.planned_capture_duration_seconds
            ),
            "planned_capture_samples": (
                None
                if prepared.live_capture_plan is None
                else prepared.live_capture_plan.planned_capture_samples
            ),
            "planned_capture_batches": (
                None
                if prepared.live_capture_plan is None
                else prepared.live_capture_plan.planned_capture_batches
            ),
            "capture_plan_policy": (
                None
                if prepared.live_capture_plan is None
                else "ceil_requested_duration_to_complete_fixed_windows_v1"
            ),
            "live_admission_scope": (
                "accepted manifest proves model numerical agreement and the NPU window budget; "
                "pipeline window checking is an independent short-run result"
            ),
            "model_id": manifest.model_id,
            "model_task": manifest.task,
            "model_manifest": str(manifest.manifest_path),
            "model_source_revision": manifest.source_revision,
            "model_upstream_weight_sha256": manifest.upstream_weight_sha256,
            "model_input_shape": list(manifest.input_shape),
            "model_input_normalization": manifest.normalization,
            "model_sampling_convention": manifest.sampling_convention,
            "model_preprocessing": dict(manifest.preprocessing),
            "model_onnx_sha256": prepared.onnx_sha256,
            "model_om_sha256": prepared.om_sha256,
            "model_manifest_sha256": prepared.manifest_sha256,
            "model_cann_version": manifest.cann_version,
            "inference_backend": status.backend,
            "accuracy_scope": (
                "synthetic data validates repeatability; unlabeled RTL-SDR IQ validates only the live path"
            ),
            "cpu_roles": ["capture", "CU8_decode", "normalization", "FFTW_if_required", "postprocess"],
            "npu_role": "reviewed OM neural-network inference only",
            "queue_capacity": config.queue_capacity,
            "rtl_device": config.device if config.source == "rtl" else None,
            "rtl_gain_db": config.gain_db if config.source == "rtl" else None,
            "rtl_ppm_error": config.ppm_error if config.source == "rtl" else None,
            "rf_input_context": config.rf_input_context if config.source == "rtl" else None,
            "cu8_replay": "real_time_paced" if config.source == "cu8" else None,
        }

    def _publish_frame(self, frame: RtlSdrDisplayFrame) -> None:
        with self._lock:
            if frame.generation != self._snapshot.generation:
                return
            previous = self._latest_frame
            if previous is not None and self._latest_frame_read_key != (
                previous.generation,
                previous.sequence,
            ):
                self._replace_snapshot(display_dropped_frames=self._snapshot.display_dropped_frames + 1)
            self._latest_frame = frame

    def _finish_run(
        self,
        generation: int,
        *,
        completion_status: str,
        error: str | None,
        produced_batches: int,
        completed_batches: int,
        queue_dropped_batches: int,
    ) -> None:
        with self._lock:
            if self._snapshot.generation != generation:
                return
            state = "failed" if error is not None else "idle"
            if error is not None:
                message = f"SDR run failed: {error}"
            elif completion_status == "stopped":
                message = "SDR run stopped by operator; this artifact is not eligible for QC"
            else:
                message = "SDR run completed; use strict QC before treating it as evidence"
            self._replace_snapshot(
                state=state,
                completion_status=completion_status,
                error=error,
                produced_batches=produced_batches,
                completed_batches=completed_batches,
                queue_dropped_batches=queue_dropped_batches,
                message=message,
            )

    def _replace_snapshot_for_generation(self, generation: int, **changes: Any) -> None:
        with self._lock:
            if self._snapshot.generation == generation:
                self._replace_snapshot(**changes)

    def _increment_archive_failures(self, generation: int) -> int:
        """Return a generation-safe archive failure count for a producer error."""
        with self._lock:
            if self._snapshot.generation != generation:
                return self._snapshot.archive_failed_batches
            return self._snapshot.archive_failed_batches + 1

    def _replace_snapshot(self, **changes: Any) -> None:
        with self._lock:
            values = {**self._snapshot.__dict__, **changes}
            self._snapshot = RtlSdrSnapshot(**values)


@dataclass(frozen=True)
class _PreparedRun:
    manifest: InferenceModelManifest
    manifest_sha256: str
    onnx_sha256: str
    om_sha256: str
    sample_rate_hz: float
    required_samples: int
    batch_duration_ms: float
    live_capture_plan: LiveCapturePlan | None
    run_dir: Path
    result_path: Path
    raw_path: Path
    stderr_path: Path


def _create_run_directory(output_dir: Path) -> Path:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    for attempt in range(100):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        suffix = f"{time.time_ns() % 1_000_000_000:09d}" if attempt == 0 else str(attempt)
        candidate = root / f"{stamp}Z_{suffix}"
        try:
            candidate.mkdir()
            return candidate
        except FileExistsError:
            continue
    raise RuntimeError(f"could not create a unique SDR run directory under {root}")


def _downsample_complex(samples: np.ndarray, maximum: int) -> np.ndarray:
    values = np.asarray(samples, dtype=np.complex64)
    if values.size <= maximum:
        return values.copy()
    indices = np.linspace(0, values.size - 1, num=maximum, dtype=np.intp)
    return values[indices].copy()


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    return numeric if math.isfinite(numeric) else None


__all__ = [
    "CapturedIqBatch",
    "CaptureArchiveError",
    "Cu8ReplaySource",
    "IqBatchSource",
    "IqSourceContext",
    "LiveCapturePlan",
    "ProducerStats",
    "RF_INPUT_CONTEXTS",
    "EXPECTED_NPU_BACKEND",
    "RtlSdrDisplayFrame",
    "RtlSdrModelOption",
    "RtlSdrRunConfig",
    "RtlSdrService",
    "RtlSdrSnapshot",
    "RtlSdrSubprocessSource",
    "SERVICE_STATES",
    "SyntheticIqSource",
    "create_iq_source",
    "discover_accepted_models",
    "estimate_capture_bytes",
    "estimate_live_capture_bytes",
    "percentile_ms",
    "plan_live_capture",
    "prepare_model_input",
    "required_complex_samples",
    "resolve_sample_rate",
    "summarize_pipeline_realtime",
    "validate_live_budget",
    "_validate_om_contract",
    "validate_model_output",
]
