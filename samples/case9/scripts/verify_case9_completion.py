#!/usr/bin/env python3
"""Verify the Case9 dual-board gap-completion evidence bundle.

The checker is standard-library only and read-only with respect to model and
report data.  It validates the explicit manifest, every referenced file, the
SHA256SUMS sidecar, and the required board/model matrix.  A combination may be
``passed`` when a report exists, or ``blocked``/``not-run`` when the manifest
contains a concrete reason and provenance report.  An absent combination is a
failure; it must not silently become a successful result.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SCHEMA_VERSION = 1
ALLOWED_COMBINATION_STATUSES = frozenset(
    {
        "passed",
        "failed",
        "blocked",
        "not-run",
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
    }
)
EXPECTED_BOARDS: Dict[str, Dict[str, str]] = {
    "board8t": {"host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
    "board20t": {"host": "192.168.1.95", "soc": "Ascend310B1", "tier": "20T"},
}
EXPECTED_MATRIX: Tuple[Tuple[str, str], ...] = (
    ("board8t", "qwen25-onnx-om"),
    ("board20t", "qwen25-onnx-om"),
    ("board8t", "qwen1.5-0.5b-mindspore"),
    ("board20t", "qwen1.5-0.5b-mindspore"),
    ("board8t", "tinyllama-1.1b-mindspore"),
    ("board20t", "tinyllama-1.1b-mindspore"),
    ("board8t", "deepseek-r1-qwen-1.5b-mindspore"),
    ("board20t", "deepseek-r1-qwen-1.5b-mindspore"),
)
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class VerificationError(ValueError):
    """Raised for malformed options or unsafe paths."""


def digest(path: Path) -> Tuple[int, str]:
    """Return byte count and SHA-256 for a regular file."""

    try:
        before = path.lstat()
    except OSError as exc:
        raise VerificationError("cannot stat %s: %s" % (path, exc)) from exc
    if not stat.S_ISREG(before.st_mode) or path.is_symlink():
        raise VerificationError("not a regular file: %s" % path)
    state = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(block)
                state.update(block)
    except OSError as exc:
        raise VerificationError("cannot read %s: %s" % (path, exc)) from exc
    try:
        after = path.lstat()
    except OSError as exc:
        raise VerificationError("file disappeared while reading %s" % path) from exc
    if (
        before.st_size != after.st_size
        or getattr(before, "st_ino", None) != getattr(after, "st_ino", None)
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise VerificationError("file changed while reading: %s" % path)
    return size, state.hexdigest()


def safe_relative(value: Any) -> Path:
    """Validate a POSIX relative path inside the bundle."""

    if (
        not isinstance(value, str)
        or not value.strip()
        or "\\" in value
        or "\x00" in value
        or ":" in value
    ):
        raise VerificationError("path must be a non-empty POSIX relative path")
    path = Path(value)
    if path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise VerificationError("path escapes bundle: %s" % value)
    return path


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _reject_symlink_components(path: Path) -> None:
    absolute = _absolute(path)
    anchor = Path(absolute.anchor)
    current = anchor
    try:
        parts = absolute.relative_to(anchor).parts
    except ValueError as exc:  # pragma: no cover - mixed-drive defensive path
        raise VerificationError("invalid path anchor: %s" % path) from exc
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise VerificationError("symlink path component is not allowed: %s" % current)


def _regular_root(value: Any) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise VerificationError("bundle root must be a non-empty path")
    lexical = _absolute(Path(value).expanduser())
    _reject_symlink_components(lexical)
    try:
        root = lexical.resolve(strict=True)
    except OSError as exc:
        raise VerificationError("bundle root cannot be resolved: %s" % exc) from exc
    if root.is_symlink() or not root.is_dir():
        raise VerificationError("bundle root must be a regular directory: %s" % root)
    _reject_symlink_components(lexical)
    return root


def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise VerificationError("invalid %s: %s" % (label, exc)) from exc


def _check_sums(root: Path, entries: Mapping[str, Mapping[str, Any]], failures: List[str]) -> None:
    sums_path = root / "SHA256SUMS.txt"
    if not sums_path.is_file() or sums_path.is_symlink():
        failures.append("missing SHA256SUMS.txt")
        return
    try:
        lines = sums_path.read_text(encoding="ascii").splitlines()
    except (OSError, UnicodeError) as exc:
        failures.append("cannot read SHA256SUMS.txt: %s" % exc)
        return
    seen = set()
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        pieces = line.split(None, 1)
        if len(pieces) != 2 or not SHA256_RE.fullmatch(pieces[0]):
            failures.append("invalid SHA256SUMS line %d" % number)
            continue
        try:
            checksum_path = pieces[1].strip()
            if checksum_path.startswith("./"):
                checksum_path = checksum_path[2:]
            relative = safe_relative(checksum_path).as_posix()
        except VerificationError as exc:
            failures.append("SHA256SUMS line %d: %s" % (number, exc))
            continue
        if relative in seen:
            failures.append("duplicate SHA256SUMS path: %s" % relative)
        seen.add(relative)
        if relative not in entries:
            failures.append("SHA256SUMS has unmanifested path: %s" % relative)
        elif pieces[0].lower() != str(entries[relative].get("sha256", "")).lower():
            failures.append("SHA256SUMS digest differs from manifest: %s" % relative)
    for relative in sorted(set(entries) - seen):
        failures.append("SHA256SUMS missing path: %s" % relative)


def _check_entry_files(root: Path, raw_entries: Any, failures: List[str]) -> Dict[str, Mapping[str, Any]]:
    if not isinstance(raw_entries, list) or not raw_entries:
        failures.append("required_files must be a non-empty array")
        return {}
    entries: Dict[str, Mapping[str, Any]] = {}
    for index, item in enumerate(raw_entries):
        if not isinstance(item, Mapping):
            failures.append("required_files[%d] is not an object" % index)
            continue
        try:
            relative = safe_relative(item.get("path")).as_posix()
        except VerificationError as exc:
            failures.append("required_files[%d]: %s" % (index, exc))
            continue
        if relative in entries:
            failures.append("duplicate required_files path: %s" % relative)
            continue
        size = item.get("bytes")
        sha = item.get("sha256")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            failures.append("invalid byte count: %s" % relative)
            continue
        if not isinstance(sha, str) or not SHA256_RE.fullmatch(sha):
            failures.append("invalid SHA-256: %s" % relative)
            continue
        entries[relative] = item
        path = root / relative
        try:
            _reject_symlink_components(path)
            if path.is_symlink() or not path.is_file():
                failures.append("missing or unsafe file: %s" % relative)
                continue
            actual_size, actual_sha = digest(path)
        except VerificationError as exc:
            failures.append(str(exc))
            continue
        if actual_size != size:
            failures.append("size mismatch: %s expected=%d actual=%d" % (relative, size, actual_size))
        if actual_sha.lower() != sha.lower():
            failures.append("SHA-256 mismatch: %s" % relative)
    return entries


def _combination_key(item: Mapping[str, Any]) -> Optional[Tuple[str, str]]:
    board = item.get("board") or item.get("board_id")
    model = item.get("model") or item.get("profile") or item.get("profile_id")
    if not isinstance(board, str) or not isinstance(model, str):
        return None
    # Normalize common descriptive spelling used by hand-authored manifests.
    aliases = {
        "qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om": "qwen25-onnx-om",
        "qwen25-static-kv-1024": "qwen25-onnx-om",
    }
    return board, aliases.get(model, model)


def _check_boards(document: Mapping[str, Any], failures: List[str]) -> None:
    boards = document.get("boards")
    if not isinstance(boards, Mapping):
        failures.append("manifest.boards must be an object")
        return
    for board_id, expected in EXPECTED_BOARDS.items():
        observed = boards.get(board_id)
        if not isinstance(observed, Mapping):
            failures.append("missing board record: %s" % board_id)
            continue
        for key, value in expected.items():
            if observed.get(key) != value:
                failures.append("%s.%s expected %s, got %s" % (board_id, key, value, observed.get(key)))


def _check_matrix(
    document: Mapping[str, Any],
    root: Path,
    failures: List[str],
    entries: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    raw = document.get("matrix")
    if not isinstance(raw, list):
        failures.append("manifest.matrix must be an array")
        return {"expected": len(EXPECTED_MATRIX), "observed": 0, "missing": [list(item) for item in EXPECTED_MATRIX]}
    seen: Dict[Tuple[str, str], Mapping[str, Any]] = {}
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            failures.append("matrix[%d] is not an object" % index)
            continue
        key = _combination_key(item)
        if key is None:
            failures.append("matrix[%d] has no board/model identity" % index)
            continue
        if key in seen:
            failures.append("duplicate matrix combination: %s/%s" % key)
            continue
        seen[key] = item
        board, _model = key
        if board not in EXPECTED_BOARDS:
            failures.append("unknown board in matrix: %s" % board)
        status = item.get("status")
        if status not in ALLOWED_COMBINATION_STATUSES:
            failures.append("invalid status for %s/%s: %s" % (board, _model, status))
        report = item.get("report") or item.get("report_path")
        reason = item.get("reason") or item.get("block_reason")
        if status in {"blocked", "not-run", "failed"} and not isinstance(reason, str):
            failures.append("%s/%s needs a concrete reason" % (board, _model))
        if isinstance(report, str) and report:
            try:
                relative = safe_relative(report).as_posix()
                report_path = root / relative
                if report_path.is_symlink() or not report_path.is_file():
                    failures.append("matrix report is missing: %s" % relative)
                elif entries is not None and relative not in entries:
                    failures.append("matrix report is not hashed in required_files: %s" % relative)
            except VerificationError as exc:
                failures.append("matrix report %s: %s" % (key, exc))
        elif status != "not-run":
            failures.append("%s/%s has no report path" % (board, _model))
    missing = [list(key) for key in EXPECTED_MATRIX if key not in seen]
    for board, model in missing:
        failures.append("missing matrix combination: %s/%s" % (board, model))
    # A blocked combination is complete only when provenance is explicit.  A
    # report may be a connectivity or load-failure text file; the verifier does
    # not infer success from its contents.
    return {
        "expected": len(EXPECTED_MATRIX),
        "observed": len(seen),
        "missing": missing,
        "statuses": {
            "%s/%s" % key: item.get("status") for key, item in sorted(seen.items())
        },
    }


def verify(root: Path) -> Dict[str, Any]:
    """Verify a bundle and return a JSON-serializable report."""

    failures: List[str] = []
    try:
        bundle = _regular_root(root)
    except VerificationError as exc:
        return {"status": "failed", "checked": 0, "failures": [str(exc)]}
    manifest_path = bundle / "bundle-manifest.json"
    if not manifest_path.is_file() or manifest_path.is_symlink():
        return {"status": "failed", "checked": 0, "failures": ["missing bundle-manifest.json"]}
    try:
        document = _load_json(manifest_path, "bundle-manifest.json")
    except VerificationError as exc:
        return {"status": "failed", "checked": 0, "failures": [str(exc)]}
    if not isinstance(document, Mapping):
        return {"status": "failed", "checked": 0, "failures": ["manifest must be an object"]}
    if document.get("schema_version") != SCHEMA_VERSION:
        failures.append("unsupported schema_version")
    _check_boards(document, failures)
    entries = _check_entry_files(bundle, document.get("required_files"), failures)
    _check_sums(bundle, entries, failures)
    for path in bundle.rglob("*.part"):
        if path.is_file() or path.is_symlink():
            failures.append("stale partial file: %s" % path.relative_to(bundle).as_posix())
    matrix = _check_matrix(document, bundle, failures, entries)
    return {
        "status": "passed" if not failures else "failed",
        "bundle": str(bundle),
        "checked": sum(1 for relative in entries if (bundle / relative).is_file()),
        "manifest_entries": len(entries),
        "matrix": matrix,
        "failures": failures,
    }


# Explicit aliases make the checker convenient for shell wrappers and keep a
# stable API for callers that describe the input as a bundle rather than root.
verify_bundle = verify
validate_manifest = verify


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path = _absolute(path.expanduser())
    _reject_symlink_components(path.parent)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise VerificationError("output must be a regular file: %s" % path)
    descriptor, temporary = tempfile.mkstemp(prefix=".%s." % path.name, suffix=".part", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "root",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "repro" / "case9-dual-board-gap",
        help="gap bundle directory",
    )
    parser.add_argument("--bundle", dest="bundle_option", type=Path, help="bundle directory (alternative to root)")
    parser.add_argument("--output", type=Path, help="also write the verification report atomically")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = verify(args.bundle_option or args.root)
        payload: Dict[str, Any] = {"schema_version": SCHEMA_VERSION, "checked_at": _timestamp(), **result}
        if args.output is not None:
            _write_json_atomic(args.output, payload)
            payload["report_path"] = str(_absolute(args.output.expanduser()))
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0 if result.get("status") == "passed" else 1
    except (OSError, VerificationError) as exc:
        print(json.dumps({"schema_version": SCHEMA_VERSION, "status": "error", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
