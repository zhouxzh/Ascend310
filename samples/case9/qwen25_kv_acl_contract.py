"""Strict descriptor contract for a Torch-free Qwen2.5 ACL/OM runtime.

The Qwen2.5 exporter used by the follow-up ACL work exposes one token at a
time and carries a fixed, split FP32 KV cache.  This module deliberately does
not import the TinyLlama or legacy Qwen1 runtime: the model dimensions, token
IDs, mask length, and cache layout are independent admission rules.

An inspected contract is the source of truth for output names and order.  The
runtime never guesses those details from a model filename or from a tensor's
byte count.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union


QWEN25_CONTRACT_SCHEMA_VERSION = 1
QWEN25_SUPPORTED_EXECUTION_MODE = "static_kv_token_fp32"
QWEN25_SUPPORTED_MODEL_FAMILY = "qwen2.5"
QWEN25_MODEL_ID = "qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om"

# These values are from Qwen2.5-0.5B-Instruct's published architecture.  They
# are intentionally duplicated here, rather than imported from another model
# adapter, so a contract cannot silently drift across model families.
QWEN25_NUM_LAYERS = 24
QWEN25_NUM_KV_HEADS = 2
QWEN25_HEAD_DIM = 64
QWEN25_VOCABULARY_SIZE = 151936
QWEN25_CACHE_LENGTH = 1024
QWEN25_MASK_LENGTH = 1024
QWEN25_SPLIT_KV_SHAPE = (
    1,
    QWEN25_NUM_KV_HEADS,
    QWEN25_CACHE_LENGTH,
    QWEN25_HEAD_DIM,
)
QWEN25_TOKEN_SPLIT_KV_SHAPE = (1, 1, QWEN25_NUM_KV_HEADS, QWEN25_HEAD_DIM)
QWEN25_KV_DTYPE = "float32"
QWEN25_LOGITS_DTYPE = "float32"
QWEN25_BASE_INPUT_ORDER = ("input_ids", "attention_mask", "position_ids")
QWEN25_CACHE_INPUT_COUNT = QWEN25_NUM_LAYERS * 2
QWEN25_INPUT_ORDER = QWEN25_BASE_INPUT_ORDER + tuple(
    f"past_key_values.{layer}.{part}"
    for layer in range(QWEN25_NUM_LAYERS)
    for part in ("key", "value")
)
QWEN25_OUTPUT_ORDER = ("logits",) + tuple(
    f"present.{layer}.{part}"
    for layer in range(QWEN25_NUM_LAYERS)
    for part in ("key", "value")
)

# Qwen2.5 has no BOS token in tokenizer_config.json.  The model config still
# reports the end-of-text ID as the padding value, and <|im_end|> is EOS.
QWEN25_BOS_TOKEN_ID: Optional[int] = None
QWEN25_PAD_TOKEN_ID = 151643
QWEN25_EOS_TOKEN_ID = 151645

# Local aliases make the public API pleasant without importing another model's
# constants.  They remain defined in this module only.
MODEL_ID = QWEN25_MODEL_ID
CONTRACT_SCHEMA_VERSION = QWEN25_CONTRACT_SCHEMA_VERSION
MAX_SEQUENCE_LENGTH = QWEN25_CACHE_LENGTH


class ContractError(ValueError):
    """Raised when a Qwen2.5 contract is absent, malformed, or inadmissible."""


@dataclass(frozen=True)
class TensorContract:
    """A descriptor entry plus explicit role metadata for cache tensors."""

    name: str
    dtype: str
    shape: Tuple[int, ...]
    byte_size: Optional[int] = None
    role: Optional[str] = None
    cache_index: Optional[int] = None
    cache_part: Optional[str] = None
    cache_update: Optional[str] = None

    @classmethod
    def from_value(cls, value: Any, field: str) -> "TensorContract":
        if not isinstance(value, Mapping):
            raise ContractError(f"{field} must be an object")
        name = value.get("name")
        dtype = value.get("dtype")
        shape = value.get("shape")
        if not isinstance(name, str) or not name.strip():
            raise ContractError(f"{field}.name must be a non-empty string")
        if not isinstance(dtype, str) or not dtype.strip():
            raise ContractError(f"{field}.dtype must be a non-empty string")
        if not isinstance(shape, list) or not shape:
            raise ContractError(f"{field}.shape must be a non-empty list")
        dimensions: List[int] = []
        for index, dimension in enumerate(shape):
            if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0:
                raise ContractError(f"{field}.shape[{index}] must be a positive integer")
            dimensions.append(int(dimension))

        byte_size = value.get("byte_size")
        if byte_size is not None:
            if isinstance(byte_size, bool) or not isinstance(byte_size, int) or byte_size <= 0:
                raise ContractError(f"{field}.byte_size must be a positive integer")
            byte_size = int(byte_size)

        role = value.get("role")
        if role is not None and (not isinstance(role, str) or not role.strip()):
            raise ContractError(f"{field}.role must be a non-empty string when present")
        cache_index = value.get("cache_index")
        if cache_index is not None:
            if isinstance(cache_index, bool) or not isinstance(cache_index, int) or cache_index < 0:
                raise ContractError(f"{field}.cache_index must be a non-negative integer")
            cache_index = int(cache_index)
        cache_part = value.get("cache_part")
        if cache_part is not None and cache_part not in {"key", "value"}:
            raise ContractError(f"{field}.cache_part must be key or value")
        cache_update = value.get("cache_update")
        if cache_update is not None and cache_update not in {"full", "token"}:
            raise ContractError(f"{field}.cache_update must be full or token")
        return cls(
            name=name.strip(),
            dtype=dtype.strip().lower(),
            shape=tuple(dimensions),
            byte_size=byte_size,
            role=role.strip() if isinstance(role, str) else None,
            cache_index=cache_index,
            cache_part=cache_part,
            cache_update=cache_update,
        )

    def as_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "name": self.name,
            "dtype": self.dtype,
            "shape": list(self.shape),
        }
        if self.byte_size is not None:
            result["byte_size"] = self.byte_size
        if self.role is not None:
            result["role"] = self.role
        if self.cache_index is not None:
            result["cache_index"] = self.cache_index
        if self.cache_part is not None:
            result["cache_part"] = self.cache_part
        if self.cache_update is not None:
            result["cache_update"] = self.cache_update
        return result


@dataclass(frozen=True)
class Qwen25Contract:
    """One admitted static Qwen2.5 graph layout.

    ``inputs`` and ``outputs`` preserve descriptor order.  Cache tensors are
    identified by explicit ``role``/``cache_index`` metadata after parsing;
    the runtime uses their names and shapes directly.
    """

    model_id: str = QWEN25_MODEL_ID
    family: str = QWEN25_SUPPORTED_MODEL_FAMILY
    eos_token_id: int = QWEN25_EOS_TOKEN_ID
    pad_token_id: int = QWEN25_PAD_TOKEN_ID
    bos_token_id: Optional[int] = QWEN25_BOS_TOKEN_ID
    static_sequence_length: int = QWEN25_CACHE_LENGTH
    mask_length: int = QWEN25_MASK_LENGTH
    vocabulary_size: int = QWEN25_VOCABULARY_SIZE
    cache_layout: str = "split"
    cache_shape: Tuple[int, ...] = QWEN25_SPLIT_KV_SHAPE
    inputs: Tuple[TensorContract, ...] = ()
    outputs: Tuple[TensorContract, ...] = ()
    input_order_verified: bool = False
    source_revision: Optional[str] = None
    source_bytes: Optional[int] = None
    source_sha256: Optional[str] = None
    opset: Optional[int] = None
    unsupported_operators: Tuple[str, ...] = ()

    @property
    def cache_inputs(self) -> Tuple[TensorContract, ...]:
        return tuple(item for item in self.inputs if item.role == "kv_cache")

    @property
    def cache_outputs(self) -> Tuple[TensorContract, ...]:
        return tuple(item for item in self.outputs if item.role == "kv_cache")

    @property
    def logits_output(self) -> TensorContract:
        matches = tuple(item for item in self.outputs if item.role == "logits")
        if len(matches) != 1:
            raise ContractError("contract must identify exactly one logits output")
        return matches[0]

    @property
    def logits_output_index(self) -> int:
        target = self.logits_output.name
        for index, item in enumerate(self.outputs):
            if item.name == target:
                return index
        raise ContractError("logits output is not present in outputs")

    @property
    def cache_input_names(self) -> Tuple[str, ...]:
        return tuple(item.name for item in self.cache_inputs)

    @property
    def cache_output_names(self) -> Tuple[str, ...]:
        return tuple(item.name for item in self.cache_outputs)

    @property
    def cache_update_modes(self) -> Tuple[str, ...]:
        return tuple(item.cache_update or _cache_update_for_shape(item.shape, self.cache_shape) for item in self.cache_outputs)

    @property
    def execution_mode(self) -> str:
        """Return the admitted execution mode for service/status consumers."""

        return QWEN25_SUPPORTED_EXECUTION_MODE

    def validate_static_expectations(self, *, strict_dimensions: bool = True) -> None:
        if self.model_id != QWEN25_MODEL_ID:
            raise ContractError(f"model_id must be {QWEN25_MODEL_ID!r}")
        if self.family != QWEN25_SUPPORTED_MODEL_FAMILY:
            raise ContractError(f"family must be {QWEN25_SUPPORTED_MODEL_FAMILY!r}")
        if self.static_sequence_length != QWEN25_CACHE_LENGTH:
            raise ContractError(f"static_sequence_length must be {QWEN25_CACHE_LENGTH}")
        if self.mask_length != QWEN25_MASK_LENGTH:
            raise ContractError(f"mask_length must be {QWEN25_MASK_LENGTH}")
        if self.vocabulary_size != QWEN25_VOCABULARY_SIZE:
            raise ContractError(f"vocabulary_size must be {QWEN25_VOCABULARY_SIZE}")
        if self.eos_token_id != QWEN25_EOS_TOKEN_ID:
            raise ContractError(f"eos_token_id must be {QWEN25_EOS_TOKEN_ID}")
        if self.pad_token_id != QWEN25_PAD_TOKEN_ID:
            raise ContractError(f"pad_token_id must be {QWEN25_PAD_TOKEN_ID}")
        if self.bos_token_id is not None and self.bos_token_id < 0:
            raise ContractError("bos_token_id must be non-negative or null")
        if self.cache_layout != "split":
            raise ContractError("the 1024 StaticCache contract admits split layout only")
        expected_shape = QWEN25_SPLIT_KV_SHAPE
        if tuple(self.cache_shape) != expected_shape:
            raise ContractError(f"cache_shape must be {list(expected_shape)} for {self.cache_layout} layout")
        if strict_dimensions and self.inputs and self.outputs:
            self._validate_descriptors()

    def _validate_descriptors(self) -> None:
        if not self.input_order_verified:
            raise ContractError("input_order_verified must be true")
        if len(self.inputs) != len(QWEN25_BASE_INPUT_ORDER) + QWEN25_CACHE_INPUT_COUNT:
            raise ContractError("contract must include exactly 3 base inputs and 48 cache inputs")
        if len(self.outputs) != 1 + QWEN25_CACHE_INPUT_COUNT:
            raise ContractError("contract must include exactly one logits output and 48 cache outputs")
        names = tuple(item.name for item in self.inputs)
        if len(set(names)) != len(names):
            raise ContractError("input names must be unique")
        output_names = tuple(item.name for item in self.outputs)
        if len(set(output_names)) != len(output_names):
            raise ContractError("output names must be unique")
        if names[:3] != QWEN25_BASE_INPUT_ORDER:
            raise ContractError("first inputs must be input_ids, attention_mask, position_ids")
        base = {item.name: item for item in self.inputs[:3]}
        expected_base = {
            "input_ids": (1, 1),
            "attention_mask": (1, QWEN25_MASK_LENGTH),
            "position_ids": (1, 1),
        }
        for name, shape in expected_base.items():
            item = base[name]
            if item.dtype != "int64" or item.shape != shape:
                raise ContractError(f"input {name} must be int64 {list(shape)}")
            if item.byte_size != _tensor_byte_size(item.dtype, item.shape):
                raise ContractError(f"input {name} has an invalid byte_size")

        cache_inputs = self.cache_inputs
        cache_outputs = self.cache_outputs
        if not cache_inputs or not cache_outputs:
            raise ContractError("cache inputs and outputs must be explicitly marked")
        if len(cache_inputs) != len(cache_outputs):
            raise ContractError("cache input/output counts must match")
        if len(cache_inputs) != QWEN25_CACHE_INPUT_COUNT:
            raise ContractError("split layout requires exactly 48 cache input/output tensors")
        input_indices = [item.cache_index for item in cache_inputs]
        output_indices = [item.cache_index for item in cache_outputs]
        if any(index is None for index in input_indices + output_indices):
            raise ContractError("every cache tensor requires cache_index")
        expected_indices = list(range(len(cache_inputs)))
        if input_indices != expected_indices:
            raise ContractError("cache input indices must be ordered layer -> key,value")
        if output_indices != expected_indices:
            raise ContractError("cache output indices must be ordered layer -> key,value")
        expected_input_shape = QWEN25_SPLIT_KV_SHAPE
        for item in cache_inputs:
            if item.dtype != QWEN25_KV_DTYPE or item.shape != expected_input_shape:
                raise ContractError(f"cache input {item.name} must be FP32 {list(expected_input_shape)}")
            if item.byte_size != _tensor_byte_size(item.dtype, item.shape):
                raise ContractError(f"cache input {item.name} has an invalid byte_size")
            if item.cache_part not in {"key", "value"}:
                raise ContractError("split cache input must declare key/value cache_part")
            if item.cache_part != ("key" if int(item.cache_index or 0) % 2 == 0 else "value"):
                raise ContractError("split cache inputs must be ordered layer -> key, value")
        for item in cache_outputs:
            if item.dtype != QWEN25_KV_DTYPE:
                raise ContractError(f"cache output {item.name} must be FP32")
            if item.shape != QWEN25_TOKEN_SPLIT_KV_SHAPE:
                raise ContractError(f"cache output {item.name} must be one-token FP32 {list(QWEN25_TOKEN_SPLIT_KV_SHAPE)}")
            if item.byte_size != _tensor_byte_size(item.dtype, item.shape):
                raise ContractError(f"cache output {item.name} has an invalid byte_size")
            if item.cache_update != "token":
                raise ContractError(f"cache output {item.name} must declare cache_update=token")
            if item.cache_part not in {"key", "value"}:
                raise ContractError("split cache output must declare key/value cache_part")
            if item.cache_part != ("key" if int(item.cache_index or 0) % 2 == 0 else "value"):
                raise ContractError("split cache outputs must be ordered layer -> key, value")

        logits = self.logits_output
        if logits.dtype != QWEN25_LOGITS_DTYPE:
            raise ContractError("logits output must be FP32")
        if len(logits.shape) != 3 or logits.shape[0] != 1 or logits.shape[-1] != self.vocabulary_size:
            raise ContractError("logits output must have shape [1, 1, 151936]")
        if logits.shape[1] != 1:
            raise ContractError("logits output sequence dimension must be one")
        if logits.byte_size != _tensor_byte_size(logits.dtype, logits.shape):
            raise ContractError("logits output has an invalid byte_size")
        if len(tuple(item for item in self.outputs if item.role == "logits")) != 1:
            raise ContractError("exactly one output must declare role=logits")
        if any(item.role not in {"logits", "kv_cache"} for item in self.outputs):
            raise ContractError("outputs may only declare logits or kv_cache roles")

    def validate_descriptor(
        self,
        inputs: Sequence[Any],
        outputs: Sequence[Any],
    ) -> None:
        """Require a loaded OM descriptor to match this exact contract."""
        actual_inputs = tuple(_descriptor_tuple(item) for item in inputs)
        actual_outputs = tuple(_descriptor_tuple(item) for item in outputs)
        expected_inputs = tuple((item.name, item.dtype, item.shape, item.byte_size) for item in self.inputs)
        expected_outputs = tuple((item.name, item.dtype, item.shape, item.byte_size) for item in self.outputs)
        if len(actual_inputs) != len(expected_inputs) or len(actual_outputs) != len(expected_outputs):
            raise ContractError("OM descriptor tensor counts differ from the contract")
        for index, (actual, expected) in enumerate(zip(actual_inputs, expected_inputs)):
            _compare_descriptor(actual, expected, f"input[{index}]")
        for index, (actual, expected) in enumerate(zip(actual_outputs, expected_outputs)):
            _compare_descriptor(actual, expected, f"output[{index}]")

    @classmethod
    def from_descriptor(
        cls,
        inputs: Sequence[Any],
        outputs: Sequence[Any],
        *,
        source_revision: Optional[str] = None,
    ) -> "Qwen25Contract":
        """Build an explicit split StaticCache contract from a descriptor.

        The descriptor must expose the three named base inputs followed by 48
        key/value tensors. Outputs must contain one token logits tensor and
        matching one-token cache tensors; descriptor order and names are
        retained verbatim.
        """
        normalized_inputs = tuple(_as_tensor_contract(item) for item in inputs)
        normalized_outputs = tuple(_as_tensor_contract(item) for item in outputs)
        if tuple(item.name for item in normalized_inputs[:3]) != QWEN25_BASE_INPUT_ORDER:
            raise ContractError("descriptor base input order is not Qwen2.5's admitted order")
        base_expected = {
            "input_ids": (1, 1),
            "attention_mask": (1, QWEN25_MASK_LENGTH),
            "position_ids": (1, 1),
        }
        for item in normalized_inputs[:3]:
            if item.dtype != "int64" or item.shape != base_expected[item.name]:
                raise ContractError(f"descriptor input {item.name} does not match Qwen2.5 static shape")
        cache_candidates = normalized_inputs[3:]
        if len(cache_candidates) == QWEN25_CACHE_INPUT_COUNT and all(
            item.shape == QWEN25_SPLIT_KV_SHAPE and item.dtype == QWEN25_KV_DTYPE
            for item in cache_candidates
        ):
            layout = "split"
            cache_shape = QWEN25_SPLIT_KV_SHAPE
            cache_inputs = tuple(
                _with_cache_metadata(item, index, "key" if index % 2 == 0 else "value")
                for index, item in enumerate(cache_candidates)
            )
        else:
            raise ContractError("descriptor cache inputs must be 48 explicit split FP32 tensors")

        logits_candidates = [
            item
            for item in normalized_outputs
            if item.dtype == QWEN25_LOGITS_DTYPE
            and len(item.shape) == 3
            and item.shape[0] == 1
            and item.shape[-1] == QWEN25_VOCABULARY_SIZE
            and item.shape == (1, 1, QWEN25_VOCABULARY_SIZE)
        ]
        if len(logits_candidates) != 1:
            raise ContractError("descriptor must expose one unambiguous FP32 logits output")
        logits_name = logits_candidates[0].name
        output_cache_candidates = [item for item in normalized_outputs if item.name != logits_name]
        expected_token = QWEN25_TOKEN_SPLIT_KV_SHAPE
        if len(output_cache_candidates) != len(cache_inputs):
            raise ContractError("descriptor cache output count does not match cache input count")
        if not all(
            item.dtype == QWEN25_KV_DTYPE and item.shape == expected_token
            for item in output_cache_candidates
        ):
            raise ContractError("descriptor cache outputs must be one-token FP32 tensors")
        marked_inputs = normalized_inputs[:3] + cache_inputs
        marked_outputs: List[TensorContract] = []
        for item in normalized_outputs:
            if item.name == logits_name:
                marked_outputs.append(
                    TensorContract(item.name, item.dtype, item.shape, item.byte_size, role="logits")
                )
            else:
                index = len([out for out in marked_outputs if out.role == "kv_cache"])
                part = "key" if index % 2 == 0 else "value"
                marked_outputs.append(
                    _with_cache_metadata(
                        item,
                        index,
                        part,
                        cache_update="token",
                    )
                )
        contract = cls(
            model_id=QWEN25_MODEL_ID,
            family=QWEN25_SUPPORTED_MODEL_FAMILY,
            eos_token_id=QWEN25_EOS_TOKEN_ID,
            pad_token_id=QWEN25_PAD_TOKEN_ID,
            bos_token_id=QWEN25_BOS_TOKEN_ID,
            static_sequence_length=QWEN25_CACHE_LENGTH,
            mask_length=QWEN25_MASK_LENGTH,
            vocabulary_size=QWEN25_VOCABULARY_SIZE,
            cache_layout=layout,
            cache_shape=cache_shape,
            inputs=tuple(marked_inputs),
            outputs=tuple(marked_outputs),
            input_order_verified=True,
            source_revision=source_revision,
        )
        contract.validate_static_expectations()
        return contract

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Qwen25Contract":
        if not isinstance(raw, Mapping):
            raise ContractError("contract root must be an object")
        if raw.get("schema_version") != QWEN25_CONTRACT_SCHEMA_VERSION:
            raise ContractError(f"schema_version must be {QWEN25_CONTRACT_SCHEMA_VERSION}")
        model = raw.get("model")
        if not isinstance(model, Mapping):
            raise ContractError("model must be an object")
        model_id = model.get("model_id")
        family = model.get("family")
        if model_id != QWEN25_MODEL_ID or family != QWEN25_SUPPORTED_MODEL_FAMILY:
            raise ContractError("contract model must be the fixed Qwen2.5 model")
        eos = model.get("eos_token_id", QWEN25_EOS_TOKEN_ID)
        pad = model.get("pad_token_id", QWEN25_PAD_TOKEN_ID)
        bos = model.get("bos_token_id", QWEN25_BOS_TOKEN_ID)
        for name, value in (("eos_token_id", eos), ("pad_token_id", pad), ("bos_token_id", bos)):
            if name in {"eos_token_id", "pad_token_id"}:
                if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                    raise ContractError(f"model.{name} must be a non-negative integer")
            elif value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ContractError(f"model.{name} must be a non-negative integer or null")
        acl_om = raw.get("acl_om")
        if not isinstance(acl_om, Mapping):
            raise ContractError("acl_om must be an object")
        if acl_om.get("supported_static_qwen25_layout") is not True:
            raise ContractError("acl_om.supported_static_qwen25_layout must be true")
        if acl_om.get("execution_mode") != QWEN25_SUPPORTED_EXECUTION_MODE:
            raise ContractError("acl_om.execution_mode is not admitted for Qwen2.5")
        static_length = acl_om.get("static_sequence_length")
        mask_length = acl_om.get("mask_length")
        if static_length != QWEN25_CACHE_LENGTH or mask_length != QWEN25_MASK_LENGTH:
            raise ContractError("Qwen2.5 static/cache mask lengths do not match the fixed exporter contract")
        layout = acl_om.get("cache_layout")
        if layout != "split":
            raise ContractError("acl_om.cache_layout must be split for the 1024 StaticCache graph")
        input_order = acl_om.get("input_order")
        if not isinstance(input_order, list) or input_order[:3] != list(QWEN25_BASE_INPUT_ORDER):
            raise ContractError("acl_om.input_order must begin with input_ids, attention_mask, position_ids")
        raw_inputs = _descriptor_values(acl_om.get("inputs"), "acl_om.inputs", input_order)
        raw_outputs = _descriptor_values(acl_om.get("outputs"), "acl_om.outputs", None)
        inputs = tuple(TensorContract.from_value(item, f"acl_om.inputs[{index}]") for index, item in enumerate(raw_inputs))
        outputs = tuple(TensorContract.from_value(item, f"acl_om.outputs[{index}]") for index, item in enumerate(raw_outputs))
        if [item.name for item in inputs] != input_order:
            raise ContractError("acl_om.input_order must exactly match descriptor input order")
        cache_shape_raw = acl_om.get("cache_shape")
        expected_shape = QWEN25_SPLIT_KV_SHAPE
        if cache_shape_raw is not None and tuple(cache_shape_raw) != expected_shape:
            raise ContractError("acl_om.cache_shape does not match the selected cache layout")
        contract = cls(
            model_id=model_id,
            family=family,
            eos_token_id=int(eos),
            pad_token_id=int(pad),
            bos_token_id=int(bos) if bos is not None else None,
            static_sequence_length=int(static_length),
            mask_length=int(mask_length),
            vocabulary_size=int(acl_om.get("vocabulary_size", QWEN25_VOCABULARY_SIZE)),
            cache_layout=layout,
            cache_shape=expected_shape,
            inputs=inputs,
            outputs=outputs,
            input_order_verified=acl_om.get("input_order_verified") is True,
            source_revision=raw.get("source_revision"),
            source_bytes=_source_bytes(raw),
            source_sha256=_source_sha(raw),
            opset=_opset(acl_om),
            unsupported_operators=_unsupported(acl_om),
        )
        contract.validate_static_expectations()
        return contract

    @classmethod
    def load(cls, path: Union[str, Path]) -> "Qwen25Contract":
        contract_path = Path(path).expanduser()
        if not contract_path.is_file():
            raise ContractError(f"contract file does not exist: {contract_path}")
        try:
            raw = json.loads(contract_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ContractError(f"cannot read contract file: {contract_path}") from exc
        except json.JSONDecodeError as exc:
            raise ContractError(f"contract is not valid JSON: {contract_path}") from exc
        return cls.from_dict(raw)

    def as_dict(self) -> Dict[str, Any]:
        self.validate_static_expectations()
        result: Dict[str, Any] = {
            "schema_version": QWEN25_CONTRACT_SCHEMA_VERSION,
            "model": {
                "family": self.family,
                "model_id": self.model_id,
                "eos_token_id": self.eos_token_id,
                "pad_token_id": self.pad_token_id,
                "bos_token_id": self.bos_token_id,
            },
            "acl_om": {
                "execution_mode": QWEN25_SUPPORTED_EXECUTION_MODE,
                "supported_static_qwen25_layout": True,
                "static_sequence_length": self.static_sequence_length,
                "mask_length": self.mask_length,
                "cache_layout": self.cache_layout,
                "cache_shape": list(self.cache_shape),
                "input_order": [item.name for item in self.inputs],
                "input_order_verified": self.input_order_verified,
                "inputs": [item.as_dict() for item in self.inputs],
                "outputs": [item.as_dict() for item in self.outputs],
                "vocabulary_size": self.vocabulary_size,
                "operator_audit": {
                    "opset": self.opset,
                    "unsupported_operators": list(self.unsupported_operators),
                },
            },
        }
        if self.source_revision:
            result["source_revision"] = self.source_revision
        if self.source_bytes is not None or self.source_sha256 is not None:
            result["source_artifact"] = {
                "bytes": self.source_bytes,
                "sha256": self.source_sha256,
            }
        return result


def _descriptor_values(value: Any, field: str, order: Optional[Sequence[str]]) -> List[Mapping[str, Any]]:
    if isinstance(value, list):
        if not all(isinstance(item, Mapping) for item in value):
            raise ContractError(f"{field} must contain descriptor objects")
        return list(value)
    if isinstance(value, Mapping):
        keys = list(order) if order is not None else list(value)
        if set(value) != set(keys):
            raise ContractError(f"{field} mapping keys do not match descriptor order")
        result: List[Mapping[str, Any]] = []
        for key in keys:
            item = value.get(key)
            if not isinstance(item, Mapping):
                raise ContractError(f"{field}.{key} must be an object")
            result.append(item)
        return result
    raise ContractError(f"{field} must be a descriptor list or mapping")


def _as_tensor_contract(item: Any) -> TensorContract:
    if isinstance(item, TensorContract):
        return item
    name = getattr(item, "name", None)
    dtype = getattr(item, "dtype", None)
    shape = getattr(item, "shape", None)
    byte_size = getattr(item, "byte_size", None)
    if not isinstance(name, str) or not name.strip() or not isinstance(dtype, str) or not dtype.strip() or not isinstance(shape, tuple):
        raise ContractError("descriptor item must expose name, dtype, shape, and byte_size")
    return TensorContract(name, dtype.lower(), tuple(int(value) for value in shape), byte_size)


def _with_cache_metadata(
    item: TensorContract,
    index: int,
    part: str,
    *,
    cache_update: Optional[str] = None,
) -> TensorContract:
    return TensorContract(
        item.name,
        item.dtype,
        item.shape,
        item.byte_size,
        role="kv_cache",
        cache_index=index,
        cache_part=part,
        cache_update=cache_update,
    )


def _cache_update_for_shape(shape: Tuple[int, ...], full_shape: Tuple[int, ...]) -> str:
    if tuple(shape) == tuple(full_shape):
        return "full"
    if tuple(shape) == QWEN25_TOKEN_SPLIT_KV_SHAPE:
        return "token"
    raise ContractError("cache tensor shape is neither a full cache nor one-token cache")


def _descriptor_tuple(item: Any) -> Tuple[str, str, Tuple[int, ...], Optional[int]]:
    name = getattr(item, "name", None)
    dtype = getattr(item, "dtype", None)
    shape = getattr(item, "shape", None)
    byte_size = getattr(item, "byte_size", None)
    if not isinstance(name, str) or not name.strip() or not isinstance(dtype, str) or not dtype.strip() or not isinstance(shape, tuple):
        raise ContractError("descriptor item is malformed")
    return name, dtype.lower(), tuple(int(value) for value in shape), byte_size


def _tensor_byte_size(dtype: str, shape: Sequence[int]) -> int:
    widths = {"int64": 8, "float32": 4, "float16": 2, "int32": 4}
    try:
        width = widths[str(dtype).lower()]
    except KeyError as exc:
        raise ContractError(f"unsupported descriptor dtype {dtype}") from exc
    size = int(width)
    for dimension in shape:
        size *= int(dimension)
    return size


def _compare_descriptor(
    actual: Tuple[str, str, Tuple[int, ...], Optional[int]],
    expected: Tuple[str, str, Tuple[int, ...], Optional[int]],
    field: str,
) -> None:
    if actual[:3] != expected[:3]:
        raise ContractError(f"{field} descriptor differs from contract")
    if expected[3] is not None and actual[3] != expected[3]:
        raise ContractError(f"{field} byte_size differs from contract")


def _source_bytes(raw: Mapping[str, Any]) -> Optional[int]:
    value = raw.get("source_artifact", {})
    if not isinstance(value, Mapping):
        raise ContractError("source_artifact must be an object")
    result = value.get("bytes")
    if result is not None and (isinstance(result, bool) or not isinstance(result, int) or result <= 0):
        raise ContractError("source_artifact.bytes must be a positive integer")
    return int(result) if result is not None else None


def _source_sha(raw: Mapping[str, Any]) -> Optional[str]:
    value = raw.get("source_artifact", {})
    if not isinstance(value, Mapping):
        raise ContractError("source_artifact must be an object")
    result = value.get("sha256")
    if result is None:
        return None
    if not isinstance(result, str) or len(result) != 64 or any(char not in "0123456789abcdefABCDEF" for char in result):
        raise ContractError("source_artifact.sha256 must be a 64-character hexadecimal string")
    return result.lower()


def _opset(acl_om: Mapping[str, Any]) -> Optional[int]:
    audit = acl_om.get("operator_audit", {})
    if not isinstance(audit, Mapping):
        raise ContractError("operator_audit must be an object")
    value = audit.get("opset")
    if value is not None and (isinstance(value, bool) or not isinstance(value, int)):
        raise ContractError("operator_audit.opset must be an integer")
    return int(value) if value is not None else None


def _unsupported(acl_om: Mapping[str, Any]) -> Tuple[str, ...]:
    audit = acl_om.get("operator_audit", {})
    if not isinstance(audit, Mapping):
        raise ContractError("operator_audit must be an object")
    value = audit.get("unsupported_operators", [])
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ContractError("operator_audit.unsupported_operators must be a string list")
    if value:
        raise ContractError("contract contains unsupported operators")
    return tuple(value)


__all__ = [
    "ContractError",
    "TensorContract",
    "Qwen25Contract",
    "QWEN25_CONTRACT_SCHEMA_VERSION",
    "QWEN25_SUPPORTED_EXECUTION_MODE",
    "QWEN25_SUPPORTED_MODEL_FAMILY",
    "QWEN25_MODEL_ID",
    "QWEN25_NUM_LAYERS",
    "QWEN25_NUM_KV_HEADS",
    "QWEN25_HEAD_DIM",
    "QWEN25_VOCABULARY_SIZE",
    "QWEN25_CACHE_LENGTH",
    "QWEN25_MASK_LENGTH",
    "QWEN25_CACHE_INPUT_COUNT",
    "QWEN25_SPLIT_KV_SHAPE",
    "QWEN25_TOKEN_SPLIT_KV_SHAPE",
    "QWEN25_KV_DTYPE",
    "QWEN25_LOGITS_DTYPE",
    "QWEN25_BASE_INPUT_ORDER",
    "QWEN25_INPUT_ORDER",
    "QWEN25_OUTPUT_ORDER",
    "QWEN25_BOS_TOKEN_ID",
    "QWEN25_PAD_TOKEN_ID",
    "QWEN25_EOS_TOKEN_ID",
    "MODEL_ID",
    "CONTRACT_SCHEMA_VERSION",
    "MAX_SEQUENCE_LENGTH",
]
