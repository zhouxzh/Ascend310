from __future__ import annotations

import json
import subprocess
import time
import unittest
from unittest.mock import patch

import numpy as np

from midi_ddsp_webui.core import ResourceBusyError, ResourceCoordinator
from midi_ddsp_webui.speaker import (
    SpeakerTestController,
    build_test_signal,
    query_audio_inputs,
    query_speaker_outputs,
)


class FakeOutputStream:
    def __init__(self, owner, **config) -> None:
        self.owner = owner
        self.config = config

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    def write(self, block: np.ndarray) -> bool:
        self.owner.blocks.append(block.copy())
        return False


class FakeSoundDevice:
    def __init__(self, channels: int = 2) -> None:
        self.channels = channels
        self.blocks: list[np.ndarray] = []
        self.checked: dict[str, object] = {}

    def query_devices(self, device, kind):
        if kind != "output":
            raise AssertionError(kind)
        return {
            "name": f"Fake output {device}",
            "max_output_channels": self.channels,
            "default_samplerate": 8_000,
        }

    def check_output_settings(self, **config) -> None:
        self.checked = config

    def OutputStream(self, **config):
        return FakeOutputStream(self, **config)


class FakePulseInput:
    def __init__(self) -> None:
        self.blocks: list[bytes] = []
        self.closed = False

    def write(self, block: bytes) -> int:
        self.blocks.append(block)
        return len(block)

    def close(self) -> None:
        self.closed = True


class FakePulseError:
    def read(self) -> bytes:
        return b""


class FakePulseProcess:
    def __init__(self, command: list[str]) -> None:
        self.command = command
        self.stdin = FakePulseInput()
        self.stderr = FakePulseError()
        self.return_code: int | None = None

    def poll(self) -> int | None:
        return self.return_code

    def wait(self, timeout: float) -> int:
        self.return_code = 0
        return 0

    def terminate(self) -> None:
        self.return_code = -15


def speaker_config(channel_mode: str = "both") -> dict[str, object]:
    return {
        "audio_device_id": "1",
        "device_name": "Fake output",
        "channel_mode": channel_mode,
        "frequency_hz": 440.0,
        "level_db": -18.0,
        "duration_seconds": 0.05,
    }


class SpeakerSignalTest(unittest.TestCase):
    def test_left_channel_does_not_leak_into_right_channel(self) -> None:
        signal = build_test_signal(8_000, 0.1, 440.0, -12.0, 2, "left")
        self.assertGreater(float(np.max(np.abs(signal[:, 0]))), 0.0)
        self.assertTrue(np.all(signal[:, 1] == 0.0))
        self.assertEqual(float(signal[0, 0]), 0.0)
        self.assertEqual(float(signal[-1, 0]), 0.0)

    def test_both_channels_receive_the_same_test_tone(self) -> None:
        signal = build_test_signal(8_000, 0.1, 440.0, -12.0, 2, "both")
        np.testing.assert_array_equal(signal[:, 0], signal[:, 1])


