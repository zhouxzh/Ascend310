from __future__ import annotations

import http.client
import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from acl_om_contract import ContractError, ModelContract
from acl_om_runtime import (
    AclOmRuntime,
    GenerationResult,
    RuntimeDescriptor,
    RuntimeExecutionTimeout,
    RuntimeUnavailable,
    TensorDescriptor,
    _execution_deadline,
)
from acl_om_service import (
    AclOmHttpService,
    CompletionRequest,
    MODEL_ID,
    RequestError,
    _parse_request,
    _read_request_body,
)


def make_contract(*, supported: bool = True) -> dict:
    return {
        "schema_version": 1,
        "model": {"family": "qwen1.5", "model_id": MODEL_ID, "eos_token_id": 3, "pad_token_id": 0},
        "acl_om": {
            "supported_autoregressive_qwen_layout": supported,
            "support_reason": "fixture",
            "execution_mode": "full_context_logits",
            "external_initializers": False,
            "has_past_key_values": False,
            "static_sequence_length": 2048,
            "input_order": ["input_ids", "attention_mask", "position_ids"],
            "input_order_verified": True,
            "inputs": {
                "input_ids": {"name": "input_ids", "dtype": "int64", "shape": [1, 2048]},
                "attention_mask": {"name": "attention_mask", "dtype": "int64", "shape": [1, 2048]},
                "position_ids": {"name": "position_ids", "dtype": "int64", "shape": [1, 2048]},
            },
            "output": {"logits": {"name": "logits", "dtype": "float16", "shape": [1, 2048, 8]}},
            "vocabulary_size": 8,
        },
    }


def descriptor(output_shape=(1, 2048, 8)) -> RuntimeDescriptor:
    return RuntimeDescriptor(
        tuple(TensorDescriptor(name, "int64", (1, 2048), 16_384) for name in ("input_ids", "attention_mask", "position_ids")),
        (TensorDescriptor("logits", "float16", output_shape, 2 * output_shape[0] * output_shape[1] * output_shape[2]),),
    )


class FakeBackend:
    def __init__(self, model_descriptor: RuntimeDescriptor):
        self.model_descriptor = model_descriptor
        self.opened = False
        self.closed = False

    def open(self, _path: Path) -> RuntimeDescriptor:
        self.opened = True
        return self.model_descriptor

    def run(self, _inputs):
        raise AssertionError("descriptor tests must not execute the graph")

    def close(self) -> None:
        self.closed = True


class FakeTokenizer:
    vocab_size = 8

    def encode_messages(self, _messages):
        return [1, 2]

    def decode(self, token_ids):
        return "".join(str(value) for value in token_ids)


class ContractTests(unittest.TestCase):
    def _runtime(self, backend: FakeBackend) -> AclOmRuntime:
        root = Path(self.temp_dir.name)
        return AclOmRuntime(root / "contract.json", root / "qwen.om", root / "tokenizer.json", backend=backend, tokenizer=FakeTokenizer())

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        (root / "contract.json").write_text(json.dumps(make_contract()), encoding="utf-8")
        (root / "qwen.om").write_bytes(b"fixture")
        (root / "tokenizer.json").write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_rejects_unsupported_inspection_result(self):
        with self.assertRaisesRegex(ContractError, "does not admit"):
            ModelContract.from_dict(make_contract(supported=False))

    def test_contract_rejects_non_fixed_model_id(self):
        contract = make_contract()
        contract["model"]["model_id"] = "other-model"
        with self.assertRaisesRegex(ContractError, "model.model_id"):
            ModelContract.from_dict(contract)

    def test_contract_requires_exact_verified_input_order(self):
        contract = make_contract()
        contract["acl_om"]["input_order"] = [
            "attention_mask",
            "input_ids",
            "position_ids",
        ]
        with self.assertRaisesRegex(ContractError, "input_order"):
            ModelContract.from_dict(contract)

    def test_contract_requires_complete_source_binding(self):
        contract = make_contract()
        contract["source_artifact"] = {"bytes": 123}
        with self.assertRaisesRegex(ContractError, "both bytes and sha256"):
            ModelContract.from_dict(contract)

        contract = make_contract()
        del contract["acl_om"]["input_order"]
        with self.assertRaisesRegex(ContractError, "input_order"):
            ModelContract.from_dict(contract)

    def test_accepts_matching_descriptor_and_closes_backend(self):
        backend = FakeBackend(descriptor())
        runtime = self._runtime(backend)
        runtime.start()
        self.assertTrue(runtime.started)
        runtime.close()
        self.assertTrue(backend.closed)

    def test_rejects_mismatching_descriptor_before_inference(self):
        backend = FakeBackend(descriptor((1, 2048, 7)))
        runtime = self._runtime(backend)
        with self.assertRaisesRegex(RuntimeUnavailable, "logits descriptor"):
            runtime.start()
        self.assertTrue(backend.closed)

    def test_runtime_uses_greedy_logits_and_contract_padding(self):
        if importlib.util.find_spec("numpy") is None:
            self.skipTest("NumPy is installed only in the board case9-acl-om environment")
        import numpy as np

        class GenerationBackend(FakeBackend):
            def __init__(self):
                super().__init__(descriptor())
                self.inputs = []

            def run(self, inputs):
                self.inputs.append(inputs)
                logits = np.full((1, 2048, 8), -100.0, dtype=np.float16)
                # First pass chooses token 4; the next pass chooses EOS 3.
                position = int(inputs["attention_mask"].sum()) - 1
                logits[0, position, 4 if len(self.inputs) == 1 else 3] = 100.0
                return logits

        backend = GenerationBackend()
        runtime = self._runtime(backend)
        runtime.start()
        result = runtime.complete([{"role": "user", "content": "你好"}], max_tokens=2)
        self.assertEqual(result.text, "4")
        self.assertEqual(result.completion_tokens, 2)
        self.assertTrue((backend.inputs[0]["input_ids"][0, 2:] == 0).all())
        runtime.close()


