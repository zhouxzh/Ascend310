#!/usr/bin/env python3
"""Create a board-specific, test-only Case9 profile registry.

The checked-in registry remains the source of admission policy.  This helper
only makes an explicit temporary copy for a gap campaign: one profile is
selected, its board identity/cache path are replaced, and blocked profiles
may be downgraded to ``experimental_dirty_base`` only with an explicit flag.
No model files are touched.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Dict, Optional, Sequence


PROFILE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
SOC_RE = re.compile(r"^Ascend[0-9A-Za-z]+$")
TIER_RE = re.compile(r"^[0-9]+T$")


def _safe_profile(value: str) -> str:
    if not PROFILE_RE.fullmatch(value):
        raise ValueError("unsafe profile id")
    return value


def _safe_board(value: str, pattern: re.Pattern[str], label: str) -> str:
    if not pattern.fullmatch(value):
        raise ValueError("unsafe %s" % label)
    return value


def _safe_relative(value: str) -> str:
    path = Path(value)
    if not value or path.is_absolute() or "\\" in value or any(part in {".", ".."} for part in path.parts):
        raise ValueError("cache path must be relative")
    return path.as_posix()


def make_registry(
    source: Path,
    target: Path,
    profile_id: str,
    host: str,
    soc: str,
    tier: str,
    cache_dir: Optional[str],
    allow_blocked: bool,
    artifact_overrides: Sequence[str],
) -> None:
    document = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or not isinstance(document.get("profiles"), list):
        raise ValueError("registry.profiles must be an array")
    profile_id = _safe_profile(profile_id)
    soc = _safe_board(soc, SOC_RE, "SoC")
    tier = _safe_board(tier, TIER_RE, "tier")
    if not re.fullmatch(r"^[0-9A-Fa-f:.]+$", host):
        raise ValueError("unsafe host")
    selected = [item for item in document["profiles"] if isinstance(item, dict) and item.get("id") == profile_id]
    if len(selected) != 1:
        raise ValueError("profile must occur exactly once")
    item: Dict[str, Any] = selected[0]
    if item.get("status") in {"blocked", "not-run"}:
        if not allow_blocked:
            raise ValueError("profile is %s; pass --allow-blocked" % item.get("status"))
        if not item.get("revision_pinned"):
            raise ValueError("mutable revision cannot be used in a gap registry")
        item["status"] = "experimental_dirty_base"
        item["admission"] = {"eligible": False, "reason": "Temporary gap acceptance only; never activate or promote."}
    item["board"] = {"host": host, "soc": soc, "tier": tier}
    if cache_dir is not None:
        item["cache_dir"] = _safe_relative(cache_dir)
    artifacts = item.get("artifacts")
    if not isinstance(artifacts, list):
        raise ValueError("profile artifacts must be an array")
    by_name = {entry.get("name"): entry for entry in artifacts if isinstance(entry, dict)}
    for override in artifact_overrides:
        pieces = override.split(":")
        if len(pieces) != 3 or pieces[0] not in by_name or not pieces[1].isdigit() or not re.fullmatch(r"[0-9A-Fa-f]{64}", pieces[2]):
            raise ValueError("artifact override must be NAME:BYTES:SHA256")
        by_name[pieces[0]]["expected_bytes"] = int(pieces[1])
        by_name[pieces[0]]["sha256"] = pieces[2].lower()
    target = target.expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=".%s." % target.name, suffix=".part", dir=str(target.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, ensure_ascii=True, indent=2)
            stream.write("\n")
            stream.flush()
        Path(temporary).replace(target)
    except Exception:
        try:
            Path(temporary).unlink(missing_ok=True)
        except OSError:
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--soc", required=True)
    parser.add_argument("--tier", required=True)
    parser.add_argument("--cache-dir")
    parser.add_argument("--allow-blocked", action="store_true")
    parser.add_argument("--artifact-override", action="append", default=[], metavar="NAME:BYTES:SHA256")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    make_registry(args.source, args.output, args.profile, args.host, args.soc, args.tier, args.cache_dir, args.allow_blocked, args.artifact_override)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
