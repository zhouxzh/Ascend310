from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from audio_io import (
    AudioBusyError,
    AudioError,
    AudioSettings,
    PulseAudioBackend,
    SherpaOnnxRecognizer,
    SpeechRuntimeError,
    _pcm16_to_float,
)


class AudioSettingsTests(unittest.TestCase):
    def test_default_board_source_sink_and_pcm_contract(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = AudioSettings.from_environ()

        self.assertIn("C922", settings.source)
        self.assertIn("Jieli", settings.sink)
        self.assertEqual(settings.sample_rate, 16_000)
        self.assertEqual(settings.channels, 1)
        self.assertEqual(settings.sample_width, 2)

    def test_rejects_a_non_mono_override(self) -> None:
        with patch.dict(os.environ, {"AUDIO_CHANNELS": "2"}, clear=True):
            with self.assertRaisesRegex(AudioError, "fixed at 1"):
                AudioSettings.from_environ()

    def test_rejects_a_non_16000_capture_rate(self) -> None:
        with patch.dict(os.environ, {"AUDIO_SAMPLE_RATE": "16001"}, clear=True):
            with self.assertRaisesRegex(AudioError, "16000"):
                AudioSettings.from_environ()

    def test_rejects_a_non_22050_playback_rate(self) -> None:
        with patch.dict(os.environ, {"AUDIO_PLAYBACK_SAMPLE_RATE": "22051"}, clear=True):
            with self.assertRaisesRegex(AudioError, "22050"):
                AudioSettings.from_environ()

    def test_direct_settings_keep_the_audio_contract(self) -> None:
        with self.assertRaisesRegex(AudioError, "16000"):
            AudioSettings(sample_rate=8_000)
        with self.assertRaisesRegex(AudioError, "22050"):
            AudioSettings(playback_sample_rate=16_000)

    def test_rejects_a_capture_limit_above_thirty_seconds(self) -> None:
        with patch.dict(os.environ, {"AUDIO_MAX_DURATION_SECONDS": "31"}, clear=True):
            with self.assertRaisesRegex(AudioError, "must not exceed 30"):
                AudioSettings.from_environ()

    def test_rejects_non_finite_capture_limit(self) -> None:
        with patch.dict(os.environ, {"AUDIO_MAX_DURATION_SECONDS": "nan"}, clear=True):
            with self.assertRaisesRegex(AudioError, "finite"):
                AudioSettings.from_environ()

    def test_pcm_conversion_requires_aligned_signed_16_bit_bytes(self) -> None:
        self.assertEqual(_pcm16_to_float(b"\x00\x00\xff\x7f"), [0.0, 32767 / 32768.0])
        with self.assertRaises(SpeechRuntimeError):
            _pcm16_to_float(b"\x00")

    def test_recognizer_uses_current_sherpa_result_api(self) -> None:
        class Result:
            text = "识别结果"

        class Stream:
            def __init__(self) -> None:
                self.finished = False

            def accept_waveform(self, sample_rate: int, samples: list[float]) -> None:
                self.sample_rate = sample_rate
                self.samples = samples

            def input_finished(self) -> None:
                self.finished = True

        class Runtime:
            def create_stream(self) -> Stream:
                self.stream = Stream()
                return self.stream

            def is_ready(self, stream: Stream) -> bool:
                return False

            def get_result_all(self, stream: Stream) -> Result:
                self.result_stream = stream
                return Result()

        recognizer = SherpaOnnxRecognizer()
        runtime = Runtime()
        recognizer._recognizer = runtime

        self.assertEqual(recognizer._transcribe(b"\x00\x00", 16_000), "识别结果")
        self.assertTrue(runtime.stream.finished)

    def test_recognizer_rejects_non_16000_input(self) -> None:
        recognizer = SherpaOnnxRecognizer()
        with self.assertRaisesRegex(SpeechRuntimeError, "16000"):
            recognizer._transcribe(b"\x00\x00", 8_000)


class AudioOperationTests(unittest.IsolatedAsyncioTestCase):
    async def test_second_audio_operation_is_rejected_without_waiting(self) -> None:
        backend = PulseAudioBackend(AudioSettings())
        await backend._acquire()
        try:
            with self.assertRaises(AudioBusyError):
                await backend._acquire()
        finally:
            await backend._release()

    async def test_playback_rejects_a_mismatched_sample_rate_before_spawn(self) -> None:
        backend = PulseAudioBackend(AudioSettings())
        with self.assertRaisesRegex(AudioError, "22050"):
            await backend.play_pcm(b"\x00\x00", 16_000)
        self.assertFalse(backend._operation_in_use)
