#!/usr/bin/env python3
"""Run a small MindSpore Lite graph on an Ascend board.

This script is intentionally board-only.  It does not import full MindSpore,
Torch, or a model framework; the caller supplies the CANN environment and the
isolated Python environment containing ``mindspore_lite``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from pathlib import Path
from typing import Any

import numpy as np


def _json(value: Any) -> Any:
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, (list, tuple)):
        return [_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json(item) for key, item in value.items()}
    return value


def emit(event: str, **fields: Any) -> None:
    record = {"event": event, "time": time.time()}
    record.update({key: _json(value) for key, value in fields.items()})
    print(json.dumps(record, ensure_ascii=False, sort_keys=True), flush=True)


def _shape(tensor: Any) -> tuple[int, ...]:
    shape = tuple(int(item) for item in tensor.shape)
    if any(item <= 0 for item in shape):
        raise ValueError(f"dynamic or invalid tensor shape: {shape}")
    return shape


def _dtype(tensor: Any) -> np.dtype:
    value = str(getattr(tensor, "dtype", "float32")).lower()
    aliases = {
        "float": np.float32,
        "float32": np.float32,
        "float16": np.float16,
        "bfloat16": np.float32,
        "int8": np.int8,
        "uint8": np.uint8,
        "int16": np.int16,
        "uint16": np.uint16,
        "int32": np.int32,
        "uint32": np.uint32,
        "int64": np.int64,
        "uint64": np.uint64,
        "bool": np.bool_,
    }
    for name, dtype in aliases.items():
        if name in value:
            return np.dtype(dtype)
    raise ValueError(f"unsupported Lite tensor dtype: {value!r}")


def _input_array(tensor: Any, input_bin: Path | None, index: int) -> np.ndarray:
    shape = _shape(tensor)
    dtype = _dtype(tensor)
    if input_bin is not None and index == 0:
        raw = np.fromfile(input_bin, dtype=dtype)
        expected = int(np.prod(shape))
        if raw.size != expected:
            raise ValueError(
                f"{input_bin} has {raw.size} {dtype} values; expected "
                f"{expected} for shape {shape}"
            )
        return np.ascontiguousarray(raw.reshape(shape))
    if dtype == np.dtype(np.bool_):
        return np.zeros(shape, dtype=dtype)
    return np.zeros(shape, dtype=dtype)


def _tensor_info(tensor: Any, index: int) -> dict[str, Any]:
    return {
        "index": index,
        "name": str(getattr(tensor, "name", "")),
        "shape": list(tensor.shape),
        "dtype": str(getattr(tensor, "dtype", "")),
    }


def _model_type(mslite: Any, value: str) -> Any:
    if value == "mindir":
        return mslite.ModelType.MINDIR
    if value == "mindir_lite":
        return mslite.ModelType.MINDIR_LITE
    raise ValueError(value)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("model", type=Path)
    parser.add_argument("--model-type", choices=("mindir", "mindir_lite"), default="mindir")
    parser.add_argument("--input-bin", type=Path)
    parser.add_argument("--device-id", type=int, default=0)
    parser.add_argument("--target", choices=("ascend", "cpu"), default="ascend")
    parser.add_argument("--provider", choices=("ge", "acl"), default=None)
    args = parser.parse_args()

    if not args.model.is_file():
        emit("error", stage="input", message=f"model not found: {args.model}")
        return 2
    if args.input_bin is not None and not args.input_bin.is_file():
        emit("error", stage="input", message=f"input not found: {args.input_bin}")
        return 2

    try:
        import mindspore_lite as mslite
    except Exception as exc:  # pragma: no cover - board-only failure path
        emit("error", stage="import", exception=type(exc).__name__, message=str(exc))
        return 10

    emit("import", version=str(getattr(mslite, "__version__", "unknown")))
    context = mslite.Context()
    context.target = [args.target]
    if args.target == "ascend":
        context.ascend.device_id = args.device_id
        if args.provider is not None:
            context.ascend.provider = args.provider
    emit("context", target=list(context.target), device_id=args.device_id, provider=args.provider)

    model = mslite.Model()
    started = time.monotonic()
    try:
        model.build_from_file(str(args.model), _model_type(mslite, args.model_type), context)
    except BaseException as exc:  # preserve native/runtime failures in the log
        emit(
            "error",
            stage="build",
            elapsed_seconds=time.monotonic() - started,
            exception=type(exc).__name__,
            message=str(exc),
        )
        traceback.print_exc()
        return 20
    emit("build_passed", elapsed_seconds=time.monotonic() - started)

    try:
        inputs = model.get_inputs()
        outputs = model.get_outputs()
        emit(
            "contract",
            inputs=[_tensor_info(tensor, index) for index, tensor in enumerate(inputs)],
            outputs=[_tensor_info(tensor, index) for index, tensor in enumerate(outputs)],
        )
        for index, tensor in enumerate(inputs):
            tensor.set_data_from_numpy(_input_array(tensor, args.input_bin, index))
        started = time.monotonic()
        result = model.predict(inputs)
        emit(
            "predict_passed",
            elapsed_seconds=time.monotonic() - started,
            outputs=[
                {
                    **_tensor_info(tensor, index),
                    "sample": _json(tensor.get_data_to_numpy().reshape(-1)[:8]),
                }
                for index, tensor in enumerate(result)
            ],
        )
    except BaseException as exc:  # pragma: no cover - board-only failure path
        emit("error", stage="predict", exception=type(exc).__name__, message=str(exc))
        traceback.print_exc()
        return 30
    finally:
        # Explicit deletion helps the caller observe whether the device can be
        # reused by a second process.  Lite owns the native model resources.
        del model
    emit("passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
