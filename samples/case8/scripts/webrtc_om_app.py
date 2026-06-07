#!/usr/bin/env python3
import argparse
import asyncio
import fractions
import logging
import os
import queue
import socket
import sys
import time
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

import av
import cv2
import numpy as np
from aiohttp import web
from aiortc import MediaStreamTrack, RTCPeerConnection, RTCRtpSender, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hagrid_yolo.backends.acl_backend import AclModel, AclRuntime
from hagrid_yolo.detector import YoloDetector
from hagrid_yolo.metadata import available_video_devices, get_source, load_labels, resolve_imgsz
from hagrid_yolo.visualization import draw_detections

try:
    from webrtc_app.cann_encoder import (
        CannH264Encoder,
        set_session_bitrate_override_kbps,
        set_encoder_status_callback,
        _try_import_cann,
    )
    from webrtc_app.dvpp_jpegd import DvppJpegDecoder
    from webrtc_app.v4l2_capture import V4l2MjpegCapture
    from webrtc_app.v4l2_raw import V4l2RawCapture
except ImportError:
    CannH264Encoder = None  # type: ignore[assignment]
    DvppJpegDecoder = None  # type: ignore[assignment]
    V4l2MjpegCapture = None  # type: ignore[assignment]
    V4l2RawCapture = None  # type: ignore[assignment]
    set_session_bitrate_override_kbps = None  # type: ignore[assignment]
    set_encoder_status_callback = None  # type: ignore[assignment]
    _try_import_cann = None  # type: ignore[assignment]


WEB_DIR = ROOT / "web"
MODELS_DIR = ROOT / "models"
LOG_DIR = ROOT / "logs"
DEFAULT_MODEL_NAME = "YOLOv10n_gestures.om"
DEFAULT_H264_BITRATE_KBPS = 4000
DEFAULT_WEBRTC_INFER_EVERY_N = 1
CAMERA_BACKEND_OPENCV = "opencv"
CAMERA_BACKEND_DVPP = "dvpp"
VIDEO_CLOCK_RATE = 90000
VIDEO_TIME_BASE = fractions.Fraction(1, VIDEO_CLOCK_RATE)
pcs: set[RTCPeerConnection] = set()
pc_tracks: dict[RTCPeerConnection, "YoloOmVideoTrack"] = {}
latest_track_stats: dict[str, object] = {}
latest_stats_lock = threading.Lock()
app_logger = logging.getLogger("webrtc_om")
encoder_state = {
    "hardware_requested": True,
    "hardware_active": False,
    "name": "cpu-libx264-fallback",
    "last_error": "",
}


def no_store_file_response(path: Path) -> web.FileResponse:
    response = web.FileResponse(path)
    response.headers["Cache-Control"] = "no-store"
    return response


def patch_h264_encoder(use_hardware: bool) -> bool:
    encoder_state["hardware_requested"] = bool(use_hardware)
    encoder_state["hardware_active"] = False
    encoder_state["name"] = "cpu-libx264-fallback"
    encoder_state["last_error"] = ""

    if not use_hardware:
        app_logger.info("Hardware H264 encoder disabled; using CPU libx264")
        return False
    if CannH264Encoder is None or _try_import_cann is None:
        app_logger.warning("CANN VENC modules are unavailable; using CPU libx264")
        return False
    if not _try_import_cann():
        app_logger.warning("CANN ACL not available; using CPU libx264")
        return False

    import aiortc.codecs as codecs_module
    import aiortc.codecs.h264 as h264_module
    import aiortc.rtcrtpsender as rtcrtpsender_module

    original_get_encoder = codecs_module.get_encoder

    def get_encoder(codec):
        if codec.mimeType.lower() == "video/h264":
            return CannH264Encoder()
        return original_get_encoder(codec)

    def update_encoder_status(name: str, hardware_active: bool, reason: str = "") -> None:
        encoder_state["name"] = name
        encoder_state["hardware_active"] = bool(hardware_active)
        encoder_state["last_error"] = reason

    if set_encoder_status_callback is not None:
        set_encoder_status_callback(update_encoder_status)

    h264_module.H264Encoder = CannH264Encoder
    codecs_module.H264Encoder = CannH264Encoder
    codecs_module.get_encoder = get_encoder
    rtcrtpsender_module.get_encoder = get_encoder
    encoder_state["hardware_active"] = True
    encoder_state["name"] = "cann-venc-h264"
    app_logger.info("H264 encoder switched to CANN VENC hardware")
    return True


def set_offer_bitrate_override(bitrate_kbps: int | None) -> None:
    if set_session_bitrate_override_kbps is None:
        return
    set_session_bitrate_override_kbps(bitrate_kbps)


def source_to_device_path(source: str) -> str:
    source_text = str(source)
    if source_text.isdigit():
        return f"/dev/video{int(source_text)}"
    return source_text


