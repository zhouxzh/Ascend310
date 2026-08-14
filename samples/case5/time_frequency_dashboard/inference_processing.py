"""Deterministic CPU preprocessing and postprocessing for SDR NPU models."""

from __future__ import annotations

from typing import Any, Sequence

import numpy as np


def _finite_real_array(values: np.ndarray, *, field: str) -> np.ndarray:
    """Return a finite real float32 tensor, rejecting lossy coercions."""
    source = np.asarray(values)
    if (
        not np.issubdtype(source.dtype, np.number)
        or np.issubdtype(source.dtype, np.complexfloating)
        or source.dtype == np.bool_
    ):
        raise ValueError(f"{field} must contain real numeric values")
    try:
        array = np.ascontiguousarray(source, dtype=np.float32)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must contain real numeric values") from exc
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{field} must be finite")
    return array


def normalize_iq_batch(values: np.ndarray, method: str) -> np.ndarray:
    batch = _finite_real_array(values, field="IQ input")
    if batch.ndim != 3 or batch.shape[1] != 2:
        raise ValueError("IQ input must have shape [batch, 2, samples]")
    if method == "none":
        return batch
    if method == "per_channel_zscore":
        mean = batch.mean(axis=2, keepdims=True, dtype=np.float32)
        standard_deviation = batch.std(axis=2, keepdims=True, dtype=np.float32)
        return np.ascontiguousarray(
            (batch - mean) / np.maximum(standard_deviation, np.float32(1.0e-8)),
            dtype=np.float32,
        )
    if method == "infinity_norm":
        scale = np.max(np.abs(batch), axis=(1, 2), keepdims=True)
        return np.ascontiguousarray(
            batch / np.maximum(scale, np.float32(1.0e-8)), dtype=np.float32
        )
    raise ValueError(f"unsupported IQ normalization: {method}")


def complex_to_model_iq(
    samples: np.ndarray, *, batch_size: int, window_samples: int, normalization: str
) -> np.ndarray:
    values = np.asarray(samples, dtype=np.complex64)
    if values.ndim != 1:
        raise ValueError("complex samples must be a 1-D array")
    if not np.all(np.isfinite(values)):
        raise ValueError("complex samples must be finite")
    required = batch_size * window_samples
    if values.size != required:
        raise ValueError(f"expected {required} complex samples, got {values.size}")
    windows = values.reshape(batch_size, window_samples)
    # Match the documented SDR capture contract before splitting I and Q.
    windows = windows - windows.mean(axis=1, keepdims=True, dtype=np.complex64)
    iq = np.stack((windows.real, windows.imag), axis=1).astype(np.float32)
    return normalize_iq_batch(iq, normalization)


def softmax_topk(
    logits: np.ndarray, class_names: Sequence[str], top_k: int
) -> list[list[dict[str, Any]]]:
    if not class_names:
        raise ValueError("classification class_names must be non-empty")
    values = _finite_real_array(logits, field="classification logits")
    if values.ndim != 2 or values.shape[1] != len(class_names):
        raise ValueError(
            f"classification logits must have shape [batch, {len(class_names)}], got {values.shape}"
        )
    if isinstance(top_k, bool):
        raise ValueError("top_k must be a positive integer")
    try:
        requested_count = int(top_k)
    except (TypeError, ValueError) as exc:
        raise ValueError("top_k must be a positive integer") from exc
    if requested_count <= 0:
        raise ValueError("top_k must be a positive integer")
    count = min(requested_count, len(class_names))
    shifted = values - values.max(axis=1, keepdims=True)
    exponentials = np.exp(shifted)
    probabilities = exponentials / exponentials.sum(axis=1, keepdims=True)
    results: list[list[dict[str, Any]]] = []
    for row in probabilities:
        # Stable sorting makes confidence ties reproducible and keeps the
        # lower, canonical class index first.
        indices = np.argsort(-row, kind="stable")[:count]
        results.append(
            [
                {
                    "class_index": int(index),
                    "label": str(class_names[int(index)]),
                    "confidence": float(row[int(index)]),
                }
                for index in indices
            ]
        )
    return results


def _box_iou(box: np.ndarray, boxes: np.ndarray) -> np.ndarray:
    top_left = np.maximum(box[:2], boxes[:, :2])
    bottom_right = np.minimum(box[2:], boxes[:, 2:])
    intersection = np.maximum(bottom_right - top_left, 0.0)
    intersection_area = intersection[:, 0] * intersection[:, 1]
    box_area = max(float((box[2] - box[0]) * (box[3] - box[1])), 0.0)
    boxes_area = np.maximum(boxes[:, 2] - boxes[:, 0], 0.0) * np.maximum(
        boxes[:, 3] - boxes[:, 1], 0.0
    )
    return intersection_area / np.maximum(box_area + boxes_area - intersection_area, 1.0e-12)


