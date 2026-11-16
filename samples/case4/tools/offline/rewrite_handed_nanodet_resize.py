#!/usr/bin/env python3
"""Replace seed43's first FPN half-pixel Resize with static arithmetic ONNX.

This is an experimental, offline graph rewrite.  It preserves the original
Resize output name so all downstream FPN and detector nodes are unchanged.  A
CUDA full-test equivalence pass is mandatory before any 310B conversion.
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
RESIZE_NAME = "/fpn/upsample/Resize"
RESIZE_INPUT = "/fpn/reduce_layers.2/act/LeakyRelu_output_0"
RESIZE_OUTPUT = "/fpn/upsample/Resize_output_0"


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
    parser.add_argument("--source-onnx", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    return parser.parse_args()


def tensor(onnx: Any, name: str, values: list[int] | list[float], dtype: int) -> Any:
    return onnx.helper.make_tensor(name, dtype, [len(values)], values)


def slice_nodes(onnx: Any, source: str, prefix: str, start: int, end: int, axis: int) -> tuple[Any, list[Any]]:
    h = onnx.helper
    output = f"{prefix}_value"
    node = h.make_node("Slice", [source, f"{prefix}_start", f"{prefix}_end", f"{prefix}_axis", "rewrite_step_1"], [output], name=f"{prefix}/Slice")
    values = [
        tensor(onnx, f"{prefix}_start", [start], onnx.TensorProto.INT64),
        tensor(onnx, f"{prefix}_end", [end], onnx.TensorProto.INT64),
        tensor(onnx, f"{prefix}_axis", [axis], onnx.TensorProto.INT64),
    ]
    return node, values


def interpolation_nodes(onnx: Any, source: str, axis: int, prefix: str) -> tuple[list[Any], list[Any], str]:
    """Build a static x2 half-pixel 1-D interpolation on the selected axis."""
    h = onnx.helper
    nodes: list[Any] = []
    inits: list[Any] = []
    axis_name = "horizontal" if axis == 3 else "vertical"
    for label, start, end in (("left", 0, 9), ("right", 1, 10), ("first", 0, 1), ("last", 9, 10)):
        node, values = slice_nodes(onnx, source, f"{prefix}_{label}", start, end, axis)
        nodes.append(node)
        inits.extend(values)
    left = f"{prefix}_left_value"
    right = f"{prefix}_right_value"
    first = f"{prefix}_first_value"
    last = f"{prefix}_last_value"
    nodes.extend([
        h.make_node("Mul", [left, "rewrite_weight_075"], [f"{prefix}_near_left"], name=f"/rewrite/{axis_name}/Mul_left_075"),
        h.make_node("Mul", [right, "rewrite_weight_025"], [f"{prefix}_near_right"], name=f"/rewrite/{axis_name}/Mul_right_025"),
        h.make_node("Add", [f"{prefix}_near_left", f"{prefix}_near_right"], [f"{prefix}_pair_first"], name=f"/rewrite/{axis_name}/Add_first"),
        h.make_node("Mul", [left, "rewrite_weight_025"], [f"{prefix}_far_left"], name=f"/rewrite/{axis_name}/Mul_left_025"),
        h.make_node("Mul", [right, "rewrite_weight_075"], [f"{prefix}_far_right"], name=f"/rewrite/{axis_name}/Mul_right_075"),
        h.make_node("Add", [f"{prefix}_far_left", f"{prefix}_far_right"], [f"{prefix}_pair_second"], name=f"/rewrite/{axis_name}/Add_second"),
    ])
    unsqueeze_axis = 4 if axis == 3 else 3
    reshape_shape = "rewrite_h_pair_shape" if axis == 3 else "rewrite_v_pair_shape"
    concat_axis = 4 if axis == 3 else 3
    nodes.extend([
        h.make_node("Unsqueeze", [f"{prefix}_pair_first"], [f"{prefix}_pair_first_u"], axes=[unsqueeze_axis], name=f"/rewrite/{axis_name}/Unsqueeze_first"),
        h.make_node("Unsqueeze", [f"{prefix}_pair_second"], [f"{prefix}_pair_second_u"], axes=[unsqueeze_axis], name=f"/rewrite/{axis_name}/Unsqueeze_second"),
        h.make_node("Concat", [f"{prefix}_pair_first_u", f"{prefix}_pair_second_u"], [f"{prefix}_pairs"], axis=concat_axis, name=f"/rewrite/{axis_name}/Concat_pairs"),
        h.make_node("Reshape", [f"{prefix}_pairs", reshape_shape], [f"{prefix}_interleaved"], name=f"/rewrite/{axis_name}/Reshape_pairs"),
        h.make_node("Concat", [first, f"{prefix}_interleaved", last], [f"{prefix}_output"], axis=axis, name=f"/rewrite/{axis_name}/Concat_output"),
    ])
    return nodes, inits, f"{prefix}_output"


def rewrite_model(onnx: Any, source_path: Path, output_path: Path) -> dict[str, Any]:
    model = onnx.load(str(source_path), load_external_data=False)
    if sha256_file(source_path) != SOURCE_ONNX_SHA256:
        raise ValueError("unexpected seed43 source ONNX")
    resize_indexes = [index for index, node in enumerate(model.graph.node) if node.name == RESIZE_NAME]
    if resize_indexes != [188]:
        raise ValueError(f"expected one first FPN Resize at node 188, got {resize_indexes}")
    original = model.graph.node[resize_indexes[0]]
    if list(original.input)[0] != RESIZE_INPUT or list(original.output) != [RESIZE_OUTPUT]:
        raise ValueError("source Resize IO contract differs")
    attributes = {attr.name: onnx.helper.get_attribute_value(attr) for attr in original.attribute}
    if attributes.get("mode") != b"linear" or attributes.get("coordinate_transformation_mode") != b"half_pixel":
        raise ValueError(f"source Resize semantics differ: {attributes}")

    h = onnx.helper
    tp = onnx.TensorProto
    h_nodes, h_inits, horizontal = interpolation_nodes(onnx, RESIZE_INPUT, 3, "rewrite_h")
    # Interpolate H directly on NCHW axis 2. This preserves the output layout
    # and uses the distinct [1,96,18,20] reshape contract for vertical pairs.
    v_nodes, v_inits, vertical = interpolation_nodes(onnx, horizontal, 2, "rewrite_v")
    v_nodes[-1].output[0] = RESIZE_OUTPUT
    replacement = [*h_nodes, *v_nodes]
    extra = [
        tensor(onnx, "rewrite_weight_025", [0.25], tp.FLOAT),
        tensor(onnx, "rewrite_weight_075", [0.75], tp.FLOAT),
        tensor(onnx, "rewrite_step_1", [1], tp.INT64),
        tensor(onnx, "rewrite_h_pair_shape", [1, 96, 10, 18], tp.INT64),
        tensor(onnx, "rewrite_v_pair_shape", [1, 96, 18, 20], tp.INT64),
        *h_inits,
        *v_inits,
    ]
    del model.graph.node[resize_indexes[0]]
    for offset, node in enumerate(replacement):
        model.graph.node.insert(resize_indexes[0] + offset, node)
    model.graph.initializer.extend(extra)
    onnx.checker.check_model(model)
    inferred = onnx.shape_inference.infer_shapes(model)
    onnx.checker.check_model(inferred)
    onnx.save(inferred, str(output_path))
    return {
        "source_resize": {"name": RESIZE_NAME, "input": RESIZE_INPUT, "output": RESIZE_OUTPUT, "attributes": {key: value.decode() if isinstance(value, bytes) else value for key, value in attributes.items()}},
        "replacement_nodes": [node.name for node in replacement],
        "output": {"path": str(output_path), "sha256": sha256_file(output_path), "bytes": output_path.stat().st_size},
    }


def main() -> int:
    args = parse_args()
    output_path = args.output.resolve()
    metadata_path = args.metadata.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = rewrite_model(__import__("onnx"), args.source_onnx.resolve(), output_path)
    metadata = {"schema_version": 1, "kind": "handed_nanodet_full_graph_resize_rewrite", "created_at": now(), **result}
    write_json(metadata_path, metadata)
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
