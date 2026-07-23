from __future__ import annotations

import asyncio
import json
from pathlib import Path
import queue
import sys
from typing import Literal, Optional
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from realtime_ddsp import query_audio_devices, query_midi_devices

from .core import (
    JOB_ROOT,
    REPORT_ROOT,
    ROOT,
    UPLOAD_ROOT,
    JobManager,
    ResourceBusyError,
    ResourceCoordinator,
    catalog,
    is_ascend_board,
    load_benchmark_summary,
    public_catalog,
    resolve_artifact,
    resolve_catalog_item,
    system_status,
)
from .live import LiveSessionController


MAX_MIDI_BYTES = 10 * 1024 * 1024
WEB_DIST = ROOT / "webui" / "dist"

app = FastAPI(title="MIDI-DDSP Studio API", version="0.1.0")
coordinator = ResourceCoordinator()
jobs = JobManager(coordinator)
live = LiveSessionController(coordinator)


class ApiModel(BaseModel):
    model_config = ConfigDict(protected_namespaces=())


class LiveStartRequest(ApiModel):
    model_id: str
    audio_device_id: Optional[str] = None
    midi_port: Optional[str] = None
    sample_rate: int = Field(48_000, ge=8_000, le=192_000)
    prebuffer: int = Field(6, ge=1, le=64)
    max_voices: int = Field(8, ge=1, le=32)
    audio_latency_ms: float = Field(80.0, gt=0, le=1000)
    output_gain_db: float = Field(0.0, ge=-60, le=36)
    attack: float = Field(0.10, gt=0, le=10)
    decay: float = Field(0.0, ge=0, le=10)
    sustain: float = Field(1.0, ge=0, le=1)
    release: float = Field(1.20, gt=0, le=20)
    device_id: int = Field(0, ge=0, le=63)


class MidiDdspJobRequest(ApiModel):
    mode: Literal["play", "render"] = "play"
    midi_id: str
    expression_model_id: str
    synthesis_model_id: str
    instrument_id: int = Field(0, ge=0, le=19)
    audio_device_id: Optional[str] = None
    sample_rate: int = Field(48_000, ge=8_000, le=192_000)
    prebuffer: int = Field(6, ge=1, le=64)
    audio_latency_ms: float = Field(80.0, gt=0, le=1000)
    output_gain_db: float = Field(24.0, ge=-60, le=36)
    tail_seconds: float = Field(0.5, ge=0, le=20)
    device_id: int = Field(0, ge=0, le=63)


def _http_error(exc: BaseException) -> HTTPException:
    if isinstance(exc, ResourceBusyError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc))
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
        "live": live.status(),
        "job_count": len(jobs.list()),
    }


@app.get("/api/v1/catalog")
def get_catalog() -> dict[str, object]:
    return public_catalog()


@app.get("/api/v1/audio-devices")
def get_audio_devices() -> dict[str, object]:
    try:
        return {"available": True, "devices": query_audio_devices(), "error": None}
    except RuntimeError as exc:
        return {"available": False, "devices": [], "error": str(exc)}


@app.get("/api/v1/midi-ports")
def get_midi_ports() -> dict[str, object]:
    try:
        return {"available": True, "ports": query_midi_devices(), "error": None}
    except RuntimeError as exc:
        return {"available": False, "ports": [], "error": str(exc)}


@app.post("/api/v1/live/start")
async def start_live(payload: LiveStartRequest) -> dict[str, object]:
    try:
        item = resolve_catalog_item("live_models", payload.model_id)
        config = payload.model_dump()
        config.update(
            {
                "model_path": item["path"],
                "backend": "om",
            }
        )
        _require_board()
        return await asyncio.to_thread(live.start, config)
    except HTTPException:
        raise
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.post("/api/v1/live/stop")
async def stop_live() -> dict[str, object]:
    return await asyncio.to_thread(live.stop)


