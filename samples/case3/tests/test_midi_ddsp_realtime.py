from __future__ import annotations

from pathlib import Path
import math
import tempfile
import unittest

import mido
import numpy as np

from midi_ddsp_realtime import (
    FrameFeatures,
    MidiDdspRenderer,
    MidiNote,
    MidiToken,
    StreamingFftReverb,
    build_frame_features,
    build_tokens,
    exp_sigmoid,
    parse_midi_details,
)
from midi_ddsp_webui.midi_analysis import MidiValidationError
from tools.export_midi_ddsp_reverb import (
    original_inference_decay,
    prepare_impulse_responses,
)


class MidiDdspRealtimeHelpersTest(unittest.TestCase):
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
