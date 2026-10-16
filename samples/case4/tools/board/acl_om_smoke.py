#!/usr/bin/env python3
"""Run a generic one-input/one-output OM with ACL and NumPy only.

This board tool deliberately does not import PyTorch.  It is intended for
research classifier/ROI OM files whose contracts differ from the production
128x128 embedding route.  It uses the same transactional ``_OmRunner`` as the
service, but does not modify the production registry.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any

import numpy as np

from palmprint_workbench.runtime.adapters import (
    _OmRunner,
    acl_runtime_status,
    shutdown_acl_runtime,
)


def _timestamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--loops", type=int, default=1)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.loops < 1:
        parser.error("--loops must be at least 1")
    return args


def main() -> int:
    args = parse_args()
    report: dict[str, Any] = {
        "schema_version": 1,
        "model": str(args.model),
        "backend": "acl",
        "framework": "numpy_only",
        "started_at": _timestamp(),
        "loops_requested": args.loops,
        "contains_biometric_data": False,
        "status": "failed",
    }
    runner: _OmRunner | None = None
    failures: list[str] = []
    try:
        runner = _OmRunner(args.model)
        report.update(
            {
                "input_shape": list(runner.input_shape),
                "input_dtype": str(runner.input_dtype),
                "output_shape": list(runner.output_shape),
                "output_dtype": str(runner.output_dtype),
                "runtime_after_load": acl_runtime_status(),
            }
        )
        rng = np.random.default_rng(20260911)
        value = rng.standard_normal(runner.input_shape).astype(runner.input_dtype)
        timings: list[float] = []
        outputs: list[np.ndarray] = []
        for _ in range(args.loops):
            tick = time.perf_counter()
            output = runner.run(value)
            timings.append((time.perf_counter() - tick) * 1000.0)
            outputs.append(output)
        finite = bool(outputs and all(np.all(np.isfinite(item)) for item in outputs))
        report.update(
            {
                "output_finite": finite,
                "timing_ms": {
                    "min": min(timings),
                    "max": max(timings),
                    "median": float(np.median(timings)),
                },
                "observed_output_shape": list(outputs[-1].shape),
            }
        )
        if not finite:
            failures.append("output contains NaN or infinity")
    except BaseException as exc:  # noqa: BLE001 - preserve candidate evidence
        failures.append(f"{type(exc).__name__}: {exc}")
        report["error"] = failures[-1]
    finally:
        report["before_close"] = acl_runtime_status()
        if runner is not None:
            try:
                report["close"] = runner.close()
            except BaseException as exc:  # noqa: BLE001 - continue runtime cleanup
                failures.append(f"close {type(exc).__name__}: {exc}")
                report["close_error"] = failures[-1]
        report["after_runner_close"] = acl_runtime_status()
        report["before_runtime_shutdown"] = acl_runtime_status()
        try:
            report["runtime_shutdown"] = shutdown_acl_runtime()
        except BaseException as exc:  # noqa: BLE001 - report final lifecycle state
            failures.append(f"runtime shutdown {type(exc).__name__}: {exc}")
            report["runtime_shutdown_error"] = failures[-1]
        report["after_runtime_shutdown"] = acl_runtime_status()
    report["finished_at"] = _timestamp()
    report["failures"] = failures
    report["status"] = "passed" if not failures else "failed"
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
