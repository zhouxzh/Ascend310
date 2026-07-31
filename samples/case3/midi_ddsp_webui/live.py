from __future__ import annotations

import base64
from dataclasses import asdict
from pathlib import Path
from collections import deque
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from typing import Any, Callable

import numpy as np

from realtime_ddsp import (
    EnvelopeSettings,
    LivePlayer,
    RealtimeSynthEngine,
    open_midi_input,
    parse_audio_device,
    shape_midi_velocity,
)
from piano_ddsp_runtime.audio_output import WavRecorder
from piano_ddsp_runtime.scheduler import MidiTimeline, load_midi_timeline

from .core import REPORT_ROOT, ResourceCoordinator


LATENCY_PROFILES: dict[str, dict[str, float | int]] = {
    "low": {"prebuffer": 1, "audio_latency_ms": 15.0},
    "balanced": {"prebuffer": 2, "audio_latency_ms": 20.0},
    "safe": {"prebuffer": 3, "audio_latency_ms": 60.0},
}
RECORDING_ROOT = REPORT_ROOT / "realtime"


def resolve_latency_profile(config: dict[str, object]) -> dict[str, object]:
    """Resolve a UI latency profile while preserving legacy numeric clients."""
    resolved = dict(config)
    raw_profile = resolved.get("latency_profile")
    if raw_profile in (None, ""):
        return resolved
    profile = str(raw_profile)
    if profile not in LATENCY_PROFILES:
        raise ValueError(f"Unknown latency profile: {profile}")

    if bool(resolved.get("is_bluetooth")):
        if profile == "low":
            raise ValueError("Bluetooth audio does not support the low latency profile")
        resolved["sample_rate"] = int(
            resolved.get("audio_device_sample_rate") or 44_100
        )
        resolved["prebuffer"] = 2 if profile == "balanced" else 3
        resolved["audio_latency_ms"] = 220.0 if profile == "balanced" else 300.0
    else:
        resolved.update(LATENCY_PROFILES[profile])
    return resolved


_PULSE_LATENCY_RE = re.compile(r"(Buffer|Sink) Latency:\s*(\d+) usec")


