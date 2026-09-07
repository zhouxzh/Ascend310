from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ACCEPTANCE = load_module("mindspore_chat_acceptance_test", ROOT / "scripts" / "mindspore_chat_acceptance.py")


class MindSporeAcceptanceUnitTests(unittest.TestCase):
    def test_sse_suffix_and_cumulative_prefix_are_not_duplicated(self):
        lines = [
            'data: {"choices":[{"delta":{"content":"\u4f60"}}]}\n',
            '\n',
            'data: {"choices":[{"delta":{"content":"\u4f60\u597d"}}]}\n',
            '\n',
            'data: {"choices":[{"delta":{"content":"\u597d"}}]}\n',
            '\n',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"completion_tokens":2}}\n',
            '\n',
            'data: [DONE]\n',
            '\n',
        ]
        result = ACCEPTANCE.parse_sse_lines(lines)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["text"], "\u4f60\u597d")
        self.assertTrue(result["duplicate_delta"])
        self.assertFalse(result["machine_valid"])

    def test_sse_suffix_stream_passes_without_duplicate_flag(self):
        lines = [
            'data: {"choices":[{"delta":{"content":"\u4f60"}}]}\n\n',
            'data: {"choices":[{"delta":{"content":"\u597d"}}]}\n\n',
            'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"completion_tokens":2}}\n\n',
            'data: [DONE]\n\n',
        ]
        result = ACCEPTANCE.parse_sse_lines(lines)
        self.assertEqual(result["text"], "\u4f60\u597d")
        self.assertFalse(result["duplicate_delta"])
        self.assertTrue(result["machine_valid"])

    def test_summary_names_rate_units_explicitly(self):
        summary = ACCEPTANCE.summarize([1.0, 2.0, 3.0], unit="tokens_per_second")
        self.assertEqual(summary["p50_tokens_per_second"], 2.0)
        self.assertNotIn("p50_ms", summary)

    def test_probe_fixture_declares_zh_and_en_languages(self):
        fixture_path = ROOT / "tests" / "fixtures" / "mindspore_chat_probe.json"
        probes = ACCEPTANCE._load_probes(fixture_path)
        self.assertEqual(len(probes), 10)
        self.assertEqual({probe["language"] for probe in probes}, {"zh", "en"})
        self.assertEqual(sum(probe["language"] == "zh" for probe in probes), 5)
        self.assertEqual(sum(probe["language"] == "en" for probe in probes), 5)

    def test_candidate_probe_fixture_is_reproducible(self):
        fixture_path = ROOT / "tests" / "fixtures" / "mindspore_chat_candidate_probe.json"
        probes = ACCEPTANCE._load_probes(fixture_path)
        self.assertEqual(len(probes), 10)
        self.assertEqual([probe["id"] for probe in probes[:2]], ["identity-zh", "addition-zh"])
        self.assertEqual({probe["language"] for probe in probes}, {"zh", "en"})

    def test_probe_language_defaults_to_chinese_for_legacy_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.json"
            path.write_text(json.dumps([{"id": "legacy", "prompt": "你好"}], ensure_ascii=False), encoding="utf-8")
            probes = ACCEPTANCE._load_probes(path)
        self.assertEqual(probes, [{"id": "legacy", "language": "zh", "prompt": "你好"}])

    def test_probe_rejects_unknown_language(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.json"
            path.write_text(json.dumps([{"id": "bad", "language": "fr", "prompt": "bonjour"}]), encoding="utf-8")
            with self.assertRaises(ACCEPTANCE.AcceptanceError):
                ACCEPTANCE._load_probes(path)

    def test_quality_summary_partitions_machine_validity_by_language(self):
        summary = ACCEPTANCE._quality_summary([
            {"id": "zh-1", "language": "zh", "machine_valid": True},
            {"id": "zh-2", "language": "zh", "machine_valid": False},
            {"id": "en-1", "language": "en", "machine_valid": True},
        ])
        self.assertEqual(summary["machine_valid_count"], 2)
        self.assertEqual(summary["by_language"]["zh"]["probe_count"], 2)
        self.assertEqual(summary["by_language"]["zh"]["machine_valid_count"], 1)
        self.assertEqual(summary["by_language"]["zh"]["machine_valid_rate"], 0.5)
        self.assertEqual(summary["by_language"]["en"]["machine_valid_rate"], 1.0)

    def test_expected_error_requires_status_and_structured_code(self):
        accepted = ACCEPTANCE._mark_expected_error(
            {
                "status": "error",
                "http_status": 404,
                "body": {"error": {"code": "model_not_found", "type": "invalid_request_error"}},
            },
            case="wrong_model",
            expected_status=404,
            expected_code="model_not_found",
        )
        self.assertEqual(accepted["contract_status"], "passed")
        rejected = ACCEPTANCE._mark_expected_error(
            {"status": "error", "http_status": 400, "body": {"error": {"message": "bad"}}},
            case="wrong_role",
            expected_status=400,
            expected_code="invalid_request_error",
        )
        self.assertEqual(rejected["contract_status"], "failed")

    def test_health_gate_requires_npu_identity_and_worker_fingerprint(self):
        base = {
            "http_status": 200,
            "body": {
                "ready": True,
                "healthy": True,
                "profile": "qwen1.5-0.5b-mindspore",
                "model_id": "case9-active",
                "busy": False,
                "cache_cleared": True,
                "npu_model": "Ascend310B4",
                "device_target": "Ascend",
                "worker_pid": 1234,
                "environment_fingerprint": "a" * 64,
            },
        }
        passed, checks = ACCEPTANCE._health_ok(base, "qwen1.5-0.5b-mindspore")
        self.assertTrue(passed)
        self.assertTrue(checks["npu_model_present"])
        self.assertTrue(checks["npu_model_matches_profile"])
        missing = {"http_status": 200, "body": dict(base["body"])}
        missing["body"]["npu_model"] = None
        passed, checks = ACCEPTANCE._health_ok(missing, "qwen1.5-0.5b-mindspore")
        self.assertFalse(passed)
        self.assertFalse(checks["npu_model_present"])

    def test_board_and_soc_select_declared_target_and_reject_mismatch(self):
        metadata = ACCEPTANCE.load_profile_metadata("qwen1.5-0.5b-mindspore")
        target = ACCEPTANCE._resolve_target(metadata, "20t", None)
        self.assertEqual(target["soc"], "Ascend310B1")
        self.assertEqual(target["tier"], "20T")
        target = ACCEPTANCE._resolve_target(metadata, None, "310B4")
        self.assertEqual(target["host"], "192.168.1.90")
        # Former addresses remain selectors for historical reports but
        # normalize to the current canonical host.
        legacy_target = ACCEPTANCE._resolve_target(metadata, "192.168.1.90", None)
        self.assertEqual(legacy_target["host"], "192.168.1.90")
        self.assertEqual(
            ACCEPTANCE._resolve_target(metadata, "192.168.8.178", None)["host"],
            "192.168.1.90",
        )
        with self.assertRaises(ACCEPTANCE.AcceptanceError):
            ACCEPTANCE._resolve_target(metadata, "8t", "Ascend310B1")
        with self.assertRaises(ACCEPTANCE.AcceptanceError):
            ACCEPTANCE._resolve_target(metadata, "192.0.2.1", None)

    def test_validate_options_uses_per_soc_status_when_aggregate_is_blocked(self):
        base = argparse.Namespace(
            profile="deepseek-r1-qwen-1.5b-mindspore",
            board="8t",
            soc=None,
            host="127.0.0.1",
            port=8090,
            output=None,
            run_id="unit-deepseek-b4",
            timeout=1.0,
            long_budgets="8",
            stability_loops=1,
            stability_max_tokens=1,
            perf_warmup=0,
            perf_loops=1,
            perf_max_tokens=1,
            probe_max_tokens=1,
            probe_file=None,
            environment_report=None,
            artifact_report=None,
            require_evidence=False,
            execute=False,
            dry_run=True,
            skip_quality=True,
            skip_snapshots=True,
        )
        options = ACCEPTANCE.validate_options(base)
        self.assertEqual(options.soc, "Ascend310B4")
        blocked = argparse.Namespace(**vars(base))
        blocked.board = "20t"
        with self.assertRaises(ACCEPTANCE.AcceptanceError):
            ACCEPTANCE.validate_options(blocked)

    def test_validate_options_rejects_aggregate_not_run_without_selector(self):
        """Legacy calls cannot promote a not-run aggregate via B4 metadata."""

        metadata = ACCEPTANCE.load_profile_metadata("qwen2.5-0.5b-mindspore")
        # Simulate a partially updated schema-v2 record: the aggregate state is
        # still not-run while a primary-board row has been populated.  A call
        # without --board/--soc must remain fail-closed; operators can opt into
        # a board-specific row only with an explicit selector.
        metadata["status"] = "not-run"
        validation = dict(metadata.get("validation", {}))
        b4 = dict(validation.get("Ascend310B4", {}))
        b4["status"] = "experimental_dirty_base"
        validation["Ascend310B4"] = b4
        metadata["validation"] = validation
        base = argparse.Namespace(
            profile="qwen2.5-0.5b-mindspore",
            board=None,
            soc=None,
            host="127.0.0.1",
            port=8090,
            output=None,
            run_id="unit-aggregate-not-run",
            timeout=1.0,
            long_budgets="8",
            stability_loops=1,
            stability_max_tokens=1,
            perf_warmup=0,
            perf_loops=1,
            perf_max_tokens=1,
            probe_max_tokens=1,
            probe_file=None,
            environment_report=None,
            artifact_report=None,
            require_evidence=False,
            execute=False,
            dry_run=True,
            skip_quality=True,
            skip_snapshots=True,
        )
        with patch.object(ACCEPTANCE, "load_profile_metadata", return_value=metadata):
            with self.assertRaisesRegex(ACCEPTANCE.AcceptanceError, "aggregate_not_run|not-run"):
                ACCEPTANCE.validate_options(base)

    def test_strict_health_gate_requires_admission_and_native_candidate_identity(self):
        body = {
            "ready": True,
            "healthy": True,
            "profile": "qwen1.5-0.5b-mindspore",
            "model_id": "case9-active",
            "busy": False,
            "cache_cleared": True,
            "npu_model": "Ascend310B1",
            "admission_soc": "Ascend310B1",
            "admission_status": "experimental_dirty_base",
            "admission_allowed": True,
            "candidate_kind": "native_mindspore",
            "conditional": False,
            "device_target": "Ascend",
            "worker_pid": 1234,
            "environment_fingerprint": "a" * 64,
        }
        record = {"http_status": 200, "body": body}
        passed, checks = ACCEPTANCE._health_ok(
            record,
            "qwen1.5-0.5b-mindspore",
            soc="Ascend310B1",
            strict_target=True,
        )
        self.assertTrue(passed)
        self.assertTrue(checks["admission_status_matches_profile"])
        self.assertTrue(checks["candidate_kind_native"])
        body["admission_soc"] = "Ascend310B4"
        passed, checks = ACCEPTANCE._health_ok(
            record,
            "qwen1.5-0.5b-mindspore",
            soc="Ascend310B1",
            strict_target=True,
        )
        self.assertFalse(passed)
        self.assertFalse(checks["admission_soc_matches_target"])
        body["admission_soc"] = "Ascend310B1"
        body["candidate_kind"] = "conditional"
        passed, checks = ACCEPTANCE._health_ok(
            record,
            "qwen1.5-0.5b-mindspore",
            soc="Ascend310B1",
            strict_target=True,
        )
        self.assertFalse(passed)
        self.assertFalse(checks["candidate_kind_native"])

    def test_evidence_gate_fails_closed_when_reports_are_missing_or_mismatched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = ACCEPTANCE.Options(
                "qwen1.5-0.5b-mindspore", "127.0.0.1", 8090, root / "out", 1.0,
                (1,), 1, 1, 0, 1, 1, 1, None, "evidence-missing", False, False, False,
                board="192.168.1.90", soc="Ascend310B4", tier="8T", target_explicit=True,
                require_evidence=True,
            )
            metadata = ACCEPTANCE.load_profile_metadata(options.profile)
            passed, evidence = ACCEPTANCE._evidence_reports(options, metadata)
            self.assertFalse(passed)
            self.assertEqual(evidence["status"], "failed")
            self.assertEqual(evidence["environment"]["status"], "failed")
            self.assertEqual(evidence["artifact"]["status"], "failed")

    def test_evidence_gate_accepts_matching_environment_and_artifact_reports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = root / "environment.json"
            artifact = root / "artifact.json"
            environment.write_text(json.dumps({
                "ok": True,
                "profile_id": "qwen1.5-0.5b-mindspore",
                "checks": {
                    "profile": {"board_host": "192.168.1.90", "board_soc": "Ascend310B4", "board_tier": "8T"},
                    "npu": {"chip": "310B4"},
                },
                "failures": [],
            }), encoding="utf-8")
            artifact.write_text(json.dumps({
                "status": "passed",
                "artifact_verified": True,
                "profile": "qwen1.5-0.5b-mindspore",
                "board": {"host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
                "errors": [],
            }), encoding="utf-8")
            options = ACCEPTANCE.Options(
                "qwen1.5-0.5b-mindspore", "127.0.0.1", 8090, root / "out", 1.0,
                (1,), 1, 1, 0, 1, 1, 1, None, "evidence-pass", False, False, False,
                board="192.168.1.90", soc="Ascend310B4", tier="8T", target_explicit=True,
                environment_report=environment, artifact_report=artifact,
                require_evidence=True,
            )
            metadata = ACCEPTANCE.load_profile_metadata(options.profile)
            passed, evidence = ACCEPTANCE._evidence_reports(options, metadata)
            self.assertTrue(passed)
            self.assertEqual(evidence["status"], "passed")
            self.assertEqual(evidence["environment"]["status"], "verified")
            self.assertEqual(evidence["artifact"]["status"], "verified")

    def test_evidence_gate_does_not_accept_only_one_supplied_report(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = root / "environment.json"
            environment.write_text(json.dumps({
                "ok": True,
                "profile_id": "qwen1.5-0.5b-mindspore",
                "checks": {"npu": {"chip": "310B4"}},
                "failures": [],
            }), encoding="utf-8")
            options = ACCEPTANCE.Options(
                "qwen1.5-0.5b-mindspore", "127.0.0.1", 8090, root / "out", 1.0,
                (1,), 1, 1, 0, 1, 1, 1, None, "evidence-partial", False, False, False,
                board="192.168.1.90", soc="Ascend310B4", tier="8T", target_explicit=True,
                environment_report=environment,
            )
            metadata = ACCEPTANCE.load_profile_metadata(options.profile)
            passed, evidence = ACCEPTANCE._evidence_reports(options, metadata)
            self.assertFalse(passed)
            self.assertEqual(evidence["artifact"]["reason"], "required report path was not supplied")

    def test_evidence_gate_rejects_wrong_current_host_even_when_soc_matches(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = root / "environment.json"
            artifact = root / "artifact.json"
            common = {
                "profile_id": "qwen1.5-0.5b-mindspore",
                "status": "passed",
                "board": {"host": "192.168.99.99", "soc": "Ascend310B4", "tier": "8T"},
            }
            environment.write_text(json.dumps({**common, "ok": True, "failures": []}), encoding="utf-8")
            artifact.write_text(json.dumps({**common, "artifact_verified": True, "errors": []}), encoding="utf-8")
            options = ACCEPTANCE.Options(
                "qwen1.5-0.5b-mindspore", "127.0.0.1", 8090, root / "out", 1.0,
                (1,), 1, 1, 0, 1, 1, 1, None, "evidence-host-mismatch", False, False, False,
                board="192.168.1.90", soc="Ascend310B4", tier="8T", target_explicit=True,
                environment_report=environment, artifact_report=artifact,
                require_evidence=True,
            )
            metadata = ACCEPTANCE.load_profile_metadata(options.profile)
            passed, evidence = ACCEPTANCE._evidence_reports(options, metadata)
            self.assertFalse(passed)
            self.assertFalse(evidence["environment"]["checks"]["host_matches"])
            self.assertFalse(evidence["artifact"]["checks"]["host_matches"])

    def test_evidence_gate_rejects_conflicting_identity_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            environment = root / "environment.json"
            artifact = root / "artifact.json"
            base = {
                "profile_id": "qwen1.5-0.5b-mindspore",
                "status": "passed",
                "board": {"host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
                "checks": {"npu": {"chip": "Ascend310B1"}},
            }
            environment.write_text(json.dumps({**base, "ok": True, "failures": []}), encoding="utf-8")
            artifact.write_text(json.dumps({**base, "artifact_verified": True, "errors": []}), encoding="utf-8")
            options = ACCEPTANCE.Options(
                "qwen1.5-0.5b-mindspore", "127.0.0.1", 8090, root / "out", 1.0,
                (1,), 1, 1, 0, 1, 1, 1, None, "evidence-conflict", False, False, False,
                board="192.168.1.90", soc="Ascend310B4", tier="8T", target_explicit=True,
                environment_report=environment, artifact_report=artifact,
                require_evidence=True,
            )
            metadata = ACCEPTANCE.load_profile_metadata(options.profile)
            passed, evidence = ACCEPTANCE._evidence_reports(options, metadata)
            self.assertFalse(passed)
            self.assertFalse(evidence["environment"]["checks"]["soc_present"])
            self.assertFalse(evidence["artifact"]["checks"]["soc_present"])

    def test_campaign_status_cannot_pass_without_required_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            options = ACCEPTANCE.Options(
                "qwen1.5-0.5b-mindspore", "127.0.0.1", 8090, root / "out", 1.0,
                (1,), 1, 1, 0, 1, 1, 1, None, "campaign-evidence", True, False, False,
                board="192.168.1.90", soc="Ascend310B4", tier="8T", target_explicit=True,
                require_evidence=True,
            )
            with patch.object(
                ACCEPTANCE, "_json_request", return_value={"status": "error", "http_status": None}
            ) as request:
                report = ACCEPTANCE.run_campaign(options)
            self.assertEqual(report["status"], "failed")
            self.assertFalse(report["gates"]["evidence"])
            self.assertEqual(report["machine_gate_total"], len(ACCEPTANCE.REQUIRED_MACHINE_GATES) + 1)
            self.assertTrue(all("/v1/chat/completions" not in str(call) for call in request.call_args_list))

    def test_protocol_helpers_are_loopback_only_and_bounded(self):
        with self.assertRaises(ACCEPTANCE.AcceptanceError):
            ACCEPTANCE._client_abort_sse("192.0.2.1", 8090, "case9-active", 1.0, 1)
        payload = ACCEPTANCE._over_context_payload("case9-active")
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.assertLess(len(encoded), ACCEPTANCE.MAX_REQUEST_BYTES)
        self.assertGreaterEqual(payload["messages"][0]["content"].count("token"), 2048)

    def test_client_abort_requires_response_acceptance_before_closing(self):
        class FakeSocket:
            def __init__(self, chunks):
                self.chunks = list(chunks)
                self.sent = b""
                self.shutdown_called = False
                self.closed = False

            def sendall(self, value):
                self.sent += value

            def settimeout(self, _value):
                return None

            def recv(self, _size):
                if self.chunks:
                    chunk = self.chunks.pop(0)
                    if isinstance(chunk, BaseException):
                        raise chunk
                    return chunk
                raise socket.timeout("fixture timeout")

            def shutdown(self, _how):
                self.shutdown_called = True

            def close(self):
                self.closed = True

        accepted = FakeSocket([
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream; charset=utf-8\r\n\r\n",
            b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
        ])
        with patch.object(ACCEPTANCE.socket, "create_connection", return_value=accepted):
            result = ACCEPTANCE._client_abort_sse("127.0.0.1", 8090, "case9-active", 0.2, 1)
        self.assertEqual(result["status"], "sent_and_closed")
        self.assertTrue(result["headers_received"])
        self.assertTrue(result["first_event_received"])
        self.assertTrue(result["started_observed"])
        self.assertTrue(accepted.shutdown_called)
        self.assertTrue(accepted.closed)

        never_started = FakeSocket([socket.timeout("no response")])
        with patch.object(ACCEPTANCE.socket, "create_connection", return_value=never_started):
            result = ACCEPTANCE._client_abort_sse("127.0.0.1", 8090, "case9-active", 0.2, 1)
        self.assertEqual(result["status"], "failed")
        self.assertFalse(result["headers_received"])
        self.assertFalse(result["started_observed"])

    def test_client_abort_accepts_explicit_busy_response(self):
        class BusySocket:
            def __init__(self):
                self.chunks = [
                    b"HTTP/1.1 429 Too Many Requests\r\nContent-Type: application/json\r\n\r\n",
                    b'{"error":{"code":"busy","message":"worker busy"}}',
                ]

            def sendall(self, _value):
                return None

            def settimeout(self, _value):
                return None

            def recv(self, _size):
                if self.chunks:
                    return self.chunks.pop(0)
                raise socket.timeout("fixture timeout")

            def shutdown(self, _how):
                return None

            def close(self):
                return None

        with patch.object(ACCEPTANCE.socket, "create_connection", return_value=BusySocket()):
            result = ACCEPTANCE._client_abort_sse("127.0.0.1", 8090, "case9-active", 0.2, 1)
        self.assertEqual(result["status"], "sent_and_closed")
        self.assertTrue(result["busy_observed"])
        self.assertEqual(result["observation"], "busy_response")

    def test_load_profile_metadata_and_validation_use_explicit_registry(self):
        source_path = ROOT / "configs" / "chat_model_profiles.json"
        document = json.loads(source_path.read_text(encoding="utf-8"))
        for item in document["profiles"]:
            if item["id"] == "qwen1.5-0.5b-mindspore":
                item["display_name"] = "fixture registry profile"
                break
        with tempfile.TemporaryDirectory() as directory:
            registry_path = Path(directory) / "profiles.json"
            registry_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
            metadata = ACCEPTANCE.load_profile_metadata("qwen1.5-0.5b-mindspore", registry_path)
            self.assertEqual(metadata["display_name"], "fixture registry profile")
            base = argparse.Namespace(
                profile="qwen1.5-0.5b-mindspore",
                registry=registry_path,
                board="20t",
                host="127.0.0.1",
                port=8090,
                output=None,
                run_id="unit-registry",
                timeout=1.0,
                long_budgets="8,16",
                stability_loops=1,
                stability_max_tokens=1,
                perf_warmup=0,
                perf_loops=1,
                perf_max_tokens=1,
                probe_max_tokens=1,
                probe_file=None,
                execute=False,
                dry_run=True,
                skip_quality=True,
                skip_snapshots=True,
            )
            options = ACCEPTANCE.validate_options(base)
            self.assertEqual(options.registry, registry_path)
            self.assertEqual(options.soc, "Ascend310B1")

    def test_output_path_is_confined_to_report_root(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "reports"
            with self.assertRaises(ACCEPTANCE.AcceptanceError):
                ACCEPTANCE.safe_report_path(Path(directory) / "outside", root)
            safe = ACCEPTANCE.safe_report_path(root / "profile" / "run", root)
            self.assertEqual(safe, (root / "profile" / "run").resolve())

    def test_validate_options_rejects_non_loopback_and_blocked_profile(self):
        base = argparse.Namespace(
            profile="qwen1.5-0.5b-mindspore",
            board="20t",
            host="127.0.0.1",
            port=8090,
            output=None,
            run_id="unit-test",
            timeout=1.0,
            long_budgets="8,16",
            stability_loops=1,
            stability_max_tokens=1,
            perf_warmup=0,
            perf_loops=1,
            perf_max_tokens=1,
            probe_max_tokens=1,
            probe_file=None,
            execute=False,
            dry_run=True,
            skip_quality=True,
            skip_snapshots=True,
        )
        options = ACCEPTANCE.validate_options(base)
        self.assertFalse(options.execute)
        invalid = argparse.Namespace(**vars(base))
        invalid.host = "0.0.0.0"
        with self.assertRaises(ACCEPTANCE.AcceptanceError):
            ACCEPTANCE.validate_options(invalid)
        blocked = argparse.Namespace(**vars(base))
        blocked.profile = "deepseek-r1-qwen-1.5b-mindspore"
        with self.assertRaises(ACCEPTANCE.AcceptanceError):
            ACCEPTANCE.validate_options(blocked)

    def test_dry_run_cli_does_not_create_report_directory(self):
        command = [
            sys.executable,
            str(ROOT / "scripts" / "mindspore_chat_acceptance.py"),
            "--profile",
            "qwen1.5-0.5b-mindspore",
            "--board",
            "20t",
            "--run-id",
            "unit-dry-run",
            "--dry-run",
        ]
        result = subprocess.run(command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["status"], "dry-run")
        self.assertFalse(payload["writes_reports"])

    def test_failed_campaign_report_keeps_all_machine_gate_keys(self):
        # An unexpected runtime exception must still produce a report that
        # downstream tooling can classify without guessing which gates ran.
        with tempfile.TemporaryDirectory() as directory:
            report_root = Path(directory) / "reports"
            argv = [
                "--profile",
                "qwen1.5-0.5b-mindspore",
                "--board",
                "20t",
                "--run-id",
                "unit-error-schema",
                "--execute",
                "--timeout",
                "0.001",
            ]
            with patch.object(ACCEPTANCE, "REPORT_ROOT", report_root), patch.object(
                ACCEPTANCE,
                "safe_report_path",
                side_effect=lambda value, root=report_root: Path(value).expanduser().resolve(),
            ), patch.object(
                ACCEPTANCE, "run_campaign", side_effect=RuntimeError("synthetic failure")
            ):
                return_code = ACCEPTANCE.main(argv)
            self.assertEqual(return_code, 1)
            output = report_root / "qwen1.5-0.5b-mindspore" / "unit-error-schema"
            report = json.loads((output / "acceptance.json").read_text(encoding="utf-8"))
            required = set(ACCEPTANCE.REQUIRED_MACHINE_GATES)
            self.assertTrue(required.issubset(set(report["gates"])))
            self.assertTrue(all(report["gates"][name] is False for name in required))

    def test_shell_wrapper_defaults_to_dry_run_and_has_no_package_or_process_commands(self):
        source = (ROOT / "scripts" / "run_mindspore_chat_acceptance.sh").read_text(encoding="utf-8")
        self.assertIn("mode=\"--dry-run\"", source)
        self.assertNotIn("pip install", source)
        self.assertNotIn("conda install", source)
        self.assertNotIn("pkill", source)
        self.assertNotIn("killall", source)
        self.assertNotIn("--delete", source)

    def test_repro_sync_allowlist_carries_candidate_ui_and_probe_inputs(self):
        source = (ROOT / "scripts" / "sync_mindspore_chat_repro_bundle.sh").read_text(encoding="utf-8")
        for required in (
            "scripts/run_mindspore_chat_acceptance.sh",
            "scripts/run_mindspore_chat_text.sh",
            "scripts/run_text_chat.sh",
            "tests/fixtures/mindspore_chat_probe.json",
            "frontend/dist/index.html",
        ):
            self.assertIn(required, source)
        self.assertIn("--partial --append-verify", source)
        # Documentation may mention the forbidden flag when describing the
        # policy.  Check the actual rsync invocation instead of the whole file.
        rsync_lines = [line for line in source.splitlines() if "rsync" in line and "--partial" in line]
        self.assertTrue(rsync_lines)
        self.assertTrue(all("--delete" not in line for line in rsync_lines))

    def test_repro_sync_derives_native_profiles_and_records_optional_missing_files(self):
        script = ROOT / "scripts" / "sync_mindspore_chat_repro_bundle.sh"
        source = script.read_text(encoding="utf-8")
        registry = json.loads((ROOT / "configs" / "chat_model_profiles.json").read_text(encoding="utf-8"))
        native = [item for item in registry["profiles"] if item.get("candidate_kind") == "native_mindspore"]
        self.assertEqual(len(native), 8)
        self.assertIn('document.get("schema_version") != 2', source)
        self.assertIn('profile.get("candidate_kind") != "native_mindspore"', source)
        self.assertIn("PROFILE_PLAN", source)
        self.assertIn("MISSING_RECORDS", source)
        self.assertIn('"missing_optional_entries"', source)
        self.assertIn("--board 8t|20t|both", source)
        self.assertIn("--profile ID[,ID...]", source)
        self.assertIn("transfer_report_optional", source)
        # The report helper must return before any SSH probe in dry-run mode;
        # otherwise a dry-run could unexpectedly block on an unavailable board.
        report_start = source.index("transfer_report_optional()")
        report_body = source[report_start : source.index("sync_profile_plan()", report_start)]
        self.assertLess(report_body.index("if ((DRY_RUN)); then"), report_body.index("ssh "))
        # Every native profile is required to carry both board validations and
        # at least one explicit model/tokenizer/config artifact entry. The
        # shell script consumes these fields dynamically rather than copying a
        # hard-coded subset.
        for profile in native:
            self.assertEqual(set(profile["validation"]), {"Ascend310B4", "Ascend310B1"})
            self.assertTrue(profile["artifacts"])
            self.assertIn("cache_dir", profile)

    def test_campaign_covers_protocol_gates_without_host_side_effects(self):
        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format, *_args):
                return

            def _send(self, status, payload, content_type="application/json"):
                raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                if self.path == "/health":
                    self._send(200, {
                        "ready": True,
                        "healthy": True,
                        "profile": "qwen1.5-0.5b-mindspore",
                        "model_id": "case9-active",
                        "busy": False,
                        "cache_cleared": True,
                        "npu_model": "Ascend310B4",
                        "device_target": "Ascend",
                        "worker_pid": 1234,
                        "environment_fingerprint": "a" * 64,
                    })
                elif self.path == "/v1/models":
                    self._send(200, {"object": "list", "data": [{"id": "case9-active"}]})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                if length > ACCEPTANCE.MAX_REQUEST_BYTES:
                    self._send(413, {"error": {"code": "request_too_large"}})
                    return
                raw = self.rfile.read(length)
                if self.path != "/v1/chat/completions":
                    self._send(404, {"error": "not found"})
                    return
                try:
                    body = json.loads(raw.decode("utf-8"))
                except (UnicodeError, ValueError):
                    body = {}
                if body.get("model") != "case9-active":
                    self._send(404, {"error": {"code": "model_not_found", "type": "invalid_request_error"}})
                    return
                if set(body) - {"model", "messages", "stream", "max_tokens", "temperature", "top_p"}:
                    self._send(400, {"error": {"code": "invalid_request_error", "type": "invalid_request_error"}})
                    return
                messages = body.get("messages") or []
                role = messages[0].get("role") if messages and isinstance(messages[0], dict) else None
                content = messages[0].get("content", "") if messages and isinstance(messages[0], dict) else ""
                if role not in {"system", "user", "assistant"}:
                    self._send(400, {"error": {"code": "invalid_request_error", "type": "invalid_request_error"}})
                    return
                if body.get("temperature") not in (None, 0):
                    self._send(400, {"error": {"code": "invalid_request_error", "type": "invalid_request_error"}})
                    return
                if body.get("top_p") not in (None, 1):
                    self._send(400, {"error": {"code": "invalid_request_error", "type": "invalid_request_error"}})
                    return
                if body.get("max_tokens", 32) > ACCEPTANCE.MAX_GENERATION_TOKENS:
                    self._send(400, {"error": {"code": "invalid_request_error", "type": "invalid_request_error"}})
                    return
                if len(str(content).split()) >= ACCEPTANCE.OVER_CONTEXT_TERM_COUNT:
                    self._send(400, {"error": {"code": "invalid_request_error", "type": "invalid_request_error"}})
                    return
                if "text/event-stream" in self.headers.get("Accept", ""):
                    chunks = [
                        b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n',
                        b'data: {"choices":[{"delta":{},"finish_reason":"stop"}],"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n',
                        b"data: [DONE]\n\n",
                    ]
                    try:
                        self._send(200, b"".join(chunks), "text/event-stream")
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                else:
                    self._send(200, {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            options = ACCEPTANCE.Options(
                "qwen1.5-0.5b-mindspore", "127.0.0.1", server.server_port, Path("fixture"), 2.0,
                (1,), 1, 1, 0, 1, 1, 1, None, "campaign-test", True, False, False,
            )
            report = ACCEPTANCE.run_campaign(options)
            self.assertEqual(report["status"], "passed")
            self.assertTrue(all(report["gates"].get(key) for key in ACCEPTANCE.REQUIRED_MACHINE_GATES))
            self.assertEqual(report["errors"]["passed"], report["errors"]["total"])
            self.assertEqual(report["protocol"]["client_abort"]["status"], "sent_and_closed")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)


if __name__ == "__main__":
    unittest.main()
