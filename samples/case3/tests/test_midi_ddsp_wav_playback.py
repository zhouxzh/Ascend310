from __future__ import annotations

from pathlib import Path
import signal
import tempfile
import unittest
from unittest.mock import Mock, patch
import wave

from midi_ddsp_webui import app as web_app
from midi_ddsp_webui import core
from midi_ddsp_webui.wav_playback import (
    paplay_command,
    pulse_volume,
    wav_duration_seconds,
)


class WavPlaybackCommandTest(unittest.TestCase):
    def test_reads_duration_and_builds_bounded_paplay_command(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "output.wav"
            with wave.open(str(path), "wb") as target:
                target.setnchannels(1)
                target.setsampwidth(2)
                target.setframerate(48_000)
                target.writeframes(b"\0\0" * 96_000)
            self.assertEqual(wav_duration_seconds(path), 2.0)
            command = paplay_command(path, "usb-speaker", 40.4, -18.0)
        self.assertEqual(command[0], "paplay")
        self.assertIn("--device=usb-speaker", command)
        self.assertIn("--latency-msec=40", command)
        self.assertIn(f"--volume={pulse_volume(-18.0)}", command)
        self.assertEqual(command[-1], str(path))

    def test_pulse_volume_is_bounded(self) -> None:
        self.assertEqual(pulse_volume(3.0), 65_536)
        self.assertEqual(pulse_volume(-60.0), 66)
        self.assertEqual(pulse_volume(-90.0), 66)

    def test_api_uses_the_wav_original_level_by_default(self) -> None:
        self.assertEqual(web_app.MidiDdspPlaybackRequest().output_gain_db, 0.0)


class ExistingRecordingApiTest(unittest.TestCase):
    def test_starts_existing_wav_on_selected_pulse_output(self) -> None:
        source = core.Job(
            id="source123",
            kind="midi-ddsp-render",
            state="succeeded",
            metadata={
                "midi_name": "canon.mid",
                "midi_id": "midi-canon",
                "model_bundle_id": "origin",
                "instrument_id": 0,
                "seed": 7,
            },
        )
        playback = core.Job(id="playback123", kind="midi-ddsp-wav-playback")
        output = {
            "id": "pulse:edifier",
            "name": "EDIFIER M16 Pro Analog Stereo",
            "backend": "pulse",
            "sink_name": "alsa_output.usb-edifier",
            "is_default": True,
        }
        with tempfile.TemporaryDirectory() as folder:
            wav_path = Path(folder) / "output.wav"
            wav_path.write_bytes(b"RIFF")
            with (
                patch.object(web_app, "_require_board"),
                patch.object(web_app.jobs, "get", return_value=source),
                patch.object(web_app, "resolve_artifact", return_value=wav_path),
                patch.object(web_app, "query_speaker_outputs", return_value=[output]),
                patch.object(web_app.jobs, "start", return_value=playback) as start,
            ):
                result = web_app.play_midi_ddsp_recording(
                    source.id,
                    web_app.MidiDdspPlaybackRequest(
                        audio_device_id="pulse:edifier",
                        latency_ms=35,
                        output_gain_db=-24,
                    ),
                )
        self.assertEqual(result["id"], playback.id)
        kind, command = start.call_args.args
        metadata = start.call_args.kwargs["metadata"]
        self.assertEqual(kind, "midi-ddsp-wav-playback")
        self.assertIn("midi_ddsp_webui.wav_playback", command)
        self.assertIn("alsa_output.usb-edifier", command)
        self.assertIn("-24.0", command)
        self.assertEqual(metadata["source_job_id"], source.id)
        self.assertEqual(metadata["midi_name"], "canon.mid")
        self.assertEqual(metadata["output_gain_db"], -24)

    def test_existing_wav_playback_can_be_paused_and_resumed(self) -> None:
        manager = core.JobManager(core.ResourceCoordinator())
        process = Mock()
        process.poll.return_value = None
        process.pid = 123
        job = core.Job(
            id="playback",
            kind="midi-ddsp-wav-playback",
            state="running",
            process=process,
        )
        manager._jobs[job.id] = job
        with (
            patch.object(core.os, "name", "posix"),
            patch.object(core.os, "kill") as kill,
            patch.object(core.signal, "SIGUSR1", 10, create=True),
            patch.object(core.signal, "SIGUSR2", 12, create=True),
            patch.object(manager, "_persist"),
            patch.object(manager, "_publish"),
        ):
            self.assertEqual(manager.pause(job.id).state, "paused")
            kill.assert_called_with(123, 10)
            self.assertEqual(manager.resume(job.id).state, "running")
            kill.assert_called_with(123, 12)


if __name__ == "__main__":
    unittest.main()
