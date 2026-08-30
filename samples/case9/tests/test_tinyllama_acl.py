from __future__ import annotations

import http.client
import hashlib
import json
import socket
from dataclasses import replace
from pathlib import Path
import tempfile
import threading
import unittest

from tinyllama_acl_contract import TinyLlamaContract, TensorContract, ContractError
from tinyllama_acl_runtime import (
    GenerationResult,
    NativeTinyLlamaBackend,
    RuntimeDescriptor,
    RuntimeRequestError,
    RuntimeUnavailable,
    TensorDescriptor,
    TinyLlamaAclRuntime,
    _contract_from_descriptor,
    _extract_cache,
    _execution_deadline,
    _greedy_next_id,
    _host_pointer,
    _verify_om_manifest,
    _validate_runtime_descriptor,
)
from tinyllama_acl_service import (
    CompletionRequest,
    MODEL_ID,
    RequestError,
    TinyLlamaAclHttpService,
    _parse_request,
    _handler_class,
)
from tinyllama_tokenizer import TinyLlamaTokenizer, TokenizerError


class FakeEncoding:
    def __init__(self, ids):
        self.ids = list(ids)


class FakeTokenizerImplementation:
    def __init__(self):
        self.calls = []

    def get_vocab_size(self, with_added_tokens=True):
        return 8

    def token_to_id(self, token):
        return {"<unk>": 0, "<s>": 1, "</s>": 2}.get(token)

    def encode(self, text, add_special_tokens=False):
        self.calls.append(text)
        # Keep the fake deterministic while still exercising prompt rendering.
        return FakeEncoding([4, 5] if "<|user|>" in text else [6])

    def decode(self, ids, skip_special_tokens=True):
        return "".join(str(value) for value in ids if value not in {0, 1, 2})


class FakeRuntimeTokenizer:
    vocab_size = 8
    bos_token_id = 1
    eos_token_id = 2
    pad_token_id = 0

    def encode_messages(self, messages):
        return [1, 4]

    def decode(self, ids):
        return "".join(str(value) for value in ids if value not in {0, 1, 2})


def _descriptor(vocab=8, layers=1, kv_heads=1, head_dim=2):
    cache_shape = (layers, 2, 1, kv_heads, 1024, head_dim)
    cache_bytes = layers * 2 * kv_heads * 1024 * head_dim * 2
    return RuntimeDescriptor(
        inputs=(
            TensorDescriptor("input_ids", "int64", (1, 1), 8),
            TensorDescriptor("attention_mask", "int64", (1, 1025), 8200),
            TensorDescriptor("position_ids", "int64", (1, 1), 8),
            TensorDescriptor("past_key_values", "float16", cache_shape, cache_bytes),
        ),
        outputs=(
            TensorDescriptor("logits", "float16", (1, 1, vocab), 2 * vocab),
            TensorDescriptor("past_key_values_out", "float16", cache_shape, cache_bytes),
        ),
    )


def _contract(descriptor):
    cache_shape = descriptor.inputs[3].shape
    return TinyLlamaContract(
        vocabulary_size=descriptor.outputs[0].shape[-1],
        num_layers=cache_shape[0],
        num_kv_heads=cache_shape[3],
        head_dim=cache_shape[-1],
        inputs=tuple(TensorContract(item.name, item.dtype, item.shape, item.byte_size) for item in descriptor.inputs),
        outputs=tuple(TensorContract(item.name, item.dtype, item.shape, item.byte_size) for item in descriptor.outputs),
        input_order_verified=True,
    )


class FakeBackend:
    def __init__(self, descriptor):
        self.descriptor = descriptor
        self.calls = []
        self.closed = False

    def open(self, path):
        return self.descriptor

    def run(self, inputs):
        import numpy as np

        self.calls.append({key: value.copy() for key, value in inputs.items()})
        vocab = self.descriptor.outputs[0].shape[-1]
        logits = np.full((1, 1, vocab), -100, dtype=np.float16)
        # The final prompt token predicts token 5; the following token predicts EOS.
        logits[0, 0, 5 if len(self.calls) <= 2 else 2] = 100
        return [logits, inputs["past_key_values"].copy()]

    def close(self):
        self.closed = True


