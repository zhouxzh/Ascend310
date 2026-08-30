"""Loopback-only OpenAI-compatible service for the 1024-token StaticCache model."""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import math
import time
import uuid
from typing import Any, Dict, Iterable, List, Mapping, Optional

from qwen25_kv_acl_runtime import (
    GenerationResult,
    MODEL_ID,
    Qwen25AclRuntime,
    RuntimeBusy,
    RuntimeExecutionTimeout,
    HARD_MAX_GENERATION_TOKENS,
    RuntimeRequestError,
    RuntimeUnavailable,
)


LOGGER = logging.getLogger("case9.qwen25_static_kv_service")
MAX_REQUEST_BYTES = 256 * 1024
MAX_MESSAGES = 32
# Reject obviously oversized text before it reaches the tokenizer or the
# serialized ACL worker.  This is an early guard only; the runtime still
# performs the authoritative token-count/context check for smaller requests.
MAX_MESSAGE_CHARACTERS = 16 * 1024
MAX_MAX_TOKENS = HARD_MAX_GENERATION_TOKENS
# The public gateway injects the same fixed-context default. Keep the ACL
# endpoint consistent for direct loopback clients, otherwise an omitted field
# silently ends a Chinese response after only a few tokens.
DEFAULT_MAX_TOKENS = MAX_MAX_TOKENS


@dataclass(frozen=True)
class CompletionRequest:
    model: str
    messages: List[Dict[str, str]]
    stream: bool
    max_tokens: int


class Qwen25StaticKvService:
    def __init__(self, runtime: Qwen25AclRuntime) -> None:
        self.runtime = runtime

    def health(self) -> Dict[str, Any]:
        return self.runtime.status()

    def models(self) -> Dict[str, Any]:
        return {"object": "list", "data": [{"id": MODEL_ID, "object": "model", "owned_by": "case9"}]}

    def complete(self, request: CompletionRequest) -> Dict[str, Any]:
        result = self.runtime.complete(request.messages, request.max_tokens)
        return _completion_payload(result, request.model)

    def stream(self, request: CompletionRequest) -> Iterable[Dict[str, Any]]:
        request_id = "chatcmpl-" + uuid.uuid4().hex
        previous = ""
        first = True
        last: Optional[GenerationResult] = None
        for event in self.runtime.stream(request.messages, request.max_tokens):
            # Runtime snapshots are cumulative.  Keep SSE deltas monotonic
            # even when a tokenizer revises a byte boundary.
            if isinstance(event, GenerationResult):
                result = event
            else:  # compatibility with a tiny fake runtime used by tests
                result = _coerce_generation_result(event)
            last = result
            delta_text = _text_delta(previous, result.text)
            # A tokenizer may revise the latest cumulative snapshot when a
            # UTF-8 boundary becomes complete.  Only advance the cursor for
            # a true prefix extension; never emit a non-prefix snapshot as a
            # fresh delta because that duplicates already delivered text.
            if result.text.startswith(previous):
                previous = result.text
            delta: Dict[str, str] = {"role": "assistant"} if first else {}
            if delta_text:
                delta["content"] = delta_text
            if first or delta_text:
                yield _chunk_payload(request_id, request.model, delta, None)
            first = False
        if first:
            yield _chunk_payload(request_id, request.model, {"role": "assistant"}, None)
        usage = None
        if last is not None:
            usage = {
                "prompt_tokens": last.prompt_tokens,
                "completion_tokens": last.completion_tokens,
                "total_tokens": last.prompt_tokens + last.completion_tokens,
            }
        yield _chunk_payload(
            request_id,
            request.model,
            {},
            last.finish_reason if last else "length",
            usage=usage,
        )


# Alias follows the existing service naming convention while making the new
# model-specific class explicit for callers and tests.
Qwen25AclService = Qwen25StaticKvService


