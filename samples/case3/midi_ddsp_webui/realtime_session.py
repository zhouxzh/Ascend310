"""Unified realtime instrument catalog and session orchestration."""

from __future__ import annotations

import copy
from pathlib import Path
import queue
import re
import threading
import time
from typing import Callable, Protocol
from uuid import uuid4

from realtime_ddsp import query_audio_devices, query_midi_devices

from .core import (
    REPORT_ROOT,
    ResourceCoordinator,
    catalog,
    public_catalog,
    resolve_catalog_item,
)
from .live import DdspVstSessionController, resolve_realtime_recording
from .piano import (
    PianoDdspController,
    piano_catalog,
    resolve_piano_bundle,
    resolve_recording as resolve_piano_recording,
)
from .speaker import query_ddsp_vst_audio_outputs, query_piano_audio_outputs


REALTIME_OWNER = "realtime-session"


def _slug(value: str) -> str:
    clean = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return clean or "patch"


def _instrument_category(name: str) -> str:
    value = name.casefold()
    if value in {"flute", "oboe", "clarinet", "saxophone", "bassoon"}:
        return "woodwind"
    if value in {"trumpet", "horn", "trombone", "tuba"}:
        return "brass"
    if value in {"violin", "viola", "cello", "double bass"}:
        return "strings"
    return "other"


def _public_patch(patch: dict[str, object]) -> dict[str, object]:
    return {
        key: copy.deepcopy(value)
        for key, value in patch.items()
        if not key.startswith("_")
    }


def build_realtime_catalog() -> dict[str, object]:
    """Build stable patches and their actual output-device compatibility."""
    piano_data = piano_catalog()
    raw_catalog = catalog()
    piano_outputs = query_piano_audio_outputs(query_audio_devices)
    ddsp_outputs = query_ddsp_vst_audio_outputs(query_audio_devices)

    patches: list[dict[str, object]] = []
    piano_output_ids = [str(item["id"]) for item in piano_outputs]
    ddsp_output_ids = [str(item["id"]) for item in ddsp_outputs]
    for model in piano_data.get("models", []):
        if not bool(model.get("available")):
            continue
        model_id = str(model["id"])
        patches.append(
            {
                "patch_id": f"piano.{_slug(model_id)}",
                "name": str(model.get("name") or "Concert Piano"),
                "category": "piano",
                "available": True,
                "pitch_min": 21,
                "pitch_max": 108,
                "polyphony": 16,
                "compatible_audio_device_ids": piano_output_ids,
                "parameters": {
                    "velocity_curve": {"min": 0.25, "max": 2.0, "default": 1.0},
                    "transpose": {"min": -24, "max": 24, "default": 0},
                    "output_gain_db": {"min": -60, "max": 6, "default": 0},
                    "reverb": {"min": 0, "max": 1, "default": 1.0},
                    "piano_year": {"options": list(piano_data.get("piano_years", [])), "default": 2018},
                },
                "details": {
                    "engine": "piano-ddsp",
                    "architecture": model.get("architecture"),
                    "quality_status": model.get("quality_status"),
                    "n_harmonics": model.get("n_harmonics"),
                    "n_noise_bands": model.get("n_noise_bands"),
                },
                "_engine": "piano-ddsp",
                "_model_id": model_id,
            }
        )

    models = list(raw_catalog.get("ddsp_vst_models", []))
    instrument_counts: dict[str, int] = {}
    for model in models:
        instrument = str(model.get("instrument") or model.get("name") or "Instrument")
        instrument_counts[instrument] = instrument_counts.get(instrument, 0) + 1
    for model in models:
        instrument = str(model.get("instrument") or model.get("name") or "Instrument")
        precision = str(model.get("precision") or "default")
        suffix = f".{_slug(precision)}" if instrument_counts[instrument] > 1 else ""
        pitch_min = max(21, round(float(model.get("pitch_min_note", 21))))
        pitch_max = min(108, round(float(model.get("pitch_max_note", 108))))
        patches.append(
            {
                "patch_id": f"neural.{_slug(instrument)}{suffix}",
                "name": instrument,
                "category": _instrument_category(instrument),
                "available": True,
                "pitch_min": pitch_min,
                "pitch_max": max(pitch_min, pitch_max),
                "polyphony": 4,
                "compatible_audio_device_ids": ddsp_output_ids,
                "parameters": {
                    "velocity_curve": {"min": 0.25, "max": 2.0, "default": 0.55},
                    "transpose": {"min": -24, "max": 24, "default": 0},
                    "output_gain_db": {"min": -60, "max": 6, "default": 0},
                    "reverb": {"min": 0, "max": 1, "default": 0.15},
                    "harmonic_gain": {"min": 0, "max": 1, "default": 1.0},
                    "noise_gain": {"min": 0, "max": 1, "default": 1.0},
                    "attack": {"min": 0.01, "max": 3, "default": 0.02},
                    "release": {"min": 0.01, "max": 5, "default": 1.2},
                },
                "details": {
                    "engine": "ddsp-vst",
                    "model": model.get("name"),
                    "precision": precision,
                    "backend": model.get("backend"),
                },
                "_engine": "ddsp-vst",
                "_model_id": str(model["id"]),
            }
        )

    compatibility: dict[str, set[str]] = {}
    for patch in patches:
        for device_id in patch["compatible_audio_device_ids"]:
            compatibility.setdefault(str(device_id), set()).add(str(patch["patch_id"]))
    devices_by_id: dict[str, dict[str, object]] = {}
    for item in [*piano_outputs, *ddsp_outputs]:
        device_id = str(item["id"])
        devices_by_id[device_id] = {
            **item,
            "compatible_patch_ids": sorted(compatibility.get(device_id, set())),
        }

    midi_data = public_catalog()
    try:
        midi_ports = query_midi_devices()
        midi_error = None
    except Exception as exc:
        midi_ports = []
        midi_error = str(exc)
    return {
        "schema_version": 1,
        "patches": [_public_patch(item) for item in patches],
        "audio_devices": list(devices_by_id.values()),
        "midi_ports": midi_ports,
        "midi_error": midi_error,
        "midi_files": midi_data.get("midi_files", []),
        "latency_profiles": ["low", "balanced", "safe"],
        "_patches": patches,
    }


