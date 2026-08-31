"""Camera capture and automatic attendance for Case 1.

The camera owns the capture thread.  Inference is supplied as a proxy by the
runtime, so all NPU calls remain serialized by the runtime's worker thread.
"""

import threading
import time
from pathlib import Path
from typing import Optional

try:  # Importing the API on a development host must not require OpenCV.
    import cv2
except ImportError:  # pragma: no cover - exercised on dependency-free hosts
    cv2 = None

try:
    import numpy as np
except ImportError:  # pragma: no cover - exercised on dependency-free hosts
    np = None

from . import database
from .config import UPLOAD_DIR
from .runtime import CameraUnavailableError


class VideoCamera:
    """Read one camera and cache an annotated JPEG for all MJPEG clients."""

    def __init__(self, face_system, camera_index=0, capture_factory=None, upload_dir=None):
        if cv2 is None:
            raise CameraUnavailableError("OpenCV 不可用")

        self.face_system = face_system
        self.camera_index = camera_index
        self.upload_dir = Path(upload_dir or UPLOAD_DIR)
        self.lock = threading.RLock()
        self.running = threading.Event()
        self.running.set()
        self.last_frame = None
        self.last_jpeg = None
        self.last_faces = []
        self.last_check_time = 0.0
        self.check_interval = 2.0
        self.frame_ready = threading.Event()
        self.last_error = None
        self._stop_lock = threading.Lock()
        self._released = False

        factory = capture_factory or cv2.VideoCapture
        self.video = factory(camera_index)
        if self.video is None or not self.video.isOpened():
            self._release_video()
            raise CameraUnavailableError("无法打开摄像头")

        if hasattr(self.video, "set"):
            self.video.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            self.video.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

        self.thread = threading.Thread(
            target=self.update,
            name="case1-camera",
            daemon=True,
        )
        self.thread.start()

    def _release_video(self):
        with self._stop_lock:
            if self._released:
                return
            self._released = True
            video = getattr(self, "video", None)
            if video is not None and hasattr(video, "release"):
                try:
                    video.release()
                except Exception:
                    pass

    def stop(self, join_timeout=2.0):
        """Stop capture, join its thread, and release the device exactly once."""

        self.running.clear()
        # Releasing before join unblocks a blocking V4L2 read on supported
        # backends.  Do not report a clean stop while the capture thread is
        # still alive.
        self._release_video()
        thread = getattr(self, "thread", None)
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=join_timeout)
        if thread is not None and thread.is_alive():
            raise CameraUnavailableError("摄像头线程未能在关闭期限内退出")

    @property
    def ready(self):
        """Whether the capture device has produced at least one valid frame."""

        return self.running.is_set() and self.frame_ready.is_set()

    @property
    def error(self):
        return self.last_error

    def __del__(self):  # pragma: no cover - interpreter shutdown path
        try:
            self.stop(join_timeout=0.2)
        except Exception:
            pass

    def update(self):
        try:
            while self.running.is_set():
                video = self.video
                if video is None or (hasattr(video, "isOpened") and not video.isOpened()):
                    with self.lock:
                        self.last_error = "摄像头已关闭"
                    break

                success, frame = video.read()
                if not success or frame is None:
                    with self.lock:
                        self.last_error = "摄像头尚未产生画面"
                    time.sleep(0.1)
                    continue

                faces = []
                try:
                    faces = list(self.face_system.detect(frame))
                except Exception as exc:
                    # A transient board inference error must not kill camera cleanup.
                    print(f"摄像头推理失败: {exc}")

                rendered = frame.copy()
                if cv2 is not None:
                    for box in faces:
                        x1, y1, x2, y2 = map(int, box)
                        cv2.rectangle(rendered, (x1, y1), (x2, y2), (0, 255, 0), 2)
                    encoded, jpeg = cv2.imencode(".jpg", rendered)
                    jpeg_bytes = jpeg.tobytes() if encoded else None
                else:  # pragma: no cover - guarded by constructor
                    jpeg_bytes = None

                with self.lock:
                    self.last_frame = frame.copy()
                    self.last_faces = faces
                    if jpeg_bytes:
                        self.last_jpeg = jpeg_bytes
                        self.frame_ready.set()
                        self.last_error = None

                current_time = time.time()
                if current_time - self.last_check_time >= self.check_interval:
                    self.last_check_time = current_time
                    try:
                        self.process_attendance(frame, faces)
                    except Exception as exc:
                        print(f"自动打卡失败: {exc}")

                time.sleep(0.03)
        finally:
            self.running.clear()

    def get_frame(self):
        """Return the latest cached JPEG without invoking inference."""

        with self.lock:
            return self.last_jpeg

    def get_snapshot(self):
        with self.lock:
            if self.last_frame is None:
                return None
            return self.last_frame.copy()

    def iter_mjpeg(self):
        while self.running.is_set():
            frame = self.get_frame()
            if frame:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                    + frame
                    + b"\r\n"
                )
            else:
                time.sleep(0.1)

    @staticmethod
    def _largest_face(faces, image):
        if not faces:
            return image
        best_face = max(faces, key=lambda b: (b[2] - b[0]) * (b[3] - b[1]))
        x1, y1, x2, y2 = map(int, best_face)
        height, width = image.shape[:2]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(width, x2), min(height, y2)
        if x2 <= x1 or y2 <= y1:
            return image
        return image[y1:y2, x1:x2]

    def process_attendance(self, frame, faces=None):
        """Recognize the largest face and retain the original auto-register rule."""

        if np is None or cv2 is None:
            return
        if faces is None:
            faces = list(self.face_system.detect(frame))
        if not faces:
            return

        face_img = self._largest_face(faces, frame)
        if face_img is None or getattr(face_img, "size", 0) == 0:
            return

        emb = self.face_system.get_embedding(face_img)
        users = database.get_users()
        max_sim = -1.0
        best_match = None
        for user in users:
            try:
                db_emb = np.frombuffer(user["embedding"], dtype=np.float32)
                denominator = np.linalg.norm(emb) * np.linalg.norm(db_emb) + 1e-6
                sim = float(np.dot(emb, db_emb) / denominator)
            except (KeyError, TypeError, ValueError):
                continue
            if sim > max_sim:
                max_sim = sim
                best_match = user

        threshold = 0.5
        if best_match is not None and max_sim > threshold:
            user_id = best_match["id"]
            print(f"User {best_match['name']} identified ({max_sim:.2f})")
        else:
            self.upload_dir.mkdir(parents=True, exist_ok=True)
            avatar_filename = f"avatar_{time.time_ns()}.jpg"
            avatar_path = str(self.upload_dir / avatar_filename)
            cv2.imwrite(avatar_path, face_img)
            user_id = database.add_user("", np.asarray(emb, dtype=np.float32).tobytes(), avatar_filename)
            print(f"New user auto-registered with ID {user_id}")

        self.upload_dir.mkdir(parents=True, exist_ok=True)
        filename = f"attendance_{user_id}_{time.time_ns()}.jpg"
        filepath = str(self.upload_dir / filename)
        cv2.imwrite(filepath, face_img)
        database.add_attendance(user_id, "camera_auto", filename)


__all__ = ["VideoCamera"]
