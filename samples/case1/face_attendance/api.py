"""FastAPI boundary for the Case 1 face-attendance sample.

This module intentionally imports no camera, OpenCV, ACL, or model classes at
module load time.  That keeps local documentation builds and API contract
tests usable when only the board has the inference dependencies.
"""

import base64
import binascii
import asyncio
import json
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable, Optional

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from . import database
from .config import CASE_ROOT, UPLOAD_DIR
from .runtime import (
    CameraUnavailableError,
    FaceAttendanceRuntime,
    RuntimeNotReadyError,
)


FRONTEND_DIST = CASE_ROOT / "frontend" / "dist"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp"}


def _default_image_decoder(data: bytes):
    """Decode bytes only when an endpoint actually receives an image."""

    try:
        import cv2
        import numpy as np
    except ImportError as exc:  # pragma: no cover - board/runtime dependency
        raise RuntimeNotReadyError("OpenCV 不可用") from exc
    array = np.frombuffer(data, dtype=np.uint8)
    return cv2.imdecode(array, cv2.IMREAD_COLOR)


def _encode_image(image):
    if isinstance(image, bytes):
        return image
    try:
        import cv2
    except ImportError as exc:  # pragma: no cover - board/runtime dependency
        raise RuntimeNotReadyError("OpenCV 不可用") from exc
    encoded, data = cv2.imencode(".jpg", image)
    if not encoded:
        raise ValueError("图像编码失败")
    return data.tobytes()


def _safe_path(root: Path, relative_name: str) -> Optional[Path]:
    """Resolve a user-supplied relative path without traversal."""

    if not relative_name or "\x00" in relative_name:
        return None
    root_resolved = root.resolve()
    candidate = (root / relative_name).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError:
        return None
    return candidate


def _json_error(message: str, status_code: int):
    return JSONResponse({"error": message}, status_code=status_code)


def _public_error(detail: Any, fallback: str) -> str:
    """Keep paths and runtime internals out of the HTTP contract."""

    text = str(detail or "").strip()
    unsafe_markers = ("/", "\\", ".om", "Traceback", "acl.", "Errno")
    if not text or len(text) > 160 or any(marker in text for marker in unsafe_markers):
        return fallback
    return text


async def _read_form(request: Request):
    try:
        return await request.form()
    except (AssertionError, RuntimeError, ValueError):
        # A missing python-multipart package or a non-form body should result
        # in the same 400 contract as an omitted image, not an import crash.
        return {}


async def _read_upload(value):
    if value is None or not hasattr(value, "read"):
        return None, None
    filename = getattr(value, "filename", "") or ""
    if not filename:
        return None, None
    chunks = []
    total = 0
    while True:
        chunk = await value.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_IMAGE_BYTES:
            raise ValueError("图片文件超过 8 MiB 限制")
        chunks.append(chunk)
    return b"".join(chunks), filename


