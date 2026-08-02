from __future__ import annotations

import copy
import hashlib
import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .vendor.partitura import estimate_voices


MODEL_FRAME_RATE = 250
VOICE_SEPARATION_ID = "partitura-chew-wu-contig-v1"
VOICE_SEPARATION_COMMIT = "427ff875bd5a49a0eec894fdd7c6631ed7f597ea"
VOICE_SEPARATION_SOURCE_SHA256 = (
    "32d9af3ccc16c75efdf7679ddb810e0b5080cbb459495481dd5205bdbb640eb8"
)
VOICE_SEPARATION_SOURCE_URL = (
    "https://github.com/CPJKU/partitura/blob/"
    f"{VOICE_SEPARATION_COMMIT}/partitura/musicanalysis/voice_separation.py"
)
VOICE_SEPARATION_INFO = {
    "id": VOICE_SEPARATION_ID,
    "name": "Chew/Wu Contig Mapping",
    "upstream": "CPJKU/partitura",
    "version": "1.9.0",
    "commit": VOICE_SEPARATION_COMMIT,
    "source": VOICE_SEPARATION_SOURCE_URL,
    "implementation": "Partitura v1.9.0",
    "source_url": VOICE_SEPARATION_SOURCE_URL,
    "source_commit": VOICE_SEPARATION_COMMIT,
    "source_sha256": VOICE_SEPARATION_SOURCE_SHA256,
    "license": "Apache-2.0",
    "modified": True,
}

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
MIDI_DDSP_INSTRUMENT_NAMES = (
    "Violin",
    "Viola",
    "Cello",
    "Double Bass",
    "Flute",
    "Oboe",
    "Clarinet",
    "Saxophone",
    "Bassoon",
    "Trumpet",
    "Horn",
    "Trombone",
    "Tuba",
)

_VOICE_CACHE_LOCK = threading.Lock()
_VOICE_CACHE: dict[tuple[str, str], dict[str, object]] = {}
_VOICE_CACHE_MAX_ITEMS = 32
_PIANO_ROLL_CACHE_LOCK = threading.Lock()
_PIANO_ROLL_CACHE: dict[tuple[str, str], dict[str, object]] = {}
_VOICE_SPLIT_CACHE_LOCK = threading.Lock()
_VOICE_SPLIT_CACHE: dict[tuple[str, str], tuple["MidiVoice", ...]] = {}
_VOICE_SPLIT_INFLIGHT: dict[tuple[str, str], threading.Event] = {}


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
    start_tick: int = 0
    end_tick: int = 0
    channel: int = 0
    program: int = 0


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
    def channels(self) -> tuple[int, ...]:
        return tuple(sorted({note.channel for note in self.notes}))

    @property
    def instrument_id(self) -> int | None:
        mapped = {
            MIDI_PROGRAM_TO_INSTRUMENT_ID[program]
            for program in self.programs
            if program in MIDI_PROGRAM_TO_INSTRUMENT_ID
        }
        return next(iter(mapped)) if len(mapped) == 1 else None


@dataclass(frozen=True)
class MidiVoiceGroup:
    id: str
    source_track_index: int
    source_track_name: str
    channel: int
    program: int
    notes: tuple[MidiNote, ...]
    max_polyphony: int
    instrument_id: int | None


@dataclass(frozen=True)
class MidiVoice:
    source_track_index: int
    source_track_name: str
    voice_index: int
    notes: tuple[MidiNote, ...]
    programs: tuple[int, ...]
    instrument_id: int | None
    id: str = ""
    source_group_id: str = ""
    channel: int = 0
    program: int = 0
    suggested_instrument_id: int = 0


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
        return self.voice_count <= 1

    @property
    def groups(self) -> tuple[MidiVoiceGroup, ...]:
        return group_midi_notes(self.tracks)

    @property
    def voice_count(self) -> int:
        return sum(group.max_polyphony for group in self.groups)

    def public(self) -> dict[str, object]:
        return {
            "note_count": self.note_count,
            "track_count": len(self.tracks),
            "max_polyphony": self.max_polyphony,
            "voice_count": self.voice_count,
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
                    "channels": [channel + 1 for channel in track.channels],
                    "programs": list(track.programs),
                    "instrument_id": track.instrument_id,
                }
                for track in self.tracks
            ],
        }


