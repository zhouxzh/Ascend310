import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from scripts import promote_mobileclip_precision_candidate as promotion


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class PromotionEvidenceTests(unittest.TestCase):
    def _summary(self, root: Path, passed=True):
        candidate = root / "reports" / "precision_sweep" / "mobileclip_s0_image_precision" / "C2"
        candidate.mkdir(parents=True)
        om = candidate / "om" / promotion.OM_NAME
        om.parent.mkdir()
        om.write_bytes(b"candidate-om")
        canonical = root / "models" / "om" / promotion.OM_NAME
        canonical.parent.mkdir(parents=True)
        canonical.write_bytes(b"production-om")
        onnx = root / "models" / "onnx" / "mobileclip_s0_image.onnx"
        onnx.parent.mkdir(parents=True)
        onnx.write_bytes(b"production-onnx")
        registry = root / "models" / "registry.json"
        registry.write_text(json.dumps({
            "schema_version": 1,
            "models": [{
                "model_id": promotion.MODEL_ID,
                "components": {"image": {
                    "onnx": "models/onnx/mobileclip_s0_image.onnx",
                    "onnx_sha256": _digest(onnx),
                    "om": "models/om/" + promotion.OM_NAME,
                    "om_sha256": _digest(canonical),
                }},
            }],
        }), encoding="utf-8")
        keep = candidate / "keep_dtype.cfg"
        keep.write_text("/image_encoder/model/network.5/proj/proj.0/lkb_reparam/Conv\n", encoding="utf-8")
        atc_log = candidate / "conversion_stdout.log"
        atc_log.write_text("ATC success\n", encoding="utf-8")
        atc_report = candidate / "atc_conversion.json"
        atc_report.write_text(json.dumps({
            "soc_version": "Ascend310B4",
            "precision_mode": "allow_fp32_to_fp16",
            "op_select_implmode": "high_precision_for_all",
            "models": {promotion.MODEL_ID: {"components": {"image": {
                "om_sha256": _digest(om),
                "command": ["atc", "--soc_version=Ascend310B4", "--op_compiler_cache_mode=disable", "--enable_graph_parallel=0"],
            }}}},
        }), encoding="utf-8")
        refs = [{"passed": True, "finite": True, "output_dim": 512, "cosine_similarity": 0.999} for _ in range(36)]
        numerical_raw = candidate / "acl_numerical_validation.json"
        numerical_raw.write_text(json.dumps({
            "model_id": promotion.MODEL_ID, "component": "image", "candidate_om_sha256": _digest(om),
            "threshold": 0.995, "references": refs, "reference_count": 36,
            "expected_reference_count": 36, "passed": True,
        }), encoding="utf-8")
        retrieval_data = {"query_count": 20, "metrics": {"recall_at_1": 0.9, "recall_at_3": 1.0, "recall_at_5": 1.0}, "passed": True}
        (candidate / "retrieval.json").write_text(json.dumps(retrieval_data), encoding="utf-8")
        worker_log = candidate / "performance.worker.log"
        worker_log.write_text("worker ok\n", encoding="utf-8")
        performance_data = {
            "warmup": 20, "loops": 100, "repeats": 3, "samples": 300,
            "p50_ms": 30.0, "p95_ms": 40.0, "passed": True,
            "worker_log": str(worker_log), "worker_log_sha256": _digest(worker_log),
            "thresholds": {"p50_max_ms": 90.0, "p95_max_ms": 50.0},
        }
        (candidate / "performance.json").write_text(json.dumps(performance_data), encoding="utf-8")
        onnx_sha = _digest(onnx)
        value = {
            "candidate_id": "C2", "model_id": promotion.MODEL_ID, "component": "image", "soc_version": "Ascend310B4",
            "om_path": str(om),
            "om_sha256": _digest(om),
            "onnx_sha256": onnx_sha, "same_path": True, "same_bytes": True,
            "passed": passed,
            "atc_report": str(atc_report), "conversion_log": str(atc_log),
            "numerical": {"passed": passed, "candidate_om_sha256": _digest(om), "reference_count": 36},
            "retrieval": {**retrieval_data, "comparisons": {
                key: {"candidate": metric, "baseline": metric, "passed": True}
                for key, metric in retrieval_data["metrics"].items()
            }},
            "performance": performance_data,
            "keep_dtype_path": str(keep), "keep_dtype_sha256": _digest(keep),
            "keep_dtype_nodes": ["/image_encoder/model/network.5/proj/proj.0/lkb_reparam/Conv"],
            "keep_dtype_node_count": 1,
        }
        summary = root / "reports" / "precision_sweep" / "mobileclip_s0_image_precision" / "summary.json"
        summary.write_text(
            json.dumps({
                "schema_version": 1, "evidence_schema_version": 2, "status": "passed", "passed": True,
                "selected_candidate": "C2", "model_id": promotion.MODEL_ID, "component": "image", "soc_version": "Ascend310B4",
                "protocol": {"single_thread": True, "cache_disabled": True, "numerical_threshold": 0.995,
                             "performance_warmup": 20, "performance_loops": 100, "performance_repeats": 3},
                "onnx": {"onnx_sha256": onnx_sha, "production_declared_onnx_sha256": onnx_sha,
                         "candidate_declared_onnx_sha256": onnx_sha, "same_path": True, "same_bytes": True},
                "production_baseline": {"retrieval": {"metrics": retrieval_data["metrics"]},
                                        "performance": {"p50_ms": 100.0, "p95_ms": 50.0}},
                "candidates": {"C2": value},
            }),
            encoding="utf-8",
        )
        return summary, om

    def test_valid_evidence_requires_all_gates_and_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, om = self._summary(root)
            evidence = promotion.validate_evidence(summary, None, root)
            self.assertEqual(evidence["candidate_id"], "C2")
            self.assertEqual(evidence["candidate_om_sha256"], _digest(om))

    def test_failed_candidate_is_refused_before_apply(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = self._summary(root, passed=False)
            with self.assertRaisesRegex(promotion.PromotionError, "did not pass"):
                promotion.validate_evidence(summary, None, root)

    def test_candidate_must_not_resolve_to_canonical_om(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = self._summary(root)
            canonical = root / "models" / "om" / promotion.OM_NAME
            payload = json.loads(summary.read_text(encoding="utf-8"))
            payload["candidates"]["C2"]["om_path"] = str(canonical)
            payload["candidates"]["C2"]["om_sha256"] = _digest(canonical)
            summary.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(promotion.PromotionError, "canonical"):
                promotion.validate_evidence(summary, None, root)

    def test_health_retries_startup_connection_race(self):
        response = mock.MagicMock()
        response.status = 200
        response.read.return_value = b'{"status":"ready"}'
        response.__enter__.return_value = response
        response.__exit__.return_value = False
        with mock.patch.object(
            promotion.urllib.request,
            "urlopen",
            side_effect=[ConnectionError("starting"), response],
        ) as urlopen, mock.patch.object(promotion.time, "sleep") as sleep:
            result = promotion._health("http://127.0.0.1:7860", timeout_seconds=1)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(urlopen.call_count, 2)
        sleep.assert_called_once()

    def test_health_rejects_non_positive_timeout(self):
        with self.assertRaisesRegex(promotion.PromotionError, "positive"):
            promotion._health("http://127.0.0.1:7860", timeout_seconds=0)

    def test_tampered_summary_model_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = self._summary(root)
            payload = json.loads(summary.read_text(encoding="utf-8"))
            payload["model_id"] = "different-model"
            summary.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(promotion.PromotionError, "model_id"):
                promotion.validate_evidence(summary, None, root)

    def test_tampered_summary_reference_count_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = self._summary(root)
            payload = json.loads(summary.read_text(encoding="utf-8"))
            payload["candidates"]["C2"]["numerical"]["reference_count"] = 1
            summary.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(promotion.PromotionError, "summary numerical"):
                promotion.validate_evidence(summary, None, root)

    def test_raw_numerical_reference_below_threshold_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            summary, _ = self._summary(root)
            raw_path = root / "reports" / "precision_sweep" / "mobileclip_s0_image_precision" / "C2" / "acl_numerical_validation.json"
            raw = json.loads(raw_path.read_text(encoding="utf-8"))
            raw["references"][0]["cosine_similarity"] = 0.5
            raw_path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(promotion.PromotionError, "cosine"):
                promotion.validate_evidence(summary, None, root)

    def test_post_promotion_health_requires_ready_npu_and_vectors(self):
        with self.assertRaisesRegex(promotion.PromotionError, "health status"):
            promotion._validate_post_promotion_health({"status": "degraded"}, 500)
        with self.assertRaisesRegex(promotion.PromotionError, "backend"):
            promotion._validate_post_promotion_health({"status": "ready", "backend": "cpu"}, 500)
        with self.assertRaisesRegex(promotion.PromotionError, "index count"):
            promotion._validate_post_promotion_health({
                "status": "ready", "backend": "npu", "admitted_models": [promotion.MODEL_ID],
                "missing_required_models": [], "index": {"available_photos": 499, "embeddings_by_model": {promotion.MODEL_ID: 499}},
            }, 500)


if __name__ == "__main__":
    unittest.main()
