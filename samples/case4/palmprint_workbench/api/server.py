#!/usr/bin/env python3
"""FastAPI service for the palmprint React touch-screen workbench.

The recognition and storage implementations live in the package service layer
for reuse by board smoke-test tools. This module owns
the HTTP boundary, request validation, short-lived enrollment sessions and
background evaluation jobs. User-triggered enrollment and recognition images
are archived below ``ROOT/data/captures`` for this internal-test release;
continuous camera preview frames remain in memory.
"""

from __future__ import annotations

import base64
from contextlib import asynccontextmanager, contextmanager
from dataclasses import dataclass, field
from functools import partial
import json
import logging
import mimetypes
from pathlib import Path
import threading
import time
from typing import Any, Optional
from uuid import uuid4

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .. import __version__
from ..services import workbench as service_layer
from ..runtime.camera import CameraError, list_v4l2_devices
from ..config import (
    AUTO_ENROLL_COUNTDOWN_SECONDS,
    AUTO_ENROLL_FRAME_INTERVAL_SECONDS,
    AUTO_ENROLL_SAMPLE_INTERVAL_SECONDS,
    AUTO_ENROLL_STABILITY_TIMEOUT_SECONDS,
    AUTO_ENROLL_STABLE_SECONDS,
    AUTO_ENROLL_WAITING_HAND_TIMEOUT_SECONDS,
    CAMERA_DEFAULT_HEIGHT,
    CAMERA_DEFAULT_WIDTH,
    DEFAULT_THRESHOLD,
    JOB_TIMEOUT_SECONDS,
    MAX_API_REPORT_BYTES,
    MAX_API_REPORT_FILES,
    MAX_JOB_TIMEOUT_SECONDS,
    MAX_ENROLL_SAMPLES,
    MANUAL_TEST_PROFILE,
    MIN_ENROLL_SAMPLES,
    REPORT_DIR,
    RELEASE_PROFILE,
    ROOT,
    FIXED_ROI_CENTER_X,
    FIXED_ROI_CENTER_Y,
    FIXED_ROI_SIDE_RATIO,
    ROI_PIPELINE_ID,
    SERVER_HOST,
    SERVER_PORT,
    camera_resolution_options,
)
from ..domain.datasets import audit_extracted, load_dataset_manifest, records
from ..domain.admission import resolve_runtime_model
from ..runtime.acl import acl_runtime_status
from ..runtime.candidate_runners import candidate_specs, candidate_spec, run_candidate, verify_candidate_asset


APP_VERSION = __version__
STATIC_DIR = ROOT / "frontend" / "dist"
PRODUCTION_PRECISION = "mixed_fp16"
MAX_API_IDENTITIES = 100
MAX_BACKGROUND_JOBS = 2
MAX_COMPARISON_CANDIDATES = 16
JOB_TTL_SECONDS = 60 * 60
CALIBRATION_PATH = ROOT / "data" / "calibration" / "visible_light.json"
ROI_CALIBRATION_PATH = ROOT / "data" / "calibration" / "fixed_roi.json"


def _read_release_id() -> str:
    try:
        payload = json.loads((ROOT / "release_manifest.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return APP_VERSION
    value = payload.get("release_id") if isinstance(payload, dict) else None
    return str(value).strip() if value else APP_VERSION


RELEASE_ID = _read_release_id()
logger = logging.getLogger(__name__)


class RecognitionOptions(BaseModel):
    model_id: str = "ccnet"
    backend: str = "npu"
    precision: str = "mixed_fp16"
    threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    assume_roi: bool = False
    top_k: int = Field(5, ge=1, le=50)


class CameraRecognitionOptions(RecognitionOptions):
    resolution: str = f"{CAMERA_DEFAULT_WIDTH}x{CAMERA_DEFAULT_HEIGHT}"


class EnrollmentOptions(BaseModel):
    model_id: str = "ccnet"
    backend: str = "npu"
    precision: str = "mixed_fp16"
    assume_roi: bool = False
    person_id: Optional[str] = Field(default=None, min_length=1, max_length=96)
    continue_session_id: Optional[str] = None
    reenroll_palm_id: Optional[str] = None
    display_name: Optional[str] = Field(default=None, max_length=128)
    palm_side: str = Field(..., min_length=1, max_length=16)
    mode: str = "self_service"


class EnrollmentAutoCaptureOptions(BaseModel):
    device: str = Field(..., min_length=1, max_length=256)
    resolution: str = f"{CAMERA_DEFAULT_WIDTH}x{CAMERA_DEFAULT_HEIGHT}"


class FixedRoiCalibration(BaseModel):
    center_x: float = Field(default=FIXED_ROI_CENTER_X, ge=0.0, le=1.0)
    center_y: float = Field(default=FIXED_ROI_CENTER_Y, ge=0.0, le=1.0)
    side_ratio: float = Field(default=FIXED_ROI_SIDE_RATIO, ge=0.4, le=1.0)


class EnrollmentCommit(BaseModel):
    # Identity is created with the session.  These two optional fields retain
    # API compatibility for a session created by an older UI.
    person_id: Optional[str] = Field(default=None, min_length=1, max_length=96)
    display_name: Optional[str] = Field(default=None, max_length=128)
    name: Optional[str] = Field(default=None, max_length=128)
    palm_side: Optional[str] = None
    # Optional[...] keeps the request model importable on the board's
    # Python 3.9 runtime; Pydantic evaluates these annotations at import.
    model_id: Optional[str] = None
    backend: Optional[str] = None
    precision: Optional[str] = None


class EvaluationOptions(BaseModel):
    dataset_id: str = "tongji"
    spectrum: str = "B"
    model_id: str = "ccnet"
    backend: str = "npu"
    precision: str = "mixed_fp16"
    threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    assume_roi: bool = False
    max_identities: int = Field(default=10, ge=1, le=MAX_API_IDENTITIES)
    threads: int = Field(1, ge=1, le=1)
    timeout_seconds: int = Field(
        default=JOB_TIMEOUT_SECONDS,
        ge=10,
        le=MAX_JOB_TIMEOUT_SECONDS,
    )


class ComparisonOptions(BaseModel):
    candidate_ids: list[str] = Field(
        ...,
        min_items=1,
        max_items=MAX_COMPARISON_CANDIDATES,
    )
    dataset_id: str = "tongji"
    spectrum: str = "cross_session"
    precision: str = "mixed_fp16"
    threshold: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    assume_roi: bool = False
    max_identities: int = Field(default=10, ge=1, le=MAX_API_IDENTITIES)
    timeout_seconds: int = Field(
        default=JOB_TIMEOUT_SECONDS,
        ge=10,
        le=MAX_JOB_TIMEOUT_SECONDS,
    )


@dataclass
class EnrollmentSample:
    sample_id: str
    roi: np.ndarray
    capture_id: str
    preview_url: str | None
    roi_url: str | None
    quality: dict[str, Any]
    geometry: dict[str, Any]


@dataclass
class EnrollmentState:
    session_id: str
    options: EnrollmentOptions
    samples: list[EnrollmentSample] = field(default_factory=list)
    capture_ids: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    phase: str = "ready"
    guide: str = "选择掌侧后开始自动采集"
    rejection_counts: dict[str, int] = field(default_factory=dict)
    device: str | None = None
    resolution: str | None = None
    worker: threading.Thread | None = None
    stop_event: threading.Event = field(default_factory=threading.Event)
    started_at: float | None = None
    completed_at: float | None = None
    last_preview_url: str | None = None
    last_roi_url: str | None = None
    last_quality: dict[str, Any] = field(default_factory=dict)
    last_geometry: dict[str, Any] = field(default_factory=dict)
    last_error: str | None = None
    last_accepted_at: float = 0.0
    roi_config: dict[str, float] = field(default_factory=dict)
    actual_resolution: str | None = None


@dataclass
class EvaluationState:
    evaluation_id: str
    request: EvaluationOptions
    status: str = "queued"
    progress: float = 0.0
    message: str = "等待执行"
    result: dict[str, Any] | None = None
    reports: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None


@dataclass
class ComparisonState:
    comparison_id: str
    request: ComparisonOptions
    status: str = "queued"
    progress: float = 0.0
    message: str = "等待执行"
    rows: list[dict[str, Any]] = field(default_factory=list)
    report_path: str | None = None
    error: str | None = None
    started_at: float | None = None
    finished_at: float | None = None


WORKBENCH = service_layer.WORKBENCH


def _runtime_specs() -> list[Any]:
    """Resolve the active release channel's NPU model set."""

    return WORKBENCH.registry.all(
        include_manual_test=RELEASE_PROFILE == MANUAL_TEST_PROFILE
    )
_SESSION_LOCK = threading.RLock()
_SESSIONS: dict[str, EnrollmentState] = {}
_AUTO_ENROLL_LOCK = threading.RLock()
_AUTO_ENROLL_SESSION_ID: str | None = None
_EVALUATION_LOCK = threading.RLock()
_EVALUATIONS: dict[str, EvaluationState] = {}
_COMPARISONS: dict[str, ComparisonState] = {}
_LIFECYCLE_LOCK = threading.RLock()
_WORKER_GATE = threading.Lock()
_BACKGROUND_WORKERS: set[threading.Thread] = set()
_SERVICE_STOPPING = False
_LAST_SERVICE_SHUTDOWN: dict[str, Any] | None = None


def _safe_report_paths(paths: Any) -> list[Path]:
    """Resolve only API-owned report files below ``reports/runs``."""

    if not isinstance(paths, dict):
        return []
    run_root = (REPORT_DIR / "runs").resolve()
    result: list[Path] = []
    for value in paths.values():
        if not value:
            continue
        try:
            path = Path(str(value)).resolve()
            path.relative_to(run_root)
        except (TypeError, ValueError, OSError):
            continue
        if path.name.startswith("api_"):
            result.append(path)
    return result


def _delete_report_paths(paths: Any) -> None:
    for path in _safe_report_paths(paths):
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue


def _prune_api_report_files_locked() -> None:
    """Bound API reports without touching offline benchmark evidence."""

    run_root = (REPORT_DIR / "runs").resolve()
    if not run_root.is_dir():
        return
    protected: set[Path] = set()
    for state in _EVALUATIONS.values():
        protected.update(_safe_report_paths(state.reports))
    for state in _COMPARISONS.values():
        protected.update(_safe_report_paths({"report": state.report_path}))
    files: list[Path] = []
    for path in run_root.iterdir():
        if not path.is_file() or not path.name.startswith("api_") or path in protected:
            continue
        files.append(path)
    files.sort(key=lambda path: path.stat().st_mtime if path.exists() else 0)
    total_bytes = sum(path.stat().st_size for path in files if path.exists())
    while files and (
        len(files) > MAX_API_REPORT_FILES or total_bytes > MAX_API_REPORT_BYTES
    ):
        path = files.pop(0)
        try:
            total_bytes -= path.stat().st_size
            path.unlink(missing_ok=True)
        except OSError:
            continue


def _prune_finished_jobs_locked(now: float | None = None) -> None:
    """Bound job state and clean expired API-owned report files."""

    cutoff = (time.time() if now is None else now) - JOB_TTL_SECONDS
    for jobs in (_EVALUATIONS, _COMPARISONS):
        expired = [
            job_id
            for job_id, state in jobs.items()
            if state.finished_at is not None
            and state.finished_at < cutoff
            and state.status not in {"queued", "running"}
        ]
        for job_id in expired:
            state = jobs.pop(job_id, None)
            if isinstance(state, EvaluationState):
                _delete_report_paths(state.reports)
            elif isinstance(state, ComparisonState):
                _delete_report_paths({"report": state.report_path})
    _prune_api_report_files_locked()


def _reserve_background_job(state: EvaluationState | ComparisonState) -> None:
    """Atomically enforce the single-NPU service's bounded task queue."""

    with _EVALUATION_LOCK:
        _prune_finished_jobs_locked()
        active = sum(
            item.status in {"queued", "running"}
            for item in list(_EVALUATIONS.values()) + list(_COMPARISONS.values())
        )
        if active >= MAX_BACKGROUND_JOBS:
            raise HTTPException(
                status_code=429,
                detail=f"后台任务队列已满（最多 {MAX_BACKGROUND_JOBS} 个等待或运行任务）",
            )
        if isinstance(state, EvaluationState):
            _EVALUATIONS[state.evaluation_id] = state
        else:
            _COMPARISONS[state.comparison_id] = state


class _ServiceStopping(RuntimeError):
    """Raised when a background job loses the race with ASGI shutdown."""


class _JobTimeout(RuntimeError):
    """A bounded HTTP evaluation exceeded its caller-selected deadline."""


@contextmanager
def _workbench_job_lock():
    """Enter the serialized Workbench lock without a shutdown race.

    The gate is held while acquiring ``execution_lock``.  Shutdown takes the
    same gate before setting ``_SERVICE_STOPPING``, so a queued worker cannot
    start after shutdown has begun.
    """

    with _WORKER_GATE:
        with _EVALUATION_LOCK:
            if _SERVICE_STOPPING:
                raise _ServiceStopping("服务正在关闭，任务未启动")
        WORKBENCH.execution_lock.acquire()
    try:
        yield
    finally:
        WORKBENCH.execution_lock.release()


def _start_background_worker(target: Any, state: Any, name: str) -> threading.Thread:
    """Register a daemon worker before it can observe the shared runtime."""

    with _WORKER_GATE:
        with _EVALUATION_LOCK:
            if _SERVICE_STOPPING:
                # The route stores its state before it calls this helper. If
                # shutdown wins that race, leave an explicit terminal state
                # instead of an orphaned queued job that can never run.
                if hasattr(state, "status"):
                    state.status = "cancelled"
                if hasattr(state, "message"):
                    state.message = "服务正在关闭，任务未启动"
                if hasattr(state, "finished_at"):
                    state.finished_at = time.time()
                raise HTTPException(status_code=503, detail="服务正在关闭，暂不接受后台任务")
            worker = threading.Thread(
                target=_background_worker_wrapper,
                args=(target, state),
                daemon=True,
                name=name,
            )
            _BACKGROUND_WORKERS.add(worker)
        worker.start()
    return worker


def _background_worker_wrapper(target: Any, state: Any) -> None:
    try:
        target(state)
    finally:
        with _EVALUATION_LOCK:
            _BACKGROUND_WORKERS.discard(threading.current_thread())


def _jsonable(value: Any) -> Any:
    """Convert NumPy/Pandas values to values accepted by JSON responses."""

    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if hasattr(value, "item"):
        try:
            return _jsonable(value.item())
        except (TypeError, ValueError):
            pass
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


def _model_dict(value: BaseModel) -> dict[str, Any]:
    """Support both Pydantic 1 (board images) and Pydantic 2 (dev images)."""

    method = getattr(value, "model_dump", None)
    if method is not None:
        return dict(method())
    return dict(value.dict())


def _frame_data_url(image: Any, *, image_format: str = ".jpg") -> str | None:
    if image is None:
        return None
    array = image
    if getattr(array, "ndim", 0) == 2:
        array = cv2.cvtColor(array, cv2.COLOR_GRAY2BGR)
    elif getattr(array, "ndim", 0) == 3 and array.shape[2] >= 3:
        array = cv2.cvtColor(array[:, :, :3], cv2.COLOR_RGB2BGR)
    else:
        return None
    ok, encoded = cv2.imencode(image_format, array, [cv2.IMWRITE_JPEG_QUALITY, 88])
    if not ok:
        return None
    mime = "image/png" if image_format.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(encoded.tobytes()).decode('ascii')}"


def _parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "y"}


