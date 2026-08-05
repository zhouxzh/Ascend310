from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import sys
import time
from typing import Literal, Mapping, Optional, Union
from uuid import uuid4
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from pyacl_ddsp import shutdown_persistent_runtimes
from realtime_ddsp import query_audio_devices, query_midi_devices

from .bluetooth import (
    connect_bluetooth_audio_device,
    disconnect_bluetooth_audio_device,
    query_bluetooth_audio_devices,
    scan_bluetooth_audio_devices,
)
from .core import (
    JOB_ROOT,
    REPORT_ROOT,
    ROOT,
    UPLOAD_ROOT,
    JobManager,
    ResourceBusyError,
    ResourceCoordinator,
    catalog,
    clear_catalog_cache,
    is_ascend_board,
    public_catalog,
    resolve_artifact,
    resolve_catalog_item,
    system_status,
    utc_timestamp,
    validate_midi_ddsp_reverb_asset,
)
from .ddsp_vst_effect import (
    DEFAULT_PARAMETERS as DDSP_VST_EFFECT_DEFAULTS,
    FEATURE_OM_PATH,
    FEATURE_OM_SHA256,
    PARAMETER_RANGES as DDSP_VST_EFFECT_PARAMETER_RANGES,
    DdspVstEffectController,
    sha256_file,
)
from .library import MidiDdspLibrary
from .live import DdspVstSessionController
from .midi_analysis import (
    MidiValidationError,
    analyze_midi,
    analyze_midi_piano_roll,
    analyze_midi_voices,
    midi_file_sha256,
)
from .piano import (
    PianoDdspController,
)
from .speaker import (
    AudioInputTestController,
    NO_AUDIO_OUTPUT,
    SpeakerTestController,
    is_pulse_output_event,
    query_audio_inputs,
    query_ddsp_vst_audio_outputs,
    query_midi_ddsp_audio_outputs,
    query_piano_audio_outputs,
)
from .realtime_session import (
    RealtimeSessionController,
    public_realtime_catalog,
    resolve_unified_recording,
)


MAX_MIDI_BYTES = 10 * 1024 * 1024
WEB_DIST = ROOT / "webui" / "dist"


@asynccontextmanager
async def lifespan(_: FastAPI):
    _sync_library()
    yield
    if realtime.running:
        realtime.stop()
    if piano.running:
        piano.stop()
    if ddsp_vst.running:
        ddsp_vst.stop()
    if speaker.running:
        speaker.stop()
    if audio_input_test.running:
        audio_input_test.stop()
    if ddsp_vst_effect.running:
        ddsp_vst_effect.stop()
    shutdown_persistent_runtimes(suppress_errors=True)


app = FastAPI(title="MIDI-DDSP Studio API", version="0.1.0", lifespan=lifespan)
coordinator = ResourceCoordinator()
library = MidiDdspLibrary(REPORT_ROOT, JOB_ROOT)
jobs = JobManager(coordinator, terminal_callback=library.index_job)
ddsp_vst = DdspVstSessionController(coordinator)
speaker = SpeakerTestController(coordinator)
audio_input_test = AudioInputTestController(coordinator)
piano = PianoDdspController(coordinator)
realtime = RealtimeSessionController(coordinator, piano, ddsp_vst)
ddsp_vst_effect = DdspVstEffectController(coordinator)


def _origin_allowed(headers: object) -> bool:
    get = getattr(headers, "get", None)
    if get is None:
        return False
    origin = str(get("origin", "") or "").rstrip("/")
    if not origin:
        return True
    explicit = {
        item.strip().rstrip("/")
        for item in os.environ.get("MIDI_DDSP_ALLOWED_ORIGINS", "").split(",")
        if item.strip()
    }
    if origin in explicit:
        return True
    try:
        origin_host = urlsplit(origin).netloc.casefold()
    except ValueError:
        return False
    request_host = str(get("host", "") or "").casefold()
    return bool(origin_host) and origin_host == request_host


@app.middleware("http")
async def enforce_same_origin(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"} and not _origin_allowed(
        request.headers
    ):
        return JSONResponse(status_code=403, content={"detail": "Cross-origin request denied"})
    return await call_next(request)


class ApiModel(BaseModel):
    model_config = ConfigDict(protected_namespaces=())


class MidiDdspJobRequest(ApiModel):
    mode: Literal["play", "render"] = "play"
    force_render: bool = False
    midi_id: str
    model_bundle_id: str
    instrument_id: int = Field(0, ge=0, le=12)
    voice_analysis_id: Optional[str] = Field(default=None, min_length=64, max_length=64)
    voice_instruments: Optional[dict[str, int]] = None
    seed: int = Field(20260724, ge=0, le=2_147_483_647)
    audio_device_id: Optional[str] = None
    sample_rate: int = Field(48_000, ge=8_000, le=192_000)
    prebuffer: int = Field(6, ge=1, le=64)
    audio_latency_ms: float = Field(80.0, gt=0, le=1000)
    output_gain_db: float = Field(0.0, ge=-60, le=0)
    tail_seconds: float = Field(2.0, ge=0, le=20)
    device_id: int = Field(0, ge=0, le=63)


class MidiDdspPlaybackRequest(ApiModel):
    audio_device_id: Optional[str] = None
    latency_ms: float = Field(40.0, ge=5.0, le=500.0)
    output_gain_db: float = Field(0.0, ge=-60.0, le=0.0)


