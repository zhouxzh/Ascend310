from __future__ import annotations

from pathlib import Path
import queue
import tempfile
import threading
import unittest

import numpy as np

from midi_ddsp_webui.core import ResourceBusyError, ResourceCoordinator
from midi_ddsp_webui.live import DdspVstSessionController
from midi_ddsp_webui.realtime_session import (
    RealtimeSessionController,
    _public_patch,
    _runtime_payload,
    _select_output,
    map_parameters,
)


class FakeAdapter:
    def __init__(self, engine: str) -> None:
        self.engine = engine
        self.running = False
        self.fail_start = False
        self.player_state = {
            "state": "empty",
            "path": None,
            "position_seconds": 0.0,
            "duration_seconds": 0.0,
            "tempo": 1.0,
            "loop": False,
        }
        self.recording = False
        self.calls: list[tuple[object, ...]] = []

    def start(self, config: dict[str, object]) -> dict[str, object]:
        self.calls.append(("start", config["patch_id"]))
        if self.fail_start:
            raise RuntimeError(f"{self.engine} start failed")
        self.running = True
        return self.status()

    def stop(self) -> dict[str, object]:
        self.calls.append(("stop",))
        self.running = False
        return self.status()

    def status(self) -> dict[str, object]:
        return {
            "running": self.running,
            "active_notes": [],
            "player": dict(self.player_state),
            "recording": {"active": self.recording, "id": None},
        }

    def note_on(self, source: str, note: int, velocity: int) -> None:
        self.calls.append(("note_on", source, note, velocity))

    def note_off(self, source: str, note: int) -> None:
        self.calls.append(("note_off", source, note))

    def sustain(self, source: str, enabled: bool) -> None:
        self.calls.append(("sustain", source, enabled))

    def pitch_bend(self, value: int) -> None:
        self.calls.append(("pitch_bend", value))

    def release_source(self, source: str) -> None:
        self.calls.append(("release", source))

    def panic(self) -> None:
        self.calls.append(("panic",))

    def parameters(self, values: dict[str, object]) -> object:
        self.calls.append(("parameters", values))
        return values

    def player(self, action: str, **values: object) -> object:
        self.calls.append(("player", action, values))
        if action == "load":
            self.player_state.update(state="loaded", path=str(values["path"]))
        elif action == "pause":
            self.player_state["state"] = "paused"
        elif action == "play":
            self.player_state["state"] = "playing"
        elif action == "seek":
            self.player_state["position_seconds"] = float(values["position_seconds"])
        elif action == "tempo":
            self.player_state["tempo"] = float(values["value"])
        elif action == "loop":
            self.player_state["loop"] = bool(values["enabled"])
        return dict(self.player_state)

    def record_start(self, recording_id: str) -> object:
        self.recording = True
        return {"id": recording_id}

    def record_stop(self) -> object:
        self.recording = False
        return None

    def monitor(self, source: str, enabled: bool) -> None:
        self.calls.append(("monitor", source, enabled))


def fixture_catalog() -> dict[str, object]:
    return {
        "_patches": [
            {
                "patch_id": "piano.paper",
                "name": "Grand Piano",
                "_engine": "piano-ddsp",
                "_model_id": "paper",
                "compatible_audio_device_ids": ["shared"],
            },
            {
                "patch_id": "neural.violin",
                "name": "Violin",
                "_engine": "ddsp-vst",
                "_model_id": "violin",
                "compatible_audio_device_ids": ["shared"],
            },
        ],
        "audio_devices": [{"id": "shared", "name": "USB", "is_default": True}],
    }


def fixture_config(
    patch: dict[str, object], values: dict[str, object], _data: dict[str, object]
) -> dict[str, object]:
    return {
        "patch_id": patch["patch_id"],
        "audio_device_id_public": values.get("audio_device_id", "shared"),
        "latency_profile": values.get("latency_profile", "balanced"),
    }


class RealtimeSessionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.coordinator = ResourceCoordinator()
        self.piano = FakeAdapter("piano-ddsp")
        self.ddsp = FakeAdapter("ddsp-vst")
        self.controller = RealtimeSessionController(
            self.coordinator,
            adapters={"piano-ddsp": self.piano, "ddsp-vst": self.ddsp},
            catalog_provider=fixture_catalog,
            config_resolver=fixture_config,
        )

    def tearDown(self) -> None:
        if self.coordinator.owner == self.controller.OWNER:
            self.controller.stop()

    def test_public_patch_strips_server_metadata(self) -> None:
        public = _public_patch(fixture_catalog()["_patches"][0])
        self.assertNotIn("_engine", public)
        self.assertNotIn("_model_id", public)
        self.assertEqual(public["patch_id"], "piano.paper")

    def test_parameter_aliases_are_engine_specific(self) -> None:
        self.assertEqual(
            map_parameters("piano-ddsp", {"reverb": 0.5, "transpose": 2}),
            {"reverb_mix": 0.5, "transpose": 2},
        )
        self.assertEqual(
            map_parameters("ddsp-vst", {"reverb": 0.25, "transpose": -3}),
            {"reverb_wet": 0.25, "pitch_shift": -3},
        )
        self.assertEqual(
            map_parameters("piano-ddsp", {"output_gain_db": 3.0}),
            {"output_gain_db": 3.0},
        )

    def test_runtime_payload_removes_unified_session_metadata(self) -> None:
        self.assertEqual(
            _runtime_payload(
                {
                    "patch_id": "piano.paper",
                    "audio_device_id_public": "shared",
                    "bundle_id": "bundle-v1",
                    "model_id": "paper",
                    "latency_profile": "balanced",
                }
            ),
            {"model_id": "paper", "latency_profile": "balanced"},
        )

    def test_output_compatibility_is_checked_before_switch(self) -> None:
        patch = fixture_catalog()["_patches"][0]
        data = {
            "audio_devices": [
                {"id": "piano-only", "name": "Onboard", "is_default": True}
            ]
        }
        with self.assertRaisesRegex(ValueError, "not compatible"):
            _select_output(patch, "piano-only", data)

    def test_session_keeps_exclusive_owner_across_switch(self) -> None:
        self.controller.start({"patch_id": "piano.paper"})
        self.assertEqual(self.coordinator.owner, "realtime-session")
        with self.assertRaises(ResourceBusyError):
            self.coordinator.acquire("offline-job")
        result = self.controller.switch({"patch_id": "neural.violin"})
        self.assertEqual(result["patch_id"], "neural.violin")
        self.assertEqual(self.coordinator.owner, "realtime-session")

    def test_switch_restores_midi_position_tempo_loop_and_playing_state(self) -> None:
        self.controller.start({"patch_id": "piano.paper"})
        self.piano.player_state.update(
            state="playing",
            path=str(Path(tempfile.gettempdir()) / "song.mid"),
            position_seconds=12.5,
            duration_seconds=40.0,
            tempo=1.25,
            loop=True,
        )
        self.controller.switch({"patch_id": "neural.violin"})
        calls = self.ddsp.calls
        self.assertIn(("player", "seek", {"position_seconds": 12.5}), calls)
        self.assertIn(("player", "tempo", {"value": 1.25}), calls)
        self.assertIn(("player", "loop", {"enabled": True}), calls)
        self.assertIn(("player", "play", {}), calls)

    def test_recording_locks_patch_switch(self) -> None:
        self.controller.start({"patch_id": "piano.paper"})
        self.piano.recording = True
        with self.assertRaisesRegex(RuntimeError, "Stop recording"):
            self.controller.switch({"patch_id": "neural.violin"})
        self.assertTrue(self.piano.running)
        self.assertEqual(self.coordinator.owner, "realtime-session")

    def test_failed_switch_rolls_back_without_releasing_owner(self) -> None:
        self.controller.start({"patch_id": "piano.paper"})
        self.ddsp.fail_start = True
        result = self.controller.switch({"patch_id": "neural.violin"})
        self.assertEqual(result["patch_id"], "piano.paper")
        self.assertTrue(result["last_switch"]["rolled_back"])
        self.assertTrue(self.piano.running)
        self.assertEqual(self.coordinator.owner, "realtime-session")

    def test_failed_switch_and_rollback_release_owner(self) -> None:
        self.controller.start({"patch_id": "piano.paper"})
        self.ddsp.fail_start = True
        self.piano.fail_start = True
        with self.assertRaisesRegex(RuntimeError, "rollback failed"):
            self.controller.switch({"patch_id": "neural.violin"})
        self.assertIsNone(self.coordinator.owner)
        self.assertEqual(self.controller.status()["state"], "failed")

    def test_audio_tap_drops_newest_block_when_queue_is_full(self) -> None:
        controller = object.__new__(DdspVstSessionController)
        controller._lock = threading.RLock()
        controller._tap_queue = queue.Queue(maxsize=1)
        controller._tap_queue.put_nowait(np.zeros((2, 2), dtype=np.float32))
        controller._audio_tap_drops = 0
        controller._recording_accepts_blocks = False
        controller._audio_tap(np.ones((2, 2), dtype=np.float32))
        self.assertEqual(controller._audio_tap_drops, 1)
        np.testing.assert_array_equal(
            controller._tap_queue.get_nowait(), np.zeros((2, 2), dtype=np.float32)
        )


if __name__ == "__main__":
    unittest.main()
