from __future__ import annotations

import json
import re
import unittest

from ascend_edge_chat.artifacts.model_store import DEFAULT_TUTORIAL_LOCK
from ascend_edge_chat.model_catalog import catalog_by_id, load_catalog
from ascend_edge_chat.backends.model_profiles import load_profiles


class TeachingModelCatalogTests(unittest.TestCase):
    def test_template_recheck_does_not_become_a_quality_verdict(self) -> None:
        entries = catalog_by_id()
        qwen2 = entries["qwen2-1.5b-instruct-mindspore"]
        self.assertEqual(qwen2["technical_status"], "observed-pass")
        self.assertEqual(qwen2["quality_status"], "pending")
        self.assertEqual(qwen2["text_integrity_status"], "passed")
        self.assertTrue(qwen2["selectable"])

    def test_catalog_exposes_default_and_hard_token_budgets(self) -> None:
        entries = catalog_by_id()
        qwen2 = entries["qwen2-1.5b-instruct-mindspore"]
        self.assertEqual(qwen2["default_max_tokens"], 128)
        self.assertEqual(qwen2["max_tokens"], 192)

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

    def test_selectable_profiles_have_complete_tutorial_artifact_locks(self) -> None:
        entries = load_catalog()
        registry = load_profiles("configs/tutorial-models.json")
        lock = json.loads(DEFAULT_TUTORIAL_LOCK.read_text(encoding="utf-8"))["profiles"]
        for entry in entries:
            if not entry["selectable"]:
                continue
            profile = registry.get(entry["id"])
            recorded = lock[profile.id]
            self.assertEqual(recorded["repository"], profile.repository)
            self.assertEqual(recorded["revision"], profile.revision)
            expected_files = {item.filename for item in profile.artifacts}
            artifacts = recorded["artifacts"]
            self.assertTrue(expected_files.issubset({item["filename"] for item in artifacts}))
            for item in artifacts:
                self.assertIsInstance(item["bytes"], int)
                self.assertGreater(item["bytes"], 0)
                self.assertRegex(item["sha256"], re.compile(r"^[0-9a-f]{64}$"))

    def test_catalog_does_not_claim_a_pass_for_an_unsupported_soc(self) -> None:
        entries = catalog_by_id()
        registry = load_profiles("configs/tutorial-models.json")
        for profile_id, entry in entries.items():
            if not entry["selectable"]:
                continue
            profile = registry.get(profile_id)
            for board, status in entry["board_results"].items():
                soc = board.split("/", 1)[0]
                if not profile.supports_soc(soc):
                    self.assertIn(status, {"not-run", "blocked"})


if __name__ == "__main__":
    unittest.main()
