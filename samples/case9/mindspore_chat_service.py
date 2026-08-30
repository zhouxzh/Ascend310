"""Loopback-only OpenAI-compatible service for MindSpore chat profiles.

The service is intentionally implemented with Python's standard library.  A
board worker can therefore run it in the existing MindSpore base environment
without adding another web framework or importing a Torch runtime.  Model
loading and generation are delegated to an injected provider (normally one
created by :func:`mindspore_chat_providers.create_provider`).

Only one request is allowed at a time.  The service is a candidate endpoint;
the public case9 gateway remains responsible for authentication and the
``case9-rag`` public model name.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import json
import logging
import math
import os
import queue
import select
import socket
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple
import uuid

from mindspore_chat_providers import (
    DEFAULT_MAX_TOKENS,
    MAX_MAX_TOKENS,
    GenerationResult,
    ProviderBusy,
    ProviderRequestError,
    ProviderTimeout,
    ProviderUnavailable,
    ProviderError,
    create_provider,
)


LOGGER = logging.getLogger("case9.mindspore_chat.service")

# Stable internal endpoint/model contract.  The browser and public gateway do
# not receive profile IDs or model paths from request bodies.
MODEL_ID = "case9-active"
MAX_GENERATION_TOKENS = MAX_MAX_TOKENS
DEFAULT_GENERATION_TOKENS = DEFAULT_MAX_TOKENS
MAX_REQUEST_BYTES = 256 * 1024
MAX_MESSAGES = 32
MAX_MESSAGE_CHARACTERS = 24_000
REQUEST_BODY_TIMEOUT_SECONDS = 15.0
REQUEST_BODY_READ_CHUNK = 64 * 1024
CLIENT_WRITE_TIMEOUT_SECONDS = 30.0
JSON_COMPLETION_POLL_SECONDS = 0.1
JSON_COMPLETION_GRACE_SECONDS = 1.0


@dataclass(frozen=True)
class CompletionRequest:
    model: str
    messages: List[Dict[str, str]]
    stream: bool
    max_tokens: int


class RequestError(ValueError):
    """An OpenAI request failed validation."""

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        code: str = "invalid_request_error",
    ) -> None:
        super().__init__(message)
        self.message = str(message)
        self.status_code = int(status_code)
        self.code = str(code)


class MindSporeChatService:
    """Own a single provider and expose the candidate HTTP contract."""

    def __init__(
        self,
        provider: Any,
        *,
        profile: Any = None,
        auto_start: bool = True,
        model_id: str = MODEL_ID,
    ) -> None:
        self.provider = provider
        self.runtime = provider  # compatibility with the ACL service helpers
        self.profile = profile if profile is not None else getattr(provider, "profile", None)
        self.model_id = str(model_id)
        self.auto_start = bool(auto_start)
        self._request_lock = threading.Lock()
        self._failed_closed = False
        self._failure_reason: Optional[str] = None
        self._server: Optional[HTTPServer] = None

    @property
    def started(self) -> bool:
        ready = getattr(self.provider, "ready", None)
        if ready is not None:
            return bool(ready)
        return bool(getattr(self.provider, "started", False))

    @property
    def healthy(self) -> bool:
        if self._failed_closed:
            return False
        value = getattr(self.provider, "healthy", None)
        if value is not None:
            return bool(value)
        try:
            status = self.provider.status()
            return bool(status.get("healthy", status.get("ready", False)))
        except Exception:
            return False

    def make_server(self, host: str = "127.0.0.1", port: int = 8090) -> HTTPServer:
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("MindSpore chat service must remain loopback-only")
        if self.auto_start:
            self.start()
        server = HTTPServer((host, int(port)), _handler_class())
        server.mindspore_chat_service = self  # type: ignore[attr-defined]
        self._server = server
        return server

    def start(self) -> None:
        if self._failed_closed:
            raise ProviderUnavailable(self._failure_reason or "MindSpore worker is unhealthy")
        profile_status = _profile_value(self.profile, "status", default="")
        normalized_status = str(profile_status).strip().lower()
        if normalized_status in {"blocked", "not-run"}:
            message = "selected MindSpore profile is %s" % normalized_status
            self._mark_failure(message)
            raise ProviderUnavailable(message)
        if normalized_status == "experimental_dirty_base" and os.environ.get("CASE9_ALLOW_EXPERIMENTAL") != "1":
            message = "selected MindSpore profile is experimental_dirty_base; explicit opt-in is required"
            self._mark_failure(message)
            raise ProviderUnavailable(message)
        if normalized_status not in {"admitted", "experimental_dirty_base"}:
            message = "selected MindSpore profile is not activatable: %s" % (profile_status or "missing status")
            self._mark_failure(message)
            raise ProviderUnavailable(message)
        runtime_provider = _profile_value(self.profile, "runtime_provider", "provider", default="mindspore")
        if str(runtime_provider).strip().lower() != "mindspore":
            message = "unsupported runtime provider: %s" % runtime_provider
            self._mark_failure(message)
            raise ProviderUnavailable(message)
        load = getattr(self.provider, "load", None)
        try:
            if callable(load):
                load()
            else:
                start = getattr(self.provider, "start", None)
                if callable(start):
                    start()
            if not self.started or not self.healthy:
                raise ProviderUnavailable("MindSpore chat model is not ready")
        except ProviderError as exc:
            self._mark_failure(str(exc))
            raise
        except Exception as exc:
            self._mark_failure(str(exc))
            raise ProviderUnavailable("MindSpore chat model failed to start: %s" % exc) from exc

    def close(self) -> None:
        self.cancel()
        close = getattr(self.provider, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                LOGGER.warning("provider close failed", exc_info=True)
        self._server = None

    def cancel(self, *, force: bool = False) -> None:
        cancel = getattr(self.provider, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                LOGGER.warning("provider cancellation failed", exc_info=True)
        if force:
            watchdog = getattr(self.provider, "cancel_and_watchdog", None)
            if callable(watchdog):
                try:
                    watchdog()
                except Exception:
                    LOGGER.warning("provider disconnect watchdog failed", exc_info=True)

    def health(self) -> Dict[str, Any]:
        try:
            value = self.provider.status() if callable(getattr(self.provider, "status", None)) else {}
            status = dict(value) if isinstance(value, Mapping) else {}
        except Exception as exc:
            status = {"last_error": str(exc)}
        status.setdefault("provider", "mindspore")
        status.setdefault("profile", _profile_value(self.profile, "id", "profile_id", default=None))
        status.setdefault("model", self.model_id)
        status["model_id"] = self.model_id
        status["worker_pid"] = _worker_pid()
        status["busy"] = bool(status.get("busy", False) or self._request_lock.locked())
        status.setdefault("cache_cleanup", "idle" if not status["busy"] else "in_progress")
        status.setdefault("cache_cleared", not status["busy"])
        status["healthy"] = bool(status.get("healthy", status.get("ready", False))) and not self._failed_closed
        status["ready"] = bool(status.get("ready", self.started)) and status["healthy"]
        status["admission"] = _profile_value(self.profile, "status", "admission", default=status.get("admission", "experimental_dirty_base"))
        status["admission_status"] = _profile_value(self.profile, "status", default=status.get("admission_status", status["admission"]))
        if self._failure_reason:
            status["last_error"] = self._failure_reason
        return status

    def models(self) -> Dict[str, Any]:
        if not self.started or not self.healthy:
            raise ProviderUnavailable("MindSpore chat model is not ready")
        return {
            "object": "list",
            "data": [
                {
                    "id": self.model_id,
                    "object": "model",
                    "owned_by": "case9-mindspore",
                    "permission": [],
                }
            ],
        }

    def complete(self, request: CompletionRequest) -> Dict[str, Any]:
        self._require_ready()
        try:
            with self._serial_request():
                self._validate_budget(request)
                result = self.provider.complete(request.messages, request.max_tokens)
        except ProviderBusy as exc:
            raise RequestError(str(exc), 429, "rate_limit_exceeded") from exc
        except ProviderRequestError as exc:
            raise RequestError(str(exc), 400, "invalid_request_error") from exc
        except ProviderTimeout as exc:
            self._mark_failure(str(exc))
            raise RequestError(str(exc), 504, "timeout") from exc
        except ProviderUnavailable as exc:
            self._mark_failure(str(exc))
            raise RequestError(str(exc), 503, "model_unavailable") from exc
        except Exception as exc:
            self._mark_failure(str(exc))
            LOGGER.exception("MindSpore completion failed")
            raise RequestError("MindSpore model inference failed", 500, "server_error") from exc
        normalized = _normalize_result(result)
        return _completion_payload(_new_id(), normalized, self.model_id)

    def stream(self, request: CompletionRequest) -> Iterable[Dict[str, Any]]:
        request_id = _new_id()
        first = True
        previous = ""
        prompt_tokens = 0
        completion_tokens = 0
        finish_reason = "length"
        self._require_ready()
        try:
            prompt_tokens = self._validate_budget(request)
            with self._serial_request():
                stream_method = getattr(self.provider, "stream", None)
                if not callable(stream_method):
                    result = self.provider.complete(request.messages, request.max_tokens)
                    events: Iterable[Any] = [_normalize_result(result)]
                else:
                    events = stream_method(request.messages, request.max_tokens)
                for event in events:
                    text, count, reason = _stream_event(event)
                    completion_tokens = max(completion_tokens, count)
                    if reason:
                        finish_reason = reason
                    # Providers are allowed to emit cumulative snapshots.  A
                    # non-prefix snapshot is reduced to the unseen suffix only
                    # when possible; it is never emitted as a repeated prefix.
                    delta_text = _prefix_delta(previous, text)
                    if text.startswith(previous):
                        previous = text
                    elif delta_text:
                        previous += delta_text
                    delta: Dict[str, str] = {"role": "assistant"} if first else {}
                    if delta_text:
                        delta["content"] = delta_text
                    if first or delta_text:
                        yield _chunk_payload(request_id, self.model_id, delta, None)
                    first = False
                provider_status = self.provider.status() if callable(getattr(self.provider, "status", None)) else {}
                if isinstance(provider_status, Mapping):
                    # A streaming provider may yield decoded text fragments,
                    # which are not a token-counting unit. Prefer its final
                    # sequence-derived count for the OpenAI terminal usage.
                    reported_tokens = provider_status.get("last_completion_tokens")
                    if isinstance(reported_tokens, int) and not isinstance(reported_tokens, bool):
                        completion_tokens = max(0, reported_tokens)
                    reason = provider_status.get("last_finish_reason")
                    if reason in {"stop", "length"}:
                        finish_reason = str(reason)
                    reported_prompt = provider_status.get("last_prompt_tokens")
                    if isinstance(reported_prompt, int) and not isinstance(reported_prompt, bool):
                        prompt_tokens = max(0, reported_prompt)
                if completion_tokens >= request.max_tokens and finish_reason != "stop":
                    finish_reason = "length"
        except ProviderBusy as exc:
            yield _stream_error(str(exc), "rate_limit_exceeded")
            return
        except ProviderRequestError as exc:
            yield _stream_error(str(exc), "invalid_request_error")
            return
        except ProviderTimeout as exc:
            self._mark_failure(str(exc))
            yield _stream_error(str(exc), "timeout")
            return
        except ProviderUnavailable as exc:
            self._mark_failure(str(exc))
            yield _stream_error(str(exc), "model_unavailable")
            return
        except (BrokenPipeError, ConnectionResetError, socket.timeout) as exc:
            self.cancel()
            LOGGER.info("MindSpore client disconnected: %s", type(exc).__name__)
            return
        except Exception:
            self._mark_failure("MindSpore streaming inference failed")
            LOGGER.exception("MindSpore streaming completion failed")
            yield _stream_error("MindSpore model inference failed", "server_error")
            return
        if first:
            yield _chunk_payload(request_id, self.model_id, {"role": "assistant"}, None)
        yield _chunk_payload(
            request_id,
            self.model_id,
            {},
            finish_reason,
            usage={
                "prompt_tokens": int(prompt_tokens),
                "completion_tokens": int(completion_tokens),
                "total_tokens": int(prompt_tokens + completion_tokens),
            },
        )

    def _require_ready(self) -> None:
        if self._failed_closed or not self.started or not self.healthy:
            raise ProviderUnavailable(self._failure_reason or "MindSpore chat model is not ready")

    def _validate_budget(self, request: CompletionRequest) -> int:
        """Run the authoritative tokenizer/context check before inference."""

        counter = getattr(self.provider, "count_tokens", None)
        if not callable(counter):
            # The context boundary must be checked with the selected
            # tokenizer.  Treat a provider without that contract as
            # unavailable instead of allowing an unbounded request through.
            raise ProviderUnavailable("MindSpore provider does not expose count_tokens")
        try:
            prompt_tokens = int(counter(request.messages))
        except ProviderRequestError:
            raise
        except ProviderUnavailable:
            raise
        except Exception as exc:
            raise ProviderRequestError("tokenizer could not encode the request") from exc
        context = getattr(self.provider, "context_length", None)
        if context is None:
            context = _profile_value(self.profile, "context_length", "context_window", default=1024)
        try:
            context = int(context)
        except (TypeError, ValueError):
            context = 1024
        if prompt_tokens < 0 or prompt_tokens + request.max_tokens > context:
            raise ProviderRequestError(
                "prompt plus max_tokens exceeds the %d-token context limit" % context
            )
        return prompt_tokens

    @contextmanager
    def _serial_request(self) -> Iterator[None]:
        if not self._request_lock.acquire(blocking=False):
            raise ProviderBusy("MindSpore model is busy")
        try:
            yield
        finally:
            self._request_lock.release()

    def _mark_failure(self, reason: str) -> None:
        self._failed_closed = True
        self._failure_reason = str(reason)
        cancel = getattr(self.provider, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                LOGGER.debug("provider cancel after failure failed", exc_info=True)

    def generation_deadline(self) -> float:
        """Return a bounded total deadline for a synchronous completion."""

        value = getattr(self.provider, "generation_timeout", 300.0)
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = 300.0
        if not math.isfinite(value):
            value = 300.0
        return max(0.1, min(value + JSON_COMPLETION_GRACE_SECONDS, 605.0))


# Kept as an alias for callers following the naming used by the ACL services.
MindSporeChatHttpService = MindSporeChatService
MindSporeHttpService = MindSporeChatService


def _handler_class() -> Any:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "Case9MindSpore/1.0"

        @property
        def service(self) -> MindSporeChatService:
            return self.server.mindspore_chat_service  # type: ignore[attr-defined]

        def log_message(self, fmt: str, *args: Any) -> None:
            LOGGER.info("peer=%s " + fmt, self.client_address[0], *args)

        def do_GET(self) -> None:
            if self.path in {"/health", "/healthz"}:
                status = self.service.health()
                self._json(HTTPStatus.OK if status.get("ready") else HTTPStatus.SERVICE_UNAVAILABLE, status)
            elif self.path == "/v1/models":
                try:
                    self._json(HTTPStatus.OK, self.service.models())
                except ProviderUnavailable as exc:
                    self._error(str(exc), 503, "model_unavailable")
            else:
                self._error("Not found", 404, "not_found")

        def do_POST(self) -> None:
            if self.path != "/v1/chat/completions":
                self._error("Not found", 404, "not_found")
                return
            try:
                request = _parse_request(self._body())
                if request.stream:
                    self._sse(request)
                else:
                    outcome, value = self._complete_with_disconnect_watchdog(request)
                    if outcome == "disconnected":
                        return
                    if outcome == "timeout":
                        raise RequestError(
                            "MindSpore model inference timed out",
                            504,
                            "timeout",
                        )
                    if outcome == "error":
                        raise value
                    self._json(HTTPStatus.OK, value)
            except RequestError as exc:
                self._error(exc.message, exc.status_code, exc.code)
            except ProviderUnavailable as exc:
                self._error(str(exc), 503, "model_unavailable")
            except (BrokenPipeError, ConnectionResetError):
                self.service.cancel(force=True)

        def _socket_disconnected(self) -> bool:
            """Probe for a peer FIN without consuming a pipelined byte."""

            try:
                readable, _, _ = select.select([self.connection], [], [], 0)
            except (OSError, ValueError):
                return True
            if not readable:
                return False
            try:
                data = self.connection.recv(1, socket.MSG_PEEK)
            except (BlockingIOError, InterruptedError):
                return False
            except OSError:
                return True
            return data == b""

        def _complete_with_disconnect_watchdog(
            self, request: CompletionRequest
        ) -> Tuple[str, Any]:
            """Run blocking generation off the HTTP handler thread.

            The provider's generation itself remains serialized.  Moving just
            the call to a daemon thread lets this handler observe a client
            close and invoke the provider's process-level watchdog instead of
            holding an abandoned NPU request until the model returns.
            """

            result_queue: "queue.Queue[Tuple[str, Any]]" = queue.Queue(maxsize=1)

            def run() -> None:
                try:
                    result_queue.put(("ok", self.service.complete(request)))
                except BaseException as exc:  # propagate provider failures
                    try:
                        result_queue.put(("error", exc))
                    except queue.Full:
                        LOGGER.debug("completion result was abandoned", exc_info=True)

            thread = threading.Thread(
                target=run,
                name="case9-ms-json-complete",
                daemon=True,
            )
            thread.start()
            deadline = time.monotonic() + self.service.generation_deadline()
            while True:
                try:
                    return result_queue.get(timeout=JSON_COMPLETION_POLL_SECONDS)
                except queue.Empty:
                    if self._socket_disconnected():
                        self.close_connection = True
                        self.service.cancel(force=True)
                        LOGGER.info("MindSpore JSON client disconnected")
                        return "disconnected", None
                    if time.monotonic() >= deadline:
                        self.close_connection = True
                        self.service.cancel(force=True)
                        LOGGER.error("MindSpore JSON completion exceeded total deadline")
                        return "timeout", None

        def _body(self) -> Mapping[str, Any]:
            if self.headers.get("Transfer-Encoding", "").lower() not in {"", "identity"}:
                raise RequestError("Chunked request bodies are not supported", 411, "length_required")
            try:
                length = int(self.headers.get("Content-Length", "-1"))
            except ValueError as exc:
                raise RequestError("Invalid Content-Length") from exc
            if length < 0:
                raise RequestError("Content-Length is required", 411, "length_required")
            if length > MAX_REQUEST_BYTES:
                raise RequestError("Request body exceeds the configured limit", 413, "request_too_large")
            if "application/json" not in self.headers.get("Content-Type", "").lower():
                raise RequestError("Content-Type must be application/json", 415, "unsupported_media_type")
            data = _read_request_body(self.rfile, self.connection, length)
            if len(data) != length:
                raise RequestError("Incomplete request body", 400, "invalid_request_error")
            try:
                value = json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise RequestError("Request body is not valid JSON") from exc
            if not isinstance(value, Mapping):
                raise RequestError("Request body must be a JSON object")
            return value

        @contextmanager
        def _write_deadline(self) -> Iterator[None]:
            previous = self.connection.gettimeout()
            self.connection.settimeout(CLIENT_WRITE_TIMEOUT_SECONDS)
            try:
                yield
            finally:
                try:
                    self.connection.settimeout(previous)
                except OSError:
                    self.close_connection = True

        def _sse(self, request: CompletionRequest) -> None:
            # Preflight before sending headers so malformed/unready requests
            # receive a normal JSON error rather than a half-open SSE stream.
            try:
                self.service._require_ready()
                self.service._validate_budget(request)
            except ProviderRequestError as exc:
                self._error(str(exc), 400, "invalid_request_error")
                return
            except ProviderUnavailable as exc:
                self._error(str(exc), 503, "model_unavailable")
                return
            self.close_connection = True
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store, private")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                with self._write_deadline():
                    for payload in self.service.stream(request):
                        self.wfile.write(b"data: " + _json_bytes(payload) + b"\n\n")
                        self.wfile.flush()
                    # Always terminate an HTTP-200 SSE stream, including when
                    # the provider emitted a structured error event.  Without
                    # this sentinel clients can wait indefinitely after a
                    # recoverable generation failure.
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, socket.timeout, TimeoutError) as exc:
                self.close_connection = True
                self.service.cancel(force=True)
                LOGGER.info("MindSpore SSE client disconnected: %s", type(exc).__name__)

        def _json(self, status: Any, payload: Mapping[str, Any]) -> None:
            body = _json_bytes(payload)
            self.close_connection = True
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store, private")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                with self._write_deadline():
                    self.wfile.write(body)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, socket.timeout, TimeoutError) as exc:
                self.close_connection = True
                self.service.cancel(force=True)
                LOGGER.info("MindSpore JSON client disconnected: %s", type(exc).__name__)

        def _error(self, message: str, status: int, code: str) -> None:
            self._json(status, _error_payload(message, code, status))

    return Handler


def make_server(host: str, port: int, service: MindSporeChatService) -> HTTPServer:
    """Compatibility factory matching the older ACL service modules."""

    return service.make_server(host, port)


def _parse_request(raw: Mapping[str, Any]) -> CompletionRequest:
    allowed = {"model", "messages", "stream", "max_tokens", "temperature", "top_p"}
    if set(raw) - allowed:
        raise RequestError("Unsupported chat completion field")
    model = raw.get("model")
    if not isinstance(model, str) or not model.strip():
        raise RequestError("model must be a non-empty string")
    if model != MODEL_ID:
        raise RequestError("Model %r is not available" % model, 404, "model_not_found")
    messages = raw.get("messages")
    if not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES:
        raise RequestError("messages must contain between 1 and %d items" % MAX_MESSAGES)
    normalized: List[Dict[str, str]] = []
    total_chars = 0
    for index, item in enumerate(messages):
        if not isinstance(item, Mapping) or set(item) - {"role", "content"}:
            raise RequestError("messages[%d] is invalid" % index)
        role, content = item.get("role"), item.get("content")
        if not isinstance(role, str) or role not in {"system", "user", "assistant"}:
            raise RequestError("messages[%d].role is not supported" % index)
        if not isinstance(content, str) or not content:
            raise RequestError("messages[%d].content must be non-empty text" % index)
        if len(content) > MAX_MESSAGE_CHARACTERS:
            raise RequestError("message content is too long", 413, "request_too_large")
        normalized.append({"role": role, "content": content})
        total_chars += len(content)
    if total_chars > MAX_MESSAGE_CHARACTERS * 2:
        raise RequestError("message content is too long", 413, "request_too_large")
    if not any(item["role"] == "user" for item in normalized):
        raise RequestError("at least one user message is required")
    stream = raw.get("stream", False)
    if not isinstance(stream, bool):
        raise RequestError("stream must be a boolean")
    max_tokens = raw.get("max_tokens", DEFAULT_GENERATION_TOKENS)
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= MAX_GENERATION_TOKENS:
        raise RequestError("max_tokens must be between 1 and %d" % MAX_GENERATION_TOKENS)
    _greedy_parameter(raw.get("temperature"), "temperature", 0.0)
    _greedy_parameter(raw.get("top_p"), "top_p", 1.0)
    return CompletionRequest(MODEL_ID, normalized, stream, int(max_tokens))


def _greedy_parameter(value: Any, name: str, expected: float) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RequestError("only greedy decoding is supported; %s must be %s" % (name, expected))
    if not math.isfinite(float(value)) or float(value) != expected:
        raise RequestError("only greedy decoding is supported; %s must be %s" % (name, expected))


def _read_request_body(stream: Any, connection: Any, length: int) -> bytes:
    previous = connection.gettimeout()
    deadline = time.monotonic() + REQUEST_BODY_TIMEOUT_SECONDS
    chunks: List[bytes] = []
    remaining = int(length)
    try:
        while remaining:
            left = deadline - time.monotonic()
            if left <= 0:
                raise RequestError("Request body read timed out", 408, "request_timeout")
            connection.settimeout(left)
            reader = getattr(stream, "read1", None)
            if not callable(reader):
                reader = stream.read
            try:
                chunk = reader(min(remaining, REQUEST_BODY_READ_CHUNK))
            except (socket.timeout, TimeoutError) as exc:
                raise RequestError("Request body read timed out", 408, "request_timeout") from exc
            if not chunk:
                break
            chunks.append(bytes(chunk))
            remaining -= len(chunk)
    finally:
        try:
            connection.settimeout(previous)
        except OSError:
            LOGGER.debug("could not restore request socket timeout", exc_info=True)
    return b"".join(chunks)


def _completion_payload(request_id: str, result: GenerationResult, model: str) -> Dict[str, Any]:
    return {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": result.text},
                "finish_reason": result.finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": int(result.prompt_tokens),
            "completion_tokens": int(result.completion_tokens),
            "total_tokens": int(result.prompt_tokens + result.completion_tokens),
        },
    }


def _chunk_payload(
    request_id: str,
    model: str,
    delta: Mapping[str, str],
    finish_reason: Optional[str],
    usage: Optional[Mapping[str, int]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": dict(delta), "finish_reason": finish_reason}],
    }
    if usage is not None:
        payload["usage"] = {key: int(value) for key, value in usage.items()}
    return payload


def _stream_error(message: str, code: str) -> Dict[str, Any]:
    return {"error": {"message": str(message), "type": "invalid_request_error" if code.startswith("invalid") else "server_error", "code": code}}


def _error_payload(message: str, code: str, status: int) -> Dict[str, Any]:
    return {"error": {"message": str(message), "type": "invalid_request_error" if status < 500 else "server_error", "code": code}}


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _reject_constant(value: str) -> Any:
    raise ValueError("JSON constant %s is not allowed" % value)


def _new_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex


def _prefix_delta(previous: str, current: str) -> str:
    if not current or current == previous:
        return ""
    if current.startswith(previous):
        return current[len(previous) :]
    # A provider may revise a tokenizer boundary.  There is no reversible way
    # to edit text already delivered to an OpenAI SSE client, so suppress a
    # non-prefix snapshot until the provider emits a true prefix extension.
    return ""


def _normalize_result(value: Any) -> GenerationResult:
    if isinstance(value, GenerationResult):
        return value
    if isinstance(value, Mapping):
        return GenerationResult(
            str(value.get("text", value.get("content", ""))),
            int(value.get("prompt_tokens", 0)),
            int(value.get("completion_tokens", value.get("tokens", 0))),
            str(value.get("finish_reason", "stop")),
        )
    text = str(getattr(value, "text", value if value is not None else ""))
    return GenerationResult(
        text,
        int(getattr(value, "prompt_tokens", 0)),
        int(getattr(value, "completion_tokens", 0)),
        str(getattr(value, "finish_reason", "stop")),
    )


def _stream_event(value: Any) -> Tuple[str, int, Optional[str]]:
    if isinstance(value, GenerationResult):
        return value.text, int(value.completion_tokens), value.finish_reason
    if isinstance(value, Mapping):
        return (
            str(value.get("text", value.get("content", ""))),
            int(value.get("completion_tokens", value.get("tokens", 0))),
            value.get("finish_reason"),
        )
    if isinstance(value, (tuple, list)):
        if not value:
            return "", 0, None
        text = str(value[0])
        count = int(value[1]) if len(value) > 1 and value[1] is not None else 0
        reason = value[2] if len(value) > 2 else None
        return text, count, reason
    return str(value), 0, None


def _profile_value(profile: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(profile, Mapping) and name in profile:
            return profile[name]
        if profile is not None and hasattr(profile, name):
            return getattr(profile, name)
    return default


def _worker_pid() -> int:
    try:
        import os

        return int(os.getpid())
    except Exception:
        return 0


def create_service(profile: Any, **provider_kwargs: Any) -> MindSporeChatService:
    """Build a service from a validated profile without exposing paths in API."""

    provider = create_provider(profile, **provider_kwargs)
    return MindSporeChatService(provider, profile=profile)


def _main() -> int:
    parser = argparse.ArgumentParser(description="Case9 MindSpore chat service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--profile", required=False)
    args = parser.parse_args()
    # The launcher performs the environment, artifact, and process-group
    # preflight before it invokes this module.  Refuse direct Python starts so
    # an incomplete cache (or a different port/interface) cannot look like an
    # admitted candidate service.
    if os.environ.get("CASE9_LAUNCHER_VERIFIED") != "1":
        parser.error(
            "direct start is disabled; use scripts/run_mindspore_chat_service.sh "
            "after its preflight"
        )
    if args.host != "127.0.0.1" or args.port != 8090:
        parser.error("MindSpore chat service is fixed to 127.0.0.1:8090")
    if not args.profile:
        parser.error("--profile is required; use the modelctl/launcher to select a profile")
    try:
        from case9_model_profiles import load_profiles

        # The board launcher exports the audited registry path so a copied
        # deployment can use its local lock/manifest without falling back to
        # the controller checkout's default registry.
        registry_path = os.environ.get("CASE9_MODEL_PROFILES", "").strip()
        profiles = load_profiles(registry_path) if registry_path else load_profiles()
        profile = profiles.get(args.profile)
        if profile is None:
            raise ValueError("unknown profile %r" % args.profile)
        status = str(getattr(profile, "status", "")).strip().lower()
        if status in {"blocked", "not-run"}:
            raise ValueError("profile %r is %s and cannot be started" % (args.profile, status))
        if status == "experimental_dirty_base" and os.environ.get("CASE9_ALLOW_EXPERIMENTAL") != "1":
            raise ValueError(
                "profile %r is experimental_dirty_base; set CASE9_ALLOW_EXPERIMENTAL=1 "
                "for an explicit candidate start" % args.profile
            )
        if status not in {"admitted", "experimental_dirty_base"}:
            raise ValueError("profile %r is not activatable (status=%s)" % (args.profile, status))
    except Exception as exc:
        parser.error("could not load profile: %s" % exc)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    service = create_service(profile)
    server = service.make_server(args.host, args.port)
    try:
        LOGGER.info("MindSpore chat service listening on %s:%d profile=%s", args.host, args.port, args.profile)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "MODEL_ID",
    "MAX_GENERATION_TOKENS",
    "DEFAULT_GENERATION_TOKENS",
    "MAX_REQUEST_BYTES",
    "CompletionRequest",
    "RequestError",
    "MindSporeChatService",
    "MindSporeChatHttpService",
    "make_server",
    "create_service",
    "_parse_request",
    "_prefix_delta",
]
