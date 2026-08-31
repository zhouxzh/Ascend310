"""Validated runtime and candidate model registries.

``models/registry.json`` is intentionally small: the ``models`` collection
contains only adapters that are part of the running service.  Additional
public checkpoints are kept in the optional ``candidate_manifest.json`` and
are exposed through :meth:`ModelRegistry.candidates` without changing the
runtime model list consumed by the API or UI.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import re
import threading
from typing import Any, Mapping

from ..config import MANUAL_TEST_MODEL_IDS, REGISTRY_PATH, ROOT


_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IMMUTABLE_REVISION_RE = re.compile(r"^[0-9a-f]{40,64}$")
_BACKENDS = {"cpu", "npu"}

# Candidate adapters are intentionally narrower than the general candidate
# inventory.  They are only used by the offline benchmark path after a model
# was exported to the project's fixed Ascend input contract.
_OFFLINE_CANDIDATE_INPUT_SHAPE = (1, 1, 128, 128)
_OFFLINE_CANDIDATE_FEATURE_DIM = 512
_OFFLINE_CANDIDATE_INPUT_RANGES = {"zero_one", "nonzero_standardize"}
_PRODUCTION_PRECISION = "mixed_fp16"
_PRODUCTION_BACKEND = "npu"
_PRODUCTION_NPU_MODEL = "Ascend 310B4"
_PRODUCTION_COMPUTE_TIER = "8T"
_NUMERIC_MIN_MEAN_COSINE = 0.999
_NUMERIC_MIN_MIN_COSINE = 0.995


@dataclass(frozen=True)
class CandidateAdmission:
    """Result of the production gate for one candidate.

    Candidate metadata remains useful for offline audits even when it is not
    eligible for the live service.  Keeping the failure reasons structured
    lets the API report a deterministic explanation without creating an
    adapter for an unadmitted model.
    """

    candidate_id: str
    known: bool
    admitted: bool
    reasons: tuple[str, ...]

    @property
    def reason(self) -> str | None:
        return self.reasons[0] if self.reasons else None


def _non_empty_string(value: Any, field: str) -> str:
    """Return a trimmed string or fail with a useful registry error."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _model_id(value: Any, field: str = "id") -> str:
    identifier = _non_empty_string(value, field)
    if not _ID_RE.fullmatch(identifier):
        raise ValueError(
            f"{field} must match ^[a-z0-9][a-z0-9_-]*$: {identifier!r}"
        )
    return identifier


def _input_shape(value: Any, field: str) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"{field} must be a non-empty list of positive integers")
    shape: list[int] = []
    for index, dimension in enumerate(value):
        if isinstance(dimension, bool) or not isinstance(dimension, int) or dimension <= 0:
            raise ValueError(f"{field}[{index}] must be a positive integer")
        shape.append(dimension)
    return tuple(shape)


