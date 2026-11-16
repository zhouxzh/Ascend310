#!/usr/bin/env python3
"""Shared, offline-only helpers for the seed43 two-class NanoDet test protocol.

This module deliberately does not import the production palmprint service.  The
seed43 detector has a different contract from the service detector:
``float32[1, 2125, 34]`` is two already-sigmoided class probabilities followed
by four independent eight-bin DFL logits.  It is used by the ACE2 CUDA ONNX
runner and the board ACL runner so they share preprocessing, decoding and
metric rules.
"""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import redirect_stdout
import hashlib
import io
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np

INPUT_SHAPE = (1, 3, 320, 320)
OUTPUT_SHAPE = (1, 2125, 34)
CLASS_NAMES = ("left_palm", "right_palm")
CATEGORY_CONTRACT = {1: "left_palm", 2: "right_palm"}
MEAN = np.asarray([103.53, 116.28, 123.675], dtype=np.float32)
STD = np.asarray([57.375, 57.12, 58.395], dtype=np.float32)
STRIDES = (8, 16, 32, 64)
REG_MAX = 7
COCO_SCORE_FLOOR = 0.05
TASK_SCORE_THRESHOLD = 0.35
NMS_IOU_THRESHOLD = 0.60
TASK_IOU_THRESHOLD = 0.50
MAX_DETECTIONS = 100


@dataclass(frozen=True)
class ImageRecord:
    image_id: int
    file_name: str
    width: int
    height: int
    image_sha256: str
    subject_id: str
    session_id: str


@dataclass(frozen=True)
class Detection:
    label: int
    score: float
    xyxy: tuple[float, float, float, float]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_array(value: np.ndarray) -> str:
    contiguous = np.ascontiguousarray(value)
    return hashlib.sha256(contiguous.tobytes()).hexdigest()


def json_hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def validate_category_contract(categories: Iterable[dict[str, Any]]) -> None:
    observed: dict[int, str] = {}
    for item in categories:
        _require(isinstance(item, dict), "COCO category must be an object")
        category_id = item.get("id")
        name = item.get("name")
        _require(isinstance(category_id, int) and isinstance(name, str), "COCO category id/name is invalid")
        _require(category_id not in observed, f"duplicate category id {category_id}")
        observed[category_id] = name
    _require(observed == CATEGORY_CONTRACT, f"expected fixed categories {CATEGORY_CONTRACT}, got {observed}")


def _safe_relative_file_name(value: Any) -> str:
    _require(isinstance(value, str) and value, "image file_name is missing")
    candidate = Path(value)
    _require(not candidate.is_absolute() and ".." not in candidate.parts, f"unsafe image path {value!r}")
    return candidate.as_posix()


