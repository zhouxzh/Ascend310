#!/usr/bin/env python3
"""Exercise the FastAPI upload recognition endpoint with a synthetic image."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import urllib.error
import urllib.request


def _multipart_field(name: str, value: str, boundary: str) -> bytes:
    return (
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
        f'{value}\r\n'
    ).encode()


def _multipart_file(name: str, path: Path, boundary: str) -> bytes:
    payload = path.read_bytes()
    header = (
        f'--{boundary}\r\n'
        f'Content-Disposition: form-data; name="{name}"; filename="{path.name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    return header + payload + b"\r\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--url", default="http://127.0.0.1:7860")
    args = parser.parse_args()
    if not args.image.is_file():
        raise FileNotFoundError(args.image)

    boundary = "----PalmprintSmokeBoundary"
    body = b"".join(
        (
            _multipart_file("image", args.image.resolve(), boundary),
            _multipart_field("model_id", "ccnet", boundary),
            _multipart_field("precision", "mixed_fp16", boundary),
            _multipart_field("threshold", "0.75", boundary),
            _multipart_field("assume_roi", "true", boundary),
            f"--{boundary}--\r\n".encode(),
        )
    )
    request = urllib.request.Request(
        f"{args.url.rstrip('/')}/api/recognitions",
        data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"recognition request failed ({exc.code}): {detail}") from exc

    if not isinstance(result, dict):
        raise RuntimeError(f"Unexpected recognition response: {result!r}")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
