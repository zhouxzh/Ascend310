#!/usr/bin/env python3
"""Export the official MIDI-DDSP TensorFlow checkpoints to static ONNX models."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import sys
import types
from typing import Any, Callable, Sequence

import numpy as np


CONDITIONING_NAMES = (
    "volume",
    "vol_fluc",
    "vibrato",
    "brightness",
    "attack",
    "vol_peak_pos",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-dir",
        type=Path,
        default=Path("_upstream/midi-ddsp"),
        help="Official magenta/midi-ddsp checkout.",
    )
    parser.add_argument(
        "--weights-dir",
        type=Path,
        default=Path(
            "models/midi_ddsp/weights/"
            "midi_ddsp_model_weights_urmp_9_10"
        ),
        help="Directory containing expression_generator and synthesis_generator.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("models/midi_ddsp/onnx"),
    )
    parser.add_argument(
        "--component",
        choices=("expression", "synthesis", "all"),
        default="all",
    )
    parser.add_argument("--expression-length", type=int, default=32)
    parser.add_argument("--synthesis-length", type=int, default=64)
    parser.add_argument("--opset", type=int, default=13)
    parser.add_argument("--seed", type=int, default=20260722)
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def package_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "missing"


def bootstrap_midi_ddsp(source_dir: Path) -> tuple[Any, Any, Any]:
    """Import only the inference modules, avoiding unused legacy dependencies."""
    source_package = (source_dir / "midi_ddsp").resolve()
    if not source_package.is_dir():
        raise FileNotFoundError(f"MIDI-DDSP source package not found: {source_package}")

    # DDSP imports CREPE for training losses, but these exports do not use it.
    sys.modules.setdefault("crepe", types.ModuleType("crepe"))
    hmmlearn = sys.modules.setdefault("hmmlearn", types.ModuleType("hmmlearn"))
    if not hasattr(hmmlearn, "hmm"):
        hmmlearn.hmm = types.ModuleType("hmmlearn.hmm")
    sys.modules.setdefault("hmmlearn.hmm", hmmlearn.hmm)

    import ddsp  # pylint: disable=import-outside-toplevel

    # ddsp.training.__init__ imports cloud and dataset tooling not needed here.
    training = types.ModuleType("ddsp.training")
    training.__path__ = [str(Path(ddsp.__file__).resolve().parent / "training")]
    sys.modules["ddsp.training"] = training
    ddsp.training = training
    from ddsp.training import decoders, nn  # pylint: disable=import-outside-toplevel

    training.decoders = decoders
    training.nn = nn

    # Avoid midi_ddsp.__init__, which imports the command-line audio pipeline.
    midi_package = types.ModuleType("midi_ddsp")
    midi_package.__path__ = [str(source_package)]
    sys.modules["midi_ddsp"] = midi_package

    from midi_ddsp.modules.expression_generator import (  # pylint: disable=import-outside-toplevel
        ExpressionGenerator,
        get_fake_data_expression_generator,
    )
    from midi_ddsp.modules.get_synthesis_generator import (  # pylint: disable=import-outside-toplevel
        get_fake_data_synthesis_generator,
        get_synthesis_generator,
    )
    from midi_ddsp.hparams_synthesis_generator import (  # pylint: disable=import-outside-toplevel
        hparams,
    )

    expression_api = (ExpressionGenerator, get_fake_data_expression_generator)
    synthesis_api = (
        get_synthesis_generator,
        get_fake_data_synthesis_generator,
        hparams,
    )
    return ddsp, expression_api, synthesis_api


def read_hparams(train_log: Path) -> dict[str, Any]:
    configs: list[dict[str, Any]] = []
    for line in train_log.read_text(encoding="utf-8", errors="replace").splitlines():
        marker = " - INFO: {"
        if marker not in line:
            continue
        value = ast.literal_eval(line[line.index(marker) + len(" - INFO: ") :])
        if isinstance(value, dict):
            configs.append(value)
    if not configs:
        raise ValueError(f"No hyperparameter dictionary found in {train_log}")
    return configs[0]


def make_expression_inputs(tf: Any, length: int) -> tuple[Any, ...]:
    pitch = np.zeros((1, length), dtype=np.int64)
    if length > 2:
        pitch[:, 1:-1] = 60 + (np.arange(length - 2) % 8)
    note_length = np.full((1, length, 1), 0.25, dtype=np.float32)
    note_length[:, 0, :] = 0.04
    instrument_id = np.array([0], dtype=np.int64)
    return tuple(tf.convert_to_tensor(value) for value in (pitch, note_length, instrument_id))


def make_synthesis_inputs(tf: Any, length: int, seed: int) -> tuple[Any, ...]:
    rng = np.random.default_rng(seed)
    conditioning = [
        rng.uniform(0.15, 0.85, (1, length, 1)).astype(np.float32)
        for _ in CONDITIONING_NAMES
    ]
    q_pitch = np.zeros((1, length, 1), dtype=np.float32)
    onsets = np.zeros((1, length), dtype=np.int64)
    offsets = np.zeros((1, length), dtype=np.int64)
    note_span = 16
    for start in range(0, length, note_span):
        stop = min(start + note_span, length)
        q_pitch[:, start:stop, :] = 60 + ((start // note_span) % 5)
        onsets[:, start] = 1
        offsets[:, stop - 1] = 1
    instrument_id = np.array([0], dtype=np.int64)
    values = (*conditioning, q_pitch, onsets, offsets, instrument_id)
    return tuple(tf.convert_to_tensor(value) for value in values)


def tensor_arrays(values: Sequence[Any]) -> list[np.ndarray]:
    return [np.asarray(value.numpy() if hasattr(value, "numpy") else value) for value in values]


def output_metrics(reference: np.ndarray, actual: np.ndarray) -> dict[str, Any]:
    reference = np.asarray(reference, dtype=np.float64)
    actual = np.asarray(actual, dtype=np.float64)
    diff = actual - reference
    denominator = float(np.linalg.norm(reference.ravel()))
    actual_norm = float(np.linalg.norm(actual.ravel()))
    cosine_denominator = denominator * actual_norm
    cosine = (
        float(np.dot(reference.ravel(), actual.ravel()) / cosine_denominator)
        if cosine_denominator
        else 1.0
    )
    return {
        "shape": list(actual.shape),
        "finite": bool(np.isfinite(actual).all()),
        "max_abs_error": float(np.max(np.abs(diff))),
        "mean_abs_error": float(np.mean(np.abs(diff))),
        "nrmse": float(np.linalg.norm(diff.ravel()) / max(denominator, 1e-12)),
        "cosine_similarity": cosine,
    }


def prune_unused_opset_imports(model: Any) -> list[tuple[str, int]]:
    """Remove converter-added opset domains that have no nodes in the graph."""
    used_domains = {node.domain for node in model.graph.node}
    retained = [
        item for item in model.opset_import if item.domain == "" or item.domain in used_domains
    ]
    removed = [
        (item.domain, int(item.version))
        for item in model.opset_import
        if item.domain != "" and item.domain not in used_domains
    ]
    if removed:
        del model.opset_import[:]
        model.opset_import.extend(retained)
    return removed


def decompose_batch_normalization_for_atc(model: Any) -> int:
    """Replace inference BatchNormalization with format-agnostic arithmetic."""
    import onnx  # pylint: disable=import-outside-toplevel
    from onnx import helper, numpy_helper  # pylint: disable=import-outside-toplevel

    inferred = onnx.shape_inference.infer_shapes(model)
    ranks: dict[str, int] = {}
    for value in (
        list(inferred.graph.input)
        + list(inferred.graph.value_info)
        + list(inferred.graph.output)
    ):
        tensor_type = value.type.tensor_type
        if tensor_type.HasField("shape"):
            ranks[value.name] = len(tensor_type.shape.dim)

    replacement_nodes = []
    new_initializers = []
    decomposed = 0
    for node in model.graph.node:
        if node.op_type != "BatchNormalization":
            replacement_nodes.append(node)
            continue
        if len(node.input) < 5 or len(node.output) != 1:
            raise ValueError(
                f"Unsupported BatchNormalization signature for {node.name}: "
                f"{len(node.input)} inputs, {len(node.output)} outputs"
            )
        rank = ranks.get(node.input[0])
        if rank is None or rank < 2:
            raise ValueError(f"Cannot determine BatchNormalization rank: {node.name}")

        epsilon = 1e-5
        for attribute in node.attribute:
            if attribute.name == "epsilon":
                epsilon = float(helper.get_attribute_value(attribute))

        base = node.name or f"BatchNormalization_{decomposed}"
        shape_name = f"{base}/atc_channel_shape"
        epsilon_name = f"{base}/atc_epsilon"
        channel_shape = np.asarray([1, -1] + [1] * (rank - 2), dtype=np.int64)
        new_initializers.append(numpy_helper.from_array(channel_shape, shape_name))
        new_initializers.append(
            numpy_helper.from_array(np.asarray(epsilon, dtype=np.float32), epsilon_name)
        )

        reshaped_names = []
        for label, parameter in zip(
            ("scale", "bias", "mean", "variance"), node.input[1:5]
        ):
            output_name = f"{base}/atc_{label}_4d"
            replacement_nodes.append(
                helper.make_node(
                    "Reshape",
                    [parameter, shape_name],
                    [output_name],
                    name=f"{base}/atc_reshape_{label}",
                )
            )
            reshaped_names.append(output_name)

        scale, bias, mean, variance = reshaped_names
        centered = f"{base}/atc_centered"
        variance_epsilon = f"{base}/atc_variance_epsilon"
        standard_deviation = f"{base}/atc_standard_deviation"
        normalized = f"{base}/atc_normalized"
        scaled = f"{base}/atc_scaled"
        replacement_nodes.extend(
            (
                helper.make_node(
                    "Sub", [node.input[0], mean], [centered], name=f"{base}/atc_sub"
                ),
                helper.make_node(
                    "Add",
                    [variance, epsilon_name],
                    [variance_epsilon],
                    name=f"{base}/atc_add_epsilon",
                ),
                helper.make_node(
                    "Sqrt",
                    [variance_epsilon],
                    [standard_deviation],
                    name=f"{base}/atc_sqrt",
                ),
                helper.make_node(
                    "Div",
                    [centered, standard_deviation],
                    [normalized],
                    name=f"{base}/atc_div",
                ),
                helper.make_node(
                    "Mul", [normalized, scale], [scaled], name=f"{base}/atc_mul"
                ),
                helper.make_node(
                    "Add", [scaled, bias], [node.output[0]], name=f"{base}/atc_add_bias"
                ),
            )
        )
        decomposed += 1

    if decomposed:
        del model.graph.node[:]
        model.graph.node.extend(replacement_nodes)
        model.graph.initializer.extend(new_initializers)
    return decomposed


def align_one_hot_types_for_atc(model: Any) -> int:
    """Cast OneHot indices to the depth type for CANN's internal Mod node."""
    import onnx  # pylint: disable=import-outside-toplevel
    from onnx import helper  # pylint: disable=import-outside-toplevel

    inferred = onnx.shape_inference.infer_shapes(model)
    element_types: dict[str, int] = {}
    for value in (
        list(inferred.graph.input)
        + list(inferred.graph.value_info)
        + list(inferred.graph.output)
    ):
        tensor_type = value.type.tensor_type
        if tensor_type.HasField("elem_type"):
            element_types[value.name] = int(tensor_type.elem_type)
    for initializer in inferred.graph.initializer:
        element_types[initializer.name] = int(initializer.data_type)

    replacement_nodes = []
    aligned = 0
    for node in model.graph.node:
        if node.op_type != "OneHot" or len(node.input) < 2:
            replacement_nodes.append(node)
            continue
        indices_type = element_types.get(node.input[0])
        depth_type = element_types.get(node.input[1])
        if indices_type is None or depth_type is None or indices_type == depth_type:
            replacement_nodes.append(node)
            continue
        cast_output = f"{node.name or 'OneHot'}/atc_indices"
        replacement_nodes.append(
            helper.make_node(
                "Cast",
                [node.input[0]],
                [cast_output],
                name=f"{node.name or 'OneHot'}/atc_cast_indices",
                to=depth_type,
            )
        )
        node.input[0] = cast_output
        replacement_nodes.append(node)
        aligned += 1

    if aligned:
        del model.graph.node[:]
        model.graph.node.extend(replacement_nodes)
    return aligned