def _decode_base64(value: Any):
    if not isinstance(value, str) or not value:
        return None
    if "," in value:
        value = value.split(",", 1)[1]
    if len(value) > ((MAX_IMAGE_BYTES + 2) * 4 // 3 + 4):
        return None
    try:
        raw = base64.b64decode(value, validate=True)
        if len(raw) > MAX_IMAGE_BYTES:
            return None
        return raw
    except (ValueError, binascii.Error):
        return None


def _read_bounded_file(path: Path) -> bytes:
    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError("图片文件超过 8 MiB 限制")
    return path.read_bytes()


def _frontend_response(path: str, frontend_dist: Path):
    """Serve a built React asset, with index fallback for SPA routes."""

    index = frontend_dist / "index.html"
    if index.is_file():
        if path:
            asset = _safe_path(frontend_dist, path)
            if asset is not None and asset.is_file():
                return FileResponse(str(asset))
            # A request for a missing file must not be turned into the SPA
            # shell.  This also prevents a normalized traversal request from
            # receiving a misleading 200 response.
            if Path(path).suffix:
                return None
        return FileResponse(str(index), media_type="text/html")
    return None


def create_app(
    runtime=None,
    image_decoder: Optional[Callable[[bytes], Any]] = None,
    template_dir: Optional[Path] = None,
    upload_dir: Optional[Path] = None,
    frontend_dist: Optional[Path] = None,
):
    """Create the ASGI application without opening hardware resources."""

    uploads = Path(upload_dir or UPLOAD_DIR)
    app_runtime = runtime or FaceAttendanceRuntime(upload_dir=uploads)
    # ``template_dir`` remains accepted for callers upgrading from the Flask
    # sample, but is intentionally ignored: the runtime serves only the
    # compiled React application.
    _ = template_dir
    frontend_root = Path(frontend_dist or FRONTEND_DIST)
    decoder = image_decoder or _default_image_decoder

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        database.init_db()
        app.state.runtime.start()
        try:
            yield
        finally:
            app.state.runtime.stop()

    app = FastAPI(
        title="Case 1 Face Attendance",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.runtime = app_runtime
    app.state.upload_dir = uploads
    app.state.frontend_dist = frontend_root
    app.state.image_decoder = decoder

    def page(name: str):
        del name
        frontend = _frontend_response("", app.state.frontend_dist)
        if frontend is not None:
            return frontend
        return _json_error("前端尚未构建", 503)

    def require_runtime_ready():
        runtime_state = app.state.runtime
        if bool(getattr(runtime_state, "ready", False)):
            return None
        message = getattr(runtime_state, "readiness_error", None) or "人脸系统未初始化"
        return _json_error(_public_error(message, "人脸系统暂不可用"), 503)

    def require_camera_ready():
        runtime_state = app.state.runtime
        if bool(getattr(runtime_state, "camera_ready", False)):
            return None
        message = getattr(runtime_state, "readiness_error", None) or "摄像头不可用"
        return _json_error(_public_error(message, "摄像头暂不可用"), 503)

    @app.get("/api/health")
    async def health():
        runtime_state = app.state.runtime
        ready = bool(getattr(runtime_state, "ready", False))
        camera_ready = bool(getattr(runtime_state, "camera_ready", False))
        error = getattr(runtime_state, "readiness_error", None)
        return {
            "status": "ok" if ready and camera_ready else "degraded",
            "ready": ready,
            "camera_ready": camera_ready,
            "error": _public_error(error, "运行时暂不可用") if error else None,
        }

    @app.get("/uploads/{filename:path}")
    async def uploaded_file(filename: str):
        path = _safe_path(app.state.upload_dir, filename)
        if path is None or not path.is_file():
            return _json_error("文件不存在", 404)
        return FileResponse(str(path))

    @app.get("/")
    async def index():
        return page("index.html")

    @app.get("/users_page")
    async def users_page():
        return page("users.html")

    @app.get("/attendance_page")
    async def attendance_page():
        return page("attendance.html")

    @app.get("/api/users")
    async def list_users():
        try:
            result = []
            for user in database.get_users():
                row = dict(user)
                row.pop("embedding", None)
                result.append(row)
            return result
        except Exception as exc:
            print(f"获取用户列表失败: {exc}")
            return _json_error("用户数据暂不可用", 503)

    @app.post("/api/camera/capture")
    async def capture_from_device():
        unavailable = require_camera_ready()
        if unavailable is not None:
            return unavailable
        try:
            frame = app.state.runtime.capture_snapshot()
        except CameraUnavailableError as exc:
            return _json_error(_public_error(exc, "摄像头暂不可用"), 503)
        if frame is None:
            return _json_error("摄像头尚未产生画面", 503)
        try:
            uploads.mkdir(parents=True, exist_ok=True)
            filename = f"capture_{time.time_ns()}.jpg"
            path = uploads / filename
            path.write_bytes(_encode_image(frame))
        except RuntimeNotReadyError as exc:
            return _json_error(_public_error(exc, "抓拍服务暂不可用"), 503)
        except Exception as exc:
            print(f"保存抓拍失败: {exc}")
            return _json_error("抓拍保存失败", 500)
        return {"success": True, "temp_path": filename}

    @app.post("/api/users")
    async def add_user(request: Request):
        unavailable = require_runtime_ready()
        if unavailable is not None:
            return unavailable
        form = await _read_form(request)
        name = form.get("name") if hasattr(form, "get") else None
        if not isinstance(name, str) or not name.strip():
            return _json_error("姓名不能为空", 400)
        image = None
        upload_value = form.get("image") if hasattr(form, "get") else None
        upload_data = None
        if upload_value is not None:
            content_type = getattr(upload_value, "content_type", None)
            if content_type not in ALLOWED_IMAGE_TYPES:
                return _json_error("仅支持 JPEG、PNG 或 WebP 图片", 400)
            try:
                upload_data, _ = await _read_upload(upload_value)
            except ValueError as exc:
                return _json_error(str(exc), 400)

        if upload_data is not None:
            try:
                image = app.state.image_decoder(upload_data)
            except Exception as exc:
                print(f"上传图像解码失败: {exc}")
                return _json_error("图像解码失败", 400)
        elif hasattr(form, "get") and form.get("temp_path"):
            temp_path = _safe_path(app.state.upload_dir, str(form.get("temp_path")))
            if temp_path is None or not temp_path.is_file():
                return _json_error("抓拍文件未找到", 400)
            try:
                image = app.state.image_decoder(_read_bounded_file(temp_path))
            except Exception as exc:
                print(f"抓拍图像解码失败: {exc}")
                return _json_error("图像解码失败", 400)
        elif hasattr(form, "get") and form.get("image_base64"):
            raw = _decode_base64(form.get("image_base64"))
            if raw is None:
                return _json_error("无效的图片", 400)
            try:
                image = app.state.image_decoder(raw)
            except Exception as exc:
                print(f"Base64 图像解码失败: {exc}")
                return _json_error("图像解码失败", 400)
        else:
            return _json_error("未提供图片", 400)

        if image is None:
            return _json_error("无效的图片", 400)
        try:
            user_id = app.state.runtime.register_user(name, image)
        except RuntimeNotReadyError as exc:
            return _json_error(_public_error(exc, "人脸系统暂不可用"), 503)
        except Exception as exc:
            print(f"添加用户失败: {exc}")
            return _json_error("用户注册失败", 500)
        return {"success": True, "user_id": user_id}

    @app.delete("/api/users/{user_id}")
    async def delete_user(user_id: int):
        try:
            database.delete_user(user_id)
        except Exception as exc:
            print(f"删除用户失败: {exc}")
            return _json_error("用户数据暂不可用", 503)
        return {"success": True}

    @app.put("/api/users/{user_id}")
    async def update_user(user_id: int, request: Request):
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            body = {}
        name = body.get("name") if isinstance(body, dict) else None
        if not name:
            return _json_error("姓名不能为空", 400)
        try:
            database.update_user_name(user_id, name)
        except Exception as exc:
            print(f"修改用户失败: {exc}")
            return _json_error("用户数据暂不可用", 503)
        return {"success": True}

    @app.post("/api/clockin")
    async def clockin(request: Request):
        unavailable = require_runtime_ready()
        if unavailable is not None:
            return unavailable
        form = await _read_form(request)
        image = None
        image_path = "unknown"
        upload_value = form.get("image") if hasattr(form, "get") else None
        upload_data = None
        if upload_value is not None:
            content_type = getattr(upload_value, "content_type", None)
            if content_type not in ALLOWED_IMAGE_TYPES:
                return _json_error("仅支持 JPEG、PNG 或 WebP 图片", 400)
            try:
                upload_data, _ = await _read_upload(upload_value)
            except ValueError as exc:
                return _json_error(str(exc), 400)
        if upload_data is not None:
            try:
                image = app.state.image_decoder(upload_data)
                uploads.mkdir(parents=True, exist_ok=True)
                image_path = f"clockin_{time.time_ns()}.jpg"
                (uploads / image_path).write_bytes(upload_data)
            except Exception as exc:
                print(f"上传打卡图像处理失败: {exc}")
                return _json_error("图像处理失败", 400)
        elif hasattr(form, "get") and form.get("image_base64"):
            raw = _decode_base64(form.get("image_base64"))
            if raw is None:
                return _json_error("无效的图片", 400)
            try:
                image = app.state.image_decoder(raw)
            except Exception as exc:
                print(f"Base64 打卡图像解码失败: {exc}")
                return _json_error("图像解码失败", 400)

        if image is None:
            return _json_error("无图片数据", 400)
        try:
            return app.state.runtime.clockin(image, image_path)
        except RuntimeNotReadyError as exc:
            return _json_error(_public_error(exc, "人脸系统暂不可用"), 503)
        except Exception as exc:
            print(f"打卡失败: {exc}")
            return _json_error("打卡失败", 500)

    @app.get("/api/attendance")
    async def list_attendance():
        try:
            return [dict(record) for record in database.get_attendance()]
        except Exception as exc:
            print(f"获取考勤记录失败: {exc}")
            return _json_error("考勤数据暂不可用", 503)

    @app.get("/video_feed")
    async def video_feed(request: Request):
        unavailable = require_camera_ready()
        if unavailable is not None:
            return unavailable
        try:
            runtime_state = app.state.runtime
            camera = getattr(runtime_state, "camera", None)

            async def stream_from_camera():
                """Poll the cached frame without holding a worker thread."""

                while camera is not None and getattr(camera, "running", None):
                    if not camera.running.is_set() or await request.is_disconnected():
                        break
                    frame = camera.get_frame()
                    if frame:
                        yield (
                            b"--frame\r\n"
                            b"Content-Type: image/jpeg\r\n\r\n"
                            + frame
                            + b"\r\n"
                        )
                    await asyncio.sleep(0.1)

            if camera is not None and hasattr(camera, "get_frame"):
                stream = stream_from_camera()
            else:
                # Compatibility path for the fake runtime used by local HTTP
                # contract tests.  Production uses the async cached-camera path.
                iterator = runtime_state.iter_mjpeg()

                async def stream_from_iterator():
                    while not await request.is_disconnected():
                        try:
                            yield next(iterator)
                        except StopIteration:
                            break
                        await asyncio.sleep(0)

                stream = stream_from_iterator()
        except CameraUnavailableError as exc:
            return _json_error(_public_error(exc, "摄像头暂不可用"), 503)
        return StreamingResponse(
            stream,
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/{path:path}")
    async def frontend_fallback(path: str):
        if (
            path == "api"
            or path.startswith("api/")
            or path in {"docs", "redoc", "openapi.json"}
        ):
            return _json_error("接口不存在", 404)
        response = _frontend_response(path, app.state.frontend_dist)
        if response is not None:
            return response
        return _json_error("页面不存在", 404)

    return app


app = create_app()


__all__ = ["app", "create_app"]