def _form_float(value: Any, default: float) -> float:
    try:
        return float(default if value in (None, "") else value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="threshold 必须是数字") from exc


def _form_optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="threshold 必须是数字") from exc


def _resolved_threshold(options: RecognitionOptions) -> float:
    """Resolve an omitted request threshold from the selected model metadata."""

    if options.threshold is not None:
        return float(options.threshold)
    try:
        return float(WORKBENCH.registry.model_threshold(options.model_id))
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="模型缺少校准阈值") from exc


def _runtime_calibration(
    options: RecognitionOptions, *, camera_resolution: str | None,
    camera_physical_id: str | None = None, roi_config: dict[str, float] | None = None,
) -> dict[str, Any] | None:
    """Return a calibration only when it exactly matches runtime provenance."""

    try:
        payload = json.loads(CALIBRATION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or not payload.get("approved", False):
        return None
    required = {
        "model_id": options.model_id,
        "backend": options.backend,
        "precision": options.precision,
        "roi_pipeline_id": ROI_PIPELINE_ID,
    }
    if any(str(payload.get(key, "")) != value for key, value in required.items()):
        return None
    if not camera_resolution or str(payload.get("camera_resolution", "")) != camera_resolution:
        return None
    if not camera_physical_id or payload.get("camera_physical_id") != camera_physical_id:
        return None
    if payload.get("roi_config") != roi_config:
        return None
    try:
        spec = resolve_runtime_model(
            options.model_id,
            registry=WORKBENCH.registry,
            verify_assets=True,
            include_manual_test=RELEASE_PROFILE == MANUAL_TEST_PROFILE,
        )
        asset = WORKBENCH.registry.runtime_asset_status(spec, verify_hash=True).get(
            "assets", {}
        ).get("mixed_fp16_om", {})
        current_om_hash = asset.get("actual_sha256")
    except (KeyError, OSError, RuntimeError, ValueError, AttributeError):
        return None
    report_om = payload.get("om") if isinstance(payload.get("om"), dict) else {}
    if not current_om_hash or report_om.get("sha256") != current_om_hash:
        return None
    threshold = payload.get("recommended_threshold")
    if not isinstance(threshold, (int, float)) or not 0.0 <= float(threshold) <= 1.0:
        return None
    return payload


def _form_int(value: Any, default: int) -> int:
    try:
        return int(default if value in (None, "") else value)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="top_k 必须是整数") from exc


def _parse_resolution(value: str | None) -> tuple[int, int]:
    try:
        width, height = service_layer.parse_camera_resolution(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if width > 4096 or height > 2160:
        raise HTTPException(status_code=422, detail="摄像头分辨率超出允许范围")
    return width, height


def _npu_runtime_health() -> dict[str, str]:
    """Check whether the PyACL Python module is importable without touching NPU."""

    try:
        import acl  # type: ignore  # noqa: F401 - presence is the diagnostic
    except Exception as exc:  # ImportError and missing CANN shared libraries
        return {
            "status": "unavailable",
            "detail": f"PyACL 未加载：{exc}",
        }
    return {
        "status": "importable",
        "detail": "PyACL 模块已加载；首次识别时初始化 NPU 设备",
    }


def _npu_error_detail(exc: BaseException) -> str:
    """Turn common PyACL startup errors into an actionable UI message."""

    message = str(exc).strip() or exc.__class__.__name__
    lowered = message.lower()
    if "pyacl is unavailable" in lowered or "no module named 'acl'" in lowered:
        return (
            "NPU 后端未打开：当前服务进程没有加载 PyACL。请在同一终端先执行 "
            "source /usr/local/Ascend/ascend-toolkit/set_env.sh，再重启服务。"
        )
    if "acl.init" in lowered or "set_device" in lowered:
        return f"NPU/PyACL 初始化失败：{message}。请检查 CANN 环境、设备号和 npu-smi 状态。"
    if "acl cleanup" in lowered or "runtime shutdown" in lowered or "blocked_active_runners" in lowered:
        return (
            f"NPU/PyACL 资源释放异常：{message}。请保留 reports/system 生命周期追踪和板端 LPM 日志，"
            "不要切换到 CPU 后端绕过故障。"
        )
    return f"NPU/PyACL 推理失败：{message}"


def _preview_jpeg(frame: Any, *, max_width: int, quality: int) -> tuple[bytes, tuple[int, int]]:
    """Encode a smaller preview while keeping the full-resolution RGB frame for recognition."""

    image = np.asarray(frame.rgb)
    if image.ndim != 3 or image.shape[2] < 3:
        raise CameraError("摄像头返回了无效图像")
    actual_height, actual_width = image.shape[:2]
    preview = image[:, :, :3]
    if actual_width > max_width:
        target_height = max(1, round(actual_height * max_width / actual_width))
        preview = cv2.resize(preview, (max_width, target_height), interpolation=cv2.INTER_AREA)
    bgr = cv2.cvtColor(np.ascontiguousarray(preview), cv2.COLOR_RGB2BGR)
    encoded, payload = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not encoded:
        raise CameraError("OpenCV 无法编码摄像头预览")
    return payload.tobytes(), (int(preview.shape[1]), int(preview.shape[0]))


def _validate_options(options: RecognitionOptions) -> None:
    if options.backend != "npu":
        raise HTTPException(
            status_code=422,
            detail="正式 API 仅支持 backend=npu；CPU 后端仅用于离线 benchmark",
        )
    # Keep the production boundary explicit: legacy static entries such as
    # EDCC and conversion-only CompNet live in ``offline_models`` and must not
    # be reported as merely pending candidates.
    try:
        WORKBENCH.registry.get_offline_model(options.model_id)
    except KeyError:
        pass
    else:
        raise HTTPException(
            status_code=503,
            detail="模型仅登记为离线研究资产，未进入生产 NPU registry",
        )
    try:
        spec = resolve_runtime_model(
            options.model_id,
            registry=WORKBENCH.registry,
            verify_assets=True,
            include_manual_test=RELEASE_PROFILE == MANUAL_TEST_PROFILE,
        )
    except PermissionError as exc:
        raise HTTPException(status_code=503, detail=f"候选模型暂不可用：{exc}") from exc
    except KeyError as exc:
        try:
            WORKBENCH.registry.get_offline_model(options.model_id)
        except KeyError:
            raise HTTPException(status_code=422, detail=f"未知模型: {options.model_id}") from exc
        raise HTTPException(
            status_code=503,
            detail="模型仅登记为离线研究资产，未进入生产 NPU registry",
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=f"模型资产不可用：{exc}") from exc
    if spec.id == "edcc" or spec.kind != "embedding":
        raise HTTPException(
            status_code=422,
            detail="EDCC/非 embedding 模型不属于正式 NPU API；EDCC 仅支持离线 CPU benchmark",
        )
    if options.precision != PRODUCTION_PRECISION:
        raise HTTPException(status_code=422, detail="正式 API 仅支持 precision=mixed_fp16")
    if spec.id == "compnet":
        try:
            service_layer._require_recognition_model(spec.id)
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
    # A registered graph is not automatically a production model.  Require
    # the same mixed-FP16 OM and admission marker used by bootstrap before
    # opening a template, camera, or inference path.
    payload = _model_payload(spec)
    if not payload.get("usable_for_recognition", False):
        reason = payload.get("conversion_only") and "模型仍为 conversion-only" or "mixed FP16 OM 尚未完成 NPU 准入"
        raise HTTPException(status_code=503, detail=f"模型暂不可用：{reason}")


def _decode_image_bytes(payload: bytes) -> Any:
    if not payload or len(payload) > 25 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="图像为空或超过 25 MB")
    buffer = cv2.imdecode(np.frombuffer(payload, dtype=np.uint8), cv2.IMREAD_COLOR)
    if buffer is None or buffer.size == 0:
        raise HTTPException(status_code=422, detail="无法解码上传图像")
    return cv2.cvtColor(buffer, cv2.COLOR_BGR2RGB)


async def _image_from_request(request: Request) -> Any:
    """Read an uploaded image without declaring a multipart dependency at import time."""

    try:
        form = await request.form()
    except Exception as exc:  # python-multipart gives a clearer message on the board
        raise HTTPException(status_code=415, detail=f"需要 multipart/form-data 图像上传: {exc}") from exc
    upload = form.get("image")
    if upload is None:
        raise HTTPException(status_code=422, detail="缺少 image 字段")
    if hasattr(upload, "read"):
        payload = await upload.read()
    elif isinstance(upload, (bytes, bytearray)):
        payload = bytes(upload)
    else:
        raise HTTPException(status_code=422, detail="image 字段不是文件")
    return _decode_image_bytes(payload)


def _safe_capture_save(*, original: Any, roi: Any | None, metadata: dict[str, Any]) -> str | None:
    """Archive a user-triggered frame without changing recognition behavior."""

    try:
        record = WORKBENCH.captures.save(original=original, roi=roi, metadata=metadata)
        return str(record["capture_id"])
    except Exception:
        logger.exception("capture archive failed; recognition result is preserved")
        return None


def _image_resolution(image: Any) -> str | None:
    try:
        height, width = np.asarray(image).shape[:2]
        return f"{int(width)}x{int(height)}"
    except (AttributeError, TypeError, ValueError):
        return None


