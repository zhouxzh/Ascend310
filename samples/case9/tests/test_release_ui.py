from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import unittest
from unittest import mock

from fastapi.testclient import TestClient

import ascend_edge_chat.ui.web as web
from ascend_edge_chat.ui.web import SECURITY_WARNING, TextChatSettings, create_text_chat_app


class _FakeLLM:
    last_finish_reason = "stop"

    def __init__(self) -> None:
        self.last_max_tokens = None

    async def stream(
        self, messages: list[dict[str, str]], *, max_tokens=None
    ) -> AsyncIterator[str]:
        self.last_max_tokens = max_tokens
        yield "你好"
        yield "，这是测试回复。"

    async def aclose(self) -> None:
        return None


class _LengthLimitedLLM(_FakeLLM):
    last_finish_reason = "length"


class ReleaseUiTests(unittest.TestCase):
    def test_model_switch_waits_for_streaming_cleanup_lock(self) -> None:
        async def exercise() -> None:
            lock = asyncio.Lock()
            await lock.acquire()

            async def release_shortly() -> None:
                await asyncio.sleep(0.01)
                lock.release()

            release_task = asyncio.create_task(release_shortly())
            acquired = await web._acquire_request_slot(lock, 0.2)
            await release_task
            self.assertTrue(acquired)
            lock.release()

        asyncio.run(exercise())

    def test_zombie_worker_pid_is_not_treated_as_running(self) -> None:
        with mock.patch.object(
            web.Path,
            "read_text",
            return_value="Name:\tpython\nState:\tZ (zombie)\n",
        ), mock.patch.object(web.os, "kill") as process_kill:
            self.assertFalse(web._pid_alive(12345))
        process_kill.assert_not_called()

    def test_ui_exposes_warning_but_not_gateway_key(self) -> None:
        settings = TextChatSettings(gateway_api_key="server-only-token-0123456789")
        with TestClient(create_text_chat_app(settings, llm=_FakeLLM())) as client:
            page = client.get("/")
            health = client.get("/health")
            config = client.get("/api/config")
            chat = client.post("/api/chat", json={"message": "请介绍自己", "stream": False})
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(settings.gateway_api_key, page.text)
        self.assertIn("测试模型", page.text)
        self.assertIn('<select id="max-tokens"', page.text)
        self.assertIn("最大输出 token", page.text)
        self.assertIn("TOKEN_BUDGETS", page.text)
        self.assertIn('font-size: 18px', page.text)
        self.assertIn('min-height: 44px', page.text)
        self.assertIn('@media (max-width: 760px)', page.text)
        self.assertIn('/api/select-model', page.text)
        self.assertIn(SECURITY_WARNING, health.json()["warning"])
        self.assertIn("profiles", config.json())
        self.assertEqual(config.json()["active_profile"], "qwen1.5-0.5b-chat")
        self.assertTrue(any(item["id"] == "tinyllama-1.1b-mindspore" for item in config.json()["profiles"]))
        self.assertEqual(chat.status_code, 200)
        self.assertEqual(chat.json()["model"], "ascend-edge-chat")

    def test_ui_forwards_profile_bounded_token_budget(self) -> None:
        settings = TextChatSettings(gateway_api_key="server-only-token-0123456789")
        llm = _FakeLLM()
        with TestClient(create_text_chat_app(settings, llm=llm)) as client:
            accepted = client.post(
                "/api/chat",
                json={"message": "请完整回答", "stream": False, "max_tokens": 128},
            )
            rejected = client.post(
                "/api/chat",
                json={"message": "超出服务上限", "stream": False, "max_tokens": 257},
            )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(llm.last_max_tokens, 128)
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("max_tokens <= 256", rejected.json()["error"]["message"])

    def test_ui_rejects_a_chat_request_that_bypasses_the_model_selector(self) -> None:
        settings = TextChatSettings(gateway_api_key="server-only-token-0123456789")
        with TestClient(create_text_chat_app(settings, llm=_FakeLLM())) as client:
            response = client.post(
                "/api/chat",
                json={
                    "message": "测试另一个模型",
                    "stream": False,
                    "profile": "deepseek-r1-qwen-1.5b-mindspore",
                },
            )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"]["type"], "model_switch_required")

    def test_length_limited_reply_is_returned_but_not_saved_as_history(self) -> None:
        """A normal output cap is not a transport or NPU failure."""

        settings = TextChatSettings(gateway_api_key="server-only-token-0123456789")
        with TestClient(create_text_chat_app(settings, llm=_LengthLimitedLLM())) as client:
            streamed = client.post(
                "/api/chat", json={"message": "请详细回答", "stream": True}
            )
            json_reply = client.post(
                "/api/chat", json={"message": "请详细回答", "stream": False}
            )
            history = client.get("/api/history")

        self.assertEqual(streamed.status_code, 200)
        self.assertIn('"type":"done"', streamed.text)
        self.assertIn('"finish_reason":"length"', streamed.text)
        self.assertIn('"truncated":true', streamed.text)
        self.assertNotIn('"type":"error"', streamed.text)
        self.assertEqual(json_reply.status_code, 200)
        self.assertEqual(json_reply.json()["finish_reason"], "length")
        self.assertTrue(json_reply.json()["truncated"])
        self.assertEqual(history.json()["messages"], [])

    def test_model_selector_accepts_the_active_profile_and_rejects_blocked_entries(self) -> None:
        settings = TextChatSettings(gateway_api_key="server-only-token-0123456789")
        with TestClient(create_text_chat_app(settings, llm=_FakeLLM())) as client:
            unchanged = client.post(
                "/api/select-model", json={"profile": "qwen1.5-0.5b-chat"}
            )
            blocked = client.post(
                "/api/select-model", json={"profile": "qwen3-0.6b-mindspore"}
            )
        self.assertEqual(unchanged.status_code, 200)
        self.assertFalse(unchanged.json()["switched"])
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["error"]["type"], "model_switch_failed")

    def test_config_reports_cache_availability_for_the_teaching_selector(self) -> None:
        settings = TextChatSettings(gateway_api_key="server-only-token-0123456789")
        with TestClient(create_text_chat_app(settings, llm=_FakeLLM())) as client:
            config = client.get("/api/config").json()
        tinyllama = next(item for item in config["profiles"] if item["id"] == "tinyllama-1.1b-mindspore")
        self.assertIn("available", tinyllama)
        self.assertIn("availability_reason", tinyllama)