def export_and_validate(
    tf: Any,
    function: Callable[..., Sequence[Any]],
    signature: Sequence[Any],
    inputs: Sequence[Any],
    output_names: Sequence[str],
    output_path: Path,
    reference_path: Path,
    opset: int,
) -> dict[str, Any]:
    import onnx  # pylint: disable=import-outside-toplevel
    import onnxruntime as ort  # pylint: disable=import-outside-toplevel
    import tf2onnx  # pylint: disable=import-outside-toplevel

    reference = tensor_arrays(function(*inputs))
    if not all(np.isfinite(value).all() for value in reference):
        raise ValueError("TensorFlow reference contains NaN or Inf")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    tf2onnx.convert.from_function(
        function,
        input_signature=signature,
        opset=opset,
        output_path=str(output_path),
    )
    model = onnx.load(str(output_path))
    removed_opset_imports = prune_unused_opset_imports(model)
    decomposed_batch_normalization = decompose_batch_normalization_for_atc(model)
    aligned_one_hot_types = align_one_hot_types_for_atc(model)
    if removed_opset_imports or decomposed_batch_normalization or aligned_one_hot_types:
        onnx.save(model, str(output_path))
    onnx.checker.check_model(model)

    session = ort.InferenceSession(str(output_path), providers=["CPUExecutionProvider"])
    session_inputs = session.get_inputs()
    input_arrays = tensor_arrays(inputs)
    if len(session_inputs) != len(input_arrays):
        raise ValueError(
            f"ONNX input count mismatch: {len(session_inputs)} != {len(input_arrays)}"
        )
    feed = {node.name: value for node, value in zip(session_inputs, input_arrays)}
    actual = session.run(None, feed)
    if len(actual) != len(reference):
        raise ValueError(f"ONNX output count mismatch: {len(actual)} != {len(reference)}")

    archive: dict[str, np.ndarray] = {}
    for node, value in zip(session_inputs, input_arrays):
        archive[f"input__{node.name.replace(':', '_')}"] = value
    for name, value in zip(output_names, reference):
        archive[f"tf__{name}"] = value
    for node, value in zip(session.get_outputs(), actual):
        archive[f"onnx__{node.name.replace(':', '_')}"] = value
    np.savez_compressed(reference_path, **archive)

    metrics = {
        name: output_metrics(expected, observed)
        for name, expected, observed in zip(output_names, reference, actual)
    }
    if not all(item["finite"] for item in metrics.values()):
        raise ValueError("ONNX Runtime output contains NaN or Inf")
    return {
        "onnx_path": str(output_path.resolve()),
        "onnx_bytes": output_path.stat().st_size,
        "onnx_sha256": sha256(output_path),
        "reference_path": str(reference_path.resolve()),
        "reference_sha256": sha256(reference_path),
        "removed_unused_opset_imports": removed_opset_imports,
        "decomposed_batch_normalization": decomposed_batch_normalization,
        "aligned_one_hot_types": aligned_one_hot_types,
        "inputs": [
            {"name": node.name, "shape": node.shape, "type": node.type}
            for node in session_inputs
        ],
        "outputs": [
            {"name": node.name, "shape": node.shape, "type": node.type}
            for node in session.get_outputs()
        ],
        "metrics": metrics,
    }


