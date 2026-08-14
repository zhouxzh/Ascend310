"""Build fixed-shape ONNX counterparts for selected VOLK kernels."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

from .volk_kernel_reference import (
    DEFAULT_VECTOR_LENGTH,
    VOLK_KERNELS,
    input_channels,
    output_shape,
    validate_contract,
)


def _channel(name: str, index: int) -> tuple[onnx.TensorProto, onnx.NodeProto]:
    initializer = numpy_helper.from_array(np.asarray(index, dtype=np.int64), f"{name}_index")
    node = helper.make_node(
        "Gather", ["input_tensor", f"{name}_index"], [name], axis=1
    )
    return initializer, node


def build_volk_kernel_onnx_model(
    kernel: str,
    *,
    batch_size: int,
    vector_length: int = DEFAULT_VECTOR_LENGTH,
) -> tuple[onnx.ModelProto, dict[str, Any]]:
    """Create a standard-operator ONNX graph matching one VOLK kernel."""
    validate_contract(kernel, batch_size, vector_length)
    names = ("ar", "ai", "br", "bi")[: input_channels(kernel)]
    initializers: list[onnx.TensorProto] = []
    nodes: list[onnx.NodeProto] = []
    for index, name in enumerate(names):
        initializer, node = _channel(name, index)
        initializers.append(initializer)
        nodes.append(node)

    output_name = "output_tensor"
    if kernel == "magnitude_squared":
        nodes.extend(
            (
                helper.make_node("Mul", ["ar", "ar"], ["ar2"]),
                helper.make_node("Mul", ["ai", "ai"], ["ai2"]),
                helper.make_node("Add", ["ar2", "ai2"], [output_name]),
            )
        )
    elif kernel in {"multiply_conjugate", "conjugate_dot_product"}:
        initializers.append(
            numpy_helper.from_array(np.asarray([1], dtype=np.int64), "unsqueeze_axis")
        )
        nodes.extend(
            (
                helper.make_node("Mul", ["ar", "br"], ["ar_br"]),
                helper.make_node("Mul", ["ai", "bi"], ["ai_bi"]),
                helper.make_node("Add", ["ar_br", "ai_bi"], ["product_real"]),
                helper.make_node("Mul", ["ai", "br"], ["ai_br"]),
                helper.make_node("Mul", ["ar", "bi"], ["ar_bi"]),
                helper.make_node("Sub", ["ai_br", "ar_bi"], ["product_imaginary"]),
            )
        )
        if kernel == "conjugate_dot_product":
            initializers.append(
                numpy_helper.from_array(np.asarray([1], dtype=np.int64), "reduce_axis")
            )
            nodes.extend(
                (
                    helper.make_node(
                        "ReduceSum",
                        ["product_real", "reduce_axis"],
                        ["reduced_real"],
                        keepdims=0,
                    ),
                    helper.make_node(
                        "ReduceSum",
                        ["product_imaginary", "reduce_axis"],
                        ["reduced_imaginary"],
                        keepdims=0,
                    ),
                )
            )
            real_name, imaginary_name = "reduced_real", "reduced_imaginary"
        else:
            real_name, imaginary_name = "product_real", "product_imaginary"
        nodes.extend(
            (
                helper.make_node("Unsqueeze", [real_name, "unsqueeze_axis"], ["real_channel"]),
                helper.make_node(
                    "Unsqueeze", [imaginary_name, "unsqueeze_axis"], ["imaginary_channel"]
                ),
                helper.make_node(
                    "Concat", ["real_channel", "imaginary_channel"], [output_name], axis=1
                ),
            )
        )
    elif kernel == "dot_product":
        initializers.append(
            numpy_helper.from_array(np.asarray([1], dtype=np.int64), "reduce_axis")
        )
        nodes.extend(
            (
                helper.make_node("Mul", ["ar", "ai"], ["products"]),
                helper.make_node(
                    "ReduceSum", ["products", "reduce_axis"], [output_name], keepdims=1
                ),
            )
        )
    else:  # pragma: no cover - validate_contract rejects this first
        raise ValueError(f"unsupported VOLK benchmark kernel: {kernel}")

    source_shape = [batch_size, input_channels(kernel), vector_length]
    result_shape = list(output_shape(kernel, batch_size, vector_length))
    graph = helper.make_graph(
        nodes,
        f"volk_{kernel}_comparison",
        [helper.make_tensor_value_info("input_tensor", TensorProto.FLOAT, source_shape)],
        [helper.make_tensor_value_info(output_name, TensorProto.FLOAT, result_shape)],
        initializer=initializers,
    )
    model = helper.make_model(
        graph,
        producer_name="ascend310-case5-numpy-onnx",
        opset_imports=[helper.make_opsetid("", 13)],
    )
    model.ir_version = 8
    onnx.checker.check_model(model)
    metadata: dict[str, Any] = {
        "schema_version": 1,
        "model": f"volk_{kernel}_comparison",
        "kernel": kernel,
        "volk_kernel": {
            "magnitude_squared": "volk_32fc_magnitude_squared_32f",
            "multiply_conjugate": "volk_32fc_x2_multiply_conjugate_32fc",
            "dot_product": "volk_32f_x2_dot_prod_32f",
            "conjugate_dot_product": "volk_32fc_x2_conjugate_dot_prod_32fc",
        }[kernel],
        "builder": "numpy_onnx",
        "input_name": "input_tensor",
        "input_shape": source_shape,
        "input_layout": "planar_float32",
        "output_name": output_name,
        "output_shape": result_shape,
        "batch_size": batch_size,
        "vector_length": vector_length,
        "opset": 13,
    }
    return model, metadata


def export_volk_kernel_onnx(
    output: Path,
    kernel: str,
    *,
    batch_size: int,
    vector_length: int = DEFAULT_VECTOR_LENGTH,
) -> dict[str, Any]:
    model, metadata = build_volk_kernel_onnx_model(
        kernel, batch_size=batch_size, vector_length=vector_length
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    onnx.save_model(model, output)
    output.with_suffix(output.suffix + ".json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=True), encoding="utf-8"
    )
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kernel", choices=VOLK_KERNELS, required=True)
    parser.add_argument("--batch-size", type=int, required=True)
    parser.add_argument("--vector-length", type=int, default=DEFAULT_VECTOR_LENGTH)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    export_volk_kernel_onnx(
        args.output,
        args.kernel,
        batch_size=args.batch_size,
        vector_length=args.vector_length,
    )
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
