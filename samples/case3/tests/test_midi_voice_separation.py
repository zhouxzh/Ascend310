from pathlib import Path
from types import SimpleNamespace
import json
import tempfile
import unittest
from unittest.mock import patch

import mido
import numpy as np

from midi_ddsp_realtime import _configured_voice_instruments, _voice_seed
from midi_ddsp_webui.midi_analysis import (
    VOICE_SEPARATION_COMMIT,
    VOICE_SEPARATION_INFO,
    VOICE_SEPARATION_SOURCE_SHA256,
    MidiValidationError,
    analyze_midi,
    analyze_midi_voices,
    split_midi_voices,
)
from midi_ddsp_webui.vendor.partitura import estimate_voices


def _write_grouping_fixture(path: Path) -> None:
    midi = mido.MidiFile(type=0, ticks_per_beat=480)
    track = mido.MidiTrack()
    track.append(mido.Message("program_change", channel=0, program=40, time=0))
    track.append(mido.Message("note_on", channel=0, note=60, velocity=80, time=0))
    track.append(mido.Message("note_on", channel=1, note=67, velocity=70, time=0))
    track.append(mido.Message("note_on", channel=9, note=36, velocity=100, time=0))
    track.append(mido.Message("note_off", channel=0, note=60, velocity=0, time=120))
    track.append(mido.Message("program_change", channel=0, program=41, time=0))
    track.append(mido.Message("note_on", channel=0, note=62, velocity=80, time=0))
    track.append(mido.Message("note_off", channel=1, note=67, velocity=0, time=0))
    track.append(mido.Message("note_off", channel=9, note=36, velocity=0, time=0))
    track.append(mido.Message("note_off", channel=0, note=62, velocity=0, time=120))
    midi.tracks.append(track)
    midi.save(path)


