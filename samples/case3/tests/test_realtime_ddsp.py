from __future__ import annotations

import time
import unittest
from types import SimpleNamespace

import numpy as np

from realtime_ddsp import (
    Adsr,
    DEFAULT_ENVELOPE,
    MODEL_HOP_SIZE,
    LivePlayer,
    PolyphonicGainSmoother,
    PolyphonicMidiState,
    RealtimeSynthEngine,
)


class FakeEngine:
    output_sample_rate = 16_000

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.render_count = 0

    def render_output_block(self) -> np.ndarray:
        if self.fail:
            raise ValueError("render failed")
        self.render_count += 1
        return np.full(MODEL_HOP_SIZE, self.render_count, dtype=np.float32)


class CallbackStatus:
    def __init__(self, output_underflow: bool = False) -> None:
        self.output_underflow = output_underflow


class NativeFourChannelDevice:
    @staticmethod
    def query_devices(_device, _kind):
        return {"name": "Native 4-channel output", "max_output_channels": 4}

    @staticmethod
    def check_output_settings(*, channels, **_kwargs) -> None:
        if channels != 4:
            raise RuntimeError("invalid channel count")


class StereoCapableDevice:
    @staticmethod
    def query_devices(_device, _kind):
        return {"name": "USB stereo output", "max_output_channels": 2}

    @staticmethod
    def check_output_settings(**_kwargs) -> None:
        return None


def wait_until(predicate, timeout: float = 0.2) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.001)
    return predicate()


class LivePlayerTest(unittest.TestCase):
    def test_default_envelope_matches_ddsp_vst_and_ramps_over_frames(self) -> None:
        self.assertEqual(DEFAULT_ENVELOPE.attack, 0.10)
        self.assertEqual(DEFAULT_ENVELOPE.decay, 0.0)
        self.assertEqual(DEFAULT_ENVELOPE.sustain, 1.0)
        self.assertEqual(DEFAULT_ENVELOPE.release, 1.20)

        envelope = Adsr(16_000)
        envelope.note_on()
        first_frame = envelope.next_block(MODEL_HOP_SIZE)
        self.assertAlmostEqual(float(first_frame[-1]), 0.2, places=5)
        self.assertEqual(envelope.phase, "attack")

    def test_consumer_wakes_producer_and_refills_prebuffer(self) -> None:
        engine = FakeEngine()
        player = LivePlayer(engine, prebuffer_blocks=3)
        player.worker.start()
        try:
            self.assertTrue(wait_until(lambda: player.blocks.qsize() == 3))
            time.sleep(0.01)

            output = np.zeros((MODEL_HOP_SIZE, 1), dtype=np.float32)
            player._audio_callback(
                output, MODEL_HOP_SIZE, None, CallbackStatus()
            )

            self.assertTrue(
                wait_until(lambda: player.blocks.qsize() == 3, timeout=0.04)
            )
            self.assertEqual(player.played_blocks, 1)
            self.assertGreaterEqual(player.rendered_blocks, 4)
            self.assertTrue(np.all(output[:, 0] == 1.0))
        finally:
            player._stop_worker()


