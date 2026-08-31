"""Read and validate the non-runtime candidate model asset manifest.

The runtime registry intentionally contains only models that have completed the
project's deployment gate.  This module tracks additional public candidates
without downloading weights, loading frameworks, or changing API/UI behavior.
"""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
import math
from pathlib import Path
import re
from typing import Any, Mapping
from urllib.parse import urlparse

from palmprint_workbench.config import ROOT


CANDIDATE_MANIFEST_PATH = ROOT / "candidate_manifest.json"

_AVAILABILITY = {
    "active_project_asset",
    "local_verified",
    "official_link_unverified",
    "repository_asset_unverified",
    "not_available",
    "not_applicable",
    "opaque_sdk",
}
_COMPARISON_SCOPES = {
    "active",
    "conversion_candidate",
    "separate_modality",
    "audit_only",
}
_NPU_STATUSES = {
    "om_ready",
    "om_ready_admission_blocked",
    "conversion_pending",
    "needs_reimplementation",
    "not_applicable",
    "not_supported",
    "unknown",
}
_TASK_TYPES = {
    "embedding",
    "code",
    "roi",
    "detector",
    "segmentation",
    "classifier",
    "vein_embedding",
    "sdk",
}
_REQUIRED_KEYS = {
    "id",
    "display_name",
    "family",
    "modality",
    "task",
    "task_type",
    "comparison_scope",
    "source",
    "license",
    "weights",
    "npu_status",
}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_IMMUTABLE_REVISION_RE = re.compile(r"^[0-9a-f]{40,64}$")
_PRODUCTION_FAULT_KEYS = (
    "rc_139",
    "aicore",
    "lpm",
    "ras",
    "device_reset",
    "resource_leak",
)
_NUMERIC_MIN_MEAN_COSINE = 0.999
_NUMERIC_MIN_MIN_COSINE = 0.995


@dataclass(frozen=True)
class CandidateSpec:
    """One audited candidate that is not necessarily runnable at runtime."""

    id: str
    display_name: str
    family: str
    modality: str
    task: str
    comparison_scope: str
    source: Mapping[str, Any]
    license: Mapping[str, Any]
    weights: Mapping[str, Any]
    npu_status: str
    raw: Mapping[str, Any]

    @property
    def reproducible(self) -> bool:
        revision = self.source.get("revision")
        return isinstance(revision, str) and bool(
            _IMMUTABLE_REVISION_RE.fullmatch(revision.strip().lower())
        )

    @property
    def reproducibility_reason(self) -> str | None:
        return None if self.reproducible else "mutable source revision"

    @property
    def local_artifact_paths(self) -> tuple[Path, ...]:
        """Return declared local artifact paths without requiring they exist."""
        paths: list[Path] = []
        for artifact in self.weights.get("artifacts", []):
            relative_path = artifact.get("local_path")
            if relative_path:
                paths.append((ROOT / relative_path).resolve())
        return tuple(paths)


class CandidateManifest:
    """Validated candidate inventory separate from ``models/registry.json``."""

    def __init__(self, payload: Mapping[str, Any]) -> None:
        errors = validate_candidate_manifest_payload(payload)
        if errors:
            raise ValueError("Invalid candidate manifest:\n- " + "\n- ".join(errors))
        self._payload = dict(payload)
        self._candidates = {
            item["id"]: CandidateSpec(
                id=item["id"],
                display_name=item["display_name"],
                family=item["family"],
                modality=item["modality"],
                task=item["task"],
                comparison_scope=item["comparison_scope"],
                source=dict(item["source"]),
                license=dict(item["license"]),
                weights=dict(item["weights"]),
                npu_status=item["npu_status"],
                raw=dict(item),
            )
            for item in payload["candidates"]
        }

    @classmethod
    def load(cls, path: Path = CANDIDATE_MANIFEST_PATH) -> "CandidateManifest":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def all(self) -> list[CandidateSpec]:
        return list(self._candidates.values())

    def get(self, candidate_id: str) -> CandidateSpec:
        try:
            return self._candidates[candidate_id]
        except KeyError as exc:
            raise KeyError(f"Unknown candidate: {candidate_id}") from exc

    def by_scope(self, comparison_scope: str) -> list[CandidateSpec]:
        return [item for item in self.all() if item.comparison_scope == comparison_scope]


