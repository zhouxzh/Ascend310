"""Torch-free Qwen ACL/OM runtime.

This module deliberately keeps the CANN import behind ``NativeAclBackend``.
The controller can therefore run contract and API tests without ``acl`` or any
Ascend libraries installed.  The first admitted graph is a static, full
context Qwen graph with three int64 inputs and one float16 logits output.  A
generic ONNX decoder, a graph with KV-cache inputs, or a mismatched OM is
rejected before inference.
"""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
import importlib
import logging
from pathlib import Path
import signal
import threading
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Protocol, Sequence, Tuple, Union

from acl_om_contract import (
    EXPECTED_INPUT_ORDER,
    EXPECTED_MODEL_ID,
    ContractError,
    ModelContract,
)
from acl_om_tokenizer import QwenTokenizer, TokenizerError


LOGGER = logging.getLogger("case9.acl_om")


class RuntimeErrorBase(RuntimeError):
    """Base class for sanitized runtime failures."""


class RuntimeUnavailable(RuntimeErrorBase):
    """Raised when ACL/OM is not initialized or the graph is not admitted."""


class RuntimeBusy(RuntimeErrorBase):
    """Raised instead of queueing a second NPU operation."""


class RuntimeRequestError(RuntimeErrorBase):
    """Raised for an invalid prompt or generation request."""


class RuntimeExecutionTimeout(RuntimeErrorBase):
    """Raised when one synchronous ACL operation exceeds its deadline."""


@dataclass(frozen=True)
class TensorDescriptor:
    name: str
    dtype: str
    shape: Tuple[int, ...]
    byte_size: Optional[int] = None


@dataclass(frozen=True)
class RuntimeDescriptor:
    inputs: Tuple[TensorDescriptor, ...]
    outputs: Tuple[TensorDescriptor, ...]


@dataclass(frozen=True)
class GenerationResult:
    text: str
    prompt_tokens: int
    completion_tokens: int


class Backend(Protocol):
    """Small backend contract used by the runtime and pure-Python fakes."""

    def open(self, model_path: Path) -> RuntimeDescriptor:
        """Load the OM and return its actual descriptor."""

    def run(self, inputs: Mapping[str, Any]) -> Any:
        """Execute one static graph call and return the logits array."""

    def close(self) -> None:
        """Release ACL resources."""


