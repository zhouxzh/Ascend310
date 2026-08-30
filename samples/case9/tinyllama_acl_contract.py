"""Contract validation for the TinyLlama static-KV ACL/OM graph.

The contract is intentionally separate from the existing Qwen contract.  An
OM file is not admitted because its filename looks familiar: the board-side
inspector must record the descriptor and the runtime validates it again before
the first inference.  This module contains no ``acl`` import and is safe to
use on the Windows controller.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union


CONTRACT_SCHEMA_VERSION = 1
MODEL_ID = "tiny-llama-1.1b-acl-om"
MODEL_FAMILY = "tinyllama"
EXECUTION_MODE = "kv_cache_token"
MAX_SEQUENCE_LENGTH = 1024
MASK_LENGTH = MAX_SEQUENCE_LENGTH + 1
VOCABULARY_SIZE = 32000
NUM_LAYERS = 22
NUM_KV_HEADS = 4
HEAD_DIM = 64
DEFAULT_BOS_TOKEN_ID = 1
DEFAULT_EOS_TOKEN_ID = 2
DEFAULT_PAD_TOKEN_ID = 0
INPUT_ORDER = ("input_ids", "attention_mask", "position_ids", "past_key_values")


class ContractError(ValueError):
    """Raised when a TinyLlama contract is malformed or not admitted."""


@dataclass(frozen=True)
class TensorContract:
    name: str
    dtype: str
    shape: Tuple[int, ...]
    byte_size: Optional[int] = None

    @classmethod
    def from_value(cls, value: Any, field: str) -> "TensorContract":
        if not isinstance(value, Mapping):
            raise ContractError(f"{field} must be an object")
        name = value.get("name")
        dtype = value.get("dtype")
        raw_shape = value.get("shape")
        if not isinstance(name, str) or not name.strip():
            raise ContractError(f"{field}.name must be a non-empty string")
        if not isinstance(dtype, str) or not dtype.strip():
            raise ContractError(f"{field}.dtype must be a non-empty string")
        if not isinstance(raw_shape, (list, tuple)) or not raw_shape:
            raise ContractError(f"{field}.shape must be a non-empty list")
        shape: List[int] = []
        for index, dimension in enumerate(raw_shape):
            if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0:
                raise ContractError(f"{field}.shape[{index}] must be a positive integer")
            shape.append(int(dimension))
        raw_bytes = value.get("byte_size", value.get("bytes"))
        if raw_bytes is not None:
            if isinstance(raw_bytes, bool) or not isinstance(raw_bytes, int) or raw_bytes <= 0:
                raise ContractError(f"{field}.byte_size must be a positive integer")
            raw_bytes = int(raw_bytes)
        return cls(name.strip(), dtype.strip().lower(), tuple(shape), raw_bytes)

    def as_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "name": self.name,
            "dtype": self.dtype,
            "shape": list(self.shape),
        }
        if self.byte_size is not None:
            result["byte_size"] = self.byte_size
        return result


@dataclass(frozen=True)
class TinyLlamaContract:
    """The descriptor-backed execution contract used by the runtime."""

    model_id: str = MODEL_ID
    family: str = MODEL_FAMILY
    bos_token_id: int = DEFAULT_BOS_TOKEN_ID
    eos_token_id: int = DEFAULT_EOS_TOKEN_ID
    pad_token_id: int = DEFAULT_PAD_TOKEN_ID
    vocabulary_size: int = VOCABULARY_SIZE
    num_layers: int = NUM_LAYERS
    num_kv_heads: int = NUM_KV_HEADS
    head_dim: int = HEAD_DIM
    max_sequence_length: int = MAX_SEQUENCE_LENGTH
    inputs: Tuple[TensorContract, ...] = ()
    outputs: Tuple[TensorContract, ...] = ()
    logits_output_index: int = 0
    kv_output_indices: Tuple[int, ...] = (1,)
    input_order_verified: bool = False
    source_revision: Optional[str] = None
    source_bytes: Optional[int] = None
    source_sha256: Optional[str] = None

    @property
    def mask_length(self) -> int:
        return self.max_sequence_length + 1

    @property
    def input_map(self) -> Dict[str, TensorContract]:
        return {item.name: item for item in self.inputs}

    @property
    def output_map(self) -> Dict[str, TensorContract]:
        return {item.name: item for item in self.outputs}

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "TinyLlamaContract":
        if not isinstance(raw, Mapping):
            raise ContractError("contract root must be an object")
        if raw.get("schema_version") != CONTRACT_SCHEMA_VERSION:
            raise ContractError(f"schema_version must be {CONTRACT_SCHEMA_VERSION}")
        model = raw.get("model")
        if not isinstance(model, Mapping):
            raise ContractError("model must be an object")
        model_id = model.get("model_id", MODEL_ID)
        family = model.get("family", MODEL_FAMILY)
        if model_id != MODEL_ID:
            raise ContractError(f"model.model_id must be {MODEL_ID!r}")
        if family != MODEL_FAMILY:
            raise ContractError(f"model.family must be {MODEL_FAMILY!r}")

        def integer(name: str, default: int, *, positive: bool = False) -> int:
            value = model.get(name, default)
            # Accept the short aliases used by earlier board reports.
            if name == "vocabulary_size":
                value = model.get("vocabulary_size", model.get("vocab_size", default))
            if name == "num_layers":
                value = model.get("num_layers", model.get("layers", default))
            if name == "num_kv_heads":
                value = model.get("num_kv_heads", model.get("kv_heads", default))
            if name == "max_sequence_length":
                value = model.get(
                    "max_sequence_length",
                    model.get("context_length", model.get("max_cache_size", default)),
                )
            if isinstance(value, bool) or not isinstance(value, int):
                raise ContractError(f"model.{name} must be an integer")
            if (positive and value <= 0) or (not positive and value < 0):
                raise ContractError(f"model.{name} has an invalid value")
            return int(value)

        bos = integer("bos_token_id", DEFAULT_BOS_TOKEN_ID)
        eos = integer("eos_token_id", DEFAULT_EOS_TOKEN_ID)
        pad = integer("pad_token_id", DEFAULT_PAD_TOKEN_ID)
        vocab = integer("vocabulary_size", VOCABULARY_SIZE, positive=True)
        layers = integer("num_layers", NUM_LAYERS, positive=True)
        kv_heads = integer("num_kv_heads", NUM_KV_HEADS, positive=True)
        head_dim = integer("head_dim", HEAD_DIM, positive=True)
        max_length = integer("max_sequence_length", MAX_SEQUENCE_LENGTH, positive=True)
        if vocab <= 0 or layers <= 0 or kv_heads <= 0 or head_dim <= 0 or max_length <= 0:
            raise ContractError("model dimensions must be positive")
        for token_name, token_id in (
            ("bos_token_id", bos),
            ("eos_token_id", eos),
            ("pad_token_id", pad),
        ):
            if token_id >= vocab:
                raise ContractError(f"model.{token_name} must be below vocabulary_size")

        acl_om = raw.get("acl_om", {})
        if not isinstance(acl_om, Mapping):
            raise ContractError("acl_om must be an object")
        mode = acl_om.get("execution_mode", EXECUTION_MODE)
        if mode != EXECUTION_MODE:
            raise ContractError(f"acl_om.execution_mode must be {EXECUTION_MODE!r}")
        verified = acl_om.get("input_order_verified", False)
        if not isinstance(verified, bool):
            raise ContractError("acl_om.input_order_verified must be a boolean")
        raw_order = acl_om.get("input_order", list(INPUT_ORDER))
        if not isinstance(raw_order, list) or tuple(raw_order) != INPUT_ORDER:
            raise ContractError("acl_om.input_order must match the TinyLlama order")

        raw_inputs = acl_om.get("inputs", [])
        inputs = _tensor_sequence(raw_inputs, "acl_om.inputs")
        raw_outputs = acl_om.get("outputs", acl_om.get("output", []))
        # A single ``output`` object is accepted for forward compatibility,
        # although a valid TinyLlama KV graph must expose logits and cache.
        if isinstance(raw_outputs, Mapping) and "name" in raw_outputs:
            raw_outputs = [raw_outputs]
        outputs = _tensor_sequence(raw_outputs, "acl_om.outputs")
        if inputs and not verified:
            raise ContractError("acl_om.input_order_verified must be true when inputs are recorded")
        logits_index = acl_om.get("logits_output_index", 0)
        if isinstance(logits_index, bool) or not isinstance(logits_index, int):
            raise ContractError("acl_om.logits_output_index must be an integer")
        kv_indices = acl_om.get("kv_output_indices", [1])
        if not isinstance(kv_indices, list) or not all(
            isinstance(item, int) and not isinstance(item, bool) and item >= 0
            for item in kv_indices
        ):
            raise ContractError("acl_om.kv_output_indices must be a list of integers")
        if outputs and (logits_index < 0 or logits_index >= len(outputs)):
            raise ContractError("acl_om.logits_output_index is outside outputs")
        if outputs and any(item >= len(outputs) for item in kv_indices):
            raise ContractError("acl_om.kv_output_indices contains an invalid index")

        artifact = raw.get("source_artifact", {})
        if artifact is None:
            artifact = {}
        if not isinstance(artifact, Mapping):
            raise ContractError("source_artifact must be an object")
        source_bytes = artifact.get("bytes")
        if source_bytes is not None:
            if isinstance(source_bytes, bool) or not isinstance(source_bytes, int) or source_bytes <= 0:
                raise ContractError("source_artifact.bytes must be positive")
        source_sha = artifact.get("sha256")
        if source_sha is not None:
            if not isinstance(source_sha, str) or len(source_sha) != 64 or any(
                char not in "0123456789abcdefABCDEF" for char in source_sha
            ):
                raise ContractError("source_artifact.sha256 must be hexadecimal SHA-256")
            source_sha = source_sha.lower()
        revision = raw.get("source_revision")
        if revision is not None and (not isinstance(revision, str) or not revision.strip()):
            raise ContractError("source_revision must be a non-empty string")
        if source_bytes is not None and source_sha is None:
            raise ContractError("source_artifact.sha256 is required with bytes")
        if source_sha is not None and source_bytes is None:
            raise ContractError("source_artifact.bytes is required with sha256")
        return cls(
            model_id=model_id,
            family=family,
            bos_token_id=bos,
            eos_token_id=eos,
            pad_token_id=pad,
            vocabulary_size=vocab,
            num_layers=layers,
            num_kv_heads=kv_heads,
            head_dim=head_dim,
            max_sequence_length=max_length,
            inputs=tuple(inputs),
            outputs=tuple(outputs),
            logits_output_index=logits_index,
            kv_output_indices=tuple(kv_indices),
            input_order_verified=verified,
            source_revision=revision.strip() if isinstance(revision, str) else None,
            source_bytes=source_bytes,
            source_sha256=source_sha,
        )

    @classmethod
    def load(cls, path: Union[Path, str]) -> "TinyLlamaContract":
        contract_path = Path(path).expanduser()
        if not contract_path.is_file():
            raise ContractError(f"contract file does not exist: {contract_path}")
        try:
            value = json.loads(contract_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ContractError(f"cannot read contract file: {contract_path}") from exc
        except json.JSONDecodeError as exc:
            raise ContractError(f"contract is not valid JSON: {contract_path}") from exc
        return cls.from_dict(value)

    @classmethod
    def from_descriptor(
        cls,
        inputs: Sequence[Any],
        outputs: Sequence[Any],
        *,
        source_revision: Optional[str] = None,
        source_bytes: Optional[int] = None,
        source_sha256: Optional[str] = None,
        bos_token_id: int = DEFAULT_BOS_TOKEN_ID,
        eos_token_id: int = DEFAULT_EOS_TOKEN_ID,
        pad_token_id: int = DEFAULT_PAD_TOKEN_ID,
        vocabulary_size: int = VOCABULARY_SIZE,
    ) -> "TinyLlamaContract":
        """Build a report contract from descriptor-like objects.

        Objects may be :class:`TensorContract`, runtime descriptors, or simple
        mappings.  The method is used by the board inspector and never imports
        ACL itself.
        """
        converted_inputs = tuple(_coerce_tensor(item, f"input[{i}]") for i, item in enumerate(inputs))
        converted_outputs = tuple(_coerce_tensor(item, f"output[{i}]") for i, item in enumerate(outputs))
        return cls(
            bos_token_id=bos_token_id,
            eos_token_id=eos_token_id,
            pad_token_id=pad_token_id,
            vocabulary_size=vocabulary_size,
            inputs=converted_inputs,
            outputs=converted_outputs,
            input_order_verified=tuple(item.name for item in converted_inputs) == INPUT_ORDER,
            source_revision=source_revision,
            source_bytes=source_bytes,
            source_sha256=source_sha256.lower() if source_sha256 else None,
        )

    def validate_descriptor(self, inputs: Sequence[Any], outputs: Sequence[Any]) -> None:
        """Validate a live descriptor against this contract."""
        actual_inputs = tuple(_coerce_tensor(item, f"input[{i}]") for i, item in enumerate(inputs))
        actual_outputs = tuple(_coerce_tensor(item, f"output[{i}]") for i, item in enumerate(outputs))
        if len(actual_inputs) != len(self.inputs):
            raise ContractError(f"OM exposes {len(actual_inputs)} inputs; contract expects {len(self.inputs)}")
        if self.inputs and tuple(item.name for item in actual_inputs) != tuple(item.name for item in self.inputs):
            raise ContractError("OM input order/names differ from the contract")
        for expected, actual in zip(self.inputs, actual_inputs):
            _compare_tensor(expected, actual)
        if self.outputs and len(actual_outputs) != len(self.outputs):
            raise ContractError(f"OM exposes {len(actual_outputs)} outputs; contract expects {len(self.outputs)}")
        if self.outputs:
            for expected, actual in zip(self.outputs, actual_outputs):
                _compare_tensor(expected, actual)

    def as_dict(self) -> Dict[str, Any]:
        model = {
            "model_id": self.model_id,
            "family": self.family,
            "bos_token_id": self.bos_token_id,
            "eos_token_id": self.eos_token_id,
            "pad_token_id": self.pad_token_id,
            "vocabulary_size": self.vocabulary_size,
            "num_layers": self.num_layers,
            "num_kv_heads": self.num_kv_heads,
            "head_dim": self.head_dim,
            "max_sequence_length": self.max_sequence_length,
        }
        acl_om = {
            "execution_mode": EXECUTION_MODE,
            "input_order": list(INPUT_ORDER),
            "input_order_verified": self.input_order_verified,
            "inputs": [item.as_dict() for item in self.inputs],
            "outputs": [item.as_dict() for item in self.outputs],
            "logits_output_index": self.logits_output_index,
            "kv_output_indices": list(self.kv_output_indices),
        }
        result: Dict[str, Any] = {"schema_version": CONTRACT_SCHEMA_VERSION, "model": model, "acl_om": acl_om}
        if self.source_revision:
            result["source_revision"] = self.source_revision
        if self.source_bytes is not None and self.source_sha256 is not None:
            result["source_artifact"] = {"bytes": self.source_bytes, "sha256": self.source_sha256}
        return result

    def validate_static_expectations(self, *, strict_dimensions: bool = True) -> None:
        """Reject a descriptor contract that cannot represent the fixed graph.

        The board admission/runtime path always uses the immutable TinyLlama
        architecture constants.  ``strict_dimensions=False`` is retained only
        for small controller-side descriptor fixtures and is not exposed by
        any service or provisioning command.
        """
        if self.model_id != MODEL_ID or self.family != MODEL_FAMILY:
            raise ContractError("contract is not for TinyLlama")
        if self.max_sequence_length != MAX_SEQUENCE_LENGTH:
            raise ContractError(f"max_sequence_length must be {MAX_SEQUENCE_LENGTH}")
        if self.vocabulary_size <= 0 or self.num_layers <= 0 or self.num_kv_heads <= 0 or self.head_dim <= 0:
            raise ContractError("invalid TinyLlama model dimensions")
        if strict_dimensions:
            expected = {
                "vocabulary_size": VOCABULARY_SIZE,
                "num_layers": NUM_LAYERS,
                "num_kv_heads": NUM_KV_HEADS,
                "head_dim": HEAD_DIM,
            }
            for field, value in expected.items():
                if getattr(self, field) != value:
                    raise ContractError(f"{field} must be {value} for the admitted TinyLlama OM")
        if len(self.inputs) == 4 and tuple(item.name for item in self.inputs) != INPUT_ORDER:
            raise ContractError("TinyLlama input order is not verified")


def _tensor_sequence(value: Any, field: str) -> List[TensorContract]:
    if isinstance(value, Mapping):
        # Accept descriptor reports keyed by tensor name.
        keyed: List[Any] = []
        for name, item in value.items():
            if isinstance(item, Mapping) and "name" not in item:
                item = dict(item)
                item["name"] = name
            keyed.append(item)
        value = keyed
    if not isinstance(value, (list, tuple)):
        raise ContractError(f"{field} must be a list")
    return [_coerce_tensor(item, f"{field}[{index}]") for index, item in enumerate(value)]


def _coerce_tensor(value: Any, field: str) -> TensorContract:
    if isinstance(value, TensorContract):
        return value
    if isinstance(value, Mapping):
        return TensorContract.from_value(value, field)
    name = getattr(value, "name", None)
    dtype = getattr(value, "dtype", None)
    shape = getattr(value, "shape", None)
    byte_size = getattr(value, "byte_size", None)
    return TensorContract.from_value(
        {"name": name, "dtype": dtype, "shape": list(shape or ()), "byte_size": byte_size},
        field,
    )


def _compare_tensor(expected: TensorContract, actual: TensorContract) -> None:
    if expected.name != actual.name or expected.dtype != actual.dtype or expected.shape != actual.shape:
        raise ContractError(
            f"tensor {actual.name!r} does not match contract "
            f"({expected.name!r}, {expected.dtype}, {list(expected.shape)})"
        )
    if expected.byte_size is not None and actual.byte_size is not None and expected.byte_size != actual.byte_size:
        raise ContractError(f"tensor {actual.name!r} byte size differs from contract")


__all__ = [
    "CONTRACT_SCHEMA_VERSION",
    "MODEL_ID",
    "MODEL_FAMILY",
    "EXECUTION_MODE",
    "MAX_SEQUENCE_LENGTH",
    "MASK_LENGTH",
    "INPUT_ORDER",
    "ContractError",
    "TensorContract",
    "TinyLlamaContract",
]
