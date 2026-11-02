from __future__ import annotations

import asyncio
import io
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ascend_edge_chat.voice.adapters import EspeakNgSynthesizer, build_speech_adapters
from ascend_edge_chat.voice.alsa import (
    AlsaAudioIO,
    AudioDeviceError,
    NoSpeechDetected,
    _pulse_source_for_alsa_device,
    downmix_pcm16le,
    discover_voice_devices,
    enumerate_alsa_devices,
)
from ascend_edge_chat.voice.audio_processing import AgcSettings, apply_pcm16_agc
from ascend_edge_chat.voice.config import VoiceConfigurationError, VoiceSettings
from ascend_edge_chat.voice.vad import EnergyVad, VadSettings, VadState, pcm_rms


class _BlockedStdout:
    async def readexactly(self, count: int) -> bytes:
        self.count = count
        await asyncio.Event().wait()
        return b""


class _FinishedReader:
    def __init__(self, data: bytes) -> None:
        self._data = data

    async def read(self, count: int = -1) -> bytes:
        if not self._data:
            return b""
        amount = len(self._data) if count < 0 else min(count, len(self._data))
        value, self._data = self._data[:amount], self._data[amount:]
        return value


class _FinishedProcess:
    def __init__(self, stdout: bytes, *, returncode: int = 0, stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = _FinishedReader(stdout)
        self.stderr = _FinishedReader(stderr)

    def terminate(self) -> None:
        self.returncode = -15

    async def wait(self) -> int:
        return self.returncode


class _StoppedProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.stdout = _BlockedStdout()
        self.stderr = None
        self.terminated = False
        self.interrupted = False

    def send_signal(self, sig: int) -> None:
        assert sig == 2
        self.interrupted = True
        self.returncode = 0

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    async def wait(self) -> int:
        return self.returncode or 0


class VoiceCoreTests(unittest.TestCase):
    def test_voice_settings_only_accept_loopback_gateway(self) -> None:
        settings = VoiceSettings.from_environ(
            {
                "ASCEND_EDGE_CHAT_VOICE_GATEWAY_URL": "http://127.0.0.1:7861/v1",
                "ASCEND_EDGE_CHAT_VOICE_GATEWAY_API_KEY": "a" * 24,
            }
        )
        self.assertEqual(settings.capture_frame_bytes, 640)
        self.assertEqual(settings.capture_plugin, "plughw")
        self.assertEqual(settings.initial_mode, "disabled")
        shared_capture = VoiceSettings.from_environ(
            {"ASCEND_EDGE_CHAT_VOICE_CAPTURE_PLUGIN": "dsnoop"}
        )
        self.assertEqual(shared_capture.capture_plugin, "dsnoop")
        with self.assertRaises(VoiceConfigurationError):
            VoiceSettings.from_environ(
                {"ASCEND_EDGE_CHAT_VOICE_CAPTURE_PLUGIN": "invalid"}
            )
        self.assertTrue(settings.adaptive_vad_enabled)
        self.assertTrue(settings.auto_gain_enabled)
        self.assertEqual(settings.max_reply_tokens, 32)
        disabled_processing = VoiceSettings.from_environ(
            {
                "ASCEND_EDGE_CHAT_VOICE_ADAPTIVE_VAD_ENABLED": "false",
                "ASCEND_EDGE_CHAT_VOICE_AUTO_GAIN_ENABLED": "off",
                "ASCEND_EDGE_CHAT_VOICE_AUTO_GAIN_TARGET_RMS": "3000",
            }
        )
        self.assertFalse(disabled_processing.adaptive_vad_enabled)
        self.assertFalse(disabled_processing.auto_gain_enabled)
        self.assertEqual(disabled_processing.auto_gain_target_rms, 3000.0)
        with self.assertRaises(VoiceConfigurationError):
            VoiceSettings.from_environ(
                {"ASCEND_EDGE_CHAT_VOICE_AUTO_GAIN_ENABLED": "sometimes"}
            )
        self.assertEqual(
            VoiceSettings.from_environ(
                {"ASCEND_EDGE_CHAT_VOICE_MODE": "browser"}
            ).initial_mode,
            "browser",
        )
        with self.assertRaises(VoiceConfigurationError):
            VoiceSettings.from_environ(
                {"ASCEND_EDGE_CHAT_VOICE_MODE": "microphone"}
            )
        with self.assertRaises(VoiceConfigurationError):
            VoiceSettings.from_environ(
                {"ASCEND_EDGE_CHAT_VOICE_GATEWAY_URL": "https://example.com/v1"}
            )

    def test_sherpa_backend_is_configured_but_not_ready_without_admitted_artifacts(self) -> None:
        settings = VoiceSettings.from_environ(
            {
                "ASCEND_EDGE_CHAT_VOICE_SPEECH_BACKEND": "sherpa-onnx",
                "ASCEND_EDGE_CHAT_VOICE_SHERPA_ASR_MODEL_DIR": "C:/not-admitted/asr",
                "ASCEND_EDGE_CHAT_VOICE_SHERPA_TTS_MODEL_DIR": "C:/not-admitted/tts",
            }
        )
        self.assertEqual(settings.speech_backend, "sherpa-onnx")
        with self.assertRaises(VoiceConfigurationError):
            VoiceSettings.from_environ({"ASCEND_EDGE_CHAT_VOICE_SPEECH_BACKEND": "cloud"})

    def test_espeak_backend_uses_standard_command_and_mono_wav_contract(self) -> None:
        settings = VoiceSettings.from_environ(
            {
                "ASCEND_EDGE_CHAT_VOICE_SPEECH_BACKEND": "sherpa-onnx",
                "ASCEND_EDGE_CHAT_VOICE_TTS_BACKEND": "espeak-ng",
                "ASCEND_EDGE_CHAT_VOICE_ESPEAK_NG_VOICE": "cmn",
                "ASCEND_EDGE_CHAT_VOICE_ESPEAK_NG_SPEED": "170",
            }
        )
        self.assertEqual(settings.tts_backend, "espeak-ng")
        self.assertEqual(settings.espeak_ng_binary, "espeak-ng")
        self.assertEqual(settings.espeak_ng_voice, "cmn")
        self.assertEqual(settings.espeak_ng_speed, 170)
        with self.assertRaises(VoiceConfigurationError):
            VoiceSettings.from_environ(
                {"ASCEND_EDGE_CHAT_VOICE_TTS_BACKEND": "cloud"}
            )

        output = io.BytesIO()
        with wave.open(output, "wb") as sink:
            sink.setnchannels(1)
            sink.setsampwidth(2)
            sink.setframerate(22_050)
            sink.writeframes(b"\x01\x00" * 320)
        completed = SimpleNamespace(
            returncode=0,
            stdout=output.getvalue(),
            stderr=b"",
        )
        recognizer, synthesizer = build_speech_adapters(settings)
        self.assertIsInstance(synthesizer, EspeakNgSynthesizer)
        self.assertEqual(type(recognizer).__name__, "SherpaOnnxRecognizer")
        with patch.object(synthesizer, "readiness", return_value=(True, "ready")), patch.object(
            synthesizer, "_executable", return_value="espeak-ng"
        ), patch(
            "ascend_edge_chat.voice.adapters.subprocess.run",
            return_value=completed,
        ):
            audio = asyncio.run(synthesizer.synthesize("你好"))
        self.assertEqual(audio.sample_rate_hz, 22_050)
        self.assertEqual(audio.channels, 1)
        self.assertEqual(audio.sample_width_bytes, 2)
        self.assertTrue(audio.pcm)

    def test_playback_uses_plughw_for_mono_to_stereo_conversion(self) -> None:
        settings = VoiceSettings()
        audio = AlsaAudioIO(
            settings,
            capture_device="hw:1,0",
            playback_device="hw:2,0",
        )
        self.assertIn("--device=plughw:1,0", audio.capture_command(2))
        self.assertIn("--device=plughw:2,0", audio.playback_command(22_050))
        self.assertEqual(audio.suspended_playback_command(22_050)[:3], ("pasuspender", "--", "aplay"))
        self.assertIn("--device=pulse", audio.pulse_playback_command(22_050))

    def test_pulse_capture_uses_the_selected_source_without_opening_alsa_directly(self) -> None:
        audio = AlsaAudioIO(
            VoiceSettings(capture_plugin="pulse", capture_device_channels=2),
            capture_device="hw:2,0",
            playback_device="hw:1,0",
        )
        listing = b'''Source #1
    Name: alsa_input.usb-selected-00.analog-stereo
    Properties:
        alsa.card = "2"
        alsa.device = "0"
'''
        completed = SimpleNamespace(returncode=0, stdout=listing, stderr=b"")
        with patch("ascend_edge_chat.voice.alsa.shutil.which", return_value="pactl"), patch(
            "ascend_edge_chat.voice.alsa.subprocess.run", return_value=completed
        ):
            command = audio.capture_command(2)
        self.assertEqual(command[0], "parec")
        self.assertIn("--device=alsa_input.usb-selected-00.analog-stereo", command)
        self.assertIn("--channels=2", command)
        self.assertIn("--latency-msec=20", command)
        self.assertIn("--raw", command)

    def test_pulse_capture_resolves_the_selected_alsa_microphone(self) -> None:
        source_listing = '''
Source #1
    Name: alsa_input.usb-other-00.analog-mono
    Properties:
        alsa.card = "1"
        alsa.device = "0"
Source #2
    Name: alsa_input.usb-selected-00.analog-stereo
    Properties:
        alsa.card = "2"
        alsa.device = "0"
'''
        self.assertEqual(
            _pulse_source_for_alsa_device(source_listing, card=2, device=0),
            "alsa_input.usb-selected-00.analog-stereo",
        )
        self.assertIsNone(_pulse_source_for_alsa_device(source_listing, card=9, device=0))
        audio = AlsaAudioIO(
            VoiceSettings(capture_plugin="pulse", capture_device_channels=2),
            capture_device="hw:2,0",
            playback_device="hw:1,0",
        )
        completed = SimpleNamespace(returncode=0, stdout=source_listing.encode(), stderr=b"")
        with patch("ascend_edge_chat.voice.alsa.shutil.which", return_value="pactl"), patch(
            "ascend_edge_chat.voice.alsa.subprocess.run", return_value=completed
        ):
            self.assertIn(
                "--device=alsa_input.usb-selected-00.analog-stereo",
                audio.capture_command(2),
            )
        with patch("ascend_edge_chat.voice.alsa.shutil.which", return_value="pactl"), patch(
            "ascend_edge_chat.voice.alsa.subprocess.run", return_value=completed
        ):
            unavailable = AlsaAudioIO(
                VoiceSettings(capture_plugin="pulse"),
                capture_device="hw:8,0",
                playback_device="hw:1,0",
            )
            with self.assertRaises(AudioDeviceError):
                unavailable.capture_command(2)

    def test_hardware_agc_respects_the_software_agc_switch(self) -> None:
        audio = AlsaAudioIO(
            VoiceSettings(auto_gain_enabled=False),
            capture_device="hw:1,0",
            playback_device="hw:2,0",
        )
        with patch("ascend_edge_chat.voice.alsa.subprocess.run") as run:
            audio.enable_hardware_auto_gain()
        run.assert_not_called()

    def test_stereo_capture_downmixes_to_mono_pcm(self) -> None:
        stereo = (
            (1000).to_bytes(2, "little", signed=True)
            + (-1000).to_bytes(2, "little", signed=True)
            + (3000).to_bytes(2, "little", signed=True)
            + (1000).to_bytes(2, "little", signed=True)
        )
        self.assertEqual(downmix_pcm16le(stereo, 2), b"\x00\x00\xd0\x07")
        self.assertEqual(downmix_pcm16le(b"\x01\x00", 1), b"\x01\x00")
        with self.assertRaises(Exception):
            downmix_pcm16le(b"\x00", 2)

    def test_alsa_enumeration_exposes_stable_public_ids(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "pcm").write_text(
                "00-00: board-playback : board : playback 1\n"
                "00-01: board-capture : board : capture 1\n"
                "01-00: USB Audio : USB Audio : capture 1\n"
                "02-00: USB Audio : USB Audio : playback 1\n",
                encoding="utf-8",
            )
            (root / "cards").write_text(
                " 0 [ascend310b     ]: ascend310b - ascend310b\n"
                "                      ascend310b\n"
                " 1 [Webcam         ]: USB-Audio - C922 Pro Stream Webcam\n"
                "                      C922 Pro Stream Webcam at usb-xhci\n"
                " 2 [Pro            ]: USB-Audio - EDIFIER M16 Pro\n"
                "                      EDIFIER M16 Pro at usb-xhci\n",
                encoding="utf-8",
            )
            devices = enumerate_alsa_devices(root)
            public = discover_voice_devices(root)
        self.assertEqual(
            [item.hw_name for item in devices],
            ["hw:0,0", "hw:0,1", "hw:1,0", "hw:2,0"],
        )
        self.assertEqual(
            [item.id for item in public],
            [
                "alsa-playback-0-0",
                "alsa-capture-0-1",
                "alsa-capture-1-0",
                "alsa-playback-2-0",
            ],
        )
        self.assertEqual(public[0].backend_id, "hw:0,0")
        self.assertEqual(public[2].model, "C922 Pro Stream Webcam")
        self.assertIn("C922 Pro Stream Webcam / USB Audio", public[2].label)
        self.assertEqual(public[3].model, "EDIFIER M16 Pro")
        self.assertIn("EDIFIER M16 Pro / USB Audio", public[3].label)

    def test_energy_vad_requires_onset_then_silence(self) -> None:
        settings = VadSettings(speech_rms_threshold=100.0, start_speech_frames=2, stop_silence_ms=40)
        vad = EnergyVad(settings)
        silence = b"\x00\x00" * (settings.frame_bytes // 2)
        speech = (200).to_bytes(2, "little", signed=True) * (settings.frame_bytes // 2)
        self.assertEqual(pcm_rms(speech), 200.0)
        self.assertEqual(vad.feed(silence).state, VadState.WAITING)
        self.assertEqual(vad.feed(speech).state, VadState.WAITING)
        onset = vad.feed(speech)
        self.assertTrue(onset.started)
        self.assertEqual(onset.state, VadState.SPEECH)
        self.assertFalse(vad.feed(silence).complete)
        completed = vad.feed(silence)
        self.assertTrue(completed.complete)
        self.assertEqual(completed.state, VadState.COMPLETE)
        with self.assertRaises(RuntimeError):
            vad.feed(silence)

    def test_adaptive_vad_calibrates_noise_floor_before_speech(self) -> None:
        settings = VadSettings(
            speech_rms_threshold=100.0,
            start_speech_frames=2,
            stop_silence_ms=40,
            adaptive=True,
            noise_floor_initial_rms=20.0,
            noise_floor_multiplier=3.0,
            noise_floor_decay=0.5,
            noise_floor_calibration_frames=5,
        )
        vad = EnergyVad(settings)
        sample_count = settings.frame_bytes // 2
        background = (500).to_bytes(2, "little", signed=True) * sample_count
        speech = (2000).to_bytes(2, "little", signed=True) * sample_count
        for _ in range(5):
            decision = vad.feed(background)
            self.assertFalse(decision.started)
        below_floor = vad.feed(background)
        self.assertFalse(below_floor.voiced)
        self.assertGreaterEqual(below_floor.noise_floor, 500.0)
        self.assertGreaterEqual(below_floor.threshold, 1500.0)
        self.assertFalse(vad.feed(speech).started)
        self.assertTrue(vad.feed(speech).started)

    def test_agc_lifts_quiet_active_frames_and_leaves_silence_unchanged(self) -> None:
        frame_bytes = 640
        quiet = (60).to_bytes(2, "little", signed=True) * (frame_bytes // 2)
        speech = (300).to_bytes(2, "little", signed=True) * (frame_bytes // 2)
        result = apply_pcm16_agc(
            quiet * 10 + speech * 10,
            AgcSettings(target_rms=2400.0, max_gain=16.0),
        )
        self.assertGreater(result.gain, 1.0)
        self.assertGreater(result.output_rms, result.input_rms)
        self.assertGreater(result.output_rms, 1500.0)
        silence = b"\x00\x00" * frame_bytes
        unchanged = apply_pcm16_agc(silence, AgcSettings())
        self.assertEqual(unchanged.gain, 1.0)
        self.assertEqual(unchanged.pcm, silence)

    def test_vad_ends_silent_capture_at_duration_limit(self) -> None:
        settings = VadSettings(frame_ms=20, max_duration_seconds=1.0)
        vad = EnergyVad(settings)
        silence = b"\x00\x00" * (settings.frame_bytes // 2)
        decision = None
        for _ in range(settings.max_frames):
            decision = vad.feed(silence)
        self.assertIsNotNone(decision)
        self.assertTrue(decision.complete)
        self.assertEqual(decision.state, VadState.COMPLETE)

    def test_vad_rejects_invalid_frame_length(self) -> None:
        vad = EnergyVad()
        with self.assertRaises(ValueError):
            vad.feed(b"\x00")

    def test_push_to_talk_capture_returns_conditioned_mono_audio(self) -> None:
        async def exercise() -> None:
            settings = VoiceSettings(
                capture_device_channels=2,
                auto_gain_target_rms=500.0,
            )
            audio = AlsaAudioIO(
                settings,
                capture_device="hw:0,0",
                playback_device="hw:1,0",
            )
            stereo = (500).to_bytes(2, "little", signed=True) * 16_000
            process = _FinishedProcess(
                stereo,
                returncode=1,
                stderr=b"arecord: read error: Interrupted system call",
            )

            async def create_process(*args, **kwargs):
                del args, kwargs
                return process

            with patch(
                "ascend_edge_chat.voice.alsa.shutil.which",
                return_value="/usr/bin/arecord",
            ), patch(
                "ascend_edge_chat.voice.alsa.asyncio.create_subprocess_exec",
                new=create_process,
            ):
                recorder = audio.begin_push_to_talk(max_duration_seconds=2.0)
                await recorder.start()
                result = await recorder.stop()

            self.assertEqual(result.sample_rate_hz, 16_000)
            self.assertEqual(result.channels, 1)
            self.assertTrue(result.pcm)
            self.assertEqual(pcm_rms(result.pcm), 500.0)

        asyncio.run(exercise())

    def test_silent_capture_timeout_is_reported_as_no_speech(self) -> None:
        async def exercise() -> None:
            settings = VoiceSettings(capture_device_channels=1)
            audio = AlsaAudioIO(
                settings,
                capture_device="hw:1,0",
                playback_device="hw:2,0",
            )
            process = _StoppedProcess()

            async def create_process(*args, **kwargs):
                del args, kwargs
                return process

            start = asyncio.get_running_loop().time()
            with patch("ascend_edge_chat.voice.alsa.shutil.which", return_value="/usr/bin/arecord"), patch(
                "ascend_edge_chat.voice.alsa.asyncio.create_subprocess_exec",
                new=create_process,
            ):
                with self.assertRaises(NoSpeechDetected):
                    await audio.capture_utterance(max_duration_seconds=1.0)

            elapsed = asyncio.get_running_loop().time() - start
            self.assertTrue(process.interrupted)
            self.assertFalse(process.terminated)
            self.assertEqual(process.stdout.count, settings.capture_frame_bytes)
            self.assertLess(elapsed, 1.5)

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
