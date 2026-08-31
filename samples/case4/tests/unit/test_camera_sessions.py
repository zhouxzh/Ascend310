from __future__ import annotations

from types import SimpleNamespace
import threading
import unittest
from unittest.mock import patch

try:
    from palmprint_workbench.runtime.camera import CameraError
    from palmprint_workbench.services.workbench import Workbench
except ImportError:  # Local authoring hosts may not have OpenCV/pandas.
    CameraError = None
    Workbench = None


class _FakeCamera:
    created: list["_FakeCamera"] = []
    fail_once = False

    def __init__(self, device, *, width, height, fps):
        self.device = str(device)
        self.width = width
        self.height = height
        self.closed = False
        self.is_open = True
        self.fail_next = bool(type(self).fail_once)
        type(self).fail_once = False
        type(self).created.append(self)

    def close(self):
        self.closed = True
        self.is_open = False

    def capture(self, *, encode_jpeg=True):
        if self.fail_next:
            self.fail_next = False
            raise CameraError("simulated unplug")
        return SimpleNamespace(
            rgb=object(),
            jpeg=b"jpeg" if encode_jpeg else b"",
            timestamp_ns=1,
            device=self.device,
        )


@unittest.skipUnless(Workbench is not None and CameraError is not None, "runtime dependencies unavailable")
class CameraSessionTests(unittest.TestCase):
    def make_workbench(self):
        workbench = object.__new__(Workbench)
        workbench.camera_lock = threading.RLock()
        workbench.cameras = {}
        workbench.camera_sessions = {}
        workbench._state_lock = threading.RLock()
        workbench._closing = False
        return workbench

    def setUp(self):
        _FakeCamera.created = []
        _FakeCamera.fail_once = False

    def test_stale_session_cannot_close_or_capture_current_camera(self):
        workbench = self.make_workbench()
        with patch("palmprint_workbench.services.workbench.CameraCapture", _FakeCamera):
            workbench.open_camera_session("/dev/video0", 1280, 720, "old")
            workbench.capture_camera_frame("/dev/video0", 1280, 720, session="old")
            old_camera = _FakeCamera.created[-1]

            workbench.open_camera_session("/dev/video0", 1280, 720, "new")
            workbench.capture_camera_frame("/dev/video0", 1280, 720, session="new")
            new_camera = _FakeCamera.created[-1]

            self.assertTrue(old_camera.closed)
            self.assertIsNot(old_camera, new_camera)
            with self.assertRaisesRegex(CameraError, "会话已切换"):
                workbench.capture_camera_frame("/dev/video0", 1280, 720, session="old")
            workbench.close_cameras(device="/dev/video0", session="old")
            self.assertFalse(new_camera.closed)
            workbench.close_cameras(device="/dev/video0", session="new")
            self.assertTrue(new_camera.closed)

    def test_failed_read_reopens_one_fresh_handle(self):
        workbench = self.make_workbench()
        _FakeCamera.fail_once = True
        with patch("palmprint_workbench.services.workbench.CameraCapture", _FakeCamera):
            workbench.open_camera_session("/dev/video0", 640, 480, "session")
            frame = workbench.capture_camera_frame(
                "/dev/video0", 640, 480, session="session", encode_jpeg=False
            )
        self.assertIsNotNone(frame)
        self.assertEqual(len(_FakeCamera.created), 2)
        self.assertTrue(_FakeCamera.created[0].closed)
        self.assertFalse(_FakeCamera.created[1].closed)

    def test_switching_devices_invalidates_old_device_token(self):
        workbench = self.make_workbench()
        with patch("palmprint_workbench.services.workbench.CameraCapture", _FakeCamera):
            workbench.open_camera_session("/dev/video2", 640, 480, "old-device")
            workbench.open_camera_session("/dev/video3", 640, 480, "new-device")
            with self.assertRaisesRegex(CameraError, "会话已切换"):
                workbench.capture_camera_frame(
                    "/dev/video2", 640, 480, session="old-device"
                )


if __name__ == "__main__":
    unittest.main()
