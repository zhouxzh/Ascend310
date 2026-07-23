from __future__ import annotations

import unittest

import numpy as np

from midi_ddsp_realtime import (
    FrameFeatures,
    MidiDdspRenderer,
    MidiNote,
    MidiToken,
    build_frame_features,
    build_tokens,
    exp_sigmoid,
)


class MidiDdspRealtimeHelpersTest(unittest.TestCase):
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
        np.testing.assert_array_equal(features.onsets, [1, 0, 0, 1, 0])
        np.testing.assert_array_equal(features.offsets, [0, 1, 0, 0, 1])
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


if __name__ == "__main__":
    unittest.main()
