from __future__ import annotations

import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import threading
import unittest
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ACCEPTANCE = load_module("qwen25_acceptance_test", ROOT / "scripts" / "qwen25_acceptance.py")
VERIFIER = load_module("qwen25_bundle_verifier_test", ROOT / "scripts" / "verify_qwen25_repro_bundle.py")


class AcceptanceHelperTests(unittest.TestCase):
    def test_probe_fixture_has_ten_stable_ids(self) -> None:
        fixture = json.loads((ROOT / "tests" / "fixtures" / "qwen25_chinese_probe.json").read_text(encoding="utf-8"))
        self.assertEqual(len(fixture), 10)
        self.assertEqual(len({item["id"] for item in fixture}), 10)
        self.assertTrue(all(isinstance(item["prompt"], str) and item["prompt"] for item in fixture))

    def test_percentile_uses_lower_index_method(self) -> None:
        self.assertEqual(ACCEPTANCE.percentile([10, 20, 30, 40, 50], 0.95), 40)

    def test_sse_reader_reconstructs_incremental_deltas(self) -> None:
        class Response:
            status = 200

            def __init__(self) -> None:
                self.lines = iter(
                    [
                        b'data: {"choices":[{"delta":{"content":"\\u4f60"}}]}\n\n',
                        b'data: {"choices":[{"delta":{"content":"\\u597d"},"finish_reason":"stop"}],"usage":{"completion_tokens":2}}\n\n',
                        b"data: [DONE]\n\n",
                    ]
                )

            def readline(self):
                return next(self.lines, b"")

        result = ACCEPTANCE._read_sse(Response(), 0.0)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["text"], "你好")
        self.assertFalse(result["duplicate_delta"])

    def test_artifact_lock_check_requires_exact_size_and_sha(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            om = root / "model.om"
            om.write_bytes(b"abc")
            sha = hashlib.sha256(b"abc").hexdigest()
            lock = root / "model.lock.json"
            lock.write_text(json.dumps({"bytes": 3, "sha256": sha}), encoding="utf-8")
            self.assertEqual(ACCEPTANCE._lock_check(om, lock)["status"], "ok")
            lock.write_text(json.dumps({"bytes": 4, "sha256": sha}), encoding="utf-8")
            self.assertEqual(ACCEPTANCE._lock_check(om, lock)["status"], "error")

    def test_required_artifact_gate_requires_all_five_regular_files(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = {}
            for name in ("om", "om_lock", "contract", "tokenizer", "tokenizer_lock"):
                path = root / name
                path.write_bytes(b"fixture")
                paths[name] = path
            details = ACCEPTANCE._required_artifacts(**paths)
            self.assertTrue(ACCEPTANCE._required_artifacts_ok(details))
            paths["contract"].unlink()
            self.assertFalse(
                ACCEPTANCE._required_artifacts_ok(ACCEPTANCE._required_artifacts(**paths))
            )

    def test_health_model_contract_checks_identity_admission_and_soc(self) -> None:
        health = {
            "status": "ok",
            "http_status": 200,
            "body": {
                "ready": True,
                "healthy": True,
                "model": "model-id",
                "artifact_lock_verified": True,
                "artifact_verified": True,
                "restart_required": False,
                "cleanup_failed": False,
                "watchdog_triggered": False,
                "target_soc": "Ascend310B4",
                "board_soc": "Ascend310B4",
            },
        }
        models = {
            "status": "ok",
            "http_status": 200,
            "body": {"data": [{"id": "model-id"}]},
        }
        result = ACCEPTANCE._health_model_contract(health, models, "model-id", "board8t")
        self.assertEqual(result["status"], "passed")
        health["body"]["board_soc"] = "Ascend310B1"
        self.assertEqual(
            ACCEPTANCE._health_model_contract(health, models, "model-id", "board8t")["status"],
            "failed",
        )

    def test_long_output_requires_full_budget_when_finish_reason_is_length(self) -> None:
        base = {
            "status": "ok",
            "text": "完整文本。",
            "utf8_valid": True,
            "completion_tokens": 8,
            "finish_reason": "length",
        }
        self.assertTrue(ACCEPTANCE._valid_long_output(base, 8))
        self.assertFalse(ACCEPTANCE._valid_long_output(base, 16))
        stopped = dict(base, completion_tokens=4, finish_reason="stop")
        self.assertTrue(ACCEPTANCE._valid_long_output(stopped, 16))

    def test_campaign_option_validation_rejects_out_of_range_values(self) -> None:
        args = SimpleNamespace(
            timeout=1.0,
            max_tokens=2,
            stability_max_tokens=2,
            probe_max_tokens=2,
            perf_max_tokens=2,
            abort_max_tokens=1,
            stability_loops=1,
            perf_warmup=0,
            perf_loops=1,
            abort_health_wait_seconds=0.0,
            long_budgets=[8, 16, 48, 64, 80],
        )
        ACCEPTANCE.validate_options(args)
        for field, value in (
            ("max_tokens", 81),
            ("stability_max_tokens", 0),
            ("probe_max_tokens", 81),
            ("perf_max_tokens", 81),
            ("stability_loops", 1001),
            ("perf_loops", 0),
            ("timeout", float("inf")),
        ):
            invalid = SimpleNamespace(**vars(args))
            setattr(invalid, field, value)
            with self.assertRaises(ValueError, msg=field):
                ACCEPTANCE.validate_options(invalid)


class ProtocolBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format: str, *_args: object) -> None:
                return

            def _write_json(self, status: int, body: object) -> None:
                raw = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self) -> None:
                if self.path == "/health":
                    self._write_json(200, {"ready": True, "healthy": True})
                else:
                    self._write_json(404, {"error": "not found"})

            def do_POST(self) -> None:
                declared_length = int(self.headers.get("Content-Length", "0"))
                if declared_length > ACCEPTANCE.MAX_REQUEST_BYTES:
                    self._write_json(400, {"error": "request body is missing or too large"})
                    return
                raw = self.rfile.read(declared_length)
                body = json.loads(raw.decode("utf-8"))
                content = str((body.get("messages") or [{}])[0].get("content") or "")
                if len(content.split()) >= ACCEPTANCE.OVER_CONTEXT_TERM_COUNT:
                    self._write_json(400, {"error": "prompt plus max_tokens exceeds context"})
                    return
                if body.get("stream"):
                    outer.abort_requests += 1
                    payload = b'data: {"choices":[{"delta":{"content":"x"}}]}\n\n'
                    try:
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Content-Length", str(len(payload)))
                        self.end_headers()
                        self.wfile.write(payload)
                        self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    return
                self._write_json(200, {"choices": [{"message": {"content": "ok"}}]})

        self.abort_requests = 0
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.endpoint = "http://127.0.0.1:%d/v1/chat/completions" % self.server.server_port
        self.health_url = "http://127.0.0.1:%d/health" % self.server.server_port

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_over_context_request_requires_a_400_rejection(self) -> None:
        result = ACCEPTANCE._mark_rejection_contract(
            ACCEPTANCE._over_context_request(self.endpoint, "fixture", timeout=2)
        )
        self.assertEqual(result["status"], 400)
        self.assertEqual(result["contract_status"], "passed")
        self.assertGreater(result["request_bytes"], 1024)

    def test_oversized_content_length_is_rejected_without_uploading_payload(self) -> None:
        result = ACCEPTANCE._mark_rejection_contract(
            ACCEPTANCE._oversized_content_length_request(self.endpoint, timeout=2)
        )
        self.assertEqual(result["status"], 400)
        self.assertEqual(result["contract_status"], "passed")
        self.assertEqual(result["transmitted_body_bytes"], 0)
        self.assertGreater(result["declared_content_length"], ACCEPTANCE.MAX_REQUEST_BYTES)

    def test_client_abort_keeps_health_endpoint_usable(self) -> None:
        abort = ACCEPTANCE._client_abort_sse(self.endpoint, "fixture", timeout=2, max_tokens=1)
        health = ACCEPTANCE._health_after_abort(self.health_url, timeout=2, wait_seconds=1)
        self.assertEqual(abort["status"], "sent_and_closed")
        self.assertEqual(health["contract_status"], "passed")
        self.assertEqual(health["status"], "healthy")
        self.assertEqual(self.abort_requests, 1)


