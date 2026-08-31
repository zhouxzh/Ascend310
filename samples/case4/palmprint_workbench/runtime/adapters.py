"""Ascend OM adapter and process-owned ACL lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
import time
from typing import Any

import cv2
import numpy as np

from ..config import NPU_DEVICE_ID
from ..domain.registry import ModelSpec


def l2_normalize(vector: np.ndarray) -> np.ndarray:
    value = np.asarray(vector, dtype=np.float32).reshape(-1)
    if not np.all(np.isfinite(value)):
        raise ValueError("Model output contains NaN or infinity")
    norm = float(np.linalg.norm(value))
    if norm <= 1e-12:
        raise ValueError("Model output has zero norm")
    return np.ascontiguousarray(value / norm, dtype=np.float32)


def preprocess_embedding_roi(roi: np.ndarray, mode: str) -> np.ndarray:
    gray = np.asarray(roi, dtype=np.uint8)
    if gray.shape != (128, 128):
        gray = cv2.resize(gray, (128, 128), interpolation=cv2.INTER_AREA)
    tensor = gray.astype(np.float32) / 255.0
    if mode == "nonzero_standardize":
        mask = tensor > 0
        if np.any(mask):
            values = tensor[mask]
            std = max(float(values.std()), 1e-6)
            tensor[mask] = (values - float(values.mean())) / std
    elif mode != "zero_one":
        raise ValueError(f"Unsupported embedding preprocessing: {mode}")
    return np.ascontiguousarray(tensor[None, None, :, :], dtype=np.float32)


@dataclass(frozen=True)
class EncodeResult:
    code: np.ndarray
    preprocess_ms: float
    inference_ms: float


class PalmAdapter:
    backend = "unknown"

    def __init__(self, spec: ModelSpec) -> None:
        self.spec = spec

    def preprocess(self, roi: np.ndarray) -> Any:
        raise NotImplementedError

    def encode_preprocessed(self, value: Any) -> np.ndarray:
        raise NotImplementedError

    def encode(self, roi: np.ndarray) -> EncodeResult:
        started = time.perf_counter_ns()
        value = self.preprocess(roi)
        prepared = time.perf_counter_ns()
        code = self.encode_preprocessed(value)
        finished = time.perf_counter_ns()
        return EncodeResult(code, (prepared - started) / 1e6, (finished - prepared) / 1e6)

    def compare(self, query: np.ndarray, references: np.ndarray) -> np.ndarray:
        raise NotImplementedError

    def health(self) -> dict[str, Any]:
        return {"ready": True, "backend": self.backend, "message": "ready"}

    def close(self) -> None:
        return None


def _acl_result_code(result: Any, *, none_is_success: bool = False) -> int:
    """Extract the ACL return code without assuming every API returns a tuple."""

    value = result[-1] if isinstance(result, tuple) else result
    if value is None and none_is_success:
        return 0
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"ACL returned an invalid status: {result!r}") from exc


def _acl_check(result: Any, operation: str) -> Any:
    value = result[0] if isinstance(result, tuple) else None
    ret = _acl_result_code(result)
    if ret != 0:
        raise RuntimeError(f"{operation} failed: {ret}")
    return value


def _acl_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


class _AclEnvironment:
    """Process-owned PyACL runtime with explicit, reference-safe shutdown.

    ``acl.init`` and ``acl.rt.set_device`` are process-wide operations.  The
    old atexit hook could reset the device while cached OM runners still owned
    a context.  This coordinator records ownership and only resets/finalizes
    a runtime that this process successfully initialized after every runner
    has released its resources.
    """

    lock = threading.RLock()
    acl: Any | None = None
    initialized = False
    owns_runtime = False
    device_set = False
    device_reset = False
    active_runners = 0
    events: list[dict[str, Any]] = []
    last_shutdown: dict[str, Any] | None = None

    @classmethod
    def _record(cls, phase: str, *, ok: bool, **details: Any) -> dict[str, Any]:
        event = {"timestamp": _acl_timestamp(), "phase": phase, "ok": bool(ok), **details}
        cls.events.append(event)
        # Keep diagnostics bounded for a long-running FastAPI process.
        if len(cls.events) > 128:
            del cls.events[:-128]
        return event

    @classmethod
    def _cleanup_call(cls, phase: str, operation: Any, *args: Any) -> dict[str, Any]:
        try:
            result = operation(*args)
            ret = _acl_result_code(result, none_is_success=True)
            return cls._record(phase, ok=ret == 0, return_code=ret)
        except BaseException as exc:
            return cls._record(phase, ok=False, error=f"{type(exc).__name__}: {exc}")

    @classmethod
    def acquire_runner(cls) -> Any:
        """Initialize the runtime once and reserve it for a new OM runner."""

        with cls.lock:
            if cls.acl is None:
                try:
                    import acl
                except ImportError as exc:
                    cls._record("acl_import", ok=False, error=f"{type(exc).__name__}: {exc}")
                    raise RuntimeError("PyACL is unavailable; source the CANN environment") from exc
                cls.acl = acl

            if not cls.initialized:
                try:
                    init_result = cls.acl.init()
                except BaseException as exc:
                    cls._record("acl_init", ok=False, error=f"{type(exc).__name__}: {exc}")
                    raise
                init_ret = _acl_result_code(init_result)
                if init_ret not in (0, 100002):
                    cls._record("acl_init", ok=False, return_code=init_ret)
                    raise RuntimeError(f"acl.init failed: {init_ret}")
                owns_runtime = init_ret == 0
                cls._record(
                    "acl_init",
                    ok=True,
                    return_code=init_ret,
                    ownership="process" if owns_runtime else "shared",
                )
                try:
                    _acl_check(cls.acl.rt.set_device(NPU_DEVICE_ID), "acl.rt.set_device")
                except BaseException as exc:
                    cls._record("acl_set_device", ok=False, error=f"{type(exc).__name__}: {exc}")
                    if owns_runtime:
                        cls._cleanup_call("acl_finalize_after_set_device_failure", cls.acl.finalize)
                    raise
                cls.initialized = True
                cls.owns_runtime = owns_runtime
                cls.device_set = True
                cls.device_reset = False
                cls._record("acl_set_device", ok=True, device_id=NPU_DEVICE_ID)

            cls.active_runners += 1
            cls._record("runner_acquired", ok=True, active_runners=cls.active_runners)
            return cls.acl

    @classmethod
    def release_runner(cls) -> None:
        with cls.lock:
            if cls.active_runners <= 0:
                cls._record("runner_release", ok=False, error="runner reference count underflow")
                return
            cls.active_runners -= 1
            cls._record("runner_released", ok=True, active_runners=cls.active_runners)

    @classmethod
    def status(cls) -> dict[str, Any]:
        with cls.lock:
            return {
                "initialized": cls.initialized,
                "owns_runtime": cls.owns_runtime,
                "device_set": cls.device_set,
                "device_reset": cls.device_reset,
                "active_runners": cls.active_runners,
                "last_shutdown": cls.last_shutdown,
                "recent_events": list(cls.events[-16:]),
            }

    @classmethod
    def shutdown(cls) -> dict[str, Any]:
        """Explicitly release a process-owned runtime after all runners close.

        It never resets/finalizes a runtime initialized by another owner and
        is safe to call repeatedly.  A live runner blocks shutdown instead of
        risking a reset beneath an active ACL context.
        """

        with cls.lock:
            result: dict[str, Any] = {
                "timestamp": _acl_timestamp(),
                "initialized": cls.initialized,
                "owns_runtime": cls.owns_runtime,
                "active_runners": cls.active_runners,
                "steps": [],
            }
            if not cls.initialized or cls.acl is None:
                result.update({"ok": True, "status": "not_initialized"})
                cls.last_shutdown = result
                cls._record("runtime_shutdown", ok=True, status="not_initialized")
                return result
            if cls.active_runners:
                result.update({"ok": False, "status": "blocked_active_runners"})
                cls.last_shutdown = result
                cls._record(
                    "runtime_shutdown", ok=False, status="blocked_active_runners", active_runners=cls.active_runners
                )
                return result
            if not cls.owns_runtime:
                cls.initialized = False
                cls.device_set = False
                cls.device_reset = False
                result.update({"ok": True, "status": "shared_runtime_left_intact"})
                cls.last_shutdown = result
                cls._record("runtime_shutdown", ok=True, status="shared_runtime_left_intact")
                return result

            reset_ok = True
            if cls.device_set and not cls.device_reset:
                reset = cls._cleanup_call("acl_reset_device", cls.acl.rt.reset_device, NPU_DEVICE_ID)
                result["steps"].append(reset)
                if reset["ok"]:
                    cls.device_reset = True
                    cls.device_set = False
                else:
                    reset_ok = False
            if cls.owns_runtime and reset_ok:
                finalize = cls._cleanup_call("acl_finalize", cls.acl.finalize)
                result["steps"].append(finalize)
                if finalize["ok"]:
                    cls.initialized = False
                    cls.owns_runtime = False
                    cls.device_set = False
                    cls.device_reset = False
            elif cls.owns_runtime and not reset_ok:
                blocked = cls._record(
                    "acl_finalize",
                    ok=False,
                    status="blocked_by_reset_failure",
                )
                result["steps"].append(blocked)

            result["ok"] = all(item["ok"] for item in result["steps"])
            result["status"] = "released" if result["ok"] else "cleanup_failed"
            cls.last_shutdown = result
            cls._record("runtime_shutdown", ok=result["ok"], status=result["status"])
            return result


def shutdown_acl_runtime() -> dict[str, Any]:
    """Public explicit shutdown hook for service and benchmark owners."""

    return _AclEnvironment.shutdown()


def acl_runtime_status() -> dict[str, Any]:
    """Return bounded lifecycle diagnostics without touching the NPU."""

    return _AclEnvironment.status()


class _OmRunner:
    """One-input/one-output static OM runner with transactional allocation."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.acl: Any | None = None
        self.context: Any | None = None
        self.model_id: Any | None = None
        self.desc: Any | None = None
        self.input_dataset: Any | None = None
        self.output_dataset: Any | None = None
        self.input_buffer: dict[str, Any] | None = None
        self.output_buffer: dict[str, Any] | None = None
        self.input_shape: tuple[int, ...] = ()
        self.output_shape: tuple[int, ...] = ()
        self.input_dtype = np.dtype(np.float32)
        self.output_dtype = np.dtype(np.float32)
        self.lock = threading.Lock()
        self.closed = False
        # A failed close leaves this runner owned and non-runnable until a
        # later close retry succeeds.  This prevents a partial teardown from
        # racing a new inference or the process-wide runtime shutdown.
        self.closing = False
        self._runtime_registered = False
        self.cleanup_diagnostics: list[dict[str, Any]] = []
        self.host_pointer_api = "numpy.ctypes.data"

        try:
            self.acl = _AclEnvironment.acquire_runner()
            self._runtime_registered = True
            self.context = _acl_check(self.acl.rt.create_context(NPU_DEVICE_ID), "acl.rt.create_context")
            self.model_id = _acl_check(self.acl.mdl.load_from_file(str(self.path)), "acl.mdl.load_from_file")
            self.desc = self.acl.mdl.create_desc()
            if self.desc is None:
                raise RuntimeError("acl.mdl.create_desc failed")
            _acl_check(self.acl.mdl.get_desc(self.desc, self.model_id), "acl.mdl.get_desc")
            if self.acl.mdl.get_num_inputs(self.desc) != 1 or self.acl.mdl.get_num_outputs(self.desc) != 1:
                raise ValueError("OM model must have exactly one input and one output")
            self.input_shape = self._shape(True, 0)
            self.output_shape = self._shape(False, 0)
            self.input_dtype = self._dtype(True, 0)
            self.output_dtype = self._dtype(False, 0)
            self.input_dataset, self.input_buffer = self._allocate(True, 0)
            self.output_dataset, self.output_buffer = self._allocate(False, 0)
        except BaseException:
            # Constructor failures are common during ATC/ACL diagnosis.  Never
            # leave a partially loaded model or context for process shutdown.
            self.close(suppress_errors=True)
            raise

    def _shape(self, is_input: bool, index: int) -> tuple[int, ...]:
        if self.acl is None or self.desc is None:
            raise RuntimeError("OM runner is not initialized")
        getter = self.acl.mdl.get_input_dims if is_input else self.acl.mdl.get_output_dims
        result = getter(self.desc, index)
        info = _acl_check(result, "get tensor dimensions")
        if not isinstance(info, dict):
            raise ValueError(f"ACL tensor dimensions are invalid: {info!r}")
        dimensions = tuple(int(value) for value in info["dims"][: int(info["dimCount"])])
        if not dimensions or any(value <= 0 for value in dimensions):
            raise ValueError(f"ACL tensor dimensions are invalid: {info!r}")
        return dimensions

    def _dtype(self, is_input: bool, index: int) -> np.dtype:
        if self.acl is None or self.desc is None:
            raise RuntimeError("OM runner is not initialized")
        getter = self.acl.mdl.get_input_data_type if is_input else self.acl.mdl.get_output_data_type
        value = getter(self.desc, index)
        mapping = {
            int(getattr(self.acl, "ACL_FLOAT", 0)): np.dtype(np.float32),
            int(getattr(self.acl, "ACL_FLOAT16", 1)): np.dtype(np.float16),
        }
        if int(value) not in mapping:
            raise ValueError(f"Unsupported OM dtype: {value}")
        return mapping[int(value)]

    def _record_cleanup(self, phase: str, operation: Any | None = None, *args: Any) -> bool:
        event: dict[str, Any] = {"timestamp": _acl_timestamp(), "phase": phase}
        try:
            if operation is None:
                event.update({"ok": True, "status": "skipped"})
            else:
                ret = _acl_result_code(operation(*args), none_is_success=True)
                event.update({"ok": ret == 0, "return_code": ret})
        except BaseException as exc:
            event.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        self.cleanup_diagnostics.append(event)
        return bool(event["ok"])

    def _record_cleanup_blocked(self, phase: str, reason: str) -> None:
        self.cleanup_diagnostics.append(
            {
                "timestamp": _acl_timestamp(),
                "phase": phase,
                "ok": False,
                "status": "blocked",
                "reason": reason,
            }
        )

    def _destroy_allocation(
        self,
        dataset: Any | None,
        buffer: dict[str, Any] | None,
        *,
        label: str,
    ) -> tuple[Any | None, dict[str, Any] | None, bool]:
        if self.acl is None:
            return dataset, buffer, False
        ok = True
        if buffer is not None and buffer.get("data_buffer") is not None:
            destroyed = self._record_cleanup(
                f"destroy_{label}_data_buffer", self.acl.destroy_data_buffer, buffer["data_buffer"]
            )
            ok = destroyed and ok
            if not destroyed:
                # The dataset still owns this ACL data-buffer wrapper.  Do
                # not destroy the dataset or free its pointer in the same
                # attempt; retain both handles for a safe retry.
                self._record_cleanup_blocked(
                    f"destroy_{label}_dataset",
                    "data buffer remains live",
                )
                return dataset, buffer, False
            buffer["data_buffer"] = None
        # ACL owns the dataset's buffer references.  Release that container
        # before freeing device memory so teardown follows the CANN resource
        # order even after a partially constructed runner.
        if dataset is not None:
            destroyed = self._record_cleanup(f"destroy_{label}_dataset", self.acl.mdl.destroy_dataset, dataset)
            ok = destroyed and ok
            if destroyed:
                dataset = None
        if buffer is not None and buffer.get("pointer") is not None:
            # Do not free device memory while its ACL data buffer or dataset
            # still owns the pointer.  Retaining the handle makes a later
            # close retry safe instead of resetting a device with a live ACL
            # object.
            if buffer.get("data_buffer") is None and dataset is None:
                freed = self._record_cleanup(f"free_{label}_device_buffer", self.acl.rt.free, buffer["pointer"])
                ok = freed and ok
                if freed:
                    buffer["pointer"] = None
            else:
                self.cleanup_diagnostics.append(
                    {
                        "timestamp": _acl_timestamp(),
                        "phase": f"free_{label}_device_buffer",
                        "ok": False,
                        "status": "blocked_by_live_acl_container",
                    }
                )
                ok = False
        if buffer is not None and buffer.get("data_buffer") is None and buffer.get("pointer") is None:
            buffer = None
        return dataset, buffer, ok

    def _allocate(self, is_input: bool, index: int) -> tuple[Any, dict[str, Any]]:
        if self.acl is None or self.desc is None:
            raise RuntimeError("OM runner is not initialized")
        label = "input" if is_input else "output"
        dataset: Any | None = None
        buffer: dict[str, Any] | None = None
        try:
            dataset = self.acl.mdl.create_dataset()
            if dataset is None:
                raise RuntimeError(f"acl.mdl.create_dataset failed for {label}")
            size_getter = self.acl.mdl.get_input_size_by_index if is_input else self.acl.mdl.get_output_size_by_index
            size = int(size_getter(self.desc, index))
            if size <= 0:
                raise ValueError(f"OM {label} byte size is invalid: {size}")
            pointer = _acl_check(self.acl.rt.malloc(size, 0), f"acl.rt.malloc {label}")
            # Register device memory before the next ACL call.  If
            # create_data_buffer fails, the shared exception path can retain
            # and retry this pointer instead of losing a local allocation.
            buffer = {"pointer": pointer, "size": size, "data_buffer": None}
            data_buffer = self.acl.create_data_buffer(pointer, size)
            if data_buffer is None:
                raise RuntimeError(f"acl.create_data_buffer failed for {label}")
            buffer["data_buffer"] = data_buffer
            _acl_check(
                self.acl.mdl.add_dataset_buffer(dataset, data_buffer),
                f"acl.mdl.add_dataset_buffer {label}",
            )
            return dataset, buffer
        except BaseException:
            # Keep any handles that could not be released attached to the
            # runner.  The constructor's outer cleanup can then retry them in
            # the same dependency-aware order instead of losing local names.
            remaining_dataset, remaining_buffer, _ = self._destroy_allocation(
                dataset, buffer, label=f"partial_{label}"
            )
            if is_input:
                self.input_dataset = remaining_dataset
                self.input_buffer = remaining_buffer
            else:
                self.output_dataset = remaining_dataset
                self.output_buffer = remaining_buffer
            raise

    def _host_pointer(self, value: np.ndarray, *, writable: bool) -> tuple[Any, Any | None]:
        """Prefer PyACL's host-pointer helpers while retaining CANN compatibility."""

        if self.acl is None:
            raise RuntimeError("OM runner is not initialized")
        util = getattr(self.acl, "util", None)
        numpy_to_ptr = getattr(util, "numpy_to_ptr", None)
        if callable(numpy_to_ptr):
            pointer = numpy_to_ptr(value)
            if pointer is None:
                raise RuntimeError("acl.util.numpy_to_ptr returned no host pointer")
            self.host_pointer_api = "acl.util.numpy_to_ptr"
            return pointer, None
        bytes_to_ptr = getattr(util, "bytes_to_ptr", None)
        if callable(bytes_to_ptr) and not writable:
            payload = value.tobytes(order="C")
            pointer = bytes_to_ptr(payload)
            if pointer is None:
                raise RuntimeError("acl.util.bytes_to_ptr returned no host pointer")
            self.host_pointer_api = "acl.util.bytes_to_ptr"
            return pointer, payload
        self.host_pointer_api = "numpy.ctypes.data"
        return value.ctypes.data, None

    def run(self, tensor: np.ndarray) -> np.ndarray:
        if self.closed or self.closing:
            raise RuntimeError("OM runner is closed")
        if self.acl is None or self.context is None or self.model_id is None:
            raise RuntimeError("OM runner is not initialized")
        if self.input_buffer is None or self.output_buffer is None:
            raise RuntimeError("OM runner buffers are unavailable")
        value = np.ascontiguousarray(tensor, dtype=self.input_dtype)
        if tuple(value.shape) != tuple(self.input_shape):
            raise ValueError(f"OM input shape {value.shape}, expected {self.input_shape}")
        if value.nbytes != self.input_buffer["size"]:
            raise ValueError("OM input byte size mismatch")
        with self.lock:
            if self.closed or self.closing:
                raise RuntimeError("OM runner is closed")
            _acl_check(self.acl.rt.set_context(self.context), "acl.rt.set_context")
            input_pointer, _input_keepalive = self._host_pointer(value, writable=False)
            _acl_check(
                self.acl.rt.memcpy(
                    self.input_buffer["pointer"],
                    self.input_buffer["size"],
                    input_pointer,
                    value.nbytes,
                    int(getattr(self.acl, "ACL_MEMCPY_HOST_TO_DEVICE", 1)),
                ),
                "host_to_device",
            )
            _acl_check(
                self.acl.mdl.execute(self.model_id, self.input_dataset, self.output_dataset),
                "acl.mdl.execute",
            )
            output = np.empty(self.output_shape, dtype=self.output_dtype)
            output_pointer, _output_keepalive = self._host_pointer(output, writable=True)
            _acl_check(
                self.acl.rt.memcpy(
                    output_pointer,
                    output.nbytes,
                    self.output_buffer["pointer"],
                    self.output_buffer["size"],
                    int(getattr(self.acl, "ACL_MEMCPY_DEVICE_TO_HOST", 2)),
                ),
                "device_to_host",
            )
        return output

    def close(self, *, suppress_errors: bool = False) -> list[dict[str, Any]]:
        """Release every resource, retaining ownership when a step fails.

        Cleanup is retryable.  A failed destroy/free keeps its handle and the
        runner reference count, so ``shutdown_acl_runtime`` cannot reset the
        device underneath a partially released ACL object.
        """

        with self.lock:
            if self.closed:
                return list(self.cleanup_diagnostics)
            self.closing = True
            diagnostics_start = len(self.cleanup_diagnostics)
            attempt_failures: list[dict[str, Any]] = []
            cleanup_ready = True
            if self.acl is not None and self.context is not None:
                context_ok = self._record_cleanup("set_context_before_cleanup", self.acl.rt.set_context, self.context)
                synchronize = getattr(self.acl.rt, "synchronize_device", None)
                if context_ok and callable(synchronize):
                    sync_ok = self._record_cleanup("synchronize_device_before_cleanup", synchronize)
                    cleanup_ready = sync_ok
                elif not callable(synchronize):
                    self._record_cleanup("synchronize_device_before_cleanup")
                else:
                    self._record_cleanup_blocked(
                        "synchronize_device_before_cleanup",
                        "set_context_before_cleanup failed",
                    )
                    cleanup_ready = False
                if not context_ok:
                    cleanup_ready = False
            if cleanup_ready:
                self.input_dataset, self.input_buffer, input_ok = self._destroy_allocation(
                    self.input_dataset, self.input_buffer, label="input"
                )
                self.output_dataset, self.output_buffer, output_ok = self._destroy_allocation(
                    self.output_dataset, self.output_buffer, label="output"
                )
                if not input_ok:
                    attempt_failures.append({"phase": "input_allocation", "ok": False})
                if not output_ok:
                    attempt_failures.append({"phase": "output_allocation", "ok": False})
                allocations_clear = all(
                    value is None
                    for value in (
                        self.input_dataset,
                        self.output_dataset,
                        self.input_buffer,
                        self.output_buffer,
                    )
                )
                if not allocations_clear:
                    cleanup_ready = False
                    self._record_cleanup_blocked(
                        "destroy_model_desc",
                        "input/output ACL allocations remain live",
                    )
            if cleanup_ready and self.acl is not None and self.desc is not None:
                if self._record_cleanup("destroy_model_desc", self.acl.mdl.destroy_desc, self.desc):
                    self.desc = None
                else:
                    attempt_failures.append({"phase": "destroy_model_desc", "ok": False})
                    cleanup_ready = False
            if cleanup_ready and self.acl is not None and self.model_id is not None:
                if self._record_cleanup("unload_model", self.acl.mdl.unload, self.model_id):
                    self.model_id = None
                else:
                    attempt_failures.append({"phase": "unload_model", "ok": False})
                    cleanup_ready = False
            if cleanup_ready and self.acl is not None and self.context is not None:
                if self._record_cleanup("destroy_context", self.acl.rt.destroy_context, self.context):
                    self.context = None
                else:
                    attempt_failures.append({"phase": "destroy_context", "ok": False})

            live_resources = any(
                value is not None
                for value in (
                    self.input_dataset,
                    self.output_dataset,
                    self.input_buffer,
                    self.output_buffer,
                    self.desc,
                    self.model_id,
                    self.context,
                )
            )
            if self._runtime_registered and not live_resources:
                _AclEnvironment.release_runner()
                self._runtime_registered = False
                self.closed = True
                self.closing = False

            failures = attempt_failures or (
                [{"phase": "live_acl_resources", "ok": False}] if live_resources else []
            )
            if failures and not suppress_errors:
                summary = "; ".join(
                    f"{event['phase']}: {event.get('return_code', event.get('error', 'failed'))}"
                    for event in failures
                )
                raise RuntimeError(f"ACL cleanup failed: {summary}")
            # Keep the full history on ``cleanup_diagnostics`` for board
            # telemetry, while returning only this attempt so a retry can be
            # evaluated independently of an earlier transient failure.
            return list(self.cleanup_diagnostics[diagnostics_start:])


