from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import hashlib
import json
from pathlib import Path
import queue
import sys
import time
from typing import Literal, Optional
from uuid import uuid4

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
    validate_midi_ddsp_reverb_asset,
)
from .live import DdspVstSessionController
from .midi_analysis import MidiValidationError, analyze_midi, analyze_midi_voices
from .piano import (
    PianoDdspController,
    piano_catalog,
    resolve_piano_bundle,
    resolve_recording,
)
from .speaker import (
    NO_AUDIO_OUTPUT,
    NO_REALTIME_AUDIO_OUTPUT,
    SpeakerTestController,
    query_audio_inputs,
    query_ddsp_vst_audio_outputs,
    query_midi_ddsp_audio_outputs,
    query_piano_audio_outputs,
)


MAX_MIDI_BYTES = 10 * 1024 * 1024
WEB_DIST = ROOT / "webui" / "dist"


@asynccontextmanager
async def lifespan(_: FastAPI):
    yield
    if piano.running:
        piano.stop()
    if ddsp_vst.running:
        ddsp_vst.stop()
    if speaker.running:
        speaker.stop()
    shutdown_persistent_runtimes(suppress_errors=True)


app = FastAPI(title="MIDI-DDSP Studio API", version="0.1.0", lifespan=lifespan)
coordinator = ResourceCoordinator()
jobs = JobManager(coordinator)
ddsp_vst = DdspVstSessionController(coordinator)
speaker = SpeakerTestController(coordinator)
piano = PianoDdspController(coordinator)


class ApiModel(BaseModel):
    model_config = ConfigDict(protected_namespaces=())


class DdspVstStartRequest(ApiModel):
    model_id: str
    audio_device_id: Optional[str] = None
    midi_port: Optional[str] = None
    latency_profile: Optional[Literal["low", "balanced", "safe"]] = None
    sample_rate: int = Field(48_000, ge=8_000, le=192_000)
    prebuffer: int = Field(6, ge=1, le=64)
    max_voices: int = Field(1, ge=1, le=8)
    audio_latency_ms: float = Field(80.0, gt=0, le=1000)
    pitch_shift: float = Field(0.0, ge=-24, le=24)
    harmonic_gain: float = Field(1.0, ge=0, le=1)
    noise_gain: float = Field(1.0, ge=0, le=1)
    output_gain_db: float = Field(-18.0, ge=-60, le=0)
    velocity_curve: float = Field(0.55, ge=0.25, le=2)
    attack: float = Field(0.02, ge=0.01, le=3)
    decay: float = Field(0.0, ge=0, le=3)
    sustain: float = Field(1.0, ge=0, le=1)
    release: float = Field(1.20, ge=0.01, le=5)
    input_pitch: float = Field(0.0, ge=-0.5, le=0.5)
    input_gain: float = Field(0.0, ge=-0.5, le=0.5)
    reverb_size: float = Field(0.4, ge=0, le=1)
    reverb_damping: float = Field(0.1, ge=0, le=1)
    reverb_wet: float = Field(0.0, ge=0, le=1)
    device_id: int = Field(0, ge=0, le=63)


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


class SpeakerTestRequest(ApiModel):
    audio_device_id: str = Field(min_length=1, max_length=256)
    channel_mode: Literal["left", "both", "right"] = "both"
    frequency_hz: float = Field(440.0, ge=100.0, le=4_000.0)
    level_db: float = Field(-18.0, ge=-50.0, le=-3.0)
    duration_seconds: float = Field(3.0, ge=0.5, le=10.0)


class BluetoothScanRequest(ApiModel):
    duration_seconds: float = Field(8.0, ge=2.0, le=30.0)


class BluetoothDeviceRequest(ApiModel):
    address: str = Field(min_length=17, max_length=17)
    pair: bool = True
    trust: bool = True


class PianoDdspStartRequest(ApiModel):
    bundle_id: Optional[str] = None
    model_id: str = "paper_ir"
    piano_year: int = Field(2018, ge=2000, le=2100)
    midi_port: Optional[str] = None
    audio_device_id: Optional[str] = None
    latency_profile: Literal["low", "balanced", "safe"] = "balanced"
    seed: int = Field(0, ge=0, le=2_147_483_647)
    velocity_curve: float = Field(1.0, ge=0.25, le=2.0)
    transpose: int = Field(0, ge=-24, le=24)
    output_gain_db: float = Field(-12.0, ge=-60.0, le=0.0)
    reverb_mix: float = Field(1.0, ge=0.0, le=1.0)
    device_id: int = Field(0, ge=0, le=63)


