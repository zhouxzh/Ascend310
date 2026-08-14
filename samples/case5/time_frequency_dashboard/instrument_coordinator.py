"""Application-level ownership for mutually exclusive Case 5 instruments.

The Hantek and RTL-SDR paths have intentionally different frame contracts and
NPU models.  This coordinator never translates one into the other; it only
serializes their lifecycle so a stale worker cannot survive a device switch.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any, Mapping, Protocol

from .controller import Case5Controller


class RtlServiceProtocol(Protocol):
    """Small runtime surface needed by the dashboard resource arbiter."""

    def start(self, config: Any) -> Any: ...

    def request_stop(self) -> None: ...

    def wait_stopped(self, timeout: float | None = None) -> bool: ...

    def snapshot(self) -> Any: ...

    def close(self) -> None: ...


@dataclass(frozen=True)
class InstrumentSnapshot:
    """Stable, UI-safe state for the active physical acquisition source."""

    state: str
    active_source: str | None
    generation: int
    message: str


@dataclass(frozen=True)
class InstrumentStartToken:
    """A coordinator-owned reservation for one asynchronous start operation."""

    source: str
    generation: int


class InstrumentCoordinator:
    """Own Hantek/RTL-SDR transitions without merging their data pipelines."""

    IDLE = "IDLE"
    STARTING = "STARTING"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    FAILED = "FAILED"
    _START_STOP_TIMEOUT_SECONDS = 15.0

    def __init__(self, hantek: Case5Controller, rtl_sdr: RtlServiceProtocol) -> None:
        self.hantek = hantek
        self.rtl_sdr = rtl_sdr
        self._lock = threading.RLock()
        self._state = self.IDLE
        self._active_source: str | None = None
        self._generation = 0
        self._message = "Ready"
        self._stop_thread: threading.Thread | None = None
        self._start_cancel: threading.Event | None = None
        self._start_done: threading.Event | None = None
        self._starting_generation: int | None = None
        self._closed = False

    def snapshot(self) -> InstrumentSnapshot:
        with self._lock:
            self._reconcile_locked()
            return self._snapshot_locked()

    def start_hantek(
        self,
        bridge_path: Path,
        settings: Mapping[str, object] | None = None,
        *,
        simulation: bool = False,
    ) -> InstrumentSnapshot:
        token = self.reserve_hantek_start()
        return self.start_hantek_reserved(
            token,
            bridge_path,
            settings,
            simulation=simulation,
        )

    def reserve_hantek_start(self) -> InstrumentStartToken:
        return self._reserve_start("hantek")

    def start_hantek_reserved(
        self,
        token: InstrumentStartToken,
        bridge_path: Path,
        settings: Mapping[str, object] | None = None,
        *,
        simulation: bool = False,
    ) -> InstrumentSnapshot:
        with self._lock:
            cancel, start_done = self._start_handles_locked(token)
        try:
            # The Hantek controller owns a different fixed DFT OM.  It is
            # initialized only when Hantek becomes the active source.
            if cancel.is_set():
                with self._lock:
                    self._mark_start_cancelled_locked(token)
                    return self._snapshot_locked()
            npu_status = self.hantek.initialize_npu()
            # A Hantek session without its fixed-shape OM worker would still
            # capture frames, but every analysis window would be discarded.
            # Treat that as a failed start rather than presenting a RUNNING
            # instrument with no core NPU analysis path.
            if npu_status is not None and not bool(getattr(npu_status, "ready", True)):
                message = str(getattr(npu_status, "message", "NPU is unavailable"))
                raise RuntimeError(f"Hantek NPU is not ready: {message}")
            if cancel.is_set():
                with self._lock:
                    self._mark_start_cancelled_locked(token)
                    return self._snapshot_locked()
            if simulation:
                self.hantek.start_simulation()
            else:
                options = dict(settings or {})
                self.hantek.start_hardware(bridge_path, **options)
        except Exception as exc:
            # Stop owns teardown after cancellation.  Running cleanup here as
            # well can finalize a Hantek/ACL resource twice when a blocked
            # start raises just as the Stop worker is released.
            with self._lock:
                cancelled = cancel.is_set() or self._closed
                if cancelled:
                    self._mark_start_cancelled_locked(token)
                    return self._snapshot_locked()
            cleanup_error = self._cleanup_failed_start("hantek")
            with self._lock:
                self._finish_start_failure_locked(token, exc, cleanup_error)
            raise
        else:
            with self._lock:
                if cancel.is_set() or self._closed:
                    self._mark_start_cancelled_locked(token)
                    return self._snapshot_locked()
                self._finish_start_success_locked(token)
                return self._snapshot_locked()
        finally:
            self._complete_start(token, start_done)

    def start_rtl_sdr(self, config: Any) -> InstrumentSnapshot:
        token = self.reserve_rtl_sdr_start()
        return self.start_rtl_sdr_reserved(token, config)

    def reserve_rtl_sdr_start(self) -> InstrumentStartToken:
        return self._reserve_start("rtl_sdr")

    def cancel_reserved_start(self, token: InstrumentStartToken) -> bool:
        """Release a reservation when its background start task cannot launch.

        The dashboard reserves ownership on Qt's thread before it starts a
        worker for expensive manifest/NPU setup.  A rare Python thread-start
        failure must not strand that reservation in ``STARTING`` forever.
        """
        with self._lock:
            cancel, start_done = self._start_handles_locked(token)
            if self._state != self.STARTING:
                return False
            cancel.set()
            # No external start call is in flight, so let the stop worker run
            # immediately instead of waiting for the normal completion hook.
            start_done.set()
            if self._starting_generation == token.generation:
                self._starting_generation = None
            self._state = self.STOPPING
            self._message = f"Cancelling {token.source} before background startup"
            if self._stop_thread is None:
                self._launch_stop_worker_locked(token.source, token.generation)
            return True

    def start_rtl_sdr_reserved(
        self, token: InstrumentStartToken, config: Any
    ) -> InstrumentSnapshot:
        with self._lock:
            cancel, start_done = self._start_handles_locked(token)
        try:
            if cancel.is_set():
                with self._lock:
                    self._mark_start_cancelled_locked(token)
                    return self._snapshot_locked()
            self.rtl_sdr.start(config)
        except Exception as exc:
            with self._lock:
                cancelled = cancel.is_set() or self._closed
                if cancelled:
                    self._mark_start_cancelled_locked(token)
                    return self._snapshot_locked()
            cleanup_error = self._cleanup_failed_start("rtl_sdr")
            with self._lock:
                self._finish_start_failure_locked(token, exc, cleanup_error)
            raise
        else:
            with self._lock:
                if cancel.is_set() or self._closed:
                    self._mark_start_cancelled_locked(token)
                    return self._snapshot_locked()
                self._finish_start_success_locked(token)
                # A real service switches to ``starting``/``running`` in its own
                # worker.  Do not immediately reconcile a just-started runtime:
                # this would let a stale pre-start snapshot erase the successful
                # coordinator transition before the worker publishes state.
                return self._snapshot_locked()
        finally:
            self._complete_start(token, start_done)

    def request_stop(self) -> bool:
        """Request stop once and asynchronously wait for every owned resource."""
        with self._lock:
            if self._state == self.IDLE:
                return False
            if self._state == self.STOPPING and self._stop_thread is not None:
                return False
            source = self._active_source
            if source is None:
                return False
            self._state = self.STOPPING
            self._message = f"Stopping {source}"
            if self._starting_generation == self._generation and self._start_cancel is not None:
                # Do not let a just-started call open a device after Stop has
                # already made the rest of the application believe it is idle.
                self._start_cancel.set()
            self._launch_stop_worker_locked(source, self._generation)
            return True

    def wait_stopped(self, timeout: float | None = None) -> bool:
        with self._lock:
            worker = self._stop_thread
            state = self._state
        if worker is not None and worker.is_alive():
            worker.join(timeout=timeout)
        with self._lock:
            self._reconcile_locked()
            return self._state == self.IDLE

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self.request_stop()
        stopped = self.wait_stopped(timeout=self._START_STOP_TIMEOUT_SECONDS + 8.0)
        with self._lock:
            if stopped:
                # The generation-bound stop worker has already released the
                # active capture and its NPU runner.  Do not invoke each
                # runtime's broad ``close()`` again, which would duplicate
                # Hantek stop/AnalysisService cleanup on window shutdown.
                self._active_source = None
                self._state = self.IDLE
                self._message = "Closed"
            else:
                self._state = self.FAILED
                self._message = "Close deferred: an instrument shutdown is still pending"

    def _reserve_start(self, source: str) -> InstrumentStartToken:
        with self._lock:
            self._begin_start_locked(source)
            return InstrumentStartToken(source=source, generation=self._generation)

    def _begin_start_locked(self, source: str) -> None:
        self._reconcile_locked()
        if self._closed:
            raise RuntimeError("instrument coordinator is closed")
        if self._state == self.FAILED and self._active_source is None:
            self._state = self.IDLE
        if self._state != self.IDLE:
            current = self._active_source or "previous instrument"
            raise RuntimeError(
                f"cannot start {source}: {current} is {self._state.lower()}; stop it first"
            )
        self._generation += 1
        self._active_source = source
        self._state = self.STARTING
        self._message = f"Starting {source}"
        self._start_cancel = threading.Event()
        self._start_done = threading.Event()
        self._starting_generation = self._generation

    def _start_handles_locked(
        self, token: InstrumentStartToken
    ) -> tuple[threading.Event, threading.Event]:
        if (
            self._generation != token.generation
            or self._active_source != token.source
            or self._start_cancel is None
            or self._start_done is None
        ):
            raise RuntimeError("instrument start reservation is stale")
        return self._start_cancel, self._start_done

    def _mark_start_cancelled_locked(self, token: InstrumentStartToken) -> None:
        if self._generation != token.generation or self._active_source != token.source:
            return
        self._state = self.STOPPING
        self._message = f"Stopping {token.source} during startup"

    def _complete_start(
        self, token: InstrumentStartToken, start_done: threading.Event
    ) -> None:
        """Release a Stop worker only after the external start call has returned."""
        start_done.set()
        with self._lock:
            if self._starting_generation == token.generation:
                self._starting_generation = None
            # The cancellation worker may have timed out while a driver or NPU
            # initialization call was blocked.  Once that call does return,
            # start teardown again before this source can ever be released.
            if (
                self._generation == token.generation
                and self._active_source == token.source
                and self._start_cancel is not None
                and self._start_cancel.is_set()
                and self._stop_thread is None
            ):
                self._state = self.STOPPING
                self._message = f"Stopping {token.source} after delayed startup"
                self._launch_stop_worker_locked(token.source, token.generation)

    def _cancelled_start_needs_cleanup_locked(self, source: str, generation: int) -> bool:
        """Whether a delayed cancelled start still owns a teardown obligation."""
        return bool(
            self._generation == generation
            and self._active_source == source
            and self._starting_generation is None
            and self._start_cancel is not None
            and self._start_cancel.is_set()
            and not self._closed
        )

    def _finish_start_success_locked(self, token: InstrumentStartToken) -> None:
        if self._generation != token.generation or self._active_source != token.source:
            raise RuntimeError("instrument start completed for a stale generation")
        if self._state != self.STARTING:
            # Stop may have won the race after the final cancellation check.
            # The stop worker owns cleanup and will not release this source
            # until the just-returned start operation has finished.
            return
        self._state = self.RUNNING
        self._message = f"{token.source} active"

    def _finish_start_failure_locked(
        self,
        token: InstrumentStartToken,
        exc: Exception,
        cleanup_error: Exception | None = None,
    ) -> None:
        if self._generation != token.generation or self._active_source != token.source:
            return
        self._state = self.FAILED
        self._message = f"{type(exc).__name__}: {exc}"
        if cleanup_error is None:
            # A failed start whose cleanup completed owns no live source. Keep
            # the failure visible until an operator explicitly tries again.
            self._active_source = None
        else:
            # Do not grant the other instrument when teardown is uncertain.
            self._message += (
                f"; cleanup {type(cleanup_error).__name__}: {cleanup_error}"
            )

    def _cleanup_failed_start(self, source: str) -> Exception | None:
        """Synchronously clean partial initialization before releasing ownership."""
        try:
            if source == "hantek":
                self._stop_hantek()
            elif source == "rtl_sdr":
                self.rtl_sdr.request_stop()
                if not self.rtl_sdr.wait_stopped(timeout=8.0):
                    raise TimeoutError("RTL-SDR service did not stop after a failed start")
            else:  # pragma: no cover - tokens only come from _reserve_start
                raise RuntimeError(f"unsupported instrument source: {source}")
        except Exception as exc:
            return exc
        return None

    def _stop_worker(self, source: str, generation: int) -> None:
        error: Exception | None = None
        try:
            self._wait_for_start_completion(generation)
            if source == "hantek":
                # Stop capture first, then close the Hantek-only NPU worker so
                # the next source does not inherit queued DFT windows.
                self._stop_hantek()
            elif source == "rtl_sdr":
                self.rtl_sdr.request_stop()
                if not self.rtl_sdr.wait_stopped(timeout=8.0):
                    raise TimeoutError("RTL-SDR service did not stop within eight seconds")
            else:  # pragma: no cover - protected by start methods
                raise RuntimeError(f"unsupported instrument source: {source}")
        except Exception as exc:  # keep UI state consistent after cleanup errors
            error = exc
        with self._lock:
            if self._generation != generation or self._active_source != source:
                return
            self._stop_thread = None
            if error is None:
                self._active_source = None
                self._state = self.IDLE
                self._message = "Stopped"
            else:
                # A timeout or cleanup failure does not prove that the old
                # source released its capture/NPU resources.  Retain ownership
                # and require an explicit Stop retry (or application close)
                # instead of allowing another instrument to start over it.
                self._state = self.FAILED
                self._message = f"{type(error).__name__}: {error}"
                # If startup returned exactly after the first stop worker
                # timed out, cancellation has already been requested but
                # there was no worker available for ``_complete_start`` to
                # restart.  Schedule one immediately so the operator does
                # not need to press Stop a second time.
                if self._cancelled_start_needs_cleanup_locked(source, generation):
                    self._state = self.STOPPING
                    self._message = f"Stopping {source} after delayed startup"
                    self._launch_stop_worker_locked(source, generation)

    def _launch_stop_worker_locked(self, source: str, generation: int) -> None:
        """Start exactly one generation-bound asynchronous teardown worker."""
        worker = threading.Thread(
            target=self._stop_worker,
            args=(source, generation),
            name="case5-instrument-stop",
            daemon=True,
        )
        self._stop_thread = worker
        worker.start()

    def _stop_hantek(self) -> None:
        """Stop capture and NPU work, refusing to release on a live worker."""
        stop_result = self.hantek.stop()
        # Third-party/fake controllers from the pre-status API returned None.
        # Real Case5Controller now returns an explicit bool; retain backward
        # compatibility without weakening its liveness check.
        capture_stopped = True if stop_result is None else bool(stop_result)
        wait_capture = getattr(self.hantek, "wait_stopped", None)
        if callable(wait_capture):
            capture_stopped = bool(wait_capture(timeout=0.0))
        if not capture_stopped:
            # A late capture callback can still submit into ``analysis``.  Do
            # not close that worker until the capture source has demonstrably
            # exited, and retain coordinator ownership for a later Stop retry.
            raise TimeoutError("Hantek capture worker did not stop")
        close_result = self.hantek.analysis.close()
        analysis_stopped = True if close_result is None else bool(close_result)
        wait_analysis = getattr(self.hantek.analysis, "wait_stopped", None)
        if callable(wait_analysis):
            analysis_stopped = bool(wait_analysis(timeout=0.0))
        if not analysis_stopped:
            raise TimeoutError("Hantek NPU worker did not stop")

    def _wait_for_start_completion(self, generation: int) -> None:
        with self._lock:
            start_done = (
                self._start_done if self._starting_generation == generation else None
            )
        if start_done is not None and not start_done.wait(
            timeout=self._START_STOP_TIMEOUT_SECONDS
        ):
            raise TimeoutError("instrument start did not return before shutdown timeout")

    def _reconcile_locked(self) -> None:
        """Reflect asynchronous source failure without accepting stale results."""
        if self._state != self.RUNNING:
            return
        if self._active_source == "hantek":
            snapshot = self.hantek.snapshot()
            npu_status = getattr(snapshot, "npu_status", None)
            npu_ready = bool(getattr(npu_status, "ready", True))
            if snapshot.acquisition_state != "RUNNING" or not npu_ready:
                self._state = self.FAILED
                if not npu_ready:
                    self._message = str(
                        getattr(npu_status, "message", "Hantek NPU is unavailable")
                    )
                else:
                    self._message = snapshot.message
        elif self._active_source == "rtl_sdr":
            snapshot = self.rtl_sdr.snapshot()
            state = str(getattr(snapshot, "state", "")).upper()
            if state == self.FAILED:
                self._state = self.FAILED
                self._message = str(getattr(snapshot, "message", "RTL-SDR failed"))
            elif state in {self.IDLE, "STOPPED"}:
                self._state = self.IDLE
                self._active_source = None
                self._message = str(getattr(snapshot, "message", "Stopped"))

    def _snapshot_locked(self) -> InstrumentSnapshot:
        return InstrumentSnapshot(
            state=self._state,
            active_source=self._active_source,
            generation=self._generation,
            message=self._message,
        )
