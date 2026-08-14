"""Run a headless RTL-SDR complex-IQ spectrum demo on Ascend 310B.

The demo invokes the installed ``rtl_sdr`` recorder for a finite CU8 capture,
forms fixed batches of I/Q windows, and obtains each displayed spectrum from a
batched Ascend OM model through ``aclruntime.InferenceSession``.  No GRC,
CUDA, OpenCL, PyTorch, or CPU spectrum fallback is used in the NPU path.

Run this on the board only, with CANN sourced and the ``base`` Conda
environment activated.  Close GQRX, GNU Radio, and every other RTL-SDR user
before selecting ``--source rtl``.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import select
import subprocess
import time
from typing import Any, Iterable

import numpy as np

from .model.rtl_iq_spectrum_numpy_reference import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_SAMPLE_RATE_HZ,
    DEFAULT_WINDOW_SAMPLES,
    iq_windows_from_complex,
    shifted_frequency_axis_hz,
    shifted_hann_periodogram_power,
    validate_iq_contract,
)
from .npu import AscendOmRunner


DEFAULT_OM_PATH = Path("models/generated/rtl_iq_dft_2048ksps_b16_n1024.om")
EXPECTED_NPU_BACKEND = "NPU (Ascend 310B)"


def _positive_int(value: object, *, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _finite_positive(value: object, *, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite positive number")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite positive number") from exc
    if not np.isfinite(numeric) or numeric <= 0.0:
        raise ValueError(f"{field} must be a finite positive number")
    return numeric


def _integral_hz(value: object, *, field: str) -> float:
    """Validate a positive RTL-SDR tuning value that can be passed losslessly."""
    numeric = _finite_positive(value, field=field)
    if not numeric.is_integer():
        raise ValueError(f"{field} must be a positive whole number of hertz")
    return numeric


def decode_rtl_sdr_cu8(raw: bytes, *, complex_samples: int) -> np.ndarray:
    """Decode RTL-SDR unsigned 8-bit interleaved I/Q into complex float32."""
    _positive_int(complex_samples, field="complex_samples")
    values = np.frombuffer(raw, dtype=np.uint8)
    required_bytes = complex_samples * 2
    if values.size < required_bytes:
        raise ValueError(
            f"RTL-SDR capture has {values.size} bytes, but {required_bytes} bytes are required"
        )
    pairs = values[:required_bytes].reshape(complex_samples, 2).astype(np.float32)
    return ((pairs[:, 0] - 127.5) + 1j * (pairs[:, 1] - 127.5)).astype(np.complex64) / 127.5


def generate_tone_iq(
    *, sample_rate_hz: float, total_samples: int, tone_offset_hz: float
) -> np.ndarray:
    """Generate a deterministic source for NPU and installation validation."""
    sample_rate_hz = _finite_positive(sample_rate_hz, field="sample_rate_hz")
    _positive_int(total_samples, field="total_samples")
    if isinstance(tone_offset_hz, bool):
        raise ValueError("tone_offset_hz must be finite and strictly inside the Nyquist interval")
    try:
        tone_offset_hz = float(tone_offset_hz)
    except (TypeError, ValueError) as exc:
        raise ValueError("tone_offset_hz must be finite and strictly inside the Nyquist interval") from exc
    if not np.isfinite(tone_offset_hz) or not abs(tone_offset_hz) < sample_rate_hz / 2.0:
        raise ValueError("tone_offset_hz must be strictly inside the Nyquist interval")
    index = np.arange(total_samples, dtype=np.float32)
    return (0.72 * np.exp(1j * 2.0 * np.pi * tone_offset_hz * index / sample_rate_hz)).astype(
        np.complex64
    )


def capture_rtl_sdr_cu8(
    *,
    output_path: Path,
    sample_rate_hz: float,
    center_frequency_hz: float,
    complex_samples: int,
    device: str,
    gain_db: float | None,
    ppm_error: int,
    timeout_seconds: float,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Run one finite rtl_sdr capture and return decoded complex samples.

    The raw CU8 file is retained in ``data/`` so every NPU result has a
    reproducible source reference.  Some RTL-SDR 2.0.2 builds ignore their
    advertised ``-n`` stop count, so the parent reads precisely the required
    bytes from stdout and then terminates the recorder itself.
    """
    sample_rate_hz = _integral_hz(sample_rate_hz, field="sample_rate_hz")
    center_frequency_hz = _integral_hz(
        center_frequency_hz, field="center_frequency_hz"
    )
    _positive_int(complex_samples, field="complex_samples")
    timeout_seconds = _finite_positive(timeout_seconds, field="timeout_seconds")
    if isinstance(ppm_error, bool) or not isinstance(ppm_error, int):
        raise ValueError("ppm_error must be an integer")
    if gain_db is not None:
        if isinstance(gain_db, bool):
            raise ValueError("gain_db must be finite when specified")
        try:
            gain_db = float(gain_db)
        except (TypeError, ValueError) as exc:
            raise ValueError("gain_db must be finite when specified") from exc
        if not np.isfinite(gain_db):
            raise ValueError("gain_db must be finite when specified")
    if not str(device).strip():
        raise ValueError("RTL-SDR device must be non-empty")
    output_path = Path(output_path)
    stderr_path = output_path.with_suffix(".rtl_sdr.log")
    if output_path.resolve() == stderr_path.resolve():
        raise ValueError("CU8 output path must not collide with the rtl_sdr diagnostic log")
    if output_path.exists():
        raise FileExistsError(f"refusing to overwrite existing CU8 capture: {output_path}")
    executable = shutil.which("rtl_sdr")
    if executable is None:
        raise RuntimeError("rtl_sdr is not on PATH; install the RTL-SDR tools package first")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        executable,
        "-f",
        str(int(center_frequency_hz)),
        "-s",
        str(int(sample_rate_hz)),
        "-d",
        str(device),
        "-p",
        str(ppm_error),
    ]
    if gain_db is not None:
        command.extend(("-g", str(gain_db)))
    command.append("-")
    required_bytes = complex_samples * 2
    started = time.perf_counter_ns()
    with stderr_path.open("wb") as stderr_handle:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=stderr_handle)
        if process.stdout is None:
            process.kill()
            raise RuntimeError("rtl_sdr stdout pipe was not created")
        collected = bytearray()
        deadline = time.monotonic() + timeout_seconds
        try:
            while len(collected) < required_bytes:
                remaining_seconds = deadline - time.monotonic()
                if remaining_seconds <= 0:
                    raise RuntimeError(
                        f"rtl_sdr supplied {len(collected)} of {required_bytes} IQ bytes "
                        f"within {timeout_seconds:g} seconds"
                    )
                ready, _, _ = select.select([process.stdout], [], [], remaining_seconds)
                if not ready:
                    continue
                chunk = os.read(process.stdout.fileno(), required_bytes - len(collected))
                if not chunk:
                    return_code = process.poll()
                    raise RuntimeError(f"rtl_sdr stream ended early with return code {return_code}")
                collected.extend(chunk)
        finally:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3.0)
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
    raw = bytes(collected)
    with output_path.open("xb") as output:
        output.write(raw)
    decoded = decode_rtl_sdr_cu8(raw, complex_samples=complex_samples)
    try:
        stderr_tail = stderr_path.read_text(encoding="utf-8", errors="replace").strip()[-1_500:]
    except OSError:
        stderr_tail = "unable to read rtl_sdr diagnostic log"
    capture_metadata = {
        "rtl_sdr_command": command,
        "capture_file": str(output_path),
        "rtl_sdr_stderr_file": str(stderr_path),
        "capture_elapsed_ms": elapsed_ms,
        "capture_bytes": len(raw),
        "rtl_sdr_stderr_tail": stderr_tail,
    }
    return decoded, capture_metadata


