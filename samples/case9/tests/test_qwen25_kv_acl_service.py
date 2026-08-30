from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import http.client
import json
import sys
import tempfile
import threading
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from qwen25_kv_acl_runtime import (
    BackendStep,
    MODEL_ID,
    NativeQwen25Backend,
    GenerationResult,
    Qwen25AclRuntime,
    RuntimeDescriptor,
    RuntimeRequestError,
    RuntimeUnavailable,
    RuntimeTensorDescriptor,
    detect_soc_version,
    verify_artifact_locks,
    _ends_with_sentence_boundary,
    _write_token_cache,
)
from qwen25_kv_acl_service import (
    CompletionRequest,
    MAX_MESSAGE_CHARACTERS,
    Qwen25StaticKvService,
    _parse_request,
    make_server,
)


class FakeContract:
    model_id = MODEL_ID
    family = "qwen2.5"
    static_sequence_length = 16
    mask_length = 16
    vocabulary_size = 32
    cache_layout = "split"
    cache_shape = (1, 2, 16, 4)
    eos_token_id = 31
    pad_token_id = 0
    bos_token_id = None

    def __init__(self) -> None:
        self.inputs = (
            RuntimeTensorDescriptor("input_ids", "int64", (1, 1), 8),
            RuntimeTensorDescriptor("attention_mask", "int64", (1, 16), 128),
            RuntimeTensorDescriptor("position_ids", "int64", (1, 1), 8),
        )
        cache_inputs = []
        cache_outputs = []
        for index in range(48):
            name = f"cache_in_{index}"
            out_name = f"cache_out_{index}"
            item = SimpleNamespace(name=name, dtype="float32", shape=(1, 2, 16, 4), byte_size=512, cache_index=index, cache_part="key" if index % 2 == 0 else "value")
            output = SimpleNamespace(name=out_name, dtype="float32", shape=(1, 1, 2, 4), byte_size=32, cache_index=index, cache_part=item.cache_part, cache_update="token")
            cache_inputs.append(item)
            cache_outputs.append(output)
        self.inputs = self.inputs + tuple(cache_inputs)
        self.outputs = (SimpleNamespace(name="logits", dtype="float32", shape=(1, 1, 32), byte_size=128, role="logits"),) + tuple(cache_outputs)
        self.cache_inputs = tuple(cache_inputs)
        self.cache_outputs = tuple(cache_outputs)
        self.logits_output = self.outputs[0]
        self.logits_output_index = 0

    def validate_static_expectations(self) -> None:
        return None

    def validate_descriptor(self, inputs, outputs) -> None:
        return None


class FakeTokenizer:
    vocab_size = 32
    eos_token_id = 31
    pad_token_id = 0

    def encode_messages(self, messages):
        return [1, 2]

    def decode(self, ids):
        return "".join({5: "你", 31: "好"}.get(int(value), "?") for value in ids)


class FakeBackend:
    supports_request_reuse = False
    supports_device_cache_update = False

    def __init__(self) -> None:
        self.calls = 0
        self.opened = False
        self.cache_snapshots = []

    def open(self, model_path: Path) -> RuntimeDescriptor:
        self.opened = True
        return RuntimeDescriptor(
            tuple(self._descriptor(item) for item in FakeContract().inputs),
            tuple(self._descriptor(item) for item in FakeContract().outputs),
        )

    @staticmethod
    def _descriptor(item):
        return RuntimeTensorDescriptor(item.name, item.dtype, tuple(item.shape), item.byte_size)

    def run(self, inputs):
        self.calls += 1
        self.cache_snapshots.append({name: np.array(value, copy=True) for name, value in inputs.items() if name.startswith("cache_in_")})
        logits = np.full((1, 1, 32), -100.0, dtype=np.float32)
        logits[0, 0, 5 if self.calls <= 2 else 31] = 100.0
        outputs = {"logits": logits}
        for index in range(48):
            outputs[f"cache_out_{index}"] = np.full((1, 1, 2, 4), self.calls, dtype=np.float32)
        return outputs

    def close(self):
        self.opened = False


