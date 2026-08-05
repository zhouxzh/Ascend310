from __future__ import annotations

import hashlib
import inspect
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
import wave
from unittest import mock

import numpy as np

from piano_ddsp_runtime.acl_model import PianoAclModel, _host_pointer
from piano_ddsp_runtime.audio_output import BoundedAudioOutput, _stereo_to_mono_s16
from piano_ddsp_runtime.bundle import PianoBundle, PianoModelAsset, load_bundle
from piano_ddsp_runtime.engine import LATENCY_PROFILES, RUNTIME_METRIC_CAPACITY, PianoDdspEngine
from piano_ddsp_runtime.harmonic import HarmonicSynthesizer
from piano_ddsp_runtime.midi_state import LiveMidiState, MIN_AUDIBLE_GATE_FRAMES
from piano_ddsp_runtime.metrics import RuntimeMetrics
from piano_ddsp_runtime.noise import NoiseSynthesizer
from piano_ddsp_runtime.reverb import PartitionedConvolver, fdn_impulse_response
from piano_ddsp_runtime.resampler import PianoSincResampler
from piano_ddsp_runtime.scheduler import MidiScheduler
from piano_ddsp_runtime.worker import Worker
from prepare_piano_ddsp_models import (
    ATC_COMPILE_ENVIRONMENT,
    atc_subprocess_environment,
    convert_one,
)
from tools.download_model_release import DEFAULT_PIANO_FILES, parse_sha256s, validate_release
from tools.validate_piano_ddsp_om import load_reference_arrays, validate_reference_provenance


ROOT = Path(__file__).resolve().parents[1]
RELEASE_ROOT = ROOT / "models" / "piano_ddsp" / "model-suite-v1.0.1"


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

    def test_existing_om_without_conversion_evidence_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            onnx = root / "paper.onnx"
            metadata = root / "paper.json"
            bundle = root / "bundle"
            (bundle / "models").mkdir(parents=True)
            onnx.write_bytes(b"onnx")
            metadata.write_text("{}", encoding="utf-8")
            (bundle / "models" / "paper.om").write_bytes(b"stale-om")
            with self.assertRaisesRegex(RuntimeError, "conversion record or raw ATC log"):
                convert_one(
                    "gru_ir_96_64",
                    onnx,
                    metadata,
                    {},
                    bundle,
                    "Ascend310B4",
                    "origin",
                )


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

    def test_prepare_failure_releases_every_allocated_device_buffer(self) -> None:
        acl = SimpleNamespace(
            ACL_FLOAT=0,
            rt=SimpleNamespace(
                malloc=mock.Mock(side_effect=[(101, 0), (102, 0)]),
                free=mock.Mock(return_value=0),
            ),
            mdl=SimpleNamespace(
                get_num_inputs=mock.Mock(return_value=2),
                get_input_name_by_index=mock.Mock(return_value="conditioning"),
                get_input_dims=mock.Mock(return_value=({"dimCount": 1, "dims": [1]}, 0)),
                get_input_size_by_index=mock.Mock(return_value=4),
                get_input_data_type=mock.Mock(return_value=0),
                add_dataset_buffer=mock.Mock(return_value=0),
            ),
            create_data_buffer=mock.Mock(side_effect=["buffer-1", "buffer-2"]),
            destroy_data_buffer=mock.Mock(return_value=0),
        )
        model = object.__new__(PianoAclModel)
        model.acl = acl
        model.model_desc = object()
        model.input_dataset = object()
        model.output_dataset = object()
        model.input_shapes = {"conditioning": (1,)}
        model.output_shapes = {}
        model.input_dtypes = {"conditioning": np.float32}
        model.output_dtypes = {}

        with self.assertRaisesRegex(ValueError, "Unexpected OM inputs"):
            model._prepare(True)
        self.assertEqual(
            acl.destroy_data_buffer.call_args_list,
            [mock.call("buffer-2"), mock.call("buffer-1")],
        )
        self.assertEqual(acl.rt.free.call_args_list, [mock.call(102), mock.call(101)])


