"""Fail-closed B4-to-B1 compatibility service for historical Qwen2.5 OM files.

This module is deliberately separate from :mod:`qwen25_acl_service`.  The
historical 2048-token OMs were built for Ascend310B4, while this experiment
runs them only on an Ascend310B1/20T board.  A caller must make that exception
explicit, bind every input artifact to a byte/SHA-256 manifest, and run the
worker below a parent process watchdog.  It is not an admission path for the
normal Case9 service.

The child process owns ACL and the loopback HTTP listener.  The parent never
imports ``acl`` and terminates the child if an ACL execute, request, or model
startup operation stops making progress.  Termination closes the client
connection and removes the listener, which is intentionally fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import logging
import math
import multiprocessing
from pathlib import Path
import signal
import time
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional, Sequence, Tuple
import uuid

from acl_om_service import (
    REQUEST_BODY_TIMEOUT_SECONDS,
    RequestError as _BodyRequestError,
    _read_request_body,
)
from qwen25_acl_contract import (
    QWEN25_SUPPORTED_EXECUTION_MODE,
    QWEN25_SUPPORTED_LAST_LOGITS_EXECUTION_MODE,
    ContractError,
    Qwen25Contract,
)
from qwen25_acl_runtime import (
    GenerationResult,
    Qwen25AclRuntime,
    RuntimeBusy,
    RuntimeRequestError,
    RuntimeUnavailable,
)
from qwen25_acl_service import MODEL_ID


LOGGER = logging.getLogger("case9.qwen25_legacy_compat")
SOURCE_SOC = "Ascend310B4"
TARGET_SOC = "Ascend310B1"
MAX_REQUEST_BYTES = 256 * 1024
MAX_MESSAGES = 32
MAX_MESSAGE_CHARACTERS = 16 * 1024
MAX_GENERATION_TOKENS = 8
WATCHDOG_EXIT_CODE = 124
WORKER_FAILED_EXIT_CODE = 70
MANIFEST_SCHEMA_VERSION = 1


class CompatibilityError(RuntimeError):
    """Raised before a compatibility experiment is allowed to start."""


class CompatibilityRequestError(ValueError):
    """A request rejected before it reaches the NPU worker."""

    def __init__(self, message: str, status_code: int = 400, code: str = "invalid_request_error") -> None:
        super().__init__(message)
        self.message = message
        self.status_code = int(status_code)
        self.code = code


@dataclass(frozen=True)
class ArtifactDigest:
    """Expected immutable identity for one local file.

    ``bytes`` may be ``None`` only for the full-context contract: the original
    evidence records its SHA-256 but not its file size.  The campaign manifest
    still supplies, and the runtime always verifies, a positive byte count.
    """

    bytes: Optional[int]
    sha256: str

    def __post_init__(self) -> None:
        if self.bytes is not None and (isinstance(self.bytes, bool) or int(self.bytes) <= 0):
            raise ValueError("artifact byte count must be positive when known")
        digest = self.sha256.lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("artifact SHA-256 must be 64 lowercase hexadecimal characters")


@dataclass(frozen=True)
class CompatibilityProfile:
    """One and only one historical B4 OM identity accepted by this service."""

    key: str
    source_soc: str
    target_soc: str
    execution_mode: str
    artifacts: Mapping[str, ArtifactDigest]

    def __post_init__(self) -> None:
        required = {"om", "contract", "tokenizer", "tokenizer_config"}
        if set(self.artifacts) != required:
            raise ValueError("compatibility profile must pin om, contract, tokenizer, tokenizer_config")
        if self.source_soc != SOURCE_SOC or self.target_soc != TARGET_SOC:
            raise ValueError("compatibility profiles are restricted to Ascend310B4 to Ascend310B1")
        if self.execution_mode not in {
            QWEN25_SUPPORTED_EXECUTION_MODE,
            QWEN25_SUPPORTED_LAST_LOGITS_EXECUTION_MODE,
        }:
            raise ValueError("compatibility profile has an unsupported execution mode")


COMPATIBILITY_PROFILES: Mapping[str, CompatibilityProfile] = {
    "qwen25-full2048-b4-compat": CompatibilityProfile(
        key="qwen25-full2048-b4-compat",
        source_soc=SOURCE_SOC,
        target_soc=TARGET_SOC,
        execution_mode=QWEN25_SUPPORTED_EXECUTION_MODE,
        artifacts={
            "om": ArtifactDigest(1407111161, "dc17b153ee1e76b3d31e971617d1cfdc7c56f366226212a61377a2c85e1d92b8"),
            # The original validation record contains the contract SHA but
            # not its byte count.  A signed campaign manifest supplies the
            # byte count and both are checked before ACL is initialized.
            "contract": ArtifactDigest(None, "7223fb5997a88aa5a7521192c2c7073cd92f23dce61f0587a87f02be7018ae82"),
            "tokenizer": ArtifactDigest(7031645, "c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539"),
            "tokenizer_config": ArtifactDigest(7305, "5b5d4f65d0acd3b2d56a35b56d374a36cbc1c8fa5cf3b3febbbfabf22f359583"),
        },
    ),
    "qwen25-lastlogits2048-b4-compat": CompatibilityProfile(
        key="qwen25-lastlogits2048-b4-compat",
        source_soc=SOURCE_SOC,
        target_soc=TARGET_SOC,
        execution_mode=QWEN25_SUPPORTED_LAST_LOGITS_EXECUTION_MODE,
        artifacts={
            "om": ArtifactDigest(1407130895, "ae196fd8ba4ccb7c721f8fcb9d0ff4d9b5b72bfcdf9a77309c6fe51d5103c460"),
            "contract": ArtifactDigest(2802, "b21609e3e9ddc05f6d6a930de0b8d268ed30b83c1cff6d9b5bb283d27d441726"),
            "tokenizer": ArtifactDigest(7031645, "c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539"),
            "tokenizer_config": ArtifactDigest(7305, "5b5d4f65d0acd3b2d56a35b56d374a36cbc1c8fa5cf3b3febbbfabf22f359583"),
        },
    ),
}


@dataclass(frozen=True)
class ArtifactPaths:
    om: Path
    contract: Path
    tokenizer: Path
    tokenizer_config: Path

    def items(self) -> Mapping[str, Path]:
        return {
            "om": self.om,
            "contract": self.contract,
            "tokenizer": self.tokenizer,
            "tokenizer_config": self.tokenizer_config,
        }


@dataclass(frozen=True)
class ArtifactBinding:
    profile: CompatibilityProfile
    artifacts: Mapping[str, ArtifactDigest]

    def public_status(self) -> Dict[str, Any]:
        return {
            "profile": self.profile.key,
            "compatibility_experiment": True,
            "source_soc": self.profile.source_soc,
            "board_soc": self.profile.target_soc,
            "artifact_pins_verified": True,
            "om_sha256": self.artifacts["om"].sha256,
            "contract_sha256": self.artifacts["contract"].sha256,
            "tokenizer_sha256": self.artifacts["tokenizer"].sha256,
            "tokenizer_config_sha256": self.artifacts["tokenizer_config"].sha256,
        }


@dataclass(frozen=True)
class WorkerConfig:
    profile: str
    host: str
    port: int
    artifact_paths: Mapping[str, str]
    pin_manifest: str
    device_id: int
    source_soc: str
    board_soc: str
    observed_board_soc: str
    compatibility_experiment: bool
    allow_dirty_base: bool


def profile_for(key: str) -> CompatibilityProfile:
    try:
        return COMPATIBILITY_PROFILES[key]
    except KeyError as exc:
        raise CompatibilityError(f"unknown compatibility profile: {key}") from exc


def validate_experiment_request(
    profile: CompatibilityProfile,
    *,
    compatibility_experiment: bool,
    source_soc: str,
    board_soc: str,
    observed_board_soc: str,
    allow_dirty_base: bool,
) -> None:
    """Reject any implicit cross-SoC or dirty-base execution request."""

    if not compatibility_experiment:
        raise CompatibilityError("--compatibility-experiment is required for a B4 OM on B1")
    if not allow_dirty_base:
        raise CompatibilityError("--allow-dirty-base is required for the shared 20T base environment")
    if source_soc != profile.source_soc:
        raise CompatibilityError(f"source SoC must be {profile.source_soc}, got {source_soc!r}")
    if board_soc != profile.target_soc:
        raise CompatibilityError(f"requested board SoC must be {profile.target_soc}, got {board_soc!r}")
    if observed_board_soc != profile.target_soc:
        raise CompatibilityError(
            f"observed board SoC must be {profile.target_soc}, got {observed_board_soc!r}"
        )


def _sha256_file(path: Path) -> Tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _as_digest(value: Any, field: str) -> ArtifactDigest:
    if not isinstance(value, Mapping):
        raise CompatibilityError(f"pin manifest {field} must be an object")
    if set(value) != {"bytes", "sha256"}:
        raise CompatibilityError(f"pin manifest {field} must contain only bytes and sha256")
    size, digest = value.get("bytes"), value.get("sha256")
    if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
        raise CompatibilityError(f"pin manifest {field}.bytes must be a positive integer")
    if not isinstance(digest, str):
        raise CompatibilityError(f"pin manifest {field}.sha256 must be a string")
    try:
        return ArtifactDigest(int(size), digest.lower())
    except ValueError as exc:
        raise CompatibilityError(f"pin manifest {field} is invalid: {exc}") from exc


def _load_manifest(path: Path, profile: CompatibilityProfile) -> Mapping[str, ArtifactDigest]:
    if not path.is_file():
        raise CompatibilityError(f"pin manifest does not exist: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CompatibilityError(f"cannot read pin manifest: {type(exc).__name__}") from exc
    required = {"schema_version", "profile", "source_soc", "board_soc", "artifacts"}
    if not isinstance(value, Mapping) or set(value) != required:
        raise CompatibilityError("pin manifest must contain exactly schema_version, profile, source_soc, board_soc, artifacts")
    if value.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise CompatibilityError(f"pin manifest schema_version must be {MANIFEST_SCHEMA_VERSION}")
    if value.get("profile") != profile.key:
        raise CompatibilityError("pin manifest profile does not match the requested profile")
    if value.get("source_soc") != profile.source_soc or value.get("board_soc") != profile.target_soc:
        raise CompatibilityError("pin manifest SoC identities do not match the requested compatibility profile")
    raw_artifacts = value.get("artifacts")
    if not isinstance(raw_artifacts, Mapping) or set(raw_artifacts) != set(profile.artifacts):
        raise CompatibilityError("pin manifest must bind exactly om, contract, tokenizer, tokenizer_config")
    artifacts = {name: _as_digest(raw_artifacts[name], f"artifacts.{name}") for name in profile.artifacts}
    for name, known in profile.artifacts.items():
        actual = artifacts[name]
        if actual.sha256 != known.sha256:
            raise CompatibilityError(f"pin manifest {name} SHA-256 does not match the fixed historical identity")
        if known.bytes is not None and actual.bytes != known.bytes:
            raise CompatibilityError(f"pin manifest {name} byte count does not match the fixed historical identity")
    return artifacts


def verify_pinned_artifacts(
    profile: CompatibilityProfile,
    paths: ArtifactPaths,
    pin_manifest: Path,
) -> ArtifactBinding:
    """Verify every supplied artifact before importing ACL or loading an OM."""

    manifest = _load_manifest(pin_manifest.expanduser(), profile)
    for name, path in paths.items().items():
        resolved = path.expanduser()
        if not resolved.is_file():
            raise CompatibilityError(f"required {name} artifact does not exist: {resolved}")
        expected = manifest[name]
        actual_size, actual_sha = _sha256_file(resolved)
        if actual_size != expected.bytes or actual_sha != expected.sha256:
            raise CompatibilityError(f"{name} artifact byte/SHA-256 verification failed")
    try:
        contract = Qwen25Contract.load(paths.contract)
        contract.validate_static_expectations(require_descriptors=True)
    except (ContractError, OSError) as exc:
        raise CompatibilityError(f"contract validation failed: {exc}") from exc
    if contract.static_sequence_length != 2048:
        raise CompatibilityError("historical compatibility contracts must have fixed sequence length 2048")
    if contract.execution_mode != profile.execution_mode:
        raise CompatibilityError("contract execution mode does not match the selected historical OM profile")
    return ArtifactBinding(profile=profile, artifacts=manifest)


class _Notifier:
    """Best-effort phase events sent from the ACL-owning child to its parent."""

    def __init__(self, connection: Any = None) -> None:
        self._connection = connection
        self._next_id = 0

    def begin(self, phase: str) -> int:
        self._next_id += 1
        token = self._next_id
        self._send({"event": "begin", "id": token, "phase": phase, "time": time.monotonic()})
        return token

    def end(self, token: int, phase: str) -> None:
        self._send({"event": "end", "id": token, "phase": phase, "time": time.monotonic()})

    def send(self, event: str, **details: Any) -> None:
        payload = {"event": event, "time": time.monotonic()}
        payload.update(details)
        self._send(payload)

    def _send(self, payload: Mapping[str, Any]) -> None:
        if self._connection is None:
            return
        try:
            self._connection.send(dict(payload))
        except (BrokenPipeError, EOFError, OSError):
            # The parent is only a watchdog.  It may already have killed the
            # worker, in which case a secondary reporting failure is harmless.
            pass


class _NotifyingBackend:
    """Wrap one low-level ACL backend so every execute has a watchdog phase."""

    def __init__(self, backend: Any, notifier: _Notifier) -> None:
        self._backend = backend
        self._notifier = notifier

    @property
    def poisoned(self) -> bool:
        return bool(getattr(self._backend, "poisoned", False))

    def open(self, model_path: Path) -> Any:
        return self._backend.open(model_path)

    def run(self, inputs: Mapping[str, Any]) -> Any:
        token = self._notifier.begin("acl_execute")
        try:
            return self._backend.run(inputs)
        finally:
            self._notifier.end(token, "acl_execute")

    def close(self) -> None:
        self._backend.close()


@dataclass(frozen=True)
class CompletionRequest:
    model: str
    messages: Tuple[Dict[str, str], ...]
    stream: bool
    max_tokens: int


def _parse_request(raw: Mapping[str, Any]) -> CompletionRequest:
    allowed = {"model", "messages", "stream", "max_tokens", "temperature", "top_p"}
    if set(raw) - allowed:
        raise CompatibilityRequestError("unsupported chat completion field")
    if raw.get("model") != MODEL_ID:
        raise CompatibilityRequestError("model is not available", 404, "model_not_found")
    messages = raw.get("messages")
    if not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES:
        raise CompatibilityRequestError("messages must be a non-empty array")
    normalized = []
    for index, item in enumerate(messages):
        if not isinstance(item, Mapping) or set(item) - {"role", "content"}:
            raise CompatibilityRequestError(f"messages[{index}] is invalid")
        role, content = item.get("role"), item.get("content")
        if role not in {"system", "user", "assistant"} or not isinstance(content, str) or not content:
            raise CompatibilityRequestError(f"messages[{index}] role/content is invalid")
        normalized.append({"role": str(role), "content": content})
    if sum(len(item["content"]) for item in normalized) > MAX_MESSAGE_CHARACTERS:
        raise CompatibilityRequestError("messages exceed the fixed-context character limit", 413, "request_too_large")
    if not any(item["role"] == "user" for item in normalized):
        raise CompatibilityRequestError("at least one user message is required")
    stream = raw.get("stream", False)
    if not isinstance(stream, bool):
        raise CompatibilityRequestError("stream must be boolean")
    max_tokens = raw.get("max_tokens", 1)
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= MAX_GENERATION_TOKENS:
        raise CompatibilityRequestError(f"max_tokens must be between 1 and {MAX_GENERATION_TOKENS}")
    for name, expected in (("temperature", 0.0), ("top_p", 1.0)):
        if name in raw and raw[name] is not None:
            value = raw[name]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or float(value) != expected:
                raise CompatibilityRequestError(f"only greedy decoding is supported; {name} must be {expected}")
    return CompletionRequest(MODEL_ID, tuple(normalized), stream, max_tokens)


def _text_delta(previous: str, current: str) -> str:
    """Only emit a true prefix extension; never duplicate revised text."""

    if current.startswith(previous):
        return current[len(previous) :]
    return ""


def _completion_payload(result: GenerationResult) -> Dict[str, Any]:
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": result.text}, "finish_reason": result.finish_reason}],
        "usage": {
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.prompt_tokens + result.completion_tokens,
        },
    }


def _chunk_payload(request_id: str, delta: Mapping[str, str], finish_reason: Optional[str], usage: Optional[Mapping[str, int]] = None) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": MODEL_ID,
        "choices": [{"index": 0, "delta": dict(delta), "finish_reason": finish_reason}],
    }
    if usage is not None:
        payload["usage"] = dict(usage)
    return payload


class CompatibilityAclService:
    """Single-request protocol adapter for a verified historical OM."""

    def __init__(self, runtime: Qwen25AclRuntime, binding: ArtifactBinding, notifier: _Notifier) -> None:
        self.runtime = runtime
        self.binding = binding
        self.notifier = notifier

    def health(self) -> Dict[str, Any]:
        value = dict(self.runtime.status())
        value.update(self.binding.public_status())
        value.update({"admission": "compatibility_experiment_only", "watchdog_supervised": True})
        return value

    def models(self) -> Dict[str, Any]:
        if not self.runtime.started:
            raise RuntimeUnavailable("historical compatibility runtime is not ready")
        return {"object": "list", "data": [{"id": MODEL_ID, "object": "model", "owned_by": "case9-compat"}]}

    def complete(self, request: CompletionRequest) -> Dict[str, Any]:
        token = self.notifier.begin("request")
        try:
            return _completion_payload(self.runtime.complete(request.messages, request.max_tokens))
        finally:
            self.notifier.end(token, "request")

    def stream(self, request: CompletionRequest) -> Iterable[Dict[str, Any]]:
        token = self.notifier.begin("request")
        request_id = "chatcmpl-" + uuid.uuid4().hex
        previous = ""
        first = True
        last: Optional[GenerationResult] = None
        try:
            for result in self.runtime.stream(request.messages, request.max_tokens):
                last = result
                delta_text = _text_delta(previous, result.text)
                if result.text.startswith(previous):
                    previous = result.text
                delta: Dict[str, str] = {"role": "assistant"} if first else {}
                if delta_text:
                    delta["content"] = delta_text
                if first or delta_text:
                    yield _chunk_payload(request_id, delta, None)
                first = False
            if first:
                yield _chunk_payload(request_id, {"role": "assistant"}, None)
            usage = None
            if last is not None:
                usage = {
                    "prompt_tokens": last.prompt_tokens,
                    "completion_tokens": last.completion_tokens,
                    "total_tokens": last.prompt_tokens + last.completion_tokens,
                }
            yield _chunk_payload(request_id, {}, last.finish_reason if last else "length", usage)
        finally:
            self.notifier.end(token, "request")


def make_server(host: str, port: int, service: CompatibilityAclService) -> HTTPServer:
    """Build a deliberately single-threaded loopback HTTP service."""

    if host != "127.0.0.1":
        raise CompatibilityError("historical OM compatibility service must remain loopback-only")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "Case9Qwen25LegacyCompat/1"

        def log_message(self, fmt: str, *args: Any) -> None:
            LOGGER.info("peer=%s " + fmt, self.client_address[0], *args)

        def _json(self, status: int, value: Mapping[str, Any]) -> None:
            data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.close_connection = True
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(data)

        def _error(self, message: str, status: int, code: str) -> None:
            self._json(status, {"error": {"message": message, "type": "server_error" if status >= 500 else "invalid_request_error", "code": code}})

        def do_GET(self) -> None:
            if self.path in {"/health", "/healthz"}:
                value = service.health()
                self._json(HTTPStatus.OK if value.get("ready") else HTTPStatus.SERVICE_UNAVAILABLE, value)
                return
            if self.path == "/v1/models":
                try:
                    self._json(HTTPStatus.OK, service.models())
                except RuntimeUnavailable as exc:
                    self._error(str(exc), HTTPStatus.SERVICE_UNAVAILABLE, "model_unavailable")
                return
            self._error("Not found", HTTPStatus.NOT_FOUND, "not_found")

        def do_POST(self) -> None:
            if self.path != "/v1/chat/completions":
                self._error("Not found", HTTPStatus.NOT_FOUND, "not_found")
                return
            try:
                request = _parse_request(self._read_body())
                if request.stream:
                    self._sse(request)
                else:
                    self._json(HTTPStatus.OK, service.complete(request))
            except CompatibilityRequestError as exc:
                self._error(exc.message, exc.status_code, exc.code)
            except RuntimeBusy as exc:
                self._error(str(exc), HTTPStatus.CONFLICT, "busy")
            except (RuntimeUnavailable, RuntimeRequestError) as exc:
                self._error(str(exc), HTTPStatus.SERVICE_UNAVAILABLE, "model_unavailable")
            except (BrokenPipeError, ConnectionResetError):
                service.runtime.cancel()
            except Exception:
                LOGGER.exception("historical compatibility request failed")
                service.runtime.cancel()
                self._error("historical compatibility worker failed", HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error")

        def _read_body(self) -> Mapping[str, Any]:
            if self.headers.get("Transfer-Encoding", "").lower() not in {"", "identity"}:
                raise CompatibilityRequestError("chunked request bodies are not supported", 411)
            try:
                length = int(self.headers.get("Content-Length", "-1"))
            except ValueError as exc:
                raise CompatibilityRequestError("invalid Content-Length") from exc
            if length < 0:
                raise CompatibilityRequestError("Content-Length is required", 411)
            if length > MAX_REQUEST_BYTES:
                raise CompatibilityRequestError("request body exceeds the configured limit", 413, "request_too_large")
            if "application/json" not in self.headers.get("Content-Type", "").lower():
                raise CompatibilityRequestError("Content-Type must be application/json", 415)
            try:
                raw = _read_request_body(
                    self.rfile,
                    self.connection,
                    length,
                    timeout_seconds=REQUEST_BODY_TIMEOUT_SECONDS,
                )
            except _BodyRequestError as exc:
                raise CompatibilityRequestError(exc.message, exc.status_code, exc.code) from exc
            if len(raw) != length:
                raise CompatibilityRequestError("incomplete request body")
            try:
                value = json.loads(raw.decode("utf-8"), parse_constant=lambda constant: (_ for _ in ()).throw(ValueError(constant)))
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise CompatibilityRequestError("request body is not valid JSON") from exc
            if not isinstance(value, Mapping):
                raise CompatibilityRequestError("request body must be an object")
            return value

        def _sse(self, request: CompletionRequest) -> None:
            self.close_connection = True
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for payload in service.stream(request):
                    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                    self.wfile.write(("data: " + data + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                service.runtime.cancel()
            except (RuntimeBusy, RuntimeUnavailable, RuntimeRequestError) as exc:
                service.runtime.cancel()
                self._sse_error(str(exc), "model_unavailable")
            except Exception:
                LOGGER.exception("historical compatibility SSE failed")
                service.runtime.cancel()
                self._sse_error("historical compatibility worker failed", "internal_error")

        def _sse_error(self, message: str, code: str) -> None:
            try:
                data = json.dumps({"error": {"message": message, "type": "server_error", "code": code}}, ensure_ascii=False, separators=(",", ":"))
                self.wfile.write(("data: " + data + "\n\ndata: [DONE]\n\n").encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

    class CompatHttpServer(HTTPServer):
        allow_reuse_address = True

    server = CompatHttpServer((host, int(port)), Handler)
    server.timeout = 0.5
    return server


class WatchdogState:
    """Track nested worker phases without depending on a particular process API."""

    def __init__(self, *, startup_timeout: float, execute_timeout: float, request_timeout: float) -> None:
        self._timeouts = {
            "startup": float(startup_timeout),
            "acl_execute": float(execute_timeout),
            "request": float(request_timeout),
        }
        if any(value <= 0 for value in self._timeouts.values()):
            raise ValueError("all watchdog timeouts must be positive")
        self.active: MutableMapping[int, Tuple[str, float]] = {}
        self.ready = False
        self.fatal: Optional[str] = None

    def consume(self, event: Mapping[str, Any], now: float) -> None:
        kind = event.get("event")
        if kind == "ready":
            self.ready = True
            # Token -1 is owned by the parent and bounds Python process
            # creation plus the child's initial import path, before the child
            # can emit its first event.
            self.active.pop(-1, None)
            return
        if kind == "fatal":
            self.fatal = str(event.get("message", "worker reported a fatal error"))
            return
        token, phase = event.get("id"), event.get("phase")
        if not isinstance(token, int) or phase not in self._timeouts:
            return
        if kind == "begin":
            self.active[token] = (str(phase), now + self._timeouts[str(phase)])
        elif kind == "end":
            self.active.pop(token, None)

    def expired(self, now: float) -> Optional[str]:
        for phase, deadline in self.active.values():
            if now >= deadline:
                return phase
        return None


def _terminate_process(process: Any, grace_seconds: float = 10.0) -> None:
    if not process.is_alive():
        process.join(timeout=0)
        return
    process.terminate()
    process.join(timeout=grace_seconds)
    if process.is_alive():
        kill = getattr(process, "kill", None)
        if callable(kill):
            kill()
        else:
            process.terminate()
        process.join(timeout=grace_seconds)


def supervise_worker(
    config: WorkerConfig,
    *,
    startup_timeout_seconds: float,
    execution_timeout_seconds: float,
    request_timeout_seconds: float,
    mp_context: Optional[Any] = None,
) -> int:
    """Run one ACL child and terminate it on a phase watchdog timeout."""

    context = mp_context or multiprocessing.get_context("spawn")
    receive_connection, send_connection = context.Pipe(duplex=False)
    process = context.Process(target=run_worker, args=(config, send_connection), name="case9-qwen25-legacy-compat")
    state = WatchdogState(
        startup_timeout=startup_timeout_seconds,
        execute_timeout=execution_timeout_seconds,
        request_timeout=request_timeout_seconds,
    )
    state.consume({"event": "begin", "id": -1, "phase": "startup"}, time.monotonic())
    process.start()
    send_connection.close()
    try:
        while process.is_alive():
            now = time.monotonic()
            if receive_connection.poll(0.2):
                try:
                    event = receive_connection.recv()
                except EOFError:
                    event = None
                if isinstance(event, Mapping):
                    state.consume(event, now)
                    if state.fatal:
                        LOGGER.error("compatibility worker reported fatal error: %s", state.fatal)
                        _terminate_process(process)
                        return WORKER_FAILED_EXIT_CODE
            expired = state.expired(time.monotonic())
            if expired:
                LOGGER.error("compatibility watchdog expired in phase=%s; terminating worker", expired)
                _terminate_process(process)
                return WATCHDOG_EXIT_CODE
        process.join(timeout=0)
        if not state.ready:
            LOGGER.error("compatibility worker exited before reporting ready")
            return WORKER_FAILED_EXIT_CODE
        return int(process.exitcode or 0)
    except KeyboardInterrupt:
        LOGGER.warning("compatibility supervisor interrupted; stopping worker")
        _terminate_process(process)
        return 130
    finally:
        try:
            receive_connection.close()
        except OSError:
            pass


def _paths_from_config(config: WorkerConfig) -> ArtifactPaths:
    required = {"om", "contract", "tokenizer", "tokenizer_config"}
    if set(config.artifact_paths) != required:
        raise CompatibilityError("worker configuration has an invalid artifact path set")
    return ArtifactPaths(**{name: Path(config.artifact_paths[name]).expanduser() for name in required})


def run_worker(config: WorkerConfig, connection: Any = None) -> None:
    """ACL-owning child process entry point used only by :func:`supervise_worker`."""

    notifier = _Notifier(connection)
    server: Optional[HTTPServer] = None
    runtime: Optional[Qwen25AclRuntime] = None
    token = notifier.begin("startup")
    try:
        profile = profile_for(config.profile)
        validate_experiment_request(
            profile,
            compatibility_experiment=bool(config.compatibility_experiment),
            source_soc=config.source_soc,
            board_soc=config.board_soc,
            observed_board_soc=config.observed_board_soc,
            allow_dirty_base=bool(config.allow_dirty_base),
        )
        paths = _paths_from_config(config)
        binding = verify_pinned_artifacts(profile, paths, Path(config.pin_manifest))
        # Importing the low-level backend here, after artifact verification,
        # keeps controller-side inspection Torch-free and ACL-free.
        from acl_om_runtime import NativeAclBackend

        backend = _NotifyingBackend(NativeAclBackend(device_id=int(config.device_id), input_order_verified=True), notifier)
        runtime = Qwen25AclRuntime(
            paths.om,
            paths.tokenizer,
            contract_path=paths.contract,
            tokenizer_config_path=paths.tokenizer_config,
            backend=backend,
            max_tokens=MAX_GENERATION_TOKENS,
            device_id=int(config.device_id),
        )
        runtime.start()
        server = make_server(config.host, int(config.port), CompatibilityAclService(runtime, binding, notifier))
        notifier.end(token, "startup")
        notifier.send("ready", pid=None)
        while True:
            server.handle_request()
    except BaseException as exc:
        notifier.send("fatal", message=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        if server is not None:
            server.server_close()
        if runtime is not None:
            runtime.close()
        notifier.end(token, "startup")
        try:
            if connection is not None:
                connection.close()
        except OSError:
            pass


__all__ = [
    "ArtifactBinding",
    "ArtifactDigest",
    "ArtifactPaths",
    "COMPATIBILITY_PROFILES",
    "CompatibilityAclService",
    "CompatibilityError",
    "CompatibilityProfile",
    "CompatibilityRequestError",
    "CompletionRequest",
    "MAX_GENERATION_TOKENS",
    "MODEL_ID",
    "SOURCE_SOC",
    "TARGET_SOC",
    "WATCHDOG_EXIT_CODE",
    "WatchdogState",
    "WorkerConfig",
    "_parse_request",
    "_text_delta",
    "make_server",
    "profile_for",
    "run_worker",
    "supervise_worker",
    "validate_experiment_request",
    "verify_pinned_artifacts",
]
