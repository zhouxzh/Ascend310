#!/usr/bin/env python3
"""Export deterministic full-sequence TensorFlow MIDI-DDSP reference tensors."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from midi_ddsp_webui.midi_analysis import analyze_midi
from tools.export_midi_ddsp_onnx import bootstrap_midi_ddsp, read_hparams, sha256


SOURCE_COMMIT = "d7af42704a63b47267ae6a1bc0fee1ed7dc5c855"
EXPRESSION_CHECKPOINT_SHA256 = "61c2e6aa8b70fe511d3d1613892addc3479165e0096c189f2c2eabf364f34375"
SYNTHESIS_CHECKPOINT_SHA256 = "d1529b405eac9a9d365edb6451a946f8e943d2bcffbeda45da4ece9ea25506e4"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--midi", type=Path, required=True)
    parser.add_argument("--instrument-id", type=int, default=0)
    parser.add_argument("--seed", type=int, default=20260724)
    parser.add_argument("--source-dir", type=Path, default=Path("_upstream/midi-ddsp"))
    parser.add_argument(
        "--weights-dir",
        type=Path,
        default=Path("models/midi_ddsp/weights/midi_ddsp_model_weights_urmp_9_10"),
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("reports/midi_ddsp/tf_reference")
    )
    return parser.parse_args()


def _source_commit(path: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _array(value: Any) -> np.ndarray:
    return np.asarray(value.numpy() if hasattr(value, "numpy") else value)


def _flatten(prefix: str, value: Any, archive: dict[str, np.ndarray]) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _flatten(f"{prefix}{key}__", item, archive)
        return
    if isinstance(value, (tuple, list)):
        for index, item in enumerate(value):
            _flatten(f"{prefix}{index}__", item, archive)
        return
    archive[prefix.rstrip("_")] = _array(value)


def _array_sha(value: np.ndarray) -> str:
    array = np.ascontiguousarray(value)
    digest = hashlib.sha256()
    digest.update(str(array.dtype).encode("ascii"))
    digest.update(np.asarray(array.shape, dtype=np.int64).tobytes())
    digest.update(array.tobytes())
    return digest.hexdigest()


def _load_models(tf: Any, expression_api: tuple[Any, Any], synthesis_api: tuple[Any, Any, Any], weights_dir: Path):
    ExpressionGenerator, get_fake_expression = expression_api
    get_synthesis_generator, get_fake_synthesis, hparams = synthesis_api

    expression = ExpressionGenerator(n_out=6, nhid=128)
    fake_expression = get_fake_expression(6)
    expression(fake_expression["cond"], out=fake_expression["target"], training=True)
    expression_checkpoint = weights_dir / "expression_generator" / "5000"
    expression.load_weights(str(expression_checkpoint)).expect_partial().assert_existing_objects_matched()

    for key, value in read_hparams(
        weights_dir / "synthesis_generator" / "train.log"
    ).items():
        setattr(hparams, key, value)
    hparams.sequence_length = 64
    synthesis = get_synthesis_generator(hparams)
    synthesis._build(get_fake_synthesis(hparams))  # pylint: disable=protected-access
    synthesis_checkpoint = weights_dir / "synthesis_generator" / "50000"
    synthesis.load_weights(str(synthesis_checkpoint)).expect_partial().assert_existing_objects_matched()
    return expression, synthesis, expression_checkpoint, synthesis_checkpoint


def main() -> int:
    args = parse_args()
    if not 0 <= args.instrument_id < 13:
        raise ValueError("--instrument-id must be in [0, 12]")
    analysis = analyze_midi(args.midi)
    if not analysis.supported or analysis.mode != "monophonic" or len(analysis.tracks) != 1:
        raise ValueError("TensorFlow reference export requires one monophonic MIDI track")
    source_dir = args.source_dir.resolve()
    commit = _source_commit(source_dir)
    if commit != SOURCE_COMMIT:
        raise ValueError(f"MIDI-DDSP source commit is {commit}; expected {SOURCE_COMMIT}")

    try:
        import pretty_midi
        import soundfile
        import tensorflow as tf
    except ImportError as exc:
        raise RuntimeError(
            "Missing local export dependency. Install requirements-export.txt "
            "in the local model-export environment."
        ) from exc

    tf.keras.utils.set_random_seed(args.seed)
    tf.config.threading.set_inter_op_parallelism_threads(1)
    tf.config.threading.set_intra_op_parallelism_threads(1)
    _ddsp, expression_api, synthesis_api = bootstrap_midi_ddsp(source_dir)
    from midi_ddsp.utils.inference_utils import (  # pylint: disable=import-outside-toplevel
        conditioning_df_to_dict,
        conditioning_df_to_midi_features,
        expression_generator_output_to_conditioning_df,
        get_process_group,
    )
    from midi_ddsp.utils.midi_synthesis_utils import (  # pylint: disable=import-outside-toplevel
        note_list_to_sequence,
    )

    weights_dir = args.weights_dir.resolve()
    expression, synthesis, expression_checkpoint, synthesis_checkpoint = _load_models(
        tf, expression_api, synthesis_api, weights_dir
    )
    expression_sha = sha256(expression_checkpoint.with_suffix(".index"))
    synthesis_sha = sha256(synthesis_checkpoint.with_suffix(".index"))
    if expression_sha.lower() != EXPRESSION_CHECKPOINT_SHA256:
        raise ValueError("Expression checkpoint SHA256 does not match the locked model")
    if synthesis_sha.lower() != SYNTHESIS_CHECKPOINT_SHA256:
        raise ValueError("Synthesis checkpoint SHA256 does not match the locked model")

    midi = pretty_midi.PrettyMIDI(str(args.midi))
    instruments = [item for item in midi.instruments if not item.is_drum and item.notes]
    if len(instruments) != 1:
        raise ValueError("Official reference requires exactly one non-drum MIDI instrument")
    notes = sorted(instruments[0].notes, key=lambda note: (note.start, note.pitch, note.end))
    note_sequence = note_list_to_sequence(notes, fs=250)
    note_sequence["instrument_id"] = tf.constant([args.instrument_id], dtype=tf.int64)
    expression_outputs = expression(note_sequence, out=None, training=False)
    conditioning_df = expression_generator_output_to_conditioning_df(
        expression_outputs["output"], note_sequence
    )
    conditioning = conditioning_df_to_dict(conditioning_df)
    midi_features = conditioning_df_to_midi_features(conditioning_df)

    gumbel_rng = np.random.default_rng(args.seed)
    gumbel_values: list[np.ndarray] = []
    sampled_values: list[np.ndarray] = []
    original_categorical = tf.random.categorical

    def deterministic_categorical(logits, num_samples, dtype=None, seed=None, name=None):
        del seed, name
        logits_array = _array(logits).astype(np.float32)
        uniform = gumbel_rng.uniform(
            np.finfo(np.float32).eps,
            1.0 - np.finfo(np.float32).eps,
            logits_array.shape,
        ).astype(np.float32)
        gumbel = (-np.log(-np.log(uniform))).astype(np.float32)
        gumbel_values.append(gumbel)
        sampled = np.argmax(logits_array + gumbel, axis=-1)[..., None]
        sampled_values.append(sampled.astype(np.int64))
        output_dtype = dtype or tf.int64
        return tf.convert_to_tensor(sampled, dtype=output_dtype)[:, :num_samples]

    tf.random.categorical = deterministic_categorical
    try:
        z_midi, raw_parameters = synthesis.midi_decoder.gen_params_from_cond(
            conditioning,
            midi_features,
            instrument_id=tf.constant([args.instrument_id], dtype=tf.int64),
            display_progressbar=False,
        )
    finally:
        tf.random.categorical = original_categorical

    processor = get_process_group(
        int(z_midi.shape[1]),
        synthesis.frame_size,
        synthesis.sample_rate,
        use_angular_cumsum=True,
    )
    processed_parameters = processor.get_controls(
        {
            "amplitudes": raw_parameters["amplitudes"],
            "harmonic_distribution": raw_parameters["harmonic_distribution"],
            "noise_magnitudes": raw_parameters["noise_magnitudes"],
            "f0_hz": raw_parameters["f0_hz"],
        },
        verbose=False,
    )

    noise_rng = np.random.default_rng(args.seed)
    white_noise_values: list[np.ndarray] = []
    original_uniform = tf.random.uniform

    def deterministic_uniform(shape, minval=0, maxval=None, dtype=tf.float32, seed=None, name=None):
        del seed, name
        dimensions = tuple(int(item) for item in tf.TensorShape(shape).as_list())
        upper = 1.0 if maxval is None else float(maxval)
        values = noise_rng.uniform(float(minval), upper, dimensions).astype(np.float32)
        white_noise_values.append(values)
        return tf.convert_to_tensor(values, dtype=dtype)

    tf.random.uniform = deterministic_uniform
    try:
        dry_audio = processor.get_signal(processed_parameters)
    finally:
        tf.random.uniform = original_uniform
    wet_audio = synthesis.reverb_module(
        dry_audio,
        reverb_number=tf.constant([args.instrument_id], dtype=tf.int64),
        training=False,
    )

    raw_ir = synthesis.reverb_module.magnitudes_embedding(
        tf.constant([args.instrument_id], dtype=tf.int64)
    )[0].numpy()
    decay = np.ones(48_000, dtype=np.float32)
    decay[16_000:] = np.exp(-4.0 * np.linspace(0.0, 1.0, 32_000)).astype(np.float32)
    prepared_ir = (raw_ir.astype(np.float32) * decay).copy()
    prepared_ir[0] = 0.0

    archive: dict[str, np.ndarray] = {
        "note_pitch": _array(note_sequence["note_pitch"]),
        "note_length": _array(note_sequence["note_length"]),
        "instrument_id": _array(note_sequence["instrument_id"]),
        "expression_controls": _array(expression_outputs["output"]),
        "expression_controls_clipped": np.clip(
            _array(expression_outputs["output"]), 0.0, 1.0
        ),
        "q_pitch": _array(midi_features[0]),
        "onsets": _array(midi_features[3]),
        "offsets": _array(midi_features[4]),
        "z_midi": _array(z_midi),
        "f0_gumbel": np.concatenate(gumbel_values, axis=0),
        "f0_sampled_bins": np.concatenate(sampled_values, axis=1),
        "white_noise": np.concatenate(white_noise_values, axis=0),
        "reverb_ir": prepared_ir,
        "audio_dry": _array(dry_audio),
        "audio_wet": _array(wet_audio),
    }
    for name in conditioning_df.columns[:6]:
        archive[f"conditioning__{name}"] = _array(conditioning[name])
    _flatten("raw__", raw_parameters, archive)
    _flatten("processed__", processed_parameters, archive)
    if not all(np.isfinite(value).all() for value in archive.values()):
        raise ValueError("TensorFlow reference contains NaN or Inf")

    output_dir = args.output_dir.resolve() / args.midi.stem
    output_dir.mkdir(parents=True, exist_ok=True)
    tensor_path = output_dir / "reference.npz"
    np.savez_compressed(tensor_path, **archive)
    soundfile.write(output_dir / "dry.wav", archive["audio_dry"][0], 16_000, subtype="FLOAT")
    soundfile.write(output_dir / "wet.wav", archive["audio_wet"][0], 16_000, subtype="FLOAT")
    manifest = {
        "schema_version": 1,
        "source_commit": commit,
        "midi": str(args.midi.resolve()),
        "midi_sha256": sha256(args.midi),
        "instrument_id": args.instrument_id,
        "seed": args.seed,
        "expression_checkpoint_sha256": expression_sha,
        "synthesis_checkpoint_sha256": synthesis_sha,
        "reference": tensor_path.name,
        "reference_sha256": sha256(tensor_path),
        "dry_wav_sha256": sha256(output_dir / "dry.wav"),
        "wet_wav_sha256": sha256(output_dir / "wet.wav"),
        "tensors": {
            name: {
                "shape": list(value.shape),
                "dtype": str(value.dtype),
                "sha256": _array_sha(value),
            }
            for name, value in archive.items()
        },
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"TensorFlow reference: {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
