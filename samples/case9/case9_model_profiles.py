"""Strict registry and state helpers for the experimental MindSpore chat models.

The registry is intentionally independent from the gateway's public model id.
It contains provenance and board constraints, while ``public_profiles`` emits a
small, path-free view that is safe for the browser model/status display.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import argparse
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_REGISTRY_PATH = BASE_DIR / "configs" / "chat_model_profiles.json"
DEFAULT_STATE_PATH = BASE_DIR / "run" / "mindspore-chat" / "active-model.json"

ALLOWED_STATUSES = frozenset(
    {
        "artifact_verified",
        "environment_verified",
        "load_passed",
        "json_passed",
        "sse_passed",
        "stability_passed",
        "quality_reviewed",
        "performance_recorded",
        "experimental_dirty_base",
        "admitted",
        "blocked",
        "not-run",
    }
)
_PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_REVISION_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|main)$")
_SAFE_RELATIVE_RE = re.compile(r"^[^\\/\x00:]+(?:/[^\\/\x00:]+)*$")
_LANGUAGE_RE = re.compile(r"^[a-z][a-z0-9-]{0,15}$", re.IGNORECASE)
_CANDIDATE_KIND_RE = re.compile(r"^[a-z][a-z0-9_-]{2,31}$")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_UTC_STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
# Only terminal activation states require a complete evidence bundle.  Earlier
# gate states are intentionally incremental and may contain just the evidence
# for the gate that has completed.
_COMPLETE_EVIDENCE_STATUSES = frozenset({"experimental_dirty_base", "admitted"})

# ``admitted`` is an operator decision, not a synonym for a machine smoke
# pass.  Keep the accepted spellings deliberately small so a free-form report
# value cannot accidentally become a promotion approval.
_QUALITY_PASS_VALUES = frozenset({"pass", "passed", "ok", "approved"})
_QUALITY_APPROVAL_VALUES = frozenset({"approved", "approve", "accepted", "accept"})
_QUALITY_MIN_SAMPLES = 10
_QUALITY_MIN_PASSED = 8


class ProfileError(ValueError):
    """Raised when registry or active-state data is unsafe or malformed."""


def _duplicate_key_object(pairs: Iterable[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProfileError("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def _object(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProfileError("%s must be an object" % name)
    return value


def _exact_keys(value: Mapping[str, Any], allowed: Sequence[str], name: str) -> None:
    extras = sorted(set(value) - set(allowed))
    missing = sorted(set(allowed) - set(value))
    if extras:
        raise ProfileError("%s has unknown keys: %s" % (name, ", ".join(extras)))
    if missing:
        raise ProfileError("%s is missing keys: %s" % (name, ", ".join(missing)))


def _string(value: Any, name: str, *, pattern: Optional[re.Pattern[str]] = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProfileError("%s must be a non-empty string" % name)
    value = value.strip()
    if pattern is not None and not pattern.fullmatch(value):
        raise ProfileError("%s has an invalid format" % name)
    return value


def _nullable_string(value: Any, name: str) -> Optional[str]:
    if value is None:
        return None
    return _string(value, name)


def _integer(value: Any, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProfileError("%s must be an integer >= %d" % (name, minimum))
    return value


def _number(value: Any, name: str, *, minimum: float = 0.0, maximum: Optional[float] = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileError("%s must be a number" % name)
    result = float(value)
    if not math.isfinite(result):
        raise ProfileError("%s must be finite" % name)
    if result < minimum or (maximum is not None and result > maximum):
        raise ProfileError("%s is outside the permitted range" % name)
    return result


def safe_relative_path(value: Union[str, Path], name: str = "path") -> Path:
    """Validate a POSIX relative path used by a profile or state file.

    Backslashes, dot components, absolute paths and NUL bytes are rejected so
    a value cannot silently become a Windows or POSIX traversal when reused by
    a board shell script.
    """

    if not isinstance(value, (str, Path)):
        raise ProfileError("%s must be a POSIX relative path" % name)
    # ``Path`` uses the host separator. Normalize it to POSIX form before
    # validating a registry-relative path so the same checked-in registry
    # works on Windows controllers and Linux boards. A raw string is kept
    # verbatim, which still rejects caller-supplied backslashes explicitly.
    raw = value.as_posix() if isinstance(value, Path) else str(value)
    if not raw or not _SAFE_RELATIVE_RE.fullmatch(raw):
        raise ProfileError("%s must be a POSIX relative path" % name)
    path = Path(raw)
    if path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise ProfileError("%s must not escape its root" % name)
    return path


def _reject_symlink_components(path: Path) -> None:
    """Reject a lexical path containing an existing symlink component."""

    anchor = Path(path.anchor)
    current = anchor
    try:
        parts = path.relative_to(anchor).parts
    except ValueError as exc:  # pragma: no cover - defensive for mixed drives
        raise ProfileError("path has an invalid anchor: %s" % path) from exc
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ProfileError("symlink path component is not allowed: %s" % current)


def resolve_safe_path(
    value: Union[str, Path],
    root: Union[str, Path] = BASE_DIR,
    *,
    allow_missing: bool = True,
) -> Path:
    """Resolve a path and require it to remain below ``root``.

    This helper is used for active state and artifact paths. Existing symlink
    components are rejected; callers never accidentally follow a link outside
    the controlled directory.
    """

    if not isinstance(value, (str, Path)) or not isinstance(root, (str, Path)):
        raise ProfileError("path and root must be strings or Path objects")
    root_path = Path(root).expanduser().resolve(strict=True)
    candidate_input = Path(value).expanduser()
    if candidate_input.is_absolute():
        lexical_candidate = candidate_input
    else:
        lexical_candidate = root_path / safe_relative_path(candidate_input)
    _reject_symlink_components(lexical_candidate)
    candidate = lexical_candidate.resolve(strict=False)
    try:
        candidate.relative_to(root_path)
    except ValueError as exc:
        raise ProfileError("path escapes root: %s" % value) from exc
    if not allow_missing and not candidate.exists():
        raise ProfileError("path does not exist: %s" % candidate)
    # Do not follow an existing symlink anywhere between root and target.
    return candidate


def _resolve_explicit_path(
    value: Union[str, Path],
    default_root: Union[str, Path] = BASE_DIR,
    *,
    allow_missing: bool = True,
) -> Path:
    """Resolve a caller-supplied file while rejecting symlink components.

    Registry/state locations may intentionally live outside the checkout on a
    board (for example under ``~/case9-mindspore-chat``).  Relative locations
    remain confined to ``default_root``; an explicitly absolute location is
    accepted only as a concrete path and is never followed through a symlink.
    """

    if not isinstance(value, (str, Path)):
        raise ProfileError("path must be a string or Path object")
    raw = Path(value).expanduser()
    if not raw.is_absolute():
        return resolve_safe_path(raw, default_root, allow_missing=allow_missing)
    # Validate the lexical path before ``resolve`` follows an existing link.
    _reject_symlink_components(raw)
    candidate = raw.resolve(strict=False)
    if not allow_missing and not candidate.exists():
        raise ProfileError("path does not exist: %s" % candidate)
    return candidate


@dataclass(frozen=True)
class ArtifactSpec:
    name: str
    kind: str
    filename: str
    url: str
    expected_bytes: Optional[int]
    sha256: Optional[str]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], index: int) -> "ArtifactSpec":
        name = "artifact[%d]" % index
        _exact_keys(raw, ("name", "kind", "filename", "url", "expected_bytes", "sha256"), name)
        artifact_name = _string(raw["name"], name + ".name", pattern=_PROFILE_ID_RE)
        kind = _string(raw["kind"], name + ".kind", pattern=re.compile(r"^[a-z][a-z0-9_-]{1,31}$"))
        filename = safe_relative_path(raw["filename"], name + ".filename").as_posix()
        url = _string(raw["url"], name + ".url")
        expected = raw["expected_bytes"]
        if expected is not None:
            expected = _integer(expected, name + ".expected_bytes", minimum=1)
        digest = raw["sha256"]
        if digest is not None:
            digest = _string(digest, name + ".sha256", pattern=_SHA256_RE).lower()
        return cls(artifact_name, kind, filename, url, expected, digest)


def _string_list(value: Any, name: str, *, pattern: Optional[re.Pattern[str]] = None) -> Tuple[str, ...]:
    """Validate a small metadata string list while preserving declaration order."""

    if not isinstance(value, list) or not value:
        raise ProfileError("%s must be a non-empty array" % name)
    result: List[str] = []
    for index, item in enumerate(value):
        result.append(_string(item, "%s[%d]" % (name, index), pattern=pattern))
    if len(set(result)) != len(result):
        raise ProfileError("%s must not contain duplicates" % name)
    return tuple(result)


def _metadata_value(value: Any, name: str, depth: int = 0) -> Any:
    """Validate JSON metadata without permitting executable/path objects."""

    if depth > 4:
        raise ProfileError("%s is too deeply nested" % name)
    if value is None or isinstance(value, (str, int, float, bool)):
        if isinstance(value, float) and not math.isfinite(value):
            raise ProfileError("%s must contain finite numbers" % name)
        return value
    if isinstance(value, list):
        return [_metadata_value(item, "%s[%d]" % (name, index), depth + 1) for index, item in enumerate(value)]
    if isinstance(value, Mapping):
        result: Dict[str, Any] = {}
        for key, item in value.items():
            key_string = _string(key, "%s key" % name, pattern=re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$"))
            result[key_string] = _metadata_value(item, "%s.%s" % (name, key_string), depth + 1)
        return result
    raise ProfileError("%s contains an unsupported value" % name)


def _quality_text(value: Any) -> str:
    """Extract a conservative status string from quality metadata."""

    if isinstance(value, Mapping):
        if value.get("approved") is True:
            return "approved"
        for key in ("status", "result", "decision", "value", "label"):
            candidate = value.get(key)
            if isinstance(candidate, str):
                return candidate.strip().lower()
        return ""
    return value.strip().lower() if isinstance(value, str) else ""


def _quality_integer(sources: Sequence[Mapping[str, Any]], names: Sequence[str]) -> Optional[int]:
    """Read one integer count without coercing strings or booleans."""

    for source in sources:
        for name in names:
            value = source.get(name)
            if isinstance(value, int) and not isinstance(value, bool):
                return value
    return None


def quality_admission_check(
    quality: Any,
    *,
    languages: Sequence[str] = (),
) -> Tuple[bool, str]:
    """Return whether human quality evidence is sufficient for ``admitted``.

    A machine quality gate or ``reviewed=true`` alone is not an admission
    decision.  The registry must carry an explicit human approval marker and
    an auditable count of at least 8 passed samples out of at least 10 (with an
    80 percent pass ratio).  Language labels are checked independently so a
    successful English probe cannot silently stand in for a required Chinese
    probe.

    The function accepts the flat and nested count spellings used by existing
    reports, but never accepts a numeric string or ``machine_valid_count`` as
    human evidence.  It is intentionally usable by the service and shell
    controller as a second fail-closed check for hand-built mappings.
    """

    if not isinstance(quality, Mapping):
        return False, "quality metadata is missing"
    if quality.get("reviewed") is not True:
        return False, "quality.reviewed must be true"

    human_review = quality.get("human_review")
    approval_value = _quality_text(human_review)
    if approval_value not in _QUALITY_APPROVAL_VALUES:
        return False, "quality.human_review must explicitly be approved by a human"

    review_sources: List[Mapping[str, Any]] = []
    if isinstance(human_review, Mapping):
        review_sources.append(human_review)
    review_sources.append(quality)
    passed = _quality_integer(
        review_sources,
        (
            "human_passed_count",
            "passed_count",
            "pass_count",
            "accepted_count",
            "correct_count",
            "passed",
        ),
    )
    samples = _quality_integer(
        review_sources,
        (
            "human_sample_count",
            "sample_count",
            "total_count",
            "evaluated_count",
            "samples",
            "total",
        ),
    )

    # A per-language review may carry the only explicit counts.  Aggregate it
    # only when every language entry supplies integer counts; partial metadata
    # remains a hard failure rather than being guessed.
    language_map = quality.get("languages")
    if (passed is None or samples is None) and isinstance(language_map, Mapping):
        language_passed = 0
        language_samples = 0
        complete = bool(language_map)
        for item in language_map.values():
            if not isinstance(item, Mapping):
                complete = False
                break
            item_passed = _quality_integer(
                (item,),
                ("human_passed_count", "passed_count", "pass_count", "passed"),
            )
            item_samples = _quality_integer(
                (item,),
                ("human_sample_count", "sample_count", "total_count", "total"),
            )
            if item_passed is None or item_samples is None:
                complete = False
                break
            language_passed += item_passed
            language_samples += item_samples
        if complete:
            passed = language_passed if passed is None else passed
            samples = language_samples if samples is None else samples

    if passed is None or samples is None:
        return False, "human quality evidence must include integer passed and sample counts"
    if passed < 0 or samples < 0 or passed > samples:
        return False, "human quality counts are inconsistent"
    if samples < _QUALITY_MIN_SAMPLES or passed < _QUALITY_MIN_PASSED:
        return False, "human quality requires at least 8 passed samples out of 10"
    if passed * _QUALITY_MIN_SAMPLES < samples * _QUALITY_MIN_PASSED:
        return False, "human quality pass ratio must be at least 80 percent"

    declared_languages = {
        str(item).strip().lower() for item in languages if isinstance(item, str) and item.strip()
    }
    if not declared_languages and isinstance(language_map, Mapping):
        declared_languages = {
            str(item).strip().lower() for item in language_map if str(item).strip()
        }
    required_languages = {"zh"} if "zh" in declared_languages else declared_languages
    if not required_languages:
        return False, "human quality evidence must declare at least one language"
    if not isinstance(language_map, Mapping):
        return False, "quality.languages is required for admission"
    normalized_language_map = {
        str(key).strip().lower(): value for key, value in language_map.items()
    }
    for language in sorted(required_languages):
        if language not in normalized_language_map:
            return False, "quality.languages is missing %s" % language
        status = _quality_text(normalized_language_map[language])
        if status not in _QUALITY_PASS_VALUES:
            return False, "quality language %s is not passed" % language
    return True, ""


def _parse_board_targets(
    raw: Any,
    fallback: Mapping[str, str],
    name: str,
    *,
    require_dual_soc: bool = False,
) -> Tuple[Mapping[str, str], ...]:
    """Parse board targets and optionally require both supported board SoCs.

    Schema v1 profiles may still describe only their historical primary board.
    Schema v2 profiles are a dual-board registry, so they opt into the explicit
    ``Ascend310B4`` and ``Ascend310B1`` requirement below.
    """

    value = fallback if raw is None else raw
    if isinstance(value, Mapping):
        value = [value]
    if not isinstance(value, list) or not value:
        raise ProfileError("%s must be a non-empty array" % name)
    targets: List[Mapping[str, str]] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        target_name = "%s[%d]" % (name, index)
        board = _object(item, target_name)
        _exact_keys(board, ("host", "soc", "tier"), target_name)
        host = _string(board["host"], target_name + ".host", pattern=re.compile(r"^[0-9a-fA-F:.]+$"))
        soc = _string(board["soc"], target_name + ".soc", pattern=re.compile(r"^Ascend[0-9A-Za-z]+$"))
        tier = _string(board["tier"], target_name + ".tier", pattern=re.compile(r"^[0-9]+T$"))
        normalized_soc = _normalize_soc(soc)
        if normalized_soc in seen:
            raise ProfileError("%s contains duplicate SoC %s" % (name, soc))
        seen.add(normalized_soc)
        targets.append({"host": host, "soc": soc, "tier": tier})
    if require_dual_soc:
        required_socs = {"310b4", "310b1"}
        missing = sorted(required_socs - seen)
        if missing:
            missing_names = ", ".join("Ascend" + item.upper() for item in missing)
            raise ProfileError(
                "%s must include board targets for Ascend310B4 and Ascend310B1 "
                "(missing %s)" % (name, missing_names)
            )
    return tuple(targets)


def _nullable_string_pattern(
    value: Any, name: str, pattern: re.Pattern[str]
) -> Optional[str]:
    """Validate an optional provenance string while preserving ``None``."""

    if value is None:
        return None
    return _string(value, name, pattern=pattern)


def _parse_hash_map(value: Any, name: str) -> Dict[str, str]:
    """Validate a map of artifact names to SHA-256 digests."""

    mapping = _object(value, name)
    result: Dict[str, str] = {}
    for key, digest in mapping.items():
        key_string = _string(
            key,
            name + " key",
            pattern=re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"),
        )
        result[key_string] = _string(digest, name + "." + key_string, pattern=_SHA256_RE).lower()
    return result


def _parse_validation(
    raw: Any,
    name: str,
    *,
    board_targets: Sequence[Mapping[str, str]] = (),
    artifact_names: Sequence[str] = (),
    languages: Sequence[str] = (),
    strict: bool = False,
) -> Mapping[str, Mapping[str, Any]]:
    """Validate per-SoC gate summaries without trusting report paths.

    Schema v2 records are deliberately explicit.  Even an unexecuted board
    must carry a ``run_id: null``/empty evidence record so a missing result
    cannot be mistaken for a successful result or silently inherited from the
    other SoC.
    """

    if raw is None:
        if strict:
            raise ProfileError(name + " is required for schema v2")
        return {}
    validation = _object(raw, name)
    result: Dict[str, Mapping[str, Any]] = {}
    allowed = (
        "status",
        "reason",
        "report_paths",
        "updated_at",
        "run_id",
        "soc",
        "tier",
        "environment_fingerprint",
        "artifact_hashes",
        "quality",
        "performance",
        "metrics",
        "notes",
    )
    required = {
        "status",
        "reason",
        "report_paths",
        "updated_at",
        "run_id",
        "soc",
        "tier",
        "environment_fingerprint",
        "artifact_hashes",
        "quality",
        "performance",
        "metrics",
    }
    target_by_soc = {
        str(item.get("soc")): item for item in board_targets if item.get("soc")
    }
    if strict and set(validation) != set(target_by_soc):
        missing = sorted(set(target_by_soc) - set(validation))
        extra = sorted(set(validation) - set(target_by_soc))
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unknown " + ", ".join(extra))
        raise ProfileError(name + " SoC keys must match board_targets exactly (" + "; ".join(details) + ")")
    for soc, item in validation.items():
        soc_name = _string(soc, name + " key", pattern=re.compile(r"^Ascend[0-9A-Za-z]+$"))
        entry = _object(item, name + "." + soc_name)
        extras = sorted(set(entry) - set(allowed))
        if extras:
            raise ProfileError(name + "." + soc_name + " has unknown keys: " + ", ".join(extras))
        if strict:
            missing = sorted(required - set(entry))
            if missing:
                raise ProfileError(
                    name + "." + soc_name + " is missing keys: " + ", ".join(missing)
                )
        if "status" not in entry:
            raise ProfileError(name + "." + soc_name + ".status is required")
        status = _string(entry["status"], name + "." + soc_name + ".status")
        if status not in ALLOWED_STATUSES:
            raise ProfileError(name + "." + soc_name + ".status is not an allowed status")
        reason = _string(entry.get("reason", "No board-specific result recorded."), name + "." + soc_name + ".reason")
        parsed: Dict[str, Any] = {"status": status, "reason": reason}
        if "report_paths" in entry:
            paths = entry["report_paths"]
            if not isinstance(paths, list):
                raise ProfileError(name + "." + soc_name + ".report_paths must be an array")
            # Report paths are provenance metadata and must remain relative.
            parsed["report_paths"] = [
                safe_relative_path(item, name + "." + soc_name + ".report_paths").as_posix()
                for item in paths
            ]
        if "updated_at" in entry:
            parsed["updated_at"] = _nullable_string_pattern(
                entry["updated_at"], name + "." + soc_name + ".updated_at", _UTC_STAMP_RE
            )
        if "run_id" in entry:
            parsed["run_id"] = _nullable_string_pattern(
                entry["run_id"], name + "." + soc_name + ".run_id", _RUN_ID_RE
            )
        if "soc" in entry:
            declared_soc = _string(
                entry["soc"], name + "." + soc_name + ".soc",
                pattern=re.compile(r"^Ascend[0-9A-Za-z]+$"),
            )
            if strict and declared_soc != soc_name:
                raise ProfileError(name + "." + soc_name + ".soc must match its key")
            parsed["soc"] = declared_soc
        if "tier" in entry:
            declared_tier = _string(
                entry["tier"], name + "." + soc_name + ".tier",
                pattern=re.compile(r"^[0-9]+T$"),
            )
            if strict and target_by_soc.get(soc_name, {}).get("tier") != declared_tier:
                raise ProfileError(name + "." + soc_name + ".tier does not match board_targets")
            parsed["tier"] = declared_tier
        if "environment_fingerprint" in entry:
            parsed["environment_fingerprint"] = _nullable_string_pattern(
                entry["environment_fingerprint"],
                name + "." + soc_name + ".environment_fingerprint",
                _SHA256_RE,
            )
        if "artifact_hashes" in entry:
            parsed["artifact_hashes"] = _parse_hash_map(
                entry["artifact_hashes"], name + "." + soc_name + ".artifact_hashes"
            )
        for field_name in ("quality", "performance", "metrics"):
            if field_name in entry:
                parsed[field_name] = _metadata_value(
                    _object(entry[field_name], name + "." + soc_name + "." + field_name),
                    name + "." + soc_name + "." + field_name,
                )
        if "notes" in entry:
            parsed["notes"] = _string(entry["notes"], name + "." + soc_name + ".notes")
        if strict and status in _COMPLETE_EVIDENCE_STATUSES:
            required_values = (
                parsed.get("run_id"),
                parsed.get("updated_at"),
                parsed.get("environment_fingerprint"),
                parsed.get("report_paths"),
                parsed.get("quality"),
                parsed.get("performance"),
                parsed.get("metrics"),
            )
            if not all(required_values):
                raise ProfileError(
                    name + "." + soc_name
                    + " has an evidence status but incomplete run metadata"
                )
            hashes = parsed.get("artifact_hashes")
            if not isinstance(hashes, Mapping):
                raise ProfileError(name + "." + soc_name + ".artifact_hashes must be an object")
            missing_hashes = sorted(set(artifact_names) - set(hashes))
            if missing_hashes:
                raise ProfileError(
                    name + "." + soc_name + ".artifact_hashes is missing: "
                    + ", ".join(missing_hashes)
                )
        if strict and status == "admitted":
            approved, approval_reason = quality_admission_check(
                parsed.get("quality"),
                languages=languages,
            )
            if not approved:
                raise ProfileError(
                    name + "." + soc_name
                    + " admitted status requires approved human quality evidence: "
                    + approval_reason
                )
        result[soc_name] = parsed
    return result


def _normalize_soc(value: Any) -> str:
    """Normalize ``Ascend310B4`` and ``310B4`` spellings for lookups."""

    return str(value or "").strip().lower().replace("ascend", "", 1)


@dataclass(frozen=True)
class ChatModelProfile:
    id: str
    display_name: str
    model_id: str
    repository: str
    source: str
    license_name: str
    revision: str
    tokenizer_revision: str
    revision_pinned: bool
    mirror: Optional[str]
    board_host: str
    board_soc: str
    board_tier: str
    runtime_provider: str
    context_length: int
    default_max_tokens: int
    max_tokens: int
    temperature: float
    top_p: float
    cache_dir: str
    artifacts: Tuple[ArtifactSpec, ...]
    status: str
    admission_eligible: bool
    admission_reason: str
    notes: str
    # The fields below were introduced by registry schema v2.  Defaults keep
    # v1 history/fixture registries readable while checked-in v2 profiles can
    # describe their model family and per-SoC evidence explicitly.
    candidate_kind: str = "native_mindspore"
    architecture: str = "causal_lm"
    languages: Tuple[str, ...] = ()
    weight_format: str = "unknown"
    board_targets: Tuple[Mapping[str, str], ...] = ()
    validation: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    quality: Mapping[str, Any] = field(default_factory=dict)
    performance: Mapping[str, Any] = field(default_factory=dict)

    @classmethod
    def from_mapping(
        cls, raw: Mapping[str, Any], index: int = 0, *, schema_version: int = 1
    ) -> "ChatModelProfile":
        name = "profiles[%d]" % index
        base_keys = {
            "id", "display_name", "model_id", "repository", "source",
            "revision", "tokenizer_revision", "revision_pinned", "mirror",
            "board", "runtime", "cache_dir", "artifacts", "status",
            "admission", "notes",
        }
        v2_keys = base_keys | {
            "license",
            "candidate_kind", "architecture", "languages", "weight_format",
            "board_targets", "validation", "quality", "performance",
        }
        if schema_version >= 2:
            # Keep the v2 contract explicit instead of relying solely on the
            # generic exact-key helper.  This makes omissions fail closed even
            # if that helper is later reused with optional keys.
            required_v2_keys = {
                "license",
                "candidate_kind", "architecture", "languages", "weight_format",
                "board_targets", "validation", "quality", "performance",
            }
            missing_v2_keys = sorted(required_v2_keys - set(raw))
            if missing_v2_keys:
                raise ProfileError(
                    name + " is missing schema v2 keys: " + ", ".join(missing_v2_keys)
                )
        _exact_keys(
            raw,
            tuple(sorted(v2_keys if schema_version >= 2 else base_keys)),
            name,
        )
        profile_id = _string(raw["id"], name + ".id", pattern=_PROFILE_ID_RE)
        display_name = _string(raw["display_name"], name + ".display_name")
        model_id = _string(raw["model_id"], name + ".model_id")
        repository = _string(raw["repository"], name + ".repository")
        source = _string(raw["source"], name + ".source", pattern=re.compile(r"^[a-z][a-z0-9_-]{1,31}$"))
        license_name = _string(raw.get("license", "unknown"), name + ".license", pattern=re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .+()/_-]{0,127}$"))
        revision = _string(raw["revision"], name + ".revision", pattern=_REVISION_RE)
        tokenizer_revision = _string(raw["tokenizer_revision"], name + ".tokenizer_revision", pattern=_REVISION_RE)
        pinned = raw["revision_pinned"]
        if not isinstance(pinned, bool):
            raise ProfileError(name + ".revision_pinned must be boolean")
        if pinned and (len(revision) != 40 or len(tokenizer_revision) != 40):
            raise ProfileError(name + " pinned revisions must be 40-hex commits")
        mirror = _nullable_string(raw["mirror"], name + ".mirror")

        board = _object(raw["board"], name + ".board")
        _exact_keys(board, ("host", "soc", "tier"), name + ".board")
        board_host = _string(board["host"], name + ".board.host", pattern=re.compile(r"^[0-9a-fA-F:.]+$"))
        board_soc = _string(board["soc"], name + ".board.soc", pattern=re.compile(r"^Ascend[0-9A-Za-z]+$"))
        board_tier = _string(board["tier"], name + ".board.tier", pattern=re.compile(r"^[0-9]+T$"))

        runtime = _object(raw["runtime"], name + ".runtime")
        _exact_keys(runtime, ("provider", "context_length", "default_max_tokens", "max_tokens", "temperature", "top_p"), name + ".runtime")
        provider = _string(runtime["provider"], name + ".runtime.provider", pattern=re.compile(r"^[a-z][a-z0-9_-]{1,31}$"))
        context_length = _integer(runtime["context_length"], name + ".runtime.context_length", minimum=1)
        default_tokens = _integer(runtime["default_max_tokens"], name + ".runtime.default_max_tokens", minimum=1)
        max_tokens = _integer(runtime["max_tokens"], name + ".runtime.max_tokens", minimum=1)
        if default_tokens > max_tokens or max_tokens >= context_length:
            raise ProfileError(name + " token limits are inconsistent with context_length")
        temperature = _number(runtime["temperature"], name + ".runtime.temperature", minimum=0.0, maximum=0.0)
        top_p = _number(runtime["top_p"], name + ".runtime.top_p", minimum=1.0, maximum=1.0)

        cache_dir = safe_relative_path(raw["cache_dir"], name + ".cache_dir").as_posix()
        raw_artifacts = raw["artifacts"]
        if not isinstance(raw_artifacts, list) or not raw_artifacts:
            raise ProfileError(name + ".artifacts must be a non-empty array")
        artifacts = tuple(ArtifactSpec.from_mapping(_object(item, "%s.artifacts[%d]" % (name, i)), i) for i, item in enumerate(raw_artifacts))
        artifact_names = [item.name for item in artifacts]
        if len(set(artifact_names)) != len(artifact_names):
            raise ProfileError(name + ".artifacts names must be unique")

        status = _string(raw["status"], name + ".status")
        if status not in ALLOWED_STATUSES:
            raise ProfileError(name + ".status is not an allowed status")
        admission = _object(raw["admission"], name + ".admission")
        _exact_keys(admission, ("eligible", "reason"), name + ".admission")
        eligible = admission["eligible"]
        if not isinstance(eligible, bool):
            raise ProfileError(name + ".admission.eligible must be boolean")
        reason = _string(admission["reason"], name + ".admission.reason")
        notes = _string(raw["notes"], name + ".notes")
        if eligible and status != "admitted":
            raise ProfileError(name + " eligible profiles must have admitted status")
        if status == "admitted" and not eligible:
            raise ProfileError(name + " admitted profiles must be eligible")
        if not pinned and status not in {"blocked", "not-run"}:
            raise ProfileError(name + " mutable revisions are only allowed for blocked/not-run profiles")

        # Schema v2 metadata is deliberately declarative.  It does not affect
        # loading until a profile has passed the normal artifact and hardware
        # gates, but it gives the UI and reports enough context to distinguish
        # native MindSpore candidates from conditional/non-executable entries.
        candidate_kind = _string(
            raw.get("candidate_kind", "native_mindspore"),
            name + ".candidate_kind",
            pattern=_CANDIDATE_KIND_RE,
        )
        if candidate_kind not in {"native_mindspore", "conditional"}:
            raise ProfileError(name + ".candidate_kind must be native_mindspore or conditional")
        architecture = _string(
            raw.get("architecture", "causal_lm"),
            name + ".architecture",
            pattern=_CANDIDATE_KIND_RE,
        )
        languages_value = raw.get("languages", ["unknown"])
        languages = _string_list(languages_value, name + ".languages", pattern=_LANGUAGE_RE)
        weight_format = _string(
            raw.get("weight_format", "unknown"),
            name + ".weight_format",
            pattern=_CANDIDATE_KIND_RE,
        )
        board_targets = _parse_board_targets(
            raw.get("board_targets"),
            board,
            name + ".board_targets",
            require_dual_soc=schema_version >= 2,
        )
        validation = _parse_validation(
            raw.get("validation", {}),
            name + ".validation",
            board_targets=board_targets,
            artifact_names=[item.name for item in artifacts],
            languages=languages,
            strict=schema_version >= 2,
        )
        quality_raw = raw.get("quality", {})
        performance_raw = raw.get("performance", {})
        quality = _metadata_value(_object(quality_raw, name + ".quality"), name + ".quality")
        performance = _metadata_value(_object(performance_raw, name + ".performance"), name + ".performance")
        if schema_version >= 2:
            if candidate_kind == "conditional" and (status == "admitted" or eligible):
                raise ProfileError(name + " conditional profiles cannot be admitted")
            if status == "admitted":
                approved, approval_reason = quality_admission_check(
                    quality,
                    languages=languages,
                )
                if not approved:
                    raise ProfileError(
                        name + " admitted status requires approved human quality evidence: "
                        + approval_reason
                    )
            if eligible and status == "admitted":
                # Keep the eligibility bit tied to the same explicit quality
                # decision.  This prevents a future editor from setting only
                # ``admission.eligible`` and accidentally enabling a worker.
                approved, approval_reason = quality_admission_check(
                    quality,
                    languages=languages,
                )
                if not approved:
                    raise ProfileError(
                        name + " eligible admission requires approved human quality evidence: "
                        + approval_reason
                    )
        return cls(
            profile_id, display_name, model_id, repository, source, license_name, revision,
            tokenizer_revision, pinned, mirror, board_host, board_soc, board_tier,
            provider, context_length, default_tokens, max_tokens, temperature,
            top_p, cache_dir, artifacts, status, eligible, reason, notes,
            candidate_kind, architecture, languages, weight_format, board_targets,
            validation, quality, performance,
        )

    # Compatibility aliases used by the service and board scripts.
    @property
    def profile_id(self) -> str:
        return self.id

    @property
    def source_model(self) -> str:
        return self.model_id

    @property
    def license(self) -> str:
        """Compatibility alias for the schema v2 ``license`` field."""

        return self.license_name

    @property
    def model_name(self) -> str:
        return self.model_id

    @property
    def context_limit(self) -> int:
        return self.context_length

    @property
    def max_new_tokens(self) -> int:
        return self.max_tokens

    @property
    def default_tokens(self) -> int:
        return self.default_max_tokens

    @property
    def board(self) -> Mapping[str, str]:
        return {"host": self.board_host, "soc": self.board_soc, "tier": self.board_tier}

    @property
    def is_conditional(self) -> bool:
        """Whether this entry is metadata-only and must never be loaded."""

        return self.candidate_kind == "conditional" or self.runtime_provider != "mindspore"

    def supports_soc(self, soc: str) -> bool:
        """Return whether the profile explicitly targets the given SoC."""

        expected = _normalize_soc(soc)
        return any(_normalize_soc(item.get("soc")) == expected for item in self.board_targets)

    def board_for_soc(self, soc: str) -> Optional[Mapping[str, str]]:
        """Return the declared target record for a SoC, if present."""

        expected = _normalize_soc(soc)
        for item in self.board_targets:
            if _normalize_soc(item.get("soc")) == expected:
                return item
        return None

    def status_for_soc(self, soc: str) -> str:
        """Return the per-board status, falling back to the aggregate status."""

        expected = _normalize_soc(soc)
        item = self.validation.get(expected)
        if isinstance(item, Mapping) and isinstance(item.get("status"), str):
            return str(item["status"])
        # Accept case-insensitive SoC lookups for callers reading npu-smi.
        for key, value in self.validation.items():
            if _normalize_soc(key) == expected and isinstance(value, Mapping):
                status = value.get("status")
                if isinstance(status, str):
                    return status
        return self.status

    def activation_status_for_soc(self, soc: str) -> str:
        """Return the status used by a board-side admission check."""

        if self.is_conditional:
            return "blocked"
        return self.status_for_soc(soc)

    def quality_admission_for_soc(self, soc: Optional[str] = None) -> Tuple[bool, str]:
        """Check the human quality gate for an aggregate or board row.

        ``experimental_dirty_base`` deliberately does not call this method;
        it is an explicitly reversible technical experiment.  The method is
        for the stronger ``admitted`` state and is also used by recovery
        paths that receive a profile object instead of re-running the parser.
        """

        if soc:
            entry = self.validation_for_soc(soc)
            if not isinstance(entry, Mapping):
                return False, "board validation record is missing"
            return quality_admission_check(
                entry.get("quality"),
                languages=self.languages,
            )
        return quality_admission_check(self.quality, languages=self.languages)

    # Descriptive alias used by service/controller callers.
    admission_quality_for_soc = quality_admission_for_soc

    def validation_for_soc(self, soc: str) -> Optional[Mapping[str, Any]]:
        """Return a validation row using case-insensitive SoC matching."""

        expected = _normalize_soc(soc)
        for key, value in self.validation.items():
            if _normalize_soc(key) == expected and isinstance(value, Mapping):
                return value
        return None

    def artifact(self, name: str) -> ArtifactSpec:
        for item in self.artifacts:
            if item.name == name:
                return item
        raise ProfileError("profile %s has no artifact %s" % (self.id, name))

    def to_public_dict(self) -> Dict[str, Any]:
        """Return only metadata appropriate for an unauthenticated browser."""

        board_targets = [dict(item) for item in self.board_targets]
        validation: Dict[str, Dict[str, Any]] = {}
        for soc, item in self.validation.items():
            if isinstance(item, Mapping):
                # Do not expose report paths or arbitrary provenance to the
                # unauthenticated browser; status/reason are sufficient for
                # model selection and troubleshooting.
                validation[str(soc)] = {
                    "status": str(item.get("status", self.status)),
                    "reason": str(item.get("reason", "No board-specific result recorded.")),
                }
        quality_public: Dict[str, Any] = {}
        if isinstance(self.quality, Mapping):
            if isinstance(self.quality.get("reviewed"), bool):
                quality_public["reviewed"] = self.quality["reviewed"]
            languages = self.quality.get("languages")
            if isinstance(languages, Mapping):
                quality_public["languages"] = {
                    str(key): str(value) for key, value in languages.items()
                    if isinstance(key, str) and isinstance(value, (str, int, float, bool))
                }
        performance_public: Dict[str, Any] = {}
        if isinstance(self.performance, Mapping):
            for key in ("first_token_ms", "total_ms", "tokens_per_second", "status"):
                value = self.performance.get(key)
                if isinstance(value, (str, int, float, bool, Mapping, list)):
                    performance_public[key] = value
        quality_status = "质量待审核"
        if isinstance(self.quality, Mapping):
            languages = self.quality.get("languages")
            language_values = []
            if isinstance(languages, Mapping):
                language_values = [str(value).strip().lower() for value in languages.values()]
            reviewed = self.quality.get("reviewed")
            if language_values and all(value in {"failed", "unavailable"} for value in language_values):
                quality_status = "质量未通过"
            elif reviewed is True and language_values and all(value in {"passed", "pass", "ok"} for value in language_values):
                quality_status = "质量已审核"
            elif reviewed is True and any(value == "failed" for value in language_values):
                quality_status = "质量未通过"
        aggregate_quality_ok, aggregate_quality_reason = (True, "")
        if self.status == "admitted":
            aggregate_quality_ok, aggregate_quality_reason = self.quality_admission_for_soc()
        blocked_reason = self.admission_reason if self.status in {"blocked", "not-run"} else ""
        if self.status == "admitted" and not aggregate_quality_ok:
            blocked_reason = aggregate_quality_reason
        # A partial gate (for example artifact_verified) is evidence, not an
        # activation permission.  The controller only accepts these terminal
        # statuses after all technical gates have been completed.
        activation_statuses = {"experimental_dirty_base", "admitted"}
        switchable = (
            not self.is_conditional
            and self.status in activation_statuses
            and (self.status != "admitted" or aggregate_quality_ok)
        )
        switchable_by_soc: Dict[str, bool] = {}
        quality_by_soc: Dict[str, Dict[str, Any]] = {}
        for soc, item in validation.items():
            if not isinstance(item, Mapping):
                continue
            row_status = str(item.get("status", self.status))
            row_allowed = not self.is_conditional and row_status in activation_statuses
            row_quality_ok, row_quality_reason = (True, "")
            if row_status == "admitted":
                row_quality_ok, row_quality_reason = self.quality_admission_for_soc(str(soc))
                row_allowed = row_allowed and row_quality_ok
            switchable_by_soc[str(soc)] = row_allowed
            quality_by_soc[str(soc)] = {
                "required": row_status == "admitted",
                "approved": row_quality_ok if row_status == "admitted" else None,
                "reason": row_quality_reason if row_status == "admitted" and not row_quality_ok else "",
            }
        return {
            "id": self.id,
            "display_name": self.display_name,
            "model_id": self.model_id,
            "license": self.license_name,
            "revision": self.revision,
            "revision_pinned": self.revision_pinned,
            "board_soc": self.board_soc,
            "board_tier": self.board_tier,
            "context_length": self.context_length,
            "default_max_tokens": self.default_max_tokens,
            "max_tokens": self.max_tokens,
            "status": self.status,
            "admitted": bool(self.admission_eligible and aggregate_quality_ok),
            "admission_reason": self.admission_reason,
            "candidate_kind": self.candidate_kind,
            "architecture": self.architecture,
            "languages": list(self.languages),
            "weight_format": self.weight_format,
            "board_targets": board_targets,
            "validation": validation,
            "quality": quality_public,
            "quality_status": quality_status,
            "quality_approved": aggregate_quality_ok if self.status == "admitted" else None,
            "quality_approval_reason": aggregate_quality_reason if self.status == "admitted" and not aggregate_quality_ok else "",
            "quality_approved_by_soc": quality_by_soc,
            "blocked_reason": blocked_reason,
            "performance": performance_public,
            "switchable": switchable,
            "switchable_by_soc": switchable_by_soc,
            "conditional": self.is_conditional,
        }


@dataclass(frozen=True)
class ProfileRegistry:
    profiles: Tuple[ChatModelProfile, ...]

    def get(self, profile_id: str) -> ChatModelProfile:
        if not isinstance(profile_id, str) or not _PROFILE_ID_RE.fullmatch(profile_id):
            raise ProfileError("invalid profile id")
        for profile in self.profiles:
            if profile.id == profile_id:
                return profile
        raise ProfileError("unknown profile: %s" % profile_id)

    get_profile = get

    def public_profiles(self) -> List[Dict[str, Any]]:
        return [profile.to_public_dict() for profile in self.profiles]

    def for_soc(self, soc: str) -> Tuple[ChatModelProfile, ...]:
        """Return profiles explicitly targeting one Ascend SoC."""

        return tuple(profile for profile in self.profiles if profile.supports_soc(soc))

    def executable(self) -> Tuple[ChatModelProfile, ...]:
        """Return native MindSpore entries; status gates are checked by modelctl."""

        return tuple(profile for profile in self.profiles if not profile.is_conditional)

    def __iter__(self):
        return iter(self.profiles)

    def __len__(self) -> int:
        return len(self.profiles)


def load_profiles(path: Union[str, Path] = DEFAULT_REGISTRY_PATH) -> ProfileRegistry:
    """Load and strictly validate a profile registry from JSON."""

    registry_path = _resolve_explicit_path(path, BASE_DIR, allow_missing=False)
    if registry_path.is_symlink() or not registry_path.is_file():
        raise ProfileError("registry must be a regular file")
    try:
        document = json.loads(
            registry_path.read_text(encoding="utf-8"), object_pairs_hook=_duplicate_key_object
        )
    except ProfileError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProfileError("invalid profile registry: %s" % exc) from exc
    document = _object(document, "registry")
    _exact_keys(document, ("schema_version", "profiles"), "registry")
    schema_version = document["schema_version"]
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version not in {1, 2}:
        raise ProfileError("unsupported registry schema_version")
    raw_profiles = document["profiles"]
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise ProfileError("registry.profiles must be a non-empty array")
    parsed = tuple(
        ChatModelProfile.from_mapping(
            _object(item, "profiles[%d]" % i), i, schema_version=int(schema_version)
        )
        for i, item in enumerate(raw_profiles)
    )
    ids = [item.id for item in parsed]
    if len(ids) != len(set(ids)):
        raise ProfileError("profile ids must be unique")
    return ProfileRegistry(parsed)


def load_profile_registry(path: Union[str, Path] = DEFAULT_REGISTRY_PATH) -> ProfileRegistry:
    """Alias retained for callers that use the longer name."""

    return load_profiles(path)


def get_profile(profile_id: str, path: Union[str, Path] = DEFAULT_REGISTRY_PATH) -> ChatModelProfile:
    return load_profiles(path).get(profile_id)


def public_profiles(path: Union[str, Path] = DEFAULT_REGISTRY_PATH) -> List[Dict[str, Any]]:
    return load_profiles(path).public_profiles()


def profile_to_public_dict(profile: ChatModelProfile) -> Dict[str, Any]:
    """Serialize one validated profile for an unauthenticated status view."""

    if not isinstance(profile, ChatModelProfile):
        raise ProfileError("profile must be a ChatModelProfile")
    return profile.to_public_dict()


@dataclass(frozen=True)
class ActiveModelState:
    profile_id: str
    status: str
    worker_pid: Optional[int]
    cache_cleared: bool
    updated_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "profile_id": self.profile_id,
            "status": self.status,
            "worker_pid": self.worker_pid,
            "cache_cleared": self.cache_cleared,
            "updated_at": self.updated_at,
        }


def _state_path(path: Union[str, Path]) -> Path:
    return _resolve_explicit_path(path, BASE_DIR, allow_missing=True)


def _parse_state(document: Mapping[str, Any]) -> ActiveModelState:
    _exact_keys(document, ("schema_version", "profile_id", "status", "worker_pid", "cache_cleared", "updated_at"), "active state")
    if document["schema_version"] != 1:
        raise ProfileError("unsupported active state schema_version")
    profile_id = _string(document["profile_id"], "active state.profile_id", pattern=_PROFILE_ID_RE)
    status = _string(document["status"], "active state.status")
    worker_pid = document["worker_pid"]
    if worker_pid is not None:
        worker_pid = _integer(worker_pid, "active state.worker_pid", minimum=1)
    cache_cleared = document["cache_cleared"]
    if not isinstance(cache_cleared, bool):
        raise ProfileError("active state.cache_cleared must be boolean")
    updated_at = _string(document["updated_at"], "active state.updated_at")
    return ActiveModelState(profile_id, status, worker_pid, cache_cleared, updated_at)


def read_active_state(
    path: Union[str, Path] = DEFAULT_STATE_PATH,
    *,
    registry: Optional[ProfileRegistry] = None,
) -> Optional[ActiveModelState]:
    state_path = _state_path(path)
    if not state_path.exists():
        return None
    if state_path.is_symlink() or not state_path.is_file():
        raise ProfileError("active state must be a regular file")
    try:
        document = json.loads(
            state_path.read_text(encoding="utf-8"), object_pairs_hook=_duplicate_key_object
        )
    except ProfileError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProfileError("invalid active state: %s" % exc) from exc
    state = _parse_state(_object(document, "active state"))
    if registry is not None:
        registry.get(state.profile_id)
    return state


get_active_state = read_active_state


def write_active_state(
    path: Union[str, Path] = DEFAULT_STATE_PATH,
    profile_id: Optional[str] = None,
    *,
    status: str = "starting",
    worker_pid: Optional[int] = None,
    cache_cleared: bool = False,
    registry: Optional[ProfileRegistry] = None,
) -> ActiveModelState:
    """Atomically write a validated active-worker state file."""

    if profile_id is None:
        raise ProfileError("profile_id is required")
    profile_id = _string(profile_id, "profile_id", pattern=_PROFILE_ID_RE)
    if registry is not None:
        registry.get(profile_id)
    if not isinstance(status, str) or not status.strip():
        raise ProfileError("status must be a non-empty string")
    if worker_pid is not None:
        worker_pid = _integer(worker_pid, "worker_pid", minimum=1)
    if not isinstance(cache_cleared, bool):
        raise ProfileError("cache_cleared must be boolean")
    state = ActiveModelState(
        profile_id=profile_id,
        status=status.strip(),
        worker_pid=worker_pid,
        cache_cleared=cache_cleared,
        updated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )
    state_path = _state_path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if state_path.exists() and state_path.is_symlink():
        raise ProfileError("active state target must not be a symlink")
    payload = json.dumps(state.to_dict(), ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".%s." % state_path.name, suffix=".part", dir=str(state_path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, state_path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    return state


def clear_active_state(path: Union[str, Path] = DEFAULT_STATE_PATH) -> bool:
    """Remove only the exact active-state file; return whether it existed."""

    state_path = _state_path(path)
    if state_path.is_symlink():
        raise ProfileError("active state target must not be a symlink")
    if not state_path.exists():
        return False
    if not state_path.is_file():
        raise ProfileError("active state must be a regular file")
    state_path.unlink()
    return True


def resolve_artifact_path(
    profile: ChatModelProfile,
    artifact_name: str,
    root: Union[str, Path] = BASE_DIR,
) -> Path:
    """Resolve one profile artifact below a caller-selected model root."""

    artifact = profile.artifact(artifact_name)
    return resolve_safe_path(Path(profile.cache_dir) / artifact.filename, root)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Small read-only CLI used by operators and smoke tests.

    Mutating lifecycle operations intentionally live in ``case9-modelctl.sh``;
    this module only exposes validated registry and active-worker metadata.
    """

    parser = argparse.ArgumentParser(description="Inspect Case9 chat model profiles")
    parser.add_argument("command", choices=("list", "status"))
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        registry = load_profiles(args.registry)
        if args.command == "list":
            # This command is consumed by shell controllers and non-UTF-8
            # Windows subprocesses.  Keep the wire format ASCII-safe; the
            # browser/API serializers still emit human-readable UTF-8.
            print(json.dumps({"profiles": registry.public_profiles()}, ensure_ascii=True, sort_keys=True))
            return 0
        state = read_active_state(args.state, registry=registry)
        payload: Dict[str, Any] = {"active": state.to_dict() if state else None}
        if state is not None:
            payload["profile"] = registry.get(state.profile_id).to_public_dict()
        print(json.dumps(payload, ensure_ascii=True, sort_keys=True))
        return 0
    except (OSError, ProfileError) as exc:
        parser.error(str(exc))
        return 2


__all__ = [
    "ALLOWED_STATUSES",
    "ActiveModelState",
    "ArtifactSpec",
    "BASE_DIR",
    "ChatModelProfile",
    "DEFAULT_REGISTRY_PATH",
    "DEFAULT_STATE_PATH",
    "ProfileError",
    "ProfileRegistry",
    "clear_active_state",
    "get_profile",
    "load_profile_registry",
    "load_profiles",
    "public_profiles",
    "profile_to_public_dict",
    "quality_admission_check",
    "read_active_state",
    "get_active_state",
    "resolve_artifact_path",
    "resolve_safe_path",
    "safe_relative_path",
    "write_active_state",
]


if __name__ == "__main__":  # pragma: no cover - exercised by board operators
    raise SystemExit(main())
