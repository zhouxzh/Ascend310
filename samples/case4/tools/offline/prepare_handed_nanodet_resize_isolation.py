#!/usr/bin/env python3
"""Extract and validate the first FPN linear Resize as a standalone ONNX probe.

The probe consumes the real output of ``reduce_layers.2`` and reproduces the
first top-down FPN resize from 10x10 to 20x20.  It distinguishes a standalone
Resize implementation difference from an error that only appears when the full
FPN graph is fused by ATC.
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
REDUCE_OUTPUT = "/fpn/reduce_layers.2/act/LeakyRelu_output_0"
RESIZE_OUTPUT = "/fpn/upsample/Resize_output_0"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_array(value: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-onnx", type=Path, required=True)
    parser.add_argument("--fpn-probe-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.source_onnx.resolve()
    fpn_probe_dir = args.fpn_probe_dir.resolve()
    output_dir = args.output_dir.resolve()
    if not source.is_file() or sha256_file(source) != SOURCE_ONNX_SHA256:
        raise ValueError("unexpected seed43 ONNX artifact")
    if output_dir.exists() and any(output_dir.iterdir()) and not args.overwrite:
        raise FileExistsError(f"output exists: {output_dir}")
    try:
        import onnx
        import onnxruntime as ort
    except ImportError as exc:  # pragma: no cover - ACE2-only dependencies
        raise RuntimeError("onnx and onnxruntime-gpu are required") from exc
    if "CUDAExecutionProvider" not in ort.get_available_providers():
        raise RuntimeError("CUDAExecutionProvider unavailable")
    fpn_manifest = json.loads((fpn_probe_dir / "manifest.json").read_text(encoding="utf-8"))
    outputs = fpn_manifest.get("outputs", [])
    names = [item.get("name") for item in outputs]
    if names[:4] != [
        "/fpn/reduce_layers.0/act/LeakyRelu_output_0",
        "/fpn/reduce_layers.1/act/LeakyRelu_output_0",
        REDUCE_OUTPUT,
        RESIZE_OUTPUT,
    ]:
        raise ValueError("unexpected FPN probe output contract")
    output_dir.mkdir(parents=True, exist_ok=True)
    inferred = onnx.shape_inference.infer_shapes(onnx.load(str(source), load_external_data=False))
    inferred_path = output_dir / "source-inferred.onnx"
    onnx.save(inferred, str(inferred_path))
    resize_path = output_dir / "seed43-fpn-resize1-isolation.onnx"
    onnx.utils.extract_model(str(inferred_path), str(resize_path), [REDUCE_OUTPUT], [RESIZE_OUTPUT], check_model=True)
    isolated = onnx.load(str(resize_path), load_external_data=False)
    input_info = isolated.graph.input[0]
    output_info = isolated.graph.output[0]
    if input_info.name != REDUCE_OUTPUT or output_info.name != RESIZE_OUTPUT:
        raise ValueError("isolated Resize IO names differ from source graph")
    input_shape = [int(dim.dim_value) for dim in input_info.type.tensor_type.shape.dim]
    output_shape = [int(dim.dim_value) for dim in output_info.type.tensor_type.shape.dim]
    if input_shape != [1, 96, 10, 10] or output_shape != [1, 96, 20, 20]:
        raise ValueError(f"unexpected isolated Resize contract: {input_shape} -> {output_shape}")
    node = next((item for item in isolated.graph.node if item.op_type == "Resize"), None)
    if node is None:
        raise ValueError("isolated graph does not contain Resize")
    attributes = {item.name: onnx.helper.get_attribute_value(item) for item in node.attribute}
    options = ort.SessionOptions()
    options.enable_profiling = True
    session = ort.InferenceSession(str(resize_path), sess_options=options, providers=["CUDAExecutionProvider"])
    if session.get_providers()[0] != "CUDAExecutionProvider":
        raise RuntimeError(f"CUDA not primary: {session.get_providers()}")
    records = []
    references_dir = output_dir / "references"
    references_dir.mkdir(exist_ok=True)
    for sample in fpn_manifest["samples"]:
        image_id = int(sample["image_id"])
        source_reference = fpn_probe_dir / "references" / Path(sample["reference"]["path"]).name
        if sha256_file(source_reference) != sample["reference"]["sha256"]:
            raise ValueError(f"FPN reference integrity mismatch for image {image_id}")
        with np.load(source_reference, allow_pickle=False) as data:
            resize_input = np.ascontiguousarray(data["output_002"], dtype=np.float32)
            expected = np.ascontiguousarray(data["output_003"], dtype=np.float32)
        actual = np.asarray(session.run([RESIZE_OUTPUT], {REDUCE_OUTPUT: resize_input})[0], dtype=np.float32)
        error = np.abs(actual - expected)
        if not np.array_equal(actual, expected):
            raise ValueError(f"CUDA isolated Resize differs from full FPN for image {image_id}: max={error.max()}")
        reference = references_dir / f"image-{image_id}.npz"
        np.savez_compressed(reference, input=resize_input, output_000=expected)
        records.append({
            "image_id": image_id,
            "input_sha256": sha256_array(resize_input),
            "output_sha256": sha256_array(expected),
            "reference": {"path": str(reference), "sha256": sha256_file(reference)},
        })
    profile_path = Path(session.end_profiling())
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    provider_counts: dict[str, int] = {}
    for event in profile:
        if event.get("cat") == "Node":
            name = str(event.get("args", {}).get("provider", "unknown"))
            provider_counts[name] = provider_counts.get(name, 0) + 1
    if provider_counts.get("CPUExecutionProvider", 0) or not provider_counts.get("CUDAExecutionProvider", 0):
        raise RuntimeError(f"isolated Resize did not run exclusively on CUDA: {provider_counts}")
    manifest = {
        "schema_version": 1,
        "kind": "handed_nanodet_fpn_resize_isolation",
        "created_at": now(),
        "source_onnx": {"path": str(source), "sha256": SOURCE_ONNX_SHA256},
        "source_boundary": {"input": REDUCE_OUTPUT, "output": RESIZE_OUTPUT},
        "resize_attributes": {key: value.decode() if isinstance(value, bytes) else value for key, value in attributes.items()},
        "isolated_onnx": {"path": str(resize_path), "sha256": sha256_file(resize_path), "bytes": resize_path.stat().st_size},
        "input": {"name": REDUCE_OUTPUT, "shape": input_shape, "dtype": "float32"},
        "output": {"name": RESIZE_OUTPUT, "shape": output_shape, "dtype": "float32"},
        "samples": records,
        "cuda_profile": {"path": str(profile_path), "sha256": sha256_file(profile_path), "node_providers": provider_counts},
    }
    write_json(output_dir / "manifest.json", manifest)
    print(json.dumps({
        "isolated_onnx": manifest["isolated_onnx"],
        "resize_attributes": manifest["resize_attributes"],
        "samples": [item["image_id"] for item in records],
        "profile": provider_counts,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
