"""Static-shape PyACL runner for Piano-DDSP control models."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import threading
from typing import Any

import numpy as np


ACL_MEM_MALLOC_HUGE_FIRST = 0
ACL_MEMCPY_HOST_TO_DEVICE = 1
ACL_MEMCPY_DEVICE_TO_HOST = 2


def _check(result: Any, operation: str) -> None:
    status = result[-1] if isinstance(result, tuple) else result
    if not isinstance(status, int) or status != 0:
        raise RuntimeError(f"{operation} failed, ret={status!r}")


def _shape(result: Any, operation: str) -> tuple[int, ...]:
    _check(result, operation)
    raw = result[0]
    count = int(raw.get("dimCount", 0))
    values = tuple(int(value) for value in raw.get("dims", ())[:count])
    if count <= 0 or len(values) != count or any(value <= 0 for value in values):
        raise ValueError(f"{operation} returned invalid dimensions: {raw!r}")
    return values


def _canonical_name(raw: str, expected: set[str], kind: str) -> str:
    exact = raw.removesuffix(":0")
    if exact in expected:
        return exact
    matches = [name for name in expected if name in raw]
    if len(matches) != 1:
        raise ValueError(f"Cannot map OM {kind} name {raw!r}")
    return matches[0]


def _host_pointer(array: np.ndarray) -> tuple[int, np.ndarray]:
    """Return a pointer and its live contiguous NumPy owner for ACL memcpy."""
    if not array.flags.c_contiguous:
        raise ValueError("ACL host input must be C-contiguous")
    return int(array.ctypes.data), array


class PianoAclModel:
    """Own one loaded OM and reusable device/host buffers."""

    _runtime_lock = threading.Lock()
    _runtime_references: dict[tuple[int, int], int] = {}

    def __init__(
        self,
        model_path: Path,
        metadata: Mapping[str, Any],
        device_id: int = 0,
        *,
        acl_module: Any | None = None,
    ) -> None:
        self.model_path = Path(model_path).resolve()
        if not self.model_path.is_file():
            raise FileNotFoundError(self.model_path)
        if device_id < 0:
            raise ValueError("device_id must be non-negative")
        if acl_module is None:
            try:
                import acl as acl_module  # type: ignore[import-not-found,no-redef]
            except ImportError as exc:
                raise RuntimeError("PyACL is unavailable; activate the existing CANN environment") from exc
        self.acl = acl_module
        self.device_id = int(device_id)
        self.input_shapes = {
            str(name): tuple(int(value) for value in shape)
            for name, shape in dict(metadata["inputs"]).items()
        }
        self.output_shapes = {
            str(name): tuple(int(value) for value in shape)
            for name, shape in dict(metadata["outputs"]).items()
        }
        self.input_dtypes = {
            name: (np.int32 if name == "piano_model" else np.float32)
            for name in self.input_shapes
        }
        self.output_dtypes = {name: np.float32 for name in self.output_shapes}
        self.context: Any = None
        self.model_id: Any = None
        self.model_desc: Any = None
        self.input_dataset: Any = None
        self.output_dataset: Any = None
        self.inputs: list[dict[str, Any]] = []
        self.outputs: list[dict[str, Any]] = []
        self._initialized = False
        self._device_set = False
        self._runtime_key: tuple[int, int] | None = None
        self._closed = False
        self._lock = threading.Lock()
        try:
            self._initialize()
        except BaseException:
            self.close(suppress_errors=True)
            raise

    def _initialize(self) -> None:
        self._acquire_runtime()
        self.context, status = self.acl.rt.create_context(self.device_id)
        _check((self.context, status), "acl.rt.create_context")
        self.model_id, status = self.acl.mdl.load_from_file(str(self.model_path))
        _check((self.model_id, status), "acl.mdl.load_from_file")
        self.model_desc = self.acl.mdl.create_desc()
        if self.model_desc is None:
            raise RuntimeError("acl.mdl.create_desc failed")
        _check(self.acl.mdl.get_desc(self.model_desc, self.model_id), "acl.mdl.get_desc")
        self.input_dataset = self.acl.mdl.create_dataset()
        self.output_dataset = self.acl.mdl.create_dataset()
        if self.input_dataset is None or self.output_dataset is None:
            raise RuntimeError("acl.mdl.create_dataset failed")
        self.inputs = self._prepare(True)
        self.outputs = self._prepare(False)

    def _acquire_runtime(self) -> None:
        key = (id(self.acl), self.device_id)
        with self._runtime_lock:
            references = self._runtime_references.get(key, 0)
            if references == 0:
                status = self.acl.init()
                if status not in (0, 100002):
                    _check(status, "acl.init")
                try:
                    _check(self.acl.rt.set_device(self.device_id), "acl.rt.set_device")
                except BaseException:
                    if status == 0:
                        self.acl.finalize()
                    raise
            self._runtime_references[key] = references + 1
        self._runtime_key = key
        self._initialized = True
        self._device_set = True

    def _release_runtime(self, errors: list[BaseException]) -> None:
        key = self._runtime_key
        if key is None:
            return
        with self._runtime_lock:
            references = self._runtime_references.get(key, 0)
            if references <= 0:
                errors.append(RuntimeError("ACL runtime reference count is invalid"))
            elif references > 1:
                self._runtime_references[key] = references - 1
            else:
                self._runtime_references.pop(key, None)
                for operation, arguments, name in (
                    (self.acl.rt.reset_device, (self.device_id,), "acl.rt.reset_device"),
                    (self.acl.finalize, (), "acl.finalize"),
                ):
                    try:
                        _check(operation(*arguments), name)
                    except BaseException as exc:
                        errors.append(exc)
        self._runtime_key = None
        self._device_set = False
        self._initialized = False

    def _prepare(self, is_input: bool) -> list[dict[str, Any]]:
        kind = "input" if is_input else "output"
        shapes = self.input_shapes if is_input else self.output_shapes
        dtypes = self.input_dtypes if is_input else self.output_dtypes
        dataset = self.input_dataset if is_input else self.output_dataset
        count = int(getattr(self.acl.mdl, f"get_num_{kind}s")(self.model_desc))
        prepared: list[dict[str, Any]] = []
        names: list[str] = []
        try:
            for index in range(count):
                raw_name = str(
                    getattr(self.acl.mdl, f"get_{kind}_name_by_index")(
                        self.model_desc, index
                    )
                )
                name = _canonical_name(raw_name, set(shapes), kind)
                names.append(name)
                actual_shape = _shape(
                    getattr(self.acl.mdl, f"get_{kind}_dims")(
                        self.model_desc, index
                    ),
                    f"acl.mdl.get_{kind}_dims[{index}]",
                )
                if actual_shape != shapes[name]:
                    raise ValueError(
                        f"Unexpected OM {kind} {name} shape {actual_shape}; expected {shapes[name]}"
                    )
                dtype = np.dtype(dtypes[name])
                expected_size = int(np.prod(shapes[name])) * dtype.itemsize
                actual_size = int(
                    getattr(self.acl.mdl, f"get_{kind}_size_by_index")(
                        self.model_desc, index
                    )
                )
                if actual_size != expected_size:
                    raise ValueError(
                        f"Unexpected OM {kind} {name} size {actual_size}; expected {expected_size}"
                    )
                dtype_getter = getattr(self.acl.mdl, f"get_{kind}_data_type", None)
                if dtype_getter is not None:
                    actual_dtype = dtype_getter(self.model_desc, index)
                    expected_acl = (
                        getattr(self.acl, "ACL_INT32", 3)
                        if dtype == np.dtype(np.int32)
                        else getattr(self.acl, "ACL_FLOAT", 0)
                    )
                    if actual_dtype != expected_acl:
                        raise ValueError(f"Unexpected OM {kind} {name} dtype {actual_dtype}")
                pointer, status = self.acl.rt.malloc(
                    actual_size, ACL_MEM_MALLOC_HUGE_FIRST
                )
                _check((pointer, status), f"acl.rt.malloc {kind}[{index}]")
                data_buffer = self.acl.create_data_buffer(pointer, actual_size)
                if data_buffer is None:
                    self.acl.rt.free(pointer)
                    raise RuntimeError(
                        f"acl.create_data_buffer {kind}[{index}] failed"
                    )
                try:
                    _check(
                        self.acl.mdl.add_dataset_buffer(dataset, data_buffer),
                        f"acl.mdl.add_dataset_buffer {kind}[{index}]",
                    )
                except BaseException:
                    self.acl.destroy_data_buffer(data_buffer)
                    self.acl.rt.free(pointer)
                    raise
                prepared.append(
                    {
                        "name": name,
                        "raw_name": raw_name,
                        "shape": list(actual_shape),
                        "dtype": "int32" if dtype == np.dtype(np.int32) else "float32",
                        "ptr": pointer,
                        "size": actual_size,
                        "buffer": data_buffer,
                        "host": np.empty(shapes[name], dtype=dtype) if not is_input else None,
                    }
                )
            if len(names) != len(set(names)) or set(names) != set(shapes):
                raise ValueError(f"Unexpected OM {kind}s: {names}")
            return prepared
        except BaseException:
            for item in reversed(prepared):
                try:
                    self.acl.destroy_data_buffer(item["buffer"])
                except BaseException:
                    pass
                try:
                    self.acl.rt.free(item["ptr"])
                except BaseException:
                    pass
            raise

    def contract_report(self) -> dict[str, object]:
        """Return the OM contract that was validated while allocating buffers."""
        return {
            "validated": True,
            "inputs": [
                {
                    "name": item["name"],
                    "om_name": item["raw_name"],
                    "shape": item["shape"],
                    "dtype": item["dtype"],
                    "bytes": item["size"],
                }
                for item in self.inputs
            ],
            "outputs": [
                {
                    "name": item["name"],
                    "om_name": item["raw_name"],
                    "shape": item["shape"],
                    "dtype": item["dtype"],
                    "bytes": item["size"],
                }
                for item in self.outputs
            ],
        }

    def infer(
        self,
        values: Mapping[str, np.ndarray],
        *,
        output_names: set[str] | None = None,
        output_targets: Mapping[str, np.ndarray] | None = None,
        copy_outputs: bool = True,
        validate_tensors: bool = True,
    ) -> dict[str, np.ndarray]:
        if self._closed:
            raise RuntimeError("Piano-DDSP ACL model is closed")
        if set(values) != set(self.input_shapes):
            raise ValueError(f"Inputs must be exactly {sorted(self.input_shapes)}")
        selected_outputs = set(self.output_shapes) if output_names is None else set(output_names)
        unknown_outputs = selected_outputs - set(self.output_shapes)
        if unknown_outputs:
            raise ValueError(f"Unknown outputs requested: {sorted(unknown_outputs)}")
        raw_targets = {} if output_targets is None else dict(output_targets)
        invalid_targets = set(raw_targets) - selected_outputs
        if invalid_targets:
            raise ValueError(
                f"Output targets were provided for unrequested outputs: {sorted(invalid_targets)}"
            )
        targets: dict[str, np.ndarray] = {}
        for name, value in raw_targets.items():
            target = np.asarray(value)
            expected_shape = self.output_shapes[name]
            expected_dtype = np.dtype(self.output_dtypes[name])
            if target.dtype != expected_dtype or target.size != int(np.prod(expected_shape)):
                raise ValueError(
                    f"Output target {name} must contain {int(np.prod(expected_shape))} "
                    f"values of type {expected_dtype}"
                )
            if not target.flags.c_contiguous:
                raise ValueError(f"Output target {name} must be C-contiguous")
            targets[name] = target.reshape(expected_shape)
        prepared: dict[str, np.ndarray] = {}
        for name, shape in self.input_shapes.items():
            array = np.ascontiguousarray(values[name], dtype=self.input_dtypes[name])
            if array.shape != shape:
                raise ValueError(f"Unexpected input {name} shape {array.shape}; expected {shape}")
            if (
                validate_tensors
                and np.issubdtype(array.dtype, np.floating)
                and not np.all(np.isfinite(array))
            ):
                raise ValueError(f"Input {name} contains NaN or Inf")
            prepared[name] = array
        with self._lock:
            _check(self.acl.rt.set_context(self.context), "acl.rt.set_context")
            for item in self.inputs:
                host_pointer, host_owner = _host_pointer(prepared[item["name"]])
                _check(
                    self.acl.rt.memcpy(
                        item["ptr"],
                        item["size"],
                        host_pointer,
                        item["size"],
                        ACL_MEMCPY_HOST_TO_DEVICE,
                    ),
                    f"acl.rt.memcpy H2D {item['name']}",
                )
                del host_owner
            _check(
                self.acl.mdl.execute(self.model_id, self.input_dataset, self.output_dataset),
                "acl.mdl.execute",
            )
            result: dict[str, np.ndarray] = {}
            for item in self.outputs:
                if item["name"] not in selected_outputs:
                    continue
                host = targets.get(item["name"], item["host"])
                _check(
                    self.acl.rt.memcpy(
                        host.ctypes.data,
                        item["size"],
                        item["ptr"],
                        item["size"],
                        ACL_MEMCPY_DEVICE_TO_HOST,
                    ),
                    f"acl.rt.memcpy D2H {item['name']}",
                )
                if validate_tensors and not np.all(np.isfinite(host)):
                    raise RuntimeError(f"OM output {item['name']} contains NaN or Inf")
                result[item["name"]] = host.copy() if copy_outputs else host
            return result

    def close(self, *, suppress_errors: bool = False) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[BaseException] = []

        def cleanup(operation: Any, *args: Any) -> None:
            try:
                _check(operation(*args), operation.__name__)
            except BaseException as exc:
                errors.append(exc)

        for dataset, buffers in (
            (self.input_dataset, self.inputs),
            (self.output_dataset, self.outputs),
        ):
            if dataset is None:
                continue
            for item in buffers:
                cleanup(self.acl.destroy_data_buffer, item["buffer"])
                cleanup(self.acl.rt.free, item["ptr"])
            cleanup(self.acl.mdl.destroy_dataset, dataset)
        self.inputs.clear()
        self.outputs.clear()
        if self.model_desc is not None:
            cleanup(self.acl.mdl.destroy_desc, self.model_desc)
            self.model_desc = None
        if self.model_id is not None:
            cleanup(self.acl.mdl.unload, self.model_id)
            self.model_id = None
        if self.context is not None:
            cleanup(self.acl.rt.destroy_context, self.context)
            self.context = None
        self._release_runtime(errors)
        if errors and not suppress_errors:
            raise RuntimeError("PyACL cleanup failed: " + "; ".join(map(str, errors)))

    def __enter__(self) -> "PianoAclModel":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()