def _note_time(note: MidiNote, *, end: bool) -> int | float:
    if note.end_tick > note.start_tick:
        return note.end_tick if end else note.start_tick
    return note.end if end else note.start


def _max_polyphony(notes: tuple[MidiNote, ...] | list[MidiNote]) -> int:
    events: list[tuple[int | float, int]] = []
    for note in notes:
        events.append((_note_time(note, end=False), 1))
        events.append((_note_time(note, end=True), -1))
    # End events precede start events at the same timestamp.
    events.sort(key=lambda item: (item[0], item[1]))
    active = 0
    maximum = 0
    for _time, delta in events:
        active = max(0, active + delta)
        maximum = max(maximum, active)
    return maximum


def _group_id(track_index: int, channel: int, program: int) -> str:
    return f"track-{track_index}-channel-{channel + 1}-program-{program}"


def _voice_id(group: MidiVoiceGroup, voice_number: int) -> str:
    return f"{group.id}-voice-{voice_number}"


def group_midi_notes(tracks: tuple[MidiTrack, ...]) -> tuple[MidiVoiceGroup, ...]:
    groups: list[MidiVoiceGroup] = []
    for track in tracks:
        grouped: dict[tuple[int, int], list[MidiNote]] = {}
        for note in track.notes:
            grouped.setdefault((note.channel, note.program), []).append(note)
        for (channel, program), notes in sorted(grouped.items()):
            ordered = tuple(
                sorted(
                    notes,
                    key=lambda note: (
                        _note_time(note, end=False),
                        note.pitch,
                        _note_time(note, end=True),
                    ),
                )
            )
            groups.append(
                MidiVoiceGroup(
                    id=_group_id(track.index, channel, program),
                    source_track_index=track.index,
                    source_track_name=track.name,
                    channel=channel,
                    program=program,
                    notes=ordered,
                    max_polyphony=_max_polyphony(list(ordered)),
                    instrument_id=MIDI_PROGRAM_TO_INSTRUMENT_ID.get(program),
                )
            )
    return tuple(groups)


def _suggested_instrument_id(notes: tuple[MidiNote, ...]) -> int:
    median_pitch = float(np.median([note.pitch for note in notes]))
    if median_pitch >= 66:
        return 0
    if median_pitch >= 54:
        return 1
    if median_pitch >= 42:
        return 2
    return 3


def split_voice_group(group: MidiVoiceGroup) -> tuple[MidiVoice, ...]:
    ordered_notes = tuple(
        sorted(
            group.notes,
            key=lambda note: (note.start_tick, note.pitch, note.end_tick),
        )
    )
    if group.max_polyphony <= 1:
        labels = np.ones(len(ordered_notes), dtype=np.int64)
    else:
        note_array = np.fromiter(
            (
                (note.pitch, note.start_tick, note.end_tick - note.start_tick)
                for note in ordered_notes
            ),
            dtype=[("pitch", "i4"), ("onset", "i8"), ("duration", "i8")],
            count=len(ordered_notes),
        )
        labels = np.asarray(
            estimate_voices(note_array, monophonic_voices=True), dtype=np.int64
        )

    voices: list[MidiVoice] = []
    for label in sorted(np.unique(labels)):
        notes = tuple(
            note for note, note_label in zip(ordered_notes, labels) if note_label == label
        )
        voice_number = int(label)
        detected = group.instrument_id
        voices.append(
            MidiVoice(
                source_track_index=group.source_track_index,
                source_track_name=group.source_track_name,
                voice_index=voice_number - 1,
                notes=notes,
                programs=(group.program,),
                instrument_id=detected,
                id=_voice_id(group, voice_number),
                source_group_id=group.id,
                channel=group.channel,
                program=group.program,
                suggested_instrument_id=(
                    detected if detected is not None else _suggested_instrument_id(notes)
                ),
            )
        )

    if len(voices) != group.max_polyphony:
        raise RuntimeError(
            f"Voice partition mismatch for {group.id}: "
            f"expected {group.max_polyphony}, got {len(voices)}"
        )
    return tuple(voices)