class DeviceResidentBackend(FakeBackend):
    """Backend double for the native logits-only D2D cache path."""

    supports_request_reuse = True
    supports_device_cache_update = True

    def begin_request(self, cache_values):
        self.initial_cache = {
            name: np.array(value, copy=True) for name, value in cache_values.items()
        }
        return object()

    def run_request_step(self, request, base_inputs, real_length, cache_values):
        self.calls += 1
        # A successful device-resident path must not require a host cache
        # update to feed the next token.
        self.cache_snapshots.append(
            {name: np.array(value, copy=True) for name, value in cache_values.items()}
        )
        logits = np.full((1, 1, 32), -100.0, dtype=np.float32)
        logits[0, 0, 5 if self.calls <= 2 else 31] = 100.0
        return BackendStep({"logits": logits}, cache_device_updated=True)

    def end_request(self, request):
        return None


class RuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.om = root / "model.om"
        self.tokenizer = root / "tokenizer.json"
        self.om.write_bytes(b"om")
        self.tokenizer.write_bytes(b"{}")
        self.backend = FakeBackend()
        self.runtime = Qwen25AclRuntime(self.om, self.tokenizer, contract=FakeContract(), tokenizer=FakeTokenizer(), backend=self.backend, max_tokens=4, require_artifact_locks=False)
        self.runtime.start()

    def tearDown(self) -> None:
        self.runtime.close()
        self.temp.cleanup()

    def test_split_cache_is_token_updated_and_reset_per_request(self) -> None:
        events = list(self.runtime.stream([{"role": "user", "content": "你好"}], 2))
        self.assertEqual(events[-1].finish_reason, "stop")
        self.assertEqual(events[-1].text, "你好")
        self.assertEqual(self.backend.calls, 3)  # two prompt tokens + one decode step
        first_cache = self.backend.cache_snapshots[2]["cache_in_0"]
        self.assertTrue(np.all(first_cache[:, :, 0, :] == 1))
        list(self.runtime.stream([{"role": "user", "content": "你好"}], 1))
        reset_cache = self.backend.cache_snapshots[3]["cache_in_0"]
        self.assertTrue(np.all(reset_cache == 0))

    def test_budget_and_cancel_are_strict(self) -> None:
        with self.assertRaises(RuntimeRequestError):
            list(self.runtime.stream([{"role": "user", "content": "x"}], 5))
        # A cancellation flag from a previous connection is cleared at the
        # start of a new request; cancellation during an active request is
        # exercised by the HTTP disconnect path.
        self.runtime.cancel()
        events = list(self.runtime.stream([{"role": "user", "content": "x"}], 1))
        self.assertTrue(events[-1].done)

    def test_sentence_boundary_helper_ignores_trailing_space(self) -> None:
        self.assertTrue(_ends_with_sentence_boundary("完整回答。  \n"))
        self.assertFalse(_ends_with_sentence_boundary("未完成回答"))

    def test_default_budget_stops_at_first_sentence_after_minimum(self) -> None:
        # Keep this policy test independent of ACL graph execution: the
        # hardware path is covered by the board smoke, while this verifies
        # that an 80-token allowance does not force slow follow-up sentences.
        self.runtime.contract.static_sequence_length = 128
        self.runtime.contract.mask_length = 128
        self.runtime.max_tokens = 80
        self.runtime._encode_messages = lambda messages: [1]  # type: ignore[method-assign]
        self.runtime._decode = lambda ids: ("x" * len(ids) if len(ids) < 16 else "x" * 15 + "。")  # type: ignore[method-assign]
        logits = np.full((32,), -100.0, dtype=np.float32)
        logits[5] = 100.0
        with patch.object(self.runtime, "_run_step", return_value=(None, self.runtime._new_cache(), logits)):
            events = list(self.runtime.stream([{"role": "user", "content": "x"}], 80))
        self.assertEqual(events[-1].completion_tokens, 16)
        self.assertEqual(events[-1].finish_reason, "stop")
        self.assertTrue(events[-1].text.endswith("。"))

    def test_token_cache_layout_transposes_singleton_sequence_axis(self) -> None:
        current = np.zeros((1, 2, 4, 3), dtype=np.float32)
        token = np.arange(6, dtype=np.float32).reshape(1, 1, 2, 3)
        updated = _write_token_cache(current, token, 2)
        np.testing.assert_array_equal(updated[0, :, 2, :], token[0, 0, :, :])

    def test_qwen_reserved_vocab_rows_do_not_block_tokenizer_startup(self) -> None:
        contract = type("Contract", (), {
            "vocabulary_size": 151936,
            "eos_token_id": 151645,
            "pad_token_id": 151643,
        })()
        tokenizer = type("Tokenizer", (), {
            "vocab_size": 151665,
            "eos_token_id": 151645,
            "pad_token_id": 151643,
        })()
        Qwen25AclRuntime._validate_tokenizer(tokenizer, contract)
        tokenizer.vocab_size = 151937
        with self.assertRaises(RuntimeUnavailable):
            Qwen25AclRuntime._validate_tokenizer(tokenizer, contract)

    def test_tokenizer_bos_id_must_match_contract(self) -> None:
        contract = type("Contract", (), {
            "vocabulary_size": 151936,
            "eos_token_id": 151645,
            "pad_token_id": 151643,
            "bos_token_id": None,
        })()
        tokenizer = type("Tokenizer", (), {
            "vocab_size": 151665,
            "eos_token_id": 151645,
            "pad_token_id": 151643,
            "bos_token_id": 151642,
        })()
        with self.assertRaises(RuntimeUnavailable):
            Qwen25AclRuntime._validate_tokenizer(tokenizer, contract)

    def test_device_resident_cache_accepts_logits_only_without_host_cache_updates(self) -> None:
        backend = DeviceResidentBackend()
        runtime = Qwen25AclRuntime(
            self.om,
            self.tokenizer,
            contract=FakeContract(),
            tokenizer=FakeTokenizer(),
            backend=backend,
            max_tokens=4,
            require_artifact_locks=False,
        )
        runtime.start()
        try:
            events = list(runtime.stream([{"role": "user", "content": "你好"}], 2))
        finally:
            runtime.close()
        self.assertEqual(events[-1].text, "你好")
        self.assertEqual(backend.calls, 3)
        self.assertTrue(np.all(backend.initial_cache["cache_in_0"] == 0))
        self.assertTrue(np.all(backend.cache_snapshots[-1]["cache_in_0"] == 0))

    def test_runtime_defaults_to_strict_artifact_locks(self) -> None:
        """Direct runtime construction cannot silently bypass launcher locks."""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            om = root / "model.om"
            tokenizer = root / "tokenizer.json"
            om.write_bytes(b"om")
            tokenizer.write_bytes(b"{}")
            runtime = Qwen25AclRuntime(
                om,
                tokenizer,
                contract=FakeContract(),
                tokenizer=FakeTokenizer(),
                backend=FakeBackend(),
            )
            self.assertTrue(runtime.require_artifact_locks)
            with self.assertRaisesRegex(RuntimeUnavailable, "OM lock file is required"):
                runtime.start()
            runtime.close()

    def test_watchdog_marks_runtime_unhealthy_and_cancels_request(self) -> None:
        runtime = Qwen25AclRuntime(
            self.om,
            self.tokenizer,
            contract=FakeContract(),
            tokenizer=FakeTokenizer(),
            backend=self.backend,
            max_tokens=1,
            execution_timeout_seconds=0.01,
            require_artifact_locks=False,
        )
        runtime.start()
        try:
            # Force a deadline that is already expired; the watchdog is
            # exercised directly without blocking an ACL call in the test.
            runtime._watchdog_triggered = True
            runtime._runtime_error = "ACL request exceeded the hard execution deadline"
            with self.assertRaisesRegex(Exception, "deadline"):
                runtime._check_cancelled()
            self.assertFalse(runtime.started)
            self.assertTrue(runtime.status()["watchdog_triggered"])
        finally:
            runtime.close()


