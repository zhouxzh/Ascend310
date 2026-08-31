"""Bounded initial configuration client for official ESP32 PhotoFrame firmware.

This module is deliberately separate from :mod:`photoframe_push`.  It does
not send photos to an e-paper display.  It validates a LAN device address,
checks that the target identifies itself as the upstream PhotoFrame firmware,
then writes the URL Rotation configuration that makes the ESP32 fetch images
from Case7 by itself.
"""

from __future__ import annotations

import ipaddress
import json
import socket
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


MAX_RESPONSE_BYTES = 64 * 1024
PRIVATE_V4_NETWORKS = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


class ProvisionError(ValueError):
    """A failed or unsafe PhotoFrame provisioning exchange."""

    def __init__(self, message: str, *, status_code: Optional[int] = None, kind: str = "request"):
        super().__init__(message)
        self.status_code = status_code
        self.kind = kind


@dataclass(frozen=True)
class ProvisionResult:
    """Sanitized evidence returned after one URL Rotation configuration run."""

    device_url: str
    device_hardware_id: Optional[str]
    firmware_version: Optional[str]
    board_name: Optional[str]
    configured_image_url: str
    rotation_cron: tuple[str, ...]
    display_orientation: str
    rotate_requested: bool
    rotate_status: str
    rotate_error: Optional[str] = None
    rotate_http_status: Optional[int] = None


