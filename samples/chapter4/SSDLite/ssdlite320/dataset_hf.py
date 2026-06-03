from __future__ import annotations

from typing import Any

import numpy as np

from .config import DEFAULT_IMAGE_SIZE
from .data import iter_dataset, load_coco_val


def extract_category_names(dataset: Any) -> list[str] | None:
    try:
        objects_feature = dataset.features["objects"]
        category_feature = getattr(objects_feature, "feature", objects_feature)["category"]
        names = getattr(category_feature, "feature", category_feature).names
        return ["BACKGROUND", *names] if isinstance(names, list) else None
    except Exception:
        return None


def extract_ground_truth(item: dict[str, Any], img_size: int = DEFAULT_IMAGE_SIZE) -> tuple[np.ndarray, np.ndarray]:
    objects = item["objects"]
    if len(objects["bbox"]) == 0:
        return np.empty((0, 4), dtype=np.float32), np.empty((0,), dtype=np.int64)

    image_width, image_height = item["image"].size
    boxes = np.asarray(objects["bbox"], dtype=np.float32)
    labels = np.asarray(objects["category"], dtype=np.int64) + 1
    boxes[:, [0, 2]] *= img_size / image_width
    boxes[:, [1, 3]] *= img_size / image_height
    return boxes, labels