class PianoDdspParametersRequest(ApiModel):
    model_id: Optional[str] = None
    piano_year: Optional[int] = Field(None, ge=2000, le=2100)
    velocity_curve: Optional[float] = Field(None, ge=0.25, le=2.0)
    transpose: Optional[int] = Field(None, ge=-24, le=24)
    output_gain_db: Optional[float] = Field(None, ge=-60.0, le=0.0)
    reverb_mix: Optional[float] = Field(None, ge=0.0, le=1.0)
    pedal: Optional[bool] = None


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
        "piano_ddsp": piano.status(),
        "ddsp_vst": ddsp_vst.status(),
        "speaker_test": speaker.status(),
        "job_count": len(jobs.list()),
    }


@app.get("/api/v1/catalog")
def get_catalog() -> dict[str, object]:
    return public_catalog()


@app.get("/api/v1/audio-devices")
def get_audio_devices() -> dict[str, object]:
    try:
        devices = query_ddsp_vst_audio_outputs(query_audio_devices)
        return {
            "available": bool(devices),
            "devices": devices,
            "error": None if devices else NO_REALTIME_AUDIO_OUTPUT,
        }
    except Exception as exc:
        return {"available": False, "devices": [], "error": str(exc)}


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


@app.get("/api/v1/piano-ddsp/catalog")
def get_piano_ddsp_catalog() -> dict[str, object]:
    try:
        return piano_catalog()
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.get("/api/v1/piano-ddsp/audio-devices")
def get_piano_ddsp_audio_devices() -> dict[str, object]:
    try:
        devices = query_piano_audio_outputs(query_audio_devices)
        return {
            "available": bool(devices),
            "devices": devices,
            "error": None if devices else NO_AUDIO_OUTPUT,
        }
    except Exception as exc:
        return {"available": False, "devices": [], "error": str(exc)}


@app.get("/api/v1/piano-ddsp/status")
def get_piano_ddsp_status() -> dict[str, object]:
    return piano.status()


@app.post("/api/v1/piano-ddsp/start")
async def start_piano_ddsp(payload: PianoDdspStartRequest) -> dict[str, object]:
    _require_board()
    try:
        catalog_data = piano_catalog()
        bundles = list(catalog_data.get("bundles", []))
        bundle_id = payload.bundle_id
        if bundle_id is None:
            active_bundle_id = catalog_data.get("active_bundle_id")
            active = next(
                (
                    item
                    for item in bundles
                    if item.get("id") == active_bundle_id
                    and payload.model_id in item.get("models", [])
                ),
                None,
            )
            bundle_id = str(active["id"]) if active else next(
                str(item["id"])
                for item in bundles
                if payload.model_id in item.get("models", [])
            )
        bundle_manifest = resolve_piano_bundle(bundle_id)
        outputs = query_piano_audio_outputs(query_audio_devices)
        if payload.audio_device_id not in (None, ""):
            audio_output = next(
                item for item in outputs if item["id"] == payload.audio_device_id
            )
        else:
            audio_output = next(
                (item for item in outputs if item.get("is_default")),
                outputs[0] if outputs else None,
            )
        if audio_output is None:
            raise RuntimeError(NO_AUDIO_OUTPUT)
        midi_port = payload.midi_port
        if midi_port:
            ports = query_midi_devices()
            selected_port = next(
                (
                    item
                    for item in ports
                    if midi_port in {str(item.get("id")), str(item.get("port"))}
                ),
                None,
            )
            if selected_port is None:
                raise KeyError(f"MIDI input {midi_port!r} was not found")
            midi_port = str(selected_port.get("port") or selected_port.get("name"))
        audio_device: str | int | None = (
            audio_output.get("index")
            if audio_output.get("backend") == "portaudio"
            else audio_output.get("id")
        )
        config = {
            "bundle_manifest": str(bundle_manifest),
            "model_id": payload.model_id,
            "piano_year": payload.piano_year,
            "output_sample_rate": int(audio_output.get("default_sample_rate", 48_000)),
            "latency_profile": payload.latency_profile,
            "seed": payload.seed,
            "velocity_curve": payload.velocity_curve,
            "transpose": payload.transpose,
            "output_gain_db": payload.output_gain_db,
            "reverb_mix": payload.reverb_mix,
            "midi_port": midi_port,
            "audio_backend": str(audio_output.get("backend", "portaudio")),
            "pulse_sink": audio_output.get("sink_name"),
            "audio_device": audio_device,
            "is_bluetooth": bool(audio_output.get("is_bluetooth", False)),
            "alsa_card": audio_output.get("alsa_card"),
            "alsa_device": audio_output.get("alsa_device"),
            "alsa_route_device_id": audio_output.get("alsa_route_device_id"),
            "alsa_playback_level": audio_output.get("alsa_playback_level"),
            "device_id": payload.device_id,
            "recorder_root": str(REPORT_ROOT / "piano-ddsp"),
        }
        return await asyncio.to_thread(piano.start, config)
    except StopIteration as exc:
        raise HTTPException(status_code=404, detail="Piano-DDSP bundle or audio output not found") from exc
    except HTTPException:
        raise
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.post("/api/v1/piano-ddsp/stop")
async def stop_piano_ddsp() -> dict[str, object]:
    return await asyncio.to_thread(piano.stop)