class AclOmRuntime:
    """Validated Qwen full-context decoder backed by a serial ACL runner."""

    def __init__(
        self,
        contract_path: Union[str, Path],
        om_path: Union[str, Path],
        tokenizer_path: Union[str, Path],
        tokenizer_config_path: Optional[Union[str, Path]] = None,
        backend: Optional[Backend] = None,
        tokenizer: Optional[Any] = None,
        device_id: int = 0,
        execution_timeout_seconds: float = 300.0,
    ) -> None:
        try:
            self.contract = ModelContract.load(contract_path)
        except ContractError as exc:
            raise RuntimeUnavailable(str(exc)) from exc
        if self.contract.model_id != EXPECTED_MODEL_ID:
            raise RuntimeUnavailable("contract model id is not the fixed ACL/OM model")
        self.om_path = Path(om_path).expanduser()
        self.tokenizer_path = Path(tokenizer_path).expanduser()
        self.tokenizer_config_path = (
            Path(tokenizer_config_path).expanduser()
            if tokenizer_config_path is not None
            else None
        )
        if not self.om_path.is_file():
            raise RuntimeUnavailable(f"OM file does not exist: {self.om_path}")
        if not self.tokenizer_path.is_file():
            raise RuntimeUnavailable(
                f"tokenizer file does not exist: {self.tokenizer_path}"
            )
        if execution_timeout_seconds <= 0:
            raise RuntimeUnavailable("execution_timeout_seconds must be positive")
        self.execution_timeout_seconds = float(execution_timeout_seconds)
        self._backend: Backend = backend or NativeAclBackend(
            device_id=device_id,
            input_order_verified=self.contract.input_order_verified,
        )
        self._tokenizer = tokenizer
        self._descriptor: Optional[RuntimeDescriptor] = None
        self._started = False
        self._closed = False
        self._operation_lock = threading.Lock()
        self._cancel_event = threading.Event()

    @property
    def model_id(self) -> str:
        return self.contract.model_id

    @property
    def started(self) -> bool:
        return self._started and not self._closed and not bool(
            getattr(self._backend, "poisoned", False)
        )

    def start(self) -> None:
        if self.started:
            return
        if self._closed:
            raise RuntimeUnavailable("ACL/OM runtime has been closed")
        self._cancel_event.clear()
        try:
            descriptor = self._backend.open(self.om_path)
            self._validate_descriptor(descriptor)
            if self._tokenizer is None:
                self._tokenizer = QwenTokenizer(
                    self.tokenizer_path, self.tokenizer_config_path
                )
            vocab_size = getattr(self._tokenizer, "vocab_size", None)
            if vocab_size is None or int(vocab_size) < self.contract.vocabulary_size:
                raise RuntimeUnavailable(
                    "tokenizer vocabulary is smaller than the OM logits vocabulary"
                )
            self._descriptor = descriptor
            self._started = True
        except RuntimeUnavailable:
            self._backend.close()
            raise
        except (ContractError, TokenizerError, OSError, ValueError) as exc:
            self._backend.close()
            raise RuntimeUnavailable(str(exc)) from exc
        except Exception as exc:
            self._backend.close()
            raise RuntimeUnavailable(
                f"ACL/OM initialization failed: {type(exc).__name__}"
            ) from exc

    def close(self) -> None:
        if self._closed:
            if getattr(self._backend, "acl", None) is not None:
                self._backend.close()
            return
        self._closed = True
        self._cancel_event.set()
        self._started = False
        self._descriptor = None
        self._backend.close()

    def cancel(self) -> None:
        """Request cancellation of the current serial generation."""
        self._cancel_event.set()

    def status(self) -> Dict[str, Any]:
        descriptor = self._descriptor
        restart_required = bool(getattr(self._backend, "poisoned", False))
        return {
            "ready": self.started and not restart_required,
            "model": self.model_id,
            "backend": "acl_om",
            "execution_mode": "full_context_logits",
            "contract_schema_version": 1,
            # Do not expose the board's filesystem layout through the API.
            "om": self.om_path.name,
            "descriptor_validated": descriptor is not None,
            "restart_required": restart_required,
        }

    def complete(
        self,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int = 128,
    ) -> GenerationResult:
        text = ""
        prompt_tokens = 0
        completion_tokens = 0
        for text, prompt_tokens, completion_tokens in self.stream(
            messages, max_tokens=max_tokens
        ):
            pass
        return GenerationResult(text, prompt_tokens, completion_tokens)

    def stream(
        self,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: int = 128,
    ) -> Iterator[Tuple[str, int, int]]:
        """Yield ``(text_so_far, prompt_tokens, completion_tokens)`` snapshots."""
        if not self.started:
            raise RuntimeUnavailable("ACL/OM runtime is not ready")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int):
            raise RuntimeRequestError("max_tokens must be an integer")
        if max_tokens < 1 or max_tokens > 128:
            raise RuntimeRequestError("max_tokens must be between 1 and 128")
        if not self._operation_lock.acquire(blocking=False):
            raise RuntimeBusy("another NPU operation is already running")
        try:
            self._cancel_event.clear()
            tokenizer = self._tokenizer
            if tokenizer is None:
                raise RuntimeUnavailable("tokenizer is not initialized")
            try:
                prompt_ids = [int(value) for value in tokenizer.encode_messages(messages)]
            except (TokenizerError, ValueError) as exc:
                raise RuntimeRequestError(str(exc)) from exc
            if len(prompt_ids) >= self.contract.static_sequence_length:
                raise RuntimeRequestError(
                    "prompt exceeds the fixed 2048-token context; shorten the messages"
                )
            if len(prompt_ids) + max_tokens > self.contract.static_sequence_length:
                raise RuntimeRequestError(
                    "prompt plus max_tokens exceeds the fixed 2048-token context"
                )

            generated_ids: List[int] = []
            previous_text = ""
            last_yielded_count = 0
            for _ in range(max_tokens):
                if self._cancel_event.is_set():
                    raise RuntimeRequestError("generation was cancelled")
                all_ids = prompt_ids + generated_ids
                if len(all_ids) >= self.contract.static_sequence_length:
                    break
                inputs = _padded_inputs(
                    all_ids,
                    self.contract.static_sequence_length,
                    self.contract.pad_token_id,
                )
                with _execution_deadline(self.execution_timeout_seconds):
                    logits = self._backend.run(inputs)
                next_id = _greedy_next_id(
                    logits,
                    len(all_ids) - 1,
                    self.contract.vocabulary_size,
                )
                generated_ids.append(next_id)
                if next_id in {self.contract.eos_token_id, self.contract.pad_token_id}:
                    break
                try:
                    current_text = str(tokenizer.decode(generated_ids))
                except Exception as exc:
                    raise RuntimeRequestError("tokenizer decode failed") from exc
                if current_text.startswith(previous_text):
                    delta = current_text[len(previous_text) :]
                else:
                    # BPE decoders can revise whitespace around a token.  Send
                    # the newly decoded text rather than silently dropping it.
                    delta = current_text
                previous_text = current_text
                if delta:
                    last_yielded_count = len(generated_ids)
                    yield current_text, len(prompt_ids), len(generated_ids)
            if generated_ids and len(generated_ids) > last_yielded_count:
                # Account for a terminal EOS/PAD token in usage without
                # manufacturing visible text.  The protocol adapter drops an
                # empty delta, while non-streaming usage remains accurate.
                yield previous_text, len(prompt_ids), len(generated_ids)
            if not generated_ids:
                yield "", len(prompt_ids), 0
            elif previous_text == "":
                try:
                    previous_text = str(tokenizer.decode(generated_ids))
                except Exception as exc:
                    raise RuntimeRequestError("tokenizer decode failed") from exc
                yield previous_text, len(prompt_ids), len(generated_ids)
        finally:
            self._operation_lock.release()

    def _validate_descriptor(self, descriptor: RuntimeDescriptor) -> None:
        if not isinstance(descriptor, RuntimeDescriptor):
            raise RuntimeUnavailable("ACL backend returned no model descriptor")
        if len(descriptor.inputs) != 3 or len(descriptor.outputs) != 1:
            raise RuntimeUnavailable(
                "OM must expose exactly three inputs and one logits output"
            )
        expected_inputs = self.contract.inputs
        seen = set()
        for actual in descriptor.inputs:
            expected = expected_inputs.get(actual.name)
            if expected is None:
                raise RuntimeUnavailable(f"OM has an unadmitted input: {actual.name}")
            seen.add(actual.name)
            if actual.dtype != expected.dtype or actual.shape != expected.shape:
                raise RuntimeUnavailable(
                    f"OM input {actual.name} does not match the inspected contract"
                )
            _validate_byte_size(actual)
        if seen != set(expected_inputs):
            raise RuntimeUnavailable("OM input names do not match the inspected contract")
        output = descriptor.outputs[0]
        expected_output = self.contract.logits
        if output.name != expected_output.name:
            raise RuntimeUnavailable("OM output is not the declared logits tensor")
        if output.dtype != expected_output.dtype or output.shape != expected_output.shape:
            raise RuntimeUnavailable("OM logits descriptor does not match the contract")
        _validate_byte_size(output)


