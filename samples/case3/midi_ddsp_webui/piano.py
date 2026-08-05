"""FastAPI-side controller for the isolated Piano-DDSP worker process."""

from __future__ import annotations

import json
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
from typing import Any
from uuid import uuid4

from piano_ddsp_runtime.bundle import scan_bundles

from .core import REPORT_ROOT, ROOT, ResourceCoordinator


BUNDLE_ROOT = ROOT / "models" / "piano_ddsp" / "bundles"
RECORDING_ROOT = REPORT_ROOT / "piano-ddsp"
ACTIVE_BUNDLE_PATH = ROOT / "models" / "piano_ddsp" / "active-bundle.json"


def active_piano_bundle_id(available_ids: set[str]) -> tuple[str | None, str | None]:
    if not ACTIVE_BUNDLE_PATH.is_file():
        return None, None
    try:
        pointer = json.loads(ACTIVE_BUNDLE_PATH.read_text(encoding="utf-8"))
        if pointer.get("schema") != "piano-ddsp-active-bundle/v1":
            raise ValueError("unsupported active bundle pointer schema")
        bundle_id = str(pointer.get("bundle_id", ""))
        if bundle_id not in available_ids:
            raise ValueError(f"active bundle {bundle_id!r} is unavailable")
        return bundle_id, None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, f"{ACTIVE_BUNDLE_PATH}: {exc}"


def piano_catalog() -> dict[str, object]:
    bundles, bundle_errors = scan_bundles(BUNDLE_ROOT)
    qualified_bundle_ids = {
        bundle.id
        for bundle in bundles
        if any(asset.validation_passed for asset in bundle.models.values())
    }
    active_bundle_id, active_error = active_piano_bundle_id(qualified_bundle_ids)
    if active_error:
        bundle_errors.append(active_error)
    ranked_bundles = sorted(
        bundles,
        key=lambda bundle: (bundle.id != active_bundle_id, bundle.id),
    )
    release: dict[str, Any] = {}
    public_bundles = [
        {
            "id": bundle.id,
            "release": bundle.release,
            "precision": bundle.precision,
            "soc_version": bundle.soc_version,
            "complete": bundle.complete,
            "models": sorted(
                model_id
                for model_id, asset in bundle.models.items()
                if asset.validation_passed
            ),
        }
        for bundle in ranked_bundles
    ]
    assets_by_id: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for bundle in ranked_bundles:
        for model_id, asset in bundle.models.items():
            if asset.validation_passed:
                assets_by_id.setdefault(model_id, []).append((bundle.id, asset.metadata))

    models: list[dict[str, object]] = []
    years: list[int] = []
    contract: dict[str, object] = {}
    for model_id, assets in assets_by_id.items():
        bundle_ids = [bundle_id for bundle_id, _metadata in assets]
        metadata = assets[0][1]
        model_config = metadata.get("model_config")
        models.append(
            {
                "id": model_id,
                "name": metadata.get("display_name", model_id),
                "architecture": metadata.get("architecture"),
                "quality_status": metadata.get("quality_status", "quality_selection_pending"),
                "available": True,
                "bundle_ids": bundle_ids,
                "n_harmonics": metadata.get("n_harmonics"),
                "n_noise_bands": metadata.get("n_noise_bands"),
                "reverb_type": dict(model_config or {}).get("reverb_type"),
            }
        )
        if not years:
            years = [
                int(value)
                for value in metadata.get("piano_model_index_to_maestro_year", [])
            ]
            contract = {
                "dtype": metadata.get("dtype"),
                "opset": metadata.get("opset"),
                "frames_per_call": metadata.get("frames_per_call"),
                "frame_rate": metadata.get("frame_rate"),
                "sample_rate": metadata.get("sample_rate"),
                "audio_samples_per_call": metadata.get("audio_samples_per_call"),
                "release_frames": metadata.get("release_frames"),
            }
    if ranked_bundles:
        release = {"release": ranked_bundles[0].release}
    return {
        "release": release.get("release"),
        "bundles": public_bundles,
        "active_bundle_id": active_bundle_id,
        "models": models,
        "piano_years": years,
        "io_contract": contract,
        "latency_profiles": {
            "low": {"frames": 4, "prebuffer_blocks": 1, "audio_latency_ms": 15},
            "balanced": {"frames": 8, "prebuffer_blocks": 1, "audio_latency_ms": 20},
            "safe": {"frames": 16, "prebuffer_blocks": 2, "audio_latency_ms": 40},
        },
        "errors": bundle_errors,
    }


def resolve_piano_bundle(bundle_id: str, model_id: str | None = None) -> Path:
    bundles, errors = scan_bundles(BUNDLE_ROOT)
    for bundle in bundles:
        if bundle.id != bundle_id:
            continue
        if model_id is not None:
            asset = bundle.models.get(model_id)
            if asset is not None and asset.validation_passed:
                return bundle.manifest_path
            raise KeyError(
                f"Piano-DDSP model {model_id!r} is unavailable in bundle {bundle_id!r}"
            )
        if any(asset.validation_passed for asset in bundle.models.values()):
            return bundle.manifest_path
    detail = f"; invalid bundles: {errors}" if errors else ""
    raise KeyError(f"Piano-DDSP bundle {bundle_id!r} is unavailable{detail}")


