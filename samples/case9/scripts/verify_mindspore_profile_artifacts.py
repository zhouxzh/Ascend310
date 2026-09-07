#!/usr/bin/env python3
"""Verify the local artifacts declared by a Case9 MindSpore chat profile.

This checker is deliberately standard-library-only and read-only with regard
to model data.  It loads the strict profile registry, resolves the declared
``cache_dir`` and artifact filenames below a caller-selected model root, and
checks regular files against their declared byte counts and SHA-256 digests.
It never downloads, installs, removes, or follows symlinked model paths.

Examples::

    python scripts/verify_mindspore_profile_artifacts.py \
        --profile qwen1.5-0.5b-mindspore --root .
    python scripts/verify_mindspore_profile_artifacts.py \
        --all --root /home/HwHiAiUser/case9-mindspore-chat --output report.json

The command prints a JSON report to stdout.  ``--output`` additionally writes
the same report atomically; it is optional and is never required for normal
verification.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
DEFAULT_REGISTRY = REPO_DIR / "configs" / "chat_model_profiles.json"
DEFAULT_MODEL_ROOT = REPO_DIR
_CHUNK_SIZE = 1024 * 1024


class VerificationError(ValueError):
    """Raised when verifier options or a path are unsafe or malformed."""


def _absolute(path: Path) -> Path:
    """Return an absolute lexical path without resolving symlinks."""

    # ``Path.absolute`` is available on Python 3.9 and deliberately does not
    # resolve links.  ``os.path.abspath`` also keeps this behavior on Windows.
    return Path(os.path.abspath(os.fspath(path)))


def _reject_symlink_components(path: Path) -> None:
    """Reject an existing symlink anywhere in ``path``'s lexical components."""

    absolute = _absolute(path)
    anchor = Path(absolute.anchor)
    current = anchor
    # ``relative_to`` avoids treating a Windows drive anchor as a component.
    try:
        parts = absolute.relative_to(anchor).parts
    except ValueError as exc:  # pragma: no cover - defensive mixed-drive case
        raise VerificationError("invalid path anchor: %s" % path) from exc
    for part in parts:
        current = current / part
        try:
            linked = current.is_symlink()
        except OSError as exc:
            raise VerificationError("cannot inspect path component %s: %s" % (current, exc)) from exc
        if linked:
            raise VerificationError("symlink path component is not allowed: %s" % current)


def _regular_directory(value: Any, name: str) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise VerificationError("%s must be a non-empty path" % name)
    raw = Path(value).expanduser()
    lexical = _absolute(raw)
    _reject_symlink_components(lexical)
    try:
        resolved = lexical.resolve(strict=True)
    except OSError as exc:
        raise VerificationError("%s cannot be resolved: %s" % (name, exc)) from exc
    if resolved.is_symlink() or not resolved.is_dir():
        raise VerificationError("%s must be a regular directory: %s" % (name, resolved))
    # ``resolve`` may have encountered a link introduced by a race.  Recheck
    # the lexical spelling immediately before using the directory.
    _reject_symlink_components(lexical)
    return resolved


def _safe_relative(value: Any, name: str) -> Path:
    """Validate a registry-relative path without accepting platform escapes."""

    if (
        not isinstance(value, str)
        or not value.strip()
        or "\\" in value
        or "\x00" in value
        or ":" in value
    ):
        raise VerificationError("%s must be a non-empty POSIX relative path" % name)
    candidate = Path(value)
    if candidate.is_absolute() or any(part in {".", ".."} for part in candidate.parts):
        raise VerificationError("%s must stay relative: %s" % (name, value))
    # A leading slash is covered by ``is_absolute`` on POSIX.  Explicitly
    # reject drive-qualified values on Windows and POSIX (``C:/...``).
    return candidate


def _resolve_artifact(root: Path, cache_dir: str, filename: str) -> Tuple[Path, str]:
    """Resolve one declared artifact and prove it remains below ``root``."""

    cache = _safe_relative(cache_dir, "cache_dir")
    relative_file = _safe_relative(filename, "artifact.filename")
    combined = cache / relative_file
    lexical = root / combined
    _reject_symlink_components(lexical)
    candidate = lexical.resolve(strict=False)
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise VerificationError("artifact escapes model root: %s" % combined.as_posix()) from exc
    # A symlink may have been created between the first check and resolve.
    _reject_symlink_components(lexical)
    return candidate, combined.as_posix()