def decode_yolo_detections(
    output: np.ndarray,
    class_names: Sequence[str],
    *,
    confidence_threshold: float,
    iou_threshold: float,
    max_detections: int,
) -> list[dict[str, Any]]:
    """Decode a YOLO11 export without embedded NMS and run class-aware CPU NMS."""
    if isinstance(max_detections, bool) or not isinstance(max_detections, int) or max_detections <= 0:
        raise ValueError("max_detections must be a positive integer")
    for value, label in (
        (confidence_threshold, "confidence threshold"),
        (iou_threshold, "IoU threshold"),
    ):
        if isinstance(value, bool):
            raise ValueError(f"{label} must be a finite value between zero and one")
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} must be a finite value between zero and one") from exc
        if not np.isfinite(numeric) or not 0.0 <= numeric <= 1.0:
            raise ValueError(f"{label} must be a finite value between zero and one")
    values = _finite_real_array(output, field="YOLO output")
    if values.ndim == 3 and values.shape[0] == 1:
        values = values[0]
    if values.ndim != 2:
        raise ValueError(f"YOLO output must be rank 2 or [1, ...], got {values.shape}")
    minimum_columns = 4 + max(len(class_names), 1)
    # The reviewed YOLO11 export is either [candidates, 4 + classes] or
    # [4 + classes, candidates].  Do not guess from a larger trailing axis:
    # doing so can silently reinterpret box coordinates as class scores.
    if values.shape[1] == minimum_columns:
        pass
    elif values.shape[0] == minimum_columns:
        values = values.T
    else:
        raise ValueError(
            "YOLO output must have exactly 4 + class_count columns in either orientation; "
            f"got {values.shape} for {len(class_names)} classes"
        )
    boxes_xywh = values[:, :4]
    class_scores = values[:, 4 : 4 + max(len(class_names), 1)]
    class_indices = np.argmax(class_scores, axis=1)
    scores = class_scores[np.arange(class_scores.shape[0]), class_indices]
    selected = np.flatnonzero(scores >= confidence_threshold)
    if selected.size == 0:
        return []
    boxes_xywh = boxes_xywh[selected]
    scores = scores[selected]
    class_indices = class_indices[selected]
    valid_boxes = (boxes_xywh[:, 2] > 0.0) & (boxes_xywh[:, 3] > 0.0)
    boxes_xywh = boxes_xywh[valid_boxes]
    scores = scores[valid_boxes]
    class_indices = class_indices[valid_boxes]
    if not boxes_xywh.size:
        return []
    boxes = np.empty_like(boxes_xywh)
    boxes[:, 0] = boxes_xywh[:, 0] - boxes_xywh[:, 2] / 2.0
    boxes[:, 1] = boxes_xywh[:, 1] - boxes_xywh[:, 3] / 2.0
    boxes[:, 2] = boxes_xywh[:, 0] + boxes_xywh[:, 2] / 2.0
    boxes[:, 3] = boxes_xywh[:, 1] + boxes_xywh[:, 3] / 2.0

    keep: list[int] = []
    for class_index in np.unique(class_indices):
        remaining = np.flatnonzero(class_indices == class_index)
        remaining = remaining[np.argsort(scores[remaining])[::-1]]
        while remaining.size and len(keep) < max_detections:
            current = int(remaining[0])
            keep.append(current)
            if remaining.size == 1:
                break
            overlaps = _box_iou(boxes[current], boxes[remaining[1:]])
            remaining = remaining[1:][overlaps <= iou_threshold]
    keep.sort(key=lambda index: float(scores[index]), reverse=True)
    detections: list[dict[str, Any]] = []
    for index in keep[:max_detections]:
        class_index = int(class_indices[index])
        label = class_names[class_index] if class_names else "signal"
        detections.append(
            {
                "class_index": class_index,
                "label": str(label),
                "confidence": float(scores[index]),
                "box_xyxy": [float(value) for value in boxes[index]],
            }
        )
    return detections


def generate_qpsk_samples(total_samples: int, *, seed: int = 310_005) -> np.ndarray:
    if total_samples <= 0:
        raise ValueError("total_samples must be positive")
    rng = np.random.default_rng(seed)
    symbols = rng.integers(0, 4, size=total_samples)
    phases = symbols.astype(np.float32) * np.float32(np.pi / 2.0) + np.float32(np.pi / 4.0)
    return np.ascontiguousarray(np.exp(1j * phases), dtype=np.complex64)
