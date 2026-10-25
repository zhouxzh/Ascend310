from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ascend_edge_chat.static_kv import launch
from ascend_edge_chat.static_kv.contract import (
    QWEN25_INPUT_ORDER,
    QWEN25_MODEL_ID,
    QWEN25_OUTPUT_ORDER,
    Qwen25Contract,
)
from ascend_edge_chat.static_kv.launch import (
    StaticKvLaunchError,
    validate_release_environment,
    validate_release_paths,
)
from ascend_edge_chat.static_kv.runtime import (
    RuntimeDescriptor,
    RuntimeTensorDescriptor,
    RuntimeUnavailable,
    verify_artifact_locks,
)
from ascend_edge_chat.static_kv.service import Qwen25StaticKvService, _parse_request, make_server


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _descriptors() -> RuntimeDescriptor:
    inputs = [
        RuntimeTensorDescriptor("input_ids", "int64", (1, 1), 8),
        RuntimeTensorDescriptor("attention_mask", "int64", (1, 1024), 8192),
        RuntimeTensorDescriptor("position_ids", "int64", (1, 1), 8),
    ]
    inputs.extend(
        RuntimeTensorDescriptor(name, "float32", (1, 2, 1024, 64), 524288)
        for name in QWEN25_INPUT_ORDER[3:]
    )
    outputs = [
        RuntimeTensorDescriptor("logits", "float32", (1, 1, 151936), 607744)
    ]
    outputs.extend(
        RuntimeTensorDescriptor(name, "float32", (1, 1, 2, 64), 512)
        for name in QWEN25_OUTPUT_ORDER[1:]
    )
    return RuntimeDescriptor(tuple(inputs), tuple(outputs))


class _RuntimeStub:
    def status(self):
        return {"ready": True, "healthy": True, "cache_tensor_count": 48}


