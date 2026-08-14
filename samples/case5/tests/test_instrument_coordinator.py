from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading

import pytest

from time_frequency_dashboard.instrument_coordinator import InstrumentCoordinator


class _FakeAnalysis:
    def __init__(self) -> None:
        self.closed = 0

    def close(self) -> None:
        self.closed += 1


@dataclass
class _HantekSnapshot:
    acquisition_state: str = "STOPPED"
    message: str = "Stopped"


class _FakeHantek:
    def __init__(self) -> None:
        self.analysis = _FakeAnalysis()
        self.started = 0
        self.stopped = 0
        self.closed = 0
        self._snapshot = _HantekSnapshot()

    def initialize_npu(self):
        return None

    def start_hardware(self, _bridge: Path, **_settings) -> None:
        self.started += 1
        self._snapshot = _HantekSnapshot("RUNNING", "Hantek active")

    def start_simulation(self) -> None:
        self.started += 1
        self._snapshot = _HantekSnapshot("RUNNING", "Simulation active")

    def stop(self) -> None:
        self.stopped += 1
        self._snapshot = _HantekSnapshot()

    def snapshot(self) -> _HantekSnapshot:
        return self._snapshot

    def close(self) -> None:
        self.closed += 1
        self._snapshot = _HantekSnapshot()


@dataclass
class _RtlSnapshot:
    state: str = "IDLE"
    message: str = "Stopped"


class _FakeRtl:
    def __init__(self) -> None:
        self.started = 0
        self.stop_requested = 0
        self.closed = 0
        self._snapshot = _RtlSnapshot()

    def start(self, _config) -> _RtlSnapshot:
        self.started += 1
        self._snapshot = _RtlSnapshot("RUNNING", "RTL-SDR active")
        return self._snapshot

    def request_stop(self) -> None:
        self.stop_requested += 1
        self._snapshot = _RtlSnapshot("IDLE", "Stopped")

    def wait_stopped(self, timeout=None) -> bool:
        del timeout
        return self._snapshot.state == "IDLE"

    def snapshot(self) -> _RtlSnapshot:
        return self._snapshot

    def close(self) -> None:
        self.closed += 1
        self._snapshot = _RtlSnapshot()


class _BlockingStartRtl(_FakeRtl):
    def __init__(self) -> None:
        super().__init__()
        self.entered_start = threading.Event()
        self.release_start = threading.Event()

    def start(self, _config) -> _RtlSnapshot:
        self.entered_start.set()
        assert self.release_start.wait(timeout=2.0)
        return super().start(_config)


class _StuckStopRtl(_FakeRtl):
    def request_stop(self) -> None:
        self.stop_requested += 1

    def wait_stopped(self, timeout=None) -> bool:
        del timeout
        return False


class _StuckStopHantek(_FakeHantek):
    def stop(self):
        self.stopped += 1
        # Mirrors a capture whose bounded join elapsed: it remains owned.
        return False


class _UnavailableNpuHantek(_FakeHantek):
    def initialize_npu(self):
        from time_frequency_dashboard.npu import NpuStatus

        return NpuStatus("NPU unavailable", False, "OM is missing")


@dataclass
class _HantekSnapshotWithNpu:
    acquisition_state: str = "RUNNING"
    message: str = "Hantek active"
    npu_status: object | None = None


class _RuntimeUnavailableNpuHantek(_FakeHantek):
    def snapshot(self):
        from time_frequency_dashboard.npu import NpuStatus

        return _HantekSnapshotWithNpu(
            npu_status=NpuStatus("NPU unavailable", False, "NPU worker stopped")
        )


def test_coordinator_rejects_second_source_until_first_fully_stops():
    hantek = _FakeHantek()
    rtl = _FakeRtl()
    coordinator = InstrumentCoordinator(hantek, rtl)

    first = coordinator.start_hantek(Path("bridge"))
    assert first.state == InstrumentCoordinator.RUNNING
    assert first.active_source == "hantek"
    with pytest.raises(RuntimeError, match="stop it first"):
        coordinator.start_rtl_sdr(object())

    assert coordinator.request_stop()
    assert coordinator.wait_stopped(timeout=1.0)
    second = coordinator.start_rtl_sdr(object())
    assert second.state == InstrumentCoordinator.RUNNING
    assert second.active_source == "rtl_sdr"
    assert second.generation == first.generation + 1
    assert hantek.stopped == 1
    assert hantek.analysis.closed == 1

    coordinator.close()


def test_coordinator_stop_is_idempotent_and_window_close_does_not_repeat_release():
    hantek = _FakeHantek()
    rtl = _FakeRtl()
    coordinator = InstrumentCoordinator(hantek, rtl)
    coordinator.start_rtl_sdr(object())

    assert coordinator.request_stop()
    assert not coordinator.request_stop()
    assert coordinator.wait_stopped(timeout=1.0)
    assert rtl.stop_requested == 1

    coordinator.close()
    coordinator.close()
    assert rtl.stop_requested == 1
    assert hantek.closed == 0
    assert rtl.closed == 0


def test_window_close_releases_active_hantek_once_through_the_stop_worker():
    hantek = _FakeHantek()
    rtl = _FakeRtl()
    coordinator = InstrumentCoordinator(hantek, rtl)
    coordinator.start_hantek(Path("bridge"))

    coordinator.close()

    assert hantek.stopped == 1
    assert hantek.analysis.closed == 1
    assert hantek.closed == 0
    assert rtl.closed == 0


