from __future__ import annotations

from pathlib import Path
from typing import Sequence

import numpy as np

from .preprocess import IMAGENET_MEAN, IMAGENET_STD
from .postprocess import Detections


def visualize_sample(
    image_chw: np.ndarray,
    gt_boxes: np.ndarray,
    gt_labels: np.ndarray,
    detections: Detections,
    category_names: Sequence[str] | None,
    save_path: str,
    score_threshold: float = 0.4,
) -> None:
    import matplotlib.patches as patches
    import matplotlib.pyplot as plt

    image = image_chw.copy()
    image = image * IMAGENET_STD + IMAGENET_MEAN
    image = np.clip(image.transpose(1, 2, 0), 0.0, 1.0)
    image_height, image_width = image.shape[:2]

    fig, ax = plt.subplots(1, figsize=(10, 10))
    ax.imshow(image)

    for box, label in zip(gt_boxes, gt_labels):
        xmin, ymin, xmax, ymax = box
        width, height = xmax - xmin, ymax - ymin
        ax.add_patch(patches.Rectangle((xmin, ymin), width, height, linewidth=2, edgecolor="lime", facecolor="none"))
        label_value = int(label)
        label_name = category_names[label_value] if category_names and label_value < len(category_names) else str(label_value)
        ax.text(xmin, ymin, f"GT: {label_name}", color="lime", fontsize=9, backgroundcolor="black", alpha=0.6)

    if detections.boxes.size > 0:
        pred_boxes = detections.boxes.copy()
        pred_boxes[:, [0, 2]] *= image_width
        pred_boxes[:, [1, 3]] *= image_height
        for box, label, score in zip(pred_boxes, detections.labels, detections.scores):
            if score < score_threshold:
                continue
            xmin, ymin, xmax, ymax = box
            width, height = xmax - xmin, ymax - ymin
            ax.add_patch(patches.Rectangle((xmin, ymin), width, height, linewidth=2, edgecolor="red", facecolor="none"))
            label_value = int(label)
            label_name = category_names[label_value] if category_names and label_value < len(category_names) else str(label_value)
            ax.text(xmin, ymax, f"Pred: {label_name} {score:.2f}", color="white", fontsize=9, backgroundcolor="red", alpha=0.7)

    Path(save_path).parent.mkdir(parents=True, exist_ok=True)
    plt.axis("off")
    plt.savefig(save_path, bbox_inches="tight", pad_inches=0)
    plt.close(fig)
