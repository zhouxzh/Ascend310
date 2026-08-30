"""Loopback-only OpenAI-compatible upstream used for board protocol checks.

It is deliberately deterministic and does not perform model inference. Start
it only for a short acceptance run alongside the gateway, then stop it.
"""

from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class UpstreamStubHandler(BaseHTTPRequestHandler):
    """Return a small, contract-correct JSON or SSE chat completion."""

    server_version = "Case9BoardUpstreamStub/1.0"

    def log_message(self, _format: str, *_args: Any) -> None:
        """Keep synthetic request content out of test logs."""

    def do_POST(self) -> None:
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(content_length))
        except (ValueError, json.JSONDecodeError):
            self._json_response(400, {"error": {"message": "invalid JSON"}})
            return
        if not isinstance(payload, dict):
            self._json_response(400, {"error": {"message": "invalid request"}})
            return

        model = str(payload.get("model", "unknown"))
        if payload.get("stream"):
            self._stream_response(model)
            return
        self._json_response(
            200,
            {
                "id": "board-stub-completion",
                "object": "chat.completion",
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "board upstream JSON acceptance response",
                        },
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    def _json_response(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _stream_response(self, model: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for content in ("board upstream ", "SSE acceptance response"):
            chunk = {
                "id": "board-stub-stream",
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": content},
                        "finish_reason": None,
                    }
                ],
            }
            self.wfile.write(
                f"data: {json.dumps(chunk, separators=(',', ':'))}\n\n".encode("utf-8")
            )
            self.wfile.flush()
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def main() -> None:
    parser = argparse.ArgumentParser(description="Case9 board OpenAI upstream stub")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=18080)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), UpstreamStubHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
