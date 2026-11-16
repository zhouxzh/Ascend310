#!/usr/bin/env python3
"""Verify a rewritten seed43 ONNX against CUDA reference outputs.

The reference pack contains fixed preprocessed tensors and CUDA outputs from the
original seed43 ONNX. This checker runs the candidate ONNX exclusively through
CUDAExecutionProvider, compares every raw output, and records aggregate error
without persisting images or tensors.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

SOURCE_ONNX_SHA256 = "35f71933a0629df4338673e1c9a3b0411e778fbc85c22b973cca33f69a267991"
ANNOTATION_SHA256 = "5db886eb40a75789b62b708445141081e2ca2c5276d445174efa3446b6b70b8a"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--atol", type=float, default=2e-6)
    parser.add_argument("--rtol", type=float, default=2e-6)
    return parser.parse_args()


def profile_summary(path: Path) -> dict[str, Any]:
    events = json.loads(path.read_text(encoding="utf-8"))
    providers: dict[str, int] = {}
    for event in events:
        if event.get("cat") != "Node":
            continue
        provider = str(event.get("args", {}).get("provider", "unknown"))
        providers[provider] = providers.get(provider, 0) + 1
    if providers.get("CPUExecutionProvider", 0) or not providers.get("CUDAExecutionProvider", 0):
        raise RuntimeError(f"candidate ONNX did not execute exclusively on CUDA: {providers}")
    return {"path": str(path), "sha256": sha256_file(path), "node_providers": providers}


def main() -> int:
    args = parse_args()
    candidate = args.candidate.resolve()
    reference_dir = args.reference_dir.resolve()
    if not candidate.is_file():
        raise FileNotFoundError(candidate)
    try:
        import onnxruntime as ort
    except ImportError as exc:  # pragma: no cover - ACE2-only dependency
        raise RuntimeError("onnxruntime-gpu is required") from exc
    if "CUDAExecutionProvider" not in ort.get_available_providers():
        raise RuntimeError("CUDAExecutionProvider unavailable")
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from handed_nanodet_fulltest import iter_reference_samples

    options = ort.SessionOptions()
    options.enable_profiling = True
    session = ort.InferenceSession(str(candidate), sess_options=options, providers=["CUDAExecutionProvider"])
    if session.get_providers()[0] != "CUDAExecutionProvider":
        raise RuntimeError(f"CUDA not primary: {session.get_providers()}")
    inputs = session.get_inputs()
    outputs = session.get_outputs()
    if [(item.name, item.shape, item.type) for item in inputs] != [("data", [1, 3, 320, 320], "tensor(float)")]:
        raise ValueError(f"candidate input contract changed: {[(item.name, item.shape, item.type) for item in inputs]}")
    if [(item.name, item.shape, item.type) for item in outputs] != [("output", [1, 2125, 34], "tensor(float)")]:
        raise ValueError(f"candidate output contract changed: {[(item.name, item.shape, item.type) for item in outputs]}")
    count = 0
    allclose = 0
    abs_sum = 0.0
    element_count = 0
    abs_max = 0.0
    worst: list[dict[str, Any]] = []
    for metadata, tensor, expected in iter_reference_samples(
        reference_dir,
        expected_onnx_sha256=SOURCE_ONNX_SHA256,
        expected_annotations_sha256=ANNOTATION_SHA256,
    ):
        actual = np.asarray(session.run(["output"], {"data": tensor})[0], dtype=np.float32)
        if actual.shape != expected.shape or not np.isfinite(actual).all():
            raise ValueError(f"candidate output contract failure at image {metadata['image_id']}")
        error = np.abs(actual - expected)
        entry = {
            "image_id": int(metadata["image_id"]),
            "max_abs_error": float(error.max()),
            "mean_abs_error": float(error.mean(dtype=np.float64)),
            "allclose": bool(np.allclose(actual, expected, atol=args.atol, rtol=args.rtol, equal_nan=False)),
        }
        worst.append(entry)
        worst.sort(key=lambda item: item["max_abs_error"], reverse=True)
        del worst[10:]
        count += 1
        allclose += int(entry["allclose"])
        abs_sum += float(error.sum(dtype=np.float64))
        element_count += int(error.size)
        abs_max = max(abs_max, entry["max_abs_error"])
    profile = profile_summary(Path(session.end_profiling()))
    report = {
        "schema_version": 1,
        "kind": "handed_nanodet_full_graph_rewrite_cuda_equivalence",
        "created_at": now(),
        "candidate": {"path": str(candidate), "sha256": sha256_file(candidate), "bytes": candidate.stat().st_size},
        "source_onnx_sha256": SOURCE_ONNX_SHA256,
        "reference_pack": {"path": str(reference_dir), "annotations_sha256": ANNOTATION_SHA256},
        "tolerance": {"atol": args.atol, "rtol": args.rtol},
        "samples": count,
        "allclose_samples": allclose,
        "allclose_rate": allclose / count if count else None,
        "max_abs_error": abs_max,
        "mean_abs_error": abs_sum / element_count if element_count else None,
        "worst_samples": worst,
        "cuda_profile": profile,
    }
    write_json(args.output.resolve(), report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if allclose == count else 1


if __name__ == "__main__":
    raise SystemExit(main())
