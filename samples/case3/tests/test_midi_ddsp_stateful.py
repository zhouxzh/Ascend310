from __future__ import annotations

from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

import mido
import numpy as np

from midi_ddsp_realtime import _boundary_continuity, _stem_seed
from midi_ddsp_webui.midi_analysis import analyze_midi
from midi_ddsp_webui.model_bundle import SOURCE_COMMIT, STATEFUL_COMPONENTS, load_runtime_bundle
from midi_ddsp_webui.stateful_midi_ddsp import StatefulMidiDdspInference


class _ContextRunner:
    def __init__(self, observed_states: list[np.ndarray]) -> None:
        self.observed_states = observed_states

    def infer(self, feeds: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        state = feeds["state_in"].copy()
        self.observed_states.append(state)
        block = next(value for name, value in feeds.items() if name != "state_in")
        width = state.shape[1]
        context = np.repeat(state[:, None, :], block.shape[1], axis=1)
        return {"context": context.astype(np.float32), "state_out": state + 1.0}


class _Component:
    def __init__(self, runner: _ContextRunner) -> None:
        self.runner = runner

    def open(self, _device_id: int):
        return nullcontext(self.runner)


class _Bundle:
    expression_block = 2
    synthesis_block = 2

    def __init__(self, runner: _ContextRunner) -> None:
        self.runner = runner

    def component(self, _name: str) -> _Component:
        return _Component(self.runner)


class StatefulInferenceTest(unittest.TestCase):
    def test_forward_context_carries_state_between_blocks(self) -> None:
        observed: list[np.ndarray] = []
        inference = StatefulMidiDdspInference(_Bundle(_ContextRunner(observed)))
        values = np.ones((4, 3), dtype=np.float32)
        context = inference._context_pass(
            "forward",
            values,
            256,
            reverse=False,
            input_name="z_midi",
        )
        self.assertEqual(len(observed), 2)
        np.testing.assert_array_equal(observed[0], np.zeros((1, 256), dtype=np.float32))
        np.testing.assert_array_equal(observed[1], np.ones((1, 256), dtype=np.float32))
        np.testing.assert_array_equal(context[:2], np.zeros((2, 256), dtype=np.float32))
        np.testing.assert_array_equal(context[2:], np.ones((2, 256), dtype=np.float32))

    def test_rest_frames_have_no_relative_position(self) -> None:
        onsets = np.asarray([1, 0, 1, 0, 1, 0], dtype=np.int64)
        pitch = np.asarray([[60], [60], [0], [0], [62], [62]], dtype=np.float32)
        position = StatefulMidiDdspInference.relative_position(onsets, pitch)
        np.testing.assert_allclose(position[:, 0], [0.5, 1.0, 0.0, 0.0, 0.5, 1.0])

    def test_track_seed_is_stable_and_track_specific(self) -> None:
        self.assertEqual(_stem_seed(20260724, 3), _stem_seed(20260724, 3))
        self.assertNotEqual(_stem_seed(20260724, 3), _stem_seed(20260724, 4))

    def test_boundary_metric_reports_known_jump(self) -> None:
        result = _boundary_continuity(
            np.asarray([0.0, 0.1, 0.7, 0.6, 0.5, -0.5], dtype=np.float32), 2
        )
        self.assertEqual(result["count"], 2)
        self.assertAlmostEqual(result["max_abs_jump"], 0.6, places=6)


class MidiAnalysisTest(unittest.TestCase):
    def test_monophonic_supported_tracks_can_be_rendered_as_stems(self) -> None:
        midi = mido.MidiFile(type=1, ticks_per_beat=480)
        for program, pitch in ((40, 60), (73, 72)):
            track = mido.MidiTrack()
            track.append(mido.Message("program_change", program=program, time=0))
            track.append(mido.Message("note_on", note=pitch, velocity=80, time=0))
            track.append(mido.Message("note_off", note=pitch, velocity=0, time=480))
            midi.tracks.append(track)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "duet.mid"
            midi.save(path)
            analysis = analyze_midi(path)
        self.assertEqual(analysis.mode, "multitrack")
        self.assertTrue(analysis.supported)
        self.assertEqual([track.instrument_id for track in analysis.tracks], [0, 4])

    def test_chord_in_one_track_is_rejected(self) -> None:
        midi = mido.MidiFile(type=0, ticks_per_beat=480)
        track = mido.MidiTrack()
        track.append(mido.Message("note_on", note=60, velocity=80, time=0))
        track.append(mido.Message("note_on", note=64, velocity=80, time=0))
        track.append(mido.Message("note_off", note=60, velocity=0, time=480))
        track.append(mido.Message("note_off", note=64, velocity=0, time=0))
        midi.tracks.append(track)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "chord.mid"
            midi.save(path)
            analysis = analyze_midi(path)
        self.assertFalse(analysis.supported)
        self.assertEqual(analysis.unsupported_code, "polyphonic_track")


class RuntimeBundleTest(unittest.TestCase):
    def _manifest(self, root: Path) -> Path:
        components = {}
        for name in STATEFUL_COMPONENTS:
            path = root / f"{name}.om"
            path.write_bytes(name.encode("ascii"))
            components[name] = {
                "file": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "inputs": [{"name": "input", "shape": [1, 1], "type": "float32"}],
                "outputs": [{"name": "output", "shape": [1, 1], "type": "float32"}],
            }
        manifest = root / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "id": "fixture",
                    "name": "Fixture",
                    "architecture": "stateful-v2",
                    "source_commit": SOURCE_COMMIT,
                    "seed": 20260724,
                    "expression_block": 32,
                    "synthesis_block": 64,
                    "timbre_halo": 124,
                    "components": components,
                }
            ),
            encoding="utf-8",
        )
        return manifest

    def test_manifest_validates_all_component_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bundle = load_runtime_bundle(self._manifest(root))
            self.assertEqual(set(bundle.components), STATEFUL_COMPONENTS)
            first = next(iter(bundle.components.values()))
            first.path.write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                load_runtime_bundle(root / "manifest.json")

    def test_manifest_rejects_component_path_escape(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = self._manifest(root)
            data = json.loads(manifest.read_text(encoding="utf-8"))
            name = next(iter(data["components"]))
            data["components"][name]["file"] = "../outside.om"
            manifest.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "escapes bundle directory"):
                load_runtime_bundle(manifest)


if __name__ == "__main__":
    unittest.main()
