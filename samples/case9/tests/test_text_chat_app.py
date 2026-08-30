from __future__ import annotations

from collections.abc import AsyncIterator
import asyncio
import os
from pathlib import Path
import tempfile
import unittest
from typing import List, Optional
from unittest.mock import patch

from fastapi.testclient import TestClient

from text_chat_app import (
    SECURITY_WARNING,
    _read_profile_generation,
    TextChatConfigurationError,
    TextChatRequestError,
    TextChatSettings,
    _TimedStreamingResponse,
    _parse_chat_request,
    create_text_chat_app,
)
from case9_model_profiles import DEFAULT_REGISTRY_PATH, load_profiles, write_active_state


class FakeTextLLM:
    def __init__(
        self, chunks: Optional[List[str]] = None, error: Optional[Exception] = None
    ):
        self.chunks = chunks if chunks is not None else ["你好", "，这是测试回复。"]
        self.error = error
        self.requests: List[List[dict[str, str]]] = []
        self.closed = False
        self.last_finish_reason = None

    async def stream(self, messages: List[dict[str, str]]) -> AsyncIterator[str]:
        self.requests.append(messages)
        if self.error is not None:
            raise self.error
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True


def make_app(llm: Optional[FakeTextLLM] = None):
    return create_text_chat_app(
        TextChatSettings(
            gateway_api_key="server-only-test-token-123456",
            max_messages=4,
            max_characters=120,
            body_timeout_seconds=0.2,
        ),
        llm=llm or FakeTextLLM(),
    )


