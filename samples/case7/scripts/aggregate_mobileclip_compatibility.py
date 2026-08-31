#!/usr/bin/env python3
"""Aggregate an isolated MobileCLIP 8T/20T compatibility campaign.

The campaign runner writes evidence on the Ascend boards.  This utility is a
controller-side, ACL-free reader: it validates the evidence it can validate,
normalizes the eight expected matrix cells, and writes one JSON report.  It
never edits a production registry, model, database, or service directory.

Without ``--strict`` a partial campaign is rendered with explicit ``not_run``
placeholders, so an interrupted run remains auditable.  ``--strict`` is the
final gate and returns non-zero unless all required evidence passes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SCHEMA_VERSION = 1
MODEL_ID = "mobileclip_s0__npu__mixed_fp16"
NUMERICAL_THRESHOLD = 0.995
EXPECTED_COMPONENTS = ("image", "text")
EXPECTED_SAMPLE_COUNTS = {"image": 36, "text": 20}
EXPECTED_ROLES = ("8t-310b4", "20t-310b1")
EXPECTED_SOC_BY_ROLE = {
    "8t-310b4": "Ascend310B4",
    "20t-310b1": "Ascend310B1",
}
EXPECTED_CELLS = (
    ("8t-310b4", "8t-310b4"),
    ("8t-310b4", "20t-310b1"),
    ("20t-310b1", "8t-310b4"),
    ("20t-310b1", "20t-310b1"),
)
ALLOWED_STATUS = {
    "passed",
    "load_rejected",
    "execute_failed",
    "output_contract_mismatch",
    "numerical_mismatch",
    "non_finite",
}
# Aggregate-only placeholder.  It is intentionally not a permitted validator
# status; missing cells cannot be mistaken for a completed test.
MISSING_STATUS = "not_run"


class AggregateError(RuntimeError):
    """Raised when campaign evidence is unreadable or structurally unsafe."""


def _load(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise AggregateError(f"cannot read JSON {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise AggregateError(f"JSON root must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _iter_json(root: Path, names: Optional[Iterable[str]] = None):
    """Yield JSON files below *root* without following escaping symlinks."""
    if not root.is_dir():
        return
    wanted = set(names) if names is not None else None
    root_resolved = root.resolve()
    for path in root.rglob("*.json"):
        try:
            path.resolve().relative_to(root_resolved)
        except ValueError:
            continue
        if wanted is None or path.name in wanted:
            yield path


def _assert_isolated_root(root: Path) -> None:
    """Reject obvious production directories before any output is written."""
    resolved = root.resolve()
    if not resolved.is_dir():
        raise AggregateError(f"campaign root is not a directory: {resolved}")
    # Temporary test directories are valid.  Exact production names are not.
    if resolved.name in {"ai-album", "current", "models", "photos", "data", "reports"}:
        raise AggregateError(f"refusing production-like campaign root: {resolved}")
    repository_root = Path(__file__).resolve().parents[1]
    if resolved == repository_root:
        raise AggregateError(f"refusing repository root as campaign root: {resolved}")


def _assert_inside(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise AggregateError(f"{label} escapes campaign root: {path}") from exc
    return resolved


def _production_mutation(value: Mapping[str, Any]) -> bool:
    """Interpret both boolean and structured mutation declarations."""
    raw = value.get("production_mutation", False)
    if isinstance(raw, Mapping):
        return any(bool(item) for item in raw.values())
    return bool(raw)


def _role_from_soc(value: Mapping[str, Any]) -> Optional[str]:
    """Infer the project role from an explicitly reported SoC only.

    This is a convenience for the board runner's compact environment JSON;
    it never infers a firmware version or a compiler target from a hostname.
    """
    text = " ".join(
        str(value.get(key, ""))
        for key in ("soc_detected", "soc_version_requested", "soc_version", "npu_model")
    ).lower()
    if "310b4" in text:
        return "8t-310b4"
    if "310b1" in text:
        return "20t-310b1"
    return None


def _role_from_detected_soc(value: Mapping[str, Any]) -> Optional[str]:
    """Map only observed hardware fields to a runtime role.

    ``soc_version_requested`` describes an ATC target and may intentionally
    differ from the board running a cross-board validation.  It therefore
    must never be used to identify the runtime environment.
    """
    observed = " ".join(
        str(value.get(key, "")) for key in ("soc_detected", "npu_model")
    )
    npu_smi = value.get("npu_smi")
    if isinstance(npu_smi, Mapping):
        observed += " " + str(npu_smi.get("model", ""))
    text = observed.lower().replace(" ", "")
    if "310b4" in text:
        return "8t-310b4"
    if "310b1" in text:
        return "20t-310b1"
    return None


def _environment_records(root: Path) -> Tuple[Dict[str, Mapping[str, Any]], List[str]]:
    records: Dict[str, Mapping[str, Any]] = {}
    errors: List[str] = []
    for path in _iter_json(root / "environment", ("environment.json",)):
        value = _load(path)
        role_value = value.get("role") or value.get("runtime_role") or value.get("runtime_label")
        role = str(role_value or "").strip()
        if not role or role == "environment":
            role = _role_from_soc(value) or ""
        if not role:
            # A nested board directory named after a role is an acceptable
            # fallback; a generic ``environment`` name is deliberately not.
            for parent in path.parents:
                if parent.name in EXPECTED_ROLES:
                    role = parent.name
                    break
        if not role:
            errors.append(f"environment role is empty in {_relative(path, root)}")
            continue
        if role not in EXPECTED_ROLES:
            errors.append(
                f"unexpected environment role {role!r} in {_relative(path, root)}"
            )
        if role in records:
            raise AggregateError(f"duplicate environment role {role!r}")
        if _production_mutation(value):
            errors.append(f"production_mutation=true in environment {role}")
        if value.get("schema_version") not in (None, SCHEMA_VERSION):
            errors.append(
                f"unsupported environment schema_version={value.get('schema_version')} "
                f"in {_relative(path, root)}"
            )
        detected_role = _role_from_detected_soc(value)
        if detected_role is None:
            errors.append(
                f"environment {role} has no recognized soc_detected/npu_model"
            )
        elif detected_role != role:
            errors.append(
                f"environment role {role} conflicts with observed SoC "
                f"{EXPECTED_SOC_BY_ROLE.get(detected_role, detected_role)}"
            )
        runtime_role = str(value.get("runtime_role") or "").strip()
        if runtime_role and runtime_role != role:
            errors.append(
                f"environment role {role} conflicts with runtime_role {runtime_role}"
            )
        npu_smi = value.get("npu_smi")
        if not isinstance(npu_smi, Mapping) or not str(
            npu_smi.get("software_version") or ""
        ).strip():
            errors.append(f"environment {role} is missing npu-smi software version")
        firmware_present = "firmware_version_raw" in value or (
            isinstance(npu_smi, Mapping) and "firmware_version_raw" in npu_smi
        )
        if not firmware_present:
            errors.append(f"environment {role} is missing raw firmware field")
        cann = value.get("cann")
        cann_versions = cann.get("versions_detected") if isinstance(cann, Mapping) else None
        cann_files = cann.get("version_files") if isinstance(cann, Mapping) else None
        if not cann_versions and not cann_files:
            errors.append(f"environment {role} is missing CANN version evidence")
        if not str(value.get("driver_version_info") or "").strip():
            errors.append(f"environment {role} is missing driver version evidence")
        records[role] = {
            **value,
            "role": role,
            "evidence_path": _relative(path, root),
            "production_mutation": False,
        }
    return records, errors


def _normalize_status(value: Mapping[str, Any], path: Path) -> Tuple[str, Optional[str]]:
    """Normalize current and older validator status fields.

    Older wrappers used ``acl_status`` or only ``passed``.  Those forms are
    accepted for interoperability, but an unknown explicit status is rejected.
    """
    raw = value.get("status")
    # The board harness uses status=failed plus a precise classification.  Use
    # that classification before considering the generic status value.
    classification = value.get("failure_class") or value.get("classification")
    if classification in ALLOWED_STATUS and str(raw).lower() in {
        "failed",
        "error",
        "not_run",
        "",
        "none",
    }:
        return str(classification), "inferred from failure_class/classification"
    if raw is None:
        raw = value.get("acl_status")
    if raw is None:
        failure = classification
        if failure in ALLOWED_STATUS:
            return str(failure), "inferred from failure_class/classification"
        if value.get("passed") is True:
            return "passed", "inferred from passed=true"
        if value.get("passed") is False:
            return "execute_failed", "inferred from passed=false"
        return MISSING_STATUS, None
    status = str(raw).strip().lower()
    aliases = {"ok": "passed", "success": "passed", "pass": "passed"}
    status = aliases.get(status, status)
    if status not in ALLOWED_STATUS:
        raise AggregateError(f"unsupported status {raw!r} in {path}")
    return status, None


def _cell_contract_errors(
    value: Mapping[str, Any], component: str, status: Optional[str] = None
) -> List[str]:
    """Validate the numerical evidence needed for a passing matrix cell.

    A wrapper that merely says ``status=passed`` is not sufficient evidence:
    the fixed fixture count, every sample's output contract, and its cosine
    threshold must be present.  Failed cells remain useful diagnostics and are
    only checked for fields they actually provide.
    """
    errors: List[str] = []
    expected = EXPECTED_SAMPLE_COUNTS[component]
    for field in ("sample_count", "fixture_count", "fixture_expected"):
        if field not in value:
            continue
        try:
            count = int(value[field])
        except (TypeError, ValueError):
            errors.append(f"{field} is not an integer")
            continue
        if count != expected:
            errors.append(f"{field}={count}, expected {expected} for {component}")
    if "passed_count" in value:
        try:
            passed_count = int(value["passed_count"])
        except (TypeError, ValueError):
            errors.append("passed_count is not an integer")
        else:
            if passed_count < 0 or passed_count > expected:
                errors.append(f"passed_count={passed_count} outside 0..{expected}")
    for field in ("min_cosine", "max_cosine"):
        if field not in value:
            continue
        try:
            number = float(value[field])
        except (TypeError, ValueError):
            errors.append(f"{field} is not numeric")
            continue
        if not math.isfinite(number):
            errors.append(f"{field} is not finite")
    if "threshold" in value:
        try:
            threshold = float(value["threshold"])
        except (TypeError, ValueError):
            errors.append("threshold is not numeric")
        else:
            if threshold < NUMERICAL_THRESHOLD:
                errors.append(
                    f"threshold={threshold} is below required {NUMERICAL_THRESHOLD}"
                )
    effective_status = status or str(value.get("status", "")).strip().lower()
    if effective_status == "passed":
        required_counts = {
            "sample_count": expected,
            "passed_count": expected,
            "fixture_expected": expected,
        }
        for field, required in required_counts.items():
            if field not in value:
                errors.append(f"missing {field} for passed {component} cell")
                continue
            try:
                actual = int(value[field])
            except (TypeError, ValueError):
                continue
            if actual != required:
                # The generic loop above reports the same mismatch for fields
                # that are present; this branch keeps the required-field rule
                # explicit without duplicating another message.
                continue
        for field in ("min_cosine", "max_cosine"):
            if field not in value:
                errors.append(f"missing {field} for passed {component} cell")
        try:
            minimum = float(value["min_cosine"])
            maximum = float(value["max_cosine"])
            if not math.isfinite(minimum) or not math.isfinite(maximum):
                raise ValueError
            if minimum < NUMERICAL_THRESHOLD or maximum < NUMERICAL_THRESHOLD:
                errors.append(
                    f"cosine range {minimum}..{maximum} is below required "
                    f"{NUMERICAL_THRESHOLD}"
                )
            if maximum < minimum:
                errors.append(f"cosine range is reversed: {minimum}..{maximum}")
        except (KeyError, TypeError, ValueError):
            # Missing/non-numeric values are reported by the required field
            # checks above or the generic finite-value checks.
            pass
        references = value.get("references")
        if not isinstance(references, list):
            errors.append(f"missing references for passed {component} cell")
        else:
            if len(references) != expected:
                errors.append(
                    f"references={len(references)}, expected {expected} for {component}"
                )
            expected_shape = (
                [1, 3, 256, 256] if component == "image" else [1, 77]
            )
            for index, sample in enumerate(references, start=1):
                if not isinstance(sample, Mapping):
                    errors.append(f"reference {index} is not an object")
                    continue
                if sample.get("passed") is not True:
                    errors.append(f"reference {index} is not marked passed")
                if sample.get("finite") is not True:
                    errors.append(f"reference {index} is not finite")
                if sample.get("output_dim") != 512:
                    errors.append(
                        f"reference {index} output_dim={sample.get('output_dim')}, expected 512"
                    )
                shape = sample.get("input_shape")
                if shape is not None and list(shape) != expected_shape:
                    errors.append(
                        f"reference {index} input_shape={shape}, expected {expected_shape}"
                    )
                try:
                    cosine = float(sample["cosine_similarity"])
                except (KeyError, TypeError, ValueError):
                    errors.append(f"reference {index} has no numeric cosine_similarity")
                else:
                    if not math.isfinite(cosine):
                        errors.append(f"reference {index} cosine_similarity is not finite")
                    elif cosine < NUMERICAL_THRESHOLD:
                        errors.append(
                            f"reference {index} cosine={cosine} is below "
                            f"{NUMERICAL_THRESHOLD}"
                        )
    return errors


def _load_nested_report(root: Path, value: Mapping[str, Any]) -> Mapping[str, Any]:
    """Load a validator report referenced by a stage wrapper, when local."""
    report = value.get("report")
    raw_path: Any = report.get("path") if isinstance(report, Mapping) else report
    if not raw_path:
        return {}
    candidate = Path(str(raw_path))
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        candidate = _assert_inside(candidate, root, "validator report")
    except AggregateError:
        # Board reports may retain an absolute board path after synchronization;
        # the wrapper itself remains useful even when the nested file is absent.
        return {}
    if not candidate.is_file():
        return {}
    try:
        return _load(candidate)
    except AggregateError:
        return {}


def _infer_stage_roles(root: Path, value: Mapping[str, Any], source_path: Path) -> Tuple[str, str]:
    compiler = str(
        value.get("compiler_role")
        or value.get("compiler_board")
        or value.get("artifact_label")
        or ""
    ).strip()
    runtime = str(
        value.get("runtime_role")
        or value.get("runtime_board")
        or value.get("runtime_label")
        or ""
    ).strip()
    # Native stage records may only carry the requested SoC.  Infer both roles
    # from that explicit field; cross-board runs must provide labels.
    inferred = _role_from_soc(value)
    if not inferred:
        for parent in source_path.parents:
            if parent.name in EXPECTED_ROLES:
                inferred = parent.name
                break
    if not inferred:
        summary_path: Optional[Path] = None
        for parent in (source_path.parent, *source_path.parents):
            candidate = parent / "summary.json"
            if candidate.is_file():
                summary_path = candidate
                break
        if summary_path is None:
            candidate = root / "summary.json"
            if candidate.is_file():
                summary_path = candidate
        if summary_path is not None:
            try:
                summary = _load(summary_path)
                nested_environment = summary.get("environment", {})
                inferred = _role_from_soc(summary)
                if not inferred and isinstance(nested_environment, Mapping):
                    inferred = _role_from_soc(nested_environment)
            except AggregateError:
                inferred = None
    if not compiler and inferred and str(value.get("stage_kind", "")) == "validation":
        # A validation run with no artifact label is only unambiguous when its
        # OM was compiled for the same requested SoC (native validation).
        compiler = inferred if value.get("mode", "native") == "native" else ""
    if not runtime:
        runtime = str(value.get("runtime_label") or "").strip() or inferred or ""
    if not compiler and runtime and value.get("mode", "native") == "native":
        compiler = runtime
    return compiler, runtime


def _candidate_cell_paths(root: Path):
    """Yield explicit cell reports, then board-runner validation stages."""
    yielded: set[Path] = set()
    for path in _iter_json(root / "cells"):
        yielded.add(path.resolve())
        yield path
    for path in _iter_json(root / "stages"):
        if path.name.startswith("validate-"):
            resolved = path.resolve()
            if resolved not in yielded:
                yield path


def _find_cell_reports(
    root: Path,
) -> Tuple[Dict[Tuple[str, str, str], Mapping[str, Any]], List[str]]:
    result: Dict[Tuple[str, str, str], Mapping[str, Any]] = {}
    errors: List[str] = []
    # Scan explicit matrix cells and the compact validation stage format used
    # by the board harness.
    for path in _candidate_cell_paths(root):
        value = _load(path)
        component = str(value.get("component", "")).strip().lower()
        compiler, runtime = _infer_stage_roles(root, value, path)
        if path.parent.name == "cells":
            compiler = str(
                value.get("compiler_role")
                or value.get("compiler_board")
                or value.get("compiler")
                or compiler
            ).strip()
            runtime = str(
                value.get("runtime_role")
                or value.get("runtime_board")
                or value.get("runtime")
                or runtime
            ).strip()
        # Ignore unrelated JSON (for example a copied fixture manifest).
        if not component and not compiler and not runtime:
            continue
        if component not in EXPECTED_COMPONENTS or not compiler or not runtime:
            errors.append(f"malformed matrix cell metadata in {_relative(path, root)}")
            continue
        if compiler not in EXPECTED_ROLES or runtime not in EXPECTED_ROLES:
            errors.append(
                f"unexpected matrix roles {compiler!r}->{runtime!r} "
                f"in {_relative(path, root)}"
            )
        key = (component, compiler, runtime)
        if key in result:
            # The board runner keeps a human-readable stage beside a stable
            # cells/<component>/.../result.json record.  Prefer the latter;
            # two explicit cell records remain an error.
            try:
                path.resolve().relative_to((root / "stages").resolve())
            except ValueError:
                raise AggregateError(f"duplicate matrix cell {key}")
            else:
                continue
        nested = _load_nested_report(root, value)
        # Stage wrappers intentionally contain only hashes and process status;
        # merge numerical summary fields from the referenced ACL report.
        enriched = dict(nested)
        enriched.update(value)
        status, note = _normalize_status(enriched, path)
        contract_errors = _cell_contract_errors(enriched, component, status)
        if enriched.get("schema_version") not in (None, SCHEMA_VERSION):
            contract_errors.append(
                f"unsupported schema_version={enriched.get('schema_version')}"
            )
        if enriched.get("model_id") not in (None, MODEL_ID):
            contract_errors.append(f"unexpected model_id={enriched.get('model_id')!r}")
        if _production_mutation(value):
            contract_errors.append("production_mutation=true")
        normalized = {
            **enriched,
            "component": component,
            "compiler_role": compiler,
            "runtime_role": runtime,
            "status": status,
            "production_mutation": False,
            "evidence_path": _relative(path, root),
        }
        if note:
            normalized["status_normalization"] = note
        if contract_errors:
            normalized["evidence_errors"] = contract_errors
            errors.extend(
                f"{_relative(path, root)}: {message}" for message in contract_errors
            )
        result[key] = normalized
    return result, errors


def _conversion_records(root: Path) -> Tuple[List[Mapping[str, Any]], List[str]]:
    records: List[Mapping[str, Any]] = []
    errors: List[str] = []
    seen: set[Tuple[str, str]] = set()
    paths: List[Path] = []
    paths.extend(
        path
        for path in _iter_json(root / "artifacts")
        if path.name in {"conversion_result.json", "atc_conversion.json"}
    )
    paths.extend(
        path
        for path in _iter_json(root / "native" / "reports")
        if path.name in {"conversion_result.json", "atc_conversion.json"}
    )
    # ``run_mobileclip_cross_board_campaign.sh`` records conversion stages in
    # stages/native-convert-<component>.json rather than duplicating them under
    # artifacts.  Accept both layouts.
    paths.extend(
        path
        for path in _iter_json(root / "stages")
        if path.name.startswith("native-convert-") and path.suffix == ".json"
    )
    for path in paths:
        value = _load(path)
        component = str(value.get("component", "")).strip().lower()
        role = str(
            value.get("compiler_role")
            or value.get("compiler_board")
            or value.get("role")
            or value.get("artifact_label")
            or ""
        ).strip()
        if not role:
            role = _role_from_soc(value) or ""
            if not role:
                # Native conversion reports commonly sit below a directory
                # named after the target role.
                for parent in path.parents:
                    if parent.name in EXPECTED_ROLES:
                        role = parent.name
                        break
            if not role:
                # Fall back to the nearest summary's explicit target SoC.  This
                # supports a single-board native campaign before it is merged
                # into the two-board matrix.
                for parent in (path.parent, *path.parents):
                    summary_path = parent / "summary.json"
                    if not summary_path.is_file():
                        continue
                    try:
                        role = _role_from_soc(_load(summary_path)) or ""
                    except AggregateError:
                        role = ""
                    if role:
                        break
        if component not in EXPECTED_COMPONENTS and not role:
            continue
        if component not in EXPECTED_COMPONENTS:
            errors.append(f"unknown conversion component in {_relative(path, root)}")
        if not role:
            errors.append(f"missing compiler role in {_relative(path, root)}")
        key = (component, role)
        if key in seen:
            try:
                path.resolve().relative_to((root / "stages").resolve())
            except ValueError:
                raise AggregateError(f"duplicate conversion evidence {key}")
            else:
                # The stage is duplicated by native/reports/<component>/
                # conversion_result.json; retain the stable report copy.
                continue
        seen.add(key)
        if _production_mutation(value):
            errors.append(f"production_mutation=true in conversion {_relative(path, root)}")
        if value.get("schema_version") not in (None, SCHEMA_VERSION):
            errors.append(
                f"unsupported conversion schema_version={value.get('schema_version')} "
                f"in {_relative(path, root)}"
            )
        records.append(
            {
                **value,
                "component": component,
                "compiler_role": role,
                "evidence_path": _relative(path, root),
                "production_mutation": False,
            }
        )
    return records, errors


def _artifact_manifest(root: Path) -> Optional[Mapping[str, Any]]:
    candidates = [root / "artifact_manifest.json", root / "reports" / "artifact_manifest.json"]
    candidates.extend(_iter_json(root / "reports", ("artifact_manifest.json",)))
    seen: set[Path] = set()
    for path in candidates:
        resolved = path.resolve()
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        value = _load(path)
        return {**value, "evidence_path": _relative(path, root)}
    return None


def _manifest_entries(manifest: Mapping[str, Any]) -> Optional[List[Mapping[str, Any]]]:
    entries: Any = manifest.get("artifacts", manifest.get("files"))
    if isinstance(entries, list):
        return [item for item in entries if isinstance(item, Mapping)]
    if isinstance(entries, Mapping):
        normalized: List[Mapping[str, Any]] = []
        for key, item in entries.items():
            if isinstance(item, Mapping):
                normalized.append({"path": key, **item})
        return normalized
    return None


def _check_artifact_hashes(root: Path, manifest: Optional[Mapping[str, Any]]) -> List[str]:
    errors: List[str] = []
    if not manifest:
        return errors
    entries = _manifest_entries(manifest)
    if entries is None:
        return ["artifact manifest has no list/map under artifacts/files"]
    if _production_mutation(manifest):
        errors.append("production_mutation=true in artifact manifest")
    if manifest.get("schema_version") not in (None, SCHEMA_VERSION):
        errors.append(
            f"unsupported artifact manifest schema_version={manifest.get('schema_version')}"
        )
    seen_paths: set[str] = set()
    for item in entries:
        raw_path = item.get("path")
        expected = str(item.get("sha256", "")).lower()
        if (
            not raw_path
            or len(expected) != 64
            or any(ch not in "0123456789abcdef" for ch in expected)
        ):
            errors.append(f"invalid artifact entry: {item}")
            continue
        normalized_raw_path = str(raw_path).replace("\\", "/")
        if normalized_raw_path in seen_paths:
            errors.append(f"duplicate artifact entry: {raw_path}")
        seen_paths.add(normalized_raw_path)
        candidate = Path(str(raw_path))
        try:
            path = _assert_inside(
                candidate if candidate.is_absolute() else root / candidate,
                root,
                "artifact path",
            )
        except AggregateError as exc:
            errors.append(str(exc))
            continue
        if not path.is_file():
            errors.append(f"artifact is missing: {raw_path}")
            continue
        actual = _sha256(path)
        if actual.lower() != expected:
            errors.append(f"artifact hash mismatch for {raw_path}: {actual} != {expected}")
        if "size_bytes" in item:
            try:
                expected_size = int(item["size_bytes"])
            except (TypeError, ValueError):
                errors.append(f"invalid size_bytes for {raw_path}")
            else:
                if path.stat().st_size != expected_size:
                    errors.append(
                        f"artifact size mismatch for {raw_path}: "
                        f"{path.stat().st_size} != {expected_size}"
                    )
    return errors


def _local_conversion_log(
    root: Path, item: Mapping[str, Any], component: str, role: str
) -> Optional[Path]:
    """Resolve a copied ATC log without following an absolute board path."""
    candidates: List[Path] = []
    evidence = item.get("evidence_path")
    if evidence:
        evidence_path = Path(str(evidence))
        if not evidence_path.is_absolute():
            candidates.append(root / evidence_path.with_suffix(".log"))
    candidates.append(root / "stages" / role / f"native-convert-{component}.log")
    for candidate in candidates:
        try:
            resolved = _assert_inside(candidate, root, "conversion log")
        except AggregateError:
            continue
        if resolved.is_file():
            return resolved
    return None


def _manifest_entry_for(
    entries: Sequence[Mapping[str, Any]], suffix: str
) -> Optional[Mapping[str, Any]]:
    normalized_suffix = suffix.replace("\\", "/")
    matches = [
        item
        for item in entries
        if str(item.get("path", "")).replace("\\", "/").endswith(normalized_suffix)
    ]
    return matches[0] if len(matches) == 1 else None


def _conversion_gate_errors(
    root: Path,
    conversions: Sequence[Mapping[str, Any]],
    manifest: Optional[Mapping[str, Any]],
) -> List[str]:
    """Require complete 8T ATC evidence, not just a zero exit code.

    A successful process exit is insufficient evidence: the report must bind
    the fixed ONNX, target OM, ATC flags, and serial/no-cache policy together.
    """
    errors: List[str] = []
    entries = _manifest_entries(manifest) if manifest else None
    for component in EXPECTED_COMPONENTS:
        matches = [
            item
            for item in conversions
            if item.get("component") == component
            and item.get("compiler_role") == "8t-310b4"
        ]
        if not matches:
            errors.append(f"missing 8t-310b4 conversion evidence for {component}")
            continue
        item = matches[0]
        status = str(item.get("status", "")).lower()
        classification = str(item.get("classification", "")).lower()
        atc_status = str(item.get("atc_status", "")).lower()
        if (
            status in {"failed", "error", "execute_failed", "conversion_failed"}
            or classification in {"failed", "error", "conversion_failed"}
            or atc_status in {"failed", "error", "conversion_failed"}
            or item.get("return_code") not in (None, 0)
            or item.get("exit_code") not in (None, 0)
        ):
            errors.append(f"8t conversion did not pass for {component}")
            continue

        if entries is None:
            errors.append(f"artifact manifest is unavailable for {component} conversion")
            continue

        onnx_entry = _manifest_entry_for(
            entries, f"artifacts/onnx/mobileclip_s0_{component}.onnx"
        )
        if onnx_entry is None:
            errors.append(f"missing manifest ONNX entry for {component}")
        else:
            actual_onnx = str(onnx_entry.get("sha256", "")).lower()
            if len(actual_onnx) != 64 or any(
                ch not in "0123456789abcdef" for ch in actual_onnx
            ):
                errors.append(f"invalid {component} ONNX SHA-256 in artifact manifest")
            declared_onnx = str(item.get("onnx_sha256", "")).lower()
            if declared_onnx and declared_onnx != actual_onnx:
                errors.append(f"ONNX hash does not match artifact manifest for {component}")
            if not isinstance(onnx_entry.get("size_bytes"), int) or onnx_entry["size_bytes"] <= 0:
                errors.append(f"invalid {component} ONNX size in artifact manifest")

        om_entry = _manifest_entry_for(
            entries,
            f"artifacts/om/8t-ascend310b4/mobileclip_s0_{component}.om",
        )
        om = item.get("om")
        if om_entry is None:
            errors.append(f"missing manifest 8T OM entry for {component}")
        if not isinstance(om, Mapping):
            errors.append(f"missing OM metadata for 8T {component} conversion")
        else:
            om_sha = str(om.get("sha256", "")).lower()
            om_size = om.get("size")
            if len(om_sha) != 64 or any(ch not in "0123456789abcdef" for ch in om_sha):
                errors.append(f"invalid OM SHA-256 for 8T {component} conversion")
            if not isinstance(om_size, int) or om_size <= 0:
                errors.append(f"invalid OM size for 8T {component} conversion")
            if om_entry is not None:
                if str(om_entry.get("sha256", "")).lower() != om_sha:
                    errors.append(f"OM hash does not match artifact manifest for {component}")
                if om_entry.get("size_bytes") != om_size:
                    errors.append(f"OM size does not match artifact manifest for {component}")

        report = item.get("report")
        if not isinstance(report, Mapping) or not report.get("exists"):
            errors.append(f"missing ATC report evidence for {component}")
        elif len(str(report.get("sha256", ""))) != 64:
            errors.append(f"invalid ATC report SHA-256 for {component}")

        log_path = _local_conversion_log(root, item, component, "8t-310b4")
        if log_path is None:
            errors.append(f"missing local ATC log evidence for {component}")
            command_text = ""
        else:
            try:
                command_text = log_path.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                errors.append(f"cannot read ATC log for {component}: {exc}")
                command_text = ""
        command = item.get("atc_command")
        if isinstance(command, list):
            command_text += " " + " ".join(str(value) for value in command)
        required_flags = (
            "--framework=5",
            "--soc_version=Ascend310B4",
            f"--input_shape={'image:1,3,256,256' if component == 'image' else 'text:1,77'}",
            "--precision_mode=allow_fp32_to_fp16",
            "--op_select_implmode=high_precision_for_all",
            "--enable_graph_parallel=0",
            "--op_compiler_cache_mode=disable",
        )
        for flag in required_flags:
            if flag not in command_text:
                errors.append(f"ATC evidence for {component} is missing {flag}")

        policy = root / "environment" / "8t-310b4" / "compile-policy.txt"
        if not policy.is_file():
            errors.append("missing 8T serial compile-policy evidence")
        else:
            try:
                policy_text = policy.read_text(encoding="utf-8", errors="replace")
            except OSError as exc:
                errors.append(f"cannot read 8T compile policy: {exc}")
                policy_text = ""
            for marker in (
                "policy=serial-no-cache-no-swap",
                "MAX_COMPILE_CORE_NUMBER=1",
                "MULTI_THREAD_COMPILE=0",
                "TBE_PARALLEL_COMPILER=0",
                "atc_op_compiler_cache_mode=disable",
                "cpu_fallback=false",
            ):
                if marker not in policy_text:
                    errors.append(f"8T compile policy is missing {marker}")
    return errors


def aggregate(campaign_root: Path) -> Dict[str, Any]:
    """Read and normalize one campaign without modifying its evidence."""
    root = campaign_root.resolve()
    _assert_isolated_root(root)
    environments, environment_errors = _environment_records(root)
    cells, cell_errors = _find_cell_reports(root)
    conversions, conversion_errors = _conversion_records(root)
    manifest = _artifact_manifest(root)
    artifact_errors = _check_artifact_hashes(root, manifest)

    matrix: Dict[str, Dict[str, Mapping[str, Any]]] = {}
    missing: List[Dict[str, str]] = []
    non_passing: List[Dict[str, Any]] = []
    for component in EXPECTED_COMPONENTS:
        matrix[component] = {}
        for compiler, runtime in EXPECTED_CELLS:
            key = (component, compiler, runtime)
            cell_name = f"{compiler}-om-on-{runtime}"
            value = cells.get(key)
            if value is None:
                missing_item = {
                    "component": component,
                    "compiler_role": compiler,
                    "runtime_role": runtime,
                }
                missing.append(missing_item)
                value = {
                    "status": MISSING_STATUS,
                    **missing_item,
                    "production_mutation": False,
                }
            matrix[component][cell_name] = value
            if value.get("status") != "passed":
                failure = value.get("failure_class")
                if failure not in ALLOWED_STATUS:
                    failure = None if value.get("status") == MISSING_STATUS else value.get("status")
                non_passing.append(
                    {
                        "component": component,
                        "cell": cell_name,
                        "compiler_role": compiler,
                        "runtime_role": runtime,
                        "status": value.get("status"),
                        "failure_class": failure,
                    }
                )

    structural_errors = list(environment_errors) + list(cell_errors) + list(conversion_errors)
    structural_errors.extend(_conversion_gate_errors(root, conversions, manifest))
    if manifest is None:
        structural_errors.append("missing artifact_manifest.json")
    elif not _manifest_entries(manifest):
        structural_errors.append("artifact manifest is empty")
    missing_roles = [role for role in EXPECTED_ROLES if role not in environments]
    structural_errors.extend(f"missing environment evidence for {role}" for role in missing_roles)
    complete = not (missing or non_passing or artifact_errors or structural_errors)
    return {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": root.name,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model_id": MODEL_ID,
        "protocol": {
            "threshold": NUMERICAL_THRESHOLD,
            "expected_components": list(EXPECTED_COMPONENTS),
            "expected_sample_counts": dict(EXPECTED_SAMPLE_COUNTS),
            "expected_runtime_roles": list(EXPECTED_ROLES),
            "expected_cells_per_component": [
                {"compiler_role": compiler, "runtime_role": runtime}
                for compiler, runtime in EXPECTED_CELLS
            ],
            "status_taxonomy": sorted(ALLOWED_STATUS),
        },
        "production_mutation": False,
        "environments": environments,
        "conversions": conversions,
        "artifact_manifest": manifest,
        "matrix": matrix,
        "missing_cells": missing,
        "non_passing_cells": non_passing,
        "errors": structural_errors,
        "artifact_hash_errors": artifact_errors,
        "complete": complete,
        "evidence_boundary": (
            "Complete means both board environment records, 8T image/text conversion "
            "evidence, all eight ACL cells passed, and any supplied artifact hashes "
            "verified. It does not admit or deploy a production model."
        ),
    }


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-root", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="return 2 unless all eight cells and structural/hash gates pass",
    )
    args = parser.parse_args(argv)
    try:
        root = args.campaign_root.resolve()
        result = aggregate(root)
        output = (args.output or (root / "compatibility_matrix.json")).resolve()
        _assert_inside(output, root, "output path")
        _write_json_atomic(output, result)
        print(output)
        if args.strict and not result["complete"]:
            print("campaign is incomplete or contains non-passing cells", file=sys.stderr)
            return 2
        return 0
    except AggregateError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
