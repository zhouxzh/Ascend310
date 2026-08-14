"""Bounded, traceable session storage without extra database dependencies."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import queue
import threading
from typing import Any, Dict, Optional

from .acquisition.frame_protocol import BridgeFrame
from .config import Case5Config


def _json_default(value: Any):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"cannot serialize {type(value)!r}")


class SessionWriter:
    """Write raw framed blocks in bounded chunks and JSONL result references."""

    def __init__(self, config: Case5Config, model_metadata: Dict[str, Any]) -> None:
        config.validate()
        started = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        self.path = self._create_session_path(config.session_root, started)
        self.config = config
        self._queue: "queue.Queue[BridgeFrame]" = queue.Queue(maxsize=8)
        self._closed = False
        self._finalized = False
        self._close_requested = threading.Event()
        self._writer_error: Optional[str] = None
        self._storage_dropped = 0
        self._stored_bytes = 0
        self._stored_frames = 0
        self._analysis_records = 0
        self._chunk_index = 0
        self._chunk_handle = None
        self._chunk_offset = 0
        self._index_handle = (self.path / "raw_index.jsonl").open("w", encoding="utf-8")
        self._analysis_handle = (self.path / "analysis.jsonl").open("w", encoding="utf-8")
        self._state_lock = threading.RLock()
        self._io_lock = threading.Lock()
        manifest = {
            "format": "case5-session-v1",
            "started_at_utc": started,
            "config": asdict(config),
            "model": model_metadata,
            "raw_frame_format": "BridgeFrameV1",
            "timebase": "host-estimated from bridge receive timestamp and sample rate",
        }
        (self.path / "manifest.json").write_text(
            json.dumps(manifest, indent=2, default=_json_default, allow_nan=False),
            encoding="utf-8",
        )
        self._thread = threading.Thread(target=self._write_loop, name="case5-session-writer", daemon=True)
        try:
            self._thread.start()
        except Exception:
            # Thread creation failures are rare, but leaving Windows file
            # handles open here prevents recovery and hides the root cause.
            self._close_requested.set()
            self._finalize()
            raise

    @staticmethod
    def _create_session_path(root: Path, started: str) -> Path:
        """Create a collision-free session directory without reusing evidence."""
        base = Path(root) / started
        for attempt in range(1_000):
            path = base if attempt == 0 else Path(f"{base}-{attempt:03d}")
            try:
                path.mkdir(parents=True, exist_ok=False)
                return path
            except FileExistsError:
                continue
        raise RuntimeError(f"cannot create a unique session directory below {root}")

    @property
    def storage_dropped_frames(self) -> int:
        with self._state_lock:
            return self._storage_dropped

    @property
    def writer_error(self) -> Optional[str]:
        with self._state_lock:
            return self._writer_error

    def record_frame(self, frame: BridgeFrame) -> bool:
        with self._state_lock:
            if self._closed or self._writer_error is not None:
                self._storage_dropped += 1
                return False
        try:
            self._queue.put_nowait(frame)
            return True
        except queue.Full:
            with self._state_lock:
                self._storage_dropped += 1
            return False

    def record_analysis(self, record: Dict[str, Any]) -> bool:
        """Persist one result record, rejecting closed or corrupt sessions."""
        try:
            encoded = json.dumps(record, default=_json_default, allow_nan=False)
        except (TypeError, ValueError) as exc:
            self._set_writer_error(f"analysis serialization failed: {type(exc).__name__}: {exc}")
            return False
        with self._state_lock:
            if self._closed or self._writer_error is not None:
                return False
            try:
                with self._io_lock:
                    self._analysis_handle.write(encoded + "\n")
                    self._analysis_handle.flush()
                self._analysis_records += 1
                return True
            except OSError as exc:
                self._set_writer_error(f"analysis write failed: {type(exc).__name__}: {exc}")
                return False

    def close(self, timeout: float | None = 10.0) -> bool:
        """Finish queued writes without closing files under a live writer thread.

        A full queue is normal under capture load, so shutdown is signalled by
        an event rather than a blocking sentinel insertion.  ``False`` means
        the writer is still alive and its files remain open for a later retry.
        """
        if timeout is not None and timeout < 0:
            raise ValueError("session close timeout must be non-negative or None")
        writer = self.request_close()
        if writer is not threading.current_thread() and writer.is_alive():
            writer.join(timeout=timeout)
        if writer.is_alive():
            # ``timeout=0`` is deliberately used by capture error callbacks
            # to request an asynchronous flush.  It is not a storage failure.
            if timeout not in (0, 0.0):
                self._set_writer_error("session writer did not stop before close timeout")
            return False
        return self._finalize()

    def request_close(self) -> threading.Thread:
        """Prevent new writes and return the writer for a non-blocking shutdown."""
        with self._state_lock:
            self._closed = True
            self._close_requested.set()
            return self._thread

    @property
    def closed(self) -> bool:
        with self._state_lock:
            return self._finalized

    def wait_closed(self, timeout: float | None = None) -> bool:
        """Wait for a previously requested close without reopening the session."""
        with self._state_lock:
            if self._finalized:
                return True
            if not self._closed:
                return False
        return self.close(timeout=timeout)

    def _open_chunk(self) -> None:
        if self._chunk_handle is not None:
            self._chunk_handle.close()
        chunk_name = f"raw_{self._chunk_index:04d}.c5raw"
        self._chunk_index += 1
        self._chunk_offset = 0
        self._chunk_handle = (self.path / chunk_name).open("wb")

    def _write_loop(self) -> None:
        try:
            while True:
                try:
                    frame = self._queue.get(timeout=0.1)
                except queue.Empty:
                    if self._close_requested.is_set():
                        return
                    continue
                packet = frame.to_bytes()
                if self._stored_bytes + len(packet) > self.config.max_session_bytes:
                    with self._state_lock:
                        self._storage_dropped += 1
                    continue
                with self._io_lock:
                    if (
                        self._chunk_handle is None
                        or self._chunk_offset + len(packet) > self.config.raw_chunk_bytes
                    ):
                        self._open_chunk()
                    assert self._chunk_handle is not None
                    chunk_name = Path(self._chunk_handle.name).name
                    offset = self._chunk_offset
                    self._chunk_handle.write(packet)
                    self._chunk_handle.flush()
                    self._chunk_offset += len(packet)
                    self._stored_bytes += len(packet)
                    self._stored_frames += 1
                    self._index_handle.write(
                        json.dumps(
                            {
                                "sequence": frame.sequence,
                                "host_receive_ns": frame.host_receive_ns,
                                "sample_rate_hz": frame.sample_rate_hz,
                                "samples": frame.sample_count,
                                "chunk": chunk_name,
                                "offset": offset,
                                "bytes": len(packet),
                            },
                            allow_nan=False,
                        )
                        + "\n"
                    )
                    self._index_handle.flush()
        except Exception as exc:  # pragma: no cover - disk failure is platform specific
            self._set_writer_error(f"raw writer failed: {type(exc).__name__}: {exc}")
        finally:
            # A controller error callback may detach this writer immediately
            # to avoid blocking a capture thread.  The writer owns the final
            # flush/summary in that case so its evidence is never left with
            # open handles solely because no later UI event occurs.
            self._finalize()

    def _set_writer_error(self, message: str) -> None:
        with self._state_lock:
            if self._writer_error is None:
                self._writer_error = message

    def _finalize(self) -> bool:
        with self._state_lock:
            if self._finalized:
                return True
            if self._thread.is_alive() and self._thread is not threading.current_thread():
                return False
            with self._io_lock:
                if self._chunk_handle is not None:
                    self._chunk_handle.close()
                    self._chunk_handle = None
                self._index_handle.close()
                self._analysis_handle.close()
                summary = {
                    "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                    "stored_raw_bytes": self._stored_bytes,
                    "stored_raw_frames": self._stored_frames,
                    "analysis_records": self._analysis_records,
                    "storage_dropped_frames": self._storage_dropped,
                    "writer_error": self._writer_error,
                }
                (self.path / "summary.json").write_text(
                    json.dumps(summary, indent=2, allow_nan=False), encoding="utf-8"
                )
            self._closed = True
            self._finalized = True
            return True
