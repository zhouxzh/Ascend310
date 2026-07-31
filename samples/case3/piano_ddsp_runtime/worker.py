"""NDJSON process boundary for the long-lived Piano-DDSP runtime."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import queue
import sys
import threading
import traceback
from typing import Any

import numpy as np

from .bundle import load_bundle
from .engine import PianoDdspEngine


class Worker:
    def __init__(self) -> None:
        self.engine: PianoDdspEngine | None = None
        self.output_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.monitor_enabled = False
        self.monitor_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=2)
        self.note_queue: queue.Queue[tuple[int, bool]] = queue.Queue(maxsize=512)
        self.status_thread = threading.Thread(target=self._status_loop, daemon=True)
        self.monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self.note_thread = threading.Thread(target=self._note_loop, daemon=True)

    def emit(self, event: str, **data: object) -> None:
        payload = {"event": event, **data}
        encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=True)
        with self.output_lock:
            print(encoded, flush=True)

    def monitor(self, block: np.ndarray) -> None:
        if not self.monitor_enabled:
            return
        try:
            self.monitor_queue.put_nowait(block.copy())
        except queue.Full:
            if self.engine is not None:
                self.engine.metrics.increment("monitor_drops")

    def _monitor_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                block = self.monitor_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            payload = base64.b64encode(block.astype("<f4", copy=False).tobytes()).decode("ascii")
            self.emit(
                "monitor",
                encoding="float32le",
                channels=2,
                sample_rate=self.engine.output_sample_rate if self.engine else 48_000,
                pcm=payload,
            )

    def _status_loop(self) -> None:
        while not self.stop_event.wait(0.5):
            engine = self.engine
            if engine is not None:
                self.emit("status", data=engine.status())

    def note_event(self, note: int, on: bool) -> None:
        try:
            self.note_queue.put_nowait((int(note), bool(on)))
        except queue.Full:
            # The renderer remains real-time safe if an unavailable client stops reading.
            return

    def _note_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                note, on = self.note_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            self.emit("note", note=note, on=on)

    def start(self, config: dict[str, Any]) -> dict[str, object]:
        if self.engine is not None:
            return self.engine.status()
        bundle = load_bundle(Path(str(config.pop("bundle_manifest"))))
        engine = PianoDdspEngine(
            bundle,
            monitor_callback=self.monitor,
            note_listener=self.note_event,
            **config,
        )
        self.engine = engine
        try:
            engine.start()
        except BaseException:
            engine.stop(graceful=False)
            self.engine = None
            raise
        return engine.status()

    def dispatch(self, message: dict[str, Any]) -> object:
        command = str(message.get("command", ""))
        if command == "start":
            config = message.get("config")
            if not isinstance(config, dict):
                raise ValueError("start requires a config object")
            return self.start(dict(config))
        if command == "status":
            return self.engine.status() if self.engine else {"state": "stopped", "running": False}
        if command == "shutdown":
            if self.engine is not None:
                self.engine.stop(graceful=True)
                self.engine = None
            self.stop_event.set()
            return {"state": "stopped", "running": False}
        if self.engine is None:
            raise RuntimeError("Piano-DDSP is not running")
        if command == "note":
            self.engine.note(
                str(message["source"]),
                int(message["note"]),
                int(message.get("velocity", 0)),
                bool(message.get("on", False)),
            )
        elif command == "cc":
            self.engine.control_change(
                str(message["source"]), int(message["controller"]), int(message["value"])
            )
        elif command == "release_source":
            self.engine.release_source(str(message["source"]))
        elif command == "panic":
            self.engine.panic()
        elif command == "parameters":
            values = message.get("values")
            if not isinstance(values, dict):
                raise ValueError("parameters requires a values object")
            model_id = values.pop("model_id", None)
            piano_year = values.pop("piano_year", None)
            if model_id is not None or piano_year is not None:
                self.engine.switch(
                    str(model_id) if model_id is not None else None,
                    int(piano_year) if piano_year is not None else None,
                )
            if values:
                self.engine.update_parameters(values)
        elif command == "player":
            values = message.get("values")
            if not isinstance(values, dict):
                values = {}
            return self.engine.player_command(str(message["action"]), **values)
        elif command == "record_start":
            return {"path": self.engine.start_recording(str(message["recording_id"]))}
        elif command == "record_stop":
            return {"path": self.engine.stop_recording()}
        elif command == "monitor":
            self.monitor_enabled = bool(message.get("enabled", False))
        else:
            raise ValueError(f"Unknown worker command: {command}")
        return self.engine.status()

    def run(self) -> int:
        self.status_thread.start()
        self.monitor_thread.start()
        self.note_thread.start()
        self.emit("ready", pid=str(__import__("os").getpid()))
        try:
            for raw in sys.stdin:
                request_id: object = None
                try:
                    message = json.loads(raw)
                    if not isinstance(message, dict):
                        raise ValueError("Worker command must be a JSON object")
                    request_id = message.get("request_id")
                    result = self.dispatch(message)
                    self.emit("response", request_id=request_id, ok=True, data=result)
                    if message.get("command") == "shutdown":
                        break
                except BaseException as exc:
                    self.emit(
                        "response",
                        request_id=request_id,
                        ok=False,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                    traceback.print_exc(file=sys.stderr)
        finally:
            self.stop_event.set()
            if self.engine is not None:
                self.engine.stop(graceful=True)
                self.engine = None
        return 0


def main() -> int:
    return Worker().run()


if __name__ == "__main__":
    raise SystemExit(main())
