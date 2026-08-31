"""Small, V4L2-oriented camera adapter for board-side API callbacks.

The adapter deliberately only accepts a numeric camera index or a ``/dev/videoN``
path.  It does not execute shell commands, probe every device by opening it, or
start a background capture thread.  The API workbench retains one
``CameraCapture`` instance and calls ``capture().rgb`` for a request.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import threading
import time
from typing import Any, Callable, Protocol

import cv2
import numpy as np


_VIDEO_NAME = re.compile(r"^video(?P<index>[0-9]+)$")


class CameraError(RuntimeError):
    """Raised when a camera cannot be opened or a usable frame is unavailable."""


class _VideoCapture(Protocol):
    def isOpened(self) -> bool: ...

    def read(self) -> tuple[bool, Any]: ...

    def release(self) -> None: ...

    def set(self, property_id: int, value: float) -> bool: ...


CaptureFactory = Callable[..., _VideoCapture]


@dataclass(frozen=True)
class CameraDevice:
    """A discovered V4L2 device without opening it."""

    index: int
    path: str
    name: str = ""


@dataclass(frozen=True)
class CameraFrame:
    """One successfully captured frame in formats convenient for the API.

    ``jpeg`` is empty when a caller explicitly requests RGB only.  This avoids
    encoding a full 1920x1080 JPEG for recognition and preview paths that will
    either use the RGB tensor directly or create a smaller preview image.
    """

    rgb: np.ndarray
    jpeg: bytes
    timestamp_ns: int
    device: str


def list_v4l2_devices(
    dev_root: str | Path = "/dev",
    sys_root: str | Path = "/sys/class/video4linux",
) -> list[CameraDevice]:
    """List V4L2 nodes without opening or querying video devices.

    ``dev_root`` and ``sys_root`` are parameters so unit tests can use a
    temporary filesystem instead of depending on the developer machine.
    """

    root = Path(dev_root)
    sysfs = Path(sys_root)
    try:
        candidates = list(root.iterdir())
    except OSError:
        return []

    devices: list[CameraDevice] = []
    for candidate in candidates:
        match = _VIDEO_NAME.fullmatch(candidate.name)
        if match is None:
            continue
        index = int(match.group("index"))
        name_path = sysfs / candidate.name / "name"
        try:
            name = name_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            name = ""
        devices.append(CameraDevice(index=index, path=str(candidate), name=name))
    return sorted(devices, key=lambda item: item.index)


def _normalize_device(device: int | str | Path) -> int | str:
    if isinstance(device, bool):
        raise ValueError("Camera device must be a non-negative index or /dev/videoN path")
    if isinstance(device, int):
        if device < 0:
            raise ValueError("Camera index must be non-negative")
        return device

    value = str(device)
    if value.isdecimal():
        return int(value)
    path = Path(value)
    match = _VIDEO_NAME.fullmatch(path.name)
    if match is None or path.parent != Path("/dev"):
        raise ValueError("Camera path must be in the form /dev/videoN")
    return str(path)


class CameraCapture:
    """Lazily open a V4L2 camera and provide one RGB/JPEG frame per call.

    The class has no board-specific import beyond OpenCV.  It can therefore be
    imported locally and tested with ``capture_factory`` without accessing a
    physical device.  The default factory requests OpenCV's V4L2 backend on
    Linux; a caller may pass ``backend=None`` when another backend is required.
    """

    def __init__(
        self,
        device: int | str | Path = 0,
        *,
        width: int | None = 1280,
        height: int | None = 720,
        fps: float | None = 30.0,
        backend: int | None = cv2.CAP_V4L2,
        jpeg_quality: int = 90,
        prefer_mjpeg: bool = True,
        capture_factory: CaptureFactory | None = None,
    ) -> None:
        if not 1 <= int(jpeg_quality) <= 100:
            raise ValueError("JPEG quality must be in [1, 100]")
        self.device = _normalize_device(device)
        self.width = self._positive_or_none(width, "width")
        self.height = self._positive_or_none(height, "height")
        self.fps = self._positive_or_none(fps, "fps")
        self.backend = backend
        self.jpeg_quality = int(jpeg_quality)
        self.prefer_mjpeg = bool(prefer_mjpeg)
        self._capture_factory = capture_factory or cv2.VideoCapture
        self._capture: _VideoCapture | None = None
        self._lock = threading.RLock()

    @staticmethod
    def _positive_or_none(value: float | None, label: str) -> float | None:
        if value is None:
            return None
        if float(value) <= 0:
            raise ValueError(f"Camera {label} must be positive")
        return value

    @property
    def is_open(self) -> bool:
        with self._lock:
            if self._capture is None:
                return False
            try:
                return bool(self._capture.isOpened())
            except Exception:
                # V4L2 can invalidate the native handle when a USB device is
                # unplugged.  Treat that as closed so the workbench can
                # release and recreate it on the next request.
                return False

    @property
    def device_label(self) -> str:
        return str(self.device) if isinstance(self.device, int) else self.device

    def open(self) -> None:
        """Open the configured device once.  Safe to call repeatedly."""

        with self._lock:
            if self.is_open:
                return
            self.close()
            capture = self._create_capture()
            if not capture.isOpened():
                capture.release()
                raise CameraError(f"Unable to open camera {self.device_label}")
            self._capture = capture
            self._configure_capture(capture)

    def _create_capture(self) -> _VideoCapture:
        try:
            if self.backend is None:
                return self._capture_factory(self.device)
            return self._capture_factory(self.device, self.backend)
        except Exception as exc:
            raise CameraError(f"Unable to initialize camera {self.device_label}: {exc}") from exc

    def _configure_capture(self, capture: _VideoCapture) -> None:
        # USB cameras commonly fall back to uncompressed YUYV at 1920x1080.
        # Request MJPG before the size/fps properties so V4L2 can keep the
        # transfer on the bus bounded.  Drivers that do not support it simply
        # return False and continue with their default format.
        if self.prefer_mjpeg and hasattr(cv2, "CAP_PROP_FOURCC"):
            capture.set(cv2.CAP_PROP_FOURCC, float(cv2.VideoWriter_fourcc(*"MJPG")))
        if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1.0)
        for property_id, value in (
            (cv2.CAP_PROP_FRAME_WIDTH, self.width),
            (cv2.CAP_PROP_FRAME_HEIGHT, self.height),
            (cv2.CAP_PROP_FPS, self.fps),
        ):
            if value is not None:
                capture.set(property_id, float(value))

    def close(self) -> None:
        """Release the device; calling it after an open failure is harmless."""

        with self._lock:
            capture, self._capture = self._capture, None
            if capture is not None:
                try:
                    capture.release()
                except Exception:
                    # A removed USB node may make release itself fail.  The
                    # Python handle is still discarded, allowing a fresh
                    # VideoCapture object to be created on the next request.
                    pass

    def capture(self, *, encode_jpeg: bool = True) -> CameraFrame:
        """Capture one BGR frame and optionally encode a JPEG copy."""

        with self._lock:
            self.open()
            assert self._capture is not None
            try:
                ok, bgr = self._capture.read()
            except Exception as exc:
                self.close()
                raise CameraError(f"Camera {self.device_label} read failed: {exc}") from exc
            if not ok or not isinstance(bgr, np.ndarray) or bgr.size == 0:
                raise CameraError(f"Camera {self.device_label} returned no frame")
            if bgr.ndim != 3 or bgr.shape[2] < 3:
                raise CameraError(f"Camera {self.device_label} returned an invalid frame")
            bgr = np.ascontiguousarray(bgr[:, :, :3], dtype=np.uint8)
            jpeg = b""
            if encode_jpeg:
                encoded, encoded_jpeg = cv2.imencode(
                    ".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality]
                )
                if not encoded:
                    raise CameraError("OpenCV could not encode the camera frame as JPEG")
                jpeg = encoded_jpeg.tobytes()
            return CameraFrame(
                rgb=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB),
                jpeg=jpeg,
                timestamp_ns=time.time_ns(),
                device=self.device_label,
            )

    def capture_rgb(self) -> np.ndarray:
        """Return RGB data directly compatible with the ROI preprocessor."""

        return self.capture(encode_jpeg=False).rgb

    def capture_jpeg(self) -> bytes:
        """Return the same camera data encoded as JPEG for HTTP/streaming code."""

        return self.capture().jpeg

    def __enter__(self) -> "CameraCapture":
        self.open()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()


def capture_frame(
    device: int | str | Path = 0,
    **kwargs: Any,
) -> CameraFrame:
    """Capture one frame with deterministic release, useful for a simple callback."""

    with CameraCapture(device, **kwargs) as camera:
        return camera.capture()