def iter_iq_batches(
    complex_samples: np.ndarray, *, batch_size: int, window_samples: int
) -> Iterable[np.ndarray]:
    """Split a finite capture into static model input tensors."""
    _positive_int(batch_size, field="batch_size")
    _positive_int(window_samples, field="window_samples")
    source = np.asarray(complex_samples)
    if source.ndim != 1:
        raise ValueError("complex_samples must be a 1-D array")
    if not np.issubdtype(source.dtype, np.complexfloating):
        raise ValueError("complex_samples must contain complex values")
    values = np.ascontiguousarray(source, dtype=np.complex64)
    if not values.size or not np.all(np.isfinite(values)):
        raise ValueError("complex_samples must be finite and non-empty")
    batch_complex_samples = batch_size * window_samples
    if values.size % batch_complex_samples:
        raise ValueError("capture length must contain an exact number of NPU batches")
    for offset in range(0, values.size, batch_complex_samples):
        windows = values[offset : offset + batch_complex_samples].reshape(batch_size, window_samples)
        yield iq_windows_from_complex(windows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("rtl", "tone"), default="rtl")
    parser.add_argument("--om", type=Path, default=DEFAULT_OM_PATH)
    parser.add_argument("--sample-rate", type=float, default=DEFAULT_SAMPLE_RATE_HZ)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--window-samples", type=int, default=DEFAULT_WINDOW_SAMPLES)
    parser.add_argument("--batches", type=int, default=8)
    parser.add_argument("--center-frequency", type=float, default=100_000_000.0)
    parser.add_argument("--device", default="0")
    parser.add_argument("--gain-db", type=float, default=None)
    parser.add_argument("--ppm-error", type=int, default=0)
    parser.add_argument("--capture-timeout-seconds", type=float, default=20.0)
    parser.add_argument("--tone-offset-hz", type=float, default=128_000.0)
    parser.add_argument("--measure-cpu-reference", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=Path("data/rtl_iq_npu_demo"))
    return parser.parse_args()


