from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

np = pytest.importorskip("numpy")
MODULE_PATH = Path(__file__).resolve().parents[2] / "tools" / "board" / "run_nanodet_sweep_om.py"
if not MODULE_PATH.is_file():
    MODULE_PATH = Path(__file__).with_name("run_nanodet_sweep_om.py")
SPEC = importlib.util.spec_from_file_location("run_nanodet_sweep_om", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def item(input_size: int, output_shape: tuple[int, int, int]) -> dict:
    return {
        "inputs": [["data", [1, 3, input_size, input_size]]],
        "outputs": [["output", list(output_shape)]],
    }


def test_plus_416_uses_ceil_for_stride_64() -> None:
    contract = MODULE.infer_contract(item(416, (1, 3598, 34)))
    assert contract["strides"] == (8, 16, 32, 64)
    assert contract["reg_max"] == 7
    assert MODULE.priors(contract["input_shape"], contract["strides"]).shape == (3598, 3)


def test_legacy_416_has_three_strides_and_reg_max_10() -> None:
    contract = MODULE.infer_contract(item(416, (1, 3549, 46)))
    assert contract["strides"] == (8, 16, 32)
    assert contract["reg_max"] == 10
    assert MODULE.priors(contract["input_shape"], contract["strides"]).shape == (3549, 3)


def test_legacy_prior_uses_half_cell_center_offset() -> None:
    contract = MODULE.infer_contract(item(320, (1, 2100, 34)))
    plus = MODULE.priors(contract["input_shape"], contract["strides"], 0.0)
    legacy = MODULE.priors(contract["input_shape"], contract["strides"], 0.5)
    assert plus[0, 0] == 0.0
    assert legacy[0, 0] == 4.0
    assert legacy[0, 1] == 4.0


def test_unsupported_prior_count_is_rejected() -> None:
    with pytest.raises(ValueError, match="cannot infer strides"):
        MODULE.infer_contract(item(416, (1, 3500, 34)))
