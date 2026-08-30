"""Torch-free ACL runtime for the Qwen2.5 static full-context graph.

The graph has no KV inputs.  Every decode step sends one fixed-shape batch and
reads the logits at the last real token.  This is intentionally slower than a
KV graph, but keeps the board contract explicit and easy to audit.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Protocol, Sequence, Tuple, Union

from qwen25_acl_contract import (
    ContractError,
    QWEN25_SUPPORTED_LAST_LOGITS_EXECUTION_MODE,
    Qwen25Contract,
)
from qwen25_tokenizer import Qwen25Tokenizer, TokenizerError


class Qwen25RuntimeError(RuntimeError):
    pass


class RuntimeUnavailable(Qwen25RuntimeError):
    pass


class RuntimeBusy(Qwen25RuntimeError):
    pass


class RuntimeRequestError(Qwen25RuntimeError):
    pass


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
    finish_reason: str


class Backend(Protocol):
    def open(self, model_path: Path) -> RuntimeDescriptor: ...
    def run(self, inputs: Mapping[str, Any]) -> Any: ...
    def close(self) -> None: ...


class Qwen25AclRuntime:
    """Serial, descriptor-checked runtime for one static Qwen2.5 OM."""

    def __init__(
        self,
        om_path: Union[str, Path],
        tokenizer_path: Union[str, Path],
        *,
        contract_path: Union[str, Path],
        tokenizer_config_path: Union[str, Path],
        backend: Optional[Backend] = None,
        tokenizer: Optional[Any] = None,
        max_tokens: int = 8,
        device_id: int = 0,
    ) -> None:
        self.om_path = Path(om_path).expanduser()
        self.tokenizer_path = Path(tokenizer_path).expanduser()
        self.tokenizer_config_path = Path(tokenizer_config_path).expanduser()
        if not self.om_path.is_file() or not self.tokenizer_path.is_file():
            raise RuntimeUnavailable("OM and tokenizer files are required")
        try:
            self.contract = Qwen25Contract.load(contract_path)
            self.contract.validate_static_expectations(require_descriptors=True)
        except (ContractError, OSError) as exc:
            raise RuntimeUnavailable(str(exc)) from exc
        if not 1 <= int(max_tokens) <= 32:
            raise RuntimeUnavailable("max_tokens must be between 1 and 32")
        self.max_tokens = int(max_tokens)
        self._tokenizer = tokenizer
        self._backend: Backend = backend or _native_backend(device_id)
        self._descriptor: Optional[RuntimeDescriptor] = None
        self._started = False
        self._closed = False
        self._lock = threading.Lock()
        self._cancel = threading.Event()

    @property
    def model_id(self) -> str:
        return self.contract.model_id

    @property
    def started(self) -> bool:
        return self._started and not self._closed

    @property
    def descriptor(self) -> Optional[RuntimeDescriptor]:
        return self._descriptor

    def start(self) -> None:
        if self.started:
            return
        if self._closed:
            raise RuntimeUnavailable("runtime is closed")
        try:
            descriptor = _coerce_descriptor(self._backend.open(self.om_path))
            if descriptor is None:
                raise RuntimeUnavailable("backend returned no descriptor")
            self.contract.validate_descriptor(descriptor.inputs, descriptor.outputs)
            if self._tokenizer is None:
                self._tokenizer = Qwen25Tokenizer(
                    self.tokenizer_path, self.tokenizer_config_path
                )
            self._validate_tokenizer(self._tokenizer)
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
            raise RuntimeUnavailable(f"ACL initialization failed: {type(exc).__name__}") from exc

    def close(self) -> None:
        if self._closed and self._descriptor is None:
            return
        self._closed = True
        self._started = False
        self._cancel.set()
        # Mark cancellation before waiting for the operation lock.  A request
        # currently inside ACL will observe the event at its next decode step;
        # the backend is only closed after that request releases the lock.
        with self._lock:
            self._descriptor = None
            self._backend.close()

    def cancel(self) -> None:
        self._cancel.set()

    def status(self) -> Dict[str, Any]:
        output_name = None
        if self._descriptor is not None and self._descriptor.outputs:
            output_name = self._descriptor.outputs[0].name
        return {
            "ready": self.started,
            "model": self.model_id,
            "backend": "acl_om",
            "execution_mode": (
                self.contract.execution_mode if self.contract is not None else "full_context_static"
            ),
            "precision": self.contract.logits_dtype,
            "sequence_length": self.contract.static_sequence_length,
            "vocabulary_size": self.contract.vocabulary_size,
            "descriptor_validated": self._descriptor is not None,
            "descriptor_output_name": output_name,
        }

    def validate_prompt_budget(self, messages: Sequence[Mapping[str, Any]], max_tokens: int) -> int:
        if not self.started:
            raise RuntimeUnavailable("runtime is not ready")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= self.max_tokens:
            raise RuntimeRequestError(f"max_tokens must be between 1 and {self.max_tokens}")
        ids = self._encode(messages)
        if len(ids) + max_tokens > self.contract.static_sequence_length:
            raise RuntimeRequestError("prompt plus max_tokens exceeds the fixed context")
        return len(ids)

    def complete(self, messages: Sequence[Mapping[str, Any]], max_tokens: Optional[int] = None) -> GenerationResult:
        last = GenerationResult("", 0, 0, "length")
        for item in self.stream(messages, max_tokens=max_tokens):
            last = item
        return last

    def stream(self, messages: Sequence[Mapping[str, Any]], max_tokens: Optional[int] = None) -> Iterator[GenerationResult]:
        if not self.started:
            raise RuntimeUnavailable("runtime is not ready")
        limit = self.max_tokens if max_tokens is None else max_tokens
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= self.max_tokens:
            raise RuntimeRequestError(f"max_tokens must be between 1 and {self.max_tokens}")
        if not self._lock.acquire(blocking=False):
            raise RuntimeBusy("another NPU operation is running")
        try:
            if self._closed or not self._started:
                raise RuntimeUnavailable("runtime is not ready")
            # Cancellation is request-scoped.  A client disconnect must not
            # poison the next independent request handled by the service.
            self._cancel.clear()
            prompt_ids = self._encode(messages)
            if len(prompt_ids) + limit > self.contract.static_sequence_length:
                raise RuntimeRequestError("prompt plus max_tokens exceeds the fixed context")
            generated: List[int] = []
            previous = ""
            stop = "length"
            for _ in range(limit):
                if self._cancel.is_set():
                    raise RuntimeRequestError("generation cancelled")
                all_ids = prompt_ids + generated
                logits = self._run(all_ids)
                next_id = int(_argmax(logits, self.contract.vocabulary_size))
                tokenizer_vocab = int(self._tokenizer.vocab_size)
                if next_id >= tokenizer_vocab:
                    raise RuntimeUnavailable(
                        "model selected a token outside tokenizer vocabulary"
                    )
                generated.append(next_id)
                if next_id == self._tokenizer.eos_token_id:
                    stop = "stop"
                try:
                    text = str(self._tokenizer.decode(generated))
                except TokenizerError as exc:
                    raise RuntimeUnavailable("generated token decoding failed") from exc
                if text != previous:
                    previous = text
                    yield GenerationResult(text, len(prompt_ids), len(generated), stop)
                if stop == "stop":
                    break
            if not generated:
                yield GenerationResult("", len(prompt_ids), 0, stop)
            elif previous == "":
                yield GenerationResult(previous, len(prompt_ids), len(generated), stop)
        finally:
            self._lock.release()

    def _encode(self, messages: Sequence[Mapping[str, Any]]) -> List[int]:
        if self._tokenizer is None:
            raise RuntimeUnavailable("tokenizer is not initialized")
        try:
            ids = [int(value) for value in self._tokenizer.encode_messages(messages)]
        except (TokenizerError, ValueError) as exc:
            raise RuntimeRequestError(str(exc)) from exc
        if not ids or any(value < 0 or value >= self.contract.vocabulary_size for value in ids):
            raise RuntimeRequestError("prompt contains an invalid token ID")
        return ids

    def _run(self, token_ids: Sequence[int]) -> Any:
        if self._descriptor is None or len(token_ids) > self.contract.static_sequence_length:
            raise RuntimeRequestError("context exceeds the static sequence length")
        np = _numpy()
        sequence = self.contract.static_sequence_length
        ids = np.full((1, sequence), int(self._tokenizer.pad_token_id), dtype=np.int64)
        mask = np.zeros((1, sequence), dtype=np.int64)
        pos = np.zeros((1, sequence), dtype=np.int64)
        length = len(token_ids)
        ids[0, :length] = np.asarray(token_ids, dtype=np.int64)
        mask[0, :length] = 1
        pos[0, :length] = np.arange(length, dtype=np.int64)
        values = {"input_ids": ids, "attention_mask": mask, "position_ids": pos}
        prepared: Dict[str, Any] = {}
        for item in self._descriptor.inputs:
            value = values.get(item.name)
            if value is None:
                raise RuntimeUnavailable(f"missing input {item.name}")
            value = np.ascontiguousarray(value, dtype=np.int64)
            if tuple(value.shape) != item.shape or (item.byte_size is not None and value.nbytes != item.byte_size):
                raise RuntimeUnavailable(f"input {item.name} violates descriptor")
            prepared[item.name] = value
        raw = self._backend.run(prepared)
        if isinstance(raw, Mapping):
            raw = [raw[item.name] for item in self._descriptor.outputs]
        elif len(self._descriptor.outputs) == 1 and not isinstance(raw, (list, tuple)):
            raw = [raw]
        if not isinstance(raw, (list, tuple)) or len(raw) != len(self._descriptor.outputs):
            raise RuntimeUnavailable("backend returned an invalid output list")
        output = np.asarray(raw[self.contract.logits_output_index])
        expected = self.contract.logits_output
        if tuple(output.shape) != expected.shape or str(output.dtype) != _numpy_dtype_name(expected.dtype):
            raise RuntimeUnavailable("logits violate the inspected descriptor")
        if self.contract.execution_mode == QWEN25_SUPPORTED_LAST_LOGITS_EXECUTION_MODE:
            if tuple(output.shape[1:]) != (1, self.contract.vocabulary_size):
                raise RuntimeUnavailable("last-logits output does not have shape [1,1,V]")
            row = output[0, 0, :]
        else:
            row = output[0, length - 1, :]
        if not np.isfinite(row).all():
            raise RuntimeUnavailable("logits contain non-finite values")
        return row

    def _validate_tokenizer(self, tokenizer: Any) -> None:
        vocab = int(tokenizer.vocab_size)
        if vocab > self.contract.vocabulary_size:
            raise RuntimeUnavailable("tokenizer vocabulary exceeds model vocabulary")
        for name in ("eos_token_id", "pad_token_id", "im_start_id", "im_end_id"):
            value = getattr(tokenizer, name, None)
            if not isinstance(value, int) or not 0 <= value < self.contract.vocabulary_size:
                raise RuntimeUnavailable(f"tokenizer {name} is outside model vocabulary")


def _numpy() -> Any:
    try:
        import numpy as np  # type: ignore
        return np
    except ImportError as exc:
        raise RuntimeUnavailable("numpy is required by the ACL runtime") from exc


def _numpy_dtype_name(dtype: str) -> str:
    return {"float16": "float16", "float32": "float32"}.get(dtype, dtype)


def _argmax(values: Any, vocabulary_size: int) -> int:
    np = _numpy()
    row = np.asarray(values)
    if row.ndim != 1 or row.shape[0] != vocabulary_size or not np.isfinite(row).all():
        raise RuntimeUnavailable("invalid logits vector")
    return int(np.argmax(row))


def _native_backend(device_id: int) -> Backend:
    # The existing low-level adapter is only a PyACL buffer/descriptor helper;
    # this module owns the Qwen2.5 contract and never imports its legacy model.
    from acl_om_runtime import NativeAclBackend

    return NativeAclBackend(device_id=int(device_id), input_order_verified=True)


def _coerce_descriptor(value: Any) -> Optional[RuntimeDescriptor]:
    """Convert the low-level adapter descriptor without importing its model contract."""
    if isinstance(value, RuntimeDescriptor):
        return value
    inputs = getattr(value, "inputs", None)
    outputs = getattr(value, "outputs", None)
    if not isinstance(inputs, (list, tuple)) or not isinstance(outputs, (list, tuple)):
        return None
    try:
        def convert(item: Any) -> TensorDescriptor:
            return TensorDescriptor(str(item.name), str(item.dtype).lower(), tuple(int(x) for x in item.shape), getattr(item, "byte_size", None))
        return RuntimeDescriptor(tuple(convert(item) for item in inputs), tuple(convert(item) for item in outputs))
    except (AttributeError, TypeError, ValueError):
        return None


__all__ = [
    "Qwen25AclRuntime", "Qwen25RuntimeError", "RuntimeUnavailable",
    "RuntimeBusy", "RuntimeRequestError", "TensorDescriptor", "RuntimeDescriptor",
    "GenerationResult",
]