def split_midi_voices(analysis: MidiAnalysis) -> tuple[MidiVoice, ...]:
    return tuple(
        voice for group in analysis.groups for voice in split_voice_group(group)
    )


def _cached_split_midi_voices(
    analysis: MidiAnalysis, midi_sha256: str
) -> tuple[MidiVoice, ...]:
    cache_key = (midi_sha256, VOICE_SEPARATION_COMMIT)
    while True:
        with _VOICE_SPLIT_CACHE_LOCK:
            cached = _VOICE_SPLIT_CACHE.get(cache_key)
            if cached is not None:
                return cached
            pending = _VOICE_SPLIT_INFLIGHT.get(cache_key)
            if pending is None:
                pending = threading.Event()
                _VOICE_SPLIT_INFLIGHT[cache_key] = pending
                break
        pending.wait()

    try:
        voices = split_midi_voices(analysis)
    except BaseException:
        with _VOICE_SPLIT_CACHE_LOCK:
            _VOICE_SPLIT_INFLIGHT.pop(cache_key, pending).set()
        raise

    with _VOICE_SPLIT_CACHE_LOCK:
        if len(_VOICE_SPLIT_CACHE) >= _VOICE_CACHE_MAX_ITEMS:
            _VOICE_SPLIT_CACHE.pop(next(iter(_VOICE_SPLIT_CACHE)))
        _VOICE_SPLIT_CACHE[cache_key] = voices
        _VOICE_SPLIT_INFLIGHT.pop(cache_key, pending).set()
    return voices


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
        programs_by_channel = {channel: 0 for channel in range(16)}
        active: dict[tuple[int, int], list[tuple[int, int, int]]] = {}
        raw_notes: list[tuple[int, int, int, int, int, int]] = []
        for message in track:
            tick += int(message.time)
            channel = int(getattr(message, "channel", -1))
            if channel == 9:
                continue
            if message.type == "program_change" and channel >= 0:
                programs_by_channel[channel] = int(message.program)
                continue
            if not hasattr(message, "note") or channel < 0:
                continue
            pitch = int(message.note)
            key = (channel, pitch)
            if message.type == "note_on" and int(message.velocity) > 0:
                active.setdefault(key, []).append(
                    (tick, int(message.velocity), programs_by_channel[channel])
                )
            elif message.type in ("note_off", "note_on"):
                values = active.get(key)
                if values:
                    start_tick, velocity, program = values.pop(0)
                    raw_notes.append(
                        (
                            start_tick,
                            max(tick, start_tick + 1),
                            pitch,
                            velocity,
                            channel,
                            program,
                        )
                    )
                    if not values:
                        active.pop(key, None)
        for (channel, pitch), values in active.items():
            for start_tick, velocity, program in values:
                raw_notes.append(
                    (
                        start_tick,
                        max(tick, start_tick + 1),
                        pitch,
                        velocity,
                        channel,
                        program,
                    )
                )

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
                        start_tick=start_tick,
                        end_tick=end_tick,
                        channel=channel,
                        program=program,
                    )
                    for (
                        start_tick,
                        end_tick,
                        pitch,
                        velocity,
                        channel,
                        program,
                    ) in raw_notes
                ),
                key=lambda note: (
                    note.start_tick,
                    note.channel,
                    note.program,
                    note.pitch,
                    note.end_tick,
                ),
            )
        )
        if notes:
            tracks.append(
                MidiTrack(
                    index=track_index,
                    name=str(track.name or f"Track {track_index}"),
                    notes=notes,
                    programs=tuple(sorted({note.program for note in notes})),
                    max_polyphony=_max_polyphony(list(notes)),
                )
            )
    if not tracks:
        raise MidiValidationError("no_notes", f"No note events found in {path}")
    return tuple(tracks)