def load_test_split(
    annotations_path: Path,
    images_root: Path,
    *,
    verify_image_hashes: bool = True,
    require_subject_and_session: bool = True,
) -> tuple[dict[str, Any], list[ImageRecord]]:
    """Validate the fixed two-class COCO test split and return deterministic records."""
    annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
    _require(isinstance(annotations, dict), "COCO annotations root must be an object")
    validate_category_contract(annotations.get("categories", []))
    image_items = annotations.get("images")
    annotation_items = annotations.get("annotations")
    _require(isinstance(image_items, list) and isinstance(annotation_items, list), "COCO images/annotations must be lists")
    seen_ids: set[int] = set()
    records: list[ImageRecord] = []
    for item in image_items:
        _require(isinstance(item, dict), "COCO image must be an object")
        image_id = item.get("id")
        width = item.get("width")
        height = item.get("height")
        _require(isinstance(image_id, int) and image_id not in seen_ids, f"invalid or duplicate image id {image_id!r}")
        _require(isinstance(width, int) and width > 0 and isinstance(height, int) and height > 0, f"invalid dimensions for image {image_id}")
        file_name = _safe_relative_file_name(item.get("file_name"))
        image_sha256 = item.get("sha256")
        _require(isinstance(image_sha256, str) and len(image_sha256) == 64, f"missing SHA-256 for image {image_id}")
        subject_id = item.get("subject_id")
        session_id = item.get("session_id")
        if require_subject_and_session:
            _require(isinstance(subject_id, str) and subject_id, f"missing subject_id for image {image_id}")
            _require(isinstance(session_id, str) and session_id, f"missing session_id for image {image_id}")
        path = images_root / file_name
        _require(path.is_file(), f"missing test image {path}")
        if verify_image_hashes:
            _require(sha256_file(path) == image_sha256, f"test image SHA-256 mismatch: {path}")
        seen_ids.add(image_id)
        records.append(ImageRecord(image_id, file_name, width, height, image_sha256, str(subject_id or ""), str(session_id or "")))
    _require(records, "test split has no images")
    records.sort(key=lambda item: item.image_id)
    seen_annotation_ids: set[int] = set()
    for item in annotation_items:
        _require(isinstance(item, dict), "COCO annotation must be an object")
        image_id = item.get("image_id")
        category_id = item.get("category_id")
        bbox = item.get("bbox")
        annotation_id = item.get("id")
        _require(isinstance(annotation_id, int) and annotation_id not in seen_annotation_ids, "invalid or duplicate annotation id")
        _require(image_id in seen_ids, f"annotation references unknown image {image_id!r}")
        _require(category_id in CATEGORY_CONTRACT, f"annotation has invalid category {category_id!r}")
        _require(isinstance(bbox, list) and len(bbox) == 4 and all(isinstance(value, (int, float)) and math.isfinite(value) for value in bbox), "invalid COCO bbox")
        _require(float(bbox[2]) > 0 and float(bbox[3]) > 0, "COCO bbox must have positive area")
        seen_annotation_ids.add(annotation_id)
    return annotations, records


def preprocess_bgr(image_bgr: np.ndarray) -> np.ndarray:
    """Apply the exported NanoDet direct-stretch BGR preprocessing contract."""
    if not isinstance(image_bgr, np.ndarray) or image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
        raise ValueError("expected HWC BGR image with exactly three channels")
    if image_bgr.dtype != np.uint8:
        raise ValueError(f"expected uint8 image, got {image_bgr.dtype}")
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - environment-specific dependency
        raise RuntimeError("OpenCV is required for NanoDet preprocessing") from exc
    resized = cv2.resize(image_bgr, (INPUT_SHAPE[3], INPUT_SHAPE[2]), interpolation=cv2.INTER_LINEAR)
    normalized = (resized.astype(np.float32) - MEAN) / STD
    return np.ascontiguousarray(normalized.transpose(2, 0, 1)[None, ...], dtype=np.float32)