def _recognition_payload(
    image: Any,
    options: RecognitionOptions,
    *,
    source: str = "upload",
    device: str | None = None,
    requested_resolution: str | None = None,
    camera_resolution: str | None = None,
    roi_config: dict[str, float] | None = None,
) -> dict[str, Any]:
    _validate_options(options)
    threshold = _resolved_threshold(options)
    calibration = _runtime_calibration(
        options, camera_resolution=camera_resolution,
        camera_physical_id=_camera_physical_id(device) if source == "camera" else None,
        roi_config=roi_config,
    )
    if calibration is not None:
        threshold = float(calibration["recommended_threshold"])
    started = time.perf_counter_ns()
    roi_result, error = service_layer._extract(
        image, options.assume_roi, roi_config=roi_config
    )
    roi_finished = time.perf_counter_ns()
    if error:
        timing = {
            "ROI / quality ms": (roi_finished - started) / 1e6,
            "total ms": (roi_finished - started) / 1e6,
        }
        payload = {
            "accepted": False,
            "outcome": "roi_rejected",
            "score": 0.0,
            "status": f"未完成：{error}",
            "quality": _jsonable(roi_result.quality),
            "geometry": _jsonable(roi_result.geometry),
            "roi_pipeline_id": roi_result.pipeline_id,
            "matches": [],
            "timing": timing,
            "preview_url": _frame_data_url(roi_result.preview),
            "roi_url": _frame_data_url(roi_result.roi),
        }
        capture_id = _safe_capture_save(
            original=image,
            roi=roi_result.roi,
            metadata={
                "purpose": "recognition",
                "source": source,
                "model_id": options.model_id,
                "backend": options.backend,
                "precision": options.precision,
                "device": device,
                "camera_physical_id": _camera_physical_id(device) if source == "camera" else None,
                "requested_resolution": requested_resolution,
                "actual_resolution": _image_resolution(image),
                "roi_ok": False,
                "quality": _jsonable(roi_result.quality),
                "geometry": _jsonable(roi_result.geometry),
                "roi_pipeline_id": roi_result.pipeline_id,
                "status": f"recognition_failed: {error}",
                "timing": timing,
            },
        )
        if capture_id:
            payload["capture_id"] = capture_id
        return payload
    assert roi_result.roi is not None
    try:
        with WORKBENCH.execution_lock:
            adapter = WORKBENCH.adapter(options.model_id, options.backend, options.precision)
            encoded = adapter.encode(roi_result.roi)
            search_started = time.perf_counter_ns()
            namespace = service_layer._template_namespace(
                options.model_id, options.backend, options.precision
            )
            try:
                result = WORKBENCH.store.search(
                    namespace,
                    adapter,
                    encoded.code,
                    threshold=threshold,
                    top_k=int(options.top_k),
                    roi_pipeline_id=ROI_PIPELINE_ID,
                    roi_config=roi_config,
                )
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=f"模板存储不可用：{exc}") from exc
    except Exception as exc:
        _safe_capture_save(
            original=image,
            roi=roi_result.roi,
            metadata={
                "purpose": "recognition",
                "source": source,
                "model_id": options.model_id,
                "backend": options.backend,
                "precision": options.precision,
                "device": device,
                "camera_physical_id": _camera_physical_id(device) if source == "camera" else None,
                "requested_resolution": requested_resolution,
                "actual_resolution": _image_resolution(image),
                "roi_ok": True,
                "quality": _jsonable(roi_result.quality),
                "status": f"recognition_error: {type(exc).__name__}: {exc}",
                "timing": {"ROI / quality ms": (roi_finished - started) / 1e6},
            },
        )
        raise
    finished = time.perf_counter_ns()
    if not result.get("matches"):
        outcome = "no_templates"
        status = "当前模型和 ROI 管线没有可用模板"
        accepted: bool | None = False
    elif calibration is None:
        outcome = "candidates_only"
        status = f"候选检索 | 最高分 {float(result.get('score', 0.0)):.4f}（尚未完成本机阈值校准）"
        accepted = None
    elif result.get("accepted"):
        outcome = "matched"
        status = f"匹配 | {result.get('display_name', result.get('user_name', ''))} · {result.get('palm_side', '')}"
        accepted = True
    else:
        outcome = "below_threshold"
        status = f"低于校准阈值 | 最高分 {float(result.get('score', 0.0)):.4f}，阈值 {threshold:.4f}"
        accepted = False
    matches = [
        {
            "palm_id": item.get("palm_id") or item.get("user_id"),
            "person_id": item.get("person_id") or item.get("user_id"),
            "display_name": item.get("display_name") or item.get("user_name"),
            "user_id": item.get("palm_id") or item.get("user_id"),
            "user_name": item.get("display_name") or item.get("user_name"),
            "palm_side": item.get("palm_side"),
            "score": float(item.get("score", 0.0)),
            "samples": int(item.get("samples", 0)),
        }
        for item in result.get("matches", [])
    ]
    payload = {
        "accepted": accepted,
        "outcome": outcome,
        "score": float(result.get("score", 0.0)),
        "palm_id": result.get("palm_id") or result.get("user_id"),
        "person_id": result.get("person_id") or result.get("user_id"),
        "display_name": result.get("display_name") or result.get("user_name"),
        "user_id": result.get("palm_id") or result.get("user_id"),
        "user_name": result.get("display_name") or result.get("user_name"),
        "palm_side": result.get("palm_side"),
        "status": status,
        "quality": _jsonable(roi_result.quality),
        "geometry": _jsonable(roi_result.geometry),
        "roi_pipeline_id": roi_result.pipeline_id,
        "template_summary": WORKBENCH.store.summary(
            service_layer._template_namespace(
                options.model_id, options.backend, options.precision
            ),
            roi_pipeline_id=ROI_PIPELINE_ID,
        ),
        "calibration": (
            {"ready": True, "recommended_threshold": calibration["recommended_threshold"]}
            if calibration is not None
            else {"ready": False}
        ),
        "matches": matches,
        "timing": {
            "ROI / quality ms": (roi_finished - started) / 1e6,
            "preprocess ms": float(encoded.preprocess_ms),
            "model ms": float(encoded.inference_ms),
            "search ms": (finished - search_started) / 1e6,
            "total ms": (finished - started) / 1e6,
        },
        "preview_url": _frame_data_url(roi_result.preview),
        "roi_url": _frame_data_url(roi_result.roi),
    }
    capture_id = _safe_capture_save(
        original=image,
        roi=roi_result.roi,
        metadata={
            "purpose": "recognition",
            "source": source,
            "model_id": options.model_id,
            "backend": options.backend,
            "precision": options.precision,
            "device": device,
            "camera_physical_id": _camera_physical_id(device) if source == "camera" else None,
            "requested_resolution": requested_resolution,
            "actual_resolution": _image_resolution(image),
            "roi_ok": True,
            "quality": _jsonable(roi_result.quality),
            "accepted": payload["accepted"],
            "score": payload["score"],
            "status": status,
            "timing": payload["timing"],
            "palm_id": payload.get("palm_id"),
            "person_id": payload.get("person_id"),
            "display_name": payload.get("display_name"),
            "user_id": payload.get("user_id"),
            "user_name": payload.get("user_name"),
            "palm_side": payload.get("palm_side"),
            "roi_pipeline_id": roi_result.pipeline_id,
        },
    )
    if capture_id:
        payload["capture_id"] = capture_id
    return payload


def _model_payload(spec: Any) -> dict[str, Any]:
    marker = spec.path("conversion_only_marker")
    conversion_only = False
    usable = True
    eligibility_reason: str | None = None
    candidate_id = spec.raw.get("candidate_id")
    production_candidate = bool(spec.raw.get("production_candidate", False))
    manual_candidate = bool(spec.raw.get("manual_test_candidate", False))
    # Manual-test candidates are deliberately outside the production
    # registry, but they still use the same immutable asset and contract
    # gate.  Do not let the absence of the production_candidate marker turn
    # them into a false "not production" result in the manual profile.
    if (production_candidate or manual_candidate) and isinstance(candidate_id, str):
        admission = (
            WORKBENCH.registry.manual_test_candidate_admission(
                candidate_id, verify_assets=True
            )
            if manual_candidate and RELEASE_PROFILE == MANUAL_TEST_PROFILE
            else WORKBENCH.registry.candidate_admission(
                candidate_id, verify_assets=True
            )
        )
        if not admission.admitted:
            conversion_only = True
            usable = False
            eligibility_reason = admission.reason or "候选尚未完成生产 NPU 准入"
        elif manual_candidate:
            eligibility_reason = "人工测试发布：稳定性准入待人工验收"
    elif spec.id == "compnet":
        # The legacy static graph is a conversion fixture, not a production
        # candidate.  Its marker must not be able to promote it by itself;
        # official CompNet checkpoints use separate candidate IDs and the full
        # registry admission evidence above.
        conversion_only = True
        usable = False
        eligibility_reason = "静态 CompNet 未完成候选级生产 NPU 准入"
    elif marker and marker.is_file():
        try:
            metadata = json.loads(marker.read_text(encoding="utf-8"))
            conversion_only = not bool(metadata.get("accuracy_eligible", False))
            usable = not conversion_only
        except (OSError, json.JSONDecodeError):
            conversion_only = True
            usable = False
    if bool(spec.raw.get("offline_candidate", False)) and not manual_candidate and not bool(
        spec.raw.get("production_enabled", False)
    ):
        # Candidate adapters exist for controlled board evaluation, but they
        # must never appear in the production model selector until a reviewed
        # manifest explicitly records the full NPU admission gate.
        conversion_only = True
        usable = False
        eligibility_reason = "候选尚未完成生产 NPU 准入"
    # File existence alone is not a production gate. Bytes and SHA-256 are
    # checked against immutable registry metadata without initializing ACL.
    try:
        asset_status = WORKBENCH.registry.runtime_asset_status(
            spec,
            verify_hash=True,
        )
    except (KeyError, OSError, ValueError) as exc:
        asset_status = {
            "ok": False,
            "status": "blocked",
            "reasons": [str(exc)],
            "assets": {},
        }
    npu_ready = bool(asset_status.get("ok", False))
    if spec.kind != "embedding":
        conversion_only = True
        usable = False
        eligibility_reason = "仅 embedding 模型可进入正式识别服务"
    usable = usable and npu_ready
    if not npu_ready and eligibility_reason is None:
        reasons = asset_status.get("reasons", [])
        eligibility_reason = str(reasons[0]) if reasons else "mixed-FP16 资产校验未通过"
    available = ["npu"] if spec.kind == "embedding" and usable else []
    calibration = spec.raw.get("calibration", {})
    threshold = calibration.get("threshold") if isinstance(calibration, dict) else None
    return {
        "id": spec.id,
        "model_id": spec.id,
        "display_name": spec.display_name,
        "license": spec.license,
        "research_only": spec.research_only,
        "source": spec.source,
        "revision": spec.revision,
        "input_shape": list(spec.input_shape),
        "feature_dim": spec.feature_dim,
        "metric": spec.metric,
        "available_backends": available,
        "precision_options": [PRODUCTION_PRECISION] if spec.kind == "embedding" else [],
        "threshold": threshold,
        "calibration": calibration if isinstance(calibration, dict) else {},
        "manual_test_pending": bool(spec.raw.get("manual_test_pending", False)),
        "conversion_only": conversion_only,
        "usable_for_recognition": usable,
        "eligibility_reason": eligibility_reason,
        "status": {
            "npu_origin": "not_supported",
            "npu_mixed_fp16": "ready" if npu_ready else "missing",
        },
        "asset_status": asset_status,
    }


def _candidate_task_type(candidate: Any) -> str:
    """Normalize manifest task text for the heterogeneous comparison table."""

    raw = dict(getattr(candidate, "raw", {}) or {})
    explicit = raw.get("task_type") or raw.get("taskType")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip().lower()
    task = str(getattr(candidate, "task", "")).lower()
    modality = str(getattr(candidate, "modality", "")).lower()
    kind = str(getattr(candidate, "kind", "")).lower()
    if "segmentation" in task or "segment" in task:
        return "segmentation"
    if "detector" in task or "detection" in task:
        return "detector"
    if "roi" in task or "alignment" in task or "localization" in task or "keypoint" in task:
        return "roi"
    if "sdk" in task or kind == "sdk":
        return "sdk"
    if "vein" in modality:
        return "vein_embedding"
    if "classifier" in task or "classification" in task or "logit" in task:
        return "classifier"
    if "embedding" in task or "feature" in task or kind == "embedding":
        return "embedding"
    if "code" in task or kind == "code":
        return "code"
    return "audit"


def _dataset_payload(item: dict[str, Any]) -> dict[str, Any]:
    extract_dir = (ROOT / item["extract_dir"]).resolve()
    archive = (ROOT / item["archive"]).resolve()
    ready = False
    if item["id"] in {"tongji", "polyu"} and extract_dir.is_dir():
        try:
            ready = bool(audit_extracted(item["id"]).get("ready"))
        except (OSError, ValueError):
            ready = False
    return {
        "id": item["id"],
        "dataset_id": item["id"],
        "display_name": item.get("display_name", item["id"]),
        "status": "ready" if ready else "downloaded" if archive.is_file() else "unavailable",
        "archive_present": archive.is_file(),
        "extract_present": extract_dir.is_dir(),
        "description": "结构校验通过" if ready else "等待下载或结构校验",
        "spectra": item.get("spectra", []),
    }


