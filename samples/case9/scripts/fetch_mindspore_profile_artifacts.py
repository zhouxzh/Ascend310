#!/usr/bin/env python3
"""Fetch and lock one native MindSpore chat profile without changing packages.

The command is intended for an activated board environment.  It writes only
the selected profile cache below ``--root`` and a caller-selected lock JSON.
Downloads use ``.part`` files and are promoted atomically after their byte
count and SHA-256 have been checked.  A registry's existing fixed digest is a
hard constraint; an unpinned ``main`` revision is resolved once through the
Hugging Face model API and recorded in the generated lock, never used as a
mutable runtime reference.
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Tuple
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


CHUNK_BYTES = 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


class FetchError(RuntimeError):
    """A source, path, download, or integrity check failed."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_registry(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FetchError("cannot read registry %s: %s" % (path, exc)) from exc
    if not isinstance(payload, Mapping) or not isinstance(payload.get("profiles"), list):
        raise FetchError("registry has no profiles array")
    return payload


def _profile(payload: Mapping[str, Any], profile_id: str) -> Mapping[str, Any]:
    for item in payload["profiles"]:
        if isinstance(item, Mapping) and item.get("id") == profile_id:
            if item.get("candidate_kind") != "native_mindspore":
                raise FetchError("profile is not a native MindSpore candidate: %s" % profile_id)
            return item
    raise FetchError("profile not found: %s" % profile_id)


def _safe_relative(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise FetchError("%s is not a safe relative path" % label)
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise FetchError("%s is not a safe relative path" % label)
    return path


def _root(path: Path) -> Path:
    lexical = Path(os.path.abspath(os.fspath(path.expanduser())))
    if lexical.is_symlink():
        raise FetchError("root may not be a symlink: %s" % lexical)
    lexical.mkdir(parents=True, exist_ok=True)
    if not lexical.is_dir():
        raise FetchError("root is not a directory: %s" % lexical)
    return lexical.resolve()


def _below(root: Path, relative: Path) -> Path:
    target = root / relative
    try:
        target.relative_to(root)
    except ValueError as exc:  # pragma: no cover - defensive
        raise FetchError("artifact escapes root: %s" % relative) from exc
    current = root
    for component in relative.parts[:-1]:
        current = current / component
        if current.is_symlink():
            raise FetchError("artifact directory may not be a symlink: %s" % current)
        current.mkdir(exist_ok=True)
    if target.exists() and target.is_symlink():
        raise FetchError("artifact may not be a symlink: %s" % target)
    return target


def _hash(path: Path) -> Tuple[int, str]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or path.is_symlink():
        raise FetchError("artifact is not a regular file: %s" % path)
    digest = hashlib.sha256()
    length = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(CHUNK_BYTES), b""):
            length += len(block)
            digest.update(block)
    return length, digest.hexdigest()


def _hf_endpoint() -> str:
    """Return the selected Hugging Face endpoint without a trailing slash."""
    endpoint = os.environ.get("HF_ENDPOINT", "https://huggingface.co").strip()
    if not endpoint:
        endpoint = "https://huggingface.co"
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.query or parsed.fragment:
        raise FetchError("HF_ENDPOINT must be an absolute http(s) URL")
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _mirror_url(url: str, *, mirror: Optional[str] = None, repository: Optional[str] = None, revision: Optional[str] = None) -> str:
    """Route a Hub URL through the profile-selected mirror.

    ``HF_ENDPOINT`` is an endpoint override for Hugging Face repositories.
    MindNLP's named mirrors (notably ``modelers``) use a different URL
    layout, so they must be constructed from the repository, revision and
    file path instead of merely replacing the hostname.
    """
    endpoint = _hf_endpoint()
    source = urlsplit(url)
    if source.scheme not in {"http", "https"} or not source.netloc:
        raise FetchError("artifact URL is not an absolute HTTP(S) URL: %s" % url)
    if source.netloc not in {"huggingface.co", "www.huggingface.co"}:
        return url
    selected = str(mirror or "").strip().lower()
    if selected == "hf-mirror.com":
        selected = "huggingface"
    if selected == "modelers":
        if not repository or not revision:
            raise FetchError("modelers mirror requires repository and immutable revision")
        marker = "/resolve/"
        if marker not in source.path:
            raise FetchError("cannot derive modelers filename from URL: %s" % url)
        relative = source.path.split(marker, 1)[1]
        parts = relative.split("/", 1)
        if len(parts) != 2 or not parts[1]:
            raise FetchError("cannot derive modelers filename from URL: %s" % url)
        filename = parts[1]
        return "https://modelers.cn/coderepo/web/v1/file/%s/%s/media/%s" % (
            quote(str(repository).strip(), safe="/-_."),
            quote(str(revision).strip(), safe="-_."),
            quote(filename, safe="/-_."),
        )
    base = urlsplit(endpoint)
    path = (base.path.rstrip("/") + source.path) or "/"
    return urlunsplit((base.scheme, base.netloc, path, source.query, source.fragment))


def _model_api_revision(repository: str) -> str:
    url = _mirror_url("https://huggingface.co/api/models/%s" % quote(repository, safe="/"))
    try:
        with urlopen(Request(url, headers={"User-Agent": "case9-artifact-lock/1"}), timeout=45) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, OSError, ValueError) as exc:
        raise FetchError("could not resolve Hugging Face revision for %s: %s" % (repository, exc)) from exc
    revision = payload.get("sha") if isinstance(payload, Mapping) else None
    if not isinstance(revision, str) or not COMMIT_RE.fullmatch(revision):
        raise FetchError("Hugging Face API returned no immutable revision for %s" % repository)
    return revision