def _priors() -> np.ndarray:
    grids = []
    for stride in STRIDES:
        yy, xx = np.meshgrid(np.arange(INPUT_SHAPE[2] // stride), np.arange(INPUT_SHAPE[3] // stride), indexing="ij")
        grids.append(np.stack((xx.ravel() * stride, yy.ravel() * stride, np.full(xx.size, stride)), axis=1))
    priors = np.concatenate(grids).astype(np.float32)
    _require(tuple(priors.shape) == (OUTPUT_SHAPE[1], 3), f"unexpected prior shape {priors.shape}")
    return priors


PRIORS = _priors()


def _softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - values.max(axis=-1, keepdims=True)
    exponent = np.exp(shifted)
    return exponent / exponent.sum(axis=-1, keepdims=True)


def _iou_one_to_many(box: np.ndarray, boxes: np.ndarray) -> np.ndarray:
    left_top = np.maximum(box[:2], boxes[:, :2])
    right_bottom = np.minimum(box[2:], boxes[:, 2:])
    intersection = np.maximum(right_bottom - left_top, 0).prod(axis=1)
    box_area = np.maximum(box[2] - box[0], 0) * np.maximum(box[3] - box[1], 0)
    areas = np.maximum(boxes[:, 2] - boxes[:, 0], 0) * np.maximum(boxes[:, 3] - boxes[:, 1], 0)
    return intersection / np.maximum(box_area + areas - intersection, 1e-8)


def class_nms(boxes: np.ndarray, scores: np.ndarray, iou_threshold: float) -> np.ndarray:
    """Stable, class-local NMS with non-finite and non-positive boxes removed."""
    _require(0.0 <= iou_threshold <= 1.0, "NMS IoU threshold must be within [0, 1]")
    boxes = np.asarray(boxes, dtype=np.float32)
    scores = np.asarray(scores, dtype=np.float32)
    _require(boxes.ndim == 2 and boxes.shape[1:] == (4,) and scores.shape == (boxes.shape[0],), "NMS requires Nx4 boxes and N scores")
    sizes = boxes[:, 2:] - boxes[:, :2]
    valid = np.isfinite(boxes).all(axis=1) & np.isfinite(scores) & (sizes > 0).all(axis=1)
    candidates = np.flatnonzero(valid)
    order = candidates[np.argsort(-scores[candidates], kind="stable")]
    kept: list[int] = []
    while order.size:
        index = int(order[0])
        kept.append(index)
        if order.size == 1:
            break
        rest = order[1:]
        order = rest[_iou_one_to_many(boxes[index], boxes[rest]) <= iou_threshold]
    return np.asarray(kept, dtype=np.int64)


def decode_output(
    output: np.ndarray,
    *,
    image_width: int,
    image_height: int,
    score_threshold: float = COCO_SCORE_FLOOR,
    nms_iou_threshold: float = NMS_IOU_THRESHOLD,
    max_detections: int = MAX_DETECTIONS,
) -> list[Detection]:
    """Decode static seed43 ONNX/OM output without applying sigmoid twice."""
    raw = np.asarray(output, dtype=np.float32)
    _require(raw.shape == OUTPUT_SHAPE, f"expected output {OUTPUT_SHAPE}, got {raw.shape}")
    _require(np.isfinite(raw).all(), "output contains NaN or Inf")
    _require(0.0 <= score_threshold <= 1.0, "score threshold must be within [0, 1]")
    _require(max_detections > 0, "max_detections must be positive")
    scores = raw[0, :, :2]
    dfl_logits = raw[0, :, 2:].reshape(OUTPUT_SHAPE[1], 4, REG_MAX + 1)
    distances = (_softmax(dfl_logits) * np.arange(REG_MAX + 1, dtype=np.float32)).sum(axis=-1) * PRIORS[:, 2:3]
    boxes = np.concatenate((PRIORS[:, :2] - distances[:, :2], PRIORS[:, :2] + distances[:, 2:]), axis=-1)
    boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0, INPUT_SHAPE[3]) * (image_width / INPUT_SHAPE[3])
    boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0, INPUT_SHAPE[2]) * (image_height / INPUT_SHAPE[2])
    detections: list[Detection] = []
    for label in range(2):
        candidates = np.flatnonzero(scores[:, label] >= score_threshold)
        if not candidates.size:
            continue
        keep = candidates[class_nms(boxes[candidates], scores[candidates, label], nms_iou_threshold)]
        for index in keep:
            left, top, right, bottom = (float(value) for value in boxes[index])
            if right > left and bottom > top:
                detections.append(Detection(label=label, score=float(scores[index, label]), xyxy=(left, top, right, bottom)))
    detections.sort(key=lambda item: (-item.score, item.label, item.xyxy[0], item.xyxy[1]))
    return detections[:max_detections]


def detections_to_coco(image_id: int, detections: Iterable[Detection]) -> list[dict[str, Any]]:
    values = []
    for item in detections:
        left, top, right, bottom = item.xyxy
        width = right - left
        height = bottom - top
        if not (math.isfinite(item.score) and math.isfinite(left) and math.isfinite(top) and math.isfinite(width) and math.isfinite(height)):
            raise ValueError("non-finite decoded detection")
        if width <= 0 or height <= 0:
            continue
        values.append({"image_id": int(image_id), "category_id": int(item.label + 1), "bbox": [left, top, width, height], "score": float(item.score)})
    return values