def _status_items(rows: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    """Normalize legacy Chinese DataFrame columns for the React asset lists."""

    title_keys = {
        "models": ("模型", "名称", "节点"),
        "datasets": ("数据集", "名称"),
        "cameras": ("节点", "名称"),
    }.get(kind, ("名称", "节点"))
    result = []
    for row in rows:
        title = next((row.get(key) for key in title_keys if row.get(key) not in (None, "")), "未命名项目")
        status = row.get("状态") or row.get("转换状态") or row.get("CPU") or row.get("status")
        description = row.get("说明") or row.get("用途") or row.get("许可") or row.get("state")
        fields = {key: value for key, value in row.items() if key not in title_keys and key not in {"状态", "说明", "state"}}
        result.append({"id": str(title), "title": str(title), "status": status, "description": description, "fields": fields, **row})
    return result


def _records(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    try:
        values = frame.to_dict(orient="records")
    except AttributeError:
        return []
    return [_jsonable(item) for item in values]


_ROI_CALIBRATION_LOCK = threading.RLock()


def _camera_physical_id(device: str | None) -> str:
    if not device:
        return "unknown"
    for item in list_v4l2_devices():
        if item.path == str(device) or str(device) in item.aliases:
            return item.physical_id or item.path
    return str(device)


def _fixed_roi_config(device: str | None, resolution: str | None) -> dict[str, float]:
    default = {
        "center_x": FIXED_ROI_CENTER_X,
        "center_y": FIXED_ROI_CENTER_Y,
        "side_ratio": FIXED_ROI_SIDE_RATIO,
    }
    key = f"{_camera_physical_id(device)}|{resolution or 'default'}"
    with _ROI_CALIBRATION_LOCK:
        try:
            payload = json.loads(ROI_CALIBRATION_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return default
    entry = payload.get(key) if isinstance(payload, dict) else None
    if not isinstance(entry, dict):
        return default
    try:
        return FixedRoiCalibration(**entry).model_dump()
    except AttributeError:
        return FixedRoiCalibration(**entry).dict()
    except Exception:
        return default


def _save_fixed_roi_config(device: str, resolution: str, config: FixedRoiCalibration) -> dict[str, Any]:
    key = f"{_camera_physical_id(device)}|{resolution}"
    try:
        payload = json.loads(ROI_CALIBRATION_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        value = config.model_dump()
    except AttributeError:
        value = config.dict()
    payload[key] = value
    ROI_CALIBRATION_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary = ROI_CALIBRATION_PATH.with_suffix(".tmp")
    with _ROI_CALIBRATION_LOCK:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(ROI_CALIBRATION_PATH)
    return {"key": key, **value, "roi_pipeline_id": ROI_PIPELINE_ID}


def _camera_payload() -> list[dict[str, Any]]:
    states = WORKBENCH.camera_states()
    resolutions = list(camera_resolution_options())
    rows = []
    for device in list_v4l2_devices():
        rows.append(
            {
                "device": device.path,
                "path": device.path,
                "name": device.name or "V4L2 视频节点",
                "physical_id": device.physical_id or device.path,
                "aliases": list(device.aliases or (device.path,)),
                "resolutions": resolutions,
                "state": states.get(device.path, "已检测，未打开"),
                **getattr(WORKBENCH, "camera_details", {}).get(device.path, {}),
            }
        )
    return rows


def _status_payload() -> dict[str, Any]:
    try:
        summary_text, model_frame, dataset_frame, camera_frame = service_layer.system_status()
        plain_summary = str(summary_text).replace("**", "")
        summary = {
            "status": "warning" if "alarm" in plain_summary.lower() else "ok",
            "message": plain_summary,
            "text": plain_summary,
            "cann": service_layer._status_summary(service_layer._cann_status()),
            "npu": service_layer._status_summary(
                service_layer._command_output(["npu-smi", "info"])
            ),
            "camera_count": len(_camera_payload()),
            "template_count": sum(
                len(
                    WORKBENCH.store.users(
                        service_layer._template_namespace(spec.id, "npu", precision)
                    )
                )
                for spec in _runtime_specs()
                if spec.kind == "embedding"
                for precision in (PRODUCTION_PRECISION,)
            ),
        }
        # The production status surface is intentionally NPU-only.  Detailed
        # CPU/EDCC and unadmitted candidates remain available from
        # /api/candidates for research auditing, never from this live asset
        # summary used by the touch UI.
        models = [_model_payload(spec) for spec in _production_model_specs()]
        datasets = _status_items(_records(dataset_frame), "datasets")
        cameras = _status_items(_records(camera_frame), "cameras")
    except Exception as exc:  # status must remain usable when optional tools are absent
        summary = {"status": "warning", "text": f"状态读取部分失败: {exc}"}
        summary.update({"message": summary["text"], "camera_count": len(_camera_payload())})
        models = [_model_payload(spec) for spec in _production_model_specs()]
        datasets = [_dataset_payload(item) for item in load_dataset_manifest()["datasets"]]
        cameras = _camera_payload()
    return {"summary": _jsonable(summary), "models": models, "datasets": datasets, "cameras": cameras}


def _session_response(state: EnrollmentState, *, status: str | None = None, **extra: Any) -> dict[str, Any]:
    """Serialize a registration session without returning biometric originals."""

    review_actions_enabled = (
        state.options.mode == "assisted"
        and state.phase == "reviewing"
        and len(state.samples) == MAX_ENROLL_SAMPLES
    )
    return {
        "id": state.session_id,
        "session_id": state.session_id,
        "phase": state.phase,
        "status": status or state.guide,
        "guide": state.guide,
        "mode": state.options.mode,
        "review_actions_enabled": review_actions_enabled,
        "person_id": state.options.person_id,
        "display_name": state.options.display_name,
        "palm_side": state.options.palm_side,
        "sample_count": len(state.samples),
        "capture_ids": list(state.capture_ids),
        "samples": [
            {
                "id": sample.sample_id,
                "capture_id": sample.capture_id,
                "preview_url": sample.preview_url,
                "roi_url": sample.roi_url,
                "original_url": f"/api/captures/{sample.capture_id}/original",
                "quality": _jsonable(sample.quality),
                "geometry": _jsonable(sample.geometry),
            }
            for sample in state.samples
        ],
        "preview_url": state.last_preview_url,
        "roi_url": state.last_roi_url,
        "quality": _jsonable(state.last_quality),
        "geometry": _jsonable(state.last_geometry),
        "rejections": dict(state.rejection_counts),
        "last_error": state.last_error,
        "min_samples": MAX_ENROLL_SAMPLES,
        "max_samples": MAX_ENROLL_SAMPLES,
        "model_id": state.options.model_id,
        "backend": state.options.backend,
        "precision": state.options.precision,
        "roi_pipeline_id": ROI_PIPELINE_ID,
        "actual_resolution": state.actual_resolution,
        **extra,
    }


def _validate_enrollment_identity(options: EnrollmentOptions) -> tuple[str, str, str]:
    person_id = (options.person_id or "").strip()
    display_name = (options.display_name or "").strip() or person_id
    palm_side = (options.palm_side or "").strip().lower()
    if not person_id:
        raise HTTPException(status_code=422, detail="人员编号为必填稳定主键")
    if ":" in person_id:
        raise HTTPException(status_code=422, detail="人员编号不能包含 ':'")
    if palm_side not in {"left", "right"}:
        raise HTTPException(status_code=422, detail="掌侧必须为 left 或 right")
    if options.mode not in {"self_service", "assisted"}:
        raise HTTPException(status_code=422, detail="注册模式必须为 self_service 或 assisted")
    return person_id, display_name, palm_side


def _cleanup_enrollment_captures(state: EnrollmentState) -> None:
    capture_ids = list(state.capture_ids)
    if not capture_ids:
        return
    try:
        WORKBENCH.captures.cleanup_many(capture_ids)
    except Exception:
        logger.exception("unable to delete enrollment captures for session %s", state.session_id)


def _stop_auto_capture(state: EnrollmentState) -> None:
    state.stop_event.set()
    if state.device and state.resolution:
        try:
            width, height = _parse_resolution(state.resolution)
            WORKBENCH.close_cameras(
                device=state.device,
                width=width,
                height=height,
                session=state.session_id,
            )
        except Exception:
            logger.exception("unable to release enrollment camera for %s", state.session_id)


def _require_camera_not_reserved(session: str | None = None) -> None:
    """Keep browser preview/recognition from invalidating auto enrollment."""

    with _AUTO_ENROLL_LOCK:
        owner = _AUTO_ENROLL_SESSION_ID
    if owner and owner != session:
        raise HTTPException(status_code=409, detail="摄像头正在由自动注册会话独占")


def _wait_for_auto_capture(state: EnrollmentState, timeout: float = 3.0) -> None:
    worker = state.worker
    if worker is not None and worker.is_alive() and worker is not threading.current_thread():
        worker.join(timeout=timeout)


def _set_enrollment_failure(state: EnrollmentState, message: str) -> None:
    global _AUTO_ENROLL_SESSION_ID
    with _SESSION_LOCK:
        if state.phase in {"completed", "cancelled"}:
            return
        state.phase = "failed"
        state.guide = message
        state.last_error = message
        state.completed_at = time.time()
    _cleanup_enrollment_captures(state)
    with _AUTO_ENROLL_LOCK:
        if _AUTO_ENROLL_SESSION_ID == state.session_id:
            _AUTO_ENROLL_SESSION_ID = None


def _geometry_is_stable(previous: dict[str, Any], current: dict[str, Any]) -> bool:
    if not previous or not current:
        return False
    try:
        center_delta = max(
            abs(float(previous["center_x"]) - float(current["center_x"])),
            abs(float(previous["center_y"]) - float(current["center_y"])),
        )
        angle_delta = abs(float(previous["angle_deg"]) - float(current["angle_deg"]))
        old_side = max(float(previous["crop_side"]), 1.0)
        scale_delta = abs(float(current["crop_side"]) - old_side) / old_side
    except (KeyError, TypeError, ValueError):
        return False
    return center_delta <= 0.035 and angle_delta <= 8.0 and scale_delta <= 0.12


def _complete_enrollment(state: EnrollmentState) -> dict[str, Any]:
    """Encode five selected ROIs and atomically replace the stable palm."""

    person_id, display_name, palm_side = _validate_enrollment_identity(state.options)
    if len(state.samples) != MAX_ENROLL_SAMPLES:
        raise ValueError(f"需要恰好 {MAX_ENROLL_SAMPLES} 个合格样本")
    namespace = service_layer._template_namespace(
        state.options.model_id, state.options.backend, state.options.precision
    )
    stable_palm_id = WORKBENCH.store.palm_id(person_id, palm_side)
    new_capture_ids = {sample.capture_id for sample in state.samples}
    # Bind the new captures before publishing the embedding generation.  If a
    # capture write fails, the caller removes these files and the old template
    # generation remains untouched.
    for capture_id in new_capture_ids:
        WORKBENCH.captures.update_metadata(
            capture_id,
            model_id=state.options.model_id,
            backend=state.options.backend,
            precision=state.options.precision,
            palm_id=stable_palm_id,
            person_id=person_id,
            display_name=display_name,
            user_id=stable_palm_id,
            user_name=display_name,
            palm_side=palm_side,
            roi_pipeline_id=ROI_PIPELINE_ID,
            accepted=False,
            status="enrollment_pending_template",
        )
    with _workbench_job_lock():
        adapter = WORKBENCH.adapter(
            state.options.model_id, state.options.backend, state.options.precision
        )
        codes = [adapter.encode(sample.roi).code for sample in state.samples]
        replacement = WORKBENCH.store.replace_palm(
            namespace,
            codes,
            person_id=person_id,
            display_name=display_name,
            palm_side=palm_side,
            roi_pipeline_id=ROI_PIPELINE_ID,
            roi_config=state.roi_config or None,
        )
    for capture_id in new_capture_ids:
        try:
            WORKBENCH.captures.update_metadata(
                capture_id, accepted=True, status="enrolled"
            )
        except Exception:
            # The template generation is already committed.  Preserve the
            # sample rather than reporting a false failed registration.
            logger.exception("unable to finalize enrollment capture %s", capture_id)
    # The replacement has committed successfully.  Only now is it safe to
    # remove earlier images for the same stable palm.
    try:
        WORKBENCH.captures.purge_template(
            model_id=state.options.model_id,
            palm_id=replacement["palm_id"],
            keep_capture_ids=new_capture_ids,
            roi_pipeline_id=ROI_PIPELINE_ID,
        )
    except Exception:
        # Do not report a committed template as failed.  The next explicit
        # template deletion still retries physical image cleanup.
        logger.exception("unable to remove replaced enrollment captures")
    return replacement


def _auto_capture_worker(state: EnrollmentState) -> None:
    """Own one camera session and archive only the five selected frames."""

    global _AUTO_ENROLL_SESSION_ID
    assert state.device is not None and state.resolution is not None
    width, height = _parse_resolution(state.resolution)
    started = time.monotonic()
    stable_since: float | None = None
    previous_geometry: dict[str, Any] = {}
    previous_roi: np.ndarray | None = None
    first_quality_at: float | None = None
    progress_at = started + AUTO_ENROLL_COUNTDOWN_SECONDS
    try:
        with _SESSION_LOCK:
            state.phase = "camera_opening"
            state.guide = "正在打开摄像头"
        WORKBENCH.open_camera_session(state.device, width, height, state.session_id)
        while not state.stop_event.is_set():
            now = time.monotonic()
            elapsed = now - started
            if elapsed > AUTO_ENROLL_COUNTDOWN_SECONDS and now - progress_at >= AUTO_ENROLL_STABILITY_TIMEOUT_SECONDS:
                _set_enrollment_failure(state, "30 秒内没有新的合格样本，请调整采集框、距离和光照后重试")
                return
            with _SESSION_LOCK:
                if state.phase in {"cancelled", "completed", "failed", "reviewing"}:
                    return
                if elapsed < AUTO_ENROLL_COUNTDOWN_SECONDS:
                    countdown_elapsed = elapsed
                    state.phase = "countdown"
                    state.guide = f"请将掌心放入采集框，{max(1, int(AUTO_ENROLL_COUNTDOWN_SECONDS - countdown_elapsed + 0.99))} 秒后开始"
                else:
                    state.phase = "waiting_position"
                    state.guide = "请将掌心放入采集框并保持稳定"
            try:
                frame = WORKBENCH.capture_camera_frame(
                    state.device,
                    width,
                    height,
                    encode_jpeg=False,
                    session=state.session_id,
                )
            except (CameraError, ValueError) as exc:
                _set_enrollment_failure(state, f"摄像头异常：{exc}")
                return
            if elapsed < AUTO_ENROLL_COUNTDOWN_SECONDS:
                with _SESSION_LOCK:
                    state.last_preview_url = _frame_data_url(frame.rgb)
                    state.last_roi_url = None
                    state.last_quality = {}
                    state.last_geometry = {}
                time.sleep(AUTO_ENROLL_FRAME_INTERVAL_SECONDS)
                continue
            result, error = service_layer._extract(
                frame.rgb,
                False,
                roi_config=state.roi_config,
            )
            now = time.monotonic()
            state.actual_resolution = _image_resolution(frame.rgb)
            with _SESSION_LOCK:
                preview = result.preview if result.preview is not None else frame.rgb
                state.last_preview_url = _frame_data_url(preview)
                state.last_roi_url = _frame_data_url(result.roi, image_format=".png")
                state.last_quality = _jsonable(result.quality)
                state.last_geometry = _jsonable(result.geometry)
            if error or result.roi is None:
                reason = error or "ROI 提取失败"
                with _SESSION_LOCK:
                    state.rejection_counts[reason] = state.rejection_counts.get(reason, 0) + 1
                    state.last_error = reason
                    state.phase = "waiting_position"
                    state.guide = "请将掌心放入采集框，调整距离和光照"
                if first_quality_at is None and now - started >= AUTO_ENROLL_WAITING_HAND_TIMEOUT_SECONDS:
                    _set_enrollment_failure(state, "20 秒内未获得合格画面，请将掌心放入采集框后重试")
                    return
                stable_since = None
                previous_geometry = {}
                time.sleep(AUTO_ENROLL_FRAME_INTERVAL_SECONDS)
                continue

            if first_quality_at is None:
                first_quality_at = now
            current_geometry = result.geometry
            frame_delta = float(np.mean(np.abs(result.roi.astype(np.float32) - previous_roi.astype(np.float32)))) if previous_roi is not None else float("inf")
            previous_roi = result.roi.copy()
            with _SESSION_LOCK:
                state.last_error = None
                state.last_quality["frame_delta"] = frame_delta if np.isfinite(frame_delta) else None
            if not _geometry_is_stable(previous_geometry, current_geometry) or frame_delta > 5.0:
                stable_since = now
                previous_geometry = current_geometry
                with _SESSION_LOCK:
                    state.phase = "stabilizing"
                    state.guide = "将掌心放入采集框，保持画面稳定"
                time.sleep(AUTO_ENROLL_FRAME_INTERVAL_SECONDS)
                continue
            previous_geometry = current_geometry
            assert stable_since is not None
            if now - stable_since < AUTO_ENROLL_STABLE_SECONDS:
                with _SESSION_LOCK:
                    state.phase = "stabilizing"
                    state.guide = "保持不动，正在稳定定位"
                time.sleep(AUTO_ENROLL_FRAME_INTERVAL_SECONDS)
                continue
            if now - state.last_accepted_at < AUTO_ENROLL_SAMPLE_INTERVAL_SECONDS:
                with _SESSION_LOCK:
                    state.phase = "collecting"
                    state.guide = "轻微调整手掌，准备下一张样本"
                time.sleep(AUTO_ENROLL_FRAME_INTERVAL_SECONDS)
                continue
            if any(float(np.mean(np.abs(result.roi.astype(np.float32) - sample.roi.astype(np.float32)))) < 0.8 for sample in state.samples):
                with _SESSION_LOCK:
                    state.rejection_counts["near_duplicate"] = state.rejection_counts.get("near_duplicate", 0) + 1
                    state.phase = "collecting"
                    state.guide = "请轻微调整手掌后重采，避免重复样本"
                time.sleep(AUTO_ENROLL_FRAME_INTERVAL_SECONDS)
                continue
            capture_id = _safe_capture_save(
                original=frame.rgb,
                roi=result.roi,
                metadata={
                    "purpose": "enrollment",
                    "source": "camera_auto",
                    "model_id": state.options.model_id,
                    "backend": state.options.backend,
                    "precision": state.options.precision,
                    "device": state.device,
                    "template_namespace": service_layer._template_namespace(state.options.model_id, state.options.backend, state.options.precision),
                    "person_id": state.options.person_id,
                    "palm_side": state.options.palm_side,
                    "requested_resolution": state.resolution,
                    "actual_resolution": _image_resolution(frame.rgb),
                    "roi_mode": "fixed_guide",
                    "camera_physical_id": _camera_physical_id(state.device),
                    "roi_ok": True,
                    "quality": {**_jsonable(result.quality), "frame_delta": frame_delta},
                    "geometry": _jsonable(result.geometry),
                    "roi_pipeline_id": ROI_PIPELINE_ID,
                    "status": "enrollment_selected",
                    "enrollment_session_id": state.session_id,
                },
            )
            if not capture_id:
                _set_enrollment_failure(state, "无法保存入选采集样本")
                return
            sample = EnrollmentSample(
                sample_id=uuid4().hex,
                roi=result.roi.copy(),
                capture_id=capture_id,
                preview_url=_frame_data_url(result.preview),
                roi_url=_frame_data_url(result.roi, image_format=".png"),
                quality=_jsonable(result.quality),
                geometry=_jsonable(result.geometry),
            )
            discard_capture = False
            with _SESSION_LOCK:
                if state.phase in {"cancelled", "completed", "failed"} or state.stop_event.is_set():
                    discard_capture = True
                else:
                    state.samples.append(sample)
                    state.capture_ids.append(capture_id)
                    state.last_accepted_at = now
                    state.phase = "collecting"
                    state.guide = f"已采集 {len(state.samples)} / {MAX_ENROLL_SAMPLES}，请轻微调整手掌"
            if discard_capture:
                try:
                    WORKBENCH.captures.cleanup(capture_id)
                except Exception:
                    logger.exception("unable to remove cancelled enrollment capture %s", capture_id)
                return
            progress_at = now
            if len(state.samples) == MAX_ENROLL_SAMPLES:
                if state.options.mode == "assisted":
                    with _SESSION_LOCK:
                        state.phase = "reviewing"
                        state.guide = "已采集 5 张，请审阅样本后保存"
                    return
                with _SESSION_LOCK:
                    state.phase = "saving"
                    state.guide = "正在编码并保存掌纹模板"
                try:
                    replacement = _complete_enrollment(state)
                except Exception as exc:
                    logger.exception("self-service enrollment failed")
                    _set_enrollment_failure(state, _npu_error_detail(exc))
                    return
                with _SESSION_LOCK:
                    state.phase = "completed"
                    state.guide = f"已保存 {replacement['palm_id']} 的 5 个掌纹样本"
                    state.completed_at = time.time()
                return
            time.sleep(AUTO_ENROLL_FRAME_INTERVAL_SECONDS)
    except Exception as exc:
        # A worker failure must become observable to the polling UI rather
        # than leaving the session indefinitely in its previous phase.
        logger.exception("automatic enrollment worker failed for %s", state.session_id)
        _set_enrollment_failure(state, f"自动采集异常：{_npu_error_detail(exc)}")
    finally:
        _stop_auto_capture(state)
        with _EVALUATION_LOCK:
            _BACKGROUND_WORKERS.discard(threading.current_thread())
        with _AUTO_ENROLL_LOCK:
            if _AUTO_ENROLL_SESSION_ID == state.session_id:
                _AUTO_ENROLL_SESSION_ID = None


def _evaluation_response(state: EvaluationState) -> dict[str, Any]:
    result = state.result or {}
    accuracy = result.get("accuracy", result)
    performance = result.get("performance", {})
    metrics = accuracy.get("metrics", {}) if isinstance(accuracy, dict) else {}
    return {
        "id": state.evaluation_id,
        "evaluation_id": state.evaluation_id,
        "status": state.status,
        "progress": float(state.progress),
        "message": state.error or state.message,
        "dataset_id": state.request.dataset_id,
        "model_id": state.request.model_id,
        "backend": state.request.backend,
        "precision": state.request.precision,
        "timeout_seconds": state.request.timeout_seconds,
        "metrics": _jsonable(metrics),
        "performance": _jsonable(performance),
        "report_url": f"/api/evaluations/{state.evaluation_id}/report" if state.reports else None,
        "reports": state.reports,
        "started_at": state.started_at,
        "finished_at": state.finished_at,
    }


def _candidate_inventory() -> list[dict[str, Any]]:
    """Expose only candidates selected for the current release UI.

    The full 28-item research audit remains available to offline tooling and
    its reports.  The serving UI intentionally hides candidates that have no
    reproducible NPU runtime path or are not palmprint embedding models.
    """

    try:
        candidates = WORKBENCH.registry.runtime_candidates()
    except (OSError, ValueError, json.JSONDecodeError, AttributeError):
        candidates = []
    runtime = {spec.id: spec for spec in _runtime_specs()}
    campaign_status: dict[str, dict[str, Any]] = {}
    campaign_index = REPORT_DIR / "candidates" / "index.json"
    try:
        campaign_payload = json.loads(campaign_index.read_text(encoding="utf-8"))
        campaign_items = campaign_payload.get("items", []) if isinstance(campaign_payload, dict) else []
        if isinstance(campaign_items, list):
            campaign_status = {
                str(item["candidate_id"]): item
                for item in campaign_items
                if isinstance(item, dict) and isinstance(item.get("candidate_id"), str)
            }
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        # The read-only inventory remains available before the first campaign.
        campaign_status = {}

    def diagnostic_status(candidate_id: str) -> dict[str, Any]:
        """Read the latest board trace without conflating it with ACL smoke."""

        system_dir = REPORT_DIR / "system"
        matches = sorted(system_dir.glob(f"{candidate_id}_*.json"), reverse=True)
        for path in matches:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
            if isinstance(value, dict) and value.get("status"):
                delta = value.get("fault_delta", {})
                categories = delta.get("blocking_categories", []) if isinstance(delta, dict) else []
                return {
                    "status": value.get("status"),
                    "blocking_categories": list(categories) if isinstance(categories, list) else [],
                    "report": str(path),
                }
        return {"status": "not_recorded", "blocking_categories": [], "report": None}

    rows: list[dict[str, Any]] = []
    for candidate in candidates:
        manual_candidate = bool(candidate.raw.get("manual_test_enabled", False))
        admission = (
            WORKBENCH.registry.manual_test_candidate_admission(
                candidate.id, verify_assets=False
            )
            if manual_candidate and RELEASE_PROFILE == MANUAL_TEST_PROFILE
            else WORKBENCH.registry.candidate_admission(
                candidate.id, verify_assets=False
            )
        )
        raw = dict(candidate.raw)
        raw_weights = raw.get("weights", {})
        weights = dict(raw_weights) if isinstance(raw_weights, dict) else {}
        source_info = raw.get("source", {})
        if not isinstance(source_info, dict):
            source_info = {"url": getattr(candidate, "source", ""), "revision": getattr(candidate, "revision", "")}
        license_info = raw.get("license", {})
        if not isinstance(license_info, dict):
            license_info = {"spdx": getattr(candidate, "license", "NOASSERTION")}
        conversion = raw.get("conversion", {})
        if not isinstance(conversion, dict):
            conversion = {}
        artifacts = weights.get("artifacts", [])
        artifact_hash = next(
            (item.get("sha256") for item in artifacts if isinstance(item, dict) and item.get("sha256")),
            None,
        )
        spec = runtime.get(candidate.id)
        audit_spec = None
        if spec is None:
            try:
                audit_spec = WORKBENCH.registry.offline_candidate_embedding_spec(candidate.id)
            except (KeyError, ValueError):
                audit_spec = None
        # Keep the manifest's declared lifecycle state, but surface a separate
        # board observation when an OM has passed a bounded smoke test.  This
        # must not promote the candidate into the production registry.
        npu_status = conversion.get("board_npu_status", candidate.npu_status)
        export_status = conversion.get("onnx_status", raw.get("export_status", "pending"))
        usable = False
        eligibility_reason = raw.get("eligibility_reason")
        production_listed = False
        production_enabled = bool(raw.get("production_enabled", False))
        admission_status = "pending"
        admission_reasons = list(admission.reasons)
        if spec is not None and spec.kind == "embedding":
            model_payload = _model_payload(spec)
            npu_status = "ready" if model_payload.get("usable_for_recognition") else model_payload.get("status", {}).get("npu_mixed_fp16", npu_status)
            export_status = "ready" if any(value == "ready" for value in model_payload.get("status", {}).values()) else export_status
            usable = bool(model_payload.get("usable_for_recognition"))
            eligibility_reason = model_payload.get("eligibility_reason") or eligibility_reason
            production_listed = True
            production_enabled = bool(raw.get("production_enabled", False))
            manual_pending = bool(spec.raw.get("manual_test_pending", False))
            admission_status = (
                "manual_test_pending" if manual_pending and usable
                else "admitted" if usable else "blocked"
            )
            admission_reasons = [] if usable else [
                str(eligibility_reason or "生产模型资产校验未通过")
            ]
        elif audit_spec is not None:
            # An OM file may exist for an offline candidate before its ACL,
            # numerical, and dataset gates are complete. Surface that asset
            # fact independently without advertising the model as production
            # recognition-capable.
            om_path = audit_spec.om_path("mixed_fp16")
            if om_path and om_path.is_file():
                conversion.setdefault("om_status", "om_file_present")
            eligibility_reason = admission.reason or eligibility_reason
        elif admission.known:
            eligibility_reason = admission.reason or eligibility_reason
        campaign = campaign_status.get(candidate.id, {})
        diagnostic = diagnostic_status(candidate.id)
        rows.append({
            "id": candidate.id,
            "candidate_id": candidate.id,
            "display_name": candidate.display_name,
            "family": candidate.family,
            "task_type": _candidate_task_type(candidate),
            "task": candidate.task,
            "modality": candidate.modality,
            "comparison_scope": candidate.comparison_scope,
            "source": source_info.get("url"),
            "revision": source_info.get("revision"),
            "license": license_info.get("spdx", "NOASSERTION"),
            "weight_status": weights.get("availability", "unknown"),
            "weight_sha256": artifact_hash,
            "export_status": export_status,
            "om_status": conversion.get("om_status", npu_status if spec is not None else raw.get("om_status", "not-tested")),
            "board_npu_status": conversion.get("board_npu_status", raw.get("board_npu_status", npu_status)),
            "onnx_sha256": conversion.get("onnx_sha256"),
            "onnx_bytes": conversion.get("onnx_bytes"),
            "conversion": conversion,
            "npu_status": npu_status,
            "metrics_status": raw.get("metrics_status", "pending"),
            "training_domains": raw.get("training_domains", [raw.get("training_domain")] if raw.get("training_domain") else []),
            "usable_for_recognition": usable,
            "production_listed": production_listed,
            "production_enabled": production_enabled,
            "manual_test_pending": bool(
                raw.get("manual_test_pending", False)
                or (spec is not None and spec.raw.get("manual_test_pending", False))
            ),
            "admission_status": admission_status if spec is not None else ("admitted" if admission.admitted else "pending"),
            "admission_reasons": admission_reasons,
            "reproducible": bool(candidate.reproducible),
            "reproducibility_reason": candidate.reproducibility_reason,
            "eligibility_reason": eligibility_reason or ("未完成 NPU 准入" if not usable else None),
            "metrics": raw.get("metrics", {}),
            "performance": raw.get("performance", {}),
            "na_reasons": raw.get("na_reasons", {}),
            "campaign_status": campaign.get("status", "not_started"),
            "campaign_reasons": campaign.get("reasons", []),
            "campaign_generated_at": campaign.get("generated_at"),
            "diagnostic_status": diagnostic["status"],
            "diagnostic_blocking_categories": diagnostic["blocking_categories"],
        })
    return rows


def _production_model_specs() -> list[Any]:
    """Return only NPU-admitted embedding models for the production API."""

    result: list[Any] = []
    for spec in _runtime_specs():
        if spec.kind != "embedding":
            continue
        if _model_payload(spec).get("usable_for_recognition", False):
            result.append(spec)
    return result


def _comparison_response(state: ComparisonState) -> dict[str, Any]:
    return {
        "id": state.comparison_id,
        "comparison_id": state.comparison_id,
        "status": state.status,
        "progress": float(state.progress),
        "message": state.error or state.message,
        "dataset_id": state.request.dataset_id,
        "rows": _jsonable(state.rows),
        "report_url": f"/api/comparisons/{state.comparison_id}/report" if state.report_path else None,
        "started_at": state.started_at,
        "finished_at": state.finished_at,
    }


def _na_row(candidate: dict[str, Any], status: str, reason: str) -> dict[str, Any]:
    return {
        "candidate_id": candidate.get("id"),
        "display_name": candidate.get("display_name", candidate.get("id")),
        "task_type": candidate.get("task_type", "audit"),
        "modality": candidate.get("modality"),
        "status": status,
        "npu_status": candidate.get("npu_status"),
        "license": candidate.get("license"),
        "weight_status": candidate.get("weight_status"),
        "export_status": candidate.get("export_status"),
        "om_status": candidate.get("om_status"),
        "board_npu_status": candidate.get("board_npu_status", candidate.get("npu_status")),
        "source": candidate.get("source"),
        "metrics": {"eer": "N/A", "auc": "N/A", "rank1": "N/A"},
        "performance": {"pipeline_p50_ms": "N/A"},
        "na_reasons": {"identity_metrics": reason},
        "error": reason if status == "failed" else None,
    }


def _run_comparison(state: ComparisonState) -> None:
    # Candidate comparison is an offline/research operation.  Keeping a
    # background endpoint for it made it possible to instantiate an
    # unadmitted OM runner from the production process.  The audit inventory
    # remains available through GET /api/candidates; execution belongs to the
    # explicit tools.offline CLI.
    with _EVALUATION_LOCK:
        state.status = "blocked"
        state.error = "候选比较仅可通过 tools.offline 执行；生产 API 只允许已准入模型"
        state.message = "生产 API 未启动候选比较"
        state.finished_at = time.time()


def _run_evaluation(state: EvaluationState) -> None:
    with _EVALUATION_LOCK:
        state.status = "running"
        state.started_at = time.time()
        state.message = "准备评测"

    deadline = time.monotonic() + state.request.timeout_seconds

    def check_deadline() -> None:
        if time.monotonic() > deadline:
            raise _JobTimeout(
                f"评测超过 timeout_seconds={state.request.timeout_seconds}，未生成报告"
            )

    def progress(value: float, message: str) -> None:
        check_deadline()
        with _EVALUATION_LOCK:
            state.progress = max(0.0, min(0.9, float(value) * 0.9))
            state.message = str(message)

    try:
        request = state.request
        # Keep the production import boundary explicit.  The service facade
        # revalidates NPU admission before constructing an OM runner.
        from ..services.evaluation import (
            evaluate_dataset,
            performance_benchmark,
            write_report,
        )
        # Keep the asynchronous worker subject to the same production
        # boundary as the HTTP enqueue route.  This prevents a mutated or
        # programmatically-created state from silently running CPU inference.
        _validate_options(RecognitionOptions(**_model_dict(request)))
        spectrum = request.spectrum.upper()
        # The touch UI uses descriptive range labels.  The validated PolyU
        # parser currently has one concrete spectrum per run; map those labels
        # to its default B channel while preserving the original request in
        # the job metadata.
        if spectrum not in {"B", "G", "I", "R"}:
            spectrum = "B"
        with _workbench_job_lock():
            check_deadline()
            accuracy = evaluate_dataset(
                request.model_id,
                request.backend,
                request.precision,
                request.dataset_id,
                spectrum,
                request.threads,
                request.max_identities or None,
                progress=progress,
            )
            check_deadline()
            sample_path = records(request.dataset_id, spectrum)[0].path
            # The HTTP workbench must remain interactive while a user waits
            # for a report.  Full 5x500-loop performance runs belong to the
            # offline benchmark command; the UI records a bounded smoke
            # measurement and labels it as such in the report payload.
            performance = performance_benchmark(
                request.model_id,
                request.backend,
                request.precision,
                sample_path,
                threads=request.threads,
                warmup=5,
                loops=20,
                repeats=1,
            )
            check_deadline()
            performance["measurement_mode"] = "ui_smoke_5_20_1"
        combined = {"accuracy": accuracy, "performance": performance}
        check_deadline()
        paths = write_report(combined, f"api_{state.evaluation_id}")
        with _EVALUATION_LOCK:
            state.result = combined
            state.reports = paths
            state.status = "completed"
            state.progress = 1.0
            state.message = "评测完成"
    except _ServiceStopping as exc:
        with _EVALUATION_LOCK:
            state.status = "cancelled"
            state.error = str(exc)
            state.message = "服务正在关闭，评测未启动"
            state.finished_at = time.time()
    except _JobTimeout as exc:
        with _EVALUATION_LOCK:
            state.status = "timed_out"
            state.error = str(exc)
            state.message = "评测超时，未生成报告"
            state.finished_at = time.time()
    except Exception as exc:  # background failures are reported through GET
        with _EVALUATION_LOCK:
            state.status = "failed"
            state.error = str(exc)
            state.message = "评测失败"
            state.finished_at = time.time()
    else:
        with _EVALUATION_LOCK:
            state.finished_at = time.time()


@asynccontextmanager
async def _workbench_lifespan(_: FastAPI):
    """Close cached ACL resources before Uvicorn tears down the process.

    A benchmark or recognition adapter owns an ACL context, model descriptor,
    datasets and device buffers.  Uvicorn shutdown must wait for the shared
    execution lock and release those objects before any process-level runtime
    reset/finalize is attempted by :meth:`Workbench.close`.
    """

    global _LAST_SERVICE_SHUTDOWN, _SERVICE_STOPPING
    with _WORKER_GATE:
        with _EVALUATION_LOCK:
            _SERVICE_STOPPING = False
    try:
        yield
    finally:
        started = time.time()
        with _SESSION_LOCK:
            enrollment_sessions = list(_SESSIONS.values())
            for session in enrollment_sessions:
                session.phase = "cancelled"
                session.guide = "服务关闭，注册会话已取消"
        for session in enrollment_sessions:
            _stop_auto_capture(session)
        with _WORKER_GATE:
            with _EVALUATION_LOCK:
                _SERVICE_STOPPING = True
                for state in list(_EVALUATIONS.values()) + list(_COMPARISONS.values()):
                    if state.status == "queued":
                        state.status = "cancelled"
                        state.message = "服务关闭，任务未启动"
                        state.finished_at = time.time()
                workers = list(_BACKGROUND_WORKERS)
        # Do not close ACL while a worker can still enter Workbench.  A
        # bounded wait avoids hanging an ASGI shutdown forever; on timeout we
        # deliberately leave the runtime untouched and expose the blocker.
        deadline = time.monotonic() + 60.0
        for worker in workers:
            remaining = max(0.0, deadline - time.monotonic())
            worker.join(timeout=remaining)
        alive = [worker.name for worker in workers if worker.is_alive()]
        with _LIFECYCLE_LOCK:
            if alive:
                _LAST_SERVICE_SHUTDOWN = {
                    "timestamp": started,
                    "ok": False,
                    "status": "background_workers_still_running",
                    "workers": alive,
                    "runtime": acl_runtime_status(),
                }
                return
            try:
                diagnostics = await run_in_threadpool(WORKBENCH.close)
                _LAST_SERVICE_SHUTDOWN = {
                    "timestamp": started,
                    "ok": bool(diagnostics.get("ok", False)),
                    "diagnostics": diagnostics,
                }
            except BaseException as exc:
                # Do not turn a service shutdown into a second uncaught
                # exception. The structured failure remains observable from
                # process logs and the next health response while alive.
                _LAST_SERVICE_SHUTDOWN = {
                    "timestamp": started,
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "runtime": acl_runtime_status(),
                }


def create_app(*, static_dir: Path | None = None) -> FastAPI:
    """Create the HTTP application; ``static_dir`` is injectable for tests."""

    static_root = Path(static_dir or STATIC_DIR).resolve()
    service = FastAPI(
        title="Palmprint Recognition Workbench",
        version=APP_VERSION,
        lifespan=_workbench_lifespan,
    )

    @service.get("/api/health")
    async def health() -> dict[str, Any]:
        transport_ready = True
        bundle_ok = (static_root / "index.html").is_file()
        npu_runtime = _npu_runtime_health()
        runtime_importable = npu_runtime["status"] == "importable"
        model_payloads = [_model_payload(spec) for spec in _runtime_specs()]
        admitted_model_ids = [
            item["id"]
            for item in model_payloads
            if item.get("usable_for_recognition") is True
        ]
        model_ready = bool(admitted_model_ids)
        template_store = WORKBENCH.store.readiness()
        runtime_snapshot = acl_runtime_status()
        inference_smoke = {
            "status": "not_run",
            "passed": None,
            "detail": "健康检查不会初始化 NPU；请通过板端合成 ROI 烟雾测试验证推理",
        }
        template_store_ready = bool(template_store.get("ready"))
        healthy = (
            transport_ready
            and bundle_ok
            and runtime_importable
            and model_ready
            and template_store_ready
        )
        return {
            "status": "ok" if healthy else "warning",
            "message": (
                "Palmprint API ready"
                if healthy
                else npu_runtime["detail"] if npu_runtime["status"] != "importable"
                else "Template storage requires an external key or migration"
                if not template_store_ready
                else "Model registry or frontend bundle unavailable"
            ),
            "app_version": APP_VERSION,
            "release_id": RELEASE_ID,
            "release_profile": RELEASE_PROFILE,
            "transport_ready": transport_ready,
            "runtime_importable": runtime_importable,
            "model_ready": model_ready,
            "template_store_ready": bool(template_store.get("ready")),
            "template_store": template_store,
            "inference_smoke": inference_smoke,
            "admitted_model_ids": admitted_model_ids,
            "readiness": {
                "transport": transport_ready,
                "frontend_bundle": bundle_ok,
                "runtime_importable": runtime_importable,
                "model_assets": model_ready,
                "template_store": bool(template_store.get("ready")),
            },
            "frontend_bundle": bundle_ok,
            "npu_runtime": npu_runtime,
            "model_assets": {
                item["id"]: item.get("asset_status", {}) for item in model_payloads
            },
            "acl_lifecycle": {
                "runtime": runtime_snapshot,
                "last_service_shutdown": _LAST_SERVICE_SHUTDOWN,
            },
        }

    @service.get("/api/bootstrap")
    async def bootstrap() -> dict[str, Any]:
        models = [_model_payload(spec) for spec in _production_model_specs()]
        default_model = models[0] if models else None
        default_threshold = (
            float(default_model["threshold"])
            if default_model is not None
            and isinstance(default_model.get("threshold"), (int, float))
            else DEFAULT_THRESHOLD
        )
        return {
            "app_version": APP_VERSION,
            "release_id": RELEASE_ID,
            "release_profile": RELEASE_PROFILE,
            "models": models,
            "manual_test_pending_model_ids": [
                item["id"] for item in models if item.get("manual_test_pending")
            ],
            "datasets": [_dataset_payload(item) for item in load_dataset_manifest()["datasets"]],
            "defaults": {
                "model_id": default_model["id"] if default_model is not None else "",
                "backend": "npu",
                "precision": PRODUCTION_PRECISION,
                "threshold": default_threshold,
                "camera_resolution": f"{CAMERA_DEFAULT_WIDTH}x{CAMERA_DEFAULT_HEIGHT}",
            },
        }

    @service.get("/api/candidates")
    async def candidates() -> dict[str, Any]:
        items = _candidate_inventory()
        active_ids = set(WORKBENCH.registry.runtime_candidate_ids())
        archived_ids = sorted(
            candidate.id
            for candidate in WORKBENCH.registry.candidates()
            if candidate.id not in active_ids
        )
        return {
            "items": items,
            "active_candidate_ids": sorted(active_ids),
            "archived_candidate_ids": archived_ids,
            "archived_count": len(archived_ids),
        }

    @service.get("/api/candidate-runtimes")
    async def candidate_runtimes() -> dict[str, Any]:
        """List board-ready heterogeneous candidate experiment adapters.

        This is intentionally separate from ``/api/bootstrap``.  A candidate
        classifier, ROI graph, vein graph, or pair comparator is callable from
        the experiment page, but it is not a template/embedding model.
        """

        rows: list[dict[str, Any]] = []
        for spec in candidate_specs():
            row = {
                "id": spec["id"],
                "display_name": spec["display_name"],
                "task_type": spec["task_type"],
                "modality": spec["modality"],
                "input_kind": spec["input_kind"],
                "input_shape": spec["input_shape"],
                "output_kind": spec["output_kind"],
                "om_path": spec["om_path"],
                "bytes": spec["bytes"],
                "sha256": spec["sha256"],
                "available": False,
                "reason": None,
            }
            try:
                verify_candidate_asset(spec)
            except (FileNotFoundError, OSError, ValueError) as exc:
                row["reason"] = str(exc)
            else:
                row["available"] = True
            rows.append(row)
        return {"items": rows, "production_registry_modified": False}

    @service.post("/api/candidate-runs")
    async def candidate_run(request: Request) -> dict[str, Any]:
        """Run one board-ready candidate using its task-specific contract."""

        try:
            form = await request.form()
        except Exception as exc:
            raise HTTPException(status_code=415, detail=f"需要 multipart/form-data 图像上传: {exc}") from exc
        candidate_id = str(form.get("candidate_id", "")).strip()
        if not candidate_id:
            raise HTTPException(status_code=422, detail="缺少 candidate_id 字段")
        upload = form.get("image")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(status_code=422, detail="缺少 image 文件字段")
        reference_upload = form.get("reference_image")
        try:
            image = _decode_image_bytes(await upload.read())
            reference = (
                _decode_image_bytes(await reference_upload.read())
                if reference_upload is not None and hasattr(reference_upload, "read")
                else None
            )
        except HTTPException:
            raise
        try:
            candidate_spec(candidate_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"未知候选实验模型: {candidate_id}") from exc
        spec = candidate_spec(candidate_id)
        if spec.get("input_kind") == "rgb_pair" and reference is None:
            raise HTTPException(status_code=422, detail="双输入候选需要 reference_image 文件字段")
        try:
            with WORKBENCH.execution_lock:
                result = await run_in_threadpool(run_candidate, candidate_id, image, reference)
        except (FileNotFoundError, OSError, ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=503, detail=f"候选模型运行失败：{exc}") from exc
        capture_id = _safe_capture_save(
            original=image,
            roi=None,
            metadata={
                "purpose": "candidate_experiment",
                "source": "upload",
                "model_id": result["candidate_id"],
                "backend": result["backend"],
                "precision": result["precision"],
                "actual_resolution": _image_resolution(image),
                "roi_ok": False,
                "status": "candidate_runtime_ok",
                "timing": {
                    "model ms": result["inference_ms"],
                    "total ms": result["total_ms"],
                },
                "candidate_task_type": result["task_type"],
                "candidate_output_kind": spec.get("output_kind"),
                "reference_image_supplied": reference is not None,
            },
        )
        response = {"status": "ok", "contains_biometric_data": True, **result}
        if capture_id:
            response["capture_id"] = capture_id
        return response

    @service.get("/api/cameras")
    async def cameras() -> dict[str, Any]:
        return {"items": _camera_payload()}

    @service.get("/api/cameras/{device:path}/roi-guide")
    async def camera_roi_guide(
        device: str,
        resolution: str = Query(f"{CAMERA_DEFAULT_WIDTH}x{CAMERA_DEFAULT_HEIGHT}"),
    ) -> dict[str, Any]:
        width, height = _parse_resolution(resolution)
        return {
            "roi_pipeline_id": ROI_PIPELINE_ID,
            "resolution": f"{width}x{height}",
            "physical_id": _camera_physical_id(device),
            "config": _fixed_roi_config(device, f"{width}x{height}"),
            "message": "系统不会自动寻找手掌，请将掌心放入固定采集框",
        }

    @service.put("/api/cameras/{device:path}/roi-guide")
    async def save_camera_roi_guide(
        device: str,
        calibration: FixedRoiCalibration,
        resolution: str = Query(f"{CAMERA_DEFAULT_WIDTH}x{CAMERA_DEFAULT_HEIGHT}"),
    ) -> dict[str, Any]:
        width, height = _parse_resolution(resolution)
        return _save_fixed_roi_config(device, f"{width}x{height}", calibration)

    @service.get("/api/cameras/{device:path}/frame")
    async def camera_frame(
        device: str,
        resolution: str = Query(f"{CAMERA_DEFAULT_WIDTH}x{CAMERA_DEFAULT_HEIGHT}"),
        session: Optional[str] = Query(None, min_length=1, max_length=160),
        preview: bool = Query(False),
        max_width: int = Query(960, ge=320, le=1920),
        quality: int = Query(72, ge=40, le=95),
    ) -> Response:
        _require_camera_not_reserved(session)
        width, height = _parse_resolution(resolution)
        try:
            # CameraCapture.read() is blocking on V4L2.  Keep it off the
            # asyncio event loop so a slow USB frame cannot freeze the rest
            # of the touch UI or queue browser requests behind it.
            frame = await run_in_threadpool(
                partial(
                    WORKBENCH.capture_camera_frame,
                    device,
                    width,
                    height,
                    encode_jpeg=not preview,
                    session=session,
                )
            )
        except (CameraError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=f"摄像头错误: {exc}") from exc
        payload = frame.jpeg
        actual_height, actual_width = frame.rgb.shape[:2]
        actual_resolution = f"{int(actual_width)}x{int(actual_height)}"
        preview_width, preview_height = actual_width, actual_height
        if preview:
            try:
                payload, (preview_width, preview_height) = _preview_jpeg(
                    frame, max_width=max_width, quality=quality
                )
            except CameraError as exc:
                raise HTTPException(status_code=503, detail=f"摄像头预览错误: {exc}") from exc
        if not payload:
            raise HTTPException(status_code=503, detail="摄像头未生成 JPEG 帧")
        return Response(
            content=payload,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-store, max-age=0",
                "X-Capture-Timestamp": str(frame.timestamp_ns),
                "X-Capture-Resolution": actual_resolution,
                "X-Requested-Resolution": f"{width}x{height}",
                "X-Preview-Resolution": f"{preview_width}x{preview_height}",
                "X-Capture-Format": getattr(frame, "frame_format", "unknown"),
                "X-Camera-Physical-Id": getattr(frame, "physical_id", ""),
            },
        )

    @service.post("/api/cameras/{device:path}/open")
    async def open_camera(
        device: str,
        resolution: str = Query(f"{CAMERA_DEFAULT_WIDTH}x{CAMERA_DEFAULT_HEIGHT}"),
        session: str = Query(..., min_length=1, max_length=160),
    ) -> dict[str, Any]:
        """Start a preview session before any frame request is dispatched.

        Registering first closes the old handle under the camera lock.  Frame
        and close requests carrying an older token are then harmless no-ops,
        which matters when a browser abort races with a V4L2 read.
        """

        _require_camera_not_reserved(session)
        width, height = _parse_resolution(resolution)
        try:
            await run_in_threadpool(
                WORKBENCH.open_camera_session,
                device,
                width,
                height,
                session,
            )
        except (CameraError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=f"摄像头错误: {exc}") from exc
        return {"ok": True, "status": "摄像头会话已打开", "session": session}

    @service.post("/api/recognitions")
    async def recognize_upload(request: Request) -> dict[str, Any]:
        form = await request.form()
        options = RecognitionOptions(
            model_id=str(form.get("model_id", "ccnet")),
            backend=str(form.get("backend", "npu")),
            precision=str(form.get("precision", "mixed_fp16")),
            threshold=_form_optional_float(form.get("threshold")),
            assume_roi=_parse_bool(form.get("assume_roi")),
            top_k=_form_int(form.get("top_k"), 5),
        )
        # Reject unsupported production backends before decoding or touching
        # any model/image resource.  CPU remains available only to the
        # offline benchmark CLI.
        _validate_options(options)
        image = await _image_from_request(request)
        try:
            return _recognition_payload(image, options, source="upload")
        except HTTPException:
            raise
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=_npu_error_detail(exc)) from exc

    @service.post("/api/cameras/{device:path}/recognitions")
    async def recognize_camera(device: str, options: CameraRecognitionOptions) -> dict[str, Any]:
        # A V4L2 frame is always a full camera frame, never a pre-cropped
        # 128 x 128 palm ROI.  Enforce this at the API boundary so a stale UI
        # or a direct client request cannot shrink the complete frame into the
        # embedding input.
        options.assume_roi = False
        _validate_options(options)
        _require_camera_not_reserved()
        width, height = _parse_resolution(options.resolution)
        try:
            frame = await run_in_threadpool(
                partial(
                    WORKBENCH.capture_camera_frame,
                    device,
                    width,
                    height,
                    encode_jpeg=False,
                )
            )
        except (CameraError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=f"摄像头错误: {exc}") from exc
        try:
            return await run_in_threadpool(
                partial(
                    _recognition_payload,
                    frame.rgb,
                    options,
                    source="camera",
                    device=device,
                    requested_resolution=options.resolution,
                    camera_resolution=_image_resolution(frame.rgb),
                    roi_config=_fixed_roi_config(device, options.resolution),
                )
            )
        except HTTPException:
            raise
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=503, detail=_npu_error_detail(exc)) from exc

    @service.post("/api/cameras/{device:path}/close")
    async def close_camera(
        device: str,
        resolution: Optional[str] = Query(None),
        session: Optional[str] = Query(None, min_length=1, max_length=160),
    ) -> dict[str, Any]:
        # Preview cleanup carries the old resolution so an asynchronous close
        # cannot tear down a newly selected resolution.  A call without a
        # resolution still releases every handle for that device.
        _require_camera_not_reserved(session)
        if resolution:
            width, height = _parse_resolution(resolution)
            WORKBENCH.close_cameras(device=device, width=width, height=height, session=session)
        else:
            WORKBENCH.close_cameras(device=device, session=session)
        return {"ok": True, "status": "摄像头已关闭"}

    @service.delete("/api/cameras/{device:path}")
    async def close_camera_delete(
        device: str,
        resolution: Optional[str] = Query(None),
        session: Optional[str] = Query(None, min_length=1, max_length=160),
    ) -> dict[str, Any]:
        return await close_camera(device, resolution, session)

    @service.post("/api/enrollment-sessions")
    async def create_enrollment(options: EnrollmentOptions) -> dict[str, Any]:
        # Enrollment sessions capture from a camera.  Uploaded pre-cropped
        # ROI samples remain supported by the assisted sample endpoint, but
        # the automatic camera workflow must always run the ROI locator.
        options.assume_roi = False
        recognition_options = RecognitionOptions(**_model_dict(options))
        _validate_options(recognition_options)
        if options.person_id:
            raise HTTPException(status_code=422, detail="新注册不能自定义人员编号")
        if options.continue_session_id and options.reenroll_palm_id:
            raise HTTPException(status_code=422, detail="只能选择一种续登记方式")
        if options.continue_session_id:
            with _SESSION_LOCK:
                previous = _SESSIONS.get(options.continue_session_id)
            if previous is None or previous.phase != "completed":
                raise HTTPException(status_code=409, detail="只能续登记已完成会话的人员")
            options.person_id = previous.options.person_id
        elif options.reenroll_palm_id:
            namespace = service_layer._template_namespace(options.model_id, options.backend, options.precision)
            palms = WORKBENCH.store.users(namespace, roi_pipeline_id=ROI_PIPELINE_ID)
            previous_palm = next((item for item in palms if item.get("palm_id") == options.reenroll_palm_id), None)
            if previous_palm is None or previous_palm["palm_side"] != options.palm_side:
                raise HTTPException(status_code=404, detail="待重注册的掌纹模板不存在")
            options.person_id = previous_palm["person_id"]
        else:
            options.person_id = WORKBENCH.store.allocate_person_id()
        person_id, display_name, palm_side = _validate_enrollment_identity(options)
        options.person_id = person_id
        options.display_name = display_name
        options.palm_side = palm_side
        session = EnrollmentState(uuid4().hex, options)
        if options.mode == "assisted":
            session.guide = "管理员审阅模式：采集 5 张合格样本后审阅、剔除或确认保存"
        with _SESSION_LOCK:
            _SESSIONS[session.session_id] = session
        return _session_response(session, status="注册会话已创建，请开始自动采集")

    def get_session(session_id: str) -> EnrollmentState:
        with _SESSION_LOCK:
            session = _SESSIONS.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="注册会话不存在或已结束")
        if time.time() - session.created_at > 30 * 60:
            with _SESSION_LOCK:
                _SESSIONS.pop(session_id, None)
            _stop_auto_capture(session)
            _wait_for_auto_capture(session)
            _cleanup_enrollment_captures(session)
            raise HTTPException(status_code=410, detail="注册会话已过期")
        return session

    @service.get("/api/enrollment-sessions/{session_id}")
    async def enrollment_status(session_id: str) -> dict[str, Any]:
        session = get_session(session_id)
        return _session_response(session)

    @service.post("/api/enrollment-sessions/{session_id}/auto-capture")
    async def start_auto_capture(
        session_id: str, request: EnrollmentAutoCaptureOptions
    ) -> dict[str, Any]:
        global _AUTO_ENROLL_SESSION_ID
        session = get_session(session_id)
        _validate_enrollment_identity(session.options)
        width, height = _parse_resolution(request.resolution)
        del width, height  # validation happens before reserving the camera session
        with _AUTO_ENROLL_LOCK:
            if _AUTO_ENROLL_SESSION_ID and _AUTO_ENROLL_SESSION_ID != session_id:
                raise HTTPException(status_code=409, detail="已有注册会话正在独占摄像头")
            if session.worker is not None and session.worker.is_alive():
                return _session_response(session, status="自动采集正在运行")
            if session.phase in {"completed", "cancelled"}:
                raise HTTPException(status_code=409, detail="当前会话已结束，请创建新的注册会话")
            if session.phase == "failed":
                raise HTTPException(status_code=409, detail="当前会话已失败，请创建新的注册会话")
            session.device = request.device
            session.resolution = request.resolution
            session.roi_config = _fixed_roi_config(request.device, request.resolution)
            session.stop_event.clear()
            session.started_at = time.time()
            session.phase = "camera_opening"
            session.guide = "正在打开摄像头并准备固定采集框"
            session.last_error = None
            _AUTO_ENROLL_SESSION_ID = session_id
            worker = threading.Thread(
                target=_auto_capture_worker,
                args=(session,),
                daemon=True,
                name=f"palmprint-auto-enroll-{session_id[:8]}",
            )
            session.worker = worker
            with _EVALUATION_LOCK:
                _BACKGROUND_WORKERS.add(worker)
            worker.start()
        return _session_response(session)

    @service.post("/api/enrollment-sessions/{session_id}/samples")
    async def enrollment_sample(session_id: str, request: Request) -> dict[str, Any]:
        """Compatibility path for assisted upload; only a valid sample is saved."""

        session = get_session(session_id)
        if session.options.mode != "assisted":
            raise HTTPException(status_code=409, detail="自助注册仅支持自动采集")
        form = await request.form()
        if len(session.samples) >= MAX_ENROLL_SAMPLES:
            raise HTTPException(status_code=409, detail=f"最多 {MAX_ENROLL_SAMPLES} 个样本")
        upload = form.get("image")
        source = "upload"
        device: str | None = None
        requested_resolution: str | None = None
        if upload is not None:
            if not hasattr(upload, "read"):
                raise HTTPException(status_code=422, detail="image 字段不是文件")
            image = _decode_image_bytes(await upload.read())
            # Only an explicitly uploaded, already-cropped sample may bypass
            # the ROI locator.  A camera frame must never use this shortcut.
            assume_roi = _parse_bool(form.get("assume_roi"), False)
        else:
            source = "camera"
            assume_roi = False
            device = str(form.get("camera_device", ""))
            if not device:
                raise HTTPException(status_code=422, detail="请上传 image 或指定 camera_device")
            width, height = _parse_resolution(
                str(form.get("resolution", f"{CAMERA_DEFAULT_WIDTH}x{CAMERA_DEFAULT_HEIGHT}"))
            )
            requested_resolution = f"{width}x{height}"
            try:
                frame = await run_in_threadpool(
                    partial(
                        WORKBENCH.capture_camera_frame,
                        device,
                        width,
                        height,
                        encode_jpeg=False,
                    )
                )
                image = frame.rgb
            except (CameraError, ValueError) as exc:
                raise HTTPException(status_code=503, detail=f"摄像头错误: {exc}") from exc
        result, error = service_layer._extract(
            image,
            assume_roi,
            roi_config=_fixed_roi_config(device, requested_resolution) if device else None,
        )
        if error or result.roi is None:
            raise HTTPException(status_code=422, detail=error or "ROI 提取失败")
        capture_id = _safe_capture_save(
            original=image,
            roi=result.roi,
            metadata={
                "purpose": "enrollment",
                "source": source,
                "model_id": session.options.model_id,
                "backend": session.options.backend,
                "precision": session.options.precision,
                "device": device,
                "requested_resolution": requested_resolution,
                "actual_resolution": _image_resolution(image),
                "roi_mode": "pre_cropped" if assume_roi else "fixed_guide",
                "camera_physical_id": _camera_physical_id(device) if device else None,
                "roi_ok": True,
                "quality": _jsonable(result.quality),
                "geometry": _jsonable(result.geometry),
                "roi_pipeline_id": ROI_PIPELINE_ID,
                "status": "enrollment_selected",
                "enrollment_session_id": session.session_id,
            },
        )
        if not capture_id:
            raise HTTPException(status_code=503, detail="无法保存入选采集样本")
        with _SESSION_LOCK:
            session.samples.append(
                EnrollmentSample(
                    sample_id=uuid4().hex,
                    roi=result.roi.copy(),
                    capture_id=capture_id,
                    preview_url=_frame_data_url(result.preview),
                    roi_url=_frame_data_url(result.roi, image_format=".png"),
                    quality=_jsonable(result.quality),
                    geometry=_jsonable(result.geometry),
                )
            )
            session.capture_ids.append(capture_id)
            session.phase = "reviewing" if len(session.samples) == MAX_ENROLL_SAMPLES else "collecting"
            session.guide = (
                "已采集 5 张，请审阅样本后保存"
                if session.phase == "reviewing"
                else f"已采集 {len(session.samples)} / {MAX_ENROLL_SAMPLES}"
            )
        return _session_response(session)

    @service.delete("/api/enrollment-sessions/{session_id}/samples/{sample_id}")
    async def discard_enrollment_sample(session_id: str, sample_id: str) -> dict[str, Any]:
        session = get_session(session_id)
        if session.options.mode != "assisted":
            raise HTTPException(status_code=403, detail="仅管理员辅助注册可以剔除样本")
        with _SESSION_LOCK:
            selected = next((item for item in session.samples if item.sample_id == sample_id), None)
            if selected is None:
                raise HTTPException(status_code=404, detail="样本不存在")
            session.samples.remove(selected)
            session.capture_ids = [item for item in session.capture_ids if item != selected.capture_id]
            session.phase = "ready"
            session.guide = "已剔除样本，正在自动补采至 5 张"
        try:
            WORKBENCH.captures.cleanup(selected.capture_id)
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"无法删除样本图像：{exc}") from exc
        if session.device and session.resolution:
            _wait_for_auto_capture(session)
            await start_auto_capture(
                session_id,
                EnrollmentAutoCaptureOptions(device=session.device, resolution=session.resolution),
            )
        return _session_response(session)

    @service.post("/api/enrollment-sessions/{session_id}/commit")
    async def commit_enrollment(session_id: str, request: EnrollmentCommit) -> dict[str, Any]:
        session = get_session(session_id)
        if session.options.mode != "assisted":
            raise HTTPException(status_code=403, detail="自助注册在第 5 张样本后自动保存")
        if len(session.samples) != MAX_ENROLL_SAMPLES:
            raise HTTPException(status_code=422, detail=f"需要恰好 {MAX_ENROLL_SAMPLES} 个合格样本")
        if session.phase != "reviewing":
            raise HTTPException(status_code=409, detail="只能保存处于审阅阶段的会话")
        if request.person_id and request.person_id != session.options.person_id:
            raise HTTPException(status_code=422, detail="不能修改会话人员编号")
        if request.name:
            raise HTTPException(status_code=422, detail="不能修改会话人员编号")
        if request.display_name is not None:
            session.options.display_name = request.display_name.strip()
        if request.palm_side and request.palm_side != session.options.palm_side:
            raise HTTPException(status_code=422, detail="不能修改会话掌侧")
        _validate_enrollment_identity(session.options)
        if request.model_id or request.backend or request.precision:
            for key in ("model_id", "backend", "precision"):
                value = getattr(request, key)
                if value and value != getattr(session.options, key):
                    raise HTTPException(status_code=422, detail="不能修改已采集会话的模型配置")
        with _SESSION_LOCK:
            session.phase = "saving"
            session.guide = "正在编码并保存掌纹模板"
        try:
            replacement = await run_in_threadpool(_complete_enrollment, session)
        except (FileNotFoundError, RuntimeError, ValueError, KeyError) as exc:
            _set_enrollment_failure(session, _npu_error_detail(exc))
            raise HTTPException(status_code=503, detail=_npu_error_detail(exc)) from exc
        with _SESSION_LOCK:
            session.phase = "completed"
            session.completed_at = time.time()
            session.guide = f"已保存 {replacement['palm_id']} 的 {MAX_ENROLL_SAMPLES} 个样本"
        return _session_response(session, palm_id=replacement["palm_id"], replaced_samples=replacement["replaced_samples"])

    @service.delete("/api/enrollment-sessions/{session_id}")
    async def cancel_enrollment(session_id: str) -> dict[str, Any]:
        with _SESSION_LOCK:
            session = _SESSIONS.pop(session_id, None)
        if session is not None:
            if session.phase == "completed":
                with _SESSION_LOCK:
                    _SESSIONS[session_id] = session
                raise HTTPException(status_code=409, detail="已完成的注册不能取消；删除模板会同步删除关联图像")
            session.phase = "cancelled"
            session.guide = "注册已取消，已删除本次采集图像"
            _stop_auto_capture(session)
            _wait_for_auto_capture(session)
            _cleanup_enrollment_captures(session)
        return {"ok": True, "cancelled": session is not None, "id": session_id}

    @service.post("/api/enrollment-sessions/{session_id}/cancel")
    async def cancel_enrollment_post(session_id: str) -> dict[str, Any]:
        return await cancel_enrollment(session_id)

    @service.get("/api/templates")
    async def templates(
        model_id: str = Query("ccnet"),
        backend: str = Query("npu"),
        precision: str = Query("mixed_fp16"),
    ) -> dict[str, Any]:
        _validate_options(RecognitionOptions(model_id=model_id, backend=backend, precision=precision))
        namespace = service_layer._template_namespace(model_id, backend, precision)
        try:
            users = WORKBENCH.store.users(namespace, roi_pipeline_id=ROI_PIPELINE_ID)
            summary = WORKBENCH.store.summary(namespace, roi_pipeline_id=ROI_PIPELINE_ID)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=f"模板存储不可用：{exc}") from exc
        return {
            "items": [
                {
                    "id": item.get("palm_id") or item["user_id"],
                    "palm_id": item.get("palm_id") or item["user_id"],
                    "person_id": item.get("person_id") or item["user_name"],
                    "display_name": item.get("display_name") or item["user_name"],
                    "user_id": item.get("palm_id") or item["user_id"],
                    "user_name": item.get("display_name") or item["user_name"],
                    "palm_side": item["palm_side"],
                    "samples": item["samples"],
                    "model_id": model_id,
                    "updated_at": item.get("enrolled_at"),
                }
                for item in users
            ],
            "model_id": model_id,
            "backend": backend,
            "precision": precision,
            "roi_pipeline_id": ROI_PIPELINE_ID,
            "summary": summary,
        }

    @service.delete("/api/templates/{template_id}")
    async def delete_template(
        template_id: str,
        model_id: str = Query("ccnet"),
        backend: str = Query("npu"),
        precision: str = Query("mixed_fp16"),
    ) -> dict[str, Any]:
        _validate_options(RecognitionOptions(model_id=model_id, backend=backend, precision=precision))
        namespace = service_layer._template_namespace(model_id, backend, precision)
        try:
            removed = WORKBENCH.store.remove(namespace, template_id, roi_pipeline_id=ROI_PIPELINE_ID)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=f"模板存储不可用：{exc}") from exc
        if not removed:
            raise HTTPException(status_code=404, detail="模板不存在")
        try:
            WORKBENCH.captures.purge_template(model_id=model_id, palm_id=template_id, roi_pipeline_id=ROI_PIPELINE_ID)
        except Exception:
            logger.exception("unable to remove captures after template deletion: %s", template_id)
        return {"ok": True, "id": template_id, "deleted": True}

    @service.delete("/api/templates")
    async def delete_template_query(
        user_id: str = Query(...),
        model_id: str = Query("ccnet"),
        backend: str = Query("npu"),
        precision: str = Query("mixed_fp16"),
    ) -> dict[str, Any]:
        return await delete_template(user_id, model_id, backend, precision)

    @service.get("/api/captures")
    async def captures(
        model_id: Optional[str] = Query(None),
        purpose: Optional[str] = Query(None),
        limit: int = Query(200, ge=1, le=1000),
    ) -> dict[str, Any]:
        return {
            "items": WORKBENCH.captures.list(model_id=model_id, purpose=purpose, limit=limit),
            "limit": limit,
        }

    @service.get("/api/captures/{capture_id}")
    async def capture_detail(capture_id: str) -> dict[str, Any]:
        try:
            return WORKBENCH.captures.get(capture_id)
        except (KeyError, ValueError):
            raise HTTPException(status_code=404, detail="采集记录不存在")

    @service.get("/api/captures/{capture_id}/original")
    async def capture_original(capture_id: str) -> Response:
        try:
            path = WORKBENCH.captures.path_for(capture_id, "original")
        except (KeyError, ValueError, FileNotFoundError):
            raise HTTPException(status_code=404, detail="原图不存在")
        return FileResponse(path, media_type="image/jpeg")

    @service.get("/api/captures/{capture_id}/roi")
    async def capture_roi(capture_id: str) -> Response:
        try:
            path = WORKBENCH.captures.path_for(capture_id, "roi")
        except (KeyError, ValueError, FileNotFoundError):
            raise HTTPException(status_code=404, detail="ROI 不存在")
        return FileResponse(path, media_type="image/png")

    @service.post("/api/evaluations")
    async def start_evaluation(options: EvaluationOptions) -> dict[str, Any]:
        # Evaluation is a production route and therefore uses the same strict
        # admitted-model resolver as recognition and enrollment.
        _validate_options(RecognitionOptions(**_model_dict(options)))
        state = EvaluationState(uuid4().hex, options)
        _reserve_background_job(state)
        _start_background_worker(
            _run_evaluation,
            state,
            f"palmprint-eval-{state.evaluation_id[:8]}",
        )
        return _evaluation_response(state)

    @service.post("/api/evaluation-jobs")
    async def start_evaluation_alias(options: EvaluationOptions) -> dict[str, Any]:
        return await start_evaluation(options)

    @service.get("/api/evaluations/{evaluation_id}/report")
    async def evaluation_report(evaluation_id: str) -> Response:
        with _EVALUATION_LOCK:
            _prune_finished_jobs_locked()
            state = _EVALUATIONS.get(evaluation_id)
        if state is None:
            raise HTTPException(status_code=404, detail="评测任务不存在")
        path_value = state.reports.get("markdown") or state.reports.get("json")
        if not path_value:
            raise HTTPException(status_code=409, detail="评测报告尚未生成")
        path = Path(path_value).resolve()
        try:
            path.relative_to(REPORT_DIR.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=500, detail="报告路径不在报告目录") from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="报告文件不存在")
        return FileResponse(path, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream", filename=path.name)

    @service.get("/api/evaluation-jobs/{evaluation_id}/report")
    async def evaluation_report_alias(evaluation_id: str) -> Response:
        return await evaluation_report(evaluation_id)

    @service.get("/api/evaluations/{evaluation_id}")
    async def evaluation(evaluation_id: str) -> dict[str, Any]:
        with _EVALUATION_LOCK:
            _prune_finished_jobs_locked()
            state = _EVALUATIONS.get(evaluation_id)
        if state is None:
            raise HTTPException(status_code=404, detail="评测任务不存在")
        return _evaluation_response(state)

    @service.get("/api/evaluation-jobs/{evaluation_id}")
    async def evaluation_alias(evaluation_id: str) -> dict[str, Any]:
        return await evaluation(evaluation_id)

    @service.get("/api/system-status")
    async def system_status() -> dict[str, Any]:
        return _status_payload()

    @service.post("/api/comparisons")
    async def start_comparison(options: ComparisonOptions) -> dict[str, Any]:
        del options
        raise HTTPException(
            status_code=409,
            detail="候选比较仅可通过 tools.offline 执行；生产 API 只允许已准入模型",
        )

    @service.get("/api/comparisons/{comparison_id}")
    async def comparison(comparison_id: str) -> dict[str, Any]:
        with _EVALUATION_LOCK:
            _prune_finished_jobs_locked()
            state = _COMPARISONS.get(comparison_id)
        if state is None:
            raise HTTPException(status_code=404, detail="比较任务不存在")
        return _comparison_response(state)

    @service.get("/api/comparisons/{comparison_id}/report")
    async def comparison_report(comparison_id: str) -> Response:
        with _EVALUATION_LOCK:
            _prune_finished_jobs_locked()
            state = _COMPARISONS.get(comparison_id)
        if state is None or not state.report_path:
            raise HTTPException(status_code=404, detail="比较报告不存在")
        path = Path(state.report_path).resolve()
        try:
            path.relative_to(REPORT_DIR.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=500, detail="报告路径不在报告目录") from exc
        if not path.is_file():
            raise HTTPException(status_code=404, detail="比较报告文件不存在")
        return FileResponse(path, media_type="application/json", filename=path.name)

    @service.get("/{full_path:path}", include_in_schema=False)
    async def frontend(full_path: str) -> Response:
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="API route not found")
        requested = (static_root / full_path).resolve()
        try:
            requested.relative_to(static_root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="非法静态资源路径") from exc
        if requested.is_file():
            # The entry document points at content-hashed JS/CSS assets.  Do
            # not let a kiosk cache an older index after a board-side bundle
            # update; the hashed assets themselves remain safely cacheable.
            headers = (
                {"Cache-Control": "no-store, max-age=0"}
                if requested.name == "index.html"
                else {"Cache-Control": "public, max-age=31536000, immutable"}
            )
            return FileResponse(requested, headers=headers)
        index = static_root / "index.html"
        if index.is_file():
            return FileResponse(index, headers={"Cache-Control": "no-store, max-age=0"})
        return JSONResponse({"detail": "frontend/dist/index.html is not available"}, status_code=503)

    return service


app = create_app()


def serve(host: str = SERVER_HOST, port: int = SERVER_PORT) -> None:
    import uvicorn

    uvicorn.run(app, host=host, port=int(port), log_level="info")


if __name__ == "__main__":
    serve()