def run_demo(args: argparse.Namespace) -> tuple[Path, list[dict[str, Any]]]:
    """Execute the NPU-only spectrum path and return the persisted result rows."""
    sample_rate_hz = _finite_positive(args.sample_rate, field="sample_rate_hz")
    center_frequency_hz: float | None = None
    if args.source == "rtl":
        sample_rate_hz = _integral_hz(args.sample_rate, field="sample_rate_hz")
        center_frequency_hz = _integral_hz(
            args.center_frequency, field="center_frequency_hz"
        )
    validate_iq_contract(
        batch_size=args.batch_size,
        window_samples=args.window_samples,
        sample_rate_hz=sample_rate_hz,
    )
    _positive_int(args.batches, field="batches")
    _finite_positive(args.capture_timeout_seconds, field="capture_timeout_seconds")
    total_samples = args.batches * args.batch_size * args.window_samples
    run_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(args.output_dir)
    result_path = output_dir / f"rtl_iq_npu_{run_stamp}.jsonl"
    capture_path = output_dir / f"rtl_iq_{run_stamp}.cu8" if args.source == "rtl" else None
    artifacts = [("RTL-SDR NPU result", result_path)]
    if capture_path is not None:
        artifacts.append(("CU8 capture", capture_path))
    for description, artifact_path in artifacts:
        if artifact_path.exists():
            raise FileExistsError(f"refusing to overwrite existing {description}: {artifact_path}")
    output_dir.mkdir(parents=True, exist_ok=True)
    capture_metadata: dict[str, Any] = {}
    if args.source == "rtl":
        assert capture_path is not None
        assert center_frequency_hz is not None
        samples, capture_metadata = capture_rtl_sdr_cu8(
            output_path=capture_path,
            sample_rate_hz=sample_rate_hz,
            center_frequency_hz=center_frequency_hz,
            complex_samples=total_samples,
            device=args.device,
            gain_db=args.gain_db,
            ppm_error=args.ppm_error,
            timeout_seconds=args.capture_timeout_seconds,
        )
    else:
        samples = generate_tone_iq(
            sample_rate_hz=sample_rate_hz,
            total_samples=total_samples,
            tone_offset_hz=args.tone_offset_hz,
        )
        capture_metadata = {
            "tone_offset_hz": args.tone_offset_hz,
            "capture_elapsed_ms": 0.0,
            "capture_bytes": 0,
        }
    runner = AscendOmRunner(args.om)
    status = runner.initialize()
    if not status.ready or status.backend != EXPECTED_NPU_BACKEND:
        runner.close()
        raise RuntimeError(status.message)
    axis = shifted_frequency_axis_hz(
        sample_rate_hz=sample_rate_hz, window_samples=args.window_samples
    )
    header = {
        "record_type": "run_metadata",
        "created_utc": run_stamp,
        "source": args.source,
        "sample_rate_hz": sample_rate_hz,
        "center_frequency_hz": center_frequency_hz,
        "batch_size": args.batch_size,
        "window_samples": args.window_samples,
        "model_input_shape": [args.batch_size, 2, args.window_samples],
        "model_output_shape": [args.batch_size, args.window_samples],
        "om_path": str(args.om),
        "inference_backend": status.backend,
        "spectrum_producer": "Ascend OM only; CPU has no display fallback",
        "capture": capture_metadata,
    }
    rows: list[dict[str, Any]] = [header]
    try:
        for batch_index, iq_batch in enumerate(
            iter_iq_batches(samples, batch_size=args.batch_size, window_samples=args.window_samples)
        ):
            spectrum_power = runner.run(iq_batch)
            spectrum_power = np.asarray(spectrum_power)
            if spectrum_power.shape != (args.batch_size, args.window_samples):
                raise RuntimeError(
                    f"unexpected OM output shape {tuple(spectrum_power.shape)}; "
                    f"expected {(args.batch_size, args.window_samples)}"
                )
            if not np.issubdtype(spectrum_power.dtype, np.number) or np.issubdtype(
                spectrum_power.dtype, np.complexfloating
            ):
                raise RuntimeError("OM spectrum output must contain real numeric values")
            spectrum_power = np.ascontiguousarray(spectrum_power, dtype=np.float32)
            if not np.all(np.isfinite(spectrum_power)):
                raise RuntimeError("OM spectrum output contains NaN or Inf")
            peak_indices = np.argmax(spectrum_power, axis=1)
            cpu_metrics: dict[str, float] = {}
            if args.measure_cpu_reference:
                cpu_started = time.perf_counter_ns()
                cpu_power = shifted_hann_periodogram_power(iq_batch)
                cpu_elapsed_ms = (time.perf_counter_ns() - cpu_started) / 1_000_000.0
                cpu_metrics = {
                    "cpu_numpy_fft_reference_ms": cpu_elapsed_ms,
                    "cpu_reference_max_abs_difference": float(np.max(np.abs(cpu_power - spectrum_power))),
                }
            for window_index, peak_index in enumerate(peak_indices):
                peak_offset_hz = float(axis[int(peak_index)])
                peak_frequency_hz = peak_offset_hz
                if args.source == "rtl":
                    assert center_frequency_hz is not None
                    peak_frequency_hz = float(center_frequency_hz + peak_offset_hz)
                row: dict[str, Any] = {
                    "record_type": "npu_spectrum_result",
                    "batch_index": batch_index,
                    "window_index": window_index,
                    "sample_rate_hz": sample_rate_hz,
                    "frequency_resolution_hz": sample_rate_hz / args.window_samples,
                    "peak_offset_hz": peak_offset_hz,
                    "peak_frequency_hz": peak_frequency_hz,
                    "peak_power": float(spectrum_power[window_index, peak_index]),
                    "inference_backend": runner.status.backend,
                    "npu_inference_latency_ms": runner.status.last_latency_ms,
                    "model_input_shape": list(iq_batch.shape),
                    "model_output_shape": list(spectrum_power.shape),
                }
                row.update(cpu_metrics)
                rows.append(row)
    finally:
        runner.close()
    with result_path.open("x", encoding="utf-8") as handle:
        for row in rows:
            handle.write(
                json.dumps(row, ensure_ascii=True, sort_keys=True, allow_nan=False) + "\n"
            )
    return result_path, rows


def main() -> int:
    args = parse_args()
    result_path, rows = run_demo(args)
    results = [row for row in rows if row["record_type"] == "npu_spectrum_result"]
    first = results[0]
    print(
        "RTL-SDR NPU demo completed; "
        f"backend={first['inference_backend']}; "
        f"first_peak_offset_hz={first['peak_offset_hz']:.3f}; "
        f"first_batch_npu_latency_ms={first['npu_inference_latency_ms']:.3f}; "
        f"results={result_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