def validate_coco_predictions(predictions: Any, image_ids: set[int]) -> list[dict[str, Any]]:
    _require(isinstance(predictions, list), "COCO predictions must be a list")
    normalized: list[dict[str, Any]] = []
    for index, value in enumerate(predictions):
        _require(isinstance(value, dict), f"prediction {index} must be an object")
        image_id = value.get("image_id")
        category_id = value.get("category_id")
        bbox = value.get("bbox")
        score = value.get("score")
        _require(isinstance(image_id, int) and image_id in image_ids, f"prediction {index} references unknown image_id")
        _require(category_id in CATEGORY_CONTRACT, f"prediction {index} has invalid category")
        _require(isinstance(bbox, list) and len(bbox) == 4 and all(isinstance(item, (int, float)) and math.isfinite(item) for item in bbox), f"prediction {index} bbox is invalid")
        _require(float(bbox[2]) > 0 and float(bbox[3]) > 0, f"prediction {index} bbox has non-positive area")
        _require(isinstance(score, (int, float)) and math.isfinite(score) and 0.0 <= float(score) <= 1.0, f"prediction {index} score is invalid")
        normalized.append({"image_id": image_id, "category_id": int(category_id), "bbox": [float(item) for item in bbox], "score": float(score)})
    return normalized


def _metric_value(value: float) -> float | None:
    return None if not math.isfinite(float(value)) or float(value) < 0 else float(value)


def coco_metrics(annotations_path: Path, predictions: list[dict[str, Any]]) -> dict[str, Any]:
    """Run COCO bbox metrics with the fixed two-class contract."""
    try:
        from pycocotools.coco import COCO
        from pycocotools.cocoeval import COCOeval
    except ImportError as exc:  # pragma: no cover - environment-specific dependency
        raise RuntimeError("pycocotools is required for task metrics") from exc
    annotations = json.loads(annotations_path.read_text(encoding="utf-8"))
    validate_category_contract(annotations.get("categories", []))
    image_ids = {int(item["id"]) for item in annotations["images"]}
    predictions = validate_coco_predictions(predictions, image_ids)
    with redirect_stdout(io.StringIO()):
        ground_truth = COCO(str(annotations_path))
        detections = ground_truth.loadRes(predictions)
        evaluator = COCOeval(ground_truth, detections, "bbox")
        evaluator.evaluate()
        evaluator.accumulate()
        evaluator.summarize()
    metric_names = ("mAP50_95", "AP50", "AP75", "AP_small", "AP_medium", "AP_large")
    metrics = {name: _metric_value(value) for name, value in zip(metric_names, evaluator.stats[:6])}
    precision = evaluator.eval["precision"]
    per_class = {}
    for class_index, category_id in enumerate(sorted(CATEGORY_CONTRACT)):
        ap50 = precision[0, :, class_index, 0, -1]
        all_ap = precision[:, :, class_index, 0, -1]
        valid_ap50 = ap50[ap50 > -1]
        valid_all = all_ap[all_ap > -1]
        per_class[CATEGORY_CONTRACT[category_id]] = {
            "AP50": float(np.mean(valid_ap50)) if valid_ap50.size else None,
            "mAP50_95": float(np.mean(valid_all)) if valid_all.size else None,
        }
    return {"metrics": metrics, "per_class": per_class, "prediction_count": len(predictions)}


def _xywh_to_xyxy(value: list[float]) -> np.ndarray:
    x, y, width, height = (float(item) for item in value)
    return np.asarray([x, y, x + width, y + height], dtype=np.float32)


def _task_row() -> dict[str, int]:
    return {"ground_truth": 0, "predictions": 0, "matched": 0, "correct_handedness": 0, "wrong_handedness": 0, "missed": 0, "duplicate": 0, "false_positive": 0}


def _finish_task_row(row: dict[str, int]) -> dict[str, Any]:
    _require(row["ground_truth"] == row["matched"] + row["missed"], "task metric invariant GT=matched+missed failed")
    _require(row["matched"] == row["correct_handedness"] + row["wrong_handedness"], "task metric invariant matched=correct+wrong failed")
    _require(row["predictions"] == row["matched"] + row["duplicate"] + row["false_positive"], "task metric invariant predictions=matched+duplicate+false positive failed")
    result: dict[str, Any] = dict(row)
    result["correct_handedness_rate"] = row["correct_handedness"] / row["matched"] if row["matched"] else None
    result["miss_rate"] = row["missed"] / row["ground_truth"] if row["ground_truth"] else None
    result["duplicate_rate"] = row["duplicate"] / row["ground_truth"] if row["ground_truth"] else None
    result["false_positive_rate"] = row["false_positive"] / row["predictions"] if row["predictions"] else None
    return result


