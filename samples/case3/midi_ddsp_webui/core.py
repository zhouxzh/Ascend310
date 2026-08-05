from __future__ import annotations

import copy
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
import socket
import subprocess
import sys
import threading
import time
from typing import Callable
from uuid import uuid4

import numpy as np

from .midi_analysis import analyze_midi, midi_file_sha256


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
CATALOG_CACHE_TTL_SECONDS = 300.0
NPU_STATUS_CACHE_TTL_SECONDS = 15.0
_CATALOG_CACHE_LOCK = threading.Lock()
_CATALOG_CACHE: tuple[tuple[str, str], float, dict[str, object]] | None = None
_NPU_STATUS_CACHE_LOCK = threading.Lock()
_NPU_STATUS_CACHE: tuple[float, dict[str, object]] | None = None


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
    relative = path.absolute().relative_to(ROOT.absolute()).as_posix()
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
            "sha256": midi_file_sha256(path),
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
                    "voice_count": 0,
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
    om_root = ROOT / "models" / "om"
    if not om_root.is_dir():
        return []

    def model_priority(path: Path) -> tuple[int, str]:
        parent = path.parent
        if parent == om_root:
            return (0, path.name.lower())
        if parent.name in {"mixed_precision", "fp16"}:
            return (1, path.name.lower())
        if "all_models" in path.parts:
            return (2, path.as_posix().lower())
        return (3, path.as_posix().lower())

    selected: dict[tuple[str, str], Path] = {}
    for path in sorted(om_root.rglob("*.om"), key=model_priority):
        if "midi_ddsp" in path.name.lower() or path.name.lower().startswith("ddsp_vst_feature"):
            continue
        stem = path.stem
        instrument = stem.split("_force_fp16")[0].split("_mixed_float16")[0]
        key = (instrument, _precision(path))
        selected.setdefault(key, path)

    result = []
    for path in sorted(selected.values(), key=lambda item: item.name.lower()):
        stem = path.stem
        instrument = stem.split("_force_fp16")[0].split("_mixed_float16")[0]
        metadata = _load_ddsp_vst_metadata(instrument)
        result.append(
            {
                "id": _file_id("ddspvst", path),
                "name": path.name,
                "instrument": instrument,
                "backend": path.suffix.lower().lstrip("."),
                "precision": _precision(path),
                "size_bytes": path.stat().st_size,
                "path": str(path.resolve()),
                **metadata,
            }
        )
    return result


def _load_ddsp_vst_metadata(instrument: str) -> dict[str, float]:
    metadata_root = ROOT / "models" / "ddsp_vst"
    raw: dict[str, object] = {}
    metadata_path = metadata_root / "metadata.json"
    try:
        all_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        candidate = all_metadata.get(instrument, {})
        if isinstance(candidate, dict):
            raw = candidate
    except (OSError, ValueError):
        raw = {}

    fields = {
        "pitch_min_note": "mean_min_pitch_note",
        "pitch_max_note": "mean_max_pitch_note",
        "pitch_min_hz": "mean_min_pitch_note_hz",
        "pitch_max_hz": "mean_max_pitch_note_hz",
        "power_min_db": "mean_min_power_note",
        "power_max_db": "mean_max_power_note",
    }
    result: dict[str, float] = {}
    for public_name, source_name in fields.items():
        value = raw.get(public_name, raw.get(source_name))
        if isinstance(value, (int, float)) and np.isfinite(value):
            result[public_name] = float(value)
    return result


def _load_midi_ddsp_bundle(path: Path) -> dict[str, object]:
    data = json.loads(path.read_text(encoding="utf-8"))
    schema_version = int(data.get("schema_version", 0))
    if schema_version not in {1, 2, 3}:
        raise ValueError(f"Unsupported MIDI-DDSP bundle schema: {path}")
    if data.get("source_commit") != MIDI_DDSP_SOURCE_COMMIT:
        raise ValueError(f"Unexpected MIDI-DDSP source commit in {path}")
    if data.get("architecture") != "stateful-v2":
        raise ValueError(f"Unsupported MIDI-DDSP bundle architecture in {path}")
    components = data.get("components")
    if not isinstance(components, dict) or not components:
        raise ValueError(f"MIDI-DDSP bundle has no components: {path}")
    voice_batch_sizes = tuple(
        sorted(int(value) for value in data.get("voice_batch_sizes", [1]))
    )
    if not voice_batch_sizes or voice_batch_sizes[0] != 1:
        raise ValueError(f"MIDI-DDSP bundle must include voice batch 1: {path}")
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
    precision = str(data.get("precision", "origin"))
    if precision != "origin":
        raise ValueError(f"MIDI-DDSP bundle must use origin precision: {path}")
    return {
        "id": str(data["id"]),
        "name": str(data["name"]),
        "architecture": str(data["architecture"]),
        "precision": precision,
        "onnx_dtype": str(data.get("onnx_dtype", "float32")),
        "recommended": bool(data.get("recommended", False)),
        "quality_status": str(data.get("quality_status", "unverified")),
        "source_commit": str(data["source_commit"]),
        "seed": int(data.get("seed", 20260724)),
        "voice_batch_sizes": list(voice_batch_sizes),
        "manifest": str(path.resolve()),
        "components": resolved_components,
    }


