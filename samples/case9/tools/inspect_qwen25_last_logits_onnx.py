#!/usr/bin/env python3
"""Inspect the fixed-shape Qwen2.5 last-logits ONNX candidate.

The inspector has no ACL/ATC or inference dependency.  It verifies the
post-export ``ReduceSum -> Sub -> Gather`` tail and emits a separate contract
because the optimized graph's public output is ``[1, 1, V]`` rather than the
historical full ``[1, S, V]`` tensor.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple


INPUT_ORDER = ("input_ids", "attention_mask", "position_ids")
OUTPUT_NAME = "last_logits"
OPTIMIZATION_MODE = "last_logits_gather"
VOCAB_SIZE = 151936
SUPPORTED_OPS = {
    "Abs", "Add", "And", "Acos", "Asin", "Atan", "Cast", "Ceil", "Clip", "Concat",
    "Constant", "ConstantOfShape", "Cos", "CumSum", "Div", "Equal", "Erf", "Exp",
    "Expand", "Gather", "GatherElements", "GatherND", "Gemm", "Greater", "GreaterOrEqual",
    "Identity", "LayerNormalization", "Less", "LessOrEqual", "Log", "MatMul", "Max", "Min",
    "Mod", "Mul", "Neg", "Not", "Or", "Pad", "Pow", "Range", "ReduceMax", "ReduceMean",
    "ReduceMin", "ReduceSum", "Reshape", "Round", "ScatterElements", "ScatterND", "Shape",
    "Sign", "Sin", "Slice", "Softmax", "Split", "Sqrt", "Squeeze", "Sub", "Tanh", "Tile",
    "Trilu", "Transpose", "Unsqueeze", "Where", "Sigmoid",
}


class InspectionError(RuntimeError):
    """Raised when the optimized graph is not statically admissible."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_lfs_pointer(path: Path) -> bool:
    try:
        with path.open("rb") as source:
            return source.read(256).startswith(b"version https://git-lfs.github.com/spec/v1")
    except OSError:
        return False


def _metadata(model: Any) -> Dict[str, str]:
    return {item.key: item.value for item in model.metadata_props}


def _shape(value: Any) -> Tuple[int, ...]:
    dimensions = []
    for index, dimension in enumerate(value.type.tensor_type.shape.dim):
        if dimension.dim_param or not dimension.HasField("dim_value") or dimension.dim_value <= 0:
            raise InspectionError(f"{value.name} has a dynamic/non-positive dimension at {index}")
        dimensions.append(int(dimension.dim_value))
    return tuple(dimensions)


def _byte_size(dtype: str, shape: Sequence[int]) -> int:
    width = {"int64": 8, "float16": 2, "float32": 4}.get(dtype)
    if width is None:
        raise InspectionError(f"cannot determine byte size for dtype {dtype}")
    size = width
    for dimension in shape:
        size *= int(dimension)
    return size


def _producer_map(graph: Any) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for node in graph.node:
        for output in node.output:
            if output:
                if output in result:
                    raise InspectionError(f"value has multiple producers: {output}")
                result[output] = node
    return result