def _feature_dim(value: Any, field: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be null or a positive integer")
    return value


def _sha256(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{field} must be a lowercase SHA-256 string")
    return value


def _relative_asset_path(value: Any, field: str) -> Path:
    """Resolve a project-relative asset path and reject traversal.

    Asset paths are metadata, but they are later used by preparation and
    verification commands. Rejecting absolute and parent paths at load time
    keeps a malformed registry from making those commands operate outside the
    project directory. Both slash styles are checked because manifests are
    shared between Windows development machines and Linux boards.
    """
    relative = _non_empty_string(value, field)
    normalized = relative.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized):
        raise ValueError(f"{field} must be project-relative")
    if any(part in {"", ".", ".."} for part in normalized.split("/")):
        # Empty components are ambiguous (for example ``foo//bar``), and
        # parent components are unsafe. A single ``.`` is unnecessary too.
        raise ValueError(f"{field} must not contain empty or traversal components")
    candidate = Path(normalized)
    resolved = (ROOT / candidate).resolve()
    root = ROOT.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{field} resolves outside the project root") from exc
    return resolved


def _validate_optional_paths(item: Mapping[str, Any], label: str) -> None:
    for key in (
        "reference_onnx",
        "checkpoint",
        "library",
        "conversion_only_marker",
    ):
        if item.get(key) is not None:
            _relative_asset_path(item[key], f"{label}.{key}")
    om_models = item.get("om_models", {})
    if om_models is None:
        return
    if not isinstance(om_models, Mapping):
        raise ValueError(f"{label}.om_models must be an object")
    for precision, value in om_models.items():
        _non_empty_string(precision, f"{label}.om_models key")
        _relative_asset_path(value, f"{label}.om_models.{precision}")


@dataclass(frozen=True)
class ModelSpec:
    id: str
    display_name: str
    kind: str
    license: str
    research_only: bool
    source: str
    revision: str
    input_shape: tuple[int, ...]
    input_range: str
    feature_dim: int | None
    metric: str
    raw: dict[str, Any]

    def path(self, key: str) -> Path | None:
        value = self.raw.get(key)
        return _relative_asset_path(value, f"models[{self.id}].{key}") if value else None

    def om_path(self, precision: str) -> Path | None:
        value = self.raw.get("om_models", {}).get(precision)
        return (
            _relative_asset_path(value, f"models[{self.id}].om_models.{precision}")
            if value
            else None
        )


@dataclass(frozen=True)
class CandidateSpec:
    """Metadata for a public or locally downloaded candidate.

    Candidates are deliberately separate from :class:`ModelSpec`. A
    checkpoint can be audited and queued for ONNX/OM conversion without being
    advertised as a selectable runtime backend before that conversion passes
    its numerical and hardware gates.
    """

    id: str
    display_name: str
    family: str
    kind: str
    modality: str
    task: str
    comparison_scope: str
    license: str
    research_only: bool
    source: str
    revision: str
    input_shape: tuple[int, ...] | None
    input_range: str
    feature_dim: int | None
    metric: str
    weight_status: str
    npu_status: str
    available_backends: tuple[str, ...]
    checkpoint: str | None
    checkpoint_sha256: str | None
    checkpoint_size_bytes: int | None
    raw: dict[str, Any]

    @property
    def reproducible(self) -> bool:
        return bool(_IMMUTABLE_REVISION_RE.fullmatch(self.revision.strip().lower()))

    @property
    def reproducibility_reason(self) -> str | None:
        return None if self.reproducible else "mutable source revision"

    @property
    def weights_status(self) -> str:
        """Compatibility alias matching the manifest terminology."""
        return self.weight_status

    @property
    def checkpoint_verified(self) -> bool:
        return self.weight_status in {"local_verified", "verified_download"}

    @property
    def checkpoint_path(self) -> Path | None:
        """Absolute project path for the declared local checkpoint, if any."""
        return self.path("checkpoint")

    @property
    def sha256(self) -> str | None:
        """Short alias used by asset-audit callers."""
        return self.checkpoint_sha256

    @property
    def size_bytes(self) -> int | None:
        return self.checkpoint_size_bytes

    @property
    def has_local_checkpoint(self) -> bool:
        path = self.path("checkpoint")
        return bool(path and path.is_file())

    def path(self, key: str) -> Path | None:
        value = self.raw.get(key)
        if value is None and key == "checkpoint":
            value = self.checkpoint
        if value is None and key == "onnx_model":
            conversion = self.raw.get("conversion", {})
            if isinstance(conversion, Mapping):
                value = conversion.get("onnx_path")
        if value is None and key == "conversion_marker":
            conversion = self.raw.get("conversion", {})
            if isinstance(conversion, Mapping):
                value = conversion.get("marker_path")
        if value is None:
            # Candidate manifests store local files under weights.artifacts.
            value = _first_artifact_value(self.raw, key)
        return _relative_asset_path(value, f"candidates[{self.id}].{key}") if value else None

    def om_path(self, precision: str) -> Path | None:
        om_models = self.raw.get("om_models", {})
        if not om_models:
            conversion = self.raw.get("conversion", {})
            if isinstance(conversion, Mapping):
                om_models = conversion.get("om_paths", {})
        if not isinstance(om_models, Mapping):
            raise ValueError(f"candidates[{self.id}].om_models must be an object")
        value = om_models.get(precision)
        return (
            _relative_asset_path(
                value, f"candidates[{self.id}].om_models.{precision}"
            )
            if value
            else None
        )


def _first_artifact_value(item: Mapping[str, Any], key: str) -> Any:
    weights = item.get("weights")
    if not isinstance(weights, Mapping):
        return None
    artifacts = weights.get("artifacts", [])
    if not isinstance(artifacts, list):
        return None
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            continue
        if key == "checkpoint" and artifact.get("local_path"):
            return artifact["local_path"]
        if key in artifact and artifact.get(key) is not None:
            return artifact[key]
    return None


def _parse_contract_shape(contract: Any, field: str) -> tuple[int, ...] | None:
    if isinstance(contract, str):
        match = re.search(r"\[(\s*\d+(?:\s*,\s*\d+)*)\]", contract)
        if match:
            return _input_shape(
                [int(part.strip()) for part in match.group(1).split(",")], field
            )
        pair = re.search(r"(\d+)\s*[xX]\s*(\d+)", contract)
        if pair:
            return _input_shape([int(pair.group(1)), int(pair.group(2))], field)
    # Some audited candidates (for example a third-party MATLAB checkpoint)
    # intentionally do not publish their tensor contract. Keep those entries
    # visible for audit, but represent the contract as unknown rather than
    # inventing a shape that could make an exporter unsafe.
    return None


def _parse_candidate(item: Mapping[str, Any], index: int) -> CandidateSpec:
    label = f"candidates[{index}]"
    if not isinstance(item, Mapping):
        raise ValueError(f"{label} must be an object")

    identifier = _model_id(item.get("id"), f"{label}.id")
    display_name = _non_empty_string(item.get("display_name"), f"{label}.display_name")
    family = _non_empty_string(item.get("family", item.get("kind", "candidate")), f"{label}.family")
    kind_value = item.get("kind")
    if kind_value is None:
        task_hint = str(item.get("task", "embedding")).lower()
        if "code" in task_hint:
            kind_value = "code"
        elif "classifier" in task_hint:
            kind_value = "classifier"
        elif "embedding" in task_hint or "feature" in task_hint:
            kind_value = "embedding"
        else:
            kind_value = "candidate"
    kind = _non_empty_string(kind_value, f"{label}.kind")
    modality = _non_empty_string(item.get("modality", "palmprint"), f"{label}.modality")
    task = _non_empty_string(item.get("task", kind), f"{label}.task")
    comparison_scope = _non_empty_string(
        item.get("comparison_scope", "audit_only"), f"{label}.comparison_scope"
    )

    source_info = item.get("source")
    if isinstance(source_info, Mapping):
        source = _non_empty_string(source_info.get("url"), f"{label}.source.url")
        revision_value = source_info.get("revision", item.get("revision"))
    else:
        source = _non_empty_string(source_info, f"{label}.source")
        revision_value = item.get("revision")
    revision = _non_empty_string(revision_value, f"{label}.revision")

    license_info = item.get("license")
    if isinstance(license_info, Mapping):
        license_value = license_info.get("spdx")
    else:
        license_value = license_info
    license_name = _non_empty_string(license_value, f"{label}.license")

    contract = item.get("input_contract")
    shape_value = item.get("input_shape")
    shape: tuple[int, ...] | None = (
        _input_shape(shape_value, f"{label}.input_shape")
        if shape_value is not None
        else _parse_contract_shape(contract, f"{label}.input_contract")
    )
    range_value = item.get("input_range")
    if range_value is None:
        contract_text = str(contract or "").lower()
        if "nonzero" in contract_text or "standard" in contract_text:
            range_value = "nonzero_standardize"
        elif "[0,1]" in contract_text or "zero_one" in contract_text:
            range_value = "zero_one"
        elif "uint8" in contract_text:
            range_value = "uint8"
        else:
            range_value = "unspecified"
    input_range = _non_empty_string(range_value, f"{label}.input_range")

    output_contract = str(item.get("output_contract", ""))
    feature_value = item.get("feature_dim")
    if feature_value is None:
        feature_match = re.search(r"(\d+)\s*[- ]?D", output_contract, flags=re.IGNORECASE)
        feature_value = int(feature_match.group(1)) if feature_match else None
    feature_dim = _feature_dim(feature_value, f"{label}.feature_dim")
    metric_value = item.get("metric")
    if metric_value is None:
        metric_value = "edcc" if "edcc" in output_contract.lower() else "cosine"
    metric = _non_empty_string(metric_value, f"{label}.metric")

    weights = item.get("weights", {})
    if weights is None:
        weights = {}
    if not isinstance(weights, Mapping):
        raise ValueError(f"{label}.weights must be an object")
    weight_status = item.get("weight_status", weights.get("availability", "unknown"))
    weight_status = _non_empty_string(weight_status, f"{label}.weight_status")

    npu_status = _non_empty_string(item.get("npu_status", "unknown"), f"{label}.npu_status")
    available_value = item.get("available_backends")
    if available_value is None:
        if npu_status == "om_ready":
            available_value = ["npu"]
        elif npu_status == "not_applicable" and kind.lower() == "code":
            available_value = ["cpu"]
        else:
            available_value = []
    if not isinstance(available_value, (list, tuple)):
        raise ValueError(f"{label}.available_backends must be a list")
    backends: list[str] = []
    for backend in available_value:
        backend_name = _non_empty_string(backend, f"{label}.available_backends item")
        if backend_name not in _BACKENDS:
            raise ValueError(
                f"{label}.available_backends contains unsupported backend: {backend_name}"
            )
        if backend_name not in backends:
            backends.append(backend_name)

    checkpoint = item.get("checkpoint") or _first_artifact_value(item, "checkpoint")
    if checkpoint is not None:
        checkpoint = _non_empty_string(checkpoint, f"{label}.checkpoint")
        _relative_asset_path(checkpoint, f"{label}.checkpoint")
    sha_value = item.get("checkpoint_sha256") or _first_artifact_value(item, "sha256")
    checkpoint_sha = _sha256(sha_value, f"{label}.checkpoint_sha256")
    size_value = item.get("checkpoint_size_bytes")
    if size_value is None:
        size_value = _first_artifact_value(item, "bytes")
    if size_value is not None and (
        isinstance(size_value, bool) or not isinstance(size_value, int) or size_value <= 0
    ):
        raise ValueError(f"{label}.checkpoint_size_bytes must be a positive integer or null")

    if item.get("om_models") is not None:
        _validate_optional_paths(item, label)
    conversion = item.get("conversion")
    if conversion is not None:
        if not isinstance(conversion, Mapping):
            raise ValueError(f"{label}.conversion must be an object")
        onnx_path = conversion.get("onnx_path")
        marker_path = conversion.get("marker_path")
        if onnx_path is not None:
            _relative_asset_path(onnx_path, f"{label}.conversion.onnx_path")
        if marker_path is not None:
            _relative_asset_path(marker_path, f"{label}.conversion.marker_path")
        om_paths = conversion.get("om_paths", {})
        if not isinstance(om_paths, Mapping):
            raise ValueError(f"{label}.conversion.om_paths must be an object")
        for precision, value in om_paths.items():
            _non_empty_string(precision, f"{label}.conversion.om_paths key")
            _relative_asset_path(value, f"{label}.conversion.om_paths.{precision}")
    research_only = bool(item.get("research_only", True))
    return CandidateSpec(
        id=identifier,
        display_name=display_name,
        family=family,
        kind=kind,
        modality=modality,
        task=task,
        comparison_scope=comparison_scope,
        license=license_name,
        research_only=research_only,
        source=source,
        revision=revision,
        input_shape=shape,
        input_range=input_range,
        feature_dim=feature_dim,
        metric=metric,
        weight_status=weight_status,
        npu_status=npu_status,
        available_backends=tuple(backends),
        checkpoint=checkpoint,
        checkpoint_sha256=checkpoint_sha,
        checkpoint_size_bytes=size_value,
        raw=dict(item),
    )


def _runtime_asset_metadata(
    item: Mapping[str, Any],
    name: str,
    label: str,
) -> Mapping[str, Any]:
    assets = item.get("assets")
    if not isinstance(assets, Mapping):
        raise ValueError(f"{label}.assets must be an object")
    asset = assets.get(name)
    if not isinstance(asset, Mapping):
        raise ValueError(f"{label}.assets.{name} must be an object")
    path_value = asset.get("path")
    _relative_asset_path(path_value, f"{label}.assets.{name}.path")
    size = asset.get("bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise ValueError(f"{label}.assets.{name}.bytes must be a positive integer")
    _sha256(asset.get("sha256"), f"{label}.assets.{name}.sha256")
    return asset


def _validate_production_model(item: Mapping[str, Any], label: str) -> None:
    """Reject offline or incompletely pinned entries from ``models``.

    A source registry is part of the production trust boundary. Merely having
    an OM path is insufficient: the serving contract, precision and immutable
    asset identities must all be present before an entry can appear in
    ``ModelRegistry.all()`` or ``/api/bootstrap``.
    """

    if item.get("production_enabled") is not True:
        raise ValueError(f"{label}.production_enabled must be true")
    if item.get("kind") != "embedding":
        raise ValueError(f"{label}.kind must be embedding for production")
    if item.get("backend") != _PRODUCTION_BACKEND:
        raise ValueError(f"{label}.backend must be {_PRODUCTION_BACKEND}")
    if item.get("precision") != _PRODUCTION_PRECISION:
        raise ValueError(f"{label}.precision must be {_PRODUCTION_PRECISION}")
    if item.get("metric") != "cosine":
        raise ValueError(f"{label}.metric must be cosine for production")

    om_models = item.get("om_models")
    if not isinstance(om_models, Mapping) or set(om_models) != {_PRODUCTION_PRECISION}:
        raise ValueError(
            f"{label}.om_models must contain only {_PRODUCTION_PRECISION}"
        )
    reference_path = item.get("reference_onnx")
    if reference_path is None:
        raise ValueError(f"{label}.reference_onnx is required")
    reference = _runtime_asset_metadata(item, "reference_onnx", label)
    mixed = _runtime_asset_metadata(item, "mixed_fp16_om", label)
    if reference.get("path") != reference_path:
        raise ValueError(f"{label}.assets.reference_onnx.path must match reference_onnx")
    if mixed.get("path") != om_models.get(_PRODUCTION_PRECISION):
        raise ValueError(
            f"{label}.assets.mixed_fp16_om.path must match om_models.{_PRODUCTION_PRECISION}"
        )

    calibration = item.get("calibration")
    if not isinstance(calibration, Mapping):
        raise ValueError(f"{label}.calibration must be an object")
    _non_empty_string(calibration.get("dataset"), f"{label}.calibration.dataset")
    _non_empty_string(calibration.get("protocol"), f"{label}.calibration.protocol")
    if calibration.get("metric") != item.get("metric"):
        raise ValueError(f"{label}.calibration.metric must match model metric")
    threshold = calibration.get("threshold")
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, (int, float))
        or not math.isfinite(float(threshold))
        or not 0.0 <= float(threshold) <= 1.0
    ):
        raise ValueError(f"{label}.calibration.threshold must be between 0 and 1")