def scan_midi_ddsp_bundles() -> list[dict[str, object]]:
    bundles: list[dict[str, object]] = []
    bundle_root = ROOT / "models" / "midi_ddsp" / "bundles"
    if bundle_root.is_dir():
        for manifest in sorted(bundle_root.glob("*/manifest.json")):
            try:
                bundles.append(_load_midi_ddsp_bundle(manifest))
            except (OSError, ValueError, KeyError, TypeError):
                continue
    if not any(bundle["recommended"] for bundle in bundles):
        validated = next(
            (
                bundle
                for bundle in bundles
                if bundle["quality_status"] == "om_validated"
            ),
            None,
        )
        if validated is not None:
            validated["recommended"] = True
            return bundles
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


def clear_catalog_cache() -> None:
    global _CATALOG_CACHE
    with _CATALOG_CACHE_LOCK:
        _CATALOG_CACHE = None


def _catalog_cache_key() -> tuple[str, str]:
    return (str(ROOT.resolve()), str(UPLOAD_ROOT.resolve()))


def _build_catalog() -> dict[str, object]:
    return {
        "midi_files": scan_midi_files(),
        "ddsp_vst_models": scan_ddsp_vst_models(),
        "midi_ddsp_bundles": scan_midi_ddsp_bundles(),
        "midi_ddsp_reverb_assets": scan_midi_ddsp_reverb_assets(),
        "instruments": [
            {"id": index, "name": name, "verified": True}
            for index, name in enumerate(INSTRUMENTS)
        ],
    }


def catalog(*, refresh: bool = False) -> dict[str, object]:
    global _CATALOG_CACHE
    key = _catalog_cache_key()
    now = time.monotonic()
    with _CATALOG_CACHE_LOCK:
        if not refresh and _CATALOG_CACHE is not None:
            cached_key, created_at, data = _CATALOG_CACHE
            if cached_key == key and now - created_at < CATALOG_CACHE_TTL_SECONDS:
                return copy.deepcopy(data)
        data = _build_catalog()
        _CATALOG_CACHE = (key, time.monotonic(), data)
        return copy.deepcopy(data)


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

    return {key: sanitize(items) for key, items in data.items()}


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


def npu_status(board: bool) -> dict[str, object]:
    global _NPU_STATUS_CACHE
    if not board:
        return {"available": False, "exit_code": None, "output": "board only"}
    now = time.monotonic()
    with _NPU_STATUS_CACHE_LOCK:
        if _NPU_STATUS_CACHE is not None:
            created_at, data = _NPU_STATUS_CACHE
            if now - created_at < NPU_STATUS_CACHE_TTL_SECONDS:
                return dict(data)
        data = command_probe(["npu-smi", "info"], timeout=8.0)
        _NPU_STATUS_CACHE = (time.monotonic(), data)
        return dict(data)


def local_ipv4_addresses() -> list[str]:
    addresses: list[str] = []

    def add(value: str) -> None:
        address = value.strip()
        if not address or address == "0.0.0.0" or address in addresses:
            return
        addresses.append(address)

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            connection.connect(("1.1.1.1", 80))
            add(str(connection.getsockname()[0]))
    except OSError:
        pass

    for hostname in {socket.gethostname(), platform.node()}:
        if not hostname:
            continue
        try:
            _name, _aliases, host_addresses = socket.gethostbyname_ex(hostname)
            for address in host_addresses:
                add(address)
        except OSError:
            pass

    if os.name != "nt" and shutil.which("hostname") is not None:
        try:
            result = subprocess.run(
                ["hostname", "-I"],
                text=True,
                capture_output=True,
                timeout=2.0,
                check=False,
            )
            if result.returncode == 0:
                for address in result.stdout.split():
                    if "." in address:
                        add(address)
        except (OSError, subprocess.TimeoutExpired):
            pass

    routed = [address for address in addresses if not address.startswith("127.")]
    loopback = [address for address in addresses if address.startswith("127.")]
    ordered = routed + loopback
    return ordered or ["127.0.0.1"]