@app.post("/api/v1/piano-ddsp/panic")
async def panic_piano_ddsp() -> dict[str, object]:
    try:
        await asyncio.to_thread(piano.command, "panic")
        return piano.status()
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.patch("/api/v1/piano-ddsp/parameters")
async def patch_piano_ddsp_parameters(
    payload: PianoDdspParametersRequest,
) -> dict[str, object]:
    values = payload.model_dump(exclude_none=True)
    try:
        await asyncio.to_thread(piano.command, "parameters", values=values)
        return piano.status()
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.get("/api/v1/piano-ddsp/recordings/{recording_id}")
def get_piano_ddsp_recording(recording_id: str) -> FileResponse:
    try:
        path = resolve_recording(recording_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Piano-DDSP recording not found") from exc
    return FileResponse(path, filename=path.name, media_type="audio/wav")


@app.websocket("/api/v1/piano-ddsp/events")
async def piano_ddsp_events(websocket: WebSocket) -> None:
    await websocket.accept()
    source = f"browser-{uuid4().hex[:10]}"
    subscriber = piano.subscribe()
    monitor_enabled = False
    last_status = 0.0
    try:
        await websocket.send_json({"event": "status", "data": piano.status()})
        while True:
            while True:
                try:
                    outgoing = subscriber.get_nowait()
                except queue.Empty:
                    break
                if outgoing.get("event") != "monitor" or monitor_enabled:
                    await websocket.send_json(outgoing)
            now = time.monotonic()
            if now - last_status >= 1.0:
                last_status = now
                await websocket.send_json({"event": "status", "data": piano.status()})
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=0.1)
            except asyncio.TimeoutError:
                continue
            event = str(message.get("event", ""))
            try:
                result: object = None
                if event in {"note_on", "note_off"}:
                    result = await asyncio.to_thread(
                        piano.command,
                        "note",
                        source=source,
                        note=int(message["note"]),
                        velocity=int(message.get("velocity", 100 if event == "note_on" else 0)),
                        on=event == "note_on",
                    )
                elif event in {"cc", "control_change"}:
                    result = await asyncio.to_thread(
                        piano.command,
                        "cc",
                        source=source,
                        controller=int(message["controller"]),
                        value=int(message["value"]),
                    )
                elif event == "sustain":
                    result = await asyncio.to_thread(
                        piano.command,
                        "cc",
                        source=source,
                        controller=64,
                        value=127 if bool(message.get("enabled")) else 0,
                    )
                elif event == "player":
                    action = str(message["action"])
                    values = dict(message.get("values") or {})
                    if action == "load" and "midi_id" in values:
                        midi = resolve_catalog_item("midi_files", str(values.pop("midi_id")))
                        values["path"] = str(midi["path"])
                    result = await asyncio.to_thread(
                        piano.command, "player", action=action, values=values
                    )
                elif event == "record_start":
                    recording_id = f"piano-{uuid4().hex}"
                    result = await asyncio.to_thread(
                        piano.command, "record_start", recording_id=recording_id
                    )
                    result = {
                        "id": recording_id,
                        "download_url": f"/api/v1/piano-ddsp/recordings/{recording_id}",
                    }
                elif event == "record_stop":
                    raw = await asyncio.to_thread(piano.command, "record_stop")
                    path = Path(str(dict(raw or {}).get("path", "")))
                    result = (
                        {
                            "id": path.stem,
                            "download_url": f"/api/v1/piano-ddsp/recordings/{path.stem}",
                        }
                        if path.name
                        else None
                    )
                elif event == "monitor":
                    monitor_enabled = bool(message.get("enabled", False))
                    await asyncio.to_thread(piano.set_monitor, source, monitor_enabled)
                    result = {"enabled": monitor_enabled}
                elif event == "panic":
                    result = await asyncio.to_thread(piano.command, "panic")
                elif event == "parameters":
                    values = message.get("values")
                    if not isinstance(values, dict):
                        raise ValueError("parameters event requires a values object")
                    result = await asyncio.to_thread(
                        piano.command, "parameters", values=values
                    )
                elif event == "ping":
                    await websocket.send_json({"event": "pong"})
                    continue
                else:
                    raise ValueError(f"Unknown Piano-DDSP event: {event}")
                await websocket.send_json({"event": "ack", "request": event, "data": result})
            except BaseException as exc:
                await websocket.send_json({"event": "error", "message": str(exc)})
    except WebSocketDisconnect:
        pass
    finally:
        if monitor_enabled:
            try:
                await asyncio.to_thread(piano.set_monitor, source, False)
            except BaseException:
                pass
        if piano.running:
            try:
                await asyncio.to_thread(piano.command, "release_source", source=source)
            except BaseException:
                pass
        piano.unsubscribe(subscriber)


