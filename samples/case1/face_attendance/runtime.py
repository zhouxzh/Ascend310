"""Lifecycle and ownership boundary for the Case 1 runtime.

The board models and ACL context live in one worker thread.  HTTP handlers and
the camera thread submit work to that owner instead of touching PyACL directly.
Importing this module is safe on a development machine without CANN, OpenCV,
or the model files.
"""

import queue
import threading
import time
from concurrent.futures import Future
from pathlib import Path
from typing import Any, Callable, Optional

from . import database
from .config import UPLOAD_DIR


class RuntimeNotReadyError(RuntimeError):
    """Raised when a model operation is requested before board readiness."""


class CameraUnavailableError(RuntimeError):
    """Raised when the board camera cannot be opened or has no stream."""


class NpuWorker:
    """Own one FaceSystem instance and serialize all calls to it."""

    def __init__(self, backend_factory: Optional[Callable[[], Any]] = None):
        self._backend_factory = backend_factory or self._default_backend_factory
        self._queue = queue.Queue()
        self._thread = None
        self._ready_event = threading.Event()
        self._stop_event = threading.Event()
        # A readiness failure is reported while holding this lock and the
        # error property takes the same lock; use an RLock to avoid a
        # self-deadlock on the fail-closed path.
        self._state_lock = threading.RLock()
        self._backend = None
        self._error = None
        self._ready = False

    @staticmethod
    def _default_backend_factory():
        # Deliberately lazy: importing the web API must not import acl.
        from .inference import FaceSystem

        return FaceSystem()

    @property
    def ready(self):
        with self._state_lock:
            return self._ready

    @property
    def error(self):
        with self._state_lock:
            return self._error

    def start(self, timeout=30.0):
        with self._state_lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._queue = queue.Queue()
            self._ready_event.clear()
            self._stop_event.clear()
            self._backend = None
            self._error = None
            self._ready = False
            self._thread = threading.Thread(
                target=self._run,
                name="case1-npu-owner",
                daemon=True,
            )
            thread = self._thread
            thread.start()
        # Startup failure is represented by ready=False; the API remains able
        # to serve pages and returns 503 for operations requiring the models.
        self._ready_event.wait(timeout=timeout)

    def _run(self):
        backend = None
        try:
            backend = self._backend_factory()
            with self._state_lock:
                self._backend = backend
                self._ready = True
        except Exception as exc:
            with self._state_lock:
                self._error = exc
                self._ready = False
        finally:
            self._ready_event.set()

        # A sentinel marks the end of the queue.  Do not exit merely because
        # stop() was requested: jobs accepted before shutdown must complete or
        # receive a deterministic exception before model resources are freed.
        while True:
            try:
                item = self._queue.get(timeout=0.1)
            except queue.Empty:
                if self._stop_event.is_set():
                    break
                continue
            if item is None:
                break
            kind, target, args, kwargs, future = item
            if future.cancelled():
                continue
            if backend is None:
                future.set_exception(self._not_ready_exception())
                continue
            try:
                if kind == "method":
                    result = getattr(backend, target)(*args, **kwargs)
                else:
                    result = target(backend, *args, **kwargs)
                future.set_result(result)
            except Exception as exc:
                future.set_exception(exc)

        try:
            if backend is not None and hasattr(backend, "release"):
                backend.release()
        except Exception as exc:
            with self._state_lock:
                if self._error is None:
                    self._error = exc
        finally:
            with self._state_lock:
                self._backend = None
                self._ready = False

    def _not_ready_exception(self):
        error = self.error
        detail = str(error) if error else "NPU 运行时未就绪"
        return RuntimeNotReadyError(detail)

    def _submit(self, kind, target, args, kwargs):
        with self._state_lock:
            thread = self._thread
            ready = self._ready
            stopping = self._stop_event.is_set()
            if stopping or not ready or thread is None or not thread.is_alive():
                raise self._not_ready_exception()
            # Keep the readiness check and queue insertion atomic with stop().
            # Otherwise stop() could place its sentinel between these two
            # operations and leave this Future waiting forever.
            future = Future()
            self._queue.put((kind, target, args, kwargs, future))
            return future

    def call(self, method, *args, **kwargs):
        return self._submit("method", method, args, kwargs).result()

    def call_job(self, job, *args, **kwargs):
        return self._submit("job", job, args, kwargs).result()

    def stop(self, join_timeout=5.0):
        with self._state_lock:
            thread = self._thread
            if thread is None:
                return
            self._stop_event.set()
            self._queue.put(None)
        if thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=join_timeout)
        if thread.is_alive():
            # Keep the thread reference.  A later start() must not create a
            # second owner while the old ACL context is still active.
            with self._state_lock:
                self._error = RuntimeError("NPU 工作线程未能在关闭期限内退出")
            raise RuntimeError("NPU 工作线程未能在关闭期限内退出")
        with self._state_lock:
            if self._thread is thread:
                self._thread = None


class InferenceProxy:
    """FaceSystem-shaped proxy used by the camera thread."""

    def __init__(self, worker: NpuWorker):
        self._worker = worker

    def detect(self, image, threshold=0.5):
        return self._worker.call("detect", image, threshold=threshold)

    def get_embedding(self, face_image):
        return self._worker.call("get_embedding", face_image)