def _validate_byte_size(descriptor: TensorDescriptor) -> None:
    if descriptor.byte_size is None:
        return
    item_size = {"int64": 8, "float16": 2}.get(descriptor.dtype)
    if item_size is None:
        raise RuntimeUnavailable(f"unsupported tensor dtype: {descriptor.dtype}")
    expected = item_size
    for dimension in descriptor.shape:
        expected *= dimension
    if descriptor.byte_size != expected:
        raise RuntimeUnavailable(
            f"OM byte size for {descriptor.name} is {descriptor.byte_size}, expected {expected}"
        )


@contextmanager
def _execution_deadline(seconds: float) -> Iterator[None]:
    """Bound a native ACL call when the service runs on the board main thread.

    ``signal.setitimer`` is intentionally used only from the main thread;
    controller tests and embedded callers without POSIX timers simply retain
    the normal synchronous behavior and do not claim timeout evidence.
    """
    if (
        threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "SIGALRM")
        or not hasattr(signal, "setitimer")
    ):
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)

    def _alarm_handler(_signum: int, _frame: Any) -> None:
        raise RuntimeExecutionTimeout(
            f"ACL operation exceeded the {seconds:g}-second execution deadline"
        )

    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    signal.signal(signal.SIGALRM, _alarm_handler)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)


@contextmanager
def _without_execution_alarm() -> Iterator[None]:
    """Temporarily disable the deadline while waiting for device quiescence."""
    if (
        threading.current_thread() is not threading.main_thread()
        or not hasattr(signal, "SIGALRM")
        or not hasattr(signal, "setitimer")
    ):
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)
    previous_timer = signal.getitimer(signal.ITIMER_REAL)
    signal.setitimer(signal.ITIMER_REAL, 0)
    signal.signal(signal.SIGALRM, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, *previous_timer)
        signal.signal(signal.SIGALRM, previous_handler)


def _padded_inputs(
    token_ids: Sequence[int], sequence_length: int, pad_token_id: int = 0
) -> Dict[str, Any]:
    try:
        import numpy as np  # type: ignore
    except ImportError as exc:
        raise RuntimeUnavailable("numpy is required by the ACL/OM runtime") from exc
    ids = np.full((1, sequence_length), int(pad_token_id), dtype=np.int64)
    mask = np.zeros((1, sequence_length), dtype=np.int64)
    positions = np.zeros((1, sequence_length), dtype=np.int64)
    length = len(token_ids)
    ids[0, :length] = token_ids
    mask[0, :length] = 1
    positions[0, :length] = np.arange(length, dtype=np.int64)
    return {"input_ids": ids, "attention_mask": mask, "position_ids": positions}


