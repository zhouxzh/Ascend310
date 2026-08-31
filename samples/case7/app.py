#!/usr/bin/env python3
"""FastAPI smart-album server for Ascend 310B and ESP32 displays."""

import argparse
import hashlib
import ipaddress
import importlib.util
import io
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from dataclasses import fields, replace
from datetime import timedelta
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlencode, urlsplit, urlunsplit

from PIL import Image
from pydantic import BaseModel

from config import DATA_DIR, EPAPER_HEIGHT, EPAPER_WIDTH, PHOTO_DIR, TOP_K_RESULTS, UPLOAD_TMP_DIR
from device_registry import (
    LOCAL_TOUCHSCREEN_ID,
    PROFILE_REQUIRED_MESSAGE,
    PHOTOFRAME_PROFILES,
    DeviceConflictError,
    DeviceError,
    DeviceRegistry,
    photo_frame_profile,
    validate_photo_frame_capability,
)
from display_policy import (
    DEFAULT_PHOTOFRAME_POLICY,
    DisplayPolicyError,
    PhotoRenderer,
    effective_cron_slot,
    hint_jpeg_decode,
    normalize_display_orientation,
    orient_image,
    orientation_for_size,
    validate_orientation_mode,
    validate_policy,
)
from embedding_backend import CHINESE_CLIP_ID, MOBILECLIP_ID, RESNET50_ID, EmbeddingError, ModelManager, resolve_text_model
from epaper_display import EpaperConfig, EpaperDisplay, prepare_frame
from model_registry import ModelRegistry, RegistryError, load_candidates
from photoframe_provisioning import (
    PRIVATE_V4_NETWORKS,
    PhotoFrameProvisioner,
    ProvisionError,
    ProvisionResult,
    normalize_device_url,
)
from photoframe_push import PUSH_PROTOCOLS, PhotoFramePushClient, PushError
from photo_index import AlbumIndex, AlbumIndexError, _sha256
from server_config import ConfigError, ConfigStore
from smart_selector import SmartSelector

REQUIRED_MODEL_IDS = (MOBILECLIP_ID, CHINESE_CLIP_ID, RESNET50_ID)
MODEL_LABELS = {"auto": "自动", MOBILECLIP_ID: "MobileCLIP-S0", CHINESE_CLIP_ID: "Chinese-CLIP RN50", RESNET50_ID: "ResNet50 经典相似图"}
_PHOTOFRAME_PROVISION_LOCK = threading.Lock()


def _normalize_registered_photoframe_capability(device: dict, capability: dict) -> dict:
    """Apply a registered product profile to a negotiated capability.

    The network PhotoFrame contract deliberately has only two orientations.
    A historical record without a confirmed product remains visible to the
    operator but is blocked from producing content until it is identified.
    """

    if (device.get("display") or {}).get("kind") != "photoframe":
        return capability
    try:
        registered = _registered_photoframe_display(device)
    except DeviceError as exc:
        raise DisplayPolicyError(str(exc)) from exc
    profile_id = registered["profile_id"]
    capability = dict(capability or {})
    capability["profile_id"] = profile_id
    try:
        return validate_photo_frame_capability(profile_id, capability)
    except DeviceError as exc:
        raise DisplayPolicyError(str(exc)) from exc


def _enforce_registered_photoframe_profile(device: dict, capability: dict) -> dict:
    """Apply fixed product dimensions/orientation to negotiated capabilities."""

    try:
        return _normalize_registered_photoframe_capability(device, capability)
    except DisplayPolicyError as exc:
        # This helper is used at the FastAPI boundary, where a malformed
        # display negotiation should be reported as a client error.
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=str(exc)) from exc


def _registered_photoframe_display(device: dict) -> dict:
    """Return the canonical product capability before any device state changes."""

    display = device.get("display") or {}
    if not isinstance(display, dict) or display.get("kind") != "photoframe":
        raise DeviceError("device is not a photoframe")
    profile_id = device.get("profile_id") or display.get("profile_id")
    if not profile_id:
        raise DeviceError(PROFILE_REQUIRED_MESSAGE)
    return validate_photo_frame_capability(profile_id, display)


def _photoframe_fetch_evidence(
    request,
    *,
    device: Optional[dict] = None,
    firmware_version: Optional[str],
    width: Optional[str],
    height: Optional[str],
    orientation: Optional[str],
) -> Optional[dict]:
    """Return evidence for an actual PhotoFrame URL Rotation GET.

    The image endpoint is public on the trusted LAN, so browser previews and
    curl probes are valid for diagnostics but are not proof that an ESP32
    received an image.  The upstream PhotoFrame URL Rotation contract sends
    the firmware and display negotiation headers together; only that complete
    contract updates the device's fetch audit fields.
    """

    version = str(firmware_version or "").strip()
    raw_orientation = str(orientation or "").strip().lower()
    if not version or width in (None, "") or height in (None, "") or raw_orientation not in {"landscape", "portrait"}:
        return None
    try:
        parsed_width, parsed_height = int(width), int(height)
    except (TypeError, ValueError):
        return None
    if not 1 <= parsed_width <= 4096 or not 1 <= parsed_height <= 4096:
        return None
    client = getattr(request, "client", None)
    client_host = str(getattr(client, "host", "") or "unknown")[:200]
    # The URL Rotation endpoint is intentionally public on the trusted LAN,
    # so request headers alone are not sufficient evidence of an ESP32 pull.
    # Require the TCP peer to match the address that was verified during
    # provisioning.  A reverse proxy must explicitly preserve the direct
    # device connection; forwarded headers are deliberately ignored.
    expected_host = ""
    if isinstance(device, dict):
        pull = device.get("pull_provision") or {}
        expected_url = str(pull.get("device_url") or "").strip()
        try:
            expected_host = str(urlsplit(expected_url).hostname or "")
        except ValueError:
            expected_host = ""
    if not expected_host or not _same_ip_host(client_host, expected_host):
        return None
    return {
        "client": client_host,
        "firmware_version": version[:200],
        "display": {"width": parsed_width, "height": parsed_height, "orientation": raw_orientation},
    }


def _same_ip_host(left: str, right: str) -> bool:
    """Compare a socket peer and a registered IPv4 address safely.

    Uvicorn normally reports the ESP32 as an IPv4 literal.  Accept an IPv4
    mapped IPv6 spelling as well, but never treat an arbitrary hostname or an
    empty value as a matching device address.
    """

    left_text, right_text = str(left or "").strip(), str(right or "").strip()
    try:
        left_ip = ipaddress.ip_address(left_text)
        right_ip = ipaddress.ip_address(right_text)
    except ValueError:
        # Production registration stores a literal RFC1918 address.  Do not
        # fall back to hostname equality: an arbitrary Host/DNS name must not
        # become connection evidence for an unauthenticated LAN endpoint.
        return False
    left_mapped = getattr(left_ip, "ipv4_mapped", None)
    right_mapped = getattr(right_ip, "ipv4_mapped", None)
    if left_mapped is not None:
        left_ip = left_mapped
    if right_mapped is not None:
        right_ip = right_mapped
    return left_ip == right_ip


def _photoframe_capability_from_headers(device: dict, *, width=None, height=None, orientation=None) -> dict:
    """Normalize URL Rotation native-size headers to the configured orientation.

    Official PhotoFrame requests report the panel's native 800x480 dimensions
    as well as a separate ``X-Display-Orientation``.  For a portrait-mounted
    Waveshare panel those fields are intentionally not the encoded JPEG size.
    Translate that documented native-size form before applying Case7's strict
    product profile validation.
    """

    try:
        registered = _registered_photoframe_display(device)
    except DeviceError as exc:
        raise DisplayPolicyError(str(exc)) from exc
    requested = str(orientation or "").strip().lower()
    native = (int(photo_frame_profile(registered["profile_id"])["width"]), int(photo_frame_profile(registered["profile_id"])["height"]))
    try:
        supplied = None if width in (None, "") or height in (None, "") else (int(width), int(height))
    except (TypeError, ValueError) as exc:
        raise DisplayPolicyError("display dimensions must be integers") from exc
    translated_width, translated_height = width, height
    if supplied == native and requested in {"landscape", "portrait"}:
        translated_width, translated_height = native if requested == "landscape" else native[::-1]
    capability = _display_capability_with_headers(
        registered,
        width=translated_width,
        height=translated_height,
        orientation=orientation,
    )
    return _enforce_registered_photoframe_profile(device, capability)

# Keep test harnesses that patch ``app.DATA_DIR`` isolated, while production
# uploads are staged beside the managed Pictures library rather than inside a
# release/shared data directory.  The staged files are removed after the
# single indexing job finishes.
_CONFIG_DATA_DIR = Path(DATA_DIR).resolve()


def _upload_staging_dir() -> Path:
    configured_data_dir = Path(DATA_DIR).expanduser().resolve()
    if configured_data_dir != _CONFIG_DATA_DIR:
        return configured_data_dir / "upload-tmp"
    return Path(UPLOAD_TMP_DIR).expanduser().resolve()


def _touchscreen_interval_seconds(config: dict) -> int:
    """Return the fast local display cadence with legacy fallback.

    ``display.interval_seconds`` was the only clock in older deployments.
    Reading it as a fallback keeps an existing configuration usable while new
    configurations can independently tune the HDMI touchscreen and browser.
    """

    display = config.get("display") or {}
    try:
        value = int(display.get("touchscreen_interval_seconds", display.get("interval_seconds", 60)))
    except (TypeError, ValueError):
        value = 60
    return max(5, value)


def _touchscreen_enabled(config: dict) -> bool:
    """Read the local panel switch, falling back to the legacy display flag."""

    display = config.get("display") or {}
    return bool(display.get("enabled", True)) and bool(
        display.get("touchscreen_enabled", display.get("enabled", True))
    )


def _remote_refresh_seconds(config: dict) -> int:
    """Return the browser polling cadence without touching e-paper timing."""

    display = config.get("display") or {}
    try:
        value = int(display.get("remote_refresh_seconds", 30))
    except (TypeError, ValueError):
        value = 30
    return max(5, value)


def _epaper_interval_seconds(config: dict) -> int:
    """Return the slow physical e-paper refresh cadence.

    The persisted setting is constrained by ``ConfigStore`` to ten or thirty
    minutes.  A defensive fallback keeps lightweight test doubles and legacy
    configs safe if they omit the new field.
    """

    epaper = config.get("epaper") or {}
    try:
        value = int(epaper.get("rotation_interval_seconds", 1800))
    except (TypeError, ValueError):
        value = 1800
    return value if value in {600, 1800} else 1800


def _device_poll_seconds(config: dict, device_kind: Optional[str] = None) -> int:
    """Return a remote endpoint's recommended polling cadence.

    LCD clients are cheap to refresh and follow the browser/remote cadence;
    physical e-paper clients (including PhotoPainter) use the independent
    ten/thirty-minute cadence.  The legacy ``device.poll_seconds`` value is
    retained for unknown device kinds.
    """

    if device_kind in {"epaper", "photoframe"}:
        return _epaper_interval_seconds(config)
    if device_kind == "lcd":
        return _remote_refresh_seconds(config)
    try:
        return max(5, int((config.get("device") or {}).get("poll_seconds", 1800)))
    except (TypeError, ValueError):
        return 1800


def _refresh_local_selection(state, *, refresh_weather: bool, render_epaper: bool = False):
    """Call the local refresh hook while tolerating legacy test doubles.

    Older integrations implemented ``refresh_display(refresh_weather=...)``
    without the new physical-output flag.  The fallback is limited to an
    unexpected keyword error and never masks failures raised by the refresh
    implementation itself.
    """

    try:
        return state.refresh_display(
            refresh_weather=refresh_weather,
            render_epaper=render_epaper,
        )
    except TypeError as exc:
        if "render_epaper" not in str(exc):
            raise
        return state.refresh_display(refresh_weather=refresh_weather)


def _advance_local_selection(state):
    """Advance a touchscreen gesture through the NPU-backed local path.

    ``refresh_display_fast`` no longer means metadata-only navigation.  It
    consumes the semantic candidate plan prepared by ``SmartSelector`` and
    performs a cached-weather NPU ranking synchronously only when that plan is
    cold or exhausted.  The thread-pool wrapper keeps this bounded work off
    Uvicorn's event loop while preserving the selector's single-flight lock.
    """

    fast = getattr(state, "refresh_display_fast", None)
    if callable(fast):
        return fast()
    return _refresh_local_selection(state, refresh_weather=False, render_epaper=False)


def _invalidate_local_preselection(state):
    """Invalidate volatile semantic candidates after a state mutation."""

    selector = getattr(state, "selector", None)
    invalidate = getattr(selector, "invalidate_preselection", None)
    if callable(invalidate):
        invalidate()


def _upload_model_ids(registry, index_config):
    """Resolve the admitted models for an automatic upload index job."""
    admitted = tuple(registry.ids())
    configured = tuple(index_config.get("models") or ())
    selected = tuple(model_id for model_id in configured if model_id in admitted) or admitted
    if index_config.get("auto_index_uploads", True) and not selected:
        raise AlbumIndexError(
            "no admitted NPU models are available; upload indexing is disabled until model admission completes"
        )
    return selected if index_config.get("auto_index_uploads", True) else ()


def _record_local_history(index, config, row, seed_only=False):
    """Persist the touchscreen back stack without duplicating its current top."""
    if row is None or not hasattr(index, "record_display_history"):
        return
    try:
        photo_id = int(row["id"])
    except (KeyError, TypeError, ValueError):
        return
    history = index.display_history_ids("local", 1) if hasattr(index, "display_history_ids") else []
    if seed_only and history:
        return
    if history and int(history[0]) == photo_id:
        return
    repeat_window = int(config.get()["display"].get("repeat_window", 12))
    index.record_display_history("local", photo_id, max(1, repeat_window))


def _selector_state_guard(selector):
    """Return the selector's shared lock, or a no-op for lightweight fakes.

    API tests and offline tooling use small selector doubles that predate the
    state lock.  Keeping the fallback here lets those callers retain the same
    contract while the production selector serializes direct state updates.
    """

    lock = getattr(selector, "state_lock", None)
    return lock if lock is not None else nullcontext()


def _selector_revision(selector, default=1):
    """Read a selector revision atomically, including for test doubles."""

    with _selector_state_guard(selector):
        try:
            return int(getattr(selector, "revision", default))
        except (TypeError, ValueError):
            return int(default)


def _rewind_local_history(index, row):
    if row is None or not hasattr(index, "rewind_display_history"):
        return
    try:
        index.rewind_display_history("local", int(row["id"]))
    except (KeyError, TypeError, ValueError):
        return


class TextSearchRequest(BaseModel):
    query: str
    model: str = "auto"
    top_k: int = TOP_K_RESULTS


