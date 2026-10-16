from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np

from acl_resnet_runner import AclSession, NaiveResNetRunner
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
    parser = argparse.ArgumentParser(description="ResNet18 naive sync inference baseline on Ascend 310B.")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help="Path to resnet18_tiny_imagenet.om.")
    parser.add_argument("--device", type=int, default=0, help="Ascend device id.")
    parser.add_argument("--warmup", type=int, default=5, help="Warmup runs.")
    parser.add_argument("--runs", type=int, default=100, help="Measured runs.")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_DIR / "baseline_resnet_sync.json"),
        help="Output metrics JSON path.",
    )
    return parser.parse_args()


def run(args: argparse.Namespace) -> dict:
    recorder = StageRecorder()
    model_path = Path(args.model)

    with AclSession(args.device):
        runner = NaiveResNetRunner(model_path)
        try:
            for index in range(args.warmup):
                frame = deterministic_rgb_frame(index, 64, 64)
                input_tensor = preprocess_resnet_rgb(frame)
                runner.infer(input_tensor)

            total_start = time.perf_counter()
            for index in range(args.runs):
                frame = deterministic_rgb_frame(index + args.warmup, 64, 64)
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
            wall_ms = (time.perf_counter() - total_start) * 1000.0
        finally:
            runner.release()

    metrics = summarize_stages(recorder.samples)
    report = {
        "case": "01_baseline_resnet_sync",
        "model": str(model_path),
        "device": args.device,
        "warmup": args.warmup,
        "runs": args.runs,
        "wall_ms": round(wall_ms, 4),
        "metrics": metrics,
        "summary_rows": [
            make_summary_row(
                "ResNet18 同步推理",
                "naive_alloc_each_frame",
                recorder.samples["end_to_end"],
                note="每帧申请/释放 ACL 输入输出 Buffer 和 Dataset",
            )
        ],
    }
    return report


def main() -> int:
    args = parse_args()
    report = run(args)
    output_path = write_report(args.output, report)
    print_stage_table(report["metrics"])
    print(f"\nmetrics saved: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
