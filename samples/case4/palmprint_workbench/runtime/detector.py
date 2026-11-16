"""NPU palm detection with selectable NanoDet and PicoDet model contracts."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import MODEL_ASSET_ROOT, ROOT


MANIFEST_PATH = ROOT / "detector_manifest.json"
CLASS_NAMES = ("left_palm", "right_palm")
PALM_SIDES = ("left", "right")


@dataclass(frozen=True)
class PalmBox:
    xyxy: tuple[float, float, float, float]
    score: float
    palm_side: str = "left"

    def __post_init__(self) -> None:
        if self.palm_side not in PALM_SIDES:
            raise ValueError(f"palm_side must be left or right, got {self.palm_side!r}")


@dataclass(frozen=True)
class InputTransform:
    mode: str
    scale_x: float
    scale_y: float
    pad_x: float
    pad_y: float
    source_width: int
    source_height: int
    target_width: int
    target_height: int

    def to_source_boxes(self, boxes: np.ndarray) -> np.ndarray:
        result = np.asarray(boxes, dtype=np.float32).copy()
        result[:, [0, 2]] = (result[:, [0, 2]] - self.pad_x) / self.scale_x
        result[:, [1, 3]] = (result[:, [1, 3]] - self.pad_y) / self.scale_y
        result[:, [0, 2]] = np.clip(result[:, [0, 2]], 0, self.source_width)
        result[:, [1, 3]] = np.clip(result[:, [1, 3]], 0, self.source_height)
        return result


@dataclass(frozen=True)
class DetectorSpec:
    id: str
    display_name: str
    family: str
    status: str
    path: str
    bytes: int
    sha256: str
    input_shape: tuple[int, ...]
    output_shapes: tuple[tuple[int, ...], ...]
    class_names: tuple[str, ...]
    score_threshold: float
    nms_iou_threshold: float
    nms_containment_threshold: float | None
    nms_top_k: int
    keep_top_k: int
    reg_max: int
    strides: tuple[int, ...]
    cell_offset: float
    resize_interpolation: str
    normalization: str
    notice: str | None
    task_map: float | None
    raw_numerical_gate_ok: bool | None

    def asset_path(self) -> Path:
        root = MODEL_ASSET_ROOT.resolve()
        path = (root / self.path).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ValueError("手掌检测 OM 路径越出模型资产目录") from exc
        return path


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _parse_shapes(value: Any, *, field: str) -> tuple[tuple[int, ...], ...]:
    _require(isinstance(value, list) and value, f"{field} must be a non-empty list")
    result: list[tuple[int, ...]] = []
    for shape in value:
        _require(isinstance(shape, list) and shape, f"{field} contains an invalid shape")
        dims = tuple(int(dim) for dim in shape)
        _require(all(dim > 0 for dim in dims), f"{field} contains a non-positive dimension")
        result.append(dims)
    return tuple(result)


def _parse_spec(item: dict[str, Any]) -> DetectorSpec:
    model_id = str(item.get("id", "")).strip()
    family = str(item.get("family", "")).strip().lower()
    _require(bool(model_id), "detector id is required")
    _require(family in {"nanodet", "picodet"}, f"unsupported detector family: {family}")
    output_shapes = _parse_shapes(item.get("output_shapes"), field="output_shapes")
    input_shape = tuple(int(value) for value in item.get("input_shape", []))
    _require(
        len(input_shape) == 4
        and input_shape[:2] == (1, 3)
        and input_shape[2] == input_shape[3],
        "detector input must be square NCHW",
    )
    class_names = tuple(str(value) for value in item.get("class_names", []))
    _require(class_names == CLASS_NAMES, f"手掌检测类别合同不匹配：{class_names}")
    score_threshold = float(item.get("score_threshold", 0.35))
    nms_iou_threshold = float(item.get("nms_iou_threshold", 0.60))
    _require(0 <= score_threshold <= 1 and 0 <= nms_iou_threshold <= 1, "detector thresholds must be within [0, 1]")
    containment = item.get("nms_containment_threshold")
    if containment is not None:
        containment = float(containment)
        _require(0 < containment <= 1, "containment threshold must be within (0, 1]")
    size = int(input_shape[-1])
    if family == "nanodet":
        _require(size == 320 and output_shapes == ((1, 2125, 34),), "unsupported NanoDet contract")
        expected_nms_top_k = 100
        expected_keep_top_k = 100
        expected_reg_max = 7
        expected_strides = (8, 16, 32, 64)
        expected_offset = 0.0
    else:
        count = sum((size + stride - 1) // stride * ((size + stride - 1) // stride) for stride in (8, 16, 32, 64))
        cls = tuple((1, (size // stride + (1 if size % stride else 0)) ** 2, 2) for stride in (8, 16, 32, 64))
        reg = tuple((1, shape[1], 32) for shape in cls)
        _require(output_shapes == cls + reg, f"unsupported PicoDet output shapes for {size}x{size}")
        expected_nms_top_k = int(item.get("nms_top_k", 1000))
        expected_keep_top_k = int(item.get("keep_top_k", 100))
        expected_reg_max = int(item.get("reg_max", 7))
        expected_strides = tuple(int(value) for value in item.get("strides", []))
        expected_offset = float(item.get("cell_offset", 0.5))
    _require(expected_reg_max == 7 and expected_strides == (8, 16, 32, 64), "unsupported DFL/stride contract")
    _require(expected_offset == 0.0 if family == "nanodet" else expected_offset == 0.5, "unsupported anchor center offset")
    digest = str(item.get("sha256", "")).lower()
    _require(len(digest) == 64 and all(character in "0123456789abcdef" for character in digest), "detector SHA-256 is invalid")
    bytes_count = int(item.get("bytes", 0))
    _require(bytes_count > 0, "detector byte count must be positive")
    return DetectorSpec(
        id=model_id,
        display_name=str(item.get("display_name") or model_id),
        family=family,
        status=str(item.get("status") or "manual_test_pending"),
        path=str(item.get("path", "")),
        bytes=bytes_count,
        sha256=digest,
        input_shape=input_shape,
        output_shapes=output_shapes,
        class_names=class_names,
        score_threshold=score_threshold,
        nms_iou_threshold=nms_iou_threshold,
        nms_containment_threshold=containment,
        nms_top_k=expected_nms_top_k,
        keep_top_k=expected_keep_top_k,
        reg_max=expected_reg_max,
        strides=expected_strides,
        cell_offset=expected_offset,
        resize_interpolation=str(item.get("resize_interpolation", "")),
        normalization=str(item.get("normalization", "")),
        notice=str(item["notice"]) if item.get("notice") else None,
        task_map=float(item["task_mAP50_95"]) if item.get("task_mAP50_95") is not None else None,
        raw_numerical_gate_ok=(
            bool(item["raw_numerical_gate_ok"])
            if item.get("raw_numerical_gate_ok") is not None
            else None
        ),
    )


def load_detector_catalog() -> tuple[str, dict[str, DetectorSpec]]:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    _require(isinstance(payload, dict), "detector manifest must be an object")
    _require(payload.get("kind") == "palm_detector_catalog", "unsupported detector manifest")
    items = payload.get("models")
    _require(isinstance(items, list) and items, "detector manifest has no models")
    specs: dict[str, DetectorSpec] = {}
    for item in items:
        _require(isinstance(item, dict), "detector model entry must be an object")
        spec = _parse_spec(item)
        _require(spec.id not in specs, f"duplicate detector id: {spec.id}")
        specs[spec.id] = spec
    default_id = str(payload.get("default_model_id", "")).strip()
    _require(default_id in specs, "default detector id is missing")
    return default_id, specs


def default_detector_id() -> str:
    return load_detector_catalog()[0]


def detector_spec(model_id: str | None = None) -> DetectorSpec:
    default_id, specs = load_detector_catalog()
    selected = str(model_id or default_id)
    try:
        return specs[selected]
    except KeyError as exc:
        raise KeyError(f"未知手掌检测模型：{selected}") from exc


def detector_catalog() -> list[dict[str, Any]]:
    """Return a compact bootstrap catalog without hashing every OM."""
    _, specs = load_detector_catalog()
    result = []
    for spec in specs.values():
        try:
            path = spec.asset_path()
            available = path.is_file() and path.stat().st_size == spec.bytes
            reason = None if available else "OM 文件缺失或字节数不匹配"
        except (OSError, ValueError) as exc:
            available = False
            reason = str(exc)
        result.append({
            "id": spec.id,
            "display_name": spec.display_name,
            "family": spec.family,
            "status": spec.status,
            "available": available,
            "reason": reason,
            "input_shape": list(spec.input_shape),
            "score_threshold": spec.score_threshold,
            "notice": spec.notice,
            "task_mAP50_95": spec.task_map,
            "raw_numerical_gate_ok": spec.raw_numerical_gate_ok,
        })
    return result


def _priors(spec: DetectorSpec) -> np.ndarray:
    rows: list[np.ndarray] = []
    height, width = spec.input_shape[2:]
    for stride in spec.strides:
        yy, xx = np.meshgrid(
            np.arange((height + stride - 1) // stride),
            np.arange((width + stride - 1) // stride),
            indexing="ij",
        )
        rows.append(np.stack((
            (xx.ravel() + spec.cell_offset) * stride,
            (yy.ravel() + spec.cell_offset) * stride,
            np.full(xx.size, stride, dtype=np.float32),
        ), axis=1))
    return np.concatenate(rows).astype(np.float32)


def prepare_input(
    image_rgb: np.ndarray,
    spec: DetectorSpec,
) -> tuple[np.ndarray, InputTransform]:
    """Direct-stretch a detector input and retain its inverse coordinate transform."""
    if not isinstance(image_rgb, np.ndarray) or image_rgb.ndim != 3 or image_rgb.shape[2] < 3:
        raise ValueError("手掌检测需要 RGB 图像")
    rgb = np.ascontiguousarray(image_rgb[:, :, :3], dtype=np.uint8)
    source_height, source_width = rgb.shape[:2]
    target_height, target_width = spec.input_shape[2:]
    scale_x = target_width / source_width
    scale_y = target_height / source_height
    pad_x = 0.0
    pad_y = 0.0
    resized = cv2.resize(
        rgb,
        (target_width, target_height),
        interpolation=cv2.INTER_LINEAR if spec.family == "nanodet" else cv2.INTER_CUBIC,
    )
    if spec.family == "nanodet":
        bgr = cv2.cvtColor(resized, cv2.COLOR_RGB2BGR)
        mean = np.asarray([103.53, 116.28, 123.675], dtype=np.float32)
        std = np.asarray([57.375, 57.12, 58.395], dtype=np.float32)
        normalized = (bgr.astype(np.float32) - mean) / std
    else:
        normalized = resized.astype(np.float32) / 255.0
        normalized = (normalized - np.asarray([0.485, 0.456, 0.406], dtype=np.float32)) / np.asarray(
            [0.229, 0.224, 0.225], dtype=np.float32
        )
    tensor = np.ascontiguousarray(normalized.transpose(2, 0, 1)[None], dtype=np.float32)
    return tensor, InputTransform(
        mode="direct_stretch",
        scale_x=scale_x,
        scale_y=scale_y,
        pad_x=pad_x,
        pad_y=pad_y,
        source_width=source_width,
        source_height=source_height,
        target_width=target_width,
        target_height=target_height,
    )


def preprocess(image_rgb: np.ndarray, spec: DetectorSpec) -> np.ndarray:
    """Return only the direct-stretch tensor for callers that do not decode boxes."""
    return prepare_input(image_rgb, spec)[0]


def _nms(
    boxes: np.ndarray, scores: np.ndarray, iou_limit: float,
    *, containment_threshold: float | None = None,
) -> list[int]:
    """Score-ordered NMS used by either detector family."""
    if not 0.0 <= iou_limit <= 1.0:
        raise ValueError("NMS IoU threshold must be within [0, 1]")
    if containment_threshold is not None and not 0.0 < containment_threshold <= 1.0:
        raise ValueError("NMS containment threshold must be within (0, 1]")
    boxes = np.asarray(boxes, dtype=np.float32)
    scores = np.asarray(scores, dtype=np.float32)
    if boxes.shape != (scores.size, 4) or scores.ndim != 1:
        raise ValueError("NMS requires Nx4 boxes and N scores")
    sizes = boxes[:, 2:] - boxes[:, :2]
    valid = np.isfinite(boxes).all(axis=1) & np.isfinite(scores) & (sizes > 0).all(axis=1)
    area = sizes[:, 0] * sizes[:, 1]
    centres = (boxes[:, :2] + boxes[:, 2:]) / 2
    candidates = np.flatnonzero(valid)
    order = candidates[np.argsort(-scores[candidates], kind="stable")]
    kept: list[int] = []
    while order.size:
        index = int(order[0])
        kept.append(index)
        other = order[1:]
        if not other.size:
            break
        lt = np.maximum(boxes[index, :2], boxes[other, :2])
        rb = np.minimum(boxes[index, 2:], boxes[other, 2:])
        intersection = np.maximum(rb - lt, 0).prod(axis=1)
        iou = intersection / np.maximum(area[index] + area[other] - intersection, 1e-8)
        duplicate = iou > iou_limit
        if containment_threshold is not None:
            smaller_area = np.minimum(area[index], area[other])
            larger_area = np.maximum(area[index], area[other])
            smaller_sizes = np.where((area[index] <= area[other])[:, None], sizes[index], sizes[other])
            close_centres = (np.abs(centres[index] - centres[other]) <= 0.25 * smaller_sizes).all(axis=1)
            duplicate |= ((intersection / smaller_area >= containment_threshold)
                          & (smaller_area / larger_area >= 0.25) & close_centres)
        order = other[~duplicate]
    return kept


def _softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - values.max(axis=-1, keepdims=True)
    exponent = np.exp(shifted)
    return exponent / exponent.sum(axis=-1, keepdims=True)


def _decode_nanodet(
    outputs: list[np.ndarray], transform: InputTransform, spec: DetectorSpec
) -> tuple[np.ndarray, np.ndarray]:
    _require(len(outputs) == 1, "NanoDet must expose one output")
    raw = np.asarray(outputs[0], dtype=np.float32)
    _require(tuple(raw.shape) == spec.output_shapes[0] and np.isfinite(raw).all(), f"手掌检测输出合同不匹配：{raw.shape}")
    logits = raw[0, :, 2:].reshape(raw.shape[1], 4, spec.reg_max + 1)
    distances = (_softmax(logits) * np.arange(spec.reg_max + 1, dtype=np.float32)).sum(axis=-1) * _priors(spec)[:, 2:3]
    priors = _priors(spec)
    boxes = np.concatenate((priors[:, :2] - distances[:, :2], priors[:, :2] + distances[:, 2:]), axis=-1)
    return transform.to_source_boxes(boxes), raw[0, :, :2]


def _decode_picodet(
    outputs: list[np.ndarray], transform: InputTransform, spec: DetectorSpec
) -> tuple[np.ndarray, np.ndarray]:
    _require(len(outputs) == 8, "PicoDet must expose eight outputs")
    values = tuple(np.asarray(value, dtype=np.float32) for value in outputs)
    for value, shape in zip(values, spec.output_shapes):
        _require(tuple(value.shape) == shape and np.isfinite(value).all(), f"PicoDet输出合同不匹配：{value.shape}")
    all_scores: list[np.ndarray] = []
    all_boxes: list[np.ndarray] = []
    height, width = spec.input_shape[2:]
    for level, stride in enumerate(spec.strides):
        yy, xx = np.meshgrid(
            np.arange((height + stride - 1) // stride),
            np.arange((width + stride - 1) // stride),
            indexing="ij",
        )
        priors = np.stack(((xx.ravel() + spec.cell_offset) * stride, (yy.ravel() + spec.cell_offset) * stride), axis=1)
        scores = values[level][0]
        dfl = values[level + 4][0].reshape(-1, 4, spec.reg_max + 1)
        distances = (_softmax(dfl) * np.arange(spec.reg_max + 1, dtype=np.float32)).sum(axis=-1) * float(stride)
        all_scores.append(scores)
        all_boxes.append(np.concatenate((priors - distances[:, :2], priors + distances[:, 2:]), axis=1))
    boxes = np.concatenate(all_boxes, axis=0)
    scores = np.concatenate(all_scores, axis=0)
    return transform.to_source_boxes(boxes), scores


def _decoded_detections(boxes: np.ndarray, scores: np.ndarray, spec: DetectorSpec) -> list[PalmBox]:
    result: list[PalmBox] = []
    for label, palm_side in enumerate(PALM_SIDES):
        class_scores = scores[:, label]
        candidates = np.flatnonzero(class_scores >= spec.score_threshold)
        if candidates.size:
            ranked = candidates[np.argsort(-class_scores[candidates], kind="stable")]
            candidates = ranked[: spec.nms_top_k]
            kept = candidates[_nms(
                boxes[candidates], class_scores[candidates], spec.nms_iou_threshold,
                containment_threshold=spec.nms_containment_threshold,
            )]
            for index in kept:
                left, top, right, bottom = (float(value) for value in boxes[index])
                if right > left and bottom > top:
                    result.append(PalmBox((left, top, right, bottom), float(class_scores[index]), palm_side))
    result.sort(key=lambda item: (-item.score, item.xyxy[0], item.xyxy[1]))
    return result[: spec.keep_top_k]


def _decode(
    outputs: list[np.ndarray], transform: InputTransform, spec: DetectorSpec
) -> tuple[list[PalmBox], np.ndarray, np.ndarray]:
    boxes, scores = (
        _decode_nanodet(outputs, transform, spec)
        if spec.family == "nanodet"
        else _decode_picodet(outputs, transform, spec)
    )
    return _decoded_detections(boxes, scores, spec), boxes, scores


def _direct_transform(image_shape: tuple[int, int], spec: DetectorSpec) -> InputTransform:
    height, width = image_shape
    target_height, target_width = spec.input_shape[2:]
    return InputTransform(
        mode="direct_stretch",
        scale_x=target_width / width,
        scale_y=target_height / height,
        pad_x=0.0,
        pad_y=0.0,
        source_width=width,
        source_height=height,
        target_width=target_width,
        target_height=target_height,
    )


def decode(
    outputs: list[np.ndarray] | np.ndarray,
    image_shape: tuple[int, int],
    *,
    model_id: str | None = None,
) -> list[PalmBox]:
    spec = detector_spec(model_id)
    values = [outputs] if isinstance(outputs, np.ndarray) else outputs
    return _decode(values, _direct_transform(image_shape, spec), spec)[0]


def decode_with_debug(
    outputs: list[np.ndarray] | np.ndarray,
    image_shape: tuple[int, int],
    *,
    model_id: str | None = None,
) -> tuple[list[PalmBox], dict[str, object]]:
    spec = detector_spec(model_id)
    values = [outputs] if isinstance(outputs, np.ndarray) else outputs
    detections, boxes, scores = _decode(values, _direct_transform(image_shape, spec), spec)
    classes: list[dict[str, object]] = []
    for label, palm_side in enumerate(PALM_SIDES):
        class_scores = scores[:, label]
        top_indices = np.argsort(-class_scores, kind="stable")[:3]
        classes.append({
            "palm_side": palm_side,
            "max_score": float(class_scores[top_indices[0]]),
            "candidates_at_threshold": int(np.count_nonzero(class_scores >= spec.score_threshold)),
            "top_candidates": [
                {"score": float(class_scores[index]), "bbox_xyxy": [float(value) for value in boxes[index]]}
                for index in top_indices
            ],
        })
    return detections, {
        "detector_id": spec.id,
        "detector_family": spec.family,
        "score_threshold": spec.score_threshold,
        "nms_iou_threshold": spec.nms_iou_threshold,
        "classes": classes,
    }


class PalmDetector:
    def __init__(self, model_id: str | None = None) -> None:
        self.spec = detector_spec(model_id)
        self.runner: object | None = None

    def _model_path(self) -> Path:
        path = self.spec.asset_path()
        if not path.is_file():
            raise FileNotFoundError(f"手掌检测 OM 不存在：{path}")
        if path.stat().st_size != self.spec.bytes:
            raise ValueError("手掌检测 OM 字节数不匹配")
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
                digest.update(block)
        if digest.hexdigest() != self.spec.sha256:
            raise ValueError("手掌检测 OM SHA-256 不匹配")
        return path

    def _run(self, image_rgb: np.ndarray) -> tuple[list[np.ndarray], InputTransform]:
        from .candidate_runners import _CandidateRunner
        if self.runner is None:
            runner = _CandidateRunner(self._model_path())
            if runner.input_shapes != [self.spec.input_shape] or tuple(tuple(shape) for shape in runner.output_shapes) != self.spec.output_shapes:
                runner.close()
                raise ValueError("手掌检测 OM 输入输出形状不匹配")
            self.runner = runner
        tensor, transform = prepare_input(image_rgb, self.spec)
        return self.runner.run([tensor]), transform

    def detect(self, image_rgb: np.ndarray) -> list[PalmBox]:
        values, transform = self._run(image_rgb)
        return _decode(values, transform, self.spec)[0]

    def detect_with_debug(self, image_rgb: np.ndarray) -> tuple[list[PalmBox], dict[str, object]]:
        values, transform = self._run(image_rgb)
        detections, boxes, scores = _decode(values, transform, self.spec)
        classes: list[dict[str, object]] = []
        for label, palm_side in enumerate(PALM_SIDES):
            class_scores = scores[:, label]
            top_indices = np.argsort(-class_scores, kind="stable")[:3]
            classes.append({
                "palm_side": palm_side,
                "max_score": float(class_scores[top_indices[0]]),
                "candidates_at_threshold": int(np.count_nonzero(class_scores >= self.spec.score_threshold)),
                "top_candidates": [
                    {"score": float(class_scores[index]), "bbox_xyxy": [float(value) for value in boxes[index]]}
                    for index in top_indices
                ],
            })
        return detections, {
            "detector_id": self.spec.id,
            "detector_family": self.spec.family,
            "preprocess_mode": transform.mode,
            "scale_x": transform.scale_x,
            "scale_y": transform.scale_y,
            "pad_x": transform.pad_x,
            "pad_y": transform.pad_y,
            "source_resolution": [transform.source_width, transform.source_height],
            "model_resolution": [transform.target_width, transform.target_height],
            "score_threshold": self.spec.score_threshold,
            "nms_iou_threshold": self.spec.nms_iou_threshold,
            "classes": classes,
        }

    def close(self) -> None:
        if self.runner is not None:
            self.runner.close()
            self.runner = None
