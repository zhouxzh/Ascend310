import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from embedding_backend import (
    CHINESE_CLIP_ID,
    MOBILECLIP_ID,
    EmbeddingError,
    TokenizerAdapter,
    l2_normalize,
    resolve_image_model,
    resolve_text_model,
)
from model_registry import ModelRegistry, RegistryError, load_candidates


class ModelRegistryTests(unittest.TestCase):
    def test_candidate_manifest_has_three_isolated_models(self):
        candidates = {record.model_id: record for record in load_candidates()}
        self.assertEqual(len(candidates), 3)
        self.assertTrue(candidates[MOBILECLIP_ID].supports_text)
        self.assertTrue(candidates[CHINESE_CLIP_ID].supports_text)
        dimensions = {record.embedding_dim for record in candidates.values()}
        self.assertEqual(dimensions, {512, 1024, 2048})

    def test_component_precision_overrides_model_default(self):
        record = next(record for record in load_candidates() if record.model_id == MOBILECLIP_ID)
        self.assertEqual(
            record.effective_precision_mode(record.components["image"]),
            "allow_fp32_to_fp16",
        )
        self.assertEqual(
            record.effective_precision_mode(record.components["text"]),
            "allow_fp32_to_fp16",
        )
        self.assertEqual(
            record.effective_precision_mode(record.components["image"], "force_fp32"),
            "force_fp32",
        )

    def test_empty_registry_is_valid_but_has_no_admitted_models(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text('{"schema_version":1,"models":[]}', encoding="utf-8")
            registry = ModelRegistry(path)
            self.assertEqual(registry.ids(), ())

    def test_registry_rejects_non_admitted_entry(self):
        candidate = json.loads(Path("candidate_manifest.json").read_text(encoding="utf-8"))["models"][0]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "registry.json"
            path.write_text(json.dumps({"schema_version": 1, "models": [candidate]}), encoding="utf-8")
            with self.assertRaises(RegistryError):
                ModelRegistry(path)

    def test_registry_verifies_admitted_om_hash(self):
        candidate = json.loads(Path("candidate_manifest.json").read_text(encoding="utf-8"))["models"][2]
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            om = directory / "model.om"
            onnx = directory / "model.onnx"
            om.write_bytes(b"verified-om")
            onnx.write_bytes(b"onnx")
            candidate["status"] = "admitted"
            candidate["components"]["image"].update(
                {
                    "om": str(om),
                    "onnx": str(onnx),
                    "onnx_sha256": hashlib.sha256(onnx.read_bytes()).hexdigest(),
                    "om_sha256": hashlib.sha256(om.read_bytes()).hexdigest(),
                }
            )
            registry_path = directory / "registry.json"
            registry_path.write_text(
                json.dumps({"schema_version": 1, "models": [candidate]}), encoding="utf-8"
            )
            registry = ModelRegistry(registry_path, require_artifacts=True)
            self.assertEqual(len(registry), 1)
            om.write_bytes(b"changed")
            with self.assertRaises(RegistryError):
                ModelRegistry(registry_path, require_artifacts=True)

            onnx.write_bytes(b"changed-onnx")
            with self.assertRaises(RegistryError):
                ModelRegistry(registry_path, require_artifacts=True)

    def test_language_routing_and_normalization(self):
        self.assertEqual(resolve_text_model("海边日落"), CHINESE_CLIP_ID)
        self.assertEqual(resolve_text_model("sunset by the sea"), MOBILECLIP_ID)
        self.assertEqual(resolve_image_model(), MOBILECLIP_ID)
        vector = l2_normalize(np.array([3.0, 4.0], np.float32))
        np.testing.assert_allclose(vector, [0.6, 0.8])
        with self.assertRaises(EmbeddingError):
            l2_normalize(np.zeros(2, np.float32))

    def test_chinese_bert_tokenizer_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            vocab = Path(directory) / "vocab.txt"
            vocab.write_text(
                "[PAD]\n[UNK]\n\u0085\n[CLS]\n[SEP]\n海\n边\n", encoding="utf-8"
            )
            candidate = next(record for record in load_candidates() if record.model_id == CHINESE_CLIP_ID)
            record = replace(
                candidate,
                tokenizer={
                    "path": str(vocab),
                    "kind": "bert",
                    "context_length": 6,
                    "pad_token": "[PAD]",
                    "unk_token": "[UNK]",
                    "cls_token": "[CLS]",
                    "sep_token": "[SEP]",
                },
            )
            tokens = TokenizerAdapter(record).encode("海边", np.int64)
            self.assertEqual(tokens.shape, (1, 6))
            self.assertEqual(tokens[0, 0], 3)
            self.assertEqual(tokens[0, 1], 5)
            self.assertEqual(tokens[0, 2], 6)
            self.assertEqual(tokens[0, 3], 4)


if __name__ == "__main__":
    unittest.main()
