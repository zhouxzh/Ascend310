from __future__ import annotations

import time
import inspect
import threading
import unittest
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np

from realtime_ddsp import (
    Adsr,
    DEFAULT_ENVELOPE,
    DdspVstSettings,
    HarmonicSynthesizer,
    JuceFreeverb,
    MODEL_HOP_SIZE,
    LivePlayer,
    PolyphonicGainSmoother,
    PolyphonicMidiState,
    RawMidiInput,
    RealtimeSynthEngine,
    VoiceRenderer,
    MidiVoiceSnapshot,
    ModelControls,
    NoiseSynthesizer,
    WindowedSincResampler,
    _midi_device_profile,
    query_midi_devices,
    shape_midi_velocity,
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


class MidiDeviceQueryTest(unittest.TestCase):
    def test_midiplus_tiny_profile_reports_32_keys(self) -> None:
        self.assertEqual(
            _midi_device_profile("MIDIPLUS TINY"),
            {"manufacturer": "MIDIPLUS", "model": "TINY", "key_count": 32},
        )

    def test_linux_uses_raw_midi_when_alsa_sequencer_is_missing(self) -> None:
        raw_device = {
            "id": "raw:/dev/snd/midiC1D0",
            "index": 0,
            "name": "MIDIPLUS TINY",
            "port": "raw:/dev/snd/midiC1D0",
            "backend": "raw",
        }
        with patch("realtime_ddsp.sys.platform", "linux"), patch(
            "realtime_ddsp.Path.exists", return_value=False
        ), patch(
            "realtime_ddsp._query_raw_midi_devices", return_value=[raw_device]
        ):
            self.assertEqual(query_midi_devices(), [raw_device])

    def test_missing_linux_midi_backends_is_reported_cleanly(self) -> None:
        with patch("realtime_ddsp.sys.platform", "linux"), patch(
            "realtime_ddsp.Path.exists", return_value=False
        ), patch("realtime_ddsp._query_raw_midi_devices", return_value=[]):
            with self.assertRaisesRegex(RuntimeError, "no raw MIDI device was found"):
                query_midi_devices()

    def test_rtmidi_initialization_error_is_normalized(self) -> None:
        with patch("realtime_ddsp.sys.platform", "win32"), patch(
            "mido.get_input_names", side_effect=SystemError("backend failed")
        ):
            with self.assertRaisesRegex(RuntimeError, "Unable to enumerate MIDI"):
                query_midi_devices()

    def test_raw_midi_reader_parses_note_on_bytes(self) -> None:
        import mido

        messages = []
        reader = RawMidiInput.__new__(RawMidiInput)
        reader.fd = 7
        reader._fd_lock = threading.Lock()
        reader.parser = mido.Parser()
        reader._parser_factory = mido.Parser
        reader.callback = messages.append
        reader.stop_event = SimpleNamespace(
            is_set=lambda calls=iter((False, True)): next(calls)
        )

        with patch.object(reader, "_fd_matches_path", return_value=True), patch(
            "realtime_ddsp.select.select", return_value=([7], [], [])
        ), patch(
            "realtime_ddsp.os.read", return_value=bytes((0x90, 60, 100))
        ):
            reader._read_loop()

        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].type, "note_on")
        self.assertEqual(messages[0].note, 60)
        self.assertEqual(messages[0].velocity, 100)

    def test_raw_midi_reader_reopens_after_device_replacement(self) -> None:
        import mido

        messages = []
        reader = RawMidiInput.__new__(RawMidiInput)
        reader.fd = 7
        reader._fd_lock = threading.Lock()
        reader.parser = mido.Parser()
        reader._parser_factory = mido.Parser
        reader.callback = messages.append
        reader.reconnect_count = 0
        reader.last_error = None
        reader.stop_event = SimpleNamespace(
            is_set=lambda calls=iter((False, False, False, True)): next(calls),
            wait=lambda _timeout: False,
        )

        def reopen() -> bool:
            reader.fd = 8
            reader.reconnect_count += 1
            return True

        with patch.object(reader, "_fd_matches_path", return_value=True), patch.object(
            reader, "_open_available", side_effect=reopen
        ), patch("realtime_ddsp.os.close"), patch(
            "realtime_ddsp.select.select",
            side_effect=[([7], [], []), ([8], [], [])],
        ), patch(
            "realtime_ddsp.os.read",
            side_effect=[b"", bytes((0x90, 64, 96))],
        ):
            reader._read_loop()

        self.assertEqual(reader.reconnect_count, 1)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].note, 64)


