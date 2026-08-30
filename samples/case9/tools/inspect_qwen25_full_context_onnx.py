#!/usr/bin/env python3
"""Fail-closed static contract inspector for the Qwen2.5 full-context graph."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

MODEL_ID = "qwen2.5-0.5b-instruct-static-fp16-acl-om"
VOCAB_SIZE = 151936
INPUT_ORDER = ("input_ids", "attention_mask", "position_ids")
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
# Kept under the audit-oriented name used by the repository's static checks.
SUPPORTED_OPERATOR_TYPES = SUPPORTED_OPS


class InspectionError(RuntimeError):
    """Dynamic or symbolic dimensions, external initializers, and KV-cache values are blocked."""


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""): h.update(block)
    return h.hexdigest()


def _dtype(code: int, proto: Any) -> str:
    return {proto.INT64: "int64", proto.FLOAT16: "float16", proto.FLOAT: "float32"}.get(code, f"onnx:{code}")


def _byte_size(dtype: str, shape: Sequence[Optional[int]]) -> Optional[int]:
    if any(dimension is None or dimension <= 0 for dimension in shape):
        return None
    width = {"int64": 8, "float16": 2, "float32": 4}.get(dtype)
    if width is None:
        return None
    size = width
    for dimension in shape:
        size *= int(dimension)
    return size


def _shape(value: Any) -> Tuple[List[Optional[int]], bool]:
    dims = value.type.tensor_type.shape.dim if value.type.tensor_type.HasField("shape") else []
    out: List[Optional[int]] = []; static = True
    for dim in dims:
        if dim.HasField("dim_value"): out.append(int(dim.dim_value))
        else: out.append(None); static = False
    return out, static


def _value(value: Any, proto: Any) -> Dict[str, Any]:
    shape, static = _shape(value)
    dtype = _dtype(value.type.tensor_type.elem_type, proto)
    return {
        "name": value.name,
        "dtype": dtype,
        "shape": shape,
        "shape_static": static,
        "byte_size": _byte_size(dtype, shape),
    }


def _optimized_tail_is_valid(graph: Any, output_name: str) -> bool:
    """Check the small mask-indexing tail without executing the graph."""
    producers: Dict[str, Any] = {}
    for node in graph.graph.node:
        for value in node.output:
            if value:
                producers[value] = node
    gather = producers.get(output_name)
    if gather is None or gather.op_type != "Gather":
        return False
    axis = next((int(item.i) for item in gather.attribute if item.name == "axis"), None)
    if axis != 1 or len(gather.input) != 2:
        return False
    sub = producers.get(gather.input[1])
    if sub is None or sub.op_type != "Sub" or not sub.input:
        return False
    reduce_sum = producers.get(sub.input[0])
    return bool(
        reduce_sum is not None
        and reduce_sum.op_type == "ReduceSum"
        and reduce_sum.input
        and reduce_sum.input[0] == "attention_mask"
    )


def inspect(model_path: Path, source_revision: str, output: Path, report: Path) -> Dict[str, Any]:
    try:
        import onnx
    except ImportError as exc:
        raise InspectionError("onnx is required on the external inspector only") from exc
    path = model_path.expanduser().resolve()
    if not path.is_file(): raise InspectionError(f"model does not exist: {path}")
    try:
        graph = onnx.load(str(path), load_external_data=False)
        onnx.checker.check_model(graph)
    except Exception as exc:
        raise InspectionError(f"ONNX checker failed: {exc}") from exc
    metadata = {item.key: item.value for item in graph.metadata_props}
    initializers = {item.name for item in graph.graph.initializer}
    inputs = [_value(item, onnx.TensorProto) for item in graph.graph.input if item.name not in initializers]
    outputs = [_value(item, onnx.TensorProto) for item in graph.graph.output]
    opsets = {item.domain or "ai.onnx": int(item.version) for item in graph.opset_import}
    unsupported = sorted({f"{node.domain or 'ai.onnx'}:{node.op_type}" for node in graph.graph.node if (node.domain or "ai.onnx") != "ai.onnx" or node.op_type not in SUPPORTED_OPS})
    optimized = metadata.get("case9.optimization.mode") == "last_logits_gather"
    expected_output_name = "last_logits" if optimized else "logits"
    reasons: List[str] = []
    if len(inputs) != 3 or [x["name"] for x in inputs] != list(INPUT_ORDER): reasons.append("input order/count mismatch")
    if inputs and any(x["dtype"] != "int64" or x["shape_static"] is not True or x["shape"][0] != 1 or x["shape"] != inputs[0]["shape"] for x in inputs): reasons.append("inputs are not identical static int64 [1,S]")
    if len(outputs) != 1 or outputs[0]["name"] != expected_output_name:
        reasons.append(
            "output must be one last_logits tensor"
            if optimized
            else "output must be one logits tensor"
        )
    expected_output_shape = [1, 1, VOCAB_SIZE] if optimized else (
        [1, inputs[0]["shape"][1], VOCAB_SIZE] if inputs else None
    )
    if outputs and (
        outputs[0]["dtype"] not in {"float16", "float32"}
        or outputs[0]["shape_static"] is not True
        or outputs[0]["shape"] != expected_output_shape
    ):
        reasons.append(
            "last_logits shape/dtype mismatch"
            if optimized
            else "logits shape/dtype mismatch"
        )
    if optimized:
        if not _optimized_tail_is_valid(graph, expected_output_name):
            reasons.append("last_logits output is not the admitted ReduceSum/Sub/Gather tail")
        if metadata.get("case9.optimization.mask_rule") != "attention_mask is a non-empty binary prefix; index=sum(mask)-1":
            reasons.append("optimization metadata does not prove the prefix-mask index rule")
    if any(any(marker in x["name"].lower() for marker in ("past", "present", "key_values", "kv_cache")) for x in inputs + outputs): reasons.append("KV-cache tensor names are not admitted (past/KV-cache values are present)")
    if unsupported: reasons.append("unsupported operators: " + ", ".join(unsupported))
    if any(not x["shape_static"] for x in inputs + outputs): reasons.append("dynamic or symbolic dimensions are present")
    if any(init.data_location == onnx.TensorProto.EXTERNAL for init in graph.graph.initializer): reasons.append("external initializers are not supported")
    opset = opsets.get("ai.onnx")
    if opset is None or not 13 <= opset <= 18: reasons.append(f"opset {opset} is outside 13..18")
    precision = metadata.get("case9.export.precision")
    if precision != "fp16": reasons.append(f"export precision metadata is {precision!r}, expected 'fp16'")
    if metadata.get("case9.export.dynamic_axes") != "none": reasons.append("export metadata does not prove dynamic_axes=none")
    supported = not reasons
    seq = inputs[0]["shape"][1] if inputs and inputs[0]["shape"] else None
    logits_dtype = outputs[0]["dtype"] if outputs else "float16"
    execution_mode = "last_logits_static" if optimized else "full_context_static"
    contract: Dict[str, Any] = {
        "schema_version": 1,
        "model": {"family": "qwen2.5", "model_id": MODEL_ID, "eos_token_id": None, "pad_token_id": None, "bos_token_id": None},
        "acl_om": {
            "execution_mode": execution_mode, "static_sequence_length": seq,
            "input_dtype": "int64", "logits_dtype": logits_dtype, "precision": logits_dtype,
            "input_order": list(INPUT_ORDER), "input_order_verified": [x["name"] for x in inputs] == list(INPUT_ORDER),
            "inputs": [{"name": x["name"], "dtype": x["dtype"], "shape": x["shape"], "byte_size": x["byte_size"], "role": "input"} for x in inputs],
            "outputs": [{"name": x["name"], "dtype": x["dtype"], "shape": x["shape"], "byte_size": x["byte_size"], "role": "logits"} for x in outputs],
            "vocabulary_size": VOCAB_SIZE, "operator_audit": {"opset": opset, "unsupported_operators": unsupported},
        },
        "source_revision": source_revision,
        "source_artifact": {"bytes": path.stat().st_size, "sha256": _sha256(path)},
        "static_onnx": {"supported": supported, "execution_mode": execution_mode, "support_reason": ("exact static last-logits contract matched" if optimized else "exact static full-context contract matched") if supported else "; ".join(reasons), "inputs": inputs, "outputs": outputs, "opset": opset, "unsupported_operators": unsupported},
    }
    output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(contract, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    report.parent.mkdir(parents=True, exist_ok=True); report.write_text(json.dumps({"supported": supported, "reasons": reasons, "metadata": metadata, "inputs": inputs, "outputs": outputs, "opsets": opsets, "artifact": contract["source_artifact"]}, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
    return contract


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=Path); parser.add_argument("--output", required=True, type=Path); parser.add_argument("--report", required=True, type=Path); parser.add_argument("--source-revision", required=True)
    args = parser.parse_args(argv)
    try:
        result = inspect(args.model, args.source_revision, args.output, args.report); print(json.dumps(result, ensure_ascii=True, indent=2)); return 0 if result["static_onnx"]["supported"] else 2
    except (InspectionError, OSError) as exc:
        print(f"inspection failed: {exc}", file=sys.stderr); return 1


if __name__ == "__main__": raise SystemExit(main())
