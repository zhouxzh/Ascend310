#!/usr/bin/env python3
"""Read-only matrix and gate report for MindSpore chat candidates.

This helper only reads the profile registry and existing evidence. It never
starts a worker, connects to a service, installs packages, or edits a profile.
The optional output is an explicit report file; --dry-run performs no report
scan and no write.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
DEFAULT_REGISTRY = REPO_DIR / "configs" / "chat_model_profiles.json"
DEFAULT_REPORTS_ROOT = REPO_DIR / "reports" / "mindspore-chat"
MAX_REPORT_FILES = 2000
MAX_JSON_BYTES = 8 * 1024 * 1024
FORMAL_PORTS = frozenset((8080, 7861, 7865))

CANONICAL_GATES: Tuple[str, ...] = (
    "G0_environment",
    "G1_artifacts",
    "G2_load",
    "G3_api",
    "G4_long_output",
    "G5_stability",
    "G6_quality",
    "G7_performance",
    "G8_candidate_chain",
)
TECHNICAL_GATES: Tuple[str, ...] = tuple(
    gate for gate in CANONICAL_GATES if gate != "G6_quality"
) + ("G6_quality_machine",)
ACTIVATION_STATUSES = frozenset(("experimental_dirty_base", "admitted"))
ACCEPTANCE_GATE_MAP: Mapping[str, Tuple[str, ...]] = {
    "G2_load": ("health", "models"),
    "G3_api": ("json", "sse", "errors", "protocol"),
    "G4_long_output": ("long_output",),
    "G5_stability": ("stability",),
    "G6_quality": ("quality_machine",),
    "G7_performance": ("performance",),
}

BOARD_ALIASES: Mapping[str, Dict[str, str]] = {
    # Canonical active address for the existing B4/8T board.  The .11.14 and
    # .178 entries remain compatibility selectors for archived reports and
    # always normalize to the current .90 host.
    "board8t": {"key": "board8t", "host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
    "8t": {"key": "board8t", "host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
    "ascend310b4": {"key": "board8t", "host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
    "192.168.1.90": {"key": "board8t", "host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
    "192.168.11.14": {"key": "board8t", "host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
    "192.168.8.178": {"key": "board8t", "host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
    "board20t": {"key": "board20t", "host": "192.168.1.95", "soc": "Ascend310B1", "tier": "20T"},
    "20t": {"key": "board20t", "host": "192.168.1.95", "soc": "Ascend310B1", "tier": "20T"},
    "ascend310b1": {"key": "board20t", "host": "192.168.1.95", "soc": "Ascend310B1", "tier": "20T"},
    "192.168.1.95": {"key": "board20t", "host": "192.168.1.95", "soc": "Ascend310B1", "tier": "20T"},
}
SIDECAR_NAMES: Mapping[str, Tuple[str, ...]] = {
    "G0_environment": ("environment.json", "environment-report.json", "preflight.json"),
    "G1_artifacts": (
        "artifact-manifest.json",
        "artifact-verification.json",
        "artifacts.json",
        "SHA256SUMS.txt",
        "sha256sums.txt",
    ),
    "G8_candidate_chain": ("chain-summary.json", "chain-summary-final.json", "candidate-chain.json", "switch-report.json"),
}
PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
_QUALITY_CHECK = None


class MatrixError(ValueError):
    """Raised for malformed or unsafe matrix input."""


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _reject_symlink_components(path: Path) -> None:
    absolute = _absolute(path)
    current = Path(absolute.anchor)
    try:
        parts = absolute.relative_to(current).parts
    except ValueError as exc:
        raise MatrixError("invalid path anchor: %s" % path) from exc
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise MatrixError("symlink path component is not allowed: %s" % current)


def _regular_file(value: Any, name: str) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise MatrixError("%s must be a non-empty path" % name)
    path = _absolute(Path(value).expanduser())
    _reject_symlink_components(path)
    if path.is_symlink() or not path.is_file():
        raise MatrixError("%s must be a regular file: %s" % (name, path))
    return path.resolve(strict=True)


def _regular_directory(value: Any, name: str, allow_missing: bool = False) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise MatrixError("%s must be a non-empty path" % name)
    path = _absolute(Path(value).expanduser())
    _reject_symlink_components(path)
    if not path.exists():
        if allow_missing:
            return path
        raise MatrixError("%s does not exist: %s" % (name, path))
    if path.is_symlink() or not path.is_dir():
        raise MatrixError("%s must be a regular directory: %s" % (name, path))
    return path.resolve(strict=True)


def _load_json(path: Path) -> Any:
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            raise MatrixError("JSON file exceeds bounded size: %s" % path)
        return json.loads(path.read_text(encoding="utf-8"))
    except MatrixError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MatrixError("could not read JSON %s: %s" % (path, exc)) from exc


def _profile_selection(values: Optional[Sequence[str]]) -> Optional[Set[str]]:
    if not values:
        return None
    selected: Set[str] = set()
    for value in values:
        for profile in str(value).split(","):
            profile = profile.strip()
            if not PROFILE_ID_RE.fullmatch(profile):
                raise MatrixError("invalid profile id: %s" % profile)
            selected.add(profile)
    return selected


def normalize_board(value: str) -> Dict[str, str]:
    key = str(value or "").strip().lower()
    if key == "both":
        raise MatrixError("both is valid only as a --board selection")
    try:
        return dict(BOARD_ALIASES[key])
    except KeyError as exc:
        raise MatrixError("unknown board: %s" % value) from exc


def select_boards(value: str) -> List[Dict[str, str]]:
    raw = str(value or "both").strip().lower()
    if raw == "both":
        return [dict(BOARD_ALIASES["board8t"]), dict(BOARD_ALIASES["board20t"])]
    result: Dict[str, Dict[str, str]] = {}
    for item in raw.split(","):
        if item.strip():
            board = normalize_board(item)
            result[board["key"]] = board
    if not result:
        raise MatrixError("--board must not be empty")
    return [result[key] for key in ("board8t", "board20t") if key in result]


def _load_registry(path: Path) -> List[Dict[str, Any]]:
    document = _load_json(_regular_file(path, "registry"))
    if not isinstance(document, Mapping) or not isinstance(document.get("profiles"), list):
        raise MatrixError("registry must contain a profiles array")
    schema_version = document.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version not in {1, 2}:
        raise MatrixError("registry schema_version must be 1 or 2")
    if schema_version == 2:
        # The matrix must not implement a second, weaker interpretation of
        # the profile registry.  Reuse the canonical loader so required v2
        # metadata, dual-SoC rows, per-board validation records, artifact
        # hashes, and duplicate-key checks all fail closed before any report
        # can influence the matrix.
        loader_path = REPO_DIR / "case9_model_profiles.py"
        if not loader_path.is_file() or loader_path.is_symlink():
            raise MatrixError("schema v2 validator is unavailable: %s" % loader_path)
        spec = importlib.util.spec_from_file_location(
            "case9_model_profiles_matrix_validator", loader_path
        )
        if spec is None or spec.loader is None:
            raise MatrixError("could not load schema v2 validator: %s" % loader_path)
        module = importlib.util.module_from_spec(spec)
        try:
            # dataclasses resolves postponed annotations through
            # ``sys.modules[cls.__module__]`` while the module is executing.
            # Register the temporary validator before exec_module, then remove
            # it below so repeated scans do not leak a module under a global
            # name.
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            module.load_profiles(path)
        except Exception as exc:
            raise MatrixError("invalid schema v2 registry: %s" % exc) from exc
        finally:
            sys.modules.pop(spec.name, None)
    profiles: List[Dict[str, Any]] = []
    seen: Set[str] = set()
    for index, raw in enumerate(document["profiles"]):
        if not isinstance(raw, Mapping):
            raise MatrixError("registry profile %d is not an object" % index)
        profile_id = raw.get("id")
        if not isinstance(profile_id, str) or not PROFILE_ID_RE.fullmatch(profile_id):
            raise MatrixError("registry profile %d has invalid id" % index)
        if profile_id in seen:
            raise MatrixError("duplicate profile id: %s" % profile_id)
        seen.add(profile_id)
        candidate_kind = str(raw.get("candidate_kind", "native_mindspore"))
        if candidate_kind not in {"native_mindspore", "conditional"}:
            raise MatrixError("profile %s has invalid candidate_kind" % profile_id)
        rows: List[Dict[str, str]] = []
        board = raw.get("board")
        if isinstance(board, Mapping):
            rows.append({
                "host": str(board.get("host", "")),
                "soc": str(board.get("soc", "")),
                "tier": str(board.get("tier", "")),
            })
        targets = raw.get("board_targets")
        if isinstance(targets, list):
            for target in targets:
                if isinstance(target, Mapping):
                    rows.append({
                        "host": str(target.get("host", "")),
                        "soc": str(target.get("soc", "")),
                        "tier": str(target.get("tier", "")),
                    })
        profiles.append({
            "id": profile_id,
            "display_name": str(raw.get("display_name", profile_id)),
            "model_id": str(raw.get("model_id", "")),
            "status": str(raw.get("status", "not-run")),
            "candidate_kind": candidate_kind,
            "revision": raw.get("revision"),
            "admission": raw.get("admission") if isinstance(raw.get("admission"), Mapping) else {},
            "languages": raw.get("languages") if isinstance(raw.get("languages"), list) else [],
            "quality": raw.get("quality") if isinstance(raw.get("quality"), Mapping) else {},
            "board_rows": rows,
            "validation": raw.get("validation") if isinstance(raw.get("validation"), Mapping) else {},
        })
    return profiles


def _profile_boards(profile: Mapping[str, Any], selected: Sequence[Mapping[str, str]]) -> List[Dict[str, str]]:
    # Always emit a row for every selected board. A model that is explicitly
    # 20T-only (for example MiniCPM3) must remain visible as an 8T not-run
    # combination instead of disappearing from the matrix.
    return [dict(board) for board in selected]


def _is_targeted(profile: Mapping[str, Any], board: Mapping[str, str]) -> bool:
    rows = profile.get("board_rows")
    if not isinstance(rows, list) or not rows:
        return True
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        if (
            str(row.get("soc", "")).lower() == board["soc"].lower()
            or str(row.get("tier", "")).lower() == board["tier"].lower()
            or str(row.get("host", "")) == board["host"]
        ):
            return True
    return False


def _identity_key(value: Any) -> Optional[str]:
    """Classify one explicit board identity token, if it is recognized."""

    text = str(value or "").strip().lower()
    if not text:
        return None
    if re.search(r"(?:ascend)?310b4\b", text):
        return "board8t"
    if re.search(r"(?:ascend)?310b1\b", text):
        return "board20t"
    if text in BOARD_ALIASES:
        return BOARD_ALIASES[text]["key"]
    if re.fullmatch(r"[0-9]+t", text):
        if text == "8t":
            return "board8t"
        if text == "20t":
            return "board20t"
    return None


def _board_from_mapping(value: Mapping[str, Any], fallback: str = "") -> Optional[Dict[str, str]]:
    """Classify a mapping only when all explicit identity tokens agree.

    A report containing (for example) B4 in ``soc`` and B1 in ``tier`` is
    ambiguous and must not be assigned to either board.  ``fallback`` is kept
    for compatibility with callers that use this helper directly; report
    identity extraction below deliberately never passes a path hint.
    """

    tokens: Set[str] = set()
    for key in ("soc", "npu_model", "board_soc", "target_soc"):
        value_token = value.get(key)
        if value_token not in (None, ""):
            identity = _identity_key(value_token)
            if identity:
                tokens.add(identity)
    for key in ("host", "board_host", "target_host"):
        value_token = value.get(key)
        if value_token not in (None, ""):
            identity = _identity_key(value_token)
            if identity:
                tokens.add(identity)
    for key in ("tier", "board_tier"):
        value_token = value.get(key)
        if value_token not in (None, ""):
            identity = _identity_key(value_token)
            if identity:
                tokens.add(identity)
    if len(tokens) == 1:
        return dict(BOARD_ALIASES[next(iter(tokens))])
    if len(tokens) > 1:
        return None
    text = fallback.lower()
    if (
        "board8t" in text
        or "ascend310b4" in text
        or "192.168.11.14" in text
        or "192.168.1.90" in text
        or "192.168.8.178" in text
    ):
        return dict(BOARD_ALIASES["board8t"])
    if "board20t" in text or "ascend310b1" in text or "192.168.1.95" in text:
        return dict(BOARD_ALIASES["board20t"])
    return None


def _identity_details(document: Mapping[str, Any], path: Path) -> Dict[str, Any]:
    """Extract explicit profile/board identity without using directory names."""

    profile_values: List[str] = []

    def add_profile(value: Any) -> None:
        if isinstance(value, str) and value.strip():
            profile_values.append(value.strip())
        elif isinstance(value, Mapping):
            nested = value.get("id") or value.get("profile_id")
            if isinstance(nested, str) and nested.strip():
                profile_values.append(nested.strip())

    add_profile(document.get("profile_id"))
    add_profile(document.get("profile"))
    checks = document.get("checks")
    if isinstance(checks, Mapping):
        add_profile(checks.get("profile"))
    unique_profiles = set(profile_values)
    profile_id = next(iter(unique_profiles)) if len(unique_profiles) == 1 else None

    mappings: List[Mapping[str, Any]] = [document]
    profile = document.get("profile")
    if isinstance(profile, Mapping):
        mappings.append(profile)
        if isinstance(profile.get("board"), Mapping):
            mappings.append(profile["board"])
    board = document.get("board")
    if isinstance(board, Mapping):
        mappings.append(board)
    for key in ("environment", "device", "hardware"):
        section = document.get(key)
        if isinstance(section, Mapping):
            mappings.append(section)
    if isinstance(checks, Mapping):
        for key in ("profile", "environment", "device", "hardware", "npu"):
            section = checks.get(key)
            if isinstance(section, Mapping):
                mappings.append(section)
    board_values = [found for item in mappings if (found := _board_from_mapping(item)) is not None]
    board_keys = {item["key"] for item in board_values}
    board_identity = board_values[0] if len(board_keys) == 1 and board_values else None
    return {
        "profile_id": profile_id,
        "profile_present": bool(profile_values),
        "profile_conflict": len(unique_profiles) > 1,
        "board": board_identity,
        "board_present": bool(board_values),
        "board_conflict": len(board_keys) > 1,
        "path": str(path),
    }


def _identity(document: Mapping[str, Any], path: Path) -> Tuple[Optional[str], Optional[Dict[str, str]]]:
    details = _identity_details(document, path)
    return details["profile_id"], details["board"]


def _status(value: Any) -> str:
    if isinstance(value, bool):
        return "passed" if value else "failed"
    if isinstance(value, str):
        value = value.strip().lower().replace("_", "-")
        if value in {"passed", "pass", "ok", "success", "verified", "approved", "reviewed", "true"}:
            return "passed"
        if value in {"failed", "fail", "error", "false"}:
            return "failed"
        if value in {"blocked", "disabled"}:
            return "blocked"
        if value in {"pending", "running", "in-progress"}:
            return "pending"
        if value in {"not-run", "notrun", "skipped", "unknown", "unavailable"}:
            return "not-run"
    return "not-run"


def _sidecar_status(document: Mapping[str, Any], gate: str) -> str:
    aliases = (gate, gate.replace("G0_", "").replace("G1_", "").replace("G8_", ""), "status")
    for key in aliases:
        if key not in document:
            continue
        value = document[key]
        if isinstance(value, Mapping):
            for child in ("status", "passed", "verified", "ok"):
                if child in value:
                    return _status(value[child])
        else:
            return _status(value)
    if gate == "G0_environment" and "environment_verified" in document:
        return _status(document["environment_verified"])
    if gate == "G1_artifacts" and "artifact_verified" in document:
        return _status(document["artifact_verified"])
    return "not-run"


def _sidecar_identity_ok(
    document: Mapping[str, Any], profile_id: str, board: Mapping[str, str]
) -> Tuple[bool, str]:
    """Require explicit profile and board identity on G0/G1 sidecars."""

    details = _identity_details(document, Path("sidecar.json"))
    if not details["profile_present"]:
        return False, "missing_profile_identity"
    if details["profile_conflict"] or details["profile_id"] != profile_id:
        return False, "profile_identity_mismatch"
    if not details["board_present"]:
        return False, "missing_board_identity"
    if details["board_conflict"]:
        return False, "conflicting_board_identity"
    if details["board"]["key"] != board["key"]:
        return False, "board_identity_mismatch"
    return True, ""


def _combined_gate(gates: Mapping[str, Any], names: Iterable[str]) -> str:
    values = [
        _status(gates.get(name))
        if isinstance(gates.get(name), (str, bool))
        else gates.get(name)
        for name in names
    ]
    if not values or any(value is None for value in values):
        return "not-run"
    if all(value == "passed" for value in values):
        return "passed"
    if any(value == "failed" for value in values):
        return "failed"
    return "not-run"


def _quality_human(document: Mapping[str, Any]) -> str:
    quality = document.get("quality")
    if not isinstance(quality, Mapping):
        return "not-run"
    value = quality.get("human_review")
    if isinstance(value, Mapping):
        value = value.get("status") or value.get("result")
    return _status(value)


def _profile_quality_admission(
    profile: Mapping[str, Any],
    board: Mapping[str, str],
    registry_status: str,
) -> Tuple[bool, str]:
    """Check the registry's manual quality gate for an admitted row.

    Matrix reports are evidence, not an authority to promote a profile.  For
    an ``admitted`` registry row, require both the operator eligibility bit
    and the same canonical human-quality evidence used by the service.  A
    dirty-base row remains eligible for reversible experiments without this
    stronger approval.
    """

    if registry_status != "admitted":
        return True, ""
    admission = profile.get("admission")
    if not isinstance(admission, Mapping) or admission.get("eligible") is not True:
        return False, "admitted profile must set admission.eligible=true"
    validation = profile.get("validation")
    entry = validation.get(board.get("soc")) if isinstance(validation, Mapping) else None
    if not isinstance(entry, Mapping):
        # The strict v2 parser normally prevents this, but the matrix also
        # handles legacy fixtures and must fail closed for them.
        expected_soc = str(board.get("soc") or "").strip().lower().replace("ascend", "", 1)
        for key, candidate in (validation.items() if isinstance(validation, Mapping) else ()):
            candidate_soc = str(key or "").strip().lower().replace("ascend", "", 1)
            if candidate_soc == expected_soc and isinstance(candidate, Mapping):
                entry = candidate
                break
    quality = entry.get("quality") if isinstance(entry, Mapping) else None
    if quality is None:
        quality = profile.get("quality")
    languages = profile.get("languages")
    if not isinstance(languages, list):
        languages = []
    try:
        # Import lazily from the repository's exact validator path.  When the
        # script is invoked by an absolute path from an arbitrary working
        # directory, Python's import path contains ``scripts/`` but not the
        # repository root; falling back to an ambient module could therefore
        # silently apply a different admission policy.
        global _QUALITY_CHECK
        if _QUALITY_CHECK is None:
            validator_path = REPO_DIR / "case9_model_profiles.py"
            if not validator_path.is_file() or validator_path.is_symlink():
                return False, "canonical quality validator is unavailable"
            spec = importlib.util.spec_from_file_location(
                "case9_model_profiles_quality_validator", validator_path
            )
            if spec is None or spec.loader is None:
                return False, "canonical quality validator cannot be loaded"
            module = importlib.util.module_from_spec(spec)
            # dataclasses resolves postponed annotations via sys.modules while
            # the module is executing; keep this private module cached for
            # subsequent rows rather than importing ambient code.
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            _QUALITY_CHECK = module.quality_admission_check
        return _QUALITY_CHECK(quality, languages=languages)
    except Exception as exc:
        return False, "quality admission check failed: %s" % exc


def _acceptance_gates(document: Mapping[str, Any]) -> Dict[str, Any]:
    raw = document.get("gates")
    gates = raw if isinstance(raw, Mapping) else {}
    result: Dict[str, Any] = {
        key: _combined_gate(gates, names)
        for key, names in ACCEPTANCE_GATE_MAP.items()
    }
    result["G6_quality"] = {
        "machine": _combined_gate(gates, ("quality_machine",)),
        "human": _quality_human(document),
    }
    return result


def _formal_ports(document: Mapping[str, Any]) -> List[int]:
    ports: Set[int] = set()
    service = document.get("service")
    if isinstance(service, Mapping):
        value = service.get("port")
        if isinstance(value, int) and not isinstance(value, bool):
            ports.add(value)
        match = re.search(r":(\d+)(?:/|$)", str(service.get("base_url", "")))
        if match:
            ports.add(int(match.group(1)))
    return sorted(ports.intersection(FORMAL_PORTS))


def _json_reports(root: Path) -> Tuple[List[Tuple[Path, Mapping[str, Any]]], List[Dict[str, str]]]:
    if not root.exists():
        return [], []
    files = list(root.rglob("acceptance.json"))
    if len(files) > MAX_REPORT_FILES:
        raise MatrixError("report root contains more than %d acceptance reports" % MAX_REPORT_FILES)
    reports: List[Tuple[Path, Mapping[str, Any]]] = []
    malformed: List[Dict[str, str]] = []
    for path in files:
        if path.is_symlink() or not path.is_file():
            continue
        _reject_symlink_components(path)
        try:
            document = _load_json(path)
        except MatrixError as exc:
            malformed.append({"path": str(path), "error": str(exc)})
            continue
        if isinstance(document, Mapping):
            reports.append((path, document))
        else:
            malformed.append({"path": str(path), "error": "acceptance report is not an object"})
    return reports, malformed


def _sidecar_index(root: Path) -> Dict[str, List[Path]]:
    index: Dict[str, List[Path]] = {}
    if not root.exists():
        return index
    wanted = {name.lower() for names in SIDECAR_NAMES.values() for name in names}
    for path in root.rglob("*"):
        if path.is_file() and not path.is_symlink() and path.name.lower() in wanted:
            index.setdefault(path.name.lower(), []).append(path)
    return index


def _nearby_sidecar(path: Path, root: Path, names: Sequence[str], index: Mapping[str, List[Path]]) -> Optional[Path]:
    root = root.resolve(strict=False)
    current = path.parent
    candidates: List[Path] = []
    while True:
        for name in names:
            candidate = current / name
            if candidate.is_file() and not candidate.is_symlink():
                candidates.append(candidate)
        if current == root or root not in current.parents:
            break
        current = current.parent
    # Deliberately do not fall back to an unrelated file elsewhere in root.
    # A same-named sidecar from another profile must never make this row pass.
    return sorted(set(candidates), key=lambda item: str(item).lower())[0] if candidates else None


def _report_key(path: Path, document: Mapping[str, Any]) -> Tuple[str, str, str]:
    return (
        str(document.get("recorded_at_utc") or document.get("checked_at") or ""),
        str(document.get("run_id") or ""),
        str(path).lower(),
    )


def _row(
    profile: Mapping[str, Any],
    board: Mapping[str, str],
    reports: Sequence[Tuple[Path, Mapping[str, Any]]],
    root: Path,
    sidecars: Mapping[str, List[Path]],
) -> Dict[str, Any]:
    matching: List[Tuple[Path, Mapping[str, Any]]] = []
    rejected: List[Dict[str, Any]] = []
    for path, document in reports:
        identity = _identity_details(document, path)
        report_profile = identity["profile_id"]
        report_board = identity["board"]
        # Directory names are deliberately not identity.  A copied or stale
        # acceptance.json must carry its own profile id before it can be
        # attributed to any registry row.
        if not identity["profile_present"]:
            rejected.append({
                "path": str(path),
                "reason": "missing_profile_identity",
            })
            continue
        if identity["profile_conflict"]:
            rejected.append({
                "path": str(path),
                "reason": "conflicting_profile_identity",
            })
            continue
        if report_profile and report_profile != profile["id"]:
            continue
        # A report that can be attributed to this profile but has no board
        # identity is unsafe to use for a per-board row.  In particular, do
        # not let one unscoped acceptance.json populate both the 8T and 20T
        # rows.  The identity may come from the report fields or from the
        # explicit board directory/path hint handled by _identity(); when
        # neither is present, retain the report as rejected evidence.
        if report_board is None:
            rejected.append({
                "path": str(path),
                "reason": "conflicting_board_identity" if identity["board_conflict"] else "missing_board_identity",
                "profile": report_profile,
            })
            continue
        if report_board and report_board["key"] != board["key"]:
            continue
        ports = _formal_ports(document)
        if ports:
            rejected.append({"path": str(path), "formal_ports": ports})
            continue
        matching.append((path, document))
    matching.sort(key=lambda item: _report_key(item[0], item[1]))
    selected_path, selected = matching[-1] if matching else (None, {})
    acceptance = _acceptance_gates(selected)
    sidecar_paths: Dict[str, str] = {}
    gates: Dict[str, Any] = {
        "G0_environment": "not-run",
        "G1_artifacts": "not-run",
        "G2_load": acceptance["G2_load"],
        "G3_api": acceptance["G3_api"],
        "G4_long_output": acceptance["G4_long_output"],
        "G5_stability": acceptance["G5_stability"],
        "G6_quality": acceptance["G6_quality"],
        "G7_performance": acceptance["G7_performance"],
        "G8_candidate_chain": "not-run",
    }
    if selected_path is not None:
        for gate in ("G0_environment", "G1_artifacts", "G8_candidate_chain"):
            sidecar = _nearby_sidecar(selected_path, root, SIDECAR_NAMES[gate], sidecars)
            if sidecar is None:
                continue
            sidecar_paths[gate] = str(sidecar)
            if sidecar.suffix.lower() == ".json":
                try:
                    sidecar_document = _load_json(sidecar)
                    if not isinstance(sidecar_document, Mapping):
                        raise MatrixError("sidecar JSON root is not an object")
                    if gate in {"G0_environment", "G1_artifacts"}:
                        identity_ok, identity_reason = _sidecar_identity_ok(
                            sidecar_document, profile["id"], board
                        )
                        if not identity_ok:
                            gates[gate] = "failed"
                            rejected.append({
                                "path": str(sidecar),
                                "reason": identity_reason,
                                "gate": gate,
                            })
                        else:
                            gates[gate] = _sidecar_status(sidecar_document, gate)
                    else:
                        gates[gate] = _sidecar_status(sidecar_document, gate)
                except MatrixError:
                    gates[gate] = "failed"
            elif gate == "G1_artifacts":
                gates[gate] = "pending"

    # G0/G1 are prerequisites, not merely independent labels.  Once either
    # preflight gate is absent or failed, no downstream result is evidence of
    # a run in this campaign.  Hide stale API/performance values and expose a
    # deterministic early-stop marker instead.
    stop_gate: Optional[str] = None
    if gates["G0_environment"] != "passed":
        stop_gate = "G0_environment"
    elif gates["G1_artifacts"] != "passed":
        stop_gate = "G1_artifacts"
    if stop_gate is not None:
        downstream = ("G2_load", "G3_api", "G4_long_output", "G5_stability", "G6_quality", "G7_performance", "G8_candidate_chain")
        for gate in downstream:
            gates[gate] = "not-run" if gate != "G6_quality" else {"machine": "not-run", "human": "not-run"}
    quality = gates["G6_quality"]
    quality_machine = quality.get("machine") if isinstance(quality, Mapping) else quality
    technical_values = {
        **{gate: gates[gate] for gate in CANONICAL_GATES if gate != "G6_quality"},
        "G6_quality_machine": quality_machine,
    }
    technical_pass = all(technical_values[gate] == "passed" for gate in TECHNICAL_GATES)
    targeted = _is_targeted(profile, board)
    validation = profile.get("validation")
    board_validation = validation.get(board["soc"]) if isinstance(validation, Mapping) else None
    board_validation = board_validation if isinstance(board_validation, Mapping) else {}
    aggregate_status = str(profile.get("status") or "not-run").strip().lower()
    board_status = str(board_validation.get("status") or "").strip().lower()
    # Schema v2 deliberately makes the per-SoC validation row authoritative.
    # A B4 failure must not be inherited by an independently verified B1 row;
    # otherwise the matrix would contradict modelctl and the browser's
    # ``switchable_by_soc`` view.  The aggregate status is used only when the
    # selected SoC has no explicit validation record (legacy/schema-v1 data).
    registry_status = board_status or aggregate_status or "not-run"
    admission_quality_ok, admission_quality_reason = _profile_quality_admission(
        profile,
        board,
        registry_status,
    )
    if isinstance(quality, Mapping):
        quality_public = {"machine": quality.get("machine"), "human": quality.get("human")}
    else:
        quality_public = {"machine": "not-run", "human": "not-run"}
    if profile.get("candidate_kind") == "conditional":
        overall = "blocked"
        technical_pass = False
    elif any(technical_values[gate] == "failed" for gate in TECHNICAL_GATES):
        overall = "failed"
    elif any(technical_values[gate] == "blocked" for gate in TECHNICAL_GATES):
        overall = "blocked"
    elif technical_pass:
        overall = "passed"
    elif not targeted and not matching:
        overall = "not-run"
    elif not matching and registry_status in {"blocked", "not-run"}:
        overall = registry_status
    else:
        overall = "incomplete"
    registry_denied = registry_status in {"blocked", "not-run"}
    if registry_denied:
        # A fresh report does not silently promote a registry entry.  Preserve
        # the explicit operator state even when an acceptance report happens
        # to contain all passing machine gates.
        overall = registry_status
        technical_pass = False
    if registry_status == "admitted" and not admission_quality_ok:
        # Keep machine-gate status visible, but do not present an unapproved
        # row as an activatable candidate.
        overall = "blocked"
    return {
        "profile": profile["id"],
        "display_name": profile["display_name"],
        "candidate_kind": profile["candidate_kind"],
        "model_id": profile.get("model_id"),
        "revision": profile.get("revision"),
        "board": dict(board),
        "targeted": targeted,
        "target_reason": (
            "declared board target"
            if targeted
            else "outside declared board target; execution was not planned"
        ),
        "registry_status": registry_status,
        "gates": gates,
        "technical_pass": technical_pass,
        "experimental_switchable": bool(
            technical_pass
            and profile.get("candidate_kind") != "conditional"
            and not registry_denied
            and registry_status in ACTIVATION_STATUSES
            and admission_quality_ok
        ),
        "admission_quality": {
            "required": registry_status == "admitted",
            "approved": admission_quality_ok if registry_status == "admitted" else None,
            "reason": admission_quality_reason if registry_status == "admitted" else "not required before admitted status",
        },
        "quality": quality_public,
        "overall_status": overall,
        "selected_report": str(selected_path) if selected_path else None,
        "evidence": {
            "acceptance": str(selected_path) if selected_path else None,
            "sidecars": sidecar_paths,
            "stopped_at": stop_gate,
        },
        "report_count": len(matching),
        "rejected_reports": rejected,
        "service_status": selected.get("status") if isinstance(selected, Mapping) else None,
    }


def acceptance_coverage() -> Dict[str, Any]:
    """Return the service gates exported by the existing acceptance helper."""

    path = SCRIPT_DIR / "mindspore_chat_acceptance.py"
    if not path.is_file():
        return {"status": "unavailable", "required_gates": [], "covered": {}}
    spec = importlib.util.spec_from_file_location("case9_acceptance_coverage", path)
    if spec is None or spec.loader is None:
        return {"status": "unavailable", "required_gates": [], "covered": {}}
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as exc:
        return {
            "status": "unavailable",
            "required_gates": [],
            "covered": {},
            "error": "%s: %s" % (type(exc).__name__, exc),
        }
    required = list(getattr(module, "REQUIRED_MACHINE_GATES", ()))
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        source = ""
    implemented = {
        gate: all(name in source for name in names)
        for gate, names in ACCEPTANCE_GATE_MAP.items()
    }
    return {
        "status": "available",
        "required_gates": required,
        "implemented": implemented,
        "required": {
            gate: all(name in required for name in names)
            for gate, names in ACCEPTANCE_GATE_MAP.items()
        },
        # Keep covered as the practical answer to “is this gate implemented?”
        # while retaining the stricter required-machine-gate distinction.
        "covered": implemented,
    }


def build_matrix(
    registry_path: Path,
    reports_root: Path,
    profiles: Optional[Set[str]],
    boards: Sequence[Mapping[str, str]],
) -> Dict[str, Any]:
    registry_path = Path(registry_path)
    reports_root = Path(reports_root)
    registry_profiles = _load_registry(registry_path)
    known = {item["id"] for item in registry_profiles}
    unknown = sorted((profiles or set()) - known)
    if unknown:
        raise MatrixError("unknown profile(s): %s" % ", ".join(unknown))
    selected_profiles = [
        item for item in registry_profiles
        if profiles is None or item["id"] in profiles
    ]
    reports, malformed = _json_reports(reports_root)
    sidecars = _sidecar_index(reports_root)
    rows: List[Dict[str, Any]] = []
    for profile in selected_profiles:
        for board in _profile_boards(profile, boards):
            rows.append(_row(profile, board, reports, reports_root, sidecars))
    return {
        "schema_version": 1,
        "recorded_at_utc": utc_now(),
        "registry": str(registry_path),
        "reports_root": str(reports_root),
        "formal_ports": sorted(FORMAL_PORTS),
        "boards": [dict(board) for board in boards],
        "profiles_requested": sorted(profiles) if profiles is not None else None,
        "profile_count": len(selected_profiles),
        "row_count": len(rows),
        "acceptance_coverage": acceptance_coverage(),
        "rows": rows,
        "malformed_reports": malformed,
        "policy": {
            "read_only_scan": True,
            "installs_packages": False,
            "manages_processes": False,
            "connects_to_services": False,
            "formal_ports_untouched": True,
        },
    }


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path = _absolute(path.expanduser())
    _reject_symlink_components(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_components(path.parent)
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise MatrixError("output must be a regular file: %s" % path)
    temporary = path.with_name(".%s.part" % path.name)
    if temporary.exists() and temporary.is_symlink():
        raise MatrixError("temporary output must not be a symlink: %s" % temporary)
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _dry_run_payload(
    registry: Path,
    reports_root: Path,
    profiles: Optional[Set[str]],
    boards: Sequence[Mapping[str, str]],
    output: Optional[Path],
) -> Dict[str, Any]:
    return {
        "status": "dry-run",
        "registry": str(registry),
        "reports_root": str(reports_root),
        "profiles": sorted(profiles) if profiles is not None else "all",
        "boards": [dict(board) for board in boards],
        "checks": list(CANONICAL_GATES),
        "acceptance_service_gates": {
            key: list(value) for key, value in ACCEPTANCE_GATE_MAP.items()
        },
        "output": str(output) if output is not None else None,
        "writes_reports": False,
        "installs_packages": False,
        "manages_processes": False,
        "connects_to_services": False,
        "formal_ports_untouched": True,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY)
    parser.add_argument("--reports-root", type=Path, default=DEFAULT_REPORTS_ROOT)
    parser.add_argument("--profile", action="append", help="profile id; repeat or comma-separate")
    parser.add_argument("--board", default="both", help="8t, 20t, board8t, board20t, or both")
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        registry = _regular_file(args.registry, "registry")
        reports_root = _regular_directory(args.reports_root, "reports-root", allow_missing=True)
        profiles = _profile_selection(args.profile)
        boards = select_boards(args.board)
        if args.dry_run:
            print(json.dumps(
                _dry_run_payload(registry, reports_root, profiles, boards, args.output),
                ensure_ascii=False,
                sort_keys=True,
            ))
            return 0
        payload = build_matrix(registry, reports_root, profiles, boards)
        if args.output is not None:
            _write_json_atomic(args.output, payload)
            payload["output"] = str(_absolute(args.output))
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, MatrixError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
