from __future__ import annotations

import builtins
import http.client
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from mindspore_chat_providers import (
    GenerationResult,
    MindSporeChatProvider,
    ProviderRequestError,
    ProviderTimeout,
    ProviderUnavailable,
    TinyLlamaMindSporeProvider,
    create_provider,
    environment_fingerprint,
    provider_class_for_profile,
)
from mindspore_chat_service import (
    DEFAULT_GENERATION_TOKENS,
    MODEL_ID,
    CompletionRequest,
    MindSporeChatService,
    RequestError,
    _parse_request,
    _main,
    _prefix_delta,
)


class FakeProvider:
    """Protocol-only provider; no optional ML package is needed in tests."""

    profile = SimpleNamespace(id="qwen1.5-0.5b-mindspore", status="experimental_dirty_base")
    profile_id = profile.id
    model_id = MODEL_ID
    context_length = 16
    ready = True
    healthy = True
    provider_name = "fake"

    def __init__(self) -> None:
        self.calls = []
        self.cancelled = 0
        self.closed = False
        self.last_finish_reason = "stop"

    def load(self) -> None:
        self.ready = True

    def status(self):
        return {
            "ready": self.ready,
            "healthy": self.healthy,
            "profile": self.profile_id,
            "last_finish_reason": self.last_finish_reason,
        }

    def count_tokens(self, messages):
        return sum(len(item["content"]) for item in messages)

    def complete(self, messages, max_tokens):
        self.calls.append((messages, max_tokens))
        return GenerationResult("你好世界", self.count_tokens(messages), 4, "stop")

    def stream(self, messages, max_tokens):
        self.calls.append((messages, max_tokens))
        yield ("你", 1)
        yield ("你好", 2)
        # This simulates a tokenizer boundary revision.  The HTTP service must
        # not resend the complete accumulated response.
        yield ("你好世界", 4, "stop")

    def cancel(self):
        self.cancelled += 1

    def close(self):
        self.closed = True
        self.ready = False


class SlowProvider(FakeProvider):
    def complete(self, messages, max_tokens):
        time.sleep(0.12)
        return super().complete(messages, max_tokens)


class ErrorProvider(FakeProvider):
    def __init__(self, error):
        super().__init__()
        self.error = error

    def complete(self, messages, max_tokens):
        raise self.error

    def stream(self, messages, max_tokens):
        raise self.error
        yield  # pragma: no cover


