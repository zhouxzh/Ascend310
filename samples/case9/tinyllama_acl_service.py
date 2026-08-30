"""Loopback-only OpenAI-compatible HTTP service for TinyLlama ACL/OM."""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from dataclasses import replace
import hashlib
import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import socket
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Union
import uuid

from tinyllama_acl_contract import ContractError, MODEL_ID as CONTRACT_MODEL_ID, TinyLlamaContract
from tinyllama_acl_runtime import (
    GenerationResult,
    NativeTinyLlamaBackend,
    RuntimeBusy,
    RuntimeExecutionTimeout,
    RuntimeRequestError,
    RuntimeUnavailable,
    RuntimeDescriptor,
    TinyLlamaAclRuntime,
    MAX_EXECUTION_TIMEOUT_SECONDS,
    DEFAULT_MAX_GENERATION_TOKENS,
)


LOGGER = logging.getLogger("case9.tinyllama_service")
MODEL_ID = CONTRACT_MODEL_ID
# Eight tokens is the measured safe default on the 310B4 board (roughly
# 24-26 seconds for the current OM).  Keep the HTTP contract bounded to this
# value so a default request cannot hit the 50-second execution deadline.
MAX_GENERATION_TOKENS = DEFAULT_MAX_GENERATION_TOKENS
MAX_REQUEST_BYTES = 256 * 1024
MAX_MESSAGES = 32
MAX_MESSAGE_CHARACTERS = 24_000
REQUEST_BODY_TIMEOUT_SECONDS = 15.0
REQUEST_BODY_READ_CHUNK = 64 * 1024
CLIENT_WRITE_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class CompletionRequest:
    model: str
    messages: List[Dict[str, str]]
    stream: bool
    max_tokens: int


class RequestError(ValueError):
    def __init__(self, message: str, status_code: int = 400, code: str = "invalid_request_error") -> None:
        super().__init__(message)
        self.message, self.status_code, self.code = message, int(status_code), code


class TinyLlamaAclHttpService:
    """Owns a serial runtime and exposes only the required OpenAI endpoints."""

    def __init__(self, runtime: Any, *, auto_start: bool = True) -> None:
        self.runtime = runtime
        self.auto_start = bool(auto_start)

    def make_server(self, host: str = "127.0.0.1", port: int = 8080) -> HTTPServer:
        if host != "127.0.0.1":
            raise ValueError("TinyLlama service must remain loopback-only")
        if self.auto_start and not self.runtime.started:
            self.runtime.start()
        server = HTTPServer((host, int(port)), _handler_class())
        server.tinyllama_service = self  # type: ignore[attr-defined]
        return server

    def close(self) -> None:
        self.runtime.close()

    def health(self) -> Dict[str, Any]:
        return dict(self.runtime.status())

    def models(self) -> Dict[str, Any]:
        _require_ready(self.runtime)
        return {"object": "list", "data": [{"id": MODEL_ID, "object": "model", "owned_by": "case9-tinyllama"}]}

    def complete(self, request: CompletionRequest) -> Dict[str, Any]:
        try:
            _require_ready(self.runtime)
            _validate_prompt_budget(self.runtime, request)
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
            LOGGER.exception("TinyLlama completion failed")
            raise RequestError("TinyLlama ACL inference failed", 500, "server_error") from exc
        finish_reason = _finish_reason(result, request.max_tokens)
        return _completion_payload(_new_id(), result, self.runtime.model_id, finish_reason)

    def stream(self, request: CompletionRequest) -> Iterable[Dict[str, Any]]:
        request_id = _new_id()
        first = True
        last_completion_tokens = 0
        try:
            _require_ready(self.runtime)
            _validate_prompt_budget(self.runtime, request)
            for text, _, completion_tokens in self.runtime.stream(request.messages, request.max_tokens):
                last_completion_tokens = int(completion_tokens)
                # The runtime yields a complete, tokenizer-stable delta.  Do
                # not infer deltas by slicing cumulative decoded strings: a
                # partial BPE/UTF-8 sequence may change its prefix later.
                content = text
                delta: Dict[str, str] = {"role": "assistant"} if first else {}
                if content:
                    delta["content"] = content
                if delta or first:
                    yield _chunk_payload(request_id, self.runtime.model_id, delta, None)
                first = False
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
            LOGGER.exception("TinyLlama streaming completion failed")
            yield _stream_error("TinyLlama ACL inference failed", "server_error")
            return
        runtime_reason = getattr(self.runtime, "last_stop_reason", None)
        if runtime_reason not in {"stop", "length"}:
            runtime_reason = "length" if last_completion_tokens >= request.max_tokens else "stop"
        finish_reason = str(runtime_reason)
        yield _chunk_payload(request_id, self.runtime.model_id, {}, finish_reason)