class MidiDdspLibraryPreferenceRequest(ApiModel):
    preferred_render_id: Optional[str] = Field(default=None, min_length=1, max_length=128)


class SpeakerTestRequest(ApiModel):
    audio_device_id: str = Field(min_length=1, max_length=256)
    channel_mode: Literal["left", "both", "right"] = "both"
    frequency_hz: float = Field(440.0, ge=100.0, le=4_000.0)
    level_db: float = Field(-18.0, ge=-50.0, le=-3.0)
    duration_seconds: float = Field(3.0, ge=0.5, le=10.0)


class AudioInputTestRequest(ApiModel):
    audio_input_id: str = Field(min_length=1, max_length=256)
    duration_seconds: float = Field(3.0, ge=0.5, le=10.0)
    threshold_dbfs: float = Field(-45.0, ge=-80.0, le=-20.0)


class BluetoothScanRequest(ApiModel):
    duration_seconds: float = Field(8.0, ge=2.0, le=30.0)


class BluetoothDeviceRequest(ApiModel):
    address: str = Field(min_length=17, max_length=17)
    pair: bool = True
    trust: bool = True


class RealtimeStartRequest(ApiModel):
    patch_id: str = Field(min_length=1, max_length=128)
    audio_device_id: Optional[str] = Field(default=None, max_length=256)
    midi_port: Optional[str] = Field(default=None, max_length=256)
    latency_profile: Literal["low", "balanced", "safe"] = "balanced"
    parameters: dict[str, Union[float, int]] = Field(default_factory=dict)
    device_id: int = Field(0, ge=0, le=63)
    seed: int = Field(0, ge=0, le=2_147_483_647)
    max_voices: int = Field(4, ge=1, le=8)


class RealtimeSwitchRequest(ApiModel):
    patch_id: str = Field(min_length=1, max_length=128)
    audio_device_id: Optional[str] = Field(default=None, max_length=256)
    parameters: dict[str, Union[float, int]] = Field(default_factory=dict)


class RealtimeParametersRequest(ApiModel):
    values: dict[str, Union[float, int]] = Field(default_factory=dict)


class DdspVstEffectStartRequest(ApiModel):
    model_config = ConfigDict(protected_namespaces=(), extra="forbid")
    model_id: str = Field(min_length=1, max_length=128)
    audio_input_id: str = Field(min_length=1, max_length=256)
    audio_output_id: str = Field(min_length=1, max_length=256)
    parameters: dict[str, Union[float, int]] = Field(default_factory=dict)
    device_id: int = Field(0, ge=0, le=63)


class DdspVstEffectParametersRequest(ApiModel):
    model_config = ConfigDict(protected_namespaces=(), extra="forbid")
    values: dict[str, Union[float, int]] = Field(default_factory=dict)


def _http_error(exc: BaseException) -> HTTPException:
    if isinstance(exc, ResourceBusyError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc))
    if isinstance(exc, MidiValidationError):
        return HTTPException(
            status_code=422,
            detail={"code": exc.code, "message": str(exc)},
        )
    return HTTPException(status_code=400, detail=str(exc))


def _require_board() -> None:
    if not is_ascend_board():
        raise HTTPException(
            status_code=409,
            detail="Ascend execution is disabled outside the target board",
        )


@app.get("/api/v1/status")
def get_status() -> dict[str, object]:
    return {
        **system_status(coordinator.owner),
        "realtime": realtime.status(),
        "speaker_test": speaker.status(),
        "audio_input_test": audio_input_test.status(),
        "ddsp_vst_effect": ddsp_vst_effect.status(),
        "job_count": len(jobs.list()),
    }


@app.get("/api/v1/catalog")
def get_catalog() -> dict[str, object]:
    return public_catalog()


@app.get("/api/v1/audio-inputs")
def get_audio_inputs() -> dict[str, object]:
    try:
        inputs = query_audio_inputs()
        return {
            "available": any(item["available"] for item in inputs),
            "devices": inputs,
            "error": None,
        }
    except Exception as exc:
        return {"available": False, "devices": [], "error": str(exc)}


@app.get("/api/v1/midi-ddsp/audio-devices")
def get_midi_ddsp_audio_devices() -> dict[str, object]:
    try:
        devices = query_midi_ddsp_audio_outputs(query_audio_devices)
        return {
            "available": bool(devices),
            "devices": devices,
            "error": None if devices else NO_AUDIO_OUTPUT,
        }
    except Exception as exc:
        return {"available": False, "devices": [], "error": str(exc)}


@app.get("/api/v1/midi-ports")
def get_midi_ports() -> dict[str, object]:
    try:
        return {"available": True, "ports": query_midi_devices(), "error": None}
    except Exception as exc:
        return {"available": False, "ports": [], "error": str(exc)}


def _effect_control_models() -> list[dict[str, object]]:
    by_instrument: dict[str, dict[str, object]] = {}
    for model in catalog().get("ddsp_vst_models", []):
        if model.get("backend") != "om":
            continue
        instrument = str(model.get("instrument", ""))
        current = by_instrument.get(instrument)
        if current is None or (
            model.get("precision") == "mixed_float16"
            and current.get("precision") != "mixed_float16"
        ):
            by_instrument[instrument] = model
    return [by_instrument[name] for name in sorted(by_instrument)]


def _public_effect_model(model: Mapping[str, object]) -> dict[str, object]:
    return {key: value for key, value in model.items() if key != "path"}


def _default_device_id(devices: list[dict[str, object]], marker: str) -> str | None:
    marker = marker.casefold()
    return next(
        (str(item["id"]) for item in devices if marker in str(item.get("name", "")).casefold()),
        None,
    )


