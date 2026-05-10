import asyncio
import fractions
import io
import logging
import math
import time
from typing import Optional

import av
import numpy as np
from aiortc import MediaStreamTrack
from aiortc.mediastreams import MediaStreamError

try:
    from webrtc_app.v4l2_capture import V4l2MjpegCapture
    from webrtc_app.dvpp_jpegd import DvppJpegDecoder
    from webrtc_app.cann_encoder import _CANN_READY, _try_import_cann

    _DVPP_READY = _CANN_READY or _try_import_cann()
except ImportError:
    V4l2MjpegCapture = None  # type: ignore
    DvppJpegDecoder = None  # type: ignore
    _DVPP_READY = False

try:
    from webrtc_app.v4l2_raw import V4l2RawCapture as _V4l2RawCapture
except ImportError:
    _V4l2RawCapture = None  # type: ignore


VIDEO_CLOCK_RATE = 90000
VIDEO_TIME_BASE = fractions.Fraction(1, VIDEO_CLOCK_RATE)
DEFAULT_SOURCE_NAME = "ascend-demo"
USB_SOURCE_NAME = "usb-camera"
DVPP_SOURCE_NAME = "dvpp-camera"

source_logger = logging.getLogger("ascend_source")


def _validate_positive(value: int, name: str) -> int:
    validated = int(value)
    if validated <= 0:
        raise ValueError(f"{name} must be a positive integer.")
    return validated


