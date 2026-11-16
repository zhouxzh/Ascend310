from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys

import pytest

np = pytest.importorskip("numpy")
ROOT = Path(__file__).resolve().parents[2]
OFFLINE_TOOLS = ROOT / "tools" / "offline"
sys.path.insert(0, str(OFFLINE_TOOLS))
MODULE_PATH = OFFLINE_TOOLS / "handed_picodet_fulltest.py"
SPEC = importlib.util.spec_from_file_location("handed_picodet_fulltest", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[MODULE.__name__] = MODULE
SPEC.loader.exec_module(MODULE)


def contract(size: int) -> dict:
    levels = []
    for index, stride in enumerate((8, 16, 32, 64)):
        count = ((size + stride - 1) // stride) ** 2
        levels.append([f"fetch_name_{index}", [1, count, 2]])
    for index, stride in enumerate((8, 16, 32, 64), start=4):
        count = ((size + stride - 1) // stride) ** 2
        levels.append([f"fetch_name_{index}", [1, count, 32]])
    return {
        "name": f"picodet-xs-{size}",
        "model_family": "PicoDet",
        "sha256": "0" * 64,
        "inputs": [["image", [1, 3, size, size]]],
        "outputs": levels,
        "input_layout": "NCHW",
        "output_layout": "NLC",
        "input_dtype": "float32",
        "output_dtype": "float32",
        "resize_mode": "direct_stretch",
        "resize_interpolation": 2,
        "color_order": "BGR_to_RGB",
        "mean": [0.485, 0.456, 0.406],
        "std": [0.229, 0.224, 0.225],
        "score_transform": "align_head_sigmoid_in_graph",
        "dfl_order": ["left", "top", "right", "bottom"],
        "class_names": ["left_palm", "right_palm"],
        "class_ids": [1, 2],
        "reg_max": 7,
        "strides": [8, 16, 32, 64],
        "cell_offset": 0.5,
        "nms": {"score_threshold": 0.025, "nms_threshold": 0.6, "nms_top_k": 1000, "keep_top_k": 100},
    }


def test_validate_xs_320_contract() -> None:
    value = MODULE.validate_contract(contract(320))
    assert value.output_shapes[0] == (1, 1600, 2)
    assert value.output_shapes[-1] == (1, 25, 32)
    assert value.keep_top_k == 100


def test_validate_xs_416_contract_uses_ceil_stride64() -> None:
    value = MODULE.validate_contract(contract(416))
    assert value.output_shapes[0] == (1, 2704, 2)
    assert value.output_shapes[3] == (1, 49, 2)
    assert value.output_shapes[-1] == (1, 49, 32)


def test_decoder_uses_half_cell_and_dfl_expectation() -> None:
    value = MODULE.validate_contract(contract(320))
    outputs = [np.zeros(shape, dtype=np.float32) for shape in value.output_shapes]
    outputs[0][0, 0, 0] = 0.9
    outputs[4][0, 0] = -100.0
    outputs[4][0, 0, [1, 9, 17, 25]] = 100.0
    detections = MODULE.decode_outputs(outputs, value, image_width=320, image_height=320)
    assert len(detections) == 1
    assert detections[0].label == 0
    assert detections[0].xyxy == pytest.approx((0.0, 0.0, 12.0, 12.0))


def test_decoder_applies_global_keep_top_k() -> None:
    value = MODULE.validate_contract(contract(320))
    outputs = [np.zeros(shape, dtype=np.float32) for shape in value.output_shapes]
    outputs[0][:, :, 0] = 0.9
    outputs[4][:] = -100.0
    outputs[4][:, :, [1, 9, 17, 25]] = 100.0
    detections = MODULE.decode_outputs(outputs, value, image_width=320, image_height=320)
    assert len(detections) == value.keep_top_k
    assert all(item.label == 0 for item in detections)