def _greedy_next_id(logits: Any, position: int, vocabulary_size: int) -> int:
    try:
        import numpy as np  # type: ignore
    except ImportError as exc:
        raise RuntimeUnavailable("numpy is required by the ACL/OM runtime") from exc
    values = np.asarray(logits)
    expected_rank = 3
    if values.ndim != expected_rank or values.shape[0] != 1:
        raise RuntimeUnavailable("ACL logits output has an unexpected rank or batch")
    if values.shape[1] <= position or values.shape[2] != vocabulary_size:
        raise RuntimeUnavailable("ACL logits output does not match the contract")
    row = values[0, position, :]
    if not np.isfinite(row).any():
        raise RuntimeUnavailable("ACL logits contain no finite values")
    return int(np.argmax(row))


class NativeAclBackend:
    """Small PyACL adapter; imported and executed only on the board."""

    def __init__(self, device_id: int = 0, input_order_verified: bool = False) -> None:
        self.device_id = device_id
        self._input_order_verified = bool(input_order_verified)
        self.acl: Any = None
        self.context: Any = None
        self.model_id: Any = None
        self.desc: Any = None
        self.stream: Any = None
        self._opened = False
        self._input_order: List[TensorDescriptor] = []
        self._output_order: List[TensorDescriptor] = []
        self._pending_run_cleanups: List[Dict[str, Any]] = []
        self._poisoned = False

    @property
    def poisoned(self) -> bool:
        """Whether a native call timed out and this backend must be restarted."""
        return self._poisoned

    def open(self, model_path: Path) -> RuntimeDescriptor:
        if self._opened:
            raise RuntimeUnavailable("ACL backend is already open")
        if self._poisoned:
            raise RuntimeUnavailable("ACL backend is unhealthy; restart the service")
        try:
            self.acl = importlib.import_module("acl")
        except ImportError as exc:
            raise RuntimeUnavailable(
                "PyACL module 'acl' is unavailable; source the CANN environment"
            ) from exc
        try:
            _check_acl(self.acl.init(), "acl.init")
            _check_acl(self.acl.rt.set_device(self.device_id), "acl.rt.set_device")
            context_result = self.acl.rt.create_context(self.device_id)
            self.context, context_ret = _split_result(context_result)
            _check_acl(context_ret, "acl.rt.create_context")
            create_stream = getattr(self.acl.rt, "create_stream", None)
            if not callable(create_stream):
                raise RuntimeUnavailable("CANN binding does not expose acl.rt.create_stream")
            stream_result = create_stream()
            self.stream, stream_ret = _split_result(stream_result)
            _check_acl(stream_ret, "acl.rt.create_stream")
            if self.stream is None:
                raise RuntimeUnavailable("acl.rt.create_stream returned no stream")
            model_result = self.acl.mdl.load_from_file(str(model_path))
            self.model_id, model_ret = _split_result(model_result)
            _check_acl(model_ret, "acl.mdl.load_from_file")
            self.desc = self.acl.mdl.create_desc()
            if self.desc is None:
                raise RuntimeUnavailable("acl.mdl.create_desc returned no descriptor")
            _check_acl(self.acl.mdl.get_desc(self.desc, self.model_id), "acl.mdl.get_desc")
            inputs = self._read_descriptors(True)
            outputs = self._read_descriptors(False)
            self._input_order = inputs
            self._output_order = outputs
            self._opened = True
            return RuntimeDescriptor(tuple(inputs), tuple(outputs))
        except RuntimeUnavailable:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise RuntimeUnavailable(
                f"ACL model descriptor initialization failed: {type(exc).__name__}"
            ) from exc

    def _read_descriptors(self, is_input: bool) -> List[TensorDescriptor]:
        if self.acl is None or self.desc is None:
            raise RuntimeUnavailable("ACL model descriptor is not initialized")
        kind = "input" if is_input else "output"
        count = int(_first_value(getattr(self.acl.mdl, f"get_num_{kind}s")(self.desc)))
        result: List[TensorDescriptor] = []
        fallback_names = (
            ["input_ids", "attention_mask", "position_ids"]
            if is_input
            else ["logits"]
        )
        name_getter = getattr(self.acl.mdl, f"get_{kind}_name_by_index", None)
        if not callable(name_getter) and count != len(fallback_names):
            raise RuntimeUnavailable(
                f"ACL binding cannot read {kind} names and tensor count is unexpected"
            )
        if not callable(name_getter) and is_input:
            # The contract records the exact ATC input order.  Do not make an
            # anonymous descriptor binding guess unless that invariant was
            # explicitly produced by the inspected graph and manifest gate.
            if not self._contract_input_order_verified():
                raise RuntimeUnavailable(
                    "ACL binding has no input-name getter and the contract does not verify ATC order"
                )
        for index in range(count):
            if callable(name_getter):
                name = _first_value(name_getter(self.desc, index))
            else:
                # CANN 8.0 bindings seen on 310B expose descriptor counts,
                # shapes and dtypes but not name getters.  The ATC command
                # uses this fixed input order; still validate every count,
                # shape, dtype and byte size against the inspected contract.
                name = EXPECTED_INPUT_ORDER[index]
            dims_getter = getattr(self.acl.mdl, f"get_{kind}_dims", None)
            if not callable(dims_getter):
                raise RuntimeUnavailable(f"ACL binding cannot read {kind} dimensions")
            dims = _first_value(dims_getter(self.desc, index))
            shape = _parse_dims(dims)
            # CANN 8.0's PyACL binding exposes get_input_data_type and
            # get_output_data_type (despite the surrounding methods using a
            # ``*_by_index`` suffix).  Keep the exact binding names here.
            dtype_getter = getattr(self.acl.mdl, f"get_{kind}_data_type", None)
            if not callable(dtype_getter):
                raise RuntimeUnavailable(f"ACL binding cannot read {kind} data type")
            dtype = _acl_dtype(self.acl, int(_first_value(dtype_getter(self.desc, index))))
            size_getter = getattr(self.acl.mdl, f"get_{kind}_size_by_index")
            byte_size = int(_first_value(size_getter(self.desc, index)))
            if isinstance(name, bytes):
                try:
                    name = name.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise RuntimeUnavailable(f"ACL {kind} name is not UTF-8") from exc
            result.append(TensorDescriptor(str(name), dtype, shape, byte_size))
        return result

    def run(self, inputs: Mapping[str, Any]) -> Any:
        if self._poisoned:
            raise RuntimeUnavailable("ACL backend is unhealthy; restart the service")
        if not self._opened or self.acl is None or self.model_id is None:
            raise RuntimeUnavailable("ACL backend is not open")
        if not self._retry_pending_run_cleanups():
            self._poisoned = True
            raise RuntimeUnavailable("ACL buffer cleanup is pending; restart the service")
        try:
            import numpy as np  # type: ignore
        except ImportError as exc:
            raise RuntimeUnavailable("numpy is required by the ACL/OM runtime") from exc
        input_dataset = None
        output_dataset = None
        allocations: List[Dict[str, Any]] = []
        output_arrays: List[Any] = []
        cleanup_record: Optional[Dict[str, Any]] = None
        native_call_may_be_active = False
        try:
            if self.context is not None:
                _check_acl(self.acl.rt.set_context(self.context), "acl.rt.set_context")
            cleanup_record = {
                "input_dataset": None,
                "output_dataset": None,
                "allocations": allocations,
            }
            native_call_may_be_active = True
            input_dataset = self.acl.mdl.create_dataset()
            cleanup_record["input_dataset"] = input_dataset
            output_dataset = self.acl.mdl.create_dataset()
            cleanup_record["output_dataset"] = output_dataset
            if input_dataset is None or output_dataset is None:
                raise RuntimeUnavailable("ACL dataset allocation failed")
            # Any ACL call below may be interrupted by the deadline signal.
            # Keep the record until an unbounded stream synchronization proves
            # that no device operation is still using its buffers.
            for descriptor in self._input_order:
                value = inputs.get(descriptor.name)
                if value is None:
                    raise RuntimeUnavailable(f"missing ACL input: {descriptor.name}")
                array = np.ascontiguousarray(value, dtype=np.int64)
                if tuple(array.shape) != descriptor.shape or array.nbytes != descriptor.byte_size:
                    raise RuntimeUnavailable(f"invalid ACL input buffer: {descriptor.name}")
                allocation = self._allocate_buffer(array.nbytes, input_dataset, allocations)
                _copy_host_to_device(self.acl, allocation["pointer"], array)
            for descriptor in self._output_order:
                allocation = self._allocate_buffer(
                    descriptor.byte_size or 0, output_dataset, allocations
                )
                output_arrays.append(np.empty(descriptor.shape, dtype=_numpy_dtype(descriptor.dtype)))
            execute_async = getattr(self.acl.mdl, "execute_async", None)
            synchronize_stream = getattr(self.acl.rt, "synchronize_stream", None)
            if not callable(execute_async) or not callable(synchronize_stream) or self.stream is None:
                raise RuntimeUnavailable(
                    "CANN binding does not expose the required asynchronous stream APIs"
                )
            _check_acl(
                execute_async(self.model_id, input_dataset, output_dataset, self.stream),
                "acl.mdl.execute_async",
            )
            _check_acl(synchronize_stream(self.stream), "acl.rt.synchronize_stream")
            for allocation, output in zip(allocations[len(self._input_order):], output_arrays):
                _copy_device_to_host(self.acl, output, allocation["pointer"], allocation["size"])
            native_call_may_be_active = False
            return output_arrays[0]
        except RuntimeExecutionTimeout:
            self._poisoned = True
            if native_call_may_be_active and not self._synchronize_stream_unbounded():
                if cleanup_record is not None:
                    self._pending_run_cleanups.append(cleanup_record)
                    cleanup_record = None
                LOGGER.error(
                    "ACL operation timed out and stream did not quiesce; retaining buffers and requiring process restart"
                )
            raise
        except BaseException:
            if native_call_may_be_active and not self._synchronize_stream_unbounded():
                if cleanup_record is not None:
                    self._pending_run_cleanups.append(cleanup_record)
                    cleanup_record = None
                LOGGER.error(
                    "ACL operation failed and stream did not quiesce; retaining buffers"
                )
            raise
        finally:
            if cleanup_record is not None:
                with _without_execution_alarm():
                    cleanup_ok = self._destroy_run_buffers(cleanup_record)
                if not cleanup_ok:
                    self._poisoned = True
                    self._pending_run_cleanups.append(cleanup_record)

    def _allocate_buffer(
        self,
        size: int,
        dataset: Any,
        allocations: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if size <= 0:
            raise RuntimeUnavailable("ACL tensor byte size is invalid")
        malloc_flag = _required_constant(self.acl, "ACL_MEM_MALLOC_HUGE_FIRST")
        result = self.acl.rt.malloc(size, malloc_flag)
        pointer, ret = _split_result(result)
        _check_acl(ret, "acl.rt.malloc")
        allocation: Dict[str, Any] = {
            "pointer": pointer,
            "size": size,
            "data_buffer": None,
            "dataset": dataset,
        }
        if allocations is not None:
            # Register the pointer before any subsequent ACL call can fail or
            # be interrupted by the execution deadline.
            allocations.append(allocation)
        data_buffer = None
        try:
            data_buffer = self.acl.create_data_buffer(pointer, size)
            if data_buffer is None:
                raise RuntimeUnavailable("acl.create_data_buffer returned no buffer")
            allocation["data_buffer"] = data_buffer
            _check_acl(
                self.acl.mdl.add_dataset_buffer(dataset, data_buffer),
                "acl.mdl.add_dataset_buffer",
            )
            return allocation
        except BaseException:
            if data_buffer is not None:
                destroyed = _safe_call(
                    self.acl.destroy_data_buffer,
                    data_buffer,
                    label="acl.destroy_data_buffer allocation rollback",
                )
            else:
                destroyed = True
            if destroyed:
                allocation["data_buffer"] = None
            if destroyed:
                if _safe_call(self.acl.rt.free, pointer, label="acl.rt.free allocation rollback"):
                    allocation["pointer"] = None
            else:
                LOGGER.error("retaining ACL pointer after failed allocation rollback")
            raise

    def _destroy_run_buffers(self, record: Dict[str, Any]) -> bool:
        if self.acl is None:
            return False
        if self.context is not None and not _safe_call(
            self.acl.rt.set_context,
            self.context,
            label="acl.rt.set_context buffer cleanup",
        ):
            LOGGER.error("skipping ACL buffer destruction because context selection failed")
            return False
        buffers = list(record.get("allocations", []))
        cleanup_ok = True
        input_dataset = record.get("input_dataset")
        output_dataset = record.get("output_dataset")
        # Follow the CANN Python examples: release each user-owned device
        # allocation, destroy its aclDataBuffer wrapper, then destroy the
        # dataset container.  The wrapper does not own the device pointer.
        for allocation in buffers:
            pointer = allocation.get("pointer")
            if pointer is not None:
                if _safe_call(self.acl.rt.free, pointer, label="acl.rt.free"):
                    allocation["pointer"] = None
                else:
                    cleanup_ok = False
                    LOGGER.error("retaining ACL pointer after acl.rt.free failure")
            data_buffer = allocation.get("data_buffer")
            if data_buffer is not None:
                destroyed = _safe_call(
                    self.acl.destroy_data_buffer,
                    data_buffer,
                    label="acl.destroy_data_buffer",
                )
                allocation["data_buffer_destroyed"] = destroyed
                if destroyed:
                    allocation["data_buffer"] = None
                else:
                    cleanup_ok = False
                    LOGGER.error("retaining ACL data-buffer handle after destruction failure")
        for dataset_key, label in (
            ("input_dataset", "acl.mdl.destroy_dataset input"),
            ("output_dataset", "acl.mdl.destroy_dataset output"),
        ):
            dataset = record.get(dataset_key)
            if dataset is None:
                continue
            if _safe_call(self.acl.mdl.destroy_dataset, dataset, label=label):
                record[dataset_key] = None
            else:
                cleanup_ok = False
        return cleanup_ok

    def _synchronize_stream_unbounded(self) -> bool:
        """Quiesce the device before touching buffers after an interrupted call."""
        if self.acl is None or self.stream is None:
            return True
        synchronize_stream = getattr(self.acl.rt, "synchronize_stream", None)
        if not callable(synchronize_stream):
            return False
        try:
            with _without_execution_alarm():
                if self.context is not None:
                    _check_acl(self.acl.rt.set_context(self.context), "acl.rt.set_context quiesce")
                # Do not install the deadline signal here.  A blocking sync is
                # preferable to freeing memory while the NPU may still reference it.
                _check_acl(synchronize_stream(self.stream), "acl.rt.synchronize_stream quiesce")
            return True
        except Exception:
            LOGGER.error("ACL stream could not be synchronized", exc_info=True)
            return False

    def _retry_pending_run_cleanups(self) -> bool:
        if not self._pending_run_cleanups:
            return True
        if not self._synchronize_stream_unbounded():
            return False
        remaining: List[Dict[str, Any]] = []
        for record in self._pending_run_cleanups:
            if not self._destroy_run_buffers(record):
                remaining.append(record)
        self._pending_run_cleanups = remaining
        return not remaining

    def close(self) -> None:
        acl = self.acl
        if acl is None:
            return
        cleanup_failed = False
        if self._pending_run_cleanups:
            cleanup_failed = not self._retry_pending_run_cleanups()
            if cleanup_failed:
                LOGGER.error("retaining pending ACL run buffers; refusing model teardown")
                self._opened = False
                return
        if self.context is not None:
            cleanup_failed = not _safe_call(
                acl.rt.set_context,
                self.context,
                label="acl.rt.set_context cleanup",
            )
        if not cleanup_failed and self.stream is not None:
            synchronize_stream = getattr(acl.rt, "synchronize_stream", None)
            cleanup_failed = not _safe_call(
                synchronize_stream,
                self.stream,
                label="acl.rt.synchronize_stream cleanup",
            )
        if not cleanup_failed and self.model_id is not None:
            if _safe_call(acl.mdl.unload, self.model_id, label="acl.mdl.unload"):
                self.model_id = None
            else:
                cleanup_failed = True
        # The descriptor is tied to the loaded model.  Unload first, then
        # destroy the descriptor so a failed unload never leaves ACL using a
        # descriptor that has already been freed.
        if not cleanup_failed and self.desc is not None:
            if _safe_call(acl.mdl.destroy_desc, self.desc, label="acl.mdl.destroy_desc"):
                self.desc = None
            else:
                cleanup_failed = True
        if not cleanup_failed and self.stream is not None:
            if _safe_call(getattr(acl.rt, "destroy_stream", None), self.stream, label="acl.rt.destroy_stream"):
                self.stream = None
            else:
                cleanup_failed = True
        if not cleanup_failed and self.context is not None:
            if _safe_call(acl.rt.destroy_context, self.context, label="acl.rt.destroy_context"):
                self.context = None
            else:
                cleanup_failed = True
        # This service owns only its context and model.  Do not reset the
        # device from a loopback LLM process because another NPU service may
        # share the board.
        if not cleanup_failed:
            cleanup_failed = not _safe_call(getattr(acl, "finalize", None), label="acl.finalize")
        if cleanup_failed:
            LOGGER.error("ACL cleanup retained one or more handles for a later retry")
        self._opened = False
        if not cleanup_failed:
            self.acl = None

    def _contract_input_order_verified(self) -> bool:
        return bool(getattr(self, "_input_order_verified", False))


def _copy_host_to_device(acl: Any, pointer: Any, array: Any) -> None:
    source = _host_pointer(acl, array, writable=False)
    _check_acl(
        acl.rt.memcpy(
            pointer,
            int(array.nbytes),
            source,
            int(array.nbytes),
            _required_constant(acl, "ACL_MEMCPY_HOST_TO_DEVICE"),
        ),
        "acl.rt.memcpy host_to_device",
    )


def _copy_device_to_host(acl: Any, array: Any, pointer: Any, size: int) -> None:
    destination = _host_pointer(acl, array, writable=True)
    _check_acl(
        acl.rt.memcpy(
            destination,
            int(array.nbytes),
            pointer,
            int(size),
            _required_constant(acl, "ACL_MEMCPY_DEVICE_TO_HOST"),
        ),
        "acl.rt.memcpy device_to_host",
    )


def _host_pointer(acl: Any, array: Any, writable: bool) -> Any:
    util = getattr(acl, "util", None)
    converter = getattr(util, "numpy_to_ptr", None)
    if callable(converter):
        pointer = converter(array)
        if pointer is not None:
            return pointer
    return array.ctypes.data


def _parse_dims(value: Any) -> Tuple[int, ...]:
    value = _first_value(value)
    if isinstance(value, Mapping):
        dims = value.get("dims")
        count = value.get("dimCount", len(dims) if isinstance(dims, list) else 0)
        if not isinstance(dims, (list, tuple)) or not isinstance(count, int):
            raise RuntimeUnavailable("ACL dimensions descriptor is malformed")
        dims = dims[:count]
    elif isinstance(value, (list, tuple)):
        dims = value
    else:
        raise RuntimeUnavailable("ACL dimensions descriptor is malformed")
    shape = tuple(int(dimension) for dimension in dims)
    if not shape or any(dimension <= 0 for dimension in shape):
        raise RuntimeUnavailable("ACL dimensions contain a dynamic or invalid value")
    return shape


def _acl_dtype(acl: Any, value: int) -> str:
    names = {
        _required_constant(acl, "ACL_INT64"): "int64",
        _required_constant(acl, "ACL_FLOAT16"): "float16",
    }
    if value not in names:
        raise RuntimeUnavailable(f"unsupported ACL tensor data type code: {value}")
    return names[value]


def _required_constant(module: Any, name: str) -> int:
    """Resolve a CANN enum exposed by PyACL or its documented C value.

    The board's CANN 8.0 ``acl.so`` does not export the C header enums as
    Python attributes.  The fallback values below are the values from
    ``acl_base.h``/``acl_rt.h`` for the exact API used here; keeping them in a
    named table avoids silently using an arbitrary default.
    """
    value = getattr(module, name, None)
    if value is None:
        # Some PyACL releases expose runtime constants under ``acl.rt`` while
        # dtype constants remain on the top-level module.  Both are accepted
        # only when the binding explicitly provides the named attribute.
        value = getattr(getattr(module, "rt", None), name, None)
    if isinstance(value, int) and not isinstance(value, bool):
        return int(value)
    documented = {
        # aclrtMemcpyKind in acl_rt.h
        "ACL_MEMCPY_HOST_TO_DEVICE": 1,
        "ACL_MEMCPY_DEVICE_TO_HOST": 2,
        # aclrtMemMallocPolicy in acl_rt.h
        "ACL_MEM_MALLOC_HUGE_FIRST": 0,
        # aclDataType in acl_base.h
        "ACL_FLOAT": 0,
        "ACL_FLOAT16": 1,
        "ACL_INT64": 9,
    }
    if name in documented:
        return documented[name]
    raise RuntimeUnavailable(f"CANN binding does not expose required constant {name}")


def _numpy_dtype(dtype: str) -> Any:
    import numpy as np  # type: ignore

    return {"int64": np.int64, "float16": np.float16, "float32": np.float32}[dtype]


def _split_result(value: Any) -> Tuple[Any, int]:
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], int):
        return value[0], int(value[1])
    return value, 0


def _first_value(value: Any) -> Any:
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], int):
        _check_acl(value[1], "ACL call")
        return value[0]
    return value


def _check_acl(value: Any, operation: str) -> None:
    if value is None:
        return
    if isinstance(value, tuple) and len(value) == 2:
        value = value[1]
    if isinstance(value, bool):
        if not value:
            raise RuntimeUnavailable(f"{operation} failed")
        return
    if isinstance(value, int) and value != 0:
        raise RuntimeUnavailable(f"{operation} failed with ACL status {value}")


def _safe_call(function: Any, *args: Any, label: str = "ACL cleanup", retries: int = 2) -> bool:
    if not callable(function):
        LOGGER.error("%s is unavailable; retaining ACL handles", label)
        return False
    for attempt in range(1, max(1, retries) + 1):
        try:
            _check_acl(function(*args), label)
            return True
        except Exception:
            if attempt < max(1, retries):
                LOGGER.warning("%s failed; retrying (%d/%d)", label, attempt, retries)
            else:
                LOGGER.error("%s failed after %d attempts", label, retries, exc_info=True)
    return False