class VendoredVoiceSeparationTest(unittest.TestCase):
    def test_vendor_provenance_and_license_are_preserved(self) -> None:
        vendor_root = (
            Path(__file__).resolve().parents[1]
            / "midi_ddsp_webui"
            / "vendor"
            / "partitura"
        )
        notice = (vendor_root / "NOTICE.md").read_text(encoding="utf-8")
        source = (vendor_root / "voice_separation.py").read_text(encoding="utf-8")
        license_text = (vendor_root / "LICENSE").read_text(encoding="utf-8")
        self.assertEqual(
            VOICE_SEPARATION_COMMIT,
            "427ff875bd5a49a0eec894fdd7c6631ed7f597ea",
        )
        self.assertEqual(
            VOICE_SEPARATION_SOURCE_SHA256,
            "32d9af3ccc16c75efdf7679ddb810e0b5080cbb459495481dd5205bdbb640eb8",
        )
        self.assertIn(VOICE_SEPARATION_COMMIT, notice)
        self.assertIn(VOICE_SEPARATION_SOURCE_SHA256, source)
        self.assertIn("Apache License", license_text)
        self.assertEqual(VOICE_SEPARATION_INFO["license"], "Apache-2.0")

    def test_fixed_vectors_match_partitura_v1_9_0(self) -> None:
        dtype = [("pitch", "i4"), ("onset", "i8"), ("duration", "i8")]
        fixtures = [
            ([(60, 0, 10), (64, 0, 10), (67, 0, 10)], [3, 2, 1]),
            ([(72, 0, 8), (60, 0, 8), (74, 8, 8), (62, 8, 8)], [2, 1, 2, 1]),
            ([(60, 0, 12), (67, 4, 4), (62, 12, 8)], [2, 1, 2]),
            ([(72, 0, 10), (60, 0, 6), (62, 6, 6), (70, 10, 8)], [2, 1, 1, 2]),
        ]
        for notes, expected in fixtures:
            values = np.asarray(notes, dtype=dtype)
            actual = estimate_voices(values, monophonic_voices=True)
            np.testing.assert_array_equal(actual, expected)

    def test_groups_by_track_channel_and_program_at_note_on(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "grouping.mid"
            _write_grouping_fixture(path)
            analysis = analyze_midi(path)
            voices = split_midi_voices(analysis)

        self.assertEqual(analysis.note_count, 3)
        self.assertEqual(
            [(voice.channel, voice.program) for voice in voices],
            [(0, 40), (0, 41), (1, 0)],
        )
        self.assertEqual(
            [voice.id for voice in voices],
            [
                "track-0-channel-1-program-40-voice-1",
                "track-0-channel-1-program-41-voice-1",
                "track-0-channel-2-program-0-voice-1",
            ],
        )
        self.assertNotIn(9, {voice.channel for voice in voices})

    def test_canon_partition_is_complete_monophonic_and_deterministic(self) -> None:
        path = Path(__file__).resolve().parents[1] / "midi" / "canon-in-d-johann-pachelbel.mid"
        analysis = analyze_midi(path)
        first = split_midi_voices(analysis)
        second = split_midi_voices(analysis)
        self.assertEqual([voice.id for voice in first], [voice.id for voice in second])
        self.assertEqual(sum(len(voice.notes) for voice in first), analysis.note_count)
        assigned = [
            (note.start_tick, note.end_tick, note.pitch, note.channel, note.program)
            for voice in first
            for note in voice.notes
        ]
        original = [
            (note.start_tick, note.end_tick, note.pitch, note.channel, note.program)
            for track in analysis.tracks
            for note in track.notes
        ]
        self.assertCountEqual(assigned, original)
        for voice in first:
            ordered = sorted(voice.notes, key=lambda note: note.start_tick)
            self.assertTrue(
                all(left.end_tick <= right.start_tick for left, right in zip(ordered, ordered[1:])),
                voice.id,
            )

    def test_analysis_cache_is_content_addressed_and_returns_copies(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "cache.mid"
            _write_grouping_fixture(path)
            first = analyze_midi_voices(path)
            second = analyze_midi_voices(path)
            second["groups"].clear()
            third = analyze_midi_voices(path)
            self.assertEqual(first["analysis_id"], third["analysis_id"])
            self.assertTrue(third["groups"])


class VoiceInstrumentRuntimeTest(unittest.TestCase):
    def test_runtime_validates_complete_mapping_and_stable_seed(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "voices.mid"
            _write_grouping_fixture(path)
            voices = split_midi_voices(analyze_midi(path))
            detailed = analyze_midi_voices(path)
            mapping = {voice.id: index % 13 for index, voice in enumerate(voices)}
            args = SimpleNamespace(
                midi=path,
                instrument_id=0,
                voice_analysis_id=detailed["analysis_id"],
                voice_instruments_json=json.dumps(mapping),
            )
            self.assertEqual(_configured_voice_instruments(args, voices), mapping)
            seeds = [_voice_seed(7, voice) for voice in voices]
            self.assertEqual(seeds, [_voice_seed(7, voice) for voice in voices])
            self.assertEqual(len(seeds), len(set(seeds)))

            stale_args = SimpleNamespace(
                midi=path,
                instrument_id=0,
                voice_analysis_id="0" * 64,
                voice_instruments_json=json.dumps(mapping),
            )
            with self.assertRaises(MidiValidationError) as raised:
                _configured_voice_instruments(stale_args, voices)
            self.assertEqual(raised.exception.code, "voice_analysis_stale")

            missing = dict(mapping)
            missing.pop(next(iter(missing)))
            invalid_args = SimpleNamespace(
                midi=path,
                instrument_id=0,
                voice_analysis_id=detailed["analysis_id"],
                voice_instruments_json=json.dumps(missing),
            )
            with self.assertRaises(MidiValidationError) as raised:
                _configured_voice_instruments(invalid_args, voices)
            self.assertEqual(raised.exception.code, "voice_assignment_mismatch")


if __name__ == "__main__":
    unittest.main()