class OmEmbeddingAdapter(PalmAdapter):
    backend = "npu"

    def __init__(self, spec: ModelSpec, precision: str) -> None:
        super().__init__(spec)
        path = spec.om_path(precision)
        if path is None or not path.is_file():
            raise FileNotFoundError(f"OM model not found: {path}")
        self.precision = precision
        self.runner = _OmRunner(path)

    def preprocess(self, roi: np.ndarray) -> np.ndarray:
        return preprocess_embedding_roi(roi, self.spec.input_range)

    def encode_preprocessed(self, value: np.ndarray) -> np.ndarray:
        output = self.runner.run(value)
        code = l2_normalize(output)
        if self.spec.feature_dim and code.size != self.spec.feature_dim:
            raise ValueError(f"Unexpected {self.spec.id} OM output size: {code.size}")
        return code

    def compare(self, query: np.ndarray, references: np.ndarray) -> np.ndarray:
        return np.asarray(references, dtype=np.float32) @ np.asarray(query, dtype=np.float32)

    def close(self) -> None:
        self.runner.close()


def create_adapter(
    spec: ModelSpec,
    backend: str,
    precision: str = "mixed_fp16",
    *,
    threads: int | None = None,
) -> PalmAdapter:
    del threads
    if backend != "npu":
        raise ValueError("Production runtime supports only backend=npu")
    if precision != "mixed_fp16":
        raise ValueError("Production runtime supports only precision=mixed_fp16")
    return OmEmbeddingAdapter(spec, precision)