class PianoMidiStateTest(unittest.TestCase):
    def test_note_listener_reports_a_short_note_as_two_edges(self) -> None:
        events: list[tuple[int, bool]] = []
        state = LiveMidiState(note_listener=lambda note, on: events.append((note, on)))

        state.note_on("browser", 60, 96)
        state.note_off("browser", 60)
        for _ in range(MIN_AUDIBLE_GATE_FRAMES):
            state.render_frame()

        self.assertEqual(events, [(60, True), (60, False)])

    def test_repeated_note_reuses_slot_and_release_pitch_is_extended(self) -> None:
        state = LiveMidiState()
        self.assertTrue(state.note_on("browser", 60, 96))
        conditioning, _, gate = state.render_frame()
        self.assertEqual(float(conditioning[0, 0, 0]), 60.0)
        self.assertAlmostEqual(float(conditioning[0, 0, 1]), 96 / 127)
        self.assertTrue(gate[0])

        state.note_off("browser", 60)
        gates = [state.render_frame()[2] for _ in range(MIN_AUDIBLE_GATE_FRAMES - 1)]
        self.assertEqual(state.snapshot().slot_notes[0], 60)
        self.assertTrue(all(bool(gate[0]) for gate in gates))
        conditioning, _, gate = state.render_frame()
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
        for _ in range(MIN_AUDIBLE_GATE_FRAMES):
            conditioning, _, gate = state.render_frame()
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


class PianoWorkerTest(unittest.TestCase):
    def test_realtime_edges_skip_full_status_calculation(self) -> None:
        worker = Worker()
        engine = SimpleNamespace(
            note=mock.Mock(),
            control_change=mock.Mock(),
            release_source=mock.Mock(),
            status=mock.Mock(side_effect=AssertionError("status should not be calculated")),
        )
        worker.engine = engine

        self.assertEqual(
            worker.dispatch({"command": "note", "source": "browser", "note": 60, "velocity": 96, "on": True}),
            {"accepted": True},
        )
        self.assertEqual(
            worker.dispatch({"command": "cc", "source": "browser", "controller": 64, "value": 127}),
            {"accepted": True},
        )
        self.assertEqual(
            worker.dispatch({"command": "release_source", "source": "browser"}),
            {"accepted": True},
        )
        engine.status.assert_not_called()


class PianoSchedulerTest(unittest.TestCase):
    def test_same_timestamp_is_stable_and_cancel_removes_old_events(self) -> None:
        state = LiveMidiState()
        scheduler = MidiScheduler(state)
        scheduler.push("file", "note_on", 60, 90, 100)
        scheduler.push("file", "note_off", 60, 0, 100)
        self.assertEqual(scheduler.drain(100), 2)
        scheduler.render_conditions(MIN_AUDIBLE_GATE_FRAMES, 100)
        self.assertEqual(state.snapshot().active_notes, ())
        scheduler.push("file", "note_on", 61, 90, 200)
        scheduler.cancel_source("file")
        scheduler.push("file", "note_on", 62, 90, 300)
        self.assertEqual(scheduler.drain(250), 0)
        self.assertEqual(scheduler.drain(300), 1)
        self.assertEqual(state.snapshot().active_notes, (62,))

    def test_same_frame_tap_keeps_four_audio_frames(self) -> None:
        state = LiveMidiState()
        scheduler = MidiScheduler(state)
        scheduler.push("browser", "note_on", 60, 100, 100)
        scheduler.push("browser", "note_off", 60, 0, 100)

        _, _, gates = scheduler.render_conditions(MIN_AUDIBLE_GATE_FRAMES, 100)

        self.assertTrue(all(bool(frame[0]) for frame in gates))
        _, _, next_gate = scheduler.render_conditions(1, 100 + MIN_AUDIBLE_GATE_FRAMES * scheduler.FRAME_NS)
        self.assertFalse(next_gate[0, 0])


