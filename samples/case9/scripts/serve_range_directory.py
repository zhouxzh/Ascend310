#!/usr/bin/env python3
"""Serve one read-only directory with HTTP byte-range support.

This controller-side helper exists for interrupted board artifact transfers.
It serves a single explicit directory and supports the ``Range`` header used
by ``curl --continue-at -``.  It does not enumerate parent directories, alter
files, or start a model service.
"""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re
import shutil
from typing import BinaryIO, Optional, Tuple


_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


class RangeNotSatisfiable(ValueError):
    """The client requested a range this small server does not accept."""


class RangeRequestHandler(SimpleHTTPRequestHandler):
    """``SimpleHTTPRequestHandler`` with one safe, single-range extension."""

    range_bounds: Optional[Tuple[int, int]] = None

    def send_head(self) -> Optional[BinaryIO]:
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            return super().send_head()
        try:
            stream = open(path, "rb")
        except OSError:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return None
        try:
            size = os.fstat(stream.fileno()).st_size
            try:
                self.range_bounds = self._parse_range(size)
            except RangeNotSatisfiable:
                stream.close()
                return None
            if self.range_bounds is None:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Length", str(size))
            else:
                start, end = self.range_bounds
                self.send_response(HTTPStatus.PARTIAL_CONTENT)
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.send_header("Content-Length", str(end - start + 1))
                stream.seek(start)
            self.send_header("Content-type", self.guess_type(path))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return stream
        except Exception:
            stream.close()
            raise

    def _parse_range(self, size: int) -> Optional[Tuple[int, int]]:
        header = self.headers.get("Range")
        if not header:
            return None
        matched = _RANGE.fullmatch(header.strip())
        if matched is None:
            self._range_not_satisfiable(size)
            raise RangeNotSatisfiable("unsupported range syntax")
        first, last = matched.groups()
        if not first and not last:
            self._range_not_satisfiable(size)
            raise RangeNotSatisfiable("empty range")
        if not first:
            length = int(last)
            if length <= 0:
                self._range_not_satisfiable(size)
                raise RangeNotSatisfiable("invalid suffix range")
            start, end = max(0, size - length), size - 1
        else:
            start = int(first)
            end = int(last) if last else size - 1
            if start >= size or start < 0 or end < start:
                self._range_not_satisfiable(size)
                raise RangeNotSatisfiable("range outside file")
            end = min(end, size - 1)
        return start, end

    def _range_not_satisfiable(self, size: int) -> None:
        self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
        self.send_header("Content-Range", f"bytes */{size}")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def copyfile(self, source: BinaryIO, outputfile: BinaryIO) -> None:
        if self.range_bounds is None:
            shutil.copyfileobj(source, outputfile)
            return
        remaining = self.range_bounds[1] - self.range_bounds[0] + 1
        while remaining:
            block = source.read(min(1024 * 1024, remaining))
            if not block:
                break
            outputfile.write(block)
            remaining -= len(block)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", required=True, help="Explicit controller LAN address")
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--directory", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    directory = args.directory.expanduser().resolve()
    if not directory.is_dir():
        raise SystemExit(f"directory does not exist: {directory}")
    if not 1 <= args.port <= 65535:
        raise SystemExit("port must be between 1 and 65535")

    def handler(*handler_args, **handler_kwargs):
        return RangeRequestHandler(*handler_args, directory=str(directory), **handler_kwargs)

    server = ThreadingHTTPServer((args.bind, args.port), handler)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