class LivePlayerTest(unittest.TestCase):
    def test_velocity_curve_boosts_light_touch_without_changing_full_scale(self) -> None:
        self.assertAlmostEqual(shape_midi_velocity(0.25, 0.5), 0.5)
        self.assertAlmostEqual(shape_midi_velocity(0.25, 1.0), 0.25)
        self.assertEqual(shape_midi_velocity(0.0, 0.5), 0.0)
        self.assertEqual(shape_midi_velocity(1.0, 0.5), 1.0)

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
    def test_output_gain_is_applied_before_stereo_reverb(self) -> None:
        engine = RealtimeSynthEngine.__new__(RealtimeSynthEngine)
        engine._settings_lock = threading.Lock()
        engine._output_stats_lock = threading.Lock()
        engine.output_peak = 0.0
        engine.clipped_samples = 0
        engine.settings = DdspVstSettings(output_gain_db=-6.0)
        engine.resampler = SimpleNamespace(process=lambda samples: samples)
        engine.reverb = SimpleNamespace(
            process=lambda samples: np.repeat(samples[:, None], 2, axis=1)
        )
        engine.render_model_frame = lambda: np.array(
            [-0.75, -0.25, 0.25, 0.75], dtype=np.float32
        )

        output = engine.render_output_block()

        expected = np.array([-0.75, -0.25, 0.25, 0.75], dtype=np.float32) * (
            10.0 ** (-6.0 / 20.0)
        )
        np.testing.assert_allclose(output[:, 0], expected, atol=1e-7)
        np.testing.assert_allclose(output[:, 1], expected, atol=1e-7)

    def test_engine_reports_over_range_samples_without_hard_clipping(self) -> None:
        engine = RealtimeSynthEngine.__new__(RealtimeSynthEngine)
        engine._settings_lock = threading.Lock()
        engine._output_stats_lock = threading.Lock()
        engine.settings = DdspVstSettings()
        engine.output_peak = 0.0
        engine.clipped_samples = 0
        engine.resampler = SimpleNamespace(process=lambda samples: samples)
        engine.reverb = SimpleNamespace(
            process=lambda samples: np.repeat(samples[:, None], 2, axis=1)
        )
        engine.render_model_frame = lambda: np.array([-1.2, 0.0, 1.3], dtype=np.float32)

        output = engine.render_output_block()

        self.assertAlmostEqual(float(output.min()), -1.2, places=6)
        self.assertAlmostEqual(float(output.max()), 1.3, places=6)
        self.assertAlmostEqual(engine.output_peak, 1.3, places=6)
        self.assertEqual(engine.clipped_samples, 4)

    def test_ddsp_vst_engine_defaults_to_google_monophonic_mode(self) -> None:
        default = inspect.signature(RealtimeSynthEngine).parameters["max_voices"].default
        self.assertEqual(default, 1)

    def test_voice_renderer_applies_plugin_control_order(self) -> None:
        class FakeControls:
            backend_name = "fake"

            def predict_from_state(self, state, f0_scaled, pw_scaled):
                self.inputs = (f0_scaled, pw_scaled)
                return (
                    ModelControls(
                        amplitude=0.8,
                        harmonics=np.ones(60, dtype=np.float32) / 60.0,
                        noise_amps=np.ones(65, dtype=np.float32),
                    ),
                    state,
                )

        controls = FakeControls()
        voice = VoiceRenderer(controls)
        voice.harmonic = SimpleNamespace(
            previous_amplitudes=np.zeros(60, dtype=np.float32),
            render=lambda amplitude, harmonics, f0_hz: np.full(
                MODEL_HOP_SIZE, amplitude + f0_hz / 1000.0, dtype=np.float32
            ),
        )
        voice.noise = SimpleNamespace(
            render=lambda magnitudes: np.full(
                MODEL_HOP_SIZE, float(magnitudes[0]), dtype=np.float32
            )
        )
        snapshot = MidiVoiceSnapshot(
            slot=0,
            note=69,
            velocity=0.25,
            pitch_bend=8192,
            volume=1.0,
            expression=1.0,
            envelope=1.0,
        )
        settings = DdspVstSettings(
            pitch_shift=12,
            harmonic_gain=0.5,
            noise_gain=0.25,
            velocity_curve=0.5,
            input_pitch=0.1,
            input_gain=0.2,
        )

        output = voice.render(snapshot, settings)

        self.assertAlmostEqual(controls.inputs[0], 81 / 127 - 0.1)
        self.assertAlmostEqual(controls.inputs[1], 0.3)
        expected_f0 = 880.0
        np.testing.assert_allclose(output, 0.4 + expected_f0 / 1000.0 + 0.25)

    def test_freeverb_bypass_is_stereo_and_wet_output_is_finite(self) -> None:
        dry = np.linspace(-0.2, 0.2, 1024, dtype=np.float32)
        reverb = JuceFreeverb(16_000, DdspVstSettings())
        bypass = reverb.process(dry)
        self.assertEqual(bypass.shape, (1024, 2))
        np.testing.assert_array_equal(bypass[:, 0], dry)
        reverb.update(DdspVstSettings(reverb_wet=0.5))
        wet = reverb.process(dry)
        self.assertTrue(np.all(np.isfinite(wet)))

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


