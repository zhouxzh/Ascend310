"""DVPP JPEGD hardware decoder — JPEG → NV12 on Ascend 310B."""

from __future__ import annotations

import logging
from typing import Optional

import numpy as np

import webrtc_app.cann_encoder as _ce

logger = logging.getLogger("dvpp_jpegd")

_ACL_SUCCESS = 0
_ACL_MEMCPY_HOST_TO_DEVICE = 1
_ACL_MEMCPY_DEVICE_TO_HOST = 2
_PIX_FMT_NV12 = 1


class DvppJpegDecoder:
    """Hardware JPEG decoder using DVPP JPEGD on Ascend 310B.

    Decodes MJPEG frames (raw JPEG bitstream) to NV12 YUV 4:2:0.
    Uses a generic DVPP channel + Stream sync model (same as VPC).
    """

    def __init__(self) -> None:
        self._ch_desc = None
        self._stream = None
        self._jpeg_dev: Optional[int] = None
        self._jpeg_dev_size: int = 0
        self._dec_buf: Optional[int] = None
        self._dec_buf_size: int = 0
        self._pic_desc: Optional[int] = None
        self._w: int = 0
        self._h: int = 0
        self._stride_w: int = 0
        self._stride_h: int = 0
        self._destroyed = False

        if not _ce._init_acl():
            raise RuntimeError("CANN ACL initialization failed — cannot create JPEGD decoder")

        media = _ce._acl_media
        rt = _ce._acl_rt

        try:
            # DVPP channel (generic, no mode setting needed)
            self._ch_desc = media.dvpp_create_channel_desc()
            ret = media.dvpp_create_channel(self._ch_desc)
            if ret != _ACL_SUCCESS:
                raise RuntimeError(f"dvpp_create_channel failed: {ret}")

            # Stream for async sync
            self._stream, ret = rt.create_stream()
            if ret != _ACL_SUCCESS:
                raise RuntimeError(f"create_stream failed: {ret}")

            # Save context for cross-thread use (executor threads need set_context)
            self._ctx, ret = _ce._acl_rt.get_context(0)
            if ret != _ACL_SUCCESS:
                raise RuntimeError(f"get_context failed: {ret}")
        except Exception:
            self.destroy()
            raise
        logger.info("DvppJpegDecoder initialized")

    @property
    def width(self) -> int:
        return self._w

    @property
    def height(self) -> int:
        return self._h

    @property
    def nv12_shape(self) -> tuple[int, int]:
        """2D shape for NV12 output (rows, columns) from predict_dec_size."""
        stride_w = ((self._w + 15) // 16) * 16
        rows = self._dec_buf_size // stride_w if self._dec_buf_size else 0
        return (rows, stride_w)

    def _ensure_jpeg_buf(self, size: int) -> None:
        media = _ce._acl_media
        if self._jpeg_dev is not None and self._jpeg_dev_size >= size:
            return
        if self._jpeg_dev is not None:
            media.dvpp_free(self._jpeg_dev)
        self._jpeg_dev, ret = media.dvpp_malloc(size)
        if ret != _ACL_SUCCESS or self._jpeg_dev is None:
            raise RuntimeError(f"dvpp_malloc jpeg_dev failed: {ret}")
        self._jpeg_dev_size = size

    def _init_dec_buf(self, jpeg_size: int) -> None:
        """Allocate output buffer using predict_dec_size (must be called per JPEGD spec)."""
        media = _ce._acl_media
        dec_size, ret = media.dvpp_jpeg_predict_dec_size(
            self._jpeg_dev, jpeg_size, _PIX_FMT_NV12)
        if ret != _ACL_SUCCESS:
            raise RuntimeError(f"predict_dec_size failed: {ret}")

        stride_w = ((self._w + 15) // 16) * 16
        stride_h = ((self._h + 1) // 2) * 2
        self._stride_w = stride_w
        self._stride_h = stride_h

        if self._dec_buf is not None:
            media.dvpp_free(self._dec_buf)
        self._dec_buf, ret = media.dvpp_malloc(dec_size)
        if ret != _ACL_SUCCESS or self._dec_buf is None:
            raise RuntimeError(f"dvpp_malloc dec_buf failed: {ret}")
        self._dec_buf_size = dec_size

        if self._pic_desc is not None:
            media.dvpp_destroy_pic_desc(self._pic_desc)
        self._pic_desc = media.dvpp_create_pic_desc()
        media.dvpp_set_pic_desc_data(self._pic_desc, self._dec_buf)
        media.dvpp_set_pic_desc_size(self._pic_desc, dec_size)
        media.dvpp_set_pic_desc_format(self._pic_desc, _PIX_FMT_NV12)
        media.dvpp_set_pic_desc_width(self._pic_desc, self._w)
        media.dvpp_set_pic_desc_height(self._pic_desc, self._h)
        media.dvpp_set_pic_desc_width_stride(self._pic_desc, stride_w)
        media.dvpp_set_pic_desc_height_stride(self._pic_desc, stride_h)

    def decode(self, jpeg_bytes: bytes) -> np.ndarray:
        """Decode one JPEG frame to NV12 (stride-aligned ndarray)."""
        if self._destroyed:
            raise RuntimeError("DvppJpegDecoder has been destroyed")

        media = _ce._acl_media
        rt = _ce._acl_rt

        # Thread pool threads don't inherit the ACL context — set it explicitly
        rt.set_context(self._ctx)

        jpeg_size = len(jpeg_bytes)

        # Ensure input buffer
        self._ensure_jpeg_buf(jpeg_size)

        # H2D: JPEG → device
        jpeg_arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        ret = rt.memcpy(
            self._jpeg_dev, jpeg_size,
            jpeg_arr.ctypes.data, jpeg_size,
            _ACL_MEMCPY_HOST_TO_DEVICE,
        )
        if ret != _ACL_SUCCESS:
            raise RuntimeError(f"memcpy H2D JPEG failed: {ret}")

        # First frame: query image info + allocate output
        if self._w == 0:
            w, h, _fmt, ret = media.dvpp_jpeg_get_image_info(self._jpeg_dev, jpeg_size)
            if ret != _ACL_SUCCESS:
                raise RuntimeError(f"jpeg_get_image_info failed: {ret}")
            self._w, self._h = w, h
            self._init_dec_buf(jpeg_size)
            logger.info("JPEGD first frame: %dx%d  dec_buf=%d", w, h, self._dec_buf_size)

        # JPEGD async + sync
        ret = media.dvpp_jpeg_decode_async(
            self._ch_desc, self._jpeg_dev, jpeg_size, self._pic_desc, self._stream)
        if ret != _ACL_SUCCESS:
            raise RuntimeError(f"jpeg_decode_async failed: {ret}")
        ret = rt.synchronize_stream(self._stream)
        if ret != _ACL_SUCCESS:
            raise RuntimeError(f"synchronize_stream failed: {ret}")

        # D2H: NV12 device → host
        nv12 = np.zeros(self._dec_buf_size, dtype=np.uint8)
        ret = rt.memcpy(
            nv12.ctypes.data, self._dec_buf_size,
            self._dec_buf, self._dec_buf_size,
            _ACL_MEMCPY_DEVICE_TO_HOST,
        )
        if ret != _ACL_SUCCESS:
            raise RuntimeError(f"memcpy D2H NV12 failed: {ret}")

        return nv12

    def destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True

        media = _ce._acl_media
        rt = _ce._acl_rt

        if self._pic_desc is not None:
            media.dvpp_destroy_pic_desc(self._pic_desc)
            self._pic_desc = None
        if self._dec_buf is not None:
            media.dvpp_free(self._dec_buf)
            self._dec_buf = None
        if self._jpeg_dev is not None:
            media.dvpp_free(self._jpeg_dev)
            self._jpeg_dev = None
        if self._stream is not None:
            rt.destroy_stream(self._stream)
            self._stream = None
        if self._ch_desc is not None:
            media.dvpp_destroy_channel(self._ch_desc)
            media.dvpp_destroy_channel_desc(self._ch_desc)
            self._ch_desc = None

        logger.info("DvppJpegDecoder destroyed")
