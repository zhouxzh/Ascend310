from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from .config import DEFAULT_IMAGE_SIZE
from .postprocess import Detections


def new_coco_ground_truth_dict() -> tuple[dict[str, Any], set[int]]:
    return {"images": [], "annotations": [], "categories": []}, set()


def add_coco_ground_truth_item(
    coco_gt: dict[str, Any],
    category_ids: set[int],
    item: dict[str, Any],
    index: int,
    image_size: int = DEFAULT_IMAGE_SIZE,
) -> None:
    image_id = int(item.get("image_id", index))
    image_width, image_height = item["image"].size
    coco_gt["images"].append({"id": image_id, "width": image_size, "height": image_size})

    objects = item.get("objects", {})
    for bbox, category_id in zip(objects.get("bbox", []), objects.get("category", [])):
        xmin, ymin, xmax, ymax = bbox
        bx = xmin * image_size / image_width
        by = ymin * image_size / image_height
        bw = (xmax - xmin) * image_size / image_width
        bh = (ymax - ymin) * image_size / image_height
        mapped_category_id = int(category_id) + 1
        coco_gt["annotations"].append(
            {
                "id": len(coco_gt["annotations"]),
                "image_id": image_id,
                "category_id": mapped_category_id,
                "bbox": [bx, by, bw, bh],
                "area": bw * bh,
                "iscrowd": 0,
            }
        )
        category_ids.add(mapped_category_id)


def write_coco_ground_truth(coco_gt: dict[str, Any], category_ids: set[int], gt_file: str) -> str:
    coco_gt["categories"] = [{"id": category_id, "name": str(category_id)} for category_id in sorted(category_ids)]
    gt_path = Path(gt_file)
    gt_path.parent.mkdir(parents=True, exist_ok=True)
    with gt_path.open("w", encoding="utf-8") as file:
        json.dump(coco_gt, file)
    return str(gt_path)


def build_coco_predictions(
    detections: Detections,
    image_id: int,
    image_width: int,
    image_height: int,
) -> list[dict[str, Any]]:
    if detections.boxes.size == 0:
        return []

    scaled_boxes = detections.boxes.astype(np.float32, copy=True)
    scaled_boxes[:, [0, 2]] *= image_width
    scaled_boxes[:, [1, 3]] *= image_height
    scaled_boxes[:, 2] -= scaled_boxes[:, 0]
    scaled_boxes[:, 3] -= scaled_boxes[:, 1]

    return [
        {
            "image_id": int(image_id),
            "category_id": int(label),
            "bbox": [round(float(x), 3) for x in box],
            "score": round(float(score), 5),
        }
        for box, label, score in zip(scaled_boxes.tolist(), detections.labels.tolist(), detections.scores.tolist())
    ]


def run_coco_evaluation(
    gt_file: str,
    result_file: str,
    image_ids: list[int],
) -> dict[str, float]:
    from pycocotools.coco import COCO
    from pycocotools.cocoeval import COCOeval

    coco_gt = COCO(gt_file)
    coco_dt = coco_gt.loadRes(result_file)
    coco_eval = COCOeval(coco_gt, coco_dt, "bbox")
    coco_eval.params.imgIds = sorted(image_ids)
    coco_eval.evaluate()
    coco_eval.accumulate()
    coco_eval.summarize()
    return {
        "mAP": float(coco_eval.stats[0]),
        "mAP_50": float(coco_eval.stats[1]),
        "mAP_75": float(coco_eval.stats[2]),
        "mAP_small": float(coco_eval.stats[3]),
        "mAP_medium": float(coco_eval.stats[4]),
        "mAP_large": float(coco_eval.stats[5]),
    }
