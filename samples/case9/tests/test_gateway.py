from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any, Optional

from fastapi.testclient import TestClient
import httpx

from app import (
    RequestBodyLimitMiddleware,
    RequestCapacity,
    _GatewayTimedStreamingResponse,
    _StreamLease,
    _scope_authorized,
    _stream_bytes,
    create_app,
)
from app import (
    _MINDSPORE_ACTIVE_UPSTREAM_MODEL,
    _QWEN25_STATIC_KV_1024_UPSTREAM_MODEL,
    _TINYLLAMA_UPSTREAM_MODEL,
)
from config import Settings
from retrieval import LocalRetriever
from upstream import OpenAICompatibleUpstream, UpstreamError


class FakeStream:
    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks
        self.closed = False

    async def _iterate(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk

    def iter_bytes(self) -> AsyncIterator[bytes]:
        return self._iterate()

    async def aclose(self) -> None:
        self.closed = True


class FakeStreamWithoutDone(FakeStream):
    pass


class FakeUpstream:
    def __init__(self) -> None:
        self.complete_payloads: list[dict[str, Any]] = []
        self.stream_payloads: list[dict[str, Any]] = []
        self.stream_handle: Optional[FakeStream] = None
        self.closed = False

    async def complete(self, payload: dict[str, Any], request_id: str) -> dict[str, Any]:
        self.complete_payloads.append(payload)
        return {
            "id": request_id,
            "object": "chat.completion",
            "model": payload["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}}],
        }

    async def stream(self, payload: dict[str, Any], request_id: str) -> FakeStream:
        self.stream_payloads.append(payload)
        self.stream_handle = FakeStream(
            [
                b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
                b"data: [DONE]\n\n",
            ]
        )
        return self.stream_handle

    async def aclose(self) -> None:
        self.closed = True


def make_settings(knowledge_dir: Path) -> Settings:
    return Settings(
        gateway_api_key="gateway-token-0123456789abcdef",
        public_model_id="case9-rag",
        upstream_base_url="http://upstream.example/v1",
        upstream_api_key="upstream-token",
        upstream_model="fixed-upstream-model",
        upstream_timeout_seconds=5.0,
        request_max_bytes=262144,
        request_body_timeout_seconds=5.0,
        request_max_messages=8,
        request_max_characters=1000,
        max_concurrent_requests=2,
        rate_limit_requests=30,
        rate_limit_window_seconds=60.0,
        stream_max_seconds=10.0,
        stream_max_bytes=16_384,
        rag_enabled=True,
        knowledge_dir=knowledge_dir,
        rag_top_k=2,
        rag_max_context_characters=500,
        rag_min_score=0.01,
        log_level="INFO",
    )


class GatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.knowledge_dir = Path(self.temp_dir.name)
        (self.knowledge_dir / "deployment.md").write_text(
            "小智服务端通过 OpenAI 兼容接口调用 case9 网关。", encoding="utf-8"
        )
        self.upstream = FakeUpstream()
        self.app = create_app(
            make_settings(self.knowledge_dir), self.upstream, LocalRetriever(self.knowledge_dir)
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_models_require_a_bearer_token(self) -> None:
        with TestClient(self.app) as client:
            rejected = client.get("/v1/models")
            accepted = client.get(
                "/v1/models",
                headers={"Authorization": "Bearer gateway-token-0123456789abcdef"},
            )

        self.assertEqual(rejected.status_code, 401)
        self.assertEqual(rejected.json()["error"]["code"], "invalid_api_key")
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["data"][0]["id"], "case9-rag")

    def test_non_ascii_authorization_bytes_are_rejected(self) -> None:
        scope = {
            "type": "http",
            "headers": [(b"authorization", b"Bearer \xff")],
        }
        self.assertFalse(
            _scope_authorized(scope, "gateway-token-0123456789abcdef")
        )

    def test_completion_injects_references_and_fixes_upstream_model(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer gateway-token-0123456789abcdef"},
                json={
                    "model": "case9-rag",
                    "messages": [{"role": "user", "content": "小智如何调用网关？"}],
                    "stream": False,
                    "temperature": 0.2,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["choices"][0]["message"]["content"], "ok")
        payload = self.upstream.complete_payloads[-1]
        self.assertEqual(payload["model"], "fixed-upstream-model")
        self.assertTrue(any("<references>" in item["content"] for item in payload["messages"]))
        self.assertTrue(self.upstream.closed)

    def test_tinyllama_adapter_filters_provider_only_fields(self) -> None:
        settings = replace(make_settings(self.knowledge_dir), upstream_model=_TINYLLAMA_UPSTREAM_MODEL)
        app = create_app(settings, self.upstream, LocalRetriever(self.knowledge_dir))
        with TestClient(app) as client:
            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer gateway-token-0123456789abcdef"},
                json={
                    "model": "case9-rag",
                    "messages": [{"role": "user", "content": "hello", "name": "device"}],
                    "stream": False,
                    "max_tokens": 8,
                    "temperature": 0,
                    "top_p": 1,
                    "frequency_penalty": 0.5,
                    "presence_penalty": 0.5,
                    "stop": ["END"],
                    "user": "device-1",
                },
            )
        self.assertEqual(response.status_code, 200)
        payload = self.upstream.complete_payloads[-1]
        for field in ("frequency_penalty", "presence_penalty", "stop", "user"):
            self.assertNotIn(field, payload)
        self.assertEqual(payload["messages"][0], {"role": "user", "content": "hello"})
        self.assertFalse(any("<references>" in item["content"] for item in payload["messages"]))

    def test_mindspore_candidate_keeps_public_model_id_in_json_and_sse(self) -> None:
        settings = replace(
            make_settings(self.knowledge_dir),
            upstream_model=_MINDSPORE_ACTIVE_UPSTREAM_MODEL,
            rag_enabled=False,
        )
        upstream = FakeUpstream()
        app = create_app(settings, upstream, None)
        with TestClient(app) as client:
            json_response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer gateway-token-0123456789abcdef"},
                json={
                    "model": "case9-rag",
                    "messages": [{"role": "user", "content": "你好"}],
                    "stream": False,
                    "max_tokens": 2,
                    "temperature": 0,
                    "top_p": 1,
                },
            )
            sse_response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer gateway-token-0123456789abcdef"},
                json={
                    "model": "case9-rag",
                    "messages": [{"role": "user", "content": "你好"}],
                    "stream": True,
                    "max_tokens": 2,
                    "temperature": 0,
                    "top_p": 1,
                },
            )

        self.assertEqual(json_response.status_code, 200)
        self.assertEqual(json_response.json()["model"], "case9-rag")
        data_lines = [
            json.loads(line[6:])
            for line in sse_response.text.splitlines()
            if line.startswith("data: {")
        ]
        self.assertTrue(data_lines)
        self.assertTrue(all(item["model"] == "case9-rag" for item in data_lines))

    def test_tinyllama_adapter_rejects_non_greedy_limits(self) -> None:
        settings = replace(make_settings(self.knowledge_dir), upstream_model=_TINYLLAMA_UPSTREAM_MODEL)
        app = create_app(settings, self.upstream, LocalRetriever(self.knowledge_dir))
        for extra in ({"max_tokens": 9}, {"temperature": 0.1}, {"top_p": 0.9}):
            with TestClient(app) as client:
                response = client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": "Bearer gateway-token-0123456789abcdef"},
                    json={
                        "model": "case9-rag",
                        "messages": [{"role": "user", "content": "hello"}],
                        **extra,
                    },
                )
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["error"]["code"], "invalid_request_error")
        self.assertEqual(self.upstream.complete_payloads, [])

    def test_qwen_static_kv_adapter_filters_fields_and_skips_rag(self) -> None:
        settings = replace(
            make_settings(self.knowledge_dir),
            upstream_model=_QWEN25_STATIC_KV_1024_UPSTREAM_MODEL,
        )
        app = create_app(settings, self.upstream, LocalRetriever(self.knowledge_dir))
        with TestClient(app) as client:
            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer gateway-token-0123456789abcdef"},
                json={
                    "model": "case9-rag",
                    "messages": [
                        {"role": "user", "content": "Qwen 中文测试", "name": "device"}
                    ],
                    "stream": False,
                    "max_tokens": 32,
                    "temperature": 0,
                    "top_p": 1,
                    "frequency_penalty": 0.5,
                    "presence_penalty": 0.5,
                    "stop": ["END"],
                    "user": "device-1",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = self.upstream.complete_payloads[-1]
        self.assertEqual(payload["model"], _QWEN25_STATIC_KV_1024_UPSTREAM_MODEL)
        for field in ("frequency_penalty", "presence_penalty", "stop", "user"):
            self.assertNotIn(field, payload)
        self.assertEqual(
            payload["messages"], [{"role": "user", "content": "Qwen 中文测试"}]
        )
        self.assertFalse(
            any("<references>" in item["content"] for item in payload["messages"])
        )

    def test_qwen_static_kv_adapter_injects_the_admitted_default_token_budget(self) -> None:
        settings = replace(
            make_settings(self.knowledge_dir),
            upstream_model=_QWEN25_STATIC_KV_1024_UPSTREAM_MODEL,
        )
        app = create_app(settings, self.upstream, LocalRetriever(self.knowledge_dir))
        with TestClient(app) as client:
            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer gateway-token-0123456789abcdef"},
                json={
                    "model": "case9-rag",
                    "messages": [{"role": "user", "content": "Qwen 中文测试"}],
                    "stream": False,
                    "temperature": 0,
                    "top_p": 1,
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.upstream.complete_payloads[-1]["max_tokens"], 80)

    def test_qwen_static_kv_rejects_context_and_sampling_over_limits(self) -> None:
        settings = replace(
            make_settings(self.knowledge_dir),
            upstream_model=_QWEN25_STATIC_KV_1024_UPSTREAM_MODEL,
        )
        app = create_app(settings, self.upstream, LocalRetriever(self.knowledge_dir))
        requests = [
            {"max_tokens": 81},
            {"temperature": 0.1},
            {"top_p": 0.9},
            {"messages": [{"role": "user", "content": "x" * 769}]},
        ]
        for overrides in requests:
            payload = {
                "model": "case9-rag",
                "messages": [{"role": "user", "content": "hello"}],
            }
            payload.update(overrides)
            with TestClient(app) as client:
                response = client.post(
                    "/v1/chat/completions",
                    headers={"Authorization": "Bearer gateway-token-0123456789abcdef"},
                    json=payload,
                )
            expected_status = 400 if "messages" not in overrides else 413
            expected_code = (
                "invalid_request_error"
                if "messages" not in overrides
                else "request_too_large"
            )
            self.assertEqual(response.status_code, expected_status)
            self.assertEqual(response.json()["error"]["code"], expected_code)
        self.assertEqual(self.upstream.complete_payloads, [])

    def test_streaming_response_is_forwarded_and_closed(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer gateway-token-0123456789abcdef"},
                json={
                    "model": "case9-rag",
                    "messages": [{"role": "user", "content": "测试流式响应"}],
                    "stream": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        self.assertIn(b"data: [DONE]", response.content)
        self.assertIsNotNone(self.upstream.stream_handle)
        self.assertTrue(self.upstream.stream_handle.closed)

    def test_unknown_model_never_reaches_the_upstream(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer gateway-token-0123456789abcdef"},
                json={
                    "model": "untrusted-model",
                    "messages": [{"role": "user", "content": "hello"}],
                },
            )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(self.upstream.complete_payloads, [])
        self.assertEqual(self.upstream.stream_payloads, [])

    def test_non_finite_generation_parameters_are_rejected(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer gateway-token-0123456789abcdef"},
                content=(
                    '{"model":"case9-rag","messages":[{"role":"user",'
                    '"content":"hello"}],"temperature":NaN}'
                ),
            )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["error"]["code"], "invalid_request_error")

    def test_request_body_limit_rejects_an_oversized_request(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer gateway-token-0123456789abcdef"},
                json={
                    "model": "case9-rag",
                    "messages": [{"role": "user", "content": "x" * 300_000}],
                },
            )

        self.assertEqual(response.status_code, 413)
        self.assertEqual(response.json()["error"]["code"], "request_too_large")
        self.assertEqual(self.upstream.complete_payloads, [])

    def test_rate_limit_rejects_a_second_request_from_the_same_peer(self) -> None:
        app = create_app(
            replace(make_settings(self.knowledge_dir), rate_limit_requests=1),
            FakeUpstream(),
            LocalRetriever(self.knowledge_dir),
        )
        with TestClient(app) as client:
            first = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer gateway-token-0123456789abcdef"},
                json={
                    "model": "case9-rag",
                    "messages": [{"role": "user", "content": "first"}],
                },
            )
            second = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer gateway-token-0123456789abcdef"},
                json={
                    "model": "case9-rag",
                    "messages": [{"role": "user", "content": "second"}],
                },
            )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 429)
        self.assertEqual(second.json()["error"]["code"], "rate_limit_exceeded")

    def test_stream_byte_limit_sends_a_terminal_error_and_releases_capacity(self) -> None:
        app = create_app(
            replace(make_settings(self.knowledge_dir), stream_max_bytes=8),
            FakeUpstream(),
            LocalRetriever(self.knowledge_dir),
        )
        with TestClient(app) as client:
            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer gateway-token-0123456789abcdef"},
                json={
                    "model": "case9-rag",
                    "messages": [{"role": "user", "content": "stream"}],
                    "stream": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'"code":"stream_limit"', response.content)
        self.assertNotIn(b"data: [DONE]", response.content)

    def test_gateway_adds_done_when_a_stream_ends_without_it(self) -> None:
        class IncompleteStreamUpstream(FakeUpstream):
            async def stream(self, payload: dict[str, Any], request_id: str) -> FakeStream:
                self.stream_payloads.append(payload)
                self.stream_handle = FakeStreamWithoutDone(
                    [b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n']
                )
                return self.stream_handle

        upstream = IncompleteStreamUpstream()
        app = create_app(make_settings(self.knowledge_dir), upstream, LocalRetriever(self.knowledge_dir))
        with TestClient(app) as client:
            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer gateway-token-0123456789abcdef"},
                json={
                    "model": "case9-rag",
                    "messages": [{"role": "user", "content": "stream"}],
                    "stream": True,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'"code":"upstream_incomplete"', response.content)
        self.assertNotIn(b"data: [DONE]", response.content)
        self.assertTrue(upstream.stream_handle.closed)

    def test_stream_stops_forwarding_after_done(self) -> None:
        class TrailingDataUpstream(FakeUpstream):
            async def stream(self, payload: dict[str, Any], request_id: str) -> FakeStream:
                self.stream_payloads.append(payload)
                self.stream_handle = FakeStream(
                    [b"data: [DONE]\n\ndata: {\"unexpected\":true}\n\n"]
                )
                return self.stream_handle

        upstream = TrailingDataUpstream()
        app = create_app(make_settings(self.knowledge_dir), upstream, LocalRetriever(self.knowledge_dir))
        with TestClient(app) as client:
            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer gateway-token-0123456789abcdef"},
                json={
                    "model": "case9-rag",
                    "messages": [{"role": "user", "content": "stream"}],
                    "stream": True,
                },
            )

        self.assertIn(b"data: [DONE]", response.content)
        self.assertNotIn(b"unexpected", response.content)

    def test_stream_accepts_done_before_oversized_same_chunk_tail(self) -> None:
        class OversizedTrailingDataUpstream(FakeUpstream):
            async def stream(self, payload: dict[str, Any], request_id: str) -> FakeStream:
                self.stream_payloads.append(payload)
                self.stream_handle = FakeStream(
                    [b"data: [DONE]\n\n" + (b"ignored-tail" * 10_000)]
                )
                return self.stream_handle

        upstream = OversizedTrailingDataUpstream()
        app = create_app(
            replace(make_settings(self.knowledge_dir), stream_max_bytes=32),
            upstream,
            LocalRetriever(self.knowledge_dir),
        )
        with TestClient(app) as client:
            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer gateway-token-0123456789abcdef"},
                json={
                    "model": "case9-rag",
                    "messages": [{"role": "user", "content": "stream"}],
                    "stream": True,
                },
            )

        self.assertIn(b"data: [DONE]", response.content)
        self.assertNotIn(b"stream_limit", response.content)
        self.assertNotIn(b"ignored-tail", response.content)

    def test_unknown_v1_write_is_authenticated_before_body_read(self) -> None:
        with TestClient(self.app) as client:
            response = client.post(
                "/v1/not-a-route",
                content=b"{\"oversized\": false}",
            )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["error"]["code"], "invalid_api_key")

    def test_text_containing_done_is_not_a_terminal_sse_event(self) -> None:
        class ContentDoneUpstream(FakeUpstream):
            async def stream(self, payload: dict[str, Any], request_id: str) -> FakeStream:
                self.stream_payloads.append(payload)
                self.stream_handle = FakeStream(
                    [b'data: {"choices":[{"delta":{"content":"[DONE]"}}]}\n\n']
                )
                return self.stream_handle

        upstream = ContentDoneUpstream()
        app = create_app(make_settings(self.knowledge_dir), upstream, LocalRetriever(self.knowledge_dir))
        with TestClient(app) as client:
            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer gateway-token-0123456789abcdef"},
                json={
                    "model": "case9-rag",
                    "messages": [{"role": "user", "content": "stream"}],
                    "stream": True,
                },
            )

        self.assertIn(b'"content":"[DONE]"', response.content)
        self.assertIn(b'"code":"upstream_incomplete"', response.content)
        self.assertNotIn(b"data: [DONE]\n\n", response.content)


class RequestBodyLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_chunked_body_is_rejected_before_the_inner_app_runs(self) -> None:
        inner_called = False
        sent: list[dict[str, Any]] = []
        messages = iter(
            [
                {"type": "http.request", "body": b"12345", "more_body": True},
                {"type": "http.request", "body": b"6789", "more_body": False},
            ]
        )

        async def inner_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            nonlocal inner_called
            inner_called = True

        async def receive() -> dict[str, Any]:
            return next(messages)

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        middleware = RequestBodyLimitMiddleware(
            inner_app, max_body_bytes=8, body_timeout_seconds=1.0
        )
        await middleware(
            {"type": "http", "method": "POST", "headers": []}, receive, send
        )

        self.assertFalse(inner_called)
        self.assertEqual(sent[0]["status"], 413)

    async def test_stalled_body_is_rejected_before_the_inner_app_runs(self) -> None:
        inner_called = False
        sent: list[dict[str, Any]] = []

        async def inner_app(scope: dict[str, Any], receive: Any, send: Any) -> None:
            nonlocal inner_called
            inner_called = True

        async def receive() -> dict[str, Any]:
            await asyncio.sleep(0.05)
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        middleware = RequestBodyLimitMiddleware(
            inner_app, max_body_bytes=8, body_timeout_seconds=0.01
        )
        await middleware(
            {"type": "http", "method": "POST", "headers": []}, receive, send
        )

        self.assertFalse(inner_called)
        self.assertEqual(sent[0]["status"], 408)


class GatewayStreamingResponseTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def _scope() -> dict[str, Any]:
        return {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.0"},
            "method": "GET",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [],
            "client": ("test", 1),
            "server": ("test", 80),
            "scheme": "http",
        }

    async def test_slow_downstream_write_closes_upstream_and_releases_capacity(self) -> None:
        stream = FakeStream([b'data: {"choices": [{"delta": {"content": "ok"}}]}\n\n'])
        capacity = RequestCapacity(1)
        self.assertTrue(await capacity.try_acquire())
        lease = _StreamLease(stream, capacity)
        response = _GatewayTimedStreamingResponse(
            _stream_bytes(stream, capacity, 10.0, 16_384, lease=lease),
            media_type="text/event-stream",
            write_timeout_seconds=0.01,
            on_close=lease.close,
        )

        async def receive() -> dict[str, Any]:
            await asyncio.sleep(1.0)
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.body" and message.get("body"):
                await asyncio.sleep(0.2)

        try:
            await response(self._scope(), receive, send)
        except BaseException:
            pass

        self.assertTrue(stream.closed)
        self.assertTrue(await capacity.try_acquire())
        await capacity.release()

    async def test_timeout_before_body_iteration_still_releases_capacity(self) -> None:
        stream = FakeStream([b"data: [DONE]\n\n"])
        capacity = RequestCapacity(1)
        self.assertTrue(await capacity.try_acquire())
        lease = _StreamLease(stream, capacity)
        response = _GatewayTimedStreamingResponse(
            _stream_bytes(stream, capacity, 10.0, 16_384, lease=lease),
            media_type="text/event-stream",
            write_timeout_seconds=0.01,
            on_close=lease.close,
        )

        async def receive() -> dict[str, Any]:
            await asyncio.sleep(1.0)
            return {"type": "http.disconnect"}

        async def send(_: dict[str, Any]) -> None:
            await asyncio.sleep(0.2)

        try:
            await response(self._scope(), receive, send)
        except BaseException:
            pass

        self.assertTrue(stream.closed)
        self.assertTrue(await capacity.try_acquire())
        await capacity.release()

    async def test_client_disconnect_cancels_stream_and_releases_capacity(self) -> None:
        stream = FakeStream([b'data: {"choices": [{"delta": {"content": "ok"}}]}\n\n'])
        capacity = RequestCapacity(1)
        self.assertTrue(await capacity.try_acquire())
        lease = _StreamLease(stream, capacity)
        response = _GatewayTimedStreamingResponse(
            _stream_bytes(stream, capacity, 10.0, 16_384, lease=lease),
            media_type="text/event-stream",
            write_timeout_seconds=1.0,
            on_close=lease.close,
        )
        receives = 0

        async def receive() -> dict[str, Any]:
            nonlocal receives
            receives += 1
            if receives == 1:
                return {"type": "http.request", "body": b"", "more_body": False}
            return {"type": "http.disconnect"}

        async def send(_: dict[str, Any]) -> None:
            await asyncio.sleep(1.0)

        try:
            await response(self._scope(), receive, send)
        except BaseException:
            pass

        self.assertTrue(stream.closed)
        self.assertTrue(await capacity.try_acquire())
        await capacity.release()


class UpstreamContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_rejects_a_non_sse_upstream_response(self) -> None:
        settings = make_settings(Path(tempfile.mkdtemp()))
        upstream = OpenAICompatibleUpstream(settings)

        async def send(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                json={"choices": []},
                request=request,
            )

        await upstream._client.aclose()
        upstream._client = httpx.AsyncClient(transport=httpx.MockTransport(send))
        try:
            with self.assertRaisesRegex(UpstreamError, "Server-Sent Event"):
                await upstream.stream({"model": "test"}, "request-id")
        finally:
            await upstream.aclose()

    async def test_stream_rejects_a_no_content_success_response(self) -> None:
        settings = make_settings(Path(tempfile.mkdtemp()))
        upstream = OpenAICompatibleUpstream(settings)

        async def send(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                204,
                headers={"content-type": "text/event-stream"},
                request=request,
            )

        await upstream._client.aclose()
        upstream._client = httpx.AsyncClient(transport=httpx.MockTransport(send))
        try:
            with self.assertRaisesRegex(UpstreamError, "HTTP 204"):
                await upstream.stream({"model": "test"}, "request-id")
        finally:
            await upstream.aclose()

    async def test_completion_rejects_a_redirect_or_invalid_shape(self) -> None:
        settings = make_settings(Path(tempfile.mkdtemp()))
        upstream = OpenAICompatibleUpstream(settings)

        async def send(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                302,
                headers={"content-type": "application/json"},
                json={"choices": []},
                request=request,
            )

        await upstream._client.aclose()
        upstream._client = httpx.AsyncClient(transport=httpx.MockTransport(send))
        try:
            with self.assertRaisesRegex(UpstreamError, "HTTP 302"):
                await upstream.complete({"model": "test"}, "request-id")
        finally:
            await upstream.aclose()

    async def test_completion_rejects_an_oversized_upstream_body(self) -> None:
        settings = make_settings(Path(tempfile.mkdtemp()))
        upstream = OpenAICompatibleUpstream(settings)

        async def send(request: httpx.Request) -> httpx.Response:
            body = b"{" + b"x" * 20_000 + b"}"
            return httpx.Response(
                200,
                headers={
                    "content-type": "application/json",
                    "content-length": str(len(body)),
                },
                content=body,
                request=request,
            )

        await upstream._client.aclose()
        upstream._client = httpx.AsyncClient(transport=httpx.MockTransport(send))
        try:
            with self.assertRaisesRegex(UpstreamError, "size limit"):
                await upstream.complete({"model": "test"}, "request-id")
        finally:
            await upstream.aclose()
