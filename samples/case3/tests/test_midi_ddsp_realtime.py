from __future__ import annotations

from pathlib import Path
import math
import pickle
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import patch

import mido
import numpy as np

from midi_ddsp_realtime import (
    _angular_cumsum,
    _linear_frame_resample,
    _run_isolated_voice_batch,
    _should_read_cache,
    _window_frame_resample,
    _render_stateful_audio,
    FrameFeatures,
    MidiDdspRenderer,
    MidiDdspHarmonicSynthesizer,
    MidiNote,
    MidiToken,
    RenderProgress,
    StreamingFftReverb,
    build_frame_features,
    build_tokens,
    exp_sigmoid,
    parse_midi_details,
    plan_voice_batches,
)
from midi_ddsp_webui.midi_analysis import MidiValidationError
from tools.export_midi_ddsp_reverb import (
    original_inference_decay,
    prepare_impulse_responses,
)


class MidiDdspRealtimeHelpersTest(unittest.TestCase):
    def test_force_render_bypasses_an_existing_cache(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            cache_wav = Path(folder) / "render.wav"
            cache_report = Path(folder) / "render.json"
            cache_wav.write_bytes(b"wav")
            cache_report.write_text("{}", encoding="utf-8")
            self.assertTrue(_should_read_cache(False, cache_wav, cache_report))
            self.assertFalse(_should_read_cache(True, cache_wav, cache_report))

    def test_chunked_harmonic_render_matches_whole_array_reference(self) -> None:
        rng = np.random.default_rng(20260726)
        frame_count = 252
        f0 = rng.uniform(110.0, 880.0, frame_count).astype(np.float32)
        amplitudes = rng.uniform(0.01, 0.5, frame_count).astype(np.float32)
        distribution = rng.uniform(0.0, 1.0, (frame_count, 60)).astype(np.float32)
        harmonic_numbers = np.arange(1, 61, dtype=np.float32)
        frequencies = f0[:, None] * harmonic_numbers[None, :]
        reference_distribution = distribution.copy()
        reference_distribution[frequencies >= 8_000.0] = 0.0
        totals = reference_distribution.sum(axis=1, keepdims=True)
        reference_distribution = np.divide(
            reference_distribution,
            totals,
            out=np.zeros_like(reference_distribution),
            where=totals > 1e-7,
        )
        frequency_envelopes = _linear_frame_resample(frequencies, 64)
        amplitude_envelopes = _window_frame_resample(
            amplitudes[:, None] * reference_distribution, 64
        )
        phases = _angular_cumsum(
            (frequency_envelopes * np.float32(2.0 * np.pi))
            / np.float32(16_000.0)
        )
        reference = np.sum(
            np.sin(phases) * amplitude_envelopes,
            axis=1,
            dtype=np.float32,
        )

        actual = MidiDdspHarmonicSynthesizer().render(
            f0, amplitudes, distribution
        )

        np.testing.assert_array_equal(actual, reference)

    def test_isolated_batch_worker_forwards_progress_and_removes_result(self) -> None:
        class FakeProcess:
            def __init__(self) -> None:
                self.stdout = iter(
                    [
                        'MIDI_DDSP_BATCH_EVENT {"component":"synthesis_timbre_b4",'
                        '"completed":2,"total":3}\n',
                        "worker diagnostic\n",
                    ]
                )

            def wait(self, timeout=None):
                return 0

            def poll(self):
                return 0

        with tempfile.TemporaryDirectory() as folder:
            result_path = Path(folder) / "batch.pkl"
            expected = {
                "parameters": ["voice-0"],
                "component_timings": {},
                "model_load_timings": {},
                "wall_seconds": 1.25,
            }
            with result_path.open("wb") as handle:
                pickle.dump(expected, handle)
            progress = []
            args = SimpleNamespace(
                midi=Path(folder) / "input.mid",
                model_bundle=Path(folder) / "manifest.json",
                instrument_id=2,
                seed=7,
                device_id=0,
            )
            with patch("midi_ddsp_realtime.subprocess.Popen", return_value=FakeProcess()):
                actual = _run_isolated_voice_batch(
                    args,
                    0,
                    1,
                    4,
                    result_path,
                    lambda component, completed, total: progress.append(
                        (component, completed, total)
                    ),
                )

            self.assertEqual(actual, expected)
            self.assertEqual(progress, [("synthesis_timbre_b4", 2, 3)])
            self.assertFalse(result_path.exists())

    def test_isolated_batch_accepts_fsynced_result_after_cann_cleanup_crash(self) -> None:
        class CleanupCrashProcess:
            stdout = iter(())

            @staticmethod
            def wait(timeout=None):
                return -11

            @staticmethod
            def poll():
                return -11

        with tempfile.TemporaryDirectory() as folder:
            result_path = Path(folder) / "batch.pkl"
            expected = {
                "parameters": ["voice-0"],
                "component_timings": {"timbre": [1.0]},
                "model_load_timings": {"timbre": [2.0]},
                "wall_seconds": 3.0,
            }
            with result_path.open("wb") as handle:
                pickle.dump(expected, handle)
            args = SimpleNamespace(
                midi=Path(folder) / "input.mid",
                model_bundle=Path(folder) / "manifest.json",
                instrument_id=0,
                seed=7,
                device_id=0,
            )
            with patch(
                "midi_ddsp_realtime.subprocess.Popen",
                return_value=CleanupCrashProcess(),
            ):
                actual = _run_isolated_voice_batch(
                    args,
                    0,
                    1,
                    4,
                    result_path,
                    lambda *_args: None,
                )
        self.assertEqual(actual, expected)

    def test_stateful_dsp_honors_render_cancellation(self) -> None:
        parameters = SimpleNamespace(
            f0_hz=np.full((2, 1), 440.0, dtype=np.float32),
            amplitudes=np.zeros((2, 1), dtype=np.float32),
            harmonic_distribution=np.zeros((2, 60), dtype=np.float32),
            noise_magnitudes=np.zeros((2, 65), dtype=np.float32),
        )
        cancelled = threading.Event()
        cancelled.set()
        with self.assertRaisesRegex(InterruptedError, "cancelled"):
            _render_stateful_audio(
                parameters, None, seed=7, cancel_event=cancelled
            )

    def test_voice_batch_planning_uses_smallest_fitting_static_batch(self) -> None:
        self.assertEqual(plan_voice_batches(7, (1, 2, 4, 8)), [(0, 7, 8)])
        self.assertEqual(
            plan_voice_batches(10, (1, 2, 4, 8)),
            [(0, 8, 8), (8, 10, 2)],
        )
        self.assertEqual(
            plan_voice_batches(3, (1,)),
            [(0, 1, 1), (1, 2, 1), (2, 3, 1)],
        )
        self.assertEqual(
            plan_voice_batches(7, (1, 2, 4, 8), requested_batch_size=4),
            [(0, 4, 4), (4, 7, 4)],
        )

    def test_render_progress_never_moves_backwards(self) -> None:
        with patch("midi_ddsp_realtime.emit_web_event") as emit:
            progress = RenderProgress(enabled=True, playback_expected=False)
            progress.update("pitch_context", 0.9, force=True)
            progress.update("expression", 0.1, force=True)
            progress.update("writing_cache", 1.0, force=True)
            progress.close()
        values = [
            call.kwargs["overall_progress"]
            for call in emit.call_args_list
            if len(call.args) >= 2 and call.args[1] == "progress"
        ]
        self.assertGreaterEqual(len(values), 3)
        self.assertEqual(values, sorted(values))
        self.assertEqual(values[-1], 1.0)

    def test_additional_reverb_tail_is_not_sent_through_the_model(self) -> None:
        parameters = SimpleNamespace(
            f0_hz=np.zeros((2, 1), dtype=np.float32),
            amplitudes=np.full((2, 1), -30.0, dtype=np.float32),
            harmonic_distribution=np.full((2, 60), -30.0, dtype=np.float32),
            noise_magnitudes=np.full((2, 65), -30.0, dtype=np.float32),
        )
        audio, _metrics = _render_stateful_audio(
            parameters, None, seed=7, tail_samples=32
        )
        self.assertEqual(audio.size, 2 * 64 + 32)
        np.testing.assert_array_equal(audio[-32:], np.zeros(32, dtype=np.float32))

    def test_angular_cumsum_preserves_phase_across_chunk_boundaries(self) -> None:
        increments = np.tile(
            np.asarray([[0.01, 0.02]], dtype=np.float32), (2_005, 1)
        )
        actual = _angular_cumsum(increments, chunk_size=1_000)
        expected = np.mod(
            np.cumsum(increments, axis=0, dtype=np.float32),
            np.float32(2.0 * np.pi),
        )
        np.testing.assert_allclose(actual, expected, rtol=0.0, atol=2e-4)

    def test_parse_midi_rejects_polyphony_instead_of_discarding_harmony(self) -> None:
        midi = mido.MidiFile(type=1, ticks_per_beat=480)
        tempo = mido.MidiTrack()
        tempo.append(mido.MetaMessage("set_tempo", tempo=500_000, time=0))
        midi.tracks.append(tempo)

        melody = mido.MidiTrack()
        melody.append(mido.MetaMessage("track_name", name="Lead Melody", time=0))
        melody.append(mido.Message("note_on", note=72, velocity=90, time=0))
        melody.append(mido.Message("note_on", note=74, velocity=90, time=400))
        melody.append(mido.Message("note_off", note=72, velocity=0, time=80))
        melody.append(mido.Message("note_off", note=74, velocity=0, time=400))
        midi.tracks.append(melody)

        accompaniment = mido.MidiTrack()
        accompaniment.append(mido.MetaMessage("track_name", name="Piano", time=0))
        for pitch in (48, 52, 55, 52, 48):
            accompaniment.append(mido.Message("note_on", note=pitch, velocity=70, time=0))
            accompaniment.append(mido.Message("note_off", note=pitch, velocity=0, time=480))
        midi.tracks.append(accompaniment)

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "polyphonic.mid"
            midi.save(path)
            with self.assertRaises(MidiValidationError) as raised:
                parse_midi_details(path)

        self.assertEqual(raised.exception.code, "polyphonic_track")

    def test_parse_midi_preserves_a_monophonic_file(self) -> None:
        midi = mido.MidiFile(type=0, ticks_per_beat=480)
        track = mido.MidiTrack()
        track.append(mido.Message("note_on", note=60, velocity=80, time=0))
        track.append(mido.Message("note_off", note=60, velocity=0, time=480))
        track.append(mido.Message("note_on", note=62, velocity=80, time=0))
        track.append(mido.Message("note_off", note=62, velocity=0, time=480))
        midi.tracks.append(track)

        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "monophonic.mid"
            midi.save(path)
            parsed = parse_midi_details(path)

        self.assertFalse(parsed.melody_extracted)
        self.assertEqual([note.pitch for note in parsed.notes], [60, 62])

    def test_build_tokens_adds_quantized_rest_and_tail(self) -> None:
        notes = [MidiNote(0.5, 1.0, 60, 80), MidiNote(1.5, 2.0, 62, 80)]
        tokens = build_tokens(notes, tail_frames=250)
        self.assertEqual(
            tokens,
            [MidiToken(0, 125), MidiToken(60, 125), MidiToken(0, 125),
             MidiToken(62, 125), MidiToken(0, 250)],
        )

        zero_start = build_tokens(
            [MidiNote(0.0, 0.5, 60, 80)], tail_frames=250
        )
        self.assertEqual(zero_start[0], MidiToken(0, 0))
        self.assertEqual(zero_start[1], MidiToken(60, 125))

    def test_frame_features_mark_note_boundaries(self) -> None:
        tokens = [MidiToken(60, 2), MidiToken(0, 1), MidiToken(62, 2)]
        controls = np.arange(18, dtype=np.float32).reshape(3, 6)
        features = build_frame_features(tokens, controls, instrument_id=0)
        np.testing.assert_array_equal(features.q_pitch[:, 0], [60, 60, 0, 62, 62])
        np.testing.assert_array_equal(features.onsets, [1, 0, 1, 1, 0])
        np.testing.assert_array_equal(features.offsets, [0, 1, 1, 0, 1])
        np.testing.assert_array_equal(features.conditioning[2], controls[1])

    def test_exp_sigmoid_matches_expected_bounds_and_noise_bias(self) -> None:
        values = exp_sigmoid(np.asarray([-100.0, 0.0, 100.0], dtype=np.float32))
        self.assertGreater(float(values[0]), 0.0)
        self.assertAlmostEqual(float(values[1]), 2.0 * 0.5 ** np.log(10.0) + 1e-7, places=6)
        self.assertLessEqual(float(values[2]), 2.000001)
        biased = exp_sigmoid(np.asarray([0.0], dtype=np.float32), bias=-5.0)
        self.assertLess(float(biased[0]), float(values[1]))

    def test_renderer_emits_32_frames_and_short_final_block(self) -> None:
        class FakeRunner:
            def infer(self, feeds):
                self.feeds = feeds
                return {
                    "f0_hz": np.full((1, 64, 1), 440.0, dtype=np.float32),
                    "amplitudes": np.full((1, 64, 1), -4.0, dtype=np.float32),
                    "harmonic_distribution": np.full(
                        (1, 64, 60), -4.0, dtype=np.float32
                    ),
                    "noise_magnitudes": np.full(
                        (1, 64, 65), -20.0, dtype=np.float32
                    ),
                }

        features = FrameFeatures(
            conditioning=np.full((80, 6), 0.5, dtype=np.float32),
            q_pitch=np.full((80, 1), 69.0, dtype=np.float32),
            onsets=np.zeros(80, dtype=np.int64),
            offsets=np.zeros(80, dtype=np.int64),
            instrument_id=0,
        )
        renderer = MidiDdspRenderer(FakeRunner(), features)
        self.assertEqual(renderer.render_block(0).shape, (32 * 64,))
        self.assertEqual(renderer.render_block(1).shape, (32 * 64,))
        self.assertEqual(renderer.render_block(2).shape, (16 * 64,))

    def test_streaming_reverb_matches_causal_dry_plus_wet_convolution(self) -> None:
        impulse_response = np.asarray(
            [0.0, 0.5, -0.25, 0.125, 0.0, 0.05, 0.0, -0.025, 0.01],
            dtype=np.float32,
        )
        dry = np.linspace(-0.5, 0.6, 12, dtype=np.float32)
        reverb = StreamingFftReverb(impulse_response, block_size=4)
        output = np.concatenate(
            [reverb.process(dry[index : index + 4]) for index in range(0, 12, 4)]
        )
        expected = dry + np.convolve(dry, impulse_response)[: dry.size]
        np.testing.assert_allclose(output, expected, rtol=1e-6, atol=1e-6)

    def test_reverb_asset_selects_the_instrument_and_original_settings(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "reverb.npz"
            responses = np.zeros((2, 8), dtype=np.float32)
            responses[1, 2] = 0.25
            np.savez_compressed(
                path,
                impulse_responses=responses,
                sample_rate=np.asarray(16_000, dtype=np.int32),
                decay_start=np.asarray(4, dtype=np.int32),
                decay_exponent=np.asarray(4.0, dtype=np.float32),
                add_dry=np.asarray(1, dtype=np.int8),
            )
            reverb = StreamingFftReverb.from_asset(path, 1, block_size=4)
        self.assertEqual(reverb.instrument_id, 1)
        self.assertEqual(reverb.ir_length, 8)
        output = reverb.process(np.ones(4, dtype=np.float32))
        np.testing.assert_allclose(output, [1.0, 1.0, 1.25, 1.25], atol=1e-6)

    def test_reverb_export_applies_original_decay_and_dry_mask(self) -> None:
        decay = original_inference_decay(total_length=8, start_length=4, decay_exponent=4)
        np.testing.assert_array_equal(decay[:4], np.ones(4, dtype=np.float32))
        self.assertAlmostEqual(float(decay[-1]), math.exp(-4.0), places=6)
        raw = np.ones((2, 48_000), dtype=np.float32)
        prepared = prepare_impulse_responses(raw)
        np.testing.assert_array_equal(prepared[:, 0], np.zeros(2, dtype=np.float32))
        np.testing.assert_array_equal(prepared[:, 1:16_000], raw[:, 1:16_000])


if __name__ == "__main__":
    unittest.main()
