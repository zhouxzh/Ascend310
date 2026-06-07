"""Direct V4L2 MJPEG capture via ioctl + mmap.

Bypasses PyAV overhead — achieves native camera frame rate (24fps at 1080p).
Uses bytearray + struct for ioctl (Python fcntl requires buffer protocol).
"""

from __future__ import annotations

import array
import fcntl
import logging
import mmap
import os
import queue
import struct
import threading
from typing import Optional

logger = logging.getLogger("v4l2_raw")

# --- V4L2 constants ---
_V4L2_BUF_TYPE_VIDEO_CAPTURE = 1
_V4L2_MEMORY_MMAP = 1
_V4L2_FIELD_NONE = 1
_V4L2_PIX_FMT_MJPEG = 0x47504A4D  # 'MJPG' little-endian

# --- ioctl helpers (aarch64 Linux) ---
def _IOC(dir_, typ, nr, size):
    return (dir_ << 30) | (ord(typ) << 8) | (nr << 0) | (size << 16)

# Verified on aarch64 (Orange Pi 310B):
#   sizeof(v4l2_capability) = 104
#   sizeof(v4l2_format)     = 208  (4 + 4pad + 200)
#   sizeof(v4l2_requestbuffers) = 20
#   sizeof(v4l2_buffer)     = 88
_VIDIOC_QUERYCAP = _IOC(2, "V", 0, 104)
_VIDIOC_ENUM_FMT = _IOC(3, "V", 2, 76)
_VIDIOC_G_FMT    = _IOC(3, "V", 4, 208)
_VIDIOC_S_FMT    = _IOC(3, "V", 5, 208)
_VIDIOC_REQBUFS  = _IOC(3, "V", 8, 20)
_VIDIOC_QUERYBUF = _IOC(3, "V", 9, 88)
_VIDIOC_QBUF     = _IOC(3, "V", 15, 88)
_VIDIOC_DQBUF    = _IOC(3, "V", 17, 88)
_VIDIOC_STREAMON  = _IOC(1, "V", 18, 4)
_VIDIOC_STREAMOFF = _IOC(1, "V", 19, 4)
_VIDIOC_G_PARM    = _IOC(3, "V", 21, 204)
_VIDIOC_S_PARM    = _IOC(3, "V", 22, 204)

# --- v4l2_format layout (208 bytes on aarch64) ---
_FMT_TYPE_OFFSET = 0
_FMT_RAW_OFFSET = 8    # after 4-byte type + 4-byte pad (union aligned to 8)
_FMT_RAW_SIZE = 200

# Pix format fields inside raw_data (offsets relative to _FMT_RAW_OFFSET)
_PIX_WIDTH = 0
_PIX_HEIGHT = 4
_PIX_PIXELFORMAT = 8
_PIX_FIELD = 12
_PIX_BYTESPERLINE = 16
_PIX_SIZEIMAGE = 20

# --- v4l2_buffer layout (88 bytes on aarch64) ---
_BUF_INDEX = 0
_BUF_TYPE = 4
_BUF_BYTESUSED = 8
_BUF_FLAGS = 12
_BUF_FIELD = 16
_BUF_TIMESTAMP = 24   # struct timeval: tv_sec(8) + tv_usec(8), 8-byte aligned
_BUF_SEQUENCE = 56
_BUF_MEMORY = 60
_BUF_M_OFFSET = 64     # union m, offset member (first 4 bytes of 8-byte union)
_BUF_LENGTH = 72
_BUF_SIZE = 88