def task_metrics(annotations: dict[str, Any], predictions: list[dict[str, Any]], *, score_threshold: float = TASK_SCORE_THRESHOLD, iou_threshold: float = TASK_IOU_THRESHOLD) -> dict[str, Any]:
    """Measure handedness, misses, duplicates and false positives after spatial matching."""
    validate_category_contract(annotations.get("categories", []))
    images = {int(item["id"]): item for item in annotations["images"]}
    normalized = validate_coco_predictions(predictions, set(images))
    grouped_predictions: dict[int, list[dict[str, Any]]] = {image_id: [] for image_id in images}
    for item in normalized:
        if item["score"] >= score_threshold:
            grouped_predictions[item["image_id"]].append(item)
    grouped_annotations: dict[int, list[dict[str, Any]]] = {image_id: [] for image_id in images}
    for item in annotations["annotations"]:
        grouped_annotations[int(item["image_id"])].append(item)
    total = _task_row()
    for image_id, image in images.items():
        gt = grouped_annotations[image_id]
        candidates = sorted(grouped_predictions[image_id], key=lambda item: (-item["score"], item["category_id"], item["bbox"]))
        total["ground_truth"] += len(gt)
        total["predictions"] += len(candidates)
        unmatched = set(range(len(gt)))
        matched = set()
        for prediction in candidates:
            pred_box = _xywh_to_xyxy(prediction["bbox"])
            available = [(index, float(_iou_one_to_many(pred_box, _xywh_to_xyxy(gt[index]["bbox"])[None, :])[0])) for index in unmatched]
            qualifying = [(index, iou) for index, iou in available if iou >= iou_threshold]
            if qualifying:
                index, _ = max(qualifying, key=lambda item: item[1])
                unmatched.remove(index)
                matched.add(index)
                total["matched"] += 1
                if int(prediction["category_id"]) == int(gt[index]["category_id"]):
                    total["correct_handedness"] += 1
                else:
                    total["wrong_handedness"] += 1
                continue
            all_overlaps = [_iou_one_to_many(pred_box, _xywh_to_xyxy(item["bbox"])[None, :])[0] for item in gt]
            if any(float(iou) >= iou_threshold for iou in all_overlaps):
                total["duplicate"] += 1
            else:
                total["false_positive"] += 1
        total["missed"] += len(unmatched)
    return {"score_threshold": score_threshold, "iou_threshold": iou_threshold, "groups": {"all": _finish_task_row(total)}}


