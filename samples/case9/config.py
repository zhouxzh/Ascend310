"""Configuration for the XiaoZhi OpenAI-compatible gateway.

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


BASE_DIR = Path(__file__).resolve().parent
_LOOPBACK_UPSTREAM_HOSTS = {"localhost", "127.0.0.1", "::1"}
_TINYLLAMA_MODEL = "tiny-llama-1.1b-acl-om"
_QWEN25_STATIC_KV_1024_MODEL = (
    "qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om"
)
# The MindSpore candidate chain always points at the one active profile.  The
# profile registry and worker own the concrete model identity; the gateway
# deliberately exposes only the stable public ``case9-rag`` name.
_MINDSPORE_ACTIVE_MODEL = "case9-active"
# The 310B4 StaticCache graph is intentionally serialized and may need more
# than a minute for a normal Chinese answer.  Keep the longer defaults scoped
# to this model; callers can still override them with the environment.
_QWEN25_STATIC_KV_TIMEOUT_SECONDS = 270.0


class ConfigurationError(ValueError):
    """Raised when the service cannot be started safely."""


def _required(environ: Mapping[str, str], name: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise ConfigurationError(f"{name} must be set")
    return value


def _gateway_secret(environ: Mapping[str, str]) -> str:
    value = _required(environ, "GATEWAY_API_KEY")
    allowed = string.ascii_letters + string.digits + "-_.~"
    if len(value) < 24 or any(character not in allowed for character in value):
        raise ConfigurationError(
            "GATEWAY_API_KEY must be at least 24 ASCII token characters"
        )
    if value.lower().startswith("replace-with"):
        raise ConfigurationError("GATEWAY_API_KEY must be replaced with a real secret")
    return value


def _integer(
    environ: Mapping[str, str], name: str, default: int, minimum: int
) -> int:
    raw = environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}")
    return value


def _decimal(
    environ: Mapping[str, str], name: str, default: float, minimum: float
) -> float:
    raw = environ.get(name, str(default)).strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} must be a number") from exc
    if not math.isfinite(value):
        raise ConfigurationError(f"{name} must be finite")
    if value < minimum:
        raise ConfigurationError(f"{name} must be at least {minimum}")
    return value


def _boolean(environ: Mapping[str, str], name: str, default: bool) -> bool:
    raw = environ.get(name, str(default)).strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(f"{name} must be true or false")


def _upstream_base_url(environ: Mapping[str, str]) -> str:
    value = _required(environ, "UPSTREAM_BASE_URL").rstrip("/")
    try:
        parsed = urlparse(value)
    except ValueError as exc:
        raise ConfigurationError("UPSTREAM_BASE_URL is malformed") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ConfigurationError(
            "UPSTREAM_BASE_URL must be an http(s) base URL without credentials"
        )
    try:
        hostname = parsed.hostname
        port = parsed.port
    except ValueError as exc:
        raise ConfigurationError("UPSTREAM_BASE_URL contains an invalid port") from exc
    if not hostname:
        raise ConfigurationError("UPSTREAM_BASE_URL must include a host")
    if port is not None and not 1 <= port <= 65535:
        raise ConfigurationError("UPSTREAM_BASE_URL port must be between 1 and 65535")

    # The board deployment is deliberately local-only: the gateway must not
    # become a relay to an accidental cloud or LAN endpoint.  A deployment
    # that genuinely needs a private upstream can opt in with an explicit,
    # comma-separated host allowlist; loopback remains the default.
    configured_hosts = environ.get("UPSTREAM_ALLOWED_HOSTS", "").strip()
    allowed_hosts = {
        item.strip().lower().strip("[]")
        for item in configured_hosts.split(",")
        if item.strip()
    }
    if not allowed_hosts:
        allowed_hosts = set(_LOOPBACK_UPSTREAM_HOSTS)
    normalized_hostname = hostname.lower().strip("[]")
    if normalized_hostname not in allowed_hosts:
        raise ConfigurationError(
            "UPSTREAM_BASE_URL host is not allowed; use loopback or set "
            "UPSTREAM_ALLOWED_HOSTS explicitly"
        )
    return value


def _knowledge_dir(environ: Mapping[str, str]) -> Path:
    raw_value = environ.get("RAG_DOCUMENTS_DIR")
    if raw_value is not None and not raw_value.strip():
        raise ConfigurationError("RAG_DOCUMENTS_DIR must not be empty")
    raw = (raw_value if raw_value is not None else "knowledge").strip()
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = BASE_DIR / candidate
    return candidate.resolve()


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
    rag_enabled: bool
    knowledge_dir: Path
    rag_top_k: int
    rag_max_context_characters: int
    rag_min_score: float
    log_level: str
    stream_write_timeout_seconds: float = 30.0

    @classmethod
    def from_environ(
        cls, environ: Optional[Mapping[str, str]] = None
    ) -> "Settings":
        values = os.environ if environ is None else environ
        log_level = values.get("LOG_LEVEL", "INFO").strip().upper()
        if log_level not in {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}:
            raise ConfigurationError("LOG_LEVEL must be a standard Python log level")

        min_score = _decimal(values, "RAG_MIN_SCORE", 0.08, 0.0)
        if min_score > 1.0:
            raise ConfigurationError("RAG_MIN_SCORE must not exceed 1")

        upstream_model = _required(values, "UPSTREAM_MODEL")
        serial_npu_models = {
            _TINYLLAMA_MODEL,
            _QWEN25_STATIC_KV_1024_MODEL,
            _MINDSPORE_ACTIVE_MODEL,
        }
        default_concurrency = 1 if upstream_model in serial_npu_models else 4
        model_timeout = (
            _QWEN25_STATIC_KV_TIMEOUT_SECONDS
            if upstream_model
            in {_QWEN25_STATIC_KV_1024_MODEL, _MINDSPORE_ACTIVE_MODEL}
            else 60.0
        )
        model_stream_timeout = (
            _QWEN25_STATIC_KV_TIMEOUT_SECONDS
            if upstream_model in {_QWEN25_STATIC_KV_1024_MODEL, _MINDSPORE_ACTIVE_MODEL}
            else 90.0
        )
        return cls(
            gateway_api_key=_gateway_secret(values),
            public_model_id=values.get("PUBLIC_MODEL_ID", "case9-rag").strip()
            or "case9-rag",
            upstream_base_url=_upstream_base_url(values),
            upstream_api_key=values.get("UPSTREAM_API_KEY", "").strip(),
            upstream_model=upstream_model,
            upstream_timeout_seconds=_decimal(
                values, "UPSTREAM_TIMEOUT_SECONDS", model_timeout, 1.0
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
                values, "MAX_CONCURRENT_REQUESTS", default_concurrency, 1
            ),
            rate_limit_requests=_integer(values, "RATE_LIMIT_REQUESTS", 60, 1),
            rate_limit_window_seconds=_decimal(
                values, "RATE_LIMIT_WINDOW_SECONDS", 60.0, 1.0
            ),
            stream_max_seconds=_decimal(
                values, "STREAM_MAX_SECONDS", model_stream_timeout, 1.0
            ),
            stream_max_bytes=_integer(values, "STREAM_MAX_BYTES", 2_097_152, 1024),
            stream_write_timeout_seconds=_decimal(
                values, "STREAM_WRITE_TIMEOUT_SECONDS", 30.0, 0.1
            ),
            rag_enabled=_boolean(values, "RAG_ENABLED", True),
            knowledge_dir=_knowledge_dir(values),
            rag_top_k=_integer(values, "RAG_TOP_K", 3, 1),
            rag_max_context_characters=_integer(
                values, "RAG_MAX_CONTEXT_CHARACTERS", 5000, 256
            ),
            rag_min_score=min_score,
            log_level=log_level,
        )


def load_settings() -> Settings:
    """Load an optional local .env file, then validate process environment."""
    if load_dotenv is not None:
        load_dotenv(BASE_DIR / ".env", override=False)
    return Settings.from_environ()