class StaticKvReleaseTests(unittest.TestCase):
    def _artifacts(self) -> tuple[tempfile.TemporaryDirectory[str], dict[str, Path]]:
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        descriptor = _descriptors()
        controller = Qwen25Contract.from_descriptor(descriptor.inputs, descriptor.outputs)
        runtime = Qwen25Contract.from_descriptor(descriptor.inputs, descriptor.outputs)
        paths = {
            "om": root / "model.om",
            "tokenizer": root / "tokenizer.json",
            "tokenizer_config": root / "tokenizer_config.json",
            "controller_contract": root / "controller.json",
            "runtime_contract": root / "runtime.json",
            "om_lock": root / "model.om.lock.json",
            "tokenizer_lock": root / "tokenizer.json.lock.json",
        }
        paths["om"].write_bytes(b"tracked-static-kv-om")
        paths["tokenizer"].write_bytes(b"tracked-tokenizer")
        paths["tokenizer_config"].write_text("{}\n", encoding="utf-8")
        paths["controller_contract"].write_text(
            json.dumps(controller.as_dict(), sort_keys=True), encoding="utf-8"
        )
        paths["runtime_contract"].write_text(
            json.dumps(runtime.as_dict(), sort_keys=True), encoding="utf-8"
        )
        paths["tokenizer_lock"].write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "bytes": paths["tokenizer"].stat().st_size,
                    "sha256": _sha256(paths["tokenizer"]),
                }
            ),
            encoding="utf-8",
        )
        paths["om_lock"].write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "model_id": QWEN25_MODEL_ID,
                    "bytes": paths["om"].stat().st_size,
                    "sha256": _sha256(paths["om"]),
                    "soc_version": "Ascend310B4",
                    "controller_contract_sha256": _sha256(paths["controller_contract"]),
                    "runtime_contract_sha256": _sha256(paths["runtime_contract"]),
                }
            ),
            encoding="utf-8",
        )
        return temporary, paths

    def test_contract_requires_exact_51_inputs_and_49_outputs(self) -> None:
        descriptor = _descriptors()
        contract = Qwen25Contract.from_descriptor(descriptor.inputs, descriptor.outputs)

        self.assertEqual(len(contract.inputs), 51)
        self.assertEqual(len(contract.outputs), 49)
        self.assertEqual(contract.inputs[0].name, "input_ids")
        self.assertEqual(contract.inputs[-1].name, "past_key_values.23.value")
        self.assertEqual(contract.outputs[0].name, "logits")
        self.assertEqual(contract.outputs[-1].name, "present.23.value")

    def test_schema2_native_lock_binds_both_contracts_and_tokenizer(self) -> None:
        temporary, paths = self._artifacts()
        self.addCleanup(temporary.cleanup)

        status = verify_artifact_locks(
            paths["om"],
            paths["tokenizer"],
            paths["controller_contract"],
            paths["runtime_contract"],
            lock_path=paths["om_lock"],
            tokenizer_lock_path=paths["tokenizer_lock"],
            expected_soc_version="Ascend310B4",
        )

        self.assertTrue(status["verified"])
        self.assertEqual(status["schema_version"], 2)
        self.assertEqual(status["soc_version"], "Ascend310B4")
        self.assertEqual(status["runtime_contract"]["sha256"], _sha256(paths["runtime_contract"]))

    def test_schema1_om_lock_is_rejected(self) -> None:
        temporary, paths = self._artifacts()
        self.addCleanup(temporary.cleanup)
        lock = json.loads(paths["om_lock"].read_text(encoding="utf-8"))
        lock["schema_version"] = 1
        paths["om_lock"].write_text(json.dumps(lock), encoding="utf-8")

        with self.assertRaisesRegex(RuntimeUnavailable, "schema_version 2"):
            verify_artifact_locks(
                paths["om"],
                paths["tokenizer"],
                paths["controller_contract"],
                paths["runtime_contract"],
                lock_path=paths["om_lock"],
                tokenizer_lock_path=paths["tokenizer_lock"],
                expected_soc_version="Ascend310B4",
            )

    def test_schema2_lock_without_runtime_contract_digest_is_rejected(self) -> None:
        temporary, paths = self._artifacts()
        self.addCleanup(temporary.cleanup)
        lock = json.loads(paths["om_lock"].read_text(encoding="utf-8"))
        del lock["runtime_contract_sha256"]
        paths["om_lock"].write_text(json.dumps(lock), encoding="utf-8")

        with self.assertRaisesRegex(RuntimeUnavailable, "runtime_contract_sha256"):
            verify_artifact_locks(
                paths["om"],
                paths["tokenizer"],
                paths["controller_contract"],
                paths["runtime_contract"],
                lock_path=paths["om_lock"],
                tokenizer_lock_path=paths["tokenizer_lock"],
                expected_soc_version="Ascend310B4",
            )

    def test_schema2_lock_without_top_level_soc_is_rejected(self) -> None:
        temporary, paths = self._artifacts()
        self.addCleanup(temporary.cleanup)
        lock = json.loads(paths["om_lock"].read_text(encoding="utf-8"))
        del lock["soc_version"]
        paths["om_lock"].write_text(json.dumps(lock), encoding="utf-8")

        with self.assertRaisesRegex(RuntimeUnavailable, "target SoC mismatch"):
            verify_artifact_locks(
                paths["om"],
                paths["tokenizer"],
                paths["controller_contract"],
                paths["runtime_contract"],
                lock_path=paths["om_lock"],
                tokenizer_lock_path=paths["tokenizer_lock"],
                expected_soc_version="Ascend310B4",
            )

    def test_runtime_contract_digest_mismatch_is_rejected(self) -> None:
        temporary, paths = self._artifacts()
        self.addCleanup(temporary.cleanup)
        paths["runtime_contract"].write_text(
            paths["runtime_contract"].read_text(encoding="utf-8") + "\n",
            encoding="utf-8",
        )

        with self.assertRaisesRegex(RuntimeUnavailable, "runtime descriptor contract SHA-256 mismatch"):
            verify_artifact_locks(
                paths["om"],
                paths["tokenizer"],
                paths["controller_contract"],
                paths["runtime_contract"],
                lock_path=paths["om_lock"],
                tokenizer_lock_path=paths["tokenizer_lock"],
                expected_soc_version="Ascend310B4",
            )

    def test_release_environment_allows_installed_frameworks_when_not_imported(self) -> None:
        report = validate_release_environment(
            {
                "conda_env": "ascend",
                "python": "3.9.25",
                "soabi": "cpython-39-aarch64-linux-gnu",
                "cann": "8.0.0",
                "conda_prefix": "/opt/conda/envs/ascend",
                "sys_prefix": "/opt/conda/envs/ascend",
                "python_executable": "/opt/conda/envs/ascend/bin/python",
                "python_no_user_site": "1",
                "user_site_enabled": False,
                "acl_import": {"ok": True},
                "prohibited_imports": [],
            }
        )

        self.assertEqual(report["launch_policy"], "PASS")

    def test_release_environment_rejects_framework_already_imported(self) -> None:
        with self.assertRaises(StaticKvLaunchError):
            validate_release_environment(
                {
                    "conda_env": "ascend",
                    "python": "3.9.25",
                    "soabi": "cpython-39-aarch64-linux-gnu",
                    "cann": "8.0.0",
                    "python_no_user_site": "1",
                    "user_site_enabled": False,
                    "acl_import": {"ok": True},
                    "prohibited_imports": ["mindspore"],
                }
            )

    def test_release_paths_require_detected_b4_and_explicit_locks(self) -> None:
        temporary, paths = self._artifacts()
        self.addCleanup(temporary.cleanup)
        with patch.object(launch, "detect_soc_version", return_value="Ascend310B4"):
            report = validate_release_paths(**paths)

        self.assertTrue(report["verified"])
        self.assertEqual(report["board_soc_version"], "Ascend310B4")
        with patch.object(launch, "detect_soc_version", return_value="Ascend310B1"):
            with self.assertRaisesRegex(StaticKvLaunchError, "formal StaticKV target"):
                validate_release_paths(**paths)

    def test_timeout_must_fit_the_tracked_runtime_cap(self) -> None:
        temporary, paths = self._artifacts()
        self.addCleanup(temporary.cleanup)
        environment = {
            "conda_env": "ascend",
            "python": "3.9.25",
            "soabi": "cpython-39-aarch64-linux-gnu",
            "cann": "8.0.0",
            "conda_prefix": "/opt/conda/envs/ascend",
            "sys_prefix": "/opt/conda/envs/ascend",
            "python_executable": "/opt/conda/envs/ascend/bin/python",
            "python_no_user_site": "1",
            "user_site_enabled": False,
            "acl_import": {"ok": True},
            "prohibited_imports": [],
        }
        with patch.object(launch, "_observe_environment", return_value=environment), patch.object(
            launch, "detect_soc_version", return_value="Ascend310B4"
        ):
            with self.assertRaisesRegex(StaticKvLaunchError, "execution_timeout_seconds"):
                launch.create_release_service(**paths, execution_timeout_seconds=241)

    def test_service_exposes_launch_policy_without_http_framework(self) -> None:
        service = Qwen25StaticKvService(_RuntimeStub(), launch_policy={"launch_policy": "PASS"})
        self.assertEqual(service.health()["launch_policy"], "PASS")
        self.assertEqual(service.models()["data"][0]["id"], QWEN25_MODEL_ID)
        request = _parse_request(
            {
                "model": QWEN25_MODEL_ID,
                "messages": [{"role": "user", "content": "hello"}],
                "max_tokens": 2,
            }
        )
        self.assertEqual(request.max_tokens, 2)
        with self.assertRaises(ValueError):
            make_server("0.0.0.0", 8084, service)


if __name__ == "__main__":
    unittest.main()