def public_realtime_catalog() -> dict[str, object]:
    data = build_realtime_catalog()
    data.pop("_patches", None)
    return data


def _find_patch(patch_id: str, data: dict[str, object] | None = None) -> dict[str, object]:
    source = data or build_realtime_catalog()
    for patch in source.get("_patches", []):
        if patch.get("patch_id") == patch_id:
            return dict(patch)
    raise KeyError(f"Unknown realtime patch: {patch_id}")


def _select_output(
    patch: dict[str, object], audio_device_id: str | None, data: dict[str, object]
) -> dict[str, object]:
    compatible = set(str(value) for value in patch["compatible_audio_device_ids"])
    devices = [dict(item) for item in data.get("audio_devices", [])]
    if audio_device_id:
        selected = next(
            (item for item in devices if str(item.get("id")) == audio_device_id), None
        )
        if selected is None:
            raise KeyError(f"Unknown audio output: {audio_device_id}")
        if audio_device_id not in compatible:
            raise ValueError(
                f"Audio output {selected.get('name', audio_device_id)} is not compatible with patch {patch['name']}"
            )
        return selected
    selected = next(
        (item for item in devices if str(item.get("id")) in compatible and item.get("is_default")),
        next((item for item in devices if str(item.get("id")) in compatible), None),
    )
    if selected is None:
        raise RuntimeError(f"No compatible audio output is available for patch {patch['name']}")
    return selected


def _resolve_midi_port(value: object) -> str | None:
    if value in (None, ""):
        return None
    requested = str(value)
    selected = next(
        (
            item
            for item in query_midi_devices()
            if requested in {str(item.get("id")), str(item.get("port"))}
        ),
        None,
    )
    if selected is None:
        raise KeyError(f"MIDI input {requested!r} was not found")
    return str(selected.get("port") or selected.get("name"))


def _parameter_defaults(patch: dict[str, object]) -> dict[str, object]:
    defaults: dict[str, object] = {}
    for name, metadata in dict(patch.get("parameters", {})).items():
        if isinstance(metadata, dict) and "default" in metadata:
            defaults[name] = metadata["default"]
    return defaults


