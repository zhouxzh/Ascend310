#!/usr/bin/env python3
"""Receive authenticated HTTP PUT uploads into a constrained local directory."""

from __future__ import annotations

import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path, PurePosixPath
from urllib.parse import unquote, urlsplit


def resolve_upload_target(root: Path, relative_path: str) -> Path:
    decoded = unquote(relative_path)
    relative = PurePosixPath(decoded)
    if relative.is_absolute() or not relative.parts:
        raise ValueError("upload path must be relative")
    if any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("upload path contains an unsafe segment")
    resolved_root = root.resolve()
    target = resolved_root.joinpath(*relative.parts).resolve()
    if resolved_root not in target.parents:
        raise ValueError("upload path escapes the output directory")
    return target


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class UploadServer(ThreadingHTTPServer):
    output_dir: Path
    token: str
    max_bytes: int


class UploadHandler(BaseHTTPRequestHandler):
    server: UploadServer

    def send_text(self, status: int, message: str) -> None:
        payload = (message + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def authenticated_relative_path(self) -> str:
        path = urlsplit(self.path).path.lstrip("/")
        token, separator, relative = path.partition("/")
        if token != self.server.token or not separator:
            raise PermissionError("invalid upload token")
        return relative

    def do_GET(self) -> None:  # noqa: N802
        try:
            relative = self.authenticated_relative_path()
        except PermissionError:
            self.send_text(403, "forbidden")
            return
        if relative != "status":
            self.send_text(404, "not found")
            return
        self.send_text(200, "ready")

    def do_HEAD(self) -> None:  # noqa: N802
        try:
            relative = self.authenticated_relative_path()
            target = resolve_upload_target(self.server.output_dir, relative)
        except PermissionError:
            self.send_error(403)
            return
        except ValueError:
            self.send_error(400)
            return
        if not target.is_file():
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(target.stat().st_size))
        self.send_header("X-Content-SHA256", sha256_file(target))
        self.end_headers()

    def do_PUT(self) -> None:  # noqa: N802
        try:
            relative = self.authenticated_relative_path()
            target = resolve_upload_target(self.server.output_dir, relative)
        except PermissionError:
            self.send_text(403, "forbidden")
            return
        except ValueError as exc:
            self.send_text(400, str(exc))
            return

        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.send_text(411, "valid Content-Length required")
            return
        if length < 0 or length > self.server.max_bytes:
            self.send_text(413, "upload is too large")
            return

        target.parent.mkdir(parents=True, exist_ok=True)
        expected_sha256 = self.headers.get("X-Content-SHA256", "").lower()
        if expected_sha256 and (
            len(expected_sha256) != 64
            or any(character not in "0123456789abcdef" for character in expected_sha256)
        ):
            self.send_text(400, "invalid X-Content-SHA256")
            return
        temporary = target.with_name(
            f".{target.name}.part-{os.getpid()}-{id(self)}"
        )
        remaining = length
        try:
            with temporary.open("wb") as output:
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise OSError("upload ended before Content-Length bytes arrived")
                    output.write(chunk)
                    remaining -= len(chunk)
            if expected_sha256 and sha256_file(temporary) != expected_sha256:
                temporary.unlink(missing_ok=True)
                self.send_text(422, "SHA256 mismatch")
                return
            os.replace(temporary, target)
        except OSError as exc:
            temporary.unlink(missing_ok=True)
            self.send_text(500, str(exc))
            return

        self.send_text(201, f"stored {relative} ({length} bytes)")

    def log_message(self, format_string: str, *args: object) -> None:
        print(f"{self.client_address[0]} {format_string % args}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", required=True, help="Local interface address")
    parser.add_argument("--port", type=int, default=18768)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--token", required=True)
    parser.add_argument("--max-bytes", type=int, default=1024 * 1024 * 1024)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.token or "/" in args.token:
        raise ValueError("--token must be non-empty and cannot contain '/'")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    server = UploadServer((args.bind, args.port), UploadHandler)
    server.output_dir = output_dir
    server.token = args.token
    server.max_bytes = args.max_bytes
    print(
        f"Listening on http://{args.bind}:{args.port}/<token>/ -> {output_dir}",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