def export_expression(
    tf: Any,
    expression_api: tuple[Any, Any],
    weights_dir: Path,
    output_dir: Path,
    length: int,
    opset: int,
) -> dict[str, Any]:
    ExpressionGenerator, get_fake_data = expression_api
    tf.keras.backend.clear_session()
    model = ExpressionGenerator(n_out=6, nhid=128)
    fake = get_fake_data(6)
    model(fake["cond"], out=fake["target"], training=True)
    checkpoint = weights_dir / "expression_generator" / "5000"
    status = model.load_weights(str(checkpoint)).expect_partial()
    status.assert_existing_objects_matched()

    signature = (
        tf.TensorSpec((1, length), tf.int64, name="note_pitch"),
        tf.TensorSpec((1, length, 1), tf.float32, name="note_length"),
        tf.TensorSpec((1,), tf.int64, name="instrument_id"),
    )

    @tf.function(input_signature=signature)
    def inference(note_pitch: Any, note_length: Any, instrument_id: Any) -> tuple[Any]:
        outputs = model(
            {
                "note_pitch": note_pitch,
                "note_length": note_length,
                "instrument_id": instrument_id,
            },
            out=None,
            training=False,
        )
        return (tf.identity(outputs["output"], name="expression_controls"),)

    stem = f"midi_ddsp_expression_notes{length}"
    result = export_and_validate(
        tf,
        inference,
        signature,
        make_expression_inputs(tf, length),
        ("expression_controls",),
        output_dir / f"{stem}.onnx",
        output_dir / f"{stem}_reference.npz",
        opset,
    )
    result.update(
        {
            "component": "expression_generator",
            "static_note_length": length,
            "checkpoint_prefix": str(checkpoint.resolve()),
            "checkpoint_index_sha256": sha256(checkpoint.with_suffix(".index")),
        }
    )
    return result


