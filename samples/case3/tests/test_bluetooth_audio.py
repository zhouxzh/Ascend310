from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from midi_ddsp_webui.bluetooth import (
    connect_bluetooth_audio_device,
    normalize_address,
    parse_bluetooth_controller,
    parse_bluetooth_device_info,
    query_bluetooth_audio_devices,
    select_a2dp_profile,
)


def completed(stdout: str, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, "")


class BluetoothParsingTest(unittest.TestCase):
    def test_controller_state_is_parsed_from_bluetoothctl_show(self) -> None:
        controller = parse_bluetooth_controller(
            "\n".join(
                [
                    "Controller 00:11:22:33:44:55 ascend8t",
                    "    Name: ascend8t",
                    "    Powered: yes",
                    "    Discovering: no",
                    "    Pairable: yes",
                ]
            )
        )
        self.assertIsNotNone(controller)
        assert controller is not None
        self.assertEqual(controller["address"], "00:11:22:33:44:55")
        self.assertTrue(controller["powered"])
        self.assertFalse(controller["discovering"])

    def test_audio_speaker_info_is_marked_as_audio(self) -> None:
        device = parse_bluetooth_device_info(
            "c8:24:78:d5:b2:9e",
            "\n".join(
                [
                    "Device C8:24:78:D5:B2:9E EDIFIER M16 Pro",
                    "    Name: EDIFIER M16 Pro",
                    "    Alias: EDIFIER M16 Pro",
                    "    Icon: audio-card",
                    "    Paired: yes",
                    "    Trusted: yes",
                    "    Connected: yes",
                    "    UUID: Audio Sink (0000110b-0000-1000-8000-00805f9b34fb)",
                    "    RSSI: -48",
                ]
            ),
        )
        self.assertEqual(device["address"], "C8:24:78:D5:B2:9E")
        self.assertEqual(device["name"], "EDIFIER M16 Pro")
        self.assertTrue(device["is_audio"])
        self.assertEqual(device["status"], "connected")
        self.assertEqual(device["rssi"], -48)

    def test_invalid_address_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "Bluetooth address"):
            normalize_address("not-a-mac")