def _start(provider=None):
    provider = provider or FakeProvider()
    service = MindSporeChatService(provider, auto_start=False)
    server = service.make_server("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return service, server, thread, provider


def _request(server, method, path, payload=None, *, timeout=3):
    host, port = server.server_address
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    headers = {}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        connection.request(method, path, body, headers)
    else:
        connection.request(method, path)
    response = connection.getresponse()
    data = response.read()
    content_type = response.getheader("Content-Type", "")
    connection.close()
    return response.status, content_type, data


class ProviderUnitTests(unittest.TestCase):
    def test_direct_main_requires_verified_launcher(self):
        argv = [
            "mindspore_chat_service.py",
            "--profile",
            "qwen1.5-0.5b-mindspore",
        ]
        with patch.dict(os.environ, {"CASE9_LAUNCHER_VERIFIED": "0"}, clear=False), patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit) as raised:
                _main()
        self.assertEqual(raised.exception.code, 2)

    def test_direct_main_rejects_non_candidate_endpoint(self):
        argv = [
            "mindspore_chat_service.py",
            "--profile",
            "qwen1.5-0.5b-mindspore",
            "--host",
            "127.0.0.1",
            "--port",
            "8091",
        ]
        with patch.dict(os.environ, {"CASE9_LAUNCHER_VERIFIED": "1"}, clear=False), patch.object(sys, "argv", argv):
            with self.assertRaises(SystemExit) as raised:
                _main()
        self.assertEqual(raised.exception.code, 2)

    def test_module_import_does_not_import_mindspore_or_torch(self):
        # The provider module is already imported for this test file.  Verify
        # its source has no eager optional-runtime imports.
        source = Path("mindspore_chat_providers.py").read_text(encoding="utf-8")
        self.assertNotIn("import torch", source)
        self.assertNotIn("import mindspore\n", source)
        self.assertNotIn("import mindnlp\n", source)

    def test_profile_selects_tinyllama_without_optional_import(self):
        provider = create_provider({"id": "tinyllama-1.1b-mindspore", "source_model": "TinyLlama/test"}, model=object(), tokenizer=SimpleNamespace(vocab_size=8))
        self.assertIsInstance(provider, TinyLlamaMindSporeProvider)
        provider.close()

    def test_unknown_profile_does_not_fall_back_to_qwen(self):
        with self.assertRaisesRegex(ProviderUnavailable, "unsupported chat model profile"):
            provider_class_for_profile({"id": "unregistered-profile"})

    def test_non_mindspore_provider_is_rejected(self):
        with self.assertRaisesRegex(ProviderUnavailable, "unsupported runtime provider"):
            provider_class_for_profile({"id": "qwen1.5-0.5b-mindspore", "provider": "torch"})

    def test_lazy_load_uses_injected_loaders(self):
        calls = []
        model = SimpleNamespace(set_train=lambda value: calls.append(("train", value)), generate=lambda **kwargs: [[1, 2]])
        tokenizer = SimpleNamespace(vocab_size=8)
        provider = MindSporeChatProvider(
            {"id": "qwen", "source_model": "Qwen/test"},
            model_loader=lambda: model,
            tokenizer_loader=lambda: tokenizer,
        )
        provider.load()
        self.assertTrue(provider.ready)
        self.assertIn(("train", False), calls)
        provider.close()

    def test_local_profile_cache_does_not_request_remote_revision(self):
        calls = []

        class Loader:
            @staticmethod
            def from_pretrained(source, **kwargs):
                calls.append((source, kwargs))
                return SimpleNamespace(vocab_size=8)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "artifacts" / "models" / "qwen"
            cache.mkdir(parents=True)
            (cache / "config.json").write_text("{}", encoding="utf-8")
            (cache / "tokenizer.json").write_text("{}", encoding="utf-8")
            (cache / "model.safetensors").write_bytes(b"fixture")
            profile = {
                "id": "qwen",
                "source_model": "Qwen/example",
                "revision": "a" * 40,
                "cache_dir": "artifacts/models/qwen",
            }
            with patch.dict("os.environ", {"CASE9_MODEL_ROOT": str(root)}, clear=False):
                provider = MindSporeChatProvider(profile)
                provider._mindspore = SimpleNamespace(float16="fp16")
                provider._from_pretrained(Loader, "tokenizer")
                provider._from_pretrained(Loader, "model")
            self.assertEqual([Path(source) for source, _ in calls], [cache, cache])
            self.assertTrue(all("revision" not in kwargs for _, kwargs in calls))
            self.assertTrue(all("cache_dir" not in kwargs for _, kwargs in calls))

    def test_environment_fingerprint_is_json_safe(self):
        value = environment_fingerprint()
        self.assertIsInstance(value["fingerprint"], str)
        self.assertIn("versions", value)

    def test_generation_kwargs_pin_greedy_sampling_and_attention_mask(self):
        class Tokenizer:
            vocab_size = 8

        provider = MindSporeChatProvider(
            {"id": "qwen", "source_model": "Qwen/test"},
            model=object(),
            tokenizer=Tokenizer(),
        )
        # The lightweight fake exposes the same constructor surface used by
        # MindSpore without importing the optional runtime in controller tests.
        provider._mindspore = SimpleNamespace(
            Tensor=lambda values, dtype=None: values,
            int64="int64",
        )
        input_ids = SimpleNamespace(shape=(1, 3))
        kwargs = provider._generate_kwargs(input_ids, 4)
        self.assertEqual(kwargs["top_p"], 1.0)
        self.assertFalse(kwargs["do_sample"])
        self.assertEqual(kwargs["attention_mask"].shape, (1, 3))
        self.assertEqual(kwargs["attention_mask"].tolist(), [[1, 1, 1]])
        provider.close()

    def test_timeout_keeps_live_generation_thread_visible_until_exit(self):
        started = threading.Event()
        release = threading.Event()

        class BlockingModel:
            def generate(self, **_kwargs):
                started.set()
                release.wait(2.0)
                return [[1, 2]]

        class Tokenizer:
            vocab_size = 8
            eos_token_id = 2

            def __call__(self, _text, **_kwargs):
                return [[1]]

            def decode(self, _tokens, **_kwargs):
                return ""

        provider = MindSporeChatProvider(
            {"id": "qwen", "source_model": "Qwen/test", "context_length": 16},
            model=BlockingModel(),
            tokenizer=Tokenizer(),
            generation_timeout=0.01,
        )
        try:
            with self.assertRaises(ProviderTimeout):
                provider.complete([{"role": "user", "content": "x"}], 1)
            self.assertTrue(started.wait(1.0))
            thread = provider._generation_thread
            self.assertIsNotNone(thread)
            self.assertTrue(thread.is_alive())
            status = provider.status()
            self.assertTrue(status["busy"])
            self.assertEqual(status["cache_cleanup"], "in_progress")
            self.assertFalse(status["cache_cleared"])

            # Closing an uninterruptible generation must not invalidate the
            # model object while the worker thread can still access it.
            provider.close()
            self.assertIsNotNone(provider.model)
            self.assertFalse(provider.status()["healthy"])
            self.assertTrue(provider.status()["busy"])

            release.set()
            thread.join(timeout=2.0)
            self.assertFalse(thread.is_alive())
            # status() reconciles a thread that finished asynchronously.
            status = provider.status()
            self.assertFalse(status["busy"])
            self.assertEqual(status["cache_cleanup"], "idle")
            self.assertTrue(status["cache_cleared"])
            self.assertIsNone(provider._generation_thread)
        finally:
            release.set()
            provider.close()


