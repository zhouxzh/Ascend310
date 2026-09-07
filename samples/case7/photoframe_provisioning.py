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
import os
import socket
import subprocess
from dataclasses import dataclass
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from device_registry import DeviceError, photo_frame_hardware_rotation_deg


MAX_RESPONSE_BYTES = 64 * 1024
PHOTOFRAME_MDNS_SERVICE_TYPE = "_esp32-pframe._tcp.local."
AVAHI_BROWSE_PATH = "/usr/bin/avahi-browse"
# ESP32 Wi-Fi association and HTTP startup can take several seconds after the
# physical wake key.  A three-second browse window regularly expired while
# Avahi was still resolving the service, so discovery now waits the full
# bounded ten seconds.
DEFAULT_MDNS_DISCOVERY_SECONDS = 10.0
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


class DiscoveryError(RuntimeError):
    """A local mDNS discovery failure that did not contact a chosen device."""


@dataclass(frozen=True)
class MdnsPhotoFrameService:
    """One PhotoFrame service advertisement observed on the local LAN.

    ``hostname`` is display-only evidence from mDNS.  It is never resolved or
    used as an HTTP destination: subsequent inspection always uses one of the
    literal IPv4 addresses from the same service record.
    """

    hostname: str
    addresses: tuple[str, ...]
    port: int


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
    # Fixed physical compensation from the selected board profile.  This is
    # separate from the user-facing landscape/portrait mode.
    display_rotation_deg: int = 0
    # The persisted firmware setting. ``None`` is reserved for older test
    # doubles; real provisioning always resolves it from the device config or
    # an explicit request.
    deep_sleep_enabled: Optional[bool] = None


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


def normalize_expected_device_id(value: object) -> Optional[str]:
    """Normalize an optional immutable PhotoFrame hardware identifier.

    Device IDs are returned by the firmware, not derived from a mutable mDNS
    host name.  Keep the accepted form intentionally small and printable so
    it can be safely persisted and compared before any configuration write.
    ``None`` means that a legacy/manual registration has no identity pin.
    """

    if value is None:
        return None
    text = str(value).strip()
    if not text or len(text) > 128 or any(not (char.isalnum() or char in "._:-") for char in text):
        raise ProvisionError(
            "expected_device_id must be a non-empty hardware identifier",
            kind="invalid_input",
        )
    return text


def _private_ipv4_addresses(values: object) -> tuple[str, ...]:
    """Return unique RFC1918 IPv4 literals without resolving host names."""

    result: list[str] = []
    for raw in values or ():
        try:
            address = ipaddress.IPv4Address(str(raw))
        except ipaddress.AddressValueError:
            continue
        if any(address in network for network in PRIVATE_V4_NETWORKS):
            value = str(address)
            if value not in result:
                result.append(value)
    return tuple(result)


def _safe_mdns_hostname(value: object) -> str:
    """Keep mDNS service metadata display-only and bounded."""

    text = str(value or "").strip().rstrip(".")
    if not text:
        return ""
    return "".join(char for char in text if char.isprintable())[:255]


def _split_avahi_fields(line: str) -> list[str]:
    """Split Avahi's semicolon format while preserving escaped text fields."""

    fields: list[str] = []
    current: list[str] = []
    escaped = False
    for char in line.rstrip("\r\n"):
        if escaped:
            current.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ";":
            fields.append("".join(current))
            current = []
        else:
            current.append(char)
    if escaped:
        current.append("\\")
    fields.append("".join(current))
    return fields


def _default_avahi_browse_runner(command: tuple[str, ...], timeout_seconds: float):
    return subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )


