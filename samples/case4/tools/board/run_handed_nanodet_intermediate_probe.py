#!/usr/bin/env python3
"""Run a prepared multi-output seed43 ONNX probe through one 310B OM.

The actual ACL execution is delegated to the repository's static detector
benchmark runner.  This wrapper freezes the output-index-to-ONNX-name mapping,
collects its reports, and produces only aggregate intermediate-boundary errors.
It is an offline diagnostic tool, never a service/model-registry component.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", type=Path, required=True, help="unified_detector_benchmark.py")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--onnx", type=Path, required=True)
    parser.add_argument("--om", type=Path, required=True)
    parser.add_argument("--reference-dir", type=Path, required=True, help="board-local directory containing probe reference NPZ files")
    parser.add_argument("--board", required=True)
    parser.add_argument("--soc", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--warmup", type=int, default=1)
    parser.add_argument("--loops", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=1)
    return parser.parse_args()


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest.resolve()
    onnx_path = args.onnx.resolve()
    om_path = args.om.resolve()
    reference_dir = args.reference_dir.resolve()
    output_dir = args.output_dir.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("kind") != "handed_nanodet_intermediate_probe":
        raise ValueError("unsupported intermediate probe manifest")
    if not args.runner.is_file() or not onnx_path.is_file() or not om_path.is_file():
        raise FileNotFoundError("runner, ONNX, or OM is missing")
    if sha256_file(onnx_path) != manifest["probe_onnx"]["sha256"]:
        raise ValueError("probe ONNX hash differs from manifest")
    output_specs = list(manifest.get("outputs", []))
    if not output_specs:
        raise ValueError("probe manifest has no outputs")
    samples = list(manifest.get("samples", []))
    if not samples:
        raise ValueError("probe manifest has no sample references")
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    aggregate: dict[int, dict[str, Any]] = {
        int(item["index"]): {
            "index": int(item["index"]),
            "name": item["name"],
            "shape": item["shape"],
            "samples": 0,
            "allclose_samples": 0,
            "max_abs_error": 0.0,
            "mean_abs_errors": [],
        }
        for item in output_specs
    }
    for sample in samples:
        image_id = int(sample["image_id"])
        reference_path = reference_dir / Path(sample["reference"]["path"]).name
        if not reference_path.is_file() or sha256_file(reference_path) != sample["reference"]["sha256"]:
            raise ValueError(f"board reference artifact mismatch for image {image_id}: {reference_path}")
        report_path = output_dir / f"image-{image_id}.json"
        command = [
            sys.executable,
            str(args.runner.resolve()),
            "run-one",
            "--board", args.board,
            "--soc", args.soc,
            "--model-id", f"seed43-{manifest['probe']}-probe",
            "--precision", "fp32",
            "--onnx", str(onnx_path),
            "--om", str(om_path),
            "--reference", str(reference_path),
            "--expected-onnx-sha256", manifest["probe_onnx"]["sha256"],
            "--expected-reference-sha256", sample["reference"]["sha256"],
            "--output", str(report_path),
            "--warmup", str(args.warmup),
            "--loops", str(args.loops),
            "--repeats", str(args.repeats),
        ]
        completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        if not report_path.is_file():
            raise RuntimeError(f"probe runner did not create report for image {image_id}: {completed.stdout[-1000:]}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        comparison = report.get("comparison", {})
        outputs = comparison.get("outputs", [])
        if len(outputs) != len(output_specs):
            raise ValueError(f"probe output count mismatch for image {image_id}: {len(outputs)} != {len(output_specs)}")
        named_outputs = []
        for spec, value in zip(output_specs, outputs):
            index = int(spec["index"])
            if value.get("index") != index:
                raise ValueError(f"output order mismatch for image {image_id}: {value.get('index')} != {index}")
            if value.get("actual_shape") != spec["shape"] or value.get("expected_shape") != spec["shape"]:
                raise ValueError(f"output shape mismatch at {spec['name']} for image {image_id}")
            if not value.get("actual_finite") or not value.get("expected_finite"):
                raise ValueError(f"non-finite intermediate output at {spec['name']} for image {image_id}")
            row = {
                "index": index,
                "name": spec["name"],
                "passed": bool(value.get("passed")),
                "max_abs_error": float(value.get("max_abs_error", 0.0)),
                "mean_abs_error": float(value.get("mean_abs_error", 0.0)),
                "max_rel_error": float(value.get("max_rel_error", 0.0)),
            }
            named_outputs.append(row)
            total = aggregate[index]
            total["samples"] += 1
            total["allclose_samples"] += int(row["passed"])
            total["max_abs_error"] = max(total["max_abs_error"], row["max_abs_error"])
            total["mean_abs_errors"].append(row["mean_abs_error"])
        rows.append({
            "image_id": image_id,
            "reference_sha256": sample["reference"]["sha256"],
            "runner_returncode": completed.returncode,
            "runner_status": report.get("status"),
            "cleanup_ok": all(step.get("ok") for step in report.get("cleanup", [])),
            "report": {"path": str(report_path), "sha256": sha256_file(report_path)},
            "outputs": named_outputs,
        })
    boundaries = []
    for index in sorted(aggregate):
        item = aggregate[index]
        means = item.pop("mean_abs_errors")
        item["mean_abs_error"] = sum(means) / len(means) if means else None
        boundaries.append(item)
    result = {
        "schema_version": 1,
        "kind": "handed_nanodet_intermediate_probe_acl_comparison",
        "created_at": now(),
        "probe": manifest["probe"],
        "board": {"id": args.board, "soc": args.soc},
        "probe_onnx": manifest["probe_onnx"],
        "om": {"path": str(om_path), "sha256": sha256_file(om_path), "bytes": om_path.stat().st_size},
        "protocol": {"warmup": args.warmup, "loops": args.loops, "repeats": args.repeats, "atol": 0.001, "rtol": 0.001},
        "boundaries": boundaries,
        "samples": rows,
    }
    write_json(output_dir / "summary.json", result)
    print(json.dumps({"probe": result["probe"], "boundaries": boundaries, "samples": len(rows)}, ensure_ascii=False, indent=2))
    return 0 if all(row["cleanup_ok"] for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