# Short alias for callers that do not need to distinguish HTTP from runtime.
TinyLlamaHttpService = TinyLlamaAclHttpService
TinyLlamaAclService = TinyLlamaAclHttpService


def _handler_class():
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "Case9TinyLlama/1.0"

        @property
        def service(self) -> TinyLlamaAclHttpService:
            return self.server.tinyllama_service  # type: ignore[attr-defined]

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
            data = _read_request_body(self.rfile, self.connection, length)
            if len(data) != length:
                raise RequestError("Incomplete request body")
            try:
                value = json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise RequestError("Request body is not valid JSON") from exc
            if not isinstance(value, Mapping):
                raise RequestError("Request body must be a JSON object")
            return value

        @contextmanager
        def _client_write_deadline(self):
            """Bound response headers/body writes to a non-reading client."""

            previous_timeout = self.connection.gettimeout()
            self.connection.settimeout(CLIENT_WRITE_TIMEOUT_SECONDS)
            try:
                yield
            finally:
                try:
                    self.connection.settimeout(previous_timeout)
                except OSError:
                    # The peer may already have closed the socket.  The
                    # handler marks it for closure and will not reuse it.
                    self.close_connection = True

        def _cancel_after_disconnect(self, exc: BaseException) -> None:
            LOGGER.info("TinyLlama client disconnected during response: %s", type(exc).__name__)
            self.close_connection = True
            try:
                self.service.runtime.cancel()
            except Exception:
                LOGGER.warning("could not cancel TinyLlama request after client disconnect", exc_info=True)

        def _sse(self, request: CompletionRequest) -> None:
            # The generator body is lazy, so checking readiness only inside
            # ``service.stream`` would send a 200 status before discovering a
            # failed ACL startup.  Preflight while an HTTP error is still
            # possible.
            try:
                _require_ready(self.service.runtime)
                _validate_prompt_budget(self.service.runtime, request)
            except RuntimeRequestError as exc:
                self._error(str(exc), 400, "invalid_request_error")
                return
            except RuntimeUnavailable as exc:
                self._error(str(exc), 503, "model_unavailable")
                return
            self.close_connection = True
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store, private")
            self.send_header("Connection", "close")
            self.end_headers()
            stream_failed = False
            try:
                with self._client_write_deadline():
                    for payload in self.service.stream(request):
                        if isinstance(payload, Mapping) and "error" in payload:
                            stream_failed = True
                        self.wfile.write(b"data: " + _json_bytes(payload) + b"\n\n")
                        self.wfile.flush()
                    if not stream_failed:
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, socket.timeout, TimeoutError) as exc:
                self._cancel_after_disconnect(exc)

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
                with self._client_write_deadline():
                    self.wfile.write(body)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, socket.timeout, TimeoutError) as exc:
                self._cancel_after_disconnect(exc)

        def _error(self, message: str, status: int, code: str) -> None:
            self._json(status, _error_payload(message, code, status))

    return Handler


def _parse_request(raw: Mapping[str, Any]) -> CompletionRequest:
    allowed = {"model", "messages", "stream", "max_tokens", "temperature", "top_p"}
    if set(raw) - allowed:
        raise RequestError("Unsupported chat completion field")
    model = raw.get("model")
    if not isinstance(model, str) or not model.strip():
        raise RequestError("model must be a non-empty string")
    if model != MODEL_ID:
        raise RequestError(f"Model {model!r} is not available", 404, "model_not_found")
    messages = raw.get("messages")
    if not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES:
        raise RequestError(f"messages must contain between 1 and {MAX_MESSAGES} items")
    normalized: List[Dict[str, str]] = []
    characters = 0
    for index, message in enumerate(messages):
        if not isinstance(message, Mapping) or set(message) - {"role", "content"}:
            raise RequestError(f"messages[{index}] is invalid")
        role, content = message.get("role"), message.get("content")
        if not isinstance(role, str) or role not in {"system", "user", "assistant"}:
            raise RequestError(f"messages[{index}].role is not supported")
        if not isinstance(content, str) or not content:
            raise RequestError(f"messages[{index}].content must be non-empty text")
        if len(content) > MAX_MESSAGE_CHARACTERS:
            raise RequestError("message content is too long", 413, "request_too_large")
        normalized.append({"role": str(role), "content": content})
        characters += len(content)
    if characters > MAX_MESSAGE_CHARACTERS * 2:
        raise RequestError("message content is too long", 413, "request_too_large")
    if not any(item["role"] == "user" for item in normalized):
        raise RequestError("at least one user message is required")
    stream = raw.get("stream", False)
    if not isinstance(stream, bool):
        raise RequestError("stream must be a boolean")
    max_tokens = raw.get("max_tokens", MAX_GENERATION_TOKENS)
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= MAX_GENERATION_TOKENS:
        raise RequestError(f"max_tokens must be between 1 and {MAX_GENERATION_TOKENS}")
    _greedy_parameter(raw.get("temperature"), "temperature", 0.0)
    _greedy_parameter(raw.get("top_p"), "top_p", 1.0)
    return CompletionRequest(MODEL_ID, normalized, stream, int(max_tokens))


