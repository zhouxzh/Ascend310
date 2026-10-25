"""Configuration for the Ascend Edge Chat OpenAI-compatible gateway.

Configuration is read once at startup. Credentials never come from an HTTP
request, which keeps device callers from changing the selected LLM endpoint.
"""

from __future__ import annotations

import os
import string
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional
from urllib.parse import urlparse

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - surfaced as a normal dependency error
    load_dotenv = None


PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PREFIX = "ASCEND_EDGE_CHAT_"
_LOOPBACK_UPSTREAM_HOSTS = {"localhost", "127.0.0.1", "::1"}
BACKEND_MINDSPORE = "mindspore"
BACKEND_ACL_STATIC_KV = "acl-static-kv"
MINDSPORE_UPSTREAM_MODEL = "ascend-edge-chat-worker"
ACL_STATIC_KV_UPSTREAM_MODEL = "qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om"
_RELEASE_TIMEOUT_SECONDS = 300.0

_BACKEND_CONTRACTS = {
    BACKEND_MINDSPORE: {
        "upstream_model": MINDSPORE_UPSTREAM_MODEL,
        "port": 8090,
        "context_tokens": 1024,
    },
    BACKEND_ACL_STATIC_KV: {
        "upstream_model": ACL_STATIC_KV_UPSTREAM_MODEL,
        "port": 8084,
        "context_tokens": 1024,
    },
}


class ConfigurationError(ValueError):
    """Raised when the service cannot be started safely."""


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"{ENV_PREFIX}{name} must be set")
    return value


def _gateway_secret(environ: Mapping[str, str]) -> str:
    value = _required(environ, "GATEWAY_API_KEY")
    allowed = string.ascii_letters + string.digits + "-_.~"
    if len(value) < 24 or any(character not in allowed for character in value):
        raise ConfigurationError(
            "ASCEND_EDGE_CHAT_GATEWAY_API_KEY must be at least 24 ASCII token characters"
        )
    if value.lower().startswith("replace-with"):
        raise ConfigurationError(
            "ASCEND_EDGE_CHAT_GATEWAY_API_KEY must be replaced with a real secret"
        )
    return value


def _release_values(environ: Mapping[str, str]) -> dict[str, str]:
    """Read only the public Ascend Edge Chat environment namespace."""

    return {
        key[len(ENV_PREFIX):]: value
        for key, value in environ.items()
        if key.startswith(ENV_PREFIX)
    }


def _integer(
    environ: Mapping[str, str], name: str, default: int, minimum: int
) -> int:
    raw = environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{ENV_PREFIX}{name} must be an integer") from exc
    if value < minimum:
        raise ConfigurationError(f"{ENV_PREFIX}{name} must be at least {minimum}")
    return value


def _decimal(
    environ: Mapping[str, str], name: str, default: float, minimum: float
) -> float:
    raw = environ.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{ENV_PREFIX}{name} must be a number") from exc
    if not math.isfinite(value):
        raise ConfigurationError(f"{ENV_PREFIX}{name} must be finite")
    if value < minimum:
        raise ConfigurationError(f"{ENV_PREFIX}{name} must be at least {minimum}")
    return value


def _boolean(environ: Mapping[str, str], name: str, default: bool) -> bool:
    raw = environ.get(name, str(default)).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{ENV_PREFIX}{name} must be true or false")


def _backend(environ: Mapping[str, str]) -> str:
    value = environ.get("BACKEND", BACKEND_MINDSPORE).strip().lower()
    if value not in _BACKEND_CONTRACTS:
        raise ConfigurationError(
            "ASCEND_EDGE_CHAT_BACKEND must be mindspore or acl-static-kv"
        )
    return value


def _validate_upstream_base_url(value: str, backend: str) -> str:
    """Validate the immutable loopback endpoint selected by one backend."""

    contract = _BACKEND_CONTRACTS.get(backend)
    if contract is None:
        raise ConfigurationError(
            "ASCEND_EDGE_CHAT_BACKEND must be mindspore or acl-static-kv"
        )
    value = value.strip().rstrip("/")
    try:
        parsed = urlparse(value)
    except ValueError as exc:
        raise ConfigurationError("ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL is malformed") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError(
            "ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL must be an http(s) base URL without credentials"
        )
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ConfigurationError("ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL contains an invalid port") from exc
    if not hostname:
        raise ConfigurationError("ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL must include a host")
    if port is not None and not 1 <= port <= 65535:
        raise ConfigurationError("ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL port must be between 1 and 65535")

    # The gateway must never become a cloud/LAN relay through an environment
    # edit, and no backend may be pointed at the other backend's worker port.
    normalized_hostname = hostname.lower().strip("[]")
    if normalized_hostname not in _LOOPBACK_UPSTREAM_HOSTS:
        raise ConfigurationError(
            "ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL must point to the loopback worker"
        )
    acl_static_kv_port = int(_BACKEND_CONTRACTS[BACKEND_ACL_STATIC_KV]["port"])
    if backend == BACKEND_ACL_STATIC_KV and port != acl_static_kv_port:
        raise ConfigurationError(
            "ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL for acl-static-kv must use loopback port %d"
            % acl_static_kv_port
        )
    if backend == BACKEND_MINDSPORE and port == acl_static_kv_port:
        raise ConfigurationError(
            "ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL port 8084 is reserved for acl-static-kv"
        )
    if backend == BACKEND_ACL_STATIC_KV and (
        parsed.scheme != "http" or parsed.path.rstrip("/") != "/v1"
    ):
        raise ConfigurationError(
            "ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL for acl-static-kv must be an http loopback /v1 URL"
        )
    return value


