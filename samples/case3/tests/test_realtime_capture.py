from __future__ import annotations

import unittest

import numpy as np

from tools.analyze_realtime_playback import analyze_capture


def make_metadata(blocks: int, block_size: int, sample_rate: int) -> dict[str, np.ndarray]:
    interval = block_size / sample_rate
    return {
        "times": np.arange(blocks, dtype=np.float64) * interval,
        "frames": np.full(blocks, block_size, dtype=np.int32),
        "queue_depth": np.full(blocks, 4, dtype=np.int32),
        "status_underflow": np.zeros(blocks, dtype=np.bool_),
        "had_audio": np.ones(blocks, dtype=np.bool_),
    }


class RealtimeCaptureAnalysisTest(unittest.TestCase):
    sample_rate = 16_000
    block_size = 320
    block_count = 12

    def continuous_sine(self) -> np.ndarray:
        samples = np.arange(self.block_count * self.block_size, dtype=np.float64)
        signal = 0.2 * np.sin(2.0 * np.pi * 440.0 * samples / self.sample_rate)
        return signal.astype(np.float32).reshape(self.block_count, self.block_size)

    def test_continuous_signal_has_no_boundary_discontinuity(self) -> None:
        blocks = self.continuous_sine()
        report = analyze_capture(
            blocks,
            make_metadata(self.block_count, self.block_size, self.sample_rate),
            self.sample_rate,
        )

        self.assertEqual(report["conclusion"], "callback_signal_continuous")
        self.assertEqual(report["signal"]["discontinuity_count"], 0)
        self.assertEqual(report["transport"]["status_underflows"], 0)

    def test_inserted_step_is_reported_at_block_boundary(self) -> None:
        blocks = self.continuous_sine()
        blocks[5:] += 0.5
        report = analyze_capture(
            blocks,
            make_metadata(self.block_count, self.block_size, self.sample_rate),
            self.sample_rate,
        )

        self.assertEqual(
            report["conclusion"], "synthesis_boundary_discontinuity_detected"
        )
        self.assertGreaterEqual(report["signal"]["discontinuity_count"], 1)
        self.assertEqual(report["signal"]["discontinuities"][0]["block"], 5)

    def test_transport_underflow_takes_priority(self) -> None:
        blocks = self.continuous_sine()
        metadata = make_metadata(
            self.block_count, self.block_size, self.sample_rate
        )
        blocks[4].fill(0.0)
        metadata["status_underflow"][4] = True
        metadata["had_audio"][4] = False
        report = analyze_capture(blocks, metadata, self.sample_rate)

        self.assertEqual(report["conclusion"], "transport_discontinuity_detected")
        self.assertEqual(report["transport"]["status_underflows"], 1)
        self.assertEqual(report["transport"]["callbacks_without_audio"], 1)

    def test_active_silence_outside_training_range_is_identified(self) -> None:
        blocks = self.continuous_sine()
        metadata = make_metadata(
            self.block_count, self.block_size, self.sample_rate
        )
        blocks[4].fill(0.0)
        metadata["held_voice_count"] = np.zeros(self.block_count, dtype=np.int32)
        metadata["total_voice_count"] = np.zeros(self.block_count, dtype=np.int32)
        metadata["minimum_midi_note"] = np.full(
            self.block_count, -1, dtype=np.int32
        )
        metadata["maximum_midi_note"] = np.full(
            self.block_count, -1, dtype=np.int32
        )
        metadata["held_voice_count"][4] = 1
        metadata["total_voice_count"][4] = 1
        metadata["minimum_midi_note"][4] = 36
        metadata["maximum_midi_note"][4] = 36

        report = analyze_capture(
            blocks,
            metadata,
            self.sample_rate,
            model_metadata={
                "mean_min_pitch_note": 56.6,
                "mean_max_pitch_note": 74.4,
            },
        )

        self.assertEqual(report["conclusion"], "model_range_dropout_detected")
        self.assertEqual(report["signal"]["silent_blocks_with_held_notes"], 1)
        self.assertTrue(
            report["signal"]["active_dropout_details"][0][
                "outside_model_pitch_range"
            ]
        )

    def test_initial_attack_silence_is_not_a_dropout(self) -> None:
        blocks = np.zeros((self.block_count, self.block_size), dtype=np.float32)
        metadata = make_metadata(
            self.block_count, self.block_size, self.sample_rate
        )
        metadata["held_voice_count"] = np.zeros(self.block_count, dtype=np.int32)
        metadata["held_voice_count"][0] = 1
        metadata["note_on_count"] = np.zeros(self.block_count, dtype=np.int32)
        metadata["note_on_count"][0] = 1

        report = analyze_capture(blocks, metadata, self.sample_rate)

        self.assertEqual(report["conclusion"], "callback_signal_continuous")
        self.assertEqual(report["signal"]["near_silent_blocks_with_held_notes"], 0)
        self.assertEqual(report["signal"]["attack_grace_blocks"], 5)


if __name__ == "__main__":
    unittest.main()