def nv12_to_bgr(nv12: np.ndarray, width: int, height: int) -> np.ndarray:
    if nv12.ndim != 2:
        raise ValueError(f"NV12 frame must be 2D, got shape={nv12.shape}")
    tight_nv12 = np.empty((height * 3 // 2, width), dtype=np.uint8)
    tight_nv12[:height, :] = nv12[:height, :width]
    tight_nv12[height:, :] = nv12[height:height + height // 2, :width]
    return cv2.cvtColor(tight_nv12, cv2.COLOR_YUV2BGR_NV12)


def bgr_to_nv12(bgr: np.ndarray) -> np.ndarray:
    height, width = bgr.shape[:2]
    if height % 2 != 0 or width % 2 != 0:
        raise ValueError(f"NV12 output requires even width/height, got {width}x{height}")

    yuv_i420 = cv2.cvtColor(bgr, cv2.COLOR_BGR2YUV_I420)
    yuv_flat = yuv_i420.reshape(-1)
    y_size = height * width
    uv_size = y_size // 4

    y = yuv_flat[:y_size].reshape(height, width)
    u = yuv_flat[y_size:y_size + uv_size].reshape(height // 2, width // 2)
    v = yuv_flat[y_size + uv_size:y_size + uv_size * 2].reshape(height // 2, width // 2)

    nv12 = np.empty((height * 3 // 2, width), dtype=np.uint8)
    nv12[:height, :] = y
    uv = nv12[height:, :].reshape(height // 2, width // 2, 2)
    uv[:, :, 0] = u
    uv[:, :, 1] = v
    return nv12


def decode_fourcc(value: float | int) -> str:
    code = int(value or 0)
    if code <= 0:
        return ""
    chars = []
    for shift in (0, 8, 16, 24):
        char = chr((code >> shift) & 0xFF)
        if char.isprintable():
            chars.append(char)
    return "".join(chars)


async def index(_: web.Request) -> web.FileResponse:
    return no_store_file_response(WEB_DIR / "webrtc_index.html")


async def client_js(_: web.Request) -> web.FileResponse:
    return no_store_file_response(WEB_DIR / "webrtc_client.js")


async def styles_css(_: web.Request) -> web.FileResponse:
    return no_store_file_response(WEB_DIR / "webrtc_styles.css")


def list_om_models() -> list[dict[str, object]]:
    models = []
    for path in sorted(MODELS_DIR.glob("*.om")):
        imgsz = resolve_imgsz(path, 0)
        label_path = path.with_name(f"{path.stem}_labels.txt")
        models.append(
            {
                "name": path.name,
                "input": f"{imgsz}x{imgsz}",
                "labels": label_path.name if label_path.exists() else "",
            }
        )
    return models


def resolve_model_path(model_value: str | os.PathLike[str]) -> Path:
    model_path = Path(str(model_value))
    if model_path.is_absolute():
        return model_path
    if model_path.parent == Path("."):
        return MODELS_DIR / model_path
    if model_path.exists():
        return model_path
    return ROOT / model_path


def default_model_name(configured: str | os.PathLike[str] | None = None) -> str:
    if configured:
        configured_path = resolve_model_path(configured)
        if configured_path.exists():
            return configured_path.name
    default_path = MODELS_DIR / DEFAULT_MODEL_NAME
    if default_path.exists():
        return default_path.name
    models = list_om_models()
    if models:
        return str(models[0]["name"])
    return DEFAULT_MODEL_NAME


async def health(request: web.Request) -> web.Response:
    return web.json_response(
        {
            "status": "ok",
            "runtime_target": "ascend-310b",
            "transport": "webrtc",
            "video_codec": "h264",
            "encoder": encoder_state["name"],
            "hardware_encode": bool(encoder_state["hardware_active"]),
            "hardware_encode_requested": bool(encoder_state["hardware_requested"]),
            "encoder_last_error": encoder_state["last_error"],
            "default_model": request.config_dict.get("default_model", DEFAULT_MODEL_NAME),
            "default_source": request.config_dict.get("default_source", "/dev/video0"),
            "default_device_id": request.config_dict.get("device_id", 0),
            "defaults": {
                "width": request.config_dict.get("camera_width", 1280),
                "height": request.config_dict.get("camera_height", 720),
                "fps": request.config_dict.get("camera_fps", 30),
                "infer_every_n": request.config_dict.get("infer_every_n", DEFAULT_WEBRTC_INFER_EVERY_N),
                "conf": request.config_dict.get("conf", 0.25),
                "iou": request.config_dict.get("iou", 0.45),
                "bitrate_kbps": request.config_dict.get("bitrate_kbps", DEFAULT_H264_BITRATE_KBPS),
                "camera_backend": request.config_dict.get("camera_backend", CAMERA_BACKEND_OPENCV),
                "camera_fourcc": request.config_dict.get("camera_fourcc", "MJPG"),
            },
        }
    )


async def models(request: web.Request) -> web.Response:
    items = list_om_models()
    return web.json_response(
        {
            "models": items,
            "default_model": request.config_dict.get("default_model", default_model_name()),
        }
    )


async def stats(_: web.Request) -> web.Response:
    with latest_stats_lock:
        return web.json_response(dict(latest_track_stats))


def parse_positive_int(value: object, name: str, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(text=f"{name} must be an integer.") from exc
    if parsed <= 0:
        raise web.HTTPBadRequest(text=f"{name} must be positive.")
    return parsed


def parse_non_negative_int(value: object, name: str, default: int) -> int:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(text=f"{name} must be an integer.") from exc
    if parsed < 0:
        raise web.HTTPBadRequest(text=f"{name} must be non-negative.")
    return parsed


def parse_float_range(value: object, name: str, default: float, lower: float, upper: float) -> float:
    if value in (None, ""):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise web.HTTPBadRequest(text=f"{name} must be a number.") from exc
    if parsed < lower or parsed > upper:
        raise web.HTTPBadRequest(text=f"{name} must be in [{lower}, {upper}].")
    return parsed


def parse_offer_payload(params: dict[str, object], default_device_id: int = 0) -> dict[str, object]:
    try:
        offer = RTCSessionDescription(sdp=str(params["sdp"]), type=str(params["type"]))
    except KeyError as exc:
        raise web.HTTPBadRequest(text=f"Missing WebRTC offer field: {exc}") from exc

    width = parse_positive_int(params.get("width"), "width", 1280)
    height = parse_positive_int(params.get("height"), "height", 720)
    fps = parse_positive_int(params.get("fps"), "fps", 30)
    infer_every_n = parse_positive_int(
        params.get("infer_every_n"),
        "infer_every_n",
        DEFAULT_WEBRTC_INFER_EVERY_N,
    )
    device_id = parse_non_negative_int(params.get("device_id"), "device_id", default_device_id)
    conf = parse_float_range(params.get("conf"), "conf", 0.25, 0.01, 0.99)
    iou = parse_float_range(params.get("iou"), "iou", 0.45, 0.01, 0.99)

    bitrate_kbps = params.get("bitrate_kbps")
    if bitrate_kbps in (None, "", 0, "0"):
        bitrate_kbps = None
    else:
        bitrate_kbps = parse_positive_int(bitrate_kbps, "bitrate_kbps", 2500)

    model_name = str(params.get("model") or DEFAULT_MODEL_NAME)
    source = str(params.get("source") or "/dev/video0")
    camera_backend = str(params.get("camera_backend") or CAMERA_BACKEND_OPENCV).lower()
    if camera_backend not in {CAMERA_BACKEND_OPENCV, CAMERA_BACKEND_DVPP}:
        raise web.HTTPBadRequest(text="camera_backend must be opencv or dvpp.")
    camera_fourcc = str(params.get("camera_fourcc") or "MJPG").upper()
    if camera_fourcc not in {"MJPG", "YUYV", "DEFAULT"}:
        raise web.HTTPBadRequest(text="camera_fourcc must be MJPG, YUYV, or DEFAULT.")

    return {
        "offer": offer,
        "model_name": model_name,
        "source": source,
        "width": width,
        "height": height,
        "fps": fps,
        "bitrate_kbps": bitrate_kbps,
        "infer_every_n": infer_every_n,
        "conf": conf,
        "iou": iou,
        "device_id": device_id,
        "camera_backend": camera_backend,
        "camera_fourcc": camera_fourcc,
    }


def _offer_has_h264(sdp: str) -> bool:
    return any(
        line.startswith("a=rtpmap:")
        and line.strip().split(None, 1)[-1].split("/", 1)[0].lower() == "h264"
        for line in sdp.splitlines()
    )


def _local_h264_codecs():
    return [
        codec
        for codec in RTCRtpSender.getCapabilities("video").codecs
        if codec.mimeType.lower() == "video/h264"
    ]


def _prefer_h264_for_sender(pc: RTCPeerConnection, sender: RTCRtpSender) -> None:
    codecs = _local_h264_codecs()
    if not codecs:
        raise web.HTTPBadRequest(text="No local video/H264 codec capability found.")

    for transceiver in pc.getTransceivers():
        if transceiver.sender == sender:
            transceiver.setCodecPreferences(codecs)
            app_logger.info("Video transceiver codec preference set to video/H264")
            return
    raise web.HTTPInternalServerError(text="Could not find sender transceiver.")


def estimate_h264_bitrate_kbps(width: int, height: int, fps: int) -> int:
    bitrate = round(width * height * fps * 0.04 / 1000)
    return max(500, min(bitrate, 10000))


def set_video_bitrate_in_sdp(sdp: str, bitrate_kbps: int) -> str:
    lines = sdp.splitlines()
    output: list[str] = []
    in_video = False
    inserted = False

    for line in lines:
        if line.startswith("m="):
            if in_video and not inserted:
                output.append(f"b=AS:{bitrate_kbps}")
            in_video = line.startswith("m=video")
            inserted = False
            output.append(line)
            continue

        if in_video and line.startswith("b=AS:"):
            if not inserted:
                output.append(f"b=AS:{bitrate_kbps}")
                inserted = True
            continue

        output.append(line)
        if in_video and not inserted and line.startswith("c="):
            output.append(f"b=AS:{bitrate_kbps}")
            inserted = True

    if in_video and not inserted:
        output.append(f"b=AS:{bitrate_kbps}")
    return "\r\n".join(output) + "\r\n"


class YoloOmVideoTrack(MediaStreamTrack):
    kind = "video"

    def __init__(
        self,
        model_path: Path,
        source: str,
        width: int,
        height: int,
        fps: int,
        conf: float,
        iou: float,
        infer_every_n: int,
        device_id: int,
        camera_backend: str = CAMERA_BACKEND_OPENCV,
        camera_fourcc: str = "MJPG",
    ) -> None:
        super().__init__()
        self.model_path = model_path
        self.source = source
        self.requested_width = width
        self.requested_height = height
        self.requested_fps = fps
        self.width = width
        self.height = height
        self.fps = fps
        self.conf = conf
        self.iou = iou
        self.infer_every_n = max(1, infer_every_n)
        self.device_id = device_id
        self.camera_backend = camera_backend
        self.camera_fourcc = camera_fourcc
        self.imgsz = resolve_imgsz(model_path, 0)
        self.labels = load_labels(model_path)
        self.backend: Optional[AclModel] = None
        self.runtime: Optional[AclRuntime] = None
        self.detector: Optional[YoloDetector] = None
        self.cap: Optional[cv2.VideoCapture] = None
        self.capture_impl = None
        self.jpegd = None
        self._actual_fourcc = ""
        self._decode_log_count = 0
        self._start: float | None = None
        self._timestamp = 0
        self._frame_time = 1 / max(self.fps, 1)
        self._frame_index = 0
        self._closed = False
        self._state_lock = threading.Lock()
        self._resource_lock = threading.Lock()
        self._capture_stop = threading.Event()
        self._infer_stop = threading.Event()
        self._render_stop = threading.Event()
        self._capture_thread: Optional[threading.Thread] = None
        self._infer_thread: Optional[threading.Thread] = None
        self._render_thread: Optional[threading.Thread] = None
        self._latest_frame: Optional[np.ndarray] = None
        self._latest_frame_id = 0
        self._last_sent_frame_id = 0
        self._latest_frame_time = 0.0
        self._latest_output_frame: Optional[np.ndarray] = None
        self._latest_output_frame_id = 0
        self._latest_output_time = 0.0
        self._last_infer_frame_id = 0
        self._capture_error = ""
        self._infer_error = ""
        self._render_error = ""
        self._infer_queue: queue.Queue[tuple[int, np.ndarray]] = queue.Queue(maxsize=1)
        self._render_queue: queue.Queue[tuple[int, np.ndarray]] = queue.Queue(maxsize=1)
        self._last_detections = []
        self._last_latency_ms = 0.0
        self._last_infer_total_ms = 0.0
        self._last_pipeline_ms = 0.0
        self._last_nv12_ms = 0.0
        self._last_send_wait_ms = 0.0
        self._last_capture_ms = 0.0
        self._capture_fps = 0.0
        self._infer_fps = 0.0
        self._render_fps = 0.0
        self._last_frame_t: float | None = None
        self._last_display_fps = 0.0
        self._fps_log_start = time.perf_counter()
        self._fps_log_frames = 0
        self._capture_fps_start = time.perf_counter()
        self._capture_fps_frames = 0
        self._infer_fps_start = time.perf_counter()
        self._infer_fps_frames = 0
        self._render_fps_start = time.perf_counter()
        self._render_fps_frames = 0
        self._render_queue_drops = 0
        try:
            self._open()
        except Exception:
            self._cleanup()
            raise

    def _open(self) -> None:
        if not self.model_path.exists():
            raise FileNotFoundError(f"OM model not found: {self.model_path}")

        self.runtime = AclRuntime(device_id=self.device_id, finalize_on_release=False)
        self.backend = AclModel(self.model_path, runtime=self.runtime)
        self.detector = YoloDetector(self.backend, imgsz=self.imgsz, conf=self.conf, iou=self.iou)

        if self.camera_backend == CAMERA_BACKEND_DVPP:
            self._open_dvpp_camera()
        else:
            self._open_opencv_camera()

        self._start_workers()
        self._wait_for_first_output(timeout=5.0)

        app_logger.info(
            "Opened WebRTC YOLO track model=%s source=%s capture=%sx%s@%s model_input=%sx%s "
            "conf=%.2f iou=%.2f infer_every=%s camera_backend=%s fourcc=%s",
            self.model_path.name,
            self.source,
            self.width,
            self.height,
            self.fps,
            self.imgsz,
            self.imgsz,
            self.conf,
            self.iou,
            self.infer_every_n,
            self.camera_backend,
            self.camera_fourcc,
        )

    def _open_opencv_camera(self) -> None:
        source = get_source(self.source)
        self.cap = cv2.VideoCapture(source)
        if not self.cap.isOpened():
            devices = available_video_devices()
            device_text = ", ".join(devices) if devices else "none"
            raise RuntimeError(
                f"Cannot open video source: {self.source}. Available video devices: {device_text}"
            )

        if self.camera_fourcc and self.camera_fourcc != "DEFAULT":
            fourcc = cv2.VideoWriter_fourcc(*self.camera_fourcc[:4])
            self.cap.set(cv2.CAP_PROP_FOURCC, fourcc)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, int(self.width))
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, int(self.height))
        self.cap.set(cv2.CAP_PROP_FPS, int(self.fps))

        actual_width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH) or self.width)
        actual_height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or self.height)
        actual_fps = int(round(self.cap.get(cv2.CAP_PROP_FPS) or self.fps))
        self._actual_fourcc = decode_fourcc(self.cap.get(cv2.CAP_PROP_FOURCC))
        if actual_width > 0 and actual_height > 0:
            self.width = actual_width
            self.height = actual_height
        if actual_fps > 0:
            self.fps = actual_fps
            self._frame_time = 1 / max(self.fps, 1)
        app_logger.info(
            "OpenCV camera applied source=%s size=%sx%s fps=%s requested_fourcc=%s actual_fourcc=%s",
            self.source,
            self.width,
            self.height,
            self.fps,
            self.camera_fourcc,
            self._actual_fourcc or "unknown",
        )

    def _open_dvpp_camera(self) -> None:
        if DvppJpegDecoder is None or (V4l2RawCapture is None and V4l2MjpegCapture is None):
            raise RuntimeError("DVPP camera backend is unavailable: missing webrtc_app DVPP/V4L2 modules")

        device_path = source_to_device_path(self.source)
        capture_impl = None
        raw_error = None
        if V4l2RawCapture is not None:
            try:
                capture_impl = V4l2RawCapture(
                    device=device_path,
                    width=self.width,
                    height=self.height,
                    fps=self.fps,
                )
                capture_impl.start()
                app_logger.info("Using direct V4L2 MJPEG capture backend for DVPP")
            except Exception as exc:
                raw_error = exc
                capture_impl = None
                app_logger.warning("Direct V4L2 MJPEG capture failed: %s", exc)

        if capture_impl is None and V4l2MjpegCapture is not None:
            try:
                capture_impl = V4l2MjpegCapture(
                    device=device_path,
                    width=self.width,
                    height=self.height,
                    fps=self.fps,
                )
                capture_impl.start()
                app_logger.info("Using PyAV V4L2 MJPEG capture backend for DVPP")
            except Exception as exc:
                raise RuntimeError(
                    f"DVPP camera backend failed to open MJPEG camera {device_path}: "
                    f"raw_v4l2_error={raw_error}; pyav_error={exc}"
                ) from exc

        if capture_impl is None:
            raise RuntimeError(f"DVPP camera backend failed to open MJPEG camera {device_path}: {raw_error}")

        try:
            self.jpegd = DvppJpegDecoder()
        except Exception:
            capture_impl.stop()
            raise

        self.capture_impl = capture_impl
        self._actual_fourcc = "MJPG"
        actual_width = int(getattr(capture_impl, "width", self.width) or self.width)
        actual_height = int(getattr(capture_impl, "height", self.height) or self.height)
        if actual_width > 0 and actual_height > 0:
            self.width = actual_width
            self.height = actual_height

    def describe_settings(self, bitrate_kbps: int | None = None) -> dict[str, object]:
        return {
            "source": self.source,
            "model": self.model_path.name,
            "model_input": f"{self.imgsz}x{self.imgsz}",
            "encoder": encoder_state["name"],
            "requested": {
                "width": self.requested_width,
                "height": self.requested_height,
                "fps": self.requested_fps,
            },
            "applied": {
                "width": self.width,
                "height": self.height,
                "fps": self.fps,
                "mode": f"{self.camera_backend}-camera+acl-om+h264-webrtc",
                "bitrate_kbps": bitrate_kbps,
                "infer_every_n": self.infer_every_n,
                "camera_backend": self.camera_backend,
                "camera_fourcc": self.camera_fourcc,
                "actual_fourcc": self._actual_fourcc,
            },
        }

    def snapshot_stats(self) -> dict[str, object]:
        with self._state_lock:
            return self._snapshot_stats_unlocked()

    def _snapshot_stats_unlocked(self) -> dict[str, object]:
        frame_age_ms = 0.0
        if self._latest_frame_time:
            frame_age_ms = max(0.0, (time.time() - self._latest_frame_time) * 1000.0)
        return {
            "source": self.source,
            "model": self.model_path.name,
            "model_input": f"{self.imgsz}x{self.imgsz}",
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "track_fps": round(self._last_display_fps, 1),
            "capture_fps": round(self._capture_fps, 1),
            "infer_fps": round(self._infer_fps, 1),
            "render_fps": round(self._render_fps, 1),
            "npu_latency_ms": round(float(self._last_latency_ms), 1),
            "infer_total_ms": round(float(self._last_infer_total_ms), 1),
            "capture_ms": round(float(self._last_capture_ms), 1),
            "pipeline_ms": round(float(self._last_pipeline_ms), 1),
            "nv12_ms": round(float(self._last_nv12_ms), 1),
            "send_wait_ms": round(float(self._last_send_wait_ms), 1),
            "frame_age_ms": round(frame_age_ms, 1),
            "infer_every_n": self.infer_every_n,
            "detections": len(self._last_detections),
            "camera_backend": self.camera_backend,
            "camera_fourcc": self.camera_fourcc,
            "actual_fourcc": self._actual_fourcc,
            "encoder": encoder_state["name"],
            "frame_index": self._frame_index,
            "latest_frame_id": self._latest_frame_id,
            "last_sent_frame_id": self._last_sent_frame_id,
            "latest_output_frame_id": self._latest_output_frame_id,
            "last_infer_frame_id": self._last_infer_frame_id,
            "capture_error": self._capture_error,
            "infer_error": self._infer_error,
            "render_error": self._render_error,
            "render_queue_drops": self._render_queue_drops,
            "async_pipeline": True,
            "updated_at": time.time(),
        }

    def _publish_stats(self) -> None:
        with self._state_lock:
            self._publish_stats_unlocked()

    def _publish_stats_unlocked(self) -> None:
        with latest_stats_lock:
            latest_track_stats.clear()
            latest_track_stats.update(self._snapshot_stats_unlocked())

    def _wait_for_first_output(self, timeout: float) -> None:
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            with self._state_lock:
                if self._latest_output_frame is not None:
                    return
                if self._capture_error:
                    raise RuntimeError(f"{self.camera_backend} camera capture failed: {self._capture_error}")
                if self._render_error:
                    raise RuntimeError(f"{self.camera_backend} frame render failed: {self._render_error}")
                if self._infer_error:
                    raise RuntimeError(f"{self.camera_backend} inference failed: {self._infer_error}")
            time.sleep(0.01)
        raise RuntimeError(
            f"{self.camera_backend} camera backend did not produce a frame within {timeout:.1f}s"
        )

    def _read_opencv_frame(self):
        if self.cap is None:
            return None
        ok, frame = self.cap.read()
        if not ok:
            return None
        return frame

    def _read_dvpp_frame(self):
        if self.capture_impl is None or self.jpegd is None:
            return None
        jpeg_bytes = self.capture_impl.read(timeout=2.0)
        t0 = time.perf_counter()
        nv12_flat = self.jpegd.decode(jpeg_bytes)
        nv12 = nv12_flat.reshape(self.jpegd.nv12_shape)
        decoded_width = int(getattr(self.jpegd, "width", 0) or self.width)
        decoded_height = int(getattr(self.jpegd, "height", 0) or self.height)
        if decoded_width > 0 and decoded_height > 0:
            with self._state_lock:
                self.width = decoded_width
                self.height = decoded_height
        if self._decode_log_count < 5:
            app_logger.info(
                "DVPP JPEGD decode frame=%d size=%sx%s bytes=%d decode_ms=%.1f",
                self._decode_log_count + 1,
                decoded_width,
                decoded_height,
                len(jpeg_bytes),
                (time.perf_counter() - t0) * 1000,
            )
            self._decode_log_count += 1
        return nv12_to_bgr(nv12, decoded_width, decoded_height)

    def _read_bgr_frame(self):
        if self.camera_backend == CAMERA_BACKEND_DVPP:
            return self._read_dvpp_frame()
        return self._read_opencv_frame()

    def _start_workers(self) -> None:
        self._infer_thread = threading.Thread(
            target=self._infer_loop,
            name="case8-webrtc-infer",
            daemon=True,
        )
        self._render_thread = threading.Thread(
            target=self._render_loop,
            name="case8-webrtc-render",
            daemon=True,
        )
        self._capture_thread = threading.Thread(
            target=self._capture_loop,
            name="case8-webrtc-capture",
            daemon=True,
        )
        self._infer_thread.start()
        self._render_thread.start()
        self._capture_thread.start()
        app_logger.info("WebRTC capture/inference/render workers started")

    def _capture_loop(self) -> None:
        app_logger.info("Capture worker started camera_backend=%s", self.camera_backend)
        try:
            while not self._capture_stop.is_set():
                t0 = time.perf_counter()
                try:
                    frame = self._read_bgr_frame()
                except queue.Empty:
                    continue
                capture_ms = (time.perf_counter() - t0) * 1000.0
                if frame is None:
                    if not self._capture_stop.is_set():
                        time.sleep(0.005)
                    continue

                height, width = frame.shape[:2]
                submit_infer = False
                frame_id = 0
                now = time.perf_counter()
                with self._state_lock:
                    if self._closed:
                        break
                    self._latest_frame = frame
                    self._latest_frame_id += 1
                    frame_id = self._latest_frame_id
                    self._latest_frame_time = time.time()
                    self._last_capture_ms = capture_ms
                    if width > 0 and height > 0:
                        self.width = int(width)
                        self.height = int(height)
                    self._capture_error = ""
                    self._capture_fps_frames += 1
                    elapsed = now - self._capture_fps_start
                    if elapsed >= 1.0:
                        self._capture_fps = self._capture_fps_frames / elapsed
                        self._capture_fps_frames = 0
                        self._capture_fps_start = now
                    submit_infer = frame_id % self.infer_every_n == 0
                    self._publish_stats_unlocked()

                if submit_infer:
                    self._submit_infer_frame(frame_id, frame)
                self._submit_render_frame(frame_id, frame)
        except Exception as exc:
            with self._state_lock:
                self._capture_error = str(exc)
                self._publish_stats_unlocked()
            if not self._capture_stop.is_set():
                app_logger.exception("Capture worker failed")
        finally:
            app_logger.info("Capture worker stopped")

    def _submit_infer_frame(self, frame_id: int, frame: np.ndarray) -> None:
        if self.detector is None or self._infer_stop.is_set():
            return

        if self._infer_queue.full():
            self._drop_oldest_queue_item(self._infer_queue)

        item = (frame_id, frame.copy())
        try:
            self._infer_queue.put_nowait(item)
            return
        except queue.Full:
            pass

    def _drop_oldest_queue_item(self, item_queue: queue.Queue) -> bool:
        try:
            item_queue.get_nowait()
            return True
        except queue.Empty:
            return False

    def _submit_render_frame(self, frame_id: int, frame: np.ndarray) -> None:
        if self._render_stop.is_set():
            return

        item = (frame_id, frame)
        try:
            self._render_queue.put_nowait(item)
            return
        except queue.Full:
            pass

        dropped = self._drop_oldest_queue_item(self._render_queue)
        if dropped:
            with self._state_lock:
                self._render_queue_drops += 1

        try:
            self._render_queue.put_nowait(item)
        except queue.Full:
            pass

    def _infer_loop(self) -> None:
        app_logger.info("Inference worker started")
        try:
            while not self._infer_stop.is_set():
                try:
                    frame_id, frame = self._infer_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                if self._infer_stop.is_set():
                    break
                detector = self.detector
                if detector is None:
                    continue

                t0 = time.perf_counter()
                try:
                    detections, latency_ms = detector.infer_frame(frame)
                except Exception as exc:
                    with self._state_lock:
                        self._infer_error = str(exc)
                        self._publish_stats_unlocked()
                    app_logger.exception("Inference worker failed")
                    break

                infer_total_ms = (time.perf_counter() - t0) * 1000.0
                now = time.perf_counter()
                with self._state_lock:
                    if self._closed:
                        break
                    self._last_detections = detections
                    self._last_latency_ms = latency_ms
                    self._last_infer_total_ms = infer_total_ms
                    self._last_infer_frame_id = frame_id
                    self._infer_error = ""
                    self._infer_fps_frames += 1
                    elapsed = now - self._infer_fps_start
                    if elapsed >= 1.0:
                        self._infer_fps = self._infer_fps_frames / elapsed
                        self._infer_fps_frames = 0
                        self._infer_fps_start = now
                    self._publish_stats_unlocked()
        finally:
            app_logger.info("Inference worker stopped")

    def _render_loop(self) -> None:
        app_logger.info("Render worker started")
        try:
            while not self._render_stop.is_set():
                try:
                    frame_id, frame = self._render_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                if self._render_stop.is_set():
                    break

                t0 = time.perf_counter()
                with self._state_lock:
                    detections = list(self._last_detections)
                draw_detections(frame, detections, self.labels)

                t1 = time.perf_counter()
                try:
                    nv12 = bgr_to_nv12(frame)
                except Exception as exc:
                    with self._state_lock:
                        self._render_error = str(exc)
                        self._publish_stats_unlocked()
                    app_logger.exception("Render worker failed")
                    break

                nv12_ms = (time.perf_counter() - t1) * 1000.0
                pipeline_ms = (time.perf_counter() - t0) * 1000.0
                now = time.perf_counter()
                with self._state_lock:
                    if self._closed:
                        break
                    self._latest_output_frame = nv12
                    self._latest_output_frame_id = frame_id
                    self._latest_output_time = time.time()
                    self._last_nv12_ms = nv12_ms
                    self._last_pipeline_ms = pipeline_ms
                    self._render_error = ""
                    self._render_fps_frames += 1
                    elapsed = now - self._render_fps_start
                    if elapsed >= 1.0:
                        self._render_fps = self._render_fps_frames / elapsed
                        self._render_fps_frames = 0
                        self._render_fps_start = now
                    self._publish_stats_unlocked()
        finally:
            app_logger.info("Render worker stopped")

    def _copy_latest_output_frame(self, timeout: float = 2.0):
        t0 = time.perf_counter()
        deadline = time.perf_counter() + timeout
        while not self._render_stop.is_set():
            frame = None
            with self._state_lock:
                if self._closed:
                    return None
                if self._capture_error:
                    raise RuntimeError(f"camera capture failed: {self._capture_error}")
                if self._render_error:
                    raise RuntimeError(f"frame render failed: {self._render_error}")
                if (
                    self._latest_output_frame is not None
                    and self._latest_output_frame_id != self._last_sent_frame_id
                ):
                    self._last_sent_frame_id = self._latest_output_frame_id
                    self._last_send_wait_ms = (time.perf_counter() - t0) * 1000.0
                    frame = self._latest_output_frame
            if frame is not None:
                return frame
            if time.perf_counter() >= deadline:
                return None
            time.sleep(0.005)
        return None

    def _read_output_frame(self):
        frame = self._copy_latest_output_frame()
        if frame is None:
            return None
        now = time.perf_counter()
        log_fps = None
        with self._state_lock:
            display_fps = 0.0
            if self._last_frame_t is not None:
                display_fps = 1.0 / max(now - self._last_frame_t, 1e-6)
            self._last_frame_t = now
            self._last_display_fps = display_fps
            self._frame_index += 1
            self._fps_log_frames += 1
            if self._fps_log_frames >= 150:
                elapsed = now - self._fps_log_start
                if elapsed > 0:
                    log_fps = self._fps_log_frames / elapsed
                self._fps_log_frames = 0
                self._fps_log_start = now
            self._publish_stats_unlocked()
        if log_fps is not None:
            app_logger.info("WebRTC track FPS %.1f", log_fps)
        return frame

    async def next_timestamp(self) -> tuple[int, fractions.Fraction]:
        if self.readyState != "live":
            raise MediaStreamError

        if self._start is None:
            self._start = time.time()
            self._timestamp = 0
        else:
            self._timestamp += int(self._frame_time * VIDEO_CLOCK_RATE)
            wait = self._start + (self._timestamp / VIDEO_CLOCK_RATE) - time.time()
            if wait > 0:
                await asyncio.sleep(wait)
        return self._timestamp, VIDEO_TIME_BASE

    async def recv(self) -> av.VideoFrame:
        pts, time_base = await self.next_timestamp()
        loop = asyncio.get_running_loop()
        try:
            frame = await loop.run_in_executor(None, self._read_output_frame)
        except RuntimeError as exc:
            raise MediaStreamError(str(exc)) from exc
        if frame is None:
            raise MediaStreamError("Camera read returned no frame")

        video_frame = av.VideoFrame.from_ndarray(frame, format="nv12")
        video_frame.pts = pts
        video_frame.time_base = time_base
        return video_frame

    def _cleanup(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            self._closed = True
            self._publish_stats_unlocked()

        self._capture_stop.set()
        self._infer_stop.set()
        self._render_stop.set()

        if self._capture_thread is not None and self._capture_thread.is_alive():
            self._capture_thread.join(timeout=3.0)
            if self._capture_thread.is_alive():
                app_logger.warning("Capture worker did not stop before timeout")
        if self._render_thread is not None and self._render_thread.is_alive():
            self._render_thread.join(timeout=3.0)
            if self._render_thread.is_alive():
                app_logger.warning("Render worker did not stop before timeout")
        infer_still_running = False
        if self._infer_thread is not None and self._infer_thread.is_alive():
            self._infer_thread.join(timeout=5.0)
            infer_still_running = self._infer_thread.is_alive()
            if infer_still_running:
                app_logger.warning("Inference worker did not stop before timeout")

        with self._resource_lock:
            if self.cap is not None:
                self.cap.release()
                self.cap = None
            if self.capture_impl is not None:
                self.capture_impl.stop()
                self.capture_impl = None
            if self.jpegd is not None:
                self.jpegd.destroy()
                self.jpegd = None
            if infer_still_running:
                app_logger.warning("Skipping OM backend release because inference worker is still active")
            elif self.backend is not None:
                self.backend.release()
                self.backend = None
            if not infer_still_running and self.runtime is not None:
                self.runtime.release()
                self.runtime = None

        while True:
            try:
                self._infer_queue.get_nowait()
            except queue.Empty:
                break
        while True:
            try:
                self._render_queue.get_nowait()
            except queue.Empty:
                break

    def stop(self) -> None:
        with self._state_lock:
            if self._closed:
                return
            model_name = self.model_path.name
        app_logger.info("Stopping WebRTC YOLO track model=%s", model_name)
        self._cleanup()
        try:
            super().stop()
        except Exception:
            return


async def offer(request: web.Request) -> web.Response:
    params = parse_offer_payload(
        await request.json(),
        default_device_id=int(request.config_dict.get("device_id", 0)),
    )
    offer_sdp: RTCSessionDescription = params["offer"]  # type: ignore[assignment]
    if not _offer_has_h264(offer_sdp.sdp):
        raise web.HTTPBadRequest(text="Browser offer does not contain video/H264.")

    if pcs:
        app_logger.info("Closing %s stale peer connection(s) before new offer", len(pcs))
        await asyncio.gather(
            *[close_peer_connection(pc) for pc in list(pcs)],
            return_exceptions=True,
        )
        pcs.clear()
        await asyncio.sleep(0.3)

    model_path = resolve_model_path(str(params["model_name"]))
    bitrate_kbps = params["bitrate_kbps"]
    if bitrate_kbps is None:
        bitrate_kbps = estimate_h264_bitrate_kbps(
            int(params["width"]),
            int(params["height"]),
            int(params["fps"]),
        )

    pc = RTCPeerConnection()
    pcs.add(pc)
    track: Optional[YoloOmVideoTrack] = None
    connect_timeout_task: Optional[asyncio.Task] = None

    try:
        track = YoloOmVideoTrack(
            model_path=model_path,
            source=str(params["source"]),
            width=int(params["width"]),
            height=int(params["height"]),
            fps=int(params["fps"]),
            conf=float(params["conf"]),
            iou=float(params["iou"]),
            infer_every_n=int(params["infer_every_n"]),
            device_id=int(params["device_id"]),
            camera_backend=str(params["camera_backend"]),
            camera_fourcc=str(params["camera_fourcc"]),
        )
        pc_tracks[pc] = track
        sender = pc.addTrack(track)
        _prefer_h264_for_sender(pc, sender)
        set_offer_bitrate_override(int(bitrate_kbps))

        async def close_if_not_connected(timeout: float = 30.0) -> None:
            await asyncio.sleep(timeout)
            if (
                pc in pcs
                and pc.connectionState not in {"connected", "closed"}
                and pc.iceConnectionState not in {"completed", "closed"}
            ):
                app_logger.warning(
                    "PeerConnection %s did not connect within %.1fs; closing stale track",
                    id(pc),
                    timeout,
                )
                await close_peer_connection(pc)

        connect_timeout_task = asyncio.create_task(close_if_not_connected())

        @pc.on("connectionstatechange")
        async def on_connectionstatechange() -> None:
            app_logger.info("PeerConnection %s state -> %s", id(pc), pc.connectionState)
            if pc.connectionState == "connected" and connect_timeout_task is not None:
                connect_timeout_task.cancel()
            if pc.connectionState in {"failed", "closed", "disconnected"}:
                await close_peer_connection(pc)

        @pc.on("iceconnectionstatechange")
        async def on_iceconnectionstatechange() -> None:
            app_logger.info("PeerConnection %s ICE -> %s", id(pc), pc.iceConnectionState)
            if pc.iceConnectionState == "completed" and connect_timeout_task is not None:
                connect_timeout_task.cancel()
            if pc.iceConnectionState in {"failed", "closed", "disconnected"}:
                await close_peer_connection(pc)

        await pc.setRemoteDescription(offer_sdp)
        answer = await pc.createAnswer()
        answer = RTCSessionDescription(
            sdp=set_video_bitrate_in_sdp(answer.sdp, int(bitrate_kbps)),
            type=answer.type,
        )
        await pc.setLocalDescription(answer)
        return web.json_response(
            {
                "sdp": pc.localDescription.sdp,
                "type": pc.localDescription.type,
                "source_settings": track.describe_settings(bitrate_kbps=int(bitrate_kbps)),
            }
        )
    except web.HTTPException:
        if connect_timeout_task is not None:
            connect_timeout_task.cancel()
        raise
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        if connect_timeout_task is not None:
            connect_timeout_task.cancel()
        app_logger.exception("Offer handling failed: %s", exc)
        await close_peer_connection(pc)
        raise web.HTTPBadRequest(text=f"Failed to create WebRTC answer: {exc}") from exc
    except Exception as exc:
        if connect_timeout_task is not None:
            connect_timeout_task.cancel()
        app_logger.exception("Offer handling failed")
        await close_peer_connection(pc)
        raise web.HTTPInternalServerError(
            text=f"Failed to create WebRTC answer: {exc}. Check logs/webrtc_om_app.log."
        ) from exc


async def close_peer_connection(pc: RTCPeerConnection) -> None:
    track = pc_tracks.pop(pc, None)

    if pc in pcs:
        pcs.discard(pc)
        try:
            await pc.close()
        except Exception:
            app_logger.exception("Failed to close PeerConnection %s cleanly", id(pc))

    if track is not None:
        try:
            track.stop()
        except Exception:
            app_logger.exception("Failed to stop source track for PeerConnection %s", id(pc))
    if not pc_tracks:
        with latest_stats_lock:
            latest_track_stats.clear()
    set_offer_bitrate_override(None)


@web.middleware
async def error_logging_middleware(request: web.Request, handler) -> web.StreamResponse:
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception:
        app_logger.exception("Unhandled server error while processing %s %s", request.method, request.path)
        raise web.HTTPInternalServerError(text="Unhandled server error. Check logs/webrtc_om_app.log.")


async def on_shutdown(_: web.Application) -> None:
    app_logger.info("Shutting down server, closing %s peer connections", len(pcs))
    await asyncio.gather(
        *[close_peer_connection(pc) for pc in list(pcs)],
        return_exceptions=True,
    )
    pcs.clear()


def build_app(args: argparse.Namespace) -> web.Application:
    app = web.Application(middlewares=[error_logging_middleware])
    app["default_model"] = default_model_name(args.model)
    app["default_source"] = args.source
    app["device_id"] = args.device_id
    app["camera_width"] = args.camera_width
    app["camera_height"] = args.camera_height
    app["camera_fps"] = args.camera_fps
    app["infer_every_n"] = args.infer_every_n
    app["conf"] = args.conf
    app["iou"] = args.iou
    app["bitrate_kbps"] = args.bitrate_kbps
    app["camera_backend"] = args.camera_backend
    app["camera_fourcc"] = args.camera_fourcc
    app["hardware_encode"] = args.hardware_encode
    app.on_shutdown.append(on_shutdown)
    app.router.add_get("/", index)
    app.router.add_get("/client.js", client_js)
    app.router.add_get("/styles.css", styles_css)
    app.router.add_get("/health", health)
    app.router.add_get("/models", models)
    app.router.add_get("/stats", stats)
    app.router.add_post("/offer", offer)
    return app


def port_is_free(host: str, port: int) -> bool:
    bind_host = "0.0.0.0" if host in ("0.0.0.0", "::") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((bind_host, int(port)))
        except OSError:
            return False
    return True


def choose_port(host: str, start_port: int, port_range: int, strict_port: bool) -> int:
    if strict_port:
        return start_port
    attempts = max(1, int(port_range))
    for offset in range(attempts):
        port = int(start_port) + offset
        if port_is_free(host, port):
            if port != start_port:
                print(f"Port {start_port} is busy. Using {port}.", flush=True)
            return port
    raise OSError(f"Cannot find an empty port in range: {start_port}-{start_port + attempts - 1}")


def local_ipv4_addresses() -> list[str]:
    addresses: list[str] = []
    try:
        hostname_ips = socket.gethostbyname_ex(socket.gethostname())[2]
    except OSError:
        hostname_ips = []

    for ip in hostname_ips:
        if not ip.startswith("127.") and ip not in addresses:
            addresses.append(ip)

    for target in ("8.8.8.8", "1.1.1.1"):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            try:
                sock.connect((target, 80))
                ip = sock.getsockname()[0]
            except OSError:
                continue
        if ip and not ip.startswith("127.") and ip not in addresses:
            addresses.insert(0, ip)

    preferred = [ip for ip in addresses if not ip.startswith("172.")]
    secondary = [ip for ip in addresses if ip.startswith("172.")]
    return preferred + secondary


def print_access_urls(host: str, port: int) -> None:
    hostname = socket.gethostname()
    print("", flush=True)
    print("WebRTC H.264 app is starting. Open one of these URLs:", flush=True)
    if host in ("0.0.0.0", "::"):
        print(f"  http://{hostname}:{port}", flush=True)
        for ip in local_ipv4_addresses():
            print(f"  http://{ip}:{port}", flush=True)
        print(f"  http://127.0.0.1:{port}  (only from this board or SSH port forwarding)", flush=True)
    else:
        print(f"  http://{host}:{port}", flush=True)
    print("", flush=True)


def setup_logging(log_level: str, log_file: str) -> None:
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(getattr(logging, log_level))

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=2 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="WebRTC H.264 OM gesture recognition sender for Ascend 310B.")
    parser.add_argument("-m", "--model", default=MODELS_DIR / DEFAULT_MODEL_NAME, type=Path)
    parser.add_argument("-s", "--source", default="/dev/video0", type=str)
    parser.add_argument("--host", default=os.environ.get("WEBRTC_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("WEBRTC_PORT", "8080")))
    parser.add_argument("--port-range", type=int, default=20)
    parser.add_argument("--strict-port", action="store_true")
    parser.add_argument("--device-id", default=0, type=int)
    parser.add_argument("--camera-width", default=1280, type=int)
    parser.add_argument("--camera-height", default=720, type=int)
    parser.add_argument("--camera-fps", default=30, type=int)
    parser.add_argument("--infer-every-n", default=DEFAULT_WEBRTC_INFER_EVERY_N, type=int)
    parser.add_argument("--conf", default=0.25, type=float)
    parser.add_argument("--iou", default=0.45, type=float)
    parser.add_argument("--bitrate-kbps", default=DEFAULT_H264_BITRATE_KBPS, type=int)
    parser.add_argument("--camera-backend", default=CAMERA_BACKEND_OPENCV, choices=[CAMERA_BACKEND_OPENCV, CAMERA_BACKEND_DVPP])
    parser.add_argument("--camera-fourcc", default="MJPG", choices=["MJPG", "YUYV", "DEFAULT"])
    hardware_group = parser.add_mutually_exclusive_group()
    hardware_group.add_argument("--hardware-encode", dest="hardware_encode", action="store_true", default=True)
    hardware_group.add_argument("--no-hardware-encode", dest="hardware_encode", action="store_false")
    parser.add_argument("--opencv-threads", default=1, type=int)
    parser.add_argument("--log-level", default=os.environ.get("WEBRTC_LOG_LEVEL", "INFO"), choices=["DEBUG", "INFO", "WARNING", "ERROR"])
    parser.add_argument("--log-file", default=os.environ.get("WEBRTC_LOG_FILE", str(LOG_DIR / "webrtc_om_app.log")))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    cv2.setNumThreads(max(1, args.opencv_threads))
    setup_logging(args.log_level, args.log_file)
    patch_h264_encoder(args.hardware_encode)
    args.port = choose_port(args.host, args.port, args.port_range, args.strict_port)
    app_logger.info("Starting WebRTC H.264 OM app on %s:%s", args.host, args.port)
    print_access_urls(args.host, args.port)
    web.run_app(build_app(args), host=args.host, port=args.port, access_log=app_logger)


if __name__ == "__main__":
    main()