def _upstream_base_url(environ: Mapping[str, str], backend: str) -> str:
    return _validate_upstream_base_url(_required(environ, "UPSTREAM_BASE_URL"), backend)


@dataclass(frozen=True)
class Settings:
    """Immutable runtime settings for one gateway process."""

    gateway_api_key: str
    public_model_id: str
    upstream_base_url: str
    upstream_api_key: str
    upstream_model: str
    upstream_timeout_seconds: float
    request_max_bytes: int
    request_body_timeout_seconds: float
    request_max_messages: int
    request_max_characters: int
    max_concurrent_requests: int
    rate_limit_requests: int
    rate_limit_window_seconds: float
    stream_max_seconds: float
    stream_max_bytes: int
    log_level: str
    backend: str = BACKEND_MINDSPORE
    upstream_context_tokens: int = 1024
    stream_write_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        contract = _BACKEND_CONTRACTS.get(self.backend)
        if contract is None:
            raise ConfigurationError(
                "ASCEND_EDGE_CHAT_BACKEND must be mindspore or acl-static-kv"
            )
        if self.upstream_model != contract["upstream_model"]:
            raise ConfigurationError(
                "ASCEND_EDGE_CHAT_UPSTREAM_MODEL does not match backend %s" % self.backend
            )
        if self.upstream_context_tokens != contract["context_tokens"]:
            raise ConfigurationError(
                "ASCEND_EDGE_CHAT_UPSTREAM_CONTEXT_TOKENS does not match backend %s"
                % self.backend
            )
        _validate_upstream_base_url(self.upstream_base_url, self.backend)

    @classmethod
    def from_environ(
        cls, environ: Optional[Mapping[str, str]] = None
    ) -> "Settings":
        source = os.environ if environ is None else environ
        values = _release_values(source)
        log_level = values.get("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ConfigurationError("ASCEND_EDGE_CHAT_LOG_LEVEL must be a standard Python log level")

        backend = _backend(values)
        contract = _BACKEND_CONTRACTS[backend]
        upstream_model = values.get("UPSTREAM_MODEL", str(contract["upstream_model"])).strip()
        if upstream_model != contract["upstream_model"]:
            raise ConfigurationError(
                "ASCEND_EDGE_CHAT_UPSTREAM_MODEL does not match backend %s" % backend
            )
        if "RAG_ENABLED" in values or "RAG_DOCUMENTS_DIR" in values:
            raise ConfigurationError(
                "Retrieval configuration is not part of the Ascend Edge Chat v0.1.0 release"
            )
        return cls(
            gateway_api_key=_gateway_secret(values),
            public_model_id="ascend-edge-chat",
            upstream_base_url=_upstream_base_url(values, backend),
            upstream_api_key=values.get("UPSTREAM_API_KEY", "").strip(),
            upstream_model=upstream_model,
            upstream_timeout_seconds=_decimal(
                values, "UPSTREAM_TIMEOUT_SECONDS", _RELEASE_TIMEOUT_SECONDS, 1.0
            ),
            request_max_bytes=_integer(values, "REQUEST_MAX_BYTES", 262144, 1024),
            request_body_timeout_seconds=_decimal(
                values, "REQUEST_BODY_TIMEOUT_SECONDS", 15.0, 1.0
            ),
            request_max_messages=_integer(values, "REQUEST_MAX_MESSAGES", 32, 1),
            request_max_characters=_integer(
                values, "REQUEST_MAX_CHARACTERS", 24000, 1
            ),
            max_concurrent_requests=_integer(
                values, "MAX_CONCURRENT_REQUESTS", 1, 1
            ),
            rate_limit_requests=_integer(values, "RATE_LIMIT_REQUESTS", 60, 1),
            rate_limit_window_seconds=_decimal(
                values, "RATE_LIMIT_WINDOW_SECONDS", 60.0, 1.0
            ),
            stream_max_seconds=_decimal(
                values, "STREAM_MAX_SECONDS", _RELEASE_TIMEOUT_SECONDS, 1.0
            ),
            stream_max_bytes=_integer(values, "STREAM_MAX_BYTES", 2_097_152, 1024),
            backend=backend,
            upstream_context_tokens=int(contract["context_tokens"]),
            stream_write_timeout_seconds=_decimal(
                values, "STREAM_WRITE_TIMEOUT_SECONDS", 30.0, 0.1
            ),
            log_level=log_level,
        )


def load_release_environment() -> None:
    """Load the optional release environment file without executing shell code."""

    if load_dotenv is not None:
        load_dotenv(
            os.environ.get("ASCEND_EDGE_CHAT_ENV_FILE", PROJECT_ROOT / ".env"),
            override=False,
        )


def load_settings() -> Settings:
    """Load an optional local .env file, then validate process environment."""

    load_release_environment()
    return Settings.from_environ()
