"""Benchmark the deployed NPU DFT OM against ARM FFTW3 on Ascend 310B.

The inputs and power convention match the dashboard's fixed contract:
two channels, 10,000 samples at 1 MS/s, a Hann window, and 201 one-sided
bins from DC to 20 kHz. FFTW necessarily calculates its complete 5,001-bin
real FFT, then retains the same first 201 bins returned by the OM model.

Run this only on the Ascend board inside its existing CANN Conda environment.
It requires the system package ``libfftw3-dev`` already installed by the user;
no Python FFT package is used.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any

import numpy as np

from time_frequency_dashboard.config import Case5Config
from time_frequency_dashboard.npu import AscendOmRunner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--om", type=Path, default=Path("models/generated/npu_dft_1ms_10000_20khz.om"))
    parser.add_argument("--iterations", type=int, default=300)
    parser.add_argument("--warmup", type=int, default=50)
    parser.add_argument("--output", type=Path, default=Path("data/benchmark_npu_dft_vs_fftw.json"))
    return parser.parse_args()


def timing_summary(values_ms: list[float]) -> dict[str, float]:
    values = np.asarray(values_ms, dtype=np.float64)
    return {
        "mean_ms": float(values.mean()),
        "p50_ms": float(np.percentile(values, 50)),
        "p95_ms": float(np.percentile(values, 95)),
        "min_ms": float(values.min()),
        "max_ms": float(values.max()),
        "windows_per_second_mean": float(1000.0 / values.mean()),
    }


def deterministic_input(config: Case5Config) -> np.ndarray:
    """Return a non-trivial real two-channel input with intentional DC offsets."""
    seconds = np.arange(config.analysis_samples, dtype=np.float32) / np.float32(config.sample_rate_hz)
    rng = np.random.default_rng(310_005)
    values = np.stack(
        (
            0.20
            + 0.42 * np.sin(2.0 * np.pi * 1_000.0 * seconds)
            + 0.18 * np.sin(2.0 * np.pi * 7_300.0 * seconds + 0.2),
            -0.12
            + 0.30 * np.sin(2.0 * np.pi * 3_000.0 * seconds - 0.4)
            + 0.11 * np.sin(2.0 * np.pi * 13_700.0 * seconds),
        )
    ).astype(np.float32)
    values += 0.015 * rng.standard_normal(values.shape, dtype=np.float32)
    return np.ascontiguousarray(values, dtype=np.float32)


def compile_fftw_benchmark(source: Path, binary: Path) -> None:
    command = ["cc", "-O3", "-std=c11", str(source), "-o", str(binary), "-lfftw3f", "-lm"]
    try:
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
    except FileNotFoundError as exc:
        raise RuntimeError("C compiler missing; install build-essential before running this benchmark") from exc
    if completed.returncode != 0:
        raise RuntimeError(
            "FFTW benchmark compilation failed. Ensure libfftw3-dev is installed.\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )


def run_fftw(
    binary: Path, source: np.ndarray, *, warmup: int, iterations: int, output: Path
) -> tuple[dict[str, Any], np.ndarray]:
    input_path = output.with_name("fftw_input.raw")
    input_path.write_bytes(np.ascontiguousarray(source, dtype=np.float32).tobytes())
    completed = subprocess.run(
        [str(binary), str(input_path), str(output), str(warmup), str(iterations)],
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"FFTW execution failed.\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}")
    try:
        metadata = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"FFTW benchmark did not return JSON: {completed.stdout!r}") from exc
    expected_values = 2 * 201
    values = np.fromfile(output, dtype=np.float32)
    if values.size != expected_values:
        raise RuntimeError(f"FFTW output has {values.size} values; expected {expected_values}")
    return metadata, values.reshape(2, 201)


def run_npu(
    om_path: Path, source: np.ndarray, *, warmup: int, iterations: int
) -> tuple[dict[str, Any], np.ndarray]:
    prepared = source.copy()
    prepared -= prepared.mean(axis=1, keepdims=True, dtype=np.float32)
    values = prepared[None, :, :]
    runner = AscendOmRunner(om_path)
    initialize_started = time.perf_counter_ns()
    status = runner.initialize()
    initialize_ms = (time.perf_counter_ns() - initialize_started) / 1_000_000.0
    if not status.ready:
        raise RuntimeError(f"NPU OM unavailable: {status.message}")
    try:
        for _ in range(warmup):
            runner.run(values)
        timings_ms: list[float] = []
        result: np.ndarray | None = None
        for _ in range(iterations):
            started = time.perf_counter_ns()
            result = runner.run(values)
            timings_ms.append((time.perf_counter_ns() - started) / 1_000_000.0)
    finally:
        runner.close()
    if result is None:
        raise RuntimeError("NPU benchmark produced no output")
    return {
        "om_initialize_ms": initialize_ms,
        "npu_om_end_to_end": timing_summary(timings_ms),
    }, np.asarray(result[0, :, :, 0], dtype=np.float32)


def main() -> int:
    args = parse_args()
    if args.warmup < 0 or args.iterations <= 0:
        raise ValueError("--warmup must be non-negative and --iterations must be positive")
    config = Case5Config()
    if config.analysis_samples != 10_000 or config.channels != 2 or config.spectrum_bins != 201:
        raise RuntimeError("benchmark is fixed to the deployed [1, 2, 10000] -> [1, 2, 201, 1] contract")
    project_root = Path(__file__).resolve().parents[1]
    source_file = project_root / "scripts" / "benchmark_fftw.c"
    if not source_file.is_file():
        raise RuntimeError(f"FFTW source missing: {source_file}")

    raw_input = deterministic_input(config)
    with tempfile.TemporaryDirectory(prefix="case5-spectrum-benchmark-") as temporary:
        temporary_path = Path(temporary)
        binary = temporary_path / "benchmark_fftw"
        fft_output = temporary_path / "fftw_power.raw"
        compile_fftw_benchmark(source_file, binary)
        fftw_metadata, fftw_power = run_fftw(
            binary,
            raw_input,
            warmup=args.warmup,
            iterations=args.iterations,
            output=fft_output,
        )
        npu_metadata, npu_power = run_npu(
            args.om,
            raw_input,
            warmup=args.warmup,
            iterations=args.iterations,
        )

    difference = np.abs(npu_power - fftw_power)
    fftw_pipeline = fftw_metadata["fftw_full_pipeline"]
    npu_summary = npu_metadata["npu_om_end_to_end"]
    result: dict[str, Any] = {
        "methodology": {
            "input_shape": [1, config.channels, config.analysis_samples],
            "sample_rate_hz": config.sample_rate_hz,
            "frequency_range_hz": [0.0, config.spectrum_max_frequency_hz],
            "frequency_bins": config.spectrum_bins,
            "frequency_resolution_hz": config.spectrum_resolution_hz,
            "window": "hann",
            "power": "one-sided V^2-equivalent, after per-channel DC removal",
            "fftw_note": "FFTW executes a full 5001-bin r2c FFT then retains bins 0..200.",
            "npu_note": "NPU metric includes Tensor creation, host-to-device transfer, OM inference, device-to-host transfer, and output copy.",
        },
        "warmup": args.warmup,
        "iterations": args.iterations,
        "fftw": fftw_metadata,
        "npu": npu_metadata,
        "numerical_difference": {
            "max_abs_power": float(difference.max()),
            "mean_abs_power": float(difference.mean()),
            "max_relative_to_fftw_peak": float(difference.max() / max(float(np.abs(fftw_power).max()), 1.0e-12)),
        },
        "efficiency_ratio": {
            "npu_end_to_end_over_fftw_full_pipeline_mean": float(npu_summary["mean_ms"] / fftw_pipeline["mean_ms"]),
            "npu_end_to_end_over_fftw_execute_only_mean": float(npu_summary["mean_ms"] / fftw_metadata["fftw_execute_two_channel"]["mean_ms"]),
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=True), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=True))
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
