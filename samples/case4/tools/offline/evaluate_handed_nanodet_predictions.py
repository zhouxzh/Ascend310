#!/usr/bin/env python3
"""Calculate held-out COCO and handedness metrics from one backend prediction file."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from handed_nanodet_fulltest import (
    TASK_IOU_THRESHOLD,
    TASK_SCORE_THRESHOLD,
    coco_metrics,
    sha256_file,
    task_metrics,
    validate_coco_predictions,
    write_json,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--backend-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    annotations = json.loads(args.annotations.read_text(encoding="utf-8"))
    predictions = json.loads(args.predictions.read_text(encoding="utf-8"))
    backend = json.loads(args.backend_report.read_text(encoding="utf-8"))
    image_ids = {int(item["id"]) for item in annotations["images"]}
    normalized = validate_coco_predictions(predictions, image_ids)
    if backend.get("execution_status") != "completed":
        raise ValueError(f"backend report is not completed: {backend.get('execution_status')!r}")
    report = {
        "schema_version": 1,
        "kind": "handed_nanodet_fulltest_metrics",
        "backend": {
            "kind": backend.get("kind"),
            "execution_status": backend.get("execution_status"),
            "artifact": backend.get("artifact"),
            "board": backend.get("board"),
            "source": backend.get("source"),
        },
        "annotations": {"path": str(args.annotations), "sha256": sha256_file(args.annotations), "images": len(annotations["images"]), "objects": len(annotations["annotations"])},
        "predictions": {"path": str(args.predictions), "sha256": sha256_file(args.predictions), "count": len(normalized)},
        "task_protocol": {"score_threshold": TASK_SCORE_THRESHOLD, "iou_threshold": TASK_IOU_THRESHOLD},
        "metrics": {
            "coco": coco_metrics(args.annotations, normalized),
            "task": task_metrics(annotations, normalized),
        },
    }
    write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
