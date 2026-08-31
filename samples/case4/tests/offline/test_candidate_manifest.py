from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.offline.candidates import (
    CandidateManifest,
    validate_candidate_manifest,
    validate_candidate_manifest_payload,
)


class CandidateManifestTests(unittest.TestCase):
    def test_checked_in_manifest_is_valid_and_contains_required_candidates(self):
        self.assertEqual(validate_candidate_manifest(), [])
        manifest = CandidateManifest.load()
        expected = {
            "ccnet",
            "ppnet",
            "holzweber_resnet18_tongji",
            "lin_dxin_resnet18_pair",
            "ee_prnet",
            "alignnet_roi_lanet",
            "kenan_cnn_palmar_veins",
            "edcc",
            "mpsnet",
            "kby_palmprint_sdk",
            "tpa_cnn",
            "robust_palm_roi",
            "glnet",
            "palmwildnet",
        }
        self.assertTrue(expected.issubset({candidate.id for candidate in manifest.all()}))

    def test_all_five_compnet_checkpoints_are_explicit_candidates(self):
        manifest = CandidateManifest.load()
        compnet = [candidate for candidate in manifest.all() if candidate.family == "CompNet Static Gabor"]
        self.assertEqual(len(compnet), 5)
        self.assertTrue(all(candidate.weights["availability"] == "local_verified" for candidate in compnet))
        self.assertTrue(all(candidate.npu_status == "om_ready_admission_blocked" for candidate in compnet))
        self.assertTrue(all(candidate.raw["task_type"] == "embedding" for candidate in compnet))
        self.assertTrue(all(candidate.raw["conversion"]["onnx_status"] == "local_verified" for candidate in compnet))

    def test_board_smoke_metadata_does_not_promote_compnet(self):
        manifest = CandidateManifest.load()
        compnet = [candidate for candidate in manifest.all() if candidate.family == "CompNet Static Gabor"]
        self.assertTrue(all(candidate.raw["conversion"]["om_status"] == "mixed_fp16_ready_smoke_only" for candidate in compnet))
        self.assertTrue(all(candidate.raw["conversion"]["board_npu_status"] == "om_ready_smoke_only" for candidate in compnet))
        self.assertTrue(all(candidate.raw.get("production_enabled", False) is False for candidate in compnet))
        self.assertTrue(all(candidate.raw["conversion"]["numeric_smoke"]["status"] == "passed" for candidate in compnet))

    def test_ppnet_keeps_an_audited_download_blocker_until_a_file_is_verified(self):
        ppnet = CandidateManifest.load().get("ppnet")
        self.assertEqual(ppnet.weights["availability"], "official_link_unverified")
        audit = ppnet.raw["download_audit"]
        self.assertEqual(audit["result"], "not_downloaded")
        self.assertEqual(audit["google_drive"]["result"], "connection_timeout")
        self.assertEqual(audit["baidu"]["file_list"], "errno=-9")
        self.assertIn("float32[1,1,128,128]", ppnet.raw["input_contract"])

    def test_candidate_manifest_load_does_not_require_ignored_model_files(self):
        manifest = CandidateManifest.load()
        paths = manifest.get("compnet_tongji_600").local_artifact_paths
        self.assertEqual(len(paths), 1)
        self.assertTrue(paths[0].is_absolute())

    def test_validator_rejects_compnet_conversion_path_traversal(self):
        payload = json.loads(
            (Path(__file__).resolve().parents[2] / "candidate_manifest.json").read_text(
                encoding="utf-8"
            )
        )
        candidate = next(
            item for item in payload["candidates"] if item["id"] == "compnet_tongji_600"
        )
        candidate["conversion"]["onnx_path"] = "models/onnx/../../outside.onnx"
        errors = validate_candidate_manifest_payload(payload)
        self.assertTrue(any("conversion.onnx_path" in error for error in errors))

    def test_validator_rejects_duplicate_ids_and_non_https_sources(self):
        payload = {
            "schema_version": 1,
            "candidates": [
                {
                    "id": "duplicate",
                    "display_name": "First",
                    "family": "Test",
                    "modality": "palmprint",
                    "task": "test",
                    "task_type": "sdk",
                    "comparison_scope": "audit_only",
                    "source": {"url": "http://invalid.example", "revision": "test"},
                    "license": {"spdx": "MIT", "usage": "test only"},
                    "weights": {"availability": "not_available", "artifacts": []},
                    "npu_status": "unknown"
                },
                {
                    "id": "duplicate",
                    "display_name": "Second",
                    "family": "Test",
                    "modality": "palmprint",
                    "task": "test",
                    "task_type": "sdk",
                    "comparison_scope": "audit_only",
                    "source": {"url": "https://valid.example", "revision": "test"},
                    "license": {"spdx": "MIT", "usage": "test only"},
                    "weights": {"availability": "not_available", "artifacts": []},
                    "npu_status": "unknown"
                }
            ]
        }
        errors = validate_candidate_manifest_payload(payload)
        self.assertTrue(any("duplicate candidate id" in error for error in errors))
        self.assertTrue(any("source.url" in error for error in errors))

    def test_validator_reports_invalid_json_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate_manifest.json"
            path.write_text("{bad json", encoding="utf-8")
            self.assertTrue(validate_candidate_manifest(path))

    def test_validator_rejects_unsafe_local_path_and_bad_conversion_hash(self):
        payload = json.loads(Path("candidate_manifest.json").read_text(encoding="utf-8"))
        payload["candidates"][1]["weights"]["artifacts"][0]["local_path"] = "../outside.pth"
        payload["candidates"][1]["conversion"]["onnx_sha256"] = "not-a-sha"
        errors = validate_candidate_manifest_payload(payload)
        self.assertTrue(any("local_path" in error for error in errors))
        self.assertTrue(any("onnx_sha256" in error for error in errors))

    def test_validator_rejects_non_boolean_production_gate(self):
        payload = json.loads(Path("candidate_manifest.json").read_text(encoding="utf-8"))
        payload["candidates"][1]["production_enabled"] = "yes"
        errors = validate_candidate_manifest_payload(payload)
        self.assertTrue(any("production_enabled" in error for error in errors))

    def test_production_gate_requires_structured_admission_evidence(self):
        payload = json.loads(Path("candidate_manifest.json").read_text(encoding="utf-8"))
        payload["candidates"][1]["production_enabled"] = True
        payload["candidates"][1].pop("production_admission", None)
        errors = validate_candidate_manifest_payload(payload)
        self.assertTrue(any("production_admission" in error for error in errors))


if __name__ == "__main__":
    unittest.main()
