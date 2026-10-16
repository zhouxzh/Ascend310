#!/usr/bin/env python3
"""Smoke-test a static multi-input/multi-output OM with ACL and NumPy only."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any

import numpy as np

from palmprint_workbench.runtime.adapters import _acl_check, _acl_result_code


def _timestamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def _dims(acl: Any, desc: Any, getter: Any, index: int) -> tuple[int, ...]:
    info = _acl_check(getter(desc, index), "get tensor dimensions")
    return tuple(int(value) for value in info["dims"][: int(info["dimCount"])])


def _dtype(acl: Any, value: Any) -> np.dtype:
    mapping = {
        int(getattr(acl, "ACL_FLOAT", 0)): np.dtype(np.float32),
        int(getattr(acl, "ACL_FLOAT16", 1)): np.dtype(np.float16),
    }
    if int(value) not in mapping:
        raise ValueError(f"unsupported OM dtype: {value}")
    return mapping[int(value)]


def _call(steps: list[dict[str, Any]], phase: str, operation: Any, *args: Any) -> bool:
    event: dict[str, Any] = {"timestamp": _timestamp(), "phase": phase}
    try:
        result = _acl_result_code(operation(*args), none_is_success=True)
        event.update({"ok": result == 0, "return_code": result})
    except BaseException as exc:  # noqa: BLE001 - retain cleanup evidence
        event.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    steps.append(event)
    return bool(event["ok"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report: dict[str, Any] = {
        "schema_version": 1,
        "model": str(args.model),
        "backend": "acl",
        "framework": "numpy_only",
        "contains_biometric_data": False,
        "started_at": _timestamp(),
        "status": "failed",
    }
    steps: list[dict[str, Any]] = []
    failures: list[str] = []
    acl: Any | None = None
    context: Any | None = None
    model_id: Any | None = None
    desc: Any | None = None
    buffers: list[dict[str, Any]] = []
    input_dataset: Any | None = None
    output_dataset: Any | None = None
    try:
        import acl as acl_module

        acl = acl_module
        _acl_check(acl.init(), "acl.init")
        _acl_check(acl.rt.set_device(0), "acl.rt.set_device")
        context = _acl_check(acl.rt.create_context(0), "acl.rt.create_context")
        model_id = _acl_check(acl.mdl.load_from_file(str(args.model)), "acl.mdl.load_from_file")
        desc = acl.mdl.create_desc()
        if desc is None:
            raise RuntimeError("acl.mdl.create_desc returned no descriptor")
        _acl_check(acl.mdl.get_desc(desc, model_id), "acl.mdl.get_desc")
        input_count = int(acl.mdl.get_num_inputs(desc))
        output_count = int(acl.mdl.get_num_outputs(desc))
        if input_count < 2 or output_count < 2:
            raise ValueError(f"expected multi-input/multi-output OM, got {input_count}/{output_count}")
        input_shapes = [_dims(acl, desc, acl.mdl.get_input_dims, i) for i in range(input_count)]
        output_shapes = [_dims(acl, desc, acl.mdl.get_output_dims, i) for i in range(output_count)]
        input_dtypes = [_dtype(acl, acl.mdl.get_input_data_type(desc, i)) for i in range(input_count)]
        output_dtypes = [_dtype(acl, acl.mdl.get_output_data_type(desc, i)) for i in range(output_count)]
        report.update(
            {
                "input_shapes": [list(shape) for shape in input_shapes],
                "input_dtypes": [str(dtype) for dtype in input_dtypes],
                "output_shapes": [list(shape) for shape in output_shapes],
                "output_dtypes": [str(dtype) for dtype in output_dtypes],
            }
        )
        input_dataset = acl.mdl.create_dataset()
        output_dataset = acl.mdl.create_dataset()
        if input_dataset is None or output_dataset is None:
            raise RuntimeError("input/output dataset unavailable")
        for index in range(input_count):
            size = int(acl.mdl.get_input_size_by_index(desc, index))
            pointer = _acl_check(acl.rt.malloc(size, 0), f"acl.rt.malloc input {index}")
            data_buffer = acl.create_data_buffer(pointer, size)
            if data_buffer is None:
                raise RuntimeError(f"input data buffer {index} unavailable")
            _acl_check(acl.mdl.add_dataset_buffer(input_dataset, data_buffer), f"add input buffer {index}")
            buffers.append({"data_buffer": data_buffer, "pointer": pointer, "size": size, "input": True})
        for index in range(output_count):
            size = int(acl.mdl.get_output_size_by_index(desc, index))
            pointer = _acl_check(acl.rt.malloc(size, 0), f"acl.rt.malloc output {index}")
            data_buffer = acl.create_data_buffer(pointer, size)
            if data_buffer is None:
                raise RuntimeError(f"output data buffer {index} unavailable")
            _acl_check(acl.mdl.add_dataset_buffer(output_dataset, data_buffer), f"add output buffer {index}")
            buffers.append({"data_buffer": data_buffer, "pointer": pointer, "size": size, "input": False})
        values = [np.zeros(shape, dtype=dtype) for shape, dtype in zip(input_shapes, input_dtypes)]
        for item, buffer in zip(values, buffers[:input_count]):
            pointer = acl.util.numpy_to_ptr(item)
            _acl_check(acl.rt.memcpy(buffer["pointer"], buffer["size"], pointer, item.nbytes, int(getattr(acl, "ACL_MEMCPY_HOST_TO_DEVICE", 1))), "host_to_device")
        started = time.perf_counter()
        _acl_check(acl.mdl.execute(model_id, input_dataset, output_dataset), "acl.mdl.execute")
        synchronize = getattr(acl.rt, "synchronize_device", None)
        if callable(synchronize):
            _acl_check(synchronize(), "synchronize_device")
        outputs: list[np.ndarray] = []
        for shape, dtype, buffer in zip(output_shapes, output_dtypes, buffers[input_count:]):
            value = np.empty(shape, dtype=dtype)
            pointer = acl.util.numpy_to_ptr(value)
            _acl_check(acl.rt.memcpy(pointer, value.nbytes, buffer["pointer"], buffer["size"], int(getattr(acl, "ACL_MEMCPY_DEVICE_TO_HOST", 2))), "device_to_host")
            outputs.append(value)
        report.update({"inference_ms": (time.perf_counter() - started) * 1000.0, "output_finite": all(bool(np.all(np.isfinite(item))) for item in outputs)})
        if not report["output_finite"]:
            failures.append("output contains NaN or infinity")
    except BaseException as exc:  # noqa: BLE001 - candidate-specific evidence
        failures.append(f"{type(exc).__name__}: {exc}")
        report["error"] = failures[-1]
    finally:
        if acl is not None and context is not None:
            _call(steps, "set_context_before_cleanup", acl.rt.set_context, context)
            synchronize = getattr(acl.rt, "synchronize_device", None)
            if callable(synchronize):
                _call(steps, "synchronize_device_before_cleanup", synchronize)
        # Keep cleanup independent: one failed resource must not hide the rest.
        for buffer in reversed(buffers):
            if acl is not None and buffer.get("data_buffer") is not None:
                _call(steps, "destroy_data_buffer", acl.destroy_data_buffer, buffer["data_buffer"])
            if acl is not None and buffer.get("pointer") is not None:
                _call(steps, "free_device_buffer", acl.rt.free, buffer["pointer"])
        if acl is not None and output_dataset is not None:
            _call(steps, "destroy_output_dataset", acl.mdl.destroy_dataset, output_dataset)
        if acl is not None and input_dataset is not None:
            _call(steps, "destroy_input_dataset", acl.mdl.destroy_dataset, input_dataset)
        if acl is not None and desc is not None:
            _call(steps, "destroy_model_desc", acl.mdl.destroy_desc, desc)
        if acl is not None and model_id is not None:
            _call(steps, "unload_model", acl.mdl.unload, model_id)
        if acl is not None and context is not None:
            _call(steps, "destroy_context", acl.rt.destroy_context, context)
        if acl is not None:
            _call(steps, "acl_reset_device", acl.rt.reset_device, 0)
            _call(steps, "acl_finalize", acl.finalize)
    report["cleanup"] = steps
    report["finished_at"] = _timestamp()
    report["failures"] = failures
    report["status"] = "passed" if not failures and all(item.get("ok") for item in steps) else "failed"
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
