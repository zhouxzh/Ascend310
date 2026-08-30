"""Small loopback-only OpenAI-compatible service for Qwen2.5 static ACL."""

from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import math
import time
import uuid
from typing import Any, Dict, List, Mapping, Optional, Sequence

from qwen25_acl_runtime import (
    GenerationResult,
    Qwen25AclRuntime,
    RuntimeBusy,
    RuntimeRequestError,
    RuntimeUnavailable,
)


LOGGER = logging.getLogger("case9.qwen25.service")
MODEL_ID = "qwen2.5-0.5b-instruct-static-fp16-acl-om"
MAX_REQUEST_BYTES = 256 * 1024
MAX_MESSAGES = 32


@dataclass(frozen=True)
class CompletionRequest:
    model: str
    messages: List[Dict[str, str]]
    stream: bool
    max_tokens: int


class Qwen25AclService:
    def __init__(self, runtime: Qwen25AclRuntime) -> None:
        self.runtime = runtime

    def health(self) -> Dict[str, Any]:
        return self.runtime.status()

    def models(self) -> Dict[str, Any]:
        return {"object": "list", "data": [{"id": MODEL_ID, "object": "model", "owned_by": "case9"}]}

    def complete(self, request: CompletionRequest) -> Dict[str, Any]:
        result = self.runtime.complete(request.messages, request.max_tokens)
        return _completion_payload(result, request.model)

    def stream(self, request: CompletionRequest):
        request_id = "chatcmpl-" + uuid.uuid4().hex
        previous_text = ""
        first = True
        last: Optional[GenerationResult] = None
        for result in self.runtime.stream(request.messages, request.max_tokens):
            last = result
            delta_text = _text_delta(previous_text, result.text)
            previous_text = result.text
            is_first = first
            delta: Dict[str, str] = {"role": "assistant"} if is_first else {}
            if delta_text:
                delta["content"] = delta_text
            first = False
            # Keep the initial role chunk even when the first generated token
            # is a special token whose decoded text is empty.
            if is_first or delta_text:
                yield _chunk_payload(request_id, request.model, delta, None)
        if first:
            yield _chunk_payload(request_id, request.model, {"role": "assistant"}, None)
        finish_reason = last.finish_reason if last is not None else "length"
        yield _chunk_payload(request_id, request.model, {}, finish_reason)


def make_server(host: str, port: int, service: Qwen25AclService) -> ThreadingHTTPServer:
    if host != "127.0.0.1":
        raise ValueError("Qwen2.5 ACL service must remain loopback-only")

    class Handler(BaseHTTPRequestHandler):
        server_version = "case9-qwen25/1"

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
            except RuntimeUnavailable as exc:
                self._error(str(exc), 503, "model_unavailable")
            except (BrokenPipeError, ConnectionResetError):
                service.runtime.cancel()
            except Exception:
                LOGGER.exception("unhandled Qwen2.5 request failure")
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
            raw = self.rfile.read(length)
            try:
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
                LOGGER.warning("SSE generation failed: %s", exc)
                self._write_sse_error(str(exc), "busy" if isinstance(exc, RuntimeBusy) else "invalid_request_error")
            except RuntimeUnavailable as exc:
                service.runtime.cancel()
                LOGGER.warning("SSE model unavailable: %s", exc)
                self._write_sse_error(str(exc), "model_unavailable")
            except Exception:
                service.runtime.cancel()
                LOGGER.exception("unhandled Qwen2.5 SSE failure")
                self._write_sse_error("internal model service error", "internal_error")

        def _write_sse_error(self, message: str, code: str) -> None:
            # Headers have already been sent for an SSE response.  Use an
            # OpenAI-compatible error event and close the stream cleanly.
            payload = {"error": {"message": message, "type": "server_error", "code": code}}
            try:
                data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                self.wfile.write(("data: " + data + "\n\ndata: [DONE]\n\n").encode("utf-8"))
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, fmt: str, *args: Any) -> None:
            LOGGER.info("peer=%s " + fmt, self.client_address[0], *args)

    class Qwen25HttpServer(ThreadingHTTPServer):
        # A client that disconnects during the slow full-context NPU call must
        # not keep interpreter shutdown blocked on a non-daemon worker.
        daemon_threads = True
        allow_reuse_address = True

    return Qwen25HttpServer((host, int(port)), Handler)


def _parse_request(raw: Mapping[str, Any]) -> CompletionRequest:
    allowed = {"model", "messages", "stream", "max_tokens", "temperature", "top_p"}
    if set(raw) - allowed:
        raise RuntimeRequestError("unsupported chat completion field")
    model = raw.get("model")
    if model != MODEL_ID:
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
    if not any(item["role"] == "user" for item in normalized):
        raise RuntimeRequestError("at least one user message is required")
    stream = raw.get("stream", False)
    if not isinstance(stream, bool):
        raise RuntimeRequestError("stream must be boolean")
    max_tokens = raw.get("max_tokens", 8)
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 8:
        raise RuntimeRequestError("max_tokens must be between 1 and 8")
    for key, expected in (("temperature", 0.0), ("top_p", 1.0)):
        if key in raw and raw[key] is not None and not _is_expected_number(raw[key], expected):
            raise RuntimeRequestError(f"only greedy decoding is supported; {key} must be {expected}")
    return CompletionRequest(MODEL_ID, normalized, stream, int(max_tokens))


def _completion_payload(result: GenerationResult, model: str) -> Dict[str, Any]:
    return {
        "id": "chatcmpl-" + uuid.uuid4().hex,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": result.text}, "finish_reason": result.finish_reason}],
        "usage": {"prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens, "total_tokens": result.prompt_tokens + result.completion_tokens},
    }


def _chunk_payload(request_id: str, model: str, delta: Mapping[str, str], finish_reason: Optional[str]) -> Dict[str, Any]:
    return {"id": request_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": model, "choices": [{"index": 0, "delta": dict(delta), "finish_reason": finish_reason}]}


def _text_delta(previous: str, current: str) -> str:
    """Return only newly decoded text for an accumulated generation result."""
    if current == previous:
        return ""
    if current.startswith(previous):
        return current[len(previous):]
    # Tokenizers can revise the boundary around a multi-byte token.  Sending
    # the current text is preferable to silently dropping a response fragment.
    return current


def _is_expected_number(value: Any, expected: float) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    numeric = float(value)
    return math.isfinite(numeric) and numeric == expected


__all__ = [
    "MODEL_ID", "CompletionRequest", "Qwen25AclService", "make_server",
    "_parse_request", "_text_delta", "_is_expected_number",
]
