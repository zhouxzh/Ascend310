from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import wave
from unittest import mock

import numpy as np

from piano_ddsp_runtime.acl_model import PianoAclModel, _host_pointer
from piano_ddsp_runtime.audio_output import _stereo_to_mono_s16
from piano_ddsp_runtime.bundle import PianoBundle, PianoModelAsset, load_bundle
from piano_ddsp_runtime.engine import PianoDdspEngine
from piano_ddsp_runtime.harmonic import HarmonicSynthesizer
from piano_ddsp_runtime.midi_state import LiveMidiState
from piano_ddsp_runtime.metrics import RuntimeMetrics
from piano_ddsp_runtime.noise import NoiseSynthesizer
from piano_ddsp_runtime.reverb import PartitionedConvolver, fdn_impulse_response
from piano_ddsp_runtime.resampler import PianoSincResampler
from piano_ddsp_runtime.scheduler import MidiScheduler
from prepare_piano_ddsp_models import ATC_COMPILE_ENVIRONMENT, atc_subprocess_environment
from tools.download_piano_ddsp_onnx import REQUIRED_FILES, parse_sha256s, validate_release
from tools.validate_piano_ddsp_om import validate_reference_provenance


ROOT = Path(__file__).resolve().parents[1]
RELEASE_ROOT = ROOT / "models" / "piano_ddsp" / "model-suite-v1.0.0"


class PianoAtcPolicyTest(unittest.TestCase):
    def test_single_thread_policy_overrides_inherited_parallel_settings(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {"MULTI_THREAD_COMPILE": "1", "TE_PARALLEL_COMPILER": "8"},
        ):
            environment = atc_subprocess_environment()

        self.assertEqual(
            ATC_COMPILE_ENVIRONMENT,
            {"MULTI_THREAD_COMPILE": "0", "TE_PARALLEL_COMPILER": "1"},
        )
        self.assertEqual(environment["MULTI_THREAD_COMPILE"], "0")
        self.assertEqual(environment["TE_PARALLEL_COMPILER"], "1")


class PianoAclInputTest(unittest.TestCase):
    def test_acl_runtime_is_released_after_last_model(self) -> None:
        acl = SimpleNamespace(
            init=mock.Mock(return_value=0),
            finalize=mock.Mock(return_value=0),
            rt=SimpleNamespace(
                set_device=mock.Mock(return_value=0),
                reset_device=mock.Mock(return_value=0),
            ),
        )
        models = [object.__new__(PianoAclModel) for _ in range(2)]
        for model in models:
            model.acl = acl
            model.device_id = 0
            model._runtime_key = None
            model._initialized = False
            model._device_set = False
            model._acquire_runtime()
        self.assertEqual(acl.init.call_count, 1)
        self.assertEqual(acl.rt.set_device.call_count, 1)

        errors: list[BaseException] = []
        models[0]._release_runtime(errors)
        self.assertEqual(acl.rt.reset_device.call_count, 0)
        models[1]._release_runtime(errors)
        self.assertEqual(errors, [])
        self.assertEqual(acl.rt.reset_device.call_count, 1)
        self.assertEqual(acl.finalize.call_count, 1)

    def test_numpy_pointer_keeps_contiguous_array_alive(self) -> None:
        array = np.arange(8, dtype=np.float32)
        pointer, owner = _host_pointer(array)
        self.assertEqual(pointer, array.ctypes.data)
        self.assertIs(owner, array)

    def test_numpy_pointer_rejects_noncontiguous_arrays(self) -> None:
        with self.assertRaisesRegex(ValueError, "C-contiguous"):
            _host_pointer(np.arange(8, dtype=np.float32)[::2])


