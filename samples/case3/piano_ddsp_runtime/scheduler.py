"""Monotonic MIDI event scheduling shared by hardware, browser, and files."""

from __future__ import annotations

from dataclasses import dataclass, field
import heapq
import math
from pathlib import Path
import threading
import time
from typing import Iterable

import numpy as np

from .midi_state import LiveMidiState, PEDAL_CONTROLLERS, PIANO_MIDI_MAX, PIANO_MIDI_MIN


@dataclass(order=True, frozen=True)
class ScheduledEvent:
    timestamp_ns: int
    sequence: int
    source: str = field(compare=False)
    kind: str = field(compare=False)
    data1: int = field(compare=False)
    data2: int = field(default=0, compare=False)


@dataclass(frozen=True)
class MidiTimeline:
    duration_seconds: float
    events: tuple[tuple[float, str, int, int], ...]


def load_midi_timeline(path: Path) -> MidiTimeline:
    try:
        from mido import MidiFile, merge_tracks, tick2second
    except ImportError as exc:
        raise RuntimeError("MIDI file playback requires the existing mido package") from exc
    midi = MidiFile(Path(path))
    tempo = 500_000
    elapsed = 0.0
    events: list[tuple[float, str, int, int]] = []
    for message in merge_tracks(midi.tracks):
        elapsed += tick2second(message.time, midi.ticks_per_beat, tempo)
        if message.type == "set_tempo":
            tempo = message.tempo
        elif message.type == "note_on" and PIANO_MIDI_MIN <= message.note <= PIANO_MIDI_MAX:
            kind = "note_on" if message.velocity > 0 else "note_off"
            events.append((elapsed, kind, int(message.note), int(message.velocity)))
        elif message.type == "note_off" and PIANO_MIDI_MIN <= message.note <= PIANO_MIDI_MAX:
            events.append((elapsed, "note_off", int(message.note), 0))
        elif message.type == "control_change" and message.control in PEDAL_CONTROLLERS:
            events.append((elapsed, "control_change", int(message.control), int(message.value)))
    return MidiTimeline(float(elapsed), tuple(events))


class MidiScheduler:
    FRAME_NS = 4_000_000

    def __init__(self, state: LiveMidiState) -> None:
        self.state = state
        self._heap: list[ScheduledEvent] = []
        self._sequence = 0
        self._lock = threading.Lock()

    def push(
        self,
        source: str,
        kind: str,
        data1: int,
        data2: int = 0,
        timestamp_ns: int | None = None,
    ) -> None:
        timestamp_ns = time.monotonic_ns() if timestamp_ns is None else int(timestamp_ns)
        with self._lock:
            self._sequence += 1
            heapq.heappush(
                self._heap,
                ScheduledEvent(timestamp_ns, self._sequence, source, kind, int(data1), int(data2)),
            )

    def schedule_timeline(
        self,
        source: str,
        timeline: MidiTimeline,
        start_ns: int,
        position_seconds: float = 0.0,
        tempo_scale: float = 1.0,
    ) -> None:
        if not math.isfinite(position_seconds) or not 0 <= position_seconds <= timeline.duration_seconds:
            raise ValueError("MIDI position is outside the timeline")
        if not math.isfinite(tempo_scale) or not 0.5 <= tempo_scale <= 2.0:
            raise ValueError("tempo_scale must be between 0.5 and 2.0")
        self.cancel_source(source)
        self.restore_timeline(source, timeline, position_seconds)
        for event_time, kind, data1, data2 in timeline.events:
            if event_time <= position_seconds:
                continue
            timestamp = start_ns + round((event_time - position_seconds) * 1e9 / tempo_scale)
            self.push(source, kind, data1, data2, timestamp)

    def restore_timeline(
        self, source: str, timeline: MidiTimeline, position_seconds: float
    ) -> None:
        self.state.release_source(source)
        for event_time, kind, data1, data2 in timeline.events:
            if event_time > position_seconds:
                break
            self._apply(ScheduledEvent(0, 0, source, kind, data1, data2))

    def cancel_source(self, source: str) -> None:
        with self._lock:
            self._heap = [event for event in self._heap if event.source != source]
            heapq.heapify(self._heap)
        self.state.release_source(source)

    def clear(self) -> None:
        with self._lock:
            self._heap.clear()

    def render_conditions(
        self, frame_count: int, first_frame_ns: int
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        conditioning = np.zeros((frame_count, 16, 2), dtype=np.float32)
        pedal = np.zeros((frame_count, 4), dtype=np.float32)
        gate = np.zeros((frame_count, 16), dtype=np.bool_)
        for frame in range(frame_count):
            self.drain(first_frame_ns + frame * self.FRAME_NS)
            frame_conditioning, frame_pedal, frame_gate = self.state.render_frame()
            conditioning[frame] = frame_conditioning[0]
            pedal[frame] = frame_pedal[0]
            gate[frame] = frame_gate
        return conditioning, pedal, gate

    def drain(self, until_ns: int) -> int:
        due: list[ScheduledEvent] = []
        with self._lock:
            while self._heap and self._heap[0].timestamp_ns <= until_ns:
                due.append(heapq.heappop(self._heap))
        applied = 0
        for event in due:
            self._apply(event)
            applied += 1
        return applied

    def _apply(self, event: ScheduledEvent) -> None:
        if event.kind == "note_on":
            self.state.note_on(event.source, event.data1, event.data2)
        elif event.kind == "note_off":
            self.state.note_off(event.source, event.data1)
        elif event.kind in {"control_change", "cc"}:
            self.state.control_change(event.source, event.data1, event.data2)
        elif event.kind == "release_source":
            self.state.release_source(event.source)
        else:
            raise ValueError(f"Unsupported MIDI event: {event.kind}")
