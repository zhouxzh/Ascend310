from __future__ import annotations

from collections.abc import AsyncIterator
import unittest

from fastapi.testclient import TestClient

from ascend_edge_chat.api.gateway import create_app
from ascend_edge_chat.runtime.config import Settings


class _Stream:
    async def aclose(self) -> None:
        return None

    async def _iterate(self) -> AsyncIterator[bytes]:
        yield b'data: {"model":"ascend-edge-chat-worker","choices":[{"delta":{"content":"ok"}}]}\n\n'
        yield b"data: [DONE]\n\n"

    def iter_bytes(self) -> AsyncIterator[bytes]:
        return self._iterate()


class _Upstream:
    async def complete(self, payload, request_id):
        return {
            "id": request_id,
            "object": "chat.completion",
            "model": payload["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}],
        }

    async def stream(self, payload, request_id):
        return _Stream()

    async def aclose(self) -> None:
        return None


def _settings() -> Settings:
    return Settings(
        gateway_api_key="gateway-token-0123456789",
        public_model_id="ascend-edge-chat",
        upstream_base_url="http://127.0.0.1:8090/v1",
        upstream_api_key="",
        upstream_model="ascend-edge-chat-worker",
        upstream_timeout_seconds=30.0,
        request_max_bytes=262144,
        request_body_timeout_seconds=15.0,
        request_max_messages=32,
        request_max_characters=24000,
        max_concurrent_requests=1,
        rate_limit_requests=60,
        rate_limit_window_seconds=60.0,
        stream_max_seconds=30.0,
        stream_max_bytes=65536,
        log_level="INFO",
    )


class ReleaseGatewayTests(unittest.TestCase):
    def test_models_and_completion_require_release_model(self) -> None:
        settings = _settings()
        app = create_app(settings, upstream=_Upstream())
        headers = {"Authorization": "Bearer " + settings.gateway_api_key}
        with TestClient(app) as client:
            models = client.get("/v1/models", headers=headers)
            completion = client.post(
                "/v1/chat/completions",
                headers=headers,
                json={"model": "ascend-edge-chat", "messages": [{"role": "user", "content": "你好"}]},
            )
        self.assertEqual(models.status_code, 200)
        self.assertEqual(models.json()["data"][0]["id"], "ascend-edge-chat")
        self.assertEqual(completion.status_code, 200)
        self.assertEqual(completion.json()["model"], "ascend-edge-chat")

    def test_gateway_rejects_missing_key(self) -> None:
        with TestClient(create_app(_settings(), upstream=_Upstream())) as client:
            response = client.get("/v1/models")
        self.assertEqual(response.status_code, 401)

    def test_stream_rewrites_internal_model_id(self) -> None:
        settings = _settings()
        with TestClient(create_app(settings, upstream=_Upstream())) as client:
            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer " + settings.gateway_api_key},
                json={"model": "ascend-edge-chat", "stream": True, "messages": [{"role": "user", "content": "你好"}]},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn('"model":"ascend-edge-chat"', response.text)
        self.assertNotIn("ascend-edge-chat-worker", response.text)
