from __future__ import annotations

from contextlib import nullcontext
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import mido
import numpy as np

import midi_ddsp_webui.stateful_midi_ddsp as stateful_module
from midi_ddsp_realtime import (
    MidiToken,
    _boundary_continuity,
    build_frame_features,
)
from midi_ddsp_webui.midi_analysis import analyze_midi, split_midi_voices
from midi_ddsp_webui.model_bundle import SOURCE_COMMIT, STATEFUL_COMPONENTS, load_runtime_bundle
from midi_ddsp_webui.stateful_midi_ddsp import (
    BatchedStatefulMidiDdspInference,
    StatefulMidiDdspInference,
)
from pyacl_midi_ddsp import MidiDdspAclRuntime


class _ContextRunner:
    def __init__(self, observed_states: list[np.ndarray]) -> None:
        self.observed_states = observed_states

    def infer(self, feeds: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        state = feeds["state_in"].copy()
        self.observed_states.append(state)
        block = next(value for name, value in feeds.items() if name != "state_in")
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

    def test_boundary_metric_reports_known_jump(self) -> None:
        result = _boundary_continuity(
            np.asarray([0.0, 0.1, 0.7, 0.6, 0.5, -0.5], dtype=np.float32), 2
        )
        self.assertEqual(result["count"], 2)
        self.assertAlmostEqual(result["max_abs_jump"], 0.6, places=6)


class _DeterministicRunner:
    def __init__(self, name: str, valid_frames: list[np.ndarray]) -> None:
        self.name = name
        self.valid_frames = valid_frames

    def infer(self, feeds: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        if "context_" in self.name:
            state = feeds["state_in"]
            source = feeds.get("z_midi", feeds.get("note_pitch"))
            context = np.repeat(state[:, None, :], source.shape[1], axis=1)
            return {"context": context, "state_out": state + 1.0}
        if "expression_decode" in self.name:
            pitch = feeds["note_pitch"].astype(np.float32) / 127.0
            controls = np.repeat(pitch[..., None], 6, axis=-1)
            return {
                "expression_controls": controls,
                "previous_controls_out": controls[:, -1],
                "state1_out": feeds["state1_in"] + 1.0,
                "state2_out": feeds["state2_in"] + 1.0,
            }
        if "precondition" in self.name:
            return {"z_midi": np.repeat(feeds["q_pitch"], 320, axis=-1)}
        if "f0_decode" in self.name:
            sampled = np.argmax(feeds["gumbel"], axis=-1).astype(np.int64)
            f0_midi = feeds["q_pitch"].astype(np.float32)
            return {
                "f0_hz": f0_midi * 10.0,
                "f0_midi": f0_midi,
                "sampled_bins": sampled,
                "previous_f0_out": np.eye(
                    feeds["previous_f0"].shape[-1], dtype=np.float32
                )[sampled[:, -1]],
                "state1_out": feeds["state1_in"] + 1.0,
                "state2_out": feeds["state2_in"] + 1.0,
            }
        if "timbre" in self.name:
            self.valid_frames.append(feeds["valid_frames"].copy())
            base = feeds["z_midi"][..., :1]
            return {
                "amplitudes": base,
                "harmonic_distribution": np.repeat(base, 60, axis=-1),
                "noise_magnitudes": np.repeat(base, 65, axis=-1),
            }
        raise AssertionError(self.name)


class _DeterministicComponent:
    def __init__(
        self,
        name: str,
        valid_frames: list[np.ndarray],
        open_counts: dict[tuple[str, int], int],
        batch_size: int,
    ) -> None:
        self.name = name
        self.valid_frames = valid_frames
        self.open_counts = open_counts
        self.batch_size = batch_size

    def open(self, _device_id: int):
        key = (self.name, self.batch_size)
        self.open_counts[key] = self.open_counts.get(key, 0) + 1
        return nullcontext(_DeterministicRunner(self.name, self.valid_frames))


class _DeterministicBundle:
    expression_block = 2
    synthesis_block = 2
    timbre_max_frames = 8
    voice_batch_sizes = (1, 2)

    def __init__(self) -> None:
        self.valid_frames: list[np.ndarray] = []
        self.open_counts: dict[tuple[str, int], int] = {}

    def component(self, name: str, voice_batch_size: int = 1):
        return _DeterministicComponent(
            name,
            self.valid_frames,
            self.open_counts,
            voice_batch_size,
        )

    def runtime_session(self, _device_id: int):
        return nullcontext()


class BatchedStatefulInferenceTest(unittest.TestCase):
    def test_context_padding_is_prepared_once_per_voice(self) -> None:
        bundle = _DeterministicBundle()
        inference = BatchedStatefulMidiDdspInference(bundle, 2)
        values = [
            np.ones((5, 320), dtype=np.float32),
            np.ones((3, 320), dtype=np.float32),
        ]
        component = "midi_ddsp_v2_synthesis_context_forward_frames64"
        with mock.patch.object(
            stateful_module, "_pad_end", wraps=stateful_module._pad_end
        ) as pad_end:
            inference._context_pass(
                component,
                values,
                256,
                reverse=False,
                input_name="z_midi",
            )
        self.assertEqual(pad_end.call_count, len(values))

        with mock.patch.object(
            stateful_module, "_pad_start", wraps=stateful_module._pad_start
        ) as pad_start:
            inference._context_pass(
                component,
                values,
                256,
                reverse=True,
                input_name="z_midi",
            )
        self.assertEqual(pad_start.call_count, len(values))

    def test_batch_matches_independent_voices_and_masks_lengths(self) -> None:
        bundle = _DeterministicBundle()
        tokens_all = [
            [MidiToken(60, 2), MidiToken(62, 1)],
            [MidiToken(67, 1)],
        ]
        seeds = [7, 19]
        batched = BatchedStatefulMidiDdspInference(bundle, 2).run(
            tokens_all,
            build_frame_features,
            [0, 0],
            seeds,
        )
        batch_open_counts = dict(bundle.open_counts)
        singles = [
            StatefulMidiDdspInference(bundle, seed=seed).run(
                tokens, build_frame_features, 0
            )
            for tokens, seed in zip(tokens_all, seeds)
        ]
        for batch_result, single_result in zip(batched, singles):
            for field in (
                "controls",
                "f0_hz",
                "f0_midi",
                "amplitudes",
                "harmonic_distribution",
                "noise_magnitudes",
                "sampled_bins",
            ):
                np.testing.assert_array_equal(
                    getattr(batch_result, field), getattr(single_result, field)
                )
        np.testing.assert_array_equal(bundle.valid_frames[0], [3, 1])
        self.assertEqual(len(batch_open_counts), len(STATEFUL_COMPONENTS))
        self.assertTrue(all(value == 1 for value in batch_open_counts.values()))


class _RuntimeApi:
    def __init__(self) -> None:
        self.set_count = 0
        self.reset_count = 0

    def set_device(self, _device_id: int) -> int:
        self.set_count += 1
        return 0

    def reset_device(self, _device_id: int) -> int:
        self.reset_count += 1
        return 0


class _RuntimeAcl:
    def __init__(self) -> None:
        self.rt = _RuntimeApi()
        self.init_count = 0
        self.finalize_count = 0

    def init(self) -> int:
        self.init_count += 1
        return 0

    def finalize(self) -> int:
        self.finalize_count += 1
        return 0


class AclRuntimeSessionTest(unittest.TestCase):
    def test_nested_component_runtime_does_not_reinitialize_ge(self) -> None:
        acl = _RuntimeAcl()
        with MidiDdspAclRuntime(acl_module=acl):
            with MidiDdspAclRuntime(acl_module=acl):
                self.assertEqual(acl.init_count, 1)
                self.assertEqual(acl.finalize_count, 0)
            self.assertEqual(acl.finalize_count, 0)
        self.assertEqual(acl.rt.set_count, 1)
        self.assertEqual(acl.rt.reset_count, 1)
        self.assertEqual(acl.finalize_count, 1)


class MidiAnalysisTest(unittest.TestCase):
    def test_generated_fixture_is_monophonic_and_complete(self) -> None:
        path = Path(__file__).resolve().parents[1] / "midi/ddsp-test.mid"
        analysis = analyze_midi(path)
        voices = split_midi_voices(analysis)
        self.assertEqual(analysis.mode, "monophonic")
        self.assertTrue(analysis.supported)
        self.assertEqual(analysis.max_polyphony, 1)
        self.assertEqual(analysis.voice_count, 1)
        self.assertEqual(sum(len(voice.notes) for voice in voices), analysis.note_count)
        self.assertTrue(
            all(
                all(
                    left.end <= right.start + 1e-9
                    for left, right in zip(voice.notes, voice.notes[1:])
                )
                for voice in voices
            )
        )

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
        self.assertEqual(analysis.voice_count, 2)
        self.assertEqual([track.instrument_id for track in analysis.tracks], [0, 4])

    def test_chord_in_one_track_is_partitioned_without_losing_notes(self) -> None:
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
        voices = split_midi_voices(analysis)
        self.assertTrue(analysis.supported)
        self.assertEqual(analysis.mode, "polyphonic")
        self.assertEqual(analysis.voice_count, 2)
        self.assertEqual(sum(len(voice.notes) for voice in voices), 2)
        self.assertEqual([[note.pitch for note in voice.notes] for voice in voices], [[64], [60]])

    def test_voice_partition_reuses_nearest_available_pitch(self) -> None:
        midi = mido.MidiFile(type=0, ticks_per_beat=480)
        track = mido.MidiTrack()
        for pitch, delta in ((60, 0), (72, 0)):
            track.append(mido.Message("note_on", note=pitch, velocity=80, time=delta))
        track.append(mido.Message("note_off", note=60, velocity=0, time=480))
        track.append(mido.Message("note_off", note=72, velocity=0, time=0))
        for pitch, delta in ((71, 0), (61, 0)):
            track.append(mido.Message("note_on", note=pitch, velocity=80, time=delta))
        track.append(mido.Message("note_off", note=71, velocity=0, time=480))
        track.append(mido.Message("note_off", note=61, velocity=0, time=0))
        midi.tracks.append(track)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "voice-leading.mid"
            midi.save(path)
            voices = split_midi_voices(analyze_midi(path))
        self.assertEqual(
            [[note.pitch for note in voice.notes] for voice in voices],
            [[72, 71], [60, 61]],
        )

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
                    "timbre_max_frames": 65536,
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

    def test_schema_two_exposes_static_voice_batch_component_sets(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = self._manifest(root)
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["schema_version"] = 2
            data["voice_batch_sizes"] = [1, 2]
            for name, component in list(data["components"].items()):
                component["logical_name"] = name
                component["voice_batch_size"] = 1
                batch_name = f"{name}_batch2"
                batch_path = root / f"{batch_name}.om"
                batch_path.write_bytes(batch_name.encode("ascii"))
                data["components"][batch_name] = {
                    **component,
                    "logical_name": name,
                    "voice_batch_size": 2,
                    "file": batch_path.name,
                    "sha256": hashlib.sha256(batch_path.read_bytes()).hexdigest(),
                }
            manifest.write_text(json.dumps(data), encoding="utf-8")
            bundle = load_runtime_bundle(manifest)
        self.assertEqual(bundle.voice_batch_sizes, (1, 2))
        self.assertEqual(bundle.select_voice_batch_size(2), 2)
        self.assertEqual(bundle.component(next(iter(STATEFUL_COMPONENTS)), 2).voice_batch_size, 2)

    def test_runtime_rejects_non_origin_precision(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            manifest = self._manifest(root)
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data.update(
                {
                    "schema_version": 3,
                    "precision": "unsupported",
                    "onnx_dtype": "float32",
                    "voice_batch_sizes": [1],
                }
            )
            manifest.write_text(json.dumps(data), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "origin precision"):
                load_runtime_bundle(manifest)


if __name__ == "__main__":
    unittest.main()
