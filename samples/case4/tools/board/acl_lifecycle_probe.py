#!/usr/bin/env python3
"""Exercise one NPU OM through repeatable load/run/close lifecycle cycles.

The probe is a board diagnostic, not a production evaluation.  It requires a
synthetic or public ROI image and writes ignored evidence under
``reports/system``.  It deliberately uses the offline candidate adapter so a
pending CompNet cannot be silently promoted by a diagnostic command.

By default the process-owned ACL runtime stays initialized while every cycle
creates, runs, and closes its OM runner.  This mirrors the FastAPI service and
avoids reinitializing CANN between adjacent model loads.  The explicit
``--shutdown-per-cycle`` switch is reserved for diagnosing the historical
per-cycle reset failure and is not the production lifecycle.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from palmprint_workbench.config import ROOT

from palmprint_workbench.domain.registry import ModelRegistry
from palmprint_workbench.runtime.adapters import acl_runtime_status, create_adapter, shutdown_acl_runtime


def _timestamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def _read_roi(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"could not read ROI image: {path}")
    return cv2.resize(image, (128, 128), interpolation=cv2.INTER_AREA)


def _runner_diagnostics(adapter: Any) -> list[dict[str, Any]]:
    runner = getattr(adapter, "runner", None)
    values = getattr(runner, "cleanup_diagnostics", []) if runner is not None else []
    return list(values) if isinstance(values, list) else []


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--image", type=Path, required=True)
    parser.add_argument("--cycles", type=int, default=10)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports" / "system")
    parser.add_argument(
        "--shutdown-per-cycle",
        action="store_true",
        help=(
            "diagnostic-only mode: reset/finalize ACL after every cycle; "
            "the default keeps one runtime for the whole probe"
        ),
    )
    args = parser.parse_args()
    if args.cycles < 1:
        parser.error("--cycles must be at least 1")
    if args.threads < 1:
        parser.error("--threads must be at least 1")
    return args


def main() -> int:
    args = parse_args()
    roi = _read_roi(args.image)
    registry = ModelRegistry()
    # This intentionally resolves the audit-only contract. Production use is
    # guarded elsewhere by candidate_admission and models/registry.json.
    spec = registry.offline_candidate_embedding_spec(args.model)
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_path = output_dir / f"acl_lifecycle_{args.model}_{stamp}.json"
    report: dict[str, Any] = {
        "model_id": args.model,
        "precision": "mixed_fp16",
        "image": str(args.image),
        # The diagnostic retains only the source path and derived lifecycle
        # metrics; it never serializes ROI pixels or embeddings.
        "contains_biometric_data": False,
        "cycles_requested": args.cycles,
        "runtime_policy": (
            "shutdown_per_cycle_diagnostic"
            if args.shutdown_per_cycle
            else "shared_runtime_until_probe_end"
        ),
        "started_at": _timestamp(),
        "cycles": [],
    }
    failures = 0
    try:
        for index in range(args.cycles):
            cycle: dict[str, Any] = {
                "cycle": index + 1,
                "started_at": _timestamp(),
                "before_create": acl_runtime_status(),
            }
            adapter: Any | None = None
            try:
                adapter = create_adapter(spec, "npu", "mixed_fp16", threads=args.threads)
                result = adapter.encode(roi)
                cycle["inference"] = {
                    "code_finite": bool(np.all(np.isfinite(result.code))),
                    "feature_dim": int(result.code.size),
                    "inference_ms": float(result.inference_ms),
                }
            except BaseException as exc:
                failures += 1
                cycle["inference_error"] = f"{type(exc).__name__}: {exc}"
            finally:
                cycle["before_close"] = acl_runtime_status()
                try:
                    if adapter is not None:
                        adapter.close()
                    cycle["adapter_close"] = {"ok": True, "runner": _runner_diagnostics(adapter)}
                except BaseException as exc:
                    failures += 1
                    cycle["adapter_close"] = {
                        "ok": False,
                        "error": f"{type(exc).__name__}: {exc}",
                        "runner": _runner_diagnostics(adapter),
                    }
                cycle["after_runner_close"] = acl_runtime_status()
                cycle["before_runtime_shutdown"] = acl_runtime_status()
                if args.shutdown_per_cycle:
                    try:
                        runtime = shutdown_acl_runtime()
                        cycle["after_runtime_shutdown"] = runtime
                        if not runtime.get("ok", False):
                            failures += 1
                    except BaseException as exc:
                        failures += 1
                        cycle["after_runtime_shutdown"] = {
                            "ok": False,
                            "status": "exception",
                            "error": f"{type(exc).__name__}: {exc}",
                        }
                else:
                    # Keep the phase names stable for telemetry consumers while
                    # making it explicit that the process-owned runtime remains
                    # live until the probe-level finally block.
                    cycle["after_runtime_shutdown"] = {
                        "ok": True,
                        "status": "deferred_until_probe_end",
                        "runtime": acl_runtime_status(),
                    }
                cycle["finished_at"] = _timestamp()
            report["cycles"].append(cycle)
            if failures:
                # A device or lifecycle failure must leave a small, unambiguous
                # report rather than continue to run later candidates.
                break
    finally:
        # Production services own one process-wide ACL runtime.  Close it once
        # after all runners have been released, even when a cycle fails.  The
        # optional per-cycle mode has already shut it down, so this call is
        # idempotent and records the final probe boundary as well.
        report["before_runtime_shutdown_final"] = acl_runtime_status()
        # Also expose the canonical phase names at report scope.  The cycle
        # entries use the same names for their per-cycle/deferred trace, while
        # these top-level fields describe the one actual probe shutdown.
        report["before_runtime_shutdown"] = report["before_runtime_shutdown_final"]
        try:
            final_runtime = shutdown_acl_runtime()
            report["after_runtime_shutdown_final"] = final_runtime
            report["after_runtime_shutdown"] = final_runtime
            if not final_runtime.get("ok", False):
                failures += 1
        except BaseException as exc:
            failures += 1
            report["after_runtime_shutdown_final"] = {
                "ok": False,
                "status": "exception",
                "error": f"{type(exc).__name__}: {exc}",
            }
            report["after_runtime_shutdown"] = report["after_runtime_shutdown_final"]
    report["finished_at"] = _timestamp()
    report["status"] = "passed" if not failures and len(report["cycles"]) == args.cycles else "failed"
    report["failures"] = failures
    report["final_runtime"] = acl_runtime_status()
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(report_path), "status": report["status"]}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