def _parse_model(
    item: Mapping[str, Any],
    index: int,
    *,
    collection: str = "models",
    production: bool = False,
) -> ModelSpec:
    label = f"{collection}[{index}]"
    if not isinstance(item, Mapping):
        raise ValueError(f"{label} must be an object")
    identifier = _model_id(item.get("id"), f"{label}.id")
    display_name = _non_empty_string(item.get("display_name"), f"{label}.display_name")
    kind = _non_empty_string(item.get("kind"), f"{label}.kind")
    license_name = _non_empty_string(item.get("license"), f"{label}.license")
    source = _non_empty_string(item.get("source"), f"{label}.source")
    revision = _non_empty_string(item.get("revision"), f"{label}.revision")
    shape = _input_shape(item.get("input_shape"), f"{label}.input_shape")
    input_range = _non_empty_string(item.get("input_range"), f"{label}.input_range")
    feature_dim = _feature_dim(item.get("feature_dim"), f"{label}.feature_dim")
    metric = _non_empty_string(item.get("metric"), f"{label}.metric")
    _validate_optional_paths(item, label)
    if production:
        _validate_production_model(item, label)
    return ModelSpec(
        id=identifier,
        display_name=display_name,
        kind=kind,
        license=license_name,
        research_only=bool(item.get("research_only", False)),
        source=source,
        revision=revision,
        input_shape=shape,
        input_range=input_range,
        feature_dim=feature_dim,
        metric=metric,
        raw=dict(item),
    )


