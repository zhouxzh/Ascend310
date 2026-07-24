from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from collections import deque
import queue
import shutil
import subprocess
import threading
import time
from typing import Any

import numpy as np

from realtime_ddsp import (
    EnvelopeSettings,
    LivePlayer,
    RealtimeSynthEngine,
    parse_audio_device,
)

from .core import ResourceCoordinator


class PulseLivePlayer:
    """Stream DDSP-VST stereo blocks directly to a selected PulseAudio sink."""

    def __init__(
        self,
        engine: RealtimeSynthEngine,
        sink_name: str,
        device_name: str,
        output_latency_seconds: float,
    ) -> None:
        self.engine = engine
        self.sink_name = sink_name
        self.device_name = device_name
        self.output_latency_seconds = max(0.001, float(output_latency_seconds))
        self.stop_event = threading.Event()
        self._stats_lock = threading.Lock()
        self._worker_error: BaseException | None = None
        self.rendered_blocks = 0
        self.played_blocks = 0
        self.underruns = 0
        self.overruns = 0
        self.max_render_ms = 0.0
        self.render_times_ms: deque[float] = deque(maxlen=1000)
        self.blocks: queue.Queue[np.ndarray] = queue.Queue()
        self.process: subprocess.Popen[bytes] | None = None
        self.worker = threading.Thread(target=self._render_loop, daemon=True)

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
        )
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
                started = time.monotonic()
                block = self.engine.render_output_block()
                elapsed_ms = (time.monotonic() - started) * 1000.0
                block = np.asarray(block, dtype=np.float32)
                if block.ndim == 1:
                    block = np.repeat(block[:, None], 2, axis=1)
                process.stdin.write(block[:, :2].astype("<f4", copy=False).tobytes())
                with self._stats_lock:
                    self.rendered_blocks += 1
                    self.played_blocks += 1
                    self.max_render_ms = max(self.max_render_ms, elapsed_ms)
                    self.render_times_ms.append(elapsed_ms)
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

    def __init__(self, midi_state: Any) -> None:
        self.midi_state = midi_state
        self._lock = threading.Lock()
        self._notes: dict[str, set[int]] = {}
        self._sustain_sources: set[str] = set()

    def note_on(self, source: str, note: int, velocity: int) -> None:
        with self._lock:
            was_active = any(note in notes for notes in self._notes.values())
            self._notes.setdefault(source, set()).add(note)
            if not was_active:
                self.midi_state.note_on(note, velocity)

    def note_off(self, source: str, note: int) -> None:
        with self._lock:
            self._notes.setdefault(source, set()).discard(note)
            if not any(note in notes for notes in self._notes.values()):
                self.midi_state.note_off(note)

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
            self.midi_state.set_sustain(bool(self._sustain_sources))

    def all_notes_off(self) -> None:
        with self._lock:
            self._notes.clear()
            self._sustain_sources.clear()
            self.midi_state.all_notes_off()

    def hardware_message(self, message: Any) -> None:
        message_type = getattr(message, "type", "")
        if message_type == "note_on" and int(getattr(message, "velocity", 0)) > 0:
            self.note_on("hardware-midi", int(message.note), int(message.velocity))
        elif message_type in {"note_off", "note_on"}:
            self.note_off("hardware-midi", int(message.note))
        elif message_type == "control_change" and int(message.control) == 64:
            self.sustain("hardware-midi", int(message.value) >= 64)
        else:
            self.midi_state.handle_message(message)


class DdspVstSessionController:
    OWNER = "ddsp-vst"

    def __init__(self, coordinator: ResourceCoordinator) -> None:
        self.coordinator = coordinator
        self._lock = threading.Lock()
        self.engine: RealtimeSynthEngine | None = None
        self.player: LivePlayer | PulseLivePlayer | None = None
        self.router: InputRouter | None = None
        self.port: Any | None = None
        self.config: dict[str, object] = {}

    @property
    def running(self) -> bool:
        with self._lock:
            return self.player is not None

    def start(self, config: dict[str, object]) -> dict[str, object]:
        self.coordinator.acquire(self.OWNER)
        engine: RealtimeSynthEngine | None = None
        player: LivePlayer | PulseLivePlayer | None = None
        port = None
        try:
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
            router = InputRouter(engine.midi)
            latency_seconds = float(config.get("audio_latency_ms", 80.0)) / 1000.0
            if config.get("audio_backend") == "pulse":
                player = PulseLivePlayer(
                    engine,
                    sink_name=str(config["pulse_sink"]),
                    device_name=str(config.get("audio_device_name", "PulseAudio")),
                    output_latency_seconds=latency_seconds,
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
                )
            midi_port = config.get("midi_port")
            if midi_port:
                try:
                    import mido
                except ImportError as exc:
                    raise RuntimeError(
                        "Physical MIDI requires mido and python-rtmidi"
                    ) from exc
                port = mido.open_input(str(midi_port), callback=router.hardware_message)
            player.start()
            with self._lock:
                self.engine = engine
                self.player = player
                self.router = router
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
            self.coordinator.release(self.OWNER)
            raise

    def stop(self) -> dict[str, object]:
        with self._lock:
            engine = self.engine
            player = self.player
            router = self.router
            port = self.port
            self.engine = None
            self.player = None
            self.router = None
            self.port = None
        try:
            if router is not None:
                router.all_notes_off()
            if port is not None:
                port.close()
            if player is not None:
                player.stop()
            if engine is not None:
                engine.close()
        finally:
            self.coordinator.release(self.OWNER)
        return self.status()

    def note_on(self, source: str, note: int, velocity: int) -> None:
        router = self._require_router()
        router.note_on(source, note, velocity)

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
            config = dict(self.config)
        if engine is None or player is None:
            return {"running": False, "active_notes": [], "config": config}
        with player._stats_lock:
            render_times = list(player.render_times_ms)
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
                "buffered_blocks": player.blocks.qsize(),
            }
        return {
            "running": True,
            "active_notes": engine.midi.active_notes,
            "backend": engine.controls_model.backend_name,
            "parameters": asdict(engine.settings),
            "metrics": metrics,
            "config": config,
        }