class TextChatAppTests(unittest.TestCase):
    def test_root_health_and_config_never_expose_gateway_key(self) -> None:
        app = make_app()
        with TestClient(app) as client:
            page = client.get("/")
            health = client.get("/health")
            config = client.get("/api/config")

        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers.get("cache-control"), "no-store")
        self.assertIn("Case9 文字聊天测试", page.text)
        self.assertIn("响应在完成前中断", page.text)
        self.assertIn("authoritative cumulative text", page.text)
        self.assertIn("buffer = buffer.replace(/\\r\\n/g, '\\n')", page.text)
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["mode"], "unauthenticated-lan-experiment")
        self.assertIn(SECURITY_WARNING, health.json()["warning"])
        self.assertEqual(config.json()["model"], "case9-rag")
        self.assertEqual(config.headers.get("cache-control"), "no-store")
        self.assertNotIn("server-only-test-token", page.text + health.text + config.text)

    def test_streaming_chat_updates_history_and_clear(self) -> None:
        llm = FakeTextLLM()
        app = make_app(llm)
        with TestClient(app) as client:
            response = client.post(
                "/api/chat",
                json={"message": "请回复一句话", "stream": True},
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn('"type":"delta"', response.text)
            self.assertIn("这是测试回复", response.text)
            self.assertIn('"finish_reason":"stop"', response.text)

            history = client.get("/api/history")
            self.assertEqual(
                history.json()["messages"],
                [
                    {"role": "user", "content": "请回复一句话"},
                    {"role": "assistant", "content": "你好，这是测试回复。"},
                ],
            )
            cleared = client.post("/api/clear")
            self.assertEqual(cleared.status_code, 200)
            self.assertEqual(client.get("/api/history").json()["messages"], [])

        self.assertEqual(llm.requests[0], [{"role": "user", "content": "请回复一句话"}])
        self.assertTrue(llm.closed)

    def test_length_finish_reason_is_reported_and_not_committed(self) -> None:
        class TruncatedLLM(FakeTextLLM):
            async def stream(self, messages: List[dict[str, str]]) -> AsyncIterator[str]:
                self.requests.append(messages)
                yield "半句"
                self.last_finish_reason = "length"

        llm = TruncatedLLM()
        with TestClient(make_app(llm)) as client:
            response = client.post(
                "/api/chat", json={"message": "截断测试", "stream": True}
            )
            history = client.get("/api/history")

        self.assertEqual(response.status_code, 200)
        self.assertIn("达到 max_tokens 上限", response.text)
        self.assertNotIn('"type":"done"', response.text)
        self.assertEqual(history.json()["messages"], [])

    def test_streaming_response_is_not_cacheable(self) -> None:
        with TestClient(make_app()) as client:
            response = client.post(
                "/api/chat", json={"message": "缓存测试", "stream": True}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_non_streaming_mode_returns_json(self) -> None:
        with TestClient(make_app()) as client:
            response = client.post(
                "/api/chat", json={"message": "测试 JSON", "stream": False}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "你好，这是测试回复。")

    def test_invalid_request_is_rejected_before_llm_call(self) -> None:
        llm = FakeTextLLM()
        with TestClient(make_app(llm)) as client:
            empty = client.post("/api/chat", json={"message": "  "})
            unknown = client.post("/api/chat", json={"message": "ok", "model": "x"})
            too_long = client.post("/api/chat", json={"message": "x" * 121})

        self.assertEqual(empty.status_code, 400)
        self.assertEqual(unknown.status_code, 400)
        self.assertEqual(too_long.status_code, 413)
        self.assertEqual(llm.requests, [])

    def test_upstream_failure_is_returned_as_sse_error(self) -> None:
        llm = FakeTextLLM(error=RuntimeError("synthetic failure"))
        with TestClient(make_app(llm)) as client:
            response = client.post("/api/chat", json={"message": "失败测试"})
            history = client.get("/api/history")
        self.assertEqual(response.status_code, 200)
        self.assertIn('"type":"error"', response.text)
        self.assertIn("文字聊天请求失败", response.text)
        self.assertEqual(history.json()["messages"], [])

    def test_oversized_stream_is_rejected_before_commit(self) -> None:
        llm = FakeTextLLM(chunks=["x" * 121])
        with TestClient(make_app(llm)) as client:
            response = client.post("/api/chat", json={"message": "长度测试"})
            history = client.get("/api/history")
        self.assertEqual(response.status_code, 200)
        self.assertIn('"type":"error"', response.text)
        self.assertEqual(history.json()["messages"], [])

    def test_long_user_turn_reserves_space_for_assistant(self) -> None:
        llm = FakeTextLLM(chunks=["x" * 11])
        with TestClient(make_app(llm)) as client:
            response = client.post(
                "/api/chat", json={"message": "u" * 110, "stream": True}
            )
            history = client.get("/api/history")
        self.assertEqual(response.status_code, 200)
        self.assertIn("会话字符限制", response.text)
        self.assertEqual(history.json()["messages"], [])
        # Ten characters remain for the assistant, so the upstream call is
        # valid; the oversized streamed reply must still roll back the turn.
        self.assertEqual(len(llm.requests), 1)

    def test_generation_deadline_rolls_back_the_turn(self) -> None:
        class SlowLLM(FakeTextLLM):
            async def stream(self, messages: List[dict[str, str]]) -> AsyncIterator[str]:
                self.requests.append(messages)
                await asyncio.sleep(0.2)
                yield "不会到达"

        settings = TextChatSettings(
            gateway_api_key="server-only-test-token-123456",
            max_messages=4,
            max_characters=120,
            llm_timeout_seconds=0.1,
            body_timeout_seconds=0.2,
        )
        with TestClient(create_text_chat_app(settings, llm=SlowLLM())) as client:
            response = client.post("/api/chat", json={"message": "超时测试"})
            history = client.get("/api/history")
        self.assertEqual(response.status_code, 200)
        self.assertIn('"type":"error"', response.text)
        self.assertEqual(history.json()["messages"], [])

    def test_large_json_integer_is_reported_as_bad_request(self) -> None:
        with TestClient(make_app(), raise_server_exceptions=False) as client:
            body = '{"message":"ok","extra":' + ("9" * 5000) + "}"
            response = client.post(
                "/api/chat", content=body, headers={"content-type": "application/json"}
            )
        self.assertEqual(response.status_code, 400)

    def test_profile_switch_discards_history_for_the_existing_browser_cookie(self) -> None:
        """A new active worker must receive no context from the old worker."""

        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "run" / "active-model.json"
            settings = TextChatSettings(
                gateway_api_key="server-only-test-token-123456",
                max_messages=4,
                max_characters=120,
                active_profile_state_path=state_path,
            )
            llm = FakeTextLLM()
            registry = load_profiles(DEFAULT_REGISTRY_PATH)
            with TestClient(create_text_chat_app(settings, llm=llm)) as client:
                response = client.post(
                    "/api/chat", json={"message": "旧模型上下文", "stream": False}
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(len(client.get("/api/history").json()["messages"]), 2)

                write_active_state(
                    state_path,
                    "tinyllama-1.1b-mindspore",
                    status="running",
                    worker_pid=101,
                    cache_cleared=True,
                    registry=registry,
                )
                history = client.get("/api/history")
                self.assertEqual(history.status_code, 200)
                self.assertEqual(history.json()["messages"], [])

                # The existing cookie is intentionally reused, but it now
                # resolves to a fresh conversation in the new namespace.
                response = client.post(
                    "/api/chat", json={"message": "新模型问题", "stream": False}
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(
                    llm.requests[-1], [{"role": "user", "content": "新模型问题"}]
                )

    def test_profile_change_during_generation_is_not_committed(self) -> None:
        """An in-flight old-worker response cannot repopulate new history."""

        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "active-model.json"
            registry = load_profiles(DEFAULT_REGISTRY_PATH)

            class SwitchingLLM(FakeTextLLM):
                async def stream(
                    self, messages: List[dict[str, str]]
                ) -> AsyncIterator[str]:
                    self.requests.append(messages)
                    write_active_state(
                        state_path,
                        "tinyllama-1.1b-mindspore",
                        status="running",
                        worker_pid=202,
                        cache_cleared=True,
                        registry=registry,
                    )
                    yield "旧模型回复"

            settings = TextChatSettings(
                gateway_api_key="server-only-test-token-123456",
                max_messages=4,
                max_characters=120,
                active_profile_state_path=state_path,
            )
            with TestClient(create_text_chat_app(settings, llm=SwitchingLLM())) as client:
                response = client.post(
                    "/api/chat", json={"message": "切换中", "stream": False}
                )
                self.assertEqual(response.status_code, 502)
                self.assertIn("活动模型已切换", response.json()["error"]["message"])
                self.assertEqual(client.get("/api/history").json()["messages"], [])


class TextChatSettingsTests(unittest.TestCase):
    def test_profile_generation_includes_worker_state_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "active-model.json"
            registry = load_profiles(DEFAULT_REGISTRY_PATH)
            settings = TextChatSettings(
                gateway_api_key="server-only-test-token-123456",
                active_profile_state_path=state_path,
            )
            first = _read_profile_generation(settings)
            self.assertEqual(first, ("none",))
            write_active_state(
                state_path,
                "qwen1.5-0.5b-mindspore",
                status="running",
                worker_pid=301,
                cache_cleared=True,
                registry=registry,
            )
            second = _read_profile_generation(settings)
            self.assertEqual(second[0:4], ("state", "qwen1.5-0.5b-mindspore", "running", 301))
            write_active_state(
                state_path,
                "qwen1.5-0.5b-mindspore",
                status="running",
                worker_pid=302,
                cache_cleared=True,
                registry=registry,
            )
            third = _read_profile_generation(settings)
            self.assertNotEqual(second, third)

    def test_default_timeout_covers_serialized_qwen_generation(self) -> None:
        settings = TextChatSettings(gateway_api_key="server-only-test-token-123456")
        self.assertEqual(settings.llm_timeout_seconds, 300.0)

    def test_default_prompt_budget_stays_below_tinyllama_gateway_limit(self) -> None:
        settings = TextChatSettings(gateway_api_key="server-only-test-token-123456")
        self.assertEqual(settings.max_characters, 700)
        with self.assertRaisesRegex(TextChatConfigurationError, "768"):
            TextChatSettings(
                gateway_api_key="server-only-test-token-123456",
                max_characters=769,
            )

    def test_requires_server_side_key_and_loopback_gateway(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(TextChatConfigurationError, "GATEWAY_API_KEY"):
                TextChatSettings.from_environ()
        with patch.dict(
            os.environ,
            {
                "TEXT_CHAT_GATEWAY_API_KEY": "test-token",
                "TEXT_CHAT_GATEWAY_URL": "https://example.test/v1",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(TextChatConfigurationError, "loopback"):
                TextChatSettings.from_environ()
        with patch.dict(
            os.environ,
            {
                "TEXT_CHAT_GATEWAY_API_KEY": "short-token",
                "TEXT_CHAT_GATEWAY_URL": "http://127.0.0.1:7861/v1",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(TextChatConfigurationError, "24 characters"):
                TextChatSettings.from_environ()
        with patch.dict(
            os.environ,
            {
                "TEXT_CHAT_GATEWAY_API_KEY": "valid-token-0123456789abcdef",
                "TEXT_CHAT_GATEWAY_URL": "http://127.0.0.1:7861",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(TextChatConfigurationError, "gateway_url"):
                TextChatSettings.from_environ()
        with patch.dict(
            os.environ,
            {
                "TEXT_CHAT_GATEWAY_API_KEY": "valid-token-0123456789abcdef",
                "TEXT_CHAT_GATEWAY_URL": "http://127.0.0.1:not-a-port/v1",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(TextChatConfigurationError, "port"):
                TextChatSettings.from_environ()


class TextChatBodyLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_chunked_body_is_bounded_before_joining(self) -> None:
        class ChunkedRequest:
            headers = {}

            async def stream(self) -> AsyncIterator[bytes]:
                yield b"{\"message\":\""
                yield b"x" * 129

        with self.assertRaises(TextChatRequestError) as context:
            await _parse_chat_request(ChunkedRequest(), 128, 120, 1.0)  # type: ignore[arg-type]
        self.assertEqual(context.exception.status_code, 413)

    async def test_chunked_body_read_has_a_deadline(self) -> None:
        class SlowRequest:
            headers = {}

            async def stream(self) -> AsyncIterator[bytes]:
                await asyncio.sleep(0.2)
                yield b'{"message":"ok"}'

        with self.assertRaisesRegex(TextChatRequestError, "超时"):
            await _parse_chat_request(SlowRequest(), 128, 120, 0.01)  # type: ignore[arg-type]


class TimedResponseTests(unittest.IsolatedAsyncioTestCase):
    async def test_write_timeout_closes_body_and_calls_cleanup(self) -> None:
        closed = []
        released = []

        class Body:
            def __aiter__(self) -> "Body":
                return self

            async def __anext__(self) -> str:
                return "payload"

            async def aclose(self) -> None:
                closed.append(True)

        async def receive() -> dict[str, str]:
            await asyncio.sleep(1)
            return {"type": "http.disconnect"}

        async def send(_: dict[str, object]) -> None:
            await asyncio.sleep(0.2)

        response = _TimedStreamingResponse(
            Body(),
            media_type="text/event-stream",
            write_timeout_seconds=0.1,
            on_close=lambda: released.append(True),
        )
        try:
            await response(
                {
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
                },
                receive,
                send,
            )
        except BaseException:
            pass
        self.assertTrue(closed)
        self.assertEqual(released, [True])

    async def test_cleanup_callback_runs_when_iterator_close_is_cancelled(self) -> None:
        released = []

        class Body:
            def __aiter__(self) -> "Body":
                return self

            async def __anext__(self) -> str:
                return "payload"

            async def aclose(self) -> None:
                raise asyncio.CancelledError()

        async def receive() -> dict[str, str]:
            await asyncio.sleep(1)
            return {"type": "http.disconnect"}

        async def send(_: dict[str, object]) -> None:
            await asyncio.sleep(0.2)

        response = _TimedStreamingResponse(
            Body(),
            media_type="text/event-stream",
            write_timeout_seconds=0.1,
            on_close=lambda: released.append(True),
        )
        try:
            await response(
                {
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
                },
                receive,
                send,
            )
        except BaseException:
            pass
        self.assertEqual(released, [True])
