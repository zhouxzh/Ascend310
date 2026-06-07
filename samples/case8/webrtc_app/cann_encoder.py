import logging
import os
import queue
import threading
import time
from typing import Iterator, Optional

import av
import numpy as np
from aiortc.codecs.h264 import H264Encoder

logger = logging.getLogger("cann_encoder")

VENC_AUTO_BITS_PER_PIXEL = 0.04
VENC_MIN_BITRATE_KBPS = 500
VENC_MAX_BITRATE_KBPS = 10_000
_SESSION_BITRATE_OVERRIDE_KBPS: Optional[int] = None
_ENCODER_STATUS_CALLBACK = None


def _clamp_venc_bitrate_kbps(bitrate_kbps: int) -> int:
    return max(VENC_MIN_BITRATE_KBPS, min(bitrate_kbps, VENC_MAX_BITRATE_KBPS))


def _is_success(ret) -> bool:
    return ret is None or ret == ACL_SUCCESS


def _normalize_video_codec(codec: str) -> str:
    return "h264"


def estimate_venc_bitrate_kbps(
    width: int,
    height: int,
    fps: int,
    codec: str = "h264",
) -> int:
    """Estimate a practical VENC bitrate in kbps for the source format."""
    _normalize_video_codec(codec)
    bitrate = round(width * height * fps * VENC_AUTO_BITS_PER_PIXEL / 1000)
    return _clamp_venc_bitrate_kbps(bitrate)


def resolve_venc_bitrate_kbps(
    width: int,
    height: int,
    fps: int,
    codec: str = "h264",
) -> int:
    """Resolve VENC bitrate from source dimensions and codec."""
    return estimate_venc_bitrate_kbps(width, height, fps, codec=codec)


