"""Benchmark Piano-DDSP host DSP components without loading an OM model."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import platform
import statistics
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from piano_ddsp_runtime.harmonic import HarmonicSynthesizer
from piano_ddsp_runtime.noise import NoiseSynthesizer
from piano_ddsp_runtime.reverb import PartitionedConvolver
from piano_ddsp_runtime.resampler import PianoSincResampler


SAMPLES_PER_FRAME = 64
MODEL_RATE = 16_000


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def summary(values: list[float]) -> dict[str, float]:
    return {
        "p50_ms": statistics.median(values),
        "p95_ms": percentile(values, 0.95),
        "p99_ms": percentile(values, 0.99),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=8)
    parser.add_argument("--voices", type=int, default=16)
    parser.add_argument("--harmonics", type=int, default=96)
    parser.add_argument("--noise-bands", type=int, default=64)
    parser.add_argument("--output-rate", type=int, default=48_000)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--warmup", type=int, default=5)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--report", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if min(args.frames, args.voices, args.harmonics, args.noise_bands) <= 0:
        raise ValueError("DSP dimensions must be positive")
    if args.iterations <= 0 or args.warmup < 0:
        raise ValueError("Iteration counts are invalid")

    rng = np.random.RandomState(args.seed)
    samples = args.frames * SAMPLES_PER_FRAME
    amplitudes = rng.normal(size=(args.frames, args.voices, 1)).astype(np.float32)
    distribution = rng.normal(
        size=(args.frames, args.voices, args.harmonics)
    ).astype(np.float32)
    inharmonicity = np.abs(
        rng.normal(scale=1e-4, size=(args.frames, args.voices, 1))
    ).astype(np.float32)
    f0_hz = rng.uniform(55.0, 1760.0, size=(args.frames, args.voices, 1)).astype(
        np.float32
    )
    noise_magnitudes = rng.normal(
        size=(args.frames, args.voices, args.noise_bands)
    ).astype(np.float32)
    envelopes = np.ones((args.voices, samples), dtype=np.float32)
    impulse = rng.normal(scale=0.001, size=24_000).astype(np.float32)

    harmonic = HarmonicSynthesizer(args.voices, args.harmonics)
    noise = NoiseSynthesizer(args.voices, args.noise_bands, seed=args.seed)
    reverb = PartitionedConvolver(impulse, samples)
    resampler = PianoSincResampler(MODEL_RATE, args.output_rate)
    resampler.prepare(samples)

    timings = {name: [] for name in ("harmonic", "noise", "reverb", "resampler", "total")}
    total_iterations = args.warmup + args.iterations
    for iteration in range(total_iterations):
        total_started = time.perf_counter()
        started = time.perf_counter()
        dry = harmonic.render(
            amplitudes, distribution, inharmonicity, f0_hz, envelopes
        )
        harmonic_ms = (time.perf_counter() - started) * 1000.0

        started = time.perf_counter()
        dry += noise.render(noise_magnitudes, envelopes)
        noise_ms = (time.perf_counter() - started) * 1000.0

        started = time.perf_counter()
        wet = dry + reverb.process(dry)
        reverb_ms = (time.perf_counter() - started) * 1000.0

        started = time.perf_counter()
        resampler.process(wet)
        resampler_ms = (time.perf_counter() - started) * 1000.0
        total_ms = (time.perf_counter() - total_started) * 1000.0

        if iteration >= args.warmup:
            timings["harmonic"].append(harmonic_ms)
            timings["noise"].append(noise_ms)
            timings["reverb"].append(reverb_ms)
            timings["resampler"].append(resampler_ms)
            timings["total"].append(total_ms)

    report = {
        "schema": "piano-ddsp-dsp-benchmark/v1",
        "platform": platform.platform(),
        "machine": platform.machine(),
        "numpy": np.__version__,
        "frames": args.frames,
        "block_samples": samples,
        "voices": args.voices,
        "harmonics": args.harmonics,
        "noise_bands": args.noise_bands,
        "output_rate": args.output_rate,
        "iterations": args.iterations,
        "timing": {name: summary(values) for name, values in timings.items()},
    }
    payload = json.dumps(report, indent=2) + "\n"
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.report.with_suffix(args.report.suffix + ".part")
        temporary.write_text(payload, encoding="utf-8")
        temporary.replace(args.report)
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
