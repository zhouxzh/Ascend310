"""Small persistent registry for ESP32 display devices."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional, Union

from config import DATA_DIR
from display_policy import (
    DEFAULT_PHOTOFRAME_POLICY,
    DEFAULT_EINK_ROTATION_CRON,
    DisplayPolicyError,
    orientation_for_size,
    validate_orientation_mode,
    validate_policy,
)
from photoframe_push import PUSH_PROTOCOLS, PushError, normalize_base_url


class DeviceError(ValueError):
    pass


class DeviceConflictError(DeviceError):
    """A managed device identity is already owned by another record."""

    def __init__(self, message: str, *, device_id: Optional[str] = None):
        super().__init__(message)
        self.device_id = device_id


# The HDMI panel is a physical output owned by the Case7 process rather than
# a network client.  Giving it a stable device identity lets the management
# page present it beside ESP32 displays without making it eligible for active
# push or device-token authentication.
LOCAL_TOUCHSCREEN_ID = "local-touchscreen"

# Supported ESP32 photo-frame products.  These profiles describe the physical
# contract known by the server; arbitrary mounting angles are intentionally not
# part of the network photo-frame API.
PHOTOFRAME_PROFILES = {
    "waveshare_photopainter_73": {
        "display_name": "Waveshare ESP32-S3-PhotoPainter 7.3\"",
        "vendor": "Waveshare",
        "model": "ESP32-S3-PhotoPainter",
        "product_name": "ESP32-S3-PhotoPainter 7.3inch E6 Full Color E-paper Display",
        "panel": "E6",
        "technology": "E6 full-color e-paper",
        "spec_url": "https://www.waveshare.com/product/displays/e-paper/epaper-1/esp32-s3-photopainter.htm",
        "wiki_url": "https://www.waveshare.com/wiki/ESP32-S3-PhotoPainter",
        "width": 800,
        "height": 480,
        "color_mode": "spectra6",
        "color_count": 6,
        "colors": ["black", "white", "green", "blue", "red", "yellow"],
        "partial_refresh": False,
        "orientations": ["landscape", "portrait"],
        "orientation_policy": "landscape_or_portrait",
        "rotation_degrees": [],
        "codecs": ["jpeg"],
    },
    "seeedstudio_reterminal_e1002": {
        "display_name": "Seeed Studio reTerminal E1002",
        "vendor": "Seeed Studio",
        "model": "reTerminal E1002",
        "product_name": "reTerminal E1002 7.3inch Spectra 6 Full-color ePaper",
        "panel": "E Ink Spectra 6",
        "technology": "ACeP / E Ink Spectra 6",
        "spec_url": "https://www.seeedstudio.com/reTerminal-E1002-p-6533.html",
        "wiki_url": "https://wiki.seeedstudio.com/getting_started_with_reterminal_e1002/",
        "width": 800,
        "height": 480,
        "color_mode": "spectra6",
        "color_count": 6,
        "colors": ["black", "white", "green", "blue", "red", "yellow"],
        "partial_refresh": False,
        "orientations": ["landscape"],
        "orientation_policy": "landscape_only",
        "rotation_degrees": [],
        "codecs": ["jpeg"],
    },
}

PROFILE_REQUIRED_MESSAGE = (
    "photoframe device model is not identified; register it as "
    "waveshare_photopainter_73 or seeedstudio_reterminal_e1002"
)


def photo_frame_profile(profile_id: Optional[str]) -> dict:
    """Return a copy of a supported PhotoFrame profile or raise DeviceError."""

    key = str(profile_id or "").strip().lower()
    if not key:
        raise DeviceError(
            "photoframe profile_id is required; choose "
            "waveshare_photopainter_73 or seeedstudio_reterminal_e1002"
        )
    profile = PHOTOFRAME_PROFILES.get(key)
    if profile is None:
        raise DeviceError(
            "unsupported photoframe profile; choose "
            "waveshare_photopainter_73 or seeedstudio_reterminal_e1002"
        )
    return {
        "profile_id": key,
        **profile,
        "orientations": list(profile["orientations"]),
        "rotation_degrees": list(profile["rotation_degrees"]),
        "codecs": list(profile["codecs"]),
        "colors": list(profile["colors"]),
    }


def validate_photo_frame_capability(profile_id: Optional[str], display: dict) -> dict:
    """Normalize and validate an ESP32 PhotoFrame product capability."""

    if not isinstance(display, dict):
        raise DeviceError("photoframe display must be an object")
    raw_codecs = display.get("codecs")
    if raw_codecs not in (None, []):
        if not isinstance(raw_codecs, (list, tuple)) or list(raw_codecs) != ["jpeg"]:
            raise DeviceError("photoframe codecs must be exactly [\"jpeg\"]")
    embedded_profile_id = display.get("profile_id")
    if profile_id and embedded_profile_id:
        requested_profile = str(profile_id).strip().lower()
        embedded_profile = str(embedded_profile_id).strip().lower()
        if requested_profile != embedded_profile:
            raise DeviceError("photoframe profile_id conflicts between device and display")
    profile = photo_frame_profile(profile_id or embedded_profile_id)
    raw_width = display.get("width")
    raw_height = display.get("height")
    requested_orientation = display.get("orientation")
    if requested_orientation in (None, ""):
        if raw_width in (None, "") or raw_height in (None, ""):
            orientation = "landscape"
        else:
            try:
                orientation = "portrait" if int(raw_height) > int(raw_width) else "landscape"
            except (TypeError, ValueError) as exc:
                raise DeviceError("photoframe display dimensions must be integers") from exc
    else:
        orientation = str(requested_orientation).strip().lower()
    if orientation not in profile["orientations"]:
        raise DeviceError(f"{profile['display_name']} only supports: {', '.join(profile['orientations'])}")
    expected = (profile["width"], profile["height"]) if orientation == "landscape" else (profile["height"], profile["width"])
    try:
        width = expected[0] if raw_width in (None, "") else int(raw_width)
        height = expected[1] if raw_height in (None, "") else int(raw_height)
    except (TypeError, ValueError) as exc:
        raise DeviceError("photoframe display dimensions must be integers") from exc
    if (width, height) != expected:
        raise DeviceError(
            f"{profile['display_name']} {orientation} display must be {expected[0]}x{expected[1]}"
        )
    try:
        rotation = int(display.get("rotation", 0))
    except (TypeError, ValueError) as exc:
        raise DeviceError("photoframe rotation must be 0; use landscape or portrait orientation") from exc
    if rotation != 0:
        raise DeviceError("photoframe rotation is not supported; use landscape or portrait orientation")
    # Keep only server-owned capability fields. Hardware metadata such as the
    # panel name and palette comes from PHOTOFRAME_PROFILES, never from an
    # untrusted HTTP payload.
    result = {
        key: display[key]
        for key in ("kind", "max_bytes", "orientation_mode")
        if key in display
    }
    result.update(
        profile_id=profile["profile_id"],
        width=width,
        height=height,
        orientation=orientation,
        rotation=0,
        codecs=["jpeg"],
    )
    return result
LOCAL_TOUCHSCREEN_DEFAULT = {
    "name": "本机触摸屏",
    "device_type": "touchscreen",
    "is_local": True,
    "protocol_version": 1,
    "display": {
        "kind": "touchscreen",
        "width": 1920,
        "height": 1080,
        "max_bytes": 25 * 1024 * 1024,
        "codecs": ["jpeg"],
        "rotation": 0,
        "orientation_mode": "auto",
    },
    "enabled": True,
    "created_at": None,
    "last_seen": None,
    "last_request": None,
    "last_status": "local",
    "last_error": None,
    "last_etag": None,
    "policy_revision": 1,
    "push": {
        "enabled": False,
        "base_url": None,
        "protocol": "photoframe_api",
    },
}


DEFAULT_PUSH_CONFIG = {
    "enabled": False,
    "base_url": None,
    # ``photoframe_api`` is the aitjcize v2 API. ``waveshare_dataup`` is the
    # separate Waveshare demo firmware contract. ``case7_push`` is the
    # endpoint added by the checked-in ESP-IDF patch under ``esp32/``. The
    # operator must select the protocol explicitly; the server never probes or
    # guesses firmware state.
    "protocol": "photoframe_api",
    # The official PhotoFrame direct-display handler performs the e-paper
    # decode/refresh before answering.  Real E1002 requests can therefore take
    # roughly 30 seconds; a 60-second single attempt avoids duplicating a
    # refresh when the first request is still being processed.
    "timeout_seconds": 60,
    "attempts": 1,
    "last_request": None,
    "last_success": None,
    "last_status": "never",
    "last_error": None,
    "last_photo_id": None,
    "last_etag": None,
    "last_slot": None,
    # Unlike last_slot (which records failures and idempotent checks), this is
    # written only after a 2xx response.  It prevents a later render/ETag
    # change in the same cron minute from issuing a second physical refresh.
    "last_success_slot": None,
    "retry_at": None,
}


DEFAULT_PULL_PROVISION = {
    # This audit state describes one-time configuration of the official
    # PhotoFrame URL Rotation feature.  It is deliberately separate from
    # ``last_request`` (ESP32 -> Case7 image fetch evidence) and ``push``
    # (legacy Case7 -> ESP32 direct-display compatibility).
    "status": "unconfigured",
    "device_url": None,
    "last_attempt": None,
    "last_success": None,
    "last_error": None,
    "last_http_status": None,
    "device_hardware_id": None,
    "firmware_version": None,
    "board_name": None,
    "configured_image_url": None,
    "rotate_requested_at": None,
    "rotate_status": "not_requested",
    # Set only after a complete PhotoFrame URL Rotation request reaches the
    # album server.  Configuration verification alone must remain awaiting.
    "first_pull_at": None,
    "last_pull_at": None,
}

_PULL_PROVISION_STATUSES = frozenset({"unconfigured", "configuring", "configured", "awaiting_pull", "pulled", "unreachable", "rejected"})
# A URL Rotation trigger may time out while the firmware is still fetching and
# dithering an e-paper image.  Keep that outcome separate from an explicit
# HTTP/firmware rejection; the subsequent device GET is the connection proof.
_PULL_ROTATE_STATUSES = frozenset({"not_requested", "requested", "timed_out", "failed"})
_UNSET = object()


def _validate_pull_provision(value: Optional[dict], base: Optional[dict] = None) -> dict:
    """Normalize server-owned URL Rotation provisioning evidence."""

    result = dict(DEFAULT_PULL_PROVISION)
    if isinstance(base, dict):
        result.update(base)
    if isinstance(value, dict):
        result.update(value)
    status = str(result.get("status") or "unconfigured")
    if status not in _PULL_PROVISION_STATUSES:
        raise DeviceError("invalid pull provisioning status")
    result["status"] = status
    rotate_status = str(result.get("rotate_status") or "not_requested")
    if rotate_status not in _PULL_ROTATE_STATUSES:
        raise DeviceError("invalid pull provisioning rotation status")
    result["rotate_status"] = rotate_status
    for key in ("device_url", "last_error", "device_hardware_id", "firmware_version", "board_name", "configured_image_url"):
        if result.get(key) is not None:
            result[key] = str(result[key])[:500 if key == "last_error" else 256]
    for key in ("last_attempt", "last_success", "rotate_requested_at", "first_pull_at", "last_pull_at"):
        raw = result.get(key)
        if raw is not None:
            try:
                result[key] = float(raw)
            except (TypeError, ValueError) as exc:
                raise DeviceError(f"pull_provision.{key} must be a timestamp or null") from exc
    raw_status = result.get("last_http_status")
    if raw_status is not None:
        try:
            result["last_http_status"] = int(raw_status)
        except (TypeError, ValueError) as exc:
            raise DeviceError("pull_provision.last_http_status must be an HTTP status or null") from exc
        if not 100 <= result["last_http_status"] <= 599:
            raise DeviceError("pull_provision.last_http_status must be an HTTP status or null")
    return result


def _validate_push(value: Optional[dict], base: Optional[dict] = None) -> dict:
    """Validate persisted active-push settings without probing the device."""

    result = dict(DEFAULT_PUSH_CONFIG)
    if isinstance(base, dict):
        result.update(base)
    if isinstance(value, dict):
        result.update(value)
    if not isinstance(result.get("enabled"), bool):
        raise DeviceError("push.enabled must be boolean")
    base_url = result.get("base_url")
    if base_url in {None, ""}:
        result["base_url"] = None
    else:
        try:
            result["base_url"] = normalize_base_url(str(base_url))
        except PushError as exc:
            raise DeviceError(str(exc)) from exc
    if result.get("enabled") and not result.get("base_url"):
        raise DeviceError("push.base_url is required when active push is enabled")
    if result.get("protocol") not in PUSH_PROTOCOLS:
        raise DeviceError("push.protocol must be photoframe_api, waveshare_dataup, or case7_push")
    try:
        timeout = int(result.get("timeout_seconds", 60))
        attempts = int(result.get("attempts", 1))
    except (TypeError, ValueError) as exc:
        raise DeviceError("push timeout and attempts must be integers") from exc
    if not 1 <= timeout <= 60:
        raise DeviceError("push.timeout_seconds must be between 1 and 60")
    if not 1 <= attempts <= 3:
        raise DeviceError("push.attempts must be between 1 and 3")
    result["timeout_seconds"] = timeout
    result["attempts"] = attempts
    # Status fields are server-owned.  Keep old registries readable, but do
    # not allow an API caller to inject arbitrary values into the audit state.
    for key in ("last_request", "last_success", "retry_at"):
        raw = result.get(key)
        if raw is not None:
            try:
                result[key] = float(raw)
            except (TypeError, ValueError) as exc:
                raise DeviceError(f"push.{key} must be a timestamp or null") from exc
    if result.get("last_photo_id") is not None:
        try:
            result["last_photo_id"] = int(result["last_photo_id"])
        except (TypeError, ValueError) as exc:
            raise DeviceError("push.last_photo_id must be an integer or null") from exc
    for key in ("last_status", "last_error", "last_etag", "last_slot", "last_success_slot"):
        if result.get(key) is not None:
            result[key] = str(result[key])[:500 if key == "last_error" else 200]
    return result


class DeviceRegistry:
    def __init__(self, path: Optional[Union[Path, str]] = None):
        self.path = Path(path or (Path(DATA_DIR) / "devices.json"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._data = self._load()

    def _load(self):
        if not self.path.is_file():
            return {"schema_version": 4, "devices": {}, "local_touchscreen": None}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(value.get("devices"), dict):
                raise ValueError
            value["schema_version"] = 4
            for item in value["devices"].values():
                self._migrate_item(item)
            # Local touchscreen settings are persisted separately from remote
            # protocol registrations.  Older registries simply get the
            # built-in default lazily on first access.
            local = value.get("local_touchscreen")
            if local is not None and not isinstance(local, dict):
                raise ValueError
            return value
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise DeviceError("invalid device registry") from exc

    @staticmethod
    def _migrate_item(item: dict):
        display = item.setdefault("display", {})
        # Direction fields were introduced after the first device registry
        # schema. Retain old devices with the conservative rendering default.
        display.setdefault("rotation", 0)
        display.setdefault("orientation_mode", "auto")
        if item.get("display", {}).get("kind") == "photoframe":
            # Only normalize a record when its product was explicitly
            # identified.  A legacy PhotoFrame without a profile is not
            # evidence of a Waveshare or Seeed product, so preserve its
            # original dimensions and leave it as an unprofiled device.
            explicit_profile = item.get("profile_id") or display.get("profile_id")
            if explicit_profile:
                try:
                    item["display"] = validate_photo_frame_capability(explicit_profile, display)
                    item["profile_id"] = item["display"]["profile_id"]
                    item.pop("profile_required", None)
                    item.pop("profile_error", None)
                except DeviceError:
                    # Keep malformed historical records readable so the
                    # service can report them; new managed registrations are
                    # strict and must use a supported profile.
                    display.pop("profile_id", None)
                    item.pop("profile_id", None)
                    item["profile_required"] = True
                    item["profile_error"] = "invalid or unsupported photoframe profile"
            else:
                item.pop("profile_id", None)
                item["profile_required"] = True
                item["profile_error"] = PROFILE_REQUIRED_MESSAGE
            raw_policy = item.get("policy")
            # Registries created before the separate touchscreen/e-paper
            # clocks used the hourly ``0 8-22 *`` default.  Migrate that
            # untouched default to the safe half-hour e-paper cadence, while
            # preserving any operator-selected cron rule verbatim.
            if isinstance(raw_policy, dict) and raw_policy.get("rotation_cron") == ["0 8-22 *"]:
                raw_policy = dict(raw_policy)
                raw_policy["rotation_cron"] = list(DEFAULT_EINK_ROTATION_CRON)
            item["policy"] = validate_policy(raw_policy, raw_policy)
            raw_push = item.get("push")
            # Older experimental registries could mark push enabled before a
            # device URL was known.  Keep the service bootable, disable that
            # incomplete entry, and leave an explicit audit note for the
            # operator instead of inventing a destination.
            if isinstance(raw_push, dict) and raw_push.get("enabled") and not raw_push.get("base_url"):
                raw_push = dict(raw_push)
                raw_push["enabled"] = False
                raw_push["last_status"] = "disabled"
                raw_push["last_error"] = "active push disabled during migration: base_url is missing"
            migrated_push = _validate_push(raw_push)
            # Registries written by the first active-push implementation used
            # 8s/2 attempts as an inactive default.  Upgrade only inactive
            # legacy entries; an operator's explicit settings on an enabled
            # device remain untouched.
            if (
                not migrated_push.get("enabled")
                and migrated_push.get("timeout_seconds") == 8
                and migrated_push.get("attempts") == 2
            ):
                migrated_push["timeout_seconds"] = 60
                migrated_push["attempts"] = 1
            # Preserve the idempotence barrier for a registry written before
            # ``last_success_slot`` existed.  A persisted ``ok`` plus
            # ``last_slot`` is sufficient evidence that that slot completed;
            # do not resend it merely because the service was upgraded.
            if (
                migrated_push.get("last_success_slot") is None
                and migrated_push.get("last_status") in {"ok", "success"}
                and migrated_push.get("last_slot")
            ):
                migrated_push["last_success_slot"] = migrated_push["last_slot"]
            item["push"] = _validate_push(migrated_push)
            item["pull_provision"] = _validate_pull_provision(item.get("pull_provision"))
        else:
            item.pop("profile_required", None)
            item.pop("profile_error", None)
            item.setdefault("push", _validate_push(None))
        item.setdefault("last_request", None)
        item.setdefault("last_request_client", None)
        item.setdefault("last_request_firmware", None)
        item.setdefault("last_request_display", None)
        item.setdefault("last_status", "unknown")
        item.setdefault("last_error", None)
        item.setdefault("last_etag", None)
        item.setdefault("policy_revision", int((item.get("policy") or {}).get("policy_revision", 1)))

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    @staticmethod
    def _public(device_id: str, item: dict, token=None, *, include_token: bool = False):
        result = {key: item[key] for key in item if key != "token_hash"}
        result["device_id"] = device_id
        # Managed LAN devices are URL-only.  A token is included only for the
        # low-level handshake caller that explicitly opts into the historical
        # registration contract; lists, updates and status responses never
        # expose a misleading ``token: null`` field.
        if include_token:
            result["token"] = token
        return result

    def _save(self):
        fd, temp_name = tempfile.mkstemp(prefix="devices.", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._data, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            Path(temp_name).replace(self.path)
        finally:
            Path(temp_name).unlink(missing_ok=True)

    @staticmethod
    def _local_default():
        value = json.loads(json.dumps(LOCAL_TOUCHSCREEN_DEFAULT, ensure_ascii=False))
        value["created_at"] = time.time()
        return value

    def _local_item(self, create: bool = False):
        """Return the persisted local touchscreen metadata.

        ``create=False`` is intentionally side-effect free so read-only health
        checks do not create a registry file.  Management calls use
        ``create=True`` and persist the stable record.
        """

        local = self._data.get("local_touchscreen")
        if local is None and create:
            local = self._local_default()
            self._data["local_touchscreen"] = local
            self._save()
        return local

    def local_touchscreen(self, create: bool = True):
        with self._lock:
            item = self._local_item(create=create)
            if item is None:
                item = self._local_default()
            # Merge newly introduced display metadata into old persisted
            # records without discarding operator-owned name/enabled fields.
            merged = self._local_default()
            merged.update(item)
            merged["display"] = dict(LOCAL_TOUCHSCREEN_DEFAULT["display"], **(item.get("display") or {}))
            merged["push"] = dict(LOCAL_TOUCHSCREEN_DEFAULT["push"], **(item.get("push") or {}))
            if create and merged != self._data.get("local_touchscreen"):
                self._data["local_touchscreen"] = merged
                self._save()
            return self._public(LOCAL_TOUCHSCREEN_ID, merged)

    def update_local_touchscreen(self, patch: dict):
        """Persist metadata for the built-in touchscreen device.

        Display behavior is kept in ``server_config.json`` and is updated by
        the API layer.  This method only accepts identity/capability fields so
        a generic remote-device patch cannot smuggle network push settings into
        the local output.
        """

        if not isinstance(patch, dict):
            raise DeviceError("touchscreen patch must be an object")
        allowed = {"name", "enabled", "display"}
        unknown = set(patch) - allowed
        if unknown:
            raise DeviceError(f"unsupported touchscreen keys: {', '.join(sorted(unknown))}")
        with self._lock:
            raw_current = self._local_item(create=True)
            current = self._local_default()
            current.update(raw_current or {})
            current["display"] = dict(LOCAL_TOUCHSCREEN_DEFAULT["display"])
            current["display"].update((raw_current or {}).get("display") or {})
            current["push"] = dict(LOCAL_TOUCHSCREEN_DEFAULT["push"])
            current["push"].update((raw_current or {}).get("push") or {})
            if "name" in patch:
                current["name"] = str(patch["name"])[:120]
            if "enabled" in patch:
                if not isinstance(patch["enabled"], bool):
                    raise DeviceError("touchscreen.enabled must be boolean")
                current["enabled"] = patch["enabled"]
            if "display" in patch:
                display = patch["display"]
                if not isinstance(display, dict):
                    raise DeviceError("touchscreen.display must be an object")
                allowed_display = {"width", "height", "rotation", "orientation_mode"}
                unknown_display = set(display) - allowed_display
                if unknown_display:
                    raise DeviceError(
                        "unsupported touchscreen display keys: "
                        + ", ".join(sorted(unknown_display))
                    )
                merged_display = dict(current.get("display") or {})
                merged_display.update(display)
                try:
                    width = int(merged_display.get("width", 1920))
                    height = int(merged_display.get("height", 1080))
                    rotation = int(merged_display.get("rotation", 0))
                except (TypeError, ValueError) as exc:
                    raise DeviceError("touchscreen display dimensions and rotation must be integers") from exc
                if not 1 <= width <= 4096 or not 1 <= height <= 4096:
                    raise DeviceError("touchscreen display dimensions are out of range")
                if rotation not in {0, 90, 180, 270}:
                    raise DeviceError("touchscreen display.rotation must be 0, 90, 180, or 270")
                try:
                    orientation_mode = validate_orientation_mode(merged_display.get("orientation_mode", "auto"))
                except DisplayPolicyError as exc:
                    raise DeviceError(str(exc)) from exc
                current["display"] = dict(
                    merged_display,
                    kind="touchscreen",
                    width=width,
                    height=height,
                    codecs=["jpeg"],
                    max_bytes=25 * 1024 * 1024,
                    rotation=rotation,
                    orientation_mode=orientation_mode,
                )
            current["last_seen"] = time.time()
            self._data["local_touchscreen"] = current
            self._save()
            return self._public(LOCAL_TOUCHSCREEN_ID, current)

    def list(self, include_local: bool = False):
        with self._lock:
            values = [self._public(device_id, item) for device_id, item in self._data["devices"].items()]
            if include_local:
                local = self._local_item(create=True)
                values.insert(0, self._public(LOCAL_TOUCHSCREEN_ID, local))
            return values

    def find_by_pull_url(self, device_url: str, *, exclude_device_id: Optional[str] = None) -> Optional[str]:
        """Find the record that owns one normalized PhotoFrame root URL.

        A PhotoFrame stores exactly one Case7 URL.  Allowing two records to
        claim that same address would make the device's next pull URL
        ambiguous and leave one server record permanently waiting.  The
        comparison tolerates a legacy trailing slash but otherwise remains
        literal; callers normalize the URL before invoking this method.
        """

        wanted = str(device_url or "").strip().rstrip("/")
        if not wanted:
            return None
        excluded = str(exclude_device_id) if exclude_device_id is not None else None
        with self._lock:
            for device_id, item in self._data["devices"].items():
                if excluded is not None and str(device_id) == excluded:
                    continue
                if (item.get("display") or {}).get("kind") != "photoframe":
                    continue
                pull = item.get("pull_provision") or {}
                current = str(pull.get("device_url") or "").strip().rstrip("/")
                if current and current == wanted:
                    return str(device_id)
        return None

    def handshake(self, payload: dict, require_token: bool = True):
        if not isinstance(payload, dict):
            raise DeviceError("device handshake must be a JSON object")
        display = payload.get("display") or {}
        if not isinstance(display, dict):
            raise DeviceError("display must be a JSON object")
        kind = display.get("kind")
        if int(payload.get("protocol_version", 1)) != 1:
            raise DeviceError("unsupported protocol version")
        if kind not in {"lcd", "epaper", "photoframe"}:
            raise DeviceError("display.kind must be lcd, epaper, or photoframe")
        # There are exactly two supported network photo-frame products.  This
        # lower-level endpoint follows the same contract as the management
        # page, so a client cannot create an arbitrary third profile by
        # skipping the page and posting a raw handshake.
        if kind == "photoframe":
            profile_id = payload.get("profile_id") or display.get("profile_id")
            if not profile_id:
                raise DeviceError(
                    "photoframe profile_id is required; choose "
                    "waveshare_photopainter_73 or seeedstudio_reterminal_e1002"
                )
            display = validate_photo_frame_capability(profile_id, display)
        # Profile normalization supplies the fixed codec contract.  Compute
        # this after normalization so product records may omit redundant
        # capability fields while LCD/E6 integrations retain their explicit
        # advertisement requirement.
        codecs = set(display.get("codecs") or [])
        try:
            width = int(display.get("width", 0))
            height = int(display.get("height", 0))
            default_max_bytes = 192000 if kind == "epaper" else 2 * 1024 * 1024 if kind == "photoframe" else 0
            max_bytes = int(display.get("max_bytes", default_max_bytes))
            rotation = int(display.get("rotation", 0))
        except (TypeError, ValueError) as exc:
            raise DeviceError("display capability values must be integers") from exc
        if not 1 <= width <= 4096 or not 1 <= height <= 4096:
            raise DeviceError("display dimensions are out of range")
        if not 4096 <= max_bytes <= 25 * 1024 * 1024:
            raise DeviceError("display.max_bytes is out of range")
        if kind == "photoframe" and rotation != 0:
            raise DeviceError("photoframe rotation is not supported; use landscape or portrait orientation")
        if rotation not in {0, 90, 180, 270}:
            raise DeviceError("display.rotation must be 0, 90, 180, or 270")
        try:
            orientation_mode = validate_orientation_mode(display.get("orientation_mode", "auto"))
        except DisplayPolicyError as exc:
            raise DeviceError(str(exc)) from exc
        orientation = display.get("orientation")
        if orientation not in (None, "landscape", "portrait"):
            raise DeviceError("display.orientation must be landscape or portrait")
        if orientation is not None:
            try:
                actual_orientation = orientation_for_size((width, height))
            except DisplayPolicyError as exc:
                raise DeviceError(str(exc)) from exc
            if actual_orientation != orientation:
                raise DeviceError("display.orientation must match display.width and display.height")
        if kind in {"lcd", "photoframe"} and "jpeg" not in codecs:
            raise DeviceError(f"{kind} must advertise jpeg")
        if kind == "epaper" and ("e6" not in codecs or width != 800 or height != 480 or rotation != 0):
            raise DeviceError("E6 device must advertise 800x480 e6")
        display = dict(
            display,
            width=width,
            height=height,
            max_bytes=max_bytes,
            rotation=rotation,
            orientation_mode=orientation_mode,
        )
        device_id = str(payload.get("device_id") or secrets.token_hex(8))
        with self._lock:
            old = self._data["devices"].get(device_id)
            token = secrets.token_urlsafe(32) if old is None else None
            if old is not None and require_token:
                existing_token = str(payload.get("token") or "")
                if not existing_token or not secrets.compare_digest(self._hash(existing_token), old["token_hash"]):
                    raise DeviceError("existing device handshake requires its device token")
            item = {
                "name": str(payload.get("name") or device_id),
                "profile_id": display.get("profile_id") if kind == "photoframe" and display.get("profile_id") else None,
                "protocol_version": int(payload.get("protocol_version", 1)),
                "display": display,
                "token_hash": self._hash(token) if token else old["token_hash"],
                "enabled": bool(old.get("enabled", True)) if old else True,
                "created_at": old.get("created_at", time.time()) if old else time.time(),
                "last_seen": time.time(),
                "policy": validate_policy(old.get("policy") if old else None) if kind == "photoframe" else (old.get("policy") if old else {}),
                "policy_revision": int((old.get("policy") if old else {}).get("policy_revision", 1)) if kind == "photoframe" else 1,
                "last_request": old.get("last_request") if old else None,
                "last_status": old.get("last_status", "unknown") if old else "unknown",
                "last_error": old.get("last_error") if old else None,
                "last_etag": old.get("last_etag") if old else None,
                "last_request_client": old.get("last_request_client") if old else None,
                "last_request_firmware": old.get("last_request_firmware") if old else None,
                "last_request_display": old.get("last_request_display") if old else None,
                "push": _validate_push(old.get("push") if old else None),
            }
            if kind == "photoframe":
                item["pull_provision"] = _validate_pull_provision(old.get("pull_provision") if old else None)
            # A migrated legacy PhotoFrame remains unclassified until an
            # operator explicitly supplies one of the supported product IDs.
            # A tokenless generic re-handshake must not silently clear that
            # safety marker.
            if old and old.get("profile_required") and not item.get("profile_id"):
                item["profile_required"] = True
                item["profile_error"] = old.get("profile_error") or PROFILE_REQUIRED_MESSAGE
            self._data["devices"][device_id] = item
            self._save()
            return self._public(device_id, item, token=token, include_token=True)

    def get(self, device_id: str):
        with self._lock:
            if str(device_id) == LOCAL_TOUCHSCREEN_ID:
                local = self._local_item(create=True)
                return self._public(LOCAL_TOUCHSCREEN_ID, local)
            item = self._data["devices"].get(device_id)
            if item is None:
                raise DeviceError("unknown device")
            return self._public(device_id, item)

    def authorize(self, device_id: str, token: Optional[str]):
        with self._lock:
            item = self._data["devices"].get(device_id)
            if item is None or not item.get("enabled"):
                raise DeviceError("device is disabled or unknown")
            if not token or not secrets.compare_digest(self._hash(token), item["token_hash"]):
                raise DeviceError("invalid device token")
            item["last_seen"] = time.time()
            self._save()
            return self._public(device_id, item)

    def update(self, device_id: str, patch: dict):
        if str(device_id) == LOCAL_TOUCHSCREEN_ID:
            return self.update_local_touchscreen(patch)
        with self._lock:
            item = self._data["devices"].get(device_id)
            if item is None:
                raise DeviceError("unknown device")
            if not isinstance(patch, dict):
                raise DeviceError("device patch must be an object")
            if "pull_provision" in patch:
                raise DeviceError("pull provisioning audit fields are server-owned")
            if "profile_id" in patch:
                if item.get("display", {}).get("kind") != "photoframe":
                    raise DeviceError("only photoframe devices have a product profile")
                requested_profile = patch.get("profile_id")
                if not requested_profile:
                    raise DeviceError(PROFILE_REQUIRED_MESSAGE)
                display_patch = patch.get("display") or {}
                if not isinstance(display_patch, dict):
                    raise DeviceError("display must be an object")
                allowed_display = {"orientation", "width", "height", "orientation_mode"}
                unknown_display = set(display_patch) - allowed_display
                if unknown_display:
                    raise DeviceError(f"unsupported photoframe display keys: {', '.join(sorted(unknown_display))}")
                try:
                    profile = photo_frame_profile(requested_profile)
                    current_display = dict(item.get("display") or {})
                    # A legacy record can have arbitrary dimensions.  When
                    # the operator explicitly identifies its hardware but
                    # does not provide a new orientation, reset the display
                    # to the selected profile's documented landscape
                    # contract instead of trying to validate stale geometry.
                    if not any(key in display_patch for key in ("orientation", "width", "height")):
                        current_display.update(
                            orientation="landscape",
                            width=profile["width"],
                            height=profile["height"],
                        )
                    current_display.update(display_patch)
                    current_display["profile_id"] = profile["profile_id"]
                    item["display"] = validate_photo_frame_capability(profile["profile_id"], current_display)
                except DeviceError:
                    raise
                item["profile_id"] = profile["profile_id"]
                item.pop("profile_required", None)
                item.pop("profile_error", None)
            if "name" in patch:
                item["name"] = str(patch["name"])[:120]
            if "enabled" in patch:
                item["enabled"] = bool(patch["enabled"])
            if "display" in patch:
                if item.get("display", {}).get("kind") != "photoframe":
                    raise DeviceError("only photoframe display capabilities can be changed")
                display_patch = patch["display"]
                if not isinstance(display_patch, dict):
                    raise DeviceError("display must be an object")
                profile_id = item.get("profile_id") or item.get("display", {}).get("profile_id")
                if not profile_id:
                    raise DeviceError(PROFILE_REQUIRED_MESSAGE)
                photo_frame_profile(profile_id)
                allowed_display = {"orientation", "width", "height", "orientation_mode"}
                unknown_display = set(display_patch) - allowed_display
                if unknown_display:
                    raise DeviceError(f"unsupported photoframe display keys: {', '.join(sorted(unknown_display))}")
                try:
                    item["display"] = validate_photo_frame_capability(
                        item.get("profile_id") or item["display"].get("profile_id"),
                        {**item["display"], **display_patch},
                    )
                except DeviceError:
                    raise
            if "policy" in patch:
                if item.get("display", {}).get("kind") != "photoframe":
                    raise DeviceError("only photoframe devices have a display policy")
                profile_id = item.get("profile_id") or item.get("display", {}).get("profile_id")
                if not profile_id:
                    raise DeviceError(PROFILE_REQUIRED_MESSAGE)
                photo_frame_profile(profile_id)
                try:
                    policy = validate_policy(patch["policy"], item.get("policy"))
                except DisplayPolicyError as exc:
                    raise DeviceError(str(exc)) from exc
                if int(policy.get("rotation", 0)) != 0:
                    raise DeviceError("photoframe rotation is not supported; use landscape or portrait orientation")
                policy["policy_revision"] = int(item.get("policy_revision", policy.get("policy_revision", 1))) + 1
                item["policy"] = policy
                item["policy_revision"] = policy["policy_revision"]
            if "push" in patch:
                if item.get("display", {}).get("kind") != "photoframe":
                    raise DeviceError("only photoframe devices support active push")
                profile_id = item.get("profile_id") or item.get("display", {}).get("profile_id")
                if not profile_id:
                    raise DeviceError(PROFILE_REQUIRED_MESSAGE)
                photo_frame_profile(profile_id)
                raw_push = patch["push"]
                if not isinstance(raw_push, dict):
                    raise DeviceError("push must be an object")
                # API callers may update configuration keys only; audit fields
                # remain controlled by mark_push().
                allowed = {"enabled", "base_url", "protocol", "timeout_seconds", "attempts"}
                unknown = set(raw_push) - allowed
                if unknown:
                    raise DeviceError(f"unsupported push keys: {', '.join(sorted(unknown))}")
                previous = _validate_push(item.get("push"))
                # A newly enabled outbound connection must name the receiver
                # contract.  Keeping the persisted protocol for edits to an
                # already enabled device is safe; silently choosing one while
                # enabling a device would make a firmware mismatch invisible.
                require_protocol = bool(raw_push.get("enabled") is True and not previous.get("enabled"))
                if require_protocol and "protocol" not in raw_push:
                    raise DeviceError(
                        "push.protocol is required when enabling active push; "
                        "choose photoframe_api, waveshare_dataup, or case7_push"
                    )
                updated = _validate_push(raw_push, previous)
                if any(updated.get(key) != previous.get(key) for key in ("enabled", "base_url", "protocol", "timeout_seconds", "attempts")):
                    updated.update(
                        last_request=None,
                        last_success=None,
                        last_status="never" if updated["enabled"] else "disabled",
                        last_error=None,
                        last_photo_id=None,
                        last_etag=None,
                        last_slot=None,
                        last_success_slot=None,
                        retry_at=None,
                    )
                item["push"] = _validate_push(updated)
            self._save()
            return self._public(device_id, item)

    def mark_request(
        self,
        device_id: str,
        status: str,
        error: Optional[str] = None,
        etag: Optional[str] = None,
        *,
        client: Optional[str] = None,
        firmware_version: Optional[str] = None,
        display: Optional[dict] = None,
    ):
        with self._lock:
            item = self._data["devices"].get(device_id)
            if item is None:
                raise DeviceError("unknown device")
            now = time.time()
            item["last_request"] = now
            item["last_status"] = str(status)
            item["last_error"] = str(error)[:500] if error else None
            if etag is not None:
                item["last_etag"] = str(etag)[:200]
            if client is not None:
                item["last_request_client"] = str(client)[:200]
            if firmware_version is not None:
                item["last_request_firmware"] = str(firmware_version)[:200]
            if display is not None:
                if not isinstance(display, dict):
                    raise DeviceError("request display evidence must be an object")
                item["last_request_display"] = {
                    key: display[key]
                    for key in ("width", "height", "orientation")
                    if key in display and display[key] is not None
                }
            # A registry entry is not considered connected merely because a
            # browser (or a provisioning client) reached the server.  The
            # PhotoFrame endpoint passes complete firmware/display evidence
            # only for its URL Rotation request; persist that evidence as the
            # transition from control-plane ``awaiting_pull`` to data-plane
            # ``pulled``.  Keep this update atomic with the request audit so a
            # restart cannot lose the connection state.
            pull = item.get("pull_provision")
            evidence_complete = (
                item.get("display", {}).get("kind") == "photoframe"
                and str(status) in {"ok", "not_modified"}
                and bool(str(firmware_version or "").strip())
                and isinstance(display, dict)
                and all(display.get(key) not in (None, "") for key in ("width", "height", "orientation"))
                and isinstance(pull, dict)
                and bool(str(pull.get("device_url") or "").strip())
            )
            if evidence_complete:
                normalized_pull = _validate_pull_provision(pull)
                if normalized_pull.get("status") != "pulled":
                    normalized_pull["first_pull_at"] = normalized_pull.get("first_pull_at") or now
                normalized_pull["last_pull_at"] = now
                normalized_pull["status"] = "pulled"
                normalized_pull["last_error"] = None
                item["pull_provision"] = _validate_pull_provision(normalized_pull)
            item["last_seen"] = time.time()
            self._save()
            return self._public(device_id, item)

    def mark_representation_etag(self, device_id: str, etag: str):
        """Remember the latest served representation without claiming a pull.

        Browsers and diagnostics may legitimately populate an ETag cache, but
        they must not update ``last_request`` or the PhotoFrame capability
        evidence.  Keeping this tiny state update separate prevents the UI
        from confusing a rendered response with an ESP32 fetch.
        """

        with self._lock:
            item = self._data["devices"].get(device_id)
            if item is None:
                raise DeviceError("unknown device")
            normalized = str(etag)[:200]
            if item.get("last_etag") != normalized:
                item["last_etag"] = normalized
                self._save()
            return self._public(device_id, item)

    def mark_pull_provision(
        self,
        device_id: str,
        status: str,
        *,
        device_url=_UNSET,
        last_error=_UNSET,
        last_http_status=_UNSET,
        device_hardware_id=_UNSET,
        firmware_version=_UNSET,
        board_name=_UNSET,
        configured_image_url=_UNSET,
        rotate_requested_at=_UNSET,
        rotate_status=_UNSET,
        successful: bool = False,
    ):
        """Persist one URL Rotation setup attempt without touching fetch state."""

        with self._lock:
            item = self._data["devices"].get(device_id)
            if item is None:
                raise DeviceError("unknown device")
            if item.get("display", {}).get("kind") != "photoframe":
                raise DeviceError("only photoframe devices support pull provisioning")
            # A device can fetch the image while the provisioning request is
            # still waiting for `/api/rotate` to return.  Preserve that
            # data-plane evidence when the control-plane request records its
            # final result; otherwise the later bookkeeping would regress a
            # genuinely connected device back to `awaiting_pull`.
            current = _validate_pull_provision(item.get("pull_provision"))
            requested_status = str(status)
            effective_status = requested_status
            if (
                requested_status in {"configured", "awaiting_pull"}
                and current.get("status") == "pulled"
                and current.get("last_pull_at") is not None
                and current.get("last_attempt") is not None
                and float(current["last_pull_at"]) >= float(current["last_attempt"])
            ):
                effective_status = "pulled"
            update = {"status": effective_status, "last_attempt": time.time()}
            values = {
                "device_url": device_url,
                "last_error": last_error,
                "last_http_status": last_http_status,
                "device_hardware_id": device_hardware_id,
                "firmware_version": firmware_version,
                "board_name": board_name,
                "configured_image_url": configured_image_url,
                "rotate_requested_at": rotate_requested_at,
                "rotate_status": rotate_status,
            }
            for key, value in values.items():
                if value is not _UNSET:
                    update[key] = value
            if successful:
                update["last_success"] = time.time()
            item["pull_provision"] = _validate_pull_provision(update, item.get("pull_provision"))
            self._save()
            return self._public(device_id, item)

    def mark_push(
        self,
        device_id: str,
        status: str,
        *,
        error: Optional[str] = None,
        photo_id: Optional[int] = None,
        etag: Optional[str] = None,
        slot: Optional[str] = None,
        retry_at: Optional[float] = None,
    ):
        """Persist one active-push attempt/result for operational auditing."""

        with self._lock:
            item = self._data["devices"].get(device_id)
            if item is None:
                raise DeviceError("unknown device")
            push = _validate_push(item.get("push"))
            now = time.time()
            push["last_request"] = now
            push["last_status"] = str(status)[:200]
            push["last_error"] = str(error)[:500] if error else None
            if status in {"ok", "success"}:
                push["last_success"] = now
                if slot is not None:
                    push["last_success_slot"] = str(slot)[:200]
            if photo_id is not None:
                push["last_photo_id"] = int(photo_id)
            if etag is not None:
                push["last_etag"] = str(etag)[:200]
            if slot is not None:
                push["last_slot"] = str(slot)[:200]
            push["retry_at"] = float(retry_at) if retry_at is not None else None
            item["push"] = _validate_push(push)
            self._save()
            return self._public(device_id, item)

    def revoke(self, device_id: str):
        with self._lock:
            if str(device_id) == LOCAL_TOUCHSCREEN_ID:
                raise DeviceError("the local touchscreen cannot be deleted; disable it instead")
            item = self._data["devices"].get(device_id)
            if item is None:
                raise DeviceError("unknown device")
            item["enabled"] = False
            self._save()
            return self._public(device_id, item)

    def delete(self, device_id: str):
        """Permanently remove one remote registration from the registry.

        This is deliberately separate from :meth:`revoke`: revocation is a
        reversible device disable operation used by the protocol endpoint,
        while deletion removes only the registration metadata.  Photo files,
        photo metadata, embeddings, and the rest of the album are untouched.
        Callers must provide their own explicit confirmation at the API/UI
        boundary before invoking this method.
        """

        device_id = str(device_id)
        with self._lock:
            if device_id == LOCAL_TOUCHSCREEN_ID:
                raise DeviceError("the local touchscreen cannot be deleted; disable it instead")
            item = self._data["devices"].pop(device_id, None)
            if item is None:
                raise DeviceError("unknown device")
            self._save()
            result = self._public(device_id, item)
            result["deleted"] = True
            return result

    # Keep a descriptive alias for integrations that use ``remove`` rather
    # than ``delete``; both paths have exactly the same semantics.
    remove = delete