class PolyphonicSynthesisTest(unittest.TestCase):
    def test_output_gain_is_applied_and_clipped(self) -> None:
        engine = RealtimeSynthEngine.__new__(RealtimeSynthEngine)
        engine.output_gain = 2.0
        engine.resampler = SimpleNamespace(process=lambda samples: samples)
        engine.render_model_frame = lambda: np.array(
            [-0.75, -0.25, 0.25, 0.75], dtype=np.float32
        )

        output = engine.render_output_block()

        np.testing.assert_array_equal(
            output, np.array([-1.0, -0.5, 0.5, 1.0], dtype=np.float32)
        )

    def test_released_voice_slot_is_reused_without_reallocating_all_slots(self) -> None:
        midi = PolyphonicMidiState(max_voices=2)
        midi.handle_message(SimpleNamespace(type="note_on", note=60, velocity=100))
        midi.handle_message(SimpleNamespace(type="note_on", note=64, velocity=100))
        initial_slots = {snapshot.note: snapshot.slot for snapshot in midi.next_snapshots()}

        midi.handle_message(SimpleNamespace(type="note_off", note=60))
        midi.handle_message(SimpleNamespace(type="note_on", note=67, velocity=100))
        reused_slots = {snapshot.note: snapshot.slot for snapshot in midi.next_snapshots()}

        self.assertEqual(initial_slots, {60: 0, 64: 1})
        self.assertEqual(reused_slots, {64: 1, 67: 0})

    def test_same_note_retrigger_continues_the_existing_envelope(self) -> None:
        midi = PolyphonicMidiState(max_voices=2)
        note_on = SimpleNamespace(type="note_on", note=64, velocity=100)
        note_off = SimpleNamespace(type="note_off", note=64, velocity=0)
        midi.handle_message(note_on)
        for _ in range(5):
            initial = midi.next_snapshots()[0]
        self.assertAlmostEqual(initial.envelope, 1.0, places=5)

        midi.handle_message(note_off)
        releasing = midi.next_snapshots()[0]
        self.assertGreater(releasing.envelope, 0.9)
        midi.handle_message(note_on)
        retriggered = midi.next_snapshots()[0]

        self.assertEqual(retriggered.slot, releasing.slot)
        self.assertGreaterEqual(retriggered.envelope, releasing.envelope)

    def test_polyphonic_gain_is_continuous_across_voice_count_changes(self) -> None:
        smoother = PolyphonicGainSmoother(release_seconds=0.08)
        block = np.ones(MODEL_HOP_SIZE, dtype=np.float32)

        single_voice = smoother.process(block, voice_count=1)
        four_voices = smoother.process(block * 4.0, voice_count=4)
        back_to_single = smoother.process(block, voice_count=1)

        self.assertAlmostEqual(float(single_voice[-1]), 1.0)
        self.assertAlmostEqual(float(four_voices[0]), 4.0)
        self.assertAlmostEqual(float(four_voices[-1]), 1.0)
        self.assertAlmostEqual(float(back_to_single[0]), 0.25)
        self.assertGreater(float(back_to_single[-1]), 0.25)


class LivePlayerDeviceTest(unittest.TestCase):
    def test_default_device_latency_absorbs_windows_callback_jitter(self) -> None:
        player = LivePlayer(FakeEngine())
        self.assertEqual(player.output_latency_seconds, 0.08)

    def test_empty_queue_and_device_underflow_count_once(self) -> None:
        player = LivePlayer(FakeEngine(), prebuffer_blocks=2)
        output = np.ones((MODEL_HOP_SIZE, 1), dtype=np.float32)

        player._audio_callback(
            output,
            MODEL_HOP_SIZE,
            None,
            CallbackStatus(output_underflow=True),
        )

        self.assertEqual(player.underruns, 1)
        self.assertEqual(player.played_blocks, 0)
        self.assertTrue(np.all(output == 0.0))

    def test_stereo_is_preferred_when_device_accepts_mono_and_stereo(self) -> None:
        player = LivePlayer(FakeEngine(), prebuffer_blocks=2, output_device=1)
        channels, name = player._select_output_channels(StereoCapableDevice)
        self.assertEqual(channels, 2)
        self.assertEqual(name, "USB stereo output")

        block = np.arange(MODEL_HOP_SIZE, dtype=np.float32)
        player.blocks.put_nowait(block)
        output = np.zeros((MODEL_HOP_SIZE, channels), dtype=np.float32)
        player._audio_callback(output, MODEL_HOP_SIZE, None, CallbackStatus())

        np.testing.assert_array_equal(output[:, 0], block)
        np.testing.assert_array_equal(output[:, 1], block)

    def test_native_output_channel_count_is_selected_and_filled(self) -> None:
        player = LivePlayer(FakeEngine(), prebuffer_blocks=2, output_device=8)
        channels, name = player._select_output_channels(NativeFourChannelDevice)
        self.assertEqual(channels, 4)
        self.assertEqual(name, "Native 4-channel output")

        block = np.arange(MODEL_HOP_SIZE, dtype=np.float32)
        player.blocks.put_nowait(block)
        output = np.zeros((MODEL_HOP_SIZE, channels), dtype=np.float32)
        player._audio_callback(output, MODEL_HOP_SIZE, None, CallbackStatus())

        self.assertEqual(player.played_blocks, 1)
        for channel in range(channels):
            np.testing.assert_array_equal(output[:, channel], block)

    def test_render_worker_failure_is_reported(self) -> None:
        player = LivePlayer(FakeEngine(fail=True), prebuffer_blocks=2)
        player.worker.start()
        try:
            self.assertTrue(wait_until(player.stop_event.is_set))
            with self.assertRaisesRegex(RuntimeError, "render worker failed"):
                player.raise_worker_error()
        finally:
            player._stop_worker()


if __name__ == "__main__":
    unittest.main()