@app.get("/api/v1/speaker-test/status")
def get_speaker_test_status() -> dict[str, object]:
    return speaker.status()


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


@app.post("/api/v1/ddsp-vst/start")
async def start_ddsp_vst(payload: DdspVstStartRequest) -> dict[str, object]:
    try:
        item = resolve_catalog_item("ddsp_vst_models", payload.model_id)
        config = payload.model_dump()
        config.update(
            {
                "model_path": item["path"],
                "backend": "om",
            }
        )
        if payload.audio_device_id:
            device = next(
                output
                for output in query_ddsp_vst_audio_outputs(query_audio_devices)
                if output["id"] == payload.audio_device_id
            )
            config.update(
                {
                    "audio_backend": device.get("backend", "portaudio"),
                    "pulse_sink": device.get("sink_name"),
                    "audio_device_name": device["name"],
                    "audio_device_sample_rate": device.get("default_sample_rate"),
                    "is_bluetooth": bool(device.get("is_bluetooth", False)),
                }
            )
        _require_board()
        return await asyncio.to_thread(ddsp_vst.start, config)
    except StopIteration as exc:
        raise HTTPException(status_code=404, detail="Audio device not found") from exc
    except HTTPException:
        raise
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.post("/api/v1/ddsp-vst/stop")
async def stop_ddsp_vst() -> dict[str, object]:
    return await asyncio.to_thread(ddsp_vst.stop)


@app.websocket("/api/v1/ddsp-vst/events")
async def ddsp_vst_events(websocket: WebSocket) -> None:
    await websocket.accept()
    source = f"browser-{uuid4().hex[:10]}"
    try:
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=0.5)
            except asyncio.TimeoutError:
                await websocket.send_json({"event": "status", "data": ddsp_vst.status()})
                continue
            event = message.get("event")
            try:
                if event == "note_on":
                    ddsp_vst.note_on(source, int(message["note"]), int(message.get("velocity", 100)))
                elif event == "note_off":
                    ddsp_vst.note_off(source, int(message["note"]))
                elif event == "sustain":
                    ddsp_vst.sustain(source, bool(message.get("enabled")))
                elif event == "pitch_bend":
                    ddsp_vst.pitch_bend(int(message.get("value", 0)))
                elif event == "parameters":
                    values = message.get("values")
                    if not isinstance(values, dict):
                        raise ValueError("parameters event requires an object")
                    ddsp_vst.update_parameters(values)
                elif event == "all_notes_off":
                    ddsp_vst.release_source(source)
                elif event != "ping":
                    raise ValueError(f"Unknown DDSP-VST event: {event}")
                await websocket.send_json({"event": "status", "data": ddsp_vst.status()})
            except WebSocketDisconnect:
                break
            except BaseException as exc:
                try:
                    await websocket.send_json({"event": "error", "message": str(exc)})
                except (WebSocketDisconnect, RuntimeError):
                    break
    except WebSocketDisconnect:
        pass
    finally:
        ddsp_vst.release_source(source)


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


@app.post("/api/v1/midi-ddsp/recordings/{source_job_id}/play")
def play_midi_ddsp_recording(
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
