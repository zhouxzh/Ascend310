from __future__ import annotations

import argparse
from pathlib import Path
from typing import Type

import numpy as np

from acl_resnet_runner import AclSession, BaseResNetRunner, NaiveResNetRunner, ReuseResNetRunner
from perf_utils import (
    DEFAULT_MODEL,
    DEFAULT_OUTPUT_DIR,
    StageRecorder,
    deterministic_rgb_frame,
    make_summary_row,
    preprocess_resnet_rgb,
    print_stage_table,
    summarize_stages,
    write_report,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare naive ACL allocation with reusable ACL buffers.")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help="Path to resnet18_tiny_imagenet.om.")
    parser.add_argument("--device", type=int, default=0, help="Ascend device id.")
    parser.add_argument("--warmup", type=int, default=5, help="Warmup runs per variant.")
    parser.add_argument("--runs", type=int, default=100, help="Measured runs per variant.")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_DIR / "buffer_reuse_benchmark.json"),
        help="Output metrics JSON path.",
    )
    return parser.parse_args()


def measure_variant(
    runner_cls: Type[BaseResNetRunner],
    model_path: Path,
    *,
    warmup: int,
    runs: int,
    name: str,
) -> dict:
    recorder = StageRecorder()
    runner = runner_cls(model_path)
    try:
        for index in range(warmup):
            frame = deterministic_rgb_frame(index, 64, 64)
            runner.infer(preprocess_resnet_rgb(frame))

        for index in range(runs):
            frame = deterministic_rgb_frame(index + warmup, 64, 64)
            with recorder.time("preprocess"):
                input_tensor = preprocess_resnet_rgb(frame)
            with recorder.time("inference_total"):
                outputs, timings = runner.infer(input_tensor)
            recorder.extend(timings)
            with recorder.time("postprocess"):
                int(np.argmax(outputs[0]))

            total_ms = recorder.samples["preprocess"][-1]
            total_ms += recorder.samples["inference_total"][-1]
            total_ms += recorder.samples["postprocess"][-1]
            recorder.add("end_to_end", total_ms)
    finally:
        runner.release()

    return {
        "variant": name,
        "metrics": summarize_stages(recorder.samples),
        "end_to_end_samples": recorder.samples["end_to_end"],
    }


def main() -> int:
    args = parse_args()
    model_path = Path(args.model)

    with AclSession(args.device):
        naive = measure_variant(
            NaiveResNetRunner,
            model_path,
            warmup=args.warmup,
            runs=args.runs,
            name="naive_alloc_each_frame",
        )
        reusable = measure_variant(
            ReuseResNetRunner,
            model_path,
            warmup=args.warmup,
            runs=args.runs,
            name="reuse_acl_buffers",
        )

    naive_mean = naive["metrics"]["end_to_end"]["mean_ms"]
    reuse_mean = reusable["metrics"]["end_to_end"]["mean_ms"]
    speedup = float(naive_mean) / float(reuse_mean) if reuse_mean else 0.0

    report = {
        "case": "02_buffer_reuse_benchmark",
        "model": str(model_path),
        "device": args.device,
        "warmup": args.warmup,
        "runs": args.runs,
        "variants": {
            "naive_alloc_each_frame": naive["metrics"],
            "reuse_acl_buffers": reusable["metrics"],
        },
        "summary_rows": [
            make_summary_row(
                "ResNet18 Buffer 复用",
                "naive_alloc_each_frame",
                naive["end_to_end_samples"],
                note="对照组",
            ),
            make_summary_row(
                "ResNet18 Buffer 复用",
                "reuse_acl_buffers",
                reusable["end_to_end_samples"],
                speedup=speedup,
                note="模型加载后一次性创建输入输出 Buffer 和 Dataset",
            ),
        ],
    }
    output_path = write_report(args.output, report)
    print("\n[naive_alloc_each_frame]")
    print_stage_table(naive["metrics"])
    print("\n[reuse_acl_buffers]")
    print_stage_table(reusable["metrics"])
    print(f"\nspeedup(end_to_end mean): {speedup:.3f}x")
    print(f"metrics saved: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
