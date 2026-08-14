"""Small fail-closed helpers for versioned JSON evidence files."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any, Callable


def encode_json(payload: object) -> bytes:
    """Return portable JSON bytes and reject NaN/Infinity before any write."""
    return (json.dumps(payload, indent=2, ensure_ascii=True, allow_nan=False) + "\n").encode("utf-8")


def _write_temporary(output: Path, encoded: bytes) -> Path:
    with tempfile.NamedTemporaryFile(
        mode="xb",
        dir=output.parent,
        prefix=f".{output.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return temporary


def _commit_new_file(temporary: Path, output: Path) -> None:
    """Publish an existing temp file without replacing a concurrent output."""
    try:
        # Same-directory hard-link creation is an atomic, non-overwriting
        # publish on the board's local filesystem and NTFS.  os.replace() is
        # intentionally unsuitable because it could destroy prior evidence.
        os.link(temporary, output)
    except FileExistsError:
        raise FileExistsError(f"refusing to overwrite existing JSON evidence: {output}") from None
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def write_new_json(path: Path, payload: object) -> Path:
    """Atomically publish a JSON record exactly once; never replace evidence."""
    output = Path(path).resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing JSON evidence: {output}")
    encoded = encode_json(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = _write_temporary(output, encoded)
    try:
        _commit_new_file(temporary, output)
    except Exception:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    return output


def write_validated_new_json(
    path: Path,
    payload: object,
    *,
    validator: Callable[[Path], Any],
) -> Path:
    """Validate a same-directory temporary file before creating the final path.

    The final exclusive create avoids overwriting another process's output.  The
    temporary file shares the final parent so relative artifact paths resolve
    exactly as they will in the finished manifest.
    """
    output = Path(path).resolve()
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing JSON evidence: {output}")
    encoded = encode_json(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = _write_temporary(output, encoded)
    try:
        validator(temporary_path)
    except Exception:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise
    try:
        _commit_new_file(temporary_path, output)
    except Exception:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass
        raise
    return output
