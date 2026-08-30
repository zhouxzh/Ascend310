from __future__ import annotations

import unittest

from config import ConfigurationError, Settings


def valid_environment() -> dict[str, str]:
    return {
        "GATEWAY_API_KEY": "gateway-token-0123456789abcdef",
        "UPSTREAM_BASE_URL": "http://127.0.0.1:8080/v1",
        "UPSTREAM_MODEL": "fixed-upstream-model",
    }


class ConfigurationTests(unittest.TestCase):
    def test_required_secrets_and_upstream_are_validated(self) -> None:
        settings = Settings.from_environ(valid_environment())
        self.assertEqual(settings.public_model_id, "case9-rag")
        self.assertEqual(settings.upstream_base_url, "http://127.0.0.1:8080/v1")
        self.assertEqual(settings.stream_write_timeout_seconds, 30.0)

    def test_stream_write_timeout_can_be_configured_and_must_be_positive(self) -> None:
        environment = valid_environment()
        environment["STREAM_WRITE_TIMEOUT_SECONDS"] = "0.5"
        settings = Settings.from_environ(environment)
        self.assertEqual(settings.stream_write_timeout_seconds, 0.5)

        environment["STREAM_WRITE_TIMEOUT_SECONDS"] = "0"
        with self.assertRaises(ConfigurationError):
            Settings.from_environ(environment)

    def test_upstream_url_cannot_embed_credentials(self) -> None:
        environment = valid_environment()
        environment["UPSTREAM_BASE_URL"] = "https://key@example.test/v1"
        with self.assertRaises(ConfigurationError):
            Settings.from_environ(environment)

    def test_upstream_defaults_to_loopback_only(self) -> None:
        environment = valid_environment()
        environment["UPSTREAM_BASE_URL"] = "https://api.example.test/v1"
        with self.assertRaisesRegex(ConfigurationError, "host is not allowed"):
            Settings.from_environ(environment)

        environment["UPSTREAM_ALLOWED_HOSTS"] = "api.example.test"
        settings = Settings.from_environ(environment)
        self.assertEqual(settings.upstream_base_url, "https://api.example.test/v1")

    def test_malformed_upstream_port_and_empty_knowledge_dir_are_rejected(self) -> None:
        environment = valid_environment()
        environment["UPSTREAM_BASE_URL"] = "http://127.0.0.1:not-a-port/v1"
        with self.assertRaises(ConfigurationError):
            Settings.from_environ(environment)

        environment = valid_environment()
        environment["UPSTREAM_BASE_URL"] = "http://127.0.0.1:0/v1"
        with self.assertRaises(ConfigurationError):
            Settings.from_environ(environment)

        environment = valid_environment()
        environment["RAG_DOCUMENTS_DIR"] = "   "
        with self.assertRaisesRegex(ConfigurationError, "must not be empty"):
            Settings.from_environ(environment)

    def test_tinyllama_defaults_to_single_concurrent_request(self) -> None:
        environment = valid_environment()
        environment["UPSTREAM_MODEL"] = "tiny-llama-1.1b-acl-om"
        settings = Settings.from_environ(environment)
        self.assertEqual(settings.max_concurrent_requests, 1)

    def test_qwen_static_kv_defaults_to_single_concurrent_request(self) -> None:
        environment = valid_environment()
        environment["UPSTREAM_MODEL"] = (
            "qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om"
        )
        settings = Settings.from_environ(environment)
        self.assertEqual(settings.max_concurrent_requests, 1)
        self.assertEqual(settings.upstream_timeout_seconds, 270.0)
        self.assertEqual(settings.stream_max_seconds, 270.0)

    def test_gateway_token_is_required(self) -> None:
        environment = valid_environment()
        del environment["GATEWAY_API_KEY"]
        with self.assertRaises(ConfigurationError):
            Settings.from_environ(environment)

    def test_placeholder_token_and_non_finite_values_are_rejected(self) -> None:
        environment = valid_environment()
        environment["GATEWAY_API_KEY"] = "replace-with-a-random-long-token"
        with self.assertRaises(ConfigurationError):
            Settings.from_environ(environment)

        environment = valid_environment()
        environment["STREAM_MAX_SECONDS"] = "nan"
        with self.assertRaises(ConfigurationError):
            Settings.from_environ(environment)
