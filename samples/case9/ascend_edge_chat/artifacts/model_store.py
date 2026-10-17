"""Verified model acquisition for the released Ascend Edge Chat profile."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping, Optional
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen

from ..backends.model_profiles import (
    ArtifactSpec,
    ChatModelProfile,
    ProfileError,
    load_profiles,
    resolve_safe_path,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL_CONFIG = PROJECT_ROOT / "configs" / "release-model.json"
DEFAULT_LOCK = PROJECT_ROOT / "configs" / "release-artifacts.lock.json"
DEFAULT_MIRROR = "https://hf-mirror.com"
RELEASE_PROFILE_ID = "qwen1.5-0.5b-chat"
_CHUNK_SIZE = 1024 * 1024


class ArtifactError(RuntimeError):
    """A model file is missing, malformed, or does not match the release lock."""


@dataclass(frozen=True)
class VerifiedArtifact:
    name: str
    path: Path
    expected_bytes: int
    sha256: str


def release_profile(config_path: Path = DEFAULT_MODEL_CONFIG) -> ChatModelProfile:
    try:
        registry = load_profiles(config_path)
        profile = registry.get(RELEASE_PROFILE_ID)
    except (OSError, ProfileError) as exc:
        raise ArtifactError("could not load release model config: %s" % exc) from exc
    if len(registry) != 1:
        raise ArtifactError("release model config must contain exactly one profile")
    return profile


def data_root(value: Optional[str] = None) -> Path:
    raw = value or os.environ.get("ASCEND_EDGE_CHAT_DATA_DIR", "")
    if not raw.strip():
        raise ArtifactError("ASCEND_EDGE_CHAT_DATA_DIR or --data-dir must be set")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ArtifactError("data directory must be an absolute path")
    if candidate.exists() and candidate.is_symlink():
        raise ArtifactError("data directory must not be a symlink")
    return candidate.resolve(strict=False)


def artifact_directory(profile: ChatModelProfile, root: Path) -> Path:
    try:
        return resolve_safe_path(profile.cache_dir, root, allow_missing=True)
    except ProfileError as exc:
        raise ArtifactError(str(exc)) from exc


def verify_release_lock(config_path: Path = DEFAULT_MODEL_CONFIG, lock_path: Path = DEFAULT_LOCK) -> None:
    profile = release_profile(config_path)
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ArtifactError("invalid release artifact lock: %s" % exc) from exc
    if not isinstance(lock, Mapping):
        raise ArtifactError("release artifact lock must be an object")
    expected = {
        "schema_version": 1,
        "project": "Ascend Edge Chat",
        "profile": profile.id,
        "revision": profile.revision,
        "license": profile.license,
        "model_config": "configs/release-model.json",
    }
    for key, value in expected.items():
        if lock.get(key) != value:
            raise ArtifactError("release artifact lock %s does not match model config" % key)
    license_url = lock.get("license_url")
    if not isinstance(license_url, str) or not license_url.startswith("https://"):
        raise ArtifactError("release artifact lock must contain an HTTPS license_url")


def verify_artifacts(
    root: Path,
    *,
    config_path: Path = DEFAULT_MODEL_CONFIG,
) -> list[VerifiedArtifact]:
    verify_release_lock(config_path)
    profile = release_profile(config_path)
    directory = artifact_directory(profile, root)
    verified: list[VerifiedArtifact] = []
    for artifact in profile.artifacts:
        _require_locked(artifact)
        target = _artifact_target(directory, artifact)
        _verify_file(target, artifact)
        verified.append(
            VerifiedArtifact(artifact.name, target, artifact.expected_bytes, artifact.sha256)
        )
    return verified


def download_artifacts(
    root: Path,
    *,
    mirror: str = DEFAULT_MIRROR,
    config_path: Path = DEFAULT_MODEL_CONFIG,
) -> list[VerifiedArtifact]:
    verify_release_lock(config_path)
    profile = release_profile(config_path)
    root.mkdir(parents=True, exist_ok=True)
    directory = artifact_directory(profile, root)
    directory.mkdir(parents=True, exist_ok=True)
    for artifact in profile.artifacts:
        _require_locked(artifact)
        target = _artifact_target(directory, artifact)
        if target.exists():
            _verify_file(target, artifact)
            continue
        _download_one(_mirror_url(artifact.url, mirror), target, artifact)
    return verify_artifacts(root, config_path=config_path)


def import_artifacts(
    root: Path,
    source: Path,
    *,
    config_path: Path = DEFAULT_MODEL_CONFIG,
) -> list[VerifiedArtifact]:
    verify_release_lock(config_path)
    if not source.is_absolute() or not source.is_dir() or source.is_symlink():
        raise ArtifactError("offline source must be an absolute regular directory")
    profile = release_profile(config_path)
    root.mkdir(parents=True, exist_ok=True)
    directory = artifact_directory(profile, root)
    directory.mkdir(parents=True, exist_ok=True)
    source_root = source.resolve()
    for artifact in profile.artifacts:
        _require_locked(artifact)
        origin = source_root / artifact.filename
        try:
            resolved = origin.resolve(strict=True)
            resolved.relative_to(source_root)
        except (OSError, ValueError) as exc:
            raise ArtifactError("offline artifact is outside source directory: %s" % artifact.filename) from exc
        if resolved.is_symlink() or not resolved.is_file():
            raise ArtifactError("offline artifact must be a regular file: %s" % artifact.filename)
        _verify_file(resolved, artifact)
        target = _artifact_target(directory, artifact)
        if target.exists():
            _verify_file(target, artifact)
            continue
        _copy_one(resolved, target, artifact)
    return verify_artifacts(root, config_path=config_path)


def report(artifacts: Iterable[VerifiedArtifact]) -> dict[str, Any]:
    return {
        "profile": RELEASE_PROFILE_ID,
        "artifacts": [
            {
                "name": item.name,
                "path": str(item.path),
                "bytes": item.expected_bytes,
                "sha256": item.sha256,
            }
            for item in artifacts
        ],
    }


def _artifact_target(directory: Path, artifact: ArtifactSpec) -> Path:
    try:
        return resolve_safe_path(artifact.filename, directory, allow_missing=True)
    except ProfileError as exc:
        raise ArtifactError(str(exc)) from exc


def _require_locked(artifact: ArtifactSpec) -> None:
    if artifact.expected_bytes is None or artifact.sha256 is None:
        raise ArtifactError("release artifact lacks size or SHA-256: %s" % artifact.name)


def _verify_file(path: Path, artifact: ArtifactSpec) -> None:
    _require_locked(artifact)
    if path.is_symlink() or not path.is_file():
        raise ArtifactError("missing regular artifact: %s" % path)
    size = path.stat().st_size
    if size != artifact.expected_bytes:
        raise ArtifactError(
            "artifact size mismatch for %s: expected %d, found %d"
            % (artifact.name, artifact.expected_bytes, size)
        )
    digest = _sha256(path)
    if digest.lower() != artifact.sha256.lower():
        raise ArtifactError("artifact SHA-256 mismatch for %s" % artifact.name)


def _download_one(url: str, target: Path, artifact: ArtifactSpec) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(".%s.part" % target.name)
    if temporary.exists():
        temporary.unlink()
    request = Request(url, headers={"User-Agent": "ascend-edge-chat/0.1.0"})
    try:
        with urlopen(request, timeout=60) as response, temporary.open("wb") as output:
            length = response.headers.get("Content-Length")
            if length is None or int(length) != artifact.expected_bytes:
                raise ArtifactError("download Content-Length mismatch for %s" % artifact.name)
            while True:
                chunk = response.read(_CHUNK_SIZE)
                if not chunk:
                    break
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        _verify_file(temporary, artifact)
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _copy_one(source: Path, target: Path, artifact: ArtifactSpec) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".%s." % target.name, suffix=".part", dir=str(target.parent)
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_stream:
            shutil.copyfileobj(input_stream, output, length=_CHUNK_SIZE)
            output.flush()
            os.fsync(output.fileno())
        _verify_file(temporary, artifact)
        os.replace(temporary, target)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def _mirror_url(url: str, mirror: str) -> str:
    original = urlsplit(url)
    endpoint = urlsplit(mirror)
    if original.scheme != "https" or original.netloc != "huggingface.co":
        raise ArtifactError("release artifact URL must be an HTTPS Hugging Face URL")
    if endpoint.scheme != "https" or not endpoint.netloc or endpoint.path not in {"", "/"}:
        raise ArtifactError("mirror must be an HTTPS origin without a path")
    return urlunsplit((endpoint.scheme, endpoint.netloc, original.path, original.query, ""))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()