class PianoDdspController:
    OWNER = "piano-ddsp"

    def __init__(self, coordinator: ResourceCoordinator) -> None:
        self.coordinator = coordinator
        self._lock = threading.RLock()
        self._write_lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._stdout_thread: threading.Thread | None = None
        self._stderr_thread: threading.Thread | None = None
        self._responses: dict[str, queue.Queue[dict[str, Any]]] = {}
        self._subscribers: list[queue.Queue[dict[str, Any]]] = []
        self._monitor_sources: set[str] = set()
        self._status: dict[str, Any] = {"state": "stopped", "running": False}
        self._last_heartbeat = 0.0
        self._intentional_stop = False
        self._stderr_path: Path | None = None
        self._failure_cleanup_started = False
        self._owns_resource = False

    @property
    def running(self) -> bool:
        with self._lock:
            return self._process is not None and self._process.poll() is None

    def subscribe(self) -> queue.Queue[dict[str, Any]]:
        target: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=64)
        with self._lock:
            self._subscribers.append(target)
        return target

    def unsubscribe(self, target: queue.Queue[dict[str, Any]]) -> None:
        with self._lock:
            if target in self._subscribers:
                self._subscribers.remove(target)

    def _publish(self, payload: dict[str, Any]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for target in subscribers:
            try:
                target.put_nowait(payload)
            except queue.Full:
                try:
                    target.get_nowait()
                    target.put_nowait(payload)
                except queue.Empty:
                    pass

    def _release_owned_resource(self) -> None:
        with self._lock:
            owns_resource = self._owns_resource
            self._owns_resource = False
        if owns_resource:
            self.coordinator.release(self.OWNER)

    def _stdout_loop(self, process: subprocess.Popen[str]) -> None:
        assert process.stdout is not None
        try:
            for line in process.stdout:
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event = payload.get("event")
                if event == "response":
                    request_id = str(payload.get("request_id", ""))
                    with self._lock:
                        target = self._responses.get(request_id)
                    if target is not None:
                        target.put(payload)
                if event == "status" and isinstance(payload.get("data"), dict):
                    with self._lock:
                        self._status = dict(payload["data"])
                        self._last_heartbeat = time.monotonic()
                        failed = self._status.get("state") == "failed"
                    if failed:
                        self._schedule_failure_cleanup(
                            str(self._status.get("error") or "Piano-DDSP runtime failed")
                        )
                elif event == "ready":
                    with self._lock:
                        self._last_heartbeat = time.monotonic()
                if event in {"ready", "status", "monitor", "note", "error"}:
                    self._publish(payload)
        finally:
            return_code = process.wait()
            with self._lock:
                if self._process is process:
                    self._process = None
                    if not self._intentional_stop:
                        self._status = {
                            **self._status,
                            "state": "failed",
                            "running": False,
                            "error": f"Piano-DDSP worker exited with code {return_code}",
                        }
            self._release_owned_resource()
            self._publish({"event": "status", "data": self.status()})

    def _stderr_loop(self, process: subprocess.Popen[str], path: Path) -> None:
        assert process.stderr is not None
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as output:
            for line in process.stderr:
                output.write(line)
                output.flush()

    def _spawn(self) -> None:
        RECORDING_ROOT.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime())
        stderr_path = RECORDING_ROOT / f"worker-{stamp}.log"
        process = subprocess.Popen(
            [sys.executable, "-u", "-m", "piano_ddsp_runtime.worker"],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        self._process = process
        self._stderr_path = stderr_path
        self._stdout_thread = threading.Thread(
            target=self._stdout_loop, args=(process,), name="piano-worker-out", daemon=True
        )
        self._stderr_thread = threading.Thread(
            target=self._stderr_loop,
            args=(process, stderr_path),
            name="piano-worker-err",
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def _schedule_failure_cleanup(self, error: str) -> None:
        with self._lock:
            if self._failure_cleanup_started:
                return
            self._failure_cleanup_started = True
        threading.Thread(
            target=self._terminate_failed,
            args=(error,),
            name="piano-worker-cleanup",
            daemon=True,
        ).start()

    def _terminate_failed(self, error: str) -> None:
        with self._lock:
            process = self._process
            self._intentional_stop = True
            self._status = {
                **self._status,
                "state": "failed",
                "running": False,
                "error": error,
            }
        if process is not None and process.poll() is None:
            try:
                self.send("shutdown", timeout=3.0)
                process.wait(timeout=3.0)
            except (RuntimeError, TimeoutError, BrokenPipeError, OSError, subprocess.TimeoutExpired):
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=3.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2.0)
        with self._lock:
            if self._process is process:
                self._process = None
        self._release_owned_resource()
        self._publish({"event": "status", "data": self.status()})

    def send(self, command: str, *, timeout: float = 15.0, **values: object) -> object:
        with self._lock:
            process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            raise RuntimeError("Piano-DDSP worker is not running")
        request_id = uuid4().hex
        response: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._lock:
            self._responses[request_id] = response
        try:
            message = {"request_id": request_id, "command": command, **values}
            with self._write_lock:
                process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
                process.stdin.flush()
            try:
                payload = response.get(timeout=timeout)
            except queue.Empty as exc:
                raise TimeoutError(f"Piano-DDSP worker timed out handling {command}") from exc
            if not payload.get("ok"):
                raise RuntimeError(str(payload.get("error", "Piano-DDSP worker command failed")))
            return payload.get("data")
        finally:
            with self._lock:
                self._responses.pop(request_id, None)

    def notify(self, command: str, **values: object) -> None:
        """Write a realtime edge without waiting for an unused status response."""
        with self._lock:
            process = self._process
        if process is None or process.poll() is not None or process.stdin is None:
            raise RuntimeError("Piano-DDSP worker is not running")
        message = {"command": command, **values}
        with self._write_lock:
            process.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
            process.stdin.flush()

    def start(
        self, config: dict[str, object], *, manage_resource: bool = True
    ) -> dict[str, object]:
        acquired = False
        try:
            with self._lock:
                if self.running:
                    return self.status()
                if manage_resource:
                    self.coordinator.acquire(self.OWNER)
                    self._owns_resource = True
                    acquired = True
                self._intentional_stop = False
                self._failure_cleanup_started = False
                self._last_heartbeat = 0.0
                self._status = {
                    "state": "starting",
                    "running": False,
                    "config": dict(config),
                }
                self._spawn()
            deadline = time.monotonic() + 5.0
            while self._last_heartbeat == 0.0 and time.monotonic() < deadline:
                time.sleep(0.02)
            if self._last_heartbeat == 0.0:
                raise TimeoutError("Piano-DDSP worker did not become ready")
            result = self.send("start", config=config, timeout=60.0)
            if isinstance(result, dict):
                with self._lock:
                    self._status = result
                    self._last_heartbeat = time.monotonic()
            return self.status()
        except BaseException:
            if acquired or self._process is not None:
                self.stop(force=True)
            raise

    def stop(self, *, force: bool = False) -> dict[str, object]:
        with self._lock:
            process = self._process
            self._intentional_stop = True
            if process is None:
                self._status = {"state": "stopped", "running": False}
                self._release_owned_resource()
                return self.status()
            self._status = {**self._status, "state": "stopping"}
        if not force and process.poll() is None:
            try:
                self.send("shutdown", timeout=5.0)
            except (RuntimeError, TimeoutError, BrokenPipeError):
                pass
        if process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2.0)
        with self._lock:
            if self._process is process:
                self._process = None
            self._status = {"state": "stopped", "running": False}
            self._monitor_sources.clear()
        self._release_owned_resource()
        return self.status()

    def command(self, command: str, **values: object) -> object:
        return self.send(command, **values)

    def set_monitor(self, source: str, enabled: bool) -> None:
        with self._lock:
            if enabled:
                self._monitor_sources.add(source)
            else:
                self._monitor_sources.discard(source)
            active = bool(self._monitor_sources)
        if self.running:
            self.send("monitor", enabled=active)

    def status(self) -> dict[str, object]:
        with self._lock:
            status = dict(self._status)
            process = self._process
            heartbeat_age = (
                time.monotonic() - self._last_heartbeat if self._last_heartbeat else None
            )
            stderr_path = self._stderr_path
        if process is not None and process.poll() is None and heartbeat_age is not None and heartbeat_age > 3.0:
            error = f"Piano-DDSP heartbeat timed out after {heartbeat_age:.1f} s"
            status.update(
                {
                    "state": "failed",
                    "running": False,
                    "error": error,
                }
            )
            self._schedule_failure_cleanup(error)
        status["heartbeat_age_seconds"] = heartbeat_age
        status["worker_pid"] = process.pid if process is not None and process.poll() is None else None
        status["worker_log"] = stderr_path.name if stderr_path else None
        recording = status.get("recording")
        if isinstance(recording, dict):
            recording = dict(recording)
            raw_path = recording.pop("path", None)
            if raw_path:
                recording["id"] = Path(str(raw_path)).stem
            status["recording"] = recording
        return status


def resolve_recording(recording_id: str) -> Path:
    safe = "".join(char for char in recording_id if char.isalnum() or char in "-_")
    if safe != recording_id or not safe:
        raise KeyError(recording_id)
    path = (RECORDING_ROOT / f"{safe}.wav").resolve()
    if RECORDING_ROOT.resolve() not in path.parents or not path.is_file():
        raise KeyError(recording_id)
    return path