class PianoDspTest(unittest.TestCase):
    def test_onboard_output_downmixes_stereo_without_clipping(self) -> None:
        stereo = np.array([[1.0, -1.0], [1.0, 1.0], [-0.5, -0.5]], dtype=np.float32)
        mono = np.frombuffer(_stereo_to_mono_s16(stereo), dtype="<i2")
        np.testing.assert_array_equal(mono, np.array([0, 32767, -16384], dtype=np.int16))

    def test_metrics_batch_add_is_bounded(self) -> None:
        metrics = RuntimeMetrics(capacity=3)
        metrics.add_many("npu_ms", [1.0, 2.0, 3.0, 4.0])
        self.assertEqual(tuple(metrics.npu_ms), (2.0, 3.0, 4.0))

    def test_metrics_reset_discards_warmup_samples_and_counters(self) -> None:
        metrics = RuntimeMetrics(capacity=3)
        metrics.add("block_ms", 42.0)
        metrics.increment("underruns", 2)
        metrics.reset()
        self.assertEqual(tuple(metrics.block_ms), ())
        self.assertEqual(metrics.underruns, 0)

    def test_metrics_calculates_each_series_percentiles_once(self) -> None:
        metrics = RuntimeMetrics(capacity=8)
        for name in ("npu_ms", "dsp_ms", "block_ms", "write_ms", "midi_to_pcm_ms"):
            metrics.add_many(name, (1.0, 2.0, 3.0, 4.0))

        with mock.patch("piano_ddsp_runtime.metrics.np.quantile", wraps=np.quantile) as quantile:
            snapshot = metrics.snapshot()

        self.assertEqual(quantile.call_count, 5)
        self.assertAlmostEqual(float(snapshot["npu_p95_ms"]), 3.85)
        self.assertAlmostEqual(float(snapshot["estimated_total_latency_ms"]), 3.85)

    def test_balanced_profile_prefills_without_a_deep_queue(self) -> None:
        profile = LATENCY_PROFILES["balanced"]
        self.assertEqual(profile["prebuffer"], 2)
        self.assertEqual(profile["capacity"], 4)

    def test_audio_latency_probe_runs_on_telemetry_worker(self) -> None:
        output = BoundedAudioOutput(48_000, 1_536, 3, 2, 20.0, RuntimeMetrics())
        output.stop_event = mock.Mock()
        output.stop_event.wait.side_effect = [False, True]
        output.refresh_latencies = mock.Mock()

        output._latency_loop()

        output.refresh_latencies.assert_called_once_with()
        self.assertEqual(output.latency_worker.name, "piano-audio-telemetry")

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
    metadata = {
        "model_id": "gru_ir_96_64",
        "display_name": "GRU IR 96/64",
        "n_harmonics": 96,
        "n_noise_bands": 64,
        "n_substrings": 1,
        "reverb_output": "reverb_ir",
        "reverb_wet_gain": 0.25,
        "reverb_ir_postprocess": {"type": "exponential_decay"},
        "outputs": {
            "amplitudes": [1, 1, 16, 1],
            "harmonic_distribution": [1, 1, 16, 96],
            "inharmonicity": [1, 1, 16, 1],
            "f0_hz": [1, 1, 16, 1],
            "noise_magnitudes": [1, 1, 16, 64],
            "reverb_ir": [1, 24_000],
            "next_context_state": [1, 1, 64],
            "next_monophonic_state": [1, 16, 192],
        },
        "piano_model_index_to_maestro_year": [
            2004,
            2006,
            2008,
            2009,
            2011,
            2013,
            2014,
            2015,
            2017,
            2018,
        ],
    }
    om = folder / "paper.om"
    meta = folder / "paper.json"
    manifest = folder / "manifest.json"
    om.write_bytes(b"om")
    meta.write_text(json.dumps(metadata), encoding="utf-8")
    asset = PianoModelAsset(
        "gru_ir_96_64",
        "GRU IR 96/64",
        om,
        meta,
        metadata,
        hashlib.sha256(b"om").hexdigest(),
        validation_passed=True,
    )
    return PianoBundle(
        "fixture",
        "model-suite-v1.0.1",
        "FP32",
        "Ascend310B4",
        manifest,
        {"gru_ir_96_64": asset},
        False,
    )


