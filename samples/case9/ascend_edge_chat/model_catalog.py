"""Public teaching catalog for technically testable chat models.

The catalog is deliberately separate from ``release-model.json``.  The latter
is the immutable default release lock; this file describes the broader
teaching experiment, including models whose language quality is poor or whose
long-output gate failed.  A quality result is evidence, not a runtime safety
gate for a classroom experiment.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG_PATH = PROJECT_ROOT / "configs" / "tutorial-model-catalog.json"
_REQUIRED_ENTRY_KEYS = {
    "id",
    "display_name",
    "runtime",
    "languages",
    "technical_status",
    "quality_status",
    "selectable",
    "reason",
    "board_results",
    "worker_registry",
}
_ALLOWED_TECHNICAL = {"observed-pass", "observed-degraded", "short-smoke", "not-run", "blocked"}
_ALLOWED_QUALITY = {"passed", "failed", "pending", "not-run"}
_ALLOWED_TEXT_INTEGRITY = {"passed", "observed-issue", "pending", "not-run"}


class CatalogError(ValueError):
    """Raised when the browser-visible teaching catalog is malformed."""


def _load_document(path: Path) -> Mapping[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CatalogError("invalid model catalog: %s" % exc) from exc
    if not isinstance(document, Mapping) or document.get("schema_version") != 1:
        raise CatalogError("model catalog schema_version must be 1")
    models = document.get("models")
    if not isinstance(models, list) or not models:
        raise CatalogError("model catalog models must be a non-empty array")
    return document


def _public_entry(raw: Mapping[str, Any]) -> Dict[str, Any]:
    missing = sorted(_REQUIRED_ENTRY_KEYS - set(raw))
    if missing:
        raise CatalogError("catalog entry is missing: %s" % ", ".join(missing))
    profile_id = raw.get("id")
    if not isinstance(profile_id, str) or not profile_id.strip():
        raise CatalogError("catalog entry id must be a non-empty string")
    technical = raw.get("technical_status")
    quality = raw.get("quality_status")
    if technical not in _ALLOWED_TECHNICAL:
        raise CatalogError("unknown technical status for %s" % profile_id)
    if quality not in _ALLOWED_QUALITY:
        raise CatalogError("unknown quality status for %s" % profile_id)
    text_integrity = raw.get("text_integrity_status", "pending")
    if text_integrity not in _ALLOWED_TEXT_INTEGRITY:
        raise CatalogError("unknown text-integrity status for %s" % profile_id)
    if not isinstance(raw.get("selectable"), bool):
        raise CatalogError("selectable must be boolean for %s" % profile_id)
    if raw["selectable"] and technical not in {"observed-pass", "observed-degraded", "short-smoke"}:
        raise CatalogError("only technically tested entries may be selectable: %s" % profile_id)
    board_results = raw.get("board_results")
    if not isinstance(board_results, Mapping):
        raise CatalogError("board_results must be an object for %s" % profile_id)
    languages = raw.get("languages")
    if not isinstance(languages, list) or not languages or not all(isinstance(item, str) for item in languages):
        raise CatalogError("languages must be a non-empty string array for %s" % profile_id)
    registry = raw.get("worker_registry")
    if not isinstance(registry, str) or not registry.strip():
        raise CatalogError("worker_registry must be a non-empty path for %s" % profile_id)
    try:
        max_tokens = int(raw.get("max_tokens", 256))
        default_max_tokens = int(raw.get("default_max_tokens", min(128, max_tokens)))
    except (TypeError, ValueError) as exc:
        raise CatalogError("token budgets must be integers for %s" % profile_id) from exc
    if not 1 <= default_max_tokens <= max_tokens <= 256:
        raise CatalogError("token budgets are invalid for %s" % profile_id)
    # Never expose source paths, artifact paths, tokens or free-form command
    # fragments to an unauthenticated browser.
    return {
        "id": profile_id,
        "display_name": str(raw["display_name"]),
        "runtime": str(raw["runtime"]),
        "languages": list(languages),
        "technical_status": technical,
        "quality_status": quality,
        "text_integrity_status": text_integrity,
        "selectable": bool(raw["selectable"]),
        "reason": str(raw["reason"]),
        "board_results": {
            str(board): str(result) for board, result in board_results.items()
        },
        "worker_registry": registry,
        "default_max_tokens": default_max_tokens,
        "max_tokens": max_tokens,
        "quality_detail": str(raw.get("quality_detail", "")),
        "technical_detail": str(raw.get("technical_detail", "")),
    }


def load_catalog(path: Path = DEFAULT_CATALOG_PATH) -> List[Dict[str, Any]]:
    """Load and validate catalog entries for API/UI consumption."""

    document = _load_document(path)
    return [_public_entry(item) for item in document["models"] if isinstance(item, Mapping)]


def catalog_by_id(path: Path = DEFAULT_CATALOG_PATH) -> Dict[str, Dict[str, Any]]:
    entries = load_catalog(path)
    result = {entry["id"]: entry for entry in entries}
    if len(result) != len(entries):
        raise CatalogError("model catalog ids must be unique")
    return result


__all__ = ["CatalogError", "DEFAULT_CATALOG_PATH", "catalog_by_id", "load_catalog"]
