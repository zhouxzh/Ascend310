"""Isolated one-model ACL worker used by the unified VOLK/NPU benchmark."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import numpy as np

from ..npu import AscendOmRunner


def parse_shape(value: str) -> tuple[int, ...]:
    parts = value.split(",")
    if not parts or any(not item.strip() for item in parts):
        raise argparse.ArgumentTypeError("input shape must contain positive dimensions")
    try:
        shape = tuple(int(item.strip()) for item in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("input shape must contain positive dimensions") from exc
    if not shape or any(item <= 0 for item in shape):
        raise argparse.ArgumentTypeError("input shape must contain positive dimensions")
    return shape


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--om", type=Path, required=True)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    parser.add_argument("--input-shape", type=parse_shape, required=True)
    parser.add_argument("--warmup", type=int, required=True)
    parser.add_argument("--iterations", type=int, required=True)
    parser.add_argument("--repeats", type=int, required=True)
    return parser.parse_args()


def timing_summary(values_ms: list[float], process_cpu_ms: float) -> dict[str, float]:
    values = np.asarray(values_ms, dtype=np.float64)
    return {
        "mean_ms": float(values.mean()),
        "p50_ms": float(np.percentile(values, 50)),
        "p95_ms": float(np.percentile(values, 95)),
        "min_ms": float(values.min()),
        "max_ms": float(values.max()),
        "process_cpu_ms": float(process_cpu_ms),
    }


def main() -> int:
    args = parse_args()
    if args.warmup < 0 or args.iterations <= 0 or args.repeats <= 0:
        raise ValueError("invalid benchmark iteration count")
    expected_values = int(np.prod(args.input_shape, dtype=np.int64))
    if args.input.stat().st_size != expected_values * np.dtype(np.float32).itemsize:
        raise ValueError("benchmark input byte length does not match --input-shape")
    for path, label in ((args.output, "output"), (args.result, "result")):
        if path.resolve() == args.input.resolve():
            raise ValueError(f"benchmark {label} path must not overwrite the input")
        if path.exists():
            raise FileExistsError(f"refusing to overwrite existing benchmark {label}: {path}")
    source = np.fromfile(args.input, dtype=np.float32).reshape(args.input_shape)
    if not np.all(np.isfinite(source)):
        raise ValueError("benchmark input contains NaN or Inf")
    runner = AscendOmRunner(args.om)
    initialize_started = time.perf_counter_ns()
    status = runner.initialize()
    initialize_ms = (time.perf_counter_ns() - initialize_started) / 1_000_000.0
    if not status.ready or status.backend != "NPU (Ascend 310B)":
        runner.close()
        raise RuntimeError(status.message)
    timings: list[float] = []
    result: np.ndarray | None = None
    process_cpu_ms = 0.0
    try:
        for _repeat in range(args.repeats):
            for _index in range(args.warmup):
                runner.run(source)
            process_started = time.process_time_ns()
            for _index in range(args.iterations):
                started = time.perf_counter_ns()
                result = runner.run(source)
                timings.append((time.perf_counter_ns() - started) / 1_000_000.0)
            process_cpu_ms += (time.process_time_ns() - process_started) / 1_000_000.0
    finally:
        runner.close()
    if result is None:
        raise RuntimeError("NPU worker produced no output")
    result_array = np.ascontiguousarray(result, dtype=np.float32)
    if not np.all(np.isfinite(result_array)):
        raise RuntimeError("NPU worker returned NaN or Inf")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.result.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("xb") as output:
        output.write(result_array.tobytes())
    with args.result.open("x", encoding="utf-8") as handle:
        json.dump(
            {
                "backend": status.backend,
                "initialize_ms_excluded": initialize_ms,
                "timing": timing_summary(timings, process_cpu_ms),
            },
            handle,
            indent=2,
            ensure_ascii=True,
            allow_nan=False,
        )
        handle.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