class ModelRegistry:
    """Load runtime models and optional conversion candidates.

    ``candidate_path`` is useful for tests and for tooling that receives a
    detached manifest. In normal operation the path declared by
    ``candidate_registry`` in ``models/registry.json`` is used; if it is not
    declared, the conventional project-root ``candidate_manifest.json`` is
    discovered for backwards compatibility.
    """

    def __init__(
        self,
        path: Path = REGISTRY_PATH,
        candidate_path: Path | None = None,
    ) -> None:
        path = Path(path)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Unable to read model registry {path}: {exc}") from exc
        if not isinstance(payload, Mapping) or payload.get("schema_version") != 1:
            raise ValueError("Unsupported model registry schema")

        admitted_candidate_ids = payload.get("admitted_candidates", [])
        if not isinstance(admitted_candidate_ids, list):
            raise ValueError("admitted_candidates must be a list")
        admitted: list[str] = []
        for index, candidate_id in enumerate(admitted_candidate_ids):
            identifier = _model_id(candidate_id, f"admitted_candidates[{index}]")
            if identifier in admitted:
                raise ValueError(f"Duplicate admitted candidate id: {identifier}")
            admitted.append(identifier)
        self._admitted_candidate_ids = tuple(admitted)

        self._models: dict[str, ModelSpec] = {}
        model_items = payload.get("models", [])
        if not isinstance(model_items, list):
            raise ValueError("models must be a list")
        for index, item in enumerate(model_items):
            spec = _parse_model(item, index, production=True)
            if spec.id in self._models:
                raise ValueError(f"Duplicate model id: {spec.id}")
            self._models[spec.id] = spec

        self._offline_models: dict[str, ModelSpec] = {}
        offline_items = payload.get("offline_models", [])
        if not isinstance(offline_items, list):
            raise ValueError("offline_models must be a list")
        for index, item in enumerate(offline_items):
            spec = _parse_model(
                item,
                index,
                collection="offline_models",
                production=False,
            )
            if spec.id in self._models or spec.id in self._offline_models:
                raise ValueError(f"Duplicate production/offline model id: {spec.id}")
            self._offline_models[spec.id] = spec

        self._asset_cache: dict[tuple[str, int, int], str] = {}
        self._asset_cache_lock = threading.RLock()

        self._candidates: dict[str, CandidateSpec] = {}
        if "candidates" in payload:
            candidate_items = payload.get("candidates")
            if not isinstance(candidate_items, list):
                raise ValueError("candidates must be a list")
        else:
            candidate_items = self._load_external_candidates(payload, path, candidate_path)
        for index, item in enumerate(candidate_items):
            spec = _parse_candidate(item, index)
            if spec.id in self._candidates:
                raise ValueError(f"Duplicate candidate id: {spec.id}")
            self._candidates[spec.id] = spec

        for candidate_id in self._admitted_candidate_ids:
            if candidate_id in self._models:
                raise ValueError(
                    "admitted_candidates collides with runtime model id: "
                    f"{candidate_id}"
                )
            if candidate_id not in self._candidates:
                raise ValueError(
                    f"admitted_candidates references unknown candidate: {candidate_id}"
                )

    @staticmethod
    def _load_external_candidates(
        payload: Mapping[str, Any], registry_path: Path, candidate_path: Path | None
    ) -> list[Mapping[str, Any]]:
        if candidate_path is None:
            declared = payload.get("candidate_registry", payload.get("candidate_manifest"))
            if declared:
                # Registry references are always project-relative. This is
                # intentionally separate from the explicit test/tool path.
                candidate_path = _relative_asset_path(declared, "candidate_registry")
            elif registry_path.resolve() == REGISTRY_PATH.resolve():
                candidate_path = ROOT / "candidate_manifest.json"
        if candidate_path is None or not Path(candidate_path).exists():
            return []
        candidate_file = Path(candidate_path)
        try:
            candidate_payload = json.loads(candidate_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Unable to read candidate registry {candidate_file}: {exc}") from exc
        if not isinstance(candidate_payload, Mapping):
            raise ValueError("Candidate registry must be an object")
        candidates = candidate_payload.get("candidates", [])
        if not isinstance(candidates, list):
            raise ValueError("Candidate registry candidates must be a list")
        return candidates

    def get_runtime(self, model_id: str, *, include_manual_test: bool = False) -> ModelSpec:
        """Resolve a production or explicitly enabled manual-test model."""

        if model_id in self._models:
            return self._models[model_id]
        if include_manual_test and model_id in MANUAL_TEST_MODEL_IDS:
            return self.manual_test_candidate_embedding_spec(model_id)
        return self.production_candidate_embedding_spec(model_id)

    def get_offline_model(self, model_id: str) -> ModelSpec:
        """Resolve a legacy CPU/conversion model for explicit offline tooling."""

        try:
            return self._offline_models[model_id]
        except KeyError as exc:
            raise KeyError(f"Unknown offline model: {model_id}") from exc

    def get(self, model_id: str) -> ModelSpec:
        """Compatibility resolver; production code must call ``get_runtime``.

        Existing export and benchmark commands predate the production/offline
        split. Keeping this method avoids breaking those explicit CLIs during
        the transition, while FastAPI and Workbench serving paths use the
        strict resolver.
        """

        try:
            return self.get_runtime(model_id)
        except KeyError:
            return self.get_offline_model(model_id)

    def all(self, *, include_manual_test: bool = False) -> list[ModelSpec]:
        """Return production models and optional manual-test candidates."""
        models = list(self._models.values())
        for candidate_id in self._admitted_candidate_ids:
            decision = self.candidate_admission(candidate_id, verify_assets=True)
            if decision.admitted:
                models.append(self._candidate_embedding_spec(candidate_id, production=True))
        if include_manual_test:
            for candidate_id in MANUAL_TEST_MODEL_IDS:
                if candidate_id in self._admitted_candidate_ids:
                    continue
                decision = self.manual_test_candidate_admission(
                    candidate_id, verify_assets=True
                )
                if decision.admitted:
                    models.append(self.manual_test_candidate_embedding_spec(candidate_id))
        return models

    def offline_models(self) -> list[ModelSpec]:
        """Return offline-only legacy entries; never use this in serving UI."""

        return list(self._offline_models.values())

    def get_candidate(self, candidate_id: str) -> CandidateSpec:
        try:
            return self._candidates[candidate_id]
        except KeyError as exc:
            raise KeyError(f"Unknown candidate: {candidate_id}") from exc

    def candidates(self) -> list[CandidateSpec]:
        """Return audited candidates, without adding them to ``all()``."""
        return list(self._candidates.values())

    def candidates_for(self, family: str) -> list[CandidateSpec]:
        family_name = _non_empty_string(family, "family")
        return [item for item in self._candidates.values() if item.family == family_name]

    def admitted_candidate_ids(self) -> list[str]:
        """Return candidate IDs explicitly listed for production admission.

        Listing a candidate is deliberately not enough to make it callable:
        :meth:`candidate_admission` also requires all evidence and asset gates.
        """

        return list(self._admitted_candidate_ids)

    @staticmethod
    def _asset_digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _cached_asset_digest(self, path: Path, *, size: int, mtime_ns: int) -> str:
        key = (str(path.resolve()), int(size), int(mtime_ns))
        with self._asset_cache_lock:
            cached = self._asset_cache.get(key)
        if cached is not None:
            return cached
        digest = self._asset_digest(path)
        with self._asset_cache_lock:
            # Each path needs at most one current stat tuple. Removing stale
            # tuples prevents long-running services from accumulating cache
            # entries when an operator replaces an asset during staging.
            for old_key in [item for item in self._asset_cache if item[0] == key[0]]:
                self._asset_cache.pop(old_key, None)
            self._asset_cache[key] = digest
        return digest

    def runtime_asset_status(
        self,
        model: str | ModelSpec,
        *,
        verify_hash: bool = True,
    ) -> dict[str, Any]:
        """Verify immutable production assets without initializing PyACL."""

        spec = self.get_runtime(model) if isinstance(model, str) else model
        is_manual_candidate = bool(spec.raw.get("manual_test_candidate", False))
        if (
            spec.id not in self._models
            and spec.id not in self._admitted_candidate_ids
            and not is_manual_candidate
        ):
            return {
                "ok": False,
                "status": "not_production",
                "model_id": spec.id,
                "precision": _PRODUCTION_PRECISION,
                "assets": {},
                "reasons": ["模型未列入生产 registry"],
            }

        assets = spec.raw.get("assets")
        if is_manual_candidate and not isinstance(assets, Mapping):
            # Manual-test specs are resolved from candidate_manifest rather
            # than copied into the production registry.  Preserve the same
            # immutable asset gate by adapting the candidate's declared
            # bytes/SHA fields to the runtime verifier's schema.
            assets = {
                "reference_onnx": {
                    "bytes": spec.raw.get("onnx_bytes"),
                    "sha256": spec.raw.get("onnx_sha256"),
                },
                "mixed_fp16_om": {
                    "bytes": spec.raw.get("mixed_fp16_bytes"),
                    "sha256": spec.raw.get("mixed_fp16_sha256"),
                },
            }
        if not isinstance(assets, Mapping):
            # Candidate-derived ModelSpec objects keep the same immutable
            # fields under their admission/conversion metadata.
            admission = spec.raw.get("production_admission")
            assets = admission.get("artifacts") if isinstance(admission, Mapping) else None
        if not isinstance(assets, Mapping):
            return {
                "ok": False,
                "status": "invalid_metadata",
                "model_id": spec.id,
                "precision": _PRODUCTION_PRECISION,
                "assets": {},
                "reasons": ["生产模型缺少资产字节数和 SHA-256"],
            }

        # The board only executes the mixed-FP16 OM.  A reference ONNX file is
        # useful for export/contract audits but is deliberately optional in a
        # runtime asset package downloaded by readers.
        declarations: tuple[
            tuple[str, Path | None, Mapping[str, Any] | None, bool], ...
        ] = (
            (
                "reference_onnx",
                spec.path("reference_onnx"),
                assets.get("reference_onnx")
                if isinstance(assets.get("reference_onnx"), Mapping)
                else assets.get("onnx") if isinstance(assets.get("onnx"), Mapping) else None,
                False,
            ),
            (
                "mixed_fp16_om",
                spec.om_path(_PRODUCTION_PRECISION),
                assets.get("mixed_fp16_om")
                if isinstance(assets.get("mixed_fp16_om"), Mapping)
                else None,
                True,
            ),
        )
        rows: dict[str, Any] = {}
        reasons: list[str] = []
        for name, path, metadata, required in declarations:
            expected_bytes = metadata.get("bytes") if metadata else None
            expected_sha = metadata.get("sha256") if metadata else None
            row: dict[str, Any] = {
                "path": str(path.relative_to(ROOT)) if path is not None else None,
                "expected_bytes": expected_bytes,
                "expected_sha256": expected_sha,
                "exists": False,
                "bytes_match": False,
                "sha256_match": False,
                "status": "missing",
            }
            if path is None or metadata is None:
                reasons.append(f"{name} 缺少路径或校验元数据")
                rows[name] = row
                continue
            try:
                stat = path.stat()
            except OSError as exc:
                if not required:
                    row["status"] = "optional_missing"
                    row["optional"] = True
                    rows[name] = row
                    continue
                reasons.append(f"{name} 文件不可用: {exc}")
                rows[name] = row
                continue
            row["exists"] = path.is_file()
            row["actual_bytes"] = stat.st_size
            row["bytes_match"] = stat.st_size == expected_bytes
            if not row["exists"]:
                if required:
                    reasons.append(f"{name} 不是普通文件")
                else:
                    row["status"] = "optional_missing"
                    row["optional"] = True
                    rows[name] = row
                    continue
            elif not row["bytes_match"]:
                reasons.append(
                    f"{name} bytes 不匹配: expected {expected_bytes}, got {stat.st_size}"
                )
            elif verify_hash:
                actual_sha = self._cached_asset_digest(
                    path,
                    size=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                )
                row["actual_sha256"] = actual_sha
                row["sha256_match"] = actual_sha == expected_sha
                if not row["sha256_match"]:
                    reasons.append(f"{name} SHA-256 不匹配")
            else:
                row["sha256_match"] = None
            row["status"] = (
                "ready"
                if row["exists"]
                and row["bytes_match"]
                and (row["sha256_match"] is True or not verify_hash)
                else "mismatch"
            )
            rows[name] = row

        return {
            "ok": not reasons,
            "status": "ready" if not reasons else "blocked",
            "model_id": spec.id,
            "backend": _PRODUCTION_BACKEND,
            "precision": _PRODUCTION_PRECISION,
            "assets": rows,
            "reasons": reasons,
        }

    def model_threshold(self, model_id: str) -> float:
        """Return the selected production model's declared cosine threshold."""

        spec = self.get_runtime(model_id)
        calibration = spec.raw.get("calibration")
        if isinstance(calibration, Mapping):
            threshold = calibration.get("threshold")
            if isinstance(threshold, (int, float)) and not isinstance(threshold, bool):
                return float(threshold)
        raise ValueError(f"Production model {model_id} has no calibration threshold")

    @staticmethod
    def _mapping(value: Any) -> Mapping[str, Any] | None:
        return value if isinstance(value, Mapping) else None

    @staticmethod
    def _append_unique(reasons: list[str], reason: str) -> None:
        if reason not in reasons:
            reasons.append(reason)

    def _check_asset(
        self,
        *,
        label: str,
        path: Path | None,
        expected_bytes: Any,
        expected_sha256: Any,
        reasons: list[str],
        verify_assets: bool,
    ) -> None:
        if not isinstance(expected_bytes, int) or isinstance(expected_bytes, bool) or expected_bytes <= 0:
            self._append_unique(reasons, f"{label} 缺少有效 bytes")
        if not _SHA256_RE.fullmatch(str(expected_sha256 or "")):
            self._append_unique(reasons, f"{label} 缺少有效 SHA-256")
        if path is None:
            self._append_unique(reasons, f"{label} 缺少项目内路径")
            return
        if not verify_assets or not isinstance(expected_bytes, int) or not _SHA256_RE.fullmatch(str(expected_sha256 or "")):
            return
        try:
            if not path.is_file():
                self._append_unique(reasons, f"{label} 文件不存在: {path.relative_to(ROOT)}")
                return
            actual_bytes = path.stat().st_size
            if actual_bytes != expected_bytes:
                self._append_unique(
                    reasons,
                    f"{label} bytes 不匹配: expected {expected_bytes}, got {actual_bytes}",
                )
                return
            actual_sha256 = self._asset_digest(path)
            if actual_sha256 != expected_sha256:
                self._append_unique(reasons, f"{label} SHA-256 不匹配")
        except OSError as exc:
            self._append_unique(reasons, f"无法校验 {label}: {exc}")

    def _candidate_embedding_contract_errors(
        self, candidate: CandidateSpec
    ) -> list[str]:
        errors: list[str] = []
        task_type = str(candidate.raw.get("task_type", "")).strip().lower()
        if candidate.kind != "embedding" or task_type != "embedding":
            errors.append("候选不是单输入 embedding 模型")
        task_text = candidate.task.strip().lower()
        if "single-input" not in task_text and "single input" not in task_text:
            errors.append("候选未声明 single-input 推理契约")
        if candidate.input_shape != _OFFLINE_CANDIDATE_INPUT_SHAPE:
            errors.append(
                "输入形状必须为 "
                f"{_OFFLINE_CANDIDATE_INPUT_SHAPE}，当前为 {candidate.input_shape}"
            )
        if candidate.feature_dim != _OFFLINE_CANDIDATE_FEATURE_DIM:
            errors.append(
                f"特征维度必须为 {_OFFLINE_CANDIDATE_FEATURE_DIM}，当前为 {candidate.feature_dim}"
            )
        if candidate.metric != "cosine":
            errors.append(f"相似度度量必须为 cosine，当前为 {candidate.metric}")
        if candidate.input_range not in _OFFLINE_CANDIDATE_INPUT_RANGES:
            errors.append(f"不支持的输入预处理: {candidate.input_range}")
        return errors

    def _candidate_embedding_spec(
        self, candidate_id: str, *, production: bool
    ) -> ModelSpec:
        candidate = self.get_candidate(candidate_id)
        errors = self._candidate_embedding_contract_errors(candidate)
        if errors:
            raise ValueError(f"Candidate {candidate.id} cannot use embedding adapter: {'; '.join(errors)}")

        conversion = self._mapping(candidate.raw.get("conversion")) or {}
        onnx_path = candidate.path("onnx_model")
        mixed_path = candidate.om_path(_PRODUCTION_PRECISION)
        if onnx_path is None or mixed_path is None:
            raise ValueError(
                f"Candidate {candidate.id} requires ONNX plus {_PRODUCTION_PRECISION} OM paths"
            )
        origin_path = candidate.om_path("origin")
        if not production and origin_path is None:
            raise ValueError(
                f"Candidate {candidate.id} requires an origin OM path for offline diagnostics"
            )
        marker_path = candidate.path("conversion_marker")
        raw: dict[str, Any] = {
            "reference_onnx": str(onnx_path.relative_to(ROOT)),
            "om_models": {
                _PRODUCTION_PRECISION: str(mixed_path.relative_to(ROOT)),
            },
            "offline_candidate": not production,
            "production_candidate": production,
            "production_enabled": bool(candidate.raw.get("production_enabled", False)),
            "candidate_id": candidate.id,
            "candidate_weight_status": candidate.weight_status,
            "candidate_npu_status": candidate.npu_status,
            "checkpoint_bytes": candidate.checkpoint_size_bytes,
            "checkpoint_sha256": candidate.checkpoint_sha256,
            "onnx_bytes": conversion.get("onnx_bytes"),
            "onnx_sha256": conversion.get("onnx_sha256"),
            "mixed_fp16_bytes": conversion.get("mixed_fp16_bytes"),
            "mixed_fp16_sha256": conversion.get("mixed_fp16_sha256"),
            "production_admission": candidate.raw.get("production_admission"),
        }
        admission = candidate.raw.get("production_admission")
        if production and isinstance(admission, Mapping):
            calibration = admission.get("calibration")
            if isinstance(calibration, Mapping):
                raw["calibration"] = dict(calibration)
        if not production and origin_path is not None:
            raw["om_models"]["origin"] = str(origin_path.relative_to(ROOT))
        if marker_path is not None:
            raw["conversion_only_marker"] = str(marker_path.relative_to(ROOT))
        _validate_optional_paths(raw, f"candidates[{candidate.id}].adapter")
        return ModelSpec(
            id=candidate.id,
            display_name=candidate.display_name,
            kind="embedding",
            license=candidate.license,
            research_only=candidate.research_only,
            source=candidate.source,
            revision=candidate.revision,
            input_shape=_OFFLINE_CANDIDATE_INPUT_SHAPE,
            input_range=candidate.input_range,
            feature_dim=_OFFLINE_CANDIDATE_FEATURE_DIM,
            metric="cosine",
            raw=raw,
        )

    def candidate_admission(
        self, candidate_id: str, *, verify_assets: bool = False
    ) -> CandidateAdmission:
        """Evaluate the complete production admission gate for a candidate.

        ``verify_assets=False`` is suitable for the audit UI: it verifies the
        immutable manifest evidence without opening ignored model artifacts.
        Runtime resolution always passes ``True`` so a listed candidate cannot
        execute if any checkpoint, ONNX, or OM byte count/hash has drifted.
        """

        try:
            candidate = self.get_candidate(candidate_id)
        except KeyError:
            return CandidateAdmission(candidate_id, False, False, ("未知候选",))

        reasons: list[str] = []
        if candidate.id not in self._admitted_candidate_ids:
            self._append_unique(reasons, "候选未列入 models/registry.json 的 admitted_candidates")
        if not _IMMUTABLE_REVISION_RE.fullmatch(candidate.revision.strip().lower()):
            self._append_unique(reasons, "候选 source revision 不是固定 commit SHA")
        if candidate.raw.get("production_enabled") is not True:
            self._append_unique(reasons, "candidate_manifest 未设置 production_enabled=true")
        if candidate.weight_status != "local_verified":
            self._append_unique(reasons, "checkpoint 不是 local_verified")
        if candidate.npu_status != "om_ready":
            self._append_unique(reasons, "候选 npu_status 不是 om_ready")
        if candidate.modality.strip().lower() != "palmprint":
            self._append_unique(reasons, "生产候选 modality 必须为 palmprint")
        if "npu" not in candidate.available_backends:
            self._append_unique(reasons, "生产候选 available_backends 必须包含 npu")
        for error in self._candidate_embedding_contract_errors(candidate):
            self._append_unique(reasons, error)

        weights = self._mapping(candidate.raw.get("weights")) or {}
        artifacts = weights.get("artifacts")
        checkpoint_artifact: Mapping[str, Any] | None = None
        if isinstance(artifacts, list):
            for artifact in artifacts:
                if isinstance(artifact, Mapping) and artifact.get("local_path"):
                    checkpoint_artifact = artifact
                    break
        if checkpoint_artifact is None:
            self._append_unique(reasons, "缺少本地 checkpoint 资产记录")
            checkpoint_artifact = {}

        conversion = self._mapping(candidate.raw.get("conversion")) or {}
        om_paths = self._mapping(conversion.get("om_paths")) or {}
        if conversion.get("om_status") != "mixed_fp16_ready":
            self._append_unique(reasons, "mixed-FP16 OM 尚未标记为正式 ready")
        if conversion.get("board_npu_status") != "om_ready":
            self._append_unique(reasons, "ACL NPU 状态尚未标记为 om_ready")
        self._check_asset(
            label="checkpoint",
            path=candidate.path("checkpoint"),
            expected_bytes=checkpoint_artifact.get("bytes"),
            expected_sha256=checkpoint_artifact.get("sha256"),
            reasons=reasons,
            verify_assets=verify_assets,
        )
        self._check_asset(
            label="ONNX",
            path=candidate.path("onnx_model"),
            expected_bytes=conversion.get("onnx_bytes"),
            expected_sha256=conversion.get("onnx_sha256"),
            reasons=reasons,
            verify_assets=verify_assets,
        )
        self._check_asset(
            label="mixed-FP16 OM",
            path=candidate.om_path(_PRODUCTION_PRECISION),
            expected_bytes=conversion.get("mixed_fp16_bytes"),
            expected_sha256=conversion.get("mixed_fp16_sha256"),
            reasons=reasons,
            verify_assets=verify_assets,
        )
        if _PRODUCTION_PRECISION not in om_paths:
            self._append_unique(reasons, "conversion.om_paths 缺少 mixed_fp16")

        admission = self._mapping(candidate.raw.get("production_admission"))
        if admission is None:
            self._append_unique(reasons, "缺少 production_admission 证据")
        else:
            self._validate_admission_evidence(
                candidate,
                admission,
                checkpoint_artifact,
                conversion,
                reasons,
            )

        return CandidateAdmission(
            candidate.id,
            True,
            not reasons,
            tuple(reasons),
        )

    def manual_test_candidate_admission(
        self, candidate_id: str, *, verify_assets: bool = True
    ) -> CandidateAdmission:
        """Validate the narrower frozen manual-test release contract.

        This channel deliberately does not assert the formal PolyU/lifecycle
        gate. It still requires an explicit allow-list entry, a palmprint
        single-input 512-D contract, and immutable local artifact hashes.
        The result is only used when ``PALMPRINT_PROFILE=manual_test``.
        """

        try:
            candidate = self.get_candidate(candidate_id)
        except KeyError:
            return CandidateAdmission(candidate_id, False, False, ("未知候选",))

        reasons: list[str] = []
        if candidate.id not in MANUAL_TEST_MODEL_IDS:
            self._append_unique(reasons, "候选不在 manual_test allow-list")
        if candidate.raw.get("manual_test_enabled") is not True:
            self._append_unique(reasons, "候选未设置 manual_test_enabled=true")
        if candidate.modality.strip().lower() != "palmprint":
            self._append_unique(reasons, "人工测试候选 modality 必须为 palmprint")
        if "npu" not in candidate.available_backends:
            self._append_unique(reasons, "人工测试候选 available_backends 必须包含 npu")
        for error in self._candidate_embedding_contract_errors(candidate):
            self._append_unique(reasons, error)

        conversion = self._mapping(candidate.raw.get("conversion")) or {}
        if conversion.get("om_status") not in {
            "mixed_fp16_ready_smoke_only",
            "mixed_fp16_ready",
        }:
            self._append_unique(reasons, "mixed-FP16 OM 尚未完成资产检查")
        if conversion.get("board_npu_status") not in {
            "om_ready_smoke_only",
            "om_ready",
        }:
            self._append_unique(reasons, "NPU OM 状态尚未完成 smoke 检查")

        weights = self._mapping(candidate.raw.get("weights")) or {}
        artifacts = weights.get("artifacts")
        checkpoint = None
        if isinstance(artifacts, list):
            checkpoint = next(
                (
                    item
                    for item in artifacts
                    if isinstance(item, Mapping) and item.get("local_path")
                ),
                None,
            )
        if checkpoint is None:
            self._append_unique(reasons, "缺少 checkpoint 资产记录")
            checkpoint = {}
        self._check_asset(
            label="checkpoint",
            path=candidate.path("checkpoint"),
            expected_bytes=checkpoint.get("bytes"),
            expected_sha256=checkpoint.get("sha256"),
            reasons=reasons,
            verify_assets=verify_assets,
        )
        self._check_asset(
            label="ONNX",
            path=candidate.path("onnx_model"),
            expected_bytes=conversion.get("onnx_bytes"),
            expected_sha256=conversion.get("onnx_sha256"),
            reasons=reasons,
            verify_assets=verify_assets,
        )
        self._check_asset(
            label="mixed-FP16 OM",
            path=candidate.om_path(_PRODUCTION_PRECISION),
            expected_bytes=conversion.get("mixed_fp16_bytes"),
            expected_sha256=conversion.get("mixed_fp16_sha256"),
            reasons=reasons,
            verify_assets=verify_assets,
        )
        return CandidateAdmission(candidate.id, True, not reasons, tuple(reasons))

    def manual_test_candidate_embedding_spec(self, candidate_id: str) -> ModelSpec:
        """Resolve a hash-verified candidate for the manual-test channel."""

        decision = self.manual_test_candidate_admission(candidate_id, verify_assets=True)
        if not decision.known:
            raise KeyError(f"Unknown model: {candidate_id}")
        if not decision.admitted:
            raise KeyError(
                f"Candidate {candidate_id} is not enabled for manual testing: "
                f"{decision.reason or 'asset or contract check failed'}"
            )
        spec = self._candidate_embedding_spec(candidate_id, production=True)
        raw = dict(spec.raw)
        raw["manual_test_candidate"] = True
        raw["manual_test_pending"] = True
        raw["production_enabled"] = False
        return ModelSpec(
            id=spec.id,
            display_name=spec.display_name,
            kind=spec.kind,
            license=spec.license,
            research_only=spec.research_only,
            source=spec.source,
            revision=spec.revision,
            input_shape=spec.input_shape,
            input_range=spec.input_range,
            feature_dim=spec.feature_dim,
            metric=spec.metric,
            raw=raw,
        )

    def _validate_admission_evidence(
        self,
        candidate: CandidateSpec,
        admission: Mapping[str, Any],
        checkpoint_artifact: Mapping[str, Any],
        conversion: Mapping[str, Any],
        reasons: list[str],
    ) -> None:
        """Validate audited, immutable evidence referenced by a formal gate."""

        def require(condition: bool, reason: str) -> None:
            if not condition:
                self._append_unique(reasons, reason)

        require(admission.get("schema_version") == 1, "production_admission.schema_version 必须为 1")
        require(admission.get("status") == "admitted", "production_admission.status 必须为 admitted")
        require(admission.get("precision") == _PRODUCTION_PRECISION, "生产精度必须为 mixed_fp16")
        require(
            admission.get("npu_model") == _PRODUCTION_NPU_MODEL,
            f"生产 NPU 必须为 {_PRODUCTION_NPU_MODEL}",
        )
        require(
            admission.get("compute_tier") == _PRODUCTION_COMPUTE_TIER,
            f"生产算力档必须为 {_PRODUCTION_COMPUTE_TIER}",
        )

        assets = self._mapping(admission.get("artifacts")) or {}
        expected_assets = {
            "checkpoint": (
                checkpoint_artifact.get("bytes"),
                checkpoint_artifact.get("sha256"),
            ),
            "onnx": (conversion.get("onnx_bytes"), conversion.get("onnx_sha256")),
            "mixed_fp16_om": (
                conversion.get("mixed_fp16_bytes"),
                conversion.get("mixed_fp16_sha256"),
            ),
        }
        for name, (expected_bytes, expected_sha256) in expected_assets.items():
            item = self._mapping(assets.get(name))
            require(item is not None, f"production_admission.artifacts.{name} 缺失")
            if item is not None:
                require(item.get("verified") is True, f"{name} 未标记 verified=true")
                require(item.get("bytes") == expected_bytes, f"{name} bytes 与资产登记不一致")
                require(item.get("sha256") == expected_sha256, f"{name} SHA-256 与资产登记不一致")
        mixed_evidence = self._mapping(assets.get("mixed_fp16_om")) or {}
        require(
            mixed_evidence.get("precision") == _PRODUCTION_PRECISION,
            "mixed_fp16_om.precision 必须为 mixed_fp16",
        )

        contract = self._mapping(admission.get("contract")) or {}
        require(contract.get("task_type") == "embedding", "生产契约 task_type 必须为 embedding")
        require(
            tuple(contract.get("input_shape", ())) == _OFFLINE_CANDIDATE_INPUT_SHAPE,
            "生产契约输入形状必须为 [1, 1, 128, 128]",
        )
        require(
            contract.get("input_range") == candidate.input_range,
            "生产契约 input_range 与候选不一致",
        )
        require(
            contract.get("feature_dim") == _OFFLINE_CANDIDATE_FEATURE_DIM,
            "生产契约 feature_dim 必须为 512",
        )
        require(contract.get("metric") == "cosine", "生产契约 metric 必须为 cosine")

        calibration = self._mapping(admission.get("calibration")) or {}
        require(bool(str(calibration.get("dataset", "")).strip()), "准入阈值缺少 calibration.dataset")
        require(bool(str(calibration.get("protocol", "")).strip()), "准入阈值缺少 calibration.protocol")
        require(calibration.get("metric") == "cosine", "准入阈值 metric 必须为 cosine")
        threshold = calibration.get("threshold")
        require(
            isinstance(threshold, (int, float))
            and not isinstance(threshold, bool)
            and math.isfinite(float(threshold))
            and 0.0 <= float(threshold) <= 1.0,
            "准入阈值 threshold 必须位于 [0,1]",
        )

        validation = self._mapping(admission.get("validation")) or {}
        numeric = self._mapping(validation.get("numeric_consistency")) or {}
        require(numeric.get("status") == "passed", "100 样本数值一致性未通过")
        require(
            isinstance(numeric.get("samples"), int)
            and not isinstance(numeric.get("samples"), bool)
            and numeric["samples"] >= 100,
            "数值一致性样本数必须不少于 100",
        )
        mean_cosine = numeric.get("mean_cosine")
        require(
            isinstance(mean_cosine, (int, float))
            and not isinstance(mean_cosine, bool)
            and math.isfinite(float(mean_cosine))
            and float(mean_cosine) >= _NUMERIC_MIN_MEAN_COSINE,
            "数值一致性平均余弦必须 >= 0.999",
        )
        min_cosine = numeric.get("min_cosine")
        require(
            isinstance(min_cosine, (int, float))
            and not isinstance(min_cosine, bool)
            and math.isfinite(float(min_cosine))
            and float(min_cosine) >= _NUMERIC_MIN_MIN_COSINE,
            "数值一致性最小余弦必须 >= 0.995",
        )

        tongji = self._mapping(validation.get("tongji")) or {}
        require(tongji.get("status") == "passed", "Tongji 全量评测未通过")
        require(tongji.get("return_code") == 0, "Tongji 全量评测返回码不是 0")
        require(tongji.get("backend") == "npu", "Tongji 评测必须使用 NPU")
        require(tongji.get("precision") == _PRODUCTION_PRECISION, "Tongji 评测必须使用 mixed_fp16")

        polyu = self._mapping(validation.get("polyu_b")) or {}
        runs = polyu.get("runs")
        require(polyu.get("status") == "passed", "PolyU-B 正式评测未通过")
        require(isinstance(runs, list) and len(runs) >= 2, "PolyU-B 需要两次独立全量运行")
        if isinstance(runs, list):
            for index, run in enumerate(runs[:2]):
                run_item = self._mapping(run) or {}
                require(
                    run_item.get("status") == "passed" and run_item.get("return_code") == 0,
                    f"PolyU-B 第 {index + 1} 次运行未 clean pass",
                )
                require(run_item.get("backend") == "npu", f"PolyU-B 第 {index + 1} 次未使用 NPU")
                require(
                    run_item.get("precision") == _PRODUCTION_PRECISION,
                    f"PolyU-B 第 {index + 1} 次未使用 mixed_fp16",
                )

        lifecycle = self._mapping(validation.get("lifecycle")) or {}
        require(lifecycle.get("status") == "passed", "ACL 生命周期验证未通过")
        require(
            isinstance(lifecycle.get("soak_cycles"), int)
            and not isinstance(lifecycle.get("soak_cycles"), bool)
            and lifecycle["soak_cycles"] >= 10,
            "ACL 生命周期 soak 必须至少 10 次",
        )
        require(lifecycle.get("clean_exit") is True, "ACL 生命周期未记录 clean_exit=true")
        require(lifecycle.get("resource_release") == "passed", "ACL 资源释放未通过")

        faults = self._mapping(validation.get("faults")) or {}
        require(faults.get("status") == "clear", "硬件/ACL 故障状态不是 clear")
        for key in ("rc_139", "aicore", "lpm", "ras", "device_reset", "resource_leak"):
            require(faults.get(key) is False, f"故障证据 {key} 未明确为 false")

    def production_candidate_embedding_spec(self, candidate_id: str) -> ModelSpec:
        """Resolve one candidate for live NPU use after all admission checks."""

        decision = self.candidate_admission(candidate_id, verify_assets=True)
        if not decision.known:
            raise KeyError(f"Unknown model: {candidate_id}")
        if not decision.admitted:
            reason = decision.reason or "未通过生产准入"
            raise KeyError(f"Candidate {candidate_id} is not production-admitted: {reason}")
        return self._candidate_embedding_spec(candidate_id, production=True)

    def offline_candidate_embedding_spec(self, candidate_id: str) -> ModelSpec:
        """Adapt one audited candidate for offline embedding evaluation only.

        This does not add the candidate to :meth:`all` or make it selectable
        by the production service.  The deliberately strict contract avoids
        sending detector, ROI, classifier, vein, multi-input, or differently
        shaped models through the palm embedding adapters by accident.
        """
        return self._candidate_embedding_spec(candidate_id, production=False)

    def offline_candidate_embedding_ids(self) -> list[str]:
        """Return candidates whose declared contract can use the offline adapter."""

        result: list[str] = []
        for candidate in self.candidates():
            try:
                self.offline_candidate_embedding_spec(candidate.id)
            except ValueError:
                continue
            result.append(candidate.id)
        return result