class RawAgreement:
    """Aggregate full tensor agreement without persisting biometric tensors."""

    def __init__(self, *, atol: float, rtol: float) -> None:
        self.atol = float(atol)
        self.rtol = float(rtol)
        self.images = 0
        self.allclose_images = 0
        self.elements = 0
        self.absolute_sum = 0.0
        self.absolute_max = 0.0
        self.class_elements = 0
        self.class_absolute_sum = 0.0
        self.class_absolute_max = 0.0
        self.dfl_elements = 0
        self.dfl_absolute_sum = 0.0
        self.dfl_absolute_max = 0.0
        self.non_finite_images = 0

    def compare(self, expected: np.ndarray, actual: np.ndarray) -> dict[str, Any]:
        expected = np.asarray(expected, dtype=np.float32)
        actual = np.asarray(actual, dtype=np.float32)
        _require(expected.shape == OUTPUT_SHAPE and actual.shape == OUTPUT_SHAPE, "raw agreement output shape mismatch")
        finite = bool(np.isfinite(actual).all())
        if not finite:
            self.non_finite_images += 1
            raise ValueError("backend output contains NaN or Inf")
        error = np.abs(actual - expected)
        class_error = error[:, :, :2]
        dfl_error = error[:, :, 2:]
        self.images += 1
        self.allclose_images += int(np.allclose(actual, expected, atol=self.atol, rtol=self.rtol, equal_nan=False))
        self.elements += int(error.size)
        self.absolute_sum += float(error.sum(dtype=np.float64))
        self.absolute_max = max(self.absolute_max, float(error.max()))
        self.class_elements += int(class_error.size)
        self.class_absolute_sum += float(class_error.sum(dtype=np.float64))
        self.class_absolute_max = max(self.class_absolute_max, float(class_error.max()))
        self.dfl_elements += int(dfl_error.size)
        self.dfl_absolute_sum += float(dfl_error.sum(dtype=np.float64))
        self.dfl_absolute_max = max(self.dfl_absolute_max, float(dfl_error.max()))
        return {
            "raw_max_abs_error": float(error.max()),
            "raw_mean_abs_error": float(error.mean(dtype=np.float64)),
            "class_max_abs_error": float(class_error.max()),
            "class_mean_abs_error": float(class_error.mean(dtype=np.float64)),
            "dfl_max_abs_error": float(dfl_error.max()),
            "dfl_mean_abs_error": float(dfl_error.mean(dtype=np.float64)),
            "allclose": bool(np.allclose(actual, expected, atol=self.atol, rtol=self.rtol, equal_nan=False)),
        }

    def summary(self) -> dict[str, Any]:
        return {
            "images": self.images,
            "non_finite_images": self.non_finite_images,
            "atol": self.atol,
            "rtol": self.rtol,
            "allclose_images": self.allclose_images,
            "allclose_rate": self.allclose_images / self.images if self.images else None,
            "raw_max_abs_error": self.absolute_max if self.images else None,
            "raw_mean_abs_error": self.absolute_sum / self.elements if self.elements else None,
            "class_max_abs_error": self.class_absolute_max if self.images else None,
            "class_mean_abs_error": self.class_absolute_sum / self.class_elements if self.class_elements else None,
            "dfl_max_abs_error": self.dfl_absolute_max if self.images else None,
            "dfl_mean_abs_error": self.dfl_absolute_sum / self.dfl_elements if self.dfl_elements else None,
        }


class ReferencePackWriter:
    """Store exact float32 inputs and ONNX outputs in bounded test-set shards."""

    def __init__(self, destination: Path, *, source: dict[str, Any], shard_size: int = 50) -> None:
        _require(shard_size > 0, "reference shard size must be positive")
        self.destination = destination
        self.source = source
        self.shard_size = shard_size
        self.destination.mkdir(parents=True, exist_ok=True)
        if (self.destination / "manifest.json").exists():
            raise FileExistsError(f"reference manifest already exists: {self.destination / 'manifest.json'}")
        self._records: list[dict[str, Any]] = []
        self._inputs: list[np.ndarray] = []
        self._outputs: list[np.ndarray] = []
        self._shards: list[dict[str, Any]] = []
        self._count = 0

    def append(self, record: ImageRecord, tensor: np.ndarray, output: np.ndarray) -> None:
        tensor = np.asarray(tensor, dtype=np.float32)
        output = np.asarray(output, dtype=np.float32)
        _require(tuple(tensor.shape) == INPUT_SHAPE, f"reference input shape mismatch: {tensor.shape}")
        _require(tuple(output.shape) == OUTPUT_SHAPE and np.isfinite(output).all(), "reference output contract mismatch")
        self._records.append({
            "image_id": record.image_id,
            "file_name": record.file_name,
            "width": record.width,
            "height": record.height,
            "image_sha256": record.image_sha256,
            "input_sha256": sha256_array(tensor),
        })
        self._inputs.append(tensor[0])
        self._outputs.append(output[0])
        if len(self._records) >= self.shard_size:
            self.flush()

    def flush(self) -> None:
        if not self._records:
            return
        index = len(self._shards)
        name = f"reference-{index:03d}.npz"
        path = self.destination / name
        temporary = self.destination / f".{name}.tmp.npz"
        np.savez_compressed(
            temporary,
            image_ids=np.asarray([item["image_id"] for item in self._records], dtype=np.int64),
            widths=np.asarray([item["width"] for item in self._records], dtype=np.int32),
            heights=np.asarray([item["height"] for item in self._records], dtype=np.int32),
            image_sha256=np.asarray([item["image_sha256"] for item in self._records], dtype="U64"),
            input_sha256=np.asarray([item["input_sha256"] for item in self._records], dtype="U64"),
            inputs=np.stack(self._inputs, axis=0),
            outputs=np.stack(self._outputs, axis=0),
        )
        os.replace(temporary, path)
        self._shards.append({
            "path": name,
            "sha256": sha256_file(path),
            "images": [item["image_id"] for item in self._records],
            "count": len(self._records),
            "bytes": path.stat().st_size,
        })
        self._count += len(self._records)
        self._records = []
        self._inputs = []
        self._outputs = []

    def finalize(self, *, annotations_sha256: str, annotations_count: int) -> dict[str, Any]:
        self.flush()
        manifest = {
            "schema_version": 1,
            "kind": "handed_nanodet_fulltest_reference_pack",
            "source": self.source,
            "contract": {
                "input_shape": list(INPUT_SHAPE),
                "input_dtype": "float32",
                "output_shape": list(OUTPUT_SHAPE),
                "output_dtype": "float32",
                "classes": list(CLASS_NAMES),
                "preprocess": "BGR direct resize 320x320, mean/std fixed",
            },
            "annotations_sha256": annotations_sha256,
            "annotations_count": annotations_count,
            "images": self._count,
            "shards": self._shards,
        }
        write_json(self.destination / "manifest.json", manifest)
        return manifest


