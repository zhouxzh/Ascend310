from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


MODEL_FRAME_RATE = 250

# General MIDI programs are zero based in mido and pretty_midi.
MIDI_PROGRAM_TO_INSTRUMENT_ID = {
    40: 0,  # violin
    41: 1,  # viola
    42: 2,  # cello
    43: 3,  # double bass
    73: 4,  # flute
    68: 5,  # oboe
    71: 6,  # clarinet
    66: 7,  # saxophone
    70: 8,  # bassoon
    56: 9,  # trumpet
    60: 10,  # horn
    57: 11,  # trombone
    58: 12,  # tuba
}


class MidiValidationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MidiNote:
    start: float
    end: float
    pitch: int
    velocity: int


@dataclass(frozen=True)
class MidiTrack:
    index: int
    name: str
    notes: tuple[MidiNote, ...]
    programs: tuple[int, ...]
    max_polyphony: int

    @property
    def monophonic(self) -> bool:
        return self.max_polyphony <= 1

    @property
    def instrument_id(self) -> int | None:
        mapped = {
            MIDI_PROGRAM_TO_INSTRUMENT_ID[program]
            for program in self.programs
            if program in MIDI_PROGRAM_TO_INSTRUMENT_ID
        }
        return next(iter(mapped)) if len(mapped) == 1 else None


@dataclass(frozen=True)
class MidiAnalysis:
    tracks: tuple[MidiTrack, ...]
    note_count: int
    max_polyphony: int
    duration_seconds: float
    mode: str
    supported: bool
    unsupported_code: str | None
    unsupported_reason: str | None

    @property
    def monophonic(self) -> bool:
        return self.max_polyphony <= 1

    def public(self) -> dict[str, object]:
        return {
            "note_count": self.note_count,
            "track_count": len(self.tracks),
            "max_polyphony": self.max_polyphony,
            "duration_seconds": self.duration_seconds,
            "monophonic": self.monophonic,
            "midi_ddsp_mode": self.mode,
            "midi_ddsp_supported": self.supported,
            "unsupported_code": self.unsupported_code,
            "unsupported_reason": self.unsupported_reason,
            "programs": sorted(
                {program for track in self.tracks for program in track.programs}
            ),
            "tracks": [
                {
                    "index": track.index,
                    "name": track.name,
                    "note_count": len(track.notes),
                    "max_polyphony": track.max_polyphony,
                    "monophonic": track.monophonic,
                    "programs": list(track.programs),
                    "instrument_id": track.instrument_id,
                }
                for track in self.tracks
            ],
        }


def _max_polyphony(notes: tuple[MidiNote, ...] | list[MidiNote]) -> int:
    events: list[tuple[float, int]] = []
    for note in notes:
        events.append((note.start, 1))
        events.append((note.end, -1))
    # End events precede start events at the same timestamp.
    events.sort(key=lambda item: (item[0], item[1]))
    active = 0
    maximum = 0
    for _time, delta in events:
        active = max(0, active + delta)
        maximum = max(maximum, active)
    return maximum


def _tempo_map(midi: Any) -> list[tuple[int, int]]:
    events: list[tuple[int, int]] = [(0, 500_000)]
    for track in midi.tracks:
        tick = 0
        for message in track:
            tick += int(message.time)
            if message.type == "set_tempo":
                events.append((tick, int(message.tempo)))
    normalized: list[tuple[int, int]] = []
    for tick, tempo in sorted(events):
        if normalized and normalized[-1][0] == tick:
            normalized[-1] = (tick, tempo)
        else:
            normalized.append((tick, tempo))
    return normalized


def _tick_to_seconds(
    target_tick: int,
    ticks_per_beat: int,
    tempos: list[tuple[int, int]],
    mido_module: Any,
) -> float:
    seconds = 0.0
    previous_tick = 0
    tempo = 500_000
    for tick, next_tempo in tempos:
        if tick > target_tick:
            break
        seconds += mido_module.tick2second(
            tick - previous_tick, ticks_per_beat, tempo
        )
        previous_tick = tick
        tempo = next_tempo
    seconds += mido_module.tick2second(
        target_tick - previous_tick, ticks_per_beat, tempo
    )
    return float(seconds)