def analyze_midi(path: Path) -> MidiAnalysis:
    tracks = read_midi_tracks(path)
    all_notes = tuple(note for track in tracks for note in track.notes)
    maximum = _max_polyphony(list(all_notes))
    duration = max(note.end for note in all_notes)
    groups = group_midi_notes(tracks)
    voice_count = sum(group.max_polyphony for group in groups)
    if voice_count <= 1:
        mode = "monophonic"
    elif all(group.max_polyphony <= 1 for group in groups):
        mode = "multitrack"
    else:
        mode = "polyphonic"
    return MidiAnalysis(
        tracks=tracks,
        note_count=len(all_notes),
        max_polyphony=maximum,
        duration_seconds=duration,
        mode=mode,
        supported=True,
        unsupported_code=None,
        unsupported_reason=None,
    )


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def midi_file_sha256(path: Path) -> str:
    return _sha256_file(path)


def _voice_public(voice: MidiVoice) -> dict[str, object]:
    pitches = np.asarray([note.pitch for note in voice.notes], dtype=np.float64)
    detected_name = (
        MIDI_DDSP_INSTRUMENT_NAMES[voice.instrument_id]
        if voice.instrument_id is not None
        else None
    )
    return {
        "id": voice.id,
        "voice_index": voice.voice_index,
        "track_index": voice.source_track_index,
        "track_name": voice.source_track_name,
        "channel": voice.channel + 1,
        "program": voice.program,
        "note_count": len(voice.notes),
        "start_seconds": min(note.start for note in voice.notes),
        "end_seconds": max(note.end for note in voice.notes),
        "pitch_min": int(np.min(pitches)),
        "pitch_max": int(np.max(pitches)),
        "pitch_median": float(np.median(pitches)),
        "detected_instrument_id": voice.instrument_id,
        "detected_instrument": detected_name,
        "suggested_instrument_id": voice.suggested_instrument_id,
        "suggested_instrument": MIDI_DDSP_INSTRUMENT_NAMES[
            voice.suggested_instrument_id
        ],
        "suggestion_source": (
            "midi_program" if voice.instrument_id is not None else "register_fallback"
        ),
    }


