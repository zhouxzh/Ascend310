"""Direct PyACL runners for the static MIDI-DDSP ONNX-to-OM contracts."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any

import numpy as np


ACL_MEM_MALLOC_HUGE_FIRST = 0
ACL_MEMCPY_HOST_TO_DEVICE = 1
ACL_MEMCPY_DEVICE_TO_HOST = 2

_RUNTIME_LOCK = threading.Lock()
_RUNTIME_STATE: dict[tuple[int, int], dict[str, Any]] = {}


@dataclass(frozen=True)
class TensorSpec:
    name: str
    shape: tuple[int, ...]
    dtype: np.dtype


EXPRESSION_INPUTS = (
    TensorSpec("note_pitch", (1, 32), np.dtype(np.int64)),
    TensorSpec("note_length", (1, 32, 1), np.dtype(np.float32)),
    TensorSpec("instrument_id", (1,), np.dtype(np.int64)),
)
EXPRESSION_OUTPUTS = (
    TensorSpec("expression_controls", (1, 32, 6), np.dtype(np.float32)),
)
SYNTHESIS_INPUTS = (
    TensorSpec("volume", (1, 64, 1), np.dtype(np.float32)),
    TensorSpec("vol_fluc", (1, 64, 1), np.dtype(np.float32)),
    TensorSpec("vibrato", (1, 64, 1), np.dtype(np.float32)),
    TensorSpec("brightness", (1, 64, 1), np.dtype(np.float32)),
    TensorSpec("attack", (1, 64, 1), np.dtype(np.float32)),
    TensorSpec("vol_peak_pos", (1, 64, 1), np.dtype(np.float32)),
    TensorSpec("q_pitch", (1, 64, 1), np.dtype(np.float32)),
    TensorSpec("onsets", (1, 64), np.dtype(np.int64)),
    TensorSpec("offsets", (1, 64), np.dtype(np.int64)),
    TensorSpec("instrument_id", (1,), np.dtype(np.int64)),
)
SYNTHESIS_OUTPUTS = (
    TensorSpec("f0_hz", (1, 64, 1), np.dtype(np.float32)),
    TensorSpec("amplitudes", (1, 64, 1), np.dtype(np.float32)),
    TensorSpec("harmonic_distribution", (1, 64, 60), np.dtype(np.float32)),
    TensorSpec("noise_magnitudes", (1, 64, 65), np.dtype(np.float32)),
)


def _check_ret(result: Any, operation: str) -> None:
    ret = result[-1] if isinstance(result, tuple) else result
    if not isinstance(ret, int):
        raise RuntimeError(f"{operation} returned non-integer status: {ret!r}")
    if ret != 0:
        raise RuntimeError(f"{operation} failed, ret={ret} (0x{ret:X})")


def _tensor_shape(result: Any, operation: str) -> tuple[int, ...]:
    _check_ret(result, operation)
    info = result[0]
    count = int(info.get("dimCount", 0))
    dims = tuple(int(value) for value in info.get("dims", [])[:count])
    if count <= 0 or len(dims) != count or any(value <= 0 for value in dims):
        raise ValueError(f"{operation} returned invalid dimensions: {info!r}")
    return dims


def _canonical_name(raw_name: str, expected: set[str]) -> str:
    matches = [name for name in expected if name in raw_name]
    if len(matches) != 1:
        raise ValueError(f"Cannot map OM input name {raw_name!r}")
    return matches[0]


class MidiDdspAclRunner:
    """Own one ACL context and execute one fixed MIDI-DDSP OM."""

    def __init__(
        self,
        model_path: Path,
        inputs: tuple[TensorSpec, ...],
        outputs: tuple[TensorSpec, ...],
        device_id: int = 0,
        *,
        acl_module: Any | None = None,
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
                    "PyACL is required. Activate the existing CANN environment first."
                ) from exc

        self.acl = acl_module
        self.model_path = model_path
        self.input_specs = inputs
        self.output_specs = outputs
        self.input_by_name = {spec.name: spec for spec in inputs}
        self.output_by_name = {spec.name: spec for spec in outputs}
        self.device_id = int(device_id)
        self.context = None
        self.model_id = None
        self.model_desc = None
        self.input_dataset = None
        self.output_dataset = None
        self.input_buffers: list[dict[str, Any]] = []
        self.output_buffers: list[dict[str, Any]] = []
        self.input_names: list[str] = []
        self.output_names: list[str] = []
        self.input_descriptors: list[dict[str, Any]] = []
        self.output_descriptors: list[dict[str, Any]] = []
        self._runtime_key = (id(self.acl), self.device_id)
        self._runtime_acquired = False
        self._closed = False
        self._lock = threading.Lock()
        try:
            self._initialize()
        except BaseException:
            self.close(suppress_errors=True)
            raise

    def _initialize(self) -> None:
        self._acquire_runtime()
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

    def _acquire_runtime(self) -> None:
        with _RUNTIME_LOCK:
            state = _RUNTIME_STATE.get(self._runtime_key)
            if state is None:
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
                state = {"count": 0, "acl": self.acl}
                _RUNTIME_STATE[self._runtime_key] = state
            state["count"] += 1
            self._runtime_acquired = True

    def _release_runtime(self, errors: list[BaseException]) -> None:
        if not self._runtime_acquired:
            return
        with _RUNTIME_LOCK:
            state = _RUNTIME_STATE.get(self._runtime_key)
            self._runtime_acquired = False
            if state is None:
                return
            state["count"] -= 1
            if state["count"] > 0:
                return
            _RUNTIME_STATE.pop(self._runtime_key, None)
            for operation, value in (
                (self.acl.rt.reset_device, self.device_id),
                (self.acl.finalize, None),
            ):
                try:
                    result = operation() if value is None else operation(value)
                    _check_ret(result, operation.__name__)
                except BaseException as exc:
                    errors.append(exc)

    def _check_dtype(self, index: int, expected: np.dtype, is_input: bool) -> None:
        kind = "input" if is_input else "output"
        getter = getattr(self.acl.mdl, f"get_{kind}_data_type", None)
        if getter is None:
            return
        actual = getter(self.model_desc, index)
        constant_name = "ACL_FLOAT" if expected == np.dtype(np.float32) else "ACL_INT64"
        expected_code = getattr(self.acl, constant_name, None)
        if expected_code is not None and actual != expected_code:
            raise ValueError(
                f"Unexpected OM {kind} dtype at index {index}: {actual}; "
                f"expected {constant_name} ({expected_code})"
            )

    def _prepare_inputs(self) -> None:
        self.input_dataset = self.acl.mdl.create_dataset()
        if self.input_dataset is None:
            raise RuntimeError("acl.mdl.create_dataset for inputs failed")
        expected = set(self.input_by_name)
        count = int(self.acl.mdl.get_num_inputs(self.model_desc))
        for index in range(count):
            raw_name = str(self.acl.mdl.get_input_name_by_index(self.model_desc, index))
            name = _canonical_name(raw_name, expected)
            spec = self.input_by_name[name]
            shape = _tensor_shape(
                self.acl.mdl.get_input_dims(self.model_desc, index),
                f"acl.mdl.get_input_dims[{index}]",
            )
            if shape != spec.shape:
                raise ValueError(
                    f"Unexpected OM input {name} shape: {shape}, expected {spec.shape}"
                )
            self._check_dtype(index, spec.dtype, True)
            self.input_names.append(name)
            self.input_descriptors.append(
                {"name": raw_name, "logical_name": name, "shape": list(shape)}
            )
            self.input_buffers.append(self._allocate_buffer(index, spec, True))
        if self.input_names != [spec.name for spec in self.input_specs]:
            raise ValueError(f"Unexpected OM input order: {self.input_names}")

    def _prepare_outputs(self) -> None:
        self.output_dataset = self.acl.mdl.create_dataset()
        if self.output_dataset is None:
            raise RuntimeError("acl.mdl.create_dataset for outputs failed")
        count = int(self.acl.mdl.get_num_outputs(self.model_desc))
        if count != len(self.output_specs):
            raise ValueError(f"Unexpected OM output count: {count}")
        for index, spec in enumerate(self.output_specs):
            raw_name = str(self.acl.mdl.get_output_name_by_index(self.model_desc, index))
            shape = _tensor_shape(
                self.acl.mdl.get_output_dims(self.model_desc, index),
                f"acl.mdl.get_output_dims[{index}]",
            )
            if shape != spec.shape:
                raise ValueError(
                    f"Unexpected OM output {spec.name} shape: {shape}, expected {spec.shape}"
                )
            self._check_dtype(index, spec.dtype, False)
            self.output_names.append(spec.name)
            self.output_descriptors.append(
                {"name": raw_name, "logical_name": spec.name, "shape": list(shape)}
            )
            self.output_buffers.append(self._allocate_buffer(index, spec, False))

    def _allocate_buffer(
        self, index: int, spec: TensorSpec, is_input: bool
    ) -> dict[str, Any]:
        kind = "input" if is_input else "output"
        size = int(getattr(self.acl.mdl, f"get_{kind}_size_by_index")(self.model_desc, index))
        expected_size = int(np.prod(spec.shape)) * spec.dtype.itemsize
        if size != expected_size:
            raise ValueError(
                f"Unexpected OM {kind} {spec.name} byte size: {size}, "
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
        buffer = {"ptr": pointer, "size": size, "buffer": data_buffer}
        if not is_input:
            buffer["host"] = np.empty(spec.shape, dtype=spec.dtype)
        return buffer

    def infer(self, inputs: Mapping[str, np.ndarray]) -> dict[str, np.ndarray]:
        if self._closed:
            raise RuntimeError("MIDI-DDSP PyACL runner is closed")
        if set(inputs) != set(self.input_by_name):
            raise ValueError(f"OM inputs must be exactly {sorted(self.input_by_name)}")
        prepared: dict[str, np.ndarray] = {}
        for spec in self.input_specs:
            array = np.ascontiguousarray(inputs[spec.name], dtype=spec.dtype)
            if array.shape != spec.shape:
                raise ValueError(
                    f"Unexpected input {spec.name} shape: {array.shape}, expected {spec.shape}"
                )
            if spec.dtype.kind == "f" and not np.all(np.isfinite(array)):
                raise ValueError(f"Input {spec.name} contains NaN or Inf")
            prepared[spec.name] = array

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
                self.acl.mdl.execute(self.model_id, self.input_dataset, self.output_dataset),
                "acl.mdl.execute",
            )
            result: dict[str, np.ndarray] = {}
            for index, spec in enumerate(self.output_specs):
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
                    raise RuntimeError(f"OM output {spec.name} contains NaN or Inf")
                result[spec.name] = host.copy()
            return result

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

        for buffers, dataset in (
            (self.input_buffers, self.input_dataset),
            (self.output_buffers, self.output_dataset),
        ):
            for buffer in buffers:
                cleanup(self.acl.destroy_data_buffer, buffer["buffer"])
                cleanup(self.acl.rt.free, buffer["ptr"])
            if dataset is not None:
                cleanup(self.acl.mdl.destroy_dataset, dataset)
        self.input_buffers.clear()
        self.output_buffers.clear()
        self.input_dataset = None
        self.output_dataset = None
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

    def __enter__(self) -> "MidiDdspAclRunner":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close(suppress_errors=True)
        except BaseException:
            pass