def _read_request_body(stream: Any, connection: Any, length: int) -> bytes:
    """Read a bounded body under one total socket deadline."""
    previous_timeout = connection.gettimeout()
    deadline = time.monotonic() + REQUEST_BODY_TIMEOUT_SECONDS
    chunks: List[bytes] = []
    remaining = int(length)
    try:
        while remaining:
            seconds_left = deadline - time.monotonic()
            if seconds_left <= 0:
                raise RequestError("Request body read timed out", 408, "request_timeout")
            connection.settimeout(seconds_left)
            read_once = getattr(stream, "read1", None)
            if not callable(read_once):
                read_once = stream.read
            try:
                chunk = read_once(min(remaining, REQUEST_BODY_READ_CHUNK))
            except (socket.timeout, TimeoutError) as exc:
                raise RequestError("Request body read timed out", 408, "request_timeout") from exc
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


def _greedy_parameter(value: Any, name: str, expected: float) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) != expected:
        raise RequestError(f"only greedy decoding is supported; {name} must be {expected}")


def _require_ready(runtime: Any) -> None:
    if not runtime.started:
        raise RuntimeUnavailable("TinyLlama ACL model is not ready")


def _validate_prompt_budget(runtime: Any, request: CompletionRequest) -> None:
    """Run the tokenizer-backed context check before committing an HTTP body."""

    validator = getattr(runtime, "validate_prompt_budget", None)
    if callable(validator):
        try:
            validator(request.messages, request.max_tokens)
        except RuntimeRequestError:
            raise
        except RuntimeUnavailable:
            raise
        except Exception as exc:
            LOGGER.exception("TinyLlama prompt validation failed")
            raise RuntimeUnavailable("TinyLlama prompt validation failed") from exc


def _new_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex


def _completion_payload(
    request_id: str, result: GenerationResult, model: str, finish_reason: str = "stop"
) -> Dict[str, Any]:
    return {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": result.text}, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "total_tokens": result.prompt_tokens + result.completion_tokens,
        },
    }


def _finish_reason(result: Any, max_tokens: int) -> str:
    """Use the runtime's explicit EOS/length reason, with old-runtime fallback."""

    reason = getattr(result, "finish_reason", None)
    if reason in {"stop", "length"}:
        return str(reason)
    return "length" if int(getattr(result, "completion_tokens", 0)) >= int(max_tokens) else "stop"


def _chunk_payload(request_id: str, model: str, delta: Mapping[str, str], finish_reason: Optional[str]) -> Dict[str, Any]:
    return {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": dict(delta), "finish_reason": finish_reason}],
    }


def _stream_error(message: str, code: str) -> Dict[str, Any]:
    return {"error": {"message": message, "type": "server_error", "param": None, "code": code}}


def _error_payload(message: str, code: str, status: int) -> Dict[str, Any]:
    return {
        "error": {
            "message": message,
            "type": "invalid_request_error" if status < 500 else "server_error",
            "param": None,
            "code": code,
        }
    }


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _reject_constant(value: str) -> None:
    raise ValueError(value)