class SpeakerOutputCatalogTest(unittest.TestCase):
    def test_pulse_catalog_exposes_friendly_bluetooth_sink(self) -> None:
        sinks = [
            {
                "index": 3,
                "state": "RUNNING",
                "name": "bluez_sink.C8_24_78_D5_B2_9E.a2dp_sink",
                "description": "EDIFIER M16 Pro",
                "sample_specification": "s16le 2ch 44100Hz",
                "properties": {"device.description": "EDIFIER M16 Pro"},
            }
        ]
        results = [
            subprocess.CompletedProcess([], 0, json.dumps(sinks), ""),
            subprocess.CompletedProcess([], 0, sinks[0]["name"] + "\n", ""),
        ]
        with patch("midi_ddsp_webui.speaker.shutil.which", return_value="/usr/bin/pactl"):
            with patch("midi_ddsp_webui.speaker.subprocess.run", side_effect=results):
                outputs = query_speaker_outputs()
        self.assertEqual(outputs[0]["name"], "EDIFIER M16 Pro")
        self.assertEqual(outputs[0]["backend"], "pulse")
        self.assertTrue(outputs[0]["is_default"])
        self.assertEqual(outputs[0]["default_sample_rate"], 44_100)

    def test_audio_inputs_distinguish_capture_from_monitor(self) -> None:
        sources = [
            {
                "index": 1,
                "state": "IDLE",
                "name": "alsa_input.usb-microphone.mono-fallback",
                "description": "USB Microphone",
                "sample_specification": "s16le 1ch 48000Hz",
                "properties": {"device.class": "sound"},
            },
            {
                "index": 2,
                "state": "IDLE",
                "name": "bluez_sink.device.a2dp_sink.monitor",
                "description": "Bluetooth Monitor",
                "sample_specification": "s16le 2ch 44100Hz",
                "properties": {"device.class": "monitor"},
            },
        ]
        result = subprocess.CompletedProcess([], 0, json.dumps(sources), "")
        with patch("midi_ddsp_webui.speaker.shutil.which", return_value="/usr/bin/pactl"):
            with patch("midi_ddsp_webui.speaker.subprocess.run", return_value=result):
                inputs = query_audio_inputs()
        self.assertEqual([item["type"] for item in inputs], ["capture", "monitor"])
        self.assertEqual([item["available"] for item in inputs], [True, False])


class SpeakerControllerTest(unittest.TestCase):
    def test_successful_test_releases_the_audio_resource(self) -> None:
        coordinator = ResourceCoordinator()
        sounddevice = FakeSoundDevice()
        controller = SpeakerTestController(coordinator, sounddevice)
        controller.start(speaker_config())
        deadline = time.monotonic() + 1.0
        while controller.running and time.monotonic() < deadline:
            time.sleep(0.001)
        status = controller.status()
        self.assertEqual(status["state"], "succeeded")
        self.assertEqual(status["progress"], 1.0)
        self.assertEqual(status["underruns"], 0)
        self.assertIsNone(coordinator.owner)
        self.assertGreater(len(sounddevice.blocks), 0)

    def test_failure_releases_the_audio_resource(self) -> None:
        coordinator = ResourceCoordinator()
        controller = SpeakerTestController(coordinator, FakeSoundDevice(channels=1))
        with self.assertRaisesRegex(RuntimeError, "stereo output"):
            controller.start(speaker_config("right"))
        self.assertEqual(controller.status()["state"], "failed")
        self.assertIsNone(coordinator.owner)

    def test_other_audio_owner_blocks_speaker_test(self) -> None:
        coordinator = ResourceCoordinator()
        coordinator.acquire("live-session")
        controller = SpeakerTestController(coordinator, FakeSoundDevice())
        with self.assertRaises(ResourceBusyError):
            controller.start(speaker_config())
        coordinator.release("live-session")

    def test_pulse_test_targets_the_selected_sink_without_a_shell(self) -> None:
        processes: list[FakePulseProcess] = []

        def create_process(command, **_options):
            process = FakePulseProcess(command)
            processes.append(process)
            return process

        coordinator = ResourceCoordinator()
        controller = SpeakerTestController(
            coordinator,
            sounddevice_module=FakeSoundDevice(),
            popen_factory=create_process,
        )
        config = {
            **speaker_config(),
            "audio_backend": "pulse",
            "pulse_sink": "bluez_sink.C8_24_78_D5_B2_9E.a2dp_sink",
            "max_output_channels": 2,
            "default_sample_rate": 8_000,
        }
        controller.start(config)
        deadline = time.monotonic() + 1.0
        while controller.running and time.monotonic() < deadline:
            time.sleep(0.001)
        self.assertEqual(controller.status()["state"], "succeeded")
        self.assertIn(
            "--device=bluez_sink.C8_24_78_D5_B2_9E.a2dp_sink",
            processes[0].command,
        )
        self.assertTrue(processes[0].stdin.closed)
        self.assertGreater(len(processes[0].stdin.blocks), 0)
        self.assertIsNone(coordinator.owner)
