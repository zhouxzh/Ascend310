"""Verify ONNX/OM numerical agreement and benchmark a reviewed SDR model."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import time
from typing import Any, Callable, Sequence

import numpy as np
import onnxruntime as ort

from ..npu import AscendOmRunner
from .inference_manifest import InferenceModelManifest, load_inference_manifest
from .model_admission import compare_model_outputs
from .safe_json import write_new_json


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def benchmark(
    function: Callable[[np.ndarray], Sequence[np.ndarray]],
    source: np.ndarray,
    *,
    warmup: int,
    iterations: int,
) -> tuple[dict[str, float], list[np.ndarray]]:
    if isinstance(warmup, bool) or not isinstance(warmup, int) or warmup < 0:
        raise ValueError("warmup must be a non-negative integer")
    if isinstance(iterations, bool) or not isinstance(iterations, int) or iterations <= 0:
        raise ValueError("iterations must be a positive integer")
    for _index in range(warmup):
        function(source)
    timings: list[float] = []
    outputs: Sequence[np.ndarray] = ()
    process_started = time.process_time_ns()
    for _index in range(iterations):
        started = time.perf_counter_ns()
        outputs = function(source)
        timings.append((time.perf_counter_ns() - started) / 1_000_000.0)
    process_cpu_ms = (time.process_time_ns() - process_started) / 1_000_000.0
    values = np.asarray(timings, dtype=np.float64)
    if not np.all(np.isfinite(values)):
        raise RuntimeError("benchmark produced a non-finite latency")
    return (
        {
            "mean_ms": float(values.mean()),
            "p50_ms": float(np.percentile(values, 50)),
            "p95_ms": float(np.percentile(values, 95)),
            "min_ms": float(values.min()),
            "max_ms": float(values.max()),
            "process_cpu_ms": process_cpu_ms,
        },
        [np.asarray(output, dtype=np.float32) for output in outputs],
    )


def deterministic_model_input(manifest: InferenceModelManifest) -> np.ndarray:
    rng = np.random.default_rng(310_005)
    if manifest.task == "iq_classification":
        values = rng.standard_normal(manifest.input_shape, dtype=np.float32)
        values -= values.mean(axis=2, keepdims=True, dtype=np.float32)
        scale = np.maximum(values.std(axis=2, keepdims=True), np.float32(1.0e-8))
        return np.ascontiguousarray(values / scale, dtype=np.float32)
    values = rng.uniform(0.0, 1.0, size=manifest.input_shape).astype(np.float32)
    return np.ascontiguousarray(values)


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
        raise ValueError(f"{field} must be a finite positive number")
    return numeric


def _load_finite_input(path: Path, expected_shape: tuple[int, ...]) -> np.ndarray:
    try:
        source = np.load(path, allow_pickle=False)
    except (OSError, ValueError) as exc:
        raise ValueError(f"unable to load --input-npy: {path}") from exc
    values = np.asarray(source)
    if np.issubdtype(values.dtype, np.complexfloating) or values.dtype == np.bool_:
        raise ValueError("verification input must contain real float values")
    try:
        result = np.ascontiguousarray(values, dtype=np.float32)
    except (TypeError, ValueError) as exc:
        raise ValueError("verification input must contain real float values") from exc
    if tuple(result.shape) != expected_shape:
        raise ValueError(
            f"verification input shape {result.shape} does not match {expected_shape}"
        )
    if not np.all(np.isfinite(result)):
        raise ValueError("verification input contains NaN or Inf")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--input-npy", type=Path)
    parser.add_argument("--warmup", type=int, default=50)
    parser.add_argument("--iterations", type=int, default=300)
    parser.add_argument("--rtol", type=float, default=1.0e-2)
    parser.add_argument("--atol", type=float, default=1.0e-3)
    parser.add_argument("--real-time-budget-ms", type=float)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--accepted-manifest", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if isinstance(args.warmup, bool) or args.warmup < 0 or isinstance(args.iterations, bool) or args.iterations <= 0:
        raise ValueError("warmup must be non-negative and iterations must be positive")
    rtol = _finite_nonnegative(args.rtol, "rtol")
    atol = _finite_nonnegative(args.atol, "atol")
    manifest = load_inference_manifest(
        args.manifest, require_accepted=False, require_artifacts=True
    )
    if sha256(manifest.onnx_path) != manifest.onnx_sha256:
        raise RuntimeError("ONNX SHA256 does not match the manifest")
    if sha256(manifest.om_path) != manifest.om_sha256:
        raise RuntimeError("OM SHA256 does not match the manifest")
    if args.input_npy is None:
        source = deterministic_model_input(manifest)
        input_provenance: dict[str, Any] = {"kind": "deterministic", "seed": 310_005}
    else:
        source = _load_finite_input(args.input_npy, manifest.input_shape)
        input_provenance = {
            "kind": "npy",
            "path": str(args.input_npy),
            "sha256": sha256(args.input_npy),
        }
    real_time_budget_ms = args.real_time_budget_ms
    if real_time_budget_ms is None and manifest.sample_rate_hz is not None:
        if manifest.task == "iq_classification":
            samples = manifest.batch_size * manifest.input_shape[2]
        else:
            samples = (
                manifest.batch_size
                * manifest.input_shape[2]
                * manifest.input_shape[3]
            )
        real_time_budget_ms = 1_000.0 * samples / manifest.sample_rate_hz
    if real_time_budget_ms is None:
        raise ValueError(
            "provide --real-time-budget-ms when the manifest has no sample_rate_hz"
        )
    real_time_budget_ms = _finite_positive(real_time_budget_ms, "real-time-budget-ms")
    if args.report is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        report_path = Path("data/model_admission") / manifest.model_id / f"admission_{stamp}.json"
    else:
        report_path = args.report.resolve()
    if report_path.exists():
        raise FileExistsError(f"refusing to overwrite existing admission report: {report_path}")
    accepted_path: Path | None = None
    if args.accepted_manifest is not None:
        accepted_path = args.accepted_manifest.resolve()
        if accepted_path == manifest.manifest_path:
            raise ValueError("--accepted-manifest must not overwrite the source manifest")
        if accepted_path.exists():
            raise FileExistsError(
                f"refusing to overwrite existing accepted manifest: {accepted_path}"
            )

    ort_session = ort.InferenceSession(
        str(manifest.onnx_path), providers=["CPUExecutionProvider"]
    )
    ort_summary, ort_outputs = benchmark(
        lambda values: ort_session.run(
            list(manifest.output_names), {manifest.input_name: values}
        ),
        source,
        warmup=args.warmup,
        iterations=args.iterations,
    )
    runner = AscendOmRunner(manifest.om_path)
    initialized = runner.initialize()
    if not initialized.ready:
        raise RuntimeError(initialized.message)
    try:
        npu_summary, npu_outputs = benchmark(
            runner.run_all,
            source,
            warmup=args.warmup,
            iterations=args.iterations,
        )
    finally:
        runner.close()
    if len(ort_outputs) != len(npu_outputs):
        raise AssertionError("ONNX Runtime and OM returned different output counts")
    comparisons = [
        compare_model_outputs(
            expected,
            actual,
            task=manifest.task if index == 0 else "auxiliary",
            rtol=rtol,
            atol=atol,
        )
        for index, (expected, actual) in enumerate(zip(ort_outputs, npu_outputs))
    ]
    numerical_passed = all(bool(comparison["passed"]) for comparison in comparisons)
    blockers = [str(value) for value in manifest.admission.get("blockers", [])]
    source_contract_verified = bool(
        manifest.admission.get("source_contract_verified", True)
    ) and not blockers
    live_demo_eligible = bool(manifest.admission.get("live_demo_eligible", True))
    if npu_summary["p50_ms"] <= 0.0:
        raise RuntimeError("NPU benchmark p50 must be positive")
    speedup = ort_summary["p50_ms"] / npu_summary["p50_ms"]
    npu_p95_meets_window_budget = npu_summary["p95_ms"] <= real_time_budget_ms
    if numerical_passed and npu_p95_meets_window_budget and source_contract_verified:
        status = "accepted"
    else:
        status = "rejected"
    if status == "accepted" and speedup >= 1.2:
        recommendation = "npu_recommended"
    elif status == "accepted" and speedup >= 0.8:
        recommendation = "conditional"
    else:
        recommendation = "cpu_preferred_or_model_rejected"
    admission = {
        "status": status,
        "recommendation": recommendation,
        "verified_utc": datetime.now(timezone.utc).isoformat(),
        "numerical_passed": numerical_passed,
        "source_contract_verified": source_contract_verified,
        "blockers": blockers,
        "live_demo_eligible": live_demo_eligible,
        "comparisons": comparisons,
        "cpu_onnxruntime": ort_summary,
        "npu_om_end_to_end": npu_summary,
        "npu_speedup_over_cpu": speedup,
        "npu_p95_ms": npu_summary["p95_ms"],
        "real_time_budget_ms": real_time_budget_ms,
        "npu_p95_meets_window_budget": npu_p95_meets_window_budget,
        "p95_meets_real_time": npu_p95_meets_window_budget,
        "p95_meets_real_time_provenance": "legacy alias for npu_p95_meets_window_budget",
        "input_provenance": input_provenance,
    }
    report = {
        "schema_version": 1,
        "model_id": manifest.model_id,
        "manifest": str(manifest.manifest_path),
        "backend": initialized.backend,
        "admission": admission,
    }
    report_path = write_new_json(report_path, report)
    if accepted_path is not None:
        if status != "accepted":
            raise RuntimeError("model did not pass admission; accepted manifest was not written")
        accepted = replace(manifest, admission=admission, manifest_path=accepted_path)
        write_new_json(accepted_path, accepted.to_dict())
        load_inference_manifest(accepted_path, require_accepted=True)
    print(json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False))
    print(f"wrote {report_path}")
    return 0 if status == "accepted" else 2


if __name__ == "__main__":
    raise SystemExit(main())