def sha256_file(path: Path) -> Tuple[int, str]:
    """Hash a regular file while detecting a replacement during the read."""

    try:
        before = path.lstat()
    except OSError as exc:
        raise VerificationError("cannot stat %s: %s" % (path, exc)) from exc
    if not stat.S_ISREG(before.st_mode) or path.is_symlink():
        raise VerificationError("artifact is not a regular file: %s" % path)
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(_CHUNK_SIZE), b""):
                size += len(block)
                digest.update(block)
    except OSError as exc:
        raise VerificationError("cannot read %s: %s" % (path, exc)) from exc
    try:
        after = path.lstat()
    except OSError as exc:
        raise VerificationError("artifact disappeared while reading %s" % path) from exc
    if (
        before.st_size != after.st_size
        or getattr(before, "st_ino", None) != getattr(after, "st_ino", None)
        or before.st_mtime_ns != after.st_mtime_ns
    ):
        raise VerificationError("artifact changed while reading: %s" % path)
    return size, digest.hexdigest()


def _artifact_result(
    *,
    name: str,
    kind: str,
    relative: str,
    expected_bytes: Optional[int],
    expected_sha256: Optional[str],
) -> Dict[str, Any]:
    return {
        "name": name,
        "kind": kind,
        "path": relative,
        "expected_bytes": expected_bytes,
        "expected_sha256": expected_sha256,
        "actual_bytes": None,
        "actual_sha256": None,
        "status": "not-checked",
        "errors": [],
    }


def _normalise_board_override(board_override: Optional[Mapping[str, Any]]) -> Optional[Dict[str, str]]:
    """Validate an observed-board override without changing profile metadata.

    A profile's primary ``board`` describes its preferred target and may differ
    from the board on which a shared artifact is being inspected (for example,
    the DeepSeek profile is primarily registered for the 20T B1 board while
    its same files are also exercised on the 8T B4 board).  Callers must pass
    all three identity fields together so a report cannot silently mix them.
    """

    if board_override is None:
        return None
    if not isinstance(board_override, Mapping):
        raise VerificationError("observed board must be a mapping")
    required = ("host", "soc", "tier")
    values: Dict[str, str] = {}
    for key in required:
        value = board_override.get(key)
        if not isinstance(value, str) or not value.strip():
            raise VerificationError("observed board.%s must be a non-empty string" % key)
        if "\x00" in value or "\\" in value:
            raise VerificationError("observed board.%s contains an unsafe character" % key)
        values[key] = value.strip()
    return values


