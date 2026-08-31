"""Bounded HTTP client for the PhotoFrame direct-display API.

    The upstream PhotoFrame API documents ``POST /api/display-image`` as an
    endpoint that accepts a JPEG body and displays it immediately.  This module is
    deliberately small and synchronous: the album scheduler owns the only push
    queue, and callers must provide an explicit device base URL.  It does not
    discover devices, probe the LAN, or assume that URL Rotation is enabled.
"""

from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass
from typing import Callable, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, build_opener


class PushError(ValueError):
    """Raised when a direct PhotoFrame push cannot be completed."""


MAX_PHOTOFRAME_UPLOAD_BYTES = 5 * 1024 * 1024
PUSH_PROTOCOLS = frozenset({"photoframe_api", "waveshare_dataup", "case7_push"})


def normalize_base_url(value: str) -> str:
    """Validate and normalize an explicitly configured PhotoFrame URL.

    A URL may include a reverse-proxy path prefix, but credentials, query
    strings and fragments are rejected.  The endpoint path is appended by the
    client, so callers should provide the device root rather than
    ``/api/display-image`` itself.
    """

    text = str(value or "").strip()
    if not text or len(text) > 512 or any(char in text for char in "\r\n"):
        raise PushError("push.base_url must be a valid HTTP(S) URL")
    try:
        parsed = urlsplit(text)
    except ValueError as exc:
        raise PushError("push.base_url must be a valid HTTP(S) URL") from exc
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise PushError("push.base_url must use http or https and include a host")
    if parsed.username is not None or parsed.password is not None:
        raise PushError("push.base_url must not include credentials")
    if parsed.query or parsed.fragment:
        raise PushError("push.base_url must not include a query or fragment")
    try:
        parsed.port
    except ValueError as exc:
        raise PushError("push.base_url has an invalid port") from exc
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, path, "", ""))


@dataclass(frozen=True)
class PushResult:
    """Outcome of one direct-display request."""

    status_code: int
    attempts: int
    response_body: str = ""


