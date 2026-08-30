from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
import numpy as np

from qwen25_acl_runtime import (
    GenerationResult,
    Qwen25AclRuntime,
    RuntimeDescriptor,
    RuntimeRequestError,
    TensorDescriptor,
)
from qwen25_acl_service import (
    CompletionRequest,
    MODEL_ID,
    Qwen25AclService,
    _parse_request,
    _text_delta,
)


class FakeBackend:
    def __init__(self, sequence=8, vocab=151936):
        self.sequence = sequence
        self.vocab = vocab
        self.calls = 0

    def open(self, path):
        return RuntimeDescriptor(
            tuple(TensorDescriptor(name, "int64", (1, self.sequence), 8 * self.sequence)
                  for name in ("input_ids", "attention_mask", "position_ids")),
            (TensorDescriptor("logits", "float16", (1, self.sequence, self.vocab), 2 * self.sequence * self.vocab),),
        )

    def run(self, inputs):
        self.calls += 1
        length = int(inputs["attention_mask"].sum())
        output = np.full((1, self.sequence, self.vocab), -1000, dtype=np.float16)
        output[0, length - 1, 151645] = 1000
        return output

    def close(self):
        pass


class Qwen25RuntimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(r"C:\Users\zhoux\case9-qwen25-build")
        cls.contract = cls.root / "reports" / "qwen25-full-contract-128.json"
        cls.om = cls.root / "onnx-fp16" / "qwen25-static-128.onnx"
        cls.tokenizer = cls.root / "model" / "tokenizer.json"
        cls.config = cls.root / "model" / "tokenizer_config.json"
        if not cls.contract.is_file():
            raise unittest.SkipTest("external Qwen artifact not available")

    def test_fake_backend_executes_and_resets(self):
        backend = FakeBackend(sequence=128)
        runtime = Qwen25AclRuntime(
            self.om, self.tokenizer, contract_path=self.contract,
            tokenizer_config_path=self.config, backend=backend, max_tokens=2,
        )
        runtime.start()
        result = runtime.complete([{"role": "user", "content": "你好"}], 2)
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.completion_tokens, 1)
        self.assertEqual(backend.calls, 1)
        runtime.close()

    def test_request_parser_is_greedy_and_model_bound(self):
        req = _parse_request({"model": MODEL_ID, "messages": [{"role": "user", "content": "你好"}], "max_tokens": 2})
        self.assertEqual(req.model, MODEL_ID)
        with self.assertRaises(RuntimeRequestError):
            _parse_request({"model": MODEL_ID, "messages": [{"role": "user", "content": "x"}], "temperature": 0.2})

    def test_cancel_is_cleared_for_next_request(self):
        backend = FakeBackend(sequence=128)
        runtime = Qwen25AclRuntime(
            self.om, self.tokenizer, contract_path=self.contract,
            tokenizer_config_path=self.config, backend=backend, max_tokens=2,
        )
        runtime.start()
        runtime.cancel()
        result = runtime.complete([{"role": "user", "content": "你好"}], 1)
        self.assertEqual(result.finish_reason, "stop")
        self.assertEqual(result.completion_tokens, 1)
        runtime.close()


class Qwen25ServiceTests(unittest.TestCase):
    class _Runtime:
        def status(self):
            return {"ready": True, "model": MODEL_ID}

        def complete(self, messages, max_tokens):
            return GenerationResult("你好", 2, 2, "stop")

        def stream(self, messages, max_tokens):
            yield GenerationResult("你", 2, 1, "length")
            yield GenerationResult("你好", 2, 2, "length")

        def cancel(self):
            pass

    def test_sse_uses_only_new_text_and_actual_finish_reason(self):
        service = Qwen25AclService(self._Runtime())
        request = CompletionRequest(MODEL_ID, [{"role": "user", "content": "x"}], True, 2)
        chunks = list(service.stream(request))
        self.assertEqual(chunks[0]["choices"][0]["delta"], {"role": "assistant", "content": "你"})
        self.assertEqual(chunks[1]["choices"][0]["delta"], {"content": "好"})
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "length")

    def test_sse_text_delta_handles_tokenizer_boundary_revision(self):
        self.assertEqual(_text_delta("你好", "你好"), "")
        self.assertEqual(_text_delta("你", "你好"), "好")
        self.assertEqual(_text_delta("你好", "你世界"), "你世界")

    def test_parser_rejects_non_numeric_sampling_values(self):
        base = {"model": MODEL_ID, "messages": [{"role": "user", "content": "x"}]}
        for key, value in (("temperature", "zero"), ("top_p", None), ("top_p", float("nan")), ("top_p", float("inf"))):
            payload = dict(base)
            payload[key] = value
            if value is None:
                continue
            with self.assertRaises(RuntimeRequestError):
                _parse_request(payload)


if __name__ == "__main__":
    unittest.main()
