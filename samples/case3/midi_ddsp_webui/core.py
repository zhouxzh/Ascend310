from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import queue
import shutil
import signal
import subprocess
import sys
import threading
import time
from typing import Callable, Iterable
from uuid import uuid4

import numpy as np

from .midi_analysis import analyze_midi


ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = ROOT / "reports" / "webui"
JOB_ROOT = REPORT_ROOT / "jobs"
UPLOAD_ROOT = REPORT_ROOT / "uploads"

INSTRUMENTS = (
    "Violin",
    "Viola",
    "Cello",
    "Double Bass",
    "Flute",
    "Oboe",
    "Clarinet",
    "Saxophone",
    "Bassoon",
    "Trumpet",
    "Horn",
    "Trombone",
    "Tuba",
)

TERMINAL_STATES = {"succeeded", "failed", "cancelled"}
ACTIVE_STATES = {"queued", "preparing", "running", "paused", "stopping"}
MIDI_DDSP_REVERB_SHA256 = "ecbc733bc9a17516dc00897e64eaae70114aa79ed97e2bbc59dedb334f356058"
MIDI_DDSP_SOURCE_COMMIT = "d7af42704a63b47267ae6a1bc0fee1ed7dc5c855"


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def is_ascend_board() -> bool:
    return platform.machine().lower() in {"aarch64", "arm64"} and any(
        path.exists()
        for path in (
            Path("/usr/local/Ascend/ascend-toolkit/set_env.sh"),
            Path("/usr/local/Ascend/latest/set_env.sh"),
            Path.home() / "Ascend/latest/set_env.sh",
        )
    )


def dependency_status() -> dict[str, bool]:
    return {
        name: importlib.util.find_spec(name) is not None
        for name in (
            "fastapi",
            "uvicorn",
            "websockets",
            "mido",
            "rtmidi",
            "sounddevice",
            "numpy",
            "pydantic",
            "acl",
            "ais_bench",
        )
    }


def _file_id(prefix: str, path: Path) -> str:
    relative = path.resolve().relative_to(ROOT.resolve()).as_posix()
    digest = hashlib.sha1(relative.encode("utf-8")).hexdigest()[:12]
    return f"{prefix}-{digest}"


def _precision(path: Path) -> str:
    name = path.stem.lower()
    if "mixed_float16" in name:
        return "mixed_float16"
    if "force_fp16" in name:
        return "force_fp16"
    return "default"


def scan_midi_files() -> list[dict[str, object]]:
    files: list[Path] = []
    for folder in (ROOT / "midi", UPLOAD_ROOT):
        if folder.is_dir():
            files.extend(folder.glob("*.mid"))
            files.extend(folder.glob("*.midi"))
    result = []
    for path in sorted(set(files), key=lambda item: item.name.lower()):
        item: dict[str, object] = {
            "id": _file_id("midi", path),
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "uploaded": UPLOAD_ROOT in path.parents,
            "path": str(path.resolve()),
        }
        try:
            item.update(analyze_midi(path).public())
        except Exception as exc:
            item.update(
                {
                    "note_count": 0,
                    "track_count": 0,
                    "max_polyphony": 0,
                    "duration_seconds": 0.0,
                    "monophonic": False,
                    "midi_ddsp_mode": "invalid",
                    "midi_ddsp_supported": False,
                    "unsupported_code": "invalid_midi",
                    "unsupported_reason": str(exc),
                    "programs": [],
                    "tracks": [],
                }
            )
        result.append(item)
    return result


def scan_ddsp_vst_models() -> list[dict[str, object]]:
    paths: list[Path] = []
    om_root = ROOT / "models" / "om"
    if om_root.is_dir():
        paths.extend(om_root.glob("*.om"))
    result = []
    for path in sorted(paths, key=lambda item: item.name.lower()):
        if "midi_ddsp" in path.name.lower():
            continue
        stem = path.stem
        instrument = stem.split("_force_fp16")[0].split("_mixed_float16")[0]
        result.append(
            {
                "id": _file_id("ddspvst", path),
                "name": path.name,
                "instrument": instrument,
                "backend": path.suffix.lower().lstrip("."),
                "precision": _precision(path),
                "size_bytes": path.stat().st_size,
                "path": str(path.resolve()),
            }
        )
    return result