def _is_https_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme == "https" and bool(parsed.netloc)


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def _is_project_relative_path(value: Any, expected_prefix: str | None = None) -> bool:
    """Check metadata paths before a board-side tool consumes them.

    Candidate assets are intentionally ignored by Git, but the checked-in
    manifest still controls where exporter markers and conversion artifacts
    are expected.  Reject absolute paths and traversal in the manifest itself
    so a malformed candidate entry cannot redirect a later tool outside the
    palmprint project.
    """

    if not isinstance(value, str) or not value.strip():
        return False
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized):
        return False
    parts = normalized.split("/")
    return (
        (expected_prefix is None or normalized.startswith(expected_prefix))
        and all(part not in {"", ".", ".."} for part in parts)
    )


def _validate_production_admission(
    admission: Any,
    label: str,
) -> list[str]:
    """Validate the evidence shape required before a candidate is promotable.

    This only validates the manifest's structure. ``ModelRegistry`` compares
    the stated values against the candidate's asset metadata and, at runtime,
    the real checkpoint/ONNX/OM files.
    """

    errors: list[str] = []
    if not isinstance(admission, Mapping):
        return [f"{label}.production_admission must be an object"]
    if admission.get("schema_version") != 1:
        errors.append(f"{label}.production_admission.schema_version must be 1")
    if admission.get("status") != "admitted":
        errors.append(f"{label}.production_admission.status must be admitted")
    if admission.get("precision") != "mixed_fp16":
        errors.append(f"{label}.production_admission.precision must be mixed_fp16")
    if admission.get("npu_model") != "Ascend 310B4":
        errors.append(f"{label}.production_admission.npu_model must be Ascend 310B4")
    if admission.get("compute_tier") != "8T":
        errors.append(f"{label}.production_admission.compute_tier must be 8T")

    artifacts = admission.get("artifacts")
    if not isinstance(artifacts, Mapping):
        errors.append(f"{label}.production_admission.artifacts must be an object")
    else:
        for name in ("checkpoint", "onnx", "mixed_fp16_om"):
            artifact = artifacts.get(name)
            artifact_label = f"{label}.production_admission.artifacts.{name}"
            if not isinstance(artifact, Mapping):
                errors.append(f"{artifact_label} must be an object")
                continue
            if artifact.get("verified") is not True:
                errors.append(f"{artifact_label}.verified must be true")
            if not isinstance(artifact.get("bytes"), int) or isinstance(artifact.get("bytes"), bool) or artifact["bytes"] <= 0:
                errors.append(f"{artifact_label}.bytes must be a positive integer")
            if not _is_sha256(artifact.get("sha256")):
                errors.append(f"{artifact_label}.sha256 must be lowercase SHA-256")
        mixed = artifacts.get("mixed_fp16_om")
        if isinstance(mixed, Mapping) and mixed.get("precision") != "mixed_fp16":
            errors.append(f"{label}.production_admission.artifacts.mixed_fp16_om.precision must be mixed_fp16")

    contract = admission.get("contract")
    if not isinstance(contract, Mapping):
        errors.append(f"{label}.production_admission.contract must be an object")
    else:
        if contract.get("task_type") != "embedding":
            errors.append(f"{label}.production_admission.contract.task_type must be embedding")
        if contract.get("input_shape") != [1, 1, 128, 128]:
            errors.append(f"{label}.production_admission.contract.input_shape must be [1, 1, 128, 128]")
        if contract.get("input_range") not in {"zero_one", "nonzero_standardize"}:
            errors.append(f"{label}.production_admission.contract.input_range is unsupported")
        if contract.get("feature_dim") != 512:
            errors.append(f"{label}.production_admission.contract.feature_dim must be 512")
        if contract.get("metric") != "cosine":
            errors.append(f"{label}.production_admission.contract.metric must be cosine")

    calibration = admission.get("calibration")
    if not isinstance(calibration, Mapping):
        errors.append(f"{label}.production_admission.calibration must be an object")
    else:
        if not isinstance(calibration.get("dataset"), str) or not calibration["dataset"].strip():
            errors.append(f"{label}.production_admission.calibration.dataset must be non-empty")
        if not isinstance(calibration.get("protocol"), str) or not calibration["protocol"].strip():
            errors.append(f"{label}.production_admission.calibration.protocol must be non-empty")
        if calibration.get("metric") != "cosine":
            errors.append(f"{label}.production_admission.calibration.metric must be cosine")
        threshold = calibration.get("threshold")
        if (
            isinstance(threshold, bool)
            or not isinstance(threshold, (int, float))
            or not math.isfinite(float(threshold))
            or not 0.0 <= float(threshold) <= 1.0
        ):
            errors.append(f"{label}.production_admission.calibration.threshold must be between 0 and 1")

    validation = admission.get("validation")
    if not isinstance(validation, Mapping):
        errors.append(f"{label}.production_admission.validation must be an object")
        return errors
    numeric = validation.get("numeric_consistency")
    if (
        not isinstance(numeric, Mapping)
        or numeric.get("status") != "passed"
        or not isinstance(numeric.get("samples"), int)
        or isinstance(numeric.get("samples"), bool)
        or numeric["samples"] < 100
    ):
        errors.append(f"{label}.production_admission.validation.numeric_consistency requires passed >=100 samples")
    if (
        not isinstance(numeric, Mapping)
        or not isinstance(numeric.get("mean_cosine"), (int, float))
        or isinstance(numeric.get("mean_cosine"), bool)
        or not math.isfinite(float(numeric.get("mean_cosine")))
        or float(numeric.get("mean_cosine")) < _NUMERIC_MIN_MEAN_COSINE
    ):
        errors.append(f"{label}.production_admission.validation.numeric_consistency.mean_cosine must be >= 0.999")
    if (
        not isinstance(numeric, Mapping)
        or not isinstance(numeric.get("min_cosine"), (int, float))
        or isinstance(numeric.get("min_cosine"), bool)
        or not math.isfinite(float(numeric.get("min_cosine")))
        or float(numeric.get("min_cosine")) < _NUMERIC_MIN_MIN_COSINE
    ):
        errors.append(f"{label}.production_admission.validation.numeric_consistency.min_cosine must be >= 0.995")
    tongji = validation.get("tongji")
    if not isinstance(tongji, Mapping) or tongji.get("status") != "passed" or tongji.get("return_code") != 0 or tongji.get("backend") != "npu" or tongji.get("precision") != "mixed_fp16":
        errors.append(f"{label}.production_admission.validation.tongji requires a clean NPU mixed_fp16 pass")
    polyu = validation.get("polyu_b")
    runs = polyu.get("runs") if isinstance(polyu, Mapping) else None
    if not isinstance(polyu, Mapping) or polyu.get("status") != "passed" or not isinstance(runs, list) or len(runs) < 2:
        errors.append(f"{label}.production_admission.validation.polyu_b requires two independent clean runs")
    elif any(
        not isinstance(run, Mapping)
        or run.get("status") != "passed"
        or run.get("return_code") != 0
        or run.get("backend") != "npu"
        or run.get("precision") != "mixed_fp16"
        for run in runs[:2]
    ):
        errors.append(f"{label}.production_admission.validation.polyu_b runs must be clean NPU mixed_fp16 passes")
    lifecycle = validation.get("lifecycle")
    if not isinstance(lifecycle, Mapping) or lifecycle.get("status") != "passed" or not isinstance(lifecycle.get("soak_cycles"), int) or isinstance(lifecycle.get("soak_cycles"), bool) or lifecycle["soak_cycles"] < 10 or lifecycle.get("clean_exit") is not True or lifecycle.get("resource_release") != "passed":
        errors.append(f"{label}.production_admission.validation.lifecycle requires >=10 clean resource-release cycles")
    faults = validation.get("faults")
    if not isinstance(faults, Mapping) or faults.get("status") != "clear" or any(faults.get(key) is not False for key in _PRODUCTION_FAULT_KEYS):
        errors.append(f"{label}.production_admission.validation.faults must explicitly clear ACL and device faults")
    return errors


