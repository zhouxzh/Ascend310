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
_RELEASE_WORKER_MODEL = "ascend-edge-chat-worker"
_RELEASE_TIMEOUT_SECONDS = 300.0


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


def _upstream_base_url(environ: Mapping[str, str]) -> str:
    value = _required(environ, "UPSTREAM_BASE_URL").rstrip("/")
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

    # The release gateway is deliberately local-only. It must never become a
    # relay to a cloud or LAN inference endpoint through an environment edit.
    normalized_hostname = hostname.lower().strip("[]")
    if normalized_hostname not in _LOOPBACK_UPSTREAM_HOSTS:
        raise ConfigurationError(
            "ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL must point to the loopback worker"
        )
    return value


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
    stream_write_timeout_seconds: float = 30.0

    @classmethod
    def from_environ(
        cls, environ: Optional[Mapping[str, str]] = None
    ) -> "Settings":
        source = os.environ if environ is None else environ
        values = _release_values(source)
        log_level = values.get("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ConfigurationError("ASCEND_EDGE_CHAT_LOG_LEVEL must be a standard Python log level")

        upstream_model = values.get("UPSTREAM_MODEL", _RELEASE_WORKER_MODEL).strip()
        if upstream_model != _RELEASE_WORKER_MODEL:
            raise ConfigurationError(
                "ASCEND_EDGE_CHAT_UPSTREAM_MODEL must be ascend-edge-chat-worker"
            )
        if "RAG_ENABLED" in values or "RAG_DOCUMENTS_DIR" in values:
            raise ConfigurationError(
                "Retrieval configuration is not part of the Ascend Edge Chat v0.1.0 release"
            )
        return cls(
            gateway_api_key=_gateway_secret(values),
            public_model_id="ascend-edge-chat",
            upstream_base_url=_upstream_base_url(values),
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