def scan_midi_ddsp_models() -> list[dict[str, object]]:
    root = ROOT / "models" / "om"
    if not root.is_dir():
        return []
    result = []
    for path in sorted(root.glob("midi_ddsp_*.om"), key=lambda item: item.name.lower()):
        if "expression" in path.name:
            component = "expression"
        elif "synthesis" in path.name:
            component = "synthesis"
        else:
            continue
        result.append(
            {
                "id": _file_id("mddsp", path),
                "name": path.name,
                "component": component,
                "precision": _precision(path),
                "size_bytes": path.stat().st_size,
                "path": str(path.resolve()),
            }
        )
    return result


def _load_midi_ddsp_bundle(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if int(data.get("schema_version", 0)) != 1:
        raise ValueError(f"Unsupported MIDI-DDSP bundle schema: {path}")
    if data.get("source_commit") != MIDI_DDSP_SOURCE_COMMIT:
        raise ValueError(f"Unexpected MIDI-DDSP source commit in {path}")
    components = data.get("components")
    if not isinstance(components, dict) or not components:
        raise ValueError(f"MIDI-DDSP bundle has no components: {path}")
    resolved_components: dict[str, dict[str, object]] = {}
    for name, raw in components.items():
        if not isinstance(raw, dict) or not isinstance(raw.get("file"), str):
            raise ValueError(f"Invalid component {name!r} in {path}")
        component_path = (path.parent / str(raw["file"])).resolve()
        if not component_path.is_file():
            raise FileNotFoundError(component_path)
        expected_sha = str(raw.get("sha256", ""))
        actual_sha = hashlib.sha256(component_path.read_bytes()).hexdigest()
        if expected_sha and actual_sha != expected_sha:
            raise ValueError(f"SHA256 mismatch for {component_path}")
        resolved_components[str(name)] = {
            **raw,
            "path": str(component_path),
            "sha256": actual_sha,
            "size_bytes": component_path.stat().st_size,
        }
    return {
        "id": str(data["id"]),
        "name": str(data["name"]),
        "architecture": str(data["architecture"]),
        "precision": str(data.get("precision", "mixed_float16")),
        "recommended": bool(data.get("recommended", False)),
        "quality_status": str(data.get("quality_status", "unverified")),
        "source_commit": str(data["source_commit"]),
        "seed": int(data.get("seed", 20260724)),
        "manifest": str(path.resolve()),
        "components": resolved_components,
    }


def _legacy_midi_ddsp_bundles() -> list[dict[str, object]]:
    models = scan_midi_ddsp_models()
    bundles: list[dict[str, object]] = []
    for precision in ("mixed_float16", "force_fp16"):
        expression = next(
            (
                item
                for item in models
                if item["component"] == "expression" and item["precision"] == precision
            ),
            None,
        )
        synthesis = next(
            (
                item
                for item in models
                if item["component"] == "synthesis" and item["precision"] == precision
            ),
            None,
        )
        if expression is None or synthesis is None:
            continue
        bundles.append(
            {
                "id": f"google-urmp-legacy-{precision}",
                "name": f"Google URMP legacy ({precision})",
                "architecture": "legacy-static-v1",
                "precision": precision,
                "recommended": False,
                "quality_status": "context_resets",
                "source_commit": MIDI_DDSP_SOURCE_COMMIT,
                "seed": 20260724,
                "manifest": None,
                "components": {
                    "expression": expression,
                    "synthesis": synthesis,
                },
            }
        )
    return bundles


def scan_midi_ddsp_bundles() -> list[dict[str, object]]:
    bundles: list[dict[str, object]] = []
    bundle_root = ROOT / "models" / "midi_ddsp" / "bundles"
    if bundle_root.is_dir():
        for manifest in sorted(bundle_root.glob("*/manifest.json")):
            try:
                bundles.append(_load_midi_ddsp_bundle(manifest))
            except (OSError, ValueError, KeyError, TypeError):
                continue
    bundles.extend(_legacy_midi_ddsp_bundles())
    if not any(bundle["recommended"] for bundle in bundles):
        for bundle in bundles:
            if bundle["precision"] == "mixed_float16":
                bundle["recommended"] = True
                break
    return bundles


def scan_midi_ddsp_reverb_assets() -> list[dict[str, object]]:
    path = ROOT / "models" / "om" / "midi_ddsp_reverb_ir.npz"
    if not path.is_file():
        return []
    return [
        {
            "id": _file_id("mddsp-ir", path),
            "name": path.name,
            "sha256": MIDI_DDSP_REVERB_SHA256,
            "size_bytes": path.stat().st_size,
            "instrument_count": len(INSTRUMENTS),
            "checkpoint_instrument_count": 20,
            "sample_rate": 16_000,
            "samples_per_instrument": 48_000,
            "path": str(path.resolve()),
        }
    ]


def validate_midi_ddsp_reverb_asset(path: Path) -> str:
    if not path.is_file():
        raise FileNotFoundError(f"MIDI-DDSP reverb asset is missing: {path}")
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != MIDI_DDSP_REVERB_SHA256:
        raise ValueError(
            f"MIDI-DDSP reverb SHA256 mismatch: {digest}; "
            f"expected {MIDI_DDSP_REVERB_SHA256}"
        )
    try:
        with np.load(path, allow_pickle=False) as data:
            responses = np.asarray(data["impulse_responses"], dtype=np.float32)
            sample_rate = int(data["sample_rate"])
            add_dry = bool(int(data["add_dry"]))
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"Invalid MIDI-DDSP reverb asset: {exc}") from exc
    if responses.shape != (20, 48_000):
        raise ValueError(f"Unexpected MIDI-DDSP reverb shape: {responses.shape}")
    if sample_rate != 16_000 or not add_dry:
        raise ValueError("Unexpected MIDI-DDSP reverb metadata")
    if not np.all(np.isfinite(responses)) or np.any(responses[:, 0] != 0.0):
        raise ValueError("MIDI-DDSP reverb values are invalid")
    return digest