@app.get("/api/v1/ddsp-vst-effect/catalog")
def get_ddsp_vst_effect_catalog() -> dict[str, object]:
    try:
        models = _effect_control_models()
        inputs = [
            item
            for item in query_audio_inputs()
            if item.get("backend") == "pulse"
            and item.get("type") == "capture"
            and item.get("available")
            and item.get("source_name")
        ]
        outputs = [
            item
            for item in query_ddsp_vst_audio_outputs(query_audio_devices)
            if item.get("backend") == "pulse" and item.get("sink_name")
        ]
        feature_hash_ok = FEATURE_OM_PATH.is_file() and sha256_file(FEATURE_OM_PATH) == FEATURE_OM_SHA256
        board = is_ascend_board()
        reasons = []
        if not board:
            reasons.append("DDSP-VST Effect 仅能在 Ascend 开发板上运行")
        if not feature_hash_ok:
            reasons.append("已发布的 Feature OM 缺失或 SHA256 不匹配")
        if len(models) < 11:
            reasons.append("11 个已发布 Control OM 未全部就绪")
        if _default_device_id(inputs, "UGREEN") is None:
            reasons.append("UGREEN 摄像头麦克风输入不可用")
        if _default_device_id(outputs, "EDIFIER") is None:
            reasons.append("EDIFIER 漫步者音频输出不可用")
        return {
            "available": not reasons,
            "error": "; ".join(reasons) if reasons else None,
            "backend": "acl/om",
            "feature_model": {
                "name": FEATURE_OM_PATH.name,
                "sha256": FEATURE_OM_SHA256,
                "available": feature_hash_ok,
                "contract": {
                    "audio": [1024],
                    "f0_scaled": [1],
                    "pw_scaled": [1],
                    "f0_hz": [1],
                    "pw_db": [1],
                },
            },
            "models": [_public_effect_model(item) for item in models],
            "audio_inputs": inputs,
            "audio_outputs": outputs,
            "default_model_id": next(
                (str(item["id"]) for item in models if item.get("instrument") == "Violin"),
                None,
            ),
            "default_audio_input_id": _default_device_id(inputs, "UGREEN"),
            "default_audio_output_id": _default_device_id(outputs, "EDIFIER"),
            "parameters": {
                name: {"min": bounds[0], "max": bounds[1], "default": DDSP_VST_EFFECT_DEFAULTS[name]}
                for name, bounds in DDSP_VST_EFFECT_PARAMETER_RANGES.items()
            },
        }
    except BaseException as exc:
        return {
            "available": False,
            "error": str(exc),
            "backend": "acl/om",
            "feature_model": {"name": FEATURE_OM_PATH.name, "sha256": FEATURE_OM_SHA256, "available": False},
            "models": [],
            "audio_inputs": [],
            "audio_outputs": [],
            "default_model_id": None,
            "default_audio_input_id": None,
            "default_audio_output_id": None,
            "parameters": {},
        }


@app.get("/api/v1/ddsp-vst-effect/status")
def get_ddsp_vst_effect_status() -> dict[str, object]:
    return ddsp_vst_effect.status()


@app.post("/api/v1/ddsp-vst-effect/start")
async def start_ddsp_vst_effect(
    payload: DdspVstEffectStartRequest,
) -> dict[str, object]:
    _require_board()
    try:
        model = next(item for item in _effect_control_models() if item["id"] == payload.model_id)
        input_device = next(
            item for item in query_audio_inputs() if item["id"] == payload.audio_input_id
        )
        output_device = next(
            item
            for item in query_ddsp_vst_audio_outputs(query_audio_devices)
            if item["id"] == payload.audio_output_id
        )
        if (
            input_device.get("backend") != "pulse"
            or input_device.get("type") != "capture"
            or not input_device.get("available")
            or not input_device.get("source_name")
        ):
            raise HTTPException(
                status_code=409,
                detail="DDSP-VST Effect requires an available physical PulseAudio capture input",
            )
        if output_device.get("backend") != "pulse" or not output_device.get("sink_name"):
            raise HTTPException(
                status_code=409,
                detail="DDSP-VST Effect requires a PulseAudio output sink",
            )
        config = {
            **payload.model_dump(),
            "feature_model_path": str(FEATURE_OM_PATH),
            "control_model_path": str(model["path"]),
            "pulse_source": str(input_device["source_name"]),
            "pulse_sink": str(output_device["sink_name"]),
            "input_device_name": str(input_device["name"]),
            "output_device_name": str(output_device["name"]),
        }
        return await asyncio.to_thread(ddsp_vst_effect.start, config)
    except StopIteration as exc:
        raise HTTPException(status_code=404, detail="Effect model or audio device not found") from exc
    except HTTPException:
        raise
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.post("/api/v1/ddsp-vst-effect/catalog/refresh")
def refresh_ddsp_vst_effect_catalog() -> dict[str, object]:
    try:
        catalog(refresh=True)
        return get_ddsp_vst_effect_catalog()
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.patch("/api/v1/ddsp-vst-effect/parameters")
async def patch_ddsp_vst_effect_parameters(
    payload: DdspVstEffectParametersRequest,
) -> dict[str, object]:
    try:
        return await asyncio.to_thread(ddsp_vst_effect.update_parameters, payload.values)
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.post("/api/v1/ddsp-vst-effect/stop")
async def stop_ddsp_vst_effect() -> dict[str, object]:
    try:
        return await asyncio.to_thread(ddsp_vst_effect.stop)
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.post("/api/v1/ddsp-vst-effect/calibrate")
async def calibrate_ddsp_vst_effect() -> dict[str, object]:
    try:
        return await asyncio.to_thread(ddsp_vst_effect.calibrate)
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.websocket("/api/v1/ddsp-vst-effect/events")
async def ddsp_vst_effect_events(websocket: WebSocket) -> None:
    if not _origin_allowed(websocket.headers):
        await websocket.close(code=1008, reason="Cross-origin WebSocket denied")
        return
    await websocket.accept()
    try:
        while True:
            status = ddsp_vst_effect.status()
            await websocket.send_json(
                {"event": "status", "data": status}
            )
            try:
                interval = 0.1 if status.get("running") else 0.5
                message = await asyncio.wait_for(
                    websocket.receive_json(), timeout=interval
                )
            except asyncio.TimeoutError:
                continue
            if message.get("event") == "ping":
                await websocket.send_json({"event": "pong"})
            else:
                await websocket.send_json(
                    {"event": "error", "message": "Only ping is accepted on this socket"}
                )
    except WebSocketDisconnect:
        pass


