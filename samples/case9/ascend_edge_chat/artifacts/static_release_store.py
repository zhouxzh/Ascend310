"""Verified acquisition for the locked Ascend310B4 Static-KV release assets.

The store intentionally accepts no artifact URL, model revision, or destination
from a caller. Those facts are frozen in ``release-8t-static-kv.lock.json``.
It never invokes ACL, ATC, or an inference runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from typing import Any, Iterable, Mapping
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_STATIC_RELEASE_LOCK = PROJECT_ROOT / "configs" / "release-8t-static-kv.lock.json"
DEFAULT_HF_MIRROR = "https://hf-mirror.com"
SOURCE_REPOSITORY = "zhouxzh/ascend310b-llm-om-zoo"
SOURCE_REVISION = "0f516070e1c0566e3c652d43c488cd3cfbad89a3"
BASE_MODEL_REPOSITORY = "Qwen/Qwen2.5-0.5B-Instruct"
BASE_MODEL_REVISION = "7ae557604adf67be50417f59c2c2f167def9a775"
TARGET = {"soc": "Ascend310B4", "tier": "8T", "cann": "8.0.0", "python": "3.9.25"}
_CHUNK_SIZE = 1024 * 1024
_SHA256_LENGTH = 64


class StaticReleaseError(RuntimeError):
    """The static release lock or an acquired artifact is unsafe or invalid."""


@dataclass(frozen=True)
class StaticReleaseArtifact:
    """One immutable source-to-local artifact binding."""

    name: str
    source_path: str
    destination: str
    expected_bytes: int
    sha256: str


@dataclass(frozen=True)
class StaticReleaseLock:
    """Validated public source, target tuple, and static-KV artifact inventory."""

    source_repository: str
    source_revision: str
    base_model_repository: str
    base_model_revision: str
    target: Mapping[str, str]
    artifacts: tuple[StaticReleaseArtifact, ...]


def _safe_relative(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise StaticReleaseError("%s must be a non-empty POSIX relative path" % label)
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise StaticReleaseError("%s escapes its declared root" % label)
    return path


def _string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StaticReleaseError("%s must be a non-empty string" % label)
    return value.strip()


def _sha256(value: Any, label: str) -> str:
    value = _string(value, label).lower()
    if len(value) != _SHA256_LENGTH or any(character not in "0123456789abcdef" for character in value):
        raise StaticReleaseError("%s must be a SHA-256 digest" % label)
    return value


def _bytes(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise StaticReleaseError("%s must be a positive integer" % label)
    return value


def load_static_release_lock(lock_path: Path = DEFAULT_STATIC_RELEASE_LOCK) -> StaticReleaseLock:
    """Parse the fixed public B4 release lock without reading model artifacts."""

    try:
        raw = json.loads(Path(lock_path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StaticReleaseError("static release lock is unreadable") from exc
    if not isinstance(raw, Mapping):
        raise StaticReleaseError("static release lock must be an object")
    if raw.get("schema_version") != 1:
        raise StaticReleaseError("static release lock schema_version must be 1")
    if raw.get("project") != "Ascend Edge Chat":
        raise StaticReleaseError("static release lock project is invalid")
    if raw.get("license") != "Apache-2.0":
        raise StaticReleaseError("static release lock license must be Apache-2.0")
    source = raw.get("source")
    base_model = raw.get("base_model")
    target = raw.get("target")
    if not isinstance(source, Mapping) or not isinstance(base_model, Mapping) or not isinstance(target, Mapping):
        raise StaticReleaseError("static release lock source, base_model, and target must be objects")
    if source.get("repository") != SOURCE_REPOSITORY or source.get("revision") != SOURCE_REVISION:
        raise StaticReleaseError("static release lock does not use the pinned public artifact source")
    if source.get("mirror") != DEFAULT_HF_MIRROR:
        raise StaticReleaseError("static release lock must use the fixed Hugging Face mirror")
    if base_model.get("repository") != BASE_MODEL_REPOSITORY or base_model.get("revision") != BASE_MODEL_REVISION:
        raise StaticReleaseError("static release lock does not use the pinned Qwen base model")
    if dict(target) != TARGET:
        raise StaticReleaseError("static release lock target tuple is invalid")
    entries = raw.get("artifacts")
    if not isinstance(entries, list) or len(entries) != 9:
        raise StaticReleaseError("static release lock must contain exactly nine artifacts")
    artifacts: list[StaticReleaseArtifact] = []
    names: set[str] = set()
    destinations: set[str] = set()
    for index, item in enumerate(entries):
        if not isinstance(item, Mapping) or set(item) != {"name", "source_path", "destination", "bytes", "sha256"}:
            raise StaticReleaseError("static release artifact[%d] has invalid fields" % index)
        name = _string(item.get("name"), "artifact name")
        source_path = _safe_relative(item.get("source_path"), "artifact source_path").as_posix()
        destination = _safe_relative(item.get("destination"), "artifact destination").as_posix()
        if name in names or destination in destinations:
            raise StaticReleaseError("static release artifacts must have unique names and destinations")
        names.add(name)
        destinations.add(destination)
        artifacts.append(
            StaticReleaseArtifact(
                name=name,
                source_path=source_path,
                destination=destination,
                expected_bytes=_bytes(item.get("bytes"), "artifact bytes"),
                sha256=_sha256(item.get("sha256"), "artifact sha256"),
            )
        )
    required_names = {
        "onnx",
        "tokenizer",
        "tokenizer-config",
        "license",
        "controller-contract",
        "b4-om",
        "b4-descriptor-contract",
        "tokenizer-lock",
        "b4-om-upstream-lock",
    }
    if names != required_names:
        raise StaticReleaseError("static release lock artifact names are incomplete")
    return StaticReleaseLock(
        source_repository=SOURCE_REPOSITORY,
        source_revision=SOURCE_REVISION,
        base_model_repository=BASE_MODEL_REPOSITORY,
        base_model_revision=BASE_MODEL_REVISION,
        target=TARGET,
        artifacts=tuple(artifacts),
    )


def static_release_root(value: str | Path) -> Path:
    """Return an absolute, non-symlink root that may contain static assets."""

    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        raise StaticReleaseError("static release root must be an absolute path")
    if candidate.exists() and candidate.is_symlink():
        raise StaticReleaseError("static release root must not be a symlink")
    return candidate.resolve(strict=False)


def _reject_symlink_components(root: Path, target: Path) -> None:
    current = root
    for component in target.relative_to(root).parts:
        current = current / component
        if current.is_symlink():
            raise StaticReleaseError("symlink path component is not allowed: %s" % current)


def _target(root: Path, artifact: StaticReleaseArtifact) -> Path:
    target = root / _safe_relative(artifact.destination, "artifact destination")
    _reject_symlink_components(root, target)
    resolved = target.resolve(strict=False)
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise StaticReleaseError("artifact destination escapes release root") from exc
    return resolved


def _digest(path: Path) -> str:
    state = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(_CHUNK_SIZE), b""):
            state.update(chunk)
    return state.hexdigest()


def _verify_file(path: Path, artifact: StaticReleaseArtifact) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise StaticReleaseError("missing regular artifact: %s" % path)
    size = path.stat().st_size
    if size != artifact.expected_bytes:
        raise StaticReleaseError(
            "artifact byte size mismatch for %s: expected %d, found %d"
            % (artifact.name, artifact.expected_bytes, size)
        )
    digest = _digest(path)
    if digest != artifact.sha256:
        raise StaticReleaseError("artifact SHA-256 mismatch for %s" % artifact.name)
    return {"name": artifact.name, "path": str(path), "bytes": size, "sha256": digest}


def _write_json_if_safe(path: Path, document: Mapping[str, Any]) -> None:
    """Atomically create a deterministic runtime lock, never replacing drift."""

    encoded = json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if path.exists() or path.is_symlink():
        if path.is_symlink() or not path.is_file():
            raise StaticReleaseError("runtime lock path is not a regular file: %s" % path)
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StaticReleaseError("existing runtime lock is invalid: %s" % path) from exc
        if existing != document:
            raise StaticReleaseError("refusing to overwrite a different runtime lock: %s" % path)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(".%s.part" % path.name)
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as output:
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def materialize_static_release_runtime_locks(
    root: str | Path,
    *,
    lock_path: Path = DEFAULT_STATIC_RELEASE_LOCK,
) -> dict[str, str]:
    """Derive strict local runtime locks from verified public source artifacts.

    The public B4 OM lock remains schema-1 provenance and is kept below
    ``upstream/``. This function validates it against the locked OM, then
    writes separate sibling locks in the mutable data directory: schema-2 for
    the runtime OM and schema-1 for the tokenizer. No generated lock belongs
    in Git, and a mismatched pre-existing lock is never overwritten.
    """

    release_root = static_release_root(root)
    lock = load_static_release_lock(lock_path)
    by_name = {item.name: item for item in lock.artifacts}
    required = {
        "onnx",
        "tokenizer",
        "controller-contract",
        "b4-om",
        "b4-descriptor-contract",
        "tokenizer-lock",
        "b4-om-upstream-lock",
    }
    if not required.issubset(by_name):
        raise StaticReleaseError("static release lock is missing runtime derivation artifacts")
    paths = {name: _target(release_root, item) for name, item in by_name.items()}
    for name in required:
        _verify_file(paths[name], by_name[name])
    try:
        upstream_om_lock = json.loads(paths["b4-om-upstream-lock"].read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StaticReleaseError("public B4 OM lock is invalid") from exc
    if not isinstance(upstream_om_lock, Mapping) or upstream_om_lock.get("schema_version") != 1:
        raise StaticReleaseError("public B4 OM lock must remain schema_version 1")
    om_meta = _verify_file(paths["b4-om"], by_name["b4-om"])
    if upstream_om_lock.get("bytes") != om_meta["bytes"] or str(upstream_om_lock.get("sha256", "")).lower() != om_meta["sha256"]:
        raise StaticReleaseError("public B4 OM lock does not match the locked OM")
    controller_meta = _verify_file(paths["controller-contract"], by_name["controller-contract"])
    runtime_meta = _verify_file(paths["b4-descriptor-contract"], by_name["b4-descriptor-contract"])
    tokenizer_meta = _verify_file(paths["tokenizer"], by_name["tokenizer"])
    runtime_lock_path = _target(release_root, StaticReleaseArtifact(
        name="derived-runtime-om-lock",
        source_path="derived/runtime",
        destination="Ascend310B4/qwen25-static-kv-1024-v2.om.lock.json",
        expected_bytes=1,
        sha256="0" * _SHA256_LENGTH,
    ))
    tokenizer_lock_path = _target(release_root, StaticReleaseArtifact(
        name="derived-runtime-tokenizer-lock",
        source_path="derived/runtime",
        destination="common/tokenizer.json.lock.json",
        expected_bytes=1,
        sha256="0" * _SHA256_LENGTH,
    ))
    runtime_lock = {
        "schema_version": 2,
        "model_id": "qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om",
        "om_path": by_name["b4-om"].destination,
        "bytes": om_meta["bytes"],
        "sha256": om_meta["sha256"],
        "soc_version": TARGET["soc"],
        "chip_tier": TARGET["tier"],
        "source_onnx": {
            "path": by_name["onnx"].destination,
            "bytes": by_name["onnx"].expected_bytes,
            "sha256": by_name["onnx"].sha256,
        },
        "controller_contract_path": by_name["controller-contract"].destination,
        "controller_contract_sha256": controller_meta["sha256"],
        "contract_path": by_name["controller-contract"].destination,
        "contract_sha256": controller_meta["sha256"],
        "runtime_contract_path": by_name["b4-descriptor-contract"].destination,
        "runtime_contract_sha256": runtime_meta["sha256"],
        "source_om_lock": {
            "path": by_name["b4-om-upstream-lock"].destination,
            "schema_version": 1,
            "sha256": by_name["b4-om-upstream-lock"].sha256,
        },
        "provenance": {
            "source_kind": "hf-derived-runtime-lock",
            "repository": SOURCE_REPOSITORY,
            "revision": SOURCE_REVISION,
        },
    }
    tokenizer_lock = {
        "schema_version": 1,
        "artifact": "tokenizer.json",
        "bytes": tokenizer_meta["bytes"],
        "sha256": tokenizer_meta["sha256"],
        "provenance": {
            "source_kind": "hf-derived-runtime-lock",
            "repository": SOURCE_REPOSITORY,
            "revision": SOURCE_REVISION,
            "source_lock": by_name["tokenizer-lock"].destination,
        },
    }
    _write_json_if_safe(runtime_lock_path, runtime_lock)
    _write_json_if_safe(tokenizer_lock_path, tokenizer_lock)
    return {"runtime_om_lock": str(runtime_lock_path), "tokenizer_lock": str(tokenizer_lock_path)}


def verify_static_release_artifacts(
    root: str | Path,
    *,
    lock_path: Path = DEFAULT_STATIC_RELEASE_LOCK,
    materialize_runtime_locks: bool = True,
) -> dict[str, Any]:
    """Fail closed unless every fixed static-KV artifact matches its lock."""

    release_root = static_release_root(root)
    lock = load_static_release_lock(lock_path)
    artifacts = [_verify_file(_target(release_root, artifact), artifact) for artifact in lock.artifacts]
    result = {
        "source_kind": "verified-local",
        "source_repository": lock.source_repository,
        "source_revision": lock.source_revision,
        "target": dict(lock.target),
        "artifacts": artifacts,
    }
    if materialize_runtime_locks:
        result["runtime_locks"] = materialize_static_release_runtime_locks(release_root, lock_path=lock_path)
    return result


def _fixed_mirror_url(artifact: StaticReleaseArtifact, mirror: str) -> str:
    if mirror != DEFAULT_HF_MIRROR:
        raise StaticReleaseError("static release downloads must use https://hf-mirror.com")
    endpoint = urlsplit(mirror)
    if endpoint.scheme != "https" or endpoint.netloc != "hf-mirror.com" or endpoint.path not in {"", "/"}:
        raise StaticReleaseError("static release mirror must be exactly https://hf-mirror.com")
    quoted_path = quote(artifact.source_path, safe="/")
    path = "/%s/resolve/%s/%s" % (SOURCE_REPOSITORY, SOURCE_REVISION, quoted_path)
    return urlunsplit((endpoint.scheme, endpoint.netloc, path, "download=true", ""))


def _download_one(url: str, target: Path, artifact: StaticReleaseArtifact) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(".%s.part" % target.name)
    if temporary.exists() or temporary.is_symlink():
        temporary.unlink()
    request = Request(url, headers={"User-Agent": "ascend-edge-chat-static-release/0.1.0"})
    try:
        with urlopen(request, timeout=60) as response, temporary.open("wb") as output:
            content_length = response.headers.get("Content-Length")
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                except (TypeError, ValueError) as exc:
                    raise StaticReleaseError(
                        "download Content-Length is invalid for %s" % artifact.name
                    ) from exc
                if declared_length != artifact.expected_bytes:
                    raise StaticReleaseError(
                        "download Content-Length mismatch for %s" % artifact.name
                    )
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


def download_static_release_artifacts(
    root: str | Path,
    *,
    mirror: str = DEFAULT_HF_MIRROR,
    lock_path: Path = DEFAULT_STATIC_RELEASE_LOCK,
    materialize_runtime_locks: bool = True,
) -> dict[str, Any]:
    """Download only locked files from the fixed public HF mirror atomically."""

    release_root = static_release_root(root)
    lock = load_static_release_lock(lock_path)
    release_root.mkdir(parents=True, exist_ok=True)
    for artifact in lock.artifacts:
        target = _target(release_root, artifact)
        if target.exists():
            _verify_file(target, artifact)
            continue
        _download_one(_fixed_mirror_url(artifact, mirror), target, artifact)
    result = verify_static_release_artifacts(
        release_root,
        lock_path=lock_path,
        materialize_runtime_locks=materialize_runtime_locks,
    )
    result["source_kind"] = "hf"
    result["mirror"] = DEFAULT_HF_MIRROR
    return result


def _offline_manifest_status(source: Path, lock: StaticReleaseLock) -> str:
    manifest_path = source / "offline-bundle.manifest.json"
    if not manifest_path.is_file():
        return "offline-unattested"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise StaticReleaseError("offline bundle manifest is invalid") from exc
    if not isinstance(manifest, Mapping) or manifest.get("schema_version") != 1:
        raise StaticReleaseError("offline bundle manifest schema_version must be 1")
    if manifest.get("source_kind") != "offline-bundle":
        raise StaticReleaseError("offline bundle manifest source_kind must be offline-bundle")
    if manifest.get("repository") != lock.source_repository or manifest.get("revision") != lock.source_revision:
        raise StaticReleaseError("offline bundle manifest source does not match static release lock")
    expected = {(item.name, item.destination, item.expected_bytes, item.sha256) for item in lock.artifacts}
    entries = manifest.get("artifacts")
    observed = {
        (item.get("name"), item.get("destination"), item.get("bytes"), item.get("sha256"))
        for item in entries
        if isinstance(item, Mapping)
    } if isinstance(entries, list) else set()
    if observed != expected:
        raise StaticReleaseError("offline bundle manifest artifacts do not match static release lock")
    return "offline-bundle"


def import_static_release_artifacts(
    root: str | Path,
    source: str | Path,
    *,
    lock_path: Path = DEFAULT_STATIC_RELEASE_LOCK,
    require_manifest: bool = False,
    materialize_runtime_locks: bool = True,
) -> dict[str, Any]:
    """Copy verified offline files and label their provenance in the result.

    An omitted manifest never becomes a claim of public HF acquisition: the
    returned ``source_kind`` is ``offline-unattested``. Callers preparing a
    release report should pass ``require_manifest=True``.
    """

    release_root = static_release_root(root)
    source_root = Path(source).expanduser()
    if not source_root.is_absolute() or not source_root.is_dir() or source_root.is_symlink():
        raise StaticReleaseError("offline source must be an absolute regular directory")
    source_root = source_root.resolve()
    lock = load_static_release_lock(lock_path)
    source_kind = _offline_manifest_status(source_root, lock)
    if require_manifest and source_kind != "offline-bundle":
        raise StaticReleaseError("offline release import requires offline-bundle.manifest.json")
    release_root.mkdir(parents=True, exist_ok=True)
    for artifact in lock.artifacts:
        source_file = source_root / _safe_relative(artifact.destination, "artifact destination")
        try:
            source_file.resolve(strict=True).relative_to(source_root)
        except (OSError, ValueError) as exc:
            raise StaticReleaseError("offline artifact is outside source directory: %s" % artifact.destination) from exc
        if source_file.is_symlink():
            raise StaticReleaseError("offline artifact must not be a symlink: %s" % artifact.destination)
        _verify_file(source_file, artifact)
        target = _target(release_root, artifact)
        if target.exists():
            _verify_file(target, artifact)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".%s." % target.name, suffix=".part", dir=str(target.parent))
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output, source_file.open("rb") as input_stream:
                shutil.copyfileobj(input_stream, output, length=_CHUNK_SIZE)
                output.flush()
                os.fsync(output.fileno())
            _verify_file(temporary, artifact)
            os.replace(temporary, target)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    result = verify_static_release_artifacts(
        release_root,
        lock_path=lock_path,
        materialize_runtime_locks=materialize_runtime_locks,
    )
    result["source_kind"] = source_kind
    return result


__all__ = [
    "BASE_MODEL_REPOSITORY",
    "BASE_MODEL_REVISION",
    "DEFAULT_HF_MIRROR",
    "DEFAULT_STATIC_RELEASE_LOCK",
    "SOURCE_REPOSITORY",
    "SOURCE_REVISION",
    "StaticReleaseArtifact",
    "StaticReleaseError",
    "StaticReleaseLock",
    "download_static_release_artifacts",
    "import_static_release_artifacts",
    "load_static_release_lock",
    "materialize_static_release_runtime_locks",
    "static_release_root",
    "verify_static_release_artifacts",
]
