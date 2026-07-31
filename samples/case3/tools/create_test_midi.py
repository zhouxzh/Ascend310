#!/usr/bin/env python3
"""Create a deterministic monophonic MIDI fixture for DDSP testing."""

from __future__ import annotations

import argparse
from pathlib import Path

import mido


ROOT_DIR = Path(__file__).resolve().parents[1]


def create_test_midi(output: Path) -> None:
    midi = mido.MidiFile(type=0, ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="DDSP MIDI Test", time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(96), time=0))
    track.append(mido.Message("program_change", program=40, channel=0, time=0))
    track.append(mido.Message("control_change", control=7, value=110, channel=0, time=0))
    track.append(mido.Message("control_change", control=11, value=112, channel=0, time=0))

    # A self-authored scale-and-interval pattern. Durations are in beats.
    melody = [
        (60, 0.5, 78), (62, 0.5, 84), (64, 0.5, 90), (65, 0.5, 96),
        (67, 0.5, 102), (69, 0.5, 96), (71, 0.5, 90), (72, 0.5, 84),
        (71, 0.75, 96), (69, 0.25, 86), (67, 0.5, 92), (65, 0.5, 88),
        (64, 0.5, 84), (62, 0.5, 80), (60, 1.0, 76),
        (55, 0.5, 80), (57, 0.5, 86), (59, 0.5, 92), (60, 0.5, 98),
        (62, 0.5, 104), (64, 0.5, 98), (65, 0.5, 92), (67, 0.5, 86),
        (65, 0.75, 96), (64, 0.25, 86), (62, 0.5, 90), (60, 0.5, 86),
        (59, 0.5, 82), (57, 0.5, 78), (55, 1.0, 74),
    ]

    pending_gap = 0
    for note, beats, velocity in melody:
        total_ticks = round(beats * midi.ticks_per_beat)
        sounding_ticks = max(1, round(total_ticks * 0.9))
        gap_ticks = total_ticks - sounding_ticks
        track.append(
            mido.Message(
                "note_on",
                note=note,
                velocity=velocity,
                channel=0,
                time=pending_gap,
            )
        )
        track.append(
            mido.Message(
                "note_off",
                note=note,
                velocity=0,
                channel=0,
                time=sounding_ticks,
            )
        )
        pending_gap = gap_ticks

    track.append(mido.MetaMessage("end_of_track", time=pending_gap))
    output.parent.mkdir(parents=True, exist_ok=True)
    midi.save(str(output))
    print(f"[MIDI] Wrote {output}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=ROOT_DIR / "test_violin.mid"
    )
    args = parser.parse_args()
    create_test_midi(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