class BundleVerifierTests(unittest.TestCase):
    def _make_bundle(self, root: Path) -> None:
        payload = root / "artifacts" / "sample.bin"
        payload.parent.mkdir(parents=True)
        payload.write_bytes(b"fixture")
        sha = hashlib.sha256(b"fixture").hexdigest()
        tokenizer = root / "artifacts" / "common" / "tokenizer.json"
        tokenizer.parent.mkdir(parents=True)
        tokenizer.write_bytes(b"tokenizer")
        tokenizer_sha = hashlib.sha256(b"tokenizer").hexdigest()
        lock = tokenizer.parent / "tokenizer.json.lock.json"
        lock.write_text(
            json.dumps({"artifact": "tokenizer.json", "bytes": 9, "sha256": tokenizer_sha}), encoding="utf-8"
        )
        lock_bytes = lock.stat().st_size
        lock_sha = hashlib.sha256(lock.read_bytes()).hexdigest()
        manifest = {
            "schema_version": 2,
            "model_id": VERIFIER.MODEL_ID,
            "boards": {
                "board8t": {"soc": "Ascend310B4"},
                "board20t": {"soc": "Ascend310B1"},
            },
            "required_files": [
                {"path": "artifacts/sample.bin", "bytes": 7, "sha256": sha},
                {"path": "artifacts/common/tokenizer.json", "bytes": 9, "sha256": tokenizer_sha},
                {"path": "artifacts/common/tokenizer.json.lock.json", "bytes": lock_bytes, "sha256": lock_sha},
            ],
        }
        (root / "bundle-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        (root / "SHA256SUMS.txt").write_text(
            "%s  ./artifacts/sample.bin\n%s  ./artifacts/common/tokenizer.json\n%s  ./artifacts/common/tokenizer.json.lock.json\n"
            % (sha, tokenizer_sha, lock_sha),
            encoding="utf-8",
        )

    def test_verifier_accepts_matching_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_bundle(root)
            result = VERIFIER.verify(root)
            self.assertEqual(result["status"], "passed")
            self.assertEqual(result["checked"], 3)

    def test_verifier_rejects_partial_file_and_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._make_bundle(root)
            (root / "leftover.part").write_bytes(b"partial")
            result = VERIFIER.verify(root)
            self.assertEqual(result["status"], "failed")
            self.assertTrue(any("partial" in item for item in result["failures"]))
            self.assertTrue(VERIFIER.safe_relative("artifacts/sample.bin").as_posix() == "artifacts/sample.bin")
            with self.assertRaises(ValueError):
                VERIFIER.safe_relative("../outside")