def read_midi_tracks(path: Path) -> tuple[MidiTrack, ...]:
    try:
        import mido
    except ImportError as exc:
        raise RuntimeError("mido is required for MIDI-DDSP") from exc

    midi = mido.MidiFile(str(path))
    tempos = _tempo_map(midi)
    tracks: list[MidiTrack] = []
    for track_index, track in enumerate(midi.tracks):
        tick = 0
        programs: set[int] = set()
        active: dict[tuple[int, int], list[tuple[int, int]]] = {}
        raw_notes: list[tuple[int, int, int, int]] = []
        for message in track:
            tick += int(message.time)
            channel = int(getattr(message, "channel", -1))
            if channel == 9:
                continue
            if message.type == "program_change":
                programs.add(int(message.program))
                continue
            if not hasattr(message, "note") or channel < 0:
                continue
            key = (channel, int(message.note))
            if message.type == "note_on" and int(message.velocity) > 0:
                active.setdefault(key, []).append((tick, int(message.velocity)))
            elif message.type in ("note_off", "note_on"):
                values = active.get(key)
                if values:
                    start_tick, velocity = values.pop(0)
                    raw_notes.append(
                        (start_tick, max(tick, start_tick + 1), key[1], velocity)
                    )
                    if not values:
                        active.pop(key, None)
        for (_channel, pitch), values in active.items():
            for start_tick, velocity in values:
                raw_notes.append((start_tick, max(tick, start_tick + 1), pitch, velocity))

        notes = tuple(
            sorted(
                (
                    MidiNote(
                        start=_tick_to_seconds(
                            start_tick, midi.ticks_per_beat, tempos, mido
                        ),
                        end=max(
                            _tick_to_seconds(
                                end_tick, midi.ticks_per_beat, tempos, mido
                            ),
                            _tick_to_seconds(
                                start_tick, midi.ticks_per_beat, tempos, mido
                            )
                            + 1e-3,
                        ),
                        pitch=pitch,
                        velocity=velocity,
                    )
                    for start_tick, end_tick, pitch, velocity in raw_notes
                ),
                key=lambda note: (note.start, note.pitch, note.end),
            )
        )
        if notes:
            tracks.append(
                MidiTrack(
                    index=track_index,
                    name=str(track.name or f"Track {track_index}"),
                    notes=notes,
                    programs=tuple(sorted(programs)),
                    max_polyphony=_max_polyphony(notes),
                )
            )
    if not tracks:
        raise MidiValidationError("no_notes", f"No note events found in {path}")
    return tuple(tracks)


def analyze_midi(path: Path) -> MidiAnalysis:
    tracks = read_midi_tracks(path)
    all_notes = tuple(note for track in tracks for note in track.notes)
    maximum = _max_polyphony(all_notes)
    duration = max(note.end for note in all_notes)
    if maximum <= 1:
        mode = "monophonic"
        supported = True
        code = None
        reason = None
    elif len(tracks) > 1 and all(track.monophonic for track in tracks):
        unsupported = [track for track in tracks if track.instrument_id is None]
        if unsupported:
            mode = "unsupported"
            supported = False
            code = "unsupported_program"
            reason = "Multi-track MIDI contains programs outside the 13 MIDI-DDSP instruments"
        else:
            mode = "multitrack"
            supported = True
            code = None
            reason = None
    else:
        mode = "unsupported"
        supported = False
        code = "polyphonic_track"
        reason = "MIDI-DDSP supports monophonic parts; this file contains chords"
    return MidiAnalysis(
        tracks=tracks,
        note_count=len(all_notes),
        max_polyphony=maximum,
        duration_seconds=duration,
        mode=mode,
        supported=supported,
        unsupported_code=code,
        unsupported_reason=reason,
    )


def require_supported_midi(path: Path) -> MidiAnalysis:
    analysis = analyze_midi(path)
    if not analysis.supported:
        raise MidiValidationError(
            analysis.unsupported_code or "unsupported_midi",
            analysis.unsupported_reason or "MIDI file is not supported",
        )
    return analysis