def catalog() -> dict[str, object]:
    return {
        "midi_files": scan_midi_files(),
        "ddsp_vst_models": scan_ddsp_vst_models(),
        "midi_ddsp_models": scan_midi_ddsp_models(),
        "midi_ddsp_bundles": scan_midi_ddsp_bundles(),
        "midi_ddsp_reverb_assets": scan_midi_ddsp_reverb_assets(),
        "instruments": [
            {"id": index, "name": name, "verified": True}
            for index, name in enumerate(INSTRUMENTS)
        ],
    }


def public_catalog() -> dict[str, object]:
    data = catalog()

    def sanitize(value: object) -> object:
        if isinstance(value, dict):
            return {
                key: sanitize(item)
                for key, item in value.items()
                if key not in {"path", "manifest"}
            }
        if isinstance(value, list):
            return [sanitize(item) for item in value]
        return value

    return {
        key: sanitize(items)
        for key, items in data.items()
    }


def resolve_catalog_item(group: str, item_id: str) -> dict[str, object]:
    items = catalog().get(group, [])
    for item in items:
        if item["id"] == item_id:
            return item
    raise KeyError(f"Unknown {group} id: {item_id}")


def command_probe(args: list[str], timeout: float = 5.0) -> dict[str, object]:
    if shutil.which(args[0]) is None:
        return {"available": False, "exit_code": None, "output": "command not found"}
    try:
        result = subprocess.run(
            args,
            cwd=ROOT,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": True, "exit_code": None, "output": str(exc)}
    output = (result.stdout + "\n" + result.stderr).strip()
    return {
        "available": True,
        "exit_code": result.returncode,
        "output": output[-12000:],
    }


def system_status(active_owner: str | None = None) -> dict[str, object]:
    board = is_ascend_board()
    npu = (
        command_probe(["npu-smi", "info"], timeout=8.0)
        if board
        else {"available": False, "exit_code": None, "output": "board only"}
    )
    output = str(npu.get("output", ""))
    health_alarm = "Health" in output and "Alarm" in output
    return {
        "time": utc_timestamp(),
        "hostname": platform.node(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "is_ascend_board": board,
        "dependencies": dependency_status(),
        "npu": {**npu, "health_alarm": health_alarm},
        "active_owner": active_owner,
    }


class ResourceBusyError(RuntimeError):
    pass


class ResourceCoordinator:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._owner: str | None = None

    @property
    def owner(self) -> str | None:
        with self._lock:
            return self._owner

    def acquire(self, owner: str) -> None:
        with self._lock:
            if self._owner is not None:
                raise ResourceBusyError(f"resource is busy: {self._owner}")
            self._owner = owner

    def release(self, owner: str) -> None:
        with self._lock:
            if self._owner == owner:
                self._owner = None


@dataclass
class Job:
    id: str
    kind: str
    state: str = "queued"
    created_at: str = field(default_factory=utc_timestamp)
    updated_at: str = field(default_factory=utc_timestamp)
    progress: float = 0.0
    message: str = ""
    exit_code: int | None = None
    command: list[str] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)
    process: subprocess.Popen[str] | None = field(default=None, repr=False)

    def public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "progress": self.progress,
            "message": self.message,
            "exit_code": self.exit_code,
            "metadata": self.metadata,
            "artifacts": self.artifacts(),
        }

    def artifacts(self) -> list[dict[str, object]]:
        folder = JOB_ROOT / self.id
        if not folder.is_dir():
            return []
        return [
            {
                "id": f"{self.id}--{path.name}",
                "name": path.name,
                "size_bytes": path.stat().st_size,
            }
            for path in sorted(folder.iterdir())
            if path.is_file() and path.name != "metadata.json"
        ]