def export_synthesis(
    tf: Any,
    synthesis_api: tuple[Any, Any, Any],
    weights_dir: Path,
    output_dir: Path,
    length: int,
    opset: int,
    seed: int,
) -> dict[str, Any]:
    get_synthesis_generator, get_fake_data, hparams = synthesis_api
    tf.keras.backend.clear_session()
    hparams_dict = read_hparams(weights_dir / "synthesis_generator" / "train.log")
    for key, value in hparams_dict.items():
        setattr(hparams, key, value)
    hparams.sequence_length = length

    model = get_synthesis_generator(hparams)
    model._build(get_fake_data(hparams))  # pylint: disable=protected-access
    checkpoint = weights_dir / "synthesis_generator" / "50000"
    status = model.load_weights(str(checkpoint)).expect_partial()
    status.assert_existing_objects_matched()

    signature = tuple(
        tf.TensorSpec((1, length, 1), tf.float32, name=name)
        for name in CONDITIONING_NAMES
    ) + (
        tf.TensorSpec((1, length, 1), tf.float32, name="q_pitch"),
        tf.TensorSpec((1, length), tf.int64, name="onsets"),
        tf.TensorSpec((1, length), tf.int64, name="offsets"),
        tf.TensorSpec((1,), tf.int64, name="instrument_id"),
    )

    @tf.function(input_signature=signature)
    def inference(*values: Any) -> tuple[Any, ...]:
        conditioning = dict(zip(CONDITIONING_NAMES, values[:6]))
        q_pitch, onsets, offsets, instrument_id = values[6:]
        midi_features = (q_pitch, q_pitch, q_pitch, onsets, offsets)
        _, params = model.midi_decoder.gen_params_from_cond(
            conditioning,
            midi_features,
            instrument_id=instrument_id,
            training=False,
            display_progressbar=False,
        )
        return (
            tf.identity(params["f0_hz"], name="f0_hz"),
            tf.identity(params["amplitudes"], name="amplitudes"),
            tf.identity(
                params["harmonic_distribution"], name="harmonic_distribution"
            ),
            tf.identity(params["noise_magnitudes"], name="noise_magnitudes"),
        )

    stem = f"midi_ddsp_synthesis_params_frames{length}"
    result = export_and_validate(
        tf,
        inference,
        signature,
        make_synthesis_inputs(tf, length, seed),
        ("f0_hz", "amplitudes", "harmonic_distribution", "noise_magnitudes"),
        output_dir / f"{stem}.onnx",
        output_dir / f"{stem}_reference.npz",
        opset,
    )
    result.update(
        {
            "component": "synthesis_generator_parameter_network",
            "static_frame_length": length,
            "sample_rate": int(hparams.sample_rate),
            "frame_size": int(hparams.frame_size),
            "checkpoint_prefix": str(checkpoint.resolve()),
            "checkpoint_index_sha256": sha256(checkpoint.with_suffix(".index")),
            "excludes": ["DDSP oscillators", "filtered-noise DSP", "reverb"],
        }
    )
    return result


