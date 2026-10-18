from __future__ import annotations

from pathlib import Path
import struct
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

    def test_list_v4l2_devices_filters_metadata_nodes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dev = root / "dev"
            sysfs = root / "sys"
            dev.mkdir()
            (dev / "video10").touch()
            (dev / "video2").touch()
            (dev / "video3").touch()
            (dev / "not-a-camera").touch()
            name_path = sysfs / "video2" / "name"
            name_path.parent.mkdir(parents=True)
            name_path.write_text("USB Palm Camera\n", encoding="utf-8")

            devices = list_v4l2_devices(
                dev,
                sysfs,
                capture_probe=lambda path: path.name in {"video2", "video10"},
            )

        self.assertEqual([(item.index, item.path, item.name) for item in devices], [
            (2, str(dev / "video2"), "USB Palm Camera"),
            (10, str(dev / "video10"), ""),
        ])

    def test_capture_probe_rejects_capture_node_with_empty_format(self):
        """The ISP media node advertises capture but reports a 0x0 format.

        Device numbering is not stable: `/dev/video0` can be either the USB
        camera or the ISP node depending on enumeration order.  The probe must
        therefore check a real pixel format, not just capability bits.
        """

        from palmprint_workbench.runtime.camera import (
            _VIDIOC_G_FMT,
            _VIDIOC_QUERYCAP,
            _is_video_capture_node,
        )

        def fake_ioctl(descriptor, request, buffer, mutate):
            del descriptor, mutate
            if request == _VIDIOC_QUERYCAP:
                # capabilities: DEVICE_CAPS; device caps: multi-planar capture.
                struct.pack_into("=II", buffer, 84, 0x80000000, 0x04201000)
            elif request == _VIDIOC_G_FMT:
                struct.pack_into("=II", buffer, 4, 0, 0)
            return 0

        with patch("os.open", return_value=5), patch("os.close"), patch(
            "fcntl.ioctl", side_effect=fake_ioctl
        ):
            self.assertFalse(_is_video_capture_node(Path("/dev/video2")))

        def fake_usb_ioctl(descriptor, request, buffer, mutate):
            del descriptor, mutate
            if request == _VIDIOC_QUERYCAP:
                struct.pack_into("=II", buffer, 84, 0x80000000, 0x04200001)
            elif request == _VIDIOC_G_FMT:
                struct.pack_into("=II", buffer, 4, 1280, 720)
            return 0

        with patch("os.open", return_value=5), patch("os.close"), patch(
            "fcntl.ioctl", side_effect=fake_usb_ioctl
        ):
            self.assertTrue(_is_video_capture_node(Path("/dev/video0")))

    def test_list_v4l2_devices_groups_nodes_of_one_physical_camera(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dev = root / "dev"
            sysfs = root / "sys"
            dev.mkdir()
            (dev / "video0").touch()
            (dev / "video1").touch()
            (sysfs / "usb-1-1").mkdir(parents=True)
            for name in ("video0", "video1"):
                node = sysfs / name
                node.mkdir(parents=True)
                (node / "device").symlink_to(sysfs / "usb-1-1", target_is_directory=True)
            devices = list_v4l2_devices(
                dev,
                sysfs,
                capture_probe=lambda _path: True,
            )

        self.assertEqual(len(devices), 1)
        self.assertEqual(devices[0].path, str(dev / "video0"))
        self.assertEqual(devices[0].aliases, (str(dev / "video0"), str(dev / "video1")))

    def test_capability_probe_rejects_metadata_capture_node(self):
        from palmprint_workbench.runtime import camera as camera_module

        def write_capability(_descriptor, _request, payload, _mutate):
            # A UVC metadata endpoint sets DEVICE_CAPS but does not advertise
            # either single- or multi-planar Video Capture.
            import struct

            struct.pack_into(
                "=II",
                payload,
                84,
                camera_module._V4L2_CAP_DEVICE_CAPS,
                0x00800000,
            )

        with patch.object(camera_module.os, "open", return_value=17), patch.object(
            camera_module.os, "close"
        ), patch.object(camera_module, "fcntl") as fcntl_mock:
            fcntl_mock.ioctl.side_effect = write_capability
            self.assertFalse(camera_module._is_video_capture_node(Path("/dev/video1")))

    def test_capability_probe_accepts_video_capture_node(self):
        from palmprint_workbench.runtime import camera as camera_module

        def write_capability(_descriptor, _request, payload, _mutate):
            import struct

            struct.pack_into(
                "=II",
                payload,
                84,
                camera_module._V4L2_CAP_DEVICE_CAPS,
                camera_module._V4L2_CAP_VIDEO_CAPTURE,
            )

        with patch.object(camera_module.os, "open", return_value=17), patch.object(
            camera_module.os, "close"
        ), patch.object(camera_module, "fcntl") as fcntl_mock:
            fcntl_mock.ioctl.side_effect = write_capability
            self.assertTrue(camera_module._is_video_capture_node(Path("/dev/video0")))

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
