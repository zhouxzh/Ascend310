#!/usr/bin/env python3
"""Palmprint recognition, camera and template service layer.

This module contains the domain callbacks shared by the HTTP API and the
board smoke-test scripts.  It deliberately has no UI framework dependency.
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Any

import cv2
import numpy as np
import pandas as pd

from ..runtime.camera import CameraCapture, CameraDevice, CameraError, CameraFrame, list_v4l2_devices
from ..config import (
    CAMERA_DEFAULT_DEVICE,
    CAMERA_DEFAULT_FPS,
    CAMERA_DEFAULT_HEIGHT,
    CAMERA_DEFAULT_WIDTH,
    camera_resolution_options,
    CAPTURE_DIR,
    DEFAULT_THRESHOLD,
    MAX_ENROLL_SAMPLES,
    MIN_ENROLL_SAMPLES,
    MANUAL_TEST_PROFILE,
    PRODUCTION_PRECISION,
    REPORT_DIR,
    RELEASE_PROFILE,
    ROOT,
    TEMPLATE_DIR,
    ensure_runtime_dirs,
)
from ..domain.registry import ModelRegistry, ModelSpec
from ..domain.admission import resolve_runtime_model
from ..runtime.adapters import PalmAdapter, create_adapter, shutdown_acl_runtime
from ..domain.datasets import audit_extracted, audit_palmmatchdb_zip, load_dataset_manifest, records
from ..domain.preprocessing import PalmPreprocessor
from ..domain.captures import CaptureStore
from ..domain.templates import TemplateStore


# The package service is the production boundary.  Research candidates and
# CPU/EDCC baselines are exposed only by ``tools.offline``.
MODEL_CHOICES = [("CCNet", "ccnet")]
BACKEND_CHOICES = [("Ascend NPU", "npu")]
PRECISION_CHOICES = [("FP16 混合精度", "mixed_fp16")]
CAMERA_RESOLUTION_CHOICES = [
    (value.replace("x", " x "), value) for value in camera_resolution_options()
]


def camera_device_choices(devices: list[CameraDevice] | None = None) -> list[tuple[str, str]]:
    """Return readable V4L2 labels without opening any device."""

    discovered = list_v4l2_devices() if devices is None else devices
    return [
        (f"{device.path} - {device.name or 'V4L2 视频节点'}", device.path)
        for device in discovered
    ]


def _default_camera_device(choices: list[tuple[str, str]]) -> str | None:
    values = {value for _, value in choices}
    if CAMERA_DEFAULT_DEVICE in values:
        return CAMERA_DEFAULT_DEVICE
    return choices[0][1] if choices else None


def parse_camera_resolution(value: str | None) -> tuple[int, int]:
    try:
        width_text, height_text = str(value or "").lower().split("x", maxsplit=1)
        width, height = int(width_text), int(height_text)
    except (TypeError, ValueError) as exc:
        raise ValueError("摄像头分辨率必须为 WIDTHxHEIGHT") from exc
    if width <= 0 or height <= 0:
        raise ValueError("摄像头分辨率必须为正数")
    return width, height


def default_camera_resolution() -> str:
    value = f"{CAMERA_DEFAULT_WIDTH}x{CAMERA_DEFAULT_HEIGHT}"
    available = {choice for _, choice in CAMERA_RESOLUTION_CHOICES}
    return value if value in available else CAMERA_RESOLUTION_CHOICES[0][1]


def refresh_camera_choices(current_device: str | None) -> tuple[dict[str, Any], str]:
    choices = camera_device_choices()
    values = {value for _, value in choices}
    value = current_device if current_device in values else _default_camera_device(choices)
    if not choices:
        return {"choices": [], "value": None}, "未检测到可用的 V4L2 视频节点"
    return {"choices": choices, "value": value}, f"已检测到 {len(choices)} 个 V4L2 视频节点"


def backend_choices_for_model(model_id: str, current_backend: str = "npu") -> tuple[list[tuple[str, str]], str]:
    """Return only the NPU backend accepted by the production service."""
    del model_id
    return BACKEND_CHOICES, "npu" if current_backend != "npu" else current_backend


def update_backend_choices(model_id: str, current_backend: str) -> dict[str, Any]:
    choices, value = backend_choices_for_model(model_id, current_backend)
    return {
        "choices": choices,
        "value": value,
    }


class Workbench:
    def __init__(self) -> None:
        ensure_runtime_dirs()
        self.registry = ModelRegistry()
        self.preprocessor = PalmPreprocessor()
        self.store = TemplateStore(TEMPLATE_DIR)
        self.captures = CaptureStore(CAPTURE_DIR)
        self.execution_lock = threading.RLock()
        self.adapters: dict[tuple[str, str, str, int], PalmAdapter] = {}
        self.camera_lock = threading.RLock()
        self.cameras: dict[tuple[str, int, int], CameraCapture] = {}
        # A browser preview has a short-lived session.  Keeping the token by
        # device lets a delayed close/frame request from an old page be
        # rejected after the user swaps cameras or changes resolution.
        self.camera_sessions: dict[str, str] = {}
        self._state_lock = threading.RLock()
        self._closing = False
        self.last_close_diagnostics: dict[str, Any] | None = None

    def adapter(
        self,
        model_id: str,
        backend: str,
        precision: str,
        threads: int = 4,
    ) -> PalmAdapter:
        if backend != "npu" or precision != "mixed_fp16":
            raise ValueError("正式服务仅支持 backend=npu 和 precision=mixed_fp16")
        # The service resolver is intentionally production-only.  Candidate,
        # ONNX and EDCC adapters are owned by tools.offline and cannot be
        # enabled through a runtime flag or an API request.
        spec = resolve_runtime_model(
            model_id,
            registry=self.registry,
            verify_assets=True,
            include_manual_test=RELEASE_PROFILE == MANUAL_TEST_PROFILE,
        )
        key = (spec.id, backend, precision, int(threads))
        with self.execution_lock:
            with self._state_lock:
                if self._closing:
                    raise RuntimeError("Workbench is shutting down; new inference is disabled")
            if key not in self.adapters:
                self.adapters[key] = create_adapter(
                    spec, backend, precision, threads=threads
                )
            return self.adapters[key]

    def capture_camera_frame(
        self,
        device: str,
        width: int,
        height: int,
        *,
        encode_jpeg: bool = True,
        session: str | None = None,
    ) -> CameraFrame:
        """Capture from one selected board-side V4L2 node and retain only that handle."""

        key = (str(device), int(width), int(height))
        with self.camera_lock:
            with self._state_lock:
                if self._closing:
                    raise CameraError("工作台正在关闭，摄像头采集已停止")
            if session is not None:
                current = self.camera_sessions.get(str(device))
                if current != session:
                    raise CameraError("摄像头预览会话已切换，请重新打开摄像头")
            for old_key, camera in list(self.cameras.items()):
                if old_key != key:
                    try:
                        camera.close()
                    finally:
                        self.cameras.pop(old_key, None)
            camera = self.cameras.get(key)
            if camera is None:
                camera = CameraCapture(
                    device,
                    width=width,
                    height=height,
                    fps=CAMERA_DEFAULT_FPS,
                )
                self.cameras[key] = camera
            try:
                return camera.capture(encode_jpeg=encode_jpeg)
            except CameraError:
                # A USB camera can be physically replaced while the process
                # remains alive.  OpenCV may retain an object whose node is no
                # longer valid; release it and retry exactly once with a new
                # handle.  Session validation above prevents stale requests
                # from reopening an old camera after a hot switch.
                try:
                    camera.close()
                finally:
                    self.cameras.pop(key, None)
                replacement = CameraCapture(
                    device,
                    width=width,
                    height=height,
                    fps=CAMERA_DEFAULT_FPS,
                )
                self.cameras[key] = replacement
                return replacement.capture(encode_jpeg=encode_jpeg)

    def open_camera_session(
        self,
        device: str,
        width: int,
        height: int,
        session: str,
    ) -> None:
        """Register a preview session and invalidate every older handle."""

        token = str(session).strip()
        if not token:
            raise CameraError("摄像头预览会话不能为空")
        device_key = str(device)
        key = (device_key, int(width), int(height))
        with self.camera_lock:
            with self._state_lock:
                if self._closing:
                    raise CameraError("工作台正在关闭，摄像头采集已停止")
            # The workbench intentionally owns one physical camera handle at
            # a time.  Invalidate tokens for other devices before closing
            # them, so an in-flight request from the old device cannot reopen
            # it after a hot switch.
            for old_device in list(self.camera_sessions):
                if old_device != device_key:
                    self.camera_sessions.pop(old_device, None)
            self.camera_sessions[device_key] = token
            # Only one physical camera handle is retained at a time.  Close
            # old devices as well as old resolutions, but do not let one
            # failed release prevent the new session from being registered.
            for old_key, camera in list(self.cameras.items()):
                if old_key == key and camera.is_open:
                    continue
                try:
                    camera.close()
                finally:
                    self.cameras.pop(old_key, None)

    def close_cameras(
        self,
        *,
        device: str | None = None,
        width: int | None = None,
        height: int | None = None,
        session: str | None = None,
    ) -> None:
        """Release camera handles matching the optional capture key.

        A page can unmount an old preview while a new resolution is already
        opening.  Limiting that asynchronous close to the old key prevents it
        from closing the new handle; shutdown still calls this without filters
        to release every camera.
        """
        with self.camera_lock:
            if session is not None and self.camera_sessions.get(str(device)) != str(session):
                # This is an old effect cleanup.  It must never close the
                # handle owned by the current preview session.
                return
            for key, camera in list(self.cameras.items()):
                key_device, key_width, key_height = key
                if device is not None and key_device != str(device):
                    continue
                if width is not None and key_width != int(width):
                    continue
                if height is not None and key_height != int(height):
                    continue
                try:
                    camera.close()
                finally:
                    self.cameras.pop(key, None)
            if device is None:
                self.camera_sessions.clear()
            elif session is None or self.camera_sessions.get(str(device)) == str(session):
                self.camera_sessions.pop(str(device), None)

    def camera_states(self) -> dict[str, str]:
        with self.camera_lock:
            return {
                device: f"已打开 {width} x {height}" if camera.is_open else "待打开"
                for (device, width, height), camera in self.cameras.items()
            }

    def close(self) -> dict[str, Any]:
        """Close cached device resources before explicitly releasing PyACL.

        A service process can cache several OM adapters.  It is unsafe to
        reset the device until every runner has destroyed its context/model
        resources, so cleanup deliberately continues after an individual
        failure and records all outcomes for the ASGI lifespan handler.
        """

        diagnostics: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "adapter_close": [],
            "camera_close": [],
            "runtime_shutdown": None,
        }
        errors: list[str] = []
        with self._state_lock:
            self._closing = True
        with self.execution_lock:
            adapters = list(self.adapters.items())
            for key, adapter in adapters:
                try:
                    adapter.close()
                    self.adapters.pop(key, None)
                    diagnostics["adapter_close"].append({"key": key, "ok": True})
                except BaseException as exc:
                    diagnostics["adapter_close"].append(
                        {"key": key, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    )
                    errors.append(f"adapter {key}: {type(exc).__name__}: {exc}")

        with self.camera_lock:
            cameras = list(self.cameras.items())
            for key, camera in cameras:
                try:
                    camera.close()
                    self.cameras.pop(key, None)
                    diagnostics["camera_close"].append({"key": key, "ok": True})
                except BaseException as exc:
                    diagnostics["camera_close"].append(
                        {"key": key, "ok": False, "error": f"{type(exc).__name__}: {exc}"}
                    )
                    errors.append(f"camera {key}: {type(exc).__name__}: {exc}")

        try:
            diagnostics["runtime_shutdown"] = shutdown_acl_runtime()
            if not diagnostics["runtime_shutdown"].get("ok", False):
                errors.append(
                    "ACL runtime shutdown: "
                    f"{diagnostics['runtime_shutdown'].get('status', 'unknown failure')}"
                )
        except BaseException as exc:
            diagnostics["runtime_shutdown"] = {
                "ok": False,
                "status": "exception",
                "error": f"{type(exc).__name__}: {exc}",
            }
            errors.append(f"ACL runtime shutdown: {type(exc).__name__}: {exc}")

        diagnostics["ok"] = not errors
        diagnostics["errors"] = errors
        self.last_close_diagnostics = diagnostics
        if errors:
            raise RuntimeError("Workbench cleanup failed: " + "; ".join(errors))
        return diagnostics


WORKBENCH = Workbench()


def _require_recognition_model(model_id: str) -> None:
    if model_id != "compnet":
        return
    marker = ROOT / "models" / "checkpoints" / "compnet_conversion_only.json"
    if not marker.is_file():
        raise RuntimeError("CompNet 尚未生成或验证")
    status = json.loads(marker.read_text(encoding="utf-8"))
    if not status.get("accuracy_eligible", False):
        raise RuntimeError("CompNet 当前为 conversion-only，不能用于注册或识别准确率结论")


def _template_namespace(model_id: str, backend: str, precision: str) -> str:
    if backend != "npu":
        raise ValueError("模板只支持 NPU 后端")
    if precision != PRODUCTION_PRECISION:
        raise ValueError("模板只支持 mixed_fp16 精度")
    # Production templates use one explicit namespace. Numerical consistency
    # is an admission check, not a reason to merge stores from another backend.
    return f"{model_id}__npu__{precision}"


def _extract(image: np.ndarray | None, assume_roi: bool) -> tuple[Any, str | None]:
    result = WORKBENCH.preprocessor.extract(image, assume_roi=assume_roi)
    if not result.ok or result.roi is None:
        return result, result.reason
    return result, None


def capture_board_camera(
    device: str | None, resolution: str | None
) -> tuple[np.ndarray | None, str]:
    """Place a single board-camera frame into the same image input used by the UI."""

    if not device:
        return None, "请选择板端 V4L2 摄像头"
    try:
        width, height = parse_camera_resolution(resolution)
        frame = WORKBENCH.capture_camera_frame(device, width, height)
    except (CameraError, ValueError) as exc:
        return None, f"摄像头错误：{exc}"
    actual_height, actual_width = frame.rgb.shape[:2]
    return frame.rgb, f"已拍摄 {frame.device} | {actual_width} x {actual_height}"


def close_board_camera() -> str:
    WORKBENCH.close_cameras()
    return "板端摄像头已关闭"


def recognize(
    image: np.ndarray | None,
    model_id: str,
    backend: str,
    precision: str,
    threshold: float,
    assume_roi: bool,
) -> tuple[Any, Any, pd.DataFrame, str, dict[str, float]]:
    request_started = time.perf_counter_ns()
    _require_recognition_model(model_id)
    roi_result, error = _extract(image, assume_roi)
    roi_finished = time.perf_counter_ns()
    if error:
        return roi_result.preview, None, pd.DataFrame(), f"未完成：{error}", roi_result.quality
    started = time.perf_counter_ns()
    with WORKBENCH.execution_lock:
        adapter = WORKBENCH.adapter(model_id, backend, precision)
        encoded = adapter.encode(roi_result.roi)
        search_started = time.perf_counter_ns()
        namespace = _template_namespace(model_id, backend, precision)
        result = WORKBENCH.store.search(namespace, adapter, encoded.code, threshold=float(threshold))
    finished = time.perf_counter_ns()
    matches = pd.DataFrame(
        [
            {
                "姓名": item["user_name"],
                "掌侧": item["palm_side"],
                "分数": round(item["score"], 6),
                "样本": item["samples"],
            }
            for item in result.get("matches", [])
        ]
    )
    if result["accepted"]:
        status = f"通过 | {result['user_name']} · {result['palm_side']} | {result['score']:.4f}"
    elif result.get("matches"):
        status = f"拒识 | 最高分 {result['score']:.4f}，阈值 {threshold:.4f}"
    else:
        status = "拒识 | 当前模型模板库为空"
    timing = {
        "ROI / quality ms": round((roi_finished - request_started) / 1e6, 3),
        "preprocess ms": round(encoded.preprocess_ms, 3),
        "model ms": round(encoded.inference_ms, 3),
        "search ms": round((finished - search_started) / 1e6, 3),
        "total ms": round((finished - request_started) / 1e6, 3),
    }
    roi_rgb = cv2.cvtColor(roi_result.roi, cv2.COLOR_GRAY2RGB)
    return roi_result.preview, roi_rgb, matches, status, timing


def add_enrollment_sample(
    image: np.ndarray | None,
    samples: list[np.ndarray] | None,
    assume_roi: bool,
) -> tuple[list[np.ndarray], list[np.ndarray], Any, str]:
    current = list(samples or [])
    if len(current) >= MAX_ENROLL_SAMPLES:
        return current, current, None, f"已达到 {MAX_ENROLL_SAMPLES} 个样本"
    result, error = _extract(image, assume_roi)
    if error:
        return current, current, result.preview, f"未采集：{error}"
    current.append(result.roi.copy())
    gallery = [cv2.cvtColor(item, cv2.COLOR_GRAY2RGB) for item in current]
    return current, gallery, result.preview, f"已采集 {len(current)} / {MAX_ENROLL_SAMPLES}"


def reset_enrollment() -> tuple[list, list, str]:
    return [], [], "采集已清空"


def confirm_enrollment(
    samples: list[np.ndarray] | None,
    name: str,
    palm_side: str,
    model_id: str,
    backend: str,
    precision: str,
) -> tuple[list, list, str, pd.DataFrame]:
    current = list(samples or [])
    if not name or not name.strip():
        return current, current, "姓名不能为空", user_table(model_id, backend, precision)
    if not MIN_ENROLL_SAMPLES <= len(current) <= MAX_ENROLL_SAMPLES:
        return (
            current,
            [cv2.cvtColor(item, cv2.COLOR_GRAY2RGB) for item in current],
            f"需要 {MIN_ENROLL_SAMPLES} 至 {MAX_ENROLL_SAMPLES} 个合格样本",
            user_table(model_id, backend, precision),
        )
    _require_recognition_model(model_id)
    with WORKBENCH.execution_lock:
        adapter = WORKBENCH.adapter(model_id, backend, precision)
        codes = [adapter.encode(item).code for item in current]
        namespace = _template_namespace(model_id, backend, precision)
        identity = WORKBENCH.store.enroll(namespace, codes, name, palm_side)
    return [], [], f"注册完成 | ID {identity[:8]} | {len(codes)} 个样本", user_table(model_id, backend, precision)


def user_table(model_id: str, backend: str, precision: str) -> pd.DataFrame:
    namespace = _template_namespace(model_id, backend, precision)
    return pd.DataFrame(
        [
            {
                "ID": item["user_id"],
                "姓名": item["user_name"],
                "掌侧": item["palm_side"],
                "样本": item["samples"],
            }
            for item in WORKBENCH.store.users(namespace)
        ]
    )


def delete_user(
    model_id: str, backend: str, precision: str, user_id: str
) -> tuple[str, pd.DataFrame]:
    if not user_id.strip():
        return "请输入 ID", user_table(model_id, backend, precision)
    namespace = _template_namespace(model_id, backend, precision)
    removed = WORKBENCH.store.remove(namespace, user_id.strip())
    return ("已删除" if removed else "未找到该 ID"), user_table(model_id, backend, precision)


def _command_output(command: list[str]) -> str:
    try:
        process = subprocess.run(command, capture_output=True, text=True, timeout=8)
        return (process.stdout + process.stderr).strip() or f"exit={process.returncode}"
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return str(exc)


def _cann_status() -> str:
    """Read installed CANN metadata instead of assuming `atc --version` exists."""

    candidates = (
        Path("/usr/local/Ascend/ascend-toolkit/latest/compiler/version.info"),
        Path("/usr/local/Ascend/ascend-toolkit/8.0.0/compiler/version.info"),
    )
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
    return _command_output(["atc", "--help"])


def model_status_table() -> pd.DataFrame:
    rows = []
    for spec in WORKBENCH.registry.all(
        include_manual_test=RELEASE_PROFILE == MANUAL_TEST_PROFILE
    ):
        reference_path = spec.path("reference_onnx")
        npu_path = spec.om_path("mixed_fp16")
        marker = spec.path("conversion_only_marker")
        conversion = _conversion_status(spec, npu_path, marker)
        model_hash = _registered_model_hash(spec, marker)
        rows.append(
            {
                "模型": spec.display_name,
                "生产后端": "NPU",
                "生产精度": "mixed-FP16",
                "参考 ONNX": "ready" if reference_path and reference_path.is_file() else "optional",
                "NPU mixed-FP16": "ready" if npu_path and npu_path.is_file() else "missing",
                "许可": spec.license,
                "用途": "研究测试" if spec.research_only else "可按许可证使用",
                "OM SHA-256": model_hash,
                "转换状态": conversion,
            }
        )
    return pd.DataFrame(rows)


def _conversion_status(
    spec: ModelSpec, npu_path: Path | None, marker: Path | None
) -> str:
    output = "mixed-FP16" if npu_path and npu_path.is_file() else "未生成 OM"
    if marker and marker.is_file():
        try:
            mode = json.loads(marker.read_text(encoding="utf-8")).get("mode")
            if mode:
                return f"{output}; {mode}"
        except (OSError, json.JSONDecodeError):
            return f"{output}; 状态标记不可读"
    return output


def _registered_model_hash(spec: ModelSpec, marker: Path | None) -> str:
    assets = spec.raw.get("assets", {})
    mixed_asset = assets.get("mixed_fp16_om", {}) if isinstance(assets, dict) else {}
    value = mixed_asset.get("sha256") if isinstance(mixed_asset, dict) else None
    value = value or spec.raw.get("mixed_fp16_sha256") or spec.raw.get("sha256")
    if value:
        return str(value)
    if marker and marker.is_file():
        try:
            metadata = json.loads(marker.read_text(encoding="utf-8"))
            value = metadata.get("onnx", {}).get("sha256") or metadata.get("checkpoint_sha256")
            if value:
                return str(value)
        except (OSError, json.JSONDecodeError):
            pass
    return "未登记"


def dataset_status_table() -> pd.DataFrame:
    rows = []
    for item in load_dataset_manifest()["datasets"]:
        if item["id"] in {"tongji", "polyu"}:
            extracted = audit_extracted(item["id"])
            status = "ready" if extracted.get("ready") else "unavailable"
            image_count = extracted.get("parsed_images", 0)
            identity_count = extracted.get("palm_identities", 0)
        else:
            extracted = audit_palmmatchdb_zip(verify_integrity=False)
            status = "audited-no-session" if extracted.get("zip_ok") else "unavailable"
            image_count = extracted.get("image_files", 0)
            identity_count = extracted.get("person_side_identities", 0)
        rows.append(
            {
                "数据集": item["display_name"],
                "状态": status,
                "图像": image_count,
                "掌身份": identity_count,
                "说明": extracted.get("reason", "结构校验通过" if extracted.get("ready") else "未解压"),
            }
        )
    return pd.DataFrame(rows)


def camera_status_table() -> pd.DataFrame:
    states = WORKBENCH.camera_states()
    rows = []
    for device in list_v4l2_devices():
        rows.append(
            {
                "节点": device.path,
                "名称": device.name or "V4L2 视频节点",
                "状态": states.get(device.path, "已检测，未打开"),
            }
        )
    if not rows:
        rows.append({"节点": "-", "名称": "未检测到 V4L2 视频节点", "状态": "不可用"})
    return pd.DataFrame(rows)


def _status_summary(value: str, max_chars: int = 180) -> str:
    """Keep the status tab readable by retaining only key one-line signals."""

    lines = [" ".join(line.split()) for line in str(value).splitlines() if line.strip()]
    if not lines:
        return "未获取"
    compact_source = " | ".join(lines)
    # The CANN probe emits a machine-oriented compatibility record. Keep the
    # two fields an operator needs instead of exposing the whole ABI string.
    if "version_dir=" in compact_source and "Version=" in compact_source:
        version_match = re.search(r"Version=([^ |]+)", compact_source)
        toolkit_match = re.search(r"version_dir=([^ |]+)", compact_source)
        if version_match or toolkit_match:
            version = version_match.group(1) if version_match else "unknown"
            toolkit = toolkit_match.group(1) if toolkit_match else "unknown"
            return f"Version {version} · toolkit {toolkit}"
    # The device row is more important than the wide npu-smi header. Keep an
    # Alarm/Error signal at the front so a short UI summary cannot hide it.
    alarm_lines = [
        line for line in lines
        if any(term in line.lower() for term in ("alarm", "error", "failed", "failure"))
    ]
    if alarm_lines:
        alarm = alarm_lines[0]
        soc_match = re.search(r"(310[A-Za-z0-9]+)", alarm)
        health_match = re.search(r"\b(Alarm|Healthy|OK|Error)\b", alarm, flags=re.IGNORECASE)
        if soc_match and health_match:
            return f"{soc_match.group(1)} · Health {health_match.group(1)}"
        return alarm[:max_chars]
    priority_terms = ("alarm", "health", "temperature", "version", "soc", "npu")
    selected = [lines[0]]
    for line in lines[1:]:
        lowered = line.lower()
        if any(term in lowered for term in priority_terms) and line not in selected:
            selected.append(line)
        if len(" | ".join(selected)) >= max_chars:
            break
    return " | ".join(selected)[:max_chars]


def system_status() -> tuple[str, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    npu = _command_output(["npu-smi", "info"])
    cann = _cann_status()
    summary = "\n\n".join(
        [
            f"**CANN / ATC:** {_status_summary(cann)}",
            f"**NPU:** {_status_summary(npu)}",
        ]
    )
    return summary, model_status_table(), dataset_status_table(), camera_status_table()


def run_dataset_evaluation(
    dataset_id: str,
    spectrum: str,
    model_id: str,
    backend: str,
    precision: str,
    max_identities: int,
    progress=None,
) -> tuple[pd.DataFrame, pd.DataFrame, str]:
    from .evaluation import (
        evaluate_dataset,
        performance_benchmark,
        write_report,
    )

    if progress is None:
        progress = lambda *_args, **_kwargs: None
    limit = None if max_identities is None or int(max_identities) <= 0 else int(max_identities)
    progress(0, desc="准备评测")
    with WORKBENCH.execution_lock:
        result = evaluate_dataset(
            model_id,
            backend,
            precision,
            dataset_id,
            spectrum,
            4,
            limit,
            progress=lambda value, message: progress(value * 0.85, desc=message),
        )
        sample_path = records(dataset_id, spectrum)[0].path
        performance = performance_benchmark(
            model_id,
            backend,
            precision,
            sample_path,
            threads=4,
            warmup=10,
            loops=100,
            repeats=3,
        )
    combined = {"accuracy": result, "performance": performance}
    stamp = time.strftime("%Y%m%d_%H%M%S")
    paths = write_report(combined, f"ui_{model_id}_{backend}_{dataset_id}_{stamp}")
    metrics = pd.DataFrame(
        [{"指标": key, "值": value} for key, value in result["metrics"].items()]
    )
    plot = pd.DataFrame(
        [
            {"阶段": "纯模型 P50", "延迟 (ms)": performance["pure_model"]["p50_ms"]},
            {"阶段": "完整流水线 P50", "延迟 (ms)": performance["pipeline"]["p50_ms"]},
        ]
    )
    progress(1, desc="完成")
    return metrics, plot, paths["markdown"]