def main() -> int:
    args = parse_args()
    if args.expression_length < 1 or args.synthesis_length < 1:
        raise ValueError("Static sequence lengths must be positive")

    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "1")
    os.environ.setdefault("TF_ENABLE_ONEDNN_OPTS", "0")
    import tensorflow as tf  # pylint: disable=import-outside-toplevel

    tf.random.set_seed(args.seed)
    np.random.seed(args.seed)
    tf.config.threading.set_inter_op_parallelism_threads(1)
    tf.config.threading.set_intra_op_parallelism_threads(1)

    _, expression_api, synthesis_api = bootstrap_midi_ddsp(args.source_dir)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    report: dict[str, Any] = {
        "seed": args.seed,
        "opset": args.opset,
        "source_dir": str(args.source_dir.resolve()),
        "weights_dir": str(args.weights_dir.resolve()),
        "versions": {
            "python": sys.version.split()[0],
            "tensorflow": package_version("tensorflow-intel"),
            "ddsp": package_version("ddsp"),
            "tf2onnx": package_version("tf2onnx"),
            "onnx": package_version("onnx"),
            "onnxruntime": package_version("onnxruntime"),
        },
        "models": [],
    }

    if args.component in ("expression", "all"):
        print(f"[EXPORT] expression generator, notes={args.expression_length}")
        report["models"].append(
            export_expression(
                tf,
                expression_api,
                args.weights_dir.resolve(),
                output_dir,
                args.expression_length,
                args.opset,
            )
        )
    if args.component in ("synthesis", "all"):
        print(f"[EXPORT] synthesis parameter network, frames={args.synthesis_length}")
        report["models"].append(
            export_synthesis(
                tf,
                synthesis_api,
                args.weights_dir.resolve(),
                output_dir,
                args.synthesis_length,
                args.opset,
                args.seed,
            )
        )

    report_name = (
        "export_report.json"
        if args.component == "all"
        else f"export_report_{args.component}.json"
    )
    report_path = output_dir / report_name
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"[OK] report={report_path}")
    for model in report["models"]:
        print(
            f"[OK] {model['component']}: {model['onnx_path']} "
            f"sha256={model['onnx_sha256']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
