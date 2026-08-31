from __future__ import annotations

import json
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HAS_CV2 = importlib.util.find_spec("cv2") is not None
if HAS_CV2:
    import numpy as np

    from tools.offline.benchmark import _score_split, evaluate_dataset
    from palmprint_workbench.domain.registry import ModelRegistry
    from palmprint_workbench.runtime.adapters import (
        PalmAdapter,
        l2_normalize,
        preprocess_embedding_roi,
    )
    from palmprint_workbench.domain.datasets import (
        PalmRecord,
        audit_palmmatchdb_zip,
        parse_palmmatch_member,
    )
    from palmprint_workbench.domain.metrics import compare_embeddings, verification_metrics
    from palmprint_workbench.domain.preprocessing import PalmPreprocessor
    from palmprint_workbench.domain.templates import TemplateStore
    from tools.board import download_assets
else:
    np = None
    PalmAdapter = object


class FakeAdapter(PalmAdapter):
    def preprocess(self, roi):
        return roi

    def encode_preprocessed(self, value):
        return np.asarray(value)

    def compare(self, query, references):
        return np.asarray(references, dtype=np.float32) @ np.asarray(query, dtype=np.float32)


class TiedScoreAdapter:
    def compare(self, query, references):
        del references
        return np.asarray(query, dtype=np.float64)


@unittest.skipUnless(HAS_CV2, "core tests require the board image stack")
class CoreTests(unittest.TestCase):

    def test_registry_contracts(self):
        registry = ModelRegistry()
        self.assertEqual(registry.get("ccnet").input_shape, (1, 1, 128, 128))
        self.assertEqual(registry.get("compnet").feature_dim, 512)
        self.assertEqual(registry.get("edcc").kind, "code")

    def test_roi_bypass_contract(self):
        image = np.full((160, 120, 3), 127, dtype=np.uint8)
        result = PalmPreprocessor().extract(image, assume_roi=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.roi.shape, (128, 128))
        self.assertEqual(result.roi.dtype, np.uint8)

    def test_embedding_preprocess_and_normalize(self):
        roi = np.arange(128 * 128, dtype=np.uint8).reshape(128, 128)
        value = preprocess_embedding_roi(roi, "zero_one")
        self.assertEqual(value.shape, (1, 1, 128, 128))
        self.assertEqual(value.dtype, np.float32)
        normalized = l2_normalize(np.array([3, 4], dtype=np.float32))
        self.assertAlmostEqual(float(np.linalg.norm(normalized)), 1.0, places=6)

    def test_store_is_atomic_and_model_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TemplateStore(Path(directory))
            adapter = FakeAdapter(ModelRegistry().get("ccnet"))
            identity = store.enroll(
                "ccnet", [np.array([1.0, 0.0], dtype=np.float32)] * 3, "Alice", "left"
            )
            result = store.search(
                "ccnet", adapter, np.array([1.0, 0.0], dtype=np.float32), threshold=0.9
            )
            self.assertTrue(result["accepted"])
            self.assertEqual(result["user_id"], identity)
            self.assertEqual(store.users("edcc"), [])

    def test_metrics_and_consistency(self):
        metrics = verification_metrics(np.array([0.8, 0.9]), np.array([0.1, 0.2]))
        self.assertEqual(metrics["eer"], 0.0)
        self.assertAlmostEqual(metrics["threshold"], 0.8)
        inverted = verification_metrics(np.array([0.1, 0.2]), np.array([0.8, 0.9]))
        self.assertEqual(inverted["auc"], 0.0)
        tied = verification_metrics(np.array([0.5]), np.array([0.5]))
        self.assertEqual(tied["auc"], 0.5)
        comparison = compare_embeddings(np.eye(2), np.eye(2))
        self.assertEqual(comparison["min_cosine"], 1.0)

    def test_rank1_ties_are_reported_by_score_split(self):
        # Both identities have identical aggregate scores. The lexical rule
        # should make the result reproducible and expose the tie count.
        encoded = {
            "palm-b": {
                1: [np.zeros(1), np.zeros(1)],
                2: [np.full(4, 0.8)],
            },
            "palm-a": {
                1: [np.zeros(1), np.zeros(1)],
                2: [np.full(4, 0.8)],
            },
        }

        result = _score_split(TiedScoreAdapter(), encoded, ["palm-b", "palm-a"])

        self.assertEqual(result["rank1_tied_queries"], 2)
        self.assertEqual(result["rank1_tie_rate"], 1.0)
        self.assertEqual(result["rank1_mean_tied_candidates"], 2.0)
        self.assertEqual(result["rank1_tie_break_policy"], "lexicographic_identity")
        self.assertEqual(result["rank1"], 0.5)

    @patch("tools.offline.benchmark.audit_extracted", return_value={"ready": True})
    @patch("tools.offline.benchmark.audit_archive", return_value={"integrity_ok": True})
    @patch("tools.offline.benchmark._accuracy_allowed", return_value=(True, "eligible"))
    @patch("tools.offline.benchmark.records")
    def test_evaluation_requires_two_calibration_identities(
        self, mocked_records, _mocked_allowed, _mocked_archive, _mocked_extracted
    ):
        mocked_records.return_value = [
            PalmRecord(Path(f"identity-{index}.bmp"), f"palm-{index}", 1, 1)
            for index in range(5)
        ]
        with self.assertRaisesRegex(ValueError, "At least ten"):
            # Exercise the identity-embedding protocol before an adapter is
            # constructed. EDCC is intentionally a non-embedding CPU-only
            # baseline and no longer belongs to this evaluation path.
            evaluate_dataset("ccnet", "npu", "mixed_fp16", "tongji", "B", 1, 5)

    def test_palmmatch_path_audit_keeps_conflicts_out_of_identity_parser(self):
        valid = parse_palmmatch_member(
            "PalmMatchDB/FEMALE/F101(15-24)/F5MB/left/ei_1608937859373.png"
        )
        self.assertTrue(valid["is_image"])
        self.assertEqual(valid["subject"], "F101")
        self.assertEqual(valid["side"], "left")
        self.assertFalse(valid["subject_conflict"])
        conflict = parse_palmmatch_member("PalmMatchDB/M055/M057/right/image.jpg")
        self.assertEqual(conflict["subject"], "M057")
        self.assertTrue(conflict["subject_conflict"])

    def test_parallel_download_assembly_preserves_existing_prefix(self):
        payload = b"palmprint-range-assembly"
        with tempfile.TemporaryDirectory() as directory:
            partial = Path(directory) / "asset.zip.part"
            partial.write_bytes(payload[:5])

            def fake_range_download(_url, destination, start, end):
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload[start : end + 1])
                return destination

            with patch.object(download_assets, "_download_range_with_curl", fake_range_download):
                segments = download_assets._download_parallel_ranges(
                    "https://mirror.invalid/asset.zip",
                    partial,
                    len(payload),
                    connections=2,
                )
            self.assertEqual(partial.read_bytes(), payload)
            self.assertEqual(len(segments), 1)

    def test_palmmatch_audit_result_is_json_serializable_when_given_archive_metadata(self):
        result = audit_palmmatchdb_zip({"integrity_ok": False, "dataset_id": "palmmatchdb"})
        self.assertFalse(result["zip_ok"])
        json.dumps({"palmmatchdb": result})


if __name__ == "__main__":
    unittest.main()