def _pulse_latencies_for_pid(process_id: int) -> tuple[float, float]:
    if shutil.which("pactl") is None:
        return 0.0, 0.0
    try:
        result = subprocess.run(
            ["pactl", "list", "sink-inputs"],
            capture_output=True,
            text=True,
            timeout=1.0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return 0.0, 0.0
    if result.returncode != 0:
        return 0.0, 0.0

    pid_marker = f'application.process.id = "{process_id}"'
    for section in re.split(r"(?=Sink Input #)", result.stdout):
        if pid_marker not in section:
            continue
        values = {name: int(value) / 1_000_000.0 for name, value in _PULSE_LATENCY_RE.findall(section)}
        return values.get("Buffer", 0.0), values.get("Sink", 0.0)
    return 0.0, 0.0


class PulseLivePlayer:
    """Stream DDSP-VST stereo blocks directly to a selected PulseAudio sink."""

    def __init__(
        self,
        engine: RealtimeSynthEngine,
        sink_name: str,
        device_name: str,
        output_latency_seconds: float,
        before_render: Callable[[int], None] | None = None,
        on_block: Callable[[np.ndarray], None] | None = None,
    ) -> None:
        self.engine = engine
        self.sink_name = sink_name
        self.device_name = device_name
        self.output_latency_seconds = max(0.001, float(output_latency_seconds))
        self.device_latency_seconds = self.output_latency_seconds
        self.sink_latency_seconds = 0.0
        self.pulse_buffer_latency_seconds = 0.0
        self.frame_period = 320 / 16_000
        self.before_render = before_render
        self.on_block = on_block
        self.stop_event = threading.Event()
        self._stats_lock = threading.Lock()
        self._worker_error: BaseException | None = None
        self.rendered_blocks = 0
        self.played_blocks = 0
        self.underruns = 0
        self.overruns = 0
        self.max_render_ms = 0.0
        self.render_times_ms: deque[float] = deque(maxlen=1000)
        self.write_times_ms: deque[float] = deque(maxlen=1000)
        self.pipe_capacity_bytes = 0
        self._last_latency_query = 0.0
        self.process: subprocess.Popen[bytes] | None = None
        self.worker = threading.Thread(target=self._render_loop, daemon=True)

    @property
    def buffered_blocks(self) -> int:
        return 0

    @property
    def queue_latency_seconds(self) -> float:
        bytes_per_second = self.engine.output_sample_rate * 2 * 4
        return self.pipe_capacity_bytes / bytes_per_second if bytes_per_second else 0.0

    def _bound_stdin_pipe(self) -> None:
        process = self.process
        if process is None or process.stdin is None or os.name != "posix":
            return
        try:
            import fcntl

            block_bytes = round(
                self.engine.output_sample_rate * self.frame_period * 2 * 4
            )
            try:
                fcntl.fcntl(process.stdin.fileno(), fcntl.F_SETPIPE_SZ, block_bytes)
            except OSError:
                pass
            self.pipe_capacity_bytes = int(
                fcntl.fcntl(process.stdin.fileno(), fcntl.F_GETPIPE_SZ)
            )
        except (AttributeError, ImportError, OSError):
            self.pipe_capacity_bytes = 0

    def refresh_audio_latencies(self) -> None:
        now = time.monotonic()
        if now - self._last_latency_query < 1.0:
            return
        self._last_latency_query = now
        process = self.process
        if process is None:
            return
        buffer_latency, sink_latency = _pulse_latencies_for_pid(process.pid)
        if buffer_latency > 0.0:
            self.pulse_buffer_latency_seconds = buffer_latency
        if sink_latency > 0.0:
            self.sink_latency_seconds = sink_latency

    def start(self) -> None:
        if shutil.which("paplay") is None:
            raise RuntimeError("paplay is required for a selected PulseAudio output")
        self.process = subprocess.Popen(
            [
                "paplay",
                "--playback",
                f"--device={self.sink_name}",
                "--client-name=MIDI-DDSP Studio",
                "--stream-name=DDSP-VST Synth",
                "--raw",
                "--format=float32le",
                f"--rate={self.engine.output_sample_rate}",
                "--channels=2",
                f"--latency-msec={round(self.output_latency_seconds * 1000.0)}",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        self._bound_stdin_pipe()
        self.worker.start()
        time.sleep(0.02)
        self.raise_worker_error()
        if self.process.poll() is not None:
            error = (
                self.process.stderr.read().decode("utf-8", errors="replace").strip()
                if self.process.stderr is not None
                else ""
            )
            raise RuntimeError(error or "paplay failed to open the selected output")
        self.refresh_audio_latencies()
        print(
            f"[AUDIO] device={self.device_name}, channels=2, "
            f"sample_rate={self.engine.output_sample_rate}, backend=PulseAudio"
        )

    def _render_loop(self) -> None:
        try:
            process = self.process
            if process is None or process.stdin is None:
                raise RuntimeError("PulseAudio output process is not ready")
            while not self.stop_event.is_set():
                with self._stats_lock:
                    frame_index = self.rendered_blocks
                if self.before_render is not None:
                    self.before_render(frame_index)
                started = time.monotonic()
                block = self.engine.render_output_block()
                elapsed_ms = (time.monotonic() - started) * 1000.0
                block = np.asarray(block, dtype=np.float32)
                if block.ndim == 1:
                    block = np.repeat(block[:, None], 2, axis=1)
                if self.on_block is not None:
                    self.on_block(block)
                payload = memoryview(
                    block[:, :2].astype("<f4", copy=False).tobytes()
                )
                write_started = time.monotonic()
                while payload and not self.stop_event.is_set():
                    written = process.stdin.write(payload)
                    if written is None or written <= 0:
                        raise BrokenPipeError("paplay stdin closed")
                    payload = payload[written:]
                write_ms = (time.monotonic() - write_started) * 1000.0
                with self._stats_lock:
                    self.rendered_blocks += 1
                    self.played_blocks += 1
                    self.max_render_ms = max(self.max_render_ms, elapsed_ms)
                    self.render_times_ms.append(elapsed_ms)
                    self.write_times_ms.append(write_ms)
        except (BrokenPipeError, OSError) as exc:
            if not self.stop_event.is_set():
                with self._stats_lock:
                    self._worker_error = exc
        except BaseException as exc:
            with self._stats_lock:
                self._worker_error = exc
        finally:
            self.stop_event.set()

    def raise_worker_error(self) -> None:
        with self._stats_lock:
            error = self._worker_error
        if error is not None:
            raise RuntimeError("Realtime PulseAudio render worker failed") from error

    def stop(self) -> None:
        self.stop_event.set()
        process = self.process
        if process is not None and process.poll() is None:
            process.terminate()
        if self.worker.ident is not None:
            self.worker.join(timeout=3.0)
        if process is not None:
            try:
                process.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2.0)
        self.process = None


class InputRouter:
    """Merge browser and hardware MIDI without leaving stuck notes."""

    def __init__(
        self,
        midi_state: Any,
        note_listener: Callable[[int, bool], None] | None = None,
    ) -> None:
        self.midi_state = midi_state
        self._note_listener = note_listener
        self._lock = threading.Lock()
        self._notes: dict[str, set[int]] = {}
        self._sustain_sources: set[str] = set()
        self._pending_note_on: deque[float] = deque()
        self.midi_to_render_times_ms: deque[float] = deque(maxlen=1000)
        self.hardware_velocities: deque[int] = deque(maxlen=1000)

    def note_on(self, source: str, note: int, velocity: int) -> None:
        with self._lock:
            was_active = any(note in notes for notes in self._notes.values())
            self._notes.setdefault(source, set()).add(note)
            if not was_active:
                self.midi_state.note_on(note, velocity)
                self._pending_note_on.append(time.monotonic())
                self._notify_note(note, True)

    def note_off(self, source: str, note: int) -> None:
        with self._lock:
            self._notes.setdefault(source, set()).discard(note)
            if not any(note in notes for notes in self._notes.values()):
                self.midi_state.note_off(note)
                self._notify_note(note, False)

    def sustain(self, source: str, enabled: bool) -> None:
        with self._lock:
            if enabled:
                self._sustain_sources.add(source)
            else:
                self._sustain_sources.discard(source)
            self.midi_state.set_sustain(bool(self._sustain_sources))

    def pitch_bend(self, value: int) -> None:
        self.midi_state.set_pitch_bend(value)

    def release_source(self, source: str) -> None:
        with self._lock:
            notes = self._notes.pop(source, set())
            self._sustain_sources.discard(source)
            for note in notes:
                if not any(note in values for values in self._notes.values()):
                    self.midi_state.note_off(note)
                    self._notify_note(note, False)
            self.midi_state.set_sustain(bool(self._sustain_sources))

    def all_notes_off(self) -> None:
        with self._lock:
            active_notes = sorted({note for notes in self._notes.values() for note in notes})
            self._notes.clear()
            self._sustain_sources.clear()
            self.midi_state.all_notes_off()
            for note in active_notes:
                self._notify_note(note, False)

    def _notify_note(self, note: int, on: bool) -> None:
        listener = self._note_listener
        if listener is None:
            return
        try:
            listener(int(note), bool(on))
        except Exception:
            # Visual telemetry must never interrupt the audio input path.
            return

    def mark_render_started(self, _frame_index: int) -> None:
        now = time.monotonic()
        with self._lock:
            while self._pending_note_on:
                self.midi_to_render_times_ms.append(
                    (now - self._pending_note_on.popleft()) * 1000.0
                )

    def latency_snapshot(self) -> list[float]:
        with self._lock:
            return list(self.midi_to_render_times_ms)

    def hardware_velocity_snapshot(self) -> list[int]:
        with self._lock:
            return list(self.hardware_velocities)

    def hardware_message(self, message: Any) -> None:
        message_type = getattr(message, "type", "")
        if message_type == "note_on" and int(getattr(message, "velocity", 0)) > 0:
            velocity = int(message.velocity)
            with self._lock:
                self.hardware_velocities.append(velocity)
            self.note_on("hardware-midi", int(message.note), velocity)
        elif message_type in {"note_off", "note_on"}:
            self.note_off("hardware-midi", int(message.note))
        elif message_type == "control_change" and int(message.control) == 64:
            self.sustain("hardware-midi", int(message.value) >= 64)
        else:
            self.midi_state.handle_message(message)


class RealtimeMidiPlayer:
    """Monotonic MIDI transport shared by DDSP-VST and unified switching."""

    SOURCE = "midi-file"

    def __init__(self, router: InputRouter) -> None:
        self.router = router
        self._lock = threading.RLock()
        self.timeline: MidiTimeline | None = None
        self.path: str | None = None
        self.state = "empty"
        self.position_seconds = 0.0
        self.tempo = 1.0
        self.loop = False
        self._started_ns = 0
        self._event_index = 0

    def _sync_position_locked(self, now_ns: int) -> None:
        if self.state == "playing":
            elapsed = (now_ns - self._started_ns) / 1e9 * self.tempo
            self.position_seconds += max(0.0, elapsed)
            self._started_ns = now_ns

    def _restore_locked(self) -> None:
        timeline = self.timeline
        self.router.release_source(self.SOURCE)
        self._event_index = 0
        if timeline is None:
            return
        for index, (event_time, kind, data1, data2) in enumerate(timeline.events):
            if event_time > self.position_seconds:
                self._event_index = index
                return
            self._apply(kind, data1, data2)
            self._event_index = index + 1

    def _apply(self, kind: str, data1: int, data2: int) -> None:
        if kind == "note_on":
            self.router.note_on(self.SOURCE, data1, data2)
        elif kind == "note_off":
            self.router.note_off(self.SOURCE, data1)
        elif kind in {"control_change", "cc"} and data1 == 64:
            self.router.sustain(self.SOURCE, data2 >= 64)

    def command(self, action: str, **values: object) -> dict[str, object]:
        now_ns = time.monotonic_ns()
        with self._lock:
            if action == "load":
                path = Path(str(values["path"])).resolve()
                self.timeline = load_midi_timeline(path)
                self.path = str(path)
                self.state = "loaded"
                self.position_seconds = 0.0
                self._event_index = 0
                self.router.release_source(self.SOURCE)
            elif self.timeline is None:
                raise RuntimeError("Load a MIDI file before using the player")
            elif action == "play":
                if self.position_seconds >= self.timeline.duration_seconds:
                    self.position_seconds = 0.0
                self._restore_locked()
                self._started_ns = now_ns
                self.state = "playing"
            elif action == "pause":
                self._sync_position_locked(now_ns)
                self.state = "paused"
                self.router.release_source(self.SOURCE)
            elif action == "stop":
                self.state = "loaded"
                self.position_seconds = 0.0
                self._event_index = 0
                self.router.release_source(self.SOURCE)
            elif action == "seek":
                self._sync_position_locked(now_ns)
                position = float(values.get("position_seconds", values.get("position", 0.0)))
                self.position_seconds = min(self.timeline.duration_seconds, max(0.0, position))
                self._restore_locked()
                self._started_ns = now_ns
            elif action == "tempo":
                self._sync_position_locked(now_ns)
                tempo = float(values.get("value", 1.0))
                if not 0.5 <= tempo <= 2.0:
                    raise ValueError("MIDI tempo must be between 0.5 and 2.0")
                self.tempo = tempo
                self._started_ns = now_ns
            elif action == "loop":
                self.loop = bool(values.get("enabled", False))
            else:
                raise ValueError(f"Unknown player action: {action}")
        return self.status()

    def before_render(self, _frame_index: int) -> None:
        now_ns = time.monotonic_ns()
        with self._lock:
            timeline = self.timeline
            if timeline is None or self.state != "playing":
                return
            self._sync_position_locked(now_ns)
            while self._event_index < len(timeline.events):
                event_time, kind, data1, data2 = timeline.events[self._event_index]
                if event_time > self.position_seconds:
                    break
                self._event_index += 1
                self._apply(kind, data1, data2)
            if self.position_seconds >= timeline.duration_seconds:
                if self.loop:
                    self.position_seconds = 0.0
                    self._started_ns = now_ns
                    self._restore_locked()
                else:
                    self.position_seconds = timeline.duration_seconds
                    self.state = "loaded"
                    self.router.release_source(self.SOURCE)

    def release(self) -> None:
        with self._lock:
            if self.state == "playing":
                self._sync_position_locked(time.monotonic_ns())
                self.state = "paused"
            self.router.release_source(self.SOURCE)

    def status(self) -> dict[str, object]:
        with self._lock:
            position = self.position_seconds
            if self.state == "playing":
                position += (time.monotonic_ns() - self._started_ns) / 1e9 * self.tempo
            duration = self.timeline.duration_seconds if self.timeline else 0.0
            return {
                "state": self.state,
                "path": self.path,
                "position_seconds": min(duration, max(0.0, position)),
                "duration_seconds": duration,
                "tempo": self.tempo,
                "loop": self.loop,
            }


class DdspVstSessionController:
    OWNER = "ddsp-vst"

    def __init__(self, coordinator: ResourceCoordinator) -> None:
        self.coordinator = coordinator
        self._lock = threading.RLock()
        self.engine: RealtimeSynthEngine | None = None
        self.player: LivePlayer | PulseLivePlayer | None = None
        self.router: InputRouter | None = None
        self.transport: RealtimeMidiPlayer | None = None
        self.port: Any | None = None
        self.config: dict[str, object] = {}
        self._owns_resource = False
        self._recorder = WavRecorder()
        self._tap_queue: queue.Queue[object] = queue.Queue(maxsize=8)
        self._audio_tap_drops = 0
        self._recording_accepts_blocks = False
        self._monitor_sources: set[str] = set()
        self._monitor_queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=2)
        self._monitor_drops = 0
        self._subscribers: list[queue.Queue[dict[str, object]]] = []
        threading.Thread(
            target=self._tap_loop,
            name="ddsp-vst-audio-tap",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._monitor_loop,
            name="ddsp-vst-monitor",
            daemon=True,
        ).start()

    @property
    def running(self) -> bool:
        with self._lock:
            return self.player is not None

    def subscribe(self) -> queue.Queue[dict[str, object]]:
        target: queue.Queue[dict[str, object]] = queue.Queue(maxsize=64)
        with self._lock:
            self._subscribers.append(target)
        return target

    def unsubscribe(self, target: queue.Queue[dict[str, object]]) -> None:
        with self._lock:
            if target in self._subscribers:
                self._subscribers.remove(target)

    def _publish(self, payload: dict[str, object]) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for target in subscribers:
            try:
                target.put_nowait(payload)
            except queue.Full:
                try:
                    target.get_nowait()
                    target.put_nowait(payload)
                except queue.Empty:
                    pass

    def _audio_tap(self, block: np.ndarray) -> None:
        stereo = np.asarray(block, dtype=np.float32)
        if stereo.ndim == 1:
            stereo = np.repeat(stereo[:, None], 2, axis=1)
        elif stereo.shape[1] == 1:
            stereo = np.repeat(stereo, 2, axis=1)
        else:
            stereo = stereo[:, :2]
        with self._lock:
            record_block = self._recording_accepts_blocks
        try:
            self._tap_queue.put_nowait((stereo.copy(), record_block))
        except queue.Full:
            with self._lock:
                self._audio_tap_drops += 1

    def _tap_loop(self) -> None:
        while True:
            item = self._tap_queue.get()
            if isinstance(item, threading.Event):
                item.set()
                self._tap_queue.task_done()
                continue
            block, record_block = item
            try:
                if record_block:
                    self._recorder.write(block)
            except (OSError, ValueError):
                with self._lock:
                    self._audio_tap_drops += 1
            with self._lock:
                monitoring = bool(self._monitor_sources)
            if not monitoring:
                self._tap_queue.task_done()
                continue
            try:
                self._monitor_queue.put_nowait(block)
            except queue.Full:
                with self._lock:
                    self._monitor_drops += 1
            finally:
                self._tap_queue.task_done()

    def _flush_tap(self) -> None:
        barrier = threading.Event()
        self._tap_queue.put(barrier, timeout=1.0)
        if not barrier.wait(timeout=1.0):
            raise TimeoutError("Realtime audio tap did not drain")

    def _monitor_loop(self) -> None:
        while True:
            block = self._monitor_queue.get()
            payload = base64.b64encode(
                np.asarray(block, dtype="<f4").tobytes()
            ).decode("ascii")
            with self._lock:
                sample_rate = int(
                    self.engine.output_sample_rate if self.engine is not None else 48_000
                )
            self._publish(
                {"event": "monitor", "sample_rate": sample_rate, "audio": payload}
            )

    def start(
        self, config: dict[str, object], *, manage_resource: bool = True
    ) -> dict[str, object]:
        if self.running:
            return self.status()
        if manage_resource:
            self.coordinator.acquire(self.OWNER)
            with self._lock:
                self._owns_resource = True
        engine: RealtimeSynthEngine | None = None
        player: LivePlayer | PulseLivePlayer | None = None
        port = None
        try:
            config = resolve_latency_profile(config)
            model_path = Path(str(config["model_path"]))
            envelope = EnvelopeSettings(
                attack=float(config.get("attack", 0.10)),
                decay=float(config.get("decay", 0.0)),
                sustain=float(config.get("sustain", 1.0)),
                release=float(config.get("release", 1.20)),
            )
            engine = RealtimeSynthEngine(
                model_path,
                output_sample_rate=int(config.get("sample_rate", 48_000)),
                max_voices=int(config.get("max_voices", 1)),
                envelope=envelope,
                output_gain_db=float(config.get("output_gain_db", 0.0)),
                backend=str(config.get("backend", "auto")),
                device_id=int(config.get("device_id", 0)),
                keep_acl_runtime=True,
            )
            engine.update_parameters(
                {
                    name: float(config[name])
                    for name in (
                        "pitch_shift",
                        "harmonic_gain",
                        "noise_gain",
                        "output_gain_db",
                        "velocity_curve",
                        "attack",
                        "decay",
                        "sustain",
                        "release",
                        "input_pitch",
                        "input_gain",
                        "reverb_size",
                        "reverb_damping",
                        "reverb_wet",
                    )
                    if name in config
                }
            )
            router = InputRouter(engine.midi, self._publish_note_event)
            transport = RealtimeMidiPlayer(router)

            def before_render(frame_index: int) -> None:
                transport.before_render(frame_index)
                router.mark_render_started(frame_index)

            latency_seconds = float(config.get("audio_latency_ms", 80.0)) / 1000.0
            if config.get("audio_backend") == "pulse":
                player = PulseLivePlayer(
                    engine,
                    sink_name=str(config["pulse_sink"]),
                    device_name=str(config.get("audio_device_name", "PulseAudio")),
                    output_latency_seconds=latency_seconds,
                    before_render=before_render,
                    on_block=self._audio_tap,
                )
            else:
                player = LivePlayer(
                    engine,
                    prebuffer_blocks=int(config.get("prebuffer", 6)),
                    output_device=parse_audio_device(
                        None
                        if config.get("audio_device_id") in (None, "")
                        else str(config["audio_device_id"])
                    ),
                    output_latency_seconds=latency_seconds,
                    before_render=before_render,
                    on_block=self._audio_tap,
                )
            midi_port = config.get("midi_port")
            if midi_port:
                port = open_midi_input(str(midi_port), router.hardware_message)
            player.start()
            with self._lock:
                self.engine = engine
                self.player = player
                self.router = router
                self.transport = transport
                self.port = port
                self.config = {key: value for key, value in config.items() if key != "model_path"}
            return self.status()
        except BaseException:
            if port is not None:
                port.close()
            if player is not None:
                player.stop()
            if engine is not None:
                engine.close()
            with self._lock:
                owns_resource = self._owns_resource
                self._owns_resource = False
            if owns_resource:
                self.coordinator.release(self.OWNER)
            raise

    def stop(self) -> dict[str, object]:
        with self._lock:
            engine = self.engine
            player = self.player
            router = self.router
            transport = self.transport
            port = self.port
            self.engine = None
            self.player = None
            self.router = None
            self.transport = None
            self.port = None
            owns_resource = self._owns_resource
            self._owns_resource = False
            self._monitor_sources.clear()
        stop_error: BaseException | None = None

        def attempt(action: Callable[[], object]) -> None:
            nonlocal stop_error
            try:
                action()
            except BaseException as exc:
                stop_error = stop_error or exc

        try:
            with self._lock:
                self._recording_accepts_blocks = False
            try:
                self._flush_tap()
            except (queue.Full, TimeoutError):
                pass
            attempt(self._recorder.stop)
            if transport is not None:
                attempt(transport.release)
            if router is not None:
                attempt(router.all_notes_off)
            if port is not None:
                attempt(port.close)
            if player is not None:
                attempt(player.stop)
            if engine is not None:
                attempt(engine.close)
        finally:
            if owns_resource:
                self.coordinator.release(self.OWNER)
        if stop_error is not None:
            raise stop_error
        return self.status()

    def note_on(self, source: str, note: int, velocity: int) -> None:
        router = self._require_router()
        router.note_on(source, note, velocity)

    def _publish_note_event(self, note: int, on: bool) -> None:
        self._publish({"event": "note", "note": int(note), "on": bool(on)})

    def note_off(self, source: str, note: int) -> None:
        router = self._require_router()
        router.note_off(source, note)

    def sustain(self, source: str, enabled: bool) -> None:
        router = self._require_router()
        router.sustain(source, enabled)

    def pitch_bend(self, value: int) -> None:
        self._require_router().pitch_bend(value)

    def update_parameters(self, values: dict[str, float]) -> dict[str, object]:
        with self._lock:
            engine = self.engine
        if engine is None:
            raise RuntimeError("DDSP-VST session is not running")
        settings = engine.update_parameters(values)
        with self._lock:
            self.config.update(values)
        return asdict(settings)

    def player_command(self, action: str, **values: object) -> dict[str, object]:
        with self._lock:
            transport = self.transport
        if transport is None:
            raise RuntimeError("DDSP-VST session is not running")
        return transport.command(action, **values)

    def record_start(self, recording_id: str) -> dict[str, object]:
        safe = "".join(
            char for char in recording_id if char.isalnum() or char in "-_"
        )
        if not safe or safe != recording_id:
            raise ValueError("Invalid realtime recording id")
        with self._lock:
            engine = self.engine
        if engine is None:
            raise RuntimeError("DDSP-VST session is not running")
        with self._lock:
            self._recording_accepts_blocks = False
        self._flush_tap()
        path = self._recorder.start(RECORDING_ROOT / f"{safe}.wav", engine.output_sample_rate)
        with self._lock:
            self._recording_accepts_blocks = True
        return {"id": path.stem, "path": str(path)}

    def record_stop(self) -> dict[str, object] | None:
        with self._lock:
            self._recording_accepts_blocks = False
        if self._recorder.active:
            self._flush_tap()
        path = self._recorder.stop()
        return {"id": path.stem, "path": str(path)} if path is not None else None

    def set_monitor(self, source: str, enabled: bool) -> None:
        with self._lock:
            if enabled:
                self._monitor_sources.add(source)
            else:
                self._monitor_sources.discard(source)

    def release_source(self, source: str) -> None:
        with self._lock:
            router = self.router
        if router is not None:
            router.release_source(source)

    def _require_router(self) -> InputRouter:
        with self._lock:
            router = self.router
        if router is None:
            raise RuntimeError("DDSP-VST session is not running")
        return router

    def status(self) -> dict[str, object]:
        with self._lock:
            engine = self.engine
            player = self.player
            router = self.router
            transport = self.transport
            port = self.port
            config = dict(self.config)
        if engine is None or player is None:
            return {
                "running": False,
                "active_notes": [],
                "config": config,
                "player": transport.status() if transport is not None else {
                    "state": "empty",
                    "path": None,
                    "position_seconds": 0.0,
                    "duration_seconds": 0.0,
                    "tempo": 1.0,
                    "loop": False,
                },
                "recording": {"active": self._recorder.active, "id": None},
            }
        audio_status: dict[str, object] = {"device_lost": False, "error": None}
        try:
            player.raise_worker_error()
        except RuntimeError as exc:
            if router is not None:
                router.all_notes_off()
            audio_status = {"device_lost": True, "error": str(exc)}
        refresh_latencies = getattr(player, "refresh_audio_latencies", None)
        if refresh_latencies is not None:
            refresh_latencies()
        elif time.monotonic() - getattr(player, "_last_latency_query", 0.0) >= 1.0:
            player._last_latency_query = time.monotonic()
            buffer_latency, sink_latency = _pulse_latencies_for_pid(os.getpid())
            if buffer_latency > 0.0:
                player.pulse_buffer_latency_seconds = buffer_latency
            if sink_latency > 0.0:
                player.sink_latency_seconds = sink_latency
        with player._stats_lock:
            render_times = list(player.render_times_ms)
            write_times = list(getattr(player, "write_times_ms", ()))
            queue_latency_ms = player.queue_latency_seconds * 1000.0
            device_latency_ms = player.device_latency_seconds * 1000.0
            pulse_buffer_latency_ms = player.pulse_buffer_latency_seconds * 1000.0
            sink_latency_ms = player.sink_latency_seconds * 1000.0
            resampler_latency_ms = (
                engine.resampler.algorithmic_latency_seconds * 1000.0
            )
            metrics = {
                "rendered_blocks": player.rendered_blocks,
                "played_blocks": player.played_blocks,
                "underruns": player.underruns,
                "overruns": player.overruns,
                "max_render_ms": player.max_render_ms,
                "p95_render_ms": (
                    float(np.quantile(render_times, 0.95))
                    if render_times
                    else 0.0
                ),
                "buffered_blocks": player.buffered_blocks,
                "queue_latency_ms": queue_latency_ms,
                "device_latency_ms": device_latency_ms,
                "pulse_buffer_latency_ms": pulse_buffer_latency_ms,
                "sink_latency_ms": sink_latency_ms,
                "resampler_latency_ms": resampler_latency_ms,
                "estimated_total_latency_ms": (
                    queue_latency_ms
                    + max(device_latency_ms, pulse_buffer_latency_ms)
                    + sink_latency_ms
                    + resampler_latency_ms
                ),
                "write_block_p95_ms": (
                    float(np.quantile(write_times, 0.95)) if write_times else 0.0
                ),
                "monitor_drops": self._monitor_drops,
                "audio_tap_drops": self._audio_tap_drops,
            }
        midi_times = router.latency_snapshot() if router is not None else []
        metrics["midi_to_render_p95_ms"] = (
            float(np.quantile(midi_times, 0.95)) if midi_times else 0.0
        )
        metrics["audio_path_latency_ms"] = metrics["estimated_total_latency_ms"]
        metrics["estimated_total_latency_ms"] += metrics["midi_to_render_p95_ms"]
        velocities = router.hardware_velocity_snapshot() if router is not None else []
        if velocities:
            raw_velocity = velocities[-1]
            metrics["midi_velocity_last"] = raw_velocity
            metrics["midi_velocity_min"] = min(velocities)
            metrics["midi_velocity_max"] = max(velocities)
            metrics["midi_velocity_p50"] = float(np.quantile(velocities, 0.5))
            metrics["midi_velocity_mapped_last"] = round(
                shape_midi_velocity(raw_velocity / 127.0, engine.settings.velocity_curve)
                * 127.0
            )
        with engine._output_stats_lock:
            metrics["output_peak"] = engine.output_peak
            metrics["clipped_samples"] = engine.clipped_samples
        if port is not None:
            midi_connected = bool(getattr(port, "connected", True))
            metrics["midi_connected"] = midi_connected
            metrics["midi_reconnects"] = int(getattr(port, "reconnect_count", 0))
            if not midi_connected and router is not None:
                router.release_source("hardware-midi")
        else:
            midi_connected = False
        return {
            "running": True,
            "active_notes": engine.midi.active_notes,
            "backend": engine.controls_model.backend_name,
            "parameters": asdict(engine.settings),
            "metrics": metrics,
            "config": config,
            "player": transport.status() if transport is not None else None,
            "recording": {
                "active": self._recorder.active,
                "id": self._recorder.path.stem if self._recorder.path else None,
            },
            "audio": audio_status,
            "midi": {
                "connected": midi_connected,
                "reconnects": int(getattr(port, "reconnect_count", 0)) if port is not None else 0,
                "error": getattr(port, "last_error", None) if port is not None else None,
            },
        }


def resolve_realtime_recording(recording_id: str) -> Path:
    safe = "".join(char for char in recording_id if char.isalnum() or char in "-_")
    if not safe or safe != recording_id:
        raise KeyError(recording_id)
    path = (RECORDING_ROOT / f"{safe}.wav").resolve()
    if RECORDING_ROOT.resolve() not in path.parents or not path.is_file():
        raise KeyError(recording_id)
    return path