class ApplicationState:
    def __init__(self, registry_path=None, allow_numpy_fallback=False, epaper_backend=None):
        self.registry = ModelRegistry(path=registry_path, require_artifacts=True) if registry_path else ModelRegistry(require_artifacts=True)
        self.manager = ModelManager(registry=self.registry)
        self.index = AlbumIndex(manager=self.manager, allow_numpy_fallback=allow_numpy_fallback)
        self.config = ConfigStore()
        epaper_config = EpaperConfig.from_environment()
        configured_epaper = self.config.get()["epaper"]
        # Rendering policy keys live beside transport keys in the persisted
        # config, but EpaperConfig intentionally models transport only.
        epaper_fields = {item.name for item in fields(EpaperConfig)}
        epaper_config = replace(
            epaper_config,
            **{key: value for key, value in configured_epaper.items() if key in epaper_fields},
        )
        if epaper_backend:
            epaper_config = replace(epaper_config, backend=epaper_backend)
        self.epaper = EpaperDisplay(epaper_config)
        self.devices = DeviceRegistry()
        self.renderer = PhotoRenderer()
        # Direct PhotoFrame pushes use one explicitly selected firmware
        # contract per device. push_device() serializes all outbound work and
        # creates the bounded client from that device's timeout/retry policy,
        # so a slow e-paper endpoint cannot create an unbounded queue.
        self.push_lock = threading.RLock()
        self.selector = SmartSelector(self.index, self.config)
        # Restore the durable local selection before exposing health/status or
        # starting the five-second scheduler.  This is intentionally local and
        # does not refresh weather or run an embedding query.
        restored_local = self.selector.restore_local()
        self.lock = threading.RLock()
        self.jobs = {}
        self.jobs_lock = threading.RLock()
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="album-index")
        self.scheduler_stop = threading.Event()
        config = self.config.get()
        touchscreen_interval = _touchscreen_interval_seconds(config)
        epaper_interval = _epaper_interval_seconds(config)
        self._initial_local_selection_restored = restored_local is not None
        self._initial_display_deadline = (
            time.monotonic() + touchscreen_interval if restored_local is not None else 0.0
        )
        # The physical e-paper output has its own clock.  A restored frame is
        # held for the complete slow interval after restart; with no restored
        # frame the first available photo is shown on the next scheduler tick.
        self._initial_epaper_deadline = (
            time.monotonic() + epaper_interval if restored_local is not None else 0.0
        )
        self.scheduler = threading.Thread(target=self._schedule_loop, name="album-display", daemon=True)
        self.scheduler.start()

    def _schedule_loop(self):
        # A restored photo stays on screen until the next configured interval.
        # With no persisted selection, the first tick is allowed to populate
        # an empty display immediately.
        next_display = getattr(self, "_initial_display_deadline", 0.0)
        next_epaper = getattr(self, "_initial_epaper_deadline", 0.0)
        next_weather = 0.0
        touchscreen_was_enabled = True
        epaper_was_enabled = True
        while not self.scheduler_stop.wait(5):
            now = time.monotonic()
            config = self.config.get()
            touchscreen_interval = _touchscreen_interval_seconds(config)
            epaper_interval = _epaper_interval_seconds(config)
            # ``display.enabled`` remains the legacy master switch (and still
            # controls the physical e-paper output).  The device page can
            # independently pause the HDMI touchscreen through
            # ``touchscreen_enabled``.
            display_enabled = bool(config["display"].get("enabled", True))
            touchscreen_enabled = _touchscreen_enabled(config) and display_enabled
            weather_due = now >= next_weather
            display_due = now >= next_display
            epaper_due = now >= next_epaper
            # Weather and the local E6/touchscreen display are independent of
            # a remote PhotoFrame. A transient weather or local-display error
            # must never suppress an explicitly configured outbound push.
            if weather_due:
                try:
                    self.selector.refresh_weather()
                except Exception:
                    pass
                next_weather = now + max(60, int(config["weather"]["refresh_seconds"]))
            # Keep one NPU-derived navigation plan warm for the local panel.
            # ``prepare_next`` is idempotent for a given photo/config/weather
            # context, so this five-second scheduler check does not repeatedly
            # run inference.  It also runs on the existing scheduler thread;
            # no second NPU worker or image cache is introduced.
            if display_enabled and touchscreen_enabled and not display_due:
                prepare_next = getattr(self.selector, "prepare_next", None)
                if callable(prepare_next):
                    try:
                        prepare_next("jpeg")
                    except Exception:
                        # A missing/being-rebuilt embedding index is reported
                        # by health and the next gesture can retry once.  It
                        # must not stop remote device pushes or the scheduler.
                        pass
            if not touchscreen_enabled:
                # Preserve the slow physical deadline while paused.  On
                # resume we deliberately start a new e-paper interval rather
                # than treating a stale deadline as permission to refresh a
                # panel immediately.
                touchscreen_was_enabled = False
            elif not touchscreen_was_enabled:
                # A resumed touchscreen may choose a new photo promptly.  Its
                # local switch must not reset the independent e-paper clock.
                try:
                    _refresh_local_selection(self, refresh_weather=False, render_epaper=False)
                except Exception:
                    pass
                next_display = now + touchscreen_interval
                touchscreen_was_enabled = True
            elif display_due:
                try:
                    # Selecting a new local photo is fast and should update
                    # the touchscreen/browser without forcing an e-paper
                    # waveform refresh on every local tick.
                    _refresh_local_selection(self, refresh_weather=False, render_epaper=False)
                except Exception:
                    pass
                next_display = now + touchscreen_interval
            if not display_enabled:
                # The legacy master switch pauses the physical e-paper output;
                # it also makes the touchscreen unavailable through the
                # conjunction above.  Keep the two wake-up deadlines separate
                # so a touchscreen-only pause does not reset e-paper timing.
                epaper_was_enabled = False
            elif not epaper_was_enabled:
                next_epaper = now + epaper_interval
                epaper_due = False
                epaper_was_enabled = True
            if display_enabled and epaper_due:
                try:
                    # E-paper is a separate physical output and therefore
                    # follows its own slow cadence, independent of local
                    # touchscreen rotation and browser polling.
                    self.refresh_epaper(refresh_weather=False)
                except Exception:
                    pass
                next_epaper = now + epaper_interval
            try:
                self.push_due()
            except Exception:
                pass

    def push_due(self):
        """Push each explicitly configured PhotoFrame at its cron slot.

        ``push.last_slot`` is written for both success and failure, while
        ``push.last_success_slot`` is written only after a 2xx response.  The
        latter prevents a later render/ETag change from sending a second
        physical refresh in the same slot. A failing endpoint is still
        retried only within its bounded retry window; a manual ``force``
        request remains available for immediate recovery.
        """

        try:
            now = self.selector._now()
        except Exception:
            from datetime import datetime
            now = datetime.now()
        for item in self.devices.list():
            if not item.get("enabled", True):
                continue
            push = item.get("push") or {}
            if not push.get("enabled") or not push.get("base_url"):
                continue
            if item.get("display", {}).get("kind") != "photoframe":
                continue
            try:
                _registered_photoframe_display(item)
                policy = validate_policy(item.get("policy") or DEFAULT_PHOTOFRAME_POLICY)
                slot = effective_cron_slot(now, policy["rotation_cron"]) if policy.get("auto_rotate", True) else None
                if slot is None:
                    continue
                # A successful direct push is a physical display operation,
                # not merely an ETag cache write.  A later render may differ
                # in the same cron slot (for example a date/time overlay),
                # but that must not trigger a second e-paper refresh.
                if push.get("last_success_slot") == slot:
                    continue
                retry_at = push.get("retry_at")
                if (
                    retry_at is not None
                    and time.time() < float(retry_at)
                    and push.get("last_slot") == slot
                ):
                    continue
                self.push_device(item["device_id"], force=False, scheduled_slot=slot)
            except Exception:
                # The attempt is recorded by push_device(); one bad device
                # must not stop pushes to other registered displays.
                continue

    def push_device(
        self,
        device_id: str,
        *,
        force: bool = False,
        scheduled_slot: Optional[str] = None,
        force_send: bool = False,
    ):
        """Render and send one image through the device's explicit transport."""

        with self.push_lock:
            device = self.devices.get(device_id)
            if not device.get("enabled", True):
                raise PushError("device is disabled")
            if device.get("display", {}).get("kind") != "photoframe":
                raise PushError("active push requires a photoframe device")
            try:
                capability = _registered_photoframe_display(device)
            except DeviceError as exc:
                raise PushError(str(exc)) from exc
            push = dict(device.get("push") or {})
            if not push.get("enabled") or not push.get("base_url"):
                raise PushError("active push is not configured; set push.enabled and push.base_url")
            policy = validate_policy(device.get("policy") or DEFAULT_PHOTOFRAME_POLICY)
            explicit_scheduled_call = scheduled_slot is not None
            if scheduled_slot is None:
                try:
                    scheduled_slot = effective_cron_slot(self.selector._now(), policy["rotation_cron"]) if policy.get("auto_rotate", True) else None
                except Exception:
                    scheduled_slot = None
            if explicit_scheduled_call and scheduled_slot is None:
                raise PushError("no active rotation slot")
            row = self.selector.current_for_device(device_id, policy, force=force)
            if row is None:
                retry_at = time.time() + min(300, max(5, int(push.get("timeout_seconds", 60)) * int(push.get("attempts", 1))))
                self.devices.mark_push(device_id, "empty", error="no available photo", slot=scheduled_slot, retry_at=retry_at)
                raise PushError("no available photo for active push")

            # Profile validation happens before selection above, so a stale or
            # corrupted registry cannot mutate display state before rejection.
            if int(policy.get("rotation", 0)) != 0:
                raise PushError(
                    "photoframe rotation is not supported; use landscape or portrait orientation"
                )

            # Keep the scheduler idempotent at the physical-device level. A
            # prior 2xx in this exact cron slot wins over a newly rendered
            # ETag; only an explicit manual force/force_send may override it.
            if (
                scheduled_slot is not None
                and not force
                and not force_send
                and push.get("last_success_slot") == scheduled_slot
            ):
                self.devices.mark_push(
                    device_id,
                    "not_modified",
                    photo_id=int(row["id"]),
                    etag=push.get("last_etag"),
                    slot=scheduled_slot,
                )
                return {
                    "device_id": device_id,
                    "status": "not_modified",
                    "photo_id": int(row["id"]),
                    "filename": str(_row_value(row, "filename", "")),
                    "etag": push.get("last_etag"),
                    "slot": scheduled_slot,
                    "reason": "cron_slot_already_sent",
                }

            # Honor the physical capability while retaining the device policy
            # as the source of crop/overlay/quality settings.
            render_policy = dict(policy)
            try:
                # The receiver's capability is the encoded frame contract.
                # Taking min(policy, capability) silently turns a portrait
                # panel such as 480x800 into a square 480x480 response.
                target_width = int(capability.get("width", render_policy["width"]))
                target_height = int(capability.get("height", render_policy["height"]))
            except (TypeError, ValueError) as exc:
                raise PushError("invalid photoframe display dimensions") from exc
            if not 1 <= target_width <= 4096 or not 1 <= target_height <= 4096:
                raise PushError("photoframe display dimensions are out of range")
            render_policy["width"] = target_width
            render_policy["height"] = target_height
            # The two registered PhotoFrame products expose only a named
            # landscape/portrait contract.  Profile validation above already
            # rejected old or corrupted degree-based rotations.
            render_policy["rotation"] = 0
            # The upstream PhotoFrame raw upload handler rejects bodies over
            # 5 MiB. Keep that protocol limit even if a registry entry was
            # created with a larger generic capability.
            render_policy["max_bytes"] = min(
                int(render_policy["max_bytes"]),
                int(capability.get("max_bytes", 2 * 1024 * 1024)),
                5 * 1024 * 1024,
            )
            weather_revision = _weather_etag_value(self.selector)
            selection_revision = int(_row_value(row, "selection_revision", _selector_revision(self.selector)))
            protocol = str(push.get("protocol") or "photoframe_api")
            if protocol not in PUSH_PROTOCOLS:
                raise PushError("unsupported active-push protocol; choose photoframe_api, waveshare_dataup, or case7_push")
            if protocol == "waveshare_dataup" and (
                int(capability.get("width", 800)) != 800 or int(capability.get("height", 480)) != 480
            ):
                raise PushError("waveshare_dataup requires an 800x480 PhotoPainter capability")
            target_orientation = normalize_display_orientation(
                capability.get("orientation"),
                (render_policy["width"], render_policy["height"]),
            )
            overlay_time = _overlay_time_etag_value(
                self.selector,
                self.config.get().get("timezone", ""),
                render_policy["overlay_date"],
            )
            variant = "{protocol}:{width}x{height}:r{rotation}:o{orientation_mode}:a{target}:m{max_bytes}:p{revision}:c{crop}:d{date}:w{weather}:z{overlay_time}:s{selection}:push".format(
                protocol=protocol,
                width=render_policy["width"], height=render_policy["height"], rotation=render_policy["rotation"],
                orientation_mode=render_policy["orientation_mode"], target=target_orientation,
                max_bytes=render_policy["max_bytes"], revision=render_policy["policy_revision"], crop=render_policy["crop_mode"],
                date=int(render_policy["overlay_date"]), weather=f"{int(render_policy['overlay_weather'])}:{weather_revision}",
                overlay_time=overlay_time,
                selection=selection_revision,
            )
            etag = _etag(row, f"{protocol}:{device_id}:{variant}", 0, selection_revision)
            if not force and not force_send and push.get("last_status") in {"ok", "success"} and push.get("last_etag") == etag:
                # The same rendered bytes may recur after a playlist wraps.
                # Advance the persisted slot even though no network transfer
                # is needed, otherwise the five-second scheduler would revisit
                # that slot forever.
                self.devices.mark_push(
                    device_id,
                    "not_modified",
                    photo_id=int(row["id"]),
                    etag=etag,
                    slot=scheduled_slot,
                )
                return {
                    "device_id": device_id,
                    "status": "not_modified",
                    "photo_id": int(row["id"]),
                    "filename": str(_row_value(row, "filename", "")),
                    "etag": etag,
                    "slot": scheduled_slot,
                }
            try:
                render_args = (
                    Path(row["filepath"]),
                    render_policy,
                    self.config.get()["timezone"],
                    (_row_value(row, "weather", "") or ""),
                )
                # The target hint only changes behavior for the opt-in mode;
                # omitting it for the compatibility default keeps lightweight
                # renderer doubles and older integrations source-compatible.
                if render_policy.get("orientation_mode") == "match_display":
                    body, actual_width, actual_height = self.renderer.render(
                        *render_args, target_orientation=target_orientation
                    )
                else:
                    body, actual_width, actual_height = self.renderer.render(*render_args)
                # Marking the slot before network I/O makes a failed slot
                # observable and prevents repeated scheduler retries.  The
                # client performs its own bounded transport retries.
                self.devices.mark_push(device_id, "sending", photo_id=int(row["id"]), etag=etag, slot=scheduled_slot)
                client = PhotoFramePushClient(
                    timeout_seconds=float(push.get("timeout_seconds", 60)),
                    attempts=int(push.get("attempts", 1)),
                )
                if protocol == "photoframe_api":
                    result = client.push_jpeg(push["base_url"], body, photo_id=int(row["id"]), etag=etag)
                    payload_bytes = len(body)
                    payload_format = "jpeg"
                elif protocol == "case7_push":
                    result = client.push_case7_jpeg(push["base_url"], body, photo_id=int(row["id"]), etag=etag)
                    payload_bytes = len(body)
                    payload_format = "jpeg"
                else:
                    # Waveshare's demo /dataUP handler reads a raw BMP body.
                    # Convert in memory only; no derived image is persisted.
                    bmp = _jpeg_to_bmp(body, width=800, height=480)
                    result = client.push_bmp(push["base_url"], bmp, photo_id=int(row["id"]), etag=etag)
                    payload_bytes = len(bmp)
                    payload_format = "bmp"
                self.devices.mark_push(device_id, "ok", photo_id=int(row["id"]), etag=etag, slot=scheduled_slot)
                return {
                    "device_id": device_id,
                    "status": "ok",
                    "photo_id": int(row["id"]),
                    "filename": str(_row_value(row, "filename", "")),
                    "etag": etag,
                    "width": actual_width,
                    "height": actual_height,
                    "protocol": protocol,
                    "format": payload_format,
                    "bytes": payload_bytes,
                    "attempts": result.attempts,
                    "http_status": result.status_code,
                    "slot": scheduled_slot,
                }
            except Exception as exc:
                retry_at = time.time() + min(300, max(5, int(push.get("timeout_seconds", 60)) * int(push.get("attempts", 1))))
                self.devices.mark_push(
                    device_id,
                    "error",
                    error=str(exc),
                    photo_id=int(row["id"]),
                    etag=etag,
                    slot=scheduled_slot,
                    retry_at=retry_at,
                )
                raise

    def _show_epaper_row(self, row):
        """Write one already-selected photo to the local E6 device.

        Selection and physical output are intentionally separate so the
        touchscreen can advance quickly without wearing the e-paper panel.
        """

        if row and self.epaper.config.backend == "orangepi":
            epaper_config = self.config.get()["epaper"]
            self.epaper.show(
                Path(row["filepath"]),
                dither=bool(epaper_config.get("e6_dither", True)),
                orientation_mode=epaper_config.get("orientation_mode", "auto"),
                rotation=int(epaper_config.get("rotation", 0)),
            )

    def refresh_epaper(self, refresh_weather=False):
        """Refresh the local e-paper output without selecting a new photo.

        The current local selection is shared with the touchscreen/browser;
        this method only emits the latest selected frame at the slow e-paper
        cadence.  A first frame is selected if the library was empty at boot.
        """

        if refresh_weather:
            self.selector.refresh_weather()
        with self.lock, _selector_state_guard(self.selector):
            saved = self.index.get_display_state("local") if hasattr(self.index, "get_display_state") else None
            row = self.index.get_photo(int(saved["photo_id"])) if saved and saved.get("photo_id") else None
            if row is None:
                current = getattr(self.selector, "current", None)
                row = current if isinstance(current, dict) and current.get("filepath") else None
            if row is None:
                row = self.selector.current_photo("e6")
            self._show_epaper_row(row)
            return row

    def refresh_display(self, refresh_weather=True, render_epaper=False, smart_selection=True):
        # Weather is an explicit refresh concern, not part of the display
        # selection critical section. Fetch it before taking the application
        # lock so a slow provider cannot queue a concurrent next/previous
        # touchscreen action.
        if refresh_weather:
            self.selector.refresh_weather()
        with self.lock, _selector_state_guard(self.selector):
            saved = self.index.get_display_state("local") if hasattr(self.index, "get_display_state") else None
            previous = self.index.get_photo(int(saved["photo_id"])) if saved and saved.get("photo_id") else None
            # A restart retains the current photo; seed its back stack before selecting a new one.
            _rewind_local_history(self.index, previous)
            _record_local_history(self.index, self.config, previous, seed_only=True)
            if smart_selection:
                current = self.selector.choose("e6")
            else:
                next_photo = getattr(self.selector, "next_local_photo", None)
                current = next_photo("jpeg") if callable(next_photo) else self.selector.choose("e6")
            if current and hasattr(self.index, "save_display_state"):
                self.index.save_display_state("local", int(current["id"]), "manual", 1, int(current.get("selection_revision", self.selector.revision)))
            _record_local_history(self.index, self.config, current)
            if render_epaper:
                self._show_epaper_row(current)
            return current

    def refresh_display_fast(self):
        """Advance the local touchscreen using the NPU semantic plan.

        Weather is intentionally not fetched here.  The selector consumes a
        precomputed NPU-ranked candidate and only performs a synchronous
        cached-weather ranking when no valid candidate is available.
        """

        return self.refresh_display(
            refresh_weather=False,
            render_epaper=False,
            smart_selection=False,
        )

    def submit_upload(self, paths, capture_time=None):
        job_id = uuid.uuid4().hex
        file_total = len(paths)
        with self.jobs_lock:
            self.jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "phase": "queued",
                "progress": 0.0,
                "files_total": file_total,
                "files_completed": 0,
                "accepted": 0,
                "duplicates": 0,
                "skipped": 0,
                "index_files_total": 0,
                "index_files_completed": 0,
                "embedding_total": 0,
                "embedding_completed": 0,
                "current_model": None,
                "created_at": time.time(),
            }

        def update_job(**values):
            with self.jobs_lock:
                self.jobs[job_id].update(**values)

        def run():
            try:
                # Capture digests before the temporary upload files are removed so
                # clients can build a playlist from the completed job response.
                upload_records = []
                for offset, value in enumerate(paths, start=1):
                    source = Path(value)
                    upload_records.append({"filename": source.name, "sha256": _sha256(source)})
                    update_job(
                        status="running",
                        phase="hashing",
                        progress=0.15 * offset / max(1, file_total),
                        files_completed=offset,
                    )

                def report_index_progress(event):
                    phase = event.get("phase")
                    if phase == "importing":
                        total = int(event.get("files_total") or 0)
                        completed = int(event.get("files_completed") or 0)
                        progress = 0.15 + 0.15 * completed / max(1, total)
                        update_job(
                            status="running",
                            phase=phase,
                            progress=progress,
                            files_total=total,
                            files_completed=completed,
                            accepted=int(event.get("accepted") or 0),
                            duplicates=int(event.get("duplicates") or 0),
                        )
                        return

                    index_total = int(event.get("files_total") or 0)
                    index_completed = int(event.get("files_completed") or 0)
                    embedding_total = int(event.get("embedding_total") or 0)
                    embedding_completed = int(event.get("embedding_completed") or 0)
                    if phase == "validating":
                        progress = 0.30 + 0.10 * index_completed / max(1, index_total)
                    elif phase == "embedding":
                        progress = 0.40 + 0.55 * embedding_completed / max(1, embedding_total)
                    else:
                        progress = 0.96
                    update_job(
                        status="running",
                        phase=phase or "indexing",
                        progress=progress,
                        index_files_total=index_total,
                        index_files_completed=index_completed,
                        embedding_total=embedding_total,
                        embedding_completed=embedding_completed,
                        current_model=event.get("current_model"),
                        duplicates=int(event.get("duplicates") or 0),
                        skipped=int(event.get("skipped") or 0),
                    )

                index_config = self.config.get()["index"]
                model_ids = _upload_model_ids(self.registry, index_config)
                summary = self.index.import_uploads(
                    paths,
                    model_ids,
                    progress_reporter=report_index_progress,
                )
                for offset, record in enumerate(upload_records, start=1):
                    row = self.index.find_by_sha256(record["sha256"])
                    if row and capture_time:
                        metadata = {"capture_time": capture_time, "capture_time_source": "client"}
                        self.index.update_photo_metadata(int(row["id"]), metadata)
                    update_job(
                        status="running",
                        phase="finalizing",
                        progress=0.96 + 0.03 * offset / max(1, file_total),
                        current_model=None,
                    )
                photo_ids = []
                seen_photo_ids = set()
                for record in upload_records:
                    row = self.index.find_by_sha256(record["sha256"])
                    if row:
                        photo_id = int(row["id"])
                        record["photo_id"] = photo_id
                        if photo_id not in seen_photo_ids:
                            seen_photo_ids.add(photo_id)
                            photo_ids.append(photo_id)
                value = summary.to_dict()
                value["photo_ids"] = photo_ids
                value["files"] = upload_records
                update_job(
                    status="completed",
                    phase="completed",
                    progress=1.0,
                    files_completed=file_total,
                    accepted=summary.discovered,
                    duplicates=summary.duplicates,
                    skipped=summary.skipped,
                    current_model=None,
                    summary=value,
                    photo_ids=photo_ids,
                    files=upload_records,
                )
                with _selector_state_guard(self.selector):
                    self.selector.revision += 1
                    _invalidate_local_preselection(self)
            except Exception as exc:
                update_job(status="failed", phase="failed", current_model=None, error=str(exc))
            finally:
                for path in paths:
                    Path(path).unlink(missing_ok=True)

        self.executor.submit(run)
        return self.jobs[job_id]

    def close(self):
        self.scheduler_stop.set()
        self.executor.shutdown(wait=True, cancel_futures=False)
        self.manager.release()
        self.index.close()


