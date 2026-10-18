from __future__ import annotations

from collections.abc import AsyncIterator
import unittest

from fastapi.testclient import TestClient

from ascend_edge_chat.ui.web import SECURITY_WARNING, TextChatSettings, create_text_chat_app


class _FakeLLM:
    last_finish_reason = "stop"

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        yield "你好"
        yield "，这是测试回复。"

    async def aclose(self) -> None:
        return None


class ReleaseUiTests(unittest.TestCase):
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
        self.assertIn(SECURITY_WARNING, health.json()["warning"])
        self.assertIn("profiles", config.json())
        self.assertEqual(config.json()["active_profile"], "qwen1.5-0.5b-chat")
        self.assertTrue(any(item["id"] == "tinyllama-1.1b-mindspore" for item in config.json()["profiles"]))
        self.assertEqual(chat.status_code, 200)
        self.assertEqual(chat.json()["model"], "ascend-edge-chat")

    def test_ui_explains_that_switching_requires_worker_restart(self) -> None:
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
