#!/usr/bin/env python3
"""Run a stateful ONNX bundle and compare it with a full TensorFlow reference."""

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
from midi_ddsp_webui.stateful_midi_ddsp import StatefulMidiDdspInference
from tools.export_midi_ddsp_onnx import output_metrics


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-manifest", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--report", type=Path)
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
        self.timbre_halo = int(data["timbre_halo"])
        self.components = {
            name: OnnxComponent(raw, self.manifest_path.parent / str(raw["file"]))
            for name, raw in data["components"].items()
        }

    def component(self, name: str) -> OnnxComponent:
        return self.components[name]


def main() -> int:
    args = parse_args()
    bundle = OnnxBundle(args.export_manifest)
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
        inference = StatefulMidiDdspInference(bundle, seed=seed)
        actual = inference.run(tokens, build_frame_features, instrument_id)
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
    sampled_bins_match = bool(np.array_equal(expected_bins, actual.sampled_bins))
    thresholds = {
        "expression_controls": 0.002,
        "f0_hz": 1e-5,
        "amplitudes": 0.003,
        "harmonic_distribution": 0.008,
        "noise_magnitudes": 0.015,
    }
    passed = sampled_bins_match and all(
        metrics[name]["nrmse"] <= threshold
        for name, threshold in thresholds.items()
    )
    report = {
        "export_manifest": str(args.export_manifest.resolve()),
        "reference": str(args.reference.resolve()),
        "metrics": metrics,
        "sampled_bins_match": sampled_bins_match,
        "thresholds": thresholds,
        "component_timings_ms": actual.metrics["component_timings_ms"],
        "tensor_sha256": actual.metrics["tensor_sha256"],
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
