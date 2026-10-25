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


_ACL_STATIC_KV_MODEL = "qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om"


class _AclStaticKvStream(_Stream):
    async def _iterate(self) -> AsyncIterator[bytes]:
        yield (
            b'data: {"model":"qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om",'
            b'"choices":[{"delta":{"content":"ok"}}]}\n\n'
        )
        yield b"data: [DONE]\n\n"


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


def _settings(*, backend: str = "mindspore") -> Settings:
    acl_static_kv = backend == "acl-static-kv"
    return Settings(
        gateway_api_key="gateway-token-0123456789",
        public_model_id="ascend-edge-chat",
        upstream_base_url=(
            "http://127.0.0.1:8084/v1" if acl_static_kv else "http://127.0.0.1:8090/v1"
        ),
        upstream_api_key="",
        upstream_model=(
            _ACL_STATIC_KV_MODEL if acl_static_kv else "ascend-edge-chat-worker"
        ),
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
        backend=backend,
    )


class ReleaseGatewayTests(unittest.TestCase):
    def test_acl_static_kv_maps_the_public_model_and_enforces_80_tokens(self) -> None:
        class RecordingUpstream(_Upstream):
            def __init__(self) -> None:
                self.payloads = []

            async def complete(self, payload, request_id):
                self.payloads.append(payload)
                return await super().complete(payload, request_id)

        settings = _settings(backend="acl-static-kv")
        upstream = RecordingUpstream()
        headers = {"Authorization": "Bearer " + settings.gateway_api_key}
        with TestClient(create_app(settings, upstream=upstream)) as client:
            defaulted = client.post(
                "/v1/chat/completions",
                headers=headers,
                json={"model": "ascend-edge-chat", "messages": [{"role": "user", "content": "你好"}]},
            )
            rejected = client.post(
                "/v1/chat/completions",
                headers=headers,
                json={"model": "ascend-edge-chat", "max_tokens": 81, "messages": [{"role": "user", "content": "你好"}]},
            )

        self.assertEqual(defaulted.status_code, 200)
        self.assertEqual(defaulted.json()["model"], "ascend-edge-chat")
        self.assertEqual(upstream.payloads[-1]["model"], _ACL_STATIC_KV_MODEL)
        self.assertEqual(upstream.payloads[-1]["max_tokens"], 16)
        self.assertEqual(rejected.status_code, 400)
        self.assertIn("max_tokens <= 80", rejected.json()["error"]["message"])

    def test_acl_static_kv_stream_hides_the_internal_model_id(self) -> None:
        class StaticKvUpstream(_Upstream):
            async def stream(self, payload, request_id):
                del payload, request_id
                return _AclStaticKvStream()

        settings = _settings(backend="acl-static-kv")
        with TestClient(create_app(settings, upstream=StaticKvUpstream())) as client:
            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": "Bearer " + settings.gateway_api_key},
                json={
                    "model": "ascend-edge-chat",
                    "stream": True,
                    "messages": [{"role": "user", "content": "你好"}],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn('"model":"ascend-edge-chat"', response.text)
        self.assertNotIn(_ACL_STATIC_KV_MODEL, response.text)

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