class JobManager:
    def __init__(self, coordinator: ResourceCoordinator) -> None:
        self.coordinator = coordinator
        self._lock = threading.Lock()
        self._jobs: dict[str, Job] = {}
        self._subscribers: list[queue.Queue[dict[str, object]]] = []
        self._load_history()

    def _load_history(self) -> None:
        if not JOB_ROOT.is_dir():
            return
        for path in JOB_ROOT.glob("*/metadata.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                job = Job(
                    id=str(data["id"]),
                    kind=str(data["kind"]),
                    state=str(data.get("state", "failed")),
                    created_at=str(data.get("created_at", utc_timestamp())),
                    updated_at=str(data.get("updated_at", utc_timestamp())),
                    progress=float(data.get("progress", 0.0)),
                    message=str(data.get("message", "")),
                    exit_code=data.get("exit_code"),
                    metadata=dict(data.get("metadata", {})),
                )
                if job.state in ACTIVE_STATES:
                    job.state = "failed"
                    job.message = "Service restarted before the job completed"
                self._jobs[job.id] = job
            except (OSError, ValueError, KeyError, TypeError):
                continue

    def subscribe(self) -> queue.Queue[dict[str, object]]:
        target: queue.Queue[dict[str, object]] = queue.Queue(maxsize=256)
        with self._lock:
            self._subscribers.append(target)
        return target

    def unsubscribe(self, target: queue.Queue[dict[str, object]]) -> None:
        with self._lock:
            if target in self._subscribers:
                self._subscribers.remove(target)

    def _publish(self, job: Job, event: str = "job") -> None:
        payload = {"event": event, "job": job.public()}
        with self._lock:
            subscribers = list(self._subscribers)
        for target in subscribers:
            try:
                target.put_nowait(payload)
            except queue.Full:
                pass

    def _persist(self, job: Job) -> None:
        folder = JOB_ROOT / job.id
        folder.mkdir(parents=True, exist_ok=True)
        payload = job.public()
        (folder / "metadata.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def list(self) -> list[dict[str, object]]:
        with self._lock:
            jobs = list(self._jobs.values())
        return [job.public() for job in sorted(jobs, key=lambda item: item.created_at, reverse=True)]

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def start(
        self,
        kind: str,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        metadata: dict[str, object] | None = None,
    ) -> Job:
        job_id = uuid4().hex[:12]
        job_dir = str((JOB_ROOT / job_id).resolve())
        resolved_command = [part.replace("{job_dir}", job_dir) for part in command]
        resolved_env = (
            {key: value.replace("{job_dir}", job_dir) for key, value in env.items()}
            if env
            else None
        )
        job = Job(id=job_id, kind=kind, command=resolved_command)
        if metadata:
            job.metadata.update(metadata)
        self.coordinator.acquire(job.id)
        with self._lock:
            self._jobs[job.id] = job
        self._persist(job)
        self._publish(job)
        thread = threading.Thread(
            target=self._run,
            args=(job, resolved_env),
            daemon=True,
            name=f"webui-job-{job.id}",
        )
        thread.start()
        return job

    def _run(self, job: Job, extra_env: dict[str, str] | None) -> None:
        folder = JOB_ROOT / job.id
        log_path = folder / "job.log"
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
        kwargs: dict[str, object] = {}
        if os.name != "nt":
            kwargs["start_new_session"] = True
        process: subprocess.Popen[str] | None = None
        final_state = "failed"
        try:
            job.state = "preparing"
            job.updated_at = utc_timestamp()
            self._persist(job)
            self._publish(job)
            process = subprocess.Popen(
                job.command,
                cwd=ROOT,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                **kwargs,
            )
            job.process = process
            job.state = "running"
            job.updated_at = utc_timestamp()
            self._persist(job)
            self._publish(job)
            with log_path.open("a", encoding="utf-8") as log:
                assert process.stdout is not None
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    job.message = line.rstrip()[-500:]
                    if line.startswith("WEBUI_EVENT "):
                        self._apply_web_event(job, line[len("WEBUI_EVENT ") :])
                    job.updated_at = utc_timestamp()
                    self._publish(job, "log")
            job.exit_code = process.wait()
            if job.state == "stopping":
                final_state = "cancelled"
            elif job.exit_code == 0:
                final_state = "succeeded"
                job.progress = 1.0
            else:
                final_state = "failed"
            self._attach_report(job)
        except BaseException as exc:
            final_state = "failed"
            job.message = str(exc)
        finally:
            if process is not None and process.stdout is not None:
                process.stdout.close()
            job.process = None
            job.updated_at = utc_timestamp()
            self.coordinator.release(job.id)
            job.state = final_state
            self._persist(job)
            self._publish(job)

    @staticmethod
    def _apply_web_event(job: Job, raw: str) -> None:
        try:
            event = json.loads(raw)
        except ValueError:
            return
        if event.get("event") == "prepared":
            job.metadata.update(
                {
                    key: event[key]
                    for key in (
                        "notes",
                        "frames",
                        "blocks",
                        "duration_seconds",
                        "source_track_count",
                        "selected_track_index",
                        "selected_track_name",
                        "melody_extracted",
                    )
                    if key in event
                }
            )
        if event.get("event") == "progress":
            total = max(1, int(event.get("total", 1)))
            job.progress = min(1.0, float(event.get("played", event.get("rendered", 0))) / total)

    @staticmethod
    def _attach_report(job: Job) -> None:
        report_path = JOB_ROOT / job.id / "report.json"
        if report_path.is_file():
            try:
                job.metadata["report"] = json.loads(report_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                pass

    def _signal(self, job: Job, action: str) -> Job:
        process = job.process
        if process is None or process.poll() is not None:
            raise RuntimeError("job is not running")
        if action in {"pause", "resume"}:
            if os.name == "nt" or not hasattr(signal, "SIGUSR1"):
                raise RuntimeError("pause and resume require Linux signals")
            os.killpg(
                os.getpgid(process.pid),
                signal.SIGUSR1 if action == "pause" else signal.SIGUSR2,
            )
            job.state = "paused" if action == "pause" else "running"
        elif action == "stop":
            job.state = "stopping"
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGINT)
        job.updated_at = utc_timestamp()
        self._persist(job)
        self._publish(job)
        return job

    def pause(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.kind != "midi-ddsp-play":
            raise RuntimeError("only MIDI-DDSP playback jobs can be paused")
        return self._signal(job, "pause")

    def resume(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.kind != "midi-ddsp-play":
            raise RuntimeError("only MIDI-DDSP playback jobs can be resumed")
        return self._signal(job, "resume")

    def stop(self, job_id: str) -> Job:
        return self._signal(self.get(job_id), "stop")


def resolve_artifact(artifact_id: str) -> Path:
    if "--" not in artifact_id:
        raise KeyError(artifact_id)
    job_id, name = artifact_id.split("--", 1)
    if not job_id.isalnum() or Path(name).name != name:
        raise KeyError(artifact_id)
    path = (JOB_ROOT / job_id / name).resolve()
    if JOB_ROOT.resolve() not in path.parents or not path.is_file():
        raise KeyError(artifact_id)
    return path


def load_benchmark_summary() -> dict[str, object] | None:
    candidates = sorted(
        (ROOT / "reports").glob("**/midi_ddsp/**/summary.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        candidates = sorted(
            (ROOT / "reports").glob("**/midi_ddsp/**/summary.md"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    if not candidates:
        return None
    path = candidates[0]
    if path.suffix == ".json":
        return {"name": path.name, "format": "json", "data": json.loads(path.read_text(encoding="utf-8"))}
    return {"name": path.name, "format": "markdown", "data": path.read_text(encoding="utf-8")}
