#!/usr/bin/env python3
"""Compare the NumPy MIDI-DDSP DSP/reverb with a TensorFlow reference asset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from midi_ddsp_realtime import StreamingFftReverb, _render_stateful_audio
from tools.export_midi_ddsp_onnx import output_metrics


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("reference", type=Path, help="reference.npz from the TF exporter")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--max-nrmse", type=float, default=1e-4)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    with np.load(args.reference, allow_pickle=False) as data:
        parameters = SimpleNamespace(
            f0_hz=np.asarray(data["raw__f0_hz"])[0],
            amplitudes=np.asarray(data["raw__amplitudes"])[0],
            harmonic_distribution=np.asarray(data["raw__harmonic_distribution"])[0],
            noise_magnitudes=np.asarray(data["raw__noise_magnitudes"])[0],
        )
        white_noise = np.asarray(data["white_noise"], dtype=np.float32).reshape(-1)
        expected_dry = np.asarray(data["audio_dry"], dtype=np.float32).reshape(-1)
        expected_wet = np.asarray(data["audio_wet"], dtype=np.float32).reshape(-1)
        impulse_response = np.asarray(data["reverb_ir"], dtype=np.float32)

    actual_dry, _dry_signal_metrics = _render_stateful_audio(
        parameters, None, 0, white_noise=white_noise
    )
    reverb = StreamingFftReverb(impulse_response, block_size=2048)
    actual_wet, _wet_signal_metrics = _render_stateful_audio(
        parameters, reverb, 0, white_noise=white_noise
    )
    if actual_dry.shape != expected_dry.shape or actual_wet.shape != expected_wet.shape:
        raise ValueError(
            "DSP output shape mismatch: "
            f"dry {actual_dry.shape}/{expected_dry.shape}, "
            f"wet {actual_wet.shape}/{expected_wet.shape}"
        )
    report = {
        "reference": str(args.reference.resolve()),
        "dry": output_metrics(expected_dry, actual_dry),
        "wet": output_metrics(expected_wet, actual_wet),
        "thresholds": {"max_nrmse": args.max_nrmse},
    }
    report["passed"] = all(
        report[name]["nrmse"] <= args.max_nrmse for name in ("dry", "wet")
    )
    report_path = args.report or args.reference.with_name("dsp_comparison.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