def resolve_runtime_config(
    patch: dict[str, object], values: dict[str, object], data: dict[str, object]
) -> dict[str, object]:
    output = _select_output(
        patch,
        str(values["audio_device_id"]) if values.get("audio_device_id") else None,
        data,
    )
    parameters = _parameter_defaults(patch)
    parameters.update(dict(values.get("parameters") or {}))
    latency_profile = str(values.get("latency_profile") or "balanced")
    midi_port = _resolve_midi_port(values.get("midi_port"))
    device_id = int(values.get("device_id", 0))
    common = {
        "latency_profile": latency_profile,
        "midi_port": midi_port,
        "audio_backend": str(output.get("backend", "portaudio")),
        "pulse_sink": output.get("sink_name"),
        "is_bluetooth": bool(output.get("is_bluetooth", False)),
        "device_id": device_id,
        "patch_id": patch["patch_id"],
        "audio_device_id_public": str(output["id"]),
    }
    if patch["_engine"] == "piano-ddsp":
        model_id = str(patch["_model_id"])
        piano_data = piano_catalog()
        bundles = list(piano_data.get("bundles", []))
        active_id = piano_data.get("active_bundle_id")
        selected_bundle = next(
            (
                item
                for item in bundles
                if item.get("id") == active_id and model_id in item.get("models", [])
            ),
            next((item for item in bundles if model_id in item.get("models", [])), None),
        )
        if selected_bundle is None:
            raise KeyError(f"No qualified Piano-DDSP bundle contains {model_id}")
        manifest = resolve_piano_bundle(str(selected_bundle["id"]), model_id)
        audio_device: str | int | None = (
            output.get("index") if output.get("backend") == "portaudio" else output.get("id")
        )
        return {
            **common,
            "bundle_manifest": str(manifest),
            "bundle_id": str(selected_bundle["id"]),
            "model_id": model_id,
            "piano_year": int(parameters.get("piano_year", 2018)),
            "output_sample_rate": int(output.get("default_sample_rate", 48_000)),
            "seed": int(values.get("seed", 0)),
            "velocity_curve": float(parameters.get("velocity_curve", 1.0)),
            "transpose": int(parameters.get("transpose", 0)),
            "output_gain_db": float(parameters.get("output_gain_db", 0.0)),
            "reverb_mix": float(parameters.get("reverb", 1.0)),
            "audio_device": audio_device,
            "alsa_card": output.get("alsa_card"),
            "alsa_device": output.get("alsa_device"),
            "alsa_route_device_id": output.get("alsa_route_device_id"),
            "alsa_playback_level": output.get("alsa_playback_level"),
            "recorder_root": str(REPORT_ROOT / "piano-ddsp"),
        }
    model = resolve_catalog_item("ddsp_vst_models", str(patch["_model_id"]))
    return {
        **common,
        "model_path": str(model["path"]),
        "backend": "om",
        "sample_rate": int(output.get("default_sample_rate", 48_000)),
        "audio_device_id": output.get("index", output.get("id")),
        "audio_device_name": output.get("name"),
        "audio_device_sample_rate": output.get("default_sample_rate"),
        "prebuffer": 2,
        "max_voices": int(values.get("max_voices", patch.get("polyphony", 4))),
        "audio_latency_ms": 20.0,
        "velocity_curve": float(parameters.get("velocity_curve", 0.55)),
        "pitch_shift": float(parameters.get("transpose", 0)),
        "output_gain_db": float(parameters.get("output_gain_db", 0.0)),
        "reverb_wet": float(parameters.get("reverb", 0.15)),
        "harmonic_gain": float(parameters.get("harmonic_gain", 1.0)),
        "noise_gain": float(parameters.get("noise_gain", 1.0)),
        "attack": float(parameters.get("attack", 0.02)),
        "release": float(parameters.get("release", 1.2)),
    }


def map_parameters(engine: str, values: dict[str, object]) -> dict[str, object]:
    aliases = {
        "master_volume": "output_gain_db",
        "volume": "output_gain_db",
    }
    mapped = {aliases.get(name, name): value for name, value in values.items()}
    if engine == "piano-ddsp":
        if "reverb" in mapped:
            mapped["reverb_mix"] = mapped.pop("reverb")
    else:
        if "reverb" in mapped:
            mapped["reverb_wet"] = mapped.pop("reverb")
        if "transpose" in mapped:
            mapped["pitch_shift"] = mapped.pop("transpose")
    return mapped


_SESSION_METADATA_KEYS = frozenset(
    {"patch_id", "audio_device_id_public", "bundle_id"}
)


