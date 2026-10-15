from __future__ import annotations

import hashlib
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

from qwen25_acl_contract import (
    QWEN25_SUPPORTED_EXECUTION_MODE,
    QWEN25_SUPPORTED_LAST_LOGITS_EXECUTION_MODE,
)
from qwen25_acl_runtime import GenerationResult
from qwen25_legacy_compat_service import (
    ArtifactDigest,
    ArtifactPaths,
    CompatibilityAclService,
    CompatibilityError,
    CompatibilityProfile,
    CompatibilityRequestError,
    MODEL_ID,
    SOURCE_SOC,
    TARGET_SOC,
    WatchdogState,
    _Notifier,
    _parse_request,
    _text_delta,
    make_server,
    validate_experiment_request,
    verify_pinned_artifacts,
)


def _digest(path: Path):
    value = path.read_bytes()
    return {"bytes": len(value), "sha256": hashlib.sha256(value).hexdigest()}


def _contract(mode: str) -> dict:
    sequence = 2048
    vocab = 8
    output_shape = [1, 1, vocab] if mode == QWEN25_SUPPORTED_LAST_LOGITS_EXECUTION_MODE else [1, sequence, vocab]
    return {
        "schema_version": 1,
        "model": {
            "family": "qwen2.5",
            "model_id": MODEL_ID,
            "eos_token_id": 1,
            "pad_token_id": 1,
            "bos_token_id": 0,
        },
        "acl_om": {
            "execution_mode": mode,
            "static_sequence_length": sequence,
            "input_dtype": "int64",
            "logits_dtype": "float16",
            "input_order": ["input_ids", "attention_mask", "position_ids"],
            "input_order_verified": True,
            "inputs": [
                {"name": name, "dtype": "int64", "shape": [1, sequence], "byte_size": 16384, "role": "input"}
                for name in ("input_ids", "attention_mask", "position_ids")
            ],
            "outputs": [
                {
                    "name": "last_logits" if mode == QWEN25_SUPPORTED_LAST_LOGITS_EXECUTION_MODE else "logits",
                    "dtype": "float16",
                    "shape": output_shape,
                    "byte_size": 2 * output_shape[0] * output_shape[1] * output_shape[2],
                    "role": "logits",
                }
            ],
            "vocabulary_size": vocab,
            "operator_audit": {"opset": 17, "unsupported_operators": []},
        },
    }


class ArtifactAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.om = self.root / "model.om"
        self.contract = self.root / "contract.json"
        self.tokenizer = self.root / "tokenizer.json"
        self.config = self.root / "tokenizer_config.json"
        self.om.write_bytes(b"fixture-om")
        self.contract.write_text(json.dumps(_contract(QWEN25_SUPPORTED_EXECUTION_MODE)), encoding="utf-8")
        self.tokenizer.write_text("{}", encoding="utf-8")
        self.config.write_text("{}", encoding="utf-8")
        self.paths = ArtifactPaths(self.om, self.contract, self.tokenizer, self.config)
        self.profile = CompatibilityProfile(
            key="fixture-full",
            source_soc=SOURCE_SOC,
            target_soc=TARGET_SOC,
            execution_mode=QWEN25_SUPPORTED_EXECUTION_MODE,
            artifacts={
                name: ArtifactDigest(value["bytes"], value["sha256"])
                for name, value in {
                    "om": _digest(self.om),
                    "contract": _digest(self.contract),
                    "tokenizer": _digest(self.tokenizer),
                    "tokenizer_config": _digest(self.config),
                }.items()
            },
        )
        self.manifest = self.root / "pins.json"
        self._write_manifest()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _write_manifest(self):
        self.manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "profile": self.profile.key,
                    "source_soc": SOURCE_SOC,
                    "board_soc": TARGET_SOC,
                    "artifacts": {name: {"bytes": item.bytes, "sha256": item.sha256} for name, item in self.profile.artifacts.items()},
                }
            ),
            encoding="utf-8",
        )

    def test_verifies_all_pinned_artifacts_and_contract(self):
        binding = verify_pinned_artifacts(self.profile, self.paths, self.manifest)
        self.assertTrue(binding.public_status()["artifact_pins_verified"])
        self.assertEqual(binding.public_status()["source_soc"], SOURCE_SOC)
        self.assertEqual(binding.public_status()["board_soc"], TARGET_SOC)

    def test_rejects_manifest_sha_or_local_file_mismatch(self):
        document = json.loads(self.manifest.read_text(encoding="utf-8"))
        document["artifacts"]["om"]["sha256"] = "0" * 64
        self.manifest.write_text(json.dumps(document), encoding="utf-8")
        with self.assertRaisesRegex(CompatibilityError, "fixed historical identity"):
            verify_pinned_artifacts(self.profile, self.paths, self.manifest)

        self._write_manifest()
        self.om.write_bytes(b"changed")
        with self.assertRaisesRegex(CompatibilityError, "byte/SHA-256"):
            verify_pinned_artifacts(self.profile, self.paths, self.manifest)

    def test_requires_explicit_cross_soc_and_dirty_base_opt_in(self):
        with self.assertRaisesRegex(CompatibilityError, "compatibility-experiment"):
            validate_experiment_request(
                self.profile,
                compatibility_experiment=False,
                source_soc=SOURCE_SOC,
                board_soc=TARGET_SOC,
                observed_board_soc=TARGET_SOC,
                allow_dirty_base=True,
            )
        with self.assertRaisesRegex(CompatibilityError, "allow-dirty-base"):
            validate_experiment_request(
                self.profile,
                compatibility_experiment=True,
                source_soc=SOURCE_SOC,
                board_soc=TARGET_SOC,
                observed_board_soc=TARGET_SOC,
                allow_dirty_base=False,
            )
        with self.assertRaisesRegex(CompatibilityError, "observed board SoC"):
            validate_experiment_request(
                self.profile,
                compatibility_experiment=True,
                source_soc=SOURCE_SOC,
                board_soc=TARGET_SOC,
                observed_board_soc=SOURCE_SOC,
                allow_dirty_base=True,
            )


