from __future__ import annotations

import numpy as np
import onnx
from onnx import TensorProto, helper
import pytest

from time_frequency_dashboard.model.diagnose_om_outputs import summarize_difference
from time_frequency_dashboard.model.generate_yolo_keep_dtype import select_keep_dtype_nodes


def test_summarize_difference_identifies_channel_and_tolerance_violation() -> None:
    reference = np.zeros((1, 3, 4), dtype=np.float32)
    candidate = reference.copy()
    candidate[0, 2, 1] = np.float32(0.02)

    result = summarize_difference(reference, candidate, rtol=1.0e-2, atol=1.0e-3, limit=2)

    assert result["violating_values"] == 1
    assert result["worst_values"][0]["index"] == [0, 2, 1]
    assert result["worst_values"][0]["violates_tolerance"] is True
    assert result["channels"][0]["channel"] == 2


def test_summarize_difference_reports_nonfinite_values_without_serializing_nan() -> None:
    result = summarize_difference(
        np.asarray([[1.0, 2.0]], dtype=np.float32),
        np.asarray([[1.0, np.nan]], dtype=np.float32),
        rtol=1.0e-2,
        atol=1.0e-3,
        limit=2,
    )

    assert result["finite"] is False
    assert result["reason"] == "nonfinite_values"
    assert "worst_values" not in result


@pytest.mark.parametrize("rtol, atol", [(-1.0, 1.0e-3), (1.0e-2, float("nan"))])
def test_summarize_difference_rejects_invalid_tolerances(rtol, atol) -> None:
    with pytest.raises(ValueError, match="finite non-negative"):
        summarize_difference(
            np.zeros((1,), dtype=np.float32),
            np.zeros((1,), dtype=np.float32),
            rtol=rtol,
            atol=atol,
            limit=1,
        )


def test_yolo_keep_dtype_selection_excludes_detect_branches() -> None:
    nodes = [
        helper.make_node("Conv", ["x", "w"], ["class"], name="/model.23/cv3.0/Conv"),
        helper.make_node("Conv", ["x", "w"], ["box"], name="/model.23/cv2.0/Conv"),
    ]
    nodes.extend(
        helper.make_node("Constant", [], [f"constant_{index}"], name=f"constant_{index}")
        for index in range(318)
    )
    nodes.extend(
        [
            helper.make_node("Identity", ["box"], ["value_1"], name="/model.23/dfl/Softmax"),
            helper.make_node("Identity", ["value_1"], ["value_2"], name="/model.23/Concat_3"),
        ]
    )
    graph = helper.make_graph(
        nodes,
        "test",
        [helper.make_tensor_value_info("x", TensorProto.FLOAT, [1])],
        [helper.make_tensor_value_info("value_2", TensorProto.FLOAT, [1])],
        initializer=[helper.make_tensor("w", TensorProto.FLOAT, [1], [1.0])],
    )
    model = helper.make_model(graph)

    selected = select_keep_dtype_nodes(model)

    assert "/model.23/cv2.0/Conv" not in selected
    assert "/model.23/cv3.0/Conv" not in selected
    assert "/model.23/dfl/Softmax" in selected
    assert "/model.23/Concat_3" in selected