def _largest_face(faces, image):
    if not faces:
        return image
    best = max(faces, key=lambda box: (box[2] - box[0]) * (box[3] - box[1]))
    x1, y1, x2, y2 = map(int, best)
    height, width = image.shape[:2]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(width, x2), min(height, y2)
    if x2 <= x1 or y2 <= y1:
        return image
    return image[y1:y2, x1:x2]


def _register_user_job(backend, name, image, upload_dir):
    """Run detection, embedding, and registration under the NPU owner."""

    try:
        import cv2
        import numpy as np
    except ImportError as exc:  # pragma: no cover - board supplies OpenCV
        raise RuntimeNotReadyError("OpenCV 不可用") from exc

    faces = list(backend.detect(image))
    face_image = _largest_face(faces, image)
    if getattr(face_image, "size", 0) == 0:
        raise ValueError("无效的人脸区域")
    embedding = np.asarray(backend.get_embedding(face_image), dtype=np.float32)
    upload_dir = Path(upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    avatar_filename = f"avatar_{time.time_ns()}.jpg"
    if not cv2.imwrite(str(upload_dir / avatar_filename), face_image):
        raise ValueError("头像保存失败")
    user_id = database.add_user(name or "", embedding.tobytes(), avatar_filename)
    return user_id


def _clockin_job(backend, image, image_path):
    """Run one manual recognition and retain today's latest-record semantics."""

    try:
        import numpy as np
    except ImportError as exc:  # pragma: no cover - board supplies NumPy
        raise RuntimeNotReadyError("NumPy 不可用") from exc

    faces = list(backend.detect(image))
    face_image = _largest_face(faces, image)
    if getattr(face_image, "size", 0) == 0:
        raise ValueError("无效的人脸区域")
    target = np.asarray(backend.get_embedding(face_image), dtype=np.float32)

    max_similarity = -1.0
    best_match = None
    for user in database.get_users():
        try:
            stored = np.frombuffer(user["embedding"], dtype=np.float32)
            denominator = np.linalg.norm(target) * np.linalg.norm(stored) + 1e-6
            similarity = float(np.dot(target, stored) / denominator)
        except (KeyError, TypeError, ValueError):
            continue
        if similarity > max_similarity:
            max_similarity = similarity
            best_match = user

    result = {
        "success": True,
        "match": bool(best_match is not None and max_similarity > 0.5),
        "similarity": float(max_similarity),
    }
    if result["match"]:
        database.add_attendance(best_match["id"], "manual", image_path)
        result["user"] = best_match["name"]
    return result


class FaceAttendanceRuntime:
    """Application lifecycle facade shared by HTTP and camera code."""

    def __init__(
        self,
        backend_factory: Optional[Callable[[], Any]] = None,
        camera_factory: Optional[Callable[[InferenceProxy], Any]] = None,
        camera_index=0,
        upload_dir: Optional[Path] = None,
    ):
        self.worker = NpuWorker(backend_factory=backend_factory)
        self.proxy = InferenceProxy(self.worker)
        self.camera_factory = camera_factory
        self.camera_index = camera_index
        self.upload_dir = Path(upload_dir or UPLOAD_DIR)
        self.camera = None
        self._started = False
        self._camera_error = None

    @property
    def ready(self):
        return self.worker.ready

    @property
    def camera_ready(self):
        camera = self.camera
        if camera is None:
            return False
        ready = getattr(camera, "ready", None)
        return bool(ready) if ready is not None else True

    @property
    def readiness_error(self):
        camera_error = getattr(self.camera, "error", None) if self.camera is not None else None
        error = self.worker.error or self._camera_error or camera_error
        return str(error) if error else None

    def start(self):
        if self._started:
            return
        self._started = True
        self.worker.start()
        if not self.ready:
            return
        try:
            if self.camera_factory is not None:
                self.camera = self.camera_factory(self.proxy)
            else:
                from .camera import VideoCamera

                self.camera = VideoCamera(
                    self.proxy,
                    camera_index=self.camera_index,
                    upload_dir=self.upload_dir,
                )
        except Exception as exc:
            self._camera_error = exc
            self.camera = None

    def stop(self):
        camera = self.camera
        self.camera = None
        if camera is not None and hasattr(camera, "stop"):
            try:
                camera.stop()
            except Exception as exc:
                self._camera_error = exc
        self.worker.stop()
        self._started = False

    def capture_snapshot(self):
        if self.camera is None:
            raise CameraUnavailableError(self.readiness_error or "摄像头不可用")
        return self.camera.get_snapshot()

    def iter_mjpeg(self):
        if self.camera is None:
            raise CameraUnavailableError(self.readiness_error or "摄像头不可用")
        if hasattr(self.camera, "iter_mjpeg"):
            return self.camera.iter_mjpeg()

        def _legacy_stream():
            while self.camera is not None:
                frame = self.camera.get_frame()
                if frame:
                    yield (
                        b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                        + frame
                        + b"\r\n"
                    )
                else:
                    time.sleep(0.1)

        return _legacy_stream()

    def register_user(self, name, image):
        return self.worker.call_job(_register_user_job, name, image, self.upload_dir)

    def clockin(self, image, image_path="unknown"):
        return self.worker.call_job(_clockin_job, image, image_path)


__all__ = [
    "CameraUnavailableError",
    "FaceAttendanceRuntime",
    "InferenceProxy",
    "NpuWorker",
    "RuntimeNotReadyError",
]