class WatchdogTests(unittest.TestCase):
    def test_nested_request_and_execute_deadlines_are_tracked_independently(self):
        state = WatchdogState(startup_timeout=5, execute_timeout=2, request_timeout=8)
        state.consume({"event": "begin", "id": 1, "phase": "request"}, now=10.0)
        state.consume({"event": "begin", "id": 2, "phase": "acl_execute"}, now=11.0)
        self.assertIsNone(state.expired(12.9))
        self.assertEqual(state.expired(13.1), "acl_execute")
        state.consume({"event": "end", "id": 2, "phase": "acl_execute"}, now=13.1)
        self.assertIsNone(state.expired(17.9))
        self.assertEqual(state.expired(18.1), "request")

    def test_ready_and_fatal_events_do_not_make_worker_appear_healthy(self):
        state = WatchdogState(startup_timeout=5, execute_timeout=2, request_timeout=8)
        state.consume({"event": "ready"}, now=1.0)
        self.assertTrue(state.ready)
        state.consume({"event": "fatal", "message": "ACL init failed"}, now=2.0)
        self.assertEqual(state.fatal, "ACL init failed")


class CompatibilityProtocolTests(unittest.TestCase):
    class _Runtime:
        started = True

        def status(self):
            return {"ready": True, "model": MODEL_ID, "backend": "acl_om", "descriptor_validated": True}

        def complete(self, _messages, _max_tokens):
            return GenerationResult("你好", 3, 2, "length")

        def stream(self, _messages, _max_tokens):
            yield GenerationResult("你", 3, 1, "length")
            yield GenerationResult("你好", 3, 2, "stop")

        def cancel(self):
            return None

    @staticmethod
    def _binding():
        profile = CompatibilityProfile(
            key="fixture-service",
            source_soc=SOURCE_SOC,
            target_soc=TARGET_SOC,
            execution_mode=QWEN25_SUPPORTED_EXECUTION_MODE,
            artifacts={name: ArtifactDigest(1, "a" * 64) for name in ("om", "contract", "tokenizer", "tokenizer_config")},
        )
        from qwen25_legacy_compat_service import ArtifactBinding

        return ArtifactBinding(profile, profile.artifacts)

    def test_single_thread_http_service_marks_cross_soc_health_and_sse_deltas(self):
        service = CompatibilityAclService(self._Runtime(), self._binding(), _Notifier())
        server = make_server("127.0.0.1", 0, service)
        self.assertNotIn("Threading", type(server).__name__)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            host, port = server.server_address
            connection = http.client.HTTPConnection(host, port, timeout=3)
            connection.request("GET", "/health")
            response = connection.getresponse()
            health = json.loads(response.read())
            self.assertEqual(response.status, 200)
            self.assertTrue(health["compatibility_experiment"])
            self.assertEqual(health["source_soc"], SOURCE_SOC)
            self.assertEqual(health["board_soc"], TARGET_SOC)
            connection.close()

            payload = {"model": MODEL_ID, "messages": [{"role": "user", "content": "你好"}], "stream": True, "max_tokens": 2}
            connection = http.client.HTTPConnection(host, port, timeout=3)
            connection.request("POST", "/v1/chat/completions", json.dumps(payload), {"Content-Type": "application/json"})
            response = connection.getresponse()
            body = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            self.assertIn('"content":"你"', body)
            self.assertIn('"content":"好"', body)
            self.assertNotIn('"content":"你好"', body)
            self.assertTrue(body.endswith("data: [DONE]\n\n"))
            connection.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)

    def test_parser_and_delta_are_bounded(self):
        request = _parse_request({"model": MODEL_ID, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1})
        self.assertEqual(request.max_tokens, 1)
        with self.assertRaises(CompatibilityRequestError):
            _parse_request({"model": MODEL_ID, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 9})
        self.assertEqual(_text_delta("你", "你好"), "好")
        self.assertEqual(_text_delta("你好", "你世界"), "")


if __name__ == "__main__":
    unittest.main()
