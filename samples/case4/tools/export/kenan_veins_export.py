"""Export the official Kenan palmar-vein Keras model to NCHW ONNX.

This is a host-side conversion helper.  It does not install TensorFlow or
PyTorch on the Ascend board.  The upstream model is a 500-class NIR classifier
with a channels-last ``[N,128,128,1]`` input; the wrapper exposes a static
NCHW ``[1,1,128,128]`` input for ATC while preserving its 500 logits.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import tensorflow as tf
import tf2onnx


def export(checkpoint: Path, output: Path, opset: int = 13) -> dict[str, Any]:
    model = tf.keras.models.load_model(
        str(checkpoint),
        compile=False,
        custom_objects={"LeakyReLU": tf.keras.layers.LeakyReLU},
    )
    if model.input_shape[1:] != (128, 128, 1) or model.output_shape[1:] != (500,):
        raise ValueError(f"unexpected Keras contract: {model.input_shape} -> {model.output_shape}")

    @tf.function(input_signature=[tf.TensorSpec([1, 1, 128, 128], tf.float32, name="input")])
    def forward(value: tf.Tensor) -> dict[str, tf.Tensor]:
        return {"logits": model(tf.transpose(value, [0, 2, 3, 1]), training=False)}

    output.parent.mkdir(parents=True, exist_ok=True)
    model_proto, _ = tf2onnx.convert.from_function(
        forward,
        input_signature=[tf.TensorSpec([1, 1, 128, 128], tf.float32, name="input")],
        opset=opset,
        output_path=str(output),
    )
    shape = [int(item.dim_value) for item in model_proto.graph.output[0].type.tensor_type.shape.dim]
    if shape != [1, 500]:
        raise ValueError(f"unexpected ONNX output shape: {shape}")
    return {
        "model_id": "kenan_cnn_palmar_veins",
        "status": "onnx_exported",
        "input_shape": [1, 1, 128, 128],
        "output_shape": shape,
        "input_layout": "NCHW wrapper around upstream NHWC",
        "onnx": str(output),
        "bytes": output.stat().st_size,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--opset", type=int, default=13)
    args = parser.parse_args()
    print(json.dumps(export(args.checkpoint, args.output, args.opset), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
