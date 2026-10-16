"""Tests for CANN VENC hardware encoder.

Run on Ascend 310B:
    pytest test/test_cann_venc.py -v
"""

import numpy as np
import pytest

from test.conftest import needs_cann


def test_estimate_venc_bitrate_scales_with_format():
    """Auto bitrate should scale conservatively with source format."""
    pytest.importorskip("av")
    pytest.importorskip("aiortc")
    from webrtc_app.cann_encoder import estimate_venc_bitrate_kbps

    assert estimate_venc_bitrate_kbps(320, 240, 15) == 500
    assert estimate_venc_bitrate_kbps(640, 480, 30) == 500
    assert estimate_venc_bitrate_kbps(1920, 1080, 30) == 2488
    assert estimate_venc_bitrate_kbps(1920, 1080, 60) == 4977
    assert estimate_venc_bitrate_kbps(3840, 2160, 60) == 6000
    assert estimate_venc_bitrate_kbps(1920, 1080, 30, codec="h265") < estimate_venc_bitrate_kbps(1920, 1080, 30)


def test_offer_payload_accepts_dimensions_and_fps():
    pytest.importorskip("aiortc")
    from server import parse_offer_payload

    payload = {"sdp": "v=0\n", "type": "offer", "width": 1920, "height": 1080, "fps": 60}

    assert parse_offer_payload(payload)[1:] == (1920, 1080, 60, None)


def test_offer_payload_accepts_manual_bitrate():
    pytest.importorskip("aiortc")
    from server import parse_offer_payload

    payload = {
        "sdp": "v=0\n",
        "type": "offer",
        "width": 1280,
        "height": 720,
        "fps": 30,
        "bitrate_kbps": 1800,
    }

    assert parse_offer_payload(payload)[1:] == (1280, 720, 30, 1800)


def _nv12_setup():
    from webrtc_app.cann_encoder import bgr_to_nv12
    bgr = np.zeros((480, 640, 3), dtype=np.uint8)
    return bgr_to_nv12(bgr)


class TestCannVenc:
    """Verify CANN VENC channel lifecycle and encoding."""

    def test_create_destroy(self, venc):
        """VENC channel is created by the module-level fixture."""
        assert venc is not None

    def test_encode_keyframe(self, venc):
        """Encoding with force_keyframe=True produces H264 output."""
        nv12 = _nv12_setup()
        data = venc.encode(nv12, force_keyframe=True)
        assert isinstance(data, bytes)
        assert len(data) > 0, "Keyframe should produce non-empty H264 bitstream"

    def test_encode_non_keyframe(self, venc):
        """Encoding without force_keyframe after a keyframe."""
        nv12 = _nv12_setup()
        venc.encode(nv12, force_keyframe=True)
        data = venc.encode(nv12, force_keyframe=False)
        assert isinstance(data, bytes)

    def test_many_frames(self, venc):
        """Encode a sequence of frames without error."""
        nv12 = _nv12_setup()
        for i in range(10):
            data = venc.encode(nv12, force_keyframe=(i == 0))
            assert isinstance(data, bytes)

    @needs_cann
    def test_different_resolution(self):
        """VENC handles 1280x720 frames."""
        from webrtc_app.cann_encoder import CannVenc, bgr_to_nv12

        v2 = CannVenc(width=1280, height=720, fps=15)
        try:
            bgr = np.zeros((720, 1280, 3), dtype=np.uint8)
            nv12 = bgr_to_nv12(bgr)
            data = v2.encode(nv12, force_keyframe=True)
            assert len(data) > 0
        finally:
            v2.destroy()