class ProtocolUnitTests(unittest.TestCase):
    def test_parser_enforces_model_roles_and_greedy_bounds(self):
        request = _parse_request({"model": MODEL_ID, "messages": [{"role": "user", "content": "你好"}]})
        self.assertEqual(request.max_tokens, DEFAULT_GENERATION_TOKENS)
        with self.assertRaisesRegex(RequestError, "greedy"):
            _parse_request({"model": MODEL_ID, "messages": [{"role": "user", "content": "x"}], "temperature": 0.1})
        with self.assertRaisesRegex(RequestError, "not available"):
            _parse_request({"model": "other", "messages": [{"role": "user", "content": "x"}]})
        with self.assertRaisesRegex(RequestError, "between"):
            _parse_request({"model": MODEL_ID, "messages": [{"role": "user", "content": "x"}], "max_tokens": 81})
        with self.assertRaises(RequestError):
            _parse_request({"model": MODEL_ID, "messages": [{"role": "system", "content": "x"}]})

    def test_prefix_delta_never_repeats_known_prefix(self):
        self.assertEqual(_prefix_delta("你", "你好"), "好")
        self.assertEqual(_prefix_delta("你好", "你好"), "")
        self.assertEqual(_prefix_delta("你好", "你好世界"), "世界")

    def test_loopback_restriction(self):
        service = MindSporeChatService(FakeProvider(), auto_start=False)
        with self.assertRaises(ValueError):
            service.make_server("0.0.0.0", 0)


