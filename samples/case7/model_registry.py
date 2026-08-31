"""Validated model manifests and production admission for the smart album."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Mapping, Optional, Tuple


ROOT = Path(__file__).resolve().parent
DEFAULT_CANDIDATE_MANIFEST = ROOT / "candidate_manifest.json"
DEFAULT_REGISTRY = ROOT / "models" / "registry.json"
SUPPORTED_DTYPES = {"float16", "float32", "int32", "int64"}
ATC_PRECISION_MODES = {
    "allow_fp32_to_fp16",
    "allow_mix_precision",
    "force_fp32",
    "force_fp16",
    "must_keep_origin_dtype",
}


class RegistryError(RuntimeError):
    """Raised when a model manifest or admitted artifact is invalid."""


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


@dataclass(frozen=True)
class ComponentContract:
    kind: str
    onnx_path: Path
    om_path: Path
    input_name: str
    input_shape: Tuple[int, ...]
    input_dtype: str
    output_dtype: str
    atc_keep_dtype: Optional[Path] = None
    om_sha256: Optional[str] = None
    onnx_sha256: Optional[str] = None
    precision_mode: Optional[str] = None

    @classmethod
    def from_dict(cls, base: Path, kind: str, value: Mapping[str, object]):
        shape = tuple(int(item) for item in value.get("input_shape", ()))
        if not shape or any(item <= 0 for item in shape):
            raise RegistryError(f"{kind}: input_shape must contain positive dimensions")
        input_dtype = str(value.get("input_dtype", ""))
        output_dtype = str(value.get("output_dtype", ""))
        if input_dtype not in SUPPORTED_DTYPES or output_dtype not in SUPPORTED_DTYPES:
            raise RegistryError(f"{kind}: unsupported input/output dtype")
        precision_mode = value.get("precision_mode")
        if precision_mode is not None:
            precision_mode = str(precision_mode)
            if precision_mode not in ATC_PRECISION_MODES:
                raise RegistryError(f"{kind}: unsupported precision_mode {precision_mode!r}")
        return cls(
            kind=kind,
            onnx_path=_resolve(base, str(value["onnx"])),
            om_path=_resolve(base, str(value["om"])),
            input_name=str(value["input_name"]),
            input_shape=shape,
            input_dtype=input_dtype,
            output_dtype=output_dtype,
            atc_keep_dtype=(
                _resolve(base, str(value["atc_keep_dtype"]))
                if value.get("atc_keep_dtype")
                else None
            ),
            om_sha256=value.get("om_sha256") or None,
            onnx_sha256=value.get("onnx_sha256") or None,
            precision_mode=precision_mode,
        )

    @property
    def input_elements(self) -> int:
        total = 1
        for dimension in self.input_shape:
            total *= dimension
        return total


@dataclass(frozen=True)
class ModelRecord:
    model_id: str
    display_name: str
    embedding_dim: int
    languages: Tuple[str, ...]
    components: Mapping[str, ComponentContract]
    image_preprocess: Mapping[str, object]
    tokenizer: Optional[Mapping[str, object]]
    precision_mode: str
    status: str
    precision_strategy: Optional[Mapping[str, object]] = None

    @classmethod
    def from_dict(cls, base: Path, value: Mapping[str, object]):
        model_id = str(value.get("model_id", ""))
        if not model_id or "__npu__" not in model_id:
            raise RegistryError(f"invalid NPU model id: {model_id!r}")
        components_raw = value.get("components")
        if not isinstance(components_raw, Mapping) or "image" not in components_raw:
            raise RegistryError(f"{model_id}: image component is required")
        components = {
            str(kind): ComponentContract.from_dict(base, str(kind), component)
            for kind, component in components_raw.items()
        }
        embedding_dim = int(value.get("embedding_dim", 0))
        if embedding_dim <= 0:
            raise RegistryError(f"{model_id}: embedding_dim must be positive")
        precision_mode = str(value.get("precision_mode", "allow_fp32_to_fp16"))
        if precision_mode not in ATC_PRECISION_MODES:
            raise RegistryError(f"{model_id}: unsupported precision_mode {precision_mode!r}")
        return cls(
            model_id=model_id,
            display_name=str(value.get("display_name", model_id)),
            embedding_dim=embedding_dim,
            languages=tuple(str(item) for item in value.get("languages", ())),
            components=components,
            image_preprocess=dict(value.get("image_preprocess", {})),
            tokenizer=dict(value["tokenizer"]) if value.get("tokenizer") else None,
            precision_mode=precision_mode,
            status=str(value.get("status", "candidate")),
            precision_strategy=(
                dict(value["precision_strategy"])
                if isinstance(value.get("precision_strategy"), Mapping)
                else None
            ),
        )

    @property
    def supports_text(self) -> bool:
        return "text" in self.components

    def effective_precision_mode(self, component: ComponentContract, override: Optional[str] = None) -> str:
        """Resolve ATC precision without silently mixing diagnostic policies."""
        if override is not None:
            return override
        return component.precision_mode or self.precision_mode

    def verify_artifacts(self, require_hashes: bool = True) -> None:
        # Text models have a tokenizer as part of their executable input
        # contract.  Admit the model only when that asset is present and,
        # for production registries, byte-verified just like ONNX and OM.
        if self.supports_text:
            if not self.tokenizer or not self.tokenizer.get("path"):
                raise RegistryError(f"missing tokenizer contract for {self.model_id}")
            # Component paths are normalized during parsing; tokenizer paths
            # remain in the public contract for API/docs, so resolve them
            # against the same repository root here.
            tokenizer_path = _resolve(ROOT, str(self.tokenizer["path"]))
            if not tokenizer_path.is_file():
                raise RegistryError(f"missing tokenizer artifact: {tokenizer_path}")
            tokenizer_sha256 = self.tokenizer.get("sha256") or self.tokenizer.get("sha256sum")
            if require_hashes and not tokenizer_sha256:
                raise RegistryError(f"missing tokenizer SHA-256 for {self.model_id}")
            if tokenizer_sha256:
                actual = sha256_file(tokenizer_path)
                if actual.lower() != str(tokenizer_sha256).lower():
                    raise RegistryError(
                        f"tokenizer SHA-256 mismatch for {self.model_id}: {actual}"
                    )
        for component in self.components.values():
            if not component.onnx_path.is_file():
                raise RegistryError(f"missing ONNX artifact: {component.onnx_path}")
            if not component.om_path.is_file():
                raise RegistryError(f"missing OM artifact: {component.om_path}")
            if require_hashes and not component.onnx_sha256:
                raise RegistryError(f"missing ONNX SHA-256 for {self.model_id}/{component.kind}")
            if require_hashes and not component.om_sha256:
                raise RegistryError(f"missing OM SHA-256 for {self.model_id}/{component.kind}")
            if component.onnx_sha256:
                actual = sha256_file(component.onnx_path)
                if actual.lower() != component.onnx_sha256.lower():
                    raise RegistryError(
                        f"ONNX SHA-256 mismatch for {self.model_id}/{component.kind}: {actual}"
                    )
            if component.om_sha256:
                actual = sha256_file(component.om_path)
                if actual.lower() != component.om_sha256.lower():
                    raise RegistryError(
                        f"OM SHA-256 mismatch for {self.model_id}/{component.kind}: {actual}"
                    )


class ModelRegistry:
    """Loads model contracts and enforces production admission boundaries."""

    def __init__(self, path: Path = DEFAULT_REGISTRY, require_artifacts: bool = False):
        self.path = Path(path)
        self.base = ROOT
        self._models: Dict[str, ModelRecord] = {}
        if self.path.is_file():
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if int(payload.get("schema_version", 0)) != 1:
                raise RegistryError("unsupported registry schema_version")
            for value in payload.get("models", []):
                record = ModelRecord.from_dict(self.base, value)
                if record.status != "admitted":
                    raise RegistryError(f"production registry contains non-admitted model {record.model_id}")
                if record.model_id in self._models:
                    raise RegistryError(f"duplicate model id: {record.model_id}")
                if require_artifacts:
                    record.verify_artifacts(require_hashes=True)
                self._models[record.model_id] = record

    def get(self, model_id: str) -> ModelRecord:
        try:
            return self._models[model_id]
        except KeyError as exc:
            raise RegistryError(f"model is not production-admitted: {model_id}") from exc

    def all(self) -> Tuple[ModelRecord, ...]:
        return tuple(self._models.values())

    def ids(self) -> Tuple[str, ...]:
        return tuple(self._models)

    def __len__(self) -> int:
        return len(self._models)


def load_candidates(path: Path = DEFAULT_CANDIDATE_MANIFEST) -> Tuple[ModelRecord, ...]:
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if int(payload.get("schema_version", 0)) != 1:
        raise RegistryError("unsupported candidate manifest schema_version")
    return tuple(ModelRecord.from_dict(ROOT, value) for value in payload.get("models", []))


def model_dict(records: Iterable[ModelRecord]) -> Dict[str, ModelRecord]:
    return {record.model_id: record for record in records}