class BluetoothCatalogTest(unittest.TestCase):
    def test_query_returns_connected_audio_first(self) -> None:
        info = {
            "C8:24:78:D5:B2:9E": "\n".join(
                [
                    "Device C8:24:78:D5:B2:9E EDIFIER M16 Pro",
                    "    Name: EDIFIER M16 Pro",
                    "    Icon: audio-card",
                    "    Connected: yes",
                    "    UUID: Audio Sink (0000110b-0000-1000-8000-00805f9b34fb)",
                ]
            ),
            "11:22:33:44:55:66": "\n".join(
                [
                    "Device 11:22:33:44:55:66 Keyboard",
                    "    Name: Keyboard",
                    "    Connected: no",
                ]
            ),
        }

        def run(args, **_kwargs):
            if args == ["show"]:
                return completed("Controller 00:11:22:33:44:55 ascend8t\n    Powered: yes\n")
            if args == ["devices"]:
                return completed(
                    "\n".join(
                        [
                            "Device 11:22:33:44:55:66 Keyboard",
                            "Device C8:24:78:D5:B2:9E EDIFIER M16 Pro",
                        ]
                    )
                )
            if args[0] == "info":
                return completed(info[args[1]])
            raise AssertionError(args)

        with patch("midi_ddsp_webui.bluetooth.run_bluetoothctl", side_effect=run):
            state = query_bluetooth_audio_devices()
        self.assertEqual(state["devices"][0]["name"], "EDIFIER M16 Pro")
        self.assertTrue(state["devices"][0]["connected"])
        self.assertTrue(state["devices"][0]["is_audio"])

    def test_connect_uses_validated_bluetoothctl_sequence(self) -> None:
        initial_info = "\n".join(
            [
                "Device C8:24:78:D5:B2:9E EDIFIER M16 Pro",
                "    Name: EDIFIER M16 Pro",
                "    Paired: yes",
                "    Trusted: no",
                "    Connected: no",
                "    UUID: Audio Sink (0000110b-0000-1000-8000-00805f9b34fb)",
            ]
        )
        trusted_info = initial_info.replace("Trusted: no", "Trusted: yes")
        connected_info = trusted_info.replace("Connected: no", "Connected: yes")

        def wait(_address, field, _expected, **_kwargs):
            return parse_bluetooth_device_info(
                "C8:24:78:D5:B2:9E",
                connected_info if field == "connected" else trusted_info,
            )

        with patch("midi_ddsp_webui.bluetooth.run_bluetoothctl_script") as script:
            with patch("midi_ddsp_webui.bluetooth.wait_for_bluetooth_device", side_effect=wait):
                with patch(
                    "midi_ddsp_webui.bluetooth.run_bluetoothctl",
                    return_value=completed(initial_info),
                ) as command:
                    with patch(
                        "midi_ddsp_webui.bluetooth.select_a2dp_profile",
                        return_value={"selected": "a2dp_sink", "error": None},
                    ):
                        result = connect_bluetooth_audio_device(
                            "c8:24:78:d5:b2:9e",
                            pair=False,
                            trust=True,
                        )

        calls = [item.args[0] for item in command.call_args_list]
        self.assertIn(["power", "on"], calls)
        self.assertIn(["agent", "on"], calls)
        self.assertIn(["default-agent"], calls)
        self.assertIn(["trust", "C8:24:78:D5:B2:9E"], calls)
        self.assertIn(["connect", "C8:24:78:D5:B2:9E"], calls)
        self.assertNotIn(["pair", "C8:24:78:D5:B2:9E"], calls)
        script.assert_not_called()
        self.assertEqual(result["profile"]["selected"], "a2dp_sink")
        self.assertTrue(result["device"]["connected"])

    def test_select_a2dp_profile_accepts_active_pulse_profile(self) -> None:
        card = "\n".join(
            [
                "Card #3",
                "    Name: bluez_card.C8_24_78_D5_B2_9E",
                "    Profiles:",
                "        a2dp_sink: High Fidelity Playback (A2DP Sink) (sinks: 1, sources: 0, priority: 40, available: yes)",
                "        handsfree_head_unit: Handsfree Head Unit (HFP) (sinks: 1, sources: 1, priority: 30, available: yes)",
                "        off: Off (sinks: 0, sources: 0, priority: 0, available: yes)",
                "    Active Profile: a2dp_sink",
            ]
        )
        sinks = (
            "3\tbluez_sink.C8_24_78_D5_B2_9E.a2dp_sink\t"
            "module-bluez5-device.c\ts16le 2ch 44100Hz\tSUSPENDED\n"
        )
        calls = []

        def pactl(args, **_kwargs):
            calls.append(args)
            if args == ["list", "cards"]:
                return completed(card)
            if args == ["list", "short", "sinks"]:
                return completed(sinks)
            if args == ["set-default-sink", "bluez_sink.C8_24_78_D5_B2_9E.a2dp_sink"]:
                return completed("")
            raise AssertionError(args)

        with patch("midi_ddsp_webui.bluetooth.shutil.which", return_value="/usr/bin/pactl"):
            with patch("midi_ddsp_webui.bluetooth._run_pactl", side_effect=pactl):
                result = select_a2dp_profile("C8:24:78:D5:B2:9E")

        self.assertEqual(result["selected"], "a2dp_sink")
        self.assertEqual(result["sink"], "bluez_sink.C8_24_78_D5_B2_9E.a2dp_sink")
        self.assertIsNone(result["error"])
        self.assertNotIn(
            ["set-card-profile", "bluez_card.C8_24_78_D5_B2_9E", "a2dp_sink"],
            calls,
        )


if __name__ == "__main__":
    unittest.main()
