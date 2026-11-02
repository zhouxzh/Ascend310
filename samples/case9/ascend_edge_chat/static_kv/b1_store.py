"""Verified Hugging Face acquisition for the 20T B1 offline ACL candidate.

This store is deliberately separate from the formal B4 release store.  It
accepts no caller-supplied repository, revision, URL, path, or hash: every
remote fact is frozen in a B1 source-download lock produced by the publisher.
Downloaded assets are labelled ``hf-experimental`` and still require the B1
runtime lock and current-board ACL gates before they can be selected.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

from .candidates import CandidateError, DEFAULT_CANDIDATE_CATALOG, load_acl_candidate


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_B1_DOWNLOAD_LOCK = PROJECT_ROOT / "configs" / "20t-acl-b1-download.lock.json"
DEFAULT_HF_MIRROR = "https://hf-mirror.com"
B1_CANDIDATE_ID = "qwen2.5-0.5b-instruct-static-kv-1024-b1-offline"
_CHUNK_SIZE = 1024 * 1024


class B1DownloadError(RuntimeError):
    """The B1 source-download lock or downloaded artifact is invalid."""


@dataclass(frozen=True)
class B1Artifact:
    name: str
    source_path: str
    destination: str
    expected_bytes: int
    sha256: str


@dataclass(frozen=True)
class B1DownloadLock:
    repository: str
    revision: str
    artifacts: tuple[B1Artifact, ...]


def _safe_relative(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise B1DownloadError("%s must be a non-empty POSIX relative path" % label)
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise B1DownloadError("%s escapes its declared root" % label)
    return path


def _sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64:
        raise B1DownloadError("%s must be a SHA-256 digest" % label)
    lowered = value.lower()
    if any(character not in "0123456789abcdef" for character in lowered):
        raise B1DownloadError("%s must be a SHA-256 digest" % label)
    return lowered


def _bytes(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise B1DownloadError("%s must be a positive integer" % label)
    return value


def load_b1_download_lock(path: Path = DEFAULT_B1_DOWNLOAD_LOCK) -> B1DownloadLock:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise B1DownloadError("B1 source-download lock is unreadable") from exc
    if not isinstance(raw, Mapping) or raw.get("schema_version") != 1:
        raise B1DownloadError("B1 source-download lock schema_version must be 1")
    if raw.get("candidate_id") != B1_CANDIDATE_ID or raw.get("release_status") != "experimental-machine":
        raise B1DownloadError("B1 source-download lock candidate status is invalid")
    source = raw.get("source")
    target = raw.get("target")
    if not isinstance(source, Mapping) or not isinstance(target, Mapping):
        raise B1DownloadError("B1 source-download lock source and target are required")
    repository = source.get("repository")
    revision = source.get("revision")
    if not isinstance(repository, str) or repository != "zhouxzh/ascend310b-llm-om-zoo":
        raise B1DownloadError("B1 source-download lock repository is invalid")
    if not isinstance(revision, str) or len(revision) != 40 or any(c not in "0123456789abcdef" for c in revision.lower()):
        raise B1DownloadError("B1 source-download lock revision must be a 40-character commit")
    if source.get("mirror") != DEFAULT_HF_MIRROR:
        raise B1DownloadError("B1 source-download lock mirror is invalid")
    if dict(target) != {"soc": "Ascend310B1", "tier": "20T", "cann": "8.0.0", "python": "3.9.25"}:
        raise B1DownloadError("B1 source-download lock target tuple is invalid")
    entries = raw.get("artifacts")
    if not isinstance(entries, list) or len(entries) != 9:
        raise B1DownloadError("B1 source-download lock must contain exactly nine artifacts")
    artifacts: list[B1Artifact] = []
    names: set[str] = set()
    destinations: set[str] = set()
    for index, item in enumerate(entries):
        if not isinstance(item, Mapping) or set(item) != {"name", "source_path", "destination", "bytes", "sha256"}:
            raise B1DownloadError("B1 artifact[%d] has invalid fields" % index)
        name = item.get("name")
        if not isinstance(name, str) or not name or name in names:
            raise B1DownloadError("B1 artifact names must be unique")
        source_path = _safe_relative(item.get("source_path"), "B1 source path").as_posix()
        destination = _safe_relative(item.get("destination"), "B1 destination").as_posix()
        if destination in destinations:
            raise B1DownloadError("B1 artifact destinations must be unique")
        names.add(name)
        destinations.add(destination)
        artifacts.append(B1Artifact(name, source_path, destination, _bytes(item.get("bytes"), "B1 bytes"), _sha256(item.get("sha256"), "B1 SHA-256")))
    required = {
        "onnx", "tokenizer", "tokenizer-config", "license", "controller-contract",
        "b1-om", "b1-descriptor-contract", "tokenizer-lock", "b1-om-upstream-lock",
    }
    if names != required:
        raise B1DownloadError("B1 source-download lock artifact names are incomplete")
    return B1DownloadLock(repository, revision, tuple(artifacts))


def _root(value: str | Path) -> Path:
    root = Path(value).expanduser()
    if not root.is_absolute() or root.is_symlink():
        raise B1DownloadError("B1 candidate data directory must be an absolute regular directory")
    return root.resolve(strict=False)


def _target(root: Path, artifact: B1Artifact) -> Path:
    path = (root / _safe_relative(artifact.destination, "B1 destination")).resolve(strict=False)
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise B1DownloadError("B1 artifact destination escapes candidate data directory") from exc
    return path


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            chunk = stream.read(_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _verify(path: Path, artifact: B1Artifact) -> None:
    if path.is_symlink() or not path.is_file():
        raise B1DownloadError("B1 artifact is missing: %s" % artifact.name)
    if path.stat().st_size != artifact.expected_bytes:
        raise B1DownloadError("B1 artifact byte size mismatch: %s" % artifact.name)
    if _hash(path) != artifact.sha256:
        raise B1DownloadError("B1 artifact SHA-256 mismatch: %s" % artifact.name)


def _url(lock: B1DownloadLock, artifact: B1Artifact) -> str:
    repository = quote(lock.repository, safe="/")
    revision = quote(lock.revision, safe="")
    source_path = quote(artifact.source_path, safe="/")
    return "%s/%s/resolve/%s/%s" % (DEFAULT_HF_MIRROR, repository, revision, source_path)


def _download(url: str, destination: Path, artifact: B1Artifact) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".%s." % destination.name, suffix=".part", dir=str(destination.parent))
    temporary = Path(temporary_name)
    try:
        request = Request(url, headers={"User-Agent": "ascend-edge-chat/20t-b1-acl"})
        with urlopen(request, timeout=120) as response, os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            length = response.headers.get("Content-Length")
            if length is not None and int(length) != artifact.expected_bytes:
                raise B1DownloadError("download Content-Length mismatch for %s" % artifact.name)
            while True:
                chunk = response.read(_CHUNK_SIZE)
                if not chunk:
                    break
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        _verify(temporary, artifact)
        os.replace(temporary, destination)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def download_b1_candidate(
    root: str | Path,
    *,
    lock_path: Path = DEFAULT_B1_DOWNLOAD_LOCK,
) -> dict[str, Any]:
    """Download and verify all fixed-revision B1 candidate assets atomically."""

    candidate_root = _root(root)
    lock = load_b1_download_lock(lock_path)
    candidate_root.mkdir(parents=True, exist_ok=True)
    for artifact in lock.artifacts:
        destination = _target(candidate_root, artifact)
        if destination.exists():
            _verify(destination, artifact)
        else:
            _download(_url(lock, artifact), destination, artifact)
    try:
        candidate = load_acl_candidate(B1_CANDIDATE_ID, data_dir=candidate_root)
    except CandidateError as exc:
        raise B1DownloadError("B1 runtime candidate gate failed after download") from exc
    return {
        "source_kind": "hf-experimental",
        "repository": lock.repository,
        "revision": lock.revision,
        "candidate": candidate.id,
        "data_dir": str(candidate_root),
        "artifacts": [{"name": item.name, "bytes": item.expected_bytes, "sha256": item.sha256} for item in lock.artifacts],
    }


__all__ = ["B1DownloadError", "B1DownloadLock", "B1Artifact", "B1_CANDIDATE_ID", "DEFAULT_B1_DOWNLOAD_LOCK", "download_b1_candidate", "load_b1_download_lock"]
