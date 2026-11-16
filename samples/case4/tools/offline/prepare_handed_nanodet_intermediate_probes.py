#!/usr/bin/env python3
"""Extract static seed43 NanoDet intermediate-output ONNX probes on ACE2.

Each probe is an ONNX prefix graph with explicitly named static outputs.  CUDA
ONNX Runtime creates fixed float32 golden tensors for selected already-verified
test inputs.  The generated artifacts are diagnostic-only and must not be used
by the Case4 service.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np

INPUT_NAME = "data"
OUTPUT_NAME = "output"
ONNX_SHA256 = "35f71933a0629df4338673e1c9a3b0411e778fbc85c22b973cca33f69a267991"
ANNOTATION_SHA256 = "5db886eb40a75789b62b708445141081e2ca2c5276d445174efa3446b6b70b8a"

# Ordered from input toward final output.  Probe A intentionally includes
# conv1/activation/pool separately, then one output at each backbone scale.
PROBES: dict[str, tuple[str, ...]] = {
    "backbone": (
        "/backbone/conv1/conv1.0/Conv_output_0",
        "/backbone/conv1/conv1.2/LeakyRelu_output_0",
        "/backbone/maxpool/MaxPool_output_0",
        "/backbone/stage2/stage2.3/Reshape_1_output_0",
        "/backbone/stage3/stage3.7/Reshape_1_output_0",
        "/backbone/stage4/stage4.3/Reshape_1_output_0",
    ),
    "fpn": (
        "/fpn/top_down_blocks.1/blocks/blocks.0/Add_output_0",
        "/fpn/bottom_up_blocks.0/blocks/blocks.0/Add_output_0",
        "/fpn/bottom_up_blocks.1/blocks/blocks.0/Add_output_0",
        "/fpn/Add_output_0",
    ),
    # Refines the first FPN diagnostic hit into each input/reduce, upsample,
    # concatenation and residual-fusion boundary on the two top-down paths.
    "fpn_topdown": (
        "/fpn/reduce_layers.0/act/LeakyRelu_output_0",
        "/fpn/reduce_layers.1/act/LeakyRelu_output_0",
        "/fpn/reduce_layers.2/act/LeakyRelu_output_0",
        "/fpn/upsample/Resize_output_0",
        "/fpn/Concat_output_0",
        "/fpn/top_down_blocks.0/blocks/blocks.0/Add_output_0",
        "/fpn/upsample_1/Resize_output_0",
        "/fpn/Concat_1_output_0",
        "/fpn/top_down_blocks.1/blocks/blocks.0/Add_output_0",
    ),
    "head": (
        "/head/gfl_cls.0/Conv_output_0",
        "/head/gfl_cls.1/Conv_output_0",
        "/head/gfl_cls.2/Conv_output_0",
        "/head/gfl_cls.3/Conv_output_0",
        OUTPUT_NAME,
    ),
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_array(value: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest()


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def parse_ids(value: str) -> list[int]:
    try:
        ids = [int(item.strip()) for item in value.split(",") if item.strip()]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--image-ids must be comma-separated integers") from exc
    if not ids or len(set(ids)) != len(ids):
        raise argparse.ArgumentTypeError("--image-ids must contain unique IDs")
    return ids


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onnx", type=Path, required=True)
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--probe", choices=tuple(PROBES), required=True)
    parser.add_argument("--image-ids", type=parse_ids, required=True)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def output_shapes(model: Any) -> dict[str, list[int]]:
    result: dict[str, list[int]] = {}
    values = list(model.graph.input) + list(model.graph.output) + list(model.graph.value_info)
    for value in values:
        if not value.type.HasField("tensor_type"):
            continue
        dims = []
        for dim in value.type.tensor_type.shape.dim:
            if not dim.dim_value:
                raise ValueError(f"dynamic or unknown dimension for {value.name!r}")
            dims.append(int(dim.dim_value))
        result[value.name] = dims
    return result


def load_reference_samples(reference_dir: Path, image_ids: list[int]) -> dict[int, tuple[dict[str, Any], np.ndarray, np.ndarray]]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from handed_nanodet_fulltest import iter_reference_samples

    samples: dict[int, tuple[dict[str, Any], np.ndarray, np.ndarray]] = {}
    for metadata, tensor, output in iter_reference_samples(
        reference_dir,
        expected_onnx_sha256=ONNX_SHA256,
        expected_annotations_sha256=ANNOTATION_SHA256,
    ):
        image_id = int(metadata["image_id"])
        if image_id in image_ids:
            samples[image_id] = (metadata, tensor, output)
    missing = sorted(set(image_ids) - set(samples))
    if missing:
        raise ValueError(f"reference pack does not contain image IDs: {missing}")
    return samples


def assert_cuda_profile(session: Any) -> dict[str, Any]:
    profile_path = Path(session.end_profiling())
    events = json.loads(profile_path.read_text(encoding="utf-8"))
    providers: dict[str, int] = {}
    for event in events:
        if event.get("cat") != "Node":
            continue
        provider = str(event.get("args", {}).get("provider", "unknown"))
        providers[provider] = providers.get(provider, 0) + 1
    if providers.get("CPUExecutionProvider", 0) or not providers.get("CUDAExecutionProvider", 0):
        raise RuntimeError(f"probe ONNX Runtime did not execute exclusively on CUDA: {providers}")
    return {"path": str(profile_path), "sha256": sha256_file(profile_path), "node_providers": providers}


def main() -> int:
    args = parse_args()
    onnx_path = args.onnx.resolve()
    reference_dir = args.reference_dir.resolve()
    output_dir = args.output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()) and not args.overwrite:
        raise FileExistsError(f"probe output exists: {output_dir}; use --overwrite or a new directory")
    if not onnx_path.is_file() or sha256_file(onnx_path) != ONNX_SHA256:
        raise ValueError("unexpected seed43 ONNX artifact")
    try:
        import onnx
        import onnxruntime as ort
    except ImportError as exc:
        raise RuntimeError("onnx and onnxruntime-gpu are required on ACE2") from exc
    if "CUDAExecutionProvider" not in ort.get_available_providers():
        raise RuntimeError(f"CUDAExecutionProvider unavailable: {ort.get_available_providers()}")

    output_dir.mkdir(parents=True, exist_ok=True)
    inferred = onnx.shape_inference.infer_shapes(onnx.load(str(onnx_path), load_external_data=False))
    shapes = output_shapes(inferred)
    selected_outputs = PROBES[args.probe]
    absent = [name for name in selected_outputs if name not in shapes]
    if absent:
        raise ValueError(f"probe outputs lack inferred static shape: {absent}")
    inferred_path = output_dir / "source-inferred.onnx"
    onnx.save(inferred, str(inferred_path))
    probe_path = output_dir / f"seed43-{args.probe}-probe.onnx"
    # Extract a real prefix graph instead of merely appending graph outputs.
    # That lets ATC dead-code-eliminate downstream branches for this boundary.
    onnx.utils.extract_model(
        str(inferred_path), str(probe_path), [INPUT_NAME], list(selected_outputs), check_model=True,
    )
    probe = onnx.load(str(probe_path), load_external_data=False)
    observed_names = [item.name for item in probe.graph.output]
    if observed_names != list(selected_outputs):
        raise ValueError(f"extracted output order mismatch: expected={selected_outputs}, actual={observed_names}")
    probe_shapes = output_shapes(probe)
    samples = load_reference_samples(reference_dir, args.image_ids)
    options = ort.SessionOptions()
    options.enable_profiling = True
    session = ort.InferenceSession(str(probe_path), sess_options=options, providers=["CUDAExecutionProvider"])
    if session.get_providers()[0] != "CUDAExecutionProvider":
        raise RuntimeError(f"CUDA not primary for probe: {session.get_providers()}")
    references_dir = output_dir / "references"
    references_dir.mkdir(exist_ok=True)
    records = []
    endpoint_checks = []
    for image_id in args.image_ids:
        metadata, tensor, original_output = samples[image_id]
        results = [np.asarray(value, dtype=np.float32) for value in session.run(observed_names, {INPUT_NAME: tensor})]
        for name, value in zip(observed_names, results):
            if list(value.shape) != probe_shapes[name] or not np.isfinite(value).all():
                raise ValueError(f"CUDA probe output contract failure for {name}: {value.shape}")
        arrays: dict[str, np.ndarray] = {"input": tensor}
        arrays.update({f"output_{index:03d}": value for index, value in enumerate(results)})
        reference_path = references_dir / f"image-{image_id}.npz"
        np.savez_compressed(reference_path, **arrays)
        record = {
            "image_id": image_id,
            "image_sha256": metadata["image_sha256"],
            "input_sha256": sha256_array(tensor),
            "reference": {"path": str(reference_path), "sha256": sha256_file(reference_path)},
            "outputs": [
                {"index": index, "name": name, "shape": list(value.shape), "dtype": str(value.dtype), "sha256": sha256_array(value)}
                for index, (name, value) in enumerate(zip(observed_names, results))
            ],
        }
        if OUTPUT_NAME in observed_names:
            endpoint = results[observed_names.index(OUTPUT_NAME)]
            error = np.abs(endpoint - original_output)
            endpoint_checks.append({
                "image_id": image_id,
                "exact_equal": bool(np.array_equal(endpoint, original_output)),
                "max_abs_error": float(error.max()),
                "mean_abs_error": float(error.mean()),
            })
        records.append(record)
    profile = assert_cuda_profile(session)
    manifest = {
        "schema_version": 1,
        "kind": "handed_nanodet_intermediate_probe",
        "created_at": now(),
        "probe": args.probe,
        "source_onnx": {"path": str(onnx_path), "sha256": ONNX_SHA256},
        "probe_onnx": {"path": str(probe_path), "sha256": sha256_file(probe_path), "bytes": probe_path.stat().st_size},
        "input": {"name": INPUT_NAME, "shape": [1, 3, 320, 320], "dtype": "float32"},
        "outputs": [
            {"index": index, "name": name, "shape": probe_shapes[name], "dtype": "float32"}
            for index, name in enumerate(observed_names)
        ],
        "samples": records,
        "cuda_profile": profile,
        "endpoint_checks_against_full_onnx": endpoint_checks,
    }
    write_json(output_dir / "manifest.json", manifest)
    print(json.dumps({
        "probe": args.probe,
        "outputs": manifest["outputs"],
        "samples": args.image_ids,
        "probe_sha256": manifest["probe_onnx"]["sha256"],
        "profile": profile["node_providers"],
        "endpoint_checks": endpoint_checks,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
