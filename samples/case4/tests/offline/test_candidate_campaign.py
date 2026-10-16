from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from tools.offline.candidate_campaign import (
    STATUS_VALUES,
    _board_preflight,
    board_campaign,
    inventory_candidates,
    local_campaign,
)


class CandidateCampaignTests(unittest.TestCase):
    def test_inventory_covers_all_candidates_without_absolute_paths(self) -> None:
        records = inventory_candidates()
        self.assertEqual(len(records), 28)
        self.assertEqual(len({item["candidate_id"] for item in records}), 28)
        for item in records:
            self.assertIn(item["status"], STATUS_VALUES)
            self.assertTrue(item["candidate_id"])
            for artifact in item["artifacts"]:
                self.assertFalse(Path(artifact["path"]).is_absolute())
                self.assertFalse("<" in artifact["path"])
            self.assertFalse(item["production_enabled"])

    def test_known_runtime_assets_are_hash_verified(self) -> None:
        records = {item["candidate_id"]: item for item in inventory_candidates()}
        self.assertEqual(records["ccnet"]["status"], "audit_pass")
        self.assertTrue(records["ccnet"]["gates"]["artifact_integrity"])
        for candidate_id in (
            "compnet_tongji_600",
            "compnet_iitd_460",
            "compnet_rest_358",
            "compnet_xjtu_flash_200",
            "compnet_xjtu_natural_200",
        ):
            candidate = records[candidate_id]
            self.assertEqual(candidate["status"], "audit_pass")
            self.assertTrue(candidate["gates"]["artifact_integrity"])

    def test_local_campaign_writes_complete_report_contract_and_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            report_root = Path(directory) / "candidates"
            records = local_campaign("ccnet", report_root=report_root)
            self.assertEqual(len(records), 1)
            run_dirs = [item for item in (report_root / "ccnet").iterdir() if item.is_dir()]
            self.assertEqual(len(run_dirs), 1)
            expected = {
                "manifest.json",
                "source-and-license.json",
                "artifact-hashes.json",
                "contract.json",
                "local-reference.json",
                "atc.json",
                "acl-smoke.json",
                "numeric-consistency.json",
                "quality.json",
                "performance.json",
                "lifecycle.json",
                "final-status.json",
            }
            self.assertEqual({item.name for item in run_dirs[0].iterdir()}, expected)
            index = json.loads((report_root / "index.json").read_text(encoding="utf-8"))
            self.assertTrue(index["production_registry_unchanged"])
            self.assertEqual(index["items"][0]["candidate_id"], "ccnet")

    def test_sdk_and_edcc_are_explicitly_not_applicable(self) -> None:
        records = {item["candidate_id"]: item for item in inventory_candidates()}
        for candidate_id in ("edcc", "kby_palmprint_sdk", "kby_palmvein_sdk", "tencent_palm_ai"):
            self.assertEqual(records[candidate_id]["status"], "not_applicable")
            self.assertFalse(records[candidate_id]["production_enabled"])

    def test_board_command_does_not_route_non_embedding_candidates_to_acl(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            payload = board_campaign(
                "edcc",
                Path(directory) / "unused.png",
                report_root=Path(directory) / "reports",
            )
            self.assertEqual(payload["status"], "not_applicable")
            self.assertIn("task-specific adapter", payload["failures"][0])

    def test_board_preflight_uses_om_only_gate_for_manual_candidates(self) -> None:
        # The board release intentionally excludes research checkpoints and
        # ONNX exports; their absence must not block a hash-verified OM smoke.
        self.assertIsNone(_board_preflight("ccnet"))
        for candidate_id in (
            "compnet_tongji_600",
            "compnet_iitd_460",
            "compnet_rest_358",
            "compnet_xjtu_flash_200",
            "compnet_xjtu_natural_200",
        ):
            self.assertIsNone(_board_preflight(candidate_id))


if __name__ == "__main__":
    unittest.main()