@app.get("/api/v1/realtime/catalog")
def get_realtime_catalog() -> dict[str, object]:
    try:
        return public_realtime_catalog()
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.get("/api/v1/realtime/status")
def get_realtime_status() -> dict[str, object]:
    return realtime.status()


@app.post("/api/v1/realtime/start")
async def start_realtime(payload: RealtimeStartRequest) -> dict[str, object]:
    _require_board()
    try:
        return await asyncio.to_thread(realtime.start, payload.model_dump())
    except HTTPException:
        raise
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.post("/api/v1/realtime/switch")
async def switch_realtime(payload: RealtimeSwitchRequest) -> dict[str, object]:
    _require_board()
    try:
        return await asyncio.to_thread(
            realtime.switch, payload.model_dump(exclude_none=True)
        )
    except HTTPException:
        raise
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.patch("/api/v1/realtime/parameters")
async def patch_realtime_parameters(
    payload: RealtimeParametersRequest,
) -> dict[str, object]:
    try:
        return await asyncio.to_thread(realtime.update_parameters, payload.values)
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.post("/api/v1/realtime/stop")
async def stop_realtime() -> dict[str, object]:
    try:
        return await asyncio.to_thread(realtime.stop)
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.post("/api/v1/realtime/panic")
async def panic_realtime() -> dict[str, object]:
    try:
        return await asyncio.to_thread(realtime.panic)
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.get("/api/v1/realtime/recordings/{recording_id}")
def get_realtime_recording(recording_id: str) -> FileResponse:
    try:
        path = resolve_unified_recording(recording_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Realtime recording not found") from exc
    return FileResponse(path, filename=path.name, media_type="audio/wav")


@app.websocket("/api/v1/realtime/events")
async def realtime_events(websocket: WebSocket) -> None:
    if not _origin_allowed(websocket.headers):
        await websocket.close(code=1008, reason="Cross-origin WebSocket denied")
        return
    await websocket.accept()
    source = f"browser-{uuid4().hex[:10]}"
    subscriber = realtime.subscribe()
    monitor_enabled = False
    last_status = 0.0
    try:
        await websocket.send_json({"event": "status", "data": realtime.status()})
        while True:
            while True:
                try:
                    outgoing = subscriber.get_nowait()
                except queue.Empty:
                    break
                if outgoing.get("event") != "monitor" or monitor_enabled:
                    await websocket.send_json(outgoing)
            now = time.monotonic()
            if now - last_status >= 2.0:
                last_status = now
                await websocket.send_json({"event": "heartbeat", "data": realtime.status()})
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            event = str(message.get("event", ""))
            try:
                result: object = None
                if event == "note_on":
                    result = await asyncio.to_thread(
                        realtime.note_on,
                        source,
                        int(message["note"]),
                        int(message.get("velocity", 100)),
                    )
                elif event == "note_off":
                    result = await asyncio.to_thread(
                        realtime.note_off, source, int(message["note"])
                    )
                elif event in {"cc", "control_change"}:
                    controller = int(message["controller"])
                    if controller != 64:
                        raise ValueError(f"Unsupported realtime controller: {controller}")
                    result = await asyncio.to_thread(
                        realtime.sustain, source, int(message["value"]) >= 64
                    )
                elif event == "sustain":
                    result = await asyncio.to_thread(
                        realtime.sustain, source, bool(message.get("enabled"))
                    )
                elif event == "pitch_bend":
                    result = await asyncio.to_thread(
                        realtime.pitch_bend, int(message.get("value", 0))
                    )
                elif event == "parameters":
                    values = message.get("values")
                    if not isinstance(values, dict):
                        raise ValueError("parameters event requires a values object")
                    result = await asyncio.to_thread(realtime.update_parameters, values)
                elif event == "player":
                    action = str(message["action"])
                    values = dict(message.get("values") or {})
                    if action == "load" and "midi_id" in values:
                        midi = resolve_catalog_item("midi_files", str(values.pop("midi_id")))
                        values["path"] = str(midi["path"])
                    result = await asyncio.to_thread(realtime.player, action, **values)
                elif event == "record_start":
                    result = await asyncio.to_thread(realtime.record_start)
                elif event == "record_stop":
                    result = await asyncio.to_thread(realtime.record_stop)
                elif event == "monitor":
                    monitor_enabled = bool(message.get("enabled", False))
                    result = await asyncio.to_thread(
                        realtime.monitor, source, monitor_enabled
                    )
                elif event in {"panic", "all_notes_off"}:
                    result = await asyncio.to_thread(realtime.panic)
                elif event == "ping":
                    await websocket.send_json({"event": "pong"})
                    continue
                else:
                    raise ValueError(f"Unknown realtime event: {event}")
                await websocket.send_json(
                    {"event": "ack", "request": event, "data": result}
                )
            except WebSocketDisconnect:
                raise
            except Exception as exc:
                try:
                    await websocket.send_json({"event": "error", "message": str(exc)})
                except (WebSocketDisconnect, RuntimeError) as disconnect:
                    raise WebSocketDisconnect(code=1006) from disconnect
    except WebSocketDisconnect:
        pass
    finally:
        if monitor_enabled and realtime.running:
            try:
                await asyncio.to_thread(realtime.monitor, source, False)
            except BaseException:
                pass
        if realtime.running:
            try:
                await asyncio.to_thread(realtime.release_source, source)
            except BaseException:
                pass
        realtime.unsubscribe(subscriber)


@app.get("/api/v1/speaker-test/status")
def get_speaker_test_status() -> dict[str, object]:
    return speaker.status()


@app.get("/api/v1/audio-input-test/status")
def get_audio_input_test_status() -> dict[str, object]:
    return audio_input_test.status()


@app.get("/api/v1/speaker-outputs")
def get_speaker_outputs() -> dict[str, object]:
    try:
        return {
            "available": True,
            "devices": query_piano_audio_outputs(query_audio_devices),
            "error": None,
        }
    except Exception as exc:
        return {"available": False, "devices": [], "error": str(exc)}


async def _send_audio_output_snapshot(
    websocket: WebSocket,
    event: str,
) -> None:
    try:
        devices = await asyncio.to_thread(
            query_piano_audio_outputs,
            query_audio_devices,
        )
        error = None
    except Exception as exc:
        devices = []
        error = str(exc)
    await websocket.send_json(
        {"event": event, "devices": devices, "error": error}
    )


@app.websocket("/api/v1/audio-output-events")
async def audio_output_events(websocket: WebSocket) -> None:
    if not _origin_allowed(websocket.headers):
        await websocket.close(code=1008, reason="Cross-origin WebSocket denied")
        return
    await websocket.accept()
    process: asyncio.subprocess.Process | None = None
    try:
        await _send_audio_output_snapshot(websocket, "snapshot")
        if shutil.which("pactl") is None:
            while True:
                await asyncio.sleep(5.0)
                await websocket.send_json({"event": "ping"})

        environment = dict(os.environ)
        environment["LC_ALL"] = "C"
        process = await asyncio.create_subprocess_exec(
            "pactl",
            "subscribe",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        if process.stdout is None:
            raise RuntimeError("Unable to read PulseAudio output events")
        while True:
            try:
                raw_event = await asyncio.wait_for(
                    process.stdout.readline(),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                await websocket.send_json({"event": "ping"})
                continue
            if not raw_event:
                detail = "PulseAudio event monitor stopped"
                if process.stderr is not None:
                    stderr = (await process.stderr.read()).decode(
                        "utf-8", errors="replace"
                    ).strip()
                    detail = stderr or detail
                await websocket.send_json({"event": "error", "error": detail})
                break
            message = raw_event.decode("utf-8", errors="replace").strip()
            if is_pulse_output_event(message):
                await _send_audio_output_snapshot(websocket, "audio_outputs")
    except (WebSocketDisconnect, ConnectionResetError):
        pass
    finally:
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=1.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()


@app.get("/api/v1/bluetooth-audio")
def get_bluetooth_audio() -> dict[str, object]:
    try:
        return {
            "available": True,
            **query_bluetooth_audio_devices(),
            "error": None,
        }
    except Exception as exc:
        return {
            "available": False,
            "controller": None,
            "devices": [],
            "error": str(exc),
        }


@app.post("/api/v1/bluetooth-audio/scan")
async def scan_bluetooth_audio(payload: BluetoothScanRequest) -> dict[str, object]:
    try:
        return {
            "available": True,
            **await asyncio.to_thread(
                scan_bluetooth_audio_devices,
                payload.duration_seconds,
            ),
            "error": None,
        }
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.post("/api/v1/bluetooth-audio/connect")
async def connect_bluetooth_audio(payload: BluetoothDeviceRequest) -> dict[str, object]:
    try:
        return await asyncio.to_thread(
            connect_bluetooth_audio_device,
            payload.address,
            pair=payload.pair,
            trust=payload.trust,
        )
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.post("/api/v1/bluetooth-audio/disconnect")
async def disconnect_bluetooth_audio(payload: BluetoothDeviceRequest) -> dict[str, object]:
    try:
        return await asyncio.to_thread(
            disconnect_bluetooth_audio_device,
            payload.address,
        )
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.post("/api/v1/speaker-test/start")
async def start_speaker_test(payload: SpeakerTestRequest) -> dict[str, object]:
    try:
        devices = query_piano_audio_outputs(query_audio_devices)
        device = next(
            item for item in devices if item["id"] == payload.audio_device_id
        )
        config = payload.model_dump()
        config.update(
            {
                "device_name": device["name"],
                "audio_device_id": device.get("index", payload.audio_device_id),
                "audio_backend": device.get("backend", "portaudio"),
                "pulse_sink": device.get("sink_name"),
                "max_output_channels": device["max_output_channels"],
                "default_sample_rate": device["default_sample_rate"],
                "alsa_card": device.get("alsa_card"),
                "alsa_device": device.get("alsa_device"),
                "alsa_route_device_id": device.get("alsa_route_device_id"),
                "alsa_playback_level": device.get("alsa_playback_level"),
            }
        )
        return await asyncio.to_thread(speaker.start, config)
    except StopIteration as exc:
        raise HTTPException(status_code=404, detail="Audio device not found") from exc
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.post("/api/v1/speaker-test/stop")
async def stop_speaker_test() -> dict[str, object]:
    try:
        return await asyncio.to_thread(speaker.stop)
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.post("/api/v1/audio-input-test/start")
async def start_audio_input_test(payload: AudioInputTestRequest) -> dict[str, object]:
    try:
        inputs = query_audio_inputs()
        device = next(item for item in inputs if item["id"] == payload.audio_input_id)
        if device.get("type") != "capture" or not device.get("available"):
            raise HTTPException(
                status_code=409,
                detail="Only an available physical capture input can be tested",
            )
        config = payload.model_dump()
        config.update(
            {
                "device_name": device["name"],
                "audio_device_id": device.get("index", payload.audio_input_id),
                "audio_backend": device.get("backend", "portaudio"),
                "pulse_source": device.get("source_name"),
                "max_input_channels": device["max_input_channels"],
                "default_sample_rate": device["default_sample_rate"],
            }
        )
        return await asyncio.to_thread(audio_input_test.start, config)
    except StopIteration as exc:
        raise HTTPException(status_code=404, detail="Audio input not found") from exc
    except HTTPException:
        raise
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.post("/api/v1/audio-input-test/stop")
async def stop_audio_input_test() -> dict[str, object]:
    try:
        return await asyncio.to_thread(audio_input_test.stop)
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.post("/api/v1/midi-files")
async def upload_midi(
    request: Request,
    filename: str = Query(..., min_length=1, max_length=200),
) -> dict[str, object]:
    suffix = Path(filename).suffix.lower()
    if suffix not in {".mid", ".midi"}:
        raise HTTPException(status_code=415, detail="Only .mid and .midi files are accepted")
    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    target = UPLOAD_ROOT / f"{uuid4().hex}{suffix}"
    size = 0
    try:
        with target.open("wb") as output:
            async for chunk in request.stream():
                size += len(chunk)
                if size > MAX_MIDI_BYTES:
                    raise HTTPException(status_code=413, detail="MIDI file exceeds 10 MiB")
                output.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="MIDI file is empty")
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    try:
        analyze_midi(target)
    except Exception as exc:
        target.unlink(missing_ok=True)
        error = exc if isinstance(exc, MidiValidationError) else MidiValidationError(
            "invalid_midi", f"Invalid MIDI file: {exc}"
        )
        raise _http_error(error) from exc
    clear_catalog_cache()
    item = next(item for item in catalog()["midi_files"] if item["path"] == str(target.resolve()))
    result = {key: value for key, value in item.items() if key != "path"}
    result["original_name"] = filename
    return result


@app.get("/api/v1/midi-files/{midi_id}/voices")
def get_midi_voices(midi_id: str) -> dict[str, object]:
    try:
        midi = resolve_catalog_item("midi_files", midi_id)
        return analyze_midi_voices(Path(str(midi["path"])))
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.get("/api/v1/midi-files/{midi_id}/piano-roll")
def get_midi_piano_roll(midi_id: str) -> dict[str, object]:
    try:
        midi = resolve_catalog_item("midi_files", midi_id)
        result = analyze_midi_piano_roll(Path(str(midi["path"])))
        result["midi_id"] = midi_id
        return result
    except BaseException as exc:
        raise _http_error(exc) from exc


def _sync_library() -> None:
    midi_files = list(catalog().get("midi_files", []))
    library.synchronize(jobs.list(), midi_files, sha256_file=midi_file_sha256)


@app.get("/api/v1/midi-ddsp/library")
def get_midi_ddsp_library() -> dict[str, object]:
    try:
        _sync_library()
        return {"tracks": library.list_tracks()}
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.get("/api/v1/midi-ddsp/library/{source_id}/versions")
def get_midi_ddsp_library_versions(source_id: str) -> dict[str, object]:
    try:
        _sync_library()
        return {"source_id": source_id, "versions": library.versions(source_id)}
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.patch("/api/v1/midi-ddsp/library/{source_id}/preference")
def set_midi_ddsp_library_preference(
    source_id: str,
    payload: MidiDdspLibraryPreferenceRequest,
) -> dict[str, object]:
    try:
        _sync_library()
        return library.set_preference(
            source_id, payload.preferred_render_id, utc_timestamp()
        )
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.post("/api/v1/midi-ddsp/jobs")
def start_midi_ddsp_job(payload: MidiDdspJobRequest) -> dict[str, object]:
    _require_board()
    try:
        midi = resolve_catalog_item("midi_files", payload.midi_id)
        bundle = resolve_catalog_item("midi_ddsp_bundles", payload.model_bundle_id)
        if not bool(midi.get("midi_ddsp_supported")):
            raise MidiValidationError(
                str(midi.get("unsupported_code") or "unsupported_midi"),
                str(midi.get("unsupported_reason") or "MIDI file is not supported"),
            )
        voice_analysis = analyze_midi_voices(Path(str(midi["path"])))
        voice_instruments: dict[str, int] | None = None
        if payload.voice_instruments is not None:
            if payload.voice_analysis_id != voice_analysis["analysis_id"]:
                raise MidiValidationError(
                    "voice_analysis_stale",
                    "MIDI voice analysis changed; reload the voice assignments",
                )
            expected_voice_ids = {
                str(voice["id"])
                for group in voice_analysis["groups"]
                for voice in group["voices"]
            }
            supplied_voice_ids = set(payload.voice_instruments)
            if supplied_voice_ids != expected_voice_ids:
                missing = sorted(expected_voice_ids - supplied_voice_ids)
                extra = sorted(supplied_voice_ids - expected_voice_ids)
                raise MidiValidationError(
                    "voice_assignment_mismatch",
                    f"Voice assignment does not match analysis; missing={missing}, extra={extra}",
                )
            invalid = {
                voice_id: instrument_id
                for voice_id, instrument_id in payload.voice_instruments.items()
                if not 0 <= int(instrument_id) <= 12
            }
            if invalid:
                raise MidiValidationError(
                    "invalid_voice_instrument",
                    f"Voice instrument ids must be between 0 and 12: {invalid}",
                )
            voice_instruments = {
                voice_id: int(instrument_id)
                for voice_id, instrument_id in payload.voice_instruments.items()
            }
        voice_config_payload = {
            "analysis_id": voice_analysis["analysis_id"],
            "voice_instruments": voice_instruments,
            "fallback_instrument_id": payload.instrument_id,
        }
        voice_config_id = hashlib.sha256(
            json.dumps(
                voice_config_payload, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()
        reverb_ir = ROOT / "models" / "om" / "midi_ddsp_reverb_ir.npz"
        reverb_sha256 = validate_midi_ddsp_reverb_asset(reverb_ir)
        audio_output = None
        if payload.mode == "play" and payload.audio_device_id not in (None, ""):
            audio_output = next(
                output
                for output in query_ddsp_vst_audio_outputs(query_audio_devices)
                if output["id"] == payload.audio_device_id
            )
        command = [
            sys.executable,
            str(ROOT / "midi_ddsp_realtime.py"),
            "--midi",
            str(midi["path"]),
            "--instrument-id",
            str(payload.instrument_id),
            "--seed",
            str(payload.seed),
            "--cache-dir",
            str(REPORT_ROOT / "cache"),
            "--device-id",
            str(payload.device_id),
            "--sample-rate",
            str(payload.sample_rate),
            "--prebuffer",
            str(payload.prebuffer),
            "--audio-latency-ms",
            str(payload.audio_latency_ms),
            "--output-gain-db",
            str(payload.output_gain_db),
            "--tail-seconds",
            str(payload.tail_seconds),
            "--reverb-ir",
            str(reverb_ir),
            "--output",
            "{job_dir}/output.wav",
            "--report",
            "{job_dir}/report.json",
            "--json-events",
            "--web-control",
        ]
        if voice_instruments is not None:
            command.extend(
                [
                    "--voice-analysis-id",
                    str(voice_analysis["analysis_id"]),
                    "--voice-instruments-json",
                    json.dumps(
                        voice_instruments, sort_keys=True, separators=(",", ":")
                    ),
                ]
            )
        command.extend(["--model-bundle", str(bundle["manifest"])])
        if payload.force_render:
            command.append("--force-render")
        if payload.mode == "render":
            command.append("--render-only")
        elif audio_output is not None and audio_output.get("backend") == "pulse":
            command.extend(
                [
                    "--pulse-sink",
                    str(audio_output["sink_name"]),
                    "--pulse-device-name",
                    str(audio_output["name"]),
                ]
            )
        elif audio_output is not None:
            command.extend(["--audio-device", str(audio_output["id"])])
        job = jobs.start(
            f"midi-ddsp-{payload.mode}",
            command,
            metadata={
                "midi_name": midi["name"],
                "midi_id": payload.midi_id,
                **(
                    {"midi_sha256": str(midi["sha256"])}
                    if midi.get("sha256")
                    else (
                        {"midi_sha256": midi_file_sha256(Path(str(midi["path"])))}
                        if Path(str(midi["path"])).is_file()
                        else {}
                    )
                ),
                "model_bundle_id": bundle["id"],
                "model_bundle": bundle["name"],
                "model_architecture": bundle["architecture"],
                "quality_status": bundle["quality_status"],
                "instrument_id": payload.instrument_id,
                "instrument_ids": sorted(
                    set(voice_instruments.values())
                    if voice_instruments is not None
                    else {payload.instrument_id}
                ),
                "instrument_mode": (
                    "per_voice" if voice_instruments is not None else "global_fallback"
                ),
                "voice_analysis_id": voice_analysis["analysis_id"],
                "voice_config_id": voice_config_id,
                "voice_instruments": voice_instruments,
                "voice_separation": voice_analysis["algorithm"],
                "midi_ddsp_mode": midi.get("midi_ddsp_mode"),
                "voice_count": midi.get("voice_count", 1),
                "seed": payload.seed,
                "mode": payload.mode,
                "force_render": payload.force_render,
                "sample_rate": payload.sample_rate,
                "output_gain_db": payload.output_gain_db,
                "tail_seconds": payload.tail_seconds,
                "reverb": "google-midi-ddsp-original",
                "reverb_ir_sha256": reverb_sha256,
                "audio_output": audio_output["name"] if audio_output else "default",
            },
        )
        return job.public()
    except BaseException as exc:
        raise _http_error(exc) from exc


def _play_midi_ddsp_recording(
    source_job_id: str,
    payload: MidiDdspPlaybackRequest,
) -> dict[str, object]:
    _require_board()
    try:
        source_job = jobs.get(source_job_id)
        if not source_job.kind.startswith("midi-ddsp"):
            raise ValueError("Selected job is not a MIDI-DDSP recording")
        artifact_id = f"{source_job_id}--output.wav"
        wav_path = resolve_artifact(artifact_id)
        outputs = query_midi_ddsp_audio_outputs(query_audio_devices)
        if payload.audio_device_id not in (None, ""):
            audio_output = next(
                output for output in outputs if output["id"] == payload.audio_device_id
            )
        else:
            audio_output = next(
                (output for output in outputs if output.get("is_default")),
                outputs[0] if outputs else None,
            )
        if audio_output is None:
            raise RuntimeError("No audio output device is available")
        command = [
            sys.executable,
            "-m",
            "midi_ddsp_webui.wav_playback",
            "--input",
            str(wav_path),
            "--latency-ms",
            str(payload.latency_ms),
            "--output-gain-db",
            str(payload.output_gain_db),
            "--json-events",
        ]
        if audio_output.get("backend") == "pulse":
            command.extend(["--pulse-sink", str(audio_output["sink_name"])])
        elif audio_output.get("backend") == "alsa_mono":
            command.extend(
                [
                    "--alsa-device",
                    str(audio_output["alsa_device"]),
                    "--alsa-card",
                    str(audio_output.get("alsa_card", 0)),
                    "--alsa-route-device-id",
                    str(audio_output.get("alsa_route_device_id", 2)),
                    "--alsa-playback-level",
                    str(audio_output.get("alsa_playback_level", 10)),
                ]
            )
        else:
            raise RuntimeError("Selected audio output is unsupported for WAV playback")
        copied_metadata = {
            key: source_job.metadata[key]
            for key in (
                "midi_name",
                "midi_id",
                "model_bundle_id",
                "model_bundle",
                "instrument_id",
                "seed",
            )
            if key in source_job.metadata
        }
        job = jobs.start(
            "midi-ddsp-wav-playback",
            command,
            metadata={
                **copied_metadata,
                "mode": "replay",
                "source_job_id": source_job_id,
                "source_artifact_id": artifact_id,
                "audio_output": audio_output["name"],
                "output_gain_db": payload.output_gain_db,
            },
        )
        return job.public()
    except StopIteration as exc:
        raise HTTPException(status_code=404, detail="Audio output device not found") from exc
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.post("/api/v1/midi-ddsp/recordings/{source_job_id}/play")
def play_midi_ddsp_recording(
    source_job_id: str,
    payload: MidiDdspPlaybackRequest,
) -> dict[str, object]:
    return _play_midi_ddsp_recording(source_job_id, payload)


@app.post("/api/v1/midi-ddsp/library/versions/{render_id}/play")
def play_midi_ddsp_library_version(
    render_id: str,
    payload: MidiDdspPlaybackRequest,
) -> dict[str, object]:
    try:
        _sync_library()
        version = library.version(render_id)
        if not version["available"]:
            raise FileNotFoundError("Selected audio version is unavailable")
    except BaseException as exc:
        raise _http_error(exc) from exc
    return _play_midi_ddsp_recording(render_id, payload)


@app.get("/api/v1/jobs")
def list_jobs() -> dict[str, object]:
    return {"jobs": jobs.list()}


@app.get("/api/v1/jobs/{job_id}")
def get_job(job_id: str) -> dict[str, object]:
    try:
        return jobs.get(job_id).public()
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc


@app.post("/api/v1/jobs/{job_id}/{action}")
def control_job(job_id: str, action: Literal["pause", "resume", "stop"]) -> dict[str, object]:
    try:
        handler = {"pause": jobs.pause, "resume": jobs.resume, "stop": jobs.stop}[action]
        return handler(job_id).public()
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.websocket("/api/v1/events")
async def job_events(websocket: WebSocket) -> None:
    if not _origin_allowed(websocket.headers):
        await websocket.close(code=1008, reason="Cross-origin WebSocket denied")
        return
    await websocket.accept()
    subscriber = jobs.subscribe()
    try:
        await websocket.send_json({"event": "snapshot", "jobs": jobs.list()})
        while True:
            try:
                event = await asyncio.to_thread(subscriber.get, True, 1.0)
                await websocket.send_json(event)
            except queue.Empty:
                await websocket.send_json({"event": "ping"})
    except WebSocketDisconnect:
        pass
    finally:
        jobs.unsubscribe(subscriber)


@app.get("/api/v1/artifacts/{artifact_id}")
def get_artifact(artifact_id: str) -> FileResponse:
    try:
        path = resolve_artifact(artifact_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Artifact not found") from exc
    return FileResponse(path, filename=path.name)


if (WEB_DIST / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIST / "assets"), name="assets")


@app.get("/{path:path}", include_in_schema=False, response_model=None)
def frontend(path: str):
    candidate = (WEB_DIST / path).resolve()
    if WEB_DIST.resolve() in candidate.parents and candidate.is_file():
        return FileResponse(candidate)
    index = WEB_DIST / "index.html"
    if index.is_file():
        return FileResponse(index)
    return JSONResponse(
        status_code=503,
        content={"detail": "Frontend build not found. Run npm run build in webui."},
    )
