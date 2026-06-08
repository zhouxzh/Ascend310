from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import numpy as np


ACL_MEM_MALLOC_HUGE_FIRST = 0
ACL_MEMCPY_HOST_TO_DEVICE = 1
ACL_MEMCPY_DEVICE_TO_HOST = 2

acl: Any | None = None


def import_acl():
    global acl
    if acl is None:
        import acl as acl_module

        acl = acl_module
    return acl


def _ret_code(result: Any) -> int:
    if isinstance(result, tuple):
        result = result[-1]
    if not isinstance(result, int):
        raise RuntimeError(f"ACL call returned non-integer status: {result!r}")
    return result


def check_ret(result: Any, message: str) -> int:
    ret = _ret_code(result)
    if ret == 0:
        return ret
    acl_module = import_acl()
    recent = ""
    if hasattr(acl_module, "get_recent_err_msg"):
        recent = f", recent_error={acl_module.get_recent_err_msg()}"
    raise RuntimeError(f"{message} failed, ret={ret}{recent}")


def _dataset_buffer(dataset: Any, ptr: int, size: int) -> Any:
    acl_module = import_acl()
    data_buffer = acl_module.create_data_buffer(ptr, size)
    ret = acl_module.mdl.add_dataset_buffer(dataset, data_buffer)
    check_ret(ret, "acl.mdl.add_dataset_buffer")
    return data_buffer


class AclSession:
    def __init__(self, device_id: int = 0) -> None:
        self.acl = import_acl()
        self.device_id = int(device_id)
        self.context = None
        self._initialized = False

    def __enter__(self) -> "AclSession":
        ret = self.acl.init()
        if ret not in (0, 100002):
            check_ret(ret, "acl.init")
        self._initialized = True

        ret = self.acl.rt.set_device(self.device_id)
        check_ret(ret, "acl.rt.set_device")

        self.context, ret = self.acl.rt.create_context(self.device_id)
        check_ret(ret, "acl.rt.create_context")
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.context is not None:
            self.acl.rt.destroy_context(self.context)
            self.context = None
        if self._initialized:
            self.acl.rt.reset_device(self.device_id)
            self.acl.finalize()
            self._initialized = False


