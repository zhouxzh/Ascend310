from __future__ import annotations

import json
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

import numpy as np

from midi_ddsp_webui.core import ResourceBusyError, ResourceCoordinator
from midi_ddsp_webui.speaker import (
    AudioInputTestController,
    SpeakerTestController,
    amplitude_to_dbfs,
    build_test_signal,
    canonical_audio_output_name,
    configure_alsa_output_route,
    is_pulse_output_event,
    query_audio_inputs,
    query_ddsp_vst_audio_outputs,
    query_midi_ddsp_audio_outputs,
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


class FakeInputStream:
    def __init__(self, owner, **config) -> None:
        self.owner = owner
        self.config = config

    def __enter__(self):
        return self

    def __exit__(self, _exc_type, _exc, _traceback) -> None:
        return None

    def read(self, frames: int):
        self.owner.read_sizes.append(frames)
        return np.full((frames, self.owner.channels), self.owner.level, dtype=np.float32), False


class FakeInputSoundDevice:
    def __init__(self, channels: int = 1, level: float = 0.1) -> None:
        self.channels = channels
        self.level = level
        self.read_sizes: list[int] = []
        self.checked: dict[str, object] = {}

    def query_devices(self, device, kind):
        if kind != "input":
            raise AssertionError(kind)
        return {
            "name": f"Fake input {device}",
            "max_input_channels": self.channels,
            "default_samplerate": 8_000,
        }

    def check_input_settings(self, **config) -> None:
        self.checked = config

    def InputStream(self, **config):
        return FakeInputStream(self, **config)


class FakeCaptureOutput:
    def __init__(self, level: float = 0.1) -> None:
        self.level = level

    def read(self, size: int) -> bytes:
        return np.full(size // 4, self.level, dtype="<f4").tobytes()


class FakePulseCaptureProcess:
    def __init__(self, command: list[str]) -> None:
        self.command = command
        self.stdout = FakeCaptureOutput()
        self.stderr = FakePulseError()
        self.return_code: int | None = None

    def poll(self) -> int | None:
        return self.return_code

    def wait(self, timeout: float) -> int:
        self.return_code = -15
        return self.return_code

    def terminate(self) -> None:
        self.return_code = -15

    def kill(self) -> None:
        self.return_code = -9


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

    def kill(self) -> None:
        self.return_code = -9


class BlockingInput(FakePulseInput):
    def __init__(self, released: threading.Event) -> None:
        super().__init__()
        self.released = released

    def write(self, block: bytes) -> int:
        self.blocks.append(block)
        self.released.wait(timeout=2.0)
        return len(block)


class BlockingProcess(FakePulseProcess):
    def __init__(self, command: list[str]) -> None:
        super().__init__(command)
        self.released = threading.Event()
        self.stdin = BlockingInput(self.released)

    def wait(self, timeout: float) -> int:
        if self.return_code is None and not self.released.wait(timeout=timeout):
            raise subprocess.TimeoutExpired(self.command, timeout)
        return int(self.return_code or 0)

    def terminate(self) -> None:
        self.return_code = -15
        self.released.set()

    def kill(self) -> None:
        self.return_code = -9
        self.released.set()


def speaker_config(channel_mode: str = "both") -> dict[str, object]:
    return {
        "audio_device_id": "1",
        "device_name": "Fake output",
        "channel_mode": channel_mode,
        "frequency_hz": 440.0,
        "level_db": -18.0,
        "duration_seconds": 0.05,
    }


def input_test_config() -> dict[str, object]:
    return {
        "audio_input_id": "1",
        "audio_device_id": "1",
        "device_name": "Fake input",
        "audio_backend": "portaudio",
        "default_sample_rate": 8_000,
        "max_input_channels": 1,
        "duration_seconds": 0.05,
        "threshold_dbfs": -45.0,
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

    def test_audio_level_dbfs_has_a_stable_silence_floor(self) -> None:
        self.assertEqual(amplitude_to_dbfs(0.0), -96.0)
        self.assertAlmostEqual(amplitude_to_dbfs(0.1), -20.0)


class AudioInputControllerTest(unittest.TestCase):
    def test_capture_reports_levels_and_releases_the_audio_resource(self) -> None:
        coordinator = ResourceCoordinator()
        sounddevice = FakeInputSoundDevice(level=0.1)
        controller = AudioInputTestController(coordinator, sounddevice)
        controller.start(input_test_config())
        deadline = time.monotonic() + 1.0
        while controller.running and time.monotonic() < deadline:
            time.sleep(0.001)
        status = controller.status()
        self.assertEqual(status["state"], "succeeded")
        self.assertEqual(status["progress"], 1.0)
        self.assertAlmostEqual(float(status["rms_dbfs"]), -20.0, places=3)
        self.assertAlmostEqual(float(status["peak_dbfs"]), -20.0, places=3)
        self.assertTrue(status["signal_detected"])
        self.assertEqual(status["overflows"], 0)
        self.assertIsNone(coordinator.owner)
        self.assertTrue(sounddevice.read_sizes)

    def test_silence_does_not_report_a_detected_signal(self) -> None:
        coordinator = ResourceCoordinator()
        controller = AudioInputTestController(
            coordinator,
            FakeInputSoundDevice(level=0.0),
        )
        controller.start(input_test_config())
        deadline = time.monotonic() + 1.0
        while controller.running and time.monotonic() < deadline:
            time.sleep(0.001)
        status = controller.status()
        self.assertEqual(status["rms_dbfs"], -96.0)
        self.assertFalse(status["signal_detected"])
        self.assertIsNone(coordinator.owner)

    def test_pulse_capture_targets_the_selected_source(self) -> None:
        processes: list[FakePulseCaptureProcess] = []

        def create_process(command, **_options):
            process = FakePulseCaptureProcess(command)
            processes.append(process)
            return process

        coordinator = ResourceCoordinator()
        controller = AudioInputTestController(
            coordinator,
            popen_factory=create_process,
        )
        config = {
            **input_test_config(),
            "audio_backend": "pulse",
            "pulse_source": "alsa_input.usb-microphone.mono-fallback",
        }
        with patch("midi_ddsp_webui.speaker.shutil.which", return_value="/usr/bin/parec"):
            controller.start(config)
            deadline = time.monotonic() + 1.0
            while controller.running and time.monotonic() < deadline:
                time.sleep(0.001)
        self.assertEqual(controller.status()["state"], "succeeded")
        self.assertIn(
            "--device=alsa_input.usb-microphone.mono-fallback",
            processes[0].command,
        )
        self.assertIsNone(coordinator.owner)

    def test_other_audio_owner_blocks_input_testing(self) -> None:
        coordinator = ResourceCoordinator()
        coordinator.acquire("live-session")
        controller = AudioInputTestController(coordinator, FakeInputSoundDevice())
        with self.assertRaises(ResourceBusyError):
            controller.start(input_test_config())
        coordinator.release("live-session")


class SpeakerOutputCatalogTest(unittest.TestCase):
    def test_canonical_names_match_across_audio_backends(self) -> None:
        self.assertEqual(
            canonical_audio_output_name(
                {"id": "pulse:edifier", "name": "EDIFIER M16 Pro Analog Stereo"}
            ),
            "EDIFIER M16 Pro",
        )
        self.assertEqual(
            canonical_audio_output_name(
                {"id": "4", "name": "EDIFIER M16 Pro (hw:2,0)"}
            ),
            "EDIFIER M16 Pro",
        )
        self.assertEqual(
            canonical_audio_output_name(
                {
                    "id": "pulse:alsa_output.platform-sound.stereo-fallback",
                    "name": "Built-in Audio Stereo",
                }
            ),
            "板载 3.5 mm",
        )

    def test_ddsp_vst_outputs_hide_unsafe_onboard_route(self) -> None:
        outputs = [
            {
                "id": "pulse:alsa_output.platform-sound.stereo-fallback",
                "name": "Built-in Audio Stereo",
                "backend": "pulse",
                "is_default": True,
            },
            {
                "id": "pulse:usb-edifier",
                "name": "EDIFIER M16 Pro Analog Stereo",
                "backend": "pulse",
                "is_default": False,
            },
        ]
        with patch(
            "midi_ddsp_webui.speaker.query_speaker_outputs",
            return_value=outputs,
        ):
            compatible = query_ddsp_vst_audio_outputs(lambda: [])
        self.assertEqual([item["id"] for item in compatible], ["pulse:usb-edifier"])
        self.assertEqual(compatible[0]["name"], "EDIFIER M16 Pro")
        self.assertTrue(compatible[0]["is_default"])

    def test_midi_ddsp_outputs_exclude_unsupported_portaudio_and_keep_default(self) -> None:
        outputs = [
            {"id": "portaudio:2", "backend": "portaudio", "is_default": True},
            {"id": "pulse:usb", "backend": "pulse", "is_default": False},
            {"id": "alsa:onboard-headset", "backend": "alsa_mono", "is_default": False},
        ]
        with patch(
            "midi_ddsp_webui.speaker.query_piano_audio_outputs",
            return_value=outputs,
        ):
            compatible = query_midi_ddsp_audio_outputs(lambda: [])
        self.assertEqual(
            [item["id"] for item in compatible],
            ["pulse:usb", "alsa:onboard-headset"],
        )
        self.assertTrue(compatible[0]["is_default"])

    def test_pulse_catalog_exposes_friendly_bluetooth_sink(self) -> None:
        sinks = [
            {
                "index": 3,
                "state": "RUNNING",
                "name": "bluez_sink.C8_24_78_D5_B2_9E.a2dp_sink",
                "description": "EDIFIER M16 Pro",
                "sample_specification": "s16le 2ch 44100Hz",
                "properties": {"device.description": "EDIFIER M16 Pro"},
                "volume": {
                    "front-left": {
                        "value": 32_768,
                        "value_percent": "50%",
                        "db": "-6.02 dB",
                    },
                    "front-right": {
                        "value": 39_322,
                        "value_percent": "60%",
                        "db": "-4.44 dB",
                    },
                },
                "mute": False,
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
        self.assertTrue(outputs[0]["is_bluetooth"])
        self.assertEqual(outputs[0]["default_sample_rate"], 44_100)
        self.assertEqual(outputs[0]["system_volume_percent"], 55.0)
        self.assertEqual(outputs[0]["system_volume_db"], -5.23)
        self.assertFalse(outputs[0]["system_muted"])

    def test_pulse_output_event_filter_ignores_unrelated_stream_events(self) -> None:
        self.assertTrue(is_pulse_output_event("Event 'change' on sink #3"))
        self.assertTrue(is_pulse_output_event("Event 'change' on server #0"))
        self.assertFalse(is_pulse_output_event("Event 'new' on sink-input #42"))
        self.assertFalse(is_pulse_output_event("Event 'change' on source #1"))

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
    def test_thread_start_failure_releases_the_audio_resource(self) -> None:
        coordinator = ResourceCoordinator()
        controller = SpeakerTestController(coordinator, FakeSoundDevice())
        with patch(
            "midi_ddsp_webui.speaker.threading.Thread.start",
            side_effect=RuntimeError("thread unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "thread unavailable"):
                controller.start(speaker_config())
        self.assertIsNone(coordinator.owner)
        self.assertEqual(controller.status()["state"], "failed")

    def test_onboard_route_uses_vendor_mixer_controls_without_a_shell(self) -> None:
        completed = subprocess.CompletedProcess([], 0, "ok", "")
        with (
            patch("midi_ddsp_webui.speaker.shutil.which", return_value="/usr/bin/amixer"),
            patch(
                "midi_ddsp_webui.speaker.subprocess.run",
                return_value=completed,
            ) as run,
        ):
            configure_alsa_output_route(0, 2, 10)

        self.assertEqual(
            [call.args[0] for call in run.call_args_list],
            [
                ["amixer", "-c", "0", "set", "Playback", "10"],
                ["amixer", "-c", "0", "set", "Deviceid", "2"],
            ],
        )

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

    def test_onboard_test_uses_killable_mono_aplay_process(self) -> None:
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
            "audio_backend": "alsa_mono",
            "alsa_device": "hw:ascend310b",
            "alsa_card": 0,
            "alsa_route_device_id": 2,
            "alsa_playback_level": 10,
            "default_sample_rate": 8_000,
        }
        with (
            patch("midi_ddsp_webui.speaker.shutil.which", return_value="/usr/bin/aplay"),
            patch("midi_ddsp_webui.speaker.configure_alsa_output_route") as route,
        ):
            controller.start(config)
            deadline = time.monotonic() + 1.0
            while controller.running and time.monotonic() < deadline:
                time.sleep(0.001)

        status = controller.status()
        self.assertEqual(status["state"], "succeeded")
        self.assertEqual(status["output_channels"], 1)
        self.assertEqual(processes[0].command[0], "aplay")
        self.assertIn("hw:ascend310b", processes[0].command)
        self.assertEqual(processes[0].command[-2:], ["-c", "1"])
        self.assertTrue(processes[0].stdin.closed)
        self.assertGreater(len(processes[0].stdin.blocks), 0)
        route.assert_called_once_with(0, 2, 10)
        self.assertIsNone(coordinator.owner)

    def test_onboard_stop_interrupts_a_blocked_aplay_write(self) -> None:
        processes: list[BlockingProcess] = []

        def create_process(command, **_options):
            process = BlockingProcess(command)
            processes.append(process)
            return process

        coordinator = ResourceCoordinator()
        controller = SpeakerTestController(coordinator, popen_factory=create_process)
        config = {
            **speaker_config(),
            "audio_backend": "alsa_mono",
            "alsa_device": "hw:ascend310b",
            "default_sample_rate": 8_000,
        }
        with (
            patch("midi_ddsp_webui.speaker.shutil.which", return_value="/usr/bin/aplay"),
            patch("midi_ddsp_webui.speaker.configure_alsa_output_route"),
        ):
            controller.start(config)
            deadline = time.monotonic() + 1.0
            while not processes[0].stdin.blocks and time.monotonic() < deadline:
                time.sleep(0.001)
            status = controller.stop()

        self.assertEqual(status["state"], "stopped")
        self.assertFalse(status["running"])
        self.assertEqual(processes[0].return_code, -15)
        self.assertIsNone(coordinator.owner)

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
