"""Run a board-only OM inference soak and record NPU runtime stability."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
import time

import numpy as np

from ..npu import AscendOmRunner
from .inference_manifest import load_inference_manifest
from .inference_manifest import verify_artifact_hashes
from .safe_json import write_new_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--duration-seconds", type=float, default=600.0)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def npu_smi_snapshot() -> str | None:
    try:
        completed = subprocess.run(
            ["npu-smi", "info"], check=False, capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def deterministic_input(shape: tuple[int, ...]) -> np.ndarray:
    if not shape or any(isinstance(value, bool) or not isinstance(value, int) or value <= 0 for value in shape):
        raise ValueError("shape must contain positive integer dimensions")
    values = np.arange(np.prod(shape), dtype=np.float32).reshape(shape)
    return np.sin(values * np.float32(0.0001)).astype(np.float32, copy=False)


def timing_summary(values_ms: list[float]) -> dict[str, float]:
    values = np.asarray(values_ms, dtype=np.float64)
    if values.size == 0:
        raise ValueError("OM soak completed without a measured inference")
    if not np.all(np.isfinite(values)) or np.any(values < 0.0):
        raise ValueError("OM soak timing contains an invalid value")
    return {
        "count": int(values.size),
        "mean_ms": float(values.mean()),
        "p50_ms": float(np.percentile(values, 50)),
        "p95_ms": float(np.percentile(values, 95)),
        "max_ms": float(values.max()),
    }


def main() -> int:
    args = parse_args()
    if (
        isinstance(args.duration_seconds, bool)
        or not np.isfinite(args.duration_seconds)
        or args.duration_seconds <= 0
        or isinstance(args.warmup, bool)
        or args.warmup < 0
    ):
        raise ValueError("duration-seconds must be positive and warmup must be non-negative")
    manifest = load_inference_manifest(args.manifest, require_accepted=False)
    verify_artifact_hashes(manifest)
    if args.report is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        report_path = Path("data/model_admission") / manifest.model_id / f"soak_{stamp}.json"
    else:
        report_path = args.report.resolve()
    if report_path.exists():
        raise FileExistsError(f"refusing to overwrite existing soak report: {report_path}")
    source = deterministic_input(manifest.input_shape)
    runner = AscendOmRunner(manifest.om_path)
    status = runner.initialize()
    if not status.ready:
        raise RuntimeError(status.message)

    started_at = datetime.now(timezone.utc).isoformat()
    wall_started = time.monotonic()
    temperatures_before = npu_smi_snapshot()
    timings: list[float] = []
    try:
        for _ in range(args.warmup):
            runner.run(source)
        while not timings or time.monotonic() - wall_started < args.duration_seconds:
            started = time.perf_counter_ns()
            output = runner.run(source)
            elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
            if not np.all(np.isfinite(output)):
                raise RuntimeError("OM soak output contains NaN or Inf")
            timings.append(elapsed_ms)
    finally:
        runner.close()
    elapsed_s = time.monotonic() - wall_started
    report = {
        "schema_version": 1,
        "model_id": manifest.model_id,
        "manifest": str(manifest.manifest_path),
        "backend": status.backend,
        "admission_status": manifest.admission.get("status"),
        "purpose": "runtime stability only; this does not change model admission",
        "started_at_utc": started_at,
        "elapsed_seconds": elapsed_s,
        "timing": timing_summary(timings),
        "npu_smi_before": temperatures_before,
        "npu_smi_after": npu_smi_snapshot(),
    }
    report_path = write_new_json(report_path, report)
    print(json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False))
    print(f"wrote {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
