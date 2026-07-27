#!/usr/bin/env python3
"""Compare a stateful ONNX or OM bundle with a full TensorFlow reference."""

from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from midi_ddsp_realtime import MidiToken, build_frame_features
from midi_ddsp_webui.model_bundle import load_runtime_bundle
from midi_ddsp_webui.stateful_midi_ddsp import (
    BatchedStatefulMidiDdspInference,
    StatefulMidiDdspInference,
)


def output_metrics(reference: np.ndarray, actual: np.ndarray) -> dict[str, object]:
    reference = np.asarray(reference, dtype=np.float64)
    actual = np.asarray(actual, dtype=np.float64)
    diff = actual - reference
    reference_norm = float(np.linalg.norm(reference.ravel()))
    actual_norm = float(np.linalg.norm(actual.ravel()))
    cosine_denominator = reference_norm * actual_norm
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
        "nrmse": float(
            np.linalg.norm(diff.ravel()) / max(reference_norm, 1e-12)
        ),
        "cosine_similarity": cosine,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    bundle = parser.add_mutually_exclusive_group(required=True)
    bundle.add_argument("--export-manifest", type=Path)
    bundle.add_argument("--runtime-manifest", type=Path)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--voice-batch-size", type=int, default=1)
    return parser.parse_args()


class OnnxRunner:
    def __init__(self, component: dict[str, object], model_path: Path) -> None:
        import onnxruntime as ort

        self.session = ort.InferenceSession(
            str(model_path), providers=["CPUExecutionProvider"]
        )
        self.input_names = [str(value) for value in component["logical_inputs"]]
        self.output_names = [str(value) for value in component["logical_outputs"]]
        if len(self.input_names) != len(self.session.get_inputs()):
            raise ValueError(f"Input count mismatch for {model_path.name}")
        if len(self.output_names) != len(self.session.get_outputs()):
            raise ValueError(f"Output count mismatch for {model_path.name}")

    def infer(self, feeds: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        expected = set(self.input_names)
        if set(feeds) != expected:
            raise ValueError(f"ONNX inputs must be exactly {sorted(expected)}")
        runtime_feeds = {
            node.name: np.ascontiguousarray(feeds[logical])
            for node, logical in zip(self.session.get_inputs(), self.input_names)
        }
        values = self.session.run(None, runtime_feeds)
        return dict(zip(self.output_names, values))


class OnnxComponent:
    def __init__(self, component: dict[str, object], model_path: Path) -> None:
        self.component_data = component
        self.model_path = model_path

    def open(self, _device_id: int):
        return nullcontext(OnnxRunner(self.component_data, self.model_path))


class OnnxBundle:
    def __init__(self, manifest_path: Path) -> None:
        self.manifest_path = manifest_path.resolve()
        data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        if data.get("architecture") != "stateful-v2":
            raise ValueError("Export manifest is not a stateful-v2 bundle")
        self.expression_block = int(data["expression_block"])
        self.synthesis_block = int(data["synthesis_block"])
        self.timbre_max_frames = int(data["timbre_max_frames"])
        self.component_sets: dict[int, dict[str, OnnxComponent]] = {}
        for export_name, raw in data["components"].items():
            batch_size = int(raw.get("voice_batch_size", 1))
            logical_name = str(raw.get("logical_name", export_name))
            self.component_sets.setdefault(batch_size, {})[logical_name] = OnnxComponent(
                raw, self.manifest_path.parent / str(raw["file"])
            )
        self.components = self.component_sets[1]
        self.voice_batch_sizes = tuple(sorted(self.component_sets))

    def component(self, name: str, voice_batch_size: int = 1) -> OnnxComponent:
        return self.component_sets[voice_batch_size][name]

    def runtime_session(self, _device_id: int):
        return nullcontext()


def main() -> int:
    args = parse_args()
    if args.runtime_manifest is not None:
        bundle = load_runtime_bundle(args.runtime_manifest)
        manifest_path = args.runtime_manifest
        runtime = "om"
    else:
        bundle = OnnxBundle(args.export_manifest)
        manifest_path = args.export_manifest
        runtime = "onnx"
    with np.load(args.reference, allow_pickle=False) as data:
        note_pitch = np.asarray(data["note_pitch"], dtype=np.int64)[0]
        note_length = np.asarray(data["note_length"], dtype=np.float32)[0, :, 0]
        instrument_id = int(np.asarray(data["instrument_id"]).reshape(-1)[0])
        tokens = [
            MidiToken(int(pitch), int(round(float(length) * 250.0)))
            for pitch, length in zip(note_pitch, note_length)
        ]
        seed = int(
            json.loads(args.reference.with_name("manifest.json").read_text(encoding="utf-8"))["seed"]
        )
        if args.voice_batch_size == 1:
            inference = StatefulMidiDdspInference(bundle, seed=seed)
            actual_members = [
                inference.run(tokens, build_frame_features, instrument_id)
            ]
        else:
            inference = BatchedStatefulMidiDdspInference(
                bundle, args.voice_batch_size
            )
            actual_members = inference.run(
                [tokens] * args.voice_batch_size,
                build_frame_features,
                [instrument_id] * args.voice_batch_size,
                [seed] * args.voice_batch_size,
            )
        actual = actual_members[0]
        expected = {
            "expression_controls": np.asarray(data["expression_controls_clipped"])[0],
            "f0_hz": np.asarray(data["raw__f0_hz"])[0],
            "amplitudes": np.asarray(data["raw__amplitudes"])[0],
            "harmonic_distribution": np.asarray(data["raw__harmonic_distribution"])[0],
            "noise_magnitudes": np.asarray(data["raw__noise_magnitudes"])[0],
        }
        expected_bins = np.asarray(data["f0_sampled_bins"], dtype=np.int64).reshape(-1)

    observed = {
        "expression_controls": actual.controls,
        "f0_hz": actual.f0_hz,
        "amplitudes": actual.amplitudes,
        "harmonic_distribution": actual.harmonic_distribution,
        "noise_magnitudes": actual.noise_magnitudes,
    }
    metrics = {
        name: output_metrics(expected[name], observed[name]) for name in expected
    }
    sampled_bins_match = all(
        np.array_equal(expected_bins, member.sampled_bins)
        for member in actual_members
    )
    thresholds = {
        "expression_controls": 0.002,
        "f0_hz": 1e-5,
        "amplitudes": 0.003,
        "harmonic_distribution": 0.008,
        "noise_magnitudes": 0.015,
    }
    batch_members_match = all(
        member.metrics["tensor_sha256"] == actual.metrics["tensor_sha256"]
        for member in actual_members
    )
    passed = sampled_bins_match and batch_members_match and all(
        metrics[name]["nrmse"] <= threshold
        for name, threshold in thresholds.items()
    )
    report = {
        "runtime": runtime,
        "voice_batch_size": args.voice_batch_size,
        "bundle_manifest": str(manifest_path.resolve()),
        "reference": str(args.reference.resolve()),
        "metrics": metrics,
        "sampled_bins_match": sampled_bins_match,
        "thresholds": thresholds,
        "component_timings_ms": actual.metrics["component_timings_ms"],
        "tensor_sha256": actual.metrics["tensor_sha256"],
        "batch_members_match": batch_members_match,
        "passed": passed,
    }
    report_path = args.report or args.reference.with_name("stateful_onnx_comparison.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
