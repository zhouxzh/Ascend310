#!/usr/bin/env python3
"""Create a board-local, single-profile registry for a bounded acceptance run.

The checked-in registry remains the source of truth.  This helper writes a
separate registry whose selected Ascend310B1 row is temporarily marked
``experimental_dirty_base`` only so the loopback candidate worker can perform
its G2-G7 tests.  It never grants production admission and requires a lock
file produced by ``fetch_mindspore_profile_artifacts.py``.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import os
from typing import Any, Dict, Mapping, Optional, Sequence


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _load(path: Path) -> Dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def build(
    source: Dict[str, Any],
    lock: Mapping[str, Any],
    profile_id: str,
    run_id: str,
    cache_dir_override: Optional[str] = None,
) -> Dict[str, Any]:
    profiles = source.get("profiles")
    if not isinstance(profiles, list):
        raise ValueError("registry has no profiles array")
    selected = next((item for item in profiles if isinstance(item, dict) and item.get("id") == profile_id), None)
    if selected is None:
        raise ValueError("profile not found: %s" % profile_id)
    if selected.get("candidate_kind") != "native_mindspore":
        raise ValueError("conditional profile cannot enter acceptance: %s" % profile_id)
    if lock.get("profile") != profile_id:
        raise ValueError("lock profile does not match requested profile")
    revision = lock.get("resolved_revision")
    if not isinstance(revision, str) or len(revision) != 40:
        raise ValueError("lock has no immutable revision")
    lock_artifacts = {item.get("name"): item for item in lock.get("artifacts", []) if isinstance(item, Mapping)}
    expected = {item.get("name") for item in selected.get("artifacts", []) if isinstance(item, Mapping)}
    if set(lock_artifacts) != expected or None in lock_artifacts:
        raise ValueError("lock artifact names do not exactly match registry")
    selected["revision"] = revision
    selected["tokenizer_revision"] = revision
    selected["revision_pinned"] = True
    if cache_dir_override is not None:
        candidate = Path(cache_dir_override)
        if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
            raise ValueError("cache-dir must be a safe relative path")
        selected["cache_dir"] = candidate.as_posix()
    for artifact in selected["artifacts"]:
        locked = lock_artifacts[artifact["name"]]
        artifact["expected_bytes"] = int(locked["bytes"])
        artifact["sha256"] = str(locked["sha256"])
        artifact["url"] = str(locked["url"])
    validation = selected.get("validation")
    if not isinstance(validation, dict) or "Ascend310B1" not in validation:
        raise ValueError("profile has no Ascend310B1 validation row")
    validation["Ascend310B1"] = {
        "status": "experimental_dirty_base",
        "reason": "Ephemeral loopback-only acceptance registry; final status is written only after measured report review.",
        "run_id": run_id,
        "updated_at": _utc_now(),
        "soc": "Ascend310B1",
        "tier": "20T",
        "environment_fingerprint": "0" * 64,
        "artifact_hashes": {name: str(item["sha256"]) for name, item in lock_artifacts.items()},
        "report_paths": ["reports/mindspore-chat/%s/acceptance.json" % run_id],
        "quality": {"machine": "pending", "human_review": "pending"},
        "performance": {"state": "pending"},
        "metrics": {"ephemeral_acceptance_registry": True, "lock_sha256": "pending"},
    }
    selected["status"] = "blocked"
    selected["admission"] = {"eligible": False, "reason": "Acceptance registry cannot grant admission."}
    return source


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache-dir", default=None, help="optional safe cache directory relative to CASE9_MODEL_ROOT")
    args = parser.parse_args(argv)
    try:
        payload = build(
            _load(args.registry), _load(args.lock), args.profile, args.run_id,
            cache_dir_override=args.cache_dir,
        )
        _atomic_json(args.output, payload)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        parser.error(str(exc))
    print(json.dumps({"output": str(args.output), "sha256": _sha256(args.output), "profile": args.profile}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
