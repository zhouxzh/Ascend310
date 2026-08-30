"""Serial, Torch-free ACL runtime for the TinyLlama static-KV OM.

This adapter deliberately does not import ``acl`` or ``numpy`` at module
import time.  That keeps controller-side tests useful while ensuring the
board process fails closed when the CANN environment is not sourced.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import importlib
import hashlib
import json
import logging
from pathlib import Path
import signal
import threading
import time
import warnings
from typing import Any, Dict, Iterator, List, Mapping, Optional, Protocol, Sequence, Tuple, Union

from tinyllama_acl_contract import (
    ContractError,
    HEAD_DIM,
    INPUT_ORDER,
    MAX_SEQUENCE_LENGTH,
    MODEL_ID,
    NUM_KV_HEADS,
    NUM_LAYERS,
    VOCABULARY_SIZE,
    TensorContract,
    TinyLlamaContract,
)
from tinyllama_tokenizer import TinyLlamaTokenizer, TokenizerError


LOGGER = logging.getLogger("case9.tinyllama_acl")
REQUIRED_TOKENIZER_FILES = frozenset(
    {"tokenizer.json", "tokenizer.model", "special_tokens_map.json", "tokenizer_config.json"}
)
MAX_EXECUTION_TIMEOUT_SECONDS = 50.0
# The precompiled 310B OM is currently measured at roughly 24-26 seconds for
# eight generated tokens.  Keep the default bounded so a request cannot
# predictably outlive the 50-second ACL deadline.  Callers may still opt into
# the historical 32-token ceiling explicitly when they also raise the
# execution deadline within the hard limit.
DEFAULT_MAX_GENERATION_TOKENS = 8
HARD_MAX_GENERATION_TOKENS = 32


class RuntimeErrorBase(RuntimeError):
    """Base class for sanitized runtime failures."""


class RuntimeUnavailable(RuntimeErrorBase):
    pass


class RuntimeBusy(RuntimeErrorBase):
    pass


class RuntimeRequestError(RuntimeErrorBase):
    pass


class RuntimeExecutionTimeout(RuntimeErrorBase):
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
    # OpenAI-compatible terminal reason.  The default preserves compatibility
    # with older test doubles and the legacy ACL service.
    finish_reason: str = "stop"


class Backend(Protocol):
    def open(self, model_path: Path) -> RuntimeDescriptor:
        ...

    def run(self, inputs: Mapping[str, Any]) -> Any:
        ...

    def close(self) -> None:
        ...


class TinyLlamaAclRuntime:
    """One-model, one-request-at-a-time TinyLlama runner."""

    def __init__(
        self,
        om_path: Union[str, Path],
        tokenizer_path: Union[str, Path],
        *,
        contract_path: Optional[Union[str, Path]] = None,
        backend: Optional[Backend] = None,
        tokenizer: Optional[Any] = None,
        tokenizer_config_path: Optional[Union[str, Path]] = None,
        tokenizer_manifest_path: Optional[Union[str, Path]] = None,
        device_id: int = 0,
        max_tokens: int = DEFAULT_MAX_GENERATION_TOKENS,
        execution_timeout_seconds: float = MAX_EXECUTION_TIMEOUT_SECONDS,
        verify_artifact: bool = True,
    ) -> None:
        self.om_path = Path(om_path).expanduser()
        self.tokenizer_path = Path(tokenizer_path).expanduser()
        self.contract_path = Path(contract_path).expanduser() if contract_path else None
        self.tokenizer_config_path = (
            Path(tokenizer_config_path).expanduser()
            if tokenizer_config_path is not None
            else None
        )
        self.tokenizer_manifest_path = (
            Path(tokenizer_manifest_path).expanduser()
            if tokenizer_manifest_path is not None
            else None
        )
        if not self.om_path.is_file():
            raise RuntimeUnavailable(f"OM file does not exist: {self.om_path}")
        if not self.tokenizer_path.is_file():
            raise RuntimeUnavailable(f"tokenizer file does not exist: {self.tokenizer_path}")
        if (
            isinstance(max_tokens, bool)
            or not isinstance(max_tokens, int)
            or not 1 <= max_tokens <= HARD_MAX_GENERATION_TOKENS
        ):
            raise RuntimeUnavailable(f"max_tokens must be between 1 and {HARD_MAX_GENERATION_TOKENS}")
        if execution_timeout_seconds <= 0 or execution_timeout_seconds > MAX_EXECUTION_TIMEOUT_SECONDS:
            raise RuntimeUnavailable(
                f"execution_timeout_seconds must be between 0 and {MAX_EXECUTION_TIMEOUT_SECONDS}"
            )
        self.max_tokens = int(max_tokens)
        self.execution_timeout_seconds = float(execution_timeout_seconds)
        self.device_id = int(device_id)
        self.verify_artifact = bool(verify_artifact)
        self.contract: Optional[TinyLlamaContract] = None
        if self.contract_path is not None:
            try:
                self.contract = TinyLlamaContract.load(self.contract_path)
                self.contract.validate_static_expectations(strict_dimensions=True)
            except ContractError as exc:
                raise RuntimeUnavailable(str(exc)) from exc
        self._backend: Backend = backend or NativeTinyLlamaBackend(
            device_id=self.device_id,
            input_names=tuple(self.contract.input_map) if self.contract and self.contract.inputs else INPUT_ORDER,
            output_names=tuple(item.name for item in self.contract.outputs) if self.contract and self.contract.outputs else None,
        )
        self._tokenizer = tokenizer
        self._descriptor: Optional[RuntimeDescriptor] = None
        self._started = False
        self._closed = False
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._last_stop_reason = "stop"

    @property
    def model_id(self) -> str:
        return MODEL_ID

    @property
    def started(self) -> bool:
        return (
            self._started
            and not self._closed
            and not bool(getattr(self._backend, "poisoned", False))
            and not bool(getattr(self._backend, "cleanup_failed", False))
        )

    @property
    def descriptor(self) -> Optional[RuntimeDescriptor]:
        return self._descriptor

    @property
    def last_stop_reason(self) -> str:
        """Reason for the most recent successful generation."""

        return self._last_stop_reason

    def start(self) -> None:
        if self.started:
            return
        if self._closed:
            raise RuntimeUnavailable("TinyLlama runtime has been closed")
        self._cancel.clear()
        try:
            if self.verify_artifact:
                if self.tokenizer_manifest_path is None:
                    raise RuntimeUnavailable(
                        "artifact verification requires the tokenizer manifest"
                    )
                if self.contract is None:
                    raise RuntimeUnavailable(
                        "artifact verification requires a bound TinyLlama contract"
                    )
                if (
                    self.contract.source_bytes is None
                    or self.contract.source_sha256 is None
                    or not self.contract.source_revision
                ):
                    raise RuntimeUnavailable(
                        "TinyLlama contract is missing source bytes, SHA-256, or revision"
                    )
                _verify_source_artifact(self.om_path, self.contract)
                _verify_om_manifest(
                    self.om_path,
                    self.tokenizer_manifest_path,
                    self.contract,
                )
                _verify_tokenizer_manifest(
                    self.tokenizer_path,
                    self.tokenizer_manifest_path,
                    expected_revision=self.contract.source_revision if self.contract else None,
                )
                if self.tokenizer_config_path is not None:
                    tokenizer_root = self.tokenizer_path.resolve().parent
                    config_path = self.tokenizer_config_path.resolve()
                    if config_path != tokenizer_root / "tokenizer_config.json":
                        raise RuntimeUnavailable(
                            "TinyLlama tokenizer config must be the manifest-bound tokenizer_config.json"
                        )
            descriptor = self._backend.open(self.om_path)
            if not isinstance(descriptor, RuntimeDescriptor):
                raise RuntimeUnavailable("ACL backend returned no model descriptor")
            if self.contract is None:
                self.contract = _contract_from_descriptor(descriptor)
            self.contract.validate_static_expectations(strict_dimensions=True)
            if self.contract.inputs or self.contract.outputs:
                try:
                    self.contract.validate_descriptor(descriptor.inputs, descriptor.outputs)
                except ContractError as exc:
                    raise RuntimeUnavailable(str(exc)) from exc
            _validate_runtime_descriptor(descriptor, self.contract)
            if self._tokenizer is None:
                config_path = self.tokenizer_config_path or self._find_config_path(self.tokenizer_path)
                self._tokenizer = TinyLlamaTokenizer(
                    self.tokenizer_path,
                    config_path,
                )
            vocab = getattr(self._tokenizer, "vocab_size", None)
            if vocab is None:
                raise RuntimeUnavailable("tokenizer does not expose a vocabulary size")
            if int(vocab) != self.contract.vocabulary_size:
                raise RuntimeUnavailable(
                    "tokenizer vocabulary does not match OM logits vocabulary: "
                    f"{int(vocab)} != {self.contract.vocabulary_size}"
                )
            for token_name in ("bos_token_id", "eos_token_id", "pad_token_id"):
                if not hasattr(self._tokenizer, token_name):
                    raise RuntimeUnavailable(f"tokenizer has no {token_name}")
                actual_id = getattr(self._tokenizer, token_name)
                expected_id = getattr(self.contract, token_name)
                if isinstance(actual_id, bool) or not isinstance(actual_id, int):
                    raise RuntimeUnavailable(f"tokenizer {token_name} is not an integer")
                if int(actual_id) != int(expected_id):
                    raise RuntimeUnavailable(
                        f"tokenizer {token_name} does not match the OM contract: "
                        f"{int(actual_id)} != {int(expected_id)}"
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
            raise RuntimeUnavailable(f"TinyLlama ACL initialization failed: {type(exc).__name__}") from exc

    def close(self) -> None:
        if self._closed and not getattr(self._backend, "acl", None):
            return
        self._closed = True
        self._started = False
        self._cancel.set()
        self._descriptor = None
        self._backend.close()

    def cancel(self) -> None:
        self._cancel.set()

    def validate_prompt_budget(
        self, messages: Sequence[Mapping[str, Any]], max_tokens: int
    ) -> int:
        """Validate/tokenize a request before an HTTP streaming response starts."""

        if not self.started:
            raise RuntimeUnavailable("TinyLlama ACL runtime is not ready")
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= self.max_tokens:
            raise RuntimeRequestError(f"max_tokens must be between 1 and {self.max_tokens}")
        tokenizer = self._tokenizer
        contract = self.contract
        if tokenizer is None or contract is None:
            raise RuntimeUnavailable("TinyLlama runtime is not initialized")
        try:
            prompt_ids = [int(item) for item in tokenizer.encode_messages(messages)]
        except (TokenizerError, ValueError) as exc:
            raise RuntimeRequestError(str(exc)) from exc
        if not prompt_ids:
            raise RuntimeRequestError("prompt produced no token IDs")
        if any(item < 0 or item >= contract.vocabulary_size for item in prompt_ids):
            raise RuntimeRequestError("prompt contains a token ID outside the OM vocabulary")
        if len(prompt_ids) + max_tokens > contract.max_sequence_length:
            raise RuntimeRequestError(
                f"prompt plus max_tokens exceeds the fixed {contract.max_sequence_length}-token context"
            )
        return len(prompt_ids)

    def status(self) -> Dict[str, Any]:
        return {
            "ready": self.started,
            "model": self.model_id,
            "backend": "acl_om",
            "execution_mode": "kv_cache_token",
            "contract_schema_version": 1,
            "om": self.om_path.name,
            "descriptor_validated": self._descriptor is not None,
            "restart_required": bool(
                getattr(self._backend, "poisoned", False)
                or getattr(self._backend, "cleanup_failed", False)
            ),
            "cleanup_failed": bool(getattr(self._backend, "cleanup_failed", False)),
            "max_sequence_length": self.contract.max_sequence_length if self.contract else MAX_SEQUENCE_LENGTH,
        }

    def complete(self, messages: Sequence[Mapping[str, Any]], max_tokens: Optional[int] = None) -> GenerationResult:
        text = ""
        prompt = completion = 0
        for text, prompt, completion in self.stream(messages, max_tokens=max_tokens):
            pass
        return GenerationResult(text, prompt, completion, self._last_stop_reason)

    def stream(
        self,
        messages: Sequence[Mapping[str, Any]],
        max_tokens: Optional[int] = None,
    ) -> Iterator[Tuple[str, int, int]]:
        if not self.started:
            raise RuntimeUnavailable("TinyLlama ACL runtime is not ready")
        limit = self.max_tokens if max_tokens is None else max_tokens
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= self.max_tokens:
            raise RuntimeRequestError(f"max_tokens must be between 1 and {self.max_tokens}")
        if not self._lock.acquire(blocking=False):
            raise RuntimeBusy("another NPU operation is already running")
        try:
            self._cancel.clear()
            tokenizer = self._tokenizer
            contract = self.contract
            descriptor = self._descriptor
            if tokenizer is None or contract is None or descriptor is None:
                raise RuntimeUnavailable("TinyLlama runtime is not initialized")
            try:
                prompt_ids = [int(item) for item in tokenizer.encode_messages(messages)]
            except (TokenizerError, ValueError) as exc:
                raise RuntimeRequestError(str(exc)) from exc
            if not prompt_ids:
                raise RuntimeRequestError("prompt produced no token IDs")
            if any(item < 0 or item >= contract.vocabulary_size for item in prompt_ids):
                raise RuntimeRequestError("prompt contains a token ID outside the OM vocabulary")
            if len(prompt_ids) + limit > contract.max_sequence_length:
                raise RuntimeRequestError(
                    f"prompt plus max_tokens exceeds the fixed {contract.max_sequence_length}-token context"
                )
            cache_descriptor = _cache_input_descriptor(descriptor, contract)
            np = _numpy()
            cache = np.zeros(cache_descriptor.shape, dtype=_numpy_dtype(cache_descriptor.dtype, np))
            generated: List[int] = []
            real_length = 0
            stop_reason = "length"
            self._last_stop_reason = "stop"
            # The model is intentionally executed token-by-token.  Bound the
            # whole request as well as each individual ACL call so a long
            # prompt cannot monopolize the single-process service indefinitely.
            request_deadline = time.monotonic() + self.execution_timeout_seconds
            # Prefill is deliberately token-by-token because this OM accepts
            # input_ids [1, 1], not a dynamic prompt sequence.
            next_id: Optional[int] = None
            for token_id in prompt_ids:
                if self._cancel.is_set():
                    raise RuntimeRequestError("generation was cancelled")
                logits, cache = self._run_step(
                    token_id, real_length, cache, descriptor, contract, request_deadline
                )
                real_length += 1
                next_id = _greedy_next_id(logits, contract.vocabulary_size, np)
            if next_id is None:
                raise RuntimeUnavailable("TinyLlama produced no logits")
            for _ in range(limit):
                if self._cancel.is_set():
                    raise RuntimeRequestError("generation was cancelled")
                token_id = int(next_id)
                generated.append(token_id)
                if token_id == contract.eos_token_id:
                    stop_reason = "stop"
                    break
                if len(generated) >= limit or real_length >= contract.max_sequence_length:
                    stop_reason = "length"
                    break
                logits, cache = self._run_step(
                    token_id, real_length, cache, descriptor, contract, request_deadline
                )
                real_length += 1
                next_id = _greedy_next_id(logits, contract.vocabulary_size, np)
            if not generated:
                yield "", len(prompt_ids), 0
            else:
                # Decode only the complete generated sequence.  Decoding a
                # partial SentencePiece/BPE sequence can emit U+FFFD and a
                # later cumulative decode can change its prefix; exposing
                # those intermediate strings as deltas duplicates text in an
                # OpenAI SSE client.  The first implementation therefore
                # emits one correct, complete delta after the NPU loop.
                try:
                    text = _strip_stop_markers(str(tokenizer.decode(generated)))
                except Exception as exc:
                    raise RuntimeRequestError("tokenizer decode failed") from exc
                self._last_stop_reason = stop_reason
                yield text, len(prompt_ids), len(generated)
        finally:
            self._lock.release()

    def _run_step(
        self,
        token_id: int,
        real_length: int,
        cache: Any,
        descriptor: RuntimeDescriptor,
        contract: TinyLlamaContract,
        request_deadline: Optional[float] = None,
    ) -> Tuple[Any, Any]:
        np = _numpy()
        if real_length >= contract.max_sequence_length:
            raise RuntimeRequestError("KV cache reached its maximum length")
        arrays: Dict[str, Any] = {
            "input_ids": np.asarray([[token_id]], dtype=np.int64),
            "attention_mask": _attention_mask(real_length, contract.mask_length, np),
            "position_ids": np.asarray([[real_length]], dtype=np.int64),
            "past_key_values": np.ascontiguousarray(cache),
        }
        for item in descriptor.inputs:
            value = arrays.get(item.name)
            if value is None:
                raise RuntimeUnavailable(f"missing TinyLlama input {item.name}")
            value = np.ascontiguousarray(value, dtype=_numpy_dtype(item.dtype, np))
            if tuple(value.shape) != item.shape:
                raise RuntimeUnavailable(f"input {item.name} has shape {tuple(value.shape)}, expected {item.shape}")
            if item.byte_size is not None and int(value.nbytes) != item.byte_size:
                raise RuntimeUnavailable(f"input {item.name} has invalid byte size")
            arrays[item.name] = value
        timeout_seconds = self.execution_timeout_seconds
        if request_deadline is not None:
            timeout_seconds = request_deadline - time.monotonic()
            if timeout_seconds <= 0:
                raise RuntimeExecutionTimeout("TinyLlama request execution timed out")
        with _execution_deadline(timeout_seconds):
            raw_outputs = self._backend.run(arrays)
        outputs = _normalize_outputs(raw_outputs, descriptor)
        logits = outputs[contract.logits_output_index]
        new_cache = _extract_cache(outputs, contract, cache, real_length, np)
        return logits, new_cache

    @staticmethod
    def _find_config_path(tokenizer_path: Path) -> Optional[Path]:
        candidate = tokenizer_path.with_name("tokenizer_config.json")
        return candidate if candidate.is_file() else None


def _contract_from_descriptor(descriptor: RuntimeDescriptor) -> TinyLlamaContract:
    if len(descriptor.inputs) != 4:
        raise RuntimeUnavailable("TinyLlama OM must expose exactly four inputs")
    names = tuple(item.name for item in descriptor.inputs)
    if names != INPUT_ORDER:
        raise RuntimeUnavailable("TinyLlama OM input order cannot be verified")
    cache = _cache_input_descriptor(descriptor, None)
    if cache.dtype != "float16" or len(cache.shape) != 6:
        raise RuntimeUnavailable("TinyLlama past_key_values must be a float16 rank-6 tensor")
    if cache.shape != (NUM_LAYERS, 2, 1, NUM_KV_HEADS, MAX_SEQUENCE_LENGTH, HEAD_DIM):
        raise RuntimeUnavailable(f"unsupported TinyLlama KV shape: {cache.shape}")
    logits_index = _find_logits_index(descriptor.outputs, VOCABULARY_SIZE)
    kv_candidates = [
        index for index, item in enumerate(descriptor.outputs)
        if index != logits_index and item.dtype == "float16" and _looks_like_kv_output(item.shape)
    ]
    if len(kv_candidates) != 1:
        raise RuntimeUnavailable("TinyLlama OM must expose updated KV cache output")
    kv_indices = (kv_candidates[0],)
    return TinyLlamaContract.from_descriptor(
        descriptor.inputs,
        descriptor.outputs,
        vocabulary_size=VOCABULARY_SIZE,
    ).__class__(
        inputs=tuple(_as_contract(item) for item in descriptor.inputs),
        outputs=tuple(_as_contract(item) for item in descriptor.outputs),
        logits_output_index=logits_index,
        kv_output_indices=kv_indices,
        input_order_verified=True,
    )


def _as_contract(item: TensorDescriptor) -> TensorContract:
    return TensorContract(item.name, item.dtype, item.shape, item.byte_size)


def _validate_runtime_descriptor(descriptor: RuntimeDescriptor, contract: TinyLlamaContract) -> None:
    if len(descriptor.inputs) != 4:
        raise RuntimeUnavailable("TinyLlama OM must expose exactly four inputs")
    names = tuple(item.name for item in descriptor.inputs)
    if names != INPUT_ORDER:
        raise RuntimeUnavailable("TinyLlama OM input order/names are not admitted")
    expected_shapes = {
        "input_ids": (1, 1),
        "attention_mask": (1, contract.mask_length),
        "position_ids": (1, 1),
    }
    for item in descriptor.inputs:
        if item.name in expected_shapes and (item.dtype != "int64" or item.shape != expected_shapes[item.name]):
            raise RuntimeUnavailable(f"TinyLlama input {item.name} has unsupported descriptor {item.shape}/{item.dtype}")
        if item.name == "past_key_values":
            if item.dtype != "float16" or len(item.shape) != 6:
                raise RuntimeUnavailable("TinyLlama past_key_values descriptor is not float16 rank-6")
            if (
                item.shape[0:4] != (contract.num_layers, 2, 1, contract.num_kv_heads)
                or item.shape[4] != contract.max_sequence_length
                or item.shape[-1] != contract.head_dim
            ):
                raise RuntimeUnavailable(f"TinyLlama KV descriptor is not admitted: {item.shape}")
        _validate_byte_size(item)
    if not descriptor.outputs:
        raise RuntimeUnavailable("TinyLlama OM has no outputs")
    if contract.logits_output_index >= len(descriptor.outputs):
        raise RuntimeUnavailable("TinyLlama logits output index is invalid")
    for output in descriptor.outputs:
        _validate_byte_size(output)
    logits = descriptor.outputs[contract.logits_output_index]
    if (
        logits.dtype not in {"float16", "float32"}
        or logits.shape != (1, 1, contract.vocabulary_size)
    ):
        raise RuntimeUnavailable("TinyLlama logits descriptor is not admitted")
    if not contract.kv_output_indices:
        raise RuntimeUnavailable("TinyLlama OM has no KV output")
    for index in contract.kv_output_indices:
        if index < 0 or index >= len(descriptor.outputs):
            raise RuntimeUnavailable("TinyLlama KV output index is invalid")
        kv = descriptor.outputs[index]
        if kv.dtype != "float16" or not _looks_like_kv_output(kv.shape, contract):
            raise RuntimeUnavailable(f"TinyLlama KV output descriptor is not admitted: {kv.shape}/{kv.dtype}")
        _validate_byte_size(kv)


def _cache_input_descriptor(descriptor: RuntimeDescriptor, contract: Optional[TinyLlamaContract]) -> TensorDescriptor:
    for item in descriptor.inputs:
        if item.name == "past_key_values":
            return item
    # Used only while constructing an error message; do not silently invent a
    # shape for an actual descriptor.
    raise RuntimeUnavailable("TinyLlama OM has no past_key_values input")


def _find_logits_index(outputs: Sequence[TensorDescriptor], vocab: int) -> int:
    candidates = [i for i, item in enumerate(outputs) if item.dtype in {"float16", "float32"} and item.shape and item.shape[-1] == vocab]
    if len(candidates) != 1:
        raise RuntimeUnavailable("cannot identify a unique TinyLlama logits output")
    return candidates[0]


def _validate_byte_size(item: TensorDescriptor) -> None:
    if item.byte_size is None:
        return
    size = {"int64": 8, "float16": 2, "float32": 4}.get(item.dtype)
    if size is None:
        raise RuntimeUnavailable(f"unsupported tensor dtype {item.dtype}")
    expected = size
    for dimension in item.shape:
        expected *= dimension
    if expected != item.byte_size:
        raise RuntimeUnavailable(f"invalid byte size for {item.name}: {item.byte_size}, expected {expected}")


def _attention_mask(real_length: int, length: int, np: Any) -> Any:
    mask = np.zeros((1, length), dtype=np.int64)
    mask[0, :real_length] = 1
    # The static graph reserves the final slot for the token currently being
    # evaluated; this is the layout used by the upstream FixSize cache.
    mask[0, length - 1] = 1
    return mask


def _normalize_outputs(raw: Any, descriptor: RuntimeDescriptor) -> List[Any]:
    if isinstance(raw, Mapping):
        result: List[Any] = []
        for item in descriptor.outputs:
            if item.name not in raw:
                raise RuntimeUnavailable(f"backend output is missing {item.name}")
            result.append(raw[item.name])
        return result
    if isinstance(raw, (list, tuple)):
        if len(raw) != len(descriptor.outputs):
            raise RuntimeUnavailable("backend returned an unexpected output count")
        return list(raw)
    raise RuntimeUnavailable("backend returned invalid outputs")


def _looks_like_kv_output(shape: Tuple[int, ...], contract: Optional[TinyLlamaContract] = None) -> bool:
    layers = contract.num_layers if contract is not None else NUM_LAYERS
    heads = contract.num_kv_heads if contract is not None else NUM_KV_HEADS
    head_dim = contract.head_dim if contract is not None else HEAD_DIM
    max_length = contract.max_sequence_length if contract is not None else MAX_SEQUENCE_LENGTH
    # The community OM emits one new KV position per execution.  A full-cache
    # output is also accepted for compatible rebuilds.
    return len(shape) == 6 and shape[:4] == (layers, 2, 1, heads) and shape[-1] == head_dim and shape[4] in {1, max_length}


def _extract_cache(
    outputs: Sequence[Any],
    contract: TinyLlamaContract,
    old_cache: Any,
    real_length: int,
    np: Any,
) -> Any:
    candidates = [outputs[index] for index in contract.kv_output_indices]
    if len(candidates) == 1:
        value = np.asarray(candidates[0])
        if value.shape == old_cache.shape or value.size == old_cache.size:
            return np.ascontiguousarray(value.reshape(old_cache.shape), dtype=old_cache.dtype)
        if value.ndim == 6 and value.shape[:4] == old_cache.shape[:4] and value.shape[5] == old_cache.shape[5]:
            length = value.shape[4]
            if real_length + length > old_cache.shape[4]:
                raise RuntimeUnavailable("TinyLlama KV output exceeds cache length")
            updated = np.array(old_cache, copy=True)
            updated[:, :, :, :, real_length : real_length + length, :] = value
            return np.ascontiguousarray(updated, dtype=old_cache.dtype)
        raise RuntimeUnavailable("TinyLlama KV output size/shape differs from input cache")
    # Some exporters expose one tensor per K/V pair.  Reassemble only the
    # unambiguous [layer*2] layout; other layouts are rejected explicitly.
    if len(candidates) == old_cache.shape[0] * 2:
        pieces = [np.asarray(item) for item in candidates]
        expected = old_cache.shape[2:]
        if not all(piece.size == int(np.prod(expected)) for piece in pieces):
            raise RuntimeUnavailable("TinyLlama split KV outputs have unsupported shapes")
        value = np.stack([piece.reshape(expected) for piece in pieces], axis=0)
        return np.ascontiguousarray(value.reshape(old_cache.shape), dtype=old_cache.dtype)
    raise RuntimeUnavailable("TinyLlama exposes an unsupported KV output layout")


def _greedy_next_id(logits: Any, vocab: int, np: Any) -> int:
    values = np.asarray(logits)
    if values.size < vocab:
        raise RuntimeUnavailable("TinyLlama logits are smaller than vocabulary")
    if values.shape[-1] != vocab:
        values = values.reshape(-1, values.size if values.ndim == 1 else values.shape[-1])
    row = values.reshape(-1, values.shape[-1])[-1, :vocab]
    try:
        finite = np.isfinite(row)
    except Exception as exc:
        raise RuntimeUnavailable("TinyLlama logits are not numeric") from exc
    if not bool(finite.all()):
        raise RuntimeUnavailable("TinyLlama logits contain non-finite values")
    return int(np.argmax(row))


def _strip_stop_markers(value: str) -> str:
    for marker in ("<|user|>", "<|assistant|>"):
        if marker in value:
            value = value.split(marker, 1)[0]
    return value


def _numpy() -> Any:
    try:
        return importlib.import_module("numpy")
    except ImportError as exc:
        raise RuntimeUnavailable("numpy is required by the TinyLlama ACL runtime") from exc


def _numpy_dtype(dtype: str, np: Any) -> Any:
    try:
        return {"int64": np.int64, "float16": np.float16, "float32": np.float32}[dtype]
    except KeyError as exc:
        raise RuntimeUnavailable(f"unsupported tensor dtype {dtype}") from exc


@contextmanager
def _execution_deadline(seconds: float) -> Iterator[None]:
    """Bound a synchronous ACL call when running on the service main thread."""
    if (
        seconds <= 0
        or not hasattr(signal, "SIGALRM")
        or not hasattr(signal, "setitimer")
        or threading.current_thread() is not threading.main_thread()
    ):
        yield
        return
    previous_handler = signal.getsignal(signal.SIGALRM)

    def alarm_handler(_signum: int, _frame: Any) -> None:
        raise RuntimeExecutionTimeout("TinyLlama ACL execution timed out")

    signal.signal(signal.SIGALRM, alarm_handler)
    signal.setitimer(signal.ITIMER_REAL, float(seconds))
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


class NativeTinyLlamaBackend:
    """Minimal PyACL adapter; ``acl`` is imported only inside ``open``."""

    def __init__(
        self,
        device_id: int = 0,
        *,
        input_names: Optional[Sequence[str]] = None,
        output_names: Optional[Sequence[str]] = None,
    ) -> None:
        self.device_id = int(device_id)
        self.acl: Any = None
        self.context: Any = None
        self.stream: Any = None
        self.model_id: Any = None
        self.desc: Any = None
        self._inputs: Tuple[TensorDescriptor, ...] = ()
        self._outputs: Tuple[TensorDescriptor, ...] = ()
        self._input_names = tuple(input_names or INPUT_ORDER)
        self._output_names = tuple(output_names or ())
        self.poisoned = False
        self.cleanup_failed = False

    def open(self, model_path: Path) -> RuntimeDescriptor:
        try:
            self.acl = importlib.import_module("acl")
        except ImportError as exc:
            raise RuntimeUnavailable("PyACL module 'acl' is unavailable; source CANN set_env.sh") from exc
        try:
            _acl_check(self.acl.init(), "acl.init")
            _acl_check(self.acl.rt.set_device(self.device_id), "acl.rt.set_device")
            context_result = self.acl.rt.create_context(self.device_id)
            self.context, ret = _split_acl(context_result)
            _acl_check(ret, "acl.rt.create_context")
            stream_fn = getattr(self.acl.rt, "create_stream", None)
            if callable(stream_fn):
                self.stream, ret = _split_acl(stream_fn())
                _acl_check(ret, "acl.rt.create_stream")
            result = self.acl.mdl.load_from_file(str(model_path))
            self.model_id, ret = _split_acl(result)
            _acl_check(ret, "acl.mdl.load_from_file")
            self.desc = self.acl.mdl.create_desc()
            if self.desc is None:
                raise RuntimeUnavailable("acl.mdl.create_desc returned no descriptor")
            _acl_check(self.acl.mdl.get_desc(self.desc, self.model_id), "acl.mdl.get_desc")
            self._inputs = tuple(self._read_descriptors(True))
            self._outputs = tuple(self._read_descriptors(False))
            return RuntimeDescriptor(self._inputs, self._outputs)
        except RuntimeUnavailable:
            self.close()
            raise
        except Exception as exc:
            self.close()
            raise RuntimeUnavailable(f"ACL descriptor initialization failed: {type(exc).__name__}") from exc

    def _read_descriptors(self, is_input: bool) -> List[TensorDescriptor]:
        kind = "input" if is_input else "output"
        count_fn = getattr(self.acl.mdl, f"get_num_{kind}s")
        count = int(_first_acl(count_fn(self.desc)))
        name_fn = getattr(self.acl.mdl, f"get_{kind}_name_by_index", None)
        dims_fn = getattr(self.acl.mdl, f"get_{kind}_dims", None)
        dtype_fn = getattr(self.acl.mdl, f"get_{kind}_data_type", None)
        size_fn = getattr(self.acl.mdl, f"get_{kind}_size_by_index")
        if is_input:
            fallback = list(self._input_names)
        else:
            fallback = list(self._output_names) or ["logits", "out_key_values", "attn_scores"]
        if len(fallback) < count:
            fallback.extend(f"output_{i}" for i in range(len(fallback), count))
        result: List[TensorDescriptor] = []
        for index in range(count):
            name = _first_acl(name_fn(self.desc, index)) if callable(name_fn) else fallback[index]
            if isinstance(name, bytes):
                name = name.decode("utf-8", errors="replace")
            if not callable(dims_fn) or not callable(dtype_fn):
                raise RuntimeUnavailable(f"CANN binding cannot read {kind} descriptor")
            shape = _parse_acl_dims(dims_fn(self.desc, index))
            dtype = _acl_dtype(self.acl, int(_first_acl(dtype_fn(self.desc, index))))
            size = int(_first_acl(size_fn(self.desc, index)))
            result.append(TensorDescriptor(str(name), dtype, shape, size))
        return result

    def run(self, inputs: Mapping[str, Any]) -> List[Any]:
        if self.acl is None or self.model_id is None or self.poisoned or self.cleanup_failed:
            raise RuntimeUnavailable("ACL backend is not open")
        np = _numpy()
        input_dataset = None
        output_dataset = None
        # Keep the dataset owner with every allocation.  This lets cleanup
        # release buffers whose dataset was destroyed even when the sibling
        # dataset reports an error, while retaining handles that ACL may still
        # own.
        allocations: List[Tuple[Any, Any, int, Any]] = []
        failed = False
        try:
            if self.context is not None:
                _acl_check(self.acl.rt.set_context(self.context), "acl.rt.set_context")
            input_dataset = self.acl.mdl.create_dataset()
            if input_dataset is None:
                raise RuntimeUnavailable("acl.mdl.create_dataset returned no input dataset")
            output_dataset = self.acl.mdl.create_dataset()
            if output_dataset is None:
                raise RuntimeUnavailable("acl.mdl.create_dataset returned no output dataset")
            for item in self._inputs:
                value = inputs.get(item.name)
                if value is None:
                    raise RuntimeUnavailable(f"missing ACL input {item.name}")
                array = np.ascontiguousarray(value, dtype=_numpy_dtype(item.dtype, np))
                if tuple(array.shape) != item.shape or (item.byte_size is not None and array.nbytes != item.byte_size):
                    raise RuntimeUnavailable(f"invalid ACL input buffer {item.name}")
                pointer, data_buffer = self._add_buffer(input_dataset, int(array.nbytes))
                allocations.append((pointer, data_buffer, int(array.nbytes), input_dataset))
                self._copy_h2d(pointer, array)
            output_allocations: List[Tuple[Any, Any, int, Any]] = []
            outputs: List[Any] = []
            for item in self._outputs:
                pointer, data_buffer = self._add_buffer(output_dataset, int(item.byte_size or _tensor_nbytes(item)))
                allocation = (
                    pointer,
                    data_buffer,
                    int(item.byte_size or _tensor_nbytes(item)),
                    output_dataset,
                )
                allocations.append(allocation)
                output_allocations.append(allocation)
                outputs.append(np.empty(item.shape, dtype=_numpy_dtype(item.dtype, np)))
            execute = getattr(self.acl.mdl, "execute", None)
            if not callable(execute):
                raise RuntimeUnavailable(
                    "CANN binding lacks the required synchronous acl.mdl.execute"
                )
            _acl_check(execute(self.model_id, input_dataset, output_dataset), "acl.mdl.execute")
            for output, (pointer, _, size, _) in zip(outputs, output_allocations):
                self._copy_d2h(output, pointer, size)
            return outputs
        except Exception:
            self.poisoned = True
            failed = True
            raise
        finally:
            try:
                quiesced = self._quiesce_stream()
                cleanup_ok = quiesced and self._destroy_buffers(
                    input_dataset, output_dataset, allocations
                )
            except Exception:
                LOGGER.exception("ACL dataset/buffer cleanup raised")
                cleanup_ok = False
            if not cleanup_ok:
                self.poisoned = True
                self.cleanup_failed = True
            if failed or not cleanup_ok:
                self._close_after_failure()
            if not failed and not cleanup_ok:
                raise RuntimeUnavailable("ACL buffer cleanup failed; runtime requires restart")

    def _add_buffer(self, dataset: Any, size: int) -> Tuple[Any, Any]:
        policy = _acl_constant(self.acl, "ACL_MEM_MALLOC_HUGE_FIRST", 0)
        pointer, ret = _split_acl(self.acl.rt.malloc(size, policy))
        _acl_check(ret, "acl.rt.malloc")
        data = self.acl.create_data_buffer(pointer, size)
        if data is None:
            _safe_acl(self.acl.rt.free, pointer)
            raise RuntimeUnavailable("acl.create_data_buffer returned no buffer")
        try:
            _acl_check(self.acl.mdl.add_dataset_buffer(dataset, data), "acl.mdl.add_dataset_buffer")
        except Exception:
            _safe_acl(self.acl.destroy_data_buffer, data)
            _safe_acl(self.acl.rt.free, pointer)
            raise
        return pointer, data

    def _copy_h2d(self, pointer: Any, array: Any) -> None:
        source = _host_pointer(self.acl, array)
        kind = _acl_constant(self.acl, "ACL_MEMCPY_HOST_TO_DEVICE", 1)
        _acl_check(self.acl.rt.memcpy(pointer, int(array.nbytes), source, int(array.nbytes), kind), "acl.rt.memcpy host_to_device")

    def _copy_d2h(self, array: Any, pointer: Any, size: int) -> None:
        destination = _host_pointer(self.acl, array)
        kind = _acl_constant(self.acl, "ACL_MEMCPY_DEVICE_TO_HOST", 2)
        _acl_check(self.acl.rt.memcpy(destination, int(array.nbytes), pointer, int(size), kind), "acl.rt.memcpy device_to_host")

    def _destroy_buffers(
        self,
        input_dataset: Any,
        output_dataset: Any,
        allocations: Sequence[Tuple[Any, Any, int, Any]],
    ) -> bool:
        if self.acl is None:
            return False
        cleanup_ok = True
        # CANN's Python/ACL examples release the user-owned device allocation,
        # destroy the aclDataBuffer wrapper, and only then destroy the owning
        # dataset.  The dataset does not own the device pointer; reversing this
        # order can leave stale data-buffer handles in older CANN releases.
        for allocation in allocations:
            try:
                if len(allocation) == 3:  # compatibility with older callers
                    pointer, data, _size = allocation  # type: ignore[misc]
                else:
                    pointer, data, _size, _owner = allocation
            except (TypeError, ValueError):
                LOGGER.error("invalid ACL allocation record; retaining it")
                cleanup_ok = False
                continue
            pointer_ok = _safe_acl(getattr(self.acl.rt, "free", None), pointer)
            cleanup_ok = cleanup_ok and pointer_ok
            if not pointer_ok:
                LOGGER.error("retaining ACL device allocation because acl.rt.free failed")
            data_ok = _safe_acl(getattr(self.acl, "destroy_data_buffer", None), data)
            cleanup_ok = cleanup_ok and data_ok
            if not data_ok:
                LOGGER.error("retaining ACL data-buffer handle because destruction failed")
        for dataset in (input_dataset, output_dataset):
            if dataset is None:
                continue
            dataset_ok = _safe_acl(getattr(self.acl.mdl, "destroy_dataset", None), dataset)
            cleanup_ok = cleanup_ok and dataset_ok
        return cleanup_ok

    def _quiesce_stream(self) -> bool:
        """Set the owning context and wait before touching ACL buffers."""

        if self.acl is None:
            return False
        if self.context is not None and not _safe_acl(
            getattr(self.acl.rt, "set_context", None), self.context
        ):
            return False
        if self.stream is not None and not _safe_acl(
            getattr(self.acl.rt, "synchronize_stream", None), self.stream
        ):
            return False
        return True

    def _close_after_failure(self) -> None:
        if self.cleanup_failed:
            LOGGER.error(
                "ACL cleanup could not prove buffer ownership is released; "
                "leaving resources for process restart"
            )
            return
        try:
            self.close()
        except Exception:
            self.cleanup_failed = True
            LOGGER.exception("ACL backend cleanup after failure raised")

    def close(self) -> None:
        acl = self.acl
        if acl is None:
            return
        if self.cleanup_failed:
            # A previous run could not prove that a dataset/buffer was
            # released.  Do not unload the model underneath a live ACL
            # dataset; leave handles for process teardown/restart.
            LOGGER.error("retaining ACL handles after an unrecoverable buffer cleanup failure")
            self.poisoned = True
            return
        # Stop at the first failed ownership transition.  Retaining the
        # current handle makes the failure observable and leaves a later
        # process restart responsible for any resource ACL could not release.
        if self.stream is not None and not _safe_acl(
            getattr(acl.rt, "synchronize_stream", None), self.stream
        ):
            self.cleanup_failed = True
            self.poisoned = True
            return
        cleanup_steps = (
            ("model_id", getattr(acl.mdl, "unload", None)),
            ("desc", getattr(acl.mdl, "destroy_desc", None)),
            ("stream", getattr(acl.rt, "destroy_stream", None)),
            ("context", getattr(acl.rt, "destroy_context", None)),
        )
        for attribute, function in cleanup_steps:
            handle = getattr(self, attribute)
            if handle is None:
                continue
            if not _safe_acl(function, handle):
                self.cleanup_failed = True
                self.poisoned = True
                return
            setattr(self, attribute, None)
        if not _safe_acl(getattr(acl.rt, "reset_device", None), self.device_id):
            self.cleanup_failed = True
            self.poisoned = True
            return
        if not _safe_acl(getattr(acl, "finalize", None)):
            self.cleanup_failed = True
            self.poisoned = True
            return
        self.acl = None


def _tensor_nbytes(item: TensorDescriptor) -> int:
    size = {"int64": 8, "float16": 2, "float32": 4}.get(item.dtype)
    if size is None:
        raise RuntimeUnavailable(f"unsupported tensor dtype {item.dtype}")
    for dimension in item.shape:
        size *= dimension
    return size


def _verify_source_artifact(path: Path, contract: TinyLlamaContract) -> None:
    if contract.source_bytes is None and contract.source_sha256 is None:
        return
    if contract.source_bytes is None or contract.source_sha256 is None:
        raise RuntimeUnavailable("contract source artifact binding is incomplete")
    try:
        actual_size = path.stat().st_size
    except OSError as exc:
        raise RuntimeUnavailable("cannot stat the TinyLlama OM") from exc
    if actual_size != contract.source_bytes:
        raise RuntimeUnavailable(
            f"TinyLlama OM byte count differs from contract: {actual_size} != {contract.source_bytes}"
        )
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise RuntimeUnavailable("cannot hash the TinyLlama OM") from exc
    if digest.hexdigest().lower() != contract.source_sha256.lower():
        raise RuntimeUnavailable("TinyLlama OM SHA-256 differs from contract")


def _verify_om_manifest(
    om_path: Path,
    manifest_path: Path,
    contract: TinyLlamaContract,
) -> None:
    """Require the OM bytes/SHA/revision to match the immutable manifest too."""

    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        artifact = document["artifacts"]["tinyllama_acl_om"]
        expected_bytes = int(artifact["expected_bytes"])
        expected_sha = str(artifact["sha256"]).lower()
        expected_revision = str(artifact["revision"])
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeUnavailable("TinyLlama OM manifest is invalid") from exc
    if len(expected_sha) != 64 or any(char not in "0123456789abcdef" for char in expected_sha):
        raise RuntimeUnavailable("TinyLlama OM manifest SHA-256 is not fixed")
    if contract.source_revision != expected_revision:
        raise RuntimeUnavailable("TinyLlama contract revision differs from the OM manifest")
    if contract.source_bytes != expected_bytes or contract.source_sha256.lower() != expected_sha:
        raise RuntimeUnavailable("TinyLlama contract OM binding differs from the manifest")
    try:
        actual_bytes = om_path.stat().st_size
    except OSError as exc:
        raise RuntimeUnavailable("cannot stat TinyLlama OM") from exc
    if actual_bytes != expected_bytes:
        raise RuntimeUnavailable("TinyLlama OM byte count differs from manifest")
    digest = hashlib.sha256()
    try:
        with om_path.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise RuntimeUnavailable("cannot hash TinyLlama OM") from exc
    if digest.hexdigest().lower() != expected_sha:
        raise RuntimeUnavailable("TinyLlama OM SHA-256 differs from manifest")


def _verify_tokenizer_manifest(
    tokenizer_path: Path,
    manifest_path: Path,
    *,
    expected_revision: Optional[str] = None,
) -> None:
    """Bind every extracted tokenizer file to the board manifest hashes."""

    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
        artifact = document["artifacts"]["tinyllama_tokenizer_zip"]
        expected_files = artifact["extracted_files"]
        if not isinstance(expected_files, dict) or not expected_files:
            raise ValueError("missing extracted_files")
        if set(expected_files) != REQUIRED_TOKENIZER_FILES:
            raise ValueError("extracted tokenizer file set is not the four admitted files")
        om_revision = document["artifacts"]["tinyllama_acl_om"]["revision"]
        tokenizer_revision = artifact["revision"]
        if not om_revision or tokenizer_revision != om_revision:
            raise ValueError("OM and tokenizer revisions differ")
        if expected_revision is not None and expected_revision != om_revision:
            raise ValueError("contract and manifest revisions differ")
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeUnavailable("TinyLlama tokenizer manifest is invalid") from exc
    if tokenizer_path.name != "tokenizer.json":
        raise RuntimeUnavailable("TinyLlama runtime must load tokenizer.json")
    root = tokenizer_path.resolve().parent
    if tokenizer_path.resolve() != root / "tokenizer.json":
        raise RuntimeUnavailable("TinyLlama tokenizer must be a regular manifest-bound file")
    for name, expected in expected_files.items():
        if not isinstance(name, str) or Path(name).name != name or not isinstance(expected, dict):
            raise RuntimeUnavailable(f"invalid extracted tokenizer manifest entry: {name!r}")
        path = root / name
        try:
            actual_bytes = path.stat().st_size
        except OSError as exc:
            raise RuntimeUnavailable(f"cannot stat manifest-bound tokenizer file: {name}") from exc
        try:
            expected_bytes = int(expected["expected_bytes"])
            expected_sha = str(expected["sha256"]).lower()
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeUnavailable(f"invalid hash contract for tokenizer file: {name}") from exc
        if actual_bytes != expected_bytes:
            raise RuntimeUnavailable(
                f"TinyLlama tokenizer file byte count differs from manifest: {name}"
            )
        digest = hashlib.sha256()
        try:
            with path.open("rb") as source:
                for block in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(block)
        except OSError as exc:
            raise RuntimeUnavailable(f"cannot hash TinyLlama tokenizer file: {name}") from exc
        if digest.hexdigest().lower() != expected_sha:
            raise RuntimeUnavailable(f"TinyLlama tokenizer SHA-256 differs from manifest: {name}")


def _host_pointer(acl: Any, array: Any) -> Any:
    util = getattr(acl, "util", None)
    for converter_name in ("numpy_contiguous_to_ptr", "numpy_to_ptr"):
        converter = getattr(util, converter_name, None)
        if callable(converter):
            # CANN 8.x emits a deprecation warning for both NumPy pointer
            # helpers even though they are the supported zero-copy bridge in
            # this runtime.  Keep the warning out of model/API output while
            # retaining the fallback for older bindings.
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message=r"acl\.util\..*will be deprecated.*",
                    category=Warning,
                )
                pointer = converter(array)
            # CANN 8.x bindings return ``(pointer, ndarray)`` from
            # numpy_contiguous_to_ptr, while numpy_to_ptr returns only the
            # integer pointer.  Normalize both forms before passing the value
            # to acl.rt.memcpy.
            if isinstance(pointer, tuple) and len(pointer) == 2:
                pointer = pointer[0]
            if pointer is not None:
                return pointer
    return int(array.ctypes.data)


def _parse_acl_dims(value: Any) -> Tuple[int, ...]:
    value = _first_acl(value)
    if isinstance(value, Mapping):
        dims = value.get("dims")
        count = value.get("dimCount", len(dims) if isinstance(dims, (list, tuple)) else 0)
        if not isinstance(dims, (list, tuple)):
            raise RuntimeUnavailable("ACL dimensions descriptor is malformed")
        value = dims[: int(count)]
    elif hasattr(value, "dims"):
        dims = getattr(value, "dims", None)
        count = getattr(value, "dimCount", len(dims) if isinstance(dims, (list, tuple)) else 0)
        if not isinstance(dims, (list, tuple)):
            raise RuntimeUnavailable("ACL dimensions descriptor is malformed")
        value = dims[: int(count)]
    if not isinstance(value, (list, tuple)) or not value:
        raise RuntimeUnavailable("ACL dimensions descriptor is malformed")
    shape = tuple(int(item) for item in value)
    if any(item <= 0 for item in shape):
        raise RuntimeUnavailable("dynamic or invalid ACL dimensions are not admitted")
    return shape


def _acl_dtype(acl: Any, value: int) -> str:
    constants = {
        _acl_constant(acl, "ACL_INT64", 9): "int64",
        _acl_constant(acl, "ACL_FLOAT16", 1): "float16",
        _acl_constant(acl, "ACL_FLOAT", 0): "float32",
    }
    if value not in constants:
        raise RuntimeUnavailable(f"unsupported ACL data type code {value}")
    return constants[value]


def _acl_constant(module: Any, name: str, fallback: int) -> int:
    value = getattr(module, name, getattr(getattr(module, "rt", None), name, None))
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else fallback


def _split_acl(value: Any) -> Tuple[Any, int]:
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], int):
        return value[0], int(value[1])
    return value, 0


def _first_acl(value: Any) -> Any:
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[1], int):
        _acl_check(value[1], "ACL call")
        return value[0]
    return value


def _acl_check(value: Any, operation: str) -> None:
    if value is None:
        return
    if isinstance(value, tuple) and len(value) == 2:
        value = value[1]
    if isinstance(value, bool):
        if not value:
            raise RuntimeUnavailable(f"{operation} failed")
    elif isinstance(value, int) and value != 0:
        raise RuntimeUnavailable(f"{operation} failed with ACL status {value}")


def _safe_acl(function: Any, *args: Any) -> bool:
    if not callable(function):
        LOGGER.warning("ACL cleanup function is unavailable")
        return False
    try:
        _acl_check(function(*args), "ACL cleanup")
        return True
    except Exception:
        LOGGER.warning("ACL cleanup operation failed", exc_info=True)
        return False


__all__ = [
    "Backend",
    "DEFAULT_MAX_GENERATION_TOKENS",
    "GenerationResult",
    "HARD_MAX_GENERATION_TOKENS",
    "NativeTinyLlamaBackend",
    "RuntimeDescriptor",
    "RuntimeBusy",
    "RuntimeExecutionTimeout",
    "RuntimeRequestError",
    "RuntimeUnavailable",
    "TensorDescriptor",
    "TinyLlamaAclRuntime",
]
