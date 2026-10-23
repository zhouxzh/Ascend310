from __future__ import annotations

import numpy as np
import pytest

from bbox_utils import scale_xyxy_boxes, xyxy_to_coco_xywh


def test_xyxy_to_coco_xywh_scales_to_canvas() -> None:
    result = xyxy_to_coco_xywh([10, 20, 40, 60], 200, 100)
    assert result == pytest.approx([15.0, 60.0, 45.0, 120.0])


def test_scale_xyxy_boxes_keeps_axis_order() -> None:
    boxes = np.asarray([[10, 20, 40, 60], [0, 0, 200, 100]], dtype=np.float32)
    result = scale_xyxy_boxes(boxes, 200, 100)
    np.testing.assert_allclose(
        result,
        np.asarray([[15.0, 60.0, 60.0, 180.0], [0.0, 0.0, 300.0, 300.0]]),
    )


def test_invalid_xyxy_box_is_rejected() -> None:
    with pytest.raises(ValueError, match="Invalid XYXY box"):
        xyxy_to_coco_xywh([40, 20, 10, 60], 200, 100)
