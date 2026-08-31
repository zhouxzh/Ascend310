"""Lifecycle tests for the serialized hardware owner (no ACL or camera)."""

import sys
from pathlib import Path

import pytest

CASE_ROOT = Path(__file__).resolve().parents[1]
if str(CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(CASE_ROOT))

from face_attendance.runtime import NpuWorker, RuntimeNotReadyError


class FakeBackend:
    def __init__(self):
        self.released = False

    def echo(self, value):
        return value

    def release(self):
        self.released = True


def test_worker_serializes_calls_and_releases_backend():
    holder = {}

    def factory():
        backend = FakeBackend()
        holder["backend"] = backend
        return backend

    worker = NpuWorker(backend_factory=factory)
    worker.start(timeout=2)
    assert worker.ready is True
    assert worker.call("echo", "serialized") == "serialized"

    worker.stop()
    assert holder["backend"].released is True
    assert worker.ready is False
    with pytest.raises(RuntimeNotReadyError):
        worker.call("echo", "after-stop")


def test_worker_reports_backend_start_failure():
    worker = NpuWorker(backend_factory=lambda: (_ for _ in ()).throw(RuntimeError("OM missing")))
    worker.start(timeout=2)
    assert worker.ready is False
    with pytest.raises(RuntimeNotReadyError, match="OM missing"):
        worker.call("echo", "unavailable")
    worker.stop()
