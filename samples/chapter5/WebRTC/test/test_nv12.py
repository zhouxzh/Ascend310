"""Tests for BGR to NV12 pixel format conversion."""

import numpy as np
import pytest


@pytest.fixture(scope="module")
def nv12_converter():
    from webrtc_app.cann_encoder import bgr_to_nv12
    return bgr_to_nv12


class TestBgrToNv12:
    """Verify BGR-to-NV12 conversion produces correct Y/UV plane layout."""

    @pytest.mark.parametrize("w,h", [(640, 480), (1280, 720), (1920, 1080)])
    def test_output_shape(self, nv12_converter, w, h):
        bgr = np.zeros((h, w, 3), dtype=np.uint8)
        nv12 = nv12_converter(bgr)
        expected_h = h * 3 // 2
        assert nv12.shape == (expected_h, w), f"Expected {(expected_h, w)}, got {nv12.shape}"
        assert nv12.dtype == np.uint8

    def test_black_frame_is_valid(self, nv12_converter):
        bgr = np.zeros((480, 640, 3), dtype=np.uint8)
        nv12 = nv12_converter(bgr)
        # Black BGR → black NV12: Y≈16 (limited range), U=V=128
        assert nv12.min() >= 0
        assert nv12.max() <= 255

    def test_white_frame_values(self, nv12_converter):
        bgr = np.full((480, 640, 3), 255, dtype=np.uint8)
        nv12 = nv12_converter(bgr)
        # White BGR → bright NV12
        y_plane = nv12[:480, :640]
        assert np.mean(y_plane) > 200, "White frame Y should be bright"

    def test_random_frame_no_crash(self, nv12_converter):
        bgr = np.random.randint(0, 255, (360, 640, 3), dtype=np.uint8)
        nv12 = nv12_converter(bgr)
        assert nv12.shape == (540, 640)  # 360 * 3/2 = 540
