from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

try:
    import cv2
    import numpy as np
    from palmprint_workbench.runtime.camera import (
        CameraCapture,
        CameraError,
        capture_frame,
        list_v4l2_devices,
    )
except ImportError:  # Local authoring hosts may not have the board image stack.
    cv2 = None
    np = None


class FakeVideoCapture:
    def __init__(self, opened: bool = True, frame: np.ndarray | None = None) -> None:
        self.opened = opened
        self.frame = (
            frame.copy()
            if frame is not None
            else np.array([[[1, 2, 3], [4, 5, 6]]], dtype=np.uint8)
        )
        self.released = False
        self.properties: list[tuple[int, float]] = []

    def isOpened(self) -> bool:
        return self.opened and not self.released

    def read(self):
        return self.isOpened(), self.frame.copy()

    def release(self) -> None:
        self.released = True

    def set(self, property_id: int, value: float) -> bool:
        self.properties.append((property_id, value))
        return True


@unittest.skipUnless(cv2 is not None, "camera tests require OpenCV")
class CameraCaptureTests(unittest.TestCase):

    def test_list_v4l2_devices_uses_filesystem_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dev = root / "dev"
            sysfs = root / "sys"
            dev.mkdir()
            (dev / "video10").touch()
            (dev / "video2").touch()
            (dev / "not-a-camera").touch()
            name_path = sysfs / "video2" / "name"
            name_path.parent.mkdir(parents=True)
            name_path.write_text("USB Palm Camera\n", encoding="utf-8")

            devices = list_v4l2_devices(dev, sysfs)

        self.assertEqual([(item.index, item.path, item.name) for item in devices], [
            (2, str(dev / "video2"), "USB Palm Camera"),
            (10, str(dev / "video10"), ""),
        ])

    def test_capture_returns_rgb_and_jpeg_and_sets_requested_properties(self):
        created: list[tuple[int | str, int]] = []
        fake = FakeVideoCapture()

        def factory(device, backend):
            created.append((device, backend))
            return fake

        camera = CameraCapture(
            2, width=640, height=480, fps=25, jpeg_quality=82, capture_factory=factory
        )
        frame = camera.capture()

        self.assertEqual(created, [(2, cv2.CAP_V4L2)])
        np.testing.assert_array_equal(frame.rgb[0, 0], np.array([3, 2, 1], dtype=np.uint8))
        self.assertEqual(frame.device, "2")
        self.assertGreater(frame.timestamp_ns, 0)
        self.assertTrue(frame.jpeg.startswith(b"\xff\xd8"))
        configured = dict(fake.properties)
        self.assertEqual(configured[cv2.CAP_PROP_FRAME_WIDTH], 640.0)
        self.assertEqual(configured[cv2.CAP_PROP_FRAME_HEIGHT], 480.0)
        self.assertEqual(configured[cv2.CAP_PROP_FPS], 25.0)
        self.assertIn(cv2.CAP_PROP_FOURCC, configured)
        self.assertIn(cv2.CAP_PROP_BUFFERSIZE, configured)
        camera.close()
        self.assertTrue(fake.released)

    def test_open_failure_releases_capture_without_accessing_real_hardware(self):
        fake = FakeVideoCapture(opened=False)
        camera = CameraCapture(0, capture_factory=lambda *_args: fake)

        with self.assertRaisesRegex(CameraError, "Unable to open camera 0"):
            camera.open()

        self.assertTrue(fake.released)
        self.assertFalse(camera.is_open)

    def test_capture_rejects_empty_frame_and_context_manager_closes(self):
        fake = FakeVideoCapture(frame=np.empty((0, 0, 3), dtype=np.uint8))
        with CameraCapture(0, capture_factory=lambda *_args: fake) as camera:
            with self.assertRaisesRegex(CameraError, "returned no frame"):
                camera.capture()
        self.assertTrue(fake.released)

    def test_capture_frame_and_invalid_sources(self):
        fake = FakeVideoCapture()
        frame = capture_frame(1, capture_factory=lambda *_args: fake)
        self.assertEqual(frame.rgb.shape, (1, 2, 3))
        self.assertTrue(fake.released)
        with self.assertRaisesRegex(ValueError, "Camera path"):
            CameraCapture("rtsp://unsafe-source")
        with self.assertRaisesRegex(ValueError, "non-negative"):
            CameraCapture(-1)

    def test_rgb_only_capture_skips_full_frame_jpeg_encode(self):
        fake = FakeVideoCapture()
        camera = CameraCapture(0, capture_factory=lambda *_args: fake)
        with patch(
            "palmprint_workbench.runtime.camera.cv2.imencode",
            side_effect=AssertionError("unexpected JPEG encode"),
        ):
            frame = camera.capture(encode_jpeg=False)
        self.assertEqual(frame.jpeg, b"")
        self.assertEqual(frame.rgb.shape, (1, 2, 3))
        camera.close()


if __name__ == "__main__":
    unittest.main()
