"""Benchmark VOLK generic/NEON dispatch, ONNX Runtime CPU, and Ascend OM.

Run this module on the Ascend 310B board after preparing the fixed OM models.
The primary NPU timing includes Tensor construction, transfers, execution, and
the host output copy performed by :class:`AscendOmRunner`.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Sequence

import numpy as np
import onnxruntime as ort

from .model.volk_kernel_reference import (
    DEFAULT_BATCH_SIZES,
    DEFAULT_VECTOR_LENGTH,
    VOLK_KERNELS,
    deterministic_input,
    output_shape,
    volk_kernel_numpy,
)


def parse_csv_ints(value: str) -> tuple[int, ...]:
    parts = value.split(",")
    if not parts or any(not item.strip() for item in parts):
        raise argparse.ArgumentTypeError("expected comma-separated positive integers")
    try:
        result = tuple(int(item.strip()) for item in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma-separated positive integers") from exc
    if not result or any(item <= 0 for item in result):
        raise argparse.ArgumentTypeError("expected comma-separated positive integers")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models-dir", type=Path, default=Path("models/generated/volk"))
    parser.add_argument("--kernels", default=",".join(VOLK_KERNELS))
    parser.add_argument(
        "--batch-sizes", type=parse_csv_ints, default=DEFAULT_BATCH_SIZES
    )
    parser.add_argument("--vector-length", type=int, default=DEFAULT_VECTOR_LENGTH)
    parser.add_argument("--warmup", type=int, default=50)
    parser.add_argument("--iterations", type=int, default=300)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--cpu",
        type=int,
        help="logical CPU for the complete process; defaults to the highest allowed CPU",
    )
    parser.add_argument("--sample-rate", type=float, default=2_048_000.0)
    parser.add_argument("--rtol", type=float, default=1.0e-2)
    parser.add_argument("--atol", type=float, default=1.0e-3)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def sha256_bytes(values: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(values).tobytes()).hexdigest()


def command_text(command: Sequence[str]) -> str:
    try:
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
    except OSError as exc:
        return f"unavailable: {exc}"
    output = completed.stdout.strip() or completed.stderr.strip()
    return output[-4_000:]


def thermal_snapshot() -> list[dict[str, Any]]:
    zones: list[dict[str, Any]] = []
    for zone in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
        try:
            zone_type = (zone / "type").read_text(encoding="utf-8").strip()
            raw = float((zone / "temp").read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        temperature_c = raw / 1000.0 if abs(raw) > 500.0 else raw
        zones.append({"zone": zone.name, "type": zone_type, "temperature_c": temperature_c})
    return zones


def npu_temperature_snapshot() -> dict[str, Any]:
    raw = command_text(["npu-smi", "info", "-t", "temp", "-i", "0"])
    match = re.search(r"Temperature \(C\)\s*:\s*(-?\d+(?:\.\d+)?)", raw)
    return {
        "temperature_c": None if match is None else float(match.group(1)),
        "raw": raw,
    }


def timing_summary(values_ms: Sequence[float], process_cpu_ms: float) -> dict[str, float]:
    values = np.asarray(values_ms, dtype=np.float64)
    if values.size == 0:
        raise ValueError("at least one timing is required")
    return {
        "mean_ms": float(values.mean()),
        "p50_ms": float(np.percentile(values, 50)),
        "p95_ms": float(np.percentile(values, 95)),
        "min_ms": float(values.min()),
        "max_ms": float(values.max()),
        "process_cpu_ms": float(process_cpu_ms),
    }


def aggregate_repeat_timings(repeat_rows: Sequence[dict[str, Any]]) -> dict[str, float]:
    """Summarize all measured samples with one percentile definition.

    The C VOLK harness retains raw per-iteration values, so generic, NEON,
    dispatcher, ONNX Runtime and OM all report percentiles over exactly
    ``repeats * iterations`` measurements.  Warm-up work is excluded from the
    CPU-time field for every backend.
    """
    timings = [
        float(value)
        for row in repeat_rows
        for value in row.get("timings_ms", [])
    ]
    if not timings:
        raise ValueError("VOLK repeat records contain no timings")
    return timing_summary(
        timings,
        sum(float(row.get("process_cpu_ms", 0.0)) for row in repeat_rows),
    )


def benchmark_callable(
    function: Callable[[np.ndarray], np.ndarray],
    source: np.ndarray,
    *,
    warmup: int,
    iterations: int,
    repeats: int,
) -> tuple[dict[str, float], np.ndarray]:
    timings: list[float] = []
    result: np.ndarray | None = None
    for _repeat in range(repeats):
        for _index in range(warmup):
            function(source)
        process_started = time.process_time_ns()
        for _index in range(iterations):
            started = time.perf_counter_ns()
            result = function(source)
            timings.append((time.perf_counter_ns() - started) / 1_000_000.0)
        if _repeat == 0:
            process_cpu_ms = 0.0
        process_cpu_ms += (time.process_time_ns() - process_started) / 1_000_000.0
    if result is None:
        raise RuntimeError("benchmark produced no output")
    return timing_summary(timings, process_cpu_ms), np.asarray(result, dtype=np.float32)


def compile_volk_harness(source: Path, binary: Path) -> list[str]:
    if shutil.which("cc") is None or shutil.which("pkg-config") is None:
        raise RuntimeError(
            "VOLK benchmark build tools are missing; install gcc and pkg-config manually"
        )
    pkg = subprocess.run(
        ["pkg-config", "--cflags", "--libs", "volk"],
        text=True,
        capture_output=True,
        check=False,
    )
    if pkg.returncode != 0:
        raise RuntimeError(
            "VOLK development files are missing; install libvolk2-dev manually\n"
            f"{pkg.stderr}"
        )
    command = ["cc", "-O3", "-std=c11", str(source), "-o", str(binary)]
    command.extend(pkg.stdout.split())
    command.append("-lm")
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"VOLK benchmark compilation failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    return command


def run_volk_harness(
    binary: Path,
    *,
    kernel: str,
    implementation: str,
    source: np.ndarray,
    expected_shape: tuple[int, ...],
    warmup: int,
    iterations: int,
    repeats: int,
    cpu: int,
    temporary: Path,
) -> tuple[dict[str, Any], np.ndarray]:
    input_path = temporary / f"{kernel}_b{source.shape[0]}_input.raw"
    output_path = temporary / f"{kernel}_{implementation}_output.raw"
    input_path.write_bytes(np.ascontiguousarray(source, dtype=np.float32).tobytes())
    taskset = shutil.which("taskset")
    repeat_rows: list[dict[str, Any]] = []
    result: np.ndarray | None = None
    for _repeat in range(repeats):
        command: list[str] = []
        if taskset is not None:
            command.extend((taskset, "-c", str(cpu)))
        command.extend(
            (
                str(binary),
                kernel,
                implementation,
                str(input_path),
                str(output_path),
                str(source.shape[0]),
                str(source.shape[2]),
                str(warmup),
                str(iterations),
            )
        )
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        if completed.returncode != 0:
            raise RuntimeError(
                f"VOLK {kernel}/{implementation} failed\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )
        repeat_rows.append(json.loads(completed.stdout))
        validate_volk_implementation_record(
            repeat_rows[-1], implementation=implementation
        )
        result = np.fromfile(output_path, dtype=np.float32).reshape(expected_shape)
    if result is None:
        raise RuntimeError("VOLK benchmark produced no output")
    aggregate = aggregate_repeat_timings(repeat_rows)
    aggregate.update(
        {
            "repeats": repeat_rows,
            "cpu_affinity": cpu if taskset is not None else None,
        }
    )
    return aggregate, result


def run_npu_worker(
    om_path: Path,
    source: np.ndarray,
    expected_shape: tuple[int, ...],
    *,
    warmup: int,
    iterations: int,
    repeats: int,
    temporary: Path,
) -> tuple[dict[str, Any], np.ndarray]:
    stem = om_path.stem
    input_path = temporary / f"{stem}_npu_input.raw"
    output_path = temporary / f"{stem}_npu_output.raw"
    result_path = temporary / f"{stem}_npu_result.json"
    input_path.write_bytes(np.ascontiguousarray(source, dtype=np.float32).tobytes())
    command = [
        sys.executable,
        "-m",
        "time_frequency_dashboard.model.benchmark_om_worker",
        "--om",
        str(om_path),
        "--input",
        str(input_path),
        "--output",
        str(output_path),
        "--result",
        str(result_path),
        "--input-shape",
        ",".join(str(value) for value in source.shape),
        "--warmup",
        str(warmup),
        "--iterations",
        str(iterations),
        "--repeats",
        str(repeats),
    ]
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        raise RuntimeError(
            f"NPU benchmark worker failed for {om_path}\n"
            f"stdout:\n{completed.stdout[-4_000:]}\n"
            f"stderr:\n{completed.stderr[-4_000:]}"
        )
    payload = json.loads(result_path.read_text(encoding="utf-8"))
    if payload.get("backend") != "NPU (Ascend 310B)":
        raise RuntimeError(
            "NPU benchmark worker did not report the required Ascend 310B NPU backend"
        )
    output = np.fromfile(output_path, dtype=np.float32).reshape(expected_shape)
    summary = dict(payload["timing"])
    summary.update(
        {
            "initialize_ms_excluded": payload["initialize_ms_excluded"],
            "worker_process_start_excluded": True,
            "backend": payload["backend"],
        }
    )
    return summary, output


def numerical_error(actual: np.ndarray, expected: np.ndarray) -> dict[str, float]:
    difference = np.abs(np.asarray(actual, dtype=np.float32) - expected)
    denominator = max(float(np.max(np.abs(expected))), 1.0e-12)
    return {
        "max_abs": float(difference.max(initial=0.0)),
        "mean_abs": float(difference.mean()),
        "max_abs_relative_to_reference_peak": float(difference.max(initial=0.0) / denominator),
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_model_provenance(om_path: Path) -> dict[str, Any]:
    """Attach the conversion record emitted beside each board-generated OM."""
    metadata_path = om_path.with_suffix(".om.json")
    if not metadata_path.is_file():
        return {
            "onnx_sha256": None,
            "om_sha256": sha256_file(om_path),
            "atc_command": None,
            "cann_version": None,
            "metadata_path": None,
        }
    try:
        raw = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read VOLK OM provenance {metadata_path}: {exc}") from exc
    recorded_om_sha = str(raw.get("om_sha256", ""))
    observed_om_sha = sha256_file(om_path)
    if recorded_om_sha and recorded_om_sha != observed_om_sha:
        raise RuntimeError(
            f"VOLK OM SHA256 mismatch for {om_path}: {recorded_om_sha} != {observed_om_sha}"
        )
    return {
        "onnx_sha256": raw.get("onnx_sha256"),
        "om_sha256": observed_om_sha,
        "atc_command": raw.get("atc_command"),
        "cann_version": raw.get("cann_version"),
        "metadata_path": str(metadata_path),
    }


def validate_volk_implementation_record(
    record: dict[str, Any], *, implementation: str
) -> None:
    """Ensure a manual VOLK request did not silently fall back to generic."""
    available = tuple(str(name) for name in record.get("available_implementations", []))
    if implementation != "dispatcher" and implementation not in available:
        raise RuntimeError(
            f"VOLK result does not prove requested implementation {implementation!r}; "
            f"available={available}"
        )
    if implementation == "dispatcher" and not str(record.get("dispatcher_machine", "")):
        raise RuntimeError("VOLK dispatcher result did not report its selected machine")


def onnxruntime_session_options() -> ort.SessionOptions:
    """Use one CPU worker so the ORT baseline matches the pinned CPU policy."""
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    return options


def add_throughput(summary: dict[str, Any], batch_size: int, batch_duration_ms: float) -> None:
    p50 = float(summary["p50_ms"])
    summary["batches_per_second_p50"] = 1_000.0 / p50
    summary["vectors_per_second_p50"] = batch_size * 1_000.0 / p50
    summary["p50_real_time_factor"] = batch_duration_ms / p50


def verdict(best_cpu_p50: float, npu: dict[str, Any], batch_duration_ms: float) -> dict[str, Any]:
    speedup = best_cpu_p50 / float(npu["p50_ms"])
    meets_real_time = float(npu["p95_ms"]) <= batch_duration_ms
    numerically_valid = bool(npu["numerically_valid"])
    if not numerically_valid:
        recommendation = "cpu_simd_recommended"
        reason = "npu_numerical_validation_failed"
    elif meets_real_time and speedup >= 1.2:
        recommendation = "npu_recommended"
        reason = "npu_meets_numeric_realtime_and_speedup_gates"
    elif meets_real_time and speedup >= 0.8:
        recommendation = "conditional"
        reason = "npu_meets_numeric_and_realtime_gates_but_speedup_is_marginal"
    else:
        recommendation = "cpu_simd_recommended"
        reason = "npu_does_not_meet_realtime_or_speedup_gate"
    return {
        "recommendation": recommendation,
        "reason": reason,
        "npu_speedup_over_best_volk_p50": speedup,
        "npu_p95_meets_batch_real_time": meets_real_time,
        "npu_numerically_valid": numerically_valid,
    }


def main() -> int:
    args = parse_args()
    kernels = tuple(item.strip() for item in args.kernels.split(",") if item.strip())
    unknown = set(kernels) - set(VOLK_KERNELS)
    if unknown:
        raise ValueError(f"unknown kernels: {sorted(unknown)}")
    if args.warmup < 0 or args.iterations <= 0 or args.repeats <= 0:
        raise ValueError("warmup must be non-negative; iterations and repeats must be positive")
    if args.vector_length <= 0 or not np.isfinite(args.sample_rate) or args.sample_rate <= 0:
        raise ValueError("vector length and sample rate must be positive")
    allowed_cpus = (
        sorted(os.sched_getaffinity(0))
        if hasattr(os, "sched_getaffinity")
        else list(range(os.cpu_count() or 1))
    )
    cpu = allowed_cpus[-1] if args.cpu is None else args.cpu
    if cpu not in allowed_cpus:
        raise ValueError(f"CPU {cpu} is unavailable; allowed CPU affinity is {allowed_cpus}")
    if hasattr(os, "sched_setaffinity"):
        os.sched_setaffinity(0, {cpu})

    project_root = Path(__file__).resolve().parents[1]
    harness_source = project_root / "scripts" / "benchmark_volk_kernels.c"
    if not harness_source.is_file():
        raise RuntimeError(f"VOLK benchmark source missing: {harness_source}")
    thermal_before = thermal_snapshot()
    npu_temperature_before = npu_temperature_snapshot()
    result: dict[str, Any] = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "methodology": {
            "vector_length": args.vector_length,
            "batch_sizes": list(args.batch_sizes),
            "warmup_per_repeat": args.warmup,
            "iterations_per_repeat": args.iterations,
            "repeats": args.repeats,
            "cpu_boundary": "VOLK kernel only; input already resident in host memory",
            "onnxruntime_boundary": "InferenceSession.run with host input and host output",
            "npu_boundary": "Tensor creation, H2D, OM execution, D2H, output copy",
            "excluded": ["model_initialization", "capture", "CU8_decode", "layout_conversion"],
            "power_measurement": "not measured; external meter required",
            "percentiles": "all repeats concatenated; 900 measured samples per backend by default",
            "process_cpu_time": "measurement loops only; warm-up excluded",
        },
        "environment": {
            "hostname": platform.node(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "numpy": np.__version__,
            "onnxruntime": ort.__version__,
            "volk_version": command_text(["volk-config-info", "--version"]),
            "volk_machine": command_text(["volk-config-info", "--machine"]),
            "volk_available_machines": command_text(["volk-config-info", "--avail-machines"]),
            "npu_smi": command_text(["npu-smi", "info"]),
            "allowed_cpu_affinity_at_start": allowed_cpus,
            "selected_cpu": cpu,
            "openblas_num_threads": os.environ.get("OPENBLAS_NUM_THREADS", "unset"),
            "omp_num_threads": os.environ.get("OMP_NUM_THREADS", "unset"),
            "onnxruntime_intra_op_num_threads": 1,
            "onnxruntime_inter_op_num_threads": 1,
            "onnxruntime_execution_mode": "ORT_SEQUENTIAL",
        },
        "thermal_before": thermal_before,
        "npu_temperature_before": npu_temperature_before,
        "cases": [],
    }

    with tempfile.TemporaryDirectory(prefix="case5-volk-npu-") as directory:
        temporary = Path(directory)
        harness = temporary / "benchmark_volk_kernels"
        result["compile_command"] = compile_volk_harness(harness_source, harness)
        for kernel in kernels:
            for batch_size in args.batch_sizes:
                source = deterministic_input(
                    kernel,
                    batch_size=batch_size,
                    vector_length=args.vector_length,
                )
                expected = volk_kernel_numpy(kernel, source)
                expected_shape = output_shape(kernel, batch_size, args.vector_length)
                stem = f"volk_{kernel}_b{batch_size}_n{args.vector_length}"
                onnx_path = args.models_dir / f"{stem}.onnx"
                om_path = args.models_dir / f"{stem}.om"
                if not onnx_path.is_file() or not om_path.is_file():
                    raise FileNotFoundError(
                        f"missing prepared model for {kernel}/batch={batch_size}: "
                        f"{onnx_path} or {om_path}"
                    )
                case: dict[str, Any] = {
                    "kernel": kernel,
                    "batch_size": batch_size,
                    "vector_length": args.vector_length,
                    "input_shape": list(source.shape),
                    "output_shape": list(expected_shape),
                    "input_sha256": sha256_bytes(source),
                    "batch_duration_ms": 1_000.0 * batch_size * args.vector_length / args.sample_rate,
                    "volk": {},
                    "model_provenance": load_model_provenance(om_path),
                }
                for implementation in ("generic", "neon", "dispatcher"):
                    summary, output = run_volk_harness(
                        harness,
                        kernel=kernel,
                        implementation=implementation,
                        source=source,
                        expected_shape=expected_shape,
                        warmup=args.warmup,
                        iterations=args.iterations,
                        repeats=args.repeats,
                        cpu=cpu,
                        temporary=temporary,
                    )
                    np.testing.assert_allclose(output, expected, rtol=5.0e-5, atol=1.0e-4)
                    add_throughput(summary, batch_size, case["batch_duration_ms"])
                    summary["numerical_error"] = numerical_error(output, expected)
                    case["volk"][implementation] = summary

                ort_session = ort.InferenceSession(
                    str(onnx_path),
                    sess_options=onnxruntime_session_options(),
                    providers=["CPUExecutionProvider"],
                )
                ort_summary, ort_output = benchmark_callable(
                    lambda values: ort_session.run(
                        ["output_tensor"], {"input_tensor": values}
                    )[0],
                    source,
                    warmup=args.warmup,
                    iterations=args.iterations,
                    repeats=args.repeats,
                )
                np.testing.assert_allclose(ort_output, expected, rtol=5.0e-5, atol=1.0e-4)
                add_throughput(ort_summary, batch_size, case["batch_duration_ms"])
                ort_summary["numerical_error"] = numerical_error(ort_output, expected)
                case["onnxruntime_cpu"] = ort_summary

                npu_summary, npu_output = run_npu_worker(
                    om_path,
                    source,
                    expected_shape,
                    warmup=args.warmup,
                    iterations=args.iterations,
                    repeats=args.repeats,
                    temporary=temporary,
                )
                finite = bool(np.all(np.isfinite(npu_output)))
                numerically_valid = finite and bool(
                    np.allclose(npu_output, expected, rtol=args.rtol, atol=args.atol)
                )
                add_throughput(npu_summary, batch_size, case["batch_duration_ms"])
                npu_summary.update(
                    {
                        "finite": finite,
                        "numerically_valid": numerically_valid,
                        "rtol": args.rtol,
                        "atol": args.atol,
                        "numerical_error": numerical_error(npu_output, expected),
                    }
                )
                case["npu_om_end_to_end"] = npu_summary
                best_cpu_p50 = min(
                    float(case["volk"][name]["p50_ms"])
                    for name in ("generic", "neon", "dispatcher")
                )
                case["decision"] = verdict(
                    best_cpu_p50, npu_summary, float(case["batch_duration_ms"])
                )
                result["cases"].append(case)

    result["thermal_after"] = thermal_snapshot()
    result["npu_temperature_after"] = npu_temperature_snapshot()
    if args.output is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output = Path("data/volk_npu_benchmark") / f"volk_npu_{stamp}.json"
    else:
        output = args.output.resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing VOLK benchmark evidence: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(result, indent=2, ensure_ascii=True, allow_nan=False)
    with output.open("x", encoding="utf-8") as handle:
        handle.write(serialized + "\n")
    print(serialized)
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
