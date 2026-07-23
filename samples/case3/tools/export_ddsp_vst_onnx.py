#!/usr/bin/env python3
"""Export a DDSP-VST predict-controls TFLite model as a static ONNX graph.

The upstream TFLite graph contains a one-step Keras GRU wrapped in a
``WHILE``/TensorList graph. This exporter reads the trained weights from the
TFLite flatbuffer and rebuilds the same one-step network with ordinary ONNX
operators. The resulting model is easier to validate with ONNX Runtime and is
better suited to a later Ascend ATC conversion.

Inputs:
    state       float32[512]
    f0_scaled   float32[1]
    pw_scaled   float32[1]

Outputs:
    amplitude   float32[1]
    harmonics   float32[60]
    noise_amps  float32[65]
    state_out   float32[512]
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
import sys
from typing import Callable

import numpy as np


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_TFLITE = ROOT_DIR / "models" / "ddsp_vst" / "Violin.tflite"
DEFAULT_ONNX = ROOT_DIR / "models" / "ddsp_vst" / "Violin.onnx"


def _import_tflite_schema():
    try:
        import tflite  # type: ignore

        return tflite
    except ImportError:
        bundled = ROOT_DIR / "_upstream" / "_python_tools"
        if bundled.exists():
            # Append so the workspace's bundled NumPy does not override the
            # environment NumPy already imported above.
            sys.path.append(str(bundled))
            import tflite  # type: ignore

            return tflite
        raise RuntimeError(
            "Missing TFLite schema package. Install it with: "
            "python -m pip install tflite flatbuffers"
        )


def _import_onnx():
    try:
        import onnx
        from onnx import TensorProto, checker, helper, numpy_helper, shape_inference

        return onnx, TensorProto, checker, helper, numpy_helper, shape_inference
    except ImportError as exc:
        raise RuntimeError(
            "Missing ONNX package. Install it with: python -m pip install onnx"
        ) from exc


def _shape(tensor) -> tuple[int, ...]:
    return tuple(int(value) for value in tensor.ShapeAsNumpy())


def _name(tensor) -> str:
    value = tensor.Name()
    return value.decode("utf-8", "replace") if value else ""


class TFLiteWeights:
    """Read named float tensors from a DDSP-VST TFLite flatbuffer."""

    def __init__(self, model_path: Path) -> None:
        tflite = _import_tflite_schema()
        data = model_path.read_bytes()
        self.model = tflite.Model.GetRootAsModel(data, 0)
        if self.model.SubgraphsLength() < 3:
            raise ValueError(
                f"Expected at least 3 TFLite subgraphs, got "
                f"{self.model.SubgraphsLength()}"
            )
        self.main = self.model.Subgraphs(0)
        self.gru_body = self.model.Subgraphs(2)

    def _buffer_array(self, tensor) -> np.ndarray:
        buffer = self.model.Buffers(tensor.Buffer()).DataAsNumpy()
        if not isinstance(buffer, np.ndarray) or buffer.size == 0:
            raise ValueError(f"Tensor {_name(tensor)!r} has no constant data")
        array = np.frombuffer(buffer.tobytes(), dtype=np.float32).copy()
        shape = _shape(tensor)
        if int(np.prod(shape, dtype=np.int64)) != array.size:
            raise ValueError(
                f"Tensor {_name(tensor)!r} shape {shape} does not match "
                f"{array.size} float values"
            )
        return array.reshape(shape)

    @staticmethod
    def _find_tensor(subgraph, predicate: Callable[[str, tuple[int, ...]], bool]):
        matches = []
        for index in range(subgraph.TensorsLength()):
            tensor = subgraph.Tensors(index)
            tensor_name = _name(tensor)
            tensor_shape = _shape(tensor)
            if predicate(tensor_name, tensor_shape):
                matches.append(tensor)
        if len(matches) != 1:
            descriptions = [f"{_name(t)} {_shape(t)}" for t in matches]
            raise ValueError(
                f"Expected exactly one matching TFLite tensor, got "
                f"{len(matches)}: {descriptions}"
            )
        return matches[0]

    def main_array(self, text: str, shape: tuple[int, ...]) -> np.ndarray:
        return self.main_array_any((text,), shape)

    def main_array_any(
        self, texts: tuple[str, ...], shape: tuple[int, ...]
    ) -> np.ndarray:
        tensor = self._find_tensor(
            self.main,
            lambda name, value_shape: any(text in name for text in texts)
            and value_shape == shape,
        )
        return self._buffer_array(tensor)

    def gru_array(self, name: str, shape: tuple[int, ...]) -> np.ndarray:
        tensor = self._find_tensor(
            self.gru_body,
            lambda value_name, value_shape: value_name == name
            and value_shape == shape,
        )
        return self._buffer_array(tensor)


@dataclass(frozen=True)
class ModelParameters:
    f0_weight: np.ndarray
    f0_bias: np.ndarray
    f0_gamma: np.ndarray
    f0_beta: np.ndarray
    power_weight: np.ndarray
    power_bias: np.ndarray
    power_gamma: np.ndarray
    power_beta: np.ndarray
    gru_input_weight: np.ndarray
    gru_input_bias: np.ndarray
    gru_recurrent_weight: np.ndarray
    gru_recurrent_bias: np.ndarray
    output_weight: np.ndarray
    output_bias: np.ndarray
    output_gamma: np.ndarray
    output_beta: np.ndarray
    controls_weight: np.ndarray
    controls_bias: np.ndarray


def load_parameters(model_path: Path) -> ModelParameters:
    weights = TFLiteWeights(model_path)
    return ModelParameters(
        # TFLite graph inspection shows stack_3 consumes call_pw_scaled and
        # stack_4 consumes call_f0_scaled.
        f0_weight=weights.main_array(
            "fc_stack_4/fc_4/dense_5/Tensordot/MatMul", (256, 1)
        ),
        f0_bias=weights.main_array_any(
            ("fc_stack_4/fc_4/dense_5/BiasAdd/ReadVariableOp", "dense_5/bias"),
            (256,),
        ),
        f0_gamma=weights.main_array_any(
            (
                "fc_stack_4/fc_4/layer_normalization_4/Cast/ReadVariableOp",
                "layer_normalization_4/gamma",
            ),
            (256,),
        ),
        f0_beta=weights.main_array_any(
            (
                "fc_stack_4/fc_4/layer_normalization_4/Cast_1/ReadVariableOp",
                "layer_normalization_4/beta",
            ),
            (256,),
        ),
        power_weight=weights.main_array(
            "fc_stack_3/fc_3/dense_4/Tensordot/MatMul", (256, 1)
        ),
        power_bias=weights.main_array_any(
            ("fc_stack_3/fc_3/dense_4/BiasAdd/ReadVariableOp", "dense_4/bias"),
            (256,),
        ),
        power_gamma=weights.main_array_any(
            (
                "fc_stack_3/fc_3/layer_normalization_3/Cast/ReadVariableOp",
                "layer_normalization_3/gamma",
            ),
            (256,),
        ),
        power_beta=weights.main_array_any(
            (
                "fc_stack_3/fc_3/layer_normalization_3/Cast_1/ReadVariableOp",
                "layer_normalization_3/beta",
            ),
            (256,),
        ),
        # The TFLite while body applies MatMul to x with "while/MatMul" and
        # to the previous hidden state with "while/MatMul_1".
        gru_input_weight=weights.gru_array("while/MatMul", (1536, 512)),
        gru_input_bias=weights.gru_array("unstack2", (1536,)),
        gru_recurrent_weight=weights.gru_array(
            "while/MatMul_1", (1536, 512)
        ),
        gru_recurrent_bias=weights.gru_array("unstack3", (1536,)),
        output_weight=weights.main_array(
            "fc_stack_5/fc_5/dense_6/Tensordot/MatMul", (256, 1024)
        ),
        output_bias=weights.main_array_any(
            ("fc_stack_5/fc_5/dense_6/BiasAdd/ReadVariableOp", "dense_6/bias"),
            (256,),
        ),
        output_gamma=weights.main_array_any(
            (
                "fc_stack_5/fc_5/layer_normalization_5/Cast/ReadVariableOp",
                "layer_normalization_5/gamma",
            ),
            (256,),
        ),
        output_beta=weights.main_array_any(
            (
                "fc_stack_5/fc_5/layer_normalization_5/Cast_1/ReadVariableOp",
                "layer_normalization_5/beta",
            ),
            (256,),
        ),
        controls_weight=weights.main_array(
            "dense_7/Tensordot/MatMul", (126, 256)
        ),
        controls_bias=weights.main_array_any(
            ("dense_7/BiasAdd/ReadVariableOp", "dense_7/bias"), (126,)
        ),
    )


class GraphBuilder:
    def __init__(self, helper, numpy_helper, opset: int) -> None:
        self.helper = helper
        self.numpy_helper = numpy_helper
        self.opset = opset
        self.nodes = []
        self.initializers = []
        self._counter = 0

    def unique(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_{self._counter}"

    def add_initializer(self, name: str, value: np.ndarray) -> str:
        value = np.ascontiguousarray(value.astype(np.float32, copy=False))
        self.initializers.append(self.numpy_helper.from_array(value, name=name))
        return name

    def add_shape(self, name: str, values: list[int]) -> str:
        value = np.asarray(values, dtype=np.int64)
        self.initializers.append(self.numpy_helper.from_array(value, name=name))
        return name

    def split(self, x: str, outputs: list[str], prefix: str, sizes: list[int]):
        if self.opset <= 11:
            self.nodes.append(
                self.helper.make_node(
                    "Split", [x], outputs, axis=-1, split=sizes
                )
            )
        else:
            split_name = self.add_initializer(
                f"{prefix}_split", np.asarray(sizes, dtype=np.int64)
            )
            self.nodes.append(
                self.helper.make_node(
                    "Split", [x, split_name], outputs, axis=-1
                )
            )

    def dense(self, x: str, prefix: str, weight: np.ndarray, bias: np.ndarray) -> str:
        weight_name = self.add_initializer(f"{prefix}_weight", weight.T)
        bias_name = self.add_initializer(f"{prefix}_bias", bias)
        matmul = self.unique(f"{prefix}_matmul")
        output = self.unique(f"{prefix}_out")
        self.nodes.append(
            self.helper.make_node("MatMul", [x, weight_name], [matmul])
        )
        self.nodes.append(self.helper.make_node("Add", [matmul, bias_name], [output]))
        return output

    def layer_norm(
        self,
        x: str,
        prefix: str,
        gamma: np.ndarray,
        beta: np.ndarray,
        epsilon: float = 1e-3,
    ) -> str:
        mean = self.unique(f"{prefix}_mean")
        centered = self.unique(f"{prefix}_centered")
        squared = self.unique(f"{prefix}_squared")
        variance = self.unique(f"{prefix}_variance")
        variance_eps = self.unique(f"{prefix}_variance_eps")
        std = self.unique(f"{prefix}_std")
        normalized = self.unique(f"{prefix}_normalized")
        scaled = self.unique(f"{prefix}_scaled")
        output = self.unique(f"{prefix}_out")
        epsilon_name = self.add_initializer(
            f"{prefix}_epsilon", np.asarray([epsilon], dtype=np.float32)
        )
        gamma_name = self.add_initializer(f"{prefix}_gamma", gamma)
        beta_name = self.add_initializer(f"{prefix}_beta", beta)
        axes = [-1]
        self.nodes.extend(
            [
                self.helper.make_node(
                    "ReduceMean", [x], [mean], axes=axes, keepdims=1
                ),
                self.helper.make_node("Sub", [x, mean], [centered]),
                self.helper.make_node("Mul", [centered, centered], [squared]),
                self.helper.make_node(
                    "ReduceMean", [squared], [variance], axes=axes, keepdims=1
                ),
                self.helper.make_node(
                    "Add", [variance, epsilon_name], [variance_eps]
                ),
                self.helper.make_node("Sqrt", [variance_eps], [std]),
                self.helper.make_node("Div", [centered, std], [normalized]),
                self.helper.make_node("Mul", [normalized, gamma_name], [scaled]),
                self.helper.make_node("Add", [scaled, beta_name], [output]),
            ]
        )
        return output

    def fc_block(
        self,
        x: str,
        prefix: str,
        weight: np.ndarray,
        bias: np.ndarray,
        gamma: np.ndarray,
        beta: np.ndarray,
    ) -> str:
        dense = self.dense(x, prefix, weight, bias)
        norm = self.layer_norm(dense, f"{prefix}_ln", gamma, beta)
        output = self.unique(f"{prefix}_leaky_relu")
        self.nodes.append(
            self.helper.make_node("LeakyRelu", [norm], [output], alpha=0.2)
        )
        return output

    def exp_sigmoid(self, x: str, prefix: str, bias: float = 0.0) -> str:
        current = x
        if bias:
            bias_name = self.add_initializer(
                f"{prefix}_input_bias", np.asarray([bias], dtype=np.float32)
            )
            biased = self.unique(f"{prefix}_biased")
            self.nodes.append(self.helper.make_node("Add", [current, bias_name], [biased]))
            current = biased
        sigmoid = self.unique(f"{prefix}_sigmoid")
        powered = self.unique(f"{prefix}_powered")
        scaled = self.unique(f"{prefix}_scaled")
        output = self.unique(f"{prefix}_out")
        exponent = self.add_initializer(
            f"{prefix}_exponent", np.asarray([math.log(10.0)], dtype=np.float32)
        )
        maximum = self.add_initializer(
            f"{prefix}_maximum", np.asarray([2.0], dtype=np.float32)
        )
        threshold = self.add_initializer(
            f"{prefix}_threshold", np.asarray([1e-7], dtype=np.float32)
        )
        self.nodes.extend(
            [
                self.helper.make_node("Sigmoid", [current], [sigmoid]),
                self.helper.make_node("Pow", [sigmoid, exponent], [powered]),
                self.helper.make_node("Mul", [powered, maximum], [scaled]),
                self.helper.make_node("Add", [scaled, threshold], [output]),
            ]
        )
        return output

    def normalize_harmonics(self, harmonics: str, f0_scaled: str) -> str:
        """Match DDSP's remove-above-Nyquist and safe normalization."""
        midi_scale = self.add_initializer(
            "midi_scale", np.asarray([127.0], dtype=np.float32)
        )
        midi_a4 = self.add_initializer(
            "midi_a4", np.asarray([69.0], dtype=np.float32)
        )
        semitones_per_octave = self.add_initializer(
            "semitones_per_octave", np.asarray([12.0], dtype=np.float32)
        )
        two = self.add_initializer("frequency_base", np.asarray([2.0], dtype=np.float32))
        a4_hz = self.add_initializer("a4_hz", np.asarray([440.0], dtype=np.float32))
        harmonic_numbers = self.add_initializer(
            "harmonic_numbers",
            np.arange(1, 61, dtype=np.float32).reshape(1, 1, 60),
        )
        nyquist = self.add_initializer(
            "nyquist_hz", np.asarray([8000.0], dtype=np.float32)
        )
        zeros = self.add_initializer(
            "harmonic_zeros", np.zeros((1, 1, 60), dtype=np.float32)
        )
        zero = self.add_initializer("zero", np.asarray([0.0], dtype=np.float32))
        threshold = self.add_initializer(
            "harmonic_normalize_threshold",
            np.asarray([1e-7], dtype=np.float32),
        )

        midi = self.unique("midi")
        midi_offset = self.unique("midi_offset")
        octaves = self.unique("octaves")
        frequency_ratio = self.unique("frequency_ratio")
        f0_hz = self.unique("f0_hz")
        harmonic_frequencies = self.unique("harmonic_frequencies")
        below_nyquist = self.unique("below_nyquist")
        masked = self.unique("harmonics_masked")
        total = self.unique("harmonics_total")
        total_is_zero = self.unique("harmonics_total_is_zero")
        denominator = self.unique("harmonics_denominator")
        normalized = self.unique("harmonics_normalized")
        self.nodes.extend(
            [
                self.helper.make_node("Mul", [f0_scaled, midi_scale], [midi]),
                self.helper.make_node("Sub", [midi, midi_a4], [midi_offset]),
                self.helper.make_node(
                    "Div", [midi_offset, semitones_per_octave], [octaves]
                ),
                self.helper.make_node("Pow", [two, octaves], [frequency_ratio]),
                self.helper.make_node("Mul", [frequency_ratio, a4_hz], [f0_hz]),
                self.helper.make_node(
                    "Mul", [f0_hz, harmonic_numbers], [harmonic_frequencies]
                ),
                self.helper.make_node(
                    "Less", [harmonic_frequencies, nyquist], [below_nyquist]
                ),
                self.helper.make_node(
                    "Where", [below_nyquist, harmonics, zeros], [masked]
                ),
                self.helper.make_node(
                    "ReduceSum", [masked], [total], axes=[-1], keepdims=1
                ),
                self.helper.make_node("Equal", [total, zero], [total_is_zero]),
                self.helper.make_node(
                    "Where", [total_is_zero, threshold, total], [denominator]
                ),
                self.helper.make_node("Div", [masked, denominator], [normalized]),
            ]
        )
        return normalized