class BaseResNetRunner:
    def __init__(self, model_path: str | Path, input_dtype: np.dtype | type | None = np.float32) -> None:
        self.acl = import_acl()
        self.model_path = str(model_path)
        self.input_dtype = input_dtype
        self.model_id = None
        self.model_desc = None
        self.output_shapes: list[tuple[int, ...] | None] = []
        self.input_size = 0
        self.output_sizes: list[int] = []
        self._load_model()

    def _load_model(self) -> None:
        if not Path(self.model_path).exists():
            raise FileNotFoundError(
                f"OM model not found: {self.model_path}. "
                "Run `python3 samples/chapter7/tools/download_model.py` first, or pass --model explicitly."
            )
        self.model_id, ret = self.acl.mdl.load_from_file(self.model_path)
        check_ret(ret, "acl.mdl.load_from_file")

        self.model_desc = self.acl.mdl.create_desc()
        ret = self.acl.mdl.get_desc(self.model_desc, self.model_id)
        check_ret(ret, "acl.mdl.get_desc")

        self.input_size = int(self.acl.mdl.get_input_size_by_index(self.model_desc, 0))
        output_count = int(self.acl.mdl.get_num_outputs(self.model_desc))
        self.output_sizes = [
            int(self.acl.mdl.get_output_size_by_index(self.model_desc, idx)) for idx in range(output_count)
        ]
        self.output_shapes = [self._get_output_shape(idx) for idx in range(output_count)]

    def _get_output_shape(self, index: int) -> tuple[int, ...] | None:
        dims_info, ret = self.acl.mdl.get_output_dims(self.model_desc, index)
        if ret != 0:
            return None
        dim_count = int(dims_info.get("dimCount", 0))
        dims = dims_info.get("dims", [])
        if dim_count <= 0 or not dims:
            return None
        shape = tuple(int(value) for value in dims[:dim_count])
        return shape if int(np.prod(shape)) > 0 else None

    def _copy_output(self, output_ptr: int, output_size: int, shape: tuple[int, ...] | None) -> np.ndarray:
        host_ptr, ret = self.acl.rt.malloc_host(output_size)
        check_ret(ret, "acl.rt.malloc_host output")
        try:
            ret = self.acl.rt.memcpy(
                host_ptr,
                output_size,
                output_ptr,
                output_size,
                ACL_MEMCPY_DEVICE_TO_HOST,
            )
            check_ret(ret, "acl.rt.memcpy device_to_host")
            output_bytes = self.acl.util.ptr_to_bytes(host_ptr, output_size)
            tensor = np.frombuffer(output_bytes, dtype=np.float32).copy()
            if shape is not None and int(np.prod(shape)) == tensor.size:
                tensor = tensor.reshape(shape)
            return tensor
        finally:
            self.acl.rt.free_host(host_ptr)

    def _prepare_input(self, input_np: np.ndarray) -> np.ndarray:
        if self.input_dtype is None:
            prepared = np.ascontiguousarray(input_np)
        else:
            prepared = np.ascontiguousarray(input_np.astype(self.input_dtype, copy=False))
        if prepared.nbytes != self.input_size:
            raise ValueError(f"Input bytes {prepared.nbytes} != model input bytes {self.input_size}")
        return prepared

    def infer(self, input_np: np.ndarray) -> tuple[list[np.ndarray], dict[str, float]]:
        raise NotImplementedError

    def release(self) -> None:
        if self.model_desc is not None:
            self.acl.mdl.destroy_desc(self.model_desc)
            self.model_desc = None
        if self.model_id is not None:
            self.acl.mdl.unload(self.model_id)
            self.model_id = None


class NaiveResNetRunner(BaseResNetRunner):
    def infer(self, input_np: np.ndarray) -> tuple[list[np.ndarray], dict[str, float]]:
        input_np = self._prepare_input(input_np)

        timings: dict[str, float] = {}

        t0 = time.perf_counter()
        input_ptr, ret = self.acl.rt.malloc(self.input_size, ACL_MEM_MALLOC_HUGE_FIRST)
        check_ret(ret, "acl.rt.malloc input")
        output_ptrs: list[int] = []
        for output_size in self.output_sizes:
            output_ptr, ret = self.acl.rt.malloc(output_size, ACL_MEM_MALLOC_HUGE_FIRST)
            check_ret(ret, "acl.rt.malloc output")
            output_ptrs.append(output_ptr)

        input_dataset = self.acl.mdl.create_dataset()
        output_dataset = self.acl.mdl.create_dataset()
        input_buffer = _dataset_buffer(input_dataset, input_ptr, self.input_size)
        output_buffers = [
            _dataset_buffer(output_dataset, ptr, size) for ptr, size in zip(output_ptrs, self.output_sizes)
        ]
        timings["alloc_dataset"] = (time.perf_counter() - t0) * 1000.0

        try:
            t0 = time.perf_counter()
            input_bytes = input_np.tobytes()
            host_input_ptr = self.acl.util.bytes_to_ptr(input_bytes)
            ret = self.acl.rt.memcpy(
                input_ptr,
                self.input_size,
                host_input_ptr,
                len(input_bytes),
                ACL_MEMCPY_HOST_TO_DEVICE,
            )
            check_ret(ret, "acl.rt.memcpy host_to_device")
            timings["h2d"] = (time.perf_counter() - t0) * 1000.0

            t0 = time.perf_counter()
            ret = self.acl.mdl.execute(self.model_id, input_dataset, output_dataset)
            check_ret(ret, "acl.mdl.execute")
            timings["execute"] = (time.perf_counter() - t0) * 1000.0

            t0 = time.perf_counter()
            outputs = [
                self._copy_output(ptr, size, shape)
                for ptr, size, shape in zip(output_ptrs, self.output_sizes, self.output_shapes)
            ]
            timings["d2h"] = (time.perf_counter() - t0) * 1000.0
            return outputs, timings
        finally:
            t0 = time.perf_counter()
            self.acl.destroy_data_buffer(input_buffer)
            for output_buffer in output_buffers:
                self.acl.destroy_data_buffer(output_buffer)
            self.acl.mdl.destroy_dataset(input_dataset)
            self.acl.mdl.destroy_dataset(output_dataset)
            self.acl.rt.free(input_ptr)
            for output_ptr in output_ptrs:
                self.acl.rt.free(output_ptr)
            timings["free_dataset"] = (time.perf_counter() - t0) * 1000.0


