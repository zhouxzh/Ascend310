from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from palmprint_workbench.domain.registry import ModelRegistry


COMPNET_CANDIDATES = {
    "compnet_tongji_600",
    "compnet_iitd_460",
    "compnet_rest_358",
    "compnet_xjtu_flash_200",
    "compnet_xjtu_natural_200",
}


class OfflineCandidateRegistryTests(unittest.TestCase):
    def test_only_fixed_contract_compnet_candidates_adapt_to_model_specs(self):
        registry = ModelRegistry()
        self.assertEqual(set(registry.offline_candidate_embedding_ids()), COMPNET_CANDIDATES)
        for candidate_id in COMPNET_CANDIDATES:
            with self.subTest(candidate_id=candidate_id):
                spec = registry.offline_candidate_embedding_spec(candidate_id)
                self.assertEqual(spec.id, candidate_id)
                self.assertEqual(spec.kind, "embedding")
                self.assertEqual(spec.input_shape, (1, 1, 128, 128))
                self.assertEqual(spec.feature_dim, 512)
                self.assertEqual(spec.metric, "cosine")
                self.assertTrue(spec.research_only)
                self.assertTrue(spec.raw["offline_candidate"])
                self.assertFalse(spec.raw["production_enabled"])
                self.assertEqual(spec.path("reference_onnx").name, f"{candidate_id}.onnx")
                self.assertEqual(spec.om_path("origin").name, f"{candidate_id}_origin.om")
                self.assertEqual(
                    spec.om_path("mixed_fp16").name,
                    f"{candidate_id}_mixed_fp16.om",
                )

    def test_candidate_adapter_does_not_change_production_models(self):
        registry = ModelRegistry()
        production = {spec.id for spec in registry.all()}
        self.assertEqual(production, {"ccnet"})
        self.assertTrue(COMPNET_CANDIDATES.isdisjoint(production))

    def test_non_embedding_or_wrong_contract_candidates_are_rejected(self):
        registry = ModelRegistry()
        for candidate_id in ("ppnet", "alignnet_roi_lanet", "kenan_cnn_palmar_veins"):
            with self.subTest(candidate_id=candidate_id):
                with self.assertRaises(ValueError):
                    registry.offline_candidate_embedding_spec(candidate_id)


HAS_CV2 = importlib.util.find_spec("cv2") is not None
HAS_PANDAS = importlib.util.find_spec("pandas") is not None

if HAS_CV2:
    from tools.offline.benchmark import (
        _benchmark_model_choices,
        _build_adapter,
        _domain_relation,
        _marker_accuracy_allowed,
    )
else:
    _benchmark_model_choices = _build_adapter = _domain_relation = _marker_accuracy_allowed = None

if HAS_CV2 and HAS_PANDAS:
    from palmprint_workbench.services.workbench import Workbench
else:
    Workbench = None


@unittest.skipUnless(HAS_CV2, "offline benchmark adapter tests require OpenCV")
class OfflineBenchmarkAdapterTests(unittest.TestCase):
    def test_benchmark_choices_include_compnet_candidates_without_promoting_them(self):
        choices = set(_benchmark_model_choices())
        self.assertTrue(COMPNET_CANDIDATES.issubset(choices))
        self.assertTrue({"ccnet", "compnet", "edcc"}.issubset(choices))

    @patch("tools.offline.benchmark.create_adapter")
    def test_build_adapter_uses_offline_candidate_spec(self, create_adapter):
        expected = MagicMock()
        create_adapter.return_value = expected
        result = _build_adapter("compnet_tongji_600", "npu", "mixed_fp16", 4)
        self.assertIs(result, expected)
        spec = create_adapter.call_args.args[0]
        self.assertEqual(spec.id, "compnet_tongji_600")
        self.assertTrue(spec.raw["offline_candidate"])
        self.assertEqual(create_adapter.call_args.args[1:], ("npu", "mixed_fp16"))

    def test_marker_requires_candidate_id_and_verified_checkpoint_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            marker = Path(directory) / "export.json"
            marker.write_text(
                json.dumps(
                    {
                        "candidate_id": "compnet_tongji_600",
                        "checkpoint_sha256": "a" * 64,
                        "checkpoint_hash_verified": True,
                        "accuracy_eligible": True,
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                _marker_accuracy_allowed(
                    marker,
                    model_id="compnet_tongji_600",
                    expected_checkpoint_sha256="a" * 64,
                ),
                (True, "eligible"),
            )
            allowed, reason = _marker_accuracy_allowed(
                marker,
                model_id="compnet_iitd_460",
                expected_checkpoint_sha256="a" * 64,
            )
            self.assertFalse(allowed)
            self.assertIn("belongs", reason)

    def test_compnet_domain_relation_uses_training_domain(self):
        self.assertEqual(
            _domain_relation("compnet_tongji_600", "tongji")["classification"],
            "in_domain_pretrained",
        )
        self.assertEqual(
            _domain_relation("compnet_iitd_460", "tongji")["classification"],
            "cross_domain_evaluation",
        )


@unittest.skipUnless(
    HAS_CV2 and HAS_PANDAS,
    "Workbench adapter test requires the service's optional OpenCV/Pandas dependencies",
)
class WorkbenchProductionBoundaryTests(unittest.TestCase):
    @patch("palmprint_workbench.services.workbench.create_adapter")
    def test_candidate_is_not_resolvable_by_production_workbench(self, create_adapter):
        adapter = MagicMock()
        create_adapter.return_value = adapter
        workbench = Workbench()
        try:
            with self.assertRaises(PermissionError):
                workbench.adapter("compnet_tongji_600", "npu", "mixed_fp16")
            create_adapter.assert_not_called()
        finally:
            workbench.close()


if __name__ == "__main__":
    unittest.main()