def validate_candidate_manifest_payload(payload: Mapping[str, Any]) -> list[str]:
    """Return deterministic schema errors without opening model or data files."""
    errors: list[str] = []
    if payload.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    candidates = payload.get("candidates")
    if not isinstance(candidates, list) or not candidates:
        return errors + ["candidates must be a non-empty list"]

    seen_ids: set[str] = set()
    for index, item in enumerate(candidates):
        label = f"candidates[{index}]"
        if not isinstance(item, Mapping):
            errors.append(f"{label} must be an object")
            continue
        missing = sorted(_REQUIRED_KEYS - item.keys())
        if missing:
            errors.append(f"{label} missing required keys: {', '.join(missing)}")
            continue
        candidate_id = item["id"]
        if not isinstance(candidate_id, str) or not candidate_id:
            errors.append(f"{label}.id must be a non-empty string")
        elif candidate_id in seen_ids:
            errors.append(f"duplicate candidate id: {candidate_id}")
        else:
            seen_ids.add(candidate_id)
        if item["comparison_scope"] not in _COMPARISON_SCOPES:
            errors.append(f"{label}.comparison_scope is not supported")
        if item["task_type"] not in _TASK_TYPES:
            errors.append(f"{label}.task_type is not supported")
        if "production_enabled" in item and not isinstance(item["production_enabled"], bool):
            errors.append(f"{label}.production_enabled must be a boolean")
        admission = item.get("production_admission")
        if admission is not None and not isinstance(admission, Mapping):
            errors.append(f"{label}.production_admission must be an object")
        if item.get("production_enabled") is True and not isinstance(admission, Mapping):
            errors.append(
                f"{label}.production_enabled=true requires production_admission evidence"
            )
        if item["npu_status"] not in _NPU_STATUSES:
            errors.append(f"{label}.npu_status is not supported")
        source = item["source"]
        if not isinstance(source, Mapping) or not _is_https_url(source.get("url")):
            errors.append(f"{label}.source.url must be an https URL")
        elif not isinstance(source.get("revision"), str) or not source["revision"].strip():
            errors.append(f"{label}.source.revision must be a non-empty string")
        elif item.get("production_enabled") is True and not _IMMUTABLE_REVISION_RE.fullmatch(
            source["revision"].strip().lower()
        ):
            errors.append(
                f"{label}.source.revision must be an immutable commit hash when production_enabled"
            )
        license_info = item["license"]
        if not isinstance(license_info, Mapping) or not isinstance(license_info.get("spdx"), str):
            errors.append(f"{label}.license.spdx must be a string")
        elif not isinstance(license_info.get("usage"), str) or not license_info["usage"].strip():
            errors.append(f"{label}.license.usage must be a non-empty string")
        weights = item["weights"]
        if not isinstance(weights, Mapping) or weights.get("availability") not in _AVAILABILITY:
            errors.append(f"{label}.weights.availability is not supported")
            continue
        artifacts = weights.get("artifacts", [])
        if not isinstance(artifacts, list):
            errors.append(f"{label}.weights.artifacts must be a list")
            continue
        for artifact_index, artifact in enumerate(artifacts):
            artifact_label = f"{label}.weights.artifacts[{artifact_index}]"
            if not isinstance(artifact, Mapping):
                errors.append(f"{artifact_label} must be an object")
                continue
            if artifact.get("url") and not _is_https_url(artifact["url"]):
                errors.append(f"{artifact_label}.url must be an https URL")
            if artifact.get("local_path") and not _is_project_relative_path(artifact["local_path"]):
                errors.append(f"{artifact_label}.local_path must be a safe project-relative path")
            sha256 = artifact.get("sha256")
            if sha256 is not None and not _is_sha256(sha256):
                errors.append(f"{artifact_label}.sha256 must be lowercase SHA-256")
            size = artifact.get("bytes")
            if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size <= 0):
                errors.append(f"{artifact_label}.bytes must be a positive integer")
        if weights.get("availability") == "local_verified":
            if not any(
                isinstance(artifact, Mapping)
                and artifact.get("local_path")
                and artifact.get("sha256")
                and artifact.get("bytes")
                for artifact in artifacts
            ):
                errors.append(f"{label}.weights local_verified requires local_path, bytes and sha256")
        conversion = item.get("conversion")
        if conversion is not None:
            if not isinstance(conversion, Mapping):
                errors.append(f"{label}.conversion must be an object")
                continue
            for path_key, prefix in (
                ("onnx_path", "models/onnx/"),
                ("marker_path", "models/checkpoints/"),
            ):
                if conversion.get(path_key) is not None and not _is_project_relative_path(
                    conversion[path_key], prefix
                ):
                    errors.append(f"{label}.conversion.{path_key} must be under {prefix}")
            onnx_sha = conversion.get("onnx_sha256")
            if onnx_sha is not None and not _is_sha256(onnx_sha):
                errors.append(f"{label}.conversion.onnx_sha256 must be lowercase SHA-256")
            onnx_bytes = conversion.get("onnx_bytes")
            if onnx_bytes is not None and (isinstance(onnx_bytes, bool) or not isinstance(onnx_bytes, int) or onnx_bytes <= 0):
                errors.append(f"{label}.conversion.onnx_bytes must be a positive integer")
            om_paths = conversion.get("om_paths", {})
            if not isinstance(om_paths, Mapping):
                errors.append(f"{label}.conversion.om_paths must be an object")
            else:
                for precision, path in om_paths.items():
                    if precision not in {"origin", "mixed_fp16"}:
                        errors.append(f"{label}.conversion.om_paths has unsupported precision")
                    if not _is_project_relative_path(path, "models/om/"):
                        errors.append(f"{label}.conversion.om_paths.{precision} must be under models/om/")
        if item.get("production_enabled") is True:
            if str(item.get("modality", "")).strip().lower() != "palmprint":
                errors.append(f"{label}.modality must be palmprint when production_enabled")
            declared_backends = item.get("available_backends")
            if declared_backends is not None and (
                not isinstance(declared_backends, (list, tuple))
                or "npu" not in declared_backends
            ):
                errors.append(f"{label}.available_backends must include npu when production_enabled")
            errors.extend(_validate_production_admission(item.get("production_admission"), label))
            if item.get("npu_status") != "om_ready":
                errors.append(f"{label}.npu_status must be om_ready when production_enabled")
            if not isinstance(conversion, Mapping):
                errors.append(f"{label}.conversion is required when production_enabled")
            else:
                if conversion.get("om_status") != "mixed_fp16_ready":
                    errors.append(f"{label}.conversion.om_status must be mixed_fp16_ready when production_enabled")
                if conversion.get("board_npu_status") != "om_ready":
                    errors.append(f"{label}.conversion.board_npu_status must be om_ready when production_enabled")
    return errors


def validate_candidate_manifest(path: Path = CANDIDATE_MANIFEST_PATH) -> list[str]:
    """Validate the checked-in manifest without downloading or importing models."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [str(exc)]
    return validate_candidate_manifest_payload(payload)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate palmprint candidate asset metadata.")
    # ``validate`` and ``--strict`` are accepted for the documented module
    # command; validation is always strict and never downloads assets.
    parser.add_argument("command", nargs="?", choices=("validate",), default="validate")
    parser.add_argument("--manifest", type=Path, default=CANDIDATE_MANIFEST_PATH)
    parser.add_argument("--strict", action="store_true", help="fail on any manifest error")
    arguments = parser.parse_args(argv)
    errors = validate_candidate_manifest(arguments.manifest)
    if errors:
        print("Candidate manifest validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    manifest = CandidateManifest.load(arguments.manifest)
    print(f"Candidate manifest valid: {len(manifest.all())} candidates")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