class ReuseResNetRunner(BaseResNetRunner):
    def __init__(self, model_path: str | Path, input_dtype: np.dtype | type | None = np.float32) -> None:
        super().__init__(model_path, input_dtype=input_dtype)
        self.input_dataset = None
        self.output_dataset = None
        self.input_ptr = None
        self.input_buffer = None
        self.output_ptrs: list[int] = []
        self.output_buffers: list[Any] = []
        self._prepare_buffers()

    def _prepare_buffers(self) -> None:
        self.input_ptr, ret = self.acl.rt.malloc(self.input_size, ACL_MEM_MALLOC_HUGE_FIRST)
        check_ret(ret, "acl.rt.malloc reusable input")

        self.input_dataset = self.acl.mdl.create_dataset()
        self.output_dataset = self.acl.mdl.create_dataset()
        self.input_buffer = _dataset_buffer(self.input_dataset, self.input_ptr, self.input_size)

        for output_size in self.output_sizes:
            output_ptr, ret = self.acl.rt.malloc(output_size, ACL_MEM_MALLOC_HUGE_FIRST)
            check_ret(ret, "acl.rt.malloc reusable output")
            output_buffer = _dataset_buffer(self.output_dataset, output_ptr, output_size)
            self.output_ptrs.append(output_ptr)
            self.output_buffers.append(output_buffer)

    def infer(self, input_np: np.ndarray) -> tuple[list[np.ndarray], dict[str, float]]:
        input_np = self._prepare_input(input_np)

        timings: dict[str, float] = {}

        t0 = time.perf_counter()
        input_bytes = input_np.tobytes()
        host_input_ptr = self.acl.util.bytes_to_ptr(input_bytes)
        ret = self.acl.rt.memcpy(
            self.input_ptr,
            self.input_size,
            host_input_ptr,
            len(input_bytes),
            ACL_MEMCPY_HOST_TO_DEVICE,
        )
        check_ret(ret, "acl.rt.memcpy host_to_device")
        timings["h2d"] = (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        ret = self.acl.mdl.execute(self.model_id, self.input_dataset, self.output_dataset)
        check_ret(ret, "acl.mdl.execute")
        timings["execute"] = (time.perf_counter() - t0) * 1000.0

        t0 = time.perf_counter()
        outputs = [
            self._copy_output(ptr, size, shape)
            for ptr, size, shape in zip(self.output_ptrs, self.output_sizes, self.output_shapes)
        ]
        timings["d2h"] = (time.perf_counter() - t0) * 1000.0
        return outputs, timings

    def release(self) -> None:
        if self.input_dataset is not None:
            if self.input_buffer is not None:
                self.acl.destroy_data_buffer(self.input_buffer)
                self.input_buffer = None
            self.acl.mdl.destroy_dataset(self.input_dataset)
            self.input_dataset = None

        if self.output_dataset is not None:
            for output_buffer in self.output_buffers:
                self.acl.destroy_data_buffer(output_buffer)
            self.output_buffers = []
            self.acl.mdl.destroy_dataset(self.output_dataset)
            self.output_dataset = None

        if self.input_ptr is not None:
            self.acl.rt.free(self.input_ptr)
            self.input_ptr = None
        for output_ptr in self.output_ptrs:
            self.acl.rt.free(output_ptr)
        self.output_ptrs = []
        super().release()