class GoogleDspParityTest(unittest.TestCase):
    def test_harmonic_synth_matches_halfway_interpolation_reference(self) -> None:
        synth = HarmonicSynthesizer()
        distribution = np.zeros(60, dtype=np.float32)
        distribution[0] = 1.0

        output = synth.render(0.4, distribution, 440.0)

        midpoint = MODEL_HOP_SIZE // 2
        amplitude = np.empty(MODEL_HOP_SIZE, dtype=np.float32)
        amplitude[:midpoint] = np.linspace(
            0.0, 0.4, midpoint, endpoint=False, dtype=np.float32
        )
        amplitude[midpoint:] = 0.4
        phases = np.cumsum(
            np.full(MODEL_HOP_SIZE, 2.0 * np.pi * 440.0 / 16_000, dtype=np.float32),
            dtype=np.float32,
        )
        expected = np.sin(phases) * amplitude
        np.testing.assert_allclose(output, expected, atol=2e-6)

    def test_noise_synth_reset_replays_the_reference_sequence(self) -> None:
        synth = NoiseSynthesizer(seed=42)
        magnitudes = np.linspace(0.01, 0.3, 65, dtype=np.float32)

        first = synth.render(magnitudes)
        synth.reset()
        replay = synth.render(magnitudes)

        np.testing.assert_array_equal(first, replay)
        self.assertTrue(np.all(np.isfinite(first)))

    def test_windowed_sinc_is_continuous_across_control_frames(self) -> None:
        rng = np.random.default_rng(20260725)
        source = rng.normal(0.0, 0.2, MODEL_HOP_SIZE * 4).astype(np.float32)
        streaming = WindowedSincResampler(16_000, 48_000)
        whole = WindowedSincResampler(16_000, 48_000)

        streamed = np.concatenate(
            [streaming.process(block) for block in source.reshape(-1, MODEL_HOP_SIZE)]
        )
        reference = whole.process(source)

        np.testing.assert_allclose(streamed, reference, atol=1e-6)
        self.assertAlmostEqual(streaming.algorithmic_latency_seconds, 0.00625)

    def test_windowed_sinc_preserves_passband_and_rejects_images(self) -> None:
        frequency = 7_000.0
        source = np.sin(
            2.0 * np.pi * frequency * np.arange(16_000) / 16_000
        ).astype(np.float32)
        resampler = WindowedSincResampler(16_000, 48_000)
        output = np.concatenate(
            [resampler.process(block) for block in source.reshape(-1, MODEL_HOP_SIZE)]
        )
        steady = output[6_000:]

        passband_db = 20.0 * np.log10(
            np.sqrt(np.mean(steady * steady)) / np.sqrt(0.5)
        )
        windowed = steady * np.hanning(steady.size)
        spectrum = np.abs(np.fft.rfft(windowed))
        frequencies = np.fft.rfftfreq(windowed.size, 1.0 / 48_000)
        tone = spectrum[np.argmin(np.abs(frequencies - frequency))]
        image = spectrum[np.argmin(np.abs(frequencies - (16_000 - frequency)))]
        image_db = 20.0 * np.log10(max(float(image / tone), 1e-12))

        self.assertLess(abs(float(passband_db)), 0.2)
        self.assertLess(image_db, -60.0)


if __name__ == "__main__":
    unittest.main()