def discover_local_photoframe_services(
    *,
    timeout_seconds: float = DEFAULT_MDNS_DISCOVERY_SECONDS,
    runner: Optional[Callable[[tuple[str, ...], float], object]] = None,
) -> list[MdnsPhotoFrameService]:
    """Browse the fixed PhotoFrame mDNS type through local Avahi only.

    The command is deliberately fixed to ``/usr/bin/avahi-browse -rpt
    _esp32-pframe._tcp``.  There is no user-controlled host, CIDR, command,
    DNS lookup, or HTTP request here.  The caller can inspect the returned
    literal RFC1918 IPv4 addresses separately with a read-only request.
    """

    try:
        bounded_timeout = float(timeout_seconds)
    except (TypeError, ValueError) as exc:
        raise DiscoveryError("mDNS discovery timeout is invalid") from exc
    if not 0.1 <= bounded_timeout <= 10.0:
        raise DiscoveryError("mDNS discovery timeout must be between 0.1 and 10 seconds")
    if runner is None:
        if not os.path.isfile(AVAHI_BROWSE_PATH) or not os.access(AVAHI_BROWSE_PATH, os.X_OK):
            raise DiscoveryError(f"mDNS discovery is unavailable: {AVAHI_BROWSE_PATH} is not executable")
        runner = _default_avahi_browse_runner
    command = (AVAHI_BROWSE_PATH, "-rpt", "_esp32-pframe._tcp")
    try:
        completed = runner(command, bounded_timeout)
    except subprocess.TimeoutExpired as exc:
        raise DiscoveryError("local mDNS discovery timed out") from exc
    except (OSError, RuntimeError) as exc:
        raise DiscoveryError(f"local mDNS discovery failed: {exc}") from exc

    return_code = int(getattr(completed, "returncode", 0) or 0)
    stdout = str(getattr(completed, "stdout", "") or "")
    stderr = str(getattr(completed, "stderr", "") or "").strip()
    if return_code != 0:
        detail = f": {stderr[:300]}" if stderr else ""
        raise DiscoveryError(f"local mDNS discovery failed with exit code {return_code}{detail}")

    grouped: dict[tuple[str, int], list[str]] = {}
    for line in stdout.splitlines():
        fields = _split_avahi_fields(line)
        # ``-p`` format: marker;interface;protocol;name;type;domain;host;ip;port;txt
        if len(fields) < 9 or fields[0] != "=":
            continue
        service_type = fields[4].strip().lower().rstrip(".")
        domain = fields[5].strip().lower().rstrip(".")
        if service_type != "_esp32-pframe._tcp" or domain != "local":
            continue
        try:
            port = int(fields[8])
        except (TypeError, ValueError):
            continue
        if port != 80:
            continue
        addresses = _private_ipv4_addresses((fields[7],))
        if not addresses:
            continue
        hostname = _safe_mdns_hostname(fields[6]) or _safe_mdns_hostname(fields[3])
        key = (hostname, port)
        bucket = grouped.setdefault(key, [])
        for address in addresses:
            if address not in bucket:
                bucket.append(address)
    return [
        MdnsPhotoFrameService(hostname=hostname, addresses=tuple(addresses), port=port)
        for (hostname, port), addresses in sorted(grouped.items())
    ]


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

    def read_system_info(self, device_url: str) -> tuple[str, dict]:
        """Read one PhotoFrame identity document without modifying the device.

        Discovery and pairing previews use this narrow method.  Keeping it
        separate from :meth:`provision` makes it mechanically impossible for
        an mDNS browse call to PATCH URL Rotation or trigger a refresh.
        """

        root = normalize_device_url(device_url)
        system_info, _ = self._request_json(root, "/api/system-info")
        return root, system_info

    @staticmethod
    def _validate_system_info(
        system_info: dict,
        *,
        native_size: tuple[int, int],
        expected_profile_id: Optional[str] = None,
        expected_device_id: Optional[str] = None,
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
        expected_id = normalize_expected_device_id(expected_device_id)
        if expected_id is not None:
            actual_id = _safe_text(system_info.get("device_id"), limit=128)
            if actual_id != expected_id:
                raise ProvisionError(
                    "PhotoFrame device_id does not match the discovered device",
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
        expected_rotation = int(expected.get("display_rotation_deg", 0))
        if config.get("display_rotation_deg") != expected_rotation:
            raise ProvisionError(
                f"PhotoFrame did not persist display_rotation_deg={expected_rotation}",
                kind="verification",
            )
        expected_sleep = expected.get("deep_sleep_enabled", True)
        if config.get("deep_sleep_enabled") is not True or expected_sleep is not True:
            raise ProvisionError(
                "PhotoFrame did not persist deep_sleep_enabled=true",
                kind="verification",
            )
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
        expected_device_id: Optional[str] = None,
        trigger_now: bool = True,
        deep_sleep_enabled: Optional[bool] = None,
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
        if deep_sleep_enabled is False:
            raise ProvisionError(
                "PhotoFrame deep sleep is fixed enabled; deep_sleep_enabled=false is not supported",
                kind="invalid_input",
            )

        expected_device_id = normalize_expected_device_id(expected_device_id)
        try:
            # The board profile owns this fixed half-turn/native-direction
            # compensation.  It must not be inferred from the requested
            # landscape/portrait orientation or exposed as a 360-degree knob.
            hardware_rotation = photo_frame_hardware_rotation_deg(expected_profile_id) if expected_profile_id else 0
        except DeviceError as exc:
            raise ProvisionError(str(exc), kind="invalid_input") from exc
        system_info, _ = self._request_json(root, "/api/system-info")
        self._validate_system_info(
            system_info,
            native_size=native_size,
            expected_profile_id=expected_profile_id,
            expected_device_id=expected_device_id,
        )
        # A read before PATCH ensures we fail early against an incomplete or
        # incompatible web API instead of leaving a half-described audit entry.
        # Keep only the fields this transaction changes so a failed read-back
        # can best-effort restore the device's previous URL Rotation state.
        previous_config, _ = self._request_json(root, "/api/config")
        # This is intentionally not inherited from the device.  Re-registering
        # a frame must repair an old always-on setting instead of preserving it.
        # The value is fixed for both supported ESP32 products.
        resolved_deep_sleep = True
        desired = {
            "auto_rotate": True,
            "rotate_cron": list(normalized_cron),
            "rotation_mode": "url",
            "image_url": image_url,
            "display_orientation": str(display_orientation),
            # This is a fixed board-installation correction, not a user-facing
            # orientation choice.  Waveshare PhotoPainter uses 180 degrees;
            # Seeed E1002 uses 0 degrees.
            "display_rotation_deg": hardware_rotation,
            # Battery operation is a product invariant.  Registration always
            # repairs the device to deep sleep, so the physical wake key and
            # firmware timer remain available after a fresh flash.
            "deep_sleep_enabled": resolved_deep_sleep,
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
            display_rotation_deg=hardware_rotation,
            deep_sleep_enabled=resolved_deep_sleep,
        )