def test_coordinator_reconciles_an_asynchronous_rtl_failure():
    hantek = _FakeHantek()
    rtl = _FakeRtl()
    coordinator = InstrumentCoordinator(hantek, rtl)
    coordinator.start_rtl_sdr(object())
    rtl._snapshot = _RtlSnapshot("FAILED", "rtl_sdr: device busy")

    snapshot = coordinator.snapshot()

    assert snapshot.state == InstrumentCoordinator.FAILED
    assert snapshot.active_source == "rtl_sdr"
    assert "device busy" in snapshot.message
    coordinator.close()


def test_failed_start_is_visible_until_the_operator_retries():
    hantek = _FakeHantek()
    rtl = _FakeRtl()
    coordinator = InstrumentCoordinator(hantek, rtl)

    def fail_start(_config):
        raise RuntimeError("manifest hash changed")

    rtl.start = fail_start
    with pytest.raises(RuntimeError, match="hash changed"):
        coordinator.start_rtl_sdr(object())
    failed = coordinator.snapshot()
    assert failed.state == InstrumentCoordinator.FAILED
    assert failed.active_source is None
    assert "hash changed" in failed.message

    def restart(_config):
        rtl._snapshot = _RtlSnapshot("RUNNING", "RTL-SDR active")
        return rtl._snapshot

    rtl.start = restart
    restarted = coordinator.start_rtl_sdr(object())
    assert restarted.state == InstrumentCoordinator.RUNNING
    coordinator.close()


def test_stop_during_start_cannot_leave_a_late_rtl_source_running():
    hantek = _FakeHantek()
    rtl = _BlockingStartRtl()
    coordinator = InstrumentCoordinator(hantek, rtl)
    outcomes: list[object] = []

    thread = threading.Thread(
        target=lambda: outcomes.append(coordinator.start_rtl_sdr(object())),
        daemon=True,
    )
    thread.start()
    assert rtl.entered_start.wait(timeout=1.0)
    assert coordinator.request_stop()
    with pytest.raises(RuntimeError, match="stop it first"):
        coordinator.start_hantek(Path("bridge"))

    rtl.release_start.set()
    thread.join(timeout=2.0)
    assert not thread.is_alive()
    assert outcomes
    assert coordinator.wait_stopped(timeout=1.0)
    assert rtl.stop_requested == 1
    assert coordinator.snapshot().active_source is None
    assert coordinator.start_hantek(Path("bridge")).state == InstrumentCoordinator.RUNNING
    coordinator.close()


def test_cancelling_an_unlaunched_start_reservation_releases_ownership():
    hantek = _FakeHantek()
    rtl = _FakeRtl()
    coordinator = InstrumentCoordinator(hantek, rtl)
    token = coordinator.reserve_rtl_sdr_start()

    assert coordinator.cancel_reserved_start(token)
    assert coordinator.wait_stopped(timeout=1.0)
    assert coordinator.snapshot().active_source is None
    assert rtl.stop_requested == 1


def test_stop_timeout_keeps_ownership_and_blocks_a_second_source():
    hantek = _FakeHantek()
    rtl = _StuckStopRtl()
    coordinator = InstrumentCoordinator(hantek, rtl)
    coordinator.start_rtl_sdr(object())

    assert coordinator.request_stop()
    assert not coordinator.wait_stopped(timeout=1.0)
    snapshot = coordinator.snapshot()
    assert snapshot.state == InstrumentCoordinator.FAILED
    assert snapshot.active_source == "rtl_sdr"
    with pytest.raises(RuntimeError, match="stop it first"):
        coordinator.start_hantek(Path("bridge"))
    coordinator.close()


def test_hantek_stop_timeout_keeps_ownership_and_blocks_rtl_sdr():
    hantek = _StuckStopHantek()
    rtl = _FakeRtl()
    coordinator = InstrumentCoordinator(hantek, rtl)
    coordinator.start_hantek(Path("bridge"))

    assert coordinator.request_stop()
    assert not coordinator.wait_stopped(timeout=1.0)
    snapshot = coordinator.snapshot()
    assert snapshot.state == InstrumentCoordinator.FAILED
    assert snapshot.active_source == "hantek"
    with pytest.raises(RuntimeError, match="stop it first"):
        coordinator.start_rtl_sdr(object())
    # A live capture could still invoke analysis.submit(); NPU teardown must
    # wait until capture has actually exited.
    assert hantek.analysis.closed == 0
    coordinator.close()


def test_hantek_start_refuses_to_open_capture_without_a_ready_npu():
    hantek = _UnavailableNpuHantek()
    rtl = _FakeRtl()
    coordinator = InstrumentCoordinator(hantek, rtl)

    with pytest.raises(RuntimeError, match="NPU is not ready"):
        coordinator.start_hantek(Path("bridge"))

    assert hantek.started == 0
    assert coordinator.snapshot().active_source is None


def test_coordinator_marks_running_hantek_failed_when_its_npu_stops():
    hantek = _RuntimeUnavailableNpuHantek()
    rtl = _FakeRtl()
    coordinator = InstrumentCoordinator(hantek, rtl)
    coordinator.start_hantek(Path("bridge"))

    snapshot = coordinator.snapshot()

    assert snapshot.state == InstrumentCoordinator.FAILED
    assert snapshot.active_source == "hantek"
    assert "NPU worker stopped" in snapshot.message
    coordinator.close()