class AscendVideoTrack(MediaStreamTrack):
    kind = "video"

    def __init__(
        self,
        width: int = 1280,
        height: int = 720,
        fps: int = 30,
        source_name: str = DEFAULT_SOURCE_NAME,
        source_type: str = "demo",
        camera_device: str | int = 0,
    ) -> None:
        super().__init__()
        self.width = _validate_positive(width, "width")
        self.height = _validate_positive(height, "height")
        self.fps = _validate_positive(fps, "fps")
        self.source_name = source_name
        self.source_type = source_type
        self._frame_time = 1 / self.fps
        self._start: float | None = None
        self._timestamp = 0
        self._frame_index = 0
        self._camera_closed = False
        self._capture: object | None = None
        self._jpegd: object | None = None
        self._decode_log_count = 0

        if source_type == "usb_camera":
            self._init_usb_camera(camera_device)
        elif source_type == "dvpp_camera":
            self._init_dvpp_camera(camera_device)
        else:
            self._init_demo()

    def _init_demo(self) -> None:
        self._x_gradient = np.linspace(0, 255, self.width, dtype=np.uint16)[None, :]
        self._y_gradient = np.linspace(0, 255, self.height, dtype=np.uint16)[:, None]
        source_logger.info(
            "Configured Ascend video track source=%s profile=%sx%s@%s mode=demo",
            self.source_name,
            self.width,
            self.height,
            self.fps,
        )

    def _init_dvpp_camera(self, camera_device: str | int) -> None:
        """Initialize V4L2 MJPEG capture + DVPP JPEGD hardware decoder."""
        if not _DVPP_READY or DvppJpegDecoder is None:
            source_logger.warning(
                "DVPP/CANN not available, falling back to demo source"
            )
            self.source_type = "demo"
            self._init_demo()
            return

        device_path = (
            str(camera_device) if not str(camera_device).isdigit()
            else f"/dev/video{int(camera_device)}"
        )

        # Try raw V4L2 first (24fps), fall back to PyAV (~15fps)
        capture_impl = None
        if _V4l2RawCapture is not None:
            try:
                capture_impl = _V4l2RawCapture(
                    device=device_path,
                    width=self.width,
                    height=self.height,
                    fps=self.fps,
                )
                capture_impl.start()
                source_logger.info("Using direct V4L2 ioctl capture backend")
            except Exception as exc:
                source_logger.info("Raw V4L2 capture unavailable: %s, trying PyAV", exc)
                capture_impl = None

        if capture_impl is None and V4l2MjpegCapture is not None:
            try:
                capture_impl = V4l2MjpegCapture(
                    device=device_path,
                    width=self.width,
                    height=self.height,
                    fps=self.fps,
                )
                capture_impl.start()
                source_logger.info("Using PyAV V4L2 capture backend")
            except Exception as exc:
                source_logger.warning(
                    "Cannot open USB camera device=%s: %s, "
                    "falling back to demo source", device_path, exc)
                self.source_type = "demo"
                self._init_demo()
                return

        if capture_impl is None:
            source_logger.warning(
                "No V4L2 capture backend available, falling back to demo source")
            self.source_type = "demo"
            self._init_demo()
            return

        self._capture = capture_impl

        try:
            self._jpegd = DvppJpegDecoder()
        except Exception as exc:
            source_logger.warning(
                "Cannot create DVPP JPEGD decoder: %s, falling back to demo source", exc)
            self._capture.stop()
            self._capture = None
            self.source_type = "demo"
            self._init_demo()
            return

        self.source_name = DVPP_SOURCE_NAME
        actual_w = self._capture.width
        actual_h = self._capture.height
        if actual_w > 0 and actual_h > 0:
            self.width = actual_w
            self.height = actual_h

        source_logger.info(
            "Configured DVPP camera source=%s device=%s profile=%sx%s@%s "
            "pipeline=V4L2_MJPEG→JPEGD→NV12",
            self.source_name,
            device_path,
            self.width,
            self.height,
            self.fps,
        )

    def _init_usb_camera(self, camera_device: str | int) -> None:
        """Initialize V4L2 MJPEG capture with CPU decode for software baseline."""
        if V4l2MjpegCapture is None:
            source_logger.warning(
                "PyAV V4L2 capture is not available, falling back to demo source"
            )
            self.source_type = "demo"
            self._init_demo()
            return

        device_path = (
            str(camera_device) if not str(camera_device).isdigit()
            else f"/dev/video{int(camera_device)}"
        )

        try:
            capture_impl = V4l2MjpegCapture(
                device=device_path,
                width=self.width,
                height=self.height,
                fps=self.fps,
            )
            capture_impl.start()
        except Exception as exc:
            source_logger.warning(
                "Cannot open USB camera device=%s: %s, falling back to demo source",
                device_path,
                exc,
            )
            self.source_type = "demo"
            self._init_demo()
            return

        self._capture = capture_impl
        self.source_name = USB_SOURCE_NAME
        actual_w = self._capture.width
        actual_h = self._capture.height
        if actual_w > 0 and actual_h > 0:
            self.width = actual_w
            self.height = actual_h

        source_logger.info(
            "Configured USB camera source=%s device=%s profile=%sx%s@%s "
            "pipeline=V4L2_MJPEG→CPU_DECODE→RGB",
            self.source_name,
            device_path,
            self.width,
            self.height,
            self.fps,
        )

    def _camera_read(self) -> np.ndarray | None:
        """Blocking call — must run in thread executor."""
        if self.source_type == "dvpp_camera":
            if self._camera_closed or self._capture is None:
                return None
            try:
                jpeg_bytes = self._capture.read(timeout=2.0)
            except Exception:
                return None
            if self._camera_closed or self._jpegd is None:
                return None
            t0 = time.perf_counter()
            nv12_flat = self._jpegd.decode(jpeg_bytes)
            if self._decode_log_count < 5:
                source_logger.info(
                    "DVPP decode frame=%d bytes=%d decode_ms=%.1f",
                    self._decode_log_count + 1,
                    len(jpeg_bytes),
                    (time.perf_counter() - t0) * 1000,
                )
                self._decode_log_count += 1
            return nv12_flat.reshape(self._jpegd.nv12_shape)

        if self.source_type == "usb_camera":
            if self._camera_closed or self._capture is None:
                return None
            try:
                jpeg_bytes = self._capture.read(timeout=2.0)
            except Exception:
                return None
            if self._camera_closed:
                return None
            try:
                t0 = time.perf_counter()
                with av.open(io.BytesIO(jpeg_bytes), format="mjpeg") as container:
                    frame = next(container.decode(video=0))
                rgb = frame.to_ndarray(format="rgb24")
            except Exception as exc:
                if self._decode_log_count < 5:
                    source_logger.warning("USB camera decode failed: %s", exc)
                    self._decode_log_count += 1
                return None
            if self._decode_log_count < 5:
                source_logger.info(
                    "USB camera decode frame=%d bytes=%d decode_ms=%.1f",
                    self._decode_log_count + 1,
                    len(jpeg_bytes),
                    (time.perf_counter() - t0) * 1000,
                )
                self._decode_log_count += 1
            return rgb
        return None

    def describe_settings(self) -> dict[str, object]:
        if self.source_type == "dvpp_camera":
            mode = "dvpp-camera-mjpeg+jpegd"
        elif self.source_type == "usb_camera":
            mode = "usb-camera-mjpeg+cpu-decode"
        else:
            mode = "synthetic-demo"
        return {
            "source": self.source_name,
            "requested": {
                "width": self.width,
                "height": self.height,
                "fps": self.fps,
            },
            "applied": {
                "width": self.width,
                "height": self.height,
                "fps": self.fps,
                "mode": mode,
            },
        }

    def _render_demo_frame(self) -> np.ndarray:
        # Keep a runnable default on Ascend until the real NPU frame path is wired in.
        phase = (self._frame_index * 4) % 256
        frame = np.empty((self.height, self.width, 3), dtype=np.uint8)
        frame[..., 0] = ((self._x_gradient + phase) % 256).astype(np.uint8)
        frame[..., 1] = ((self._y_gradient + 48) % 256).astype(np.uint8)
        frame[..., 2] = (
            ((self._x_gradient // 2) + (self._y_gradient // 2) + (phase * 2) + 96) % 256
        ).astype(np.uint8)

        band_width = max(self.width // 14, 24)
        band_start = int(
            (math.sin(self._frame_index / 12.0) + 1) * 0.5 * max(self.width - band_width, 1)
        )
        frame[:, band_start : band_start + band_width, 1] = 255

        box_size = max(min(self.width, self.height) // 8, 24)
        box_x = int(
            (math.cos(self._frame_index / 19.0) + 1) * 0.5 * max(self.width - box_size, 1)
        )
        box_y = int(
            (math.sin(self._frame_index / 17.0) + 1) * 0.5 * max(self.height - box_size, 1)
        )
        frame[box_y : box_y + box_size, box_x : box_x + box_size, :] = np.array(
            [255, 255, 255],
            dtype=np.uint8,
        )

        return frame

    async def recv(self) -> av.VideoFrame:
        pts, time_base = await self.next_timestamp()

        t0 = time.perf_counter()
        if self.source_type in ("usb_camera", "dvpp_camera") and self._capture is not None:
            loop = asyncio.get_running_loop()
            frame = await loop.run_in_executor(None, self._camera_read)
            if frame is None:
                raise MediaStreamError("Camera read returned no frame")
            pixel_format = "nv12" if self.source_type == "dvpp_camera" else "rgb24"
        else:
            frame = self._render_demo_frame()
            pixel_format = "rgb24"

        self._frame_index += 1

        # Periodic FPS logging (every 150 frames)
        if not hasattr(self, '_fps_log_idx'):
            self._fps_log_idx = 0
            self._fps_log_start = t0
        self._fps_log_idx += 1
        if self._fps_log_idx % 150 == 0:
            elapsed = t0 - self._fps_log_start
            source_logger.info(
                "Track FPS: %.1f  (frames=%d elapsed=%.1fs)",
                150 / elapsed, self._fps_log_idx, elapsed,
            )
            self._fps_log_start = t0

        video_frame = av.VideoFrame.from_ndarray(frame, format=pixel_format)
        video_frame.pts = pts
        video_frame.time_base = time_base
        return video_frame

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

    def stop(self) -> None:
        source_logger.info("Stopping Ascend video track source=%s", self.source_name)
        self._camera_closed = True
        if self._capture is not None:
            self._capture.stop()
            self._capture = None
        if self._jpegd is not None:
            self._jpegd.destroy()
            self._jpegd = None
        super().stop()
