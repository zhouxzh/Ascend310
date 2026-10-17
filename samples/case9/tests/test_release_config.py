from __future__ import annotations

import unittest

from ascend_edge_chat.artifacts.model_store import release_profile, verify_release_lock
from ascend_edge_chat.runtime.config import ConfigurationError, Settings


class ReleaseConfigTests(unittest.TestCase):
    def test_release_lock_and_profile_are_pinned(self) -> None:
        verify_release_lock()
        profile = release_profile()
        self.assertEqual(profile.id, "qwen1.5-0.5b-chat")
        self.assertEqual(profile.revision, "4d14e384a4b037942bb3f3016665157c8bcb70ea")
        self.assertEqual(profile.board_soc, "Ascend310B1")
        self.assertEqual(profile.board_tier, "20T")
        self.assertEqual(profile.license, "Tongyi Qianwen Research License Agreement")
        self.assertTrue(all(item.expected_bytes and item.sha256 for item in profile.artifacts))

    def test_release_lock_records_the_model_license_source(self) -> None:
        import json
        from ascend_edge_chat.artifacts.model_store import DEFAULT_LOCK

        lock = json.loads(DEFAULT_LOCK.read_text(encoding="utf-8"))
        self.assertEqual(lock["license"], "Tongyi Qianwen Research License Agreement")
        self.assertTrue(lock["license_url"].startswith("https://huggingface.co/"))

    def test_settings_use_only_release_namespace(self) -> None:
        settings = Settings.from_environ(
            {
                "ASCEND_EDGE_CHAT_GATEWAY_API_KEY": "a" * 24,
                "ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL": "http://127.0.0.1:8090/v1",
                "GATEWAY_API_KEY": "legacy-value-must-not-be-read",
            }
        )
        self.assertEqual(settings.public_model_id, "ascend-edge-chat")
        self.assertEqual(settings.upstream_model, "ascend-edge-chat-worker")

    def test_legacy_environment_cannot_supply_a_gateway_key(self) -> None:
        with self.assertRaises(ConfigurationError):
            Settings.from_environ(
                {
                    "GATEWAY_API_KEY": "a" * 24,
                    "ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL": "http://127.0.0.1:8090/v1",
                }
            )

    def test_release_rejects_non_loopback_worker(self) -> None:
        with self.assertRaises(ConfigurationError):
            Settings.from_environ(
                {
                    "ASCEND_EDGE_CHAT_GATEWAY_API_KEY": "a" * 24,
                    "ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL": "http://192.168.1.101:8090/v1",
                }
            )

    def test_release_rejects_retrieval_configuration(self) -> None:
        with self.assertRaises(ConfigurationError):
            Settings.from_environ(
                {
                    "ASCEND_EDGE_CHAT_GATEWAY_API_KEY": "a" * 24,
                    "ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL": "http://127.0.0.1:8090/v1",
                    "ASCEND_EDGE_CHAT_RAG_ENABLED": "true",
                }
            )

    def test_data_directory_must_be_absolute(self) -> None:
        from ascend_edge_chat.artifacts.model_store import ArtifactError, data_root

        with self.assertRaises(ArtifactError):
            data_root("models")

    def test_release_configuration_has_only_one_profile(self) -> None:
        profile = release_profile()
        self.assertEqual(profile.id, "qwen1.5-0.5b-chat")
        self.assertEqual(len(profile.artifacts), 7)

    def test_service_requirements_are_hash_locked(self) -> None:
        root = __import__("pathlib").Path(__file__).resolve().parents[1]
        text = (root / "configs" / "release-service-requirements.txt").read_text(encoding="utf-8")
        self.assertEqual(text.count("--hash=sha256:"), 8)
        self.assertNotIn(">=", text)