class PianoMidiStateTest(unittest.TestCase):
    def test_repeated_note_reuses_slot_and_release_pitch_is_extended(self) -> None:
        state = LiveMidiState()
        self.assertTrue(state.note_on("browser", 60, 96))
        conditioning, _, gate = state.render_frame()
        self.assertEqual(float(conditioning[0, 0, 0]), 60.0)
        self.assertAlmostEqual(float(conditioning[0, 0, 1]), 96 / 127)
        self.assertTrue(gate[0])

        state.note_off("browser", 60)
        conditioning, _, gate = state.render_frame()
        self.assertEqual(float(conditioning[0, 0, 0]), 0.0)
        self.assertEqual(state.snapshot().slot_notes[0], 60)
        self.assertFalse(gate[0])

        state.note_on("browser", 60, 64)
        conditioning, _, gate = state.render_frame()
        self.assertEqual(float(conditioning[0, 0, 0]), 60.0)
        self.assertEqual(state.snapshot().slot_notes.count(60), 1)
        self.assertTrue(gate[0])

    def test_sustain_and_source_release_do_not_drop_other_source(self) -> None:
        state = LiveMidiState()
        state.note_on("hardware", 64, 100)
        state.note_on("browser", 64, 80)
        state.control_change("hardware", 64, 127)
        state.note_off("browser", 64)
        state.note_off("hardware", 64)
        conditioning, pedal, gate = state.render_frame()
        self.assertEqual(float(conditioning[0, 0, 0]), 64.0)
        self.assertTrue(gate[0])
        self.assertGreater(float(pedal[0, 0]), 0.99)
        state.release_source("browser")
        self.assertTrue(state.snapshot().sustain)
        state.control_change("hardware", 64, 0)
        conditioning, _, gate = state.render_frame()
        self.assertEqual(float(conditioning[0, 0, 0]), 0.0)
        self.assertFalse(gate[0])

    def test_voice_stealing_prefers_oldest_released_then_oldest_active(self) -> None:
        state = LiveMidiState()
        for pitch in range(40, 56):
            state.note_on("hardware", pitch, 100)
        state.note_off("hardware", 47)
        state.note_on("hardware", 80, 100)
        snapshot = state.snapshot()
        self.assertEqual(snapshot.slot_notes[7], 80)
        self.assertEqual(snapshot.voice_steals, 1)

        active = LiveMidiState()
        for pitch in range(40, 56):
            active.note_on("hardware", pitch, 100)
        active.note_on("hardware", 80, 100)
        self.assertEqual(active.snapshot().slot_notes[0], 80)


class PianoSchedulerTest(unittest.TestCase):
    def test_same_timestamp_is_stable_and_cancel_removes_old_events(self) -> None:
        state = LiveMidiState()
        scheduler = MidiScheduler(state)
        scheduler.push("file", "note_on", 60, 90, 100)
        scheduler.push("file", "note_off", 60, 0, 100)
        self.assertEqual(scheduler.drain(100), 2)
        self.assertEqual(state.snapshot().active_notes, ())
        scheduler.push("file", "note_on", 61, 90, 200)
        scheduler.cancel_source("file")
        scheduler.push("file", "note_on", 62, 90, 300)
        self.assertEqual(scheduler.drain(250), 0)
        self.assertEqual(scheduler.drain(300), 1)
        self.assertEqual(state.snapshot().active_notes, (62,))


