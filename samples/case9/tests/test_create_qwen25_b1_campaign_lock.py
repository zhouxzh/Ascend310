from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "create_qwen25_b1_campaign_lock.py"
SPEC = importlib.util.spec_from_file_location("create_qwen25_b1_campaign_lock", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _contract(*, runtime: bool, om_bytes: int, om_sha: str) -> dict:
    inputs = [
        {"name": "input_ids", "dtype": "int64", "shape": [1, 1], "byte_size": 8},
        {"name": "attention_mask", "dtype": "int64", "shape": [1, 1024], "byte_size": 8192},
        {"name": "position_ids", "dtype": "int64", "shape": [1, 1], "byte_size": 8},
    ]
    for index in range(48):
        inputs.append({
            "name": "cache-%d" % index,
            "dtype": "float32",
            "shape": [1, 2, 1024, 64],
            "byte_size": 524288,
        })
    outputs = [{"name": "logits", "dtype": "float32", "shape": [1, 1, 151936], "byte_size": 607744}]
    for index in range(48):
        outputs.append({
            "name": "present-%d" % index,
            "dtype": "float32",
            "shape": [1, 1, 2, 64],
            "byte_size": 512,
        })
    document = {
        "schema_version": 1,
        "model": {"model_id": MODULE.MODEL_ID, "family": "qwen2.5"},
        "acl_om": {
            "cache_layout": "split",
            "cache_shape": [1, 2, 1024, 64],
            "static_sequence_length": 1024,
            "vocabulary_size": 151936,
            "supported_static_qwen25_layout": True,
            "input_order_verified": True,
            "inputs": inputs,
            "outputs": outputs,
        },
    }
    if runtime:
        document["om_artifact"] = {"bytes": om_bytes, "sha256": om_sha}
    else:
        document["static_onnx"] = {
            "batch_size": 1,
            "sequence_length": 1024,
            "cache_layout": "split",
            "cache_input_shape": [1, 2, 1024, 64],
            "cache_output_shape": [1, 1, 2, 64],
        }
    return document


class CreateQwen25B1CampaignLockTests(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[dict, dict]:
        artifact_dir = root / "artifacts" / "qwen25-b1"
        artifact_dir.mkdir(parents=True)
        om = artifact_dir / "model.om"
        om.write_bytes(b"qwen-b1-om")
        om_size = om.stat().st_size
        om_sha = _digest(om.read_bytes())
        controller = _contract(runtime=False, om_bytes=om_size, om_sha=om_sha)
        runtime = _contract(runtime=True, om_bytes=om_size, om_sha=om_sha)
        controller_path = artifact_dir / "controller.json"
        runtime_path = artifact_dir / "runtime.json"
        controller_path.write_text(json.dumps(controller, sort_keys=True), encoding="utf-8")
        runtime_path.write_text(json.dumps(runtime, sort_keys=True), encoding="utf-8")
        controller_sha = _digest(controller_path.read_bytes())
        lock = {
            "bytes": om_size,
            "sha256": om_sha,
            "soc_version": MODULE.SOC_VERSION,
            "contract_sha256": controller_sha,
        }
        historical_path = artifact_dir / "historical.lock.json"
        historical_path.write_text(json.dumps(lock, sort_keys=True), encoding="utf-8")
        expected = {
            "om": {"bytes": om_size, "sha256": om_sha},
            "controller_contract": {"bytes": controller_path.stat().st_size, "sha256": controller_sha},
            "runtime_contract": {"bytes": runtime_path.stat().st_size, "sha256": _digest(runtime_path.read_bytes())},
            "historical_lock": {"bytes": historical_path.stat().st_size, "sha256": _digest(historical_path.read_bytes())},
        }
        return expected, {
            "om": "artifacts/qwen25-b1/model.om",
            "controller": "artifacts/qwen25-b1/controller.json",
            "runtime": "artifacts/qwen25-b1/runtime.json",
            "historical": "artifacts/qwen25-b1/historical.lock.json",
            "output": "artifacts/qwen25-b1/campaign.lock.json",
        }

    def test_creates_deterministic_v2_lock_without_changing_historical_lock(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected, paths = self._fixture(root)
            historical_before = (root / paths["historical"]).read_bytes()
            with patch.object(MODULE, "EXPECTED_B1", expected):
                result = MODULE.create_campaign_lock(
                    root_value=str(root),
                    om_value=paths["om"],
                    controller_contract_value=paths["controller"],
                    runtime_contract_value=paths["runtime"],
                    historical_lock_value=paths["historical"],
                    output_value=paths["output"],
                    recorded_at_utc="2026-09-14T12:00:00Z",
                )
            output = root / paths["output"]
            self.assertEqual(result["status"], "created")
            self.assertEqual(result["sha256"], _digest(output.read_bytes()))
            self.assertEqual(historical_before, (root / paths["historical"]).read_bytes())
            document = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(document["schema_version"], 2)
            self.assertEqual(document["contract_sha256"], expected["controller_contract"]["sha256"])
            self.assertEqual(document["runtime_contract_sha256"], expected["runtime_contract"]["sha256"])
            self.assertEqual(document["historical_lock"]["sha256"], expected["historical_lock"]["sha256"])

    def test_rejects_unsafe_or_existing_output_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected, paths = self._fixture(root)
            with patch.object(MODULE, "EXPECTED_B1", expected):
                with self.assertRaisesRegex(MODULE.CampaignLockError, "escapes"):
                    MODULE.create_campaign_lock(
                        root_value=str(root),
                        om_value="../outside.om",
                        controller_contract_value=paths["controller"],
                        runtime_contract_value=paths["runtime"],
                        historical_lock_value=paths["historical"],
                        output_value=paths["output"],
                        recorded_at_utc="2026-09-14T12:00:00Z",
                    )
                output = root / paths["output"]
                output.write_text("existing", encoding="utf-8")
                with self.assertRaisesRegex(MODULE.CampaignLockError, "overwrite"):
                    MODULE.create_campaign_lock(
                        root_value=str(root),
                        om_value=paths["om"],
                        controller_contract_value=paths["controller"],
                        runtime_contract_value=paths["runtime"],
                        historical_lock_value=paths["historical"],
                        output_value=paths["output"],
                        recorded_at_utc="2026-09-14T12:00:00Z",
                    )

    def test_expected_output_digest_is_an_admission_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected, paths = self._fixture(root)
            with patch.object(MODULE, "EXPECTED_B1", expected):
                with self.assertRaisesRegex(MODULE.CampaignLockError, "output lock SHA-256 mismatch"):
                    MODULE.create_campaign_lock(
                        root_value=str(root),
                        om_value=paths["om"],
                        controller_contract_value=paths["controller"],
                        runtime_contract_value=paths["runtime"],
                        historical_lock_value=paths["historical"],
                        output_value=paths["output"],
                        recorded_at_utc="2026-09-14T12:00:00Z",
                        expected_output_sha256="0" * 64,
                    )
            self.assertFalse((root / paths["output"]).exists())

    def test_dry_run_reports_the_same_digest_without_writing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            expected, paths = self._fixture(root)
            with patch.object(MODULE, "EXPECTED_B1", expected):
                preview = MODULE.create_campaign_lock(
                    root_value=str(root),
                    om_value=paths["om"],
                    controller_contract_value=paths["controller"],
                    runtime_contract_value=paths["runtime"],
                    historical_lock_value=paths["historical"],
                    output_value=paths["output"],
                    recorded_at_utc="2026-09-14T12:00:00Z",
                    dry_run=True,
                )
                self.assertFalse((root / paths["output"]).exists())
                created = MODULE.create_campaign_lock(
                    root_value=str(root),
                    om_value=paths["om"],
                    controller_contract_value=paths["controller"],
                    runtime_contract_value=paths["runtime"],
                    historical_lock_value=paths["historical"],
                    output_value=paths["output"],
                    recorded_at_utc="2026-09-14T12:00:00Z",
                    expected_output_sha256=preview["sha256"],
                )
            self.assertEqual(preview["status"], "verified")
            self.assertEqual(created["status"], "created")
            self.assertEqual(preview["sha256"], created["sha256"])


if __name__ == "__main__":
    unittest.main()
