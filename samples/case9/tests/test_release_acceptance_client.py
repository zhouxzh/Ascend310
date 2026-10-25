from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import threading
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "release_acceptance", ROOT / "scripts" / "verify" / "release_acceptance.py"
)
assert SPEC is not None and SPEC.loader is not None
release_acceptance = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release_acceptance)


class _Handler(BaseHTTPRequestHandler):
    token = "release-acceptance-token-0123456789"

    def log_message(self, _format: str, *_args: object) -> None:
        return None

    def _json(self, status: int, value: dict[str, object]) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        return self.headers.get("Authorization") == "Bearer " + self.token

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/v1/models":
            if not self._authorized():
                self._json(401, {"error": {"code": "invalid_api_key"}})
                return
            self._json(200, {"object": "list", "data": [{"id": "ascend-edge-chat"}]})
            return
        if self.path == "/api/config":
            self._json(200, {"model": "ascend-edge-chat", "warning": "trusted LAN only"})
            return
        self._json(404, {"error": {"code": "not_found"}})

    def do_POST(self) -> None:  # noqa: N802
        size = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(size).decode("utf-8"))
        if self.path == "/api/chat":
            self._json(
                200,
                {"model": "ascend-edge-chat", "message": "界面链路已验证。", "finish_reason": "stop"},
            )
            return
        if self.path != "/v1/chat/completions" or not self._authorized():
            self._json(401, {"error": {"code": "invalid_api_key"}})
            return
        if request.get("model") != "ascend-edge-chat":
            self._json(404, {"error": {"code": "model_not_found"}})
            return
        if request.get("stream"):
            payload = json.dumps(
                {
                    "model": "ascend-edge-chat",
                    "choices": [{"delta": {"content": "你好"}, "finish_reason": None}],
                },
                ensure_ascii=False,
            )
            terminal = json.dumps(
                {
                    "model": "ascend-edge-chat",
                    "choices": [{"delta": {}, "finish_reason": "stop"}],
                    "usage": {"completion_tokens": 2},
                },
                ensure_ascii=False,
            )
            body = ("data: " + payload + "\n\ndata: " + terminal + "\n\ndata: [DONE]\n\n").encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self._json(
            200,
            {
                "model": "ascend-edge-chat",
                "choices": [{"message": {"role": "assistant", "content": "你好"}, "finish_reason": "stop"}],
                "usage": {"completion_tokens": 2},
            },
        )


