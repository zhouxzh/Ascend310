"""Persistent, validated smart-album application configuration."""

from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any, Mapping, Optional, Union

from config import DATA_DIR
from display_policy import DisplayPolicyError, validate_orientation_mode


DEFAULT_CONFIG = {
    "schema_version": 1,
    "revision": 1,
    "timezone": "Asia/Shanghai",
    "display": {
        # The HDMI touchscreen and browser clients are intentionally much
        # more responsive than an e-paper panel.  Keep the legacy
        # ``interval_seconds`` key so older configuration files remain
        # readable, but use the explicit names below for new deployments.
        "interval_seconds": 60,
        "touchscreen_interval_seconds": 60,
        "remote_refresh_seconds": 30,
        "repeat_window": 12,
        "enabled": True,
        # The built-in HDMI panel is managed as its own device.  Keep the
        # historical ``enabled`` switch for the combined display controls and
        # let the device page pause only the touchscreen when requested.
        "touchscreen_enabled": True,
        "show_filename": True,
        # ``auto`` normalizes EXIF and preserves the photo's intended
        # direction. ``match_display`` is an explicit opt-in quarter-turn
        # when a full opposite aspect orientation is desired.
        "orientation_mode": "auto",
        "rotation": 0,
    },
    "selection": {
        "semantic_weight": 0.60,
        "date_weight": 0.25,
        "weather_weight": 0.15,
        "language": "zh",
    },
    "weather": {
        "enabled": True,
        "provider": "open_meteo",
        "latitude": 31.2304,
        "longitude": 121.4737,
        "refresh_seconds": 1800,
        "timeout_seconds": 5,
    },
    "index": {
        "auto_index_uploads": True,
        "models": [],
    },
    "device": {
        # Generic remote display polling follows the e-paper cadence by
        # default.  LCD clients can override their own refresh cadence via
        # the touchscreen/browser settings and do not need to poll this often.
        "poll_seconds": 1800,
        "jpeg_quality": 82,
    },
    "epaper": {
        "backend": "dry-run",
        # E-paper refreshes are deliberately slow to avoid unnecessary panel
        # wear and power use.  600 seconds (10 minutes) is the fastest
        # supported value; 1800 seconds (30 minutes) is the default.
        "rotation_interval_seconds": 1800,
        # E6 is a separate physical output from the HDMI touchscreen.  Keep
        # its orientation policy independent so changing the touchscreen
        # layout cannot silently alter an e-Paper frame.
        "orientation_mode": "auto",
        "rotation": 0,
        "e6_dither": True,
        "spi_device": "/dev/spidev0.0",
        "gpiochip": "/dev/gpiochip0",
        "dc_line": None,
        "rst_line": None,
        "busy_line": None,
        "pwr_line": None,
        "spi_hz": 4000000,
    },
}


class ConfigError(ValueError):
    pass


def _migrate_legacy_config(value: Mapping[str, Any]) -> dict:
    """Move the former display-owned E6 controls to ``epaper`` once.

    Early server builds placed ``e6_dither`` beside HDMI display settings.
    Retaining that location makes a touchscreen-only change affect a separate
    physical output, so old persisted files are normalized before validation.
    """

    result = copy.deepcopy(dict(value))
    display = result.get("display")
    if not isinstance(display, Mapping):
        return result
    display = dict(display)
    legacy_dither = display.pop("e6_dither", None)
    # ``display.backend`` was a no-op compatibility field; EpaperConfig has
    # always sourced the actual backend from the dedicated group.
    display.pop("backend", None)
    # ``interval_seconds`` was the only local display clock in older
    # releases.  Copy an explicitly persisted value to the new touchscreen
    # clock unless the operator already supplied the new key.  The legacy
    # key remains in the normalized document for clients that still read it.
    if "touchscreen_interval_seconds" not in display and "interval_seconds" in display:
        # 3600 was the former shipped default, not an intentional e-paper
        # policy.  Upgrade that known default to the responsive touchscreen
        # cadence; preserve any different operator-selected legacy value.
        legacy_interval = display["interval_seconds"]
        display["touchscreen_interval_seconds"] = 60 if legacy_interval == 3600 else legacy_interval
    # Before the local panel gained its own device switch, ``enabled`` was the
    # only visible pause state.  Preserve an explicit old pause rather than
    # silently waking the touchscreen when the configuration is migrated.
    if "touchscreen_enabled" not in display and "enabled" in display:
        display["touchscreen_enabled"] = display["enabled"]
    result["display"] = display
    if legacy_dither is not None:
        epaper = result.get("epaper")
        epaper = dict(epaper) if isinstance(epaper, Mapping) else {}
        epaper.setdefault("e6_dither", legacy_dither)
        result["epaper"] = epaper
    return result