def inspect_model(om_path: Union[str, Path], contract_path: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """Load an OM descriptor and emit a JSON-safe contract report."""
    om_file = Path(om_path).expanduser()
    if not om_file.is_file():
        raise RuntimeUnavailable(f"OM file does not exist: {om_file}")
    contract_file = Path(contract_path).expanduser() if contract_path else None
    existing_contract = None
    if contract_file is not None and contract_file.is_file():
        existing_contract = TinyLlamaContract.load(contract_file)
    backend = NativeTinyLlamaBackend(
        input_names=tuple(existing_contract.input_map) if existing_contract and existing_contract.inputs else None,
        output_names=tuple(item.name for item in existing_contract.outputs) if existing_contract and existing_contract.outputs else None,
    )
    try:
        descriptor = backend.open(om_file)
        contract = existing_contract
        if contract is None:
            from tinyllama_acl_runtime import _contract_from_descriptor

            contract = _contract_from_descriptor(descriptor)
        contract.validate_static_expectations(strict_dimensions=True)
        contract.validate_descriptor(descriptor.inputs, descriptor.outputs)
        # Bind every generated contract to the exact OM object.  The board
        # provisioning flow later adds the immutable source revision; direct
        # CLI users still get byte/SHA fail-closed behavior immediately.
        actual_bytes = om_file.stat().st_size
        digest = hashlib.sha256()
        with om_file.open("rb") as source:
            for block in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(block)
        actual_sha = digest.hexdigest()
        if contract.source_bytes is not None and contract.source_bytes != actual_bytes:
            raise ContractError("existing contract source byte count differs from the OM")
        if contract.source_sha256 is not None and contract.source_sha256.lower() != actual_sha:
            raise ContractError("existing contract source SHA-256 differs from the OM")
        if contract.source_bytes is None or contract.source_sha256 is None:
            contract = replace(contract, source_bytes=actual_bytes, source_sha256=actual_sha)
        result = contract.as_dict()
        result["descriptor"] = {
            "inputs": [item.__dict__ for item in descriptor.inputs],
            "outputs": [item.__dict__ for item in descriptor.outputs],
        }
        if contract_file is not None:
            contract_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = contract_file.with_name(contract_file.name + ".tmp")
            temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            temporary.replace(contract_file)
        return result
    finally:
        backend.close()


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Torch-free TinyLlama ACL/OM service")
    sub = parser.add_subparsers(dest="command")
    for command in ("inspect", "smoke", "serve"):
        subparser = sub.add_parser(command)
        subparser.add_argument("--om", required=True)
        subparser.add_argument("--tokenizer", required=command != "inspect")
        subparser.add_argument("--tokenizer-config", default=None)
        subparser.add_argument("--manifest", required=command != "inspect")
        subparser.add_argument("--contract", required=command != "inspect")
        subparser.add_argument("--device-id", type=int, default=0)
    serve_parser = sub.choices["serve"]
    serve_parser.add_argument("--host", default="127.0.0.1")
    serve_parser.add_argument("--port", type=int, default=8080)
    serve_parser.add_argument("--max-tokens", type=int, default=MAX_GENERATION_TOKENS)
    serve_parser.add_argument("--execution-timeout", type=float, default=MAX_EXECUTION_TIMEOUT_SECONDS)
    smoke_parser = sub.choices["smoke"]
    smoke_parser.add_argument("--prompt", default="Hello")
    smoke_parser.add_argument("--max-tokens", type=int, default=8)
    smoke_parser.add_argument("--execution-timeout", type=float, default=MAX_EXECUTION_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    if not args.command:
        parser.error("a command is required: inspect, smoke, or serve")
    try:
        if args.command == "inspect":
            print(json.dumps(inspect_model(args.om, args.contract), ensure_ascii=False, indent=2))
            return 0
        runtime = TinyLlamaAclRuntime(
            args.om,
            args.tokenizer,
            contract_path=args.contract,
            tokenizer_config_path=args.tokenizer_config,
            tokenizer_manifest_path=args.manifest,
            device_id=args.device_id,
            max_tokens=args.max_tokens,
            execution_timeout_seconds=args.execution_timeout,
        )
        service = TinyLlamaAclHttpService(runtime, auto_start=False)
        try:
            runtime.start()
            if args.command == "smoke":
                request = CompletionRequest(
                    MODEL_ID,
                    [{"role": "user", "content": args.prompt}],
                    False,
                    args.max_tokens,
                )
                print(json.dumps(service.complete(request), ensure_ascii=False))
                return 0
            if args.host != "127.0.0.1":
                parser.error("TinyLlama service must remain loopback-only")
            server = service.make_server(args.host, args.port)
            logging.basicConfig(level=logging.INFO)
            LOGGER.info("starting TinyLlama ACL service on %s:%s", args.host, args.port)
            try:
                server.serve_forever(poll_interval=0.5)
            finally:
                server.server_close()
            return 0
        finally:
            # Keep ACL teardown on every path, including failed startup,
            # inference exceptions, and interrupted smoke runs.
            service.close()
    except (RuntimeUnavailable, RuntimeRequestError, ContractError) as exc:
        LOGGER.error("TinyLlama startup failed: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CompletionRequest",
    "MODEL_ID",
    "MAX_GENERATION_TOKENS",
    "RequestError",
    "TinyLlamaAclHttpService",
    "TinyLlamaAclService",
    "TinyLlamaHttpService",
    "_parse_request",
    "inspect_model",
]
