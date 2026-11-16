#!/usr/bin/env python3
"""Compare an isolated seed43 FPN Resize OM against CUDA ONNX golden tensors."""

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


def timestamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runner", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--onnx", type=Path, required=True)
    parser.add_argument("--om", type=Path, required=True)
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--board", required=True)
    parser.add_argument("--soc", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("kind") not in {
        "handed_nanodet_fpn_resize_isolation",
        "handed_nanodet_fpn_resize_static_arithmetic_rewrite",
    }:
        raise ValueError("unsupported isolated Resize manifest")
    onnx_key = "isolated_onnx" if "isolated_onnx" in manifest else "rewritten_onnx"
    onnx_metadata = manifest[onnx_key]
    onnx_path = args.onnx.resolve()
    om_path = args.om.resolve()
    reference_dir = args.reference_dir.resolve()
    output_dir = args.output_dir.resolve()
    if sha256_file(onnx_path) != onnx_metadata["sha256"]:
        raise ValueError("isolated Resize ONNX SHA mismatch")
    if not om_path.is_file():
        raise FileNotFoundError(f"OM missing: {om_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    samples = []
    max_abs_error = 0.0
    mean_errors: list[float] = []
    allclose_samples = 0
    for source in manifest["samples"]:
        image_id = int(source["image_id"])
        reference_path = reference_dir / Path(source["reference"]["path"]).name
        if not reference_path.is_file() or sha256_file(reference_path) != source["reference"]["sha256"]:
            raise ValueError(f"reference integrity mismatch for image {image_id}")
        report_path = output_dir / f"image-{image_id}.json"
        command = [
            sys.executable, str(args.runner.resolve()), "run-one",
            "--board", args.board,
            "--soc", args.soc,
            "--model-id", "seed43-fpn-resize1-isolation",
            "--precision", "fp32",
            "--onnx", str(onnx_path),
            "--om", str(om_path),
            "--reference", str(reference_path),
            "--expected-onnx-sha256", onnx_metadata["sha256"],
            "--expected-reference-sha256", source["reference"]["sha256"],
            "--output", str(report_path),
            "--warmup", "1", "--loops", "1", "--repeats", "1",
        ]
        completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        if not report_path.is_file():
            raise RuntimeError(f"runner did not report for image {image_id}: {completed.stdout[-1000:]}")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        outputs = report.get("comparison", {}).get("outputs", [])
        if len(outputs) != 1:
            raise ValueError(f"expected one isolated Resize output for image {image_id}")
        output = outputs[0]
        if output.get("actual_shape") != manifest["output"]["shape"] or output.get("expected_shape") != manifest["output"]["shape"]:
            raise ValueError(f"Resize output shape mismatch for image {image_id}")
        if not output.get("actual_finite") or not output.get("expected_finite"):
            raise ValueError(f"non-finite Resize output for image {image_id}")
        entry = {
            "image_id": image_id,
            "runner_returncode": completed.returncode,
            "runner_status": report.get("status"),
            "cleanup_ok": all(step.get("ok") for step in report.get("cleanup", [])),
            "passed": bool(output.get("passed")),
            "max_abs_error": float(output.get("max_abs_error", 0.0)),
            "mean_abs_error": float(output.get("mean_abs_error", 0.0)),
            "report": {"path": str(report_path), "sha256": sha256_file(report_path)},
        }
        samples.append(entry)
        max_abs_error = max(max_abs_error, entry["max_abs_error"])
        mean_errors.append(entry["mean_abs_error"])
        allclose_samples += int(entry["passed"])
    result = {
        "schema_version": 1,
        "kind": "handed_nanodet_fpn_resize_isolation_acl_comparison",
        "created_at": timestamp(),
        "board": {"id": args.board, "soc": args.soc},
        "source_boundary": manifest.get("source_boundary", {"input": manifest["input"]["name"], "output": manifest["output"]["name"]}),
        "resize_attributes": manifest.get("resize_attributes", {"rewrite_semantics": manifest.get("semantics")}),
        "isolated_onnx": onnx_metadata,
        "om": {"path": str(om_path), "sha256": sha256_file(om_path), "bytes": om_path.stat().st_size},
        "protocol": {"atol": 0.001, "rtol": 0.001, "warmup": 1, "loops": 1, "repeats": 1},
        "summary": {
            "samples": len(samples),
            "allclose_samples": allclose_samples,
            "max_abs_error": max_abs_error,
            "mean_abs_error": sum(mean_errors) / len(mean_errors),
        },
        "samples_detail": samples,
    }
    write_json(output_dir / "summary.json", result)
    print(json.dumps({"summary": result["summary"], "om": result["om"]}, ensure_ascii=False, indent=2))
    return 0 if all(item["cleanup_ok"] for item in samples) else 1


if __name__ == "__main__":
    raise SystemExit(main())
