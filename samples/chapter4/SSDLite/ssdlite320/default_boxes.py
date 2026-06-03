from __future__ import annotations

import itertools
from math import sqrt
from typing import Literal

import numpy as np

BoxOrder = Literal["ltrb", "xywh"]


class DefaultBoxes:
    def __init__(
        self,
        fig_size: int,
        feat_size: list[int],
        steps: list[float],
        scales: list[float],
        aspect_ratios: list[list[int]],
        scale_xy: float = 0.1,
        scale_wh: float = 0.2,
    ):
        self.feat_size = feat_size
        self.fig_size = fig_size
        self.scale_xy = scale_xy
        self.scale_wh = scale_wh
        self.steps = steps
        self.scales = scales
        self.aspect_ratios = aspect_ratios

        feature_scales = fig_size / np.asarray(steps, dtype=np.float32)
        default_boxes: list[tuple[float, float, float, float]] = []

        for level_index, feature_size in enumerate(self.feat_size):
            scale_small = scales[level_index] / fig_size
            scale_large = scales[level_index + 1] / fig_size
            scale_mid = sqrt(scale_small * scale_large)
            all_sizes = [(scale_small, scale_small), (scale_mid, scale_mid)]

            for alpha in aspect_ratios[level_index]:
                width, height = scale_small * sqrt(alpha), scale_small / sqrt(alpha)
                all_sizes.append((width, height))
                all_sizes.append((height, width))

            for width, height in all_sizes:
                for row_index, col_index in itertools.product(range(feature_size), repeat=2):
                    center_x = (col_index + 0.5) / feature_scales[level_index]
                    center_y = (row_index + 0.5) / feature_scales[level_index]
                    default_boxes.append((center_x, center_y, width, height))

        self.dboxes_xywh = np.clip(np.asarray(default_boxes, dtype=np.float32), 0.0, 1.0)
        self.dboxes_ltrb = self.dboxes_xywh.copy()
        self.dboxes_ltrb[:, 0] = self.dboxes_xywh[:, 0] - 0.5 * self.dboxes_xywh[:, 2]
        self.dboxes_ltrb[:, 1] = self.dboxes_xywh[:, 1] - 0.5 * self.dboxes_xywh[:, 3]
        self.dboxes_ltrb[:, 2] = self.dboxes_xywh[:, 0] + 0.5 * self.dboxes_xywh[:, 2]
        self.dboxes_ltrb[:, 3] = self.dboxes_xywh[:, 1] + 0.5 * self.dboxes_xywh[:, 3]

    def __call__(self, order: BoxOrder = "ltrb") -> np.ndarray:
        if order == "ltrb":
            return self.dboxes_ltrb.copy()
        if order == "xywh":
            return self.dboxes_xywh.copy()
        raise ValueError(f"Unsupported default-box order: {order}")


def dboxes320_coco(min_ratio: float = 0.1, max_ratio: float = 0.9) -> DefaultBoxes:
    fig_size = 320
    feat_size = [20, 10, 5, 3, 2, 1]
    steps = [fig_size / feature_size for feature_size in feat_size]

    num_layers = len(feat_size)
    scales_norm = [
        min_ratio + (max_ratio - min_ratio) * layer_index / (num_layers - 1)
        for layer_index in range(num_layers)
    ]
    scales_norm.append(1.0)
    scales = [scale * fig_size for scale in scales_norm]

    aspect_ratios = [[2, 3] for _ in range(num_layers)]
    return DefaultBoxes(fig_size, feat_size, steps, scales, aspect_ratios)