class PhotoFramePushClient:
    """Send a rendered JPEG to an explicitly configured PhotoFrame.

    ``opener`` is injectable for deterministic tests.  Retries are bounded and
    only applied to transport failures and transient HTTP statuses; a malformed
    request or unsupported endpoint is surfaced immediately.
    """

    def __init__(
        self,
        opener: Optional[Callable[[Request, float], object]] = None,
        timeout_seconds: float = 8.0,
        attempts: int = 2,
        retry_delay_seconds: float = 0.25,
    ):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if attempts < 1 or attempts > 3:
            raise ValueError("attempts must be between 1 and 3")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must be non-negative")
        self.timeout_seconds = float(timeout_seconds)
        self.attempts = int(attempts)
        self.retry_delay_seconds = float(retry_delay_seconds)
        if opener is None:
            factory = build_opener()

            def opener(request: Request, timeout: float):
                return factory.open(request, timeout=timeout)

        self._opener = opener

    @staticmethod
    def _endpoint(base_url: str, path: str) -> str:
        root = normalize_base_url(base_url)
        if path not in {"/api/display-image", "/dataUP", "/api/case7/push"}:
            raise PushError("unsupported PhotoFrame push endpoint")
        return root + path

    @staticmethod
    def _body(value: object) -> str:
        if isinstance(value, bytes):
            value = value[:512]
        else:
            value = str(value)[:512]
        if isinstance(value, bytes):
            return value.decode("utf-8", "replace")
        return value

    @staticmethod
    def _retryable_status(status: int) -> bool:
        return status in {408, 429} or 500 <= status <= 599

    def _push(
        self,
        base_url: str,
        body: bytes,
        *,
        endpoint: str,
        content_type: str,
        photo_id: Optional[int] = None,
        etag: Optional[str] = None,
        headers: Optional[Mapping[str, str]] = None,
        required_response_header: Optional[tuple[str, str]] = None,
    ) -> PushResult:
        if not isinstance(body, (bytes, bytearray)) or not body:
            raise PushError("push body must be non-empty bytes")
        if len(body) > MAX_PHOTOFRAME_UPLOAD_BYTES:
            raise PushError("push body exceeds the PhotoFrame 5 MiB upload limit")
        endpoint_url = self._endpoint(base_url, endpoint)
        request_headers = {
            "Content-Type": content_type,
            "Content-Length": str(len(body)),
            "User-Agent": "AscendCase7-PhotoFramePush/1",
        }
        if photo_id is not None:
            request_headers["X-Album-Photo-Id"] = str(int(photo_id))
        if etag:
            request_headers["X-Album-ETag"] = str(etag)[:200]
        if headers:
            for key, value in headers.items():
                if not str(key) or any(char in str(key) for char in "\r\n"):
                    raise PushError("invalid push header name")
                if any(char in str(value) for char in "\r\n"):
                    raise PushError("invalid push header value")
                request_headers[str(key)] = str(value)

        last_error = None
        for attempt in range(1, self.attempts + 1):
            request = Request(endpoint_url, data=bytes(body), headers=request_headers, method="POST")
            try:
                response = self._opener(request, self.timeout_seconds)
                status = int(getattr(response, "status", getattr(response, "code", 200)))
                raw = response.read(512) if hasattr(response, "read") else b""
                response_body = self._body(raw)
                close = getattr(response, "close", None)
                if required_response_header is not None and 200 <= status < 300:
                    header_name, expected_value = required_response_header
                    actual_value = None
                    getheader = getattr(response, "getheader", None)
                    if callable(getheader):
                        actual_value = getheader(header_name)
                    if actual_value is None:
                        response_headers = getattr(response, "headers", None)
                        if response_headers is not None:
                            getter = getattr(response_headers, "get", None)
                            if callable(getter):
                                actual_value = getter(header_name)
                    if str(actual_value or "").strip() != expected_value:
                        if callable(close):
                            close()
                        raise PushError(
                            f"PhotoFrame response missing {header_name}: {expected_value}"
                        )
                if callable(close):
                    close()
                if 200 <= status < 300:
                    return PushResult(status_code=status, attempts=attempt, response_body=response_body)
                last_error = PushError(f"PhotoFrame returned HTTP {status}: {response_body}")
                if not self._retryable_status(status):
                    raise last_error
            except HTTPError as exc:
                raw = exc.read(512) if hasattr(exc, "read") else b""
                response_body = self._body(raw)
                last_error = PushError(f"PhotoFrame returned HTTP {exc.code}: {response_body}")
                if not self._retryable_status(int(exc.code)):
                    raise last_error from exc
            except (OSError, URLError, TimeoutError, socket.timeout) as exc:
                last_error = PushError(f"PhotoFrame push transport failed: {exc}")
            if attempt < self.attempts and self.retry_delay_seconds:
                time.sleep(self.retry_delay_seconds)
        raise last_error or PushError("PhotoFrame push failed")

    def push_jpeg(
        self,
        base_url: str,
        jpeg: bytes,
        *,
        photo_id: Optional[int] = None,
        etag: Optional[str] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> PushResult:
        """Push raw JPEG to the official PhotoFrame v2 API."""

        return self._push(
            base_url,
            jpeg,
            endpoint="/api/display-image",
            content_type="image/jpeg",
            photo_id=photo_id,
            etag=etag,
            headers=headers,
        )

    def push_case7_jpeg(
        self,
        base_url: str,
        jpeg: bytes,
        *,
        photo_id: Optional[int] = None,
        etag: Optional[str] = None,
    ) -> PushResult:
        """Push JPEG to the Case7-modified firmware endpoint.

        The response marker is mandatory.  This prevents an unmodified
        E1002 image server, proxy, or captive portal from being mistaken for
        the firmware that implements the Case7 receiver.
        """

        return self._push(
            base_url,
            jpeg,
            endpoint="/api/case7/push",
            content_type="image/jpeg",
            photo_id=photo_id,
            etag=etag,
            headers={"X-Case7-Push": "1"},
            required_response_header=("X-Case7-Push", "1"),
        )

    def push_bmp(
        self,
        base_url: str,
        bmp: bytes,
        *,
        photo_id: Optional[int] = None,
        etag: Optional[str] = None,
    ) -> PushResult:
        """Push a raw 24-bit BMP using the Waveshare demo ``/dataUP`` contract.

        The demo's browser helper uses multipart only for a different upload
        page; the embedded ``/dataUP`` handler reads the request body directly
        and stores it as ``user_send.bmp``.  Keep this transport explicit and
        never infer it from a failed request to another firmware.
        """

        if not isinstance(bmp, (bytes, bytearray)) or not bmp:
            raise PushError("BMP body must be non-empty bytes")
        if not bytes(bmp).startswith(b"BM"):
            raise PushError("BMP body does not have a Windows BMP signature")
        return self._push(
            base_url,
            bytes(bmp),
            endpoint="/dataUP",
            content_type="image/bmp",
            photo_id=photo_id,
            etag=etag,
            headers={"X-Album-Transport": "waveshare-dataup-raw"},
        )

    # Kept as a source-compatible alias for early local callers.  It is
    # intentionally raw BMP, despite the historical method name.
    def push_bmp_multipart(self, base_url: str, bmp: bytes, *, photo_id: Optional[int] = None, etag: Optional[str] = None) -> PushResult:
        return self.push_bmp(base_url, bmp, photo_id=photo_id, etag=etag)


def parse_response_body(value: str) -> dict:
    """Best-effort JSON decoding for diagnostics; never required for success."""

    try:
        parsed = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}
