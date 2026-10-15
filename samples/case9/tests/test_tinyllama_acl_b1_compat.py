from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from tinyllama_acl_b1_compat import (
    CompatibilityError,
    CompatibilityMetadata,
    SOURCE_SOC,
    TARGET_SOC,
    _detect_soc,
    _require_experiment_opt_in,
    inspect_and_bind_contract,
    verify_artifacts,
)
from tinyllama_acl_contract import TinyLlamaContract
from tinyllama_acl_runtime import RuntimeDescriptor, TensorDescriptor


class TinyLlamaB1CompatibilityTests(unittest.TestCase):
    def _artifacts(self):
        directory = tempfile.TemporaryDirectory()
        root = Path(directory.name)
        om = root / "tiny-llama.om"
        archive = root / "tokenizer.zip"
        tokenizer = root / "tokenizer.json"
        om.write_bytes(b"om-fixture")
        archive.write_bytes(b"archive-fixture")
        extracted = {
            "tokenizer.json": b"tokenizer-fixture",
            "tokenizer.model": b"model-fixture",
            "special_tokens_map.json": b"special-fixture",
            "tokenizer_config.json": b"config-fixture",
        }
        for name, value in extracted.items():
            (root / name).write_bytes(value)
        manifest = root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "artifacts": {
                        "tinyllama_acl_om": {
                            "revision": "fixture-revision",
                            "expected_bytes": om.stat().st_size,
                            "sha256": hashlib.sha256(om.read_bytes()).hexdigest(),
                        },
                        "tinyllama_tokenizer_zip": {
                            "revision": "fixture-revision",
                            "expected_bytes": archive.stat().st_size,
                            "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
                            "extracted_files": {
                                name: {
                                    "expected_bytes": len(value),
                                    "sha256": hashlib.sha256(value).hexdigest(),
                                }
                                for name, value in extracted.items()
                            },
                        },
                    }
                }
            ),
            encoding="utf-8",
        )
        contract = root / "contract.json"
        bound = TinyLlamaContract(
            source_revision="fixture-revision",
            source_bytes=om.stat().st_size,
            source_sha256=hashlib.sha256(om.read_bytes()).hexdigest(),
        )
        contract.write_text(json.dumps(bound.as_dict()), encoding="utf-8")
        return directory, manifest, om, archive, tokenizer, root / "tokenizer_config.json", contract

    def test_verifies_manifest_bound_om_tokenizer_and_contract(self):
        directory, manifest, om, archive, tokenizer, config, contract = self._artifacts()
        try:
            binding = verify_artifacts(
                manifest_path=manifest,
                om_path=om,
                tokenizer_zip_path=archive,
                tokenizer_path=tokenizer,
                tokenizer_config_path=config,
                contract_path=contract,
                require_contract=True,
            )
            self.assertEqual(binding.revision, "fixture-revision")
            self.assertEqual(binding.om_sha256, hashlib.sha256(om.read_bytes()).hexdigest())
        finally:
            directory.cleanup()

    def test_rejects_tampered_extracted_tokenizer(self):
        directory, manifest, om, archive, tokenizer, config, contract = self._artifacts()
        try:
            # Preserve the immutable byte count so this test exercises the
            # digest branch rather than the earlier byte-count rejection.
            tokenizer.write_bytes(b"x" * tokenizer.stat().st_size)
            with self.assertRaisesRegex(CompatibilityError, "tokenizer file tokenizer.json SHA-256"):
                verify_artifacts(
                    manifest_path=manifest,
                    om_path=om,
                    tokenizer_zip_path=archive,
                    tokenizer_path=tokenizer,
                    tokenizer_config_path=config,
                    contract_path=contract,
                    require_contract=True,
                )
        finally:
            directory.cleanup()

    def test_explicit_dirty_base_opt_in_is_required(self):
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaisesRegex(CompatibilityError, "--compatibility-experiment"):
                _require_experiment_opt_in(False)
        with patch.dict(
            "os.environ",
            {"CASE9_TINYLLAMA_B1_COMPAT_ALLOW_DIRTY_BASE": "1", "CONDA_DEFAULT_ENV": "base", "CONDA_PREFIX": ""},
            clear=True,
        ):
            with self.assertRaisesRegex(CompatibilityError, "outside the declared conda base"):
                _require_experiment_opt_in(True)

    def test_detect_soc_accepts_only_b1_target(self):
        completed = types.SimpleNamespace(returncode=0, stdout="Device Type: Ascend310B1\n")
        with patch("tinyllama_acl_b1_compat.subprocess.run", return_value=completed):
            self.assertEqual(_detect_soc("npu-smi"), TARGET_SOC)
        b4 = types.SimpleNamespace(returncode=0, stdout="Device Type: Ascend310B4\n")
        with patch("tinyllama_acl_b1_compat.subprocess.run", return_value=b4):
            self.assertEqual(_detect_soc("npu-smi"), SOURCE_SOC)

    def test_inspect_writes_new_manifest_bound_contract_and_closes_backend(self):
        directory, manifest, om, archive, tokenizer, config, _contract = self._artifacts()
        try:
            binding = verify_artifacts(
                manifest_path=manifest,
                om_path=om,
                tokenizer_zip_path=archive,
                tokenizer_path=tokenizer,
                tokenizer_config_path=config,
                contract_path=None,
                require_contract=False,
            )
            descriptor = RuntimeDescriptor(
                inputs=(
                    TensorDescriptor("input_ids", "int64", (1, 1), 8),
                    TensorDescriptor("attention_mask", "int64", (1, 1025), 8200),
                    TensorDescriptor("position_ids", "int64", (1, 1), 8),
                    TensorDescriptor("past_key_values", "float16", (22, 2, 1, 4, 1024, 64), 23068672),
                ),
                outputs=(
                    TensorDescriptor("logits", "float16", (1, 1, 32000), 64000),
                    TensorDescriptor("past_key_values_out", "float16", (22, 2, 1, 4, 1, 64), 22528),
                ),
            )

            class Backend:
                closed = False

                def open(self, _path):
                    return descriptor

                def close(self):
                    self.closed = True

            backend = Backend()
            target = Path(directory.name) / "new-contract.json"
            with patch(
                "tinyllama_acl_b1_compat.NativeTinyLlamaBackend", return_value=backend
            ) as backend_class:
                payload = inspect_and_bind_contract(om, binding, target, device_id=7)
            backend_class.assert_called_once_with(device_id=7)
            self.assertTrue(backend.closed)
            self.assertTrue(target.is_file())
            self.assertEqual(payload["source_revision"], "fixture-revision")
            self.assertEqual(payload["source_artifact"]["sha256"], binding.om_sha256)
        finally:
            directory.cleanup()

    def test_metadata_cannot_be_mistaken_for_admission(self):
        metadata = CompatibilityMetadata(
            SOURCE_SOC,
            TARGET_SOC,
            ("torch", "mindspore"),
            "a" * 64,
        )
        status = metadata.as_dict()
        self.assertTrue(status["compatibility_experiment"])
        self.assertEqual(status["source_soc"], SOURCE_SOC)
        self.assertEqual(status["board_soc"], TARGET_SOC)
        self.assertEqual(status["admission"], "not_admitted")
        self.assertEqual(status["om_sha256"], "a" * 64)


if __name__ == "__main__":
    unittest.main()