class ServiceTests(unittest.TestCase):
    def test_parser_and_sse_contract(self) -> None:
        request = _parse_request({"model": MODEL_ID, "messages": [{"role": "user", "content": "你好"}], "stream": True, "max_tokens": 2})
        self.assertIsInstance(request, CompletionRequest)
        default_request = _parse_request(
            {"model": MODEL_ID, "messages": [{"role": "user", "content": "你好"}]}
        )
        self.assertEqual(default_request.max_tokens, 80)
        with self.assertRaises(RuntimeRequestError):
            _parse_request({"model": MODEL_ID, "messages": [{"role": "user", "content": "x"}], "max_tokens": 81})
        with self.assertRaisesRegex(RuntimeRequestError, "preflight character limit"):
            _parse_request(
                {
                    "model": MODEL_ID,
                    "messages": [{"role": "user", "content": "x" * (MAX_MESSAGE_CHARACTERS + 1)}],
                    "max_tokens": 1,
                }
            )

        class Runtime:
            def status(self):
                return {"ready": True, "model": MODEL_ID}

            def stream(self, messages, max_tokens):
                yield GenerationResult("你", 2, 1, "in_progress", False)
                yield GenerationResult("你好", 2, 2, "stop", True)

        chunks = list(Qwen25StaticKvService(Runtime()).stream(request))
        self.assertEqual(chunks[0]["choices"][0]["delta"].get("content"), "你")
        self.assertEqual(chunks[1]["choices"][0]["delta"].get("content"), "好")
        self.assertEqual(chunks[-1]["choices"][0]["finish_reason"], "stop")
        self.assertEqual(chunks[-1]["usage"], {"prompt_tokens": 2, "completion_tokens": 2, "total_tokens": 4})

    def test_non_prefix_snapshot_never_repeats_delta(self) -> None:
        from qwen25_kv_acl_service import _text_delta

        self.assertEqual(_text_delta("你好", "你世界"), "")
        self.assertEqual(_text_delta("你", "你好"), "好")

    def test_artifact_lock_checks_bytes_hash_and_contract(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            om = base / "model.om"
            tokenizer = base / "tokenizer.json"
            contract = base / "contract.json"
            om.write_bytes(b"model")
            tokenizer.write_bytes(b"tokens")
            contract.write_text("{}", encoding="utf-8")
            import hashlib

            lock = base / "model.om.lock.json"
            tokenizer_lock = base / "tokenizer.json.lock.json"
            lock.write_text(
                json.dumps({
                    "bytes": om.stat().st_size,
                    "sha256": hashlib.sha256(om.read_bytes()).hexdigest(),
                    "soc_version": "Ascend310B4",
                    "contract_sha256": hashlib.sha256(contract.read_bytes()).hexdigest(),
                }),
                encoding="utf-8",
            )
            tokenizer_lock.write_text(
                json.dumps({"bytes": tokenizer.stat().st_size, "sha256": hashlib.sha256(tokenizer.read_bytes()).hexdigest()}),
                encoding="utf-8",
            )
            status = verify_artifact_locks(
                om,
                tokenizer,
                contract,
                lock_path=lock,
                tokenizer_lock_path=tokenizer_lock,
                expected_soc_version="Ascend310B4",
            )
            self.assertTrue(status["verified"])
            with self.assertRaisesRegex(RuntimeUnavailable, "SoC mismatch"):
                verify_artifact_locks(
                    om,
                    tokenizer,
                    contract,
                    lock_path=lock,
                    tokenizer_lock_path=tokenizer_lock,
                    expected_soc_version="Ascend310B1",
                )
            cross_status = verify_artifact_locks(
                om,
                tokenizer,
                contract,
                lock_path=lock,
                tokenizer_lock_path=tokenizer_lock,
                expected_soc_version="Ascend310B1",
                allow_cross_soc=True,
            )
            self.assertTrue(cross_status["verified"])
            self.assertTrue(cross_status["compatibility_experiment"])
            self.assertEqual(cross_status["soc_version"], "Ascend310B4")
            self.assertEqual(cross_status["board_soc_version"], "Ascend310B1")
            om.write_bytes(b"tampered")
            with self.assertRaises(RuntimeUnavailable):
                verify_artifact_locks(
                    om,
                    tokenizer,
                    contract,
                    lock_path=lock,
                    tokenizer_lock_path=tokenizer_lock,
                    expected_soc_version="Ascend310B4",
                )

    def test_artifact_lock_uses_runtime_contract_sha_when_present(self) -> None:
        """A B1 lock can preserve export and runtime descriptor hashes."""

        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            om = base / "model.om"
            tokenizer = base / "tokenizer.json"
            controller_contract = base / "controller-contract.json"
            runtime_contract = base / "om-contract.json"
            om.write_bytes(b"model")
            tokenizer.write_bytes(b"tokens")
            controller_contract.write_text('{"kind":"controller"}', encoding="utf-8")
            runtime_contract.write_text('{"kind":"runtime"}', encoding="utf-8")
            import hashlib

            lock = base / "model.om.lock.json"
            tokenizer_lock = base / "tokenizer.json.lock.json"
            lock.write_text(
                json.dumps({
                    "bytes": om.stat().st_size,
                    "sha256": hashlib.sha256(om.read_bytes()).hexdigest(),
                    "soc_version": "Ascend310B1",
                    "contract_sha256": hashlib.sha256(controller_contract.read_bytes()).hexdigest(),
                    "runtime_contract_sha256": hashlib.sha256(runtime_contract.read_bytes()).hexdigest(),
                }),
                encoding="utf-8",
            )
            tokenizer_lock.write_text(
                json.dumps({"bytes": tokenizer.stat().st_size, "sha256": hashlib.sha256(tokenizer.read_bytes()).hexdigest()}),
                encoding="utf-8",
            )

            status = verify_artifact_locks(
                om,
                tokenizer,
                runtime_contract,
                lock_path=lock,
                tokenizer_lock_path=tokenizer_lock,
                expected_soc_version="Ascend310B1",
            )
            self.assertTrue(status["verified"])
            self.assertEqual(status["contract"]["lock_field"], "runtime_contract_sha256")
            self.assertEqual(status["runtime_contract_sha256"], hashlib.sha256(runtime_contract.read_bytes()).hexdigest())
            self.assertEqual(
                status["controller_contract_sha256"],
                hashlib.sha256(controller_contract.read_bytes()).hexdigest(),
            )
            tampered_lock = json.loads(lock.read_text(encoding="utf-8"))
            tampered_lock["controller_contract_sha256"] = "0" * 64
            lock.write_text(json.dumps(tampered_lock), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeUnavailable, "controller contract SHA-256 fields disagree"):
                verify_artifact_locks(
                    om,
                    tokenizer,
                    runtime_contract,
                    lock_path=lock,
                    tokenizer_lock_path=tokenizer_lock,
                    expected_soc_version="Ascend310B1",
                )
            lock.write_text(
                json.dumps({
                    "bytes": om.stat().st_size,
                    "sha256": hashlib.sha256(om.read_bytes()).hexdigest(),
                    "soc_version": "Ascend310B1",
                    "contract_sha256": hashlib.sha256(controller_contract.read_bytes()).hexdigest(),
                    "runtime_contract_sha256": hashlib.sha256(runtime_contract.read_bytes()).hexdigest(),
                }),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(RuntimeUnavailable, "runtime descriptor contract"):
                verify_artifact_locks(
                    om,
                    tokenizer,
                    controller_contract,
                    lock_path=lock,
                    tokenizer_lock_path=tokenizer_lock,
                    expected_soc_version="Ascend310B1",
                )

    def test_http_json_and_sse_endpoints(self) -> None:
        class Runtime:
            def status(self):
                return {"ready": True, "model": MODEL_ID}

            def complete(self, messages, max_tokens):
                return GenerationResult("你好", 2, 2, "stop", True)

            def stream(self, messages, max_tokens):
                yield GenerationResult("你", 2, 1, "in_progress", False)
                yield GenerationResult("你好", 2, 2, "stop", True)

            def cancel(self):
                return None

        server = make_server("127.0.0.1", 0, Qwen25StaticKvService(Runtime()))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            body = json.dumps({"model": MODEL_ID, "messages": [{"role": "user", "content": "x"}]})
            connection = http.client.HTTPConnection(host, port, timeout=3)
            connection.request("POST", "/v1/chat/completions", body, {"Content-Type": "application/json"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            self.assertEqual(json.loads(response.read())["choices"][0]["message"]["content"], "你好")
            connection.close()

            body = json.dumps({"model": MODEL_ID, "messages": [{"role": "user", "content": "x"}], "stream": True, "max_tokens": 2})
            connection = http.client.HTTPConnection(host, port, timeout=3)
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

    def test_models_endpoint_fails_closed_when_runtime_is_not_ready(self) -> None:
        class Runtime:
            def status(self):
                return {"ready": False, "model": MODEL_ID, "runtime_error": "load failed"}

            def cancel(self):
                return None

        server = make_server("127.0.0.1", 0, Qwen25StaticKvService(Runtime()))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            connection = http.client.HTTPConnection(host, port, timeout=3)
            connection.request("GET", "/v1/models")
            response = connection.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.status, 503)
            self.assertEqual(payload["error"]["code"], "model_unavailable")
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


class NativeBackendTests(unittest.TestCase):
    def test_soc_detector_accepts_bare_npu_smi_chip_name(self) -> None:
        completed = SimpleNamespace(returncode=0, stdout="| 0       310B4 | Alarm |", stderr="")
        with patch("qwen25_kv_acl_runtime.subprocess.run", return_value=completed):
            self.assertEqual(detect_soc_version(), "Ascend310B4")

    def test_cann_abi_d2d_fallback_is_used_when_python_symbol_is_missing(self) -> None:
        backend = NativeQwen25Backend()
        backend.acl = SimpleNamespace(
            ACL_MEMCPY_HOST_TO_DEVICE=1,
            ACL_MEMCPY_DEVICE_TO_HOST=2,
            rt=SimpleNamespace(
                ACL_MEMCPY_HOST_TO_DEVICE=1,
                ACL_MEMCPY_DEVICE_TO_HOST=2,
            ),
        )
        self.assertEqual(backend._device_to_device_memcpy_kind(), 3)

    def test_cann_abi_d2d_fallback_rejects_incompatible_binding_values(self) -> None:
        backend = NativeQwen25Backend()
        backend.acl = SimpleNamespace(
            ACL_MEMCPY_HOST_TO_DEVICE=7,
            ACL_MEMCPY_DEVICE_TO_HOST=2,
            rt=SimpleNamespace(),
        )
        self.assertIsNone(backend._device_to_device_memcpy_kind())


class LauncherTests(unittest.TestCase):
    def test_launcher_default_max_tokens_matches_admitted_qwen_limit(self) -> None:
        launcher_path = Path(__file__).resolve().parents[1] / "scripts" / "serve_qwen25_kv_acl.py"
        spec = importlib.util.spec_from_file_location("qwen25_kv_acl_launcher_test", launcher_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        previous_argv = sys.argv
        previous_value = os.environ.pop("QWEN25_KV_MAX_TOKENS", None)
        try:
            sys.argv = [str(launcher_path)]
            parsed = module.parse_args()
            self.assertEqual(parsed.max_tokens, 80)
            self.assertFalse(parsed.allow_unlocked_artifacts)
            self.assertFalse(parsed.compatibility_experiment)
        finally:
            sys.argv = previous_argv
            if previous_value is not None:
                os.environ["QWEN25_KV_MAX_TOKENS"] = previous_value

    def test_dirty_base_requires_explicit_override(self) -> None:
        """20T base may be used only with the documented explicit override."""

        launcher_path = Path(__file__).resolve().parents[1] / "scripts" / "serve_qwen25_kv_acl.py"
        spec = importlib.util.spec_from_file_location("qwen25_kv_acl_launcher_dirty_base_test", launcher_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            fake_python = prefix / "bin" / "python"
            fake_python.parent.mkdir()
            fake_python.touch()
            base_environment = {
                "CONDA_PREFIX": str(prefix),
                "CONDA_DEFAULT_ENV": "base",
                "CASE9_QWEN25_KV_ALLOW_DIRTY_BASE": "1",
            }
            with (
                patch.dict(os.environ, base_environment, clear=False),
                patch.object(module.sys, "version_info", (3, 9, 0)),
                patch.object(module.sys, "prefix", str(prefix)),
                patch.object(module.sys, "executable", str(fake_python)),
                patch.object(module.importlib.util, "find_spec", return_value=object()),
                patch.object(module, "detect_soc_version", return_value="Ascend310B1"),
                patch.dict(sys.modules, {"acl": types.ModuleType("acl")}),
            ):
                self.assertEqual(module._check_board_environment(), "Ascend310B1")

            blocked_environment = dict(base_environment)
            blocked_environment["CASE9_QWEN25_KV_ALLOW_DIRTY_BASE"] = "0"
            with (
                patch.dict(os.environ, blocked_environment, clear=False),
                patch.object(module.sys, "version_info", (3, 9, 0)),
                patch.object(module.sys, "prefix", str(prefix)),
                patch.object(module.sys, "executable", str(fake_python)),
                patch.object(module.importlib.util, "find_spec", return_value=object()),
                patch.object(module, "detect_soc_version", return_value="Ascend310B1"),
                patch.dict(sys.modules, {"acl": types.ModuleType("acl")}),
            ):
                with self.assertRaisesRegex(SystemExit, "forbidden board package"):
                    module._check_board_environment()

    def test_provisioning_records_runtime_descriptor_contract_after_smoke(self) -> None:
        source = (Path(__file__).resolve().parents[1] / "scripts" / "provision_qwen25_kv102_board.sh").read_text(encoding="utf-8")
        self.assertIn("record_runtime_contract_lock()", source)
        self.assertIn('"runtime_contract_sha256": runtime_sha', source)
        self.assertIn("record_runtime_contract_lock", source[source.index("smoke()") : source.index("serve()")])
        self.assertIn('"controller_contract_sha256": contract_sha', source)


if __name__ == "__main__":
    unittest.main()
