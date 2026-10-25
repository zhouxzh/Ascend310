from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from ascend_edge_chat.quantized_onnx import (
    DEFAULT_CANDIDATES_PATH,
    QuantizedCandidateError,
    classify_operator_risks,
    load_candidate_lock,
    screen_candidates,
)


class QuantizedCandidateLockTests(unittest.TestCase):
    def test_lock_pins_fixed_public_revisions_and_graph_hashes(self) -> None:
        lock = load_candidate_lock()
        candidates = {item.id: item for item in lock.candidates}

        self.assertEqual(len(candidates), 12)
        self.assertEqual(
            candidates["qwen15-uint8"].revision,
            "6d413dd9a252749e0760902c93331e3e4e65b73c",
        )
        self.assertEqual(
            candidates["qwen25-gqa-uint8"].graph_file().sha256,
            "863ba5e328fc2ce592897d17a5b5c8ed9a73d524219d01a22ad90465df088720",
        )
        self.assertEqual(
            candidates["hazem-int4-block32"].graph_file().basename,
            "qwen-2.5-32k-500m-instruct.onnx",
        )
        self.assertEqual(
            candidates["amd-int4-fp16"].license_status,
            "not declared by candidate repository",
        )

    def test_operator_policy_rejects_vendor_domains_and_quantized_forms(self) -> None:
        lock = load_candidate_lock()
        reasons = classify_operator_risks(
            ["ai.onnx", "com.microsoft"],
            ["MatMul", "MatMulNBits", "GroupQueryAttention"],
            lock,
        )

        self.assertIn(
            {"code": "domain-not-allowed", "value": "com.microsoft"},
            [{key: item[key] for key in ("code", "value")} for item in reasons],
        )
        self.assertEqual(
            {item["value"] for item in reasons if item["code"] == "operator-not-eligible"},
            {"GroupQueryAttention", "MatMulNBits"},
        )

    def test_standard_graph_is_not_rejected_by_the_static_policy(self) -> None:
        lock = load_candidate_lock()
        reasons = classify_operator_risks(
            ["ai.onnx"],
            ["MatMul", "Softmax", "Add"],
            lock,
        )
        self.assertEqual(reasons, [])

    def test_main_branch_is_not_a_valid_candidate_revision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raw = json.loads(DEFAULT_CANDIDATES_PATH.read_text(encoding="utf-8"))
            raw["candidates"][0]["revision"] = "main"
            path = Path(directory) / "lock.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(QuantizedCandidateError, "full 40-char SHA"):
                load_candidate_lock(path)

    def test_duplicate_graph_blob_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            raw = json.loads(DEFAULT_CANDIDATES_PATH.read_text(encoding="utf-8"))
            raw["candidates"][1]["files"][0] = dict(raw["candidates"][0]["files"][0])
            path = Path(directory) / "lock.json"
            path.write_text(json.dumps(raw), encoding="utf-8")
            with self.assertRaisesRegex(QuantizedCandidateError, "duplicate graph SHA-256"):
                load_candidate_lock(path)


class QuantizedCandidateScreeningTests(unittest.TestCase):
    def test_missing_graph_stays_untested(self) -> None:
        lock = load_candidate_lock()
        with tempfile.TemporaryDirectory() as directory:
            report = screen_candidates(
                directory,
                lock,
                candidate_ids=["qwen15-uint8"],
            )

        self.assertEqual(report["candidates"][0]["state"], "untested")
        self.assertEqual(report["candidates"][0]["files"][0]["present"], False)

    def test_bad_bytes_are_rejected_before_onnx_loading(self) -> None:
        lock = load_candidate_lock()
        candidate = next(item for item in lock.candidates if item.id == "qwen15-uint8")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / candidate.id / candidate.graph_file().basename
            path.parent.mkdir(parents=True)
            path.write_bytes(b"not-the-locked-model")
            report = screen_candidates(
                directory,
                lock,
                candidate_ids=[candidate.id],
            )

        self.assertEqual(report["candidates"][0]["state"], "artifact-mismatch")


if __name__ == "__main__":
    unittest.main()
