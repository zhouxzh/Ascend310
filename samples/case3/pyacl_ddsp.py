"""Small synchronous PyACL runner for static float32 OM models."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import threading
from typing import Any

import numpy as np


ACL_MEM_MALLOC_HUGE_FIRST = 0
ACL_MEMCPY_HOST_TO_DEVICE = 1
ACL_MEMCPY_DEVICE_TO_HOST = 2

INPUT_SHAPES = {
    "state": (512,),
    "f0_scaled": (1,),
    "pw_scaled": (1,),
}
OUTPUT_SHAPES = {
    "amplitude": (1,),
    "harmonics": (60,),
    "noise_amps": (65,),
    "state_out": (512,),
}

_PERSISTENT_RUNTIME_LOCK = threading.Lock()
_PERSISTENT_RUNTIMES: dict[tuple[int, int], dict[str, Any]] = {}


def _check_ret(result: Any, operation: str) -> None:
    ret = result[-1] if isinstance(result, tuple) else result
    if not isinstance(ret, int):
        raise RuntimeError(f"{operation} returned non-integer status: {ret!r}")
    if ret != 0:
        raise RuntimeError(f"{operation} failed, ret={ret} (0x{ret:X})")


def _canonical_name(raw_name: str, expected_names: set[str], kind: str) -> str:
    matches = [name for name in expected_names if name in raw_name]
    if len(matches) != 1:
        raise ValueError(f"Cannot map OM {kind} name {raw_name!r}")
    return matches[0]


def _tensor_shape(dims_result: Any, operation: str) -> tuple[int, ...]:
    _check_ret(dims_result, operation)
    info = dims_result[0]
    count = int(info.get("dimCount", 0))
    dims = tuple(int(value) for value in info.get("dims", [])[:count])
    if count <= 0 or len(dims) != count or any(value <= 0 for value in dims):
        raise ValueError(f"{operation} returned invalid dimensions: {info!r}")
    return dims


class PyAclModelRunner:
    """Own one ACL context, model, and reusable static I/O datasets."""

    def __init__(
        self,
        model_path: Path,
        device_id: int = 0,
        *,
        acl_module: Any | None = None,
        keep_runtime: bool = False,
        input_shapes: Mapping[str, tuple[int, ...]] | None = None,
        output_shapes: Mapping[str, tuple[int, ...]] | None = None,
    ) -> None:
        if device_id < 0:
            raise ValueError("device_id must be non-negative")
        model_path = Path(model_path).resolve()
        if not model_path.is_file():
            raise FileNotFoundError(f"OM model not found: {model_path}")

        if acl_module is None:
            try:
                import acl as acl_module  # type: ignore[import-not-found,no-redef]
            except ImportError as exc:
                raise RuntimeError(
                    "PyACL is required for OM inference. Activate the Ascend CANN "
                    "environment before running this program."
                ) from exc

        self.acl = acl_module
        self.model_path = model_path
        self.device_id = int(device_id)
        self.keep_runtime = bool(keep_runtime)
        self.input_shapes = self._validated_shapes(input_shapes or INPUT_SHAPES, "input")
        self.output_shapes = self._validated_shapes(output_shapes or OUTPUT_SHAPES, "output")
        self._persistent_runtime_key = (id(self.acl), self.device_id)
        self.context = None
        self.model_id = None
        self.model_desc = None
        self.input_dataset = None
        self.output_dataset = None
        self.input_buffers: list[dict[str, Any]] = []
        self.output_buffers: list[dict[str, Any]] = []
        self.input_names: list[str] = []
        self.output_names: list[str] = []
        self._acl_initialized = False
        self._device_set = False
        self._closed = False
        self._lock = threading.Lock()

        try:
            self._initialize()
        except BaseException:
            self.close(suppress_errors=True)
            raise

    @staticmethod
    def _validated_shapes(
        values: Mapping[str, tuple[int, ...]], kind: str
    ) -> dict[str, tuple[int, ...]]:
        shapes = {str(name): tuple(int(value) for value in shape) for name, shape in values.items()}
        if not shapes or any(
            not name or not shape or any(value <= 0 for value in shape)
            for name, shape in shapes.items()
        ):
            raise ValueError(f"OM {kind} shapes must be named positive static dimensions")
        return shapes

    def _initialize(self) -> None:
        if self.keep_runtime:
            self._acquire_persistent_runtime()
        else:
            self._initialize_owned_runtime()

        self.context, ret = self.acl.rt.create_context(self.device_id)
        _check_ret((self.context, ret), "acl.rt.create_context")

        self.model_id, ret = self.acl.mdl.load_from_file(str(self.model_path))
        _check_ret((self.model_id, ret), "acl.mdl.load_from_file")

        self.model_desc = self.acl.mdl.create_desc()
        if self.model_desc is None:
            raise RuntimeError("acl.mdl.create_desc failed")
        _check_ret(
            self.acl.mdl.get_desc(self.model_desc, self.model_id),
            "acl.mdl.get_desc",
        )
        self._prepare_inputs()
        self._prepare_outputs()

    def _initialize_owned_runtime(self) -> None:
        ret = self.acl.init()
        if ret not in (0, 100002):
            _check_ret(ret, "acl.init")
        self._acl_initialized = True

        _check_ret(self.acl.rt.set_device(self.device_id), "acl.rt.set_device")
        self._device_set = True

    def _acquire_persistent_runtime(self) -> None:
        with _PERSISTENT_RUNTIME_LOCK:
            if self._persistent_runtime_key in _PERSISTENT_RUNTIMES:
                return
            ret = self.acl.init()
            if ret not in (0, 100002):
                _check_ret(ret, "acl.init")
            try:
                _check_ret(
                    self.acl.rt.set_device(self.device_id),
                    "acl.rt.set_device",
                )
            except BaseException:
                self.acl.finalize()
                raise
            _PERSISTENT_RUNTIMES[self._persistent_runtime_key] = {
                "acl": self.acl,
                "device_id": self.device_id,
            }

    def _prepare_inputs(self) -> None:
        self.input_dataset = self.acl.mdl.create_dataset()
        if self.input_dataset is None:
            raise RuntimeError("acl.mdl.create_dataset for inputs failed")

        expected = set(self.input_shapes)
        count = int(self.acl.mdl.get_num_inputs(self.model_desc))
        for index in range(count):
            raw_name = str(
                self.acl.mdl.get_input_name_by_index(self.model_desc, index)
            )
            name = _canonical_name(raw_name, expected, "input")
            shape = _tensor_shape(
                self.acl.mdl.get_input_dims(self.model_desc, index),
                f"acl.mdl.get_input_dims[{index}]",
            )
            self._validate_tensor(index, name, shape, self.input_shapes[name], True)
            self.input_names.append(name)
            self.input_buffers.append(self._allocate_buffer(index, True))
        if set(self.input_names) != expected or len(self.input_names) != len(expected):
            raise ValueError(f"Unexpected OM inputs: {self.input_names}")

    def _prepare_outputs(self) -> None:
        self.output_dataset = self.acl.mdl.create_dataset()
        if self.output_dataset is None:
            raise RuntimeError("acl.mdl.create_dataset for outputs failed")

        expected = set(self.output_shapes)
        count = int(self.acl.mdl.get_num_outputs(self.model_desc))
        for index in range(count):
            raw_name = str(
                self.acl.mdl.get_output_name_by_index(self.model_desc, index)
            )
            name = _canonical_name(raw_name, expected, "output")
            shape = _tensor_shape(
                self.acl.mdl.get_output_dims(self.model_desc, index),
                f"acl.mdl.get_output_dims[{index}]",
            )
            self._validate_tensor(index, name, shape, self.output_shapes[name], False)
            self.output_names.append(name)
            buffer = self._allocate_buffer(index, False)
            buffer["host"] = np.empty(self.output_shapes[name], dtype=np.float32)
            self.output_buffers.append(buffer)
        if set(self.output_names) != expected or len(self.output_names) != len(expected):
            raise ValueError(f"Unexpected OM outputs: {self.output_names}")

    def _validate_tensor(
        self,
        index: int,
        name: str,
        actual_shape: tuple[int, ...],
        expected_shape: tuple[int, ...],
        is_input: bool,
    ) -> None:
        kind = "input" if is_input else "output"
        if actual_shape != expected_shape:
            raise ValueError(
                f"Unexpected OM {kind} {name} shape: {actual_shape}, "
                f"expected {expected_shape}"
            )
        dtype_getter = getattr(
            self.acl.mdl,
            f"get_{kind}_data_type",
            None,
        )
        if dtype_getter is not None:
            actual_dtype = dtype_getter(self.model_desc, index)
            acl_float = getattr(self.acl, "ACL_FLOAT", 0)
            if actual_dtype != acl_float:
                raise ValueError(
                    f"Unexpected OM {kind} {name} dtype: {actual_dtype}; "
                    "float32 is required"
                )

    def _allocate_buffer(self, index: int, is_input: bool) -> dict[str, Any]:
        kind = "input" if is_input else "output"
        size_getter = getattr(self.acl.mdl, f"get_{kind}_size_by_index")
        size = int(size_getter(self.model_desc, index))
        names = self.input_names if is_input else self.output_names
        shapes = self.input_shapes if is_input else self.output_shapes
        expected_size = int(np.prod(shapes[names[index]])) * np.dtype(np.float32).itemsize
        if size != expected_size:
            raise ValueError(
                f"Unexpected OM {kind} {names[index]} byte size: {size}, "
                f"expected {expected_size}"
            )

        pointer, ret = self.acl.rt.malloc(size, ACL_MEM_MALLOC_HUGE_FIRST)
        _check_ret((pointer, ret), f"acl.rt.malloc {kind}[{index}]")
        data_buffer = self.acl.create_data_buffer(pointer, size)
        if data_buffer is None:
            self.acl.rt.free(pointer)
            raise RuntimeError(f"acl.create_data_buffer {kind}[{index}] failed")
        dataset = self.input_dataset if is_input else self.output_dataset
        try:
            _check_ret(
                self.acl.mdl.add_dataset_buffer(dataset, data_buffer),
                f"acl.mdl.add_dataset_buffer {kind}[{index}]",
            )
        except BaseException:
            self.acl.destroy_data_buffer(data_buffer)
            self.acl.rt.free(pointer)
            raise
        return {"ptr": pointer, "size": size, "buffer": data_buffer}

    def infer(self, inputs: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
        if self._closed:
            raise RuntimeError("PyACL model runner is closed")
        if set(inputs) != set(self.input_shapes):
            raise ValueError(f"OM inputs must be exactly {sorted(self.input_shapes)}")

        prepared: dict[str, np.ndarray] = {}
        for name, shape in self.input_shapes.items():
            array = np.ascontiguousarray(inputs[name], dtype=np.float32)
            if array.shape != shape:
                raise ValueError(
                    f"Unexpected input {name} shape: {array.shape}, expected {shape}"
                )
            if not np.all(np.isfinite(array)):
                raise ValueError(f"Input {name} contains NaN or Inf")
            prepared[name] = array

        with self._lock:
            _check_ret(self.acl.rt.set_context(self.context), "acl.rt.set_context")
            for index, name in enumerate(self.input_names):
                array = prepared[name]
                input_bytes = array.tobytes()
                buffer = self.input_buffers[index]
                host_pointer = self.acl.util.bytes_to_ptr(input_bytes)
                _check_ret(
                    self.acl.rt.memcpy(
                        buffer["ptr"],
                        buffer["size"],
                        host_pointer,
                        len(input_bytes),
                        ACL_MEMCPY_HOST_TO_DEVICE,
                    ),
                    f"acl.rt.memcpy host_to_device input[{index}]",
                )

            _check_ret(
                self.acl.mdl.execute(
                    self.model_id, self.input_dataset, self.output_dataset
                ),
                "acl.mdl.execute",
            )

            outputs: dict[str, np.ndarray] = {}
            for index, name in enumerate(self.output_names):
                buffer = self.output_buffers[index]
                host = buffer["host"]
                _check_ret(
                    self.acl.rt.memcpy(
                        host.ctypes.data,
                        buffer["size"],
                        buffer["ptr"],
                        buffer["size"],
                        ACL_MEMCPY_DEVICE_TO_HOST,
                    ),
                    f"acl.rt.memcpy device_to_host output[{index}]",
                )
                if not np.all(np.isfinite(host)):
                    raise RuntimeError(f"OM output {name} contains NaN or Inf")
                outputs[name] = host.copy()
            return outputs

    def close(self, *, suppress_errors: bool = False) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[BaseException] = []

        def cleanup(operation: Any, *args: Any) -> None:
            try:
                _check_ret(operation(*args), operation.__name__)
            except BaseException as exc:
                errors.append(exc)

        if self.input_dataset is not None:
            for buffer in self.input_buffers:
                cleanup(self.acl.destroy_data_buffer, buffer["buffer"])
                cleanup(self.acl.rt.free, buffer["ptr"])
            cleanup(self.acl.mdl.destroy_dataset, self.input_dataset)
            self.input_dataset = None
            self.input_buffers.clear()

        if self.output_dataset is not None:
            for buffer in self.output_buffers:
                cleanup(self.acl.destroy_data_buffer, buffer["buffer"])
                cleanup(self.acl.rt.free, buffer["ptr"])
            cleanup(self.acl.mdl.destroy_dataset, self.output_dataset)
            self.output_dataset = None
            self.output_buffers.clear()

        if self.model_desc is not None:
            cleanup(self.acl.mdl.destroy_desc, self.model_desc)
            self.model_desc = None
        if self.model_id is not None:
            cleanup(self.acl.mdl.unload, self.model_id)
            self.model_id = None
        if self.context is not None:
            cleanup(self.acl.rt.destroy_context, self.context)
            self.context = None
        if self._device_set and not self.keep_runtime:
            cleanup(self.acl.rt.reset_device, self.device_id)
            self._device_set = False
        if self._acl_initialized and not self.keep_runtime:
            cleanup(self.acl.finalize)
            self._acl_initialized = False

        if errors and not suppress_errors:
            details = "; ".join(str(error) for error in errors)
            raise RuntimeError(f"PyACL cleanup failed: {details}")

    def __enter__(self) -> "PyAclModelRunner":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close(suppress_errors=True)
        except BaseException:
            pass


def shutdown_persistent_runtimes(*, suppress_errors: bool = False) -> None:
    """Release process-level ACL runtimes after all long-lived sessions stop."""
    with _PERSISTENT_RUNTIME_LOCK:
        states = list(_PERSISTENT_RUNTIMES.values())
        _PERSISTENT_RUNTIMES.clear()
    errors: list[BaseException] = []
    for state in states:
        acl = state["acl"]
        device_id = int(state["device_id"])
        for operation, argument in (
            (acl.rt.reset_device, device_id),
            (acl.finalize, None),
        ):
            try:
                result = operation() if argument is None else operation(argument)
                _check_ret(result, operation.__name__)
            except BaseException as exc:
                errors.append(exc)
    if errors and not suppress_errors:
        raise RuntimeError(
            "Persistent PyACL cleanup failed: "
            + "; ".join(str(error) for error in errors)
        )
