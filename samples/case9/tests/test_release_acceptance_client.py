from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import threading
import unittest


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