def _target_bitrate_kbps(target_bitrate_bps: int) -> int:
    if target_bitrate_bps <= 0:
        return 0
    return _clamp_venc_bitrate_kbps(target_bitrate_bps // 1000)


def _resolve_venc_bitrate_kbps(
    width: int,
    height: int,
    fps: int,
    codec: str,
    target_bitrate_bps: int,
) -> int:
    bitrate = resolve_venc_bitrate_kbps(width, height, fps, codec=codec)
    target_kbps = _target_bitrate_kbps(target_bitrate_bps)
    if target_kbps:
        bitrate = min(bitrate, target_kbps)
    return bitrate


def set_session_bitrate_override_kbps(bitrate_kbps: Optional[int]) -> None:
    global _SESSION_BITRATE_OVERRIDE_KBPS
    if bitrate_kbps is None:
        _SESSION_BITRATE_OVERRIDE_KBPS = None
        return
    _SESSION_BITRATE_OVERRIDE_KBPS = _clamp_venc_bitrate_kbps(int(bitrate_kbps))


def get_session_bitrate_override_kbps() -> Optional[int]:
    return _SESSION_BITRATE_OVERRIDE_KBPS


def set_encoder_status_callback(callback) -> None:
    global _ENCODER_STATUS_CALLBACK
    _ENCODER_STATUS_CALLBACK = callback


def _notify_encoder_status(name: str, hardware_active: bool, reason: str = "") -> None:
    if _ENCODER_STATUS_CALLBACK is None:
        return
    try:
        _ENCODER_STATUS_CALLBACK(name, hardware_active, reason)
    except Exception:
        logger.exception("Encoder status callback failed")


# ---------------------------------------------------------------------------
#  CANN constants
# ---------------------------------------------------------------------------
ENTYPE_H264_BASE = 1
ENTYPE_H264_MAIN = 2
ENTYPE_H264_HIGH = 3
PIXEL_FORMAT_YUV_SEMIPLANAR_420 = 1  # NV12
ACL_MEMCPY_HOST_TO_DEVICE = 1
ACL_MEMCPY_DEVICE_TO_HOST = 2
ACL_SUCCESS = 0
ACL_ALREADY_INITIALIZED = 100002

# ---------------------------------------------------------------------------
#  Optional CANN import
# ---------------------------------------------------------------------------
_acl = None
_acl_media = None
_acl_rt = None
_acl_util = None
_CANN_READY = False

_ACL_INIT_LOCK = threading.Lock()
_ACL_INITIALIZED = False
_ACL_CONTEXT = None


def _try_import_cann():
    """Import CANN modules with proper library paths set."""
    global _acl, _acl_media, _acl_rt, _acl_util, _CANN_READY

    if _CANN_READY:
        return True
    if _acl is not None:
        return _CANN_READY

    cann_paths = [
        "/usr/local/Ascend/ascend-toolkit/latest",
        "/usr/local/Ascend/ascend-toolkit/8.3.RC1",
    ]
    for base in cann_paths:
        # Python site-packages is directly under the toolkit, NOT under aarch64-linux
        py_path = os.path.join(base, "python", "site-packages")
        lib_path = os.path.join(base, "aarch64-linux", "lib64")
        if os.path.isdir(py_path) and os.path.isdir(lib_path):
            os.environ.setdefault("LD_LIBRARY_PATH", "")
            if lib_path not in os.environ["LD_LIBRARY_PATH"]:
                os.environ["LD_LIBRARY_PATH"] = (
                    f"{lib_path}:{os.environ['LD_LIBRARY_PATH']}"
                )
            if py_path not in os.environ.get("PYTHONPATH", ""):
                os.environ["PYTHONPATH"] = (
                    f"{py_path}:{os.environ.get('PYTHONPATH', '')}"
                )
            import sys
            if py_path not in sys.path:
                sys.path.insert(0, py_path)
            break

    try:
        import acl as _acl_mod
        _acl = _acl_mod
        _acl_media = _acl.media
        _acl_rt = _acl.rt
        _acl_util = _acl.util
        _CANN_READY = True
        logger.info("CANN ACL imported successfully")
        return True
    except ImportError as exc:
        _acl = False
        logger.warning("CANN ACL not available: %s", exc)
        return False


def _init_acl(device_id: int = 0) -> bool:
    """One-time ACL runtime initialization (thread-safe)."""
    global _ACL_CONTEXT, _ACL_INITIALIZED
    if _ACL_INITIALIZED:
        if _ACL_CONTEXT is not None:
            ret = _acl_rt.set_context(_ACL_CONTEXT)
            if ret != ACL_SUCCESS:
                logger.error("acl.rt.set_context() failed: %s", ret)
                return False
        return True
    if not _try_import_cann():
        return False
    with _ACL_INIT_LOCK:
        if _ACL_INITIALIZED:
            if _ACL_CONTEXT is not None:
                ret = _acl_rt.set_context(_ACL_CONTEXT)
                if ret != ACL_SUCCESS:
                    logger.error("acl.rt.set_context() failed: %s", ret)
                    return False
            return True
        ret = _acl.init()
        if ret not in (ACL_SUCCESS, ACL_ALREADY_INITIALIZED):
            logger.error("acl.init() failed: %s", ret)
            return False
        ret = _acl_rt.set_device(device_id)
        if ret != ACL_SUCCESS:
            logger.error("acl.rt.set_device(%s) failed: %s", device_id, ret)
            return False
        ctx, ret = _acl_rt.create_context(device_id)
        if ret != ACL_SUCCESS:
            logger.error("acl.rt.create_context(%s) failed: %s", device_id, ret)
            return False
        ret = _acl_rt.set_context(ctx)
        if ret != ACL_SUCCESS:
            logger.error("acl.rt.set_context() failed: %s", ret)
            return False
        _ACL_CONTEXT = ctx
        _ACL_INITIALIZED = True
        logger.info("ACL initialized  device=%s  soc=%s", device_id, _acl.get_soc_name())
        return True


# ---------------------------------------------------------------------------
#  NV12 conversion helpers
# ---------------------------------------------------------------------------
def bgr_to_nv12(bgr: np.ndarray) -> np.ndarray:
    """Convert a BGR (H,W,3) uint8 numpy array to NV12 (H*3/2, W) uint8."""
    h, w = bgr.shape[:2]
    if h % 2 != 0 or w % 2 != 0:
        raise ValueError(f"NV12 requires even width/height, got {w}x{h}")

    b = bgr[..., 0].astype(np.int32)
    g = bgr[..., 1].astype(np.int32)
    r = bgr[..., 2].astype(np.int32)

    y = ((66 * r + 129 * g + 25 * b + 128) >> 8) + 16
    y = np.clip(y, 0, 255).astype(np.uint8)

    b2 = b.reshape(h // 2, 2, w // 2, 2).sum(axis=(1, 3))
    g2 = g.reshape(h // 2, 2, w // 2, 2).sum(axis=(1, 3))
    r2 = r.reshape(h // 2, 2, w // 2, 2).sum(axis=(1, 3))

    u_sub = (((-38 * r2 - 74 * g2 + 112 * b2 + 512) >> 10) + 128)
    v_sub = (((112 * r2 - 94 * g2 - 18 * b2 + 512) >> 10) + 128)

    uv = np.empty((h // 2, w), dtype=np.uint8)
    uv[:, 0::2] = np.clip(u_sub, 0, 255).astype(np.uint8)
    uv[:, 1::2] = np.clip(v_sub, 0, 255).astype(np.uint8)
    return np.vstack([y, uv])


# ---------------------------------------------------------------------------
#  Synchronous CANN VENC wrapper
# ---------------------------------------------------------------------------
class CannVenc:
    """Synchronous wrapper around the async CANN VENC callback API."""

    def __init__(
        self,
        width: int,
        height: int,
        fps: int = 30,
        bitrate: Optional[int] = None,  # kbps; VENC unit is kbps
        entype: int = ENTYPE_H264_BASE,
        channel_id: int = 10,
    ):
        if not _init_acl():
            raise RuntimeError("CANN ACL initialization failed")

        self.width = width
        self.height = height
        self.fps = fps
        if bitrate is not None and bitrate <= 0:
            raise ValueError(f"bitrate must be positive, got {bitrate}")
        self.bitrate = bitrate or resolve_venc_bitrate_kbps(
            width,
            height,
            fps,
            codec="h264",
        )
        self.entype = entype
        self._channel_id = channel_id
        self._channel_desc = None
        self._frame_config = None
        self._callback_tid = None
        self._ctx = _ACL_CONTEXT
        if self._ctx is None:
            raise RuntimeError("ACL context is not initialized")
        ret = _acl_rt.set_context(self._ctx)
        if ret != ACL_SUCCESS:
            raise RuntimeError(f"acl.rt.set_context() failed: {ret}")
        self._running = True

        # Callback synchronization
        self._cb_event = threading.Event()
        self._cb_lock = threading.Lock()
        self._encoded_data: Optional[bytes] = None
        self._encoded_size: int = 0
        self._cb_error: int = 0
        self._cb_queue: queue.Queue = queue.Queue(maxsize=64)

        # Input alignment (CANN VENC requires 16-aligned width for NV12)
        self._align = 16
        self._stride = ((width + self._align - 1) // self._align) * self._align
        self._nv12_size = self._stride * height * 3 // 2

        # Per-frame output buffer size (conservative: raw frame size)
        self._out_buf_size = width * height * 3 // 2

        self._create_channel()

    def _callback_thread(self, _args):
        _acl_rt.set_context(self._ctx)
        while self._running:
            _acl_rt.process_report(300)

    def _venc_callback(self, input_pic_desc, output_stream_desc, _user_data):
        """Called by CANN when a frame is encoded."""
        try:
            size = _acl_media.dvpp_get_stream_desc_size(output_stream_desc)
            if size > 0:
                data_ptr = _acl_media.dvpp_get_stream_desc_data(output_stream_desc)
                # Copy encoded data from DVPP memory to host
                host_buf, ret = _acl_rt.malloc_host(size)
                if ret == ACL_SUCCESS:
                    _acl_rt.memcpy(host_buf, size, data_ptr, size,
                                   ACL_MEMCPY_DEVICE_TO_HOST)
                    encoded = ctypes_copy_bytes(host_buf, size)
                    _acl_rt.free_host(host_buf)
                    self._cb_queue.put(encoded)
                else:
                    self._cb_queue.put(None)
            else:
                self._cb_queue.put(None)
        except Exception as exc:
            logger.error("VENC callback error: %s", exc)
            try:
                self._cb_queue.put(None)
            except Exception:
                pass
        finally:
            # Clean up input pic_desc; output stream_desc is managed by caller
            _acl_media.dvpp_destroy_pic_desc(input_pic_desc)

    def _create_channel(self):
        self._channel_desc = _acl_media.venc_create_channel_desc()

        tid, ret = _acl_util.start_thread(self._callback_thread, [])
        if ret != ACL_SUCCESS:
            raise RuntimeError(f"acl.util.start_thread failed: {ret}")
        self._callback_tid = tid

        _acl_media.venc_set_channel_desc_thread_id(self._channel_desc, tid)
        _acl_media.venc_set_channel_desc_callback(
            self._channel_desc, self._venc_callback)
        _acl_media.venc_set_channel_desc_entype(self._channel_desc, self.entype)
        _acl_media.venc_set_channel_desc_pic_format(
            self._channel_desc, PIXEL_FORMAT_YUV_SEMIPLANAR_420)
        _acl_media.venc_set_channel_desc_pic_width(
            self._channel_desc, self.width)
        _acl_media.venc_set_channel_desc_pic_height(
            self._channel_desc, self.height)
        _acl_media.venc_set_channel_desc_key_frame_interval(
            self._channel_desc, max(self.fps, 1))
        _acl_media.venc_set_channel_desc_src_rate(
            self._channel_desc, max(self.fps, 1))
        _acl_media.venc_set_channel_desc_max_bit_rate(
            self._channel_desc, self.bitrate)
        _acl_media.venc_set_channel_desc_rc_mode(self._channel_desc, 2)  # CBR

        ret = _acl_media.venc_create_channel(self._channel_desc)
        if ret != ACL_SUCCESS:
            raise RuntimeError(
                f"venc_create_channel failed: {ret} (0x{ret:x}). "
                f"Check: npu-smi info, driver loaded, channel ID available."
            )

        self._frame_config = _acl_media.venc_create_frame_config()
        if self._frame_config is None:
            raise RuntimeError("venc_create_frame_config failed")

        logger.info(
            "CANN VENC channel created  %dx%d@%d  bitrate=%d  entype=%d",
            self.width, self.height, self.fps, self.bitrate, self.entype,
        )

    def encode(self, nv12_data: np.ndarray, force_keyframe: bool = False,
               pre_padded: bool = False) -> bytes:
        """Encode one NV12 frame. Returns Annex-B bitstream bytes.

        Args:
            nv12_data: NV12 numpy array (H*3/2, W) tightly packed, or
                       (stride*H*3/2) pre-padded when pre_padded=True.
            force_keyframe: Force this frame to be an I-frame.
            pre_padded: If True, nv12_data is already stride-aligned (from JPEGD).
        """
        if not self._running:
            raise RuntimeError("VENC channel is closed")

        _acl_rt.set_context(self._ctx)

        h = self.height
        w = self.width
        stride = self._stride

        if pre_padded:
            nv12_padded = nv12_data.ravel()
            # Derive height stride from padded buffer: rows = size / stride_w
            padded_rows = nv12_padded.nbytes // stride
            height_stride = padded_rows * 2 // 3
        else:
            height_stride = h
            # Build padded NV12 for VENC (Y plane padded to stride, UV plane padded to stride)
            nv12_padded = np.zeros(stride * h * 3 // 2, dtype=np.uint8).reshape(-1, stride)
            nv12_src = nv12_data.reshape(-1, w)
            # Y plane
            for row in range(h):
                nv12_padded[row, :w] = nv12_src[row, :w]
            # UV plane
            for row in range(h // 2):
                nv12_padded[h + row, :w] = nv12_src[h + row, :w]
            nv12_padded = nv12_padded.ravel()

        input_size = nv12_padded.nbytes
        input_buffer, ret = _acl_media.dvpp_malloc(input_size)
        if ret != ACL_SUCCESS or input_buffer is None:
            raise RuntimeError(f"dvpp_malloc input failed: {ret}")

        _acl_rt.memcpy(input_buffer, input_size,
                       nv12_padded.ctypes.data, input_size,
                       ACL_MEMCPY_HOST_TO_DEVICE)

        pic_desc = _acl_media.dvpp_create_pic_desc()
        _acl_media.dvpp_set_pic_desc_data(pic_desc, input_buffer)
        _acl_media.dvpp_set_pic_desc_size(pic_desc, input_size)
        _acl_media.dvpp_set_pic_desc_format(pic_desc, PIXEL_FORMAT_YUV_SEMIPLANAR_420)
        _acl_media.dvpp_set_pic_desc_width(pic_desc, self.width)
        _acl_media.dvpp_set_pic_desc_height(pic_desc, self.height)
        _acl_media.dvpp_set_pic_desc_width_stride(pic_desc, self._stride)
        _acl_media.dvpp_set_pic_desc_height_stride(pic_desc, height_stride)

        out_buffer, ret = _acl_media.dvpp_malloc(self._out_buf_size)
        if ret != ACL_SUCCESS or out_buffer is None:
            _acl_media.dvpp_free(input_buffer)
            _acl_media.dvpp_destroy_pic_desc(pic_desc)
            raise RuntimeError(f"dvpp_malloc output failed: {ret}")

        stream_desc = _acl_media.dvpp_create_stream_desc()
        _acl_media.dvpp_set_stream_desc_data(stream_desc, out_buffer)
        _acl_media.dvpp_set_stream_desc_size(stream_desc, self._out_buf_size)

        if force_keyframe:
            _acl_media.venc_set_frame_config_force_i_frame(self._frame_config, True)

        # Drain callback queue before sending — leftover data indicates a
        # previous consume failure; log it so silent frame loss is visible.
        drained = 0
        while not self._cb_queue.empty():
            try:
                self._cb_queue.get_nowait()
                drained += 1
            except queue.Empty:
                break
        if drained:
            logger.warning("VENC callback queue drained %d leftover frame(s)", drained)

        ret = _acl_media.venc_send_frame(self._channel_desc, pic_desc,
                                          stream_desc, self._frame_config, None)
        if ret != ACL_SUCCESS:
            _acl_media.dvpp_free(input_buffer)
            _acl_media.dvpp_free(out_buffer)
            _acl_media.dvpp_destroy_pic_desc(pic_desc)
            _acl_media.dvpp_destroy_stream_desc(stream_desc)
            raise RuntimeError(f"venc_send_frame failed: {ret}")

        try:
            encoded = self._cb_queue.get(timeout=5.0)
        except queue.Empty:
            encoded = None

        if encoded is None:
            logger.debug("VENC produced no output for this frame (encoder buffering)")
            encoded = b""

        # Cleanup
        _acl_media.dvpp_free(input_buffer)
        _acl_media.dvpp_free(out_buffer)
        _acl_media.dvpp_destroy_stream_desc(stream_desc)
        # pic_desc is destroyed in callback

        if force_keyframe:
            _acl_media.venc_set_frame_config_force_i_frame(self._frame_config, False)

        return encoded or b""

    def destroy(self):
        self._running = False
        if self._channel_desc is not None:
            channel_desc = self._channel_desc
            self._channel_desc = None
            ret = _acl_media.venc_destroy_channel(channel_desc)
            if not _is_success(ret):
                logger.warning("venc_destroy_channel failed: %s", ret)
            if hasattr(_acl_media, "venc_destroy_channel_desc"):
                ret = _acl_media.venc_destroy_channel_desc(channel_desc)
                if not _is_success(ret):
                    logger.warning("venc_destroy_channel_desc failed: %s", ret)
        if self._callback_tid is not None:
            stop_thread = getattr(_acl_util, "stop_thread", None)
            if stop_thread is not None:
                ret = stop_thread(self._callback_tid)
                if not _is_success(ret):
                    logger.warning("acl.util.stop_thread failed: %s", ret)
            self._callback_tid = None
        if self._frame_config is not None:
            ret = _acl_media.venc_destroy_frame_config(self._frame_config)
            if not _is_success(ret):
                logger.warning("venc_destroy_frame_config failed: %s", ret)
            self._frame_config = None
        logger.info("CANN VENC channel destroyed")


def ctypes_copy_bytes(ptr, size):
    """Copy bytes from a ctypes pointer to a Python bytes object."""
    import ctypes
    return ctypes.string_at(ptr, size)


# ---------------------------------------------------------------------------
#  aiortc-compatible H264 encoder using CANN VENC
# ---------------------------------------------------------------------------
class CannH264Encoder(H264Encoder):
    """aiortc H264 encoder backed by CANN VENC hardware.

    Replaces libx264 encoding with Ascend 310B VENC.
    Inherits _packetize / pack / _split_bitstream from H264Encoder.
    """

    def __init__(self):
        self._target_bitrate_bps: int = 0
        super().__init__()
        self._venc: Optional[CannVenc] = None
        self._last_width: int = 0
        self._last_height: int = 0
        self._last_fps: int = 0
        self._last_bitrate: int = 0
        self._last_timestamp_sec: Optional[float] = None
        self._perf_log_count: int = 0

    def close(self) -> None:
        if self._venc is not None:
            self._venc.destroy()
            self._venc = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    @property
    def target_bitrate(self) -> int:
        return self._target_bitrate_bps

    @target_bitrate.setter
    def target_bitrate(self, bitrate: int) -> None:
        self._target_bitrate_bps = max(0, int(bitrate))

    def _estimate_fps(self, frame: av.VideoFrame) -> int:
        if frame.pts is None or frame.time_base is None:
            return self._last_fps or 30

        timestamp_sec = float(frame.pts * frame.time_base)
        if self._last_timestamp_sec is None:
            self._last_timestamp_sec = timestamp_sec
            return self._last_fps or 30

        delta = timestamp_sec - self._last_timestamp_sec
        self._last_timestamp_sec = timestamp_sec
        if delta <= 0:
            return self._last_fps or 30

        fps = round(1.0 / delta)
        return max(1, min(fps, 120))

    def _ensure_venc(self, width: int, height: int, fps: int):
        session_bitrate_kbps = get_session_bitrate_override_kbps()
        if session_bitrate_kbps is not None:
            bitrate = session_bitrate_kbps
        else:
            bitrate = _resolve_venc_bitrate_kbps(
                width=width,
                height=height,
                fps=fps,
                codec="h264",
                target_bitrate_bps=self.target_bitrate,
            )
        if (self._venc is not None
                and self._last_width == width
                and self._last_height == height
                and self._last_fps == fps
                and self._last_bitrate == bitrate):
            return
        if self._venc is not None:
            self._venc.destroy()
        self._venc = CannVenc(width=width, height=height, fps=fps, bitrate=bitrate)
        _notify_encoder_status("cann-venc-h264", True)
        self._last_width = width
        self._last_height = height
        self._last_fps = fps
        self._last_bitrate = bitrate
        self.buffer_data = b""
        self.buffer_pts = None

    def _encode_frame(
        self, frame: av.VideoFrame, force_keyframe: bool
    ) -> Iterator[bytes]:
        if not _CANN_READY:
            # Fallback to CPU libx264
            yield from super()._encode_frame(frame, force_keyframe)
            return

        fps = self._estimate_fps(frame)
        try:
            self._ensure_venc(frame.width, frame.height, fps=fps)
        except RuntimeError as exc:
            logger.error("CANN VENC initialization failed: %s, falling back to libx264", exc)
            self.close()
            _notify_encoder_status("cpu-libx264-fallback", False, str(exc))
            yield from super()._encode_frame(frame, force_keyframe)
            return

        # NV12 passthrough: if the track already prepared NV12, skip PyAV's
        # expensive RGB/BGR -> NV12 reformat step. VENC still pads rows when
        # the source width is not 16-aligned.
        if getattr(frame.format, "name", None) == "nv12":
            t0 = time.perf_counter()
            nv12 = frame.to_ndarray(format="nv12")
            pre_padded = frame.width % 16 == 0
            convert_ms = (time.perf_counter() - t0) * 1000
            if self._perf_log_count < 5:
                logger.info(
                    "VENC input ndarray frame=%d format=nv12 pre_padded=%s ndarray_ms=%.1f",
                    self._perf_log_count + 1,
                    pre_padded,
                    convert_ms,
                )
        else:
            t0 = time.perf_counter()
            nv12_frame = frame.reformat(format="nv12")
            t1 = time.perf_counter()
            nv12 = nv12_frame.to_ndarray(format="nv12")
            pre_padded = False
            convert_ms = (time.perf_counter() - t1) * 1000
            if self._perf_log_count < 5:
                logger.info(
                    "VENC input convert frame=%d reformat_ms=%.1f ndarray_ms=%.1f",
                    self._perf_log_count + 1,
                    (t1 - t0) * 1000,
                    convert_ms,
                )

        try:
            t0 = time.perf_counter()
            encoded = self._venc.encode(nv12, force_keyframe=force_keyframe, pre_padded=pre_padded)
            encode_ms = (time.perf_counter() - t0) * 1000
        except RuntimeError as exc:
            logger.error("CANN VENC encode failed: %s, falling back to libx264", exc)
            self.close()
            _notify_encoder_status("cpu-libx264-fallback", False, str(exc))
            yield from super()._encode_frame(frame, force_keyframe)
            return

        if self._perf_log_count < 5:
            logger.info(
                "VENC encode frame=%d size=%dx%d fps=%d pre_padded=%s encode_ms=%.1f bytes=%d",
                self._perf_log_count + 1,
                frame.width,
                frame.height,
                fps,
                pre_padded,
                encode_ms,
                len(encoded),
            )
            self._perf_log_count += 1

        if encoded:
            yield from self._split_bitstream(encoded)
