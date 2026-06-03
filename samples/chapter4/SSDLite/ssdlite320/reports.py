from __future__ import annotations

import csv
from pathlib import Path
from typing import Any


REPORT_COLUMNS = [
    "timestamp",
    "backbone",
    "model_path",
    "provider",
    "img_size",
    "dbox_min_ratio",
    "dbox_max_ratio",
    "mAP",
    "AP50",
    "AP75",
    "mAP_small",
    "mAP_medium",
    "mAP_large",
    "fps_total",
    "fps_inference",
    "result_file",
]


def resolve_report_path(args) -> Path:
    if args.report_file:
        return Path(args.report_file)

    run_id = getattr(args, "run_id", None)
    if not run_id:
        compact_timestamp = args.run_timestamp.replace("-", "").replace(":", "").replace("T", "_")
        run_id = f"{args.backend}_eval_{compact_timestamp}"
    return Path(args.report_dir) / f"{run_id}_{args.backbone}.csv"


def write_eval_report(args, metrics: dict[str, Any], timestamp: str) -> str:
    report_path = resolve_report_path(args)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    row = {
        "timestamp": timestamp,
        "backbone": args.backbone,
        "model_path": str(args.model_path),
        "provider": args.backend,
        "img_size": args.img_size,
        "dbox_min_ratio": args.dbox_min_ratio,
        "dbox_max_ratio": args.dbox_max_ratio,
        "mAP": metrics.get("mAP", 0.0),
        "AP50": metrics.get("mAP_50", 0.0),
        "AP75": metrics.get("mAP_75", 0.0),
        "mAP_small": metrics.get("mAP_small", 0.0),
        "mAP_medium": metrics.get("mAP_medium", 0.0),
        "mAP_large": metrics.get("mAP_large", 0.0),
        "fps_total": metrics.get("fps_total", 0.0),
        "fps_inference": metrics.get("fps_inference", 0.0),
        "result_file": metrics.get("result_file", ""),
    }

    with report_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=REPORT_COLUMNS)
        writer.writeheader()
        writer.writerow(row)

    return str(report_path)