def system_status(active_owner: str | None = None) -> dict[str, object]:
    board = is_ascend_board()
    ip_addresses = local_ipv4_addresses()
    npu = npu_status(board)
    output = str(npu.get("output", ""))
    health_alarm = "Health" in output and "Alarm" in output
    return {
        "time": utc_timestamp(),
        "hostname": platform.node(),
        "primary_ip": ip_addresses[0],
        "ip_addresses": ip_addresses,
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
    progress_detail: dict[str, object] | None = None
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
            "progress_detail": self.progress_detail,
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
    def __init__(
        self,
        coordinator: ResourceCoordinator,
        terminal_callback: Callable[[Job], None] | None = None,
    ) -> None:
        self.coordinator = coordinator
        self.terminal_callback = terminal_callback
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
                    progress_detail=(
                        dict(data["progress_detail"])
                        if isinstance(data.get("progress_detail"), dict)
                        else None
                    ),
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
        acquired = False
        try:
            self.coordinator.acquire(job.id)
            acquired = True
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
        except BaseException as exc:
            job.state = "failed"
            job.message = str(exc)
            job.updated_at = utc_timestamp()
            if acquired:
                self.coordinator.release(job.id)
            try:
                self._persist(job)
                self._publish(job)
            except BaseException:
                pass
            raise
        return job

    @staticmethod
    def _terminate_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            process.wait(timeout=3.0)
        except (OSError, ProcessLookupError):
            if process.poll() is None:
                raise
        except subprocess.TimeoutExpired:
            if os.name == "nt":
                process.kill()
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            process.wait(timeout=2.0)

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
            termination_error: BaseException | None = None
            if process is not None and process.poll() is None:
                try:
                    self._terminate_process(process)
                except BaseException as exc:
                    termination_error = exc
                    job.message = f"{job.message}; failed to terminate child: {exc}".strip("; ")
            if process is not None and process.stdout is not None:
                process.stdout.close()
            child_exited = process is None or process.poll() is not None
            if child_exited:
                if process is not None and job.exit_code is None:
                    job.exit_code = process.returncode
                job.process = None
                self.coordinator.release(job.id)
            else:
                job.process = process
            job.updated_at = utc_timestamp()
            job.state = final_state
            self._persist(job)
            if self.terminal_callback is not None:
                try:
                    self.terminal_callback(job)
                except BaseException as exc:
                    job.message = f"{job.message}; library index failed: {exc}".strip("; ")
                    self._persist(job)
            self._publish(job)
            if termination_error is not None:
                raise termination_error

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
        event_name = event.get("event")
        if event_name in {"progress", "heartbeat"}:
            detail_keys = (
                "stage",
                "stage_progress",
                "overall_progress",
                "completed",
                "total",
                "voice_batch_index",
                "voice_batch_count",
                "component",
                "activity",
                "elapsed_seconds",
                "eta_seconds",
                "heartbeat_at",
                "paused",
            )
            detail = {
                key: event[key]
                for key in detail_keys
                if key in event
            }
            if detail:
                job.progress_detail = detail
            if "overall_progress" in event:
                job.progress = max(
                    job.progress,
                    min(1.0, max(0.0, float(event["overall_progress"]))),
                )
            elif event_name == "progress":
                total = max(1, int(event.get("total", 1)))
                legacy_progress = float(
                    event.get("played", event.get("rendered", 0))
                ) / total
                job.progress = max(job.progress, min(1.0, legacy_progress))
            if event_name == "progress":
                job.message = str(event.get("activity") or event.get("stage") or "")
        if event_name == "rendered":
            JobManager._attach_report(job)
            job.metadata["rendered"] = True
            job.metadata["cache_hit"] = bool(event.get("cache_hit", False))

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
            control_signal = signal.SIGUSR1 if action == "pause" else signal.SIGUSR2
            if job.kind == "midi-ddsp-wav-playback":
                os.kill(process.pid, control_signal)
            else:
                os.killpg(os.getpgid(process.pid), control_signal)
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
        if job.kind == "midi-ddsp-wav-playback":
            return self._signal(job, "pause")
        if job.kind != "midi-ddsp-play":
            raise RuntimeError("only MIDI-DDSP playback jobs can be paused")
        if not job.progress_detail or job.progress_detail.get("stage") != "playback":
            raise RuntimeError("MIDI-DDSP can only be paused during playback")
        return self._signal(job, "pause")

    def resume(self, job_id: str) -> Job:
        job = self.get(job_id)
        if job.kind not in {"midi-ddsp-play", "midi-ddsp-wav-playback"}:
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