class PianoEngineTest(unittest.TestCase):
    def test_output_gain_defaults_to_zero_db(self) -> None:
        default = inspect.signature(PianoDdspEngine).parameters["output_gain_db"].default
        self.assertEqual(default, 0.0)

    def test_fake_model_render_is_deterministic_and_panic_clears_all_state(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            bundle = fixture_bundle(Path(folder))
            engines = [
                PianoDdspEngine(bundle, model_factory=FakePianoModel, seed=11)
                for _ in range(2)
            ]
            blocks = []
            for engine in engines:
                engine._load_model(bundle.models["gru_ir_96_64"])
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

    def test_audio_priming_warms_then_prefills_from_reset_state(self) -> None:
        class AudioQueue:
            prebuffer = 2
            capacity = 4

            def __init__(self) -> None:
                self.blocks: list[np.ndarray] = []

            def submit(self, block: np.ndarray) -> bool:
                self.blocks.append(block)
                return True

        with tempfile.TemporaryDirectory() as folder:
            bundle = fixture_bundle(Path(folder))
            engine = PianoDdspEngine(bundle, model_factory=FakePianoModel)
            engine._load_model(bundle.models["gru_ir_96_64"])
            audio = AudioQueue()

            engine._prime_audio(audio)  # type: ignore[arg-type]

            self.assertEqual(len(audio.blocks), 4)
            self.assertTrue(all(block.shape == (1_536, 2) for block in audio.blocks))
            self.assertEqual(engine.context_state[0, 0, 0], 4 * engine.block_frames)
            self.assertEqual(engine.metrics.rendered_blocks, 0)
            self.assertEqual(tuple(engine.metrics.block_ms), ())
            engine.model.close()

    def test_rejected_audio_block_does_not_add_a_second_block_delay(self) -> None:
        engine = object.__new__(PianoDdspEngine)
        engine._stop = SimpleNamespace(is_set=mock.Mock(side_effect=[False, True]))
        engine._state = "running"
        engine._error = None
        engine.audio = SimpleNamespace(error=None, submit=mock.Mock(return_value=False))
        engine.render_block = mock.Mock(return_value=np.zeros((1_536, 2), dtype=np.float32))

        with mock.patch("piano_ddsp_runtime.engine.time.sleep") as sleep:
            engine._render_loop()

        sleep.assert_not_called()
        engine.audio.submit.assert_called_once()

    def test_runtime_metrics_keep_a_short_realtime_window(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            engine = PianoDdspEngine(fixture_bundle(Path(folder)), model_factory=FakePianoModel)
        self.assertEqual(engine.metrics.block_ms.maxlen, RUNTIME_METRIC_CAPACITY)

    def test_all_notes_off_uses_global_fade(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            bundle = fixture_bundle(Path(folder))
            engine = PianoDdspEngine(bundle, model_factory=FakePianoModel)
            engine._load_model(bundle.models["gru_ir_96_64"])
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
            engine._load_model(bundle.models["gru_ir_96_64"])
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
                        "model_id": "gru_ir_96_64",
                        "frames": 10_000,
                        "npz": reference.name,
                        "npz_sha256": digest,
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(validate_reference_provenance(reference, "gru_ir_96_64"), digest)
            with self.assertRaisesRegex(ValueError, "does not match"):
                validate_reference_provenance(reference, "film_fdn_128_96")

    def test_v2_reference_uses_shared_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            model_root = root / "gru_ir_96_64"
            model_root.mkdir()
            inputs = root / "inputs-10000.npz"
            reference = model_root / "reference-10000.npz"
            np.savez(
                inputs,
                conditioning=np.zeros((2, 16, 2), dtype=np.float32),
                pedal=np.zeros((2, 4), dtype=np.float32),
                piano_model=np.zeros((1,), dtype=np.int32),
                extended_pitch=np.zeros((2, 16, 1), dtype=np.float32),
            )
            np.savez(
                reference,
                amplitudes=np.zeros((2, 16, 1), dtype=np.float32),
                harmonic_distribution=np.zeros((2, 16, 96), dtype=np.float32),
                inharmonicity=np.zeros((2, 16, 1), dtype=np.float32),
                f0_hz=np.zeros((2, 16, 1), dtype=np.float32),
                noise_magnitudes=np.zeros((2, 16, 64), dtype=np.float32),
                next_context_state=np.zeros((2, 64), dtype=np.float32),
                next_monophonic_state=np.zeros((2, 16, 192), dtype=np.float32),
            )
            (model_root / "report.json").write_text(
                json.dumps(
                    {
                        "schema": "piano-ddsp-onnx-reference/v2",
                        "release": "model-suite-v1.0.1",
                        "source_hf_commit": "c41911aa7de454aeacf0b3edbb2d06a0801fb3ff",
                        "model_id": "gru_ir_96_64",
                        "frames": 10_000,
                        "inputs": inputs.name,
                        "inputs_sha256": hashlib.sha256(inputs.read_bytes()).hexdigest(),
                        "npz": reference.name,
                        "npz_sha256": hashlib.sha256(reference.read_bytes()).hexdigest(),
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(
                validate_reference_provenance(reference, "gru_ir_96_64"),
                hashlib.sha256(reference.read_bytes()).hexdigest(),
            )
            frames, arrays = load_reference_arrays(reference, 2)
            self.assertEqual(frames, 2)
            self.assertEqual(arrays["next_context_state"].shape, (2, 64))

    def test_downloaded_release_is_complete_and_source_pt_files_are_not_required(self) -> None:
        if not (RELEASE_ROOT / "SHA256SUMS").is_file():
            self.skipTest("ignored Piano-DDSP release assets are not installed")
        hashes = parse_sha256s((RELEASE_ROOT / "SHA256SUMS").read_text(encoding="utf-8"))
        validate_release(RELEASE_ROOT, hashes, DEFAULT_PIANO_FILES)
        self.assertFalse(any(path.suffix == ".pt" for path in map(Path, DEFAULT_PIANO_FILES)))

    def test_bundle_rejects_hash_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            metadata = fixture_bundle(root).models["gru_ir_96_64"].metadata
            om = root / "model.om"
            meta = root / "model.json"
            om.write_bytes(b"fixture")
            meta.write_text(json.dumps(metadata), encoding="utf-8")
            (root / "manifest.json").write_text(
                json.dumps(
                    {
                        "schema": "piano-ddsp-om-bundle/v1",
                        "id": "fixture",
                        "release": "model-suite-v1.0.1",
                        "precision": "FP32",
                        "precision_mode_v2": "origin",
                        "soc_version": "Ascend310B4",
                        "models": {
                            "gru_ir_96_64": {
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
