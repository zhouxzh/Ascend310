#!/usr/bin/env python3
"""Build a static arithmetic equivalent of seed43's first half-pixel Resize.

The source Resize scales a fixed ``[1,96,10,10]`` tensor to ``[1,96,20,20]``
with linear interpolation and ``half_pixel`` coordinates.  This tool replaces
that single operator with static Slice/Mul/Add/Concat operations.  It refuses
to publish board references unless CUDA ONNX Runtime confirms the rewritten
ONNX matches the original Resize outputs for every selected fixed tensor.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

SOURCE_ONNX_SHA256 = "6a0e07fdb2e5a92776ae09a556f6845387acf84732212f9d4428bdb60959124c"
INPUT_NAME = "/fpn/reduce_layers.2/act/LeakyRelu_output_0"
OUTPUT_NAME = "/fpn/upsample/Resize_output_0"
INPUT_SHAPE = [1, 96, 10, 10]
OUTPUT_SHAPE = [1, 96, 20, 20]


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
    parser.add_argument("--source-manifest", type=Path, required=True)
    parser.add_argument("--source-reference-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--atol", type=float, default=2e-6)
    parser.add_argument("--rtol", type=float, default=2e-6)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def initializer(onnx: Any, name: str, values: list[int] | list[float], dtype: int) -> Any:
    return onnx.helper.make_tensor(name, dtype, [len(values)], values)


def slice_node(onnx: Any, source: str, output: str, *, start: int, end: int, axis: int, name: str) -> Any:
    return onnx.helper.make_node(
        "Slice",
        [source, f"{name}_start", f"{name}_end", f"{name}_axis", "step_1"],
        [output],
        name=name,
    )


def slice_initializers(onnx: Any, name: str, *, start: int, end: int, axis: int) -> list[Any]:
    return [
        initializer(onnx, f"{name}_start", [start], onnx.TensorProto.INT64),
        initializer(onnx, f"{name}_end", [end], onnx.TensorProto.INT64),
        initializer(onnx, f"{name}_axis", [axis], onnx.TensorProto.INT64),
    ]


def build_rewrite(onnx: Any, output_path: Path) -> None:
    """Build exact static x2 half-pixel bilinear resize with edge replication."""
    h = onnx.helper
    tp = onnx.TensorProto
    input_info = h.make_tensor_value_info(INPUT_NAME, tp.FLOAT, INPUT_SHAPE)
    output_info = h.make_tensor_value_info(OUTPUT_NAME, tp.FLOAT, OUTPUT_SHAPE)
    nodes: list[Any] = []
    initializers: list[Any] = [
        initializer(onnx, "weight_025", [0.25], tp.FLOAT),
        initializer(onnx, "weight_075", [0.75], tp.FLOAT),
        initializer(onnx, "step_1", [1], tp.INT64),
        initializer(onnx, "h_pair_shape", [1, 96, 10, 18], tp.INT64),
        initializer(onnx, "v_pair_shape", [1, 96, 18, 20], tp.INT64),
    ]

    # Horizontal: endpoints, then each adjacent pair contributes positions
    # 0.25 and 0.75 between its two source pixels.
    for label, start, end in (("h_left", 0, 9), ("h_right", 1, 10), ("h_first", 0, 1), ("h_last", 9, 10)):
        nodes.append(slice_node(onnx, INPUT_NAME, f"{label}_value", start=start, end=end, axis=3, name=f"/rewrite/{label}/Slice"))
        initializers.extend(slice_initializers(onnx, f"/rewrite/{label}/Slice", start=start, end=end, axis=3))
    nodes.extend([
        h.make_node("Mul", ["h_left_value", "weight_075"], ["h_near_left"], name="/rewrite/horizontal/Mul_left_075"),
        h.make_node("Mul", ["h_right_value", "weight_025"], ["h_near_right"], name="/rewrite/horizontal/Mul_right_025"),
        h.make_node("Add", ["h_near_left", "h_near_right"], ["h_pair_first"], name="/rewrite/horizontal/Add_first"),
        h.make_node("Mul", ["h_left_value", "weight_025"], ["h_far_left"], name="/rewrite/horizontal/Mul_left_025"),
        h.make_node("Mul", ["h_right_value", "weight_075"], ["h_far_right"], name="/rewrite/horizontal/Mul_right_075"),
        h.make_node("Add", ["h_far_left", "h_far_right"], ["h_pair_second"], name="/rewrite/horizontal/Add_second"),
        h.make_node("Unsqueeze", ["h_pair_first"], ["h_pair_first_u"], axes=[4], name="/rewrite/horizontal/Unsqueeze_first"),
        h.make_node("Unsqueeze", ["h_pair_second"], ["h_pair_second_u"], axes=[4], name="/rewrite/horizontal/Unsqueeze_second"),
        h.make_node("Concat", ["h_pair_first_u", "h_pair_second_u"], ["h_pairs"], axis=4, name="/rewrite/horizontal/Concat_pairs"),
        h.make_node("Reshape", ["h_pairs", "h_pair_shape"], ["h_interleaved"], name="/rewrite/horizontal/Reshape_pairs"),
        h.make_node("Concat", ["h_first_value", "h_interleaved", "h_last_value"], ["h_output"], axis=3, name="/rewrite/horizontal/Concat_output"),
    ])

    # Vertical repeats the same interpolation over the horizontally resized map.
    for label, start, end in (("v_top", 0, 9), ("v_bottom", 1, 10), ("v_first", 0, 1), ("v_last", 9, 10)):
        nodes.append(slice_node(onnx, "h_output", f"{label}_value", start=start, end=end, axis=2, name=f"/rewrite/{label}/Slice"))
        initializers.extend(slice_initializers(onnx, f"/rewrite/{label}/Slice", start=start, end=end, axis=2))
    nodes.extend([
        h.make_node("Mul", ["v_top_value", "weight_075"], ["v_near_top"], name="/rewrite/vertical/Mul_top_075"),
        h.make_node("Mul", ["v_bottom_value", "weight_025"], ["v_near_bottom"], name="/rewrite/vertical/Mul_bottom_025"),
        h.make_node("Add", ["v_near_top", "v_near_bottom"], ["v_pair_first"], name="/rewrite/vertical/Add_first"),
        h.make_node("Mul", ["v_top_value", "weight_025"], ["v_far_top"], name="/rewrite/vertical/Mul_top_025"),
        h.make_node("Mul", ["v_bottom_value", "weight_075"], ["v_far_bottom"], name="/rewrite/vertical/Mul_bottom_075"),
        h.make_node("Add", ["v_far_top", "v_far_bottom"], ["v_pair_second"], name="/rewrite/vertical/Add_second"),
        h.make_node("Unsqueeze", ["v_pair_first"], ["v_pair_first_u"], axes=[3], name="/rewrite/vertical/Unsqueeze_first"),
        h.make_node("Unsqueeze", ["v_pair_second"], ["v_pair_second_u"], axes=[3], name="/rewrite/vertical/Unsqueeze_second"),
        h.make_node("Concat", ["v_pair_first_u", "v_pair_second_u"], ["v_pairs"], axis=3, name="/rewrite/vertical/Concat_pairs"),
        h.make_node("Reshape", ["v_pairs", "v_pair_shape"], ["v_interleaved"], name="/rewrite/vertical/Reshape_pairs"),
        h.make_node("Concat", ["v_first_value", "v_interleaved", "v_last_value"], [OUTPUT_NAME], axis=2, name="/rewrite/vertical/Concat_output"),
    ])
    graph = h.make_graph(nodes, "seed43_half_pixel_resize_rewrite", [input_info], [output_info], initializers)
    model = h.make_model(graph, producer_name="ascend310b-seed43-diagnostic", opset_imports=[h.make_operatorsetid("", 11)])
    model.ir_version = 7
    onnx.checker.check_model(model)
    inferred = onnx.shape_inference.infer_shapes(model)
    onnx.checker.check_model(inferred)
    onnx.save(inferred, str(output_path))


def main() -> int:
    args = parse_args()
    source_manifest_path = args.source_manifest.resolve()
    source_reference_dir = args.source_reference_dir.resolve()
    output_dir = args.output_dir.resolve()
    if output_dir.exists() and any(output_dir.iterdir()) and not args.overwrite:
        raise FileExistsError(f"output exists: {output_dir}")
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if source_manifest.get("kind") != "handed_nanodet_fpn_resize_isolation":
        raise ValueError("unsupported source Resize manifest")
    if source_manifest.get("isolated_onnx", {}).get("sha256") != SOURCE_ONNX_SHA256:
        raise ValueError("source Resize ONNX SHA mismatch")
    try:
        import onnx
        import onnxruntime as ort
    except ImportError as exc:  # pragma: no cover - ACE2-only dependency
        raise RuntimeError("onnx and onnxruntime-gpu are required") from exc
    if "CUDAExecutionProvider" not in ort.get_available_providers():
        raise RuntimeError("CUDAExecutionProvider unavailable")
    output_dir.mkdir(parents=True, exist_ok=True)
    rewrite_path = output_dir / "seed43-fpn-resize1-static-arithmetic.onnx"
    build_rewrite(onnx, rewrite_path)
    options = ort.SessionOptions()
    options.enable_profiling = True
    session = ort.InferenceSession(str(rewrite_path), sess_options=options, providers=["CUDAExecutionProvider"])
    if session.get_providers()[0] != "CUDAExecutionProvider":
        raise RuntimeError(f"CUDA is not primary: {session.get_providers()}")
    references_dir = output_dir / "references"
    references_dir.mkdir(exist_ok=True)
    samples = []
    for source in source_manifest["samples"]:
        image_id = int(source["image_id"])
        source_reference = source_reference_dir / Path(source["reference"]["path"]).name
        if not source_reference.is_file() or sha256_file(source_reference) != source["reference"]["sha256"]:
            raise ValueError(f"source reference mismatch for image {image_id}")
        with np.load(source_reference, allow_pickle=False) as data:
            input_tensor = np.ascontiguousarray(data["input"], dtype=np.float32)
            expected = np.ascontiguousarray(data["output_000"], dtype=np.float32)
        actual = np.asarray(session.run([OUTPUT_NAME], {INPUT_NAME: input_tensor})[0], dtype=np.float32)
        error = np.abs(actual - expected)
        if not np.allclose(actual, expected, atol=args.atol, rtol=args.rtol, equal_nan=False):
            raise ValueError(f"rewrite is not CUDA-equivalent for image {image_id}: max={error.max()}, mean={error.mean()}")
        reference_path = references_dir / f"image-{image_id}.npz"
        np.savez_compressed(reference_path, input=input_tensor, output_000=expected)
        samples.append({
            "image_id": image_id,
            "input_sha256": sha256_array(input_tensor),
            "output_sha256": sha256_array(expected),
            "cuda_rewrite_vs_original": {"max_abs_error": float(error.max()), "mean_abs_error": float(error.mean()), "exact_equal": bool(np.array_equal(actual, expected))},
            "reference": {"path": str(reference_path), "sha256": sha256_file(reference_path)},
        })
    profile_path = Path(session.end_profiling())
    events = json.loads(profile_path.read_text(encoding="utf-8"))
    providers: dict[str, int] = {}
    for event in events:
        if event.get("cat") == "Node":
            provider = str(event.get("args", {}).get("provider", "unknown"))
            providers[provider] = providers.get(provider, 0) + 1
    if providers.get("CPUExecutionProvider", 0) or not providers.get("CUDAExecutionProvider", 0):
        raise RuntimeError(f"rewrite did not execute exclusively on CUDA: {providers}")
    manifest = {
        "schema_version": 1,
        "kind": "handed_nanodet_fpn_resize_static_arithmetic_rewrite",
        "created_at": now(),
        "source_resize": source_manifest["isolated_onnx"],
        "semantics": "fixed x2 half_pixel linear interpolation with edge replication, represented as Slice/Mul/Add/Concat",
        "input": {"name": INPUT_NAME, "shape": INPUT_SHAPE, "dtype": "float32"},
        "output": {"name": OUTPUT_NAME, "shape": OUTPUT_SHAPE, "dtype": "float32"},
        "rewritten_onnx": {"path": str(rewrite_path), "sha256": sha256_file(rewrite_path), "bytes": rewrite_path.stat().st_size},
        "cuda_profile": {"path": str(profile_path), "sha256": sha256_file(profile_path), "node_providers": providers},
        "equivalence_tolerance": {"atol": args.atol, "rtol": args.rtol},
        "samples": samples,
    }
    write_json(output_dir / "manifest.json", manifest)
    print(json.dumps({
        "rewritten_onnx": manifest["rewritten_onnx"],
        "samples": [{"image_id": s["image_id"], "comparison": s["cuda_rewrite_vs_original"]} for s in samples],
        "profile": providers,
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
