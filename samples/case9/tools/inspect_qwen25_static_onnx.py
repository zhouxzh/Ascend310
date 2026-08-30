#!/usr/bin/env python3
"""Inspect a fixed static-KV Qwen2.5 CPU-FP32 ONNX export.

The inspector is independent of PyTorch, Transformers, ACL, CANN, and ONNX
Runtime. It parses the graph with the ``onnx`` protobuf API only, writes a
contract compatible with ``qwen25_kv_acl_contract.py``, and returns a blocking
status for dynamic dimensions, external initializers, KV/layout mismatches,
wrong dtypes/shapes, or operators outside the reviewed standard-ONNX set.
It never executes the graph and never modifies the model file.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple


MODEL_FAMILY = "qwen2.5"
MODEL_ID = "qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om"
EXECUTION_MODE = "static_kv_token_fp32"
BATCH_SIZE = 1
SEQUENCE_LENGTH = 1024
MASK_LENGTH = SEQUENCE_LENGTH
VOCABULARY_SIZE = 151936
NUM_LAYERS = 24
NUM_KV_HEADS = 2
HEAD_DIM = 64
SPLIT_KV_SHAPE = (BATCH_SIZE, NUM_KV_HEADS, SEQUENCE_LENGTH, HEAD_DIM)
TOKEN_SPLIT_KV_SHAPE = (BATCH_SIZE, 1, NUM_KV_HEADS, HEAD_DIM)
EXPECTED_BASE_INPUT_ORDER = ("input_ids", "attention_mask", "position_ids")
DEFAULT_CACHE_LAYOUT = "split"
MIN_SUPPORTED_OPSET = 13
MAX_SUPPORTED_OPSET = 18

SUPPORTED_OPERATOR_TYPES = {
    "Abs", "Acos", "Add", "And", "Asin", "Atan", "Cast", "Ceil", "Clip",
    "Concat", "Constant", "ConstantOfShape", "Cos", "CumSum", "Div", "Equal",
    "Erf", "Exp", "Expand", "Gather", "GatherElements", "GatherND", "Gemm",
    "Greater", "GreaterOrEqual", "Identity", "LayerNormalization", "Less",
    "LessOrEqual", "Log", "MatMul", "Max", "Min", "Mod", "Mul", "Neg", "Not",
    "Or", "Pad", "Pow", "Range", "ReduceMax", "ReduceMean", "ReduceMin",
    "ReduceSum", "Reshape", "Round", "ScatterElements", "ScatterND", "Shape",
    "Sign", "Sigmoid", "Sin", "Slice", "Softmax", "Split", "Sqrt", "Squeeze", "Sub",
    "Tanh", "Tile", "Trilu", "Transpose", "Unsqueeze", "Where",
}


class InspectionError(RuntimeError):
    """Raised when an ONNX file cannot be parsed or checked."""


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


def _dtype_name(element_type: int, tensor_proto: Any) -> str:
    names = {
        int(tensor_proto.INT32): "int32",
        int(tensor_proto.INT64): "int64",
        int(tensor_proto.FLOAT16): "float16",
        int(tensor_proto.FLOAT): "float32",
        int(tensor_proto.UINT8): "uint8",
        int(tensor_proto.INT8): "int8",
        int(tensor_proto.BOOL): "bool",
        int(getattr(tensor_proto, "BFLOAT16", 16)): "bfloat16",
    }
    return names.get(int(element_type), f"onnx_type_{int(element_type)}")


def _shape(value_info: Any) -> Tuple[List[Optional[int]], bool]:
    tensor_type = value_info.type.tensor_type
    if not tensor_type.HasField("shape"):
        return [], False
    values: List[Optional[int]] = []
    static = True
    for dimension in tensor_type.shape.dim:
        if dimension.HasField("dim_value"):
            values.append(int(dimension.dim_value))
        else:
            values.append(None)
            static = False
    return values, static


def _value_info(value: Any, tensor_proto: Any) -> Dict[str, Any]:
    shape, static = _shape(value)
    dim_params: List[Optional[str]] = []
    if value.type.tensor_type.HasField("shape"):
        for dimension in value.type.tensor_type.shape.dim:
            dim_params.append(str(dimension.dim_param) if dimension.HasField("dim_param") else None)
    return {
        "name": str(value.name),
        "dtype": _dtype_name(value.type.tensor_type.elem_type, tensor_proto),
        "shape": shape,
        "shape_static": static,
        "dim_params": dim_params,
    }


def _metadata(model: Any) -> Dict[str, str]:
    return {str(item.key): str(item.value) for item in model.metadata_props}


def _external_initializers(graph: Any, tensor_proto: Any) -> List[str]:
    result = []
    external_location = int(getattr(tensor_proto, "EXTERNAL", 1))
    for initializer in graph.initializer:
        if int(getattr(initializer, "data_location", 0)) == external_location:
            result.append(str(initializer.name))
            continue
        if any(str(entry.key) == "location" for entry in initializer.external_data):
            result.append(str(initializer.name))
    return result


def _load_onnx(path: Path) -> Tuple[Any, Any]:
    if not path.is_file():
        raise InspectionError(f"ONNX file does not exist: {path}")
    if _is_lfs_pointer(path):
        raise InspectionError("the model is a Git LFS pointer, not an ONNX artifact")
    try:
        import onnx  # type: ignore
    except ImportError as exc:
        raise InspectionError(
            "the active inspection environment has no 'onnx' package; install the external CPU requirements"
        ) from exc
    try:
        # Do not read arbitrary sidecars while auditing the artifact.
        model = onnx.load(str(path), load_external_data=False)
        onnx.checker.check_model(model)
    except Exception as exc:  # noqa: BLE001 - normalize protobuf/checker errors
        raise InspectionError(f"ONNX checker failed: {exc}") from exc
    return onnx, model


def _dtype_bytes(dtype: str) -> int:
    return {"int64": 8, "float32": 4, "float16": 2, "int32": 4}.get(dtype, 0)


def _byte_size(info: Dict[str, Any]) -> Optional[int]:
    width = _dtype_bytes(str(info["dtype"]))
    shape = info.get("shape")
    if not width or not isinstance(shape, list) or any(item is None for item in shape):
        return None
    result = width
    for item in shape:
        result *= int(item)
    return result


def _descriptor(
    info: Dict[str, Any],
    *,
    role: Optional[str] = None,
    cache_index: Optional[int] = None,
    cache_part: Optional[str] = None,
    cache_update: Optional[str] = None,
) -> Dict[str, Any]:
    result: Dict[str, Any] = {
        "name": info["name"],
        "dtype": info["dtype"],
        "shape": list(info["shape"]),
    }
    size = _byte_size(info)
    if size is not None:
        result["byte_size"] = size
    if role is not None:
        result["role"] = role
    if cache_index is not None:
        result["cache_index"] = cache_index
    if cache_part is not None:
        result["cache_part"] = cache_part
    if cache_update is not None:
        result["cache_update"] = cache_update
    return result


def _expected_cache(layout: str) -> Tuple[Tuple[int, ...], Tuple[int, ...], int]:
    if layout == "split":
        return SPLIT_KV_SHAPE, TOKEN_SPLIT_KV_SHAPE, NUM_LAYERS * 2
    raise InspectionError("the 1024 StaticCache inspector admits split layout only")


def _expected_cache_names(*, output: bool) -> Tuple[str, ...]:
    prefix = "present" if output else "past_key_values"
    return tuple(
        f"{prefix}.{layer}.{part}"
        for layer in range(NUM_LAYERS)
        for part in ("key", "value")
    )


def _contract(
    *,
    supported: bool,
    reason: str,
    source_bytes: int,
    source_sha256: str,
    source_revision: str,
    cache_layout: str,
    inputs: Sequence[Dict[str, Any]],
    outputs: Sequence[Dict[str, Any]],
    input_order_verified: bool,
    opset: Optional[int],
    unsupported_operators: Sequence[str],
) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "model": {
            "family": MODEL_FAMILY,
            "model_id": MODEL_ID,
            "eos_token_id": 151645,
            "pad_token_id": 151643,
            "bos_token_id": None,
        },
        "acl_om": {
            "execution_mode": EXECUTION_MODE,
            "supported_static_qwen25_layout": bool(supported),
            "support_reason": reason,
            "static_sequence_length": SEQUENCE_LENGTH,
            "mask_length": MASK_LENGTH,
            "cache_layout": cache_layout,
            "cache_shape": list(SPLIT_KV_SHAPE),
            "input_order": [item["name"] for item in inputs],
            "input_order_verified": bool(input_order_verified),
            "inputs": list(inputs),
            "outputs": list(outputs),
            "vocabulary_size": VOCABULARY_SIZE,
            "operator_audit": {
                "opset": opset,
                "unsupported_operators": list(unsupported_operators),
            },
        },
        "source_revision": source_revision,
        "source_artifact": {"bytes": source_bytes, "sha256": source_sha256},
    }


def inspect(
    model_path: Path,
    source_revision: Optional[str] = None,
    *,
    cache_layout: str = DEFAULT_CACHE_LAYOUT,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Return ``(contract, report)`` without executing or modifying the graph."""

    if cache_layout != "split":
        raise InspectionError("cache_layout must be split for the 1024 StaticCache graph")
    path = Path(model_path).expanduser().resolve()
    onnx, graph_model = _load_onnx(path)
    metadata = _metadata(graph_model)
    if source_revision is None:
        source_revision = metadata.get("case9.source_revision")
    if not isinstance(source_revision, str) or not source_revision.strip():
        raise InspectionError("an immutable source revision is required")
    source_revision = source_revision.strip()
    full_cache_shape, token_cache_shape, cache_count = _expected_cache(cache_layout)

    initializer_names = {str(item.name) for item in graph_model.graph.initializer}
    graph_inputs = [item for item in graph_model.graph.input if item.name not in initializer_names]
    observed_inputs = [_value_info(item, onnx.TensorProto) for item in graph_inputs]
    observed_outputs = [_value_info(item, onnx.TensorProto) for item in graph_model.graph.output]
    all_value_infos = tuple(graph_inputs) + tuple(graph_model.graph.output) + tuple(graph_model.graph.value_info)
    dynamic_values = [item.name for item in all_value_infos if not _shape(item)[1]]
    external_initializers = _external_initializers(graph_model.graph, onnx.TensorProto)
    op_types = sorted({str(node.op_type) for node in graph_model.graph.node})
    unsupported_operators = sorted(
        {
            f"{node.domain or 'ai.onnx'}:{node.op_type}"
            for node in graph_model.graph.node
            if (node.domain or "ai.onnx") != "ai.onnx" or node.op_type not in SUPPORTED_OPERATOR_TYPES
        }
    )
    opsets = {(str(item.domain or "ai.onnx")): int(item.version) for item in graph_model.opset_import}
    onnx_opset = opsets.get("ai.onnx")
    reasons: List[str] = []
    if onnx_opset is None:
        reasons.append("the graph has no ai.onnx opset declaration")
    elif not MIN_SUPPORTED_OPSET <= onnx_opset <= MAX_SUPPORTED_OPSET:
        reasons.append(
            f"ai.onnx opset {onnx_opset} is outside the admitted range "
            f"[{MIN_SUPPORTED_OPSET}, {MAX_SUPPORTED_OPSET}]"
        )
    if dynamic_values:
        reasons.append(f"dynamic or symbolic dimensions are present: {dynamic_values!r}")
    if external_initializers:
        reasons.append("external initializers are not supported")
    input_names = [item["name"] for item in observed_inputs]
    if tuple(input_names[:3]) != EXPECTED_BASE_INPUT_ORDER:
        reasons.append(
            f"base input order is {input_names[:3]!r}; expected {list(EXPECTED_BASE_INPUT_ORDER)!r}"
        )
    base_infos = {item["name"]: item for item in observed_inputs[:3]}
    expected_base = {
        "input_ids": ("int64", [BATCH_SIZE, 1]),
        "attention_mask": ("int64", [BATCH_SIZE, MASK_LENGTH]),
        "position_ids": ("int64", [BATCH_SIZE, 1]),
    }
    for name, (dtype, shape) in expected_base.items():
        item = base_infos.get(name)
        if item is None or item["dtype"] != dtype or item["shape"] != shape:
            reasons.append(f"input {name} does not match {dtype} {shape}")
    cache_infos = observed_inputs[3:]
    if len(cache_infos) != cache_count:
        reasons.append(f"cache input count is {len(cache_infos)}; expected {cache_count}")
    expected_cache_inputs = _expected_cache_names(output=False)
    observed_cache_names = [item["name"] for item in cache_infos]
    if observed_cache_names != list(expected_cache_inputs):
        reasons.append(
            "cache input order/names must be layer -> key,value: "
            f"observed {observed_cache_names[:4]!r}..."
        )
    for item in cache_infos:
        if item["dtype"] != "float32" or item["shape"] != list(full_cache_shape):
            reasons.append(f"cache input {item['name']} is not FP32 {list(full_cache_shape)}")
    output_names = [item["name"] for item in observed_outputs]
    logits_candidates = [
        item
        for item in observed_outputs
        if item["name"] == "logits"
        and item["dtype"] == "float32"
        and item["shape"] == [BATCH_SIZE, 1, VOCABULARY_SIZE]
    ]
    if len(logits_candidates) != 1:
        reasons.append("outputs must contain exactly one FP32 logits [1,1,151936] tensor named logits")
    cache_outputs = [item for item in observed_outputs if item["name"] != "logits"]
    if len(cache_outputs) != cache_count:
        reasons.append(f"cache output count is {len(cache_outputs)}; expected {cache_count}")
    expected_cache_outputs = _expected_cache_names(output=True)
    observed_cache_outputs = [item["name"] for item in cache_outputs]
    if observed_cache_outputs != list(expected_cache_outputs):
        reasons.append(
            "cache output order/names must be layer -> key,value: "
            f"observed {observed_cache_outputs[:4]!r}..."
        )
    for item in cache_outputs:
        if item["dtype"] != "float32" or item["shape"] != list(token_cache_shape):
            reasons.append(f"cache output {item['name']} is not FP32 {list(token_cache_shape)}")
    quantization_ops = sorted(
        op for op in op_types if op in {"MatMulInteger", "DynamicQuantizeLinear", "QLinearMatMul"}
    )
    if quantization_ops:
        reasons.append(f"quantization operators require explicit audit: {quantization_ops!r}")
    if unsupported_operators:
        reasons.append(f"unsupported operators require ACL audit: {unsupported_operators!r}")
    metadata_warnings: List[str] = []
    expected_metadata = {
        "case9.model_id": MODEL_ID,
        "case9.execution_mode": EXECUTION_MODE,
        "case9.export.device": "cpu",
        "case9.export.precision": "fp32",
        "case9.export.dynamic_axes": "none",
        "case9.export.cache_layout": cache_layout,
        "case9.export.static_sequence_length": str(SEQUENCE_LENGTH),
        "case9.export.mask_length": str(MASK_LENGTH),
        "case9.export.num_logits_to_keep": "1",
    }
    for key, expected in expected_metadata.items():
        actual = metadata.get(key)
        if actual is not None and actual != expected:
            metadata_warnings.append(f"metadata {key} is {actual!r}, expected {expected!r}")
    supported = not reasons and not metadata_warnings
    reason = "exact static-KV token FP32 contract matched" if supported else "; ".join(reasons + metadata_warnings)

    marked_inputs: List[Dict[str, Any]] = []
    for index, item in enumerate(observed_inputs):
        if index < 3:
            marked_inputs.append(_descriptor(item))
        else:
            cache_index = index - 3
            part = "key" if cache_index % 2 == 0 else "value"
            marked_inputs.append(
                _descriptor(item, role="kv_cache", cache_index=cache_index, cache_part=part)
            )
    marked_outputs: List[Dict[str, Any]] = []
    for item in observed_outputs:
        if item["name"] == "logits":
            marked_outputs.append(_descriptor(item, role="logits"))
        else:
            cache_index = len([value for value in marked_outputs if value.get("role") == "kv_cache"])
            part = "key" if cache_index % 2 == 0 else "value"
            marked_outputs.append(
                _descriptor(
                    item,
                    role="kv_cache",
                    cache_index=cache_index,
                    cache_part=part,
                    cache_update="token",
                )
            )
    source_bytes = path.stat().st_size
    source_sha256 = _sha256(path)
    contract = _contract(
        supported=supported,
        reason=reason,
        source_bytes=source_bytes,
        source_sha256=source_sha256,
        source_revision=source_revision,
        cache_layout=cache_layout,
        inputs=marked_inputs,
        outputs=marked_outputs,
        input_order_verified=tuple(input_names[:3]) == EXPECTED_BASE_INPUT_ORDER,
        opset=onnx_opset,
        unsupported_operators=unsupported_operators,
    )
    # A compact alias keeps generic static-ONNX tooling useful while acl_om is
    # the canonical contract consumed by the Qwen2.5 runtime.
    contract["static_onnx"] = {
        "supported": bool(supported),
        "support_reason": reason,
        "execution_mode": "token_with_static_kv",
        "device": "cpu",
        "precision": "fp32",
        "batch_size": BATCH_SIZE,
        "sequence_length": SEQUENCE_LENGTH,
        "mask_length": MASK_LENGTH,
        "cache_layout": cache_layout,
        "cache_input_shape": list(full_cache_shape),
        "cache_output_shape": list(token_cache_shape),
        "input_order": input_names,
        "outputs": output_names,
    }
    report: Dict[str, Any] = {
        "schema_version": 1,
        "status": "admitted" if supported else "blocked",
        "model_path": str(path),
        "bytes": source_bytes,
        "sha256": source_sha256,
        "source_revision": source_revision,
        "metadata": metadata,
        "onnx_ir_version": int(graph_model.ir_version),
        "opsets": opsets,
        "graph_name": str(graph_model.graph.name),
        "node_count": len(graph_model.graph.node),
        "op_types": op_types,
        "unsupported_operators": unsupported_operators,
        "quantization_operators": quantization_ops,
        "external_initializers": external_initializers,
        "dynamic_values": dynamic_values,
        "inputs": observed_inputs,
        "outputs": observed_outputs,
        "contract": contract,
    }
    return contract, report


def _write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as output:
        json.dump(value, output, ensure_ascii=True, indent=2, sort_keys=True)
        output.write("\n")
    os.replace(temporary, path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=Path, help="ONNX file to inspect")
    parser.add_argument("--output", required=True, type=Path, help="Qwen2.5 ACL contract JSON")
    parser.add_argument("--report", required=True, type=Path, help="detailed inspection report JSON")
    parser.add_argument("--source-revision", required=True, help="immutable export/checkpoint revision")
    parser.add_argument("--cache-layout", choices=("split",), default=DEFAULT_CACHE_LAYOUT)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        contract, report = inspect(
            args.model,
            args.source_revision,
            cache_layout=args.cache_layout,
        )
    except (InspectionError, OSError) as exc:
        print(f"Qwen2.5 static ONNX inspection failed: {exc}", file=sys.stderr)
        return 1
    _write_json(args.output, contract)
    _write_json(args.report, report)
    print(json.dumps(contract, ensure_ascii=True, indent=2, sort_keys=True))
    return 0 if contract["acl_om"]["supported_static_qwen25_layout"] else 2


inspect_model = inspect


if __name__ == "__main__":
    raise SystemExit(main())
