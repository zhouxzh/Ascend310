from __future__ import annotations

import builtins
import http.client
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import mindspore_chat_providers as provider_module
from mindspore_chat_providers import (
    GenerationResult,
    MindSporeChatProvider,
    MiniCPM3MindSporeProvider,
    Qwen15MindSporeProvider,
    Qwen25MindSporeProvider,
    Qwen3MindSporeProvider,
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
    _stream_delta,
    _stream_mode,
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
        self.close_calls = 0
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
        self.close_calls += 1
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
    def test_explicit_placement_checks_parameters_dict_and_nested_data(self):
        class Model:
            def parameters_and_names(self):
                return []

            def parameters_dict(self):
                return {
                    "weight": SimpleNamespace(
                        data=SimpleNamespace(device_type="Ascend")
                    )
                }

        evidence = provider_module._explicit_model_placement(Model())
        self.assertIn(("parameters_dict[0].data.device_type", "Ascend"), evidence)

    def test_explicit_placement_tries_nonempty_source_after_empty_iterator(self):
        class Model:
            def parameters_and_names(self):
                return []

            def trainable_params(self):
                return [SimpleNamespace(data=SimpleNamespace(device="CPU:0"))]

        evidence = provider_module._explicit_model_placement(Model())
        self.assertIn(("trainable_params[0].data.device", "CPU"), evidence)

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

    def test_concrete_provider_rejects_tokenizer_without_chat_template(self):
        """Registered concrete profiles must not silently invent a prompt format."""

        provider = Qwen15MindSporeProvider(
            {"id": "qwen1.5-0.5b-mindspore", "source_model": "Qwen/test"},
            model=object(),
            tokenizer=SimpleNamespace(vocab_size=8),
        )
        try:
            with self.assertRaisesRegex(ProviderUnavailable, "no usable chat template"):
                provider.count_tokens([{"role": "user", "content": "hello"}])
        finally:
            provider.close()

    def test_qwen3_template_fallback_preserves_enable_thinking_option(self):
        """Text fallback must not re-enable Qwen3 thinking implicitly."""

        class Tokenizer:
            vocab_size = 8

            def __init__(self):
                self.calls = []

            def apply_chat_template(self, messages, **kwargs):
                self.calls.append(dict(kwargs))
                if kwargs.get("return_tensors") == "ms":
                    raise TypeError("fixture does not support tensor output")
                if kwargs.get("enable_thinking") is not False:
                    raise TypeError("enable_thinking is required")
                return "<prompt>"

            def __call__(self, text, **kwargs):
                return {"input_ids": [[1, 2, 3]]}

        tokenizer = Tokenizer()
        provider = Qwen3MindSporeProvider(
            {"id": "qwen3-0.6b-mindspore", "source_model": "fixture/model"},
            model=object(),
            tokenizer=tokenizer,
        )
        try:
            self.assertEqual(provider.count_tokens([{"role": "user", "content": "hello"}]), 3)
            self.assertEqual(tokenizer.calls[0]["enable_thinking"], False)
            self.assertEqual(tokenizer.calls[1]["enable_thinking"], False)
        finally:
            provider.close()

    def test_qwen3_rejects_template_when_option_cannot_be_enforced(self):
        class Tokenizer:
            vocab_size = 8

            def apply_chat_template(self, messages, **kwargs):
                raise TypeError("unsupported option")

        provider = Qwen3MindSporeProvider(
            {"id": "qwen3-0.6b-mindspore", "source_model": "fixture/model"},
            model=object(),
            tokenizer=Tokenizer(),
        )
        try:
            with self.assertRaisesRegex(ProviderUnavailable, "cannot enforce chat template options"):
                provider.count_tokens([{"role": "user", "content": "hello"}])
        finally:
            provider.close()

    def test_new_native_profiles_select_explicit_provider_classes(self):
        cases = {
            "qwen2.5-0.5b-mindspore": Qwen25MindSporeProvider,
            "qwen2.5-1.5b-mindspore": Qwen25MindSporeProvider,
            "qwen3-0.6b-mindspore": Qwen3MindSporeProvider,
            "qwen3-1.7b-mindspore": Qwen3MindSporeProvider,
            "minicpm3-4b-mindspore": MiniCPM3MindSporeProvider,
        }
        for profile_id, expected in cases.items():
            provider = create_provider(
                {"id": profile_id, "source_model": "fixture/model"},
                model=object(),
                tokenizer=SimpleNamespace(vocab_size=8),
            )
            self.assertIsInstance(provider, expected)
            provider.close()

    def test_conditional_profile_is_rejected_before_runtime_import(self):
        with self.assertRaisesRegex(ProviderUnavailable, "conditional"):
            provider_class_for_profile(
                {"id": "openpangu-embedded-1b-conditional", "candidate_kind": "conditional"}
            )

    def test_unknown_candidate_kind_is_rejected_by_provider_factory(self):
        with self.assertRaisesRegex(ProviderUnavailable, "unsupported candidate kind"):
            provider_class_for_profile(
                {"id": "qwen1.5-0.5b-mindspore", "candidate_kind": "future_runtime"}
            )

    def test_unknown_profile_does_not_fall_back_to_qwen(self):
        with self.assertRaisesRegex(ProviderUnavailable, "unsupported chat model profile"):
            provider_class_for_profile({"id": "unregistered-profile"})

    def test_non_mindspore_provider_is_rejected(self):
        with self.assertRaisesRegex(ProviderUnavailable, "unsupported runtime provider"):
            provider_class_for_profile({"id": "qwen1.5-0.5b-mindspore", "provider": "torch"})
        with self.assertRaisesRegex(ProviderUnavailable, "unsupported runtime provider"):
            provider_class_for_profile({
                "id": "qwen1.5-0.5b-mindspore",
                "runtime": {"provider": "torch"},
            })

    def test_lazy_load_uses_injected_loaders(self):
        calls = []
        model = SimpleNamespace(set_train=lambda value: calls.append(("train", value)), generate=lambda **kwargs: [[1, 2]])
        tokenizer = SimpleNamespace(vocab_size=8, decode=lambda values, **_kwargs: "ok")
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

    def test_qualified_ms_dtype_is_normalized_without_mutating_config(self):
        calls = []

        class Loader:
            @staticmethod
            def from_pretrained(source, **kwargs):
                calls.append((source, kwargs))
                return SimpleNamespace(vocab_size=8)

        class AutoConfig:
            @staticmethod
            def for_model(model_type, **kwargs):
                calls.append(("config", model_type, kwargs))
                return SimpleNamespace(model_type=model_type, ms_dtype=kwargs["ms_dtype"])

        fake_transformers = SimpleNamespace(AutoConfig=AutoConfig)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            cache = root / "artifacts" / "models" / "deepseek"
            cache.mkdir(parents=True)
            original = {
                "model_type": "qwen2",
                "hidden_size": 16,
                "ms_dtype": "mindspore.float16",
            }
            config_path = cache / "config.json"
            config_path.write_text(json.dumps(original), encoding="utf-8")
            (cache / "tokenizer.json").write_text("{}", encoding="utf-8")
            (cache / "model.safetensors").write_bytes(b"fixture")
            profile = {
                "id": "deepseek-r1-qwen-1.5b-mindspore",
                "source_model": "MindSpore-Lab/example",
                "revision": "a" * 40,
                "cache_dir": "artifacts/models/deepseek",
            }
            with patch.dict("os.environ", {"CASE9_MODEL_ROOT": str(root)}, clear=False), patch.object(
                provider_module.importlib,
                "import_module",
                return_value=fake_transformers,
            ):
                provider = MindSporeChatProvider(profile)
                provider._mindspore = SimpleNamespace(float16="fp16")
                provider._from_pretrained(Loader, "model")
            self.assertEqual(json.loads(config_path.read_text(encoding="utf-8")), original)
            config_calls = [item for item in calls if item[0] == "config"]
            self.assertEqual(len(config_calls), 1)
            self.assertEqual(config_calls[0][1], "qwen2")
            self.assertEqual(config_calls[0][2]["ms_dtype"], "float16")
            loader_calls = [item for item in calls if isinstance(item[0], str) and item[0] != "config"]
            self.assertEqual(len(loader_calls), 1)
            self.assertNotIn("ms_dtype", loader_calls[0][1])
            self.assertEqual(loader_calls[0][1]["config"].ms_dtype, "float16")

    def test_environment_fingerprint_is_json_safe(self):
        value = environment_fingerprint()
        self.assertIsInstance(value["fingerprint"], str)
        self.assertIn("versions", value)

    def test_string_eos_token_is_resolved_through_tokenizer(self):
        class Tokenizer:
            vocab_size = 8
            eos_token = "</s>"

            def convert_tokens_to_ids(self, token):
                return {"</s>": 7}.get(token)

        provider = MindSporeChatProvider(
            {"id": "qwen", "source_model": "Qwen/test"},
            model=object(),
            tokenizer=Tokenizer(),
        )
        try:
            self.assertEqual(provider.eos_token_id, 7)
        finally:
            provider.close()

    def test_completion_output_accepts_completion_only_sequence(self):
        class Model:
            def generate(self, **_kwargs):
                return [[4, 5]]

        class Tokenizer:
            vocab_size = 8
            eos_token_id = 7

            def __call__(self, _text, **_kwargs):
                return [[1, 2, 3]]

            def decode(self, values, **_kwargs):
                return "".join(str(value) for value in values)

        provider = MindSporeChatProvider(
            {"id": "qwen", "source_model": "Qwen/test", "context_length": 16},
            model=Model(),
            tokenizer=Tokenizer(),
        )
        try:
            result = provider.complete([{"role": "user", "content": "x"}], 2)
            self.assertEqual(result.text, "45")
            self.assertEqual(result.completion_tokens, 2)
            self.assertEqual(result.finish_reason, "length")
        finally:
            provider.close()

    def test_completion_output_without_prefix_or_bound_is_rejected(self):
        class Model:
            def generate(self, **_kwargs):
                return [[9, 8, 7]]

        class Tokenizer:
            vocab_size = 16

            def __call__(self, _text, **_kwargs):
                return [[1, 2, 3]]

        provider = MindSporeChatProvider(
            {"id": "qwen", "source_model": "Qwen/test", "context_length": 16},
            model=Model(),
            tokenizer=Tokenizer(),
        )
        try:
            with self.assertRaises(ProviderUnavailable):
                provider.complete([{"role": "user", "content": "x"}], 2)
            self.assertFalse(provider.healthy)
        finally:
            provider.close()

    def test_generation_tuple_prefers_integer_sequence_over_logits(self):
        class IntegerTensor:
            dtype = "Int64"

            def asnumpy(self):
                return [[4, 5]]

        class FloatTensor:
            dtype = "Float32"

            def asnumpy(self):
                return [[[0.1, 0.2]]]

        model = SimpleNamespace(generate=lambda **_kwargs: (FloatTensor(), IntegerTensor()))
        class Tokenizer:
            vocab_size = 8

            def __call__(self, _text, **_kwargs):
                return [[1, 2, 3]]

            def decode(self, _values, **_kwargs):
                return "ok"

        tokenizer = Tokenizer()
        provider = MindSporeChatProvider(
            {"id": "qwen", "source_model": "Qwen/test"},
            model=model,
            tokenizer=tokenizer,
        )
        try:
            result = provider.complete([{"role": "user", "content": "x"}], 2)
            self.assertEqual(result.completion_tokens, 2)
        finally:
            provider.close()

    def test_health_fails_closed_for_unverified_model_placement(self):
        profile = SimpleNamespace(
            id="qwen",
            status="experimental_dirty_base",
            candidate_kind="native_mindspore",
            runtime_provider="mindspore",
            is_conditional=False,
            board_targets=(
                {"host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
            ),
            validation={
                "Ascend310B4": {
                    "status": "experimental_dirty_base",
                    "reason": "fixture",
                }
            },
        )
        profile.validation["Ascend310B4"]["status"] = "experimental_dirty_base"
        provider = FakeProvider()
        provider.status = lambda: {
            "ready": True,
            "healthy": True,
            "npu_model": "Ascend310B4",
            "placement_status": "unknown",
        }
        service = MindSporeChatService(provider, profile=profile, auto_start=False)
        with patch.dict(os.environ, {"CASE9_ALLOW_EXPERIMENTAL": "1"}, clear=False):
            health = service.health()
        self.assertFalse(health["healthy"])
        self.assertFalse(health["ready"])
        self.assertFalse(health["admission_allowed"])
        self.assertIn("placement", health["admission_reason"])
        service.close()

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

    def test_streamer_early_end_cancels_and_triggers_watchdog(self):
        """An ended streamer must not hide a still-running model call."""

        release = threading.Event()

        class BlockingModel:
            def __init__(self):
                self.cancel_calls = 0

            def generate(self, **_kwargs):
                release.wait(2.0)
                return [[1, 2]]

            def cancel_generation(self):
                self.cancel_calls += 1

        class Tokenizer:
            vocab_size = 8

            def __call__(self, _text, **_kwargs):
                return SimpleNamespace(shape=(1, 1))

        class ImmediateEndStreamer:
            def __init__(self, *_args, **_kwargs):
                pass

            def __iter__(self):
                return self

            def __next__(self):
                raise StopIteration

        model = BlockingModel()
        provider = MindSporeChatProvider(
            {"id": "qwen", "source_model": "Qwen/test", "context_length": 16},
            model=model,
            tokenizer=Tokenizer(),
            generation_timeout=5.0,
        )
        try:
            with patch(
                "mindspore_chat_providers.importlib.import_module",
                return_value=SimpleNamespace(TextIteratorStreamer=ImmediateEndStreamer),
            ), patch.object(provider, "_terminate_after_watchdog") as terminate:
                with self.assertRaisesRegex(ProviderTimeout, "did not stop"):
                    list(provider.stream([{"role": "user", "content": "x"}], 1))
            self.assertGreaterEqual(model.cancel_calls, 1)
            terminate.assert_called_once()
            self.assertFalse(provider.healthy)
        finally:
            release.set()
            thread = provider._generation_thread
            if thread is not None:
                thread.join(timeout=2.0)
            provider.close()

    def test_streamer_blocking_next_is_bounded_by_generation_watchdog(self):
        """A streamer that ignores its timeout must not block ``stream``."""

        release = threading.Event()

        class FastModel:
            def generate(self, **_kwargs):
                # The model call itself has completed; only the untrusted
                # streamer reader is stuck.  This exercises the separate
                # bounded reader thread rather than the complete() watchdog.
                return [[1, 2]]

        class Tokenizer:
            vocab_size = 8

            def __call__(self, _text, **_kwargs):
                return SimpleNamespace(shape=(1, 1))

        class BlockingNextStreamer:
            def __init__(self, *_args, **_kwargs):
                pass

            def __iter__(self):
                return self

            def __next__(self):
                release.wait(2.0)
                raise StopIteration

        provider = MindSporeChatProvider(
            {"id": "qwen", "source_model": "Qwen/test", "context_length": 16},
            model=FastModel(),
            tokenizer=Tokenizer(),
            generation_timeout=0.05,
        )
        try:
            with patch(
                "mindspore_chat_providers.importlib.import_module",
                return_value=SimpleNamespace(TextIteratorStreamer=BlockingNextStreamer),
            ), patch.object(provider, "_terminate_after_watchdog") as terminate:
                started = time.monotonic()
                with self.assertRaisesRegex(ProviderTimeout, "timed out"):
                    list(provider.stream([{"role": "user", "content": "x"}], 1))
                self.assertLess(time.monotonic() - started, 1.0)
            terminate.assert_called_once()
            self.assertFalse(provider.healthy)
            reader = provider._stream_reader_thread
            self.assertIsNotNone(reader)
            self.assertTrue(reader.is_alive())
        finally:
            release.set()
            reader = provider._stream_reader_thread
            if reader is not None:
                reader.join(timeout=2.0)
            provider.close()

    def test_streamer_reader_provider_error_latches_unhealthy(self):
        """Reader-side backend errors must fail closed like generate errors."""

        class FastModel:
            def generate(self, **_kwargs):
                return [[1, 2]]

        class Tokenizer:
            vocab_size = 8

            def __call__(self, _text, **_kwargs):
                return SimpleNamespace(shape=(1, 1))

        class ErrorStreamer:
            def __init__(self, *_args, **_kwargs):
                pass

            def __iter__(self):
                return self

            def __next__(self):
                raise ProviderUnavailable("stream reader backend failed")

        provider = MindSporeChatProvider(
            {"id": "qwen", "source_model": "Qwen/test", "context_length": 16},
            model=FastModel(),
            tokenizer=Tokenizer(),
            generation_timeout=1.0,
        )
        try:
            with patch(
                "mindspore_chat_providers.importlib.import_module",
                return_value=SimpleNamespace(TextIteratorStreamer=ErrorStreamer),
            ):
                with self.assertRaisesRegex(ProviderUnavailable, "stream reader backend failed"):
                    list(provider.stream([{"role": "user", "content": "x"}], 1))
            self.assertFalse(provider.healthy)
            self.assertIn("stream reader backend failed", provider.status()["last_error"])
        finally:
            provider.close()

    def test_stream_delta_supports_explicit_fragments_without_deduplicating_repeats(self):
        # Fragment mode must preserve repeated characters; cumulative mode must
        # suppress a repeated/non-prefix snapshot because SSE cannot edit text
        # already delivered to the client.
        delta, assembled = _stream_delta("ha", "ha", "fragment")
        self.assertEqual(delta, "ha")
        self.assertEqual(assembled, "haha")
        delta, assembled = _stream_delta("你好", "好", "cumulative")
        self.assertEqual(delta, "")
        self.assertEqual(assembled, "你好")

    def test_stream_mode_accepts_explicit_event_markers(self):
        self.assertEqual(_stream_mode({"text": "x", "cumulative": False}), "fragment")
        self.assertEqual(_stream_mode(("x", 1, None, "fragment")), "fragment")
        self.assertEqual(_stream_mode({"text": "x", "mode": "snapshot"}), "cumulative")
        self.assertEqual(_stream_mode({"text": "x", "mode": "unknown"}), "auto")

    def test_launcher_validates_profile_before_using_it_in_report_paths(self):
        launcher = Path("scripts/run_mindspore_chat_service.sh").read_text(encoding="utf-8")
        self.assertIn("Invalid profile identifier for worker launch", launcher)
        self.assertIn("^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$", launcher)
        # The wrapper is normally invoked as ``bash script.sh`` on a board;
        # re-exec through bash so a checkout with mode 0644 still works.
        self.assertIn('exec setsid bash "${BASH_SOURCE[0]}" "$@"', launcher)
        # Artifact verification must use the selected copied/temporary
        # registry, rather than silently reading the controller default.
        self.assertIn('--registry "${registry}"', launcher)


class ProtocolUnitTests(unittest.TestCase):
    @staticmethod
    def _dual_soc_profile():
        return SimpleNamespace(
            id="dual-soc-profile",
            status="blocked",
            candidate_kind="native_mindspore",
            runtime_provider="mindspore",
            is_conditional=False,
            board_targets=(
                {"host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
                {"host": "192.168.1.95", "soc": "Ascend310B1", "tier": "20T"},
            ),
            validation={
                "Ascend310B4": {"status": "experimental_dirty_base", "reason": "B4 smoke passed"},
                "Ascend310B1": {"status": "blocked", "reason": "B1 not tested"},
            },
            admission_reason="No aggregate admission.",
        )

    def test_start_uses_matching_soc_validation_only(self):
        profile = self._dual_soc_profile()
        provider = FakeProvider()
        service = MindSporeChatService(provider, profile=profile, auto_start=False)
        with patch.dict(os.environ, {"CASE9_ALLOW_EXPERIMENTAL": "1"}, clear=False), patch(
            "mindspore_chat_service._probe_current_soc", return_value="Ascend310B4"
        ):
            service.start()
        self.assertTrue(service.started)
        self.assertTrue(service.healthy)
        service.close()

        provider = FakeProvider()
        service = MindSporeChatService(provider, profile=self._dual_soc_profile(), auto_start=False)
        with patch("mindspore_chat_service._probe_current_soc", return_value="Ascend310B1"):
            with self.assertRaises(ProviderUnavailable):
                service.start()
        self.assertFalse(service.healthy)
        service.close()

    def test_health_reports_current_soc_admission(self):
        profile = self._dual_soc_profile()
        provider = FakeProvider()
        provider.status = lambda: {
            "ready": True,
            "healthy": True,
            "npu_model": "Ascend310B1",
        }
        service = MindSporeChatService(provider, profile=profile, auto_start=False)
        health = service.health()
        self.assertEqual(health["profile_status"], "blocked")
        self.assertEqual(health["admission_status"], "blocked")
        self.assertEqual(health["admission_soc"], "Ascend310B1")
        self.assertFalse(health["conditional"])
        self.assertFalse(health["admission_allowed"])
        self.assertFalse(health["healthy"])

    def test_models_and_completion_fail_closed_on_current_soc_admission(self):
        """Endpoint admission must honor the matching per-SoC registry row."""

        profile = self._dual_soc_profile()
        provider = FakeProvider()
        provider.status = lambda: {
            "ready": True,
            "healthy": True,
            "npu_model": "Ascend310B1",
            "placement_status": "verified",
        }
        service = MindSporeChatService(provider, profile=profile, auto_start=False)
        with patch.dict(os.environ, {"CASE9_ALLOW_EXPERIMENTAL": "1"}, clear=False):
            with self.assertRaises(ProviderUnavailable):
                service.models()
            with self.assertRaises(ProviderUnavailable):
                service.complete(
                    CompletionRequest(
                        MODEL_ID,
                        [{"role": "user", "content": "x"}],
                        False,
                        1,
                    )
                )
        self.assertFalse(service.healthy)
        self.assertGreaterEqual(provider.cancelled, 1)
        service.close()

    def test_endpoint_admission_fails_closed_on_unverified_placement(self):
        """A loaded NPU model without placement evidence is not servable."""

        profile = self._dual_soc_profile()
        provider = FakeProvider()
        provider.status = lambda: {
            "ready": True,
            "healthy": True,
            "npu_model": "Ascend310B4",
            "placement_status": "unknown",
        }
        service = MindSporeChatService(provider, profile=profile, auto_start=False)
        with patch.dict(os.environ, {"CASE9_ALLOW_EXPERIMENTAL": "1"}, clear=False):
            with self.assertRaises(ProviderUnavailable):
                service.models()
        self.assertFalse(service.healthy)
        self.assertIn("placement", service.health().get("last_error", ""))
        service.close()

    def test_health_marks_conditional_profile_explicitly(self):
        profile = self._dual_soc_profile()
        profile.candidate_kind = "conditional"
        profile.is_conditional = True
        provider = FakeProvider()
        provider.status = lambda: {
            "ready": True,
            "healthy": True,
            "npu_model": "Ascend310B4",
        }
        service = MindSporeChatService(provider, profile=profile, auto_start=False)
        health = service.health()
        self.assertTrue(health["conditional"])
        self.assertEqual(health["candidate_kind"], "conditional")
        self.assertFalse(health["admission_allowed"])
        self.assertFalse(health["healthy"])
        service.close()

    def test_health_blocks_non_mindspore_runtime_provider(self):
        """A provider alias must not make an unsupported profile look ready."""

        profile = SimpleNamespace(
            id="unsupported-runtime",
            status="admitted",
            candidate_kind="native_mindspore",
            runtime_provider="torch",
            is_conditional=False,
            board_targets=(),
            validation={},
        )
        provider = FakeProvider()
        service = MindSporeChatService(provider, profile=profile, auto_start=False)
        health = service.health()
        self.assertTrue(health["conditional"])
        self.assertEqual(health["admission_status"], "blocked")
        self.assertFalse(health["admission_allowed"])
        self.assertFalse(health["healthy"])
        self.assertFalse(health["ready"])
        service.close()

    def test_health_blocks_admitted_profile_without_human_quality_approval(self):
        """An ``admitted`` bit cannot bypass the manual 8/10 quality gate."""

        profile = {
            "id": "admitted-without-review",
            "status": "admitted",
            "candidate_kind": "native_mindspore",
            "runtime_provider": "mindspore",
            "is_conditional": False,
            "languages": ["zh"],
            "quality": {
                "reviewed": True,
                "human_review": "pending",
                "human_passed_count": 10,
                "human_sample_count": 10,
                "languages": {"zh": "passed"},
            },
            "board_targets": [
                {"host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
            ],
            "validation": {
                "Ascend310B4": {
                    "status": "admitted",
                    "quality": {
                        "reviewed": True,
                        "human_review": "pending",
                        "human_passed_count": 10,
                        "human_sample_count": 10,
                        "languages": {"zh": "passed"},
                    },
                },
            },
        }
        provider = FakeProvider()
        provider.status = lambda: {
            "ready": True,
            "healthy": True,
            "npu_model": "Ascend310B4",
        }
        service = MindSporeChatService(provider, profile=profile, auto_start=False)
        health = service.health()
        self.assertEqual(health["admission_status"], "blocked")
        self.assertFalse(health["admission_allowed"])
        self.assertFalse(health["healthy"])
        self.assertIn("human", health["admission_reason"])
        service.close()

    def test_string_false_conditional_flag_is_not_treated_as_true(self):
        """Mappings from JSON/config layers must preserve an explicit false."""

        profile = {
            "id": "string-flag-profile",
            "status": "admitted",
            "candidate_kind": "native_mindspore",
            "runtime_provider": "mindspore",
            "is_conditional": "false",
            "board_targets": [],
            "validation": {},
        }
        provider = FakeProvider()
        provider.status = lambda: {
            "ready": True,
            "healthy": True,
            "npu_model": "Ascend310B4",
        }
        service = MindSporeChatService(provider, profile=profile, auto_start=False)
        health = service.health()
        self.assertFalse(health["conditional"])
        self.assertEqual(health["candidate_kind"], "native_mindspore")
        service.close()

    def test_unknown_candidate_kind_is_fail_closed(self):
        profile = {
            "id": "unknown-kind-profile",
            "status": "admitted",
            "candidate_kind": "future-runtime",
            "runtime_provider": "mindspore",
            "is_conditional": False,
            "board_targets": [],
            "validation": {},
        }
        provider = FakeProvider()
        provider.status = lambda: {"ready": True, "healthy": True, "npu_model": "Ascend310B4"}
        service = MindSporeChatService(provider, profile=profile, auto_start=False)
        health = service.health()
        self.assertTrue(health["conditional"])
        self.assertEqual(health["admission_status"], "blocked")
        self.assertFalse(health["ready"])
        service.close()

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

    def test_bind_failure_closes_provider_after_auto_start(self):
        provider = FakeProvider()
        service = MindSporeChatService(provider, auto_start=True)
        try:
            with patch.dict(os.environ, {"CASE9_ALLOW_EXPERIMENTAL": "1"}, clear=False), patch(
                "mindspore_chat_service._probe_current_soc", return_value=""
            ):
                with patch("mindspore_chat_service._ManagedHTTPServer", side_effect=OSError("address in use")):
                    with self.assertRaises(OSError):
                        service.make_server("127.0.0.1", 8090)
            self.assertTrue(provider.closed)
            self.assertEqual(provider.close_calls, 1)
            self.assertIsNone(service._server)
        finally:
            service.close()

    def test_start_failure_closes_partially_loaded_provider(self):
        class FailingProvider(FakeProvider):
            def load(self):
                self.ready = True
                raise RuntimeError("loader failed after allocation")

        provider = FailingProvider()
        service = MindSporeChatService(provider, auto_start=True)
        with patch.dict(os.environ, {"CASE9_ALLOW_EXPERIMENTAL": "1"}, clear=False):
            with patch("mindspore_chat_service._probe_current_soc", return_value=""):
                with self.assertRaises(ProviderUnavailable):
                    service.make_server("127.0.0.1", 0)
        self.assertTrue(provider.closed)
        self.assertEqual(provider.close_calls, 1)
        self.assertIsNone(service._server)
        service.close()

    def test_close_releases_owned_listener(self):
        provider = FakeProvider()
        service = MindSporeChatService(provider, auto_start=False)
        server = service.make_server("127.0.0.1", 0)
        self.assertGreaterEqual(server.fileno(), 0)
        service.close()
        self.assertEqual(server.fileno(), -1)
        self.assertIsNone(service._server)


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

    def test_json_disconnect_barrier_cancels_before_provider_call(self):
        """A FIN before worker admission must not start a late generation."""

        class TrackingProvider(FakeProvider):
            def __init__(self):
                super().__init__()
                self.cancel_seen = threading.Event()
                self.complete_calls = 0

            def cancel(self):
                super().cancel()
                self.cancel_seen.set()

            def complete(self, messages, max_tokens):
                self.complete_calls += 1
                return super().complete(messages, max_tokens)

        self.service.close()
        provider = TrackingProvider()
        self.service, self.server, self.thread, self.provider = _start(provider)
        entered = threading.Event()
        release = threading.Event()
        original_impl = self.service._complete_impl

        def delayed_impl(request, *, cancellation):
            entered.set()
            release.wait(2.0)
            return original_impl(request, cancellation=cancellation)

        # This creates the real ordering window: the HTTP worker has been
        # launched, but the service has not yet reached provider admission.
        self.service._complete_impl = delayed_impl
        body = json.dumps(
            {
                "model": MODEL_ID,
                "messages": [{"role": "user", "content": "x"}],
                "max_tokens": 1,
            }
        ).encode("utf-8")
        host, port = self.server.server_address
        connection = socket.create_connection((host, port), timeout=3)
        try:
            request = (
                "POST /v1/chat/completions HTTP/1.1\r\n"
                "Host: localhost\r\n"
                "Content-Type: application/json\r\n"
                "Connection: close\r\n"
                "Content-Length: %d\r\n\r\n" % len(body)
            ).encode("ascii")
            connection.sendall(request + body)
            self.assertTrue(entered.wait(2.0))
        finally:
            connection.close()

        # Wait until the handler has observed the FIN and requested cancel;
        # only then let the delayed worker continue.
        self.assertTrue(provider.cancel_seen.wait(3.0))
        release.set()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not provider.cancelled:
            time.sleep(0.01)
        self.assertEqual(provider.complete_calls, 0)
        self.assertTrue(self.service.healthy)

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

    def test_sse_explicit_fragment_events_are_sent_once(self):
        self.service.close()

        class FragmentProvider(FakeProvider):
            def stream(self, messages, max_tokens):
                self.calls.append((messages, max_tokens))
                yield ("哈", 1, None, "fragment")
                yield ("哈", 2, "stop", "fragment")

        self.service, self.server, self.thread, self.provider = _start(FragmentProvider())
        status, _, body = _request(
            self.server,
            "POST",
            "/v1/chat/completions",
            {"model": MODEL_ID, "messages": [{"role": "user", "content": "x"}], "stream": True, "max_tokens": 2},
        )
        self.assertEqual(status, 200)
        chunks = [json.loads(line[6:]) for line in body.decode("utf-8").splitlines() if line.startswith("data: {")]
        text = "".join(item["choices"][0]["delta"].get("content", "") for item in chunks)
        self.assertEqual(text, "哈哈")

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

    def test_http_watchdog_timeout_latches_service_failed_closed(self):
        release = threading.Event()

        class BlockingProvider(FakeProvider):
            def complete(self, messages, max_tokens):
                release.wait(2.0)
                return super().complete(messages, max_tokens)

        self.service.close()
        provider = BlockingProvider()
        self.service, self.server, self.thread, self.provider = _start(provider)
        self.service.generation_deadline = lambda: 0.05
        try:
            status, _, body = _request(
                self.server,
                "POST",
                "/v1/chat/completions",
                {"model": MODEL_ID, "messages": [{"role": "user", "content": "x"}], "max_tokens": 1},
                timeout=3,
            )
            self.assertEqual(status, 504)
            self.assertEqual(json.loads(body)["error"]["code"], "timeout")
            health_status, _, health_body = _request(self.server, "GET", "/health")
            self.assertEqual(health_status, 503)
            self.assertFalse(json.loads(health_body)["healthy"])
        finally:
            release.set()

    def test_service_budget_is_enforced_for_direct_completion_requests(self):
        # A caller constructing CompletionRequest directly must not bypass the
        # HTTP parser's max-token and fixed-context limits.
        request = CompletionRequest(MODEL_ID, [{"role": "user", "content": "x"}], False, 65)
        with self.assertRaises(RequestError) as raised:
            self.service.complete(request)
        self.assertEqual(raised.exception.status_code, 400)

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
