from __future__ import annotations

import unittest

from ascend_edge_chat.artifacts.model_store import release_profile, verify_release_lock
from ascend_edge_chat.backends.runtime_preflight import (
    RuntimePreflightError,
    UNIFIED_CONDA_ENV,
    UNIFIED_SOABI,
    validate_runtime_lock_data,
)
from ascend_edge_chat.runtime.config import ConfigurationError, Settings


class ReleaseConfigTests(unittest.TestCase):
    def test_release_lock_and_profile_are_pinned(self) -> None:
        verify_release_lock()
        profile = release_profile()
        self.assertEqual(profile.id, "qwen1.5-0.5b-chat")
        self.assertEqual(profile.revision, "4d14e384a4b037942bb3f3016665157c8bcb70ea")
        self.assertEqual(profile.board_soc, "Ascend310B1")
        self.assertEqual(profile.board_tier, "20T")
        self.assertEqual(
            {(item["soc"], item["tier"]) for item in profile.board_targets},
            {("Ascend310B1", "20T"), ("Ascend310B4", "8T")},
        )
        self.assertEqual(profile.license, "Tongyi Qianwen Research License Agreement")
        self.assertTrue(all(item.expected_bytes and item.sha256 for item in profile.artifacts))

    def test_release_lock_records_the_model_license_source(self) -> None:
        import json
        from ascend_edge_chat.artifacts.model_store import DEFAULT_LOCK

        lock = json.loads(DEFAULT_LOCK.read_text(encoding="utf-8"))
        self.assertEqual(lock["license"], "Tongyi Qianwen Research License Agreement")
        self.assertTrue(lock["license_url"].startswith("https://huggingface.co/"))

    def test_shared_release_catalog_declares_mindspore_on_both_boards(self) -> None:
        import json
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        catalog = json.loads((root / "configs" / "release-model-catalog.json").read_text(encoding="utf-8"))
        entry = catalog["models"][0]
        self.assertEqual(entry["id"], "qwen1.5-0.5b-chat")
        self.assertEqual(entry["worker_registry"], "configs/release-model.json")
        self.assertEqual(
            entry["board_results"],
            {"Ascend310B1/20T": "observed-pass", "Ascend310B4/8T": "observed-pass"},
        )
        for filename, soc, tier in (
            ("20t-backend-catalog.json", "Ascend310B1", "20T"),
            ("8t-backend-catalog.json", "Ascend310B4", "8T"),
        ):
            backend_catalog = json.loads((root / "configs" / filename).read_text(encoding="utf-8"))
            self.assertEqual(backend_catalog["target"], {"soc": soc, "tier": tier})
            self.assertEqual(backend_catalog["backends"][0]["id"], "mindspore")
            self.assertEqual(backend_catalog["backends"][0]["default_model"], entry["id"])

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

    def test_acl_static_kv_backend_has_a_fixed_loopback_contract(self) -> None:
        settings = Settings.from_environ(
            {
                "ASCEND_EDGE_CHAT_GATEWAY_API_KEY": "a" * 24,
                "ASCEND_EDGE_CHAT_BACKEND": "acl-static-kv",
                "ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL": "http://127.0.0.1:8084/v1",
            }
        )
        self.assertEqual(settings.backend, "acl-static-kv")
        self.assertEqual(settings.upstream_base_url, "http://127.0.0.1:8084/v1")
        self.assertEqual(
            settings.upstream_model,
            "qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om",
        )
        self.assertEqual(settings.upstream_context_tokens, 1024)
        self.assertEqual(settings.public_model_id, "ascend-edge-chat")

    def test_mindspore_rejects_nonproduction_loopback_ports(self) -> None:
        with self.assertRaises(ConfigurationError):
            Settings.from_environ(
                {
                    "ASCEND_EDGE_CHAT_GATEWAY_API_KEY": "a" * 24,
                    "ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL": "http://127.0.0.1:8091/v1",
                }
            )

    def test_backend_rejects_model_or_port_cross_wiring(self) -> None:
        baseline = {
            "ASCEND_EDGE_CHAT_GATEWAY_API_KEY": "a" * 24,
            "ASCEND_EDGE_CHAT_BACKEND": "acl-static-kv",
            "ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL": "http://127.0.0.1:8084/v1",
        }
        with self.assertRaises(ConfigurationError):
            Settings.from_environ(
                {
                    **baseline,
                    "ASCEND_EDGE_CHAT_UPSTREAM_MODEL": "ascend-edge-chat-worker",
                }
            )
        with self.assertRaises(ConfigurationError):
            Settings.from_environ(
                {
                    **baseline,
                    "ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL": "http://127.0.0.1:8090/v1",
                }
            )
        with self.assertRaises(ConfigurationError):
            Settings.from_environ(
                {
                    "ASCEND_EDGE_CHAT_GATEWAY_API_KEY": "a" * 24,
                    "ASCEND_EDGE_CHAT_BACKEND": "mindspore",
                    "ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL": "http://127.0.0.1:8084/v1",
                    "ASCEND_EDGE_CHAT_UPSTREAM_MODEL": "ascend-edge-chat-worker",
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
        package_lines = [line for line in text.splitlines() if "==" in line and not line.lstrip().startswith("#")]
        self.assertGreaterEqual(len(package_lines), 16)
        self.assertEqual(text.count("--hash=sha256:"), len(package_lines))
        self.assertIn("httpx==0.27.2", text)
        self.assertIn("httpcore==1.0.7", text)
        self.assertIn("anyio==4.6.2.post1", text)
        installer = (root / "scripts" / "verify" / "install-service-dependencies.sh").read_text(encoding="utf-8")
        self.assertIn("--only-binary=:all:", installer)
        self.assertNotIn(">=", text)

    def test_8t_requirements_extend_the_hashed_service_closure(self) -> None:
        root = __import__("pathlib").Path(__file__).resolve().parents[1]
        text = (root / "configs" / "release-8t-service-requirements.txt").read_text(encoding="utf-8")
        self.assertIn("-r release-service-requirements.txt", text)
        self.assertIn("numpy==1.26.4", text)
        self.assertIn("tokenizers==0.19.1", text)
        self.assertNotIn("https://", text)
        self.assertEqual(text.count("--hash=sha256:"), 2)

    def test_8t_preflight_locks_static_runtime_dependency_versions(self) -> None:
        root = __import__("pathlib").Path(__file__).resolve().parents[1]
        text = (root / "scripts" / "verify" / "release-8t-preflight.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn("import importlib.metadata", text)
        self.assertIn('(\"numpy\", \"1.26.4\")', text)
        self.assertIn('(\"tokenizers\", \"0.19.1\")', text)
        self.assertIn("8T release requires %s==%s, got %s", text)

    def test_dependency_installer_requires_ascend_and_local_wheelhouse(self) -> None:
        root = __import__("pathlib").Path(__file__).resolve().parents[1]
        text = (root / "scripts" / "verify" / "install-service-dependencies.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn('[[ "${conda_env}" == "ascend" ]]', text)
        self.assertIn("--no-index --no-deps", text)
        self.assertIn('--find-links "${wheelhouse}"', text)

    def test_release_preflight_uses_the_shared_runtime_lock(self) -> None:
        root = __import__("pathlib").Path(__file__).resolve().parents[1]
        text = (root / "scripts" / "verify" / "release-preflight.sh").read_text(encoding="utf-8")
        self.assertIn("validate_runtime_lock", text)
        self.assertIn("PYTHONNOUSERSITE=1", text)
        self.assertNotIn("EXPECTED_PYTHON", text)
        self.assertNotIn("gateway --check-config", text)

    def test_runtime_lock_accepts_the_unified_ascend_environment(self) -> None:
        lock = {
            "target": {
                "boards": [
                    {"soc": "Ascend310B1", "tier": "20T"},
                    {"soc": "Ascend310B4", "tier": "8T"},
                ],
                "python": "3.9.25",
                "cann": "8.0.0",
                "mindspore": "2.4.10",
                "mindnlp": "0.4.1",
            }
        }
        observed = {
            "python": "3.9.25",
            "soabi": UNIFIED_SOABI,
            "board_soc": "Ascend310B1",
            "board_tier": "20T",
            "conda_env": UNIFIED_CONDA_ENV,
            "python_no_user_site": "1",
            "user_site_enabled": False,
            "imports": {"acl": True, "mindspore": True, "mindnlp": True},
            "package_versions": {"mindspore": "2.4.10", "mindnlp": "0.4.1"},
            "cann": "8.0.0",
        }

        report = validate_runtime_lock_data(lock, observed)

        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["expected"]["conda_env"], "ascend")

    def test_tutorial_runtime_keeps_unified_gate_without_requiring_release_versions(self) -> None:
        lock = {
            "target": {
                "boards": [
                    {"soc": "Ascend310B1", "tier": "20T"},
                    {"soc": "Ascend310B4", "tier": "8T"},
                ],
                "python": "3.9.25",
                "cann": "8.0.0",
                "mindspore": "2.4.10",
                "mindnlp": "0.4.1",
            }
        }
        observed = {
            "python": "3.9.25",
            "soabi": UNIFIED_SOABI,
            "board_soc": "Ascend310B1",
            "board_tier": "20T",
            "conda_env": UNIFIED_CONDA_ENV,
            "python_no_user_site": "1",
            "user_site_enabled": False,
            "imports": {"acl": True, "mindspore": True, "mindnlp": True},
            "package_versions": {"mindspore": "2.2.14", "mindnlp": "0.3.1"},
            "cann": "7.0.0",
        }
        report = validate_runtime_lock_data(lock, observed, strict_target=False)
        self.assertEqual(report["status"], "passed")
        self.assertFalse(report["strict_target"])

    def test_runtime_lock_reports_package_drift_without_falling_back(self) -> None:
        lock = {
            "target": {
                "boards": [
                    {"soc": "Ascend310B1", "tier": "20T"},
                    {"soc": "Ascend310B4", "tier": "8T"},
                ],
                "python": "3.9.25",
                "cann": "8.0.0",
                "mindspore": "2.4.10",
                "mindnlp": "0.4.1",
            }
        }
        observed = {
            "python": "3.9.25",
            "soabi": UNIFIED_SOABI,
            "board_soc": "Ascend310B1",
            "board_tier": "20T",
            "conda_env": UNIFIED_CONDA_ENV,
            "python_no_user_site": "1",
            "user_site_enabled": False,
            "imports": {"acl": True, "mindspore": True, "mindnlp": True},
            "package_versions": {"mindspore": "2.4.9", "mindnlp": "0.4.1"},
            "cann": "8.0.0",
        }

        with self.assertRaises(RuntimePreflightError) as captured:
            validate_runtime_lock_data(lock, observed)

        fields = {item["field"] for item in captured.exception.details["errors"]}
        self.assertIn("mindspore", fields)
