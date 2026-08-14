"""Benchmark the deployed RTL-SDR IQ OM against CPU spectrum implementations.

The benchmark keeps preprocessed IQ batches in host RAM.  It excludes RTL-SDR
startup, raw CU8 decoding, DC removal, and OM initialization so CPU and NPU
numbers describe the repeated spectrum-computation boundary.  NPU timing still
includes Tensor creation, host-to-device transfer, OM execution, result return,
and the final NumPy copy performed by :class:`AscendOmRunner`.

Run only on the Ascend board with CANN sourced and Conda ``base`` activated.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Callable, Sequence

import numpy as np

from .model.rtl_iq_spectrum_numpy_reference import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_SAMPLE_RATE_HZ,
    DEFAULT_WINDOW_SAMPLES,
    complex_dft_projection_weights,
    shifted_hann_periodogram_power,
    validate_iq_contract,
)
from .npu import AscendOmRunner
from .rtl_sdr_npu_demo import decode_rtl_sdr_cu8, generate_tone_iq, iter_iq_batches


DEFAULT_OM_PATH = Path("models/generated/rtl_iq_dft_2048ksps_b16_n1024.om")


def cpu_fft_periodogram_power(iq_batch: np.ndarray, *, hann: np.ndarray, normalization: float) -> np.ndarray:
    """Run the CPU FFT spectrum path with the same power convention as OM."""
    values = np.asarray(iq_batch, dtype=np.float32)
    if values.ndim != 3 or values.shape[1] != 2 or values.shape[2] != hann.size:
        raise ValueError("iq_batch must have shape [batch, 2, window_samples]")
    complex_values = values[:, 0, :] + 1j * values[:, 1, :]
    spectrum = np.fft.fftshift(np.fft.fft(complex_values * hann[None, :], axis=1), axes=1)
    return (np.abs(spectrum / normalization) ** 2).astype(np.float32)


def cpu_dense_dft_power(
    iq_batch: np.ndarray, *, real_weights: np.ndarray, imaginary_weights: np.ndarray
) -> np.ndarray:
    """Run the same fixed matrix DFT algorithm as OM on the ARM CPU."""
    values = np.ascontiguousarray(iq_batch, dtype=np.float32)
    if values.ndim != 3 or values.shape[1] != 2:
        raise ValueError("iq_batch must have shape [batch, 2, window_samples]")
    flattened = values.reshape(values.shape[0], values.shape[1] * values.shape[2])
    real = flattened @ real_weights
    imaginary = flattened @ imaginary_weights
    return (np.square(real) + np.square(imaginary)).astype(np.float32, copy=False)


def timing_summary(values_ms: Sequence[float], *, batch_duration_ms: float, batch_size: int) -> dict[str, float]:
    """Summarize repeated batch timings and their real-time budget."""
    values = np.asarray(values_ms, dtype=np.float64)
    if values.size == 0:
        raise ValueError("at least one timing is required")
    mean_ms = float(values.mean())
    p50_ms = float(np.percentile(values, 50))
    p95_ms = float(np.percentile(values, 95))
    return {
        "mean_ms": mean_ms,
        "p50_ms": p50_ms,
        "p95_ms": p95_ms,
        "min_ms": float(values.min()),
        "max_ms": float(values.max()),
        "batches_per_second_mean": float(1_000.0 / mean_ms),
        "windows_per_second_mean": float(batch_size * 1_000.0 / mean_ms),
        "p50_real_time_factor": float(batch_duration_ms / p50_ms),
    }


def benchmark_callable(
    function: Callable[[np.ndarray], np.ndarray],
    batches: Sequence[np.ndarray],
    *,
    warmup: int,
    iterations: int,
    batch_duration_ms: float,
) -> tuple[dict[str, float], np.ndarray]:
    """Warm a callable, then time it while cycling through supplied IQ batches."""
    if not batches:
        raise ValueError("at least one IQ batch is required")
    for index in range(warmup):
        function(batches[index % len(batches)])
    timings_ms: list[float] = []
    result: np.ndarray | None = None
    for index in range(iterations):
        started = time.perf_counter_ns()
        result = function(batches[index % len(batches)])
        timings_ms.append((time.perf_counter_ns() - started) / 1_000_000.0)
    if result is None:
        raise RuntimeError("benchmark produced no result")
    return (
        timing_summary(
            timings_ms,
            batch_duration_ms=batch_duration_ms,
            batch_size=int(result.shape[0]),
        ),
        result,
    )


def sha256(path: Path) -> str:
    """Return the provenance checksum for a real IQ capture."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_iq_batches(args: argparse.Namespace) -> tuple[list[np.ndarray], dict[str, object]]:
    """Load real CU8 IQ batches or deterministic tones into the shared input contract."""
    samples_per_batch = args.batch_size * args.window_samples
    if args.source == "cu8":
        if args.input_cu8 is None:
            raise ValueError("--input-cu8 is required when --source cu8")
        if not args.input_cu8.is_file():
            raise FileNotFoundError(f"IQ capture not found: {args.input_cu8}")
        raw = args.input_cu8.read_bytes()
        if len(raw) % 2:
            raise ValueError("CU8 input has an odd byte count")
        samples = decode_rtl_sdr_cu8(raw, complex_samples=len(raw) // 2)
        provenance: dict[str, object] = {
            "source": "cu8",
            "input_cu8": str(args.input_cu8),
            "input_bytes": len(raw),
            "input_sha256": sha256(args.input_cu8),
        }
    else:
        requested_batches = args.batches if args.batches > 0 else 8
        samples = generate_tone_iq(
            sample_rate_hz=args.sample_rate,
            total_samples=requested_batches * samples_per_batch,
            tone_offset_hz=args.tone_offset_hz,
        )
        provenance = {
            "source": "tone",
            "tone_offset_hz": args.tone_offset_hz,
            "input_bytes": 0,
        }
    if samples.size % samples_per_batch:
        raise ValueError(
            f"IQ input has {samples.size} samples, not an exact multiple of {samples_per_batch} per batch"
        )
    batches = list(
        iter_iq_batches(samples, batch_size=args.batch_size, window_samples=args.window_samples)
    )
    if args.batches > 0:
        if args.batches > len(batches):
            raise ValueError(f"requested {args.batches} batches, but only {len(batches)} are available")
        batches = batches[: args.batches]
    if not batches:
        raise ValueError("IQ input contains no complete NPU batch")
    provenance["batches_used"] = len(batches)
    return batches, provenance


def compile_fftw_benchmark(source: Path, binary: Path) -> None:
    """Compile the small board-local FFTW reference without installing packages."""
    command = ["cc", "-O3", "-std=c11", str(source), "-o", str(binary), "-lfftw3f", "-lm"]
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            "FFTW benchmark compilation failed; ensure the user has installed libfftw3-dev.\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )


def run_fftw_benchmark(
    source: Path,
    batches: Sequence[np.ndarray],
    *,
    warmup: int,
    iterations: int,
) -> tuple[dict[str, object], np.ndarray]:
    """Measure the C FFTW3 reference with the same host-memory batch rotation."""
    with tempfile.TemporaryDirectory(prefix="case5-rtl-iq-fftw-") as temporary:
        temporary_path = Path(temporary)
        input_path = temporary_path / "iq_batches.raw"
        output_path = temporary_path / "fftw_power.raw"
        binary_path = temporary_path / "benchmark_rtl_iq_fftw"
        input_path.write_bytes(np.ascontiguousarray(np.stack(batches), dtype=np.float32).tobytes())
        compile_fftw_benchmark(source, binary_path)
        completed = subprocess.run(
            [str(binary_path), str(input_path), str(output_path), str(warmup), str(iterations)],
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError(f"FFTW benchmark failed.\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}")
        try:
            metadata = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"FFTW benchmark did not return JSON: {completed.stdout!r}") from exc
        values = np.fromfile(output_path, dtype=np.float32)
    expected = batches[0].shape[0] * batches[0].shape[2]
    if values.size != expected:
        raise RuntimeError(f"FFTW output has {values.size} values; expected {expected}")
    return metadata, values.reshape(batches[0].shape[0], batches[0].shape[2])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--om", type=Path, default=DEFAULT_OM_PATH)
    parser.add_argument("--source", choices=("cu8", "tone"), default="cu8")
    parser.add_argument("--input-cu8", type=Path)
    parser.add_argument("--sample-rate", type=float, default=DEFAULT_SAMPLE_RATE_HZ)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--window-samples", type=int, default=DEFAULT_WINDOW_SAMPLES)
    parser.add_argument("--batches", type=int, default=0, help="0 uses every complete batch in the input")
    parser.add_argument("--warmup", type=int, default=30)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--tone-offset-hz", type=float, default=128_000.0)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    validate_iq_contract(
        batch_size=args.batch_size,
        window_samples=args.window_samples,
        sample_rate_hz=args.sample_rate,
    )
    if args.warmup < 0 or args.iterations <= 0 or args.batches < 0:
        raise ValueError("warmup and batches must be non-negative; iterations must be positive")
    batches, provenance = load_iq_batches(args)
    hann = np.hanning(args.window_samples).astype(np.float32)
    normalization = float(args.window_samples) * np.sqrt(float(np.mean(hann * hann)))
    real_weights, imaginary_weights = complex_dft_projection_weights(
        window_samples=args.window_samples
    )
    batch_duration_ms = 1_000.0 * args.batch_size * args.window_samples / args.sample_rate

    cpu_fft = lambda values: cpu_fft_periodogram_power(
        values, hann=hann, normalization=normalization
    )
    cpu_dense_dft = lambda values: cpu_dense_dft_power(
        values, real_weights=real_weights, imaginary_weights=imaginary_weights
    )
    reference_fft = cpu_fft(batches[0])
    reference_dense_dft = cpu_dense_dft(batches[0])
    np.testing.assert_allclose(reference_dense_dft, reference_fft, rtol=3.0e-5, atol=3.0e-5)

    runner = AscendOmRunner(args.om)
    initialize_started = time.perf_counter_ns()
    status = runner.initialize()
    initialize_ms = (time.perf_counter_ns() - initialize_started) / 1_000_000.0
    if not status.ready:
        raise RuntimeError(status.message)
    try:
        first_npu = runner.run(batches[0])
        np.testing.assert_allclose(first_npu, reference_fft, rtol=3.0e-3, atol=3.0e-3)
        npu_summary, _ = benchmark_callable(
            runner.run,
            batches,
            warmup=args.warmup,
            iterations=args.iterations,
            batch_duration_ms=batch_duration_ms,
        )
    finally:
        runner.close()
    cpu_fft_summary, _ = benchmark_callable(
        cpu_fft,
        batches,
        warmup=args.warmup,
        iterations=args.iterations,
        batch_duration_ms=batch_duration_ms,
    )
    cpu_dense_dft_summary, _ = benchmark_callable(
        cpu_dense_dft,
        batches,
        warmup=args.warmup,
        iterations=args.iterations,
        batch_duration_ms=batch_duration_ms,
    )
    project_root = Path(__file__).resolve().parents[1]
    fftw_source = project_root / "scripts" / "benchmark_rtl_iq_fftw.c"
    if not fftw_source.is_file():
        raise RuntimeError(f"FFTW reference source missing: {fftw_source}")
    fftw_metadata, fftw_power = run_fftw_benchmark(
        fftw_source,
        batches,
        warmup=args.warmup,
        iterations=args.iterations,
    )
    np.testing.assert_allclose(fftw_power, reference_fft, rtol=5.0e-5, atol=5.0e-5)
    fftw_summary = fftw_metadata["fftw_full_pipeline"]
    result = {
        "methodology": {
            "input_shape": [args.batch_size, 2, args.window_samples],
            "sample_rate_hz": args.sample_rate,
            "batch_duration_ms": batch_duration_ms,
            "window": "hann",
            "frequency_order": "fftshift_negative_to_positive",
            "preprocessing_excluded": ["rtl_sdr_capture", "CU8_decode", "per_window_dc_removal"],
            "npu_included": ["Tensor_creation", "host_to_device", "OM_inference", "device_to_host", "output_copy"],
            "cpu_fft_algorithm": "numpy.fft.fft full complex spectrum plus power",
            "cpu_dense_dft_algorithm": "two fixed float32 matrix projections plus power, matching OM",
        },
        "provenance": provenance,
        "warmup": args.warmup,
        "iterations": args.iterations,
        "om_initialize_ms_excluded": initialize_ms,
        "npu_om_end_to_end": npu_summary,
        "cpu_numpy_fft": cpu_fft_summary,
        "cpu_dense_dft": cpu_dense_dft_summary,
        "cpu_fftw": fftw_metadata,
        "p50_speed_ratio_cpu_over_npu": {
            "cpu_numpy_fft": float(cpu_fft_summary["p50_ms"] / npu_summary["p50_ms"]),
            "cpu_dense_dft": float(cpu_dense_dft_summary["p50_ms"] / npu_summary["p50_ms"]),
            "cpu_fftw": float(fftw_summary["p50_ms"] / npu_summary["p50_ms"]),
        },
        "numerical_verification": {
            "npu_vs_cpu_fft_max_abs_power": float(np.max(np.abs(first_npu - reference_fft))),
            "cpu_dense_dft_vs_cpu_fft_max_abs_power": float(
                np.max(np.abs(reference_dense_dft - reference_fft))
            ),
            "cpu_fftw_vs_cpu_fft_max_abs_power": float(np.max(np.abs(fftw_power - reference_fft))),
        },
        "environment": {
            "openblas_num_threads": os.environ.get("OPENBLAS_NUM_THREADS", "unset"),
            "omp_num_threads": os.environ.get("OMP_NUM_THREADS", "unset"),
        },
    }
    if args.output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = Path("data/rtl_iq_npu_benchmark") / f"rtl_iq_cpu_npu_{stamp}.json"
    else:
        output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, ensure_ascii=True), encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=True))
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
