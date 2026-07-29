"""Validate optimized NumPy Piano-DDSP audio against a pinned PyTorch NPZ reference."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from piano_ddsp_runtime.harmonic import HarmonicSynthesizer
from piano_ddsp_runtime.noise import NoiseSynthesizer
from piano_ddsp_runtime.reverb import StreamingReverb


SAMPLES_PER_FRAME = 64
SNR_THRESHOLD_DB = 60.0


def snr_db(reference: np.ndarray, actual: np.ndarray) -> float:
    expected = np.asarray(reference, dtype=np.float64)
    error = expected - np.asarray(actual, dtype=np.float64)
    signal_power = float(np.sum(np.square(expected)))
    error_power = float(np.sum(np.square(error)))
    return 10.0 * math.log10(max(signal_power, 1e-30) / max(error_power, 1e-30))


def voice_envelopes(gates: np.ndarray) -> np.ndarray:
    frames, voices = gates.shape
    samples = frames * SAMPLES_PER_FRAME
    result = np.empty((voices, samples), dtype=np.float32)
    gains = np.zeros(voices, dtype=np.float32)
    step = np.float32(1.0 / round(0.060 * 16_000))
    expanded = np.repeat(gates, SAMPLES_PER_FRAME, axis=0)
    for voice in range(voices):
        for index in range(samples):
            gains[voice] = (
                1.0 if expanded[index, voice] else max(0.0, gains[voice] - step)
            )
            result[voice, index] = gains[voice]
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--block-frames", type=int, default=8)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.block_frames <= 0:
        raise ValueError("--block-frames must be positive")
    metadata = json.loads(args.metadata.read_text(encoding="utf-8"))
    with np.load(args.reference, allow_pickle=False) as archive:
        required = {
            "amplitudes",
            "harmonic_distribution",
            "inharmonicity",
            "f0_hz",
            "noise_magnitudes",
            "gates",
            "white_noise",
            "reverb_condition",
            "reference_harmonic",
            "reference_noise",
            "reference_dry",
            "reference_wet",
        }
        missing = sorted(required - set(archive.files))
        if missing:
            raise ValueError(f"Reference NPZ is missing arrays: {missing}")
        reference_samples = int(archive["reference_harmonic"].size)
        frames = reference_samples // SAMPLES_PER_FRAME
        if frames <= 0 or frames % args.block_frames:
            raise ValueError("Reference audio is not aligned to --block-frames")
        arrays = {name: archive[name][:frames].copy() for name in required if name not in {
            "reverb_condition", "reference_harmonic", "reference_noise", "reference_dry", "reference_wet"
        }}
        reverb_condition = archive["reverb_condition"].copy()
        references = {
            name: archive[f"reference_{name}"].copy()
            for name in ("harmonic", "noise", "dry", "wet")
        }

    voices = int(arrays["amplitudes"].shape[1])
    harmonic = HarmonicSynthesizer(
        voices,
        int(metadata["n_harmonics"]),
        int(metadata.get("n_substrings", 1)),
    )
    noise = NoiseSynthesizer(voices, int(metadata["n_noise_bands"]), seed=args.seed)
    block_samples = args.block_frames * SAMPLES_PER_FRAME
    reverb = StreamingReverb(metadata, reverb_condition, block_samples, mix=1.0)
    envelopes = voice_envelopes(arrays["gates"])
    harmonic_blocks: list[np.ndarray] = []
    noise_blocks: list[np.ndarray] = []
    wet_blocks: list[np.ndarray] = []
    for start in range(0, frames, args.block_frames):
        end = start + args.block_frames
        envelope = envelopes[:, start * SAMPLES_PER_FRAME : end * SAMPLES_PER_FRAME]
        harmonic_block = harmonic.render(
            arrays["amplitudes"][start:end],
            arrays["harmonic_distribution"][start:end],
            arrays["inharmonicity"][start:end],
            arrays["f0_hz"][start:end],
            envelope,
        )
        noise_block = noise.render(
            arrays["noise_magnitudes"][start:end],
            envelope,
            arrays["white_noise"][start:end],
        )
        dry_block = harmonic_block + noise_block
        harmonic_blocks.append(harmonic_block)
        noise_blocks.append(noise_block)
        wet_blocks.append(reverb.process(dry_block))

    actual_harmonic = np.concatenate(harmonic_blocks)
    actual_noise = np.concatenate(noise_blocks)
    actual_dry = actual_harmonic + actual_noise
    actual_wet = np.concatenate(wet_blocks)
    actual = {
        "harmonic": actual_harmonic,
        "noise": actual_noise,
        "dry": actual_dry,
        "wet": actual_wet,
    }
    scores = {name: snr_db(references[name], value) for name, value in actual.items()}
    passed = all(value >= SNR_THRESHOLD_DB for value in scores.values())
    report = {
        "schema": "piano-ddsp-dsp-validation/v1",
        "model_id": metadata.get("model_id"),
        "reference": args.reference.name,
        "frames": frames,
        "block_frames": args.block_frames,
        "snr_threshold_db": SNR_THRESHOLD_DB,
        "scores_db": scores,
        "passed": passed,
    }
    payload = json.dumps(report, indent=2) + "\n"
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.report.with_suffix(args.report.suffix + ".part")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(args.report)
    print(payload, end="")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
