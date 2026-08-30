"""Strict registry and state helpers for the experimental MindSpore chat models.

The registry is intentionally independent from the gateway's public model id.
It contains provenance and board constraints, while ``public_profiles`` emits a
small, path-free view that is safe for the browser model/status display.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import argparse
import math
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple, Union


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_REGISTRY_PATH = BASE_DIR / "configs" / "chat_model_profiles.json"
DEFAULT_STATE_PATH = BASE_DIR / "run" / "mindspore-chat" / "active-model.json"

ALLOWED_STATUSES = frozenset(
    {
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
        "blocked",
        "not-run",
    }
)
_PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_REVISION_RE = re.compile(r"^(?:[0-9a-fA-F]{40}|main)$")
_SAFE_RELATIVE_RE = re.compile(r"^[^\\/\x00:]+(?:/[^\\/\x00:]+)*$")


class ProfileError(ValueError):
    """Raised when registry or active-state data is unsafe or malformed."""


def _duplicate_key_object(pairs: Iterable[Tuple[str, Any]]) -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ProfileError("duplicate JSON key: %s" % key)
        result[key] = value
    return result


def _object(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ProfileError("%s must be an object" % name)
    return value


def _exact_keys(value: Mapping[str, Any], allowed: Sequence[str], name: str) -> None:
    extras = sorted(set(value) - set(allowed))
    missing = sorted(set(allowed) - set(value))
    if extras:
        raise ProfileError("%s has unknown keys: %s" % (name, ", ".join(extras)))
    if missing:
        raise ProfileError("%s is missing keys: %s" % (name, ", ".join(missing)))


def _string(value: Any, name: str, *, pattern: Optional[re.Pattern[str]] = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProfileError("%s must be a non-empty string" % name)
    value = value.strip()
    if pattern is not None and not pattern.fullmatch(value):
        raise ProfileError("%s has an invalid format" % name)
    return value


def _nullable_string(value: Any, name: str) -> Optional[str]:
    if value is None:
        return None
    return _string(value, name)


def _integer(value: Any, name: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ProfileError("%s must be an integer >= %d" % (name, minimum))
    return value


def _number(value: Any, name: str, *, minimum: float = 0.0, maximum: Optional[float] = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ProfileError("%s must be a number" % name)
    result = float(value)
    if not math.isfinite(result):
        raise ProfileError("%s must be finite" % name)
    if result < minimum or (maximum is not None and result > maximum):
        raise ProfileError("%s is outside the permitted range" % name)
    return result


def safe_relative_path(value: Union[str, Path], name: str = "path") -> Path:
    """Validate a POSIX relative path used by a profile or state file.

    Backslashes, dot components, absolute paths and NUL bytes are rejected so
    a value cannot silently become a Windows or POSIX traversal when reused by
    a board shell script.
    """

    if not isinstance(value, (str, Path)):
        raise ProfileError("%s must be a POSIX relative path" % name)
    # ``Path`` uses the host separator. Normalize it to POSIX form before
    # validating a registry-relative path so the same checked-in registry
    # works on Windows controllers and Linux boards. A raw string is kept
    # verbatim, which still rejects caller-supplied backslashes explicitly.
    raw = value.as_posix() if isinstance(value, Path) else str(value)
    if not raw or not _SAFE_RELATIVE_RE.fullmatch(raw):
        raise ProfileError("%s must be a POSIX relative path" % name)
    path = Path(raw)
    if path.is_absolute() or any(part in {".", ".."} for part in path.parts):
        raise ProfileError("%s must not escape its root" % name)
    return path


def _reject_symlink_components(path: Path) -> None:
    """Reject a lexical path containing an existing symlink component."""

    anchor = Path(path.anchor)
    current = anchor
    try:
        parts = path.relative_to(anchor).parts
    except ValueError as exc:  # pragma: no cover - defensive for mixed drives
        raise ProfileError("path has an invalid anchor: %s" % path) from exc
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ProfileError("symlink path component is not allowed: %s" % current)


def resolve_safe_path(
    value: Union[str, Path],
    root: Union[str, Path] = BASE_DIR,
    *,
    allow_missing: bool = True,
) -> Path:
    """Resolve a path and require it to remain below ``root``.

    This helper is used for active state and artifact paths. Existing symlink
    components are rejected; callers never accidentally follow a link outside
    the controlled directory.
    """

    if not isinstance(value, (str, Path)) or not isinstance(root, (str, Path)):
        raise ProfileError("path and root must be strings or Path objects")
    root_path = Path(root).expanduser().resolve(strict=True)
    candidate_input = Path(value).expanduser()
    if candidate_input.is_absolute():
        lexical_candidate = candidate_input
    else:
        lexical_candidate = root_path / safe_relative_path(candidate_input)
    _reject_symlink_components(lexical_candidate)
    candidate = lexical_candidate.resolve(strict=False)
    try:
        candidate.relative_to(root_path)
    except ValueError as exc:
        raise ProfileError("path escapes root: %s" % value) from exc
    if not allow_missing and not candidate.exists():
        raise ProfileError("path does not exist: %s" % candidate)
    # Do not follow an existing symlink anywhere between root and target.
    return candidate


def _resolve_explicit_path(
    value: Union[str, Path],
    default_root: Union[str, Path] = BASE_DIR,
    *,
    allow_missing: bool = True,
) -> Path:
    """Resolve a caller-supplied file while rejecting symlink components.

    Registry/state locations may intentionally live outside the checkout on a
    board (for example under ``~/case9-mindspore-chat``).  Relative locations
    remain confined to ``default_root``; an explicitly absolute location is
    accepted only as a concrete path and is never followed through a symlink.
    """

    if not isinstance(value, (str, Path)):
        raise ProfileError("path must be a string or Path object")
    raw = Path(value).expanduser()
    if not raw.is_absolute():
        return resolve_safe_path(raw, default_root, allow_missing=allow_missing)
    # Validate the lexical path before ``resolve`` follows an existing link.
    _reject_symlink_components(raw)
    candidate = raw.resolve(strict=False)
    if not allow_missing and not candidate.exists():
        raise ProfileError("path does not exist: %s" % candidate)
    return candidate


@dataclass(frozen=True)
class ArtifactSpec:
    name: str
    kind: str
    filename: str
    url: str
    expected_bytes: Optional[int]
    sha256: Optional[str]

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], index: int) -> "ArtifactSpec":
        name = "artifact[%d]" % index
        _exact_keys(raw, ("name", "kind", "filename", "url", "expected_bytes", "sha256"), name)
        artifact_name = _string(raw["name"], name + ".name", pattern=_PROFILE_ID_RE)
        kind = _string(raw["kind"], name + ".kind", pattern=re.compile(r"^[a-z][a-z0-9_-]{1,31}$"))
        filename = safe_relative_path(raw["filename"], name + ".filename").as_posix()
        url = _string(raw["url"], name + ".url")
        expected = raw["expected_bytes"]
        if expected is not None:
            expected = _integer(expected, name + ".expected_bytes", minimum=1)
        digest = raw["sha256"]
        if digest is not None:
            digest = _string(digest, name + ".sha256", pattern=_SHA256_RE).lower()
        return cls(artifact_name, kind, filename, url, expected, digest)


@dataclass(frozen=True)
class ChatModelProfile:
    id: str
    display_name: str
    model_id: str
    repository: str
    source: str
    revision: str
    tokenizer_revision: str
    revision_pinned: bool
    mirror: Optional[str]
    board_host: str
    board_soc: str
    board_tier: str
    runtime_provider: str
    context_length: int
    default_max_tokens: int
    max_tokens: int
    temperature: float
    top_p: float
    cache_dir: str
    artifacts: Tuple[ArtifactSpec, ...]
    status: str
    admission_eligible: bool
    admission_reason: str
    notes: str

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any], index: int = 0) -> "ChatModelProfile":
        name = "profiles[%d]" % index
        _exact_keys(
            raw,
            (
                "id", "display_name", "model_id", "repository", "source",
                "revision", "tokenizer_revision", "revision_pinned", "mirror",
                "board", "runtime", "cache_dir", "artifacts", "status",
                "admission", "notes",
            ),
            name,
        )
        profile_id = _string(raw["id"], name + ".id", pattern=_PROFILE_ID_RE)
        display_name = _string(raw["display_name"], name + ".display_name")
        model_id = _string(raw["model_id"], name + ".model_id")
        repository = _string(raw["repository"], name + ".repository")
        source = _string(raw["source"], name + ".source", pattern=re.compile(r"^[a-z][a-z0-9_-]{1,31}$"))
        revision = _string(raw["revision"], name + ".revision", pattern=_REVISION_RE)
        tokenizer_revision = _string(raw["tokenizer_revision"], name + ".tokenizer_revision", pattern=_REVISION_RE)
        pinned = raw["revision_pinned"]
        if not isinstance(pinned, bool):
            raise ProfileError(name + ".revision_pinned must be boolean")
        if pinned and (len(revision) != 40 or len(tokenizer_revision) != 40):
            raise ProfileError(name + " pinned revisions must be 40-hex commits")
        mirror = _nullable_string(raw["mirror"], name + ".mirror")

        board = _object(raw["board"], name + ".board")
        _exact_keys(board, ("host", "soc", "tier"), name + ".board")
        board_host = _string(board["host"], name + ".board.host", pattern=re.compile(r"^[0-9a-fA-F:.]+$"))
        board_soc = _string(board["soc"], name + ".board.soc", pattern=re.compile(r"^Ascend[0-9A-Za-z]+$"))
        board_tier = _string(board["tier"], name + ".board.tier", pattern=re.compile(r"^[0-9]+T$"))

        runtime = _object(raw["runtime"], name + ".runtime")
        _exact_keys(runtime, ("provider", "context_length", "default_max_tokens", "max_tokens", "temperature", "top_p"), name + ".runtime")
        provider = _string(runtime["provider"], name + ".runtime.provider", pattern=re.compile(r"^[a-z][a-z0-9_-]{1,31}$"))
        context_length = _integer(runtime["context_length"], name + ".runtime.context_length", minimum=1)
        default_tokens = _integer(runtime["default_max_tokens"], name + ".runtime.default_max_tokens", minimum=1)
        max_tokens = _integer(runtime["max_tokens"], name + ".runtime.max_tokens", minimum=1)
        if default_tokens > max_tokens or max_tokens >= context_length:
            raise ProfileError(name + " token limits are inconsistent with context_length")
        temperature = _number(runtime["temperature"], name + ".runtime.temperature", minimum=0.0, maximum=0.0)
        top_p = _number(runtime["top_p"], name + ".runtime.top_p", minimum=1.0, maximum=1.0)

        cache_dir = safe_relative_path(raw["cache_dir"], name + ".cache_dir").as_posix()
        raw_artifacts = raw["artifacts"]
        if not isinstance(raw_artifacts, list) or not raw_artifacts:
            raise ProfileError(name + ".artifacts must be a non-empty array")
        artifacts = tuple(ArtifactSpec.from_mapping(_object(item, "%s.artifacts[%d]" % (name, i)), i) for i, item in enumerate(raw_artifacts))
        artifact_names = [item.name for item in artifacts]
        if len(set(artifact_names)) != len(artifact_names):
            raise ProfileError(name + ".artifacts names must be unique")

        status = _string(raw["status"], name + ".status")
        if status not in ALLOWED_STATUSES:
            raise ProfileError(name + ".status is not an allowed status")
        admission = _object(raw["admission"], name + ".admission")
        _exact_keys(admission, ("eligible", "reason"), name + ".admission")
        eligible = admission["eligible"]
        if not isinstance(eligible, bool):
            raise ProfileError(name + ".admission.eligible must be boolean")
        reason = _string(admission["reason"], name + ".admission.reason")
        notes = _string(raw["notes"], name + ".notes")
        if eligible and status != "admitted":
            raise ProfileError(name + " eligible profiles must have admitted status")
        if status == "admitted" and not eligible:
            raise ProfileError(name + " admitted profiles must be eligible")
        if not pinned and status != "blocked":
            raise ProfileError(name + " mutable revisions are only allowed for blocked profiles")
        return cls(
            profile_id, display_name, model_id, repository, source, revision,
            tokenizer_revision, pinned, mirror, board_host, board_soc, board_tier,
            provider, context_length, default_tokens, max_tokens, temperature,
            top_p, cache_dir, artifacts, status, eligible, reason, notes,
        )

    # Compatibility aliases used by the service and board scripts.
    @property
    def profile_id(self) -> str:
        return self.id

    @property
    def source_model(self) -> str:
        return self.model_id

    @property
    def model_name(self) -> str:
        return self.model_id

    @property
    def context_limit(self) -> int:
        return self.context_length

    @property
    def max_new_tokens(self) -> int:
        return self.max_tokens

    @property
    def default_tokens(self) -> int:
        return self.default_max_tokens

    @property
    def board(self) -> Mapping[str, str]:
        return {"host": self.board_host, "soc": self.board_soc, "tier": self.board_tier}

    def artifact(self, name: str) -> ArtifactSpec:
        for item in self.artifacts:
            if item.name == name:
                return item
        raise ProfileError("profile %s has no artifact %s" % (self.id, name))

    def to_public_dict(self) -> Dict[str, Any]:
        """Return only metadata appropriate for an unauthenticated browser."""

        return {
            "id": self.id,
            "display_name": self.display_name,
            "model_id": self.model_id,
            "revision": self.revision,
            "revision_pinned": self.revision_pinned,
            "board_soc": self.board_soc,
            "board_tier": self.board_tier,
            "context_length": self.context_length,
            "default_max_tokens": self.default_max_tokens,
            "max_tokens": self.max_tokens,
            "status": self.status,
            "admitted": self.admission_eligible,
            "admission_reason": self.admission_reason,
        }


@dataclass(frozen=True)
class ProfileRegistry:
    profiles: Tuple[ChatModelProfile, ...]

    def get(self, profile_id: str) -> ChatModelProfile:
        if not isinstance(profile_id, str) or not _PROFILE_ID_RE.fullmatch(profile_id):
            raise ProfileError("invalid profile id")
        for profile in self.profiles:
            if profile.id == profile_id:
                return profile
        raise ProfileError("unknown profile: %s" % profile_id)

    get_profile = get

    def public_profiles(self) -> List[Dict[str, Any]]:
        return [profile.to_public_dict() for profile in self.profiles]

    def __iter__(self):
        return iter(self.profiles)

    def __len__(self) -> int:
        return len(self.profiles)


def load_profiles(path: Union[str, Path] = DEFAULT_REGISTRY_PATH) -> ProfileRegistry:
    """Load and strictly validate a profile registry from JSON."""

    registry_path = _resolve_explicit_path(path, BASE_DIR, allow_missing=False)
    if registry_path.is_symlink() or not registry_path.is_file():
        raise ProfileError("registry must be a regular file")
    try:
        document = json.loads(
            registry_path.read_text(encoding="utf-8"), object_pairs_hook=_duplicate_key_object
        )
    except ProfileError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProfileError("invalid profile registry: %s" % exc) from exc
    document = _object(document, "registry")
    _exact_keys(document, ("schema_version", "profiles"), "registry")
    if document["schema_version"] != 1:
        raise ProfileError("unsupported registry schema_version")
    raw_profiles = document["profiles"]
    if not isinstance(raw_profiles, list) or not raw_profiles:
        raise ProfileError("registry.profiles must be a non-empty array")
    parsed = tuple(ChatModelProfile.from_mapping(_object(item, "profiles[%d]" % i), i) for i, item in enumerate(raw_profiles))
    ids = [item.id for item in parsed]
    if len(ids) != len(set(ids)):
        raise ProfileError("profile ids must be unique")
    return ProfileRegistry(parsed)


def load_profile_registry(path: Union[str, Path] = DEFAULT_REGISTRY_PATH) -> ProfileRegistry:
    """Alias retained for callers that use the longer name."""

    return load_profiles(path)


def get_profile(profile_id: str, path: Union[str, Path] = DEFAULT_REGISTRY_PATH) -> ChatModelProfile:
    return load_profiles(path).get(profile_id)


def public_profiles(path: Union[str, Path] = DEFAULT_REGISTRY_PATH) -> List[Dict[str, Any]]:
    return load_profiles(path).public_profiles()


def profile_to_public_dict(profile: ChatModelProfile) -> Dict[str, Any]:
    """Serialize one validated profile for an unauthenticated status view."""

    if not isinstance(profile, ChatModelProfile):
        raise ProfileError("profile must be a ChatModelProfile")
    return profile.to_public_dict()


@dataclass(frozen=True)
class ActiveModelState:
    profile_id: str
    status: str
    worker_pid: Optional[int]
    cache_cleared: bool
    updated_at: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": 1,
            "profile_id": self.profile_id,
            "status": self.status,
            "worker_pid": self.worker_pid,
            "cache_cleared": self.cache_cleared,
            "updated_at": self.updated_at,
        }


def _state_path(path: Union[str, Path]) -> Path:
    return _resolve_explicit_path(path, BASE_DIR, allow_missing=True)


def _parse_state(document: Mapping[str, Any]) -> ActiveModelState:
    _exact_keys(document, ("schema_version", "profile_id", "status", "worker_pid", "cache_cleared", "updated_at"), "active state")
    if document["schema_version"] != 1:
        raise ProfileError("unsupported active state schema_version")
    profile_id = _string(document["profile_id"], "active state.profile_id", pattern=_PROFILE_ID_RE)
    status = _string(document["status"], "active state.status")
    worker_pid = document["worker_pid"]
    if worker_pid is not None:
        worker_pid = _integer(worker_pid, "active state.worker_pid", minimum=1)
    cache_cleared = document["cache_cleared"]
    if not isinstance(cache_cleared, bool):
        raise ProfileError("active state.cache_cleared must be boolean")
    updated_at = _string(document["updated_at"], "active state.updated_at")
    return ActiveModelState(profile_id, status, worker_pid, cache_cleared, updated_at)


def read_active_state(
    path: Union[str, Path] = DEFAULT_STATE_PATH,
    *,
    registry: Optional[ProfileRegistry] = None,
) -> Optional[ActiveModelState]:
    state_path = _state_path(path)
    if not state_path.exists():
        return None
    if state_path.is_symlink() or not state_path.is_file():
        raise ProfileError("active state must be a regular file")
    try:
        document = json.loads(
            state_path.read_text(encoding="utf-8"), object_pairs_hook=_duplicate_key_object
        )
    except ProfileError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProfileError("invalid active state: %s" % exc) from exc
    state = _parse_state(_object(document, "active state"))
    if registry is not None:
        registry.get(state.profile_id)
    return state


get_active_state = read_active_state


def write_active_state(
    path: Union[str, Path] = DEFAULT_STATE_PATH,
    profile_id: Optional[str] = None,
    *,
    status: str = "starting",
    worker_pid: Optional[int] = None,
    cache_cleared: bool = False,
    registry: Optional[ProfileRegistry] = None,
) -> ActiveModelState:
    """Atomically write a validated active-worker state file."""

    if profile_id is None:
        raise ProfileError("profile_id is required")
    profile_id = _string(profile_id, "profile_id", pattern=_PROFILE_ID_RE)
    if registry is not None:
        registry.get(profile_id)
    if not isinstance(status, str) or not status.strip():
        raise ProfileError("status must be a non-empty string")
    if worker_pid is not None:
        worker_pid = _integer(worker_pid, "worker_pid", minimum=1)
    if not isinstance(cache_cleared, bool):
        raise ProfileError("cache_cleared must be boolean")
    state = ActiveModelState(
        profile_id=profile_id,
        status=status.strip(),
        worker_pid=worker_pid,
        cache_cleared=cache_cleared,
        updated_at=datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    )
    state_path = _state_path(path)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    if state_path.exists() and state_path.is_symlink():
        raise ProfileError("active state target must not be a symlink")
    payload = json.dumps(state.to_dict(), ensure_ascii=True, sort_keys=True, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".%s." % state_path.name, suffix=".part", dir=str(state_path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, state_path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except OSError:
            pass
        raise
    return state


def clear_active_state(path: Union[str, Path] = DEFAULT_STATE_PATH) -> bool:
    """Remove only the exact active-state file; return whether it existed."""

    state_path = _state_path(path)
    if state_path.is_symlink():
        raise ProfileError("active state target must not be a symlink")
    if not state_path.exists():
        return False
    if not state_path.is_file():
        raise ProfileError("active state must be a regular file")
    state_path.unlink()
    return True


def resolve_artifact_path(
    profile: ChatModelProfile,
    artifact_name: str,
    root: Union[str, Path] = BASE_DIR,
) -> Path:
    """Resolve one profile artifact below a caller-selected model root."""

    artifact = profile.artifact(artifact_name)
    return resolve_safe_path(Path(profile.cache_dir) / artifact.filename, root)


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Small read-only CLI used by operators and smoke tests.

    Mutating lifecycle operations intentionally live in ``case9-modelctl.sh``;
    this module only exposes validated registry and active-worker metadata.
    """

    parser = argparse.ArgumentParser(description="Inspect Case9 chat model profiles")
    parser.add_argument("command", choices=("list", "status"))
    parser.add_argument("--registry", type=Path, default=DEFAULT_REGISTRY_PATH)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    args = parser.parse_args(list(argv) if argv is not None else None)
    try:
        registry = load_profiles(args.registry)
        if args.command == "list":
            print(json.dumps({"profiles": registry.public_profiles()}, ensure_ascii=False, sort_keys=True))
            return 0
        state = read_active_state(args.state, registry=registry)
        payload: Dict[str, Any] = {"active": state.to_dict() if state else None}
        if state is not None:
            payload["profile"] = registry.get(state.profile_id).to_public_dict()
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0
    except (OSError, ProfileError) as exc:
        parser.error(str(exc))
        return 2


__all__ = [
    "ALLOWED_STATUSES",
    "ActiveModelState",
    "ArtifactSpec",
    "BASE_DIR",
    "ChatModelProfile",
    "DEFAULT_REGISTRY_PATH",
    "DEFAULT_STATE_PATH",
    "ProfileError",
    "ProfileRegistry",
    "clear_active_state",
    "get_profile",
    "load_profile_registry",
    "load_profiles",
    "public_profiles",
    "profile_to_public_dict",
    "read_active_state",
    "get_active_state",
    "resolve_artifact_path",
    "resolve_safe_path",
    "safe_relative_path",
    "write_active_state",
]


if __name__ == "__main__":  # pragma: no cover - exercised by board operators
    raise SystemExit(main())