class PianoDspTest(unittest.TestCase):
    def test_onboard_output_downmixes_stereo_without_clipping(self) -> None:
        stereo = np.array([[1.0, -1.0], [1.0, 1.0], [-0.5, -0.5]], dtype=np.float32)
        mono = np.frombuffer(_stereo_to_mono_s16(stereo), dtype="<i2")
        np.testing.assert_array_equal(mono, np.array([0, 32767, -16384], dtype=np.int16))

    def test_metrics_batch_add_is_bounded(self) -> None:
        metrics = RuntimeMetrics(capacity=3)
        metrics.add_many("npu_ms", [1.0, 2.0, 3.0, 4.0])
        self.assertEqual(tuple(metrics.npu_ms), (2.0, 3.0, 4.0))

    def test_voice_release_envelope_matches_scalar_sample_updates(self) -> None:
        engine = object.__new__(PianoDdspEngine)
        engine.latency_profile = "balanced"
        engine.voice_gain = np.linspace(0.0, 1.0, 16, dtype=np.float32)
        gates = np.zeros((8, 16), dtype=bool)
        gates[0, 0] = True
        gates[3:5, 7] = True
        initial = engine.voice_gain.copy()

        expected = np.empty((16, 8 * 64), dtype=np.float32)
        current = initial.copy()
        step = np.float32(1.0 / round(0.060 * 16_000))
        expanded = np.repeat(gates, 64, axis=0)
        for voice in range(16):
            for sample in range(expanded.shape[0]):
                current[voice] = (
                    1.0
                    if expanded[sample, voice]
                    else max(0.0, current[voice] - step)
                )
                expected[voice, sample] = current[voice]

        actual = engine._voice_envelopes(gates)
        np.testing.assert_allclose(actual, expected, rtol=0.0, atol=1e-5)
        np.testing.assert_allclose(engine.voice_gain, current, rtol=0.0, atol=1e-5)

    def test_piano_sinc_resampler_is_stateful_and_frequency_accurate(self) -> None:
        source_rate, target_rate, block_size = 16_000, 48_000, 512
        frequency = 1_000.0
        source = np.sin(
            2.0 * np.pi * frequency * np.arange(block_size * 20) / source_rate
        ).astype(np.float32)
        resampler = PianoSincResampler(source_rate, target_rate)
        actual = np.concatenate(
            [
                resampler.process(source[index : index + block_size])
                for index in range(0, source.size, block_size)
            ]
        )
        output_time = np.arange(actual.size) / target_rate
        expected = np.sin(
            2.0
            * np.pi
            * frequency
            * (output_time - resampler.algorithmic_latency_seconds)
        )
        settled = round(0.050 * target_rate)
        signal = expected[settled:]
        error = signal - actual[settled:]
        snr = 10.0 * np.log10(np.sum(signal**2) / np.sum(error**2))
        self.assertGreater(snr, 60.0)
        self.assertAlmostEqual(resampler.algorithmic_latency_seconds * 1000.0, 2.0)

    def test_harmonic_state_is_chunk_equivalent(self) -> None:
        rng = np.random.RandomState(4)
        controls = {
            "amplitudes": rng.normal(-2, 0.2, (6, 2, 1)).astype(np.float32),
            "distribution": rng.normal(0, 0.2, (6, 2, 8)).astype(np.float32),
            "inharmonicity": np.full((6, 2, 1), 1e-4, dtype=np.float32),
            "f0": np.full((6, 2, 1), 220.0, dtype=np.float32),
        }
        whole = HarmonicSynthesizer(2, 8)
        expected = whole.render(
            controls["amplitudes"], controls["distribution"], controls["inharmonicity"], controls["f0"]
        )
        chunked = HarmonicSynthesizer(2, 8)
        actual = np.concatenate(
            [
                chunked.render(
                    controls["amplitudes"][part],
                    controls["distribution"][part],
                    controls["inharmonicity"][part],
                    controls["f0"][part],
                )
                for part in (slice(0, 2), slice(2, 6))
            ]
        )
        np.testing.assert_allclose(actual, expected, rtol=1e-6, atol=8e-6)

    def test_noise_state_is_deterministic_and_chunk_equivalent(self) -> None:
        magnitudes = np.linspace(-5, 1, 4 * 2 * 8, dtype=np.float32).reshape(4, 2, 8)
        expected = NoiseSynthesizer(2, 8, seed=7).render(magnitudes)
        chunked = NoiseSynthesizer(2, 8, seed=7)
        actual = np.concatenate([chunked.render(magnitudes[:1]), chunked.render(magnitudes[1:])])
        np.testing.assert_allclose(actual, expected, rtol=1e-5, atol=2e-6)
        reset = NoiseSynthesizer(2, 8, seed=7)
        np.testing.assert_array_equal(reset.render(magnitudes), expected)

    def test_partitioned_reverb_matches_linear_convolution_across_blocks(self) -> None:
        impulse = np.asarray([0.0, 0.5, -0.25, 0.1, 0.05, -0.02], dtype=np.float32)
        audio = np.linspace(-1, 1, 24, dtype=np.float32)
        convolver = PartitionedConvolver(impulse, block_size=4)
        actual = np.concatenate(
            [convolver.process(audio[index : index + 4]) for index in range(0, audio.size, 4)]
        )
        expected = np.convolve(audio, impulse)[: audio.size]
        np.testing.assert_allclose(actual, expected, rtol=1e-6, atol=1e-6)
        convolver.reset()
        np.testing.assert_array_equal(convolver.history, 0.0)
        np.testing.assert_array_equal(convolver.overlap, 0.0)

    def test_fdn_is_bounded_and_dynamic_wet_is_not_static_metadata(self) -> None:
        impulse, wet = fdn_impulse_response(np.zeros(9, dtype=np.float32))
        self.assertEqual(impulse.shape, (24_000,))
        self.assertAlmostEqual(float(np.max(np.abs(impulse))), 1.0, places=6)
        self.assertAlmostEqual(wet, 0.4, places=6)