class ShellWrapperTests(unittest.TestCase):
    @staticmethod
    def _bash_path(path: Path) -> str:
        value = str(path.resolve()).replace("\\", "/")
        if len(value) >= 2 and value[1] == ":":
            return "/mnt/" + value[0].lower() + value[2:]
        return value

    @unittest.skipUnless(subprocess.call(["bash", "-lc", "command -v bash >/dev/null"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0, "bash unavailable")
    def test_acceptance_dry_run_lists_both_boards_without_ssh(self) -> None:
        script = shlex.quote(self._bash_path(ROOT / "scripts" / "run_qwen25_dual_board_acceptance.sh"))
        result = subprocess.run(["bash", "-lc", "bash %s --board both --dry-run --no-pull --probe-file-rel tests/fixtures/qwen25_chinese_probe.json" % script], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
        self.assertEqual(result.returncode, 0)
        self.assertIn("board8t", result.stdout)
        self.assertIn("board20t", result.stdout)
        self.assertIn("--probe-file", result.stdout)

    @unittest.skipUnless(subprocess.call(["bash", "-lc", "command -v bash >/dev/null"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0, "bash unavailable")
    def test_sync_dry_run_does_not_create_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle"
            script = shlex.quote(self._bash_path(ROOT / "scripts" / "sync_qwen25_repro_bundle.sh"))
            bundle = shlex.quote(str(output).replace("\\", "/"))
            result = subprocess.run(["bash", "-lc", "bash %s --dry-run --no-source --no-reports --bundle %s" % (script, bundle)], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
            self.assertEqual(result.returncode, 0)
            self.assertFalse(output.exists())
            self.assertIn("dry-run complete", result.stdout)

    @unittest.skipUnless(subprocess.call(["bash", "-lc", "command -v bash >/dev/null"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) == 0, "bash unavailable")
    def test_candidate_sync_dry_run_is_explicit_and_campaign_scoped(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "bundle"
            script = shlex.quote(self._bash_path(ROOT / "scripts" / "sync_qwen25_repro_bundle.sh"))
            bundle = shlex.quote(str(output).replace("\\", "/"))
            command = (
                "bash %s --layout candidate --campaign-run-id 20260827T123456Z "
                "--board8-root /home/HwHiAiUser/candidate8 "
                "--board20-root /home/HwHiAiUser/candidate20 "
                "--source-model-root /home/HwHiAiUser/source-model "
                "--no-source --bundle %s --dry-run"
                % (script, bundle)
            )
            result = subprocess.run(["bash", "-lc", command], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertIn("candidate8/artifacts/qwen25-static-kv-1024-v2.om", result.stdout)
            self.assertIn("candidate20/artifacts/qwen25-static-kv-1024-b1.om", result.stdout)
            self.assertIn("reports/board8t/20260827T123456Z/acceptance.json", result.stdout)
            self.assertIn("reports/board20t/20260827T123456Z/acceptance.json", result.stdout)
            self.assertIn("reports/board8t/candidate/20260827T102500Z/preflight.json", result.stdout)
            self.assertNotIn("reports/board8t/candidate/reports/", result.stdout)
            self.assertIn("--partial --append-verify", (ROOT / "scripts" / "sync_qwen25_repro_bundle.sh").read_text(encoding="utf-8"))
            script_text = (ROOT / "scripts" / "sync_qwen25_repro_bundle.sh").read_text(encoding="utf-8")
            self.assertNotRegex(script_text, r"(?m)^\s*(?:rsync|scp|ssh)[^\n]*--delete")
            self.assertFalse(output.exists())

    def test_repro_bundle_allowlist_covers_runtime_ui_and_provenance(self) -> None:
        script_text = (ROOT / "scripts" / "sync_qwen25_repro_bundle.sh").read_text(encoding="utf-8")
        for relative in (
            "app.py",
            "text_chat_app.py",
            "scripts/run_qwen25_kv102_gateway.sh",
            "frontend/src/App.tsx",
            "frontend/dist/index.html",
            "docs/02-qwen25-reproducibility-and-sync.md",
        ):
            self.assertIn(relative, script_text)
        self.assertIn("environment/historical", script_text)
        self.assertIn("write_environment_snapshot", script_text)


if __name__ == "__main__":
    unittest.main()
