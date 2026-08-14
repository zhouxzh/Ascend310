from __future__ import annotations

from io import BytesIO
from pathlib import Path
import subprocess

import numpy as np

from time_frequency_dashboard.acquisition.sigrok import SigrokCapture
from time_frequency_dashboard.acquisition.frame_protocol import BridgeFrame


class FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes = b"", return_code: int = 0) -> None:
        self.stdout = BytesIO(stdout)
        self.stderr = BytesIO(stderr)
        self.return_code = return_code
        self.terminated = False

    def poll(self):
        return self.return_code

    def wait(self, timeout=None):
        del timeout
        return self.return_code

    def send_signal(self, _signal):
        return None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.terminated = True


class ImmortalProcess(FakeProcess):
    """A process stub which remains alive despite both termination attempts."""

    def __init__(self) -> None:
        super().__init__(b"", return_code=None)
        self.killed = False

    def poll(self):
        return None

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired("sigrok_capture_bridge", timeout)

    def kill(self):
        self.killed = True


def make_capture(process, frames, errors, *, rate=1_000.0):
    return SigrokCapture(
        sample_rate_hz=rate,
        frame_samples=4,
        callback_msec=40,
        ch1_volts_per_division=0.5,
        ch2_volts_per_division=0.25,
        ch1_probe_ratio=10.0,
        ch2_probe_ratio=2.0,
        bridge_path=Path("build/sigrok_capture_bridge"),
        frame_callback=frames.append,
        error_callback=errors.append,
        process_factory=lambda *_args, **_kwargs: process,
    )


def test_sigrok_command_uses_integer_rational_vdiv_values():
    capture = make_capture(FakeProcess(b""), [], [])
    command = capture.command()
    assert Path(command[0]) == Path("build/sigrok_capture_bridge")
    assert command[1:] == [
        "1000",
        "4",
        "40",
        "500",
        "1000",
        "250",
        "1000",
    ]


def test_sigrok_binary_frames_apply_probe_ratios_and_report_unexpected_continuous_eof():
    original = BridgeFrame(
        sequence=0,
        host_receive_ns=123,
        sample_rate_hz=1_000.0,
        flags=1,
        samples=np.asarray([[0.1, -0.2], [0.2, 0.3], [0.3, 0.4], [0.4, 0.5]]),
    )
    frames = []
    errors = []
    capture = make_capture(FakeProcess(original.to_bytes()), frames, errors)

    capture._run()

    # A finite fake stream is EOF for a process which is required to run
    # continuously.  Earlier frames remain valid, but the controller must be
    # told that the live capture stopped unexpectedly.
    assert len(errors) == 1
    assert "ended unexpectedly" in errors[0]
    assert len(frames) == 1
    assert frames[0].flags == 1
    np.testing.assert_allclose(frames[0].samples[:, 0], original.samples[:, 0] * 10.0)
    np.testing.assert_allclose(frames[0].samples[:, 1], original.samples[:, 1] * 2.0)


def test_sigrok_rejects_wire_sequence_discontinuity():
    bad = BridgeFrame(
        sequence=2,
        host_receive_ns=123,
        sample_rate_hz=1_000.0,
        flags=0,
        samples=np.zeros((4, 2), dtype=np.float32),
    )
    errors = []
    capture = make_capture(FakeProcess(bad.to_bytes(), b"driver detail\n"), [], errors)

    capture._run()

    assert len(errors) == 1
    assert "sequence discontinuity" in errors[0]
    assert "driver detail" in errors[0]


def test_sigrok_rejects_actual_rate_that_does_not_match_fixed_om():
    frame = BridgeFrame(
        sequence=0,
        host_receive_ns=123,
        sample_rate_hz=500.0,
        flags=0,
        samples=np.zeros((4, 2), dtype=np.float32),
    )
    errors = []
    capture = make_capture(FakeProcess(frame.to_bytes()), [], errors, rate=1_000.0)

    capture._run()

    assert len(errors) == 1
    assert "fixed OM expects 1000 S/s" in errors[0]


def test_sigrok_stop_reports_termination_before_discarding_worker_reference():
    capture = make_capture(FakeProcess(b""), [], [])
    capture._thread = __import__("threading").Thread(target=lambda: None)

    assert capture.stop()
    assert capture.wait_stopped(timeout=0.0)


def test_sigrok_stop_keeps_ownership_when_bridge_refuses_to_exit():
    process = ImmortalProcess()
    capture = make_capture(process, [], [])
    capture._process = process

    assert not capture.stop()
    assert process.terminated
    assert process.killed
    assert capture._process is process


def test_sigrok_does_not_open_a_bridge_after_stop_was_requested():
    errors = []
    capture = make_capture(FakeProcess(b""), [], errors)
    opened = []

    def fail_if_opened(*_args, **_kwargs):
        opened.append(True)
        raise AssertionError("bridge should not start after Stop")

    capture._process_factory = fail_if_opened
    capture._stop.set()
    capture._run()

    assert opened == []
    assert errors == []


def test_sigrok_rejects_truncated_wire_frame_before_reporting_continuous_eof():
    errors = []
    original = BridgeFrame(
        sequence=0,
        host_receive_ns=123,
        sample_rate_hz=1_000.0,
        flags=0,
        samples=np.zeros((4, 2), dtype=np.float32),
    )
    capture = make_capture(FakeProcess(original.to_bytes()[:-1]), [], errors)

    capture._run()

    assert len(errors) == 1
    assert "truncated frame bytes" in errors[0]