def iter_reference_samples(reference_dir: Path, *, expected_onnx_sha256: str, expected_annotations_sha256: str) -> Iterator[tuple[dict[str, Any], np.ndarray, np.ndarray]]:
    manifest_path = reference_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _require(manifest.get("schema_version") == 1 and manifest.get("kind") == "handed_nanodet_fulltest_reference_pack", "unsupported reference pack")
    _require(manifest.get("source", {}).get("onnx_sha256") == expected_onnx_sha256, "reference pack ONNX SHA mismatch")
    _require(manifest.get("annotations_sha256") == expected_annotations_sha256, "reference pack annotation SHA mismatch")
    seen: set[int] = set()
    for item in manifest.get("shards", []):
        path = reference_dir / item["path"]
        _require(path.is_file() and sha256_file(path) == item["sha256"], f"reference shard integrity mismatch: {path}")
        with np.load(path, allow_pickle=False) as data:
            image_ids = data["image_ids"]
            widths = data["widths"]
            heights = data["heights"]
            image_hashes = data["image_sha256"]
            input_hashes = data["input_sha256"]
            inputs = data["inputs"]
            outputs = data["outputs"]
            size = len(image_ids)
            _require(size == int(item["count"]) and inputs.shape == (size, *INPUT_SHAPE[1:]) and outputs.shape == (size, *OUTPUT_SHAPE[1:]), f"reference shard contract mismatch: {path}")
            for index in range(size):
                image_id = int(image_ids[index])
                _require(image_id not in seen, f"duplicate image ID in reference pack: {image_id}")
                seen.add(image_id)
                tensor = np.ascontiguousarray(inputs[index][None, ...], dtype=np.float32)
                output = np.ascontiguousarray(outputs[index][None, ...], dtype=np.float32)
                _require(sha256_array(tensor) == str(input_hashes[index]), f"reference input hash mismatch for image {image_id}")
                yield {
                    "image_id": image_id,
                    "width": int(widths[index]),
                    "height": int(heights[index]),
                    "image_sha256": str(image_hashes[index]),
                    "input_sha256": str(input_hashes[index]),
                }, tensor, output
    _require(len(seen) == int(manifest.get("images", -1)), "reference pack image count mismatch")


def compact_time_summary(values_ms: list[float]) -> dict[str, Any]:
    if not values_ms:
        return {"count": 0, "p50_ms": None, "p95_ms": None, "mean_ms": None, "min_ms": None, "max_ms": None}
    values = np.asarray(values_ms, dtype=np.float64)
    return {
        "count": int(values.size),
        "p50_ms": float(np.percentile(values, 50)),
        "p95_ms": float(np.percentile(values, 95)),
        "mean_ms": float(values.mean()),
        "min_ms": float(values.min()),
        "max_ms": float(values.max()),
    }