_state = None


def get_state():
    if _state is None:
        raise RuntimeError("application state has not been initialized")
    return _state


def _npu_snapshot():
    snapshot = {"pyacl_available": importlib.util.find_spec("acl") is not None, "npu_smi_available": shutil.which("npu-smi") is not None, "device": None, "health": None}
    if not snapshot["npu_smi_available"]:
        return snapshot
    try:
        completed = subprocess.run(["npu-smi", "info"], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=4)
        for line in completed.stdout.splitlines():
            if "310B" in line:
                snapshot["device"] = "Ascend 310B4" if "310B4" in line else "Ascend 310B"
                snapshot["health"] = "Alarm" if "Alarm" in line else "Normal"
                break
    except (OSError, subprocess.SubprocessError):
        pass
    return snapshot


def health_payload():
    state = get_state()
    admitted = set(state.registry.ids())
    missing = [model_id for model_id in REQUIRED_MODEL_IDS if model_id not in admitted]
    npu = _npu_snapshot()
    return {
        "status": "ready" if not missing and npu["pyacl_available"] else "degraded",
        "backend": "npu",
        "role": "smart_album_server",
        "npu": npu,
        "admitted_models": sorted(admitted),
        "missing_required_models": missing,
        "index": state.index.stats(),
        "epaper_backend": state.epaper.config.backend,
        "config_revision": state.config.get()["revision"] if hasattr(state, "config") else None,
        "selection": _public_selector_status(state.selector.status()) if hasattr(state, "selector") else None,
        "devices": len(state.devices.list()) if hasattr(state, "devices") else 0,
    }


def models_payload():
    state = get_state()
    candidates = {record.model_id: record for record in load_candidates()}
    admitted = set(state.registry.ids())
    payload = []
    for model_id, candidate in candidates.items():
        # The production registry is the source of truth after admission.  A
        # candidate record is retained for metadata when a model is not
        # admitted yet, while test doubles may only expose ``ids()``.
        record = candidate
        getter = getattr(state.registry, "get", None)
        if model_id in admitted and getter is not None:
            try:
                record = getter(model_id)
            except (KeyError, RegistryError):
                record = candidate
        components = {}
        for kind, component in record.components.items():
            components[kind] = {
                "precision_mode": record.effective_precision_mode(component),
                "input_dtype": component.input_dtype,
                "output_dtype": component.output_dtype,
                "input_shape": list(component.input_shape),
                "keep_dtype": str(component.atc_keep_dtype) if component.atc_keep_dtype else None,
            }
        image_precision = components.get("image", {}).get("precision_mode", record.precision_mode)
        strategy = getattr(record, "precision_strategy", None)
        payload.append(
            {
                "model_id": record.model_id,
                "display_name": record.display_name,
                "languages": list(record.languages),
                "embedding_dim": record.embedding_dim,
                # Keep the legacy scalar while exposing the per-component
                # contract needed to audit selective mixed precision.
                "precision": image_precision,
                "precision_mode": record.precision_mode,
                "components": components,
                "precision_strategy": dict(strategy) if strategy else None,
                "status": "admitted" if model_id in admitted else "candidate",
                "supports_text": record.supports_text,
            }
        )
    return payload


def system_markdown():
    """Compatibility summary for CLI callers and existing service checks."""
    payload = health_payload()
    stats = payload["index"]
    return "<br>\n".join((
        f"**服务状态**　{payload['status']}",
        f"**NPU**　{payload['npu'].get('device') or '未检测'} / {payload['npu'].get('health') or '未知'}",
        f"**照片**　{stats['available_photos']} 可用 / {stats['unavailable_photos']} 不可用",
        f"**电子墨水**　{payload['epaper_backend']}",
    ))


def _safe_photo(row):
    return {key: row[key] for key in row.keys() if key not in {"filepath", "sha256", "mtime_ns"}}


def _photo_preview_url(photo_id: int) -> str:
    """Return the transient browser-safe URL for a managed photo.

    Camera MPO files are valid JPEG containers but are not consistently
    rendered by embedded browsers when served with ``image/mpo``.  Gallery
    consumers therefore use the on-demand JPEG endpoint; the original file
    endpoint remains available separately for downloads.
    """

    return f"/api/photos/{int(photo_id)}/preview?width=480&height=360"


def _row_value(row, key, default=None):
    """Read either a dict or sqlite3.Row without exposing storage details."""
    if row is None:
        return default
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return default


def _photo_state(index, photo_id):
    if photo_id is None:
        return None
    row = index.get_photo(int(photo_id)) if hasattr(index, "get_photo") else None
    if row is None:
        return None
    return {"photo_id": int(_row_value(row, "id")), "filename": str(_row_value(row, "filename", ""))}


def _local_touchscreen_state(state):
    """Build a safe, device-shaped view of the built-in HDMI panel."""

    config = state.config.get()
    local_getter = getattr(state.devices, "local_touchscreen", None)
    device = local_getter(create=True) if callable(local_getter) else {
        "name": "本机触摸屏",
        "display": {"kind": "touchscreen", "width": 1920, "height": 1080},
        "last_status": "local",
    }
    saved = state.index.get_display_state("local") if hasattr(state.index, "get_display_state") else None
    photo_id = _row_value(saved, "photo_id")
    current = _photo_state(state.index, photo_id)
    selector_status = _public_selector_status(state.selector.status()) if hasattr(state, "selector") else {}
    return {
        "device_id": LOCAL_TOUCHSCREEN_ID,
        "name": device.get("name", "本机触摸屏"),
        "device_type": "touchscreen",
        "is_local": True,
        "enabled": _touchscreen_enabled(config),
        "display": dict(device.get("display") or {}),
        "current": current,
        "selection_revision": int(_row_value(saved, "selection_revision", selector_status.get("selection_revision", 1))),
        "slot_key": _row_value(saved, "slot_key"),
        "selection_source": (selector_status.get("current") or {}).get("selection_source"),
        "semantic_preselection": selector_status.get("semantic_preselection"),
        "last_status": device.get("last_status", "local"),
        "last_seen": device.get("last_seen"),
        "interval_seconds": _touchscreen_interval_seconds(config),
        "show_filename": bool(config.get("display", {}).get("show_filename", True)),
        "repeat_window": int(config.get("display", {}).get("repeat_window", 12)),
        "orientation_mode": config.get("display", {}).get("orientation_mode", "auto"),
        "rotation": int(config.get("display", {}).get("rotation", 0)),
    }


def _local_touchscreen_payload(state):
    """Return the dedicated management API representation for the HDMI panel."""

    config = state.config.get()
    local_state = _local_touchscreen_state(state)
    device = dict(local_state)
    device.pop("current", None)
    device.pop("selection_revision", None)
    device.pop("slot_key", None)
    device.pop("interval_seconds", None)
    device.pop("show_filename", None)
    device.pop("repeat_window", None)
    device.pop("orientation_mode", None)
    device.pop("rotation", None)
    device["display"] = dict(
        device.get("display") or {},
        kind="touchscreen",
        orientation_mode=local_state["orientation_mode"],
        rotation=local_state["rotation"],
    )
    return {
        "device": device,
        "config": {
            "revision": config["revision"],
            "display": {
                "enabled": local_state["enabled"],
                "touchscreen_enabled": local_state["enabled"],
                "interval_seconds": local_state["interval_seconds"],
                "touchscreen_interval_seconds": local_state["interval_seconds"],
                "show_filename": local_state["show_filename"],
                "repeat_window": local_state["repeat_window"],
                "orientation_mode": local_state["orientation_mode"],
                "rotation": local_state["rotation"],
            },
        },
        "state": {
            "current": local_state["current"],
            "selection_revision": local_state["selection_revision"],
            "slot_key": local_state["slot_key"],
            "last_status": local_state["last_status"],
            "last_seen": local_state["last_seen"],
        },
    }


def _touchscreen_config_patch(payload):
    """Normalize the small, device-page touchscreen settings contract.

    The endpoint deliberately does not accept an arbitrary server
    configuration object.  It maps only settings that describe the built-in
    HDMI panel into the existing versioned ``display`` configuration group.
    Both flat fields (convenient for a device card) and a ``display`` object
    (convenient for API clients) are accepted.
    """

    if not isinstance(payload, dict):
        raise ConfigError("touchscreen settings must be an object")
    allowed = {
        "enabled",
        "interval_seconds",
        "touchscreen_interval_seconds",
        "show_filename",
        "repeat_window",
        "orientation_mode",
        "rotation",
    }
    nested = payload.get("display")
    if nested is not None and not isinstance(nested, dict):
        raise ConfigError("touchscreen.display must be an object")
    values = {key: payload[key] for key in allowed if key in payload}
    if nested:
        unknown_nested = set(nested) - allowed - {"width", "height"}
        if unknown_nested:
            raise ConfigError(
                "unsupported touchscreen display keys: "
                + ", ".join(sorted(unknown_nested))
            )
        values.update({key: nested[key] for key in nested if key in allowed})
    unknown = set(payload) - allowed - {"display", "revision", "name"}
    if unknown:
        raise ConfigError("unsupported touchscreen keys: " + ", ".join(sorted(unknown)))
    if "interval_seconds" in values and "touchscreen_interval_seconds" not in values:
        values["touchscreen_interval_seconds"] = values["interval_seconds"]
    values.pop("interval_seconds", None)
    if not values:
        return {}
    result = {}
    for key, value in values.items():
        if key in {"enabled", "show_filename"} and not isinstance(value, bool):
            raise ConfigError(f"touchscreen.{key} must be boolean")
        if key in {"touchscreen_interval_seconds", "repeat_window", "rotation"}:
            if isinstance(value, bool):
                raise ConfigError(f"touchscreen.{key} must be an integer")
            try:
                value = int(value)
            except (TypeError, ValueError) as exc:
                raise ConfigError(f"touchscreen.{key} must be an integer") from exc
        result[key] = value
    display = {}
    if "enabled" in result:
        display["touchscreen_enabled"] = result.pop("enabled")
    display.update(result)
    return {"display": display}


def _next_rotation_slot(selector, policy, limit_minutes=8 * 24 * 60):
    """Return the next matching cron slot as an ISO timestamp.

    The scheduler is deliberately minute-granular, matching PhotoFrame's
    three-field URL Rotation cron contract. This calculation is in-memory and
    does not trigger a weather request or create an image artifact.
    """
    if not policy.get("auto_rotate", True):
        return None
    try:
        now = selector._now().replace(second=0, microsecond=0)
    except Exception:
        from datetime import datetime
        now = datetime.now().replace(second=0, microsecond=0)
    for offset in range(1, int(limit_minutes) + 1):
        candidate = now + timedelta(minutes=offset)
        if effective_cron_slot(candidate, policy.get("rotation_cron", []), lookback_minutes=0):
            return candidate.isoformat()
    return None