def build_model(params: ModelParameters, opset: int = 13):
    onnx, TensorProto, checker, helper, numpy_helper, shape_inference = _import_onnx()
    builder = GraphBuilder(helper, numpy_helper, opset)

    state_shape = builder.add_shape("state_input_shape", [1, 512])
    scalar_shape = builder.add_shape("scalar_input_shape", [1, 1, 1])
    state_3d_shape = builder.add_shape("state_3d_shape", [1, 1, 512])
    amplitude_shape = builder.add_shape("amplitude_output_shape", [1])
    harmonics_shape = builder.add_shape("harmonics_output_shape", [60])
    noise_shape = builder.add_shape("noise_output_shape", [65])
    state_output_shape = builder.add_shape("state_output_shape", [512])

    state_2d = "state_2d"
    f0_3d = "f0_3d"
    power_3d = "power_3d"
    builder.nodes.extend(
        [
            helper.make_node("Reshape", ["state", state_shape], [state_2d]),
            helper.make_node("Reshape", ["f0_scaled", scalar_shape], [f0_3d]),
            helper.make_node("Reshape", ["pw_scaled", scalar_shape], [power_3d]),
        ]
    )

    f0_features = builder.fc_block(
        f0_3d,
        "f0_stack",
        params.f0_weight,
        params.f0_bias,
        params.f0_gamma,
        params.f0_beta,
    )
    power_features = builder.fc_block(
        power_3d,
        "power_stack",
        params.power_weight,
        params.power_bias,
        params.power_gamma,
        params.power_beta,
    )
    rnn_input_3d = "rnn_input_3d"
    rnn_input_shape = builder.add_shape("rnn_input_shape", [1, 512])
    rnn_input = "rnn_input"
    builder.nodes.extend(
        [
            helper.make_node(
                "Concat", [power_features, f0_features], [rnn_input_3d], axis=-1
            ),
            helper.make_node(
                "Reshape", [rnn_input_3d, rnn_input_shape], [rnn_input]
            ),
        ]
    )

    x_linear = builder.dense(
        rnn_input,
        "gru_input",
        params.gru_input_weight,
        params.gru_input_bias,
    )
    h_linear = builder.dense(
        state_2d,
        "gru_recurrent",
        params.gru_recurrent_weight,
        params.gru_recurrent_bias,
    )
    x_z, x_r, x_h = "x_z", "x_r", "x_h"
    h_z, h_r, h_h = "h_z", "h_r", "h_h"
    builder.split(x_linear, [x_z, x_r, x_h], "gru_input", [512, 512, 512])
    builder.split(
        h_linear, [h_z, h_r, h_h], "gru_recurrent", [512, 512, 512]
    )

    z_pre, z_gate = "z_pre", "z_gate"
    r_pre, r_gate = "r_pre", "r_gate"
    reset_h, candidate_pre, candidate = "reset_h", "candidate_pre", "candidate"
    kept, one_minus_z, proposed, state_new = (
        "state_kept",
        "one_minus_z",
        "state_proposed",
        "state_new",
    )
    one = builder.add_initializer("one", np.asarray([1.0], dtype=np.float32))
    builder.nodes.extend(
        [
            helper.make_node("Add", [x_z, h_z], [z_pre]),
            helper.make_node("Sigmoid", [z_pre], [z_gate]),
            helper.make_node("Add", [x_r, h_r], [r_pre]),
            helper.make_node("Sigmoid", [r_pre], [r_gate]),
            helper.make_node("Mul", [r_gate, h_h], [reset_h]),
            helper.make_node("Add", [x_h, reset_h], [candidate_pre]),
            helper.make_node("Tanh", [candidate_pre], [candidate]),
            helper.make_node("Mul", [z_gate, state_2d], [kept]),
            helper.make_node("Sub", [one, z_gate], [one_minus_z]),
            helper.make_node("Mul", [one_minus_z, candidate], [proposed]),
            helper.make_node("Add", [kept, proposed], [state_new]),
        ]
    )

    state_new_3d = "state_new_3d"
    decoder_input = "decoder_input"
    builder.nodes.extend(
        [
            helper.make_node(
                "Reshape", [state_new, state_3d_shape], [state_new_3d]
            ),
            helper.make_node(
                "Concat", [rnn_input_3d, state_new_3d], [decoder_input], axis=-1
            ),
        ]
    )
    decoder_features = builder.fc_block(
        decoder_input,
        "decoder_stack",
        params.output_weight,
        params.output_bias,
        params.output_gamma,
        params.output_beta,
    )
    controls_raw = builder.dense(
        decoder_features,
        "controls",
        params.controls_weight,
        params.controls_bias,
    )
    amplitude_raw, harmonics_raw, noise_raw = (
        "amplitude_raw",
        "harmonics_raw",
        "noise_raw",
    )
    builder.split(
        controls_raw,
        [amplitude_raw, harmonics_raw, noise_raw],
        "controls",
        [1, 60, 65],
    )

    amplitude_3d = builder.exp_sigmoid(amplitude_raw, "amplitude")
    harmonics_positive = builder.exp_sigmoid(harmonics_raw, "harmonics")
    harmonics_3d = builder.normalize_harmonics(harmonics_positive, f0_3d)
    noise_3d = builder.exp_sigmoid(noise_raw, "noise", bias=-5.0)
    builder.nodes.extend(
        [
            helper.make_node(
                "Reshape", [amplitude_3d, amplitude_shape], ["amplitude"]
            ),
            helper.make_node(
                "Reshape", [harmonics_3d, harmonics_shape], ["harmonics"]
            ),
            helper.make_node(
                "Reshape", [noise_3d, noise_shape], ["noise_amps"]
            ),
            helper.make_node(
                "Reshape", [state_new, state_output_shape], ["state_out"]
            ),
        ]
    )

    inputs = [
        helper.make_tensor_value_info("state", TensorProto.FLOAT, [512]),
        helper.make_tensor_value_info("f0_scaled", TensorProto.FLOAT, [1]),
        helper.make_tensor_value_info("pw_scaled", TensorProto.FLOAT, [1]),
    ]
    outputs = [
        helper.make_tensor_value_info("amplitude", TensorProto.FLOAT, [1]),
        helper.make_tensor_value_info("harmonics", TensorProto.FLOAT, [60]),
        helper.make_tensor_value_info("noise_amps", TensorProto.FLOAT, [65]),
        helper.make_tensor_value_info("state_out", TensorProto.FLOAT, [512]),
    ]
    graph = helper.make_graph(
        builder.nodes,
        "DDSP_VST_PredictControls",
        inputs,
        outputs,
        initializer=builder.initializers,
    )
    model = helper.make_model(
        graph,
        producer_name="ascend310-case3-ddsp-vst-exporter",
        opset_imports=[helper.make_opsetid("", opset)],
    )
    model.ir_version = min(model.ir_version, 10)
    checker.check_model(model)
    model = shape_inference.infer_shapes(model)
    checker.check_model(model)
    return model


