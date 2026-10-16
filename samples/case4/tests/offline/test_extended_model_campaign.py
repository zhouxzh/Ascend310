from __future__ import annotations

import unittest

from tools.offline.extended_model_campaign import (
    ARCHITECTURE_SPECS,
    SPECIALIZED_SPECS,
)


class ExtendedModelCampaignTests(unittest.TestCase):
    def test_architecture_inventory_is_explicit_and_unique(self) -> None:
        ids = [item["id"] for item in ARCHITECTURE_SPECS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(
            {item["family"] for item in ARCHITECTURE_SPECS},
            {"ResNet", "VGG", "DenseNet", "MobileNetV2", "EfficientNetV2-S", "ViT-B/16", "Swin-T"},
        )

    def test_specialized_inventory_never_claims_deployability(self) -> None:
        ids = [item["id"] for item in SPECIALIZED_SPECS]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(item["reason"] for item in SPECIALIZED_SPECS))
        self.assertTrue(all(item["task"] for item in SPECIALIZED_SPECS))


if __name__ == "__main__":
    unittest.main()