def _pinned_url(url: str, revision: str, *, mirror: Optional[str] = None, repository: Optional[str] = None) -> str:
    marker = "/resolve/main/"
    if marker in url:
        url = url.replace(marker, "/resolve/%s/" % revision, 1)
    return _mirror_url(url, mirror=mirror, repository=repository, revision=revision)


def _download(url: str, destination: Path) -> Tuple[int, str, Optional[str]]:
    part = destination.with_name(destination.name + ".part")
    if part.exists() or part.is_symlink():
        raise FetchError("partial file already exists; inspect before retrying: %s" % part)
    try:
        request = Request(url, headers={"User-Agent": "case9-artifact-lock/1"})
        with urlopen(request, timeout=120) as response, part.open("xb") as output:
            header_length = response.headers.get("Content-Length")
            expected_transport_length = int(header_length) if header_length and header_length.isdigit() else None
            digest = hashlib.sha256()
            length = 0
            for block in iter(lambda: response.read(CHUNK_BYTES), b""):
                output.write(block)
                length += len(block)
                digest.update(block)
            if expected_transport_length is not None and length != expected_transport_length:
                raise FetchError("HTTP Content-Length mismatch for %s: %d != %d" % (url, length, expected_transport_length))
            commit = response.headers.get("X-Repo-Commit")
        os.replace(part, destination)
        return length, digest.hexdigest(), commit
    except FetchError:
        # Retain the .part file for diagnosis; it is never treated as an artifact.
        raise
    except (HTTPError, URLError, OSError, ValueError) as exc:
        # A board-side transport failure is an artifact-acquisition result,
        # not a framework or model compatibility result.  Convert it into the
        # command's stable error contract while retaining any .part evidence.
        raise FetchError("could not download %s: %s" % (url, exc)) from exc


def _verify_known(name: str, actual_size: int, actual_digest: str, artifact: Mapping[str, Any]) -> None:
    expected_size = artifact.get("expected_bytes")
    expected_digest = artifact.get("sha256")
    if expected_size is not None and actual_size != expected_size:
        raise FetchError("%s byte count mismatch: %s != %s" % (name, actual_size, expected_size))
    if expected_digest is not None:
        if not isinstance(expected_digest, str) or not SHA256_RE.fullmatch(expected_digest.lower()):
            raise FetchError("%s registry SHA-256 is malformed" % name)
        if actual_digest != expected_digest.lower():
            raise FetchError("%s SHA-256 mismatch" % name)


def fetch(profile: Mapping[str, Any], root: Path, *, resolve_main: bool) -> Dict[str, Any]:
    revision = profile.get("revision")
    if not isinstance(revision, str):
        raise FetchError("profile revision is missing")
    if revision == "main":
        if not resolve_main:
            raise FetchError("profile uses mutable main; pass --resolve-main to lock it first")
        if str(profile.get("mirror", "")).strip().lower() == "modelers":
            raise FetchError(
                "modelers profile uses mutable main; supply an immutable Modelers revision "
                "instead of resolving through the Hugging Face API"
            )
        revision = _model_api_revision(str(profile.get("repository", "")))
    if not COMMIT_RE.fullmatch(revision):
        raise FetchError("profile revision is not a 40-hex commit")
    cache = _safe_relative(profile.get("cache_dir"), "cache_dir")
    results = []
    artifacts = profile.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        raise FetchError("profile has no artifacts")
    for artifact in artifacts:
        if not isinstance(artifact, Mapping):
            raise FetchError("artifact is not an object")
        name = artifact.get("name")
        filename = artifact.get("filename")
        url = artifact.get("url")
        if not all(isinstance(value, str) and value for value in (name, filename, url)):
            raise FetchError("artifact lacks name, filename, or URL")
        destination = _below(root, cache / _safe_relative(filename, "artifact.filename"))
        source_url = _pinned_url(
            url,
            revision,
            mirror=profile.get("mirror"),
            repository=str(profile.get("repository", "")),
        )
        if destination.exists():
            size, digest = _hash(destination)
            source_commit = None
            action = "reused"
        else:
            size, digest, source_commit = _download(source_url, destination)
            action = "downloaded"
        _verify_known(name, size, digest, artifact)
        results.append({
            "name": name, "kind": artifact.get("kind"), "filename": filename,
            "url": source_url, "bytes": size, "sha256": digest,
            "source_commit": source_commit, "action": action,
        })
    return {
        "schema_version": 1,
        "created_at": _utc_now(),
        "profile": profile.get("id"),
        "repository": profile.get("repository"),
        "resolved_revision": revision,
        "artifacts": results,
    }


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as stream:
        json.dump(payload, stream, ensure_ascii=False, indent=2, sort_keys=True)
        stream.write("\n")
        temporary = Path(stream.name)
    os.replace(temporary, path)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--registry", type=Path, default=Path(__file__).resolve().parents[1] / "configs/chat_model_profiles.json")
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--lock-out", type=Path, required=True)
    parser.add_argument("--cache-dir", default=None, help="optional safe cache directory relative to --root")
    parser.add_argument("--resolve-main", action="store_true", help="resolve a mutable registry revision once through the HF model API")
    args = parser.parse_args(argv)
    try:
        profile = copy.deepcopy(_profile(_read_registry(args.registry), args.profile))
        if args.cache_dir is not None:
            profile["cache_dir"] = _safe_relative(args.cache_dir, "cache_dir").as_posix()
        result = fetch(profile, _root(args.root), resolve_main=args.resolve_main)
        _atomic_json(args.lock_out, result)
    except FetchError as exc:
        print("artifact fetch refused: %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