def inspect(
    model_path: Path,
    *,
    source_revision: Optional[str] = None,
    output: Optional[Path] = None,
    report: Optional[Path] = None,
) -> Dict[str, Any]:
    """Inspect one optimized graph and optionally write contract/report JSON."""

    model_path = model_path.expanduser().resolve()
    if not model_path.is_file():
        raise InspectionError(f"ONNX file does not exist: {model_path}")
    if _is_lfs_pointer(model_path):
        raise InspectionError("ONNX file is a Git LFS pointer")
    try:
        import onnx  # type: ignore
    except ImportError as exc:
        raise InspectionError("onnx is required for static inspection") from exc
    try:
        model = onnx.load(str(model_path), load_external_data=False)
        onnx.checker.check_model(model)
    except Exception as exc:  # noqa: BLE001 - normalize checker failures
        raise InspectionError(f"ONNX checker failed: {exc}") from exc
    graph = model.graph
    if any(item.data_location == onnx.TensorProto.EXTERNAL for item in graph.initializer):
        raise InspectionError("external initializers are not admitted")
    metadata = _metadata(model)
    if metadata.get("case9.optimization.mode") != OPTIMIZATION_MODE:
        raise InspectionError("graph does not declare the case9 last-logits optimization")
    inputs = [item for item in graph.input if item.name not in {x.name for x in graph.initializer}]
    if tuple(item.name for item in inputs) != INPUT_ORDER:
        raise InspectionError("input order must be input_ids, attention_mask, position_ids")
    input_shapes = [_shape(item) for item in inputs]
    if any(shape != input_shapes[0] for shape in input_shapes) or input_shapes[0][0] != 1:
        raise InspectionError("inputs must share static shape [1,S]")
    if any(item.type.tensor_type.elem_type != onnx.TensorProto.INT64 for item in inputs):
        raise InspectionError("all inputs must be int64")
    if len(graph.output) != 1 or graph.output[0].name != OUTPUT_NAME:
        raise InspectionError("optimized graph must expose only last_logits")
    optimized_output = graph.output[0]
    output_shape = _shape(optimized_output)
    if len(output_shape) != 3 or output_shape[:2] != (1, 1) or output_shape[2] != VOCAB_SIZE:
        raise InspectionError("last_logits must have static shape [1,1,151936]")
    if optimized_output.type.tensor_type.elem_type not in {
        onnx.TensorProto.FLOAT16,
        onnx.TensorProto.FLOAT,
    }:
        raise InspectionError("last_logits must be float16 or float32")
    op_types = [node.op_type for node in graph.node]
    unsupported = sorted(set(op_types) - SUPPORTED_OPS)
    if unsupported:
        raise InspectionError("unsupported operators: " + ", ".join(unsupported))
    producers = _producer_map(graph)
    gather = producers.get(OUTPUT_NAME)
    if gather is None or gather.op_type != "Gather" or gather.attribute[0].i != 1:
        raise InspectionError("last_logits must be produced by Gather(axis=1)")
    if len(gather.input) != 2:
        raise InspectionError("last_logits Gather must have logits and index inputs")
    sub = producers.get(gather.input[1])
    if sub is None or sub.op_type != "Sub":
        raise InspectionError("Gather index must be produced by Sub")
    reduce_sum = producers.get(sub.input[0])
    if reduce_sum is None or reduce_sum.op_type != "ReduceSum" or reduce_sum.input[0] != "attention_mask":
        raise InspectionError("Sub must consume ReduceSum(attention_mask)")
    sequence_length = input_shapes[0][1]
    source_shape = [1, sequence_length, VOCAB_SIZE]
    source_bytes = metadata.get("case9.optimization.source_bytes")
    source_sha = metadata.get("case9.optimization.source_sha256")
    contract: Dict[str, Any] = {
        "schema_version": 1,
        "model": {
            "family": "qwen2.5",
            "model_id": "qwen2.5-0.5b-instruct-static-fp16-acl-om",
            "vocabulary_size": VOCAB_SIZE,
        },
        "source_revision": source_revision or metadata.get("case9.source_revision"),
        "source_artifact": {
            "path": str(model_path),
            "bytes": model_path.stat().st_size,
            "sha256": _sha256(model_path),
        },
        "optimization": {
            "mode": OPTIMIZATION_MODE,
            "source_shape": source_shape,
            "output_shape": list(output_shape),
            "mask_rule": "non-empty binary prefix; index=sum(attention_mask)-1",
            "source_full_logits_bytes": source_bytes,
            "source_full_logits_sha256": source_sha,
        },
        "static_onnx": {
            "supported": True,
            "execution_mode": "last_logits_static",
            "batch_size": 1,
            "sequence_length": sequence_length,
            "input_order": list(INPUT_ORDER),
            "inputs": [
                {"name": item.name, "dtype": "int64", "shape": list(shape), "byte_size": _byte_size("int64", shape), "role": "input"}
                for item, shape in zip(inputs, input_shapes)
            ],
            "output": {
                "name": OUTPUT_NAME,
                "dtype": "float16"
                if optimized_output.type.tensor_type.elem_type == onnx.TensorProto.FLOAT16
                else "float32",
                "shape": list(output_shape),
                "byte_size": _byte_size(
                    "float16"
                    if optimized_output.type.tensor_type.elem_type == onnx.TensorProto.FLOAT16
                    else "float32",
                    output_shape,
                ),
                "role": "logits",
            },
            "opset": {
                (item.domain or "ai.onnx"): int(item.version) for item in model.opset_import
            },
            "operators": sorted(set(op_types)),
            "unsupported_operators": unsupported,
            "input_order_verified": True,
        },
    }
    if output is not None:
        output = output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(contract, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    if report is not None:
        report = report.expanduser().resolve()
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(
                {
                    "supported": True,
                    "metadata": metadata,
                    "inputs": contract["static_onnx"]["inputs"],
                    "output": contract["static_onnx"]["output"],
                    "optimization": contract["optimization"],
                    "artifact": contract["source_artifact"],
                },
                ensure_ascii=True,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return contract


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--source-revision")
    args = parser.parse_args(argv)
    try:
        result = inspect(
            args.model,
            source_revision=args.source_revision,
            output=args.output,
            report=args.report,
        )
    except (InspectionError, OSError) as exc:
        print(f"Qwen2.5 last-logits inspection failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
