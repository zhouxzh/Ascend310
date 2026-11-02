from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from ascend_edge_chat.backend_supervisor import BackendSupervisor, create_app
from ascend_edge_chat.static_kv.candidates import CandidateError, load_acl_candidate
from ascend_edge_chat.ui.web import TextChatConfigurationError, TextChatSettings


class BackendSupervisorTests(unittest.TestCase):
    def _candidate_data(self, root: Path) -> Path:
        for relative in (
            "om/qwen25-static-kv-1024-b1.om",
            "common/tokenizer.json",
            "common/tokenizer_config.json",
            "common/tokenizer.json.lock.json",
            "contracts/qwen25-static-kv-1024-fp32-contract.json",
            "contracts/qwen25-static-kv-1024-v2-om-contract.json",
        ):
            target = root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"fixture")
        return root

    def test_acl_candidate_resolves_only_declared_relative_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._candidate_data(Path(directory))
            candidate = load_acl_candidate(
                "qwen2.5-0.5b-instruct-static-kv-1024-b1-offline",
                data_dir=root,
            )
        self.assertEqual(candidate.om, root / "om/qwen25-static-kv-1024-b1.om")
        self.assertEqual(candidate.default_max_tokens, 32)
        self.assertEqual(candidate.max_tokens, 80)

    def test_acl_candidate_rejects_missing_artifacts_before_a_transition(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(CandidateError):
                load_acl_candidate(
                    "qwen2.5-0.5b-instruct-static-kv-1024-b1-offline",
                    data_dir=Path(directory),
                )

    def test_supervisor_only_exposes_allowlisted_backends(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            supervisor = BackendSupervisor(
                catalog_path=Path("configs/tutorial-model-catalog.json").resolve(),
                acl_catalog_path=Path("configs/20t-backend-catalog.json").resolve(),
                candidate_data_dir=self._candidate_data(Path(directory)),
            )
            app = create_app(supervisor)
            with TestClient(app) as client:
                status = client.get("/api/status")
                rejected = client.post("/api/select", json={"backend": "http://evil", "model": "x"})
        self.assertEqual(status.status_code, 200)
        self.assertEqual({item["id"] for item in status.json()["backends"]}, {"mindspore", "acl-static-kv"})
        self.assertEqual(rejected.status_code, 409)

    def test_acl_candidate_rejects_an_untracked_path_outside_candidate_root(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = self._candidate_data(Path(directory))
            catalog = Path("configs/20t-backend-catalog.json").read_text(encoding="utf-8")
            self.assertIn('"om": "om/qwen25-static-kv-1024-b1.om"', catalog)
            self.assertNotIn('"om": "/', catalog)
            candidate = load_acl_candidate(
                "qwen2.5-0.5b-instruct-static-kv-1024-b1-offline",
                data_dir=root,
            )
            self.assertEqual(candidate.om.relative_to(root), Path("om/qwen25-static-kv-1024-b1.om"))

    def test_b1_catalog_uses_default_32_but_admits_80_tokens(self) -> None:
        catalog = __import__("json").loads(
            Path("configs/20t-backend-catalog.json").read_text(encoding="utf-8")
        )
        acl = next(item for item in catalog["backends"] if item["id"] == "acl-static-kv")
        candidate = acl["models"][0]
        self.assertEqual(candidate["default_max_tokens"], 32)
        self.assertEqual(candidate["max_tokens"], 80)

    def test_ui_supervisor_url_must_be_loopback(self) -> None:
        with self.assertRaises(TextChatConfigurationError):
            TextChatSettings(
                gateway_api_key="gateway-token-0123456789",
                catalog_path=str(Path("configs/tutorial-model-catalog.json").resolve()),
                backend_supervisor_url="http://192.168.1.5:7863",
            )


if __name__ == "__main__":
    unittest.main()
