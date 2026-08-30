"""Strict, board-local contract for the no-Torch Qwen ACL/OM runtime.

The ONNX inspection step writes this JSON file on the board.  The service does
not infer an execution contract from a model filename or from an arbitrary OM
descriptor: it only accepts a contract that explicitly describes the one
runtime layout implemented by :mod:`acl_om_runtime`.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple, Union


CONTRACT_SCHEMA_VERSION = 1
SUPPORTED_EXECUTION_MODE = "full_context_logits"
SUPPORTED_MODEL_FAMILY = "qwen1.5"
EXPECTED_MODEL_ID = "qwen1.5-0.5b-chat-acl-om"
MAX_SEQUENCE_LENGTH = 2048
EXPECTED_INPUT_ORDER = ("input_ids", "attention_mask", "position_ids")


class ContractError(ValueError):
    """Raised when an inspected contract is absent, malformed, or unsupported."""


@dataclass(frozen=True)
class TensorContract:
    name: str
    dtype: str
    shape: Tuple[int, ...]

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
            dimensions.append(dimension)
        return cls(name=name.strip(), dtype=dtype.strip().lower(), shape=tuple(dimensions))

    def as_dict(self) -> Dict[str, Any]:
        return {"name": self.name, "dtype": self.dtype, "shape": list(self.shape)}


@dataclass(frozen=True)
class ModelContract:
    """The only graph shape accepted by the first ACL/OM adapter."""

    model_id: str
    family: str
    eos_token_id: int
    pad_token_id: int
    static_sequence_length: int
    inputs: Dict[str, TensorContract]
    logits: TensorContract
    vocabulary_size: int
    source_revision: Optional[str] = None
    source_bytes: Optional[int] = None
    source_sha256: Optional[str] = None
    input_order_verified: bool = False
    opset: Optional[int] = None
    unsupported_operators: Tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ModelContract":
        if not isinstance(raw, Mapping):
            raise ContractError("contract root must be an object")
        if raw.get("schema_version") != CONTRACT_SCHEMA_VERSION:
            raise ContractError(
                f"schema_version must be {CONTRACT_SCHEMA_VERSION}"
            )

        model = raw.get("model")
        if not isinstance(model, Mapping):
            raise ContractError("model must be an object")
        model_id = model.get("model_id")
        family = model.get("family")
        eos_token_id = model.get("eos_token_id")
        # Qwen 1.5 uses the end-of-text token as its padding token.  The
        # inspector may omit this redundant field, but token 0 is not a safe
        # implicit padding choice.
        pad_token_id = model.get("pad_token_id", 151643)
        if not isinstance(model_id, str) or not model_id.strip():
            raise ContractError("model.model_id must be a non-empty string")
        if model_id.strip() != EXPECTED_MODEL_ID:
            raise ContractError(
                f"model.model_id must be {EXPECTED_MODEL_ID!r}"
            )
        if family != SUPPORTED_MODEL_FAMILY:
            raise ContractError(
                f"unsupported model family: {family!r}; expected {SUPPORTED_MODEL_FAMILY!r}"
            )
        if not _is_nonnegative_int(eos_token_id):
            raise ContractError("model.eos_token_id must be a non-negative integer")
        if not _is_nonnegative_int(pad_token_id):
            raise ContractError("model.pad_token_id must be a non-negative integer")

        acl_om = raw.get("acl_om")
        if not isinstance(acl_om, Mapping):
            raise ContractError("acl_om must be an object")
        if acl_om.get("supported_autoregressive_qwen_layout") is not True:
            reason = acl_om.get("support_reason")
            suffix = f": {reason}" if isinstance(reason, str) and reason else ""
            raise ContractError(
                "contract does not admit the supported ACL/OM layout" + suffix
            )
        if acl_om.get("execution_mode") != SUPPORTED_EXECUTION_MODE:
            raise ContractError(
                "acl_om.execution_mode must be 'full_context_logits'"
            )
        if acl_om.get("external_initializers", False) is not False:
            raise ContractError("external ONNX initializers are not admitted")
        if acl_om.get("has_past_key_values", False) is not False:
            raise ContractError("past-key-value graph inputs are not admitted")

        sequence_length = acl_om.get("static_sequence_length")
        if sequence_length != MAX_SEQUENCE_LENGTH:
            raise ContractError(
                f"acl_om.static_sequence_length must be {MAX_SEQUENCE_LENGTH}"
            )

        raw_inputs = acl_om.get("inputs")
        if not isinstance(raw_inputs, Mapping):
            raise ContractError("acl_om.inputs must be an object")
        expected_input_keys = {"input_ids", "attention_mask", "position_ids"}
        if set(raw_inputs) != expected_input_keys:
            raise ContractError(
                "acl_om.inputs must contain exactly input_ids, attention_mask, position_ids"
            )
        inputs = {
            key: TensorContract.from_value(raw_inputs[key], f"acl_om.inputs.{key}")
            for key in sorted(expected_input_keys)
        }
        for key, tensor in inputs.items():
            if tensor.dtype != "int64":
                raise ContractError(f"acl_om.inputs.{key}.dtype must be int64")
            if tensor.shape != (1, MAX_SEQUENCE_LENGTH):
                raise ContractError(
                    f"acl_om.inputs.{key}.shape must be [1, {MAX_SEQUENCE_LENGTH}]"
                )

        input_order = acl_om.get("input_order")
        if not isinstance(input_order, list) or input_order != list(EXPECTED_INPUT_ORDER):
            raise ContractError(
                "acl_om.input_order must be input_ids, attention_mask, position_ids"
            )
        input_order_verified = acl_om.get("input_order_verified")
        if input_order_verified is not True:
            raise ContractError(
                "acl_om.input_order_verified must be true for the fixed ATC order"
            )

        raw_output = acl_om.get("output")
        if not isinstance(raw_output, Mapping):
            raise ContractError("acl_om.output must be an object")
        if set(raw_output) != {"logits"}:
            raise ContractError("acl_om.output must contain exactly logits")
        logits = TensorContract.from_value(raw_output.get("logits"), "acl_om.output.logits")
        if logits.dtype != "float16":
            raise ContractError("acl_om.output.logits.dtype must be float16")
        if len(logits.shape) != 3 or logits.shape[0] != 1 or logits.shape[1] != MAX_SEQUENCE_LENGTH:
            raise ContractError(
                "acl_om.output.logits.shape must be [1, 2048, vocabulary_size]"
            )
        vocabulary_size = acl_om.get("vocabulary_size", logits.shape[2])
        if (
            isinstance(vocabulary_size, bool)
            or not isinstance(vocabulary_size, int)
            or vocabulary_size != logits.shape[2]
        ):
            raise ContractError(
                "acl_om.vocabulary_size must equal the logits vocabulary dimension"
            )
        if vocabulary_size <= 0:
            raise ContractError("acl_om.vocabulary_size must be positive")

        operator_audit = acl_om.get("operator_audit", {})
        if not isinstance(operator_audit, Mapping):
            raise ContractError("acl_om.operator_audit must be an object")
        raw_opset = operator_audit.get("opset")
        opset = None
        if raw_opset is not None:
            if isinstance(raw_opset, bool) or not isinstance(raw_opset, int):
                raise ContractError("acl_om.operator_audit.opset must be an integer")
            opset = int(raw_opset)
        raw_unsupported = operator_audit.get("unsupported_operators", [])
        if not isinstance(raw_unsupported, list) or not all(
            isinstance(item, str) and item for item in raw_unsupported
        ):
            raise ContractError(
                "acl_om.operator_audit.unsupported_operators must be a string list"
            )
        unsupported_operators = tuple(raw_unsupported)
        if unsupported_operators:
            raise ContractError(
                "acl_om.operator_audit contains unsupported operators"
            )
        if opset is not None and not 13 <= opset <= 18:
            raise ContractError("acl_om.operator_audit.opset is outside the admitted range")

        source_artifact = raw.get("source_artifact", {})
        if source_artifact is None:
            source_artifact = {}
        if not isinstance(source_artifact, Mapping):
            raise ContractError("source_artifact must be an object")
        source_bytes = source_artifact.get("bytes")
        if source_bytes is not None:
            if isinstance(source_bytes, bool) or not isinstance(source_bytes, int) or source_bytes <= 0:
                raise ContractError("source_artifact.bytes must be a positive integer")
            source_bytes = int(source_bytes)
        source_sha256 = source_artifact.get("sha256")
        if source_sha256 is not None:
            if not isinstance(source_sha256, str) or len(source_sha256) != 64:
                raise ContractError("source_artifact.sha256 must be a 64-character string")
            source_sha256 = source_sha256.lower()
            if any(character not in "0123456789abcdef" for character in source_sha256):
                raise ContractError("source_artifact.sha256 must be hexadecimal")

        revision = raw.get("source_revision")
        if revision is not None and (not isinstance(revision, str) or not revision.strip()):
            raise ContractError("source_revision must be a non-empty string when present")
        if source_artifact:
            if source_bytes is None or source_sha256 is None:
                raise ContractError(
                    "source_artifact must include both bytes and sha256"
                )
            if revision is None:
                raise ContractError(
                    "source_revision is required when source_artifact is present"
                )
        return cls(
            model_id=model_id.strip(),
            family=family,
            eos_token_id=eos_token_id,
            pad_token_id=pad_token_id,
            static_sequence_length=sequence_length,
            inputs=inputs,
            logits=logits,
            vocabulary_size=vocabulary_size,
            source_revision=revision.strip() if isinstance(revision, str) else None,
            source_bytes=source_bytes,
            source_sha256=source_sha256,
            input_order_verified=input_order_verified,
            opset=opset,
            unsupported_operators=unsupported_operators,
        )

    @classmethod
    def load(cls, path: Union[str, Path]) -> "ModelContract":
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
        return {
            "schema_version": CONTRACT_SCHEMA_VERSION,
            "model": {
                "family": self.family,
                "model_id": self.model_id,
                "eos_token_id": self.eos_token_id,
                "pad_token_id": self.pad_token_id,
            },
            "acl_om": {
                "supported_autoregressive_qwen_layout": True,
                "execution_mode": SUPPORTED_EXECUTION_MODE,
                "external_initializers": False,
                "has_past_key_values": False,
                "static_sequence_length": self.static_sequence_length,
                "input_order": list(EXPECTED_INPUT_ORDER),
                "input_order_verified": self.input_order_verified,
                "inputs": {key: value.as_dict() for key, value in self.inputs.items()},
                "output": {"logits": self.logits.as_dict()},
                "vocabulary_size": self.vocabulary_size,
                "operator_audit": {
                    "opset": self.opset,
                    "unsupported_operators": list(self.unsupported_operators),
                },
            },
            **({"source_revision": self.source_revision} if self.source_revision else {}),
            **(
                {
                    "source_artifact": {
                        "bytes": self.source_bytes,
                        "sha256": self.source_sha256,
                    }
                }
                if self.source_bytes is not None and self.source_sha256 is not None
                else {}
            ),
        }


def _is_nonnegative_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
