from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from palmprint_workbench.config import ROOT
from tools.export.prepare_models import (
    COMPNET_FEATURE_DIM,
    COMPNET_INPUT_SHAPE,
    COMPNET_VARIANT_IDS,
    compnet_variant_conversion_paths,
    compnet_variant_marker_path,
    compnet_variant_output_path,
)


class CompNetVariantPathTests(unittest.TestCase):
    def test_allow_list_has_five_distinct_dataset_variants(self):
        self.assertEqual(len(COMPNET_VARIANT_IDS), 5)
        self.assertEqual(len(set(COMPNET_VARIANT_IDS)), 5)
        self.assertTrue(all(identifier.startswith("compnet_") for identifier in COMPNET_VARIANT_IDS))

    def test_output_and_marker_names_are_id_scoped(self):
        with tempfile.TemporaryDirectory() as directory:
            output_dir = Path(directory) / "onnx"
            marker_dir = Path(directory) / "markers"
            for identifier in COMPNET_VARIANT_IDS:
                output = compnet_variant_output_path(output_dir, identifier)
                marker = compnet_variant_marker_path(marker_dir, identifier)
                self.assertEqual(output.name, f"{identifier}.onnx")
                self.assertEqual(marker.name, f"{identifier}_export.json")
                self.assertEqual(output.parent, output_dir.resolve())
                self.assertEqual(marker.parent, marker_dir.resolve())
            with self.assertRaises(ValueError):
                compnet_variant_output_path(output_dir, "../../outside")
            with self.assertRaises(ValueError):
                compnet_variant_marker_path(marker_dir, "unknown")

    def test_manifest_contract_and_conversion_paths_match_exporter(self):
        payload = json.loads((ROOT / "candidate_manifest.json").read_text(encoding="utf-8"))
        candidates = {
            candidate["id"]: candidate
            for candidate in payload["candidates"]
            if candidate["id"] in COMPNET_VARIANT_IDS
        }
        self.assertEqual(set(candidates), set(COMPNET_VARIANT_IDS))
        for identifier in COMPNET_VARIANT_IDS:
            candidate = candidates[identifier]
            self.assertIn("float32[1,1,128,128]", candidate["input_contract"])
            self.assertIn("512-D", candidate["output_contract"])
            conversion = candidate["conversion"]
            self.assertEqual(conversion["onnx_path"], f"models/onnx/{identifier}.onnx")
            self.assertEqual(
                conversion["marker_path"],
                f"models/checkpoints/{identifier}_export.json",
            )
            self.assertEqual(
                conversion["om_paths"]["mixed_fp16"],
                f"models/om/{identifier}_mixed_fp16.om",
            )
            paths = compnet_variant_conversion_paths(identifier)
            self.assertEqual(paths["onnx"], ROOT / f"models/onnx/{identifier}.onnx")
            self.assertEqual(
                paths["origin"], ROOT / f"models/om/{identifier}_origin.om"
            )
            self.assertEqual(
                paths["mixed_fp16"],
                ROOT / f"models/om/{identifier}_mixed_fp16.om",
            )

    def test_export_contract_constants_are_fixed_shape(self):
        self.assertEqual(COMPNET_INPUT_SHAPE, (1, 1, 128, 128))
        self.assertEqual(COMPNET_FEATURE_DIM, 512)


if __name__ == "__main__":
    unittest.main()
