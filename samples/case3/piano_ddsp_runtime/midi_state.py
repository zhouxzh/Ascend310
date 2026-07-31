"""Source-aware deterministic 16-slot MIDI state for Piano-DDSP."""

from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Callable

import numpy as np


PIANO_MIDI_MIN = 21
PIANO_MIDI_MAX = 108
PEDAL_CONTROLLERS = (64, 65, 66, 67)
MIN_AUDIBLE_GATE_FRAMES = 4


@dataclass(frozen=True)
class MidiSnapshot:
    active_notes: tuple[int, ...]
    slot_notes: tuple[int, ...]
    pedal: tuple[float, ...]
    sustain: bool
    voice_steals: int
    last_velocity: int


class LiveMidiState:
    """Map independent MIDI sources onto the fixed neural polyphony slots."""

    def __init__(
        self,
        max_polyphony: int = 16,
        note_listener: Callable[[int, bool], None] | None = None,
    ) -> None:
        if max_polyphony != 16:
            raise ValueError("Piano-DDSP v1 requires exactly 16 voices")
        self.max_polyphony = max_polyphony
        self._pitch = np.zeros(max_polyphony, dtype=np.int16)
        self._key_down = np.zeros(max_polyphony, dtype=np.bool_)
        self._pending_velocity = np.zeros(max_polyphony, dtype=np.float32)
        self._gate_target = np.zeros(max_polyphony, dtype=np.bool_)
        self._started = np.zeros(max_polyphony, dtype=np.int64)
        self._released = np.zeros(max_polyphony, dtype=np.int64)
        self._release_frames = np.zeros(max_polyphony, dtype=np.int16)
        self._minimum_gate_frames = np.zeros(max_polyphony, dtype=np.int16)
        self._deferred_release = np.zeros(max_polyphony, dtype=np.bool_)
        self._pedal = np.zeros(4, dtype=np.float32)
        self._source_notes: dict[str, set[int]] = {}
        self._source_pedals: dict[str, np.ndarray] = {}
        self._counter = 0
        self._voice_steals = 0
        self._last_velocity = 0
        self._note_listener = note_listener
        self._lock = threading.Lock()

    def note_on(self, source: str, pitch: int, velocity: int) -> bool:
        if velocity <= 0:
            return self.note_off(source, pitch)
        if not PIANO_MIDI_MIN <= int(pitch) <= PIANO_MIDI_MAX:
            return False
        pitch, velocity = int(pitch), min(127, int(velocity))
        with self._lock:
            before = self._active_notes_locked()
            self._source_notes.setdefault(source, set()).add(pitch)
            matching = np.flatnonzero(self._pitch == pitch)
            if matching.size:
                slot = int(matching[0])
            else:
                free = np.flatnonzero(self._pitch == 0)
                if free.size:
                    slot = int(free[0])
                else:
                    released = np.flatnonzero(~self._gate_target | self._deferred_release)
                    if released.size:
                        slot = int(released[np.argmin(self._released[released])])
                    else:
                        candidates = np.arange(self.max_polyphony)
                        slot = int(candidates[np.argmin(self._started[candidates])])
                    stolen_pitch = int(self._pitch[slot])
                    for notes in self._source_notes.values():
                        notes.discard(stolen_pitch)
                    self._voice_steals += 1
            self._counter += 1
            self._pitch[slot] = pitch
            self._key_down[slot] = True
            self._pending_velocity[slot] = np.float32(velocity / 127.0)
            self._gate_target[slot] = True
            self._started[slot] = self._counter
            self._released[slot] = 0
            self._release_frames[slot] = 0
            self._minimum_gate_frames[slot] = MIN_AUDIBLE_GATE_FRAMES
            self._deferred_release[slot] = False
            self._last_velocity = velocity
            after = self._active_notes_locked()
        self._notify_changes(before, after)
        return True

    def note_off(self, source: str, pitch: int) -> bool:
        pitch = int(pitch)
        with self._lock:
            before = self._active_notes_locked()
            notes = self._source_notes.get(source)
            if notes is not None:
                notes.discard(pitch)
            if any(pitch in held for held in self._source_notes.values()):
                result = True
            else:
                matching = np.flatnonzero(self._pitch == pitch)
                if not matching.size:
                    result = False
                else:
                    for slot_value in matching:
                        slot = int(slot_value)
                        self._key_down[slot] = False
                        if self._pedal[0] < 0.5:
                            self._request_release(slot)
                    result = True
            after = self._active_notes_locked()
        self._notify_changes(before, after)
        return result

    def control_change(self, source: str, controller: int, value: int) -> bool:
        if controller not in PEDAL_CONTROLLERS:
            return False
        index = PEDAL_CONTROLLERS.index(int(controller))
        normalized = np.float32(min(127, max(0, int(value))) / 127.0)
        with self._lock:
            before = self._active_notes_locked()
            was_sustained = self._pedal[0] >= 0.5
            source_pedal = self._source_pedals.setdefault(
                source, np.zeros(4, dtype=np.float32)
            )
            source_pedal[index] = normalized
            self._recompute_pedal()
            if index == 0 and was_sustained and self._pedal[0] < 0.5:
                self._release_unheld_slots()
            after = self._active_notes_locked()
        self._notify_changes(before, after)
        return True

    def release_source(self, source: str) -> None:
        with self._lock:
            before = self._active_notes_locked()
            released_notes = self._source_notes.pop(source, set())
            was_sustained = self._pedal[0] >= 0.5
            self._source_pedals.pop(source, None)
            self._recompute_pedal()
            for pitch in released_notes:
                if any(pitch in held for held in self._source_notes.values()):
                    continue
                matching = np.flatnonzero(self._pitch == pitch)
                for slot_value in matching:
                    slot = int(slot_value)
                    self._key_down[slot] = False
                    if self._pedal[0] < 0.5:
                        self._request_release(slot)
            if was_sustained and self._pedal[0] < 0.5:
                self._release_unheld_slots()
            after = self._active_notes_locked()
        self._notify_changes(before, after)

    def panic(self) -> None:
        with self._lock:
            before = self._active_notes_locked()
            self._pitch.fill(0)
            self._key_down.fill(False)
            self._pending_velocity.fill(0.0)
            self._gate_target.fill(False)
            self._released.fill(0)
            self._release_frames.fill(0)
            self._minimum_gate_frames.fill(0)
            self._deferred_release.fill(False)
            self._pedal.fill(0.0)
            self._source_notes.clear()
            self._source_pedals.clear()
            after = self._active_notes_locked()
        self._notify_changes(before, after)

    def render_frame(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        with self._lock:
            before = self._active_notes_locked()
            conditioning = np.zeros((1, self.max_polyphony, 2), dtype=np.float32)
            conditioning[0, self._gate_target, 0] = self._pitch[self._gate_target]
            conditioning[0, :, 1] = self._pending_velocity
            self._pending_velocity.fill(0.0)
            gates = self._gate_target.copy()
            releasing = (~self._gate_target) & (self._pitch != 0)
            self._release_frames[releasing] -= 1
            expired = releasing & (self._release_frames <= 0)
            self._pitch[expired] = 0
            self._released[expired] = 0
            self._release_frames[expired] = 0
            active = self._gate_target & (self._pitch != 0)
            self._minimum_gate_frames[active] = np.maximum(
                self._minimum_gate_frames[active] - 1, 0
            )
            ready = (
                self._deferred_release
                & ~self._key_down
                & self._gate_target
                & (self._minimum_gate_frames <= 0)
                & (self._pedal[0] < 0.5)
            )
            for slot_value in np.flatnonzero(ready):
                self._release_slot(int(slot_value))
            pedal = self._pedal[np.newaxis, :].copy()
            after = self._active_notes_locked()
        self._notify_changes(before, after)
        return conditioning, pedal, gates

    def snapshot(self) -> MidiSnapshot:
        with self._lock:
            notes = tuple(
                sorted(int(self._pitch[index]) for index in np.flatnonzero(self._gate_target))
            )
            return MidiSnapshot(
                active_notes=notes,
                slot_notes=tuple(int(value) for value in self._pitch),
                pedal=tuple(float(value) for value in self._pedal),
                sustain=bool(self._pedal[0] >= 0.5),
                voice_steals=self._voice_steals,
                last_velocity=self._last_velocity,
            )

    def _recompute_pedal(self) -> None:
        if self._source_pedals:
            self._pedal[:] = np.maximum.reduce(tuple(self._source_pedals.values()))
        else:
            self._pedal.fill(0.0)

    def _active_notes_locked(self) -> set[int]:
        return {int(self._pitch[index]) for index in np.flatnonzero(self._gate_target)}

    def _notify_changes(self, before: set[int], after: set[int]) -> None:
        listener = self._note_listener
        if listener is None:
            return
        for pitch in sorted(before - after):
            listener(pitch, False)
        for pitch in sorted(after - before):
            listener(pitch, True)

    def _release_unheld_slots(self) -> None:
        for slot_value in np.flatnonzero(~self._key_down & (self._pitch != 0)):
            self._request_release(int(slot_value))

    def _request_release(self, slot: int) -> None:
        if self._minimum_gate_frames[slot] > 0:
            self._deferred_release[slot] = True
            self._counter += 1
            self._released[slot] = self._counter
            return
        self._release_slot(slot)

    def _release_slot(self, slot: int) -> None:
        self._key_down[slot] = False
        self._pending_velocity[slot] = 0.0
        self._gate_target[slot] = False
        self._counter += 1
        self._released[slot] = self._counter
        self._release_frames[slot] = 250
        self._minimum_gate_frames[slot] = 0
        self._deferred_release[slot] = False
