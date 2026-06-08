from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

from acl_resnet_runner import AclSession, ReuseResNetRunner
from perf_utils import (
    DEFAULT_MODEL,
    DEFAULT_OUTPUT_DIR,
    REPO_ROOT,
    StageRecorder,
    deterministic_rgb_frame,
    make_summary_row,
    preprocess_resnet_rgb,
    print_stage_table,
    summarize_stages,
    write_report,
)


DEFAULT_AIPP_MODEL = REPO_ROOT / "samples/chapter7/model/resnet18_tiny_imagenet_aipp.om"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare CPU preprocessing with static AIPP preprocessing.")
    parser.add_argument("--model", default=str(DEFAULT_MODEL), help="Float32 NCHW OM model path.")
    parser.add_argument("--aipp-model", default=str(DEFAULT_AIPP_MODEL), help="Static AIPP OM model path.")
    parser.add_argument("--device", type=int, default=0, help="Ascend device id.")
    parser.add_argument("--warmup", type=int, default=5, help="Warmup runs.")
    parser.add_argument("--runs", type=int, default=100, help="Measured runs.")
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT_DIR / "aipp_preprocess_compare.json"),
        help="Output metrics JSON path.",
    )
    return parser.parse_args()


def ensure_inputs(args: argparse.Namespace) -> None:
    if not Path(args.model).exists():
        raise FileNotFoundError(
            f"Base OM model not found: {args.model}. Run `python3 tools/download_model.py` from samples/chapter7 first."
        )
    if not Path(args.aipp_model).exists():
        raise FileNotFoundError(
            f"AIPP OM model not found: {args.aipp_model}. "
            "Download it with `python3 tools/download_model.py --all`, or create it with "
            "`bash tools/convert_aipp_resnet18.sh` after downloading ONNX. "
            "This benchmarking script does not invoke ATC."
        )


def record_total(recorder: StageRecorder) -> None:
    total_ms = recorder.samples["prepare"][-1]
    total_ms += recorder.samples["inference_total"][-1]
    total_ms += recorder.samples["postprocess"][-1]
    recorder.add("end_to_end", total_ms)


def main() -> int:
    args = parse_args()
    ensure_inputs(args)

    cpu_recorder = StageRecorder()
    aipp_recorder = StageRecorder()
    max_abs_diffs: list[float] = []
    mean_abs_diffs: list[float] = []
    top1_matches = 0

    with AclSession(args.device):
        cpu_runner = ReuseResNetRunner(args.model, input_dtype=np.float32)
        aipp_runner = ReuseResNetRunner(args.aipp_model, input_dtype=np.uint8)
        try:
            for index in range(args.warmup):
                frame = deterministic_rgb_frame(index, 64, 64)
                cpu_runner.infer(preprocess_resnet_rgb(frame))
                aipp_runner.infer(frame)

            for index in range(args.runs):
                frame = deterministic_rgb_frame(index + args.warmup, 64, 64)

                with cpu_recorder.time("prepare"):
                    cpu_input = preprocess_resnet_rgb(frame)
                with cpu_recorder.time("inference_total"):
                    cpu_outputs, cpu_timings = cpu_runner.infer(cpu_input)
                cpu_recorder.extend(cpu_timings)
                with cpu_recorder.time("postprocess"):
                    cpu_top1 = int(np.argmax(cpu_outputs[0]))
                record_total(cpu_recorder)

                with aipp_recorder.time("prepare"):
                    aipp_input = np.ascontiguousarray(frame)
                with aipp_recorder.time("inference_total"):
                    aipp_outputs, aipp_timings = aipp_runner.infer(aipp_input)
                aipp_recorder.extend(aipp_timings)
                with aipp_recorder.time("postprocess"):
                    aipp_top1 = int(np.argmax(aipp_outputs[0]))
                record_total(aipp_recorder)

                cpu_flat = np.asarray(cpu_outputs[0], dtype=np.float32).reshape(-1)
                aipp_flat = np.asarray(aipp_outputs[0], dtype=np.float32).reshape(-1)
                diff = np.abs(cpu_flat - aipp_flat)
                max_abs_diffs.append(float(np.max(diff)))
                mean_abs_diffs.append(float(np.mean(diff)))
                top1_matches += int(cpu_top1 == aipp_top1)
        finally:
            cpu_runner.release()
            aipp_runner.release()

    cpu_metrics = summarize_stages(cpu_recorder.samples)
    aipp_metrics = summarize_stages(aipp_recorder.samples)
    cpu_mean = float(cpu_metrics["end_to_end"]["mean_ms"])
    aipp_mean = float(aipp_metrics["end_to_end"]["mean_ms"])
    speedup = cpu_mean / aipp_mean if aipp_mean else 0.0

    report = {
        "case": "06_aipp_preprocess_compare",
        "model": str(args.model),
        "aipp_model": str(args.aipp_model),
        "device": args.device,
        "warmup": args.warmup,
        "runs": args.runs,
        "variants": {
            "cpu_preprocess": cpu_metrics,
            "static_aipp_rgb": aipp_metrics,
        },
        "output_diff": {
            "top1_match_count": top1_matches,
            "top1_match_rate": round(top1_matches / args.runs, 6) if args.runs else 0.0,
            "max_abs_diff_mean": round(float(np.mean(max_abs_diffs)), 6) if max_abs_diffs else 0.0,
            "max_abs_diff_max": round(float(np.max(max_abs_diffs)), 6) if max_abs_diffs else 0.0,
            "mean_abs_diff_mean": round(float(np.mean(mean_abs_diffs)), 6) if mean_abs_diffs else 0.0,
        },
        "summary_rows": [
            make_summary_row(
                "AIPP 预处理下沉",
                "cpu_preprocess",
                cpu_recorder.samples["end_to_end"],
                note="CPU 完成 resize/normalize/HWC->CHW 后推理",
            ),
            make_summary_row(
                "AIPP 预处理下沉",
                "static_aipp_rgb",
                aipp_recorder.samples["end_to_end"],
                speedup=speedup,
                note="AIPP 接收 RGB888_U8 并在模型输入侧归一化",
            ),
        ],
    }

    output_path = write_report(args.output, report)
    print("\n[cpu_preprocess]")
    print_stage_table(cpu_metrics)
    print("\n[static_aipp_rgb]")
    print_stage_table(aipp_metrics)
    print(f"\nspeedup(end_to_end mean): {speedup:.3f}x")
    print(
        "output diff: "
        f"top1_match_rate={report['output_diff']['top1_match_rate']:.4f}, "
        f"max_abs_diff_max={report['output_diff']['max_abs_diff_max']:.6f}"
    )
    print(f"metrics saved: {output_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
