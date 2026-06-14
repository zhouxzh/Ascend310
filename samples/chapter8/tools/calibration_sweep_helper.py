#!/usr/bin/env python3
"""Small helpers used by sweep_calibration_samples.sh."""

from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path


SUMMARY_FIELDS = [
    "calibration_samples",
    "validation_samples",
    "fp32_top1_pct",
    "fp32_top5_pct",
    "fp16_top1_pct",
    "fp16_top5_pct",
    "int8_top1_pct",
    "int8_top5_pct",
    "int8_minus_fp32_top1_pp",
    "int8_minus_fp32_top5_pp",
    "int8_mean_ms",
    "int8_p95_ms",
    "report",
    "om_model",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Helper for calibration sample sweep.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subset = subparsers.add_parser("make-subset", help="Create a deterministic calibration subset.")
    subset.add_argument("--source", default="data/calib_list.txt", help="Full calibration list.")
    subset.add_argument("--output", required=True, help="Subset list output path.")
    subset.add_argument("--count", type=int, required=True, help="Number of images in the subset.")
    subset.add_argument("--seed", type=int, default=2024, help="Shuffle seed.")

    summary = subparsers.add_parser("append-summary", help="Append one accuracy report to summary.csv.")
    summary.add_argument("--report", required=True, help="JSON report from 05_validate_accuracy.py.")
    summary.add_argument("--summary", required=True, help="Summary CSV path.")
    summary.add_argument("--calibration-samples", type=int, required=True, help="Calibration image count.")
    summary.add_argument("--int8-label", required=True, help="INT8 variant label in the JSON report.")
    summary.add_argument("--int8-model", required=True, help="INT8 OM model path for this point.")
    summary.add_argument("--init", action="store_true", help="Write CSV header before appending.")

    return parser.parse_args()


def make_subset(args: argparse.Namespace) -> None:
    source = Path(args.source)
    output = Path(args.output)
    if args.count <= 0:
        raise ValueError("--count must be positive")
    if not source.exists():
        raise FileNotFoundError(f"Calibration list not found: {source}")

    entries = [
        line.strip()
        for line in source.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    if args.count > len(entries):
        raise ValueError(f"Requested {args.count} samples, but {source} only has {len(entries)} entries.")

    rng = random.Random(args.seed)
    rng.shuffle(entries)
    selected = entries[: args.count]

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(selected) + "\n", encoding="utf-8")
    print(f"subset list: {output} ({len(selected)} images)")


def append_summary(args: argparse.Namespace) -> None:
    report_path = Path(args.report)
    summary_path = Path(args.summary)
    report = json.loads(report_path.read_text(encoding="utf-8"))

    fp32 = report["variants"]["fp32"]
    fp16 = report["variants"]["fp16"]
    int8 = report["variants"][args.int8_label]

    fp32_top1 = float(fp32["accuracy"]["top1_accuracy"]) * 100.0
    fp32_top5 = float(fp32["accuracy"]["top5_accuracy"]) * 100.0
    fp16_top1 = float(fp16["accuracy"]["top1_accuracy"]) * 100.0
    fp16_top5 = float(fp16["accuracy"]["top5_accuracy"]) * 100.0
    int8_top1 = float(int8["accuracy"]["top1_accuracy"]) * 100.0
    int8_top5 = float(int8["accuracy"]["top5_accuracy"]) * 100.0
    int8_timing = int8["metrics"].get("inference_total", {})

    row = {
        "calibration_samples": args.calibration_samples,
        "validation_samples": report["dataset"]["evaluated_images"],
        "fp32_top1_pct": f"{fp32_top1:.4f}",
        "fp32_top5_pct": f"{fp32_top5:.4f}",
        "fp16_top1_pct": f"{fp16_top1:.4f}",
        "fp16_top5_pct": f"{fp16_top5:.4f}",
        "int8_top1_pct": f"{int8_top1:.4f}",
        "int8_top5_pct": f"{int8_top5:.4f}",
        "int8_minus_fp32_top1_pp": f"{int8_top1 - fp32_top1:.4f}",
        "int8_minus_fp32_top5_pp": f"{int8_top5 - fp32_top5:.4f}",
        "int8_mean_ms": f"{float(int8_timing.get('mean_ms', 0.0)):.6f}",
        "int8_p95_ms": f"{float(int8_timing.get('p95_ms', 0.0)):.6f}",
        "report": str(report_path),
        "om_model": args.int8_model,
    }

    summary_path.parent.mkdir(parents=True, exist_ok=True)
    with summary_path.open("w" if args.init else "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=SUMMARY_FIELDS)
        if args.init:
            writer.writeheader()
        writer.writerow(row)
    print(f"summary row appended: {summary_path}")


def main() -> int:
    args = parse_args()
    if args.command == "make-subset":
        make_subset(args)
    elif args.command == "append-summary":
        append_summary(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
