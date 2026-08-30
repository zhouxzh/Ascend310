"""Controller-side safety checks for the board-only ACL/OM workflow.

These tests inspect text and metadata only.  They must never import ``acl``,
run ATC, download model bytes, or make an SSH connection.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "provision_acl_om_board.sh"
INSPECTOR = ROOT / "scripts" / "inspect_qwen_onnx.py"
REQUIREMENTS = ROOT / "requirements-acl-om.txt"
MANIFEST = ROOT / "local_model_manifest.json"


class AclOmStaticChecks(unittest.TestCase):
    def test_manifest_contains_immutable_acl_candidate(self) -> None:
        document = json.loads(MANIFEST.read_text(encoding="utf-8"))
        artifacts = document["artifacts"]
        for name, expected_filename in (
            ("acl_om_llm", "qwen1.5-0.5b-chat-model_fp16.onnx"),
            ("acl_om_tokenizer", "tokenizer.json"),
        ):
            item = artifacts[name]
            self.assertEqual(len(item["revision"]), 40)
            self.assertEqual(item["filename"], expected_filename)
            self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")
            self.assertGreater(item["expected_bytes"], 0)
            self.assertIn(
                f"/resolve/{item['revision']}/", item["url"]
            )

    def test_script_has_all_fail_closed_gates(self) -> None:
        source = SCRIPT.read_text(encoding="utf-8")
        for command in ("create-env", "install-runtime", "check", "download", "inspect", "convert", "smoke", "serve"):
            self.assertIn(command, source)
        for forbidden in ("torch", "torch_npu", "torchaudio", "mindtorch"):
            self.assertIn(forbidden, source)
        self.assertIn("--no-deps", source)
        self.assertIn("--require-hashes", source)
        self.assertIn("Ascend310B4", source)
        self.assertIn("127.0.0.1", source)
        self.assertIn("verify_contract_source", source)
        self.assertIn("verify_om_lock", source)
        self.assertIn("om.lock.json", source)
        self.assertIn("contract_sha256", source)
        self.assertIn('ATC produced an empty OM', source)
        self.assertIn("download_log", source)
        self.assertIn("--connect-timeout", source)
        self.assertIn("available_kib", source)
        self.assertNotIn("--delete", source)
        self.assertNotRegex(source, r"\b(conda|pip)\s+(remove|uninstall)\b")

    def test_requirements_are_direct_pinned_wheels(self) -> None:
        lines = [line.strip() for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()]
        wheel_lines = [line for line in lines if line and not line.startswith("#")]
        self.assertEqual(len(wheel_lines), 5)
        for line in wheel_lines:
            self.assertRegex(line, r"^[A-Za-z0-9_.-]+\s+@\s+https://files\.pythonhosted\.org/.+\.whl\s+--hash=sha256:[0-9a-f]{64}$")
            self.assertNotIn("torch", line.lower())
        self.assertIn("cp39-cp39-manylinux_2_17_aarch64", "\n".join(wheel_lines))
        self.assertIn("cp37-abi3-manylinux2014_aarch64", "\n".join(wheel_lines))

    def test_inspector_is_controller_safe(self) -> None:
        source = INSPECTOR.read_text(encoding="utf-8")
        self.assertIn("load_external_data=False", source)
        self.assertIn("supported_autoregressive_qwen_layout", source)
        self.assertIn("external initializers are not supported", source)
        self.assertIn("SUPPORTED_OPERATOR_TYPES", source)
        self.assertIn("onnx.checker.check_model", source)
        self.assertIn("source_artifact", source)
        self.assertIn("tuple(input_names) == EXPECTED_INPUT_ORDER", source)
        self.assertIn('"input_order_verified": bool(input_order_verified)', source)
        self.assertIn('"--source-revision"', source)
        self.assertIn("required=True", source)
        self.assertIn("self.close_connection = True", (ROOT / "acl_om_service.py").read_text(encoding="utf-8"))
        self.assertNotIn("import acl", source)

    def test_runtime_retains_buffers_until_stream_quiesces(self) -> None:
        source = (ROOT / "acl_om_runtime.py").read_text(encoding="utf-8")
        self.assertIn("_without_execution_alarm", source)
        self.assertIn("_pending_run_cleanups", source)
        self.assertIn("stream did not quiesce", source)
        self.assertIn("restart the service", source)


if __name__ == "__main__":
    unittest.main()