def verify_onnx(model_path: Path, steps: int = 8) -> dict[str, object]:
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise RuntimeError(
            "Missing ONNX Runtime. Install it with: "
            "python -m pip install onnxruntime"
        ) from exc

    session = ort.InferenceSession(
        str(model_path), providers=["CPUExecutionProvider"]
    )
    expected_inputs = {
        "state": [512],
        "f0_scaled": [1],
        "pw_scaled": [1],
    }
    expected_outputs = {
        "amplitude": [1],
        "harmonics": [60],
        "noise_amps": [65],
        "state_out": [512],
    }
    actual_inputs = {value.name: value.shape for value in session.get_inputs()}
    actual_outputs = {value.name: value.shape for value in session.get_outputs()}
    if actual_inputs != expected_inputs:
        raise AssertionError(f"Unexpected ONNX inputs: {actual_inputs}")
    if actual_outputs != expected_outputs:
        raise AssertionError(f"Unexpected ONNX outputs: {actual_outputs}")

    state = np.zeros(512, dtype=np.float32)
    amplitude_values = []
    state_delta_values = []
    rng = np.random.default_rng(20260718)
    for step in range(steps):
        f0_scaled = np.asarray([0.45 + 0.1 * rng.random()], dtype=np.float32)
        pw_scaled = np.asarray([0.2 + 0.7 * rng.random()], dtype=np.float32)
        outputs = session.run(
            None,
            {
                "state": state,
                "f0_scaled": f0_scaled,
                "pw_scaled": pw_scaled,
            },
        )
        amplitude, harmonics, noise_amps, next_state = outputs
        for name, value in zip(expected_outputs, outputs):
            if not np.all(np.isfinite(value)):
                raise AssertionError(f"Non-finite values in output {name}")
        if np.any(amplitude <= 0.0) or np.any(noise_amps <= 0.0):
            raise AssertionError("DDSP exp-sigmoid outputs must be positive")
        if np.any(harmonics < 0.0):
            raise AssertionError("Normalized harmonics must be non-negative")
        if not np.isclose(float(harmonics.sum()), 1.0, atol=1e-5):
            raise AssertionError(
                f"Normalized harmonics must sum to 1, got {harmonics.sum()}"
            )
        amplitude_values.append(float(amplitude[0]))
        state_delta_values.append(float(np.linalg.norm(next_state - state)))
        state = next_state.astype(np.float32, copy=True)

    if max(state_delta_values) <= 1e-6:
        raise AssertionError("GRU state did not change during verification")

    return {
        "model": str(model_path),
        "providers": session.get_providers(),
        "inputs": actual_inputs,
        "outputs": actual_outputs,
        "steps": steps,
        "amplitude_min": min(amplitude_values),
        "amplitude_max": max(amplitude_values),
        "state_delta_min": min(state_delta_values),
        "state_delta_max": max(state_delta_values),
        "status": "passed",
    }