def verify_profile_artifacts(
    profile: Any,
    model_root: Any,
    *,
    board_override: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Verify all explicitly declared artifacts for one validated profile.

    ``profile`` is normally a :class:`ChatModelProfile` returned by
    ``case9_model_profiles.load_profiles``.  The function intentionally uses
    attribute access only, making it straightforward to test with a small
    validated fixture while retaining the registry's strict parsing boundary.
    """

    root = _regular_directory(model_root, "model root")
    profile_id = str(getattr(profile, "id", getattr(profile, "profile_id", "")))
    if not profile_id:
        raise VerificationError("profile has no id")
    cache_dir = str(getattr(profile, "cache_dir", ""))
    raw_artifacts = tuple(getattr(profile, "artifacts", ()) or ())
    profile_status = str(getattr(profile, "status", ""))
    profile_board = {
        "host": str(getattr(profile, "board_host", "")),
        "soc": str(getattr(profile, "board_soc", "")),
        "tier": str(getattr(profile, "board_tier", "")),
    }
    observed_board = _normalise_board_override(board_override)
    result: Dict[str, Any] = {
        "profile": profile_id,
        "model_id": str(getattr(profile, "model_id", "")),
        "revision": str(getattr(profile, "revision", "")),
        "board": observed_board or profile_board,
        "profile_board": profile_board,
        "profile_status": profile_status,
        "model_root": str(root),
        "cache_dir": cache_dir,
        "checked": 0,
        "verified": 0,
        "artifacts": [],
        "errors": [],
    }
    if observed_board is not None:
        result["board_source"] = "observed_override"
        result["observed_board"] = dict(observed_board)

    if not raw_artifacts:
        result["status"] = "failed"
        result["errors"].append("profile declares no artifacts")
        result["artifact_verified"] = False
        return result

    seen_paths: Set[str] = set()
    for index, artifact in enumerate(raw_artifacts):
        name = str(getattr(artifact, "name", "artifact-%d" % index))
        kind = str(getattr(artifact, "kind", "unknown"))
        filename = str(getattr(artifact, "filename", ""))
        expected_bytes = getattr(artifact, "expected_bytes", None)
        expected_sha = getattr(artifact, "sha256", None)
        relative = "%s/%s" % (cache_dir.strip("/"), filename.lstrip("/"))
        entry = _artifact_result(
            name=name,
            kind=kind,
            relative=relative,
            expected_bytes=expected_bytes,
            expected_sha256=str(expected_sha).lower() if expected_sha is not None else None,
        )
        result["artifacts"].append(entry)
        try:
            path, canonical_relative = _resolve_artifact(root, cache_dir, filename)
            entry["path"] = canonical_relative
            if canonical_relative in seen_paths:
                raise VerificationError("duplicate artifact path: %s" % canonical_relative)
            seen_paths.add(canonical_relative)
            if expected_bytes is None or expected_sha is None:
                entry["status"] = "unverified"
                entry["errors"].append("registry artifact is missing expected_bytes or sha256")
                continue
            if path.is_symlink():
                raise VerificationError("symlink artifact is not allowed: %s" % canonical_relative)
            try:
                mode = path.lstat().st_mode
            except OSError as exc:
                raise VerificationError("artifact is missing: %s" % canonical_relative) from exc
            if not stat.S_ISREG(mode):
                raise VerificationError("artifact is not a regular file: %s" % canonical_relative)
            if not path.exists():
                raise VerificationError("artifact is missing: %s" % canonical_relative)
            actual_bytes, actual_sha = sha256_file(path)
            result["checked"] += 1
            entry["actual_bytes"] = actual_bytes
            entry["actual_sha256"] = actual_sha
            if actual_bytes != int(expected_bytes):
                entry["errors"].append(
                    "byte count mismatch: expected %d, got %d" % (int(expected_bytes), actual_bytes)
                )
            if actual_sha.lower() != str(expected_sha).lower():
                entry["errors"].append(
                    "sha256 mismatch: expected %s, got %s" % (str(expected_sha).lower(), actual_sha)
                )
            if entry["errors"]:
                entry["status"] = "failed"
            else:
                entry["status"] = "verified"
                result["verified"] += 1
        except VerificationError as exc:
            entry["status"] = "failed"
            entry["errors"].append(str(exc))

    # A deliberately mutable/unreachable profile is reported as blocked rather
    # than accidentally promoted by a successful empty-directory check.  Its
    # missing lock values remain visible in each artifact entry.
    if profile_status in {"blocked", "not-run"}:
        result["status"] = profile_status
    elif result["errors"] or any(item["status"] in {"failed", "unverified"} for item in result["artifacts"]):
        result["status"] = "failed"
    else:
        result["status"] = "passed"
    # Surface artifact-level errors at the top level while retaining their
    # structured location for callers that need to display a detailed report.
    for item in result["artifacts"]:
        for error in item["errors"]:
            result["errors"].append("%s: %s" % (item["name"], error))
    result["verified"] = sum(item["status"] == "verified" for item in result["artifacts"])
    result["checked"] = sum(item["actual_sha256"] is not None for item in result["artifacts"])
    # Artifact integrity is independent from the profile admission/status
    # gate. A pinned file set may be fully verified while the profile remains
    # deliberately ``blocked`` or ``not-run`` for hardware reasons.
    result["artifact_verified"] = bool(result["artifacts"]) and all(
        item["status"] == "verified" for item in result["artifacts"]
    )
    return result


def _load_registry(path: Any) -> Any:
    if str(REPO_DIR) not in sys.path:
        sys.path.insert(0, str(REPO_DIR))
    try:
        from case9_model_profiles import load_profiles

        return load_profiles(path)
    except Exception as exc:
        raise VerificationError("could not load profile registry: %s" % exc) from exc


def verify_profile_id(
    profile_id: str,
    *,
    registry_path: Any = DEFAULT_REGISTRY,
    model_root: Any = DEFAULT_MODEL_ROOT,
    board_override: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Load a profile from the strict registry and verify its local artifacts."""

    registry = _load_registry(registry_path)
    try:
        profile = registry.get(profile_id)
    except Exception as exc:
        raise VerificationError("unknown profile: %s" % profile_id) from exc
    return verify_profile_artifacts(profile, model_root, board_override=board_override)


def verify_all(
    *,
    registry_path: Any = DEFAULT_REGISTRY,
    model_root: Any = DEFAULT_MODEL_ROOT,
    board_override: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Verify every registry profile, preserving blocked/not-run evidence."""

    registry = _load_registry(registry_path)
    reports = [verify_profile_artifacts(profile, model_root, board_override=board_override) for profile in registry]
    passed = all(item.get("status") == "passed" for item in reports)
    return {
        "status": "passed" if passed else "failed",
        "profiles": reports,
        "model_root": str(_regular_directory(model_root, "model root")),
    }


# Short aliases are useful to board-side callers and keep the public helper
# surface unsurprising without introducing a second implementation.
verify_profile = verify_profile_id
verify = verify_profile_id


def _write_json_atomic(path_value: Any, payload: Mapping[str, Any]) -> Path:
    """Write a report without following an existing symlink target."""

    if not isinstance(path_value, (str, Path)) or not str(path_value).strip():
        raise VerificationError("output must be a non-empty path")
    path = _absolute(Path(path_value).expanduser())
    parent = path.parent
    # Creating a report directory is the only filesystem mutation performed by
    # this tool, and it is restricted to the explicitly selected output path.
    _reject_symlink_components(parent)
    parent.mkdir(parents=True, exist_ok=True)
    _reject_symlink_components(parent)
    if path.exists() and path.is_symlink():
        raise VerificationError("output must not be a symlink: %s" % path)
    if path.exists() and not path.is_file():
        raise VerificationError("output must be a regular file: %s" % path)
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".%s." % path.name, suffix=".part", dir=str(parent))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists() and path.is_symlink():
            raise VerificationError("output became a symlink while writing: %s" % path)
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    return path


def _timestamp() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _build_report(args: argparse.Namespace) -> Dict[str, Any]:
    root = args.root.expanduser() if isinstance(args.root, Path) else Path(args.root).expanduser()
    board_override = None
    observed_values = (args.observed_board_host, args.observed_board_soc, args.observed_board_tier)
    if any(value is not None for value in observed_values):
        if not all(isinstance(value, str) and value.strip() for value in observed_values):
            raise VerificationError(
                "--observed-board-host, --observed-board-soc and --observed-board-tier must be supplied together"
            )
        board_override = {
            "host": args.observed_board_host,
            "soc": args.observed_board_soc,
            "tier": args.observed_board_tier,
        }
    if args.all_profiles:
        payload = verify_all(registry_path=args.registry, model_root=root, board_override=board_override)
    else:
        payload = verify_profile_id(
            args.profile,
            registry_path=args.registry,
            model_root=root,
            board_override=board_override,
        )
    return {
        "schema_version": 1,
        "checked_at": _timestamp(),
        "registry": str(Path(args.registry).expanduser()),
        **payload,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile_name", nargs="?", help="profile id (alternative to --profile)")
    parser.add_argument("--profile", dest="profile", help="profile id to verify")
    parser.add_argument("--all", dest="all_profiles", action="store_true", help="verify every registry profile")
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY, help="strict profile registry JSON")
    parser.add_argument(
        "--root", "--model-root", dest="root", type=Path,
        default=Path(os.environ.get("CASE9_MODEL_ROOT", str(DEFAULT_MODEL_ROOT))),
        help="deployment/model root containing each profile cache_dir",
    )
    parser.add_argument(
        "--observed-board-host",
        help="observed board host for provenance when it differs from the profile's primary board",
    )
    parser.add_argument(
        "--observed-board-soc",
        help="observed board SoC (must be supplied with host and tier)",
    )
    parser.add_argument(
        "--observed-board-tier",
        help="observed board compute tier (must be supplied with host and SoC)",
    )
    parser.add_argument("--output", type=Path, help="also write the JSON report atomically")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    selected = args.profile or args.profile_name
    if args.all_profiles and selected:
        parser.error("--all cannot be combined with a profile id")
    if not args.all_profiles and not selected:
        parser.error("provide --profile PROFILE, a positional PROFILE, or --all")
    args.profile = selected
    try:
        payload = _build_report(args)
        if args.output is not None:
            output_path = _write_json_atomic(args.output, payload)
            payload["report_path"] = str(output_path)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0 if payload.get("status") == "passed" else 1
    except (OSError, VerificationError) as exc:
        error = {"schema_version": 1, "status": "error", "error": str(exc)}
        print(json.dumps(error, ensure_ascii=False, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
