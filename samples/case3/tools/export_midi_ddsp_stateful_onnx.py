#!/usr/bin/env python3
"""Export state-continuous MIDI-DDSP component models for Ascend.

This script runs only in the local export environment. It decomposes the
official bidirectional and autoregressive networks into fixed-size blocks with
explicit state I/O, so block boundaries do not reset musical context.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Callable

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.export_midi_ddsp_onnx import (
    CONDITIONING_NAMES,
    bootstrap_midi_ddsp,
    export_and_validate,
    package_version,
    read_hparams,
    sha256,
)


SOURCE_COMMIT = "d7af42704a63b47267ae6a1bc0fee1ed7dc5c855"
DEFAULT_SEED = 20260724
EXPRESSION_BLOCK = 32
SYNTHESIS_BLOCK = 64
F0_BINS = 201
TIMBRE_HALO = 124
TIMBRE_WINDOW = SYNTHESIS_BLOCK + 2 * TIMBRE_HALO


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=Path("_upstream/midi-ddsp"))
    parser.add_argument(
        "--weights-dir",
        type=Path,
        default=Path(
            "models/midi_ddsp/weights/midi_ddsp_model_weights_urmp_9_10"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("models/midi_ddsp/stateful_v2/onnx"),
    )
    parser.add_argument("--opset", type=int, default=13)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    return parser.parse_args()


def _source_commit(source_dir: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(source_dir), "rev-parse", "HEAD"],
        text=True,
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _run_gru_cell(
    tf: Any,
    cell: Any,
    inputs: Any,
    state: Any,
    *,
    reverse: bool = False,
) -> tuple[Any, Any]:
    length = int(inputs.shape[1])
    indices = range(length - 1, -1, -1) if reverse else range(length)
    outputs = []
    for index in indices:
        output, states = cell(inputs[:, index, :], states=[state], training=False)
        state = states[0]
        outputs.append(output[:, tf.newaxis, :])
    if reverse:
        outputs.reverse()
    return tf.concat(outputs, axis=1), state


def _expression_embedding(tf: Any, model: Any, pitch: Any, length: Any, instrument: Any) -> Any:
    z_pitch = model.pitch_emb(pitch)
    z_duration = model.duration_emb(length)
    z_instrument = model.instrument_emb(
        tf.tile(instrument[:, tf.newaxis], [1, int(pitch.shape[1])])
    )
    return tf.concat([z_pitch, z_duration, z_instrument], axis=-1)


def _fixture_expression(seed: int) -> tuple[np.ndarray, ...]:
    rng = np.random.default_rng(seed)
    pitch = rng.integers(48, 78, size=(1, EXPRESSION_BLOCK), dtype=np.int64)
    pitch[:, 0] = 0
    length = rng.uniform(0.04, 0.8, (1, EXPRESSION_BLOCK, 1)).astype(np.float32)
    instrument = np.asarray([0], dtype=np.int64)
    state = rng.normal(0.0, 0.1, (1, 128)).astype(np.float32)
    return pitch, length, instrument, state


def _fixture_decoder(seed: int) -> tuple[np.ndarray, ...]:
    rng = np.random.default_rng(seed)
    context = rng.normal(0.0, 0.2, (1, EXPRESSION_BLOCK, 256)).astype(np.float32)
    pitch = rng.integers(48, 78, size=(1, EXPRESSION_BLOCK), dtype=np.int64)
    previous = np.zeros((1, 6), dtype=np.float32)
    state1 = np.zeros((1, 128), dtype=np.float32)
    state2 = np.zeros((1, 128), dtype=np.float32)
    return context, pitch, previous, state1, state2


def _fixture_precondition(seed: int) -> tuple[np.ndarray, ...]:
    rng = np.random.default_rng(seed)
    controls = tuple(
        rng.uniform(0.1, 0.9, (1, SYNTHESIS_BLOCK, 1)).astype(np.float32)
        for _ in CONDITIONING_NAMES
    )
    q_pitch = np.full((1, SYNTHESIS_BLOCK, 1), 69.0, dtype=np.float32)
    onsets = np.zeros((1, SYNTHESIS_BLOCK), dtype=np.int64)
    offsets = np.zeros((1, SYNTHESIS_BLOCK), dtype=np.int64)
    onsets[:, 0] = 1
    offsets[:, -1] = 1
    relative = np.linspace(1 / SYNTHESIS_BLOCK, 1.0, SYNTHESIS_BLOCK, dtype=np.float32)
    relative = relative.reshape(1, SYNTHESIS_BLOCK, 1)
    instrument = np.asarray([0], dtype=np.int64)
    return (*controls, q_pitch, onsets, offsets, relative, instrument)


def _export(
    tf: Any,
    function: Callable[..., tuple[Any, ...]],
    signature: tuple[Any, ...],
    fixtures: tuple[np.ndarray, ...],
    outputs: tuple[str, ...],
    output_dir: Path,
    name: str,
    opset: int,
) -> dict[str, Any]:
    tensor_fixtures = tuple(tf.convert_to_tensor(value) for value in fixtures)
    result = export_and_validate(
        tf,
        function,
        signature,
        tensor_fixtures,
        outputs,
        output_dir / f"{name}.onnx",
        output_dir / f"{name}_reference.npz",
        opset,
    )
    result["name"] = name
    result["file"] = f"{name}.onnx"
    result["logical_inputs"] = [str(item.name) for item in signature]
    result["logical_outputs"] = list(outputs)
    return result


def export_expression(
    tf: Any,
    expression_api: tuple[Any, Any],
    weights_dir: Path,
    output_dir: Path,
    opset: int,
    seed: int,
) -> list[dict[str, Any]]:
    ExpressionGenerator, get_fake_data = expression_api
    tf.keras.backend.clear_session()
    model = ExpressionGenerator(n_out=6, nhid=128)
    fake = get_fake_data(6)
    model(fake["cond"], out=fake["target"], training=True)
    checkpoint = weights_dir / "expression_generator" / "5000"
    model.load_weights(str(checkpoint)).expect_partial().assert_existing_objects_matched()

    context_signature = (
        tf.TensorSpec((1, EXPRESSION_BLOCK), tf.int64, name="note_pitch"),
        tf.TensorSpec((1, EXPRESSION_BLOCK, 1), tf.float32, name="note_length"),
        tf.TensorSpec((1,), tf.int64, name="instrument_id"),
        tf.TensorSpec((1, 128), tf.float32, name="state_in"),
    )

    def context_function(cell: Any, reverse: bool) -> Callable[..., tuple[Any, ...]]:
        @tf.function(input_signature=context_signature)
        def inference(note_pitch: Any, note_length: Any, instrument_id: Any, state_in: Any) -> tuple[Any, ...]:
            embedded = _expression_embedding(
                tf, model, note_pitch, note_length, instrument_id
            )
            context, state_out = _run_gru_cell(
                tf, cell, embedded, state_in, reverse=reverse
            )
            return (
                tf.identity(context, name="context"),
                tf.identity(state_out, name="state_out"),
            )

        return inference

    fixture = _fixture_expression(seed)
    results = [
        _export(
            tf,
            context_function(model.birnn.forward_layer.cell, False),
            context_signature,
            fixture,
            ("context", "state_out"),
            output_dir,
            "midi_ddsp_v2_expression_context_forward_notes32",
            opset,
        ),
        _export(
            tf,
            context_function(model.birnn.backward_layer.cell, True),
            context_signature,
            fixture,
            ("context", "state_out"),
            output_dir,
            "midi_ddsp_v2_expression_context_backward_notes32",
            opset,
        ),
    ]

    decoder_signature = (
        tf.TensorSpec((1, EXPRESSION_BLOCK, 256), tf.float32, name="context"),
        tf.TensorSpec((1, EXPRESSION_BLOCK), tf.int64, name="note_pitch"),
        tf.TensorSpec((1, 6), tf.float32, name="previous_controls"),
        tf.TensorSpec((1, 128), tf.float32, name="state1_in"),
        tf.TensorSpec((1, 128), tf.float32, name="state2_in"),
    )

    @tf.function(input_signature=decoder_signature)
    def decode(
        context: Any,
        note_pitch: Any,
        previous_controls: Any,
        state1_in: Any,
        state2_in: Any,
    ) -> tuple[Any, ...]:
        outputs = []
        previous = previous_controls
        state1 = state1_in
        state2 = state2_in
        for index in range(EXPRESSION_BLOCK):
            z_in = tf.concat(
                [context[:, index : index + 1, :], previous[:, tf.newaxis, :]],
                axis=-1,
            )
            z_out, state1 = model.rnn1(z_in, initial_state=state1, training=False)
            z_out, state2 = model.rnn2(z_out, initial_state=state2, training=False)
            decoded = model.decode_out(z_out)
            sampled = model.sample_out(
                decoded, note_pitch[:, index : index + 1, tf.newaxis]
            )["output"]
            outputs.append(sampled)
            previous = sampled[:, 0, :]
        return (
            tf.identity(tf.concat(outputs, axis=1), name="expression_controls"),
            tf.identity(previous, name="previous_controls_out"),
            tf.identity(state1, name="state1_out"),
            tf.identity(state2, name="state2_out"),
        )

    results.append(
        _export(
            tf,
            decode,
            decoder_signature,
            _fixture_decoder(seed + 1),
            (
                "expression_controls",
                "previous_controls_out",
                "state1_out",
                "state2_out",
            ),
            output_dir,
            "midi_ddsp_v2_expression_decode_notes32",
            opset,
        )
    )
    for result in results:
        result["checkpoint_index_sha256"] = sha256(checkpoint.with_suffix(".index"))
    return results


def export_synthesis(
    tf: Any,
    synthesis_api: tuple[Any, Any, Any],
    weights_dir: Path,
    output_dir: Path,
    opset: int,
    seed: int,
) -> list[dict[str, Any]]:
    get_model, get_fake_data, hparams = synthesis_api
    tf.keras.backend.clear_session()
    for key, value in read_hparams(
        weights_dir / "synthesis_generator" / "train.log"
    ).items():
        setattr(hparams, key, value)
    hparams.sequence_length = SYNTHESIS_BLOCK
    model = get_model(hparams)
    model._build(get_fake_data(hparams))  # pylint: disable=protected-access
    checkpoint = weights_dir / "synthesis_generator" / "50000"
    model.load_weights(str(checkpoint)).expect_partial().assert_existing_objects_matched()
    midi_decoder = model.midi_decoder
    params_decoder = midi_decoder.decoder
    f0_decoder = params_decoder.midi_to_f0

    precondition_signature = tuple(
        tf.TensorSpec((1, SYNTHESIS_BLOCK, 1), tf.float32, name=name)
        for name in CONDITIONING_NAMES
    ) + (
        tf.TensorSpec((1, SYNTHESIS_BLOCK, 1), tf.float32, name="q_pitch"),
        tf.TensorSpec((1, SYNTHESIS_BLOCK), tf.int64, name="onsets"),
        tf.TensorSpec((1, SYNTHESIS_BLOCK), tf.int64, name="offsets"),
        tf.TensorSpec((1, SYNTHESIS_BLOCK, 1), tf.float32, name="relative_position"),
        tf.TensorSpec((1,), tf.int64, name="instrument_id"),
    )

    @tf.function(input_signature=precondition_signature)
    def precondition(*values: Any) -> tuple[Any, ...]:
        controls = values[:6]
        q_pitch, onsets, offsets, relative_position, instrument_id = values[6:]
        z = tf.concat(
            [
                *controls,
                q_pitch / 127.0,
                tf.cast(onsets, tf.float32)[..., tf.newaxis],
                tf.cast(offsets, tf.float32)[..., tf.newaxis],
                relative_position,
            ],
            axis=-1,
        )
        z = midi_decoder.z_preconditioning_stack(z)
        instrument_z = tf.tile(
            midi_decoder.instrument_emb(instrument_id)[:, tf.newaxis, :],
            [1, SYNTHESIS_BLOCK, 1],
        )
        return (tf.identity(tf.concat([z, instrument_z], axis=-1), name="z_midi"),)

    results = [
        _export(
            tf,
            precondition,
            precondition_signature,
            _fixture_precondition(seed),
            ("z_midi",),
            output_dir,
            "midi_ddsp_v2_synthesis_precondition_frames64",
            opset,
        )
    ]

    context_signature = (
        tf.TensorSpec((1, SYNTHESIS_BLOCK, 320), tf.float32, name="z_midi"),
        tf.TensorSpec((1, 256), tf.float32, name="state_in"),
    )

    def synthesis_context(cell: Any, reverse: bool) -> Callable[..., tuple[Any, ...]]:
        @tf.function(input_signature=context_signature)
        def inference(z_midi: Any, state_in: Any) -> tuple[Any, ...]:
            context, state_out = _run_gru_cell(
                tf, cell, z_midi, state_in, reverse=reverse
            )
            return (
                tf.identity(context, name="context"),
                tf.identity(state_out, name="state_out"),
            )

        return inference

    rng = np.random.default_rng(seed + 2)
    context_fixture = (
        rng.normal(0.0, 0.2, (1, SYNTHESIS_BLOCK, 320)).astype(np.float32),
        np.zeros((1, 256), dtype=np.float32),
    )
    results.extend(
        [
            _export(
                tf,
                synthesis_context(f0_decoder.birnn.forward_layer.cell, False),
                context_signature,
                context_fixture,
                ("context", "state_out"),
                output_dir,
                "midi_ddsp_v2_synthesis_context_forward_frames64",
                opset,
            ),
            _export(
                tf,
                synthesis_context(f0_decoder.birnn.backward_layer.cell, True),
                context_signature,
                context_fixture,
                ("context", "state_out"),
                output_dir,
                "midi_ddsp_v2_synthesis_context_backward_frames64",
                opset,
            ),
        ]
    )

    f0_signature = (
        tf.TensorSpec((1, SYNTHESIS_BLOCK, 512), tf.float32, name="context"),
        tf.TensorSpec((1, SYNTHESIS_BLOCK, 1), tf.float32, name="q_pitch"),
        tf.TensorSpec((1, SYNTHESIS_BLOCK, F0_BINS), tf.float32, name="gumbel"),
        tf.TensorSpec((1, F0_BINS), tf.float32, name="previous_f0"),
        tf.TensorSpec((1, 256), tf.float32, name="state1_in"),
        tf.TensorSpec((1, 256), tf.float32, name="state2_in"),
    )

    @tf.function(input_signature=f0_signature)
    def decode_f0(
        context: Any,
        q_pitch: Any,
        gumbel: Any,
        previous_f0: Any,
        state1_in: Any,
        state2_in: Any,
    ) -> tuple[Any, ...]:
        logits_all = []
        f0_midi_all = []
        sampled_all = []
        previous = previous_f0
        state1 = state1_in
        state2 = state2_in
        for index in range(SYNTHESIS_BLOCK):
            z_in = tf.concat(
                [context[:, index : index + 1, :], previous[:, tf.newaxis, :]],
                axis=-1,
            )
            z_out, state1 = f0_decoder.rnn1(
                z_in, initial_state=state1, training=False
            )
            z_out, state2 = f0_decoder.rnn2(
                z_out, initial_state=state2, training=False
            )
            logits = f0_decoder.decode_out(z_out)[:, 0, :]
            logits_sorted = tf.sort(logits, direction="DESCENDING", axis=-1)
            probabilities = tf.nn.softmax(logits_sorted, axis=-1)
            cumulative = tf.cumsum(probabilities, axis=-1, exclusive=True)
            retained = tf.where(
                cumulative < 0.95,
                logits_sorted,
                tf.ones_like(logits_sorted) * 1000.0,
            )
            threshold = tf.reduce_min(retained, axis=-1, keepdims=True)
            top_p_logits = tf.where(
                logits < threshold,
                tf.ones_like(logits) * -1e10,
                logits,
            )
            sampled = tf.argmax(
                top_p_logits + gumbel[:, index, :],
                axis=-1,
                output_type=tf.int64,
            )
            previous = tf.one_hot(sampled, F0_BINS, dtype=tf.float32)
            deviation = tf.cast(sampled, tf.float32) / 100.0 - 1.0
            f0_midi = deviation + q_pitch[:, index, 0]
            logits_all.append(logits[:, tf.newaxis, :])
            sampled_all.append(sampled[:, tf.newaxis])
            f0_midi_all.append(f0_midi[:, tf.newaxis, tf.newaxis])
        f0_midi = tf.concat(f0_midi_all, axis=1)
        f0_hz = 440.0 * tf.pow(2.0, (f0_midi - 69.0) / 12.0)
        f0_hz = tf.where(q_pitch > 0.0, f0_hz, tf.zeros_like(f0_hz))
        return (
            tf.identity(f0_hz, name="f0_hz"),
            tf.identity(f0_midi, name="f0_midi"),
            tf.identity(tf.concat(logits_all, axis=1), name="f0_logits"),
            tf.identity(tf.concat(sampled_all, axis=1), name="sampled_bins"),
            tf.identity(previous, name="previous_f0_out"),
            tf.identity(state1, name="state1_out"),
            tf.identity(state2, name="state2_out"),
        )

    uniform = rng.uniform(
        np.finfo(np.float32).eps,
        1.0 - np.finfo(np.float32).eps,
        (1, SYNTHESIS_BLOCK, F0_BINS),
    ).astype(np.float32)
    gumbel = -np.log(-np.log(uniform)).astype(np.float32)
    f0_fixture = (
        rng.normal(0.0, 0.2, (1, SYNTHESIS_BLOCK, 512)).astype(np.float32),
        np.full((1, SYNTHESIS_BLOCK, 1), 69.0, dtype=np.float32),
        gumbel,
        np.zeros((1, F0_BINS), dtype=np.float32),
        np.zeros((1, 256), dtype=np.float32),
        np.zeros((1, 256), dtype=np.float32),
    )
    results.append(
        _export(
            tf,
            decode_f0,
            f0_signature,
            f0_fixture,
            (
                "f0_hz",
                "f0_midi",
                "f0_logits",
                "sampled_bins",
                "previous_f0_out",
                "state1_out",
                "state2_out",
            ),
            output_dir,
            "midi_ddsp_v2_synthesis_f0_decode_frames64",
            opset,
        )
    )

    timbre_signature = (
        tf.TensorSpec((1, TIMBRE_WINDOW, 320), tf.float32, name="z_midi"),
        tf.TensorSpec((1, TIMBRE_WINDOW, 1), tf.float32, name="f0_midi"),
    )

    @tf.function(input_signature=timbre_signature)
    def decode_timbre(z_midi: Any, f0_midi: Any) -> tuple[Any, ...]:
        z = tf.concat(
            [z_midi, params_decoder.q_pitch_emb(f0_midi / 127.0)], axis=-1
        )
        outputs = params_decoder.midi_f0_to_harmonic(z, training=False)
        return (
            tf.identity(outputs["amplitudes"], name="amplitudes"),
            tf.identity(
                outputs["harmonic_distribution"], name="harmonic_distribution"
            ),
            tf.identity(outputs["noise_magnitudes"], name="noise_magnitudes"),
        )

    timbre_fixture = (
        rng.normal(0.0, 0.2, (1, TIMBRE_WINDOW, 320)).astype(np.float32),
        rng.uniform(45.0, 80.0, (1, TIMBRE_WINDOW, 1)).astype(np.float32),
    )
    results.append(
        _export(
            tf,
            decode_timbre,
            timbre_signature,
            timbre_fixture,
            ("amplitudes", "harmonic_distribution", "noise_magnitudes"),
            output_dir,
            f"midi_ddsp_v2_synthesis_timbre_frames{TIMBRE_WINDOW}",
            opset,
        )
    )
    for result in results:
        result["checkpoint_index_sha256"] = sha256(checkpoint.with_suffix(".index"))
    return results


def main() -> int:
    args = parse_args()
    source_dir = args.source_dir.resolve()
    source_commit = _source_commit(source_dir)
    if source_commit != SOURCE_COMMIT:
        raise ValueError(
            f"MIDI-DDSP source commit is {source_commit}; expected {SOURCE_COMMIT}"
        )

    import tensorflow as tf  # pylint: disable=import-outside-toplevel

    tf.keras.utils.set_random_seed(args.seed)
    tf.config.threading.set_inter_op_parallelism_threads(1)
    tf.config.threading.set_intra_op_parallelism_threads(1)
    _, expression_api, synthesis_api = bootstrap_midi_ddsp(source_dir)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    weights_dir = args.weights_dir.resolve()

    components = export_expression(
        tf, expression_api, weights_dir, output_dir, args.opset, args.seed
    )
    components.extend(
        export_synthesis(
            tf,
            synthesis_api,
            weights_dir,
            output_dir,
            args.opset,
            args.seed,
        )
    )
    manifest = {
        "schema_version": 1,
        "id": "google-urmp-stateful-v2-mixed_float16",
        "name": "Google URMP stateful v2",
        "architecture": "stateful-v2",
        "precision": "mixed_float16",
        "recommended": True,
        "quality_status": "requires_om_validation",
        "source_commit": source_commit,
        "seed": args.seed,
        "expression_block": EXPRESSION_BLOCK,
        "synthesis_block": SYNTHESIS_BLOCK,
        "timbre_halo": TIMBRE_HALO,
        "versions": {
            "tensorflow": package_version("tensorflow"),
            "ddsp": package_version("ddsp"),
            "tf2onnx": package_version("tf2onnx"),
            "onnx": package_version("onnx"),
            "onnxruntime": package_version("onnxruntime"),
        },
        "checkpoints": {
            "expression_index_sha256": sha256(
                weights_dir / "expression_generator" / "5000.index"
            ),
            "synthesis_index_sha256": sha256(
                weights_dir / "synthesis_generator" / "50000.index"
            ),
        },
        "components": {
            str(component["name"]): {
                "file": str(component["file"]),
                "sha256": sha256(Path(component["onnx_path"])),
                "inputs": component["inputs"],
                "outputs": component["outputs"],
                "logical_inputs": component["logical_inputs"],
                "logical_outputs": component["logical_outputs"],
                "metrics": component["metrics"],
            }
            for component in components
        },
    }
    manifest_path = output_dir / "export_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Exported {len(components)} stateful MIDI-DDSP components")
    print(f"Manifest: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