def _weather_etag_value(selector):
    """Return stable weather content for ETag calculation.

    Open-Meteo responses include ``updated_at`` on every refresh. That timestamp
    is useful in status reports but must not invalidate a PhotoFrame retry when
    the actual weather values are unchanged.
    """
    with _selector_state_guard(selector):
        value = getattr(selector, "weather", {}) or {}
        if isinstance(value, dict):
            value = dict(value)
    if not isinstance(value, dict):
        return ""
    return json.dumps(
        {key: value.get(key) for key in ("status", "temperature", "weather_code", "wind_speed")},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _overlay_time_etag_value(selector, timezone: str, enabled: bool) -> str:
    """Return the exact minute that can change a rendered date overlay."""

    if not enabled:
        return ""
    try:
        now = selector._now()
    except Exception:
        from datetime import datetime

        now = datetime.now()
    # PhotoRenderer renders only to the minute.  Keep the ETag stable inside
    # that minute while ensuring a later minute cannot return stale overlay
    # text through a conditional GET.
    try:
        return now.strftime("%Y-%m-%dT%H:%M%z")
    except Exception:
        return str(timezone)


def _public_selector_status(value):
    status = dict(value)
    if status.get("current"):
        status["current"] = _safe_photo(status["current"])
    return status


def _selection_evidence(state, row=None):
    """Expose safe proof of how the local selection was produced.

    The image bytes and model internals stay private, but operators need to
    distinguish an NPU-ranked candidate from the exceptional metadata-only
    fallback while diagnosing a slow or empty index.
    """

    selector_status = _public_selector_status(state.selector.status())
    current = row if isinstance(row, dict) else selector_status.get("current") or {}
    source = current.get("selection_source") or "unknown"
    preselection = selector_status.get("semantic_preselection") or {}
    return {
        "used": str(source).startswith("npu_semantic"),
        "source": source,
        "last_inference": preselection.get("last_inference"),
        "queue_size": preselection.get("queue_size", 0),
    }


def _public_push_state(value):
    """Expose active-push configuration and audit fields without secrets."""

    push = dict(value or {})
    # Device tokens are never part of this structure.  Keep the URL visible to
    # the LAN management page because it is an operator-supplied endpoint, but
    # cap all strings returned from potentially old registries.
    for key in ("base_url", "last_status", "last_error", "last_etag", "last_slot", "last_success_slot"):
        if push.get(key) is not None:
            push[key] = str(push[key])[:500 if key == "last_error" else 200]
    return push


def _etag(row, profile, config_revision, selection_revision):
    # E6 responses contain only the photo bytes and explicit epaper rendering
    # options. The selector revision may advance for a local weather refresh,
    # but that does not change an E6 frame; keep its ETag content-addressed.
    if str(profile) == "e6" or str(profile).startswith("e6:"):
        selection_revision = 0
    value = f"{_row_value(row, 'id')}:{_row_value(row, 'sha256', '')}:{profile}:{config_revision}:{selection_revision}"
    return '"' + hashlib.sha256(value.encode()).hexdigest()[:32] + '"'


def _render_defaults(config, capability=None):
    """Build generic JPEG defaults from a local display or device capability."""

    capability = capability or {}
    display = config.get("display", {})
    return {
        "width": int(capability.get("width", 1920)),
        "height": int(capability.get("height", 1080)),
        "max_bytes": int(capability.get("max_bytes", 8 * 1024 * 1024)),
        "quality": int(config.get("device", {}).get("jpeg_quality", 82)),
        "rotation": int(capability.get("rotation", display.get("rotation", 0))),
        "orientation_mode": capability.get("orientation_mode") or display.get("orientation_mode", "auto"),
    }


def _render_options(source, defaults=None):
    """Normalize dimensions and orientation inputs for a JPEG response."""

    defaults = defaults or {}

    def integer(name, fallback, minimum, maximum):
        raw = source.get(name, fallback)
        if raw in (None, ""):
            raw = fallback
        try:
            value = int(raw)
        except (TypeError, ValueError) as exc:
            raise DisplayPolicyError(f"{name} must be an integer") from exc
        if not minimum <= value <= maximum:
            raise DisplayPolicyError(f"{name} is outside its range")
        return value

    width = integer("width", defaults.get("width", 1920), 1, 4096)
    height = integer("height", defaults.get("height", 1080), 1, 4096)
    max_bytes = integer("max_bytes", defaults.get("max_bytes", 8 * 1024 * 1024), 4096, 25 * 1024 * 1024)
    quality = integer("quality", defaults.get("quality", 82), 1, 100)
    rotation = integer("rotation", defaults.get("rotation", 0), 0, 360)
    if rotation not in {0, 90, 180, 270}:
        raise DisplayPolicyError("rotation must be 0, 90, 180, or 270")
    mode = validate_orientation_mode(source.get("orientation_mode", defaults.get("orientation_mode", "auto")))
    requested_orientation = source.get("orientation") or source.get("display_orientation")
    # The encoded frame remains ``width x height`` even when its pixels are
    # rotated for a physically mounted panel.
    target_size = (width, height)
    target_orientation = normalize_display_orientation(requested_orientation, target_size)
    if requested_orientation not in (None, "", "auto") and target_orientation != orientation_for_size(target_size):
        raise DisplayPolicyError("display orientation does not match width and height")
    return {
        "width": width,
        "height": height,
        "max_bytes": max_bytes,
        "quality": quality,
        "rotation": rotation,
        "orientation_mode": mode,
        "target_orientation": target_orientation,
        "variant": (
            f"{width}x{height}:r{rotation}:o{mode}:a{target_orientation}:"
            f"q{quality}:m{max_bytes}"
        ),
    }


def _display_capability_with_headers(
    capability=None,
    *,
    width=None,
    height=None,
    orientation=None,
):
    """Apply an optional display negotiation without creating an aspect mismatch.

    A width and height header are a pair.  When only an orientation hint is
    supplied, an existing capability is transposed when necessary so the
    declared orientation describes the actual encoded frame.  This keeps a
    portrait PhotoFrame request from being labelled portrait while still
    returning an 800x480 landscape JPEG.
    """

    result = dict(capability or {})
    supplied_width = width not in (None, "")
    supplied_height = height not in (None, "")
    if supplied_width != supplied_height:
        raise DisplayPolicyError("display width and height must be provided together")
    if supplied_width:
        try:
            parsed_width, parsed_height = int(width), int(height)
        except (TypeError, ValueError) as exc:
            raise DisplayPolicyError("display dimensions must be integers") from exc
        if not 1 <= parsed_width <= 4096 or not 1 <= parsed_height <= 4096:
            raise DisplayPolicyError("display dimensions are out of range")
        result["width"], result["height"] = parsed_width, parsed_height
        # Width/height headers are a complete capability override. Do not
        # carry an orientation label from the registered capability when a
        # device switches between landscape and portrait. A square request
        # has no public orientation label, so remove any stale one.
        inferred = orientation_for_size((parsed_width, parsed_height))
        if inferred == "square":
            result.pop("orientation", None)
        else:
            result["orientation"] = inferred

    requested_value = None if orientation in (None, "") else str(orientation).strip().lower()
    requested = None if requested_value == "auto" else requested_value
    if requested_value == "auto" and not supplied_width:
        # An explicit ``auto`` request still asks the server to normalize the
        # registered capability. This repairs stale orientation labels from
        # old registry entries without changing the dimensions.
        try:
            inferred = orientation_for_size((int(result["width"]), int(result["height"])))
        except (KeyError, TypeError, ValueError) as exc:
            raise DisplayPolicyError("display orientation requires known dimensions") from exc
        if inferred == "square":
            result.pop("orientation", None)
        else:
            result["orientation"] = inferred
    if requested is not None:
        if requested not in {"landscape", "portrait"}:
            raise DisplayPolicyError("display orientation must be landscape or portrait")
        try:
            current = orientation_for_size((int(result["width"]), int(result["height"])))
        except (KeyError, TypeError, ValueError) as exc:
            raise DisplayPolicyError("display orientation requires known dimensions") from exc
        if current == "square":
            raise DisplayPolicyError("display orientation is undefined for a square display")
        if current != requested:
            if supplied_width:
                raise DisplayPolicyError("display orientation does not match width and height")
            result["width"], result["height"] = int(result["height"]), int(result["width"])
        result["orientation"] = requested
    return result


def _epaper_options(config, capability=None):
    """Return the E6 rendering variant shared by manifest and content.

    E6 always has a fixed 800x480 wire frame.  Its policy is intentionally
    read from ``config.epaper``; touchscreen settings must not invalidate an
    E6 conditional response or change a physical refresh.
    """

    epaper = config.get("epaper", {})
    # E6 devices advertise a fixed wire contract (800x480/e6).  Their
    # handshake capability is deliberately not allowed to override the
    # server-wide E6 rendering policy; otherwise the default ``rotation=0``
    # inserted by device migration would mask a configured epaper rotation.
    try:
        rotation = int(epaper.get("rotation", 0))
    except (TypeError, ValueError) as exc:
        raise DisplayPolicyError("epaper.rotation must be 0, 90, 180, or 270") from exc
    if rotation not in {0, 90, 180, 270}:
        raise DisplayPolicyError("epaper.rotation must be 0, 90, 180, or 270")
    mode = validate_orientation_mode(epaper.get("orientation_mode", "auto"))
    # The E6 wire protocol is always 800x480.  A remote declaration cannot
    # turn that into a portrait byte stream; physical portrait mounting needs
    # a confirmed panel/controller mapping.  Keep the render target aligned
    # with the actual protocol shape and use the explicit rotation setting for
    # any supported physical correction.
    target = orientation_for_size((EPAPER_WIDTH, EPAPER_HEIGHT))
    dither = bool(epaper.get("e6_dither", True))
    return {
        "rotation": rotation,
        "orientation_mode": mode,
        "target_orientation": target,
        "dither": dither,
        "variant": f"{EPAPER_WIDTH}x{EPAPER_HEIGHT}:r{rotation}:o{mode}:a{target}:d{int(dither)}",
    }


def _content_config_revision(profile, config, *, policy_scoped: bool = False):
    """Return the revision input that can actually change response bytes."""

    # E6 and PhotoFrame render variants encode every input that can affect
    # their bytes. A global configuration revision includes unrelated
    # touchscreen changes, which must not force an expensive e-paper refresh
    # or a needless bounded-JPEG transfer. Generic JPEG remains coupled to
    # ``device.jpeg_quality`` and the local display configuration.
    return 0 if profile == "e6" or policy_scoped else int(config["revision"])


def _photoframe_options(config, policy, capability, selector, row):
    """Normalize a PhotoFrame variant for manifest and URL Rotation content."""

    policy = validate_policy(policy or DEFAULT_PHOTOFRAME_POLICY)
    capability = dict(capability or {})
    profile_id = capability.get("profile_id")
    if not profile_id:
        raise DisplayPolicyError(PROFILE_REQUIRED_MESSAGE)
    try:
        capability = validate_photo_frame_capability(profile_id, capability)
    except DeviceError as exc:
        raise DisplayPolicyError(str(exc)) from exc
    # Product profiles do not expose a mounting-angle knob.  Reject a stale
    # policy rather than silently rotating a frame that was registered with
    # the two-value orientation contract.
    if int(policy.get("rotation", 0)) != 0:
        raise DisplayPolicyError(
            "photoframe rotation is not supported; use landscape or portrait orientation"
        )
    try:
        width = int(capability.get("width", policy["width"]))
        height = int(capability.get("height", policy["height"]))
        max_bytes = min(
            int(policy["max_bytes"]),
            int(capability.get("max_bytes", 2 * 1024 * 1024)),
        )
        rotation = 0
    except (TypeError, ValueError) as exc:
        raise DisplayPolicyError("invalid PhotoFrame display capability") from exc
    if not 1 <= width <= 4096 or not 1 <= height <= 4096:
        raise DisplayPolicyError("PhotoFrame display dimensions are out of range")
    if max_bytes < 4096:
        raise DisplayPolicyError("PhotoFrame max_bytes is out of range")
    target = normalize_display_orientation(capability.get("orientation"), (width, height))
    selection = int(_row_value(row, "selection_revision", _selector_revision(selector)))
    weather_revision = _weather_etag_value(selector)
    overlay_time = _overlay_time_etag_value(selector, config.get("timezone", ""), policy["overlay_date"])
    variant = "{width}x{height}:r{rotation}:o{orientation_mode}:a{target}:m{max_bytes}:p{revision}:c{crop}:d{date}:w{weather}:z{overlay_time}:s{selection}:t".format(
        width=width,
        height=height,
        rotation=rotation,
        orientation_mode=policy["orientation_mode"],
        target=target,
        max_bytes=max_bytes,
        revision=policy["policy_revision"],
        crop=policy["crop_mode"],
        date=int(policy["overlay_date"]),
        weather=f"{int(policy['overlay_weather'])}:{weather_revision}",
        overlay_time=overlay_time,
        selection=selection,
    )
    effective_policy = dict(policy, width=width, height=height, max_bytes=max_bytes, rotation=rotation)
    return {
        "policy": effective_policy,
        "width": width,
        "height": height,
        "max_bytes": max_bytes,
        "rotation": rotation,
        "target_orientation": target,
        "selection_revision": selection,
        "variant": variant,
    }


def _jpeg_bytes(
    path: Path,
    width: int,
    height: int,
    quality: int,
    rotation: int = 0,
    orientation_mode: str = "auto",
    target_orientation: Optional[str] = None,
):
    with Image.open(path) as source:
        hint_jpeg_decode(source, (max(1, width), max(1, height)))
        image = orient_image(
            source,
            (width, height),
            mode=orientation_mode,
            rotation=rotation,
            target_orientation=target_orientation,
        )
        image.thumbnail((max(1, width), max(1, height)), Image.Resampling.LANCZOS)
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=quality, optimize=False)
        return output.getvalue(), image.width, image.height


def _bounded_jpeg(
    path: Path,
    width: int,
    height: int,
    quality: int,
    max_bytes: int,
    rotation: int = 0,
    orientation_mode: str = "auto",
    target_orientation: Optional[str] = None,
):
    width, height = max(1, width), max(1, height)
    for _ in range(6):
        for candidate_quality in range(quality, 14, -12):
            body, actual_width, actual_height = _jpeg_bytes(
                path,
                width,
                height,
                candidate_quality,
                rotation,
                orientation_mode,
                target_orientation,
            )
            if len(body) <= max_bytes:
                return body, actual_width, actual_height
        width, height = max(1, int(width * 0.8)), max(1, int(height * 0.8))
    raise ValueError("photo cannot fit device max_bytes capability")


def _jpeg_to_bmp(jpeg: bytes, *, width: int = 800, height: int = 480) -> bytes:
    """Convert a rendered JPEG to an in-memory 24-bit BMP for ``/dataUP``."""

    if not isinstance(jpeg, (bytes, bytearray)) or not jpeg:
        raise PushError("rendered JPEG is empty")
    if width != 800 or height != 480:
        raise PushError("waveshare_dataup BMP dimensions must be 800x480")
    with Image.open(io.BytesIO(bytes(jpeg))) as image:
        output = io.BytesIO()
        converted = image.convert("RGB")
        if converted.size != (width, height):
            converted = converted.resize((width, height), Image.Resampling.LANCZOS)
        converted.save(output, format="BMP")
        return output.getvalue()


