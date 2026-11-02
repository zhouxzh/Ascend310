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
    last_usage = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}

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


class _InvalidUsageLLM(_FakeLLM):
    last_usage = {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 99}


_ACL_STATIC_KV_MODEL = "qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om"


def _acl_static_kv_health() -> dict[str, object]:
    return {
        "ready": True,
        "healthy": True,
        "model": _ACL_STATIC_KV_MODEL,
        "backend": "acl_om",
        "sequence_length": 1024,
        "descriptor_validated": True,
    }


class ReleaseUiTests(unittest.TestCase):
    def test_external_acl_worker_uses_real_health_with_an_injected_provider(self) -> None:
        settings = TextChatSettings(
            gateway_api_key="server-only-token-0123456789",
            worker_mode="external",
        )
        llm = _FakeLLM()
        probe = mock.AsyncMock(return_value=_acl_static_kv_health())
        with mock.patch.object(web, "_worker_health", probe):
            with TestClient(create_text_chat_app(settings, llm=llm)) as client:
                page = client.get("/")
                health = client.get("/health")
                config = client.get("/api/config")
                chat = client.post("/api/chat", json={"message": "ACL 测试", "stream": False})
                switch = client.post("/api/select-model", json={"profile": _ACL_STATIC_KV_MODEL})

        self.assertEqual(settings.worker_health_url, "http://127.0.0.1:8084/health")
        self.assertIn("budgets.includes(fallback)", page.text)
        self.assertIn("Math.min(64, maximum)", page.text)
        self.assertIn("budget >= minimum", page.text)
        self.assertEqual(health.json()["status"], "ok")
        self.assertTrue(health.json()["worker"]["healthy"])
        self.assertFalse(config.json()["model_switching"])
        self.assertEqual(config.json()["active_profile"], _ACL_STATIC_KV_MODEL)
        self.assertEqual(config.json()["profiles"][0]["default_max_tokens"], 64)
        self.assertEqual(config.json()["profiles"][0]["max_tokens"], 80)
        self.assertEqual(chat.status_code, 200)
        self.assertEqual(llm.last_max_tokens, 64)
        self.assertEqual(switch.status_code, 409)
        self.assertEqual(switch.json()["error"]["type"], "model_switch_failed")
        self.assertTrue(probe.await_args_list)
        self.assertTrue(
            all(call.args == ("http://127.0.0.1:8084/health",) for call in probe.await_args_list)
        )

    def test_external_acl_worker_does_not_treat_an_injected_provider_as_healthy(self) -> None:
        settings = TextChatSettings(
            gateway_api_key="server-only-token-0123456789",
            worker_mode="external",
        )
        offline = mock.AsyncMock(side_effect=web.ModelSwitchError("ACL worker offline"))
        with mock.patch.object(web, "_worker_health", offline):
            with TestClient(create_text_chat_app(settings, llm=_FakeLLM())) as client:
                health = client.get("/health")
                chat = client.post("/api/chat", json={"message": "ACL 离线", "stream": False})

        self.assertEqual(health.json()["status"], "degraded")
        self.assertFalse(health.json()["worker"]["healthy"])
        self.assertEqual(chat.status_code, 503)
        self.assertEqual(chat.json()["error"]["type"], "worker_unavailable")
        self.assertTrue(offline.await_args_list)

    def test_external_acl_worker_requires_explicit_healthy_true(self) -> None:
        settings = TextChatSettings(
            gateway_api_key="server-only-token-0123456789",
            worker_mode="external",
        )
        unhealthy = dict(_acl_static_kv_health())
        unhealthy["healthy"] = False
        probe = mock.AsyncMock(return_value=unhealthy)
        with mock.patch.object(web, "_worker_health", probe):
            with TestClient(create_text_chat_app(settings, llm=_FakeLLM())) as client:
                health = client.get("/health")
        self.assertEqual(health.json()["status"], "degraded")
        self.assertFalse(health.json()["worker"]["healthy"])

    def test_ui_health_and_chat_reflect_missing_worker(self) -> None:
        settings = TextChatSettings(gateway_api_key="server-only-token-0123456789")
        offline = mock.AsyncMock(side_effect=web.ModelSwitchError("worker offline"))
        with mock.patch.object(web, "_worker_health", offline):
            with TestClient(create_text_chat_app(settings)) as client:
                health = client.get("/health")
                config = client.get("/api/config")
                chat = client.post("/api/chat", json={"message": "测试", "stream": False})
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["status"], "degraded")
        self.assertFalse(health.json()["worker"]["healthy"])
        self.assertFalse(config.json()["worker"]["healthy"])
        self.assertEqual(chat.status_code, 503)
        self.assertEqual(chat.json()["error"]["type"], "worker_unavailable")

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

    def test_ui_keeps_security_warning_server_side_and_hides_the_banner(self) -> None:
        settings = TextChatSettings(gateway_api_key="server-only-token-0123456789")
        with TestClient(create_text_chat_app(settings, llm=_FakeLLM())) as client:
            page = client.get("/")
            health = client.get("/health")
            config = client.get("/api/config")
            chat = client.post("/api/chat", json={"message": "请介绍自己", "stream": False})
        self.assertEqual(page.status_code, 200)
        self.assertNotIn(settings.gateway_api_key, page.text)
        self.assertNotIn("实验页面未启用浏览器鉴权", page.text)
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

    def test_chat_composer_stays_in_normal_flow_after_messages(self) -> None:
        settings = TextChatSettings(gateway_api_key="server-only-token-0123456789")
        with TestClient(create_text_chat_app(settings, llm=_FakeLLM())) as client:
            page = client.get("/")

        self.assertEqual(page.status_code, 200)
        self.assertNotIn("position: sticky", page.text)
        self.assertLess(
            page.text.index('<section id="messages"'),
            page.text.index('<form id="composer"'),
        )

    def test_chat_workspace_keeps_readable_typography(self) -> None:
        settings = TextChatSettings(gateway_api_key="server-only-token-0123456789")
        with TestClient(create_text_chat_app(settings, llm=_FakeLLM())) as client:
            page = client.get("/")

        workspace_css = page.text.split(
            "/* Chat workspace: a fixed application shell with a normal-flow bottom composer. */",
            1,
        )[1].split("</style>", 1)[0]
        self.assertIn("font-size: 18px;", workspace_css)
        self.assertIn("font-size: 1rem; line-height: 1.75;", workspace_css)
        self.assertIn("textarea { width: 100%; min-height: 64px;", workspace_css)
        self.assertIn("button { min-height: 40px;", workspace_css)

    def test_npu_smi_board_parser_keeps_alarm_as_diagnostic(self) -> None:
        output = """| npu-smi 25.2.0 Version: 25.2.0 |
| 0       310B1                 | Alarm           | 0.0          70                1060 / 1060 |
| 0       0                     | NA              | 0            14080 / 23673                  |
"""
        parsed = web._parse_npu_smi_board_info(output)
        self.assertTrue(parsed["available"])
        self.assertEqual(parsed["soc"], "Ascend 310B1")
        self.assertEqual(parsed["compute_tier"], "20T")
        self.assertEqual(parsed["health"], "Alarm")
        self.assertEqual(parsed["temperature_c"], 70)
        self.assertEqual(parsed["memory"]["used_mb"], 14080)
        self.assertEqual(parsed["memory"]["total_mb"], 23673)
        self.assertEqual(parsed["driver_version"], "25.2.0")

        parsed_b4 = web._parse_npu_smi_board_info(output.replace("310B1", "310B4"))
        self.assertEqual(parsed_b4["compute_tier"], "8T")

    def test_board_info_api_is_structured_and_fail_soft(self) -> None:
        settings = TextChatSettings(gateway_api_key="server-only-token-0123456789")
        snapshot = {
            "available": True,
            "soc": "Ascend 310B4",
            "compute_tier": "8T",
            "health": "Alarm",
            "temperature_c": 79,
            "memory": {"used_mb": 13879, "total_mb": 15610, "source": "npu-smi device accounting"},
            "driver_version": "25.2.0",
            "cann_version": "8.0.0",
            "observed_at_unix": 123,
            "refresh_interval_seconds": 5,
        }
        with mock.patch.object(web, "_cached_board_hardware_snapshot", return_value=snapshot):
            with TestClient(create_text_chat_app(settings, llm=_FakeLLM())) as client:
                response = client.get("/api/board-info")
                chat = client.post("/api/chat", json={"message": "板端信息", "stream": False})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["board"], {"soc": "Ascend310B4", "tier": "8T"})
        self.assertEqual(payload["npu"]["health"], "Alarm")
        self.assertEqual(payload["npu"]["memory"]["free_mb"], 1731)
        self.assertEqual(payload["cann"]["version"], "8.0.0")
        self.assertNotIn("raw", response.text.lower())
        self.assertEqual(chat.status_code, 200)

    def test_runtime_controls_live_in_accessible_sidebar_drawer(self) -> None:
        settings = TextChatSettings(gateway_api_key="server-only-token-0123456789")
        with TestClient(create_text_chat_app(settings, llm=_FakeLLM())) as client:
            page = client.get("/")

        sidebar = page.text.split('<aside id="settings-sidebar"', 1)[1].split("</aside>", 1)[0]
        header = page.text.split("<header>", 1)[1].split("</header>", 1)[0]
        for control_id in ("backend-select", "profile-select", "max-tokens"):
            self.assertIn(f'id="{control_id}"', sidebar)
            self.assertNotIn(f'id="{control_id}"', header)
        self.assertIn('id="settings-toggle"', header)
        self.assertIn('id="settings-close"', sidebar)
        self.assertIn('id="sidebar-scrim"', page.text)
        self.assertIn("settingsSidebar.inert", page.text)
        self.assertIn("content.inert = open", page.text)
        self.assertIn("aria-modal", page.text)
        self.assertIn("trapSettingsFocus", page.text)
        self.assertIn(".sidebar button { flex: 0 0 auto; }", page.text)
        self.assertIn("refreshRuntimeSettings", page.text)
        self.assertIn("snapshotRuntimeSelection", page.text)
        self.assertIn("restoreRuntimeSelection", page.text)
        self.assertIn("event.key === 'Escape'", page.text)
        for field_id in (
            "board-soc", "board-tier", "board-memory", "board-temperature",
            "board-health", "board-cann", "board-driver", "board-backend", "board-model",
        ):
            self.assertIn(f'id="{field_id}"', sidebar)
        self.assertIn("/api/board-info", page.text)
        self.assertIn("window.setInterval(() => { void refreshBoardInfo(); }, 5000)", page.text)
        self.assertIn("内存为npu-smi设备级统计", page.text)

    def test_frontend_uses_chinese_brand_and_real_usage_rate_contract(self) -> None:
        settings = TextChatSettings(gateway_api_key="server-only-token-0123456789")
        with TestClient(create_text_chat_app(settings, llm=_FakeLLM())) as client:
            page = client.get("/")
            streamed = client.post(
                "/api/chat", json={"message": "速率测试", "stream": True}
            )

        self.assertIn("<title>昇腾310B智能聊天机器人</title>", page.text)
        self.assertIn("<h1>昇腾310B智能聊天机器人</h1>", page.text)
        self.assertIn('id="generation-rate"', page.text)
        self.assertIn("performance.now()", page.text)
        self.assertIn("生成速率 ", page.text)
        self.assertIn(
            '"usage":{"prompt_tokens":3,"completion_tokens":2,"total_tokens":5}',
            streamed.text,
        )

    def test_openai_client_preserves_valid_terminal_usage(self) -> None:
        client = web.OpenAIChatClient()
        try:
            content = client._decode_event(
                [
                    '{"choices":[{"delta":{},"finish_reason":"stop"}],'
                    '"usage":{"prompt_tokens":7,"completion_tokens":4,"total_tokens":11}}'
                ]
            )
            self.assertIsNone(content)
            self.assertEqual(
                client.last_usage,
                {"prompt_tokens": 7, "completion_tokens": 4, "total_tokens": 11},
            )
        finally:
            asyncio.run(client.aclose())

    def test_invalid_token_usage_is_not_exposed_as_a_rate_input(self) -> None:
        settings = TextChatSettings(gateway_api_key="server-only-token-0123456789")
        with TestClient(create_text_chat_app(settings, llm=_InvalidUsageLLM())) as client:
            streamed = client.post(
                "/api/chat", json={"message": "无效 usage", "stream": True}
            )

        self.assertNotIn('"usage"', streamed.text)

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
