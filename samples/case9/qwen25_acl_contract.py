"""Fail-closed contract for the Qwen2.5 full-context static ACL/OM graph.

The currently available exporter produces a three-input graph rather than a
KV-cache graph.  This contract therefore admits only:

``input_ids      int64   [1, S]``
``attention_mask int64   [1, S]``
``position_ids   int64   [1, S]``
``logits         float16|float32 [1, S, V]`` (full-context mode), or
``last_logits    float16|float32 [1, 1, V]`` (last-logits mode)

``S`` and ``V`` are read from the inspected descriptor and recorded in the
contract.  No past/present tensors, dynamic dimensions, or size-only reshapes
are accepted.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union


QWEN25_CONTRACT_SCHEMA_VERSION = 1
QWEN25_SUPPORTED_EXECUTION_MODE = "full_context_static"
QWEN25_SUPPORTED_LAST_LOGITS_EXECUTION_MODE = "last_logits_static"
QWEN25_SUPPORTED_EXECUTION_MODES = (
    QWEN25_SUPPORTED_EXECUTION_MODE,
    QWEN25_SUPPORTED_LAST_LOGITS_EXECUTION_MODE,
)
QWEN25_SUPPORTED_MODEL_FAMILY = "qwen2.5"
QWEN25_MODEL_ID = "qwen2.5-0.5b-instruct-static-fp16-acl-om"
QWEN25_DEFAULT_SEQUENCE_LENGTH = 128
QWEN25_MODEL_VOCABULARY_SIZE = 151936
QWEN25_INPUT_ORDER = ("input_ids", "attention_mask", "position_ids")
QWEN25_INPUT_DTYPE = "int64"
QWEN25_ALLOWED_LOGITS_DTYPES = ("float16", "float32")
QWEN25_LOGITS_DTYPE = "float16"

# Local aliases are deliberately defined here, not imported from the Tiny or
# legacy Qwen1 adapters.
MODEL_ID = QWEN25_MODEL_ID
CONTRACT_SCHEMA_VERSION = QWEN25_CONTRACT_SCHEMA_VERSION
STATIC_SEQUENCE_LENGTH = QWEN25_DEFAULT_SEQUENCE_LENGTH


class ContractError(ValueError):
    """Raised when a static Qwen2.5 contract is malformed or inadmissible."""


@dataclass(frozen=True)
class TensorContract:
    name: str
    dtype: str
    shape: Tuple[int, ...]
    byte_size: Optional[int] = None
    role: Optional[str] = None

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
            raise ContractError(f"{field}.role must be a non-empty string")
        return cls(
            name=name.strip(),
            dtype=dtype.strip().lower(),
            shape=tuple(dimensions),
            byte_size=byte_size,
            role=role.strip() if isinstance(role, str) else None,
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
        return result


@dataclass(frozen=True)
class Qwen25Contract:
    """One inspected full-context graph and its tokenizer IDs."""

    model_id: str = QWEN25_MODEL_ID
    family: str = QWEN25_SUPPORTED_MODEL_FAMILY
    static_sequence_length: int = QWEN25_DEFAULT_SEQUENCE_LENGTH
    vocabulary_size: int = QWEN25_MODEL_VOCABULARY_SIZE
    input_dtype: str = QWEN25_INPUT_DTYPE
    logits_dtype: str = QWEN25_LOGITS_DTYPE
    inputs: Tuple[TensorContract, ...] = ()
    outputs: Tuple[TensorContract, ...] = ()
    eos_token_id: Optional[int] = None
    pad_token_id: Optional[int] = None
    bos_token_id: Optional[int] = None
    input_order_verified: bool = False
    source_revision: Optional[str] = None
    source_bytes: Optional[int] = None
    source_sha256: Optional[str] = None
    opset: Optional[int] = None
    unsupported_operators: Tuple[str, ...] = ()
    # ``last_logits_static`` is a compatible full-context graph whose public
    # output is gathered to [1, 1, V].  Keep the default unchanged for all
    # existing contracts and callers.
    execution_mode: str = QWEN25_SUPPORTED_EXECUTION_MODE

    @property
    def logits_output(self) -> TensorContract:
        matches = tuple(item for item in self.outputs if item.role in {"logits", "logits_last"})
        if len(matches) != 1:
            raise ContractError("contract must identify exactly one logits output")
        return matches[0]

    @property
    def logits_output_index(self) -> int:
        name = self.logits_output.name
        for index, item in enumerate(self.outputs):
            if item.name == name:
                return index
        raise ContractError("logits output is missing from the output descriptor")

    def validate_static_expectations(self, *, require_descriptors: bool = False) -> None:
        if self.model_id != QWEN25_MODEL_ID:
            raise ContractError(f"model_id must be {QWEN25_MODEL_ID!r}")
        if self.family != QWEN25_SUPPORTED_MODEL_FAMILY:
            raise ContractError(f"family must be {QWEN25_SUPPORTED_MODEL_FAMILY!r}")
        if isinstance(self.static_sequence_length, bool) or not isinstance(self.static_sequence_length, int) or self.static_sequence_length <= 0:
            raise ContractError("static_sequence_length must be a positive integer")
        if isinstance(self.vocabulary_size, bool) or not isinstance(self.vocabulary_size, int) or self.vocabulary_size <= 0:
            raise ContractError("vocabulary_size must be a positive integer")
        if self.input_dtype != QWEN25_INPUT_DTYPE:
            raise ContractError("Qwen2.5 static input dtype must be int64")
        if self.execution_mode not in QWEN25_SUPPORTED_EXECUTION_MODES:
            raise ContractError(
                "execution_mode must be full_context_static or last_logits_static"
            )
        if self.logits_dtype not in QWEN25_ALLOWED_LOGITS_DTYPES:
            raise ContractError("Qwen2.5 static logits dtype must be float16 or float32")
        for name, value in (("eos_token_id", self.eos_token_id), ("pad_token_id", self.pad_token_id), ("bos_token_id", self.bos_token_id)):
            if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
                raise ContractError(f"{name} must be a non-negative integer or null")
        if self.unsupported_operators:
            raise ContractError("contract contains unsupported operators")
        if require_descriptors or self.inputs or self.outputs:
            self._validate_descriptors()

    def _validate_descriptors(self) -> None:
        if not self.input_order_verified:
            raise ContractError("input_order_verified must be true")
        if len(self.inputs) != 3:
            raise ContractError("Qwen2.5 static graph must expose exactly three inputs")
        names = tuple(item.name for item in self.inputs)
        if names != QWEN25_INPUT_ORDER:
            raise ContractError("inputs must be ordered input_ids, attention_mask, position_ids")
        if len(set(names)) != len(names):
            raise ContractError("input names must be unique")
        expected_shape = (1, self.static_sequence_length)
        for item in self.inputs:
            if item.dtype != self.input_dtype or item.shape != expected_shape or item.role not in {None, "input"}:
                raise ContractError(f"input {item.name} must be {self.input_dtype} {list(expected_shape)}")
        if len(self.outputs) != 1:
            raise ContractError("Qwen2.5 static graph must expose exactly one logits output")
        logits = self.logits_output
        expected_logits_shape = (
            (1, 1, self.vocabulary_size)
            if self.execution_mode == QWEN25_SUPPORTED_LAST_LOGITS_EXECUTION_MODE
            else (1, self.static_sequence_length, self.vocabulary_size)
        )
        if logits.dtype != self.logits_dtype or logits.shape != expected_logits_shape:
            raise ContractError(f"logits must be {self.logits_dtype} {list(expected_logits_shape)}")
        if any(item.role not in {"logits", "logits_last", None} for item in self.outputs):
            raise ContractError("the only admitted output role is logits")

    def validate_descriptor(self, inputs: Sequence[Any], outputs: Sequence[Any]) -> None:
        actual_inputs = tuple(_descriptor_tuple(item) for item in inputs)
        actual_outputs = tuple(_descriptor_tuple(item) for item in outputs)
        expected_inputs = tuple((item.name, item.dtype, item.shape, item.byte_size) for item in self.inputs)
        expected_outputs = tuple((item.name, item.dtype, item.shape, item.byte_size) for item in self.outputs)
        if len(actual_inputs) != len(expected_inputs) or len(actual_outputs) != len(expected_outputs):
            raise ContractError("OM descriptor tensor counts differ from the contract")
        for index, (actual, expected) in enumerate(zip(actual_inputs, expected_inputs)):
            _compare_descriptor(actual, expected, f"input[{index}]")
        for index, (actual, expected) in enumerate(zip(actual_outputs, expected_outputs)):
            # ATC/CANN may rename a graph output while preserving its order,
            # dtype, shape, and byte size (the 310B4 build produced
            # ``/Cast:0:logits`` from the ONNX name ``logits``).  There is only
            # one admitted output, so bind it by descriptor position and keep
            # the contract's role/shape checks as the hard boundary.
            if index == 0 and actual[1] == expected[1] and actual[2] == expected[2] and (
                expected[3] is None or actual[3] == expected[3]
            ):
                continue
            _compare_descriptor(actual, expected, f"output[{index}]")

    @classmethod
    def from_descriptor(
        cls,
        inputs: Sequence[Any],
        outputs: Sequence[Any],
        *,
        source_revision: Optional[str] = None,
        execution_mode: Optional[str] = None,
    ) -> "Qwen25Contract":
        normalized_inputs = tuple(_as_tensor_contract(item) for item in inputs)
        normalized_outputs = tuple(_as_tensor_contract(item) for item in outputs)
        if len(normalized_inputs) != 3:
            raise ContractError("Qwen2.5 static graph requires exactly three inputs")
        if tuple(item.name for item in normalized_inputs) != QWEN25_INPUT_ORDER:
            raise ContractError("descriptor input order is not the admitted Qwen2.5 order")
        sequence_lengths = {item.shape[1] for item in normalized_inputs if len(item.shape) == 2 and item.shape[0] == 1}
        if len(sequence_lengths) != 1:
            raise ContractError("all Qwen2.5 static inputs must share one [1,S] shape")
        sequence_length = next(iter(sequence_lengths))
        if any(item.dtype != QWEN25_INPUT_DTYPE or item.shape != (1, sequence_length) for item in normalized_inputs):
            raise ContractError("Qwen2.5 static inputs must be contiguous int64 [1,S] tensors")
        logits_candidates = [
            item for item in normalized_outputs
            if item.dtype in QWEN25_ALLOWED_LOGITS_DTYPES
            and len(item.shape) == 3
            and item.shape[0] == 1
            and item.shape[1] in {1, sequence_length}
            and item.shape[2] > 0
        ]
        if len(normalized_outputs) != 1 or len(logits_candidates) != 1:
            raise ContractError("descriptor must expose exactly one FP16/FP32 logits output")
        logits = logits_candidates[0]
        inferred_mode = (
            QWEN25_SUPPORTED_LAST_LOGITS_EXECUTION_MODE
            if logits.shape[1] == 1
            else QWEN25_SUPPORTED_EXECUTION_MODE
        )
        selected_mode = execution_mode or inferred_mode
        if selected_mode not in QWEN25_SUPPORTED_EXECUTION_MODES:
            raise ContractError("descriptor execution_mode is not admitted")
        expected_output_shape = (
            (1, 1, logits.shape[2])
            if selected_mode == QWEN25_SUPPORTED_LAST_LOGITS_EXECUTION_MODE
            else (1, sequence_length, logits.shape[2])
        )
        if logits.shape != expected_output_shape:
            raise ContractError(
                f"descriptor logits shape {list(logits.shape)} does not match {selected_mode}"
            )
        marked_inputs = tuple(
            TensorContract(item.name, item.dtype, item.shape, item.byte_size, role="input")
            for item in normalized_inputs
        )
        marked_outputs = (TensorContract(logits.name, logits.dtype, logits.shape, logits.byte_size, role="logits"),)
        contract = cls(
            model_id=QWEN25_MODEL_ID,
            family=QWEN25_SUPPORTED_MODEL_FAMILY,
            static_sequence_length=sequence_length,
            vocabulary_size=logits.shape[2],
            input_dtype=QWEN25_INPUT_DTYPE,
            logits_dtype=logits.dtype,
            inputs=marked_inputs,
            outputs=marked_outputs,
            input_order_verified=True,
            source_revision=source_revision,
            execution_mode=selected_mode,
        )
        contract.validate_static_expectations(require_descriptors=True)
        return contract

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "Qwen25Contract":
        if not isinstance(raw, Mapping) or raw.get("schema_version") != QWEN25_CONTRACT_SCHEMA_VERSION:
            raise ContractError(f"schema_version must be {QWEN25_CONTRACT_SCHEMA_VERSION}")
        model = raw.get("model")
        acl_om = raw.get("acl_om")
        if not isinstance(model, Mapping) or not isinstance(acl_om, Mapping):
            raise ContractError("model and acl_om must be objects")
        if model.get("model_id") != QWEN25_MODEL_ID or model.get("family") != QWEN25_SUPPORTED_MODEL_FAMILY:
            raise ContractError("contract model must be the fixed Qwen2.5 static model")
        execution_mode = acl_om.get("execution_mode")
        if execution_mode not in {
            QWEN25_SUPPORTED_EXECUTION_MODE,
            QWEN25_SUPPORTED_LAST_LOGITS_EXECUTION_MODE,
            "full_context_static_fp16",
            "full_context_static_fp32",
        }:
            raise ContractError("acl_om.execution_mode is not an admitted static mode")
        if execution_mode in {"full_context_static_fp16", "full_context_static_fp32"}:
            execution_mode = QWEN25_SUPPORTED_EXECUTION_MODE
        input_order = acl_om.get("input_order")
        if input_order != list(QWEN25_INPUT_ORDER):
            raise ContractError("acl_om.input_order must be input_ids, attention_mask, position_ids")
        inputs = tuple(
            TensorContract.from_value(item, f"acl_om.inputs[{index}]")
            for index, item in enumerate(_descriptor_values(acl_om.get("inputs"), "acl_om.inputs"))
        )
        outputs = tuple(
            TensorContract.from_value(item, f"acl_om.outputs[{index}]")
            for index, item in enumerate(_descriptor_values(acl_om.get("outputs"), "acl_om.outputs"))
        )
        source = raw.get("source_artifact", {})
        if source is None:
            source = {}
        if not isinstance(source, Mapping):
            raise ContractError("source_artifact must be an object")
        source_bytes = source.get("bytes")
        if source_bytes is not None and (isinstance(source_bytes, bool) or not isinstance(source_bytes, int) or source_bytes <= 0):
            raise ContractError("source_artifact.bytes must be positive")
        source_sha = source.get("sha256")
        if source_sha is not None and (
            not isinstance(source_sha, str)
            or len(source_sha) != 64
            or any(character not in "0123456789abcdefABCDEF" for character in source_sha)
        ):
            raise ContractError("source_artifact.sha256 must be hexadecimal SHA-256")
        audit = acl_om.get("operator_audit", {})
        if not isinstance(audit, Mapping):
            raise ContractError("operator_audit must be an object")
        unsupported = audit.get("unsupported_operators", [])
        if not isinstance(unsupported, list) or not all(isinstance(item, str) and item for item in unsupported):
            raise ContractError("operator_audit.unsupported_operators must be a string list")
        contract = cls(
            model_id=QWEN25_MODEL_ID,
            family=QWEN25_SUPPORTED_MODEL_FAMILY,
            static_sequence_length=int(acl_om.get("static_sequence_length", 0)),
            vocabulary_size=int(acl_om.get("vocabulary_size", 0)),
            input_dtype=str(acl_om.get("input_dtype", QWEN25_INPUT_DTYPE)).lower(),
            logits_dtype=str(acl_om.get("logits_dtype", QWEN25_LOGITS_DTYPE)).lower(),
            inputs=inputs,
            outputs=outputs,
            eos_token_id=_optional_int(model.get("eos_token_id"), "eos_token_id"),
            pad_token_id=_optional_int(model.get("pad_token_id"), "pad_token_id"),
            bos_token_id=_optional_int(model.get("bos_token_id"), "bos_token_id"),
            input_order_verified=acl_om.get("input_order_verified") is True,
            source_revision=raw.get("source_revision"),
            source_bytes=int(source_bytes) if source_bytes is not None else None,
            source_sha256=source_sha.lower() if isinstance(source_sha, str) else None,
            opset=_optional_int(audit.get("opset"), "opset"),
            unsupported_operators=tuple(unsupported),
            execution_mode=str(execution_mode),
        )
        contract.validate_static_expectations(require_descriptors=True)
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
        self.validate_static_expectations(require_descriptors=True)
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
                "execution_mode": self.execution_mode,
                "static_sequence_length": self.static_sequence_length,
                "input_dtype": self.input_dtype,
                "logits_dtype": self.logits_dtype,
                "precision": self.logits_dtype,
                "input_order": list(QWEN25_INPUT_ORDER),
                "input_order_verified": self.input_order_verified,
                "inputs": [item.as_dict() for item in self.inputs],
                "outputs": [item.as_dict() for item in self.outputs],
                "vocabulary_size": self.vocabulary_size,
                "output_selection": (
                    "attention_mask_sum_minus_one"
                    if self.execution_mode == QWEN25_SUPPORTED_LAST_LOGITS_EXECUTION_MODE
                    else "full_sequence"
                ),
                "operator_audit": {
                    "opset": self.opset,
                    "unsupported_operators": list(self.unsupported_operators),
                },
            },
        }
        if self.source_revision:
            result["source_revision"] = self.source_revision
        if self.source_bytes is not None or self.source_sha256 is not None:
            result["source_artifact"] = {"bytes": self.source_bytes, "sha256": self.source_sha256}
        return result


def _descriptor_values(value: Any, field: str) -> List[Mapping[str, Any]]:
    if isinstance(value, list) and all(isinstance(item, Mapping) for item in value):
        return list(value)
    if isinstance(value, Mapping):
        result: List[Mapping[str, Any]] = []
        for name, item in value.items():
            if not isinstance(item, Mapping):
                raise ContractError(f"{field}.{name} must be an object")
            if "name" not in item:
                item = dict(item)
                item["name"] = name
            result.append(item)
        return result
    raise ContractError(f"{field} must be a descriptor list or mapping")


def _as_tensor_contract(item: Any) -> TensorContract:
    if isinstance(item, TensorContract):
        return item
    name, dtype, shape, byte_size = (
        getattr(item, "name", None),
        getattr(item, "dtype", None),
        getattr(item, "shape", None),
        getattr(item, "byte_size", None),
    )
    if not isinstance(name, str) or not isinstance(dtype, str) or not isinstance(shape, tuple):
        raise ContractError("descriptor item must expose name, dtype, shape")
    return TensorContract(name, dtype.lower(), tuple(int(value) for value in shape), byte_size)


def _descriptor_tuple(item: Any) -> Tuple[str, str, Tuple[int, ...], Optional[int]]:
    descriptor = _as_tensor_contract(item)
    return descriptor.name, descriptor.dtype, descriptor.shape, descriptor.byte_size


def _compare_descriptor(
    actual: Tuple[str, str, Tuple[int, ...], Optional[int]],
    expected: Tuple[str, str, Tuple[int, ...], Optional[int]],
    field: str,
) -> None:
    if actual[:3] != expected[:3]:
        raise ContractError(f"{field} descriptor differs from contract")
    if expected[3] is not None and actual[3] != expected[3]:
        raise ContractError(f"{field} byte_size differs from contract")


def _optional_int(value: Any, field: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ContractError(f"{field} must be a non-negative integer or null")
    return int(value)


__all__ = [
    "ContractError",
    "TensorContract",
    "Qwen25Contract",
    "QWEN25_CONTRACT_SCHEMA_VERSION",
    "QWEN25_SUPPORTED_EXECUTION_MODE",
    "QWEN25_SUPPORTED_LAST_LOGITS_EXECUTION_MODE",
    "QWEN25_SUPPORTED_EXECUTION_MODES",
    "QWEN25_SUPPORTED_MODEL_FAMILY",
    "QWEN25_MODEL_ID",
    "QWEN25_DEFAULT_SEQUENCE_LENGTH",
    "QWEN25_MODEL_VOCABULARY_SIZE",
    "QWEN25_INPUT_ORDER",
    "QWEN25_INPUT_DTYPE",
    "QWEN25_LOGITS_DTYPE",
    "QWEN25_ALLOWED_LOGITS_DTYPES",
    "MODEL_ID",
    "CONTRACT_SCHEMA_VERSION",
    "STATIC_SEQUENCE_LENGTH",
]