class FakePianoModel:
    def __init__(self, _path: Path, metadata: dict[str, object], _device_id: int) -> None:
        self.metadata = metadata
        self.closed = False

    def infer(self, values: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        pitch = values["extended_pitch"]
        f0 = np.where(
            pitch > 0,
            440.0 * np.power(2.0, (pitch - 69.0) / 12.0),
            0.0,
        ).astype(np.float32)
        outputs = {
            "amplitudes": np.full((1, 1, 16, 1), -4.0, dtype=np.float32),
            "harmonic_distribution": np.full((1, 1, 16, 96), -4.0, dtype=np.float32),
            "inharmonicity": np.full((1, 1, 16, 1), 1e-4, dtype=np.float32),
            "f0_hz": f0,
            "noise_magnitudes": np.full((1, 1, 16, 64), -30.0, dtype=np.float32),
            "reverb_ir": np.zeros((1, 24_000), dtype=np.float32),
            "next_context_state": values["context_state"] + 1,
            "next_monophonic_state": values["monophonic_state"] + 1,
        }
        return outputs

    def close(self, **_kwargs: object) -> None:
        self.closed = True


def fixture_bundle(folder: Path) -> PianoBundle:
    metadata = json.loads((RELEASE_ROOT / "ddsp_piano_paper_ir.json").read_text(encoding="utf-8"))
    om = folder / "paper.om"
    meta = folder / "paper.json"
    manifest = folder / "manifest.json"
    om.write_bytes(b"om")
    meta.write_text(json.dumps(metadata), encoding="utf-8")
    asset = PianoModelAsset(
        "paper_ir",
        "Paper IR",
        om,
        meta,
        metadata,
        hashlib.sha256(b"om").hexdigest(),
        validation_passed=True,
    )
    return PianoBundle("fixture", "model-suite-v1.0.0", "FP32", "Ascend310B4", manifest, {"paper_ir": asset}, False)


class PianoEngineTest(unittest.TestCase):
    def test_fake_model_render_is_deterministic_and_panic_clears_all_state(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            bundle = fixture_bundle(Path(folder))
            engines = [
                PianoDdspEngine(bundle, model_factory=FakePianoModel, seed=11)
                for _ in range(2)
            ]
            blocks = []
            for engine in engines:
                engine._load_model(bundle.models["paper_ir"])
                engine.note("browser", 60, 100, True)
                blocks.append(engine.render_block(10**20))
                self.assertEqual(engine.context_state[0, 0, 0], engine.block_frames)
                engine.panic()
                self.assertEqual(engine.midi.snapshot().active_notes, ())
                self.assertFalse(np.any(engine.context_state))
                self.assertFalse(np.any(engine.extended_pitch))
                self.assertFalse(np.any(engine.reverb.convolver.history))
                engine.model.close()
            np.testing.assert_allclose(blocks[0], blocks[1], rtol=1e-6, atol=1e-6)

    def test_all_notes_off_uses_global_fade(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            bundle = fixture_bundle(Path(folder))
            engine = PianoDdspEngine(bundle, model_factory=FakePianoModel)
            engine._load_model(bundle.models["paper_ir"])
            engine.note("hardware", 60, 100, True)
            engine.render_block(10**20)
            engine.control_change("hardware", 123, 0)
            self.assertEqual(engine._fade_total, 1_920)
            self.assertEqual(engine.midi.snapshot().active_notes, ())
            engine.model.close()

    def test_recording_receives_blocks_after_audio_write(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bundle = fixture_bundle(root)
            engine = PianoDdspEngine(
                bundle, model_factory=FakePianoModel, recorder_root=root / "recordings"
            )
            engine._load_model(bundle.models["paper_ir"])
            path = Path(engine.start_recording("played"))
            block = np.full((96, 2), 0.25, dtype=np.float32)
            engine._on_played(block)
            engine.stop_recording()
            with wave.open(str(path), "rb") as recording:
                self.assertEqual(recording.getnchannels(), 2)
                self.assertEqual(recording.getframerate(), 48_000)
                self.assertEqual(recording.getnframes(), 96)
            engine.model.close()


class PianoBundleAndDownloadTest(unittest.TestCase):
    def test_reference_provenance_rejects_the_wrong_model(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            reference = root / "reference-10000.npz"
            reference.write_bytes(b"reference")
            digest = hashlib.sha256(reference.read_bytes()).hexdigest()
            (root / "report.json").write_text(
                json.dumps(
                    {
                        "schema": "piano-ddsp-reference/v1",
                        "model_id": "paper_ir",
                        "frames": 10_000,
                        "npz": reference.name,
                        "npz_sha256": digest,
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(validate_reference_provenance(reference, "paper_ir"), digest)
            with self.assertRaisesRegex(ValueError, "does not match"):
                validate_reference_provenance(reference, "film_fdn")

    def test_downloaded_release_is_complete_and_pt_files_are_excluded(self) -> None:
        hashes = parse_sha256s((RELEASE_ROOT / "SHA256SUMS").read_text(encoding="utf-8"))
        validate_release(RELEASE_ROOT, hashes, REQUIRED_FILES)
        self.assertFalse(any(path.suffix == ".pt" for path in RELEASE_ROOT.rglob("*")))

    def test_bundle_rejects_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            metadata = json.loads(
                (RELEASE_ROOT / "ddsp_piano_paper_ir.json").read_text(encoding="utf-8")
            )
            om = root / "model.om"
            meta = root / "model.json"
            om.write_bytes(b"fixture")
            meta.write_text(json.dumps(metadata), encoding="utf-8")
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema": "piano-ddsp-om-bundle/v1",
                        "id": "fixture",
                        "release": "model-suite-v1.0.0",
                        "precision": "FP32",
                        "precision_mode_v2": "origin",
                        "soc_version": "Ascend310B4",
                        "models": {
                            "paper_ir": {
                                "om": om.name,
                                "om_sha256": "0" * 64,
                                "metadata": meta.name,
                                "metadata_sha256": hashlib.sha256(meta.read_bytes()).hexdigest(),
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                load_bundle(root)


if __name__ == "__main__":
    unittest.main()