class TinyLlamaContractTests(unittest.TestCase):
    def test_confirmed_board_descriptor_is_admitted(self):
        descriptor = RuntimeDescriptor(
            inputs=(
                TensorDescriptor("input_ids", "int64", (1, 1), 8),
                TensorDescriptor("attention_mask", "int64", (1, 1025), 8200),
                TensorDescriptor("position_ids", "int64", (1, 1), 8),
                TensorDescriptor("past_key_values", "float16", (22, 2, 1, 4, 1024, 64), 23068672),
            ),
            outputs=(
                TensorDescriptor("logits", "float32", (1, 1, 32000), 128000),
                TensorDescriptor("out_key_values", "float16", (22, 2, 1, 4, 1, 64), 22528),
                TensorDescriptor("attn_scores", "float16", (22, 1, 32, 1, 1025), 1443200),
            ),
        )
        contract = _contract_from_descriptor(descriptor)
        _validate_runtime_descriptor(descriptor, contract)
        self.assertEqual(contract.logits_output_index, 0)
        self.assertEqual(contract.kv_output_indices, (1,))

    def test_single_position_kv_output_is_scattered_into_cache(self):
        import numpy as np

        contract = TinyLlamaContract()
        old_cache = np.zeros((22, 2, 1, 4, 1024, 64), dtype=np.float16)
        one_position = np.ones((22, 2, 1, 4, 1, 64), dtype=np.float16)
        updated = _extract_cache(
            [np.zeros((1, 1, 32000), dtype=np.float32), one_position],
            contract,
            old_cache,
            7,
            np,
        )
        self.assertEqual(updated.shape, old_cache.shape)
        self.assertEqual(float(updated[:, :, :, :, 7, :].mean()), 1.0)
        self.assertEqual(float(updated[:, :, :, :, :7, :].sum()), 0.0)

    def test_contract_round_trip_and_descriptor_validation(self):
        descriptor = _descriptor()
        contract = _contract(descriptor)
        contract.validate_descriptor(descriptor.inputs, descriptor.outputs)
        decoded = TinyLlamaContract.from_dict(contract.as_dict())
        self.assertEqual(decoded.model_id, MODEL_ID)
        self.assertEqual(decoded.inputs[3].shape[-1], 2)

        bad = contract.as_dict()
        bad["model"]["model_id"] = "other"
        with self.assertRaises(ContractError):
            TinyLlamaContract.from_dict(bad)

        bad = contract.as_dict()
        bad["model"]["eos_token_id"] = bad["model"]["vocabulary_size"]
        with self.assertRaises(ContractError):
            TinyLlamaContract.from_dict(bad)

    def test_contract_rejects_dynamic_or_wrong_input_order(self):
        descriptor = _descriptor()
        contract = _contract(descriptor)
        with self.assertRaises(ContractError):
            contract.validate_descriptor(tuple(reversed(descriptor.inputs)), descriptor.outputs)

    def test_contract_rejects_tampered_architecture_dimensions(self):
        contract = TinyLlamaContract()
        for field in ("vocabulary_size", "num_layers", "num_kv_heads", "head_dim"):
            with self.subTest(field=field):
                tampered = replace(contract, **{field: getattr(contract, field) + 1})
                with self.assertRaises(ContractError):
                    tampered.validate_static_expectations()

    def test_contract_om_binding_must_match_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            om = root / "tiny.om"
            om.write_bytes(b"fixture")
            manifest = root / "manifest.json"
            digest = hashlib.sha256(b"different").hexdigest()
            manifest.write_text(
                json.dumps(
                    {
                        "artifacts": {
                            "tinyllama_acl_om": {
                                "expected_bytes": 9,
                                "sha256": digest,
                                "revision": "r1",
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            contract = replace(
                TinyLlamaContract(),
                source_bytes=7,
                source_sha256=hashlib.sha256(b"fixture").hexdigest(),
                source_revision="r1",
            )
            with self.assertRaises(RuntimeUnavailable):
                _verify_om_manifest(om, manifest, contract)

    def test_runtime_descriptor_requires_single_position_logits(self):
        descriptor = _descriptor()
        malformed = RuntimeDescriptor(
            descriptor.inputs,
            (
                TensorDescriptor("logits", "float16", (1, 2, 8), 4 * 8),
                descriptor.outputs[1],
            ),
        )
        contract = _contract(descriptor)
        with self.assertRaises(RuntimeUnavailable):
            _validate_runtime_descriptor(malformed, contract)


class TinyLlamaTokenizerTests(unittest.TestCase):
    def test_prompt_template_and_bos(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokenizer.json"
            path.write_text("{}", encoding="utf-8")
            implementation = FakeTokenizerImplementation()
            tokenizer = TinyLlamaTokenizer(path, implementation=implementation)
            ids = tokenizer.encode_messages([{"role": "user", "content": "你好"}])
            self.assertEqual(ids[0], 1)
            self.assertIn("<|user|>\n你好</s>\n<|assistant|>", implementation.calls[0])

    def test_out_of_range_fake_token_is_rejected(self):
        class Bad(FakeTokenizerImplementation):
            def encode(self, text, add_special_tokens=False):
                return FakeEncoding([999])

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tokenizer.json"
            path.write_text("{}", encoding="utf-8")
            tokenizer = TinyLlamaTokenizer(path, implementation=Bad())
            with self.assertRaises(TokenizerError):
                tokenizer.encode_text("x")


class TinyLlamaRuntimeTests(unittest.TestCase):
    def test_acl_buffer_cleanup_follows_cann_release_order(self):
        calls = []

        class Mdl:
            @staticmethod
            def destroy_dataset(value):
                calls.append(("dataset", value))
                return 0

        class Rt:
            @staticmethod
            def free(value):
                calls.append(("free", value))
                return 0

        class Acl:
            mdl = Mdl()
            rt = Rt()

            @staticmethod
            def destroy_data_buffer(value):
                calls.append(("data", value))
                return 0

        backend = NativeTinyLlamaBackend()
        backend.acl = Acl()
        self.assertTrue(
            backend._destroy_buffers(
                "input-dataset",
                "output-dataset",
                [
                    (11, "input-buffer", 4, "input-dataset"),
                    (12, "output-buffer", 4, "output-dataset"),
                ],
            )
        )
        self.assertEqual(
            calls,
            [
                ("free", 11),
                ("data", "input-buffer"),
                ("free", 12),
                ("data", "output-buffer"),
                ("dataset", "input-dataset"),
                ("dataset", "output-dataset"),
            ],
        )

    def test_cleanup_failure_retains_unreleased_handles(self):
        class FailingModel:
            def __init__(self):
                self.calls = []

            def unload(self, handle):
                self.calls.append(("unload", handle))
                return 1

            def destroy_desc(self, handle):
                self.calls.append(("destroy_desc", handle))
                return 0

        class RuntimeApi:
            def synchronize_stream(self, handle):
                return 0

            def destroy_stream(self, handle):
                return 0

            def destroy_context(self, handle):
                return 0

            def reset_device(self, device_id):
                return 0

        model = FailingModel()
        backend = NativeTinyLlamaBackend()
        backend.acl = type("FakeAcl", (), {"mdl": model, "rt": RuntimeApi(), "finalize": lambda self: 0})()
        backend.model_id = "model"
        backend.desc = "desc"
        backend.stream = "stream"
        backend.context = "context"

        backend.close()

        self.assertTrue(backend.cleanup_failed)
        self.assertTrue(backend.poisoned)
        self.assertEqual(backend.model_id, "model")
        self.assertEqual(backend.desc, "desc")
        self.assertEqual(backend.stream, "stream")
        self.assertEqual(backend.context, "context")
        self.assertEqual(model.calls, [("unload", "model")])

    def test_host_pointer_normalizes_cann_tuple_return(self):
        import numpy as np

        class Util:
            @staticmethod
            def numpy_contiguous_to_ptr(value):
                return (123456, value)

        class FakeAcl:
            util = Util()

        array = np.ascontiguousarray(np.zeros((1,), dtype=np.float16))
        self.assertEqual(_host_pointer(FakeAcl(), array), 123456)

    def test_execution_deadline_is_safe_on_controller(self):
        # Windows has no SIGALRM; the context must remain a no-op rather than
        # importing or emulating ACL locally.
        with _execution_deadline(0.01):
            pass

    def _runtime(self):
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name)
        om = root / "tiny.om"
        tokenizer = root / "tokenizer.json"
        om.write_bytes(b"fixture")
        tokenizer.write_text("{}", encoding="utf-8")
        extracted = {
            "tokenizer.json": b"{}",
            "tokenizer.model": b"model-fixture",
            "special_tokens_map.json": b"{}",
            "tokenizer_config.json": b"{}",
        }
        for name, content in extracted.items():
            (root / name).write_bytes(content)
        manifest = root / "manifest.json"
        om_digest = hashlib.sha256(b"fixture").hexdigest()
        manifest.write_text(
            json.dumps(
                {
                    "artifacts": {
                        "tinyllama_acl_om": {
                            "revision": "fixture-revision",
                            "expected_bytes": 7,
                            "sha256": om_digest,
                        },
                        "tinyllama_tokenizer_zip": {
                            "revision": "fixture-revision",
                            "extracted_files": {
                                name: {
                                    "expected_bytes": len(content),
                                    "sha256": hashlib.sha256(content).hexdigest(),
                                }
                                for name, content in extracted.items()
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        descriptor = _descriptor(vocab=32000, layers=22, kv_heads=4, head_dim=64)
        contract_path = root / "contract.json"
        contract = replace(
            _contract(descriptor),
            source_revision="fixture-revision",
            source_bytes=7,
            source_sha256=om_digest,
        )
        contract_path.write_text(json.dumps(contract.as_dict()), encoding="utf-8")
        backend = FakeBackend(descriptor)
        runtime_tokenizer = FakeRuntimeTokenizer()
        runtime_tokenizer.vocab_size = 32000
        runtime = TinyLlamaAclRuntime(
            om,
            tokenizer,
            contract_path=contract_path,
            tokenizer_manifest_path=manifest,
            backend=backend,
            tokenizer=runtime_tokenizer,
            max_tokens=4,
        )
        return directory, runtime, backend

    def test_greedy_kv_generation_and_mask(self):
        directory, runtime, backend = self._runtime()
        try:
            runtime.start()
            result = runtime.complete([{"role": "user", "content": "hi"}], max_tokens=2)
            self.assertEqual(result.text, "5")
            self.assertEqual(result.prompt_tokens, 2)
            self.assertEqual(result.completion_tokens, 2)
            self.assertEqual(result.finish_reason, "stop")
            self.assertEqual(len(backend.calls), 3)
            self.assertEqual(int(backend.calls[0]["attention_mask"].sum()), 1)
            self.assertEqual(int(backend.calls[1]["attention_mask"].sum()), 2)
            self.assertEqual(int(backend.calls[1]["position_ids"][0, 0]), 1)
        finally:
            runtime.close()
            directory.cleanup()

    def test_token_id_zero_is_not_treated_as_end_of_sequence(self):
        import numpy as np

        directory, runtime, backend = self._runtime()

        def run_with_zero_then_eos(inputs):
            backend.calls.append({key: value.copy() for key, value in inputs.items()})
            vocab = backend.descriptor.outputs[0].shape[-1]
            logits = np.full((1, 1, vocab), -100, dtype=np.float16)
            logits[0, 0, 0 if len(backend.calls) == 2 else 2] = 100
            return [logits, inputs["past_key_values"].copy()]

        backend.run = run_with_zero_then_eos
        try:
            runtime.start()
            result = runtime.complete([{"role": "user", "content": "hi"}], max_tokens=2)
            self.assertEqual(result.completion_tokens, 2)
            self.assertEqual(len(backend.calls), 3)
        finally:
            runtime.close()
            directory.cleanup()

    def test_generation_reports_length_when_budget_is_exhausted(self):
        directory, runtime, backend = self._runtime()
        try:
            runtime.start()
            result = runtime.complete([{"role": "user", "content": "hi"}], max_tokens=1)
            self.assertEqual(result.completion_tokens, 1)
            self.assertEqual(result.finish_reason, "length")
        finally:
            runtime.close()
            directory.cleanup()

    def test_nonfinite_logits_are_rejected(self):
        import numpy as np

        for value in (np.nan, np.inf, -np.inf):
            logits = np.zeros((1, 1, 8), dtype=np.float32)
            logits[0, 0, 3] = value
            with self.subTest(value=value), self.assertRaises(RuntimeUnavailable):
                _greedy_next_id(logits, 8, np)

    def test_runtime_closes_backend_on_bad_descriptor(self):
        directory, runtime, backend = self._runtime()
        try:
            bad = RuntimeDescriptor(_descriptor().inputs[:-1], _descriptor().outputs)
            backend.descriptor = bad
            with self.assertRaises(RuntimeUnavailable):
                runtime.start()
            self.assertTrue(backend.closed)
        finally:
            runtime.close()
            directory.cleanup()

    def test_runtime_rejects_tokenizer_contract_mismatch(self):
        directory, runtime, backend = self._runtime()
        try:
            runtime._tokenizer = type(
                "MismatchedTokenizer",
                (),
                {
                    "vocab_size": 9,
                    "bos_token_id": 1,
                    "eos_token_id": 2,
                    "pad_token_id": 0,
                },
            )()
            with self.assertRaises(RuntimeUnavailable):
                runtime.start()
            self.assertTrue(backend.closed)
        finally:
            runtime.close()
            directory.cleanup()

    def test_explicit_tokenizer_config_path_is_retained(self):
        directory, runtime, _ = self._runtime()
        try:
            expected = Path(directory.name) / "custom-tokenizer-config.json"
            expected.write_text("{}", encoding="utf-8")
            runtime_with_config = TinyLlamaAclRuntime(
                runtime.om_path,
                runtime.tokenizer_path,
                contract_path=runtime.contract_path,
                backend=FakeBackend(runtime.descriptor or _descriptor(vocab=32000, layers=22, kv_heads=4, head_dim=64)),
                tokenizer=FakeRuntimeTokenizer(),
                tokenizer_config_path=expected,
            )
            self.assertEqual(runtime_with_config.tokenizer_config_path, expected)
            runtime_with_config.close()
        finally:
            runtime.close()
            directory.cleanup()

    def test_runtime_rejects_out_of_range_prompt_ids(self):
        directory, runtime, backend = self._runtime()
        try:
            runtime._tokenizer = type(
                "OutOfRangeTokenizer",
                (),
                {
                    "vocab_size": 32000,
                    "bos_token_id": 1,
                    "eos_token_id": 2,
                    "pad_token_id": 0,
                    "encode_messages": lambda self, messages: [1, 99999],
                    "decode": lambda self, ids: "",
                },
            )()
            runtime.start()
            with self.assertRaises(RuntimeRequestError) as caught:
                runtime.complete([{"role": "user", "content": "x"}], max_tokens=1)
            self.assertIn("outside the OM vocabulary", str(caught.exception))
            self.assertEqual(backend.calls, [])
        finally:
            runtime.close()
            directory.cleanup()

    def test_cleanup_failure_requires_runtime_restart(self):
        directory, runtime, backend = self._runtime()
        try:
            runtime.start()
            backend.cleanup_failed = True
            self.assertFalse(runtime.started)
            status = runtime.status()
            self.assertFalse(status["ready"])
            self.assertTrue(status["restart_required"])
            self.assertTrue(status["cleanup_failed"])
        finally:
            runtime.close()
            directory.cleanup()


class TinyLlamaServiceTests(unittest.TestCase):
    def test_request_parser_is_greedy_and_bounded(self):
        parsed = _parse_request({"model": MODEL_ID, "messages": [{"role": "user", "content": "你好"}]})
        self.assertEqual(parsed.max_tokens, 8)
        with self.assertRaises(RequestError):
            _parse_request({"model": MODEL_ID, "messages": [{"role": "user", "content": "x"}], "temperature": 0.2})
        with self.assertRaises(RequestError):
            _parse_request({"model": MODEL_ID, "messages": [{"role": "user", "content": "x"}], "max_tokens": 33})
        with self.assertRaises(RequestError):
            _parse_request({"model": MODEL_ID, "messages": [{"role": [], "content": "x"}]})

    def test_json_and_sse_endpoints(self):
        class FakeServiceRuntime:
            model_id = MODEL_ID
            started = True

            def status(self):
                return {"ready": True, "model": MODEL_ID, "descriptor_validated": True}

            def close(self):
                self.started = False

            def cancel(self):
                return None

            def complete(self, messages, max_tokens):
                return GenerationResult("你好", 2, 1)

            def stream(self, messages, max_tokens):
                yield "你", 2, 1
                yield "好", 2, 2

        service = TinyLlamaAclHttpService(FakeServiceRuntime(), auto_start=False)
        server = service.make_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            connection = http.client.HTTPConnection(host, port, timeout=3)
            body = json.dumps({"model": MODEL_ID, "messages": [{"role": "user", "content": "你好"}]})
            connection.request("POST", "/v1/chat/completions", body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            payload = json.loads(response.read())
            self.assertEqual(payload["object"], "chat.completion")
            self.assertEqual(payload["choices"][0]["finish_reason"], "stop")
            connection.close()

            connection = http.client.HTTPConnection(host, port, timeout=3)
            body = json.dumps({"model": MODEL_ID, "messages": [{"role": "user", "content": "你好"}], "stream": True, "max_tokens": 2})
            connection.request("POST", "/v1/chat/completions", body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            text = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn('"content":"你"', text)
            self.assertIn('"finish_reason":"length"', text)
            self.assertTrue(text.endswith("data: [DONE]\n\n"))
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            service.close()

    def test_sse_error_does_not_emit_done(self):
        class FailingRuntime:
            model_id = MODEL_ID
            started = True
            last_stop_reason = "stop"

            def status(self):
                return {"ready": True, "model": MODEL_ID}

            def close(self):
                self.started = False

            def cancel(self):
                return None

            def stream(self, _messages, _max_tokens):
                raise RuntimeRequestError("synthetic ACL failure")
                yield  # pragma: no cover

            def complete(self, _messages, _max_tokens):
                raise AssertionError("not used")

        service = TinyLlamaAclHttpService(FailingRuntime(), auto_start=False)
        server = service.make_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            connection = http.client.HTTPConnection(host, port, timeout=3)
            body = json.dumps(
                {
                    "model": MODEL_ID,
                    "messages": [{"role": "user", "content": "你好"}],
                    "stream": True,
                    "max_tokens": 1,
                }
            )
            connection.request("POST", "/v1/chat/completions", body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            payload = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("synthetic ACL failure", payload)
            self.assertNotIn("[DONE]", payload)
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            service.close()

    def test_sse_client_write_timeout_cancels_runtime(self):
        class Runtime:
            model_id = MODEL_ID
            started = True
            cancelled = False

            def status(self):
                return {"ready": True, "model": MODEL_ID}

            def close(self):
                self.started = False

            def cancel(self):
                self.cancelled = True

            def stream(self, _messages, _max_tokens):
                yield "partial", 1, 1

            def complete(self, _messages, _max_tokens):
                raise AssertionError("not used")

        runtime = Runtime()
        service = TinyLlamaAclHttpService(runtime, auto_start=False)
        server = service.make_server("127.0.0.1", 0)
        handler = object.__new__(_handler_class())
        handler.server = server
        handler.connection = type("Socket", (), {
            "gettimeout": lambda self: None,
            "settimeout": lambda self, _value: None,
        })()
        handler.wfile = type("Writer", (), {
            "write": lambda self, _value: (_ for _ in ()).throw(socket.timeout("slow peer")),
            "flush": lambda self: None,
        })()
        handler.send_response = lambda _status: None
        handler.send_header = lambda _name, _value: None
        handler.end_headers = lambda: None
        handler.close_connection = False
        handler._sse(CompletionRequest(MODEL_ID, [{"role": "user", "content": "x"}], True, 1))
        self.assertTrue(runtime.cancelled)
        self.assertTrue(handler.close_connection)
        server.server_close()
        service.close()


if __name__ == "__main__":
    unittest.main()
