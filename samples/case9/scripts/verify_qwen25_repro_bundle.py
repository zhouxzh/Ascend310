#!/usr/bin/env python3
"""Verify a Qwen2.5 dual-board reproducibility bundle.

Only the standard library is used.  The checker rejects path traversal,
symlinked entries, stale ``.part`` files and size/SHA-256 mismatches.  It does
not import ACL, load an OM, or modify the bundle.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Dict, Iterable, List, Mapping, Tuple


MODEL_ID = "qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om"


def digest(path: Path) -> Tuple[int, str]:
    state = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            state.update(block)
    return size, state.hexdigest()


def safe_relative(value: Any) -> Path:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ValueError("path must be a non-empty POSIX relative string")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise ValueError("path escapes bundle: %s" % value)
    return path


def _check_sums_file(root: Path, entries: Mapping[str, Mapping[str, Any]], failures: List[str]) -> None:
    sums_path = root / "SHA256SUMS.txt"
    if not sums_path.is_file():
        failures.append("missing SHA256SUMS.txt")
        return
    seen = set()
    for line_number, line in enumerate(sums_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        parts = line.split(None, 1)
        if len(parts) != 2 or len(parts[0]) != 64:
            failures.append("invalid SHA256SUMS line %d" % line_number)
            continue
        relative = parts[1].strip()
        if relative.startswith("./"):
            relative = relative[2:]
        if relative not in entries:
            failures.append("SHA256SUMS has unmanifested path: %s" % relative)
        if relative in seen:
            failures.append("duplicate SHA256SUMS path: %s" % relative)
        seen.add(relative)
        if relative in entries and parts[0].lower() != str(entries[relative]["sha256"]).lower():
            failures.append("SHA256SUMS digest differs from manifest: %s" % relative)
    missing = set(entries) - seen
    failures.extend("SHA256SUMS missing path: %s" % item for item in sorted(missing))


def _check_tokenizer_lock(root: Path, failures: List[str]) -> None:
    tokenizer = root / "artifacts" / "common" / "tokenizer.json"
    lock = root / "artifacts" / "common" / "tokenizer.json.lock.json"
    if not tokenizer.is_file() or not lock.is_file():
        failures.append("tokenizer.json and tokenizer.json.lock.json are both required")
        return
    try:
        document = json.loads(lock.read_text(encoding="utf-8"))
        size, sha = digest(tokenizer)
        if document.get("artifact") != "tokenizer.json":
            failures.append("tokenizer lock artifact name is incorrect")
        if document.get("bytes") != size:
            failures.append("tokenizer lock byte count does not match tokenizer.json")
        if str(document.get("sha256", "")).lower() != sha.lower():
            failures.append("tokenizer lock SHA-256 does not match tokenizer.json")
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        failures.append("invalid tokenizer lock: %s" % exc)


def verify(root: Path) -> Dict[str, Any]:
    failures: List[str] = []
    manifest_path = root / "bundle-manifest.json"
    if not manifest_path.is_file():
        return {"status": "failed", "checked": 0, "failures": ["missing bundle-manifest.json"]}
    try:
        document = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return {"status": "failed", "checked": 0, "failures": ["invalid manifest: %s" % exc]}
    if document.get("model_id") != MODEL_ID:
        failures.append("unexpected model_id")
    boards = document.get("boards")
    board8 = boards.get("board8t") if isinstance(boards, Mapping) else None
    board20 = boards.get("board20t") if isinstance(boards, Mapping) else None
    if not isinstance(board8, Mapping) or not isinstance(board20, Mapping) or board8.get("soc") != "Ascend310B4" or board20.get("soc") != "Ascend310B1":
        failures.append("manifest must contain separate Ascend310B4 and Ascend310B1 board records")
    raw_entries = document.get("required_files")
    if not isinstance(raw_entries, list) or not raw_entries:
        failures.append("required_files is empty or malformed")
        return {"status": "failed", "checked": 0, "failures": failures}
    entries: Dict[str, Mapping[str, Any]] = {}
    checked = 0
    for index, item in enumerate(raw_entries):
        if not isinstance(item, Mapping):
            failures.append("required_files[%d] is not an object" % index)
            continue
        try:
            relative_path = safe_relative(item.get("path"))
        except ValueError as exc:
            failures.append("required_files[%d]: %s" % (index, exc))
            continue
        relative = relative_path.as_posix()
        if relative in entries:
            failures.append("duplicate manifest path: %s" % relative)
            continue
        expected_size = item.get("bytes")
        expected_sha = item.get("sha256")
        if isinstance(expected_size, bool) or not isinstance(expected_size, int) or expected_size < 0:
            failures.append("invalid byte count: %s" % relative)
            continue
        if not isinstance(expected_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha):
            failures.append("invalid SHA-256: %s" % relative)
            continue
        entries[relative] = item
        path = root / relative_path
        if path.is_symlink():
            failures.append("symlink is not allowed: %s" % relative)
            continue
        if not path.is_file():
            failures.append("missing: %s" % relative)
            continue
        actual_size, actual_sha = digest(path)
        checked += 1
        if actual_size != expected_size:
            failures.append("size: %s expected=%d actual=%d" % (relative, expected_size, actual_size))
        if actual_sha.lower() != expected_sha.lower():
            failures.append("sha256: %s expected=%s actual=%s" % (relative, expected_sha, actual_sha))
    for part in root.rglob("*.part"):
        failures.append("stale partial file: %s" % part.relative_to(root).as_posix())
    _check_sums_file(root, entries, failures)
    _check_tokenizer_lock(root, failures)
    return {"status": "passed" if not failures else "failed", "checked": checked, "manifest_entries": len(entries), "failures": failures}


def main(argv: Iterable[str] = ()) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[1] / "repro" / "qwen25-kv1024-dual-board-20260827")
    args = parser.parse_args(list(argv) or None)
    result = verify(args.root.expanduser().resolve())
    print(json.dumps({"bundle": str(args.root.expanduser().resolve()), **result}, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