@app.websocket("/api/v1/live/events")
async def live_events(websocket: WebSocket) -> None:
    await websocket.accept()
    source = f"browser-{uuid4().hex[:10]}"
    try:
        while True:
            try:
                message = await asyncio.wait_for(websocket.receive_json(), timeout=0.5)
            except asyncio.TimeoutError:
                await websocket.send_json({"event": "status", "data": live.status()})
                continue
            event = message.get("event")
            try:
                if event == "note_on":
                    live.note_on(source, int(message["note"]), int(message.get("velocity", 100)))
                elif event == "note_off":
                    live.note_off(source, int(message["note"]))
                elif event == "sustain":
                    live.sustain(source, bool(message.get("enabled")))
                elif event == "all_notes_off":
                    live.release_source(source)
                elif event != "ping":
                    raise ValueError(f"Unknown live event: {event}")
                await websocket.send_json({"event": "status", "data": live.status()})
            except BaseException as exc:
                await websocket.send_json({"event": "error", "message": str(exc)})
    except WebSocketDisconnect:
        pass
    finally:
        live.release_source(source)


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
    item = next(item for item in catalog()["midi_files"] if item["path"] == str(target.resolve()))
    result = {key: value for key, value in item.items() if key != "path"}
    result["original_name"] = filename
    return result


@app.post("/api/v1/midi-ddsp/jobs")
def start_midi_ddsp_job(payload: MidiDdspJobRequest) -> dict[str, object]:
    _require_board()
    try:
        midi = resolve_catalog_item("midi_files", payload.midi_id)
        expression = resolve_catalog_item("midi_ddsp_models", payload.expression_model_id)
        synthesis = resolve_catalog_item("midi_ddsp_models", payload.synthesis_model_id)
        if expression["component"] != "expression" or synthesis["component"] != "synthesis":
            raise ValueError("Expression and synthesis model roles do not match")
        command = [
            sys.executable,
            str(ROOT / "midi_ddsp_realtime.py"),
            "--midi",
            str(midi["path"]),
            "--expression-om",
            str(expression["path"]),
            "--synthesis-om",
            str(synthesis["path"]),
            "--instrument-id",
            str(payload.instrument_id),
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
            "--output",
            "{job_dir}/output.wav",
            "--report",
            "{job_dir}/report.json",
            "--json-events",
            "--web-control",
        ]
        if payload.mode == "render":
            command.append("--render-only")
        elif payload.audio_device_id not in (None, ""):
            command.extend(["--audio-device", str(payload.audio_device_id)])
        job = jobs.start(
            f"midi-ddsp-{payload.mode}",
            command,
            metadata={
                "midi_name": midi["name"],
                "expression_model": expression["name"],
                "synthesis_model": synthesis["name"],
                "instrument_id": payload.instrument_id,
                "mode": payload.mode,
            },
        )
        return job.public()
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


@app.post("/api/v1/tests/runtime")
def run_runtime_test() -> dict[str, object]:
    _require_board()
    try:
        job = jobs.start(
            "runtime-validation",
            ["bash", str(ROOT / "tools" / "validate_midi_ddsp_ascend_om.sh")],
            env={"REPORT_DIR": "{job_dir}"},
        )
        return job.public()
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.post("/api/v1/tests/benchmark-smoke")
def run_benchmark_smoke() -> dict[str, object]:
    _require_board()
    try:
        job = jobs.start(
            "benchmark-smoke",
            ["bash", str(ROOT / "tools" / "run_webui_benchmark_smoke.sh")],
            env={"REPORT_DIR": "{job_dir}"},
        )
        return job.public()
    except BaseException as exc:
        raise _http_error(exc) from exc


@app.get("/api/v1/benchmark-summary")
def benchmark_summary() -> dict[str, object]:
    return {"summary": load_benchmark_summary()}


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


@app.on_event("shutdown")
def shutdown() -> None:
    if live.running:
        live.stop()


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