def _build_voice_analysis(path: Path, analysis: MidiAnalysis, midi_sha256: str) -> dict[str, object]:
    voices = _cached_split_midi_voices(analysis, midi_sha256)
    signature = {
        "midi_sha256": midi_sha256,
        "algorithm_commit": VOICE_SEPARATION_COMMIT,
        "voices": [
            {
                "id": voice.id,
                "notes": [
                    [note.start_tick, note.end_tick, note.pitch] for note in voice.notes
                ],
            }
            for voice in voices
        ],
    }
    analysis_id = hashlib.sha256(
        json.dumps(signature, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    groups = []
    for group in analysis.groups:
        group_voices = [voice for voice in voices if voice.source_group_id == group.id]
        groups.append(
            {
                "id": group.id,
                "track_index": group.source_track_index,
                "track_name": group.source_track_name,
                "channel": group.channel + 1,
                "program": group.program,
                "note_count": len(group.notes),
                "max_polyphony": group.max_polyphony,
                "detected_instrument_id": group.instrument_id,
                "detected_instrument": (
                    MIDI_DDSP_INSTRUMENT_NAMES[group.instrument_id]
                    if group.instrument_id is not None
                    else None
                ),
                "voices": [_voice_public(voice) for voice in group_voices],
            }
        )
    return {
        "analysis_id": analysis_id,
        "algorithm": dict(VOICE_SEPARATION_INFO),
        "midi_name": path.name,
        "note_count": analysis.note_count,
        "group_count": len(groups),
        "voice_count": len(voices),
        "groups": groups,
    }


def analyze_midi_voices(path: Path) -> dict[str, object]:
    midi_sha256 = _sha256_file(path)
    cache_key = (midi_sha256, VOICE_SEPARATION_COMMIT)
    with _VOICE_CACHE_LOCK:
        cached = _VOICE_CACHE.get(cache_key)
        if cached is not None:
            return copy.deepcopy(cached)

    result = _build_voice_analysis(path, analyze_midi(path), midi_sha256)
    with _VOICE_CACHE_LOCK:
        if len(_VOICE_CACHE) >= _VOICE_CACHE_MAX_ITEMS:
            _VOICE_CACHE.pop(next(iter(_VOICE_CACHE)))
        _VOICE_CACHE[cache_key] = result
    return copy.deepcopy(result)


def _midi_timing(path: Path) -> dict[str, object]:
    try:
        import mido
    except ImportError as exc:
        raise RuntimeError("mido is required for MIDI-DDSP") from exc

    midi = mido.MidiFile(str(path))
    tempos = _tempo_map(midi)
    tempo_changes = [
        {
            "tick": tick,
            "time_seconds": _tick_to_seconds(tick, midi.ticks_per_beat, tempos, mido),
            "bpm": 60_000_000.0 / tempo,
        }
        for tick, tempo in tempos
    ]
    signature_events: list[tuple[int, int, int]] = [(0, 4, 4)]
    for track in midi.tracks:
        tick = 0
        for message in track:
            tick += int(message.time)
            if message.type == "time_signature":
                signature_events.append(
                    (tick, int(message.numerator), int(message.denominator))
                )
    normalized: list[tuple[int, int, int]] = []
    for event in sorted(signature_events, key=lambda item: item[0]):
        if normalized and normalized[-1][0] == event[0]:
            normalized[-1] = event
        else:
            normalized.append(event)
    return {
        "ticks_per_beat": int(midi.ticks_per_beat),
        "tempo_changes": tempo_changes,
        "time_signatures": [
            {
                "tick": tick,
                "time_seconds": _tick_to_seconds(
                    tick, midi.ticks_per_beat, tempos, mido
                ),
                "numerator": numerator,
                "denominator": denominator,
            }
            for tick, numerator, denominator in normalized
        ],
    }


def analyze_midi_piano_roll(path: Path) -> dict[str, object]:
    midi_sha256 = _sha256_file(path)
    cache_key = (midi_sha256, VOICE_SEPARATION_COMMIT)
    with _PIANO_ROLL_CACHE_LOCK:
        cached = _PIANO_ROLL_CACHE.get(cache_key)
        if cached is not None:
            return copy.deepcopy(cached)

    analysis = analyze_midi(path)
    voices = _cached_split_midi_voices(analysis, midi_sha256)
    notes = [note for voice in voices for note in voice.notes]
    result = {
        "midi_sha256": midi_sha256,
        "midi_name": path.name,
        "duration_seconds": analysis.duration_seconds,
        "note_count": analysis.note_count,
        "pitch_min": min(note.pitch for note in notes),
        "pitch_max": max(note.pitch for note in notes),
        "timing": _midi_timing(path),
        "voices": [
            {
                "id": voice.id,
                "track_index": voice.source_track_index,
                "track_name": voice.source_track_name,
                "channel": voice.channel + 1,
                "program": voice.program,
                "suggested_instrument_id": voice.suggested_instrument_id,
                "notes": [
                    {
                        "start_seconds": note.start,
                        "duration_seconds": max(0.001, note.end - note.start),
                        "pitch": note.pitch,
                        "velocity": note.velocity,
                    }
                    for note in voice.notes
                ],
            }
            for voice in voices
        ],
    }
    with _PIANO_ROLL_CACHE_LOCK:
        if len(_PIANO_ROLL_CACHE) >= _VOICE_CACHE_MAX_ITEMS:
            _PIANO_ROLL_CACHE.pop(next(iter(_PIANO_ROLL_CACHE)))
        _PIANO_ROLL_CACHE[cache_key] = result
    return copy.deepcopy(result)
