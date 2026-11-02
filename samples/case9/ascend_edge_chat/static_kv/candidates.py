"""Allowlisted offline ACL static-KV candidates for isolated 8T/20T runs.

The browser never reads this file.  The backend supervisor supplies only a
candidate ID; this module resolves all artifact paths below one operator-owned
candidate directory and verifies the static-KV schema-2 contracts before ACL is
opened.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .runtime import MODEL_ID


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CANDIDATE_CATALOG = PROJECT_ROOT / "configs" / "20t-backend-catalog.json"


class CandidateError(ValueError):
    """One offline ACL candidate registry or path is invalid."""


@dataclass(frozen=True)
class AclCandidate:
    """One fully allowlisted B1/B4 static-KV candidate."""

    id: str
    soc_version: str
    display_name: str
    default_max_tokens: int
    max_tokens: int
    lock_path: Path
    data_dir: Path
    om: Path
    tokenizer: Path
    tokenizer_config: Path
    tokenizer_lock: Path
    controller_contract: Path
    runtime_contract: Path


def _safe_relative(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise CandidateError("candidate %s path is required" % label)
    candidate = (root / value).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise CandidateError("candidate %s path escapes its data directory" % label) from exc
    return candidate


def _load_catalog(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CandidateError("invalid backend catalog: %s" % exc) from exc
    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        raise CandidateError("backend catalog schema_version must be 1")
    target = value.get("target")
    if not isinstance(target, Mapping) or target.get("soc") not in {"Ascend310B1", "Ascend310B4"}:
        raise CandidateError("ACL candidate catalog must target Ascend310B1 or Ascend310B4")
    return value


def load_acl_candidate(
    candidate_id: str,
    *,
    data_dir: Path,
    catalog_path: Path = DEFAULT_CANDIDATE_CATALOG,
) -> AclCandidate:
    """Resolve one locked candidate without accepting browser-provided paths."""

    if not isinstance(candidate_id, str) or not candidate_id.strip():
        raise CandidateError("candidate id is required")
    root = data_dir.expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise CandidateError("candidate data directory is unavailable")
    catalog = _load_catalog(catalog_path.expanduser().resolve())
    target = catalog.get("target")
    target_soc = str(target.get("soc")) if isinstance(target, Mapping) else ""
    backends = catalog.get("backends")
    if not isinstance(backends, list):
        raise CandidateError("backend catalog has no backends")
    raw: Mapping[str, Any] | None = None
    for backend in backends:
        if not isinstance(backend, Mapping) or backend.get("id") != "acl-static-kv":
            continue
        models = backend.get("models")
        if not isinstance(models, list):
            continue
        for model in models:
            if isinstance(model, Mapping) and model.get("id") == candidate_id:
                raw = model
                break
    if raw is None or raw.get("selectable") is not True:
        raise CandidateError("ACL candidate is not allowlisted")
    artifacts = raw.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise CandidateError("ACL candidate artifacts are invalid")
    lock_relative = raw.get("candidate_lock")
    lock_path = _safe_relative(PROJECT_ROOT, lock_relative, "lock")
    if lock_path.is_symlink() or not lock_path.is_file():
        raise CandidateError("ACL candidate lock is unavailable")
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CandidateError("ACL candidate lock is invalid") from exc
    if (
        not isinstance(lock, Mapping)
        or lock.get("schema_version") != 2
        or lock.get("model_id") != MODEL_ID
        or lock.get("soc_version") != target_soc
    ):
        raise CandidateError("ACL candidate lock does not match the catalog target SoC")
    try:
        default_max_tokens = int(raw.get("default_max_tokens"))
        max_tokens = int(raw.get("max_tokens"))
    except (TypeError, ValueError) as exc:
        raise CandidateError("ACL candidate token limits are invalid") from exc
    if not 1 <= default_max_tokens <= max_tokens <= 80:
        raise CandidateError("ACL candidate token limits are invalid")
    paths = {
        "om": _safe_relative(root, artifacts.get("om"), "om"),
        "tokenizer": _safe_relative(root, artifacts.get("tokenizer"), "tokenizer"),
        "tokenizer_config": _safe_relative(root, artifacts.get("tokenizer_config"), "tokenizer config"),
        "tokenizer_lock": _safe_relative(root, artifacts.get("tokenizer_lock"), "tokenizer lock"),
        "controller_contract": _safe_relative(root, artifacts.get("controller_contract"), "controller contract"),
        "runtime_contract": _safe_relative(root, artifacts.get("runtime_contract"), "runtime contract"),
    }
    for label, path in paths.items():
        if path.is_symlink() or not path.is_file():
            raise CandidateError("ACL candidate %s is unavailable" % label)
    return AclCandidate(
        id=candidate_id,
        soc_version=target_soc,
        display_name=str(raw.get("display_name", candidate_id)),
        default_max_tokens=default_max_tokens,
        max_tokens=max_tokens,
        lock_path=lock_path,
        data_dir=root,
        **paths,
    )


__all__ = ["AclCandidate", "CandidateError", "DEFAULT_CANDIDATE_CATALOG", "load_acl_candidate"]
