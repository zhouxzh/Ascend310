"""Small, V4L2-oriented camera adapter for board-side API callbacks.

The adapter deliberately only accepts a numeric camera index or a ``/dev/videoN``
path.  It does not execute shell commands or start a background capture thread.
Device discovery performs a short V4L2 capability query so metadata-only UVC
nodes cannot be presented as cameras.  The API workbench retains one
``CameraCapture`` instance and calls ``capture().rgb`` for a request.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import struct
import threading
import time
from typing import Any, Callable, Protocol

import cv2
import numpy as np

try:  # ``fcntl`` is Linux-only; the board is Linux while controller tests are not.
    import fcntl
except ImportError:  # pragma: no cover - exercised only on non-POSIX controllers
    fcntl = None  # type: ignore[assignment]


_VIDEO_NAME = re.compile(r"^video(?P<index>[0-9]+)$")
_V4L2_CAP_VIDEO_CAPTURE = 0x00000001
_V4L2_CAP_VIDEO_CAPTURE_MPLANE = 0x00001000
_V4L2_CAP_DEVICE_CAPS = 0x80000000
_V4L2_CAPABILITY_SIZE = 104
# Linux's VIDIOC_QUERYCAP = _IOR('V', 0, struct v4l2_capability).  The UAPI
# structure has a fixed 104-byte layout across the supported 32/64-bit boards.
_VIDIOC_QUERYCAP = (2 << 30) | (_V4L2_CAPABILITY_SIZE << 16) | (ord("V") << 8)
# ``struct v4l2_format`` is 208 bytes on both 32- and 64-bit Linux builds.  The
# first field is the buffer type and the pixel format's width/height follow it,
# which lets one layout serve both single-planar and multi-planar nodes.
_V4L2_FORMAT_SIZE = 208
_VIDIOC_G_FMT = (3 << 30) | (_V4L2_FORMAT_SIZE << 16) | (ord("V") << 8) | 4
_V4L2_BUF_TYPE_VIDEO_CAPTURE = 1
_V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE = 9


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
    physical_id: str = ""
    aliases: tuple[str, ...] = ()


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
    frame_format: str = "unknown"
    physical_id: str = ""


def _is_video_capture_node(path: Path) -> bool:
    """Return whether a V4L2 node can actually deliver an image frame.

    Capability bits alone are not enough on this board: the ISP media node
    ``vin-media_mainpath`` advertises ``VIDEO_CAPTURE_MPLANE`` but reports a
    0x0 pixel format and never returns frames.  Querying ``VIDIOC_G_FMT`` and
    requiring a non-zero width and height filters that node out, so device
    numbering drift (``/dev/video0`` being either the USB camera or the ISP
    node depending on enumeration order) can no longer select a dead device.
    """

    if fcntl is None:
        return False
    try:
        descriptor = os.open(str(path), os.O_RDONLY | os.O_NONBLOCK)
    except OSError:
        return False
    try:
        capability = bytearray(_V4L2_CAPABILITY_SIZE)
        fcntl.ioctl(descriptor, _VIDIOC_QUERYCAP, capability, True)
        capabilities, device_caps = struct.unpack_from("=II", capability, 84)
        effective_caps = (
            device_caps if capabilities & _V4L2_CAP_DEVICE_CAPS else capabilities
        )
        if effective_caps & _V4L2_CAP_VIDEO_CAPTURE:
            buffer_type = _V4L2_BUF_TYPE_VIDEO_CAPTURE
        elif effective_caps & _V4L2_CAP_VIDEO_CAPTURE_MPLANE:
            buffer_type = _V4L2_BUF_TYPE_VIDEO_CAPTURE_MPLANE
        else:
            return False

        pixel_format = bytearray(_V4L2_FORMAT_SIZE)
        struct.pack_into("=I", pixel_format, 0, buffer_type)
        fcntl.ioctl(descriptor, _VIDIOC_G_FMT, pixel_format, True)
        width, height = struct.unpack_from("=II", pixel_format, 4)
        return width > 0 and height > 0
    except OSError:
        return False
    finally:
        os.close(descriptor)


def _physical_identity(sysfs_node: Path) -> str:
    try:
        device = (sysfs_node / "device").resolve(strict=True)
    except OSError:
        return f"node:{sysfs_node.name}"
    # UVC interfaces and capture endpoints share their parent USB device.
    for parent in (device, *device.parents):
        if (parent / "idVendor").is_file() and (parent / "idProduct").is_file():
            def read(name: str) -> str:
                try:
                    return (parent / name).read_text().strip()
                except OSError:
                    return ""
            return f"usb:{parent}|{read('busnum')}|{read('idVendor')}:{read('idProduct')}|{read('serial')}"
    return str(device)


def list_v4l2_devices(
    dev_root: str | Path = "/dev",
    sys_root: str | Path = "/sys/class/video4linux",
    *,
    capture_probe: Callable[[Path], bool] | None = None,
) -> list[CameraDevice]:
    """List only V4L2 nodes that can capture image frames.

    V4L2 may expose metadata endpoints beside a webcam's actual capture node.
    They share the same card name but cannot return an image, so each candidate
    is queried with ``VIDIOC_QUERYCAP`` and metadata-only nodes are omitted.
    ``capture_probe`` keeps the filesystem-level behavior testable without a
    physical V4L2 device.
    """

    root = Path(dev_root)
    sysfs = Path(sys_root)
    try:
        candidates = list(root.iterdir())
    except OSError:
        return []

    probe = capture_probe or _is_video_capture_node
    grouped: dict[str, list[CameraDevice]] = {}
    for candidate in candidates:
        match = _VIDEO_NAME.fullmatch(candidate.name)
        if match is None:
            continue
        try:
            if not probe(candidate):
                continue
        except OSError:
            continue
        index = int(match.group("index"))
        name_path = sysfs / candidate.name / "name"
        try:
            name = name_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            name = ""
        physical_id = _physical_identity(sysfs / candidate.name)
        item = CameraDevice(
            index=index,
            path=str(candidate),
            name=name,
            physical_id=physical_id,
            aliases=(str(candidate),),
        )
        grouped.setdefault(physical_id, []).append(item)

    devices: list[CameraDevice] = []
    for physical_id, items in grouped.items():
        ordered = sorted(items, key=lambda item: item.index)
        primary = ordered[0]
        aliases = tuple(item.path for item in ordered)
        devices.append(
            CameraDevice(
                index=primary.index,
                path=primary.path,
                name=primary.name or next((item.name for item in ordered if item.name), ""),
                physical_id=physical_id,
                aliases=aliases,
            )
        )
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
                self.close()
                raise CameraError(f"Camera {self.device_label} returned no frame")
            if bgr.ndim != 3 or bgr.shape[2] < 3:
                raise CameraError(f"Camera {self.device_label} returned an invalid frame")
            bgr = np.ascontiguousarray(bgr[:, :, :3], dtype=np.uint8)
            # Fail visibly instead of silently accepting a different capture
            # contract and invalidating guide calibration.
            if self._capture_factory is cv2.VideoCapture and (bgr.shape[1], bgr.shape[0]) != (self.width, self.height):
                actual = f"{bgr.shape[1]}x{bgr.shape[0]}"
                self.close()
                raise CameraError(f"Resolution negotiation failed: requested {self.width}x{self.height}, actual {actual}")
            frame_format = "unknown"
            if hasattr(self._capture, "get"):
                value = int(self._capture.get(cv2.CAP_PROP_FOURCC))
                frame_format = "".join(chr((value >> (8 * index)) & 255) for index in range(4)).strip("\x00") or "unknown"
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
                frame_format=frame_format,
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