def _runtime_payload(config: dict[str, object]) -> dict[str, object]:
    """Remove unified-session metadata before calling a legacy runtime."""
    return {
        key: value
        for key, value in config.items()
        if key not in _SESSION_METADATA_KEYS
    }


class RuntimeAdapter(Protocol):
    engine: str

    def start(self, config: dict[str, object]) -> dict[str, object]: ...
    def stop(self) -> dict[str, object]: ...
    def status(self) -> dict[str, object]: ...
    def note_on(self, source: str, note: int, velocity: int) -> None: ...
    def note_off(self, source: str, note: int) -> None: ...
    def sustain(self, source: str, enabled: bool) -> None: ...
    def pitch_bend(self, value: int) -> None: ...
    def release_source(self, source: str) -> None: ...
    def panic(self) -> None: ...
    def parameters(self, values: dict[str, object]) -> object: ...
    def player(self, action: str, **values: object) -> object: ...
    def record_start(self, recording_id: str) -> object: ...
    def record_stop(self) -> object: ...
    def monitor(self, source: str, enabled: bool) -> None: ...


class PianoRuntimeAdapter:
    engine = "piano-ddsp"

    def __init__(self, controller: PianoDdspController) -> None:
        self.controller = controller

    def start(self, config: dict[str, object]) -> dict[str, object]:
        return self.controller.start(_runtime_payload(config), manage_resource=False)

    def stop(self) -> dict[str, object]:
        return self.controller.stop()

    def status(self) -> dict[str, object]:
        return self.controller.status()

    def note_on(self, source: str, note: int, velocity: int) -> None:
        self.controller.command("note", source=source, note=note, velocity=velocity, on=True)

    def note_off(self, source: str, note: int) -> None:
        self.controller.command("note", source=source, note=note, velocity=0, on=False)

    def sustain(self, source: str, enabled: bool) -> None:
        self.controller.command("cc", source=source, controller=64, value=127 if enabled else 0)

    def pitch_bend(self, value: int) -> None:
        del value  # Piano-DDSP checkpoint has no continuous pitch-bend input.

    def release_source(self, source: str) -> None:
        self.controller.command("release_source", source=source)

    def panic(self) -> None:
        self.controller.command("panic")

    def parameters(self, values: dict[str, object]) -> object:
        return self.controller.command("parameters", values=map_parameters(self.engine, values))

    def player(self, action: str, **values: object) -> object:
        return self.controller.command("player", action=action, values=values)

    def record_start(self, recording_id: str) -> object:
        return self.controller.command("record_start", recording_id=recording_id)

    def record_stop(self) -> object:
        return self.controller.command("record_stop")

    def monitor(self, source: str, enabled: bool) -> None:
        self.controller.set_monitor(source, enabled)


class DdspRuntimeAdapter:
    engine = "ddsp-vst"

    def __init__(self, controller: DdspVstSessionController) -> None:
        self.controller = controller

    def start(self, config: dict[str, object]) -> dict[str, object]:
        return self.controller.start(_runtime_payload(config), manage_resource=False)

    def stop(self) -> dict[str, object]:
        return self.controller.stop()

    def status(self) -> dict[str, object]:
        return self.controller.status()

    def note_on(self, source: str, note: int, velocity: int) -> None:
        self.controller.note_on(source, note, velocity)

    def note_off(self, source: str, note: int) -> None:
        self.controller.note_off(source, note)

    def sustain(self, source: str, enabled: bool) -> None:
        self.controller.sustain(source, enabled)

    def pitch_bend(self, value: int) -> None:
        self.controller.pitch_bend(value)

    def release_source(self, source: str) -> None:
        self.controller.release_source(source)

    def panic(self) -> None:
        router = self.controller._require_router()
        router.all_notes_off()

    def parameters(self, values: dict[str, object]) -> object:
        return self.controller.update_parameters(
            {key: float(value) for key, value in map_parameters(self.engine, values).items()}
        )

    def player(self, action: str, **values: object) -> object:
        return self.controller.player_command(action, **values)

    def record_start(self, recording_id: str) -> object:
        return self.controller.record_start(recording_id)

    def record_stop(self) -> object:
        return self.controller.record_stop()

    def monitor(self, source: str, enabled: bool) -> None:
        self.controller.set_monitor(source, enabled)