def compare_tflite_onnx(
    tflite_path: Path, onnx_path: Path, steps: int = 8
) -> dict[str, object]:
    """Compare stateful TFLite and ONNX outputs on identical inputs."""
    try:
        import tensorflow as tf
    except ImportError as exc:
        raise RuntimeError(
            "TensorFlow is required for --compare-tflite. Install a CPU build "
            "such as: python -m pip install tensorflow-cpu"
        ) from exc
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise RuntimeError(
            "Missing ONNX Runtime. Install it with: "
            "python -m pip install onnxruntime"
        ) from exc

    interpreter = tf.lite.Interpreter(model_path=str(tflite_path), num_threads=1)
    interpreter.allocate_tensors()
    input_details = interpreter.get_input_details()
    output_details = interpreter.get_output_details()
    session = ort.InferenceSession(
        str(onnx_path), providers=["CPUExecutionProvider"]
    )

    keys = ["amplitude", "harmonics", "noise_amps", "state_out"]
    max_abs = {key: 0.0 for key in keys}
    max_rel = {key: 0.0 for key in keys}
    rng = np.random.default_rng(20260718)
    state = np.zeros(512, dtype=np.float32)

    for _ in range(steps):
        f0_scaled = np.asarray([0.1 + 0.8 * rng.random()], dtype=np.float32)
        pw_scaled = np.asarray([0.1 + 0.8 * rng.random()], dtype=np.float32)
        for item in input_details:
            name = item["name"]
            if "state" in name:
                value = state
            elif "f0" in name:
                value = f0_scaled
            elif "pw" in name or "loudness" in name:
                value = pw_scaled
            else:
                raise AssertionError(f"Unknown TFLite input: {name}")
            interpreter.set_tensor(item["index"], value.reshape(item["shape"]))
        interpreter.invoke()

        tflite_outputs: dict[str, np.ndarray] = {}
        for item in output_details:
            name = item["name"]
            value = interpreter.get_tensor(item["index"]).astype(
                np.float32, copy=False
            ).reshape(-1)
            if "StatefulPartitionedCall:0" in name:
                key = "amplitude"
            elif "StatefulPartitionedCall:1" in name:
                key = "harmonics"
            elif "StatefulPartitionedCall:2" in name:
                key = "noise_amps"
            elif "StatefulPartitionedCall:3" in name:
                key = "state_out"
            else:
                raise AssertionError(f"Unknown TFLite output: {name}")
            tflite_outputs[key] = value

        onnx_values = session.run(
            None,
            {
                "state": state,
                "f0_scaled": f0_scaled,
                "pw_scaled": pw_scaled,
            },
        )
        onnx_outputs = {
            key: value.reshape(-1) for key, value in zip(keys, onnx_values)
        }
        for key in keys:
            difference = np.abs(onnx_outputs[key] - tflite_outputs[key])
            denominator = np.maximum(np.abs(tflite_outputs[key]), 1e-6)
            max_abs[key] = max(max_abs[key], float(difference.max()))
            max_rel[key] = max(
                max_rel[key], float((difference / denominator).max())
            )
        state = tflite_outputs["state_out"].copy()

    absolute_tolerance = 1e-5
    relative_tolerances: dict[str, float | None] = {
        "amplitude": 2e-2,
        "harmonics": 2e-2,
        "noise_amps": 2e-2,
        # Near-zero recurrent-state values make relative error unstable.
        # State parity is enforced by the strict absolute tolerance instead.
        "state_out": None,
    }
    if max(max_abs.values()) > absolute_tolerance:
        raise AssertionError(f"TFLite/ONNX maximum absolute error is too high: {max_abs}")
    if any(
        tolerance is not None and max_rel[key] > tolerance
        for key, tolerance in relative_tolerances.items()
    ):
        raise AssertionError(f"TFLite/ONNX maximum relative error is too high: {max_rel}")

    return {
        "steps": steps,
        "max_absolute_error": max_abs,
        "max_relative_error": max_rel,
        "absolute_tolerance": absolute_tolerance,
        "relative_tolerance": relative_tolerances,
        "status": "passed",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export a DDSP-VST TFLite predict-controls model to ONNX"
    )
    parser.add_argument("--tflite", type=Path, default=DEFAULT_TFLITE)
    parser.add_argument("--output", type=Path, default=DEFAULT_ONNX)
    parser.add_argument("--opset", type=int, default=11)
    parser.add_argument("--verify-steps", type=int, default=8)
    parser.add_argument(
        "--compare-tflite",
        action="store_true",
        help="Also compare ONNX outputs against TensorFlow Lite",
    )
    parser.add_argument(
        "--skip-verify", action="store_true", help="Do not run ONNX Runtime"
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.tflite.exists():
        raise FileNotFoundError(f"TFLite model not found: {args.tflite}")
    if args.verify_steps < 1:
        raise ValueError("--verify-steps must be at least 1")

    params = load_parameters(args.tflite)
    model = build_model(params, opset=args.opset)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    onnx, *_ = _import_onnx()
    onnx.save(model, str(args.output))
    print(f"[ONNX] Saved: {args.output}")

    metadata = {
        "source_tflite": str(args.tflite),
        "onnx_model": str(args.output),
        "opset": args.opset,
        "input_order": ["state", "f0_scaled", "pw_scaled"],
        "output_order": ["amplitude", "harmonics", "noise_amps", "state_out"],
        "state_size": 512,
        "num_harmonics": 60,
        "num_noise_amps": 65,
        "sample_rate": 16000,
        "frame_rate": 50,
        "hop_size": 320,
    }
    if not args.skip_verify:
        report = verify_onnx(args.output, steps=args.verify_steps)
        metadata["verification"] = report
        print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.compare_tflite:
        parity_report = compare_tflite_onnx(
            args.tflite, args.output, steps=args.verify_steps
        )
        metadata["tflite_parity"] = parity_report
        print(json.dumps(parity_report, ensure_ascii=False, indent=2))

    metadata_path = args.output.with_suffix(".json")
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"[META] Saved: {metadata_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