def create_app(touchscreen=False):
    from fastapi import FastAPI, Header, HTTPException, Query, Request
    from fastapi.responses import FileResponse, Response
    from fastapi.staticfiles import StaticFiles
    from starlette.concurrency import run_in_threadpool

    api = FastAPI(title="Ascend Smart Album Server", version="2.0.0")

    async def json_object(request: Request):
        """Decode a JSON object and turn malformed bodies into a client error."""

        try:
            value = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail="request body must be valid JSON") from exc
        if not isinstance(value, dict):
            raise HTTPException(status_code=400, detail="request body must be a JSON object")
        return value

    def managed_photoframe_pull_url(request: Request, device_id: str, *, strict: bool = False) -> str:
        """Return the LAN URL a managed PhotoFrame must poll for its image."""

        configured_origin = os.environ.get("SMART_ALBUM_PUBLIC_URL", "").strip().rstrip("/")
        candidate = configured_origin or str(request.base_url).rstrip("/")
        origin = None
        try:
            parsed_origin = urlsplit(candidate)
            if parsed_origin.scheme not in {"http", "https"} or not parsed_origin.hostname:
                raise ValueError("origin must use http or https")
            if parsed_origin.username or parsed_origin.password or parsed_origin.path not in {"", "/"}:
                raise ValueError("origin must be a root URL without credentials or a path")
            if parsed_origin.query or parsed_origin.fragment:
                raise ValueError("origin must not contain query or fragment")
            public_ip = ipaddress.ip_address(parsed_origin.hostname)
            if public_ip.version != 4 or not any(public_ip in network for network in PRIVATE_V4_NETWORKS):
                raise ValueError("origin must use a private IPv4 address")
            port = parsed_origin.port
            if port is not None and not 1 <= port <= 65535:
                raise ValueError("origin port is invalid")
            host = str(public_ip)
            netloc = host if port is None else f"{host}:{port}"
            origin = urlunsplit((parsed_origin.scheme.lower(), netloc, "", "", ""))
        except (ValueError, TypeError):
            # A management page opened via a test hostname or mDNS name can
            # still display a relative endpoint.  A URL written into an
            # ESP32, however, must be an explicit routable LAN origin.
            if strict:
                raise HTTPException(
                    status_code=503,
                    detail="cannot determine a trusted LAN server URL; set SMART_ALBUM_PUBLIC_URL to the board IPv4 origin",
                )
            return f"/api/devices/{quote(str(device_id), safe='')}/photoframe"
        return f"{origin}/api/devices/{quote(str(device_id), safe='')}/photoframe"

    @api.middleware("http")
    async def no_cache_touchscreen_assets(request: Request, call_next):
        response = await call_next(request)
        if request.url.path in {"/", "/index.html", "/style.css", "/app.js"}:
            response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
        return response

    @api.get("/api/health")
    def api_health():
        return health_payload()

    @api.get("/api/models")
    def api_models():
        return models_payload()

    @api.get("/api/device-profiles")
    def api_device_profiles():
        """Return the fixed ESP32 PhotoFrame hardware contracts."""

        return {
            "profiles": [
                {
                    "profile_id": profile_id,
                    **profile,
                    "orientations": list(profile["orientations"]),
                    "codecs": list(profile["codecs"]),
                    "colors": list(profile["colors"]),
                    "rotation_degrees": list(profile["rotation_degrees"]),
                }
                for profile_id, profile in PHOTOFRAME_PROFILES.items()
            ]
        }

    @api.get("/api/index/stats")
    def api_index_stats():
        return get_state().index.stats()

    @api.get("/api/admin/status")
    def api_admin_status():
        return {"configured": False, "mode": "lan-open", "warning": "管理接口仅建议在可信局域网使用"}

    @api.post("/api/search/text")
    def api_search_text(request: TextSearchRequest):
        try:
            model_id = resolve_text_model(request.query, request.model)
            results = get_state().index.search_text(request.query, model_id, int(request.top_k))
            return {
                "model_id": model_id,
                "results": [
                    {
                        "photo_id": result.photo_id,
                        "filename": result.filename,
                        "face_count": result.face_count,
                        "score": result.score,
                        "model_id": result.model_id,
                        "url": _photo_preview_url(result.photo_id),
                        "preview_url": _photo_preview_url(result.photo_id),
                        "file_url": f"/api/photos/{result.photo_id}/file",
                    }
                    for result in results
                ],
            }
        except (AlbumIndexError, EmbeddingError, RegistryError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.get("/api/photos")
    def api_photos(face_filter: str = "all", limit: int = 100):
        try:
            rows = get_state().index.list_photos(face_filter=face_filter, limit=max(1, min(limit, 1000)))
            return {
                "photos": [
                    dict(
                        _safe_photo(row),
                        url=_photo_preview_url(row["id"]),
                        preview_url=_photo_preview_url(row["id"]),
                        file_url=f"/api/photos/{row['id']}/file",
                    )
                    for row in rows
                ]
            }
        except AlbumIndexError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.get("/api/photos/{photo_id}/preview")
    def api_photo_preview(request: Request, photo_id: int, width: int = 480, height: int = 360):
        """Render a browser-safe JPEG preview without creating a disk cache.

        Several camera imports are MPO containers.  Serving those originals
        as ``image/mpo`` with the download-oriented file endpoint is not
        reliable for Firefox or embedded touchscreen browsers.  This endpoint
        performs a bounded, in-memory conversion and always returns an inline
        standard JPEG; the original bytes remain available from ``/file``.
        """

        row = get_state().index.get_photo(photo_id)
        if row is None:
            raise HTTPException(status_code=404, detail="photo not found")
        try:
            width, height = int(width), int(height)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="preview width and height must be integers") from exc
        if not (32 <= width <= 1600 and 32 <= height <= 1200):
            raise HTTPException(status_code=400, detail="preview width must be 32..1600 and height 32..1200")
        # The content is derived only from the source digest and requested
        # size.  Check the conditional request before decoding the high-
        # resolution source so repeated gallery opens stay cheap.
        digest = str(_row_value(row, "sha256", ""))
        if not digest:
            digest = f"{_row_value(row, 'updated_at', '')}:{_row_value(row, 'size_bytes', '')}"
        etag = '"preview-' + hashlib.sha256(f"{digest}:{width}x{height}".encode("utf-8")).hexdigest()[:24] + '"'
        common_headers = {
            "ETag": etag,
            "Cache-Control": "private, max-age=60, must-revalidate",
            "Vary": "Accept",
        }
        if request.headers.get("if-none-match", "").strip() == etag:
            return Response(status_code=304, headers=common_headers)
        try:
            body, actual_width, actual_height = _bounded_jpeg(
                Path(row["filepath"]),
                width,
                height,
                82,
                2 * 1024 * 1024,
                0,
                "auto",
                orientation_for_size((width, height)),
            )
        except (OSError, ValueError, DisplayPolicyError) as exc:
            raise HTTPException(status_code=400, detail=f"photo preview failed: {exc}") from exc
        common_headers.update(
            {
                "Content-Disposition": "inline",
                "X-Album-Preview": "1",
                "X-Album-Width": str(actual_width),
                "X-Album-Height": str(actual_height),
            }
        )
        return Response(
            content=body,
            media_type="image/jpeg",
            headers=common_headers,
        )

    @api.get("/api/photos/{photo_id}/file")
    def api_photo_file(photo_id: int):
        row = get_state().index.get_photo(photo_id)
        if row is None:
            raise HTTPException(status_code=404, detail="photo not found")
        return FileResponse(row["filepath"], media_type=row["mime_type"] or "application/octet-stream", filename=row["filename"])

    @api.post("/api/photos/upload", status_code=202)
    async def api_upload(request: Request):
        """Stage a multipart upload without an application file/count cap.

        FastAPI's ``File(...)`` dependency uses Starlette's default
        ``max_files=1000`` parser limit.  Parse the form here with a very high
        transport ceiling so folder uploads are not rejected at an arbitrary
        count; actual resource use remains bounded by disk, decoding safety,
        and the single indexing worker.
        """
        temp_paths = []
        upload_dir = _upload_staging_dir()
        upload_dir.mkdir(parents=True, exist_ok=True)
        try:
            async with request.form(max_files=2_147_483_647, max_fields=2_147_483_647) as form:
                files = [item for item in form.getlist("files") if hasattr(item, "read") and hasattr(item, "filename")]
                if not files:
                    raise HTTPException(status_code=400, detail="at least one image file is required")
                capture_time = form.get("capture_time") or None
                for upload in files:
                    suffix = Path(upload.filename or "photo.jpg").suffix.lower()
                    if suffix not in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
                        raise HTTPException(status_code=400, detail=f"unsupported image extension: {suffix}")
                    fd, name = tempfile.mkstemp(prefix="upload-", suffix=suffix, dir=str(upload_dir))
                    temp_paths.append(name)
                    try:
                        with os.fdopen(fd, "wb") as handle:
                            while True:
                                chunk = await upload.read(1024 * 1024)
                                if not chunk:
                                    break
                                handle.write(chunk)
                    finally:
                        await upload.close()
                # Uploads do not accept user-authored semantic labels. Existing
                # dataset metadata remains readable, while normal albums rely
                # on EXIF time and CLIP retrieval.
                return get_state().submit_upload(temp_paths, capture_time=capture_time)
        except HTTPException:
            for name in temp_paths:
                Path(name).unlink(missing_ok=True)
            raise
        except Exception:
            for name in temp_paths:
                Path(name).unlink(missing_ok=True)
            raise

    @api.get("/api/jobs/{job_id}")
    def api_job(job_id: str):
        with get_state().jobs_lock:
            job = get_state().jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="job not found")
        return job

    @api.delete("/api/photos/{photo_id}")
    def api_delete_photo(photo_id: int, confirm: bool = False):
        try:
            result = get_state().index.delete_photo(photo_id, confirmed=confirm)
            selector = get_state().selector
            with _selector_state_guard(selector):
                if selector.current and int(selector.current["id"]) == int(photo_id):
                    selector.current = None
                selector.revision += 1
                _invalidate_local_preselection(get_state())
            return result
        except AlbumIndexError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.get("/api/config")
    def api_config():
        return get_state().config.get()

    @api.patch("/api/config")
    async def api_config_update(request: Request):
        payload = await request.json()
        expected = payload.pop("revision", None)
        try:
            requested_models = (payload.get("index") or {}).get("models")
            if requested_models is not None:
                unknown = sorted(set(requested_models) - set(get_state().registry.ids()))
                if unknown:
                    raise ConfigError(f"models are not admitted: {', '.join(unknown)}")
            value = get_state().config.update(payload, expected_revision=expected)
            # Direction, JPEG and E6 transport changes alter rendering only.
            # They must not invalidate a stable selection (and in particular
            # must not turn an E6 conditional GET into a physical refresh).
            if any(key in payload for key in ("timezone", "selection", "weather")):
                with _selector_state_guard(get_state().selector):
                    get_state().selector.revision += 1
                    _invalidate_local_preselection(get_state())
            return dict(value, restart_required="epaper" in payload)
        except ConfigError as exc:
            raise HTTPException(status_code=409 if "conflict" in str(exc) else 400, detail=str(exc)) from exc

    @api.get("/api/display/current")
    def api_display_current(request: Request, profile: str = "jpeg"):
        if profile not in {"jpeg", "e6"}:
            raise HTTPException(status_code=400, detail="profile must be jpeg or e6")
        selector = get_state().selector
        with _selector_state_guard(selector):
            saved = get_state().index.get_display_state("local") if hasattr(get_state().index, "get_display_state") else None
            row = get_state().index.get_photo(int(saved["photo_id"])) if saved and saved.get("photo_id") else None
            _record_local_history(get_state().index, get_state().config, row, seed_only=True)
            row = row or selector.current_photo(profile)
            selection_meta = {}
            if isinstance(getattr(selector, "current", None), dict) and row is not None:
                try:
                    same_photo = int(selector.current.get("id")) == int(_row_value(row, "id"))
                except (TypeError, ValueError):
                    same_photo = False
                if same_photo:
                    selection_meta = {
                        key: selector.current[key]
                        for key in ("selection_source", "semantic_score", "prompt")
                        if key in selector.current
                    }
            selection_revision = int(_row_value(row, "selection_revision", _selector_revision(selector))) if row else None
        if row is None:
            return {
                "current": None,
                "display": {
                    "enabled": _touchscreen_enabled(get_state().config.get()),
                    "mode": "touchscreen" if touchscreen else "server",
                    "touchscreen_interval_seconds": _touchscreen_interval_seconds(get_state().config.get()),
                    "remote_refresh_seconds": _remote_refresh_seconds(get_state().config.get()),
                },
            }
        _record_local_history(get_state().index, get_state().config, row, seed_only=True)
        config = get_state().config.get()
        content_url = f"/api/display/content?profile={profile}"
        if profile == "e6":
            try:
                e6_options = _epaper_options(config)
            except (DisplayPolicyError, ValueError, TypeError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            etag = _etag(
                row,
                f"e6:local:{e6_options['variant']}",
                _content_config_revision(profile, config),
                selection_revision,
            )
        else:
            try:
                # Use exactly the same negotiated variant as
                # ``/api/display/content``.  The frontend passes its actual
                # viewport here, so the metadata ETag and the following image
                # request describe one representation rather than two.
                options = _render_options(request.query_params, _render_defaults(config))
            except DisplayPolicyError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            etag = _etag(
                row,
                f"jpeg:local:{options['variant']}",
                _content_config_revision(profile, config),
                selection_revision,
            )
            content_query = {
                "profile": "jpeg",
                "width": options["width"],
                "height": options["height"],
                "max_bytes": options["max_bytes"],
                "quality": options["quality"],
                "rotation": options["rotation"],
                "orientation_mode": options["orientation_mode"],
            }
            # ``square`` is inferred from dimensions.  It is not an explicit
            # device orientation contract, which only accepts landscape or
            # portrait, so do not turn a valid square viewport into a 400 on
            # its follow-up content request.
            if options["target_orientation"] != "square":
                content_query["orientation"] = options["target_orientation"]
            content_url = "/api/display/content?" + urlencode(content_query)
        return {
            "current": dict(
                _safe_photo(row),
                **selection_meta,
                url=content_url,
                etag=etag,
                profile=profile,
                selection_revision=selection_revision,
            ),
            "display": {
                "enabled": _touchscreen_enabled(config),
                "mode": "touchscreen" if touchscreen else "server",
                "touchscreen_interval_seconds": _touchscreen_interval_seconds(config),
                "remote_refresh_seconds": _remote_refresh_seconds(config),
            },
        }

    @api.post("/api/display/refresh")
    def api_display_refresh():
        # Explicit refresh controls the local touchscreen selection.  The
        # physical e-paper output is refreshed by its independent slow clock.
        row = _refresh_local_selection(get_state(), refresh_weather=True, render_epaper=False)
        return {
            "current": _safe_photo(row) if row else None,
            "status": _public_selector_status(get_state().selector.status()),
            "selection": _selection_evidence(get_state(), row),
        }

    @api.post("/api/display/select")
    async def api_display_select(request: Request):
        payload = await request.json()
        try:
            photo_id = int(payload.get("photo_id"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="photo_id must be an integer") from exc
        row = get_state().index.get_photo(photo_id)
        if row is None:
            raise HTTPException(status_code=404, detail="photo is not available")
        selector = get_state().selector
        with _selector_state_guard(selector):
            previous_state = get_state().index.get_display_state("local") if hasattr(get_state().index, "get_display_state") else None
            previous = get_state().index.get_photo(int(previous_state["photo_id"])) if previous_state and previous_state.get("photo_id") else None
            _rewind_local_history(get_state().index, previous)
            selected = dict(row, selection_revision=selector.revision + 1)
            selector.current = selected
            selector.revision += 1
            _invalidate_local_preselection(get_state())
            if hasattr(get_state().index, "save_display_state"):
                get_state().index.save_display_state("local", photo_id, "manual", 1, selector.revision)
            _record_local_history(get_state().index, get_state().config, selected)
        return {
            "current": _safe_photo(selected),
            "status": _public_selector_status(get_state().selector.status()),
            "selection": _selection_evidence(get_state(), selected),
        }

    @api.post("/api/display/control")
    async def api_display_control(request: Request):
        payload = await request.json()
        action = str(payload.get("action", "")).lower()
        if action == "next":
            # Keep the NPU-backed selection work off Uvicorn's event loop.
            # Normally this consumes the scheduler's precomputed semantic
            # candidate; a cold queue performs one cached-weather NPU query.
            row = await run_in_threadpool(_advance_local_selection, get_state())
        elif action == "previous":
            state = get_state()
            with _selector_state_guard(state.selector):
                limit = int(state.config.get()["display"].get("repeat_window", 12))
                history = state.index.display_history_ids("local", max(2, limit)) if hasattr(state.index, "display_history_ids") else []
                saved = state.index.get_display_state("local") if hasattr(state.index, "get_display_state") else None
                current_id = int(saved["photo_id"]) if saved and saved.get("photo_id") else None
                if current_id is None and isinstance(getattr(state.selector, "current", None), dict):
                    current_id = int(state.selector.current.get("id")) if state.selector.current.get("id") is not None else None
                if current_id not in history:
                    memory_history = list(reversed(getattr(state.selector, "history", [])))
                    if current_id in memory_history:
                        history = memory_history
                elif hasattr(state.index, "rewind_display_history"):
                    state.index.rewind_display_history("local", current_id)
                    history = state.index.display_history_ids("local", max(2, limit))
                if current_id not in history:
                    raise HTTPException(status_code=409, detail="没有上一张照片")
                previous_index = history.index(current_id) + 1
                if previous_index >= len(history):
                    raise HTTPException(status_code=409, detail="没有上一张照片")
                row = state.index.get_photo(history[previous_index])
                if row is None:
                    raise HTTPException(status_code=404, detail="上一张照片不可用")
                if hasattr(state.index, "pop_display_history"):
                    state.index.pop_display_history("local", expected_photo_id=current_id)
                state.selector.current = dict(row, selection_revision=state.selector.revision)
                if hasattr(state.index, "save_display_state"):
                    state.index.save_display_state("local", int(row["id"]), "manual", 1, state.selector.revision)
                _invalidate_local_preselection(state)
        elif action in {"pause", "resume"}:
            enabled = action == "resume"
            current = get_state().config.get()
            value = get_state().config.update({"display": {"enabled": enabled}}, expected_revision=current["revision"])
            saved = get_state().index.get_display_state("local") if hasattr(get_state().index, "get_display_state") else None
            row = get_state().index.get_photo(int(saved["photo_id"])) if saved and saved.get("photo_id") else get_state().selector.current_photo("jpeg")
            return {"current": _safe_photo(row) if row else None, "display": {"enabled": enabled}, "config_revision": value["revision"]}
        else:
            raise HTTPException(status_code=400, detail="action must be next, previous, pause, or resume")
        return {
            "current": _safe_photo(row) if row else None,
            "display": {"enabled": _touchscreen_enabled(get_state().config.get())},
            "status": _public_selector_status(get_state().selector.status()),
            "selection": _selection_evidence(get_state(), row),
        }

    @api.get("/api/display/status")
    def api_display_status():
        config = get_state().config.get()
        return {
            "backend": get_state().epaper.config.backend,
            "enabled": _touchscreen_enabled(config),
            "display_master_enabled": bool(config["display"].get("enabled", True)),
            "touchscreen_interval_seconds": _touchscreen_interval_seconds(config),
            "remote_refresh_seconds": _remote_refresh_seconds(config),
            "epaper_rotation_interval_seconds": _epaper_interval_seconds(config),
            **_public_selector_status(get_state().selector.status()),
        }

    @api.get("/api/display/content")
    def api_display_content(request: Request, profile: str = "jpeg", if_none_match: Optional[str] = Header(None)):
        if profile not in {"jpeg", "e6"}:
            raise HTTPException(status_code=400, detail="profile must be jpeg or e6")
        return content_response(request, profile, if_none_match)

    def device_auth(device_id, token=None):
        try:
            if str(device_id) == LOCAL_TOUCHSCREEN_ID:
                raise DeviceError("local touchscreen is managed at /api/admin/touchscreen")
            device = get_state().devices.get(device_id)
            if not device.get("enabled", True):
                raise DeviceError("device is disabled")
            return device
        except DeviceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def device_token(x_device_token, authorization):
        """Accept the token mechanisms supported by PhotoFrame URL rotation."""
        if x_device_token:
            return x_device_token
        if authorization and authorization.lower().startswith("bearer "):
            return authorization[7:].strip()
        return None

    def selected_device_photo(device_id, device):
        selector = get_state().selector
        if device.get("display", {}).get("kind") == "photoframe" and hasattr(selector, "current_for_device"):
            return selector.current_for_device(device_id, device.get("policy") or DEFAULT_PHOTOFRAME_POLICY)
        return selector.current_photo("e6" if device.get("display", {}).get("kind") == "epaper" else "jpeg")

    @api.get("/api/devices")
    def api_devices():
        return {"devices": get_state().devices.list()}

    @api.get("/api/admin/devices")
    def api_admin_devices(request: Request):
        # ``/api/devices`` retains its remote-protocol-only contract.  The
        # management view additionally exposes the local HDMI panel as a
        # virtual device, without registering it for device auth or push.
        devices = []
        for device in get_state().devices.list():
            value = dict(device)
            if value.get("display", {}).get("kind") == "photoframe" and value.get("device_id"):
                value["delivery_mode"] = "device_pull"
                value["pull_url"] = managed_photoframe_pull_url(request, value["device_id"])
            devices.append(value)
        return {"devices": [_local_touchscreen_state(get_state()), *devices]}

    @api.get("/api/admin/touchscreen")
    def api_admin_touchscreen():
        """Read the separately configurable local HDMI touchscreen."""

        return _local_touchscreen_payload(get_state())

    @api.patch("/api/admin/touchscreen")
    async def api_admin_touchscreen_update(request: Request):
        """Update local panel settings without changing ESP32 devices."""

        payload = await json_object(request)
        state = get_state()
        expected = payload.get("revision")
        try:
            config_patch = _touchscreen_config_patch(payload)
            display = payload.get("display") or {}
            metadata_requested = "name" in payload or any(
                key in display for key in ("width", "height", "rotation", "orientation_mode")
            ) or "enabled" in payload
            if not config_patch and not metadata_requested:
                raise ConfigError("touchscreen settings are empty")
            config = (
                state.config.update(config_patch, expected_revision=expected)
                if config_patch
                else state.config.get()
            )
            if not config_patch and expected is not None and int(expected) != int(config["revision"]):
                raise ConfigError("configuration revision conflict")
            # Device identity/capability lives in the registry, while actual
            # rendering and scheduler behavior follows ConfigStore.  Accept a
            # small nested display patch for the physical panel dimensions.
            capability = {
                key: display[key]
                for key in ("width", "height", "rotation", "orientation_mode")
                if key in display
            }
            for key in ("rotation", "orientation_mode"):
                if key in payload:
                    capability[key] = payload[key]
            if "name" in payload or capability or "enabled" in payload:
                registry_patch = {}
                if "name" in payload:
                    registry_patch["name"] = payload["name"]
                if "enabled" in payload:
                    registry_patch["enabled"] = payload["enabled"]
                if capability:
                    registry_patch["display"] = capability
                updater = getattr(state.devices, "update_local_touchscreen", None)
                if callable(updater):
                    updater(registry_patch)
            # Changes that affect semantic selection use the same revision
            # boundary as the general configuration endpoint.
            if any(key in config_patch.get("display", {}) for key in ("repeat_window",)):
                with _selector_state_guard(state.selector):
                    state.selector.revision += 1
                    _invalidate_local_preselection(state)
            result = _local_touchscreen_payload(state)
            result["config"]["revision"] = config["revision"]
            return result
        except (ConfigError, DeviceError) as exc:
            raise HTTPException(status_code=409 if "conflict" in str(exc) else 400, detail=str(exc)) from exc

    @api.post("/api/admin/touchscreen/advance")
    async def api_admin_touchscreen_advance(request: Request):
        """Advance/pause/resume the local display through the device view."""

        payload = await json_object(request)
        action = str(payload.get("action", "next")).lower()
        if action not in {"next", "previous", "pause", "resume"}:
            raise HTTPException(status_code=400, detail="action must be next, previous, pause, or resume")
        # Reuse the established display control implementation so the durable
        # local history and scheduler state remain exactly compatible.
        if action == "next":
            row = await run_in_threadpool(_advance_local_selection, get_state())
            result = {"current": _safe_photo(row) if row else None}
        elif action in {"pause", "resume"}:
            state = get_state()
            current = state.config.get()
            state.config.update(
                {"display": {"touchscreen_enabled": action == "resume"}},
                expected_revision=current["revision"],
            )
            result = {"current": _local_touchscreen_state(state)["current"]}
        else:
            # Delegate the history-sensitive previous logic to the existing
            # display control endpoint's implementation through its shared
            # state primitives, avoiding a second inconsistent history stack.
            state = get_state()
            with _selector_state_guard(state.selector):
                limit = int(state.config.get()["display"].get("repeat_window", 12))
                history = state.index.display_history_ids("local", max(2, limit)) if hasattr(state.index, "display_history_ids") else []
                saved = state.index.get_display_state("local") if hasattr(state.index, "get_display_state") else None
                current_id = int(saved["photo_id"]) if saved and saved.get("photo_id") else None
                if current_id not in history:
                    memory_history = list(reversed(getattr(state.selector, "history", [])))
                    if current_id in memory_history:
                        history = memory_history
                elif hasattr(state.index, "rewind_display_history"):
                    state.index.rewind_display_history("local", current_id)
                    history = state.index.display_history_ids("local", max(2, limit))
                if current_id not in history or history.index(current_id) + 1 >= len(history):
                    raise HTTPException(status_code=409, detail="没有上一张照片")
                row = state.index.get_photo(history[history.index(current_id) + 1])
                if row is None:
                    raise HTTPException(status_code=404, detail="上一张照片不可用")
                if hasattr(state.index, "pop_display_history"):
                    state.index.pop_display_history("local", expected_photo_id=current_id)
                state.selector.current = dict(row, selection_revision=state.selector.revision)
                if hasattr(state.index, "save_display_state"):
                    state.index.save_display_state("local", int(row["id"]), "manual", 1, state.selector.revision)
                _invalidate_local_preselection(state)
            result = {"current": _safe_photo(row)}
        payload = _local_touchscreen_payload(get_state())
        # A lightweight integration harness may implement ``refresh_display``
        # without writing the durable state row.  The production implementation
        # always writes it, but retain the just-selected result in the response
        # so this management endpoint is truthful in both cases.
        if payload["state"]["current"] is None and result.get("current"):
            payload["state"]["current"] = {
                "photo_id": int(result["current"]["id"]),
                "filename": str(result["current"].get("filename", "")),
            }
        result["selection"] = _selection_evidence(get_state(), result.get("current"))
        result.update(payload)
        return result

    def _managed_device_spec(payload: dict):
        """Validate a managed PhotoFrame registration without changing state.

        Both the legacy pending-registration endpoint and the atomic pairing
        endpoint use this parser.  Keeping capability validation in one place
        prevents an atomic registration from accepting a contract that the
        normal content route would later reject.
        """

        requested_kind = str(payload.get("kind", payload.get("device_type", "photoframe"))).lower()
        if requested_kind != "photoframe":
            raise HTTPException(
                status_code=400,
                detail="only photoframe can be registered here; configure the local touchscreen at /api/admin/touchscreen",
            )
        requested_transport = str(
            payload.get("transport", payload.get("delivery_mode", "device_pull"))
        ).strip().lower()
        if requested_transport not in {"device_pull", "pull"}:
            raise HTTPException(
                status_code=400,
                detail="ESP32 registration supports only device_pull; the device must poll the returned URL",
            )
        if payload.get("push") is not None:
            raise HTTPException(
                status_code=400,
                detail="ESP32 registration supports only delivery_mode=device_pull; server push is not configured; register first, then use the device pull URL",
            )
        raw_display = payload.get("display") or {}
        if not isinstance(raw_display, dict):
            raise HTTPException(status_code=400, detail="display must be a JSON object")
        display = dict(raw_display)
        top_profile_id = payload.get("profile_id")
        embedded_profile_id = display.get("profile_id")
        if top_profile_id and embedded_profile_id and str(top_profile_id).strip().lower() != str(embedded_profile_id).strip().lower():
            raise HTTPException(status_code=400, detail="photoframe profile_id conflicts between device and display")
        profile_id = top_profile_id or embedded_profile_id
        if not profile_id:
            raise HTTPException(
                status_code=400,
                detail=(
                    "profile_id is required; choose "
                    "waveshare_photopainter_73 or seeedstudio_reterminal_e1002"
                ),
            )
        try:
            profile = photo_frame_profile(profile_id)
        except DeviceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            requested_orientation = str(display.get("orientation") or "landscape").lower()
            default_width, default_height = (
                (profile["height"], profile["width"])
                if requested_orientation == "portrait"
                else (profile["width"], profile["height"])
            )
            width = int(display.get("width", default_width))
            height = int(display.get("height", default_height))
            if not display.get("orientation"):
                requested_orientation = "portrait" if height > width else "landscape"
            max_bytes = int(display.get("max_bytes", DEFAULT_PHOTOFRAME_POLICY["max_bytes"]))
            rotation = int(display.get("rotation", 0))
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="invalid PhotoFrame display capability") from exc
        if rotation != 0:
            raise HTTPException(status_code=400, detail="photoframe rotation is not supported; choose landscape or portrait")
        # Preserve a portrait device contract (for example 480x800) instead
        # of forcing every newly paired panel into the historical 800x480
        # landscape default.
        display.update(
            kind="photoframe",
            profile_id=profile["profile_id"],
            width=width,
            height=height,
            codecs=list(display.get("codecs") or ["jpeg"]),
            max_bytes=max_bytes,
            rotation=0,
            orientation_mode=display.get("orientation_mode", "auto"),
        )
        if payload.get("policy") is not None:
            try:
                policy = validate_policy(payload["policy"], DEFAULT_PHOTOFRAME_POLICY)
            except (DisplayPolicyError, ValueError, TypeError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            if int(policy.get("rotation", 0)) != 0:
                raise HTTPException(
                    status_code=400,
                    detail="photoframe rotation is not supported; use landscape or portrait orientation",
                )
        return profile, display

    @api.post("/api/admin/devices")
    async def api_admin_device_create(request: Request):
        payload = await json_object(request)
        # A managed PhotoFrame must never be represented by a local-only
        # placeholder.  Without the device URL, /api/system-info identity
        # check, and URL Rotation configuration read-back, the server cannot
        # tell an unreachable or wrong device from a successful registration.
        # Keep this historical route read-only for PhotoFrame callers and
        # direct them to the transactional endpoint, which removes its
        # temporary record on every failed verification path.
        # Run the side-effect-free capability parser first so malformed or
        # incomplete requests retain the precise client error (for example
        # ``profile_id is required``).  A valid PhotoFrame is then rejected
        # before ``DeviceRegistry.handshake`` can create a placeholder.
        _managed_device_spec(payload)
        raise HTTPException(
            status_code=400,
            detail={
                "message": (
                    "PhotoFrame cannot be registered through this pending "
                    "entry point; provide device_url and use "
                    "POST /api/admin/devices/register"
                ),
                "registration_status": "not_registered",
                "next": "/api/admin/devices/register",
            },
        )

    @api.post("/api/admin/devices/register", status_code=202)
    async def api_admin_device_register(request: Request):
        """Verify and configure one official PhotoFrame for device pull.

        A successful response means the supplied ESP32 root was reachable,
        identified as the supported PhotoFrame firmware, and accepted the
        Case7 URL Rotation configuration.  It deliberately does *not* claim
        that the ESP32 has fetched or displayed an image yet: that state is
        established only by a later request carrying the official PhotoFrame
        display-negotiation headers.  If any network, identity, or
        verification step fails before configuration is complete, the
        temporary registry entry is removed so the UI cannot present an
        unreachable device as registered.
        """

        payload = await json_object(request)
        raw_device_url = payload.get("device_url")
        if not isinstance(raw_device_url, str) or not raw_device_url.strip():
            raise HTTPException(
                status_code=400,
                detail="device_url is required; registration verifies the ESP32 before creating a device record",
            )
        trigger_now = payload.get("trigger_now", True)
        if not isinstance(trigger_now, bool):
            raise HTTPException(status_code=400, detail="trigger_now must be boolean")

        # Validate all user-controlled capability and policy fields before
        # creating the temporary record.  This is the first half of the
        # transaction and has no filesystem side effects.
        profile, display = _managed_device_spec(payload)
        try:
            device_url = normalize_device_url(raw_device_url)
        except ProvisionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            policy = validate_policy(payload.get("policy") or DEFAULT_PHOTOFRAME_POLICY)
        except (DisplayPolicyError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if int(policy.get("rotation", 0)) != 0:
            raise HTTPException(
                status_code=400,
                detail="photoframe rotation is not supported; use landscape or portrait orientation",
            )

        # The image URL includes the generated ID, so the record must exist
        # before /api/rotate can cause the ESP32 to fetch it.  It is marked as
        # configuring and removed on every failure path below.
        requested_id = payload.get("device_id")
        if requested_id is not None and str(requested_id).strip():
            raise HTTPException(
                status_code=400,
                detail="device_id is assigned by the server for a new verified registration",
            )
        device_id = secrets.token_hex(8)
        while True:
            try:
                get_state().devices.get(device_id)
            except DeviceError:
                break
            device_id = secrets.token_hex(8)

        # This URL is written into the ESP32.  Do not derive it from an
        # arbitrary Host header; the board must advertise an explicit private
        # LAN origin (the launch script sets SMART_ALBUM_PUBLIC_URL).
        pull_url = managed_photoframe_pull_url(request, device_id, strict=True)
        if urlsplit(device_url).hostname == urlsplit(pull_url).hostname:
            raise HTTPException(status_code=400, detail="device_url must identify the ESP32, not this smart-album server")

        created = False
        try:
            with _PHOTOFRAME_PROVISION_LOCK:
                existing_id = get_state().devices.find_by_pull_url(device_url)
                if existing_id:
                    raise DeviceConflictError(
                        f"device_url is already registered by device {existing_id}",
                        device_id=existing_id,
                    )
                registration = get_state().devices.handshake(
                    {
                        "device_id": device_id,
                        "name": payload.get("name"),
                        "profile_id": profile["profile_id"],
                        "protocol_version": 1,
                        "display": display,
                    },
                    require_token=False,
                )
                created = True
                registration = get_state().devices.update(device_id, {"policy": policy})
                get_state().devices.mark_pull_provision(
                    device_id,
                    "configuring",
                    device_url=device_url,
                    last_error=None,
                    last_http_status=None,
                    rotate_status="not_requested",
                )
                result = PhotoFrameProvisioner().provision(
                    device_url,
                    image_url=pull_url,
                    rotation_cron=list(policy["rotation_cron"]),
                    display_orientation=str(display.get("orientation") or "landscape"),
                    native_size=(int(profile["width"]), int(profile["height"])),
                    expected_profile_id=profile["profile_id"],
                    trigger_now=trigger_now,
                )
                # Configuration is a control-plane result only.  Even when
                # the optional /api/rotate request was skipped or failed, the
                # device still has to make a subsequent URL Rotation GET
                # before it can be considered connected.
                provision_status = "awaiting_pull"
                saved = get_state().devices.mark_pull_provision(
                    device_id,
                    provision_status,
                    device_url=result.device_url,
                    last_error=result.rotate_error,
                    last_http_status=result.rotate_http_status,
                    device_hardware_id=result.device_hardware_id,
                    firmware_version=result.firmware_version,
                    board_name=result.board_name,
                    configured_image_url=result.configured_image_url,
                    rotate_requested_at=time.time() if result.rotate_requested else None,
                    rotate_status=result.rotate_status,
                    successful=True,
                )
        except DeviceConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": str(exc),
                    "registration_status": "already_registered",
                    "device_id": exc.device_id,
                },
            ) from exc
        except ProvisionError as exc:
            if created:
                try:
                    get_state().devices.delete(device_id)
                except DeviceError:
                    pass
            status = "unreachable" if exc.kind in {"transport", "http", "response"} else "rejected"
            response_status = 502 if status == "unreachable" else 400
            raise HTTPException(
                status_code=response_status,
                detail={"message": str(exc), "registration_status": "not_registered"},
            ) from exc
        except (DeviceError, DisplayPolicyError, ValueError, TypeError) as exc:
            if created:
                try:
                    get_state().devices.delete(device_id)
                except DeviceError:
                    pass
            raise HTTPException(status_code=400, detail={"message": str(exc), "registration_status": "not_registered"}) from exc
        except Exception:
            # Preserve an unexpected programming/runtime exception for the
            # server error handler, but never leave a half-registered device
            # behind after the transactional setup has failed.
            if created:
                try:
                    get_state().devices.delete(device_id)
                except Exception:
                    pass
            raise

        device = saved
        device.pop("token", None)
        pull_state = str((device.get("pull_provision") or {}).get("status") or "").lower()
        # ``POST /api/rotate`` can remain busy while the ESP32 downloads and
        # renders an e-paper image.  A client-side timeout is therefore not
        # proof that the request was rejected.  The later GET from the ESP32
        # is the only connection evidence and may already have arrived while
        # this registration request was being finalized.
        immediate_refresh = "requested" if result.rotate_status == "requested" else (
            "not_requested" if result.rotate_status == "not_requested" else
            "unconfirmed" if result.rotate_status in {"timed_out", "timeout"} else "failed"
        )
        if pull_state == "pulled":
            registration_status = "pulled"
            pull_status = "device_pull_verified"
            connection_evidence = "device_http_verified+device_pull_verified"
            message = "设备地址和 URL Rotation 配置已验证；ESP32 已主动拉取照片"
        elif immediate_refresh == "requested":
            registration_status = "awaiting_pull"
            pull_status = "awaiting_device_pull"
            connection_evidence = "device_http_verified"
            message = "设备地址已验证并完成 URL Rotation 配置；等待 ESP32 主动拉取第一张照片"
        elif immediate_refresh == "not_requested":
            registration_status = "awaiting_pull"
            pull_status = "awaiting_device_pull"
            connection_evidence = "device_http_verified"
            message = "设备地址已验证并完成 URL Rotation 配置；设备将在下一次轮播时主动拉取照片"
        elif immediate_refresh == "unconfirmed":
            registration_status = "awaiting_pull"
            pull_status = "awaiting_device_pull"
            connection_evidence = "device_http_verified"
            message = "设备地址已验证并完成 URL Rotation 配置；立即刷新未在时限内返回，等待 ESP32 实际拉图确认"
        else:
            registration_status = "awaiting_pull"
            pull_status = "awaiting_device_pull"
            connection_evidence = "device_http_verified"
            message = "设备地址已验证并完成 URL Rotation 配置，但立即刷新被设备拒绝；等待设备下一次主动拉取或检查设备状态"
        return {
            # Do not overload "connected": the server-side HTTP exchange is
            # proven here, while the physical device pull is asynchronous.
            "registration_status": registration_status,
            "connection_evidence": connection_evidence,
            "pull_status": pull_status,
            "message": message,
            "immediate_refresh": immediate_refresh,
            "device": device,
            "device_id": device_id,
            "name": device.get("name"),
            "profile_id": device.get("profile_id"),
            "display": device.get("display"),
            "policy": device.get("policy"),
            "delivery_mode": "device_pull",
            "pull_url": pull_url,
            "pull_provision": device.get("pull_provision"),
        }

    @api.patch("/api/admin/devices/{device_id}")
    async def api_admin_device_update(device_id: str, request: Request):
        try:
            return get_state().devices.update(device_id, await json_object(request))
        except DeviceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.delete("/api/admin/devices/{device_id}")
    def api_admin_device_delete(device_id: str, confirm: bool = Query(False)):
        """Delete a remote registration after an explicit confirmation.

        The old route called ``revoke`` and therefore only disabled a record,
        which left the same card in the management list.  Keep revocation
        available through ``DELETE /api/devices/{id}`` for protocol clients,
        while this management route performs the user-requested removal.
        """

        if not confirm:
            raise HTTPException(
                status_code=400,
                detail="confirm=true is required to delete the device registration; use PATCH enabled=false to disable it",
            )
        try:
            result = get_state().devices.delete(device_id)
            # Device selection/history is operational state, not a photo
            # asset.  Remove it when the index exposes the optional cleanup
            # hook; failures do not resurrect a deleted registration.
            cleanup = getattr(get_state().index, "delete_display_state", None)
            if callable(cleanup):
                try:
                    cleanup(device_id)
                except Exception:
                    pass
            return result
        except DeviceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.post("/api/admin/devices/{device_id}/advance")
    def api_admin_device_advance(device_id: str):
        try:
            device = get_state().devices.get(device_id)
            if device["display"]["kind"] != "photoframe":
                raise DeviceError("device is not a photoframe")
            if not device.get("enabled", True):
                raise DeviceError("device is disabled")
            _registered_photoframe_display(device)
            if hasattr(get_state().selector, "current_for_device"):
                row = get_state().selector.current_for_device(device_id, device.get("policy") or DEFAULT_PHOTOFRAME_POLICY, force=True)
            else:
                row = get_state().selector.current_photo("jpeg")
            return {
                "current": _safe_photo(row) if row else None,
                "selection_revision": _selector_revision(get_state().selector),
                "delivery_mode": "device_pull",
            }
        except DeviceError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.post("/api/admin/devices/{device_id}/provision-pull")
    async def api_admin_device_provision_pull(device_id: str, request: Request):
        """Write official PhotoFrame URL Rotation configuration once.

        This establishes ESP32-initiated retrieval.  It never sends an image
        from Case7 to the device, and a successful ``/api/rotate`` only means
        that the ESP32 accepted a request to start its own rotation cycle.
        """

        payload = await json_object(request)
        try:
            device = get_state().devices.get(device_id)
            if device.get("display", {}).get("kind") != "photoframe":
                raise DeviceError("device is not a photoframe")
            if not device.get("enabled", True):
                raise DeviceError("device is disabled")
            display = _registered_photoframe_display(device)
            raw_device_url = payload.get("device_url")
            if not isinstance(raw_device_url, str) or not raw_device_url.strip():
                raise DeviceError("device_url is required, for example http://192.168.1.137")
            trigger_now = payload.get("trigger_now", True)
            if not isinstance(trigger_now, bool):
                raise DeviceError("trigger_now must be boolean")
            device_url = normalize_device_url(raw_device_url)
            pull_url = managed_photoframe_pull_url(request, device_id, strict=True)
            if urlsplit(device_url).hostname == urlsplit(pull_url).hostname:
                raise DeviceError("device_url must identify the ESP32, not this smart-album server")
            policy = validate_policy(device.get("policy") or DEFAULT_PHOTOFRAME_POLICY)
            profile = photo_frame_profile(display["profile_id"])
        except ProvisionError as exc:
            # Invalid input is not a connection attempt and must not create a
            # misleading "configured" record.
            try:
                get_state().devices.mark_pull_provision(device_id, "rejected", last_error=str(exc))
            except DeviceError:
                pass
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (DeviceError, DisplayPolicyError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        try:
            with _PHOTOFRAME_PROVISION_LOCK:
                existing_id = get_state().devices.find_by_pull_url(
                    device_url, exclude_device_id=device_id
                )
                if existing_id:
                    raise DeviceConflictError(
                        f"device_url is already registered by device {existing_id}",
                        device_id=existing_id,
                    )
                # Serialize the complete control-plane attempt.  In
                # particular, do not let two retries overwrite each other's
                # configuring/final state while one of them is talking to the
                # ESP32.  A real device pull may still arrive concurrently;
                # DeviceRegistry preserves that evidence atomically.
                get_state().devices.mark_pull_provision(
                    device_id,
                    "configuring",
                    device_url=device_url,
                    last_error=None,
                    last_http_status=None,
                    rotate_status="not_requested",
                )
                result = PhotoFrameProvisioner().provision(
                    device_url,
                    image_url=pull_url,
                    rotation_cron=list(policy["rotation_cron"]),
                    display_orientation=str(display.get("orientation") or "landscape"),
                    native_size=(int(profile["width"]), int(profile["height"])),
                    expected_profile_id=profile["profile_id"],
                    trigger_now=trigger_now,
                )
                # A successful control-plane write always leaves the device
                # waiting for its own URL Rotation GET.  A trigger timeout or
                # explicit refusal is not a connected state; it is only the
                # immediate-rotate outcome and must not be surfaced as a
                # completed provisioning result.
                provision_status = "awaiting_pull"
                saved = get_state().devices.mark_pull_provision(
                    device_id,
                    provision_status,
                    device_url=result.device_url,
                    last_error=result.rotate_error,
                    last_http_status=result.rotate_http_status,
                    device_hardware_id=result.device_hardware_id,
                    firmware_version=result.firmware_version,
                    board_name=result.board_name,
                    configured_image_url=result.configured_image_url,
                    rotate_requested_at=time.time() if result.rotate_requested else None,
                    rotate_status=result.rotate_status,
                    successful=True,
                )
        except DeviceConflictError as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": str(exc),
                    "registration_status": "already_registered",
                    "device_id": exc.device_id,
                },
            ) from exc
        except ProvisionError as exc:
            status = "unreachable" if exc.kind in {"transport", "http", "response"} else "rejected"
            saved = get_state().devices.mark_pull_provision(
                device_id,
                status,
                device_url=device_url,
                last_error=str(exc),
                last_http_status=exc.status_code,
                rotate_status="not_requested",
            )
            response_status = 502 if status == "unreachable" else 400
            raise HTTPException(
                status_code=response_status,
                detail={"message": str(exc), "pull_provision": saved.get("pull_provision")},
            ) from exc

        return {
            "device_id": device_id,
            "delivery_mode": "device_pull",
            "pull_url": pull_url,
            "pull_provision": saved.get("pull_provision"),
            "message": (
                "PhotoFrame URL Rotation configuration was written; waiting for the ESP32 image request"
                if provision_status == "awaiting_pull"
                else "PhotoFrame URL Rotation configuration was written; automatic retrieval will use the next rotation slot"
            ),
        }

    @api.post("/api/admin/devices/{device_id}/push")
    async def api_admin_device_push(device_id: str, request: Request):
        """Immediately send the selected JPEG to an explicitly configured device.

        The device registry selects one explicit transport: the official
        PhotoFrame ``POST /api/display-image`` contract, the Case7-modified
        ``POST /api/case7/push`` contract, or the Waveshare demo raw-BMP
        ``POST /dataUP`` contract.  This endpoint never discovers a device,
        probes firmware, or changes URL Rotation configuration.
        """

        payload = await json_object(request)
        try:
            result = get_state().push_device(
                device_id,
                force=bool(payload.get("force", False)),
                force_send=bool(payload.get("force_send", False)),
            )
            return result
        except (DeviceError, PushError, DisplayPolicyError, OSError, ValueError) as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @api.post("/api/admin/devices/{device_id}/playlist")
    async def api_admin_device_playlist(device_id: str, request: Request):
        """Install a deterministic PhotoFrame playlist.

        Playlist membership is checked against currently available photos before
        the registry is changed, so a typo cannot silently switch back to smart
        selection. The optional immediate selection uses the same per-device
        selector state as a normal URL Rotation request.
        """
        try:
            device = get_state().devices.get(device_id)
            if device.get("display", {}).get("kind") != "photoframe":
                raise DeviceError("device is not a photoframe")
            _registered_photoframe_display(device)
            payload = await json_object(request)
            raw_ids = payload.get("photo_ids")
            if not isinstance(raw_ids, list) or not raw_ids:
                raise DeviceError("photo_ids must be a non-empty list")
            photo_ids = []
            for value in raw_ids:
                if isinstance(value, bool) or not isinstance(value, int):
                    raise DeviceError("photo_ids must contain integers")
                photo_id = value
                if photo_id <= 0 or photo_id in photo_ids:
                    raise DeviceError("photo_ids must contain unique positive integers")
                if get_state().index.get_photo(photo_id) is None:
                    raise DeviceError(f"photo is unavailable: {photo_id}")
                photo_ids.append(photo_id)
            rotation_cron = payload.get("rotation_cron", ["*/5 * *"])
            if isinstance(rotation_cron, str):
                rotation_cron = [rotation_cron]
            if not isinstance(rotation_cron, list):
                raise DeviceError("rotation_cron must be a list")
            patch = {
                "selection_mode": "playlist",
                "playlist_photo_ids": photo_ids,
                "rotation_cron": rotation_cron,
                "auto_rotate": bool(payload.get("auto_rotate", True)),
                "repeat_window": int(payload.get("repeat_window", 20)),
            }
            updated = get_state().devices.update(device_id, {"policy": patch})
            current = None
            if bool(payload.get("start_immediately", False)):
                current = get_state().selector.current_for_device(device_id, updated.get("policy"), force=True)
            policy = updated.get("policy") or DEFAULT_PHOTOFRAME_POLICY
            state = get_state().index.get_display_state(device_id) if hasattr(get_state().index, "get_display_state") else None
            response = {
                "device_id": device_id,
                "playlist_photo_ids": photo_ids,
                "policy": policy,
                "policy_revision": int(updated.get("policy_revision", policy.get("policy_revision", 1))),
                "current": _safe_photo(current) if current else _photo_state(get_state().index, _row_value(state, "photo_id")),
                "slot_key": _row_value(state, "slot_key"),
                "next_slot": _next_rotation_slot(get_state().selector, policy),
            }
            response["delivery_mode"] = "device_pull"
            return response
        except (DeviceError, DisplayPolicyError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.get("/api/admin/devices/{device_id}/state")
    def api_admin_device_state(device_id: str, request: Request):
        """Return audit-safe PhotoFrame state without paths, hashes, or tokens."""
        try:
            device = get_state().devices.get(device_id)
        except DeviceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        try:
            policy = device.get("policy") or DEFAULT_PHOTOFRAME_POLICY
            if (
                device.get("display", {}).get("kind") == "photoframe"
                and not (device.get("profile_id") or (device.get("display") or {}).get("profile_id"))
            ):
                return {
                    "device_id": device_id,
                    "delivery_mode": "device_pull",
                    "pull_url": managed_photoframe_pull_url(request, device_id),
                    "profile_id": None,
                    "profile_required": True,
                    "profile_error": device.get("profile_error") or PROFILE_REQUIRED_MESSAGE,
                    "enabled": bool(device.get("enabled", True)),
                    "selection_mode": policy.get("selection_mode", "smart"),
                    "playlist_photo_ids": list(policy.get("playlist_photo_ids", [])),
                    "policy_revision": int(device.get("policy_revision", policy.get("policy_revision", 1))),
                    "selection_revision": None,
                    "current": None,
                    "slot_key": None,
                    "next_slot": None,
                    "etag": None,
                    "history": [],
                    "last_request": device.get("last_request"),
                    "last_status": device.get("last_status"),
                    "last_error": device.get("last_error"),
                    "last_request_client": device.get("last_request_client"),
                    "last_request_firmware": device.get("last_request_firmware"),
                    "last_request_display": device.get("last_request_display"),
                    "pull_provision": device.get("pull_provision"),
                    "push": _public_push_state(device.get("push")),
                }
            if device.get("display", {}).get("kind") == "photoframe":
                _registered_photoframe_display(device)
            state = get_state().index.get_display_state(device_id) if hasattr(get_state().index, "get_display_state") else None
            photo_id = _row_value(state, "photo_id")
            current_row = get_state().index.get_photo(int(photo_id)) if photo_id is not None else None
            selection_revision = int(_row_value(state, "selection_revision", 1))
            state_etag = None
            if current_row:
                options = _photoframe_options(
                    get_state().config.get(),
                    policy,
                    device.get("display") or {},
                    get_state().selector,
                    current_row,
                )
                state_etag = _etag(
                    current_row,
                    f"jpeg:{device_id}:{options['variant']}",
                    _content_config_revision("jpeg", get_state().config.get(), policy_scoped=True),
                    options["selection_revision"],
                )
            history = []
            if hasattr(get_state().index, "display_history"):
                entries = get_state().index.display_history(device_id, limit=100)
                for entry in entries:
                    item = _photo_state(get_state().index, entry.get("photo_id"))
                    if item:
                        item["shown_at"] = entry.get("shown_at")
                        history.append(item)
            elif hasattr(get_state().index, "display_history_ids"):
                for value in get_state().index.display_history_ids(device_id, 100):
                    item = _photo_state(get_state().index, value)
                    if item:
                        history.append(item)
            response = {
                "device_id": device_id,
                "delivery_mode": "device_pull" if device.get("display", {}).get("kind") == "photoframe" else None,
                "pull_url": managed_photoframe_pull_url(request, device_id)
                if device.get("display", {}).get("kind") == "photoframe"
                else None,
                "profile_id": device.get("profile_id"),
                "profile_required": bool(device.get("profile_required", False)),
                "enabled": bool(device.get("enabled", True)),
                "selection_mode": policy.get("selection_mode", "smart"),
                "playlist_photo_ids": list(policy.get("playlist_photo_ids", [])),
                "policy_revision": int(device.get("policy_revision", policy.get("policy_revision", 1))),
                "selection_revision": selection_revision,
                "current": _photo_state(get_state().index, photo_id),
                "slot_key": _row_value(state, "slot_key"),
                "next_slot": _next_rotation_slot(get_state().selector, policy),
                "etag": device.get("last_etag") or state_etag,
                "history": history,
                "last_request": device.get("last_request"),
                "last_status": device.get("last_status"),
                "last_error": device.get("last_error"),
                "last_request_client": device.get("last_request_client"),
                "last_request_firmware": device.get("last_request_firmware"),
                "last_request_display": device.get("last_request_display"),
                "pull_provision": device.get("pull_provision"),
                "push": _public_push_state(device.get("push")),
            }
            return response
        except DeviceError as exc:
            # The device exists, but its declared product profile is invalid.
            # This is a client/configuration error rather than a missing ID.
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.post("/api/devices/handshake")
    async def api_device_handshake(request: Request):
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise DeviceError("device handshake must be a JSON object")
            display_payload = payload.get("display")
            requested_kind = payload.get("kind", payload.get("device_type"))
            if isinstance(display_payload, dict):
                requested_kind = display_payload.get("kind", requested_kind)
            if str(requested_kind or "").strip().lower() == "photoframe":
                # The generic handshake is intentionally retained for LCD/E6
                # clients.  PhotoFrame identity and configuration must be
                # verified by the atomic management flow before any registry
                # record is created; this route must have no write side effect.
                profile_id = payload.get("profile_id")
                if isinstance(display_payload, dict):
                    profile_id = profile_id or display_payload.get("profile_id")
                if not profile_id:
                    raise HTTPException(
                        status_code=400,
                        detail=(
                            "photoframe profile_id is required; choose "
                            "waveshare_photopainter_73 or "
                            "seeedstudio_reterminal_e1002"
                        ),
                    )
                raise HTTPException(
                    status_code=400,
                    detail={
                        "message": (
                            "PhotoFrame handshake is not a registration flow; "
                            "use POST /api/admin/devices/register with "
                            "device_url"
                        ),
                        "registration_status": "not_registered",
                        "next": "/api/admin/devices/register",
                    },
                )
            # The public HTTP handshake is URL-only.  The registry's optional
            # token response remains an internal migration aid for callers
            # that use it directly, but is not part of this API contract.
            result = get_state().devices.handshake(payload, require_token=False)
            result.pop("token", None)
            profile = "e6" if result["display"]["kind"] == "epaper" else "jpeg"
            row = selected_device_photo(result["device_id"], result) if result["display"]["kind"] == "photoframe" else get_state().selector.current_photo(profile)
            config = get_state().config.get()
            result.update(
                poll_seconds=_device_poll_seconds(config, result["display"]["kind"]),
                current=manifest(row, profile, result["device_id"], result["display"]["kind"], result["display"]) if row else None,
            )
            if result["display"]["kind"] == "photoframe":
                result["delivery_mode"] = "device_pull"
                result["pull_url"] = managed_photoframe_pull_url(request, result["device_id"])
            return result
        except (DeviceError, ValueError, TypeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @api.get("/api/devices/{device_id}/manifest")
    def api_device_manifest(
        device_id: str,
        x_device_token: Optional[str] = Header(None),
        x_display_width: Optional[str] = Header(None),
        x_display_height: Optional[str] = Header(None),
        x_display_orientation: Optional[str] = Header(None),
    ):
        device = device_auth(device_id, x_device_token)
        profile = "e6" if device["display"]["kind"] == "epaper" else "jpeg"
        capability = device["display"]
        if profile == "jpeg":
            try:
                capability = _display_capability_with_headers(
                    capability,
                    width=x_display_width,
                    height=x_display_height,
                    orientation=x_display_orientation,
                )
                capability = _enforce_registered_photoframe_profile(device, capability)
            except DisplayPolicyError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        row = selected_device_photo(device_id, device)
        if row is None and device["display"]["kind"] == "photoframe":
            raise HTTPException(status_code=404, detail="playlist has no available photos")
        return manifest(row, profile, device_id, device["display"]["kind"], capability)

    @api.get("/api/devices/{device_id}/content")
    def api_device_content(
        device_id: str,
        request: Request,
        x_device_token: Optional[str] = Header(None),
        if_none_match: Optional[str] = Header(None),
        x_display_width: Optional[str] = Header(None),
        x_display_height: Optional[str] = Header(None),
        x_display_orientation: Optional[str] = Header(None),
        x_firmware_version: Optional[str] = Header(None),
    ):
        device = device_auth(device_id, x_device_token)
        profile = "e6" if device["display"]["kind"] == "epaper" else "jpeg"
        capability = device["display"]
        # Legacy LCD/E6 clients retain their existing request audit.  The
        # public PhotoFrame URL endpoint is different: only a full official
        # URL Rotation request proves that the ESP32, rather than a browser or
        # curl probe, fetched a photograph.
        request_evidence = None
        record_device_request = device["display"].get("kind") != "photoframe"
        if profile == "jpeg":
            try:
                if device["display"].get("kind") == "photoframe":
                    capability = _photoframe_capability_from_headers(
                        device,
                        width=x_display_width,
                        height=x_display_height,
                        orientation=x_display_orientation,
                    )
                    request_evidence = _photoframe_fetch_evidence(
                        request,
                        device=device,
                        firmware_version=x_firmware_version,
                        width=x_display_width,
                        height=x_display_height,
                        orientation=x_display_orientation,
                    )
                    record_device_request = request_evidence is not None
                else:
                    capability = _display_capability_with_headers(
                        capability,
                        width=x_display_width,
                        height=x_display_height,
                        orientation=x_display_orientation,
                    )
                    capability = _enforce_registered_photoframe_profile(device, capability)
            except DisplayPolicyError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return content_response(
            request,
            profile,
            if_none_match,
            capability,
            device_id=device_id,
            record_device_request=record_device_request,
            device_request_evidence=request_evidence,
        )

    @api.get("/api/devices/{device_id}/photoframe")
    def api_photoframe_content(
        device_id: str,
        request: Request,
        x_device_token: Optional[str] = Header(None),
        authorization: Optional[str] = Header(None),
        if_none_match: Optional[str] = Header(None),
        x_display_width: Optional[str] = Header(None),
        x_display_height: Optional[str] = Header(None),
        x_display_orientation: Optional[str] = Header(None),
        x_firmware_version: Optional[str] = Header(None),
        force: bool = False,
    ):
        """Serve the URL Rotation Fetch contract used by esp32-photoframe."""
        device = device_auth(device_id, device_token(x_device_token, authorization))
        if device["display"]["kind"] != "photoframe":
            raise HTTPException(status_code=400, detail="device is not registered as a photoframe")
        try:
            capability = _photoframe_capability_from_headers(
                device,
                width=x_display_width,
                height=x_display_height,
                orientation=x_display_orientation,
            )
        except DisplayPolicyError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        request_evidence = _photoframe_fetch_evidence(
            request,
            device=device,
            firmware_version=x_firmware_version,
            width=x_display_width,
            height=x_display_height,
            orientation=x_display_orientation,
        )
        policy = validate_policy(device.get("policy") or DEFAULT_PHOTOFRAME_POLICY)
        row = get_state().selector.current_for_device(device_id, policy, force=force) if hasattr(get_state().selector, "current_for_device") else get_state().selector.current_photo("jpeg")
        if row is None:
            if request_evidence:
                get_state().devices.mark_request(device_id, "empty", **request_evidence)
            raise HTTPException(status_code=404, detail="playlist has no available photos")
        options = _photoframe_options(
            get_state().config.get(), policy, capability, get_state().selector, row
        )
        return content_response(
            request,
            "jpeg",
            if_none_match,
            capability,
            etag_variant=options["variant"],
            row=row,
            device_id=device_id,
            render_policy=options["policy"],
            target_orientation=options["target_orientation"],
            record_device_request=bool(request_evidence),
            device_request_evidence=request_evidence,
        )

    @api.post("/api/devices/{device_id}/heartbeat")
    async def api_device_heartbeat(device_id: str, x_device_token: Optional[str] = Header(None)):
        device = device_auth(device_id, x_device_token)
        if device.get("display", {}).get("kind") == "photoframe":
            try:
                _registered_photoframe_display(device)
            except DeviceError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"ok": True, "time": time.time()}

    @api.patch("/api/devices/{device_id}")
    async def api_device_update(device_id: str, request: Request):
        try:
            return get_state().devices.update(device_id, await request.json())
        except DeviceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @api.delete("/api/devices/{device_id}")
    def api_device_revoke(device_id: str):
        try:
            return get_state().devices.revoke(device_id)
        except DeviceError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    def manifest(row, profile, device_id=None, device_kind=None, capability=None):
        config = get_state().config.get()
        poll_seconds = _device_poll_seconds(config, device_kind) if device_kind else (
            _epaper_interval_seconds(config) if profile == "e6" else _remote_refresh_seconds(config)
        )
        if not row:
            return {"current": None, "poll_seconds": poll_seconds}
        selection_revision = int(_row_value(row, "selection_revision", _selector_revision(get_state().selector)))
        etag_profile = profile
        if profile == "e6":
            try:
                options = _epaper_options(config, capability if capability is not None else None)
            except (DisplayPolicyError, DeviceError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            etag_profile = f"e6:{device_id or 'local'}:{options['variant']}"
        elif profile == "jpeg" and device_id and device_kind == "photoframe":
            try:
                device = get_state().devices.get(device_id)
                options = _photoframe_options(
                    config,
                    device.get("policy") or DEFAULT_PHOTOFRAME_POLICY,
                    capability or device.get("display") or {},
                    get_state().selector,
                    row,
                )
            except (DeviceError, DisplayPolicyError, ValueError, TypeError) as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            etag_profile = f"jpeg:{device_id}:{options['variant']}"
        elif profile == "jpeg" and device_id and device_kind != "photoframe":
            # The generic content endpoint uses the registered capability when
            # no query parameters are present, so its manifest must use the
            # same variant for an immediate conditional GET to yield 304.
            try:
                options = _render_options(capability or {}, _render_defaults(config, capability))
            except DisplayPolicyError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            etag_profile = f"{profile}:{device_id}:{options['variant']}"
        etag = _etag(
            row,
            etag_profile,
            _content_config_revision(
                profile,
                config,
                policy_scoped=profile == "jpeg" and device_kind == "photoframe",
            ),
            selection_revision,
        )
        url = f"/api/devices/{device_id}/photoframe" if device_id and device_kind == "photoframe" else (f"/api/devices/{device_id}/content" if device_id else "/api/display/content")
        return {
            "photo_id": int(row["id"]),
            "profile": profile,
            "device_profile": (capability or {}).get("profile_id"),
            "orientation": (capability or {}).get("orientation") or ("landscape" if profile == "jpeg" else None),
            "etag": etag,
            "selection_revision": selection_revision,
            "url": url,
            "poll_seconds": poll_seconds,
        }

    def content_response(
        request,
        profile,
        if_none_match,
        capability=None,
        etag_variant="",
        row=None,
        device_id=None,
        render_policy=None,
        target_orientation=None,
        record_device_request=True,
        device_request_evidence=None,
    ):
        config = get_state().config.get()
        render_options = None
        epaper_options = None
        if profile == "jpeg" and render_policy is None:
            # Generic LCD and local touchscreen callers negotiate dimensions
            # through query parameters or the stored device capability.
            source = capability or request.query_params
            defaults = _render_defaults(config, capability)
            try:
                render_options = _render_options(source, defaults)
            except DisplayPolicyError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            target_orientation = render_options["target_orientation"]
            etag_variant = etag_variant or render_options["variant"]
        elif profile == "e6":
            try:
                epaper_options = _epaper_options(config, capability if capability is not None else None)
            except DisplayPolicyError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            target_orientation = epaper_options["target_orientation"]
            etag_variant = etag_variant or epaper_options["variant"]
        selector = get_state().selector
        with _selector_state_guard(selector):
            row = row or selector.current_photo(profile)
            selection_revision = int(_row_value(row, "selection_revision", _selector_revision(selector))) if row else None
        if row is None:
            raise HTTPException(status_code=404, detail="no available photo")
        etag_profile = f"{profile}:{device_id or 'local'}:{etag_variant}" if etag_variant else profile
        etag = _etag(
            row,
            etag_profile,
            _content_config_revision(profile, config, policy_scoped=render_policy is not None),
            selection_revision,
        )
        if if_none_match and if_none_match.strip() == etag:
            if device_id:
                mark_etag = getattr(get_state().devices, "mark_representation_etag", None)
                if callable(mark_etag):
                    mark_etag(device_id, etag)
                if record_device_request:
                    get_state().devices.mark_request(
                        device_id, "not_modified", etag=etag, **dict(device_request_evidence or {})
                    )
            return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "private, max-age=0, must-revalidate", "Vary": "Authorization, X-Device-Token, X-Display-Width, X-Display-Height, X-Display-Orientation, If-None-Match"})
        if profile == "e6":
            epaper_options = epaper_options or _epaper_options(config, capability if capability is not None else None)
            frame = prepare_frame(
                Path(row["filepath"]),
                dither=epaper_options["dither"],
                orientation_mode=epaper_options["orientation_mode"],
                rotation=epaper_options["rotation"],
                target_orientation=epaper_options["target_orientation"],
            )
            headers = {
                "ETag": etag,
                "X-Album-Width": str(EPAPER_WIDTH),
                "X-Album-Height": str(EPAPER_HEIGHT),
                "X-Album-Orientation": str(epaper_options["target_orientation"]),
                "X-Album-Target-Orientation": str(epaper_options["target_orientation"]),
                "X-Album-Orientation-Mode": str(epaper_options["orientation_mode"]),
                "X-Album-Rotation": str(epaper_options["rotation"]),
                "Cache-Control": "private, max-age=0, must-revalidate",
                "Vary": "Authorization, X-Device-Token, X-Display-Orientation, If-None-Match",
            }
            if device_id:
                mark_etag = getattr(get_state().devices, "mark_representation_etag", None)
                if callable(mark_etag):
                    mark_etag(device_id, etag)
                if record_device_request:
                    headers["X-PhotoFrame-Revision"] = str(selection_revision)
                    get_state().devices.mark_request(device_id, "ok", etag=etag, **dict(device_request_evidence or {}))
            return Response(content=frame.packed, media_type="application/octet-stream", headers=headers)
        try:
            if render_policy is not None:
                renderer = getattr(get_state(), "renderer", PhotoRenderer())
                render_args = (
                    Path(row["filepath"]),
                    render_policy,
                    config["timezone"],
                    (_row_value(row, "weather", "") or ""),
                )
                if render_policy.get("orientation_mode") == "match_display":
                    body, actual_width, actual_height = renderer.render(
                        *render_args, target_orientation=target_orientation
                    )
                else:
                    body, actual_width, actual_height = renderer.render(*render_args)
            else:
                body, actual_width, actual_height = _bounded_jpeg(
                    Path(row["filepath"]),
                    render_options["width"],
                    render_options["height"],
                    render_options["quality"],
                    render_options["max_bytes"],
                    render_options["rotation"],
                    render_options["orientation_mode"],
                    render_options["target_orientation"],
                )
        except (TypeError, ValueError, DisplayPolicyError, OSError) as exc:
            if device_id and record_device_request:
                get_state().devices.mark_request(
                    device_id, "error", str(exc), **dict(device_request_evidence or {})
                )
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        actual_orientation = orientation_for_size((actual_width, actual_height))
        headers = {
            "ETag": etag,
            "X-Album-Width": str(actual_width),
            "X-Album-Height": str(actual_height),
            # This describes the JPEG's actual pixel aspect. The target is a
            # separate header because ``auto`` intentionally preserves a
            # landscape photograph inside a portrait viewport when requested.
            "X-Album-Orientation": actual_orientation,
            "X-Album-Target-Orientation": str(target_orientation or actual_orientation),
            "X-Album-Orientation-Mode": str((render_policy or {}).get("orientation_mode", render_options["orientation_mode"] if render_options else "auto")),
            "Cache-Control": "private, max-age=0, must-revalidate",
            "Vary": "Authorization, X-Device-Token, X-Display-Width, X-Display-Height, X-Display-Orientation, If-None-Match",
        }
        if device_id:
            headers["X-PhotoFrame-Revision"] = str(selection_revision)
            if render_policy is not None:
                # This is the upstream PhotoFrame config-payload schema, not
                # Case7's local rendering schema.  It keeps the device's
                # persisted URL Rotation schedule aligned with its registered
                # policy on the next successful device-initiated fetch.
                headers["X-Config-Payload"] = json.dumps(
                    {
                        "config": {
                            "auto_rotate": bool(render_policy.get("auto_rotate", True)),
                            "rotate_cron": list(render_policy.get("rotation_cron", [])),
                            "display_orientation": str(target_orientation or "landscape"),
                            "display_rotation_deg": 0,
                        }
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            mark_etag = getattr(get_state().devices, "mark_representation_etag", None)
            if callable(mark_etag):
                mark_etag(device_id, etag)
            if record_device_request:
                get_state().devices.mark_request(device_id, "ok", etag=etag, **dict(device_request_evidence or {}))
        return Response(content=body, media_type="image/jpeg", headers=headers)

    web_dir = Path(__file__).resolve().parent / "web"
    if web_dir.is_dir():
        api.mount("/", StaticFiles(directory=str(web_dir), html=True), name="web")
    return api


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--host", default="0.0.0.0")
    value.add_argument("--port", type=int, default=7860)
    value.add_argument("--backend", choices=("npu",), default="npu")
    value.add_argument("--touchscreen", action="store_true")
    value.add_argument("--epaper-backend", choices=("dry-run", "orangepi"), default=None)
    value.add_argument("--allow-numpy-index", action="store_true", help="offline tests only")
    return value


def main(argv=None):
    args = parser().parse_args(argv)
    global _state
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(PHOTO_DIR, exist_ok=True)
    _state = ApplicationState(allow_numpy_fallback=args.allow_numpy_index, epaper_backend=args.epaper_backend)
    import uvicorn
    uvicorn.run(create_app(touchscreen=args.touchscreen), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
