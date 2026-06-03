from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True)
class Detections:
    boxes: np.ndarray
    labels: np.ndarray
    scores: np.ndarray


def pick_locs_confs(outputs):
    if len(outputs) != 2:
        raise RuntimeError(f"SSDLite320 expects 2 outputs, got {len(outputs)}")

    out0, out1 = outputs[0], outputs[1]

    def is_loc_tensor(tensor):
        tensor = np.asarray(tensor)
        return tensor.ndim >= 2 and 4 in tensor.shape

    if is_loc_tensor(out0) and not is_loc_tensor(out1):
        return out0, out1
    if is_loc_tensor(out1) and not is_loc_tensor(out0):
        return out1, out0

    if np.asarray(out0).size <= np.asarray(out1).size:
        return out0, out1
    return out1, out0


def softmax(values: np.ndarray, axis: int = -1) -> np.ndarray:
    values = values.astype(np.float32, copy=False)
    shifted = values - np.max(values, axis=axis, keepdims=True)
    exp_values = np.exp(shifted)
    return exp_values / np.sum(exp_values, axis=axis, keepdims=True)


def normalize_locs_scores(locs: np.ndarray, scores: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    locs = np.asarray(locs, dtype=np.float32)
    scores = np.asarray(scores, dtype=np.float32)

    if locs.ndim == 2:
        locs = locs[np.newaxis, ...]
    if scores.ndim == 2:
        scores = scores[np.newaxis, ...]

    if locs.ndim != 3 or scores.ndim != 3:
        raise ValueError(f"Expected 3D locs/scores tensors, got {locs.shape} and {scores.shape}")

    if locs.shape[1] == 4:
        locs = np.transpose(locs, (0, 2, 1))
    if scores.shape[1] != locs.shape[1] and scores.shape[2] == locs.shape[1]:
        scores = np.transpose(scores, (0, 2, 1))

    if locs.shape[-1] != 4:
        raise ValueError(f"Expected locs last dimension to be 4, got {locs.shape}")
    if scores.shape[1] != locs.shape[1]:
        raise ValueError(f"locs/scores box count mismatch: {locs.shape} vs {scores.shape}")

    return locs, scores


def scale_back_batch(locs: np.ndarray, scores: np.ndarray, dboxes: DefaultBoxes) -> tuple[np.ndarray, np.ndarray]:
    locs, scores = normalize_locs_scores(locs, scores)
    dboxes_xywh = dboxes(order="xywh").astype(np.float32, copy=False)

    if locs.shape[1] != dboxes_xywh.shape[0]:
        raise ValueError(f"Model predicted {locs.shape[1]} boxes, but default boxes have {dboxes_xywh.shape[0]}")

    decoded = locs.copy()
    decoded[:, :, :2] = dboxes.scale_xy * decoded[:, :, :2]
    decoded[:, :, 2:] = dboxes.scale_wh * decoded[:, :, 2:]
    decoded[:, :, :2] = decoded[:, :, :2] * dboxes_xywh[:, 2:] + dboxes_xywh[:, :2]
    decoded[:, :, 2:] = np.exp(decoded[:, :, 2:]) * dboxes_xywh[:, 2:]

    left = decoded[:, :, 0] - 0.5 * decoded[:, :, 2]
    top = decoded[:, :, 1] - 0.5 * decoded[:, :, 3]
    right = decoded[:, :, 0] + 0.5 * decoded[:, :, 2]
    bottom = decoded[:, :, 1] + 0.5 * decoded[:, :, 3]
    decoded[:, :, 0] = left
    decoded[:, :, 1] = top
    decoded[:, :, 2] = right
    decoded[:, :, 3] = bottom

    return decoded, softmax(scores, axis=-1)


def nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> np.ndarray:
    if boxes.size == 0:
        return np.empty((0,), dtype=np.int64)

    x1 = boxes[:, 0]
    y1 = boxes[:, 1]
    x2 = boxes[:, 2]
    y2 = boxes[:, 3]
    areas = np.maximum(x2 - x1, 0.0) * np.maximum(y2 - y1, 0.0)
    order = scores.argsort()[::-1]
    keep: list[int] = []

    while order.size > 0:
        current = int(order[0])
        keep.append(current)
        if order.size == 1:
            break

        rest = order[1:]
        xx1 = np.maximum(x1[current], x1[rest])
        yy1 = np.maximum(y1[current], y1[rest])
        xx2 = np.minimum(x2[current], x2[rest])
        yy2 = np.minimum(y2[current], y2[rest])
        width = np.maximum(xx2 - xx1, 0.0)
        height = np.maximum(yy2 - yy1, 0.0)
        inter = width * height
        union = areas[current] + areas[rest] - inter
        iou = np.divide(inter, union, out=np.zeros_like(inter), where=union > 0)
        order = rest[iou <= iou_threshold]

    return np.asarray(keep, dtype=np.int64)


def decode_single(
    boxes: np.ndarray,
    probs: np.ndarray,
    iou_threshold: float,
    max_output: int,
    score_threshold: float = 0.05,
    max_candidates_per_class: int = 100,
) -> Detections:
    out_boxes: list[np.ndarray] = []
    out_scores: list[np.ndarray] = []
    out_labels: list[np.ndarray] = []

    for class_index in range(1, probs.shape[1]):
        class_scores = probs[:, class_index]
        keep_mask = class_scores > score_threshold
        candidate_boxes = boxes[keep_mask]
        candidate_scores = class_scores[keep_mask]
        if candidate_scores.size == 0:
            continue

        if candidate_scores.size > max_candidates_per_class:
            top_indices = candidate_scores.argsort()[-max_candidates_per_class:]
            candidate_boxes = candidate_boxes[top_indices]
            candidate_scores = candidate_scores[top_indices]

        selected = nms(candidate_boxes, candidate_scores, iou_threshold)
        if selected.size == 0:
            continue

        out_boxes.append(candidate_boxes[selected])
        out_scores.append(candidate_scores[selected])
        out_labels.append(np.full((selected.size,), class_index, dtype=np.int64))

    if not out_boxes:
        return Detections(
            np.empty((0, 4), dtype=np.float32),
            np.empty((0,), dtype=np.int64),
            np.empty((0,), dtype=np.float32),
        )

    merged_boxes = np.concatenate(out_boxes, axis=0).astype(np.float32, copy=False)
    merged_labels = np.concatenate(out_labels, axis=0)
    merged_scores = np.concatenate(out_scores, axis=0).astype(np.float32, copy=False)
    order = merged_scores.argsort()[-max_output:]
    return Detections(merged_boxes[order], merged_labels[order], merged_scores[order])


def decode_batch(
    locs: np.ndarray,
    scores: np.ndarray,
    dboxes: DefaultBoxes,
    iou_threshold: float = 0.5,
    max_output: int = 200,
    score_threshold: float = 0.05,
) -> list[Detections]:
    boxes, probs = scale_back_batch(locs, scores, dboxes)
    return [
        decode_single(box, prob, iou_threshold, max_output, score_threshold)
        for box, prob in zip(boxes, probs)
    ]
