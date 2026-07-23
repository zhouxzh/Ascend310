from __future__ import annotations

from pathlib import Path
import threading
from typing import Any

from realtime_ddsp import (
    EnvelopeSettings,
    LivePlayer,
    RealtimeSynthEngine,
    parse_audio_device,
)

from .core import ResourceCoordinator


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


class LiveSessionController:
    OWNER = "live-session"

    def __init__(self, coordinator: ResourceCoordinator) -> None:
        self.coordinator = coordinator
        self._lock = threading.Lock()
        self.engine: RealtimeSynthEngine | None = None
        self.player: LivePlayer | None = None
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
        player: LivePlayer | None = None
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
                max_voices=int(config.get("max_voices", 8)),
                envelope=envelope,
                output_gain_db=float(config.get("output_gain_db", 0.0)),
                backend=str(config.get("backend", "auto")),
                device_id=int(config.get("device_id", 0)),
            )
            router = InputRouter(engine.midi)
            player = LivePlayer(
                engine,
                prebuffer_blocks=int(config.get("prebuffer", 6)),
                output_device=parse_audio_device(
                    None
                    if config.get("audio_device_id") in (None, "")
                    else str(config["audio_device_id"])
                ),
                output_latency_seconds=float(config.get("audio_latency_ms", 80.0))
                / 1000.0,
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

    def release_source(self, source: str) -> None:
        with self._lock:
            router = self.router
        if router is not None:
            router.release_source(source)

    def _require_router(self) -> InputRouter:
        with self._lock:
            router = self.router
        if router is None:
            raise RuntimeError("live session is not running")
        return router

    def status(self) -> dict[str, object]:
        with self._lock:
            engine = self.engine
            player = self.player
            config = dict(self.config)
        if engine is None or player is None:
            return {"running": False, "active_notes": [], "config": config}
        with player._stats_lock:
            metrics = {
                "rendered_blocks": player.rendered_blocks,
                "played_blocks": player.played_blocks,
                "underruns": player.underruns,
                "overruns": player.overruns,
                "max_render_ms": player.max_render_ms,
                "buffered_blocks": player.blocks.qsize(),
            }
        return {
            "running": True,
            "active_notes": engine.midi.active_notes,
            "backend": engine.controls_model.backend_name,
            "metrics": metrics,
            "config": config,
        }
