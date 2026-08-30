"""Stdlib-only loopback OpenAI-compatible service for ACL/OM Qwen."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import socket
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional
import uuid

from acl_om_runtime import (
    AclOmRuntime,
    GenerationResult,
    RuntimeBusy,
    RuntimeExecutionTimeout,
    RuntimeRequestError,
    RuntimeUnavailable,
)

LOGGER = logging.getLogger("case9.acl_om_service")
MODEL_ID = "qwen1.5-0.5b-chat-acl-om"
MAX_GENERATION_TOKENS = 128
MAX_REQUEST_BYTES = 262_144
MAX_MESSAGES = 32
MAX_MESSAGE_CHARACTERS = 24_000
# The server is intentionally single-threaded.  Bound the complete request
# body read so a peer cannot hold the serving loop indefinitely with a partial
# Content-Length body (including a slow trickle below the socket timeout).
REQUEST_BODY_TIMEOUT_SECONDS = 15.0
REQUEST_BODY_READ_CHUNK = 64 * 1024


@dataclass(frozen=True)
class CompletionRequest:
    model: str
    messages: List[Dict[str, str]]
    stream: bool
    max_tokens: int


class RequestError(ValueError):
    def __init__(self, message: str, status_code: int = 400, code: str = "invalid_request_error"):
        super().__init__(message)
        self.message, self.status_code, self.code = message, int(status_code), code


class AclOmHttpService:
    """Runtime owner and protocol adapter; no web-framework dependency."""

    def __init__(self, runtime: AclOmRuntime, *, auto_start: bool = True) -> None:
        self.runtime = runtime
        self.auto_start = auto_start

    def make_server(self, host: str = "127.0.0.1", port: int = 8080) -> HTTPServer:
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("ACL/OM service must remain loopback-only")
        if self.auto_start and not self.runtime.started:
            self.runtime.start()
        # The NPU runtime is deliberately serial.  A single-threaded server
        # also keeps the POSIX execution deadline in the serving thread.
        server = HTTPServer((host, int(port)), _handler_class())
        server.acl_om_service = self  # type: ignore[attr-defined]
        return server

    def close(self) -> None:
        self.runtime.close()

    def health(self) -> Dict[str, Any]:
        return self.runtime.status()

    def models(self) -> Dict[str, Any]:
        _require_ready(self.runtime)
        return {"object": "list", "data": [{"id": MODEL_ID, "object": "model", "owned_by": "case9-acl-om"}]}

    def complete(self, request: CompletionRequest) -> Dict[str, Any]:
        _require_ready(self.runtime)
        try:
            result = self.runtime.complete(request.messages, request.max_tokens)
        except RuntimeBusy as exc:
            raise RequestError(str(exc), 429, "rate_limit_exceeded") from exc
        except RuntimeRequestError as exc:
            raise RequestError(str(exc), 400, "invalid_request_error") from exc
        except RuntimeExecutionTimeout as exc:
            raise RequestError(str(exc), 504, "timeout") from exc
        except RuntimeUnavailable as exc:
            raise RequestError(str(exc), 503, "model_unavailable") from exc
        except Exception as exc:
            LOGGER.exception("ACL completion failed")
            raise RequestError("ACL/OM inference failed", 500, "server_error") from exc
        return _completion_payload(_new_id(), result, self.runtime.model_id)

    def stream(self, request: CompletionRequest) -> Iterable[Dict[str, Any]]:
        request_id, first, previous = _new_id(), True, ""
        try:
            _require_ready(self.runtime)
            for text, _, _ in self.runtime.stream(request.messages, request.max_tokens):
                content = text[len(previous):] if text.startswith(previous) else text
                previous = text
                if not content and not first:
                    continue
                delta: Dict[str, str] = {"role": "assistant"} if first else {}
                if content:
                    delta["content"] = content
                first = False
                yield _chunk_payload(request_id, self.runtime.model_id, delta, None)
        except RuntimeBusy as exc:
            yield _stream_error(str(exc), "rate_limit_exceeded")
            return
        except RuntimeRequestError as exc:
            yield _stream_error(str(exc), "invalid_request_error")
            return
        except RuntimeExecutionTimeout as exc:
            yield _stream_error(str(exc), "timeout")
            return
        except RuntimeUnavailable as exc:
            yield _stream_error(str(exc), "model_unavailable")
            return
        except Exception:
            LOGGER.exception("ACL streaming completion failed")
            yield _stream_error("ACL/OM inference failed", "server_error")
            return
        yield _chunk_payload(request_id, self.runtime.model_id, {}, "stop")


def _handler_class():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "Case9AclOm/1.0"

        @property
        def service(self) -> AclOmHttpService:
            return self.server.acl_om_service  # type: ignore[attr-defined]

        def log_message(self, fmt: str, *args: Any) -> None:
            LOGGER.info("peer=%s " + fmt, self.client_address[0], *args)

        def do_GET(self) -> None:
            if self.path in {"/health", "/healthz"}:
                status = self.service.health()
                self._json(HTTPStatus.OK if status.get("ready") else HTTPStatus.SERVICE_UNAVAILABLE, status)
            elif self.path == "/v1/models":
                try:
                    self._json(HTTPStatus.OK, self.service.models())
                except RuntimeUnavailable as exc:
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
                    self._json(200, self.service.complete(request))
            except RequestError as exc:
                self._error(exc.message, exc.status_code, exc.code)
            except RuntimeUnavailable as exc:
                self._error(str(exc), 503, "model_unavailable")
            except (BrokenPipeError, ConnectionResetError):
                self.service.runtime.cancel()

        def _body(self) -> Mapping[str, Any]:
            if self.headers.get("Transfer-Encoding", "").lower() not in {"", "identity"}:
                raise RequestError("Chunked request bodies are not supported", 411)
            try:
                length = int(self.headers.get("Content-Length", "-1"))
            except ValueError as exc:
                raise RequestError("Invalid Content-Length") from exc
            if length < 0:
                raise RequestError("Content-Length is required", 411)
            if length > MAX_REQUEST_BYTES:
                raise RequestError("Request body exceeds the configured limit", 413, "request_too_large")
            if "application/json" not in self.headers.get("Content-Type", "").lower():
                raise RequestError("Content-Type must be application/json", 415)
            data = _read_request_body(
                self.rfile,
                self.connection,
                length,
                timeout_seconds=REQUEST_BODY_TIMEOUT_SECONDS,
            )
            if len(data) != length:
                raise RequestError("Incomplete request body")
            try:
                value = json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise RequestError("Request body is not valid JSON") from exc
            if not isinstance(value, Mapping):
                raise RequestError("Request body must be a JSON object")
            return value

        def _sse(self, request: CompletionRequest) -> None:
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                for payload in self.service.stream(request):
                    self.wfile.write(b"data: " + _json_bytes(payload) + b"\n\n")
                    self.wfile.flush()
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                self.service.runtime.cancel()

        def _json(self, status: Any, payload: Mapping[str, Any]) -> None:
            body = _json_bytes(payload)
            self.close_connection = True
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)

        def _error(self, message: str, status: int, code: str) -> None:
            self._json(status, _error_payload(message, code, status))

    return Handler


def _read_request_body(
    stream: Any,
    connection: Any,
    length: int,
    *,
    timeout_seconds: float = REQUEST_BODY_TIMEOUT_SECONDS,
) -> bytes:
    """Read exactly ``length`` bytes under one total socket deadline.

    ``BufferedReader.read(length)`` can otherwise wait forever for a peer that
    advertises a larger Content-Length than it sends.  Updating the socket
    timeout before each bounded read makes the deadline total rather than a
    fresh timeout for every trickled byte.  The prior timeout is restored so
    the HTTP handler's response path keeps its normal socket behavior.
    """
    if length < 0:
        raise RequestError("Request body length must be non-negative")
    if timeout_seconds <= 0:
        raise RequestError("Request body timeout must be positive")
    previous_timeout = connection.gettimeout()
    deadline = time.monotonic() + float(timeout_seconds)
    chunks: List[bytes] = []
    remaining = int(length)
    try:
        while remaining:
            seconds_left = deadline - time.monotonic()
            if seconds_left <= 0:
                raise RequestError(
                    "Request body read timed out", 408, "request_timeout"
                )
            connection.settimeout(seconds_left)
            try:
                # ``BufferedReader.read`` may perform several recv calls in
                # one invocation, resetting the socket timeout for each
                # trickled byte.  ``read1`` performs at most one raw read and
                # therefore keeps the outer monotonic deadline meaningful.
                read_once = getattr(stream, "read1", None)
                if not callable(read_once):
                    read_once = stream.read
                chunk = read_once(min(remaining, REQUEST_BODY_READ_CHUNK))
            except (socket.timeout, TimeoutError) as exc:
                raise RequestError(
                    "Request body read timed out", 408, "request_timeout"
                ) from exc
            if not chunk:
                break
            chunks.append(bytes(chunk))
            remaining -= len(chunk)
    finally:
        try:
            connection.settimeout(previous_timeout)
        except OSError:
            LOGGER.warning("could not restore request socket timeout", exc_info=True)
    return b"".join(chunks)


def _parse_request(raw: Mapping[str, Any]) -> CompletionRequest:
    if set(raw) - {"model", "messages", "stream", "max_tokens", "temperature", "top_p"}:
        raise RequestError("Unsupported chat completion field")
    model = raw.get("model")
    if not isinstance(model, str) or not model.strip():
        raise RequestError("model must be a non-empty string")
    if model != MODEL_ID:
        raise RequestError("Model '{}' is not available".format(model), 404, "model_not_found")
    messages = raw.get("messages")
    if not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES:
        raise RequestError("messages must contain between 1 and {} items".format(MAX_MESSAGES))
    normalized: List[Dict[str, str]] = []
    chars = 0
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping) or set(message) - {"role", "content"}:
            raise RequestError("messages[{}] is invalid".format(index))
        role, content = message.get("role"), message.get("content")
        if role not in {"system", "user", "assistant"}:
            raise RequestError("messages[{}].role is not supported".format(index))
        if not isinstance(content, str) or not content:
            raise RequestError("messages[{}].content must be non-empty text".format(index))
        if len(content) > MAX_MESSAGE_CHARACTERS:
            raise RequestError("message content is too long", 413, "request_too_large")
        normalized.append({"role": role, "content": content})
        chars += len(content)
    if chars > MAX_MESSAGE_CHARACTERS * 2:
        raise RequestError("message content is too long", 413, "request_too_large")
    if not any(item["role"] == "user" for item in normalized):
        raise RequestError("at least one user message is required")
    stream = raw.get("stream", False)
    if not isinstance(stream, bool):
        raise RequestError("stream must be a boolean")
    max_tokens = raw.get("max_tokens", MAX_GENERATION_TOKENS)
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= MAX_GENERATION_TOKENS:
        raise RequestError("max_tokens must be between 1 and {}".format(MAX_GENERATION_TOKENS))
    _greedy_parameter(raw.get("temperature"), "temperature", 0.0)
    _greedy_parameter(raw.get("top_p"), "top_p", 1.0)
    return CompletionRequest(model, normalized, stream, max_tokens)


def _greedy_parameter(value: Any, name: str, expected: float) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) != expected:
        raise RequestError("only greedy decoding is supported; {} must be {}".format(name, expected))


def _reject_constant(value: str) -> None:
    raise ValueError(value)


def _require_ready(runtime: AclOmRuntime) -> None:
    if not runtime.started:
        raise RuntimeUnavailable("ACL/OM model is not ready")


def _new_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex


def _completion_payload(request_id: str, result: GenerationResult, model: str) -> Dict[str, Any]:
    return {"id": request_id, "object": "chat.completion", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "message": {"role": "assistant", "content": result.text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": result.prompt_tokens, "completion_tokens": result.completion_tokens,
                      "total_tokens": result.prompt_tokens + result.completion_tokens}}


def _chunk_payload(request_id: str, model: str, delta: Mapping[str, str], finish_reason: Optional[str]) -> Dict[str, Any]:
    return {"id": request_id, "object": "chat.completion.chunk", "created": int(time.time()), "model": model,
            "choices": [{"index": 0, "delta": dict(delta), "finish_reason": finish_reason}]}


def _stream_error(message: str, code: str) -> Dict[str, Any]:
    return {"error": {"message": message, "type": "server_error", "param": None, "code": code}}


def _error_payload(message: str, code: str, status: int) -> Dict[str, Any]:
    return {"error": {"message": message, "type": "invalid_request_error" if status < 500 else "server_error", "param": None, "code": code}}


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Loopback-only Qwen ACL/OM OpenAI service")
    parser.add_argument("command", nargs="?", choices=("serve", "smoke"), default="serve")
    parser.add_argument("--contract", required=True)
    parser.add_argument("--om", required=True)
    parser.add_argument("--tokenizer", required=True)
    parser.add_argument("--tokenizer-config", default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--device-id", type=int, default=0)
    parser.add_argument("--execution-timeout", type=float, default=300.0)
    parser.add_argument("--prompt", default="你好")
    parser.add_argument("--max-tokens", type=int, default=8)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        parser.error("ACL/OM service must remain loopback-only")
    runtime = AclOmRuntime(
        args.contract,
        args.om,
        args.tokenizer,
        args.tokenizer_config,
        device_id=args.device_id,
        execution_timeout_seconds=args.execution_timeout,
    )
    service = AclOmHttpService(runtime)
    try:
        runtime.start()
        if args.command == "smoke":
            request = CompletionRequest(MODEL_ID, [{"role": "user", "content": args.prompt}], False, args.max_tokens)
            print(json.dumps(service.complete(request), ensure_ascii=False))
            return
        server = service.make_server(args.host, args.port)
        logging.basicConfig(level=logging.INFO)
        LOGGER.info("starting ACL/OM service on %s:%s", args.host, args.port)
        try:
            server.serve_forever(poll_interval=0.5)
        finally:
            server.server_close()
    except RuntimeUnavailable as exc:
        parser.error(str(exc))
    finally:
        service.close()


if __name__ == "__main__":
    main()