class V4l2RawCapture:
    """Direct V4L2 MJPEG capture using ioctl + mmap.

    Achieves native camera frame rate without PyAV overhead.
    """

    def __init__(
        self,
        device: str = "/dev/video0",
        width: int = 1920,
        height: int = 1080,
        fps: int = 30,
        num_bufs: int = 4,
    ) -> None:
        self._device = device
        self._width = width
        self._height = height
        self._fps = fps
        self._num_bufs = num_bufs
        self._fd: int = -1
        self._buffers: list[tuple[mmap.mmap, int]] = []
        self._thread: Optional[threading.Thread] = None
        self._running = threading.Event()
        self._queue: queue.Queue = queue.Queue(maxsize=2)
        self._dropped = 0
        self._captured = 0

    @property
    def width(self) -> int:
        return self._width

    @property
    def height(self) -> int:
        return self._height

    def start(self) -> None:
        if self._running.is_set():
            return

        self._fd = os.open(self._device, os.O_RDWR)
        try:
            self._set_format()
            self._set_framerate()
            self._init_mmap()
            self._start_stream()
        except Exception:
            os.close(self._fd)
            self._fd = -1
            raise

        self._running.set()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info(
            "V4L2 raw capture started  %s  %dx%d@%d  bufs=%d",
            self._device, self._width, self._height, self._fps, self._num_bufs,
        )

    def _set_format(self) -> None:
        buf = bytearray(208)
        struct.pack_into("I", buf, _FMT_TYPE_OFFSET, _V4L2_BUF_TYPE_VIDEO_CAPTURE)
        fcntl.ioctl(self._fd, _VIDIOC_G_FMT, buf)
        fcntl.ioctl(self._fd, _VIDIOC_S_FMT, buf)  # read current

        # Write MJPEG + resolution into raw_data
        raw_ofs = _FMT_RAW_OFFSET
        struct.pack_into("IIIII", buf, raw_ofs + _PIX_WIDTH,
                         self._width, self._height, _V4L2_PIX_FMT_MJPEG,
                         _V4L2_FIELD_NONE, 0)
        fcntl.ioctl(self._fd, _VIDIOC_S_FMT, buf)

        actual_w = struct.unpack_from("I", buf, raw_ofs + _PIX_WIDTH)[0]
        actual_h = struct.unpack_from("I", buf, raw_ofs + _PIX_HEIGHT)[0]
        self._width = actual_w
        self._height = actual_h
        logger.info("V4L2 format set: %dx%d MJPG", actual_w, actual_h)

    def _set_framerate(self) -> None:
        """Set frame rate via VIDIOC_S_PARM (timeperframe = 1/fps)."""
        buf = bytearray(204)
        struct.pack_into("I", buf, 0, _V4L2_BUF_TYPE_VIDEO_CAPTURE)

        try:
            fcntl.ioctl(self._fd, _VIDIOC_G_PARM, buf)
        except OSError:
            logger.warning("VIDIOC_G_PARM not supported, using driver default fps")
            return

        # timeperframe: numerator at offset 12, denominator at offset 16
        struct.pack_into("II", buf, 12, 1, self._fps)
        try:
            fcntl.ioctl(self._fd, _VIDIOC_S_PARM, buf)
        except OSError:
            logger.warning("VIDIOC_S_PARM not supported, using driver default fps")
            return

        num = struct.unpack_from("I", buf, 12)[0]
        den = struct.unpack_from("I", buf, 16)[0]
        actual_fps = den / num if num > 0 else 0
        logger.info("V4L2 frame rate set: %.1f fps (requested %d)", actual_fps, self._fps)

    def _init_mmap(self) -> None:
        buf = bytearray(20)
        struct.pack_into("IIII", buf, 0,
                         self._num_bufs, _V4L2_BUF_TYPE_VIDEO_CAPTURE,
                         _V4L2_MEMORY_MMAP, 0)
        fcntl.ioctl(self._fd, _VIDIOC_REQBUFS, buf)
        count = struct.unpack_from("I", buf, 0)[0]
        self._num_bufs = count
        logger.info("V4L2 MMAP: %d buffers", count)

        for i in range(self._num_bufs):
            qbuf = bytearray(_BUF_SIZE)
            struct.pack_into("I", qbuf, _BUF_INDEX, i)
            struct.pack_into("I", qbuf, _BUF_TYPE, _V4L2_BUF_TYPE_VIDEO_CAPTURE)
            struct.pack_into("I", qbuf, _BUF_MEMORY, _V4L2_MEMORY_MMAP)
            fcntl.ioctl(self._fd, _VIDIOC_QUERYBUF, qbuf)

            m_offset = struct.unpack_from("I", qbuf, _BUF_M_OFFSET)[0]
            length = struct.unpack_from("I", qbuf, _BUF_LENGTH)[0]

            m = mmap.mmap(
                self._fd, length,
                mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE,
                offset=m_offset,
            )
            self._buffers.append((m, length))

            # Enqueue
            fcntl.ioctl(self._fd, _VIDIOC_QBUF, qbuf)

    def _start_stream(self) -> None:
        buf = array.array("i", [_V4L2_BUF_TYPE_VIDEO_CAPTURE])
        fcntl.ioctl(self._fd, _VIDIOC_STREAMON, buf)

    def _capture_loop(self) -> None:
        try:
            while self._running.is_set():
                dbuf = bytearray(_BUF_SIZE)
                struct.pack_into("I", dbuf, _BUF_TYPE, _V4L2_BUF_TYPE_VIDEO_CAPTURE)
                struct.pack_into("I", dbuf, _BUF_MEMORY, _V4L2_MEMORY_MMAP)
                try:
                    fcntl.ioctl(self._fd, _VIDIOC_DQBUF, dbuf)
                except OSError:
                    continue

                if not self._running.is_set():
                    break

                idx = struct.unpack_from("I", dbuf, _BUF_INDEX)[0]
                bytesused = struct.unpack_from("I", dbuf, _BUF_BYTESUSED)[0]

                if idx < len(self._buffers):
                    jpeg_bytes = bytes(self._buffers[idx][0][:bytesused])
                    self._captured += 1
                    try:
                        self._queue.put_nowait(jpeg_bytes)
                    except queue.Full:
                        try:
                            self._queue.get_nowait()
                            self._dropped += 1
                        except queue.Empty:
                            pass
                        self._queue.put_nowait(jpeg_bytes)

                # QBUF — reuse same buffer struct (index/type/memory already set)
                try:
                    fcntl.ioctl(self._fd, _VIDIOC_QBUF, dbuf)
                except OSError:
                    pass
        except Exception:
            if self._running.is_set():
                logger.exception("V4L2 capture loop error")
        finally:
            self._stop_stream()

    def _stop_stream(self) -> None:
        if self._fd >= 0:
            try:
                buf = array.array("i", [_V4L2_BUF_TYPE_VIDEO_CAPTURE])
                fcntl.ioctl(self._fd, _VIDIOC_STREAMOFF, buf)
            except OSError:
                pass

    def read(self, timeout: float = 1.0) -> bytes:
        return self._queue.get(timeout=timeout)

    def stop(self) -> None:
        self._running.clear()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        for m, _ in self._buffers:
            try:
                m.close()
            except Exception:
                pass
        self._buffers.clear()
        if self._fd >= 0:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = -1
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
        logger.info(
            "V4L2 raw capture stopped  frames=%d  dropped=%d",
            self._captured, self._dropped,
        )
