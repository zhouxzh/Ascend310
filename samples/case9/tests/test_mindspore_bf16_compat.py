from __future__ import annotations

import json
from pathlib import Path
import struct
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from ascend_edge_chat.backends import mindspore_qwen


class _Tensor:
    @staticmethod
    def from_numpy(array):
        return np.array(array, copy=True)


def _bf16_bytes(values: list[float]) -> bytes:
    array = np.asarray(values, dtype=np.float32)
    return (array.view(np.uint32) >> 16).astype("<u2").tobytes()


def test_bf16_bytes_are_numerically_converted_to_float16() -> None:
    result = mindspore_qwen._bf16_bytes_to_float16(
        _bf16_bytes([1.0, -2.5, 0.25]),
        (3,),
        np,
    )
    np.testing.assert_array_equal(result, np.asarray([1.0, -2.5, 0.25], dtype=np.float16))


def test_old_mindnlp_loader_is_patched_for_wrong_bf16_width(tmp_path: Path) -> None:
    serialization = SimpleNamespace(_NP_TYPES={"BF16": np.float64})
    modeling_utils = SimpleNamespace()

    def import_module(name: str):
        return {
            "numpy": np,
            "mindnlp.core.serialization": serialization,
            "mindnlp.transformers.modeling_utils": modeling_utils,
        }[name]

    with patch.object(mindspore_qwen.importlib, "import_module", side_effect=import_module):
        assert mindspore_qwen._install_mindnlp_bf16_loader_compat(
            SimpleNamespace(Tensor=_Tensor)
        )

    header = {
        "weight": {
            "dtype": "BF16",
            "shape": [2],
            "data_offsets": [0, 4],
        }
    }
    encoded = json.dumps(header, separators=(",", ":")).encode("utf-8")
    fixture = tmp_path / "model.safetensors"
    fixture.write_bytes(struct.pack("<Q", len(encoded)) + encoded + _bf16_bytes([1.5, -4.0]))

    loaded = modeling_utils.safe_load_file(str(fixture))
    np.testing.assert_array_equal(
        loaded["weight"], np.asarray([1.5, -4.0], dtype=np.float16)
    )


def test_valid_two_byte_bf16_mapping_is_not_patched() -> None:
    serialization = SimpleNamespace(_NP_TYPES={"BF16": np.uint16})

    def import_module(name: str):
        if name == "numpy":
            return np
        if name == "mindnlp.core.serialization":
            return serialization
        raise AssertionError("unexpected import: %s" % name)

    with patch.object(mindspore_qwen.importlib, "import_module", side_effect=import_module):
        assert not mindspore_qwen._install_mindnlp_bf16_loader_compat(
            SimpleNamespace(Tensor=_Tensor)
        )
