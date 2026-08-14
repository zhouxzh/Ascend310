"""Build a fixed high-resolution NPU DFT spectrum model without PyTorch.

The graph projects each fixed input snapshot through precomputed Hann
windowed cosine/sine DFT weights.  MatMul, square, and pairwise power summing
all run in the Ascend OM model; Python only provides the already de-meaned
sample window and converts the returned power to display dB.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

from .npu_spectrum_numpy_reference import dft_projection_weights, spectrum_axis_hz


def build_npu_spectrum_onnx_model(
    *, sample_rate_hz: float, samples: int, channels: int = 2, max_frequency_hz: float = 20_000.0
) -> tuple[onnx.ModelProto, dict[str, Any]]:
    """Create a static two-channel DFT periodogram graph for Ascend ATC."""
    if channels <= 0:
        raise ValueError("channels must be positive")
    weights, bins = dft_projection_weights(
        sample_rate_hz=sample_rate_hz,
        samples=samples,
        max_frequency_hz=max_frequency_hz,
    )
    frequency_axis = spectrum_axis_hz(
        sample_rate_hz=sample_rate_hz,
        samples=samples,
        max_frequency_hz=max_frequency_hz,
    )
    n_bins = int(bins.size)
    initializers = [
        numpy_helper.from_array(weights, "dft_projection_weights"),
        numpy_helper.from_array(
            np.asarray([1, channels, n_bins * 2, 1], dtype=np.int64), "pair_shape"
        ),
        numpy_helper.from_array(np.asarray(2.0, dtype=np.float32), "pair_sum_scale"),
    ]
    nodes = [
        helper.make_node("MatMul", ["waveforms", "dft_projection_weights"], ["projected"]),
        helper.make_node("Mul", ["projected", "projected"], ["squared_projection"]),
        helper.make_node("Reshape", ["squared_projection", "pair_shape"], ["squared_pairs"]),
        helper.make_node(
            "AveragePool",
            ["squared_pairs"],
            ["pair_average"],
            kernel_shape=[2, 1],
            strides=[2, 1],
        ),
        helper.make_node("Mul", ["pair_average", "pair_sum_scale"], ["spectrum_power"]),
    ]
    graph = helper.make_graph(
        nodes,
        "fixed_npu_dft_spectrum",
        [helper.make_tensor_value_info("waveforms", TensorProto.FLOAT, [1, channels, samples])],
        [
            helper.make_tensor_value_info(
                "spectrum_power", TensorProto.FLOAT, [1, channels, n_bins, 1]
            )
        ],
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
        "model": "fixed_npu_dft_periodogram",
        "builder": "numpy_onnx",
        "input_shape": [1, channels, samples],
        "output_shape": [1, channels, n_bins, 1],
        "sample_rate_hz": sample_rate_hz,
        "window": "hann",
        "remove_dc_before_model": True,
        "frequency_scale": "linear_hz",
        "frequency_resolution_hz": sample_rate_hz / samples,
        "max_frequency_hz": float(frequency_axis[-1]),
        "frequency_axis_hz": frequency_axis.tolist(),
        "frequency_bins": bins.tolist(),
        "one_sided_power": True,
        "output": "spectral_power_v_squared",
    }
    return model, metadata


def export_npu_spectrum_onnx(output: Path, **model_options: Any) -> dict[str, Any]:
    """Write ONNX and a readable sidecar without any training framework."""
    model, metadata = build_npu_spectrum_onnx_model(**model_options)
    output.parent.mkdir(parents=True, exist_ok=True)
    onnx.save_model(model, output)
    output.with_suffix(output.suffix + ".json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=True), encoding="utf-8"
    )
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-rate", type=float, required=True)
    parser.add_argument("--samples", type=int, required=True)
    parser.add_argument("--channels", type=int, default=2)
    parser.add_argument("--max-frequency-hz", type=float, default=20_000.0)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    export_npu_spectrum_onnx(
        args.output,
        sample_rate_hz=args.sample_rate,
        samples=args.samples,
        channels=args.channels,
        max_frequency_hz=args.max_frequency_hz,
    )
    print("wrote", args.output)
    print("wrote", args.output.with_suffix(args.output.suffix + ".json"))


if __name__ == "__main__":
    main()
