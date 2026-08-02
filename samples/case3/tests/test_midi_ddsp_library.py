from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import mido

from midi_ddsp_webui.library import MidiDdspLibrary, SCHEMA_VERSION
from midi_ddsp_webui import midi_analysis
from midi_ddsp_webui.midi_analysis import analyze_midi_piano_roll, analyze_midi_voices


def make_job(
    report_root: Path,
    job_id: str,
    *,
    midi_sha256: str = "a" * 64,
    instrument_id: int = 0,
    created_at: str = "2026-08-01T08:00:00Z",
) -> dict[str, object]:
    folder = report_root / "jobs" / job_id
    folder.mkdir(parents=True)
    (folder / "output.wav").write_bytes(b"RIFF-fixture")
    return {
        "id": job_id,
        "kind": "midi-ddsp-render",
        "state": "succeeded",
        "created_at": created_at,
        "updated_at": created_at,
        "metadata": {
            "midi_id": "midi-canon",
            "midi_name": "canon.mid",
            "midi_sha256": midi_sha256,
            "model_bundle_id": "origin",
            "model_bundle": "Stateful v2",
            "instrument_id": instrument_id,
            "instrument_ids": [instrument_id],
            "seed": 20260724,
            "sample_rate": 48000,
            "output_gain_db": 0,
            "tail_seconds": 2,
            "report": {"duration_seconds": 12.5},
        },
    }


class MidiDdspLibraryTest(unittest.TestCase):
    def test_schema_import_is_idempotent_and_groups_versions(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            report_root = Path(folder)
            library = MidiDdspLibrary(report_root, report_root / "jobs")
            first = make_job(report_root, "render-one")
            second = make_job(
                report_root,
                "render-two",
                instrument_id=2,
                created_at="2026-08-01T09:00:00Z",
            )
            library.synchronize([first, second, first], [])

            tracks = library.list_tracks()
            self.assertEqual(len(tracks), 1)
            self.assertEqual(tracks[0]["version_count"], 2)
            self.assertEqual(tracks[0]["default_render_id"], "render-two")
            self.assertEqual(len(library.versions(tracks[0]["source_id"])), 2)

            with library._connection() as connection:
                self.assertEqual(
                    int(connection.execute("PRAGMA user_version").fetchone()[0]),
                    SCHEMA_VERSION,
                )

    def test_preference_overrides_latest_and_can_be_cleared(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            report_root = Path(folder)
            library = MidiDdspLibrary(report_root, report_root / "jobs")
            jobs = [
                make_job(report_root, "older"),
                make_job(
                    report_root,
                    "newer",
                    created_at="2026-08-01T09:00:00Z",
                ),
            ]
            library.synchronize(jobs, [])
            source_id = library.list_tracks()[0]["source_id"]

            preferred = library.set_preference(
                source_id, "older", "2026-08-01T10:00:00Z"
            )
            self.assertEqual(preferred["default_render_id"], "older")
            cleared = library.set_preference(
                source_id, None, "2026-08-01T10:01:00Z"
            )
            self.assertEqual(cleared["default_render_id"], "newer")

    def test_missing_wav_is_reported_without_deleting_version(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            report_root = Path(folder)
            library = MidiDdspLibrary(report_root, report_root / "jobs")
            job = make_job(report_root, "missing-later")
            (report_root / "jobs" / "missing-later" / "output.wav").unlink()
            library.index_job(job)

            track = library.list_tracks()[0]
            self.assertEqual(track["version_count"], 1)
            self.assertEqual(track["available_version_count"], 0)
            self.assertIsNone(track["default_render_id"])
            self.assertFalse(library.version("missing-later")["available"])

    def test_corrupt_database_is_quarantined_and_rebuilt_from_jobs(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            report_root = Path(folder)
            report_root.mkdir(parents=True, exist_ok=True)
            database = report_root / "library.sqlite3"
            database.write_bytes(b"not-a-sqlite-database")
            library = MidiDdspLibrary(report_root, report_root / "jobs")
            job = make_job(report_root, "recovered")

            library.synchronize([job], [])

            self.assertEqual(library.list_tracks()[0]["default_render_id"], "recovered")
            self.assertTrue(database.is_file())
            self.assertEqual(len(list(report_root.glob("library.sqlite3.corrupt-*"))), 1)


class MidiPianoRollTest(unittest.TestCase):
    def test_voice_analysis_and_roll_share_the_partition_cache(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "shared-cache.mid"
            midi = mido.MidiFile(type=0, ticks_per_beat=480)
            track = mido.MidiTrack()
            track.append(mido.Message("note_on", note=71, velocity=73, time=0))
            track.append(mido.Message("note_off", note=71, velocity=0, time=37))
            midi.tracks.append(track)
            midi.save(path)

            with patch.object(
                midi_analysis,
                "split_midi_voices",
                wraps=midi_analysis.split_midi_voices,
            ) as split:
                analyze_midi_voices(path)
                analyze_midi_piano_roll(path)

            self.assertEqual(split.call_count, 1)

    def test_short_notes_and_timing_are_returned(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "short.mid"
            midi = mido.MidiFile(type=0, ticks_per_beat=480)
            track = mido.MidiTrack()
            track.append(mido.MetaMessage("time_signature", numerator=3, denominator=4, time=0))
            track.append(mido.MetaMessage("set_tempo", tempo=600_000, time=0))
            track.append(mido.Message("program_change", program=40, time=0))
            track.append(mido.Message("note_on", note=60, velocity=100, time=0))
            track.append(mido.Message("note_off", note=60, velocity=0, time=1))
            track.append(mido.Message("note_on", note=64, velocity=80, time=10))
            track.append(mido.Message("note_off", note=64, velocity=0, time=240))
            midi.tracks.append(track)
            midi.save(path)

            result = analyze_midi_piano_roll(path)
            notes = [note for voice in result["voices"] for note in voice["notes"]]
            self.assertEqual(result["note_count"], 2)
            self.assertEqual(len(notes), 2)
            self.assertGreaterEqual(min(note["duration_seconds"] for note in notes), 0.001)
            self.assertEqual(result["timing"]["time_signatures"][0]["numerator"], 3)
            self.assertAlmostEqual(result["timing"]["tempo_changes"][0]["bpm"], 100.0)


if __name__ == "__main__":
    unittest.main()
