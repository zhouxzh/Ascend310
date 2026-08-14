"""Build a static batched complex-IQ DFT ONNX model for Ascend 310B.

The model intentionally uses only fixed MatMul projections and elementwise
power operations.  It is the Ascend counterpart to the batch-oriented compute
boundary used by GNU Radio CUDA/OpenCL out-of-tree modules, without relying on
CUDA, OpenCL, PyTorch, or an ONNX FFT operator.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

from .rtl_iq_spectrum_numpy_reference import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_SAMPLE_RATE_HZ,
    DEFAULT_WINDOW_SAMPLES,
    complex_dft_projection_weights,
    shifted_frequency_axis_hz,
    validate_iq_contract,
)


def build_rtl_iq_spectrum_onnx_model(
    *,
    sample_rate_hz: float = DEFAULT_SAMPLE_RATE_HZ,
    batch_size: int = DEFAULT_BATCH_SIZE,
    window_samples: int = DEFAULT_WINDOW_SAMPLES,
) -> tuple[onnx.ModelProto, dict[str, Any]]:
    """Create a fixed-shape full complex DFT periodogram graph for ATC."""
    validate_iq_contract(
        batch_size=batch_size, window_samples=window_samples, sample_rate_hz=sample_rate_hz
    )
    real_weights, imaginary_weights = complex_dft_projection_weights(window_samples=window_samples)
    frequency_axis = shifted_frequency_axis_hz(
        sample_rate_hz=sample_rate_hz, window_samples=window_samples
    )
    initializers = [
        numpy_helper.from_array(np.asarray([batch_size, 2 * window_samples], dtype=np.int64), "flatten_shape"),
        numpy_helper.from_array(real_weights, "dft_real_weights"),
        numpy_helper.from_array(imaginary_weights, "dft_imaginary_weights"),
    ]
    nodes = [
        helper.make_node("Reshape", ["iq_samples", "flatten_shape"], ["flattened_iq"]),
        helper.make_node("MatMul", ["flattened_iq", "dft_real_weights"], ["dft_real"]),
        helper.make_node("MatMul", ["flattened_iq", "dft_imaginary_weights"], ["dft_imaginary"]),
        helper.make_node("Mul", ["dft_real", "dft_real"], ["dft_real_power"]),
        helper.make_node("Mul", ["dft_imaginary", "dft_imaginary"], ["dft_imaginary_power"]),
        helper.make_node("Add", ["dft_real_power", "dft_imaginary_power"], ["spectrum_power"]),
    ]
    graph = helper.make_graph(
        nodes,
        "fixed_batched_rtl_iq_dft_spectrum",
        [helper.make_tensor_value_info("iq_samples", TensorProto.FLOAT, [batch_size, 2, window_samples])],
        [helper.make_tensor_value_info("spectrum_power", TensorProto.FLOAT, [batch_size, window_samples])],
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
        "model": "fixed_batched_rtl_iq_dft_periodogram",
        "builder": "numpy_onnx",
        "input_name": "iq_samples",
        "input_shape": [batch_size, 2, window_samples],
        "input_channels": ["I", "Q"],
        "output_name": "spectrum_power",
        "output_shape": [batch_size, window_samples],
        "sample_rate_hz": float(sample_rate_hz),
        "window": "hann",
        "remove_complex_dc_before_model": True,
        "frequency_order": "fftshift_negative_to_positive",
        "frequency_resolution_hz": float(sample_rate_hz / window_samples),
        "frequency_axis_hz": frequency_axis.tolist(),
        "output": "normalized_complex_spectral_power",
        "cpu_role": "capture_decode_dc_removal_and_optional_test_baseline_only",
    }
    return model, metadata


def export_rtl_iq_spectrum_onnx(output: Path, **model_options: Any) -> dict[str, Any]:
    """Write ONNX and an inspectable JSON sidecar without training frameworks."""
    model, metadata = build_rtl_iq_spectrum_onnx_model(**model_options)
    output.parent.mkdir(parents=True, exist_ok=True)
    onnx.save_model(model, output)
    output.with_suffix(output.suffix + ".json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=True), encoding="utf-8"
    )
    return metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sample-rate", type=float, default=DEFAULT_SAMPLE_RATE_HZ)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--window-samples", type=int, default=DEFAULT_WINDOW_SAMPLES)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    export_rtl_iq_spectrum_onnx(
        args.output,
        sample_rate_hz=args.sample_rate,
        batch_size=args.batch_size,
        window_samples=args.window_samples,
    )
    print(f"wrote {args.output}")
    print(f"wrote {args.output.with_suffix(args.output.suffix + '.json')}")


if __name__ == "__main__":
    main()
