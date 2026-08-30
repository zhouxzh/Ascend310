#!/usr/bin/env python3
"""Inspect the fixed Qwen ONNX graph without executing it.

The output contract is intentionally strict.  It is consumed by the ACL
runtime and therefore remains ``supported_autoregressive_qwen_layout=false``
unless the graph is exactly the static full-context shape that the runtime
implements.  Generic Hugging Face/Transformers.js exports normally contain
past-key-value inputs or dynamic dimensions and are expected to fail this
gate.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


MODEL_ID = "qwen1.5-0.5b-chat-acl-om"
VOCAB_SIZE = 151936
SEQUENCE_LENGTH = 2048
MIN_SUPPORTED_OPSET = 13
MAX_SUPPORTED_OPSET = 18
# Conservative standard-ONNX audit for the first ACL adapter.  A graph using
# a newer or vendor-specific operator must be reviewed as a new candidate.
SUPPORTED_OPERATOR_TYPES = {
    "Abs", "Add", "And", "Cast", "Ceil", "Clip", "Concat", "Constant",
    "ConstantOfShape", "Cos", "CumSum", "Div", "Equal", "Erf", "Exp",
    "Expand", "Gather", "GatherElements", "GatherND", "Gemm", "Greater", "GreaterOrEqual",
    "Identity", "LayerNormalization", "Less", "LessOrEqual", "Log",
    "IsNaN", "MatMul", "Max", "Min", "Mod", "Mul", "Neg", "Not", "Or",
    "Pad", "Pow", "Range", "ReduceMax", "ReduceMean", "ReduceMin", "ReduceSum",
    "Reshape", "Round", "ScatterElements", "ScatterND", "Shape", "Sign", "Sin",
    "Slice", "Softmax", "Split", "Sqrt", "Squeeze", "Sub", "Tanh", "Tile",
    "Transpose", "Trilu", "Unsqueeze", "Where",
}
EXPECTED_INPUTS = {
    "input_ids": {"name": "input_ids", "dtype": "int64", "shape": [1, SEQUENCE_LENGTH]},
    "attention_mask": {"name": "attention_mask", "dtype": "int64", "shape": [1, SEQUENCE_LENGTH]},
    "position_ids": {"name": "position_ids", "dtype": "int64", "shape": [1, SEQUENCE_LENGTH]},
}
EXPECTED_INPUT_ORDER = ("input_ids", "attention_mask", "position_ids")
EXPECTED_OUTPUT = {
    "logits": {
        "name": "logits",
        "dtype": "float16",
        "shape": [1, SEQUENCE_LENGTH, VOCAB_SIZE],
    }
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _is_lfs_pointer(path: Path) -> bool:
    try:
        with path.open("rb") as source:
            prefix = source.read(256)
    except OSError:
        return False
    return prefix.startswith(b"version https://git-lfs.github.com/spec/v1")


def _dimension_value(dimension: Any) -> Optional[int]:
    if dimension.HasField("dim_value"):
        return int(dimension.dim_value)
    return None


def _shape(value_info: Any) -> List[Optional[int]]:
    tensor_type = value_info.type.tensor_type
    if not tensor_type.HasField("shape"):
        return []
    return [_dimension_value(dim) for dim in tensor_type.shape.dim]


def _dtype_name(element_type: int) -> str:
    # These values are stable ONNX TensorProto enum values.  Importing the
    # enum keeps the report readable while avoiding a dependency on numpy.
    try:
        from onnx import TensorProto

        names = {
            TensorProto.INT32: "int32",
            TensorProto.INT64: "int64",
            TensorProto.FLOAT16: "float16",
            TensorProto.FLOAT: "float32",
            TensorProto.UINT8: "uint8",
            TensorProto.INT8: "int8",
            TensorProto.BOOL: "bool",
        }
        return names.get(element_type, f"onnx_type_{element_type}")
    except Exception:
        return f"onnx_type_{element_type}"


def _observed_value_infos(values: Iterable[Any]) -> List[Dict[str, Any]]:
    result = []
    for value in values:
        result.append(
            {
                "name": value.name,
                "dtype": _dtype_name(value.type.tensor_type.elem_type),
                "shape": _shape(value),
            }
        )
    return result


def _contract(
    reason: str,
    supported: bool,
    *,
    model_bytes: int,
    model_sha256: str,
    source_revision: Optional[str],
    opset: Optional[int],
    unsupported_operators: Sequence[str],
    input_order: Sequence[str],
    input_order_verified: bool,
) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "model": {
            "family": "qwen1.5",
            "model_id": MODEL_ID,
            "eos_token_id": 151645,
        },
        "acl_om": {
            "supported_autoregressive_qwen_layout": bool(supported),
            "support_reason": reason,
            "execution_mode": "full_context_logits",
            "static_sequence_length": SEQUENCE_LENGTH,
            "input_order": list(input_order),
            "input_order_verified": bool(input_order_verified),
            "inputs": EXPECTED_INPUTS,
            "output": EXPECTED_OUTPUT,
            "operator_audit": {
                "opset": opset,
                "unsupported_operators": list(unsupported_operators),
            },
        },
        "source_artifact": {"bytes": model_bytes, "sha256": model_sha256},
        **({"source_revision": source_revision} if source_revision else {}),
    }


def _write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as output:
        json.dump(value, output, ensure_ascii=True, indent=2, sort_keys=True)
        output.write("\n")
    os.replace(temporary, path)


def inspect(
    model_path: Path, source_revision: Optional[str] = None
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    if not isinstance(source_revision, str) or not source_revision.strip():
        raise ValueError("an immutable source revision is required")
    if not model_path.is_file():
        raise FileNotFoundError(model_path)
    if _is_lfs_pointer(model_path):
        raise ValueError("the model is a Git LFS pointer, not an ONNX artifact")

    try:
        import onnx  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "the active board environment has no 'onnx' package; refusing to "
            "inspect or convert the graph"
        ) from exc

    # Loading without external data lets us explicitly reject external
    # initializers before any accidental sidecar read.
    model_bytes = model_path.stat().st_size
    model_sha256 = _sha256(model_path)
    graph_model = onnx.load(str(model_path), load_external_data=False)
    # Catch malformed graphs before interpreting their shapes or operator set.
    onnx.checker.check_model(graph_model)
    graph = graph_model.graph
    initializer_names = {item.name for item in graph.initializer}
    graph_inputs = [item for item in graph.input if item.name not in initializer_names]
    observed_inputs = _observed_value_infos(graph_inputs)
    observed_outputs = _observed_value_infos(graph.output)
    op_types = sorted({node.op_type for node in graph.node})
    unsupported_operators = sorted(
        {
            f"{node.domain or 'ai.onnx'}:{node.op_type}"
            for node in graph.node
            if (node.domain or "ai.onnx") != "ai.onnx"
            or node.op_type not in SUPPORTED_OPERATOR_TYPES
        }
    )
    external_initializers = [
        item.name
        for item in graph.initializer
        if getattr(item, "data_location", 0) == getattr(onnx.TensorProto, "EXTERNAL", 1)
        or any(entry.key == "location" for entry in item.external_data)
    ]
    all_value_infos = (*graph_inputs, *graph.output, *graph.value_info)
    has_dynamic_dims = any(
        dimension is None
        for value in all_value_infos
        for dimension in _shape(value)
    )
    input_names = [value["name"] for value in observed_inputs]
    output_names = [value["name"] for value in observed_outputs]
    reasons: List[str] = []
    if graph_model.opset_import:
        opsets = {item.domain or "ai.onnx": int(item.version) for item in graph_model.opset_import}
    else:
        opsets = {}
    onnx_opset = opsets.get("ai.onnx")
    if onnx_opset is None:
        reasons.append("the graph has no ai.onnx opset declaration")
    elif not MIN_SUPPORTED_OPSET <= onnx_opset <= MAX_SUPPORTED_OPSET:
        reasons.append(
            f"ai.onnx opset {onnx_opset} is outside the admitted range "
            f"[{MIN_SUPPORTED_OPSET}, {MAX_SUPPORTED_OPSET}]"
        )
    if external_initializers:
        reasons.append("external initializers are not supported")
    if has_dynamic_dims:
        reasons.append("dynamic or symbolic dimensions are not supported")
    input_order_verified = tuple(input_names) == EXPECTED_INPUT_ORDER
    if not input_order_verified:
        reasons.append(
            f"input order is {input_names!r}; expected {list(EXPECTED_INPUT_ORDER)!r}"
        )
    if output_names != ["logits"]:
        reasons.append(f"outputs are {output_names!r}; expected ['logits']")
    observed_input_map = {value["name"]: value for value in observed_inputs}
    if observed_input_map != EXPECTED_INPUTS:
        reasons.append("input dtype/shape contract does not match int64 [1,2048] inputs")
    if observed_outputs != [EXPECTED_OUTPUT["logits"]]:
        reasons.append("logits dtype/shape contract does not match float16 [1,2048,151936]")
    suspicious_names = [
        value["name"]
        for value in (*observed_inputs, *observed_outputs)
        if "past" in value["name"].lower()
        or "present" in value["name"].lower()
        or "key_values" in value["name"].lower()
    ]
    if suspicious_names:
        reasons.append(f"past/KV-cache values are present: {suspicious_names!r}")
    # A graph containing integer quantization operators is not automatically
    # invalid, but it requires a model-specific ACL operator audit.  Keep the
    # hard gate explicit rather than silently assuming CANN supports it.
    quant_ops = sorted(
        op for op in op_types if op in {"MatMulInteger", "DynamicQuantizeLinear", "QLinearMatMul"}
    )
    if quant_ops:
        reasons.append(f"quantization operators require explicit ACL audit: {quant_ops!r}")
    if unsupported_operators:
        reasons.append(f"unsupported operators require ACL audit: {unsupported_operators!r}")

    supported = not reasons
    reason = "exact static full-context contract matched" if supported else "; ".join(reasons)
    contract = _contract(
        reason,
        supported,
        model_bytes=model_bytes,
        model_sha256=model_sha256,
        source_revision=source_revision,
        opset=onnx_opset,
        unsupported_operators=unsupported_operators,
        input_order=input_names,
        input_order_verified=input_order_verified,
    )
    report = {
        "schema_version": 1,
        "model_path": str(model_path),
        "bytes": model_bytes,
        "sha256": model_sha256,
        "onnx_ir_version": int(graph_model.ir_version),
        "opsets": opsets,
        "graph_name": graph.name,
        "node_count": len(graph.node),
        "op_types": op_types,
        "unsupported_operators": unsupported_operators,
        "quantization_ops": quant_ops,
        "initializer_count": len(graph.initializer),
        "external_initializers": external_initializers,
        "inputs": observed_inputs,
        "outputs": observed_outputs,
        "dynamic_dimensions": has_dynamic_dims,
        "value_info_count": len(graph.value_info),
        "contract": contract,
    }
    return contract, report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="strict ACL contract JSON")
    parser.add_argument("--report", required=True, type=Path, help="detailed inspection report JSON")
    parser.add_argument(
        "--source-revision",
        required=True,
        help="immutable model revision from the board manifest",
    )
    args = parser.parse_args(argv)
    try:
        contract, report = inspect(args.model, args.source_revision)
    except Exception as exc:  # noqa: BLE001 - convert all gate errors to a reportable exit
        print(f"ONNX inspection failed: {exc}", file=sys.stderr)
        return 1
    _write_json(args.output, contract)
    _write_json(args.report, report)
    print(json.dumps(contract, ensure_ascii=True, indent=2, sort_keys=True))
    if not contract["acl_om"]["supported_autoregressive_qwen_layout"]:
        print("ONNX graph is not admitted for the ACL/OM runtime", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
