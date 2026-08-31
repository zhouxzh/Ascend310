from __future__ import annotations

import copy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from palmprint_workbench.config import REGISTRY_PATH, ROOT
from palmprint_workbench.domain.admission import resolve_runtime_model
from palmprint_workbench.domain.registry import ModelRegistry


class ModelRegistryCandidateTests(unittest.TestCase):
    def test_runtime_models_remain_separate_from_candidates(self):
        registry = ModelRegistry()
        # The production registry is NPU-only.  Legacy CompNet/EDCC entries
        # live in the offline inventory and must not appear in bootstrap data.
        self.assertEqual({spec.id for spec in registry.all()}, {"ccnet"})
        self.assertEqual({spec.id for spec in registry.offline_models()}, {"compnet", "edcc"})
        self.assertGreaterEqual(len(registry.candidates()), 6)
        self.assertNotIn("compnet_tongji_600", {spec.id for spec in registry.all()})

    def test_manual_test_profile_exposes_only_hash_verified_compnet(self):
        registry = ModelRegistry()
        specs = registry.all(include_manual_test=True)
        ids = {spec.id for spec in specs}
        self.assertEqual(
            ids,
            {
                "ccnet",
                "compnet_tongji_600",
                "compnet_iitd_460",
                "compnet_rest_358",
                "compnet_xjtu_flash_200",
                "compnet_xjtu_natural_200",
            },
        )
        for spec in specs:
            if spec.id.startswith("compnet_"):
                self.assertTrue(spec.raw["manual_test_candidate"])
                self.assertTrue(spec.raw["manual_test_pending"])
                self.assertEqual(spec.raw["om_models"].keys(), {"mixed_fp16"})

    def test_manual_test_runtime_asset_gate_keeps_candidate_spec_context(self):
        registry = ModelRegistry()
        spec = resolve_runtime_model(
            "compnet_tongji_600",
            registry=registry,
            verify_assets=True,
            include_manual_test=True,
        )
        status = registry.runtime_asset_status(spec)
        self.assertTrue(status["ok"], status)
        self.assertEqual(status["assets"]["mixed_fp16_om"]["status"], "ready")

    def test_ccnet_runtime_assets_are_pinned_and_verified(self):
        registry = ModelRegistry()
        status = registry.runtime_asset_status("ccnet")
        self.assertTrue(status["ok"], status)
        self.assertEqual(status["precision"], "mixed_fp16")
        self.assertEqual(status["assets"]["mixed_fp16_om"]["status"], "ready")
        self.assertEqual(registry.model_threshold("ccnet"), 0.75)

    def test_reference_onnx_is_optional_for_om_only_runtime_assets(self):
        registry = ModelRegistry()
        spec = registry.get_runtime("ccnet")
        raw = copy.deepcopy(dict(spec.raw))
        raw["reference_onnx"] = "models/onnx/not-shipped-with-om-package.onnx"
        raw["assets"]["reference_onnx"]["path"] = raw["reference_onnx"]
        om_only = replace(spec, raw=raw)
        status = registry.runtime_asset_status(om_only)
        self.assertTrue(status["ok"], status)
        self.assertEqual(status["assets"]["reference_onnx"]["status"], "optional_missing")
        self.assertEqual(status["assets"]["mixed_fp16_om"]["status"], "ready")

    def test_all_compnet_checkpoints_have_verified_metadata(self):
        registry = ModelRegistry()
        expected = {
            "compnet_tongji_600": (
                "net_params_tongji_600.pth",
                21210404,
                "e27c86179dad2a69e9d9c7d4365eae5a31c3ccb4770a891a4ea3c1844d9f8a9a",
            ),
            "compnet_iitd_460": (
                "net_params_iitd_460.pth",
                20923684,
                "2e0744f4d73070d274822aa7c566a020daedd428761e90d78d1aecee6acccd77",
            ),
            "compnet_rest_358": (
                "net_params_rest_358.pth",
                20714788,
                "9d188319ffb3adbcde6543baac5e2487df2b025e49cffe174ef6279990a60e58",
            ),
            "compnet_xjtu_flash_200": (
                "net_params_if_200.pth",
                20391203,
                "af4f50d21843e57bf4039f9508d2c5bc2aa3d7352947d6a3e447292588b4e924",
            ),
            "compnet_xjtu_natural_200": (
                "net_params_in_200.pth",
                20391203,
                "65d99dd95237c645f1ce2a9e6f06e307beb5a5035c264ff10d8e8342e6a267bb",
            ),
        }
        compnet = registry.candidates_for("CompNet Static Gabor")
        self.assertEqual({spec.id for spec in compnet}, set(expected))
        with self.assertRaises(KeyError):
            registry.get_candidate("compnet_if_200")
        with self.assertRaises(KeyError):
            registry.get_candidate("compnet_in_200")
        for spec in compnet:
            filename, size, sha256 = expected[spec.id]
            self.assertEqual(spec.input_shape, (1, 1, 128, 128))
            self.assertEqual(spec.input_range, "nonzero_standardize")
            self.assertEqual(spec.feature_dim, 512)
            self.assertEqual(spec.metric, "cosine")
            self.assertEqual(spec.weight_status, "local_verified")
            self.assertEqual(spec.npu_status, "om_ready_admission_blocked")
            self.assertEqual(spec.checkpoint_size_bytes, size)
            self.assertEqual(spec.checkpoint_sha256, sha256)
            self.assertTrue(spec.checkpoint_verified)
            self.assertEqual(Path(spec.checkpoint).name, filename)
            self.assertTrue(spec.path("checkpoint").is_relative_to(ROOT))
            self.assertEqual(spec.path("onnx_model").name, f"{spec.id}.onnx")
            self.assertEqual(spec.om_path("mixed_fp16").name, f"{spec.id}_mixed_fp16.om")

    def test_ppnet_candidate_preserves_license_and_pending_weight_state(self):
        spec = ModelRegistry().get_candidate("ppnet")
        self.assertEqual(spec.license, "CC-BY-NC-4.0")
        self.assertEqual(spec.source, "https://github.com/xuliangcs/ppnet")
        self.assertEqual(spec.weight_status, "official_link_unverified")
        self.assertEqual(spec.npu_status, "conversion_pending")
        self.assertIsNone(spec.checkpoint_sha256)
        self.assertEqual(spec.available_backends, ())
        artifact = spec.raw["weights"]["artifacts"][0]
        self.assertIn("drive.google.com", artifact["url"])

    def test_legacy_registry_without_candidates_still_loads(self):
        payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        payload.pop("candidate_registry", None)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            registry = ModelRegistry(path)
        self.assertEqual(registry.candidates(), [])

    def test_candidate_fields_reject_bad_sha_path_shape_backend_and_duplicates(self):
        manifest = json.loads((ROOT / "candidate_manifest.json").read_text(encoding="utf-8"))

        mutations = {
            "sha": lambda data: data["candidates"][1]["weights"]["artifacts"][0].update(
                {"sha256": "not-a-sha"}
            ),
            "path": lambda data: data["candidates"][1]["weights"]["artifacts"][0].update(
                {"local_path": "../escape.pth"}
            ),
            "duplicate": lambda data: data["candidates"][1].update(
                {"id": data["candidates"][0]["id"]}
            ),
            "backend": lambda data: data["candidates"][1].update(
                {"available_backends": ["cuda"]}
            ),
            "shape": lambda data: data["candidates"][1].update(
                {"input_shape": [0, 128]}
            ),
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as directory:
                candidate_path = Path(directory) / "candidates.json"
                mutated = copy.deepcopy(manifest)
                mutate(mutated)
                candidate_path.write_text(json.dumps(mutated), encoding="utf-8")
                with self.assertRaises(ValueError):
                    ModelRegistry(candidate_path=candidate_path)

    def test_pending_candidate_cannot_be_resolved_as_a_runtime_model(self):
        payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        payload["admitted_candidates"] = ["compnet_tongji_600"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            registry = ModelRegistry(path)
            decision = registry.candidate_admission("compnet_tongji_600", verify_assets=False)
            self.assertFalse(decision.admitted)
            self.assertIn("candidate_manifest 未设置 production_enabled=true", decision.reasons)
            with self.assertRaises(KeyError):
                registry.get("compnet_tongji_600")

    def test_admitted_candidate_cannot_shadow_runtime_model(self):
        payload = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        payload["admitted_candidates"] = ["ccnet"]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "collides with runtime model id"):
                ModelRegistry(path)

    def test_formal_admission_rejects_missing_two_clean_polyu_runs(self):
        """A clean Tongji report cannot substitute for the PolyU stability gate."""

        registry = ModelRegistry()
        candidate = registry.get_candidate("compnet_tongji_600")
        checkpoint = candidate.raw["weights"]["artifacts"][0]
        conversion = candidate.raw["conversion"]
        admission = {
            "schema_version": 1,
            "status": "admitted",
            "precision": "mixed_fp16",
            "npu_model": "Ascend 310B4",
            "compute_tier": "8T",
            "artifacts": {
                "checkpoint": {"verified": True, "bytes": checkpoint["bytes"], "sha256": checkpoint["sha256"]},
                "onnx": {"verified": True, "bytes": conversion["onnx_bytes"], "sha256": conversion["onnx_sha256"]},
                "mixed_fp16_om": {
                    "verified": True,
                    "bytes": conversion["mixed_fp16_bytes"],
                    "sha256": conversion["mixed_fp16_sha256"],
                    "precision": "mixed_fp16",
                },
            },
            "contract": {
                "task_type": "embedding",
                "input_shape": [1, 1, 128, 128],
                "input_range": candidate.input_range,
                "feature_dim": 512,
                "metric": "cosine",
            },
            "validation": {
                "numeric_consistency": {"status": "passed", "samples": 100},
                "tongji": {"status": "passed", "return_code": 0, "backend": "npu", "precision": "mixed_fp16"},
                "polyu_b": {"status": "failed", "runs": []},
                "lifecycle": {"status": "passed", "soak_cycles": 10, "clean_exit": True, "resource_release": "passed"},
                "faults": {
                    "status": "clear",
                    "rc_139": False,
                    "aicore": False,
                    "lpm": False,
                    "ras": False,
                    "device_reset": False,
                    "resource_leak": False,
                },
            },
        }
        reasons: list[str] = []
        registry._validate_admission_evidence(candidate, admission, checkpoint, conversion, reasons)
        self.assertIn("PolyU-B 正式评测未通过", reasons)
        self.assertIn("PolyU-B 需要两次独立全量运行", reasons)


if __name__ == "__main__":
    unittest.main()
