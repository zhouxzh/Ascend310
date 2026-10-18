from __future__ import annotations

import unittest

from ascend_edge_chat.model_catalog import catalog_by_id, load_catalog
from ascend_edge_chat.backends.model_profiles import load_profiles


class TeachingModelCatalogTests(unittest.TestCase):
    def test_quality_failure_does_not_hide_a_technically_tested_model(self) -> None:
        entries = catalog_by_id()
        qwen2 = entries["qwen2-1.5b-instruct-mindspore"]
        self.assertEqual(qwen2["technical_status"], "observed-pass")
        self.assertEqual(qwen2["quality_status"], "failed")
        self.assertTrue(qwen2["selectable"])

    def test_unrun_loader_is_not_selectable(self) -> None:
        entries = catalog_by_id()
        qwen3 = entries["qwen3-0.6b-mindspore"]
        self.assertEqual(qwen3["technical_status"], "blocked")
        self.assertFalse(qwen3["selectable"])

    def test_all_selectable_profiles_have_runtime_registry_entries(self) -> None:
        entries = load_catalog()
        registry = load_profiles("configs/tutorial-models.json")
        registry_ids = {profile.id for profile in registry}
        self.assertTrue(all(item["id"] in registry_ids for item in entries if item["selectable"]))


if __name__ == "__main__":
    unittest.main()