class ProtocolTests(unittest.TestCase):
    def test_request_parser_enforces_model_and_greedy_limits(self):
        parsed = _parse_request({"model": MODEL_ID, "messages": [{"role": "user", "content": "你好"}]})
        self.assertEqual(parsed.max_tokens, 128)
        with self.assertRaisesRegex(ValueError, "greedy"):
            _parse_request({"model": MODEL_ID, "messages": [{"role": "user", "content": "x"}], "temperature": 0.2})
        with self.assertRaisesRegex(ValueError, "not available"):
            _parse_request({"model": "other", "messages": [{"role": "user", "content": "x"}]})

    def test_stdlib_server_exposes_json_and_sse(self):
        class FakeRuntime:
            model_id = MODEL_ID
            started = True

            def status(self):
                return {"ready": True, "model": MODEL_ID, "backend": "acl_om", "descriptor_validated": True}

            def close(self):
                self.started = False

            def cancel(self):
                return None

            def complete(self, _messages, _max_tokens):
                return GenerationResult("你好", 2, 1)

            def stream(self, _messages, _max_tokens):
                yield "你", 2, 1
                yield "你好", 2, 2

        runtime = FakeRuntime()
        service = AclOmHttpService(runtime, auto_start=False)
        server = service.make_server("127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            connection = http.client.HTTPConnection(host, port, timeout=3)
            connection.request("GET", "/v1/models")
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["data"][0]["id"], MODEL_ID)
            connection.close()

            connection = http.client.HTTPConnection(host, port, timeout=3)
            body = json.dumps({"model": MODEL_ID, "messages": [{"role": "user", "content": "你好"}]})
            connection.request("POST", "/v1/chat/completions", body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["object"], "chat.completion")
            connection.close()

            connection = http.client.HTTPConnection(host, port, timeout=3)
            body = json.dumps({"model": MODEL_ID, "messages": [{"role": "user", "content": "你好"}], "stream": True, "max_tokens": 2})
            connection.request("POST", "/v1/chat/completions", body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            text = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn("text/event-stream", response.getheader("Content-Type", ""))
            self.assertIn('"content":"你"', text)
            self.assertTrue(text.endswith("data: [DONE]\n\n"))
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
            service.close()

    def test_execution_deadline_on_posix_main_thread(self):
        import signal

        if not hasattr(signal, "SIGALRM") or not hasattr(signal, "setitimer"):
            self.skipTest("POSIX interval timers are unavailable")
        with self.assertRaises(RuntimeExecutionTimeout):
            with _execution_deadline(0.01):
                time.sleep(0.05)

    def test_request_body_timeout_is_total_and_restores_socket(self):
        class FakeConnection:
            def __init__(self):
                self.timeout = 9.0
                self.history = []

            def gettimeout(self):
                return self.timeout

            def settimeout(self, value):
                self.history.append(value)
                self.timeout = value

        class TimedOutStream:
            def read1(self, _size):
                raise TimeoutError("simulated socket deadline")

        connection = FakeConnection()
        with self.assertRaises(RequestError) as raised:
            _read_request_body(
                TimedOutStream(), connection, 4, timeout_seconds=0.01
            )
        self.assertEqual(raised.exception.status_code, 408)
        self.assertEqual(raised.exception.code, "request_timeout")
        self.assertEqual(connection.timeout, 9.0)
        self.assertEqual(connection.history[-1], 9.0)

    def test_request_body_timeout_covers_slow_trickle(self):
        class FakeConnection:
            def __init__(self):
                self.timeout = None

            def gettimeout(self):
                return self.timeout

            def settimeout(self, value):
                self.timeout = value

        class SlowStream:
            def __init__(self):
                self.calls = 0

            def read1(self, _size):
                self.calls += 1
                time.sleep(0.02)
                return b"x"

        connection = FakeConnection()
        stream = SlowStream()
        started = time.monotonic()
        with self.assertRaises(RequestError) as raised:
            _read_request_body(stream, connection, 100, timeout_seconds=0.05)
        elapsed = time.monotonic() - started
        self.assertEqual(raised.exception.status_code, 408)
        self.assertLess(elapsed, 0.15)
        self.assertGreater(stream.calls, 0)
