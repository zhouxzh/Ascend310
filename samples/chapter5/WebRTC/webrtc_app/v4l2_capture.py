"""V4L2 MJPEG capture via PyAV — raw JPEG frames from USB camera."""

from __future__ import annotations

import logging
import queue
import threading
from typing import Optional

import av

logger = logging.getLogger("v4l2_capture")


class V4l2MjpegCapture:
    """Captures raw MJPEG frames from a V4L2 USB camera using PyAV.

    Each read() returns the raw JPEG bitstream bytes — no decoding happens on CPU.
    Intended to feed directly into DVPP JPEGD hardware decoder.
    """

    def __init__(
        self,
        device: str = "/dev/video0",
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
        queue_depth: int = 2,
    ) -> None:
        self._device = device
        self._width = width
        self._height = height
        self._fps = fps
        self._container: Optional[av.container.InputContainer] = None
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._queue: queue.Queue = queue.Queue(maxsize=queue_depth)
        self._dropped_frames = 0
        self._captured_frames = 0

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    def start(self) -> None:
        if self._running.is_set():
            return

        self._container = av.open(
            self._device,
            format="v4l2",
            options={
                "video_size": f"{self._width}x{self._height}",
                "pixel_format": "mjpeg",
                "framerate": str(self._fps),
            },
        )
        self._running.set()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info(
            "V4L2 MJPEG capture started  %s  %dx%d@%d",
            self._device,
            self._width,
            self._height,
            self._fps,
        )

    def _capture_loop(self) -> None:
        try:
            for packet in self._container.demux():
                if not self._running.is_set():
                    break
                jpeg_bytes = bytes(packet)
                self._captured_frames += 1
                # Drop oldest if queue full — always deliver freshest frame
                try:
                    self._queue.put_nowait(jpeg_bytes)
                except queue.Full:
                    try:
                        self._queue.get_nowait()
                        self._dropped_frames += 1
                    except queue.Empty:
                        pass
                    self._queue.put_nowait(jpeg_bytes)
        except Exception:
            if self._running.is_set():
                logger.exception("V4L2 capture loop error")
        finally:
            logger.info(
                "V4L2 capture stopped  frames=%d  dropped=%d",
                self._captured_frames,
                self._dropped_frames,
            )

    def read(self, timeout: float = 1.0) -> bytes:
        """Block until next MJPEG frame is available. Returns raw JPEG bytes."""
        return self._queue.get(timeout=timeout)

    def stop(self) -> None:
        self._running.clear()
        if self._container is not None:
            try:
                self._container.close()
            except Exception:
                pass
            self._container = None
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        # Drain queue
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        logger.info("V4L2 MJPEG capture closed")