class ReleaseAcceptanceClientTests(unittest.TestCase):
    def setUp(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.root = "http://127.0.0.1:%d" % self.server.server_port

    def tearDown(self) -> None:
        self.server.shutdown()
        self.thread.join(timeout=2)
        self.server.server_close()

    def test_release_contract_helpers_cover_gateway_and_ui(self) -> None:
        token = _Handler.token
        gateway = release_acceptance._check_gateway_contract(self.root + "/v1", token, 2)
        ui = release_acceptance._check_ui(self.root, 2)
        completion = release_acceptance._completion(
            self.root + "/v1/chat/completions", token, "测试", 2, 2
        )
        stream = release_acceptance._stream(self.root + "/v1/chat/completions", token, 2)
        self.assertEqual(gateway["models"], "passed")
        self.assertEqual(ui["chat"], "passed")
        self.assertEqual(completion["completion_tokens"], 2)
        self.assertEqual(stream["text"], "你好")
        self.assertEqual(stream["completion_tokens"], 2)

    def test_source_provenance_requires_full_clean_commit_and_redacts_credentials(self) -> None:
        responses = iter(
            (
                {"argv": ["git", "config"], "returncode": 0, "output": "https://token@example.test/org/repo.git\n"},
                {"argv": ["git", "rev-parse"], "returncode": 0, "output": "a" * 40 + "\n"},
                {"argv": ["git", "status"], "returncode": 0, "output": ""},
                {"argv": ["git", "ls-remote"], "returncode": 0, "output": "a" * 40 + "\trefs/heads/main\n"},
            )
        )
        with mock.patch.object(release_acceptance, "_run_command", side_effect=lambda *args, **kwargs: next(responses)):
            source = release_acceptance._source_provenance(ROOT)
        self.assertEqual(source["repository"], "https://example.test/org/repo.git")
        self.assertEqual(source["commit"], "a" * 40)
        self.assertEqual(source["public_main_commit"], "a" * 40)
        self.assertTrue(source["working_tree_clean"])

    def test_source_provenance_normalizes_the_public_github_proxy(self) -> None:
        responses = iter(
            (
                {
                    "argv": ["git", "config"],
                    "returncode": 0,
                    "output": "https://gh-proxy.com/https://github.com/zhouxzh/Ascend310.git\n",
                },
                {"argv": ["git", "rev-parse"], "returncode": 0, "output": "a" * 40 + "\n"},
                {"argv": ["git", "status"], "returncode": 0, "output": ""},
                {
                    "argv": ["git", "ls-remote"],
                    "returncode": 0,
                    "output": "a" * 40 + "\trefs/heads/main\n",
                },
            )
        )
        with mock.patch.object(
            release_acceptance,
            "_run_command",
            side_effect=lambda *args, **kwargs: next(responses),
        ):
            source = release_acceptance._source_provenance(ROOT)
        self.assertEqual(source["repository"], release_acceptance.EXPECTED_SOURCE_REPOSITORY)
        self.assertEqual(source["public_main_commit"], "a" * 40)

    def test_provenance_blocks_dirty_source_tree(self) -> None:
        with mock.patch.object(
            release_acceptance,
            "_source_provenance",
            return_value={
                "repository": release_acceptance.EXPECTED_SOURCE_REPOSITORY,
                "commit": "a" * 40,
                "public_main_commit": "a" * 40,
                "working_tree_clean": False,
                "commands": [],
            },
        ), mock.patch.object(
            release_acceptance,
            "_model_provenance",
            return_value={"source_kind": "hf", "revision": "b" * 40},
        ), mock.patch.object(
            release_acceptance,
            "_environment_provenance",
            return_value={"npu_smi": {"argv": ["npu-smi", "info"]}},
        ):
            provenance = release_acceptance._provenance(ROOT, "hf")
        self.assertEqual(provenance["status"], "blocked")
        self.assertIn("source working tree is not clean", provenance["reasons"])

    def test_provenance_blocks_a_commit_not_on_public_main(self) -> None:
        with mock.patch.object(
            release_acceptance,
            "_source_provenance",
            return_value={
                "repository": release_acceptance.EXPECTED_SOURCE_REPOSITORY,
                "commit": "a" * 40,
                "public_main_commit": "b" * 40,
                "working_tree_clean": True,
                "commands": [],
            },
        ), mock.patch.object(
            release_acceptance,
            "_model_provenance",
            return_value={"source_kind": "hf", "revision": "c" * 40},
        ), mock.patch.object(
            release_acceptance,
            "_environment_provenance",
            return_value={"npu_smi": {"argv": ["npu-smi", "info"]}},
        ):
            provenance = release_acceptance._provenance(ROOT, "hf")
        self.assertEqual(provenance["status"], "blocked")
        self.assertIn("source commit is not the current public main commit", provenance["reasons"])

    def test_model_provenance_labels_offline_bundle(self) -> None:
        with mock.patch.object(
            release_acceptance,
            "_source_provenance",
            return_value={
                "repository": release_acceptance.EXPECTED_SOURCE_REPOSITORY,
                "commit": "a" * 40,
                "public_main_commit": "a" * 40,
                "working_tree_clean": True,
                "commands": [],
            },
        ), mock.patch.object(
            release_acceptance,
            "_model_provenance",
            return_value={
                "source_kind": "offline-bundle",
                "revision": "b" * 40,
                "profile_status": "admitted",
                "admission_eligible": True,
            },
        ), mock.patch.object(
            release_acceptance,
            "_environment_provenance",
            return_value={"npu_smi": {"argv": ["npu-smi", "info"]}},
        ):
            provenance = release_acceptance._provenance(ROOT, "offline-bundle")
        self.assertEqual(provenance["status"], "passed")
        self.assertEqual(provenance["model"]["source_kind"], "offline-bundle")
        self.assertEqual(provenance["model"]["evidence_label"], "offline-bundle")

    def test_static_kv_model_provenance_uses_verified_public_artifacts(self) -> None:
        verified = {
            "source_repository": "zhouxzh/ascend310b-llm-om-zoo",
            "source_revision": "b" * 40,
            "target": {"soc": "Ascend310B4", "tier": "8T"},
            "artifacts": [
                {
                    "name": "b4-om",
                    "path": "/data/Ascend310B4/model.om",
                    "bytes": 10,
                    "sha256": "c" * 64,
                }
            ],
            "runtime_locks": {"runtime_om_lock": "/data/Ascend310B4/model.om.lock.json"},
        }
        with mock.patch.dict(
            "os.environ", {"ASCEND_EDGE_CHAT_DATA_DIR": "/data"}, clear=False
        ), mock.patch(
            "ascend_edge_chat.artifacts.static_release_store.verify_static_release_artifacts",
            return_value=verified,
        ):
            result = release_acceptance._static_kv_model_provenance(ROOT, "hf")
        self.assertEqual(result["repository"], "zhouxzh/ascend310b-llm-om-zoo")
        self.assertEqual(result["target"]["soc"], "Ascend310B4")
        self.assertEqual(result["profile_status"], "formal")
        self.assertEqual(result["artifacts"][0]["observed_bytes"], 10)

    def test_provenance_labels_the_public_static_release_as_formal_machine(self) -> None:
        with mock.patch.object(
            release_acceptance,
            "_source_provenance",
            return_value={
                "repository": release_acceptance.EXPECTED_SOURCE_REPOSITORY,
                "commit": "a" * 40,
                "public_main_commit": "a" * 40,
                "working_tree_clean": True,
                "commands": [],
            },
        ), mock.patch.object(
            release_acceptance,
            "_model_provenance",
            return_value={
                "source_kind": "hf",
                "revision": "b" * 40,
                "profile_status": "formal",
                "admission_eligible": True,
            },
        ), mock.patch.object(
            release_acceptance,
            "_environment_provenance",
            return_value={"npu_smi": {"argv": ["npu-smi", "info"]}},
        ):
            provenance = release_acceptance._provenance(ROOT, "hf")
        self.assertEqual(provenance["status"], "passed")
        self.assertEqual(provenance["model"]["evidence_label"], "formal-machine-release")

    def test_provenance_labels_a_technical_preview_profile_without_blocking_it(self) -> None:
        with mock.patch.object(
            release_acceptance,
            "_source_provenance",
            return_value={
                "repository": release_acceptance.EXPECTED_SOURCE_REPOSITORY,
                "commit": "a" * 40,
                "public_main_commit": "a" * 40,
                "working_tree_clean": True,
                "commands": [],
            },
        ), mock.patch.object(
            release_acceptance,
            "_model_provenance",
            return_value={
                "source_kind": "hf",
                "revision": "b" * 40,
                "profile_status": "experimental_isolated",
                "admission_eligible": False,
            },
        ), mock.patch.object(
            release_acceptance,
            "_environment_provenance",
            return_value={"npu_smi": {"argv": ["npu-smi", "info"]}},
        ):
            provenance = release_acceptance._provenance(ROOT, "hf")
        self.assertEqual(provenance["status"], "passed")
        self.assertEqual(provenance["model"]["evidence_label"], "technical-preview")
