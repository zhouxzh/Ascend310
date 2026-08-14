"""Headless entry point for the reviewed RTL-SDR/NPU inference service.

The implementation lives in :mod:`rtl_sdr_service` so the Qt dashboard and
this command use exactly the same manifest checks, capture source, NPU runner,
post-processing, and JSONL artifact format.  This module intentionally keeps
the historical helper names as compatibility imports for scripts and tests.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import threading
from typing import Any

import numpy as np

from .model.inference_manifest import (
    InferenceModelManifest,
    select_default_manifest,
)
from .processing import LatestQueue
from .rtl_sdr_service import (
    CapturedIqBatch,
    Cu8ReplaySource,
    EXPECTED_NPU_BACKEND,
    IqSourceContext,
    ProducerStats,
    RF_INPUT_CONTEXTS,
    RtlSdrRunConfig,
    RtlSdrService,
    SyntheticIqSource,
    create_iq_source,
    discover_accepted_models,
    estimate_capture_bytes,
    percentile_ms,
    prepare_model_input,
    required_complex_samples,
    resolve_sample_rate,
    summarize_pipeline_realtime,
    validate_live_budget,
    validate_model_output,
)

# Re-export these names as part of the old module's API.  In particular,
# ``attach_pipeline_realtime_evidence`` and existing pytest fixtures import
# helpers from this path.
__all__ = [
    "CapturedIqBatch",
    "ProducerStats",
    "RF_INPUT_CONTEXTS",
    "RtlSdrRunConfig",
    "RtlSdrService",
    "discover_accepted_models",
    "estimate_capture_bytes",
    "percentile_ms",
    "prepare_model_input",
    "required_complex_samples",
    "resolve_sample_rate",
    "summarize_pipeline_realtime",
    "validate_live_budget",
    "validate_model_output",
]


def _produce_cu8(
    args: argparse.Namespace,
    target: LatestQueue,
    done: threading.Event,
    stop: threading.Event,
    errors: list[str],
    required_samples: int,
    sample_rate_hz: float,
    stats: ProducerStats | None = None,
) -> None:
    """Compatibility wrapper for the historical CU8 producer test helper."""
    config = RtlSdrRunConfig(
        source="cu8",
        input_cu8=Path(args.input_cu8),
        sample_rate_hz=sample_rate_hz,
        duration_seconds=float(args.duration_seconds),
        max_batches=int(args.max_batches),
    )
    # The service source is deliberately reused here; this keeps replay timing
    # and fixed-batch decoding identical to the threaded production path.
    context = IqSourceContext(
        config=config,
        required_samples=required_samples,
        sample_rate_hz=sample_rate_hz,
        raw_path=Path("capture.cu8"),
        stderr_path=Path("rtl_sdr.log"),
    )
    try:
        for captured in Cu8ReplaySource(context).iter_batches(stop):
            target.put_latest(captured)
            if stats is not None:
                stats.produced_batches += 1
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        done.set()


def _produce_synthetic(
    args: argparse.Namespace,
    target: LatestQueue,
    done: threading.Event,
    stop: threading.Event,
    errors: list[str],
    required_samples: int,
    sample_rate_hz: float,
    stats: ProducerStats | None = None,
) -> None:
    """Compatibility wrapper for the historical synthetic producer helper."""
    config = RtlSdrRunConfig(
        source="synthetic",
        sample_rate_hz=sample_rate_hz,
        duration_seconds=float(args.duration_seconds),
        max_batches=int(args.max_batches),
    )
    context = IqSourceContext(
        config=config,
        required_samples=required_samples,
        sample_rate_hz=sample_rate_hz,
        raw_path=Path("capture.cu8"),
        stderr_path=Path("rtl_sdr.log"),
    )
    try:
        for captured in SyntheticIqSource(context).iter_batches(stop):
            target.put_latest(captured)
            if stats is not None:
                stats.produced_batches += 1
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        done.set()


def _produce_rtl(
    args: argparse.Namespace,
    target: LatestQueue,
    done: threading.Event,
    stop: threading.Event,
    errors: list[str],
    required_samples: int,
    sample_rate_hz: float,
    raw_path: Path,
    stderr_path: Path,
    stats: ProducerStats | None = None,
) -> None:
    """Compatibility wrapper for callers that used the old RTL producer."""
    if int(args.max_batches) != 0:
        raise ValueError(
            "_produce_rtl no longer accepts max_batches; live RTL-SDR runs use a complete-window capture plan"
        )
    config = RtlSdrRunConfig(
        source="rtl",
        sample_rate_hz=sample_rate_hz,
        center_frequency_hz=float(args.center_frequency),
        device=str(args.device),
        gain_db=args.gain_db,
        ppm_error=int(args.ppm_error),
        rf_input_context=getattr(args, "rf_input_context", "unknown"),
        duration_seconds=float(args.duration_seconds),
        max_batches=0,
        capture_timeout_seconds=float(args.capture_timeout_seconds),
    )
    context = IqSourceContext(
        config=config,
        required_samples=required_samples,
        sample_rate_hz=sample_rate_hz,
        raw_path=Path(raw_path),
        stderr_path=Path(stderr_path),
    )
    try:
        for captured in create_iq_source(context).iter_batches(stop):
            target.put_latest(captured)
            if stats is not None:
                stats.produced_batches += 1
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        done.set()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("rtl", "cu8", "synthetic"), default="rtl")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument(
        "--models-dir", type=Path, default=Path("models/generated/inference")
    )
    parser.add_argument("--input-cu8", type=Path)
    parser.add_argument("--sample-rate", type=float)
    parser.add_argument("--center-frequency", type=float, default=100_000_000.0)
    parser.add_argument("--device", default="0")
    parser.add_argument("--gain-db", type=float)
    parser.add_argument("--ppm-error", type=int, default=0)
    parser.add_argument("--rf-input-context", choices=RF_INPUT_CONTEXTS, default="unknown")
    parser.add_argument("--duration-seconds", type=float, default=10.0)
    parser.add_argument("--max-batches", type=int, default=0)
    parser.add_argument("--queue-capacity", type=int, default=4)
    parser.add_argument("--capture-timeout-seconds", type=float, default=5.0)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--confidence", type=float, default=0.25)
    parser.add_argument("--iou", type=float, default=0.7)
    parser.add_argument("--max-detections", type=int, default=300)
    parser.add_argument(
        "--output-dir", type=Path, default=Path("data/rtl_sdr_npu_inference")
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = RtlSdrRunConfig(
        source=args.source,
        manifest_path=args.manifest,
        models_dir=args.models_dir,
        input_cu8=args.input_cu8,
        sample_rate_hz=args.sample_rate,
        center_frequency_hz=args.center_frequency,
        device=args.device,
        gain_db=args.gain_db,
        ppm_error=args.ppm_error,
        rf_input_context=args.rf_input_context,
        duration_seconds=args.duration_seconds,
        max_batches=args.max_batches,
        queue_capacity=args.queue_capacity,
        capture_timeout_seconds=args.capture_timeout_seconds,
        top_k=args.top_k,
        confidence=args.confidence,
        iou=args.iou,
        max_detections=args.max_detections,
        output_dir=args.output_dir,
    )
    service = RtlSdrService()
    try:
        service.start(config)
        service.wait_stopped()
        snapshot = service.snapshot()
        if snapshot.state == "failed":
            raise RuntimeError(snapshot.error or "SDR inference service failed")
        if snapshot.completed_batches <= 0:
            raise RuntimeError("the SDR inference run completed without an inference batch")
        if snapshot.completion_status != "completed":
            raise RuntimeError(
                "the SDR inference run did not finish normally and is not eligible for QC evidence"
            )
        if (
            not snapshot.npu_status.ready
            or snapshot.npu_status.backend != EXPECTED_NPU_BACKEND
        ):
            raise RuntimeError("the SDR inference run did not retain a verified Ascend 310B NPU backend")
        print(
            f"SDR NPU inference completed; model={snapshot.model_id}; "
            f"backend={snapshot.npu_status.backend}; batches={snapshot.completed_batches}; "
            f"dropped={snapshot.queue_dropped_batches}; results={snapshot.result_path}"
        )
        return 0
    finally:
        service.close()


if __name__ == "__main__":
    raise SystemExit(main())