def make_server(host: str, port: int, service: Qwen25StaticKvService) -> ThreadingHTTPServer:
    if host != "127.0.0.1":
        raise ValueError("Qwen2.5 StaticCache service must remain loopback-only")

    class Handler(BaseHTTPRequestHandler):
        server_version = "case9-qwen25-static-kv/1"

        def _json(self, status: int, value: Mapping[str, Any]) -> None:
            data = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _error(self, message: str, status: int = 400, code: str = "invalid_request_error") -> None:
            self._json(status, {"error": {"message": message, "type": "server_error" if status >= 500 else "invalid_request_error", "code": code}})

        def do_GET(self) -> None:
            if self.path in {"/health", "/healthz"}:
                value = service.health()
                self._json(200 if value.get("ready") else 503, value)
            elif self.path == "/v1/models":
                # A model listing is also an admission signal for the gateway.
                # Do not advertise an OM that failed to initialize or was
                # poisoned by a watchdog/cleanup error.
                health = service.health()
                if not health.get("ready"):
                    self._error("model service is not ready", 503, "model_unavailable")
                    return
                self._json(200, service.models())
            else:
                self._error("Not found", 404, "not_found")

        def do_POST(self) -> None:
            if self.path != "/v1/chat/completions":
                self._error("Not found", 404, "not_found")
                return
            try:
                request = _parse_request(self._read_body())
                if request.stream:
                    self._send_sse(request)
                else:
                    self._json(200, service.complete(request))
            except (RuntimeRequestError, RuntimeBusy) as exc:
                self._error(str(exc), 409 if isinstance(exc, RuntimeBusy) else 400)
            except (RuntimeUnavailable, RuntimeExecutionTimeout) as exc:
                self._error(str(exc), 503, "model_unavailable")
            except (BrokenPipeError, ConnectionResetError):
                service.runtime.cancel()
            except Exception:
                LOGGER.exception("unhandled StaticCache request failure")
                self._error("internal model service error", 500, "internal_error")

        def _read_body(self) -> Mapping[str, Any]:
            try:
                length = int(self.headers.get("Content-Length", "-1"))
            except ValueError as exc:
                raise RuntimeRequestError("invalid Content-Length") from exc
            if length < 0 or length > MAX_REQUEST_BYTES:
                raise RuntimeRequestError("request body is missing or too large")
            if "application/json" not in self.headers.get("Content-Type", "").lower():
                raise RuntimeRequestError("Content-Type must be application/json")
            try:
                raw = self.rfile.read(length)
                value = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise RuntimeRequestError("request body is not valid JSON") from exc
            if not isinstance(value, Mapping):
                raise RuntimeRequestError("request body must be an object")
            return value

        def _send_sse(self, request: CompletionRequest) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for item in service.stream(request):
                    data = json.dumps(item, ensure_ascii=False, separators=(",", ":"))
                    self.wfile.write(("data: " + data + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                service.runtime.cancel()
            except (RuntimeRequestError, RuntimeBusy) as exc:
                service.runtime.cancel()
                self._write_sse_error(str(exc), "busy" if isinstance(exc, RuntimeBusy) else "invalid_request_error")
            except (RuntimeUnavailable, RuntimeExecutionTimeout) as exc:
                service.runtime.cancel()
                self._write_sse_error(str(exc), "model_unavailable")
            except Exception:
                service.runtime.cancel()
                LOGGER.exception("unhandled StaticCache SSE failure")
                self._write_sse_error("internal model service error", "internal_error")

        def _write_sse_error(self, message: str, code: str) -> None:
            payload = {"error": {"message": message, "type": "server_error", "code": code}}
            try:
                data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                self.wfile.write(("data: " + data + "\n\ndata: [DONE]\n\n").encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, fmt: str, *args: Any) -> None:
            LOGGER.info("peer=%s " + fmt, self.client_address[0], *args)

    class StaticKvHttpServer(ThreadingHTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    return StaticKvHttpServer((host, int(port)), Handler)


def _parse_request(raw: Mapping[str, Any]) -> CompletionRequest:
    allowed = {"model", "messages", "stream", "max_tokens", "temperature", "top_p"}
    if set(raw) - allowed:
        raise RuntimeRequestError("unsupported chat completion field")
    if raw.get("model") != MODEL_ID:
        raise RuntimeRequestError("model is not available")
    messages = raw.get("messages")
    if not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES:
        raise RuntimeRequestError("messages must be a non-empty array")
    normalized: List[Dict[str, str]] = []
    for index, item in enumerate(messages):
        if not isinstance(item, Mapping) or set(item) - {"role", "content"}:
            raise RuntimeRequestError(f"messages[{index}] is invalid")
        role, content = item.get("role"), item.get("content")
        if role not in {"system", "user", "assistant"} or not isinstance(content, str) or not content:
            raise RuntimeRequestError(f"messages[{index}] role/content is invalid")
        normalized.append({"role": str(role), "content": content})
    character_count = sum(len(item["content"]) for item in normalized)
    if character_count > MAX_MESSAGE_CHARACTERS:
        raise RuntimeRequestError(
            "messages exceed the preflight character limit for the fixed context"
        )
    if not any(item["role"] == "user" for item in normalized):
        raise RuntimeRequestError("at least one user message is required")
    stream = raw.get("stream", False)
    if not isinstance(stream, bool):
        raise RuntimeRequestError("stream must be boolean")
    max_tokens = raw.get("max_tokens", DEFAULT_MAX_TOKENS)
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= MAX_MAX_TOKENS:
        raise RuntimeRequestError(f"max_tokens must be between 1 and {MAX_MAX_TOKENS}")
    for key, expected in (("temperature", 0.0), ("top_p", 1.0)):
        if key in raw and raw[key] is not None and not _is_expected_number(raw[key], expected):
            raise RuntimeRequestError(f"only greedy decoding is supported; {key} must be {expected}")
    return CompletionRequest(MODEL_ID, normalized, stream, int(max_tokens))


def _coerce_generation_result(value: Any) -> GenerationResult:
    if isinstance(value, GenerationResult):
        return value
    if isinstance(value, tuple) and len(value) >= 4:
        return GenerationResult(str(value[0]), int(value[1]), int(value[2]), str(value[3]), True)
    raise RuntimeRequestError("runtime returned an invalid generation event")


def _completion_payload(result: GenerationResult, model: str) -> Dict[str, Any]:
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": result.text}, "finish_reason": result.finish_reason}],
        "usage": {"prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens, "total_tokens": result.prompt_tokens + result.completion_tokens},
    }


def _chunk_payload(
    request_id: str,
    model: str,
    delta: Mapping[str, str],
    finish_reason: Optional[str],
    *,
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
        payload["usage"] = dict(usage)
    return payload


def _text_delta(previous: str, current: str) -> str:
    if current == previous:
        return ""
    # Cumulative runtime snapshots must be prefix-monotonic.  Returning an
    # entire revised snapshot here would make OpenAI clients render duplicate
    # text; wait for a later snapshot that extends the emitted prefix.
    return current[len(previous):] if current.startswith(previous) else ""


def _is_expected_number(value: Any, expected: float) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    numeric = float(value)
    return math.isfinite(numeric) and numeric == expected


__all__ = ["MODEL_ID", "CompletionRequest", "Qwen25StaticKvService", "Qwen25AclService", "make_server", "_parse_request", "_text_delta"]