class RealtimeSessionController:
    """Own one realtime resource lease across cross-runtime patch switches."""

    OWNER = REALTIME_OWNER

    def __init__(
        self,
        coordinator: ResourceCoordinator,
        piano: PianoDdspController | None = None,
        ddsp: DdspVstSessionController | None = None,
        *,
        adapters: dict[str, RuntimeAdapter] | None = None,
        catalog_provider: Callable[[], dict[str, object]] = build_realtime_catalog,
        config_resolver: Callable[[dict[str, object], dict[str, object], dict[str, object]], dict[str, object]] = resolve_runtime_config,
    ) -> None:
        self.coordinator = coordinator
        self.catalog_provider = catalog_provider
        self.config_resolver = config_resolver
        if adapters is None:
            if piano is None or ddsp is None:
                raise ValueError("Piano and DDSP controllers are required")
            adapters = {
                "piano-ddsp": PianoRuntimeAdapter(piano),
                "ddsp-vst": DdspRuntimeAdapter(ddsp),
            }
        self.adapters = adapters
        self._lock = threading.RLock()
        self._transition_lock = threading.Lock()
        self._subscribers: list[queue.Queue[dict[str, object]]] = []
        self._adapter: RuntimeAdapter | None = None
        self._patch: dict[str, object] | None = None
        self._runtime_config: dict[str, object] = {}
        self._session_options: dict[str, object] = {}
        self._session_id: str | None = None
        self._state = "stopped"
        self._monitor_sources: set[str] = set()
        self._last_switch: dict[str, object] | None = None
        if piano is not None:
            self._start_relay("piano-ddsp", piano.subscribe())
        if ddsp is not None:
            self._start_relay("ddsp-vst", ddsp.subscribe())

    def _start_relay(
        self, engine: str, source: queue.Queue[dict[str, object]]
    ) -> None:
        threading.Thread(
            target=self._relay_loop,
            args=(engine, source),
            name=f"realtime-{engine}-events",
            daemon=True,
        ).start()

    def _relay_loop(
        self, engine: str, source: queue.Queue[dict[str, object]]
    ) -> None:
        while True:
            payload = source.get()
            if payload.get("event") not in {"monitor", "note", "error"}:
                continue
            with self._lock:
                active_engine = self._patch.get("_engine") if self._patch else None
            if active_engine == engine:
                self.publish(payload)

    @property
    def running(self) -> bool:
        with self._lock:
            return self._adapter is not None and bool(self._adapter.status().get("running"))

    def subscribe(self) -> queue.Queue[dict[str, object]]:
        target: queue.Queue[dict[str, object]] = queue.Queue(maxsize=64)
        with self._lock:
            self._subscribers.append(target)
        return target

    def unsubscribe(self, target: queue.Queue[dict[str, object]]) -> None:
        with self._lock:
            if target in self._subscribers:
                self._subscribers.remove(target)

    def publish(self, payload: dict[str, object]) -> None:
        with self._lock:
            targets = list(self._subscribers)
        for target in targets:
            try:
                target.put_nowait(payload)
            except queue.Full:
                try:
                    target.get_nowait()
                    target.put_nowait(payload)
                except queue.Empty:
                    pass

    def _publish_status(self) -> None:
        self.publish({"event": "status", "data": self.status()})

    def start(self, options: dict[str, object]) -> dict[str, object]:
        with self._transition_lock:
            if self.running:
                return self.status()
            data = self.catalog_provider()
            patch = _find_patch(str(options["patch_id"]), data)
            config = self.config_resolver(patch, options, data)
            adapter = self.adapters[str(patch["_engine"])]
            self.coordinator.acquire(self.OWNER)
            with self._lock:
                self._state = "starting"
                self._session_id = uuid4().hex
                self._last_switch = None
            try:
                adapter.start(config)
            except BaseException:
                with self._lock:
                    self._state = "failed"
                    self._session_id = None
                self.coordinator.release(self.OWNER)
                self._publish_status()
                raise
            with self._lock:
                self._adapter = adapter
                self._patch = patch
                self._runtime_config = config
                self._session_options = copy.deepcopy(options)
                self._state = "running"
            self._publish_status()
            return self.status()

    @staticmethod
    def _player_snapshot(adapter: RuntimeAdapter) -> dict[str, object]:
        player = adapter.status().get("player")
        return dict(player) if isinstance(player, dict) else {}

    @staticmethod
    def _restore_player(adapter: RuntimeAdapter, snapshot: dict[str, object]) -> None:
        path = snapshot.get("path")
        if not path:
            return
        adapter.player("load", path=str(path))
        adapter.player("tempo", value=float(snapshot.get("tempo", 1.0)))
        adapter.player("loop", enabled=bool(snapshot.get("loop", False)))
        adapter.player("seek", position_seconds=float(snapshot.get("position_seconds", 0.0)))
        if snapshot.get("state") == "playing":
            adapter.player("play")

    def switch(self, options: dict[str, object]) -> dict[str, object]:
        with self._transition_lock:
            with self._lock:
                old_adapter = self._adapter
                old_patch = self._patch
                old_config = dict(self._runtime_config)
                old_options = copy.deepcopy(self._session_options)
            if old_adapter is None or old_patch is None:
                raise RuntimeError("Realtime session is not running")
            recording = old_adapter.status().get("recording")
            if isinstance(recording, dict) and recording.get("active"):
                raise RuntimeError("Stop recording before switching patches")

            data = self.catalog_provider()
            target_patch = _find_patch(str(options["patch_id"]), data)
            merged_options = {**old_options, **options}
            target_config = self.config_resolver(target_patch, merged_options, data)
            target_adapter = self.adapters[str(target_patch["_engine"])]
            if target_patch["patch_id"] == old_patch["patch_id"]:
                values = dict(options.get("parameters") or {})
                if values:
                    old_adapter.parameters(values)
                return self.status()

            player = self._player_snapshot(old_adapter)
            if player.get("state") == "playing":
                old_adapter.player("pause")
                player = self._player_snapshot(old_adapter)
                player["state"] = "playing"
            old_adapter.panic()
            with self._lock:
                self._state = "switching"
            started = time.monotonic()
            old_adapter.stop()
            try:
                target_adapter.start(target_config)
                self._restore_player(target_adapter, player)
                for source in list(self._monitor_sources):
                    target_adapter.monitor(source, True)
            except BaseException as switch_error:
                try:
                    target_adapter.stop()
                except BaseException:
                    pass
                try:
                    old_adapter.start(old_config)
                    self._restore_player(old_adapter, player)
                    for source in list(self._monitor_sources):
                        old_adapter.monitor(source, True)
                except BaseException as rollback_error:
                    with self._lock:
                        self._adapter = None
                        self._patch = None
                        self._runtime_config = {}
                        self._session_options = {}
                        self._session_id = None
                        self._state = "failed"
                        self._last_switch = {
                            "ok": False,
                            "rolled_back": False,
                            "error": str(switch_error),
                            "rollback_error": str(rollback_error),
                        }
                    self.coordinator.release(self.OWNER)
                    self._publish_status()
                    raise RuntimeError(
                        f"Patch switch failed and rollback failed: {switch_error}; {rollback_error}"
                    ) from rollback_error
                with self._lock:
                    self._adapter = old_adapter
                    self._patch = old_patch
                    self._runtime_config = old_config
                    self._session_options = old_options
                    self._state = "running"
                    self._last_switch = {
                        "ok": False,
                        "rolled_back": True,
                        "error": str(switch_error),
                        "duration_ms": (time.monotonic() - started) * 1000.0,
                    }
                self._publish_status()
                return self.status()

            with self._lock:
                self._adapter = target_adapter
                self._patch = target_patch
                self._runtime_config = target_config
                self._session_options = merged_options
                self._state = "running"
                self._last_switch = {
                    "ok": True,
                    "rolled_back": False,
                    "duration_ms": (time.monotonic() - started) * 1000.0,
                }
            self._publish_status()
            return self.status()

    def update_parameters(self, values: dict[str, object]) -> dict[str, object]:
        adapter = self._require_adapter()
        adapter.parameters(values)
        with self._lock:
            parameters = dict(self._session_options.get("parameters") or {})
            parameters.update(values)
            self._session_options["parameters"] = parameters
        self._publish_status()
        return self.status()

    def note_on(self, source: str, note: int, velocity: int) -> None:
        self._require_adapter().note_on(source, note, velocity)

    def note_off(self, source: str, note: int) -> None:
        self._require_adapter().note_off(source, note)

    def sustain(self, source: str, enabled: bool) -> None:
        self._require_adapter().sustain(source, enabled)

    def pitch_bend(self, value: int) -> None:
        self._require_adapter().pitch_bend(value)

    def release_source(self, source: str) -> None:
        with self._lock:
            adapter = self._adapter
        if adapter is not None:
            adapter.release_source(source)

    def panic(self) -> dict[str, object]:
        self._require_adapter().panic()
        self._publish_status()
        return self.status()

    def player(self, action: str, **values: object) -> object:
        result = self._require_adapter().player(action, **values)
        self._publish_status()
        return result

    def record_start(self) -> dict[str, object]:
        recording_id = f"realtime-{uuid4().hex}"
        self._require_adapter().record_start(recording_id)
        self._publish_status()
        return {
            "id": recording_id,
            "download_url": f"/api/v1/realtime/recordings/{recording_id}",
        }

    def record_stop(self) -> dict[str, object] | None:
        result = self._require_adapter().record_stop()
        self._publish_status()
        if not result:
            return None
        recording_id = str(dict(result).get("id") or Path(str(dict(result).get("path", ""))).stem)
        return {
            "id": recording_id,
            "download_url": f"/api/v1/realtime/recordings/{recording_id}",
        }

    def monitor(self, source: str, enabled: bool) -> None:
        adapter = self._require_adapter()
        adapter.monitor(source, enabled)
        with self._lock:
            if enabled:
                self._monitor_sources.add(source)
            else:
                self._monitor_sources.discard(source)

    def stop(self) -> dict[str, object]:
        with self._transition_lock:
            with self._lock:
                adapter = self._adapter
                self._state = "stopping" if adapter is not None else "stopped"
            stop_error: BaseException | None = None
            if adapter is not None:
                try:
                    adapter.record_stop()
                except BaseException as exc:
                    stop_error = exc
                try:
                    adapter.panic()
                except BaseException as exc:
                    stop_error = stop_error or exc
                try:
                    adapter.stop()
                except BaseException as exc:
                    stop_error = stop_error or exc
            try:
                with self._lock:
                    self._adapter = None
                    self._patch = None
                    self._runtime_config = {}
                    self._session_options = {}
                    self._session_id = None
                    self._monitor_sources.clear()
                    self._state = "stopped"
            finally:
                self.coordinator.release(self.OWNER)
            self._publish_status()
            if stop_error is not None:
                raise stop_error
            return self.status()

    def _require_adapter(self) -> RuntimeAdapter:
        with self._lock:
            adapter = self._adapter
        if adapter is None:
            raise RuntimeError("Realtime session is not running")
        return adapter

    def status(self) -> dict[str, object]:
        with self._lock:
            adapter = self._adapter
            patch = self._patch
            session_id = self._session_id
            state = self._state
            config = dict(self._runtime_config)
            last_switch = copy.deepcopy(self._last_switch)
        raw = adapter.status() if adapter is not None else {}
        if adapter is not None and state == "running" and not bool(raw.get("running")):
            with self._lock:
                if self._adapter is adapter and self._state == "running":
                    self._adapter = None
                    self._session_id = None
                    self._state = "failed"
                    state = "failed"
                    session_id = None
            self.coordinator.release(self.OWNER)
            adapter = None
        midi = raw.get("midi") if isinstance(raw.get("midi"), dict) else {}
        active_notes = raw.get("active_notes", midi.get("active_notes", []))
        return {
            "state": state,
            "running": adapter is not None and bool(raw.get("running")),
            "session_id": session_id,
            "patch_id": patch.get("patch_id") if patch else None,
            "patch": _public_patch(patch) if patch else None,
            "active_notes": active_notes,
            "audio_device_id": config.get("audio_device_id_public"),
            "latency_profile": config.get("latency_profile"),
            "player": raw.get("player"),
            "recording": raw.get("recording", {"active": False, "id": None}),
            "metrics": raw.get("metrics", {}),
            "audio": raw.get("audio", {}),
            "midi": midi,
            "diagnostics": {
                "engine": patch.get("_engine") if patch else None,
                "runtime": raw,
            },
            "last_switch": last_switch,
        }


def resolve_unified_recording(recording_id: str) -> Path:
    for resolver in (resolve_realtime_recording, resolve_piano_recording):
        try:
            return resolver(recording_id)
        except KeyError:
            continue
    raise KeyError(recording_id)