class _NoRedirect(HTTPRedirectHandler):
    """Return redirect responses to the caller instead of following them."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802 - stdlib API name
        return None


def normalize_device_url(value: str) -> str:
    """Return a safe local PhotoFrame root URL without resolving DNS.

    The management API is LAN-only but unauthenticated, so permitting arbitrary
    URLs would turn it into an SSRF endpoint.  Official PhotoFrame firmware
    serves its setup page on plain HTTP port 80; accepting only a literal RFC
    1918 IPv4 root is sufficient for the supported boards and has no DNS or
    redirect ambiguity.
    """

    text = str(value or "").strip()
    if not text or len(text) > 256 or any(char in text for char in "\r\n"):
        raise ProvisionError("device_url must be a private IPv4 HTTP root URL", kind="invalid_url")
    try:
        parsed = urlsplit(text)
    except ValueError as exc:
        raise ProvisionError("device_url must be a private IPv4 HTTP root URL", kind="invalid_url") from exc
    if parsed.scheme.lower() != "http" or not parsed.hostname:
        raise ProvisionError("device_url must use http and include a private IPv4 address", kind="invalid_url")
    if parsed.username is not None or parsed.password is not None:
        raise ProvisionError("device_url must not include credentials", kind="invalid_url")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ProvisionError("device_url must be the device root without a path, query, or fragment", kind="invalid_url")
    try:
        address = ipaddress.IPv4Address(parsed.hostname)
    except ipaddress.AddressValueError as exc:
        raise ProvisionError("device_url must use a literal private IPv4 address", kind="invalid_url") from exc
    if not any(address in network for network in PRIVATE_V4_NETWORKS):
        raise ProvisionError("device_url must use an RFC1918 private IPv4 address", kind="invalid_url")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ProvisionError("device_url has an invalid port", kind="invalid_url") from exc
    if port not in {None, 80}:
        raise ProvisionError("device_url must use the PhotoFrame HTTP port 80", kind="invalid_url")
    return urlunsplit(("http", str(address), "", "", ""))


def _safe_text(value: object, limit: int = 200) -> Optional[str]:
    text = str(value or "").strip()
    return text[:limit] if text else None


class PhotoFrameProvisioner:
    """Single-run, no-retry client for PhotoFrame URL Rotation setup."""

    def __init__(
        self,
        opener: Optional[Callable[[Request, float], object]] = None,
        timeout_seconds: float = 8.0,
    ):
        if timeout_seconds <= 0 or timeout_seconds > 30:
            raise ValueError("timeout_seconds must be between 0 and 30")
        self.timeout_seconds = float(timeout_seconds)
        if opener is None:
            # Do not inherit proxy environment variables.  A private LAN
            # address must be reached directly; otherwise a desktop proxy can
            # turn a local setup request into an unrelated Internet request.
            factory = build_opener(ProxyHandler({}), _NoRedirect())

            def opener(request: Request, timeout: float):
                return factory.open(request, timeout=timeout)

        self._opener = opener

    @staticmethod
    def _response_body(response: object) -> bytes:
        reader = getattr(response, "read", None)
        if not callable(reader):
            return b""
        value = reader(MAX_RESPONSE_BYTES + 1)
        if not isinstance(value, bytes):
            value = bytes(value)
        if len(value) > MAX_RESPONSE_BYTES:
            raise ProvisionError("PhotoFrame response exceeds 64 KiB", kind="response")
        return value

    @staticmethod
    def _decode_json(body: bytes, *, endpoint: str) -> dict:
        try:
            value = json.loads(body.decode("utf-8")) if body else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProvisionError(f"PhotoFrame {endpoint} did not return JSON", kind="response") from exc
        if not isinstance(value, dict):
            raise ProvisionError(f"PhotoFrame {endpoint} did not return a JSON object", kind="response")
        return value

    def _request_json(
        self,
        root: str,
        path: str,
        *,
        method: str = "GET",
        payload: Optional[dict] = None,
        allow_empty: bool = False,
        require_success_status: bool = False,
    ) -> tuple[dict, int]:
        url = root + path
        body = None
        headers = {"Accept": "application/json", "User-Agent": "AscendCase7-PhotoFrameProvision/1"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        request = Request(url, data=body, headers=headers, method=method)
        response = None
        try:
            response = self._opener(request, self.timeout_seconds)
            status = int(getattr(response, "status", getattr(response, "code", 200)))
            raw = self._response_body(response)
        except HTTPError as exc:
            raw = exc.read(MAX_RESPONSE_BYTES + 1) if hasattr(exc, "read") else b""
            if len(raw) > MAX_RESPONSE_BYTES:
                raw = raw[:MAX_RESPONSE_BYTES]
            detail = raw.decode("utf-8", "replace")[:300].strip()
            raise ProvisionError(
                f"PhotoFrame {path} returned HTTP {exc.code}{': ' + detail if detail else ''}",
                status_code=int(exc.code),
                kind="http",
            ) from exc
        except (OSError, URLError, TimeoutError, socket.timeout) as exc:
            raise ProvisionError(f"PhotoFrame {path} is unreachable: {exc}", kind="transport") from exc
        finally:
            closer = getattr(response, "close", None)
            if callable(closer):
                closer()
        if not 200 <= status < 300:
            detail = raw.decode("utf-8", "replace")[:300].strip()
            raise ProvisionError(
                f"PhotoFrame {path} returned HTTP {status}{': ' + detail if detail else ''}",
                status_code=status,
                kind="http",
            )
        if not raw and allow_empty:
            return {}, status
        value = self._decode_json(raw, endpoint=path)
        if require_success_status and str(value.get("status") or "").strip().lower() != "success":
            raise ProvisionError(
                f"PhotoFrame {path} did not confirm status=success",
                status_code=status,
                kind="rejected",
            )
        return value, status

    @staticmethod
    def _validate_system_info(
        system_info: dict,
        *,
        native_size: tuple[int, int],
        expected_profile_id: Optional[str] = None,
    ) -> None:
        if str(system_info.get("project_name") or "").strip().lower() != "esp32-photoframe":
            raise ProvisionError("target is not official esp32-photoframe firmware", kind="identity")
        try:
            size = (int(system_info.get("width")), int(system_info.get("height")))
        except (TypeError, ValueError) as exc:
            raise ProvisionError("PhotoFrame system information is missing display dimensions", kind="identity") from exc
        if size not in {native_size, native_size[::-1]}:
            raise ProvisionError(
                f"PhotoFrame display is {size[0]}x{size[1]}, expected {native_size[0]}x{native_size[1]}",
                kind="identity",
            )
        # A shared 800x480 contract is not enough to identify the product.
        # When the firmware exposes a board/product name, require it to agree
        # with the profile selected by the operator.  Older builds may omit
        # those fields, so an empty identity remains acceptable after the
        # project name and dimensions have passed.
        profile = str(expected_profile_id or "").strip().lower()
        if profile:
            identity = " ".join(
                str(system_info.get(key) or "").strip().lower()
                for key in ("board_name", "product_name", "device_name")
            ).strip()
            # A single vendor or product token is not enough: both supported
            # panels use the same 800x480 contract.  Require the vendor and a
            # product-family token together.  Older PhotoPainter builds report
            # names such as ``Waveshare 7.3 7-Color`` rather than the literal
            # ``PhotoPainter`` string, so retain those documented aliases.
            identity_groups = {
                "waveshare_photopainter_73": (
                    ("waveshare",),
                    ("photopainter", "photo painter", "7.3", "7in3", "7inch"),
                ),
                "seeedstudio_reterminal_e1002": (
                    ("seeed",),
                    ("e1002", "reterminal"),
                ),
            }.get(profile)
            if identity_groups and identity:
                vendor_tokens, product_tokens = identity_groups
                matches_vendor = any(token in identity for token in vendor_tokens)
                matches_product = any(token in identity for token in product_tokens)
                if not (matches_vendor and matches_product):
                    raise ProvisionError(
                        f"PhotoFrame identity {identity!r} does not match profile {profile}",
                        kind="identity",
                    )

    @staticmethod
    def _validate_config(config: dict, expected: dict) -> None:
        if config.get("auto_rotate") is not True:
            raise ProvisionError("PhotoFrame did not persist auto_rotate=true", kind="verification")
        if config.get("rotation_mode") != "url":
            raise ProvisionError("PhotoFrame did not persist rotation_mode=url", kind="verification")
        if config.get("image_url") != expected["image_url"]:
            raise ProvisionError("PhotoFrame did not persist the Case7 image URL", kind="verification")
        if list(config.get("rotate_cron") or []) != list(expected["rotate_cron"]):
            raise ProvisionError("PhotoFrame did not persist the requested rotation schedule", kind="verification")
        if config.get("display_orientation") != expected["display_orientation"]:
            raise ProvisionError("PhotoFrame did not persist the requested display orientation", kind="verification")
        if config.get("display_rotation_deg") != expected["display_rotation_deg"]:
            raise ProvisionError("PhotoFrame did not persist display_rotation_deg=0", kind="verification")
        if config.get("deep_sleep_enabled") is not False:
            raise ProvisionError("PhotoFrame did not persist deep_sleep_enabled=false", kind="verification")
        if config.get("save_downloaded_images") is not False:
            raise ProvisionError("PhotoFrame did not persist save_downloaded_images=false", kind="verification")

    def provision(
        self,
        device_url: str,
        *,
        image_url: str,
        rotation_cron: list[str] | tuple[str, ...],
        display_orientation: str,
        native_size: tuple[int, int],
        expected_profile_id: Optional[str] = None,
        trigger_now: bool = True,
    ) -> ProvisionResult:
        """Configure URL Rotation and optionally ask the ESP32 to rotate now.

        ``POST /api/rotate`` only asks the device to perform its own fetch.  A
        successful response is deliberately recorded independently from a later
        Case7 image GET, which is the only evidence that the device reached the
        album server.
        """

        root = normalize_device_url(device_url)
        if str(display_orientation) not in {"landscape", "portrait"}:
            raise ProvisionError("display_orientation must be landscape or portrait", kind="invalid_input")
        normalized_cron = tuple(str(item).strip() for item in rotation_cron)
        if not normalized_cron or any(not item for item in normalized_cron):
            raise ProvisionError("rotation_cron must contain at least one cron rule", kind="invalid_input")
        if not isinstance(image_url, str) or not image_url.startswith(("http://", "https://")) or len(image_url) > 256:
            raise ProvisionError("Case7 image URL is invalid for PhotoFrame", kind="invalid_input")

        system_info, _ = self._request_json(root, "/api/system-info")
        self._validate_system_info(
            system_info,
            native_size=native_size,
            expected_profile_id=expected_profile_id,
        )
        # A read before PATCH ensures we fail early against an incomplete or
        # incompatible web API instead of leaving a half-described audit entry.
        # Keep only the fields this transaction changes so a failed read-back
        # can best-effort restore the device's previous URL Rotation state.
        previous_config, _ = self._request_json(root, "/api/config")
        desired = {
            "auto_rotate": True,
            "rotate_cron": list(normalized_cron),
            "rotation_mode": "url",
            "image_url": image_url,
            "display_orientation": str(display_orientation),
            # Case7 has only landscape/portrait modes, so reset arbitrary
            # angle state rather than presenting a 360-degree control.
            "display_rotation_deg": 0,
            # Keep Wi-Fi online while proving the first retrieval path.
            "deep_sleep_enabled": False,
            # URL Rotation is a stream from Case7. Do not fill device storage
            # with a separate copy of every server-rendered image.
            "save_downloaded_images": False,
        }
        # Mark before issuing PATCH: a transport failure can happen after the
        # ESP32 has accepted the body but before the client receives a reply.
        # In that case restoration is still safer than silently leaving a URL
        # for a server record that will be rolled back.
        patch_attempted = False
        try:
            patch_attempted = True
            self._request_json(
                root,
                "/api/config",
                method="PATCH",
                payload=desired,
                require_success_status=True,
            )
            saved_config, _ = self._request_json(root, "/api/config")
            self._validate_config(saved_config, desired)
        except ProvisionError as exc:
            if patch_attempted:
                restore_keys = (
                    "auto_rotate",
                    "rotate_cron",
                    "rotation_mode",
                    "image_url",
                    "display_orientation",
                    "display_rotation_deg",
                    "deep_sleep_enabled",
                    "save_downloaded_images",
                )
                restore = {key: previous_config[key] for key in restore_keys if key in previous_config}
                if restore:
                    try:
                        self._request_json(
                            root,
                            "/api/config",
                            method="PATCH",
                            payload=restore,
                            require_success_status=True,
                        )
                    except ProvisionError as rollback_exc:
                        raise ProvisionError(
                            f"{exc}; device configuration may have changed and rollback failed: {rollback_exc}",
                            status_code=exc.status_code,
                            kind=exc.kind,
                        ) from exc
            raise

        rotate_status = "not_requested"
        rotate_error = None
        rotate_http_status = None
        if trigger_now:
            try:
                _, rotate_http_status = self._request_json(
                    root,
                    "/api/rotate",
                    method="POST",
                    # The documented endpoint has no required response body;
                    # a successful empty response still means the ESP32
                    # accepted its own rotation request.
                    allow_empty=True,
                    require_success_status=True,
                )
                rotate_status = "requested"
            except ProvisionError as exc:
                # The settings above are already verified.  Report an immediate
                # rotation failure without rolling back a usable URL schedule.
                # A sleeping/busy device may time out after accepting the
                # command and still fetch Case7 shortly afterwards. Keep that
                # transport outcome distinct from an explicit HTTP/JSON
                # rejection so callers do not present it as a failed pairing.
                rotate_status = "timed_out" if exc.kind == "transport" else "failed"
                rotate_error = str(exc)
                rotate_http_status = exc.status_code

        return ProvisionResult(
            device_url=root,
            device_hardware_id=_safe_text(system_info.get("device_id")),
            firmware_version=_safe_text(system_info.get("version")),
            board_name=_safe_text(system_info.get("board_name")),
            configured_image_url=image_url,
            rotation_cron=normalized_cron,
            display_orientation=str(display_orientation),
            rotate_requested=bool(trigger_now),
            rotate_status=rotate_status,
            rotate_error=rotate_error,
            rotate_http_status=rotate_http_status,
        )