def _merge(base: dict, patch: Mapping[str, Any]) -> dict:
    result = copy.deepcopy(base)
    for key, value in patch.items():
        if key in {"schema_version", "revision"}:
            continue
        if isinstance(value, Mapping) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = value
    return result


def _validate_patch_keys(patch: Mapping[str, Any], template: Mapping[str, Any] = DEFAULT_CONFIG, prefix: str = ""):
    for key, value in patch.items():
        if key in {"schema_version", "revision"}:
            continue
        name = f"{prefix}.{key}" if prefix else key
        if key not in template:
            raise ConfigError(f"unsupported configuration key: {name}")
        expected = template[key]
        if isinstance(expected, Mapping):
            if not isinstance(value, Mapping):
                raise ConfigError(f"configuration group must be an object: {name}")
            _validate_patch_keys(value, expected, name)


def validate_config(value: Mapping[str, Any]) -> dict:
    result = _merge(DEFAULT_CONFIG, _migrate_legacy_config(value))
    if not isinstance(result.get("timezone"), str) or not result["timezone"]:
        raise ConfigError("timezone must be a non-empty string")
    display = result["display"]
    if not 60 <= int(display["interval_seconds"]) <= 7 * 24 * 3600:
        raise ConfigError("display.interval_seconds must be between 60 and 604800")
    if not 0 <= int(display["repeat_window"]) <= 1000:
        raise ConfigError("display.repeat_window is out of range")
    if not isinstance(display["show_filename"], bool):
        raise ConfigError("display.show_filename must be boolean")
    if not isinstance(display.get("enabled", True), bool):
        raise ConfigError("display.enabled must be boolean")
    if not isinstance(display.get("touchscreen_enabled", True), bool):
        raise ConfigError("display.touchscreen_enabled must be boolean")
    try:
        display["orientation_mode"] = validate_orientation_mode(display.get("orientation_mode", "auto"))
    except DisplayPolicyError as exc:
        raise ConfigError(str(exc)) from exc
    try:
        display["rotation"] = int(display.get("rotation", 0))
    except (TypeError, ValueError) as exc:
        raise ConfigError("display.rotation must be 0, 90, 180, or 270") from exc
    if display["rotation"] not in {0, 90, 180, 270}:
        raise ConfigError("display.rotation must be 0, 90, 180, or 270")
    selection = result["selection"]
    weights = [float(selection[name]) for name in ("semantic_weight", "date_weight", "weather_weight")]
    if any(weight < 0 for weight in weights) or sum(weights) <= 0:
        raise ConfigError("selection weights must be non-negative and non-zero")
    weather = result["weather"]
    if weather["provider"] not in {"open_meteo", "disabled"}:
        raise ConfigError("unsupported weather provider")
    if not -90 <= float(weather["latitude"]) <= 90 or not -180 <= float(weather["longitude"]) <= 180:
        raise ConfigError("weather coordinates are invalid")
    if not 60 <= int(weather["refresh_seconds"]) <= 7 * 24 * 3600:
        raise ConfigError("weather.refresh_seconds is out of range")
    if not 1 <= int(result["device"]["jpeg_quality"]) <= 100:
        raise ConfigError("device.jpeg_quality must be between 1 and 100")
    if not 60 <= int(result["device"]["poll_seconds"]) <= 7 * 24 * 3600:
        raise ConfigError("device.poll_seconds is out of range")
    if not isinstance(result["index"]["auto_index_uploads"], bool):
        raise ConfigError("index.auto_index_uploads must be boolean")
    if not isinstance(result["index"]["models"], list) or not all(isinstance(item, str) for item in result["index"]["models"]):
        raise ConfigError("index.models must be a string list")
    epaper = result["epaper"]
    try:
        display["interval_seconds"] = int(display.get("interval_seconds", 60))
        display["touchscreen_interval_seconds"] = int(
            display.get("touchscreen_interval_seconds", display["interval_seconds"])
        )
        display["remote_refresh_seconds"] = int(display.get("remote_refresh_seconds", 30))
    except (TypeError, ValueError) as exc:
        raise ConfigError("display refresh intervals must be integers") from exc
    if not 60 <= display["interval_seconds"] <= 7 * 24 * 3600:
        raise ConfigError("display.interval_seconds must be between 60 and 604800")
    if not 5 <= display["touchscreen_interval_seconds"] <= 7 * 24 * 3600:
        raise ConfigError("display.touchscreen_interval_seconds must be between 5 and 604800")
    if not 5 <= display["remote_refresh_seconds"] <= 7 * 24 * 3600:
        raise ConfigError("display.remote_refresh_seconds must be between 5 and 604800")
    try:
        epaper["rotation_interval_seconds"] = int(epaper.get("rotation_interval_seconds", 1800))
    except (TypeError, ValueError) as exc:
        raise ConfigError("epaper.rotation_interval_seconds must be an integer") from exc
    if epaper["rotation_interval_seconds"] not in {600, 1800}:
        raise ConfigError("epaper.rotation_interval_seconds must be 600 or 1800 seconds")
    if epaper["backend"] not in {"dry-run", "orangepi"}:
        raise ConfigError("unsupported epaper backend")
    try:
        epaper["orientation_mode"] = validate_orientation_mode(epaper.get("orientation_mode", "auto"))
    except DisplayPolicyError as exc:
        raise ConfigError(str(exc)) from exc
    try:
        epaper["rotation"] = int(epaper.get("rotation", 0))
    except (TypeError, ValueError) as exc:
        raise ConfigError("epaper.rotation must be 0, 90, 180, or 270") from exc
    if epaper["rotation"] not in {0, 90, 180, 270}:
        raise ConfigError("epaper.rotation must be 0, 90, 180, or 270")
    if not isinstance(epaper["e6_dither"], bool):
        raise ConfigError("epaper.e6_dither must be boolean")
    for key in ("spi_device", "gpiochip"):
        if not isinstance(epaper[key], str) or not epaper[key].startswith("/dev/"):
            raise ConfigError(f"epaper.{key} must be a /dev path")
    for key in ("dc_line", "rst_line", "busy_line", "pwr_line"):
        if epaper[key] is not None and not 0 <= int(epaper[key]) <= 4095:
            raise ConfigError(f"epaper.{key} is out of range")
    if not 100000 <= int(epaper["spi_hz"]) <= 20000000:
        raise ConfigError("epaper.spi_hz is out of range")
    result["schema_version"] = 1
    result["revision"] = int(value.get("revision", result.get("revision", 1)))
    return result


class ConfigStore:
    def __init__(self, path: Optional[Union[Path, str]] = None):
        self.path = Path(path or (Path(DATA_DIR) / "server_config.json"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._config = self._load()

    def _load(self) -> dict:
        if not self.path.is_file():
            return copy.deepcopy(DEFAULT_CONFIG)
        try:
            return validate_config(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise ConfigError(f"invalid configuration: {self.path}") from exc

    def get(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._config)

    def update(self, patch: Mapping[str, Any], expected_revision: Optional[int] = None) -> dict:
        with self._lock:
            current = self._config
            if expected_revision is not None and int(expected_revision) != int(current["revision"]):
                raise ConfigError("configuration revision conflict")
            _validate_patch_keys(patch)
            merged = validate_config(_merge(current, patch))
            merged["revision"] = int(current["revision"]) + 1
            self._atomic_write(merged)
            self._config = merged
            return self.get()

    def _atomic_write(self, value: dict):
        fd, temp_name = tempfile.mkstemp(prefix="server_config.", suffix=".tmp", dir=str(self.path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
