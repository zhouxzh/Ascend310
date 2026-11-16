#!/usr/bin/env python3
"""Shared PicoDet helpers for the fixed two-class palm test protocol."""

from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from handed_nanodet_fulltest import Detection, _require, class_nms

PICO_SCORE_THRESHOLD = 0.025
PICO_NMS_IOU_THRESHOLD = 0.6
PICO_NMS_TOP_K = 1000
PICO_KEEP_TOP_K = 100
PICO_RESIZE_INTERPOLATION = 2

MEAN = np.asarray([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.asarray([0.229, 0.224, 0.225], dtype=np.float32)
CLASS_NAMES = ("left_palm", "right_palm")
REG_MAX = 7
STRIDES = (8, 16, 32, 64)
CELL_OFFSET = 0.5


@dataclass(frozen=True)
class PicoDetContract:
    name: str
    input_name: str
    input_shape: tuple[int, int, int, int]
    output_names: tuple[str, ...]
    output_shapes: tuple[tuple[int, int, int], ...]
    output_dtype: str
    onnx_sha256: str
    score_threshold: float
    nms_iou_threshold: float
    nms_top_k: int
    keep_top_k: int
    resize_interpolation: int

    @property
    def image_height(self) -> int:
        return self.input_shape[2]

    @property
    def image_width(self) -> int:
        return self.input_shape[3]


def preprocess_bgr(image_bgr: np.ndarray, contract: PicoDetContract) -> np.ndarray:
    """Apply PicoDet direct-stretch BGR-to-RGB ImageNet normalization."""
    if not isinstance(image_bgr, np.ndarray) or image_bgr.ndim != 3 or image_bgr.shape[2] != 3:
        raise ValueError("expected HWC BGR image with exactly three channels")
    if image_bgr.dtype != np.uint8:
        raise ValueError(f"expected uint8 image, got {image_bgr.dtype}")
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - environment-specific dependency
        raise RuntimeError("OpenCV is required for PicoDet preprocessing") from exc
    resized = cv2.resize(
        image_bgr,
        (contract.image_width, contract.image_height),
        interpolation=contract.resize_interpolation,
    )
    rgb = resized[:, :, ::-1].astype(np.float32) / 255.0
    normalized = (rgb - MEAN) / STD
    return np.ascontiguousarray(normalized.transpose(2, 0, 1)[None, ...], dtype=np.float32)


def expected_level_shapes(input_shape: tuple[int, int, int, int]) -> tuple[tuple[int, int, int], ...]:
    height, width = input_shape[2:]
    return tuple((1, math.ceil(height / stride) * math.ceil(width / stride), 2) for stride in STRIDES)


def expected_regression_shapes(input_shape: tuple[int, int, int, int]) -> tuple[tuple[int, int, int], ...]:
    height, width = input_shape[2:]
    return tuple((1, math.ceil(height / stride) * math.ceil(width / stride), 4 * (REG_MAX + 1)) for stride in STRIDES)


def validate_contract(item: dict[str, Any]) -> PicoDetContract:
    _require(item.get("model_family") == "PicoDet", "unsupported model family")
    _require(item.get("input_layout") == "NCHW", "unsupported input layout")
    _require(item.get("output_layout") == "NLC", "unsupported output layout")
    _require(item.get("input_dtype") == "float32", "unsupported input dtype")
    _require(item.get("output_dtype") == "float32", "unsupported output dtype")
    _require(item.get("resize_mode") == "direct_stretch", "unsupported resize mode")
    _require(item.get("color_order") == "BGR_to_RGB", "unsupported color order")
    _require(item.get("mean") == [0.485, 0.456, 0.406], "unsupported mean")
    _require(item.get("std") == [0.229, 0.224, 0.225], "unsupported std")
    _require(item.get("class_names") == list(CLASS_NAMES), "unexpected class names")
    _require(item.get("class_ids") == [1, 2], "unexpected class IDs")
    _require(item.get("reg_max") == REG_MAX, "unexpected reg_max")
    _require(item.get("strides") == list(STRIDES), "unexpected FPN strides")
    _require(float(item.get("cell_offset")) == CELL_OFFSET, "unexpected cell offset")
    _require(item.get("score_transform") == "align_head_sigmoid_in_graph", "unexpected score transform")
    _require(item.get("dfl_order") == ["left", "top", "right", "bottom"], "unexpected DFL order")
    nms = item.get("nms")
    _require(nms == {
        "score_threshold": PICO_SCORE_THRESHOLD,
        "nms_threshold": PICO_NMS_IOU_THRESHOLD,
        "nms_top_k": PICO_NMS_TOP_K,
        "keep_top_k": PICO_KEEP_TOP_K,
    }, "unexpected PicoDet NMS contract")
    _require(item.get("resize_interpolation") == PICO_RESIZE_INTERPOLATION, "unexpected resize interpolation")
    inputs = item.get("inputs")
    outputs = item.get("outputs")
    _require(isinstance(inputs, list) and len(inputs) == 1, "expected one ONNX input")
    _require(isinstance(outputs, list) and len(outputs) == 8, "expected eight PicoDet ONNX outputs")
    input_name, input_shape = inputs[0]
    input_shape = tuple(int(value) for value in input_shape)
    _require(input_name == "image" and len(input_shape) == 4 and input_shape[:2] == (1, 3), "unexpected input")
    output_names = tuple(str(value[0]) for value in outputs)
    output_shapes = tuple(tuple(int(dim) for dim in value[1]) for value in outputs)
    _require(output_shapes[:4] == expected_level_shapes(input_shape), "classification output level shapes mismatch")
    _require(output_shapes[4:] == expected_regression_shapes(input_shape), "DFL output level shapes mismatch")
    return PicoDetContract(
        name=str(item["name"]),
        input_name=str(input_name),
        input_shape=input_shape,
        output_names=output_names,
        output_shapes=output_shapes,
        output_dtype=str(item["output_dtype"]),
        onnx_sha256=str(item["sha256"]),
        score_threshold=float(nms["score_threshold"]),
        nms_iou_threshold=float(nms["nms_threshold"]),
        nms_top_k=int(nms["nms_top_k"]),
        keep_top_k=int(nms["keep_top_k"]),
        resize_interpolation=int(item["resize_interpolation"]),
    )


def _softmax(values: np.ndarray) -> np.ndarray:
    shifted = values - values.max(axis=-1, keepdims=True)
    exponent = np.exp(shifted)
    return exponent / exponent.sum(axis=-1, keepdims=True)


def _level_priors(input_shape: tuple[int, int, int, int], stride: int) -> np.ndarray:
    height, width = input_shape[2:]
    yy, xx = np.meshgrid(
        np.arange(math.ceil(height / stride)),
        np.arange(math.ceil(width / stride)),
        indexing="ij",
    )
    return np.stack(((xx.ravel() + CELL_OFFSET) * stride, (yy.ravel() + CELL_OFFSET) * stride), axis=1).astype(np.float32)


def decode_outputs(
    outputs: Iterable[np.ndarray],
    contract: PicoDetContract,
    *,
    image_width: int,
    image_height: int,
    score_threshold: float | None = None,
    nms_iou_threshold: float | None = None,
    max_detections: int | None = None,
) -> list[Detection]:
    """Decode postprocess-free PicoDet V2 class scores and four DFL levels."""
    score_threshold = contract.score_threshold if score_threshold is None else score_threshold
    nms_iou_threshold = contract.nms_iou_threshold if nms_iou_threshold is None else nms_iou_threshold
    max_detections = contract.keep_top_k if max_detections is None else max_detections
    values = tuple(np.asarray(value, dtype=np.float32) for value in outputs)
    _require(len(values) == 8, "expected eight PicoDet outputs")
    for value, shape in zip(values, contract.output_shapes):
        _require(tuple(value.shape) == shape and np.isfinite(value).all(), f"PicoDet output contract mismatch: {value.shape} != {shape}")
    all_scores: list[np.ndarray] = []
    all_boxes: list[np.ndarray] = []
    for level, stride in enumerate(STRIDES):
        scores = values[level][0]
        dfl = values[level + 4][0].reshape(-1, 4, REG_MAX + 1)
        priors = _level_priors(contract.input_shape, stride)
        _require(scores.shape == (priors.shape[0], 2), "classification/priors mismatch")
        _require(dfl.shape == (priors.shape[0], 4, REG_MAX + 1), "DFL/priors mismatch")
        distances = (_softmax(dfl) * np.arange(REG_MAX + 1, dtype=np.float32)).sum(axis=-1) * float(stride)
        boxes = np.concatenate((priors - distances[:, :2], priors + distances[:, 2:]), axis=1)
        all_scores.append(scores)
        all_boxes.append(boxes)
    scores = np.concatenate(all_scores, axis=0)
    boxes = np.concatenate(all_boxes, axis=0)
    boxes[:, [0, 2]] = np.clip(boxes[:, [0, 2]], 0, contract.image_width) * (image_width / contract.image_width)
    boxes[:, [1, 3]] = np.clip(boxes[:, [1, 3]], 0, contract.image_height) * (image_height / contract.image_height)
    detections: list[Detection] = []
    for label in range(2):
        candidates = np.flatnonzero(scores[:, label] >= score_threshold)
        if not candidates.size:
            continue
        ranked = candidates[np.argsort(-scores[candidates, label], kind="stable")]
        candidates = ranked[:contract.nms_top_k]
        kept = candidates[class_nms(boxes[candidates], scores[candidates, label], nms_iou_threshold)]
        for index in kept:
            left, top, right, bottom = (float(value) for value in boxes[index])
            if right > left and bottom > top:
                detections.append(Detection(label=label, score=float(scores[index, label]), xyxy=(left, top, right, bottom)))
    detections.sort(key=lambda item: (-item.score, item.label, item.xyxy[0], item.xyxy[1]))
    return detections[:max_detections]
