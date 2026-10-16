from __future__ import annotations

import argparse
import time

import numpy as np

from perf_utils import (
    DEFAULT_OUTPUT_DIR,
    ResNetPreprocessWorkspace,
    deterministic_rgb_frame,
    make_summary_row,
    preprocess_resnet_rgb,
    print_stage_table,
    summarize_stages,
    write_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="CPU preprocessing benchmark for ResNet18 input.")
    parser.add_argument("--warmup", type=int, default=10, help="Warmup runs per variant.")
    parser.add_argument("--runs", type=int, default=500, help="Measured runs per variant.")
    parser.add_argument("--resolution", default="1920x1080", help="Input resolution, for example 1920x1080.")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_DIR / "cpu_preprocess_benchmark.json"),
        help="Output metrics JSON path.",
    )
    return parser.parse_args()


def parse_resolution(text: str) -> tuple[int, int]:
    width_text, height_text = text.lower().split("x", 1)
    return int(width_text), int(height_text)


def measure_naive(frames: list[np.ndarray], warmup: int, runs: int) -> list[float]:
    for index in range(warmup):
        preprocess_resnet_rgb(frames[index])

    samples = []
    for index in range(runs):
        start = time.perf_counter()
        preprocess_resnet_rgb(frames[warmup + index])
        samples.append((time.perf_counter() - start) * 1000.0)
    return samples


def measure_workspace(frames: list[np.ndarray], width: int, height: int, warmup: int, runs: int) -> list[float]:
    workspace = ResNetPreprocessWorkspace(height, width)
    for index in range(warmup):
        workspace(frames[index])

    samples = []
    for index in range(runs):
        start = time.perf_counter()
        workspace(frames[warmup + index])
        samples.append((time.perf_counter() - start) * 1000.0)
    return samples


def main() -> int:
    args = parse_args()
    width, height = parse_resolution(args.resolution)
    frame_count = args.warmup + args.runs
    frames = [deterministic_rgb_frame(index, height, width) for index in range(frame_count)]

    naive_samples = measure_naive(frames, args.warmup, args.runs)
    workspace_samples = measure_workspace(frames, width, height, args.warmup, args.runs)

    naive_mean = float(np.mean(naive_samples))
    workspace_mean = float(np.mean(workspace_samples))
    speedup = naive_mean / workspace_mean if workspace_mean else 0.0

    metrics = {
        "naive_numpy": summarize_stages({"preprocess": naive_samples})["preprocess"],
        "workspace_reuse": summarize_stages({"preprocess": workspace_samples})["preprocess"],
    }
    report = {
        "case": "03_cpu_preprocess_benchmark",
        "resolution": f"{width}x{height}",
        "warmup": args.warmup,
        "runs": args.runs,
        "variants": metrics,
        "summary_rows": [
            make_summary_row(
                "CPU 预处理",
                "naive_numpy",
                naive_samples,
                note="每帧创建中间数组",
            ),
            make_summary_row(
                "CPU 预处理",
                "workspace_reuse",
                workspace_samples,
                speedup=speedup,
                note="复用 resize/CHW 工作区",
            ),
        ],
    }
    output_path = write_report(args.output, report)
    print_stage_table(
        {
            "naive_numpy": metrics["naive_numpy"],
            "workspace_reuse": metrics["workspace_reuse"],
        }
    )
    print(f"\nspeedup(mean): {speedup:.3f}x")
    print(f"metrics saved: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
