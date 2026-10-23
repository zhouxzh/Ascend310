"""Shared COCO/Hugging Face bounding-box coordinate conversions."""

from __future__ import annotations

from collections.abc import Iterable

import numpy as np


def _validate_xyxy(box: Iterable[float]) -> tuple[float, float, float, float]:
    xmin, ymin, xmax, ymax = (float(value) for value in box)
    if xmax < xmin or ymax < ymin:
        raise ValueError(f"Invalid XYXY box: {(xmin, ymin, xmax, ymax)}")
    return xmin, ymin, xmax, ymax


def scale_xyxy_boxes(
    boxes: Iterable[Iterable[float]],
    original_width: int | float,
    original_height: int | float,
    output_width: int | float = 300,
    output_height: int | float = 300,
) -> np.ndarray:
    """Scale XYXY boxes from the source image to the model canvas."""
    if original_width <= 0 or original_height <= 0:
        raise ValueError("original_width and original_height must be positive")
    output = []
    scale_x = float(output_width) / float(original_width)
    scale_y = float(output_height) / float(original_height)
    for box in boxes:
        xmin, ymin, xmax, ymax = _validate_xyxy(box)
        output.append([xmin * scale_x, ymin * scale_y, xmax * scale_x, ymax * scale_y])
    return np.asarray(output, dtype=np.float32).reshape((-1, 4))


def xyxy_to_coco_xywh(
    box: Iterable[float],
    original_width: int | float,
    original_height: int | float,
    output_width: int | float = 300,
    output_height: int | float = 300,
) -> list[float]:
    """Convert one XYXY source box to COCO XYWH on the model canvas."""
    scaled = scale_xyxy_boxes(
        [box],
        original_width,
        original_height,
        output_width,
        output_height,
    )[0]
    xmin, ymin, xmax, ymax = scaled
    return [float(xmin), float(ymin), float(xmax - xmin), float(ymax - ymin)]