class HttpServiceTests(unittest.TestCase):
    def tearDown(self):
        server = getattr(self, "server", None)
        service = getattr(self, "service", None)
        if server is not None:
            server.shutdown()
            server.server_close()
            getattr(self, "thread", threading.Thread()).join(timeout=2)
        if service is not None:
            service.close()

    def setUp(self):
        self.service, self.server, self.thread, self.provider = _start()

    def test_health_and_models(self):
        status, _, body = _request(self.server, "GET", "/health")
        self.assertEqual(status, 200)
        health = json.loads(body)
        self.assertEqual(health["model_id"], MODEL_ID)
        self.assertIn("worker_pid", health)
        self.assertIn("cache_cleanup", health)
        status, _, body = _request(self.server, "GET", "/v1/models")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["data"][0]["id"], MODEL_ID)

    def test_json_completion_and_usage(self):
        status, content_type, body = _request(
            self.server,
            "POST",
            "/v1/chat/completions",
            {"model": MODEL_ID, "messages": [{"role": "user", "content": "你好"}], "max_tokens": 4},
        )
        self.assertEqual(status, 200)
        self.assertIn("application/json", content_type)
        payload = json.loads(body)
        self.assertEqual(payload["object"], "chat.completion")
        self.assertEqual(payload["choices"][0]["message"]["content"], "你好世界")
        self.assertEqual(payload["usage"]["completion_tokens"], 4)

    def test_sse_is_prefix_delta_and_ends_done(self):
        status, content_type, body = _request(
            self.server,
            "POST",
            "/v1/chat/completions",
            {"model": MODEL_ID, "messages": [{"role": "user", "content": "你好"}], "stream": True, "max_tokens": 4},
        )
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", content_type)
        wire = body.decode("utf-8")
        self.assertTrue(wire.endswith("data: [DONE]\n\n"))
        chunks = [json.loads(line[6:]) for line in wire.splitlines() if line.startswith("data: {")]
        text = "".join(item["choices"][0]["delta"].get("content", "") for item in chunks)
        self.assertEqual(text, "你好世界")
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")
        self.assertEqual(chunks[-1]["usage"]["completion_tokens"], 4)
        self.assertEqual(chunks[-1]["usage"]["prompt_tokens"], 2)

    def test_sse_provider_error_is_terminated_with_done(self):
        """A post-header provider failure must not leave SSE clients hanging."""

        self.service.close()
        self.service, self.server, self.thread, self.provider = _start(
            ErrorProvider(ProviderUnavailable("backend unavailable"))
        )
        status, content_type, body = _request(
            self.server,
            "POST",
            "/v1/chat/completions",
            {
                "model": MODEL_ID,
                "messages": [{"role": "user", "content": "你好"}],
                "stream": True,
                "max_tokens": 1,
            },
        )
        self.assertEqual(status, 200)
        self.assertIn("text/event-stream", content_type)
        wire = body.decode("utf-8")
        self.assertIn('"error"', wire)
        self.assertTrue(wire.endswith("data: [DONE]\n\n"))
        self.assertEqual(wire.count("data: [DONE]"), 1)

    def test_context_budget_is_rejected_before_provider_call(self):
        status, _, body = _request(
            self.server,
            "POST",
            "/v1/chat/completions",
            {"model": MODEL_ID, "messages": [{"role": "user", "content": "123456789012345"}], "max_tokens": 2},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(body)["error"]["code"], "invalid_request_error")
        self.assertEqual(self.provider.calls, [])

    def test_missing_token_counter_fails_closed(self):
        # A provider without the tokenizer-counting contract must not be able
        # to bypass the context budget check.
        self.provider.count_tokens = None
        status, _, body = _request(
            self.server,
            "POST",
            "/v1/chat/completions",
            {"model": MODEL_ID, "messages": [{"role": "user", "content": "x"}], "max_tokens": 1},
        )
        self.assertEqual(status, 503)
        payload = json.loads(body)
        self.assertEqual(payload["error"]["code"], "model_unavailable")
        health_status, _, health_body = _request(self.server, "GET", "/health")
        self.assertEqual(health_status, 503)
        self.assertFalse(json.loads(health_body)["healthy"])

    def test_provider_timeout_fail_closes_service(self):
        self.service.close()
        self.service, self.server, self.thread, self.provider = _start(ErrorProvider(ProviderTimeout("deadline")))
        status, _, body = _request(
            self.server,
            "POST",
            "/v1/chat/completions",
            {"model": MODEL_ID, "messages": [{"role": "user", "content": "x"}], "max_tokens": 1},
        )
        self.assertEqual(status, 504)
        health_status, _, health_body = _request(self.server, "GET", "/health")
        self.assertEqual(health_status, 503)
        self.assertFalse(json.loads(health_body)["healthy"])

    def test_busy_provider_maps_to_429(self):
        # Exercise the service's serial lock directly; HTTPServer itself is
        # intentionally single-threaded.
        request = CompletionRequest(MODEL_ID, [{"role": "user", "content": "x"}], False, 1)
        self.assertTrue(self.service._request_lock.acquire())
        try:
            with self.assertRaises(RequestError) as raised:
                self.service.complete(request)
            self.assertEqual(raised.exception.status_code, 429)
        finally:
            self.service._request_lock.release()

    def test_oversized_body_and_unknown_path(self):
        status, _, body = _request(self.server, "GET", "/missing")
        self.assertEqual(status, 404)
        status, _, body = _request(self.server, "POST", "/v1/chat/completions", {"model": MODEL_ID, "messages": []})
        self.assertEqual(status, 400)


if __name__ == "__main__":
    unittest.main()
