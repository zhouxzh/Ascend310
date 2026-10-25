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
    downmix_pcm16le,
    discover_voice_devices,
    enumerate_alsa_devices,
)
from ascend_edge_chat.voice.config import VoiceConfigurationError, VoiceSettings
from ascend_edge_chat.voice.vad import EnergyVad, VadSettings, VadState, pcm_rms


class _BlockedStdout:
    async def readexactly(self, count: int) -> bytes:
        self.count = count
        await asyncio.Event().wait()
        return b""


class _StoppedProcess:
    def __init__(self) -> None:
        self.returncode = None
        self.stdout = _BlockedStdout()
        self.stderr = None
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0

    async def wait(self) -> int:
        return 0


class VoiceCoreTests(unittest.TestCase):
    def test_voice_settings_only_accept_loopback_gateway(self) -> None:
        settings = VoiceSettings.from_environ(
            {
                "ASCEND_EDGE_CHAT_VOICE_GATEWAY_URL": "http://127.0.0.1:7861/v1",
                "ASCEND_EDGE_CHAT_VOICE_GATEWAY_API_KEY": "a" * 24,
            }
        )
        self.assertEqual(settings.capture_frame_bytes, 640)
        self.assertEqual(settings.initial_mode, "disabled")
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
        self.assertIn("--device=hw:1,0", audio.capture_command(2))
        self.assertIn("--device=plughw:2,0", audio.playback_command(22_050))
        self.assertEqual(audio.suspended_playback_command(22_050)[:3], ("pasuspender", "--", "aplay"))
        self.assertIn("--device=pulse", audio.pulse_playback_command(22_050))

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
                "00-01: board-capture : board : capture 1\n",
                encoding="utf-8",
            )
            devices = enumerate_alsa_devices(root)
            public = discover_voice_devices(root)
        self.assertEqual([item.hw_name for item in devices], ["hw:0,0", "hw:0,1"])
        self.assertEqual([item.id for item in public], ["alsa-playback-0-0", "alsa-capture-0-1"])
        self.assertEqual(public[0].backend_id, "hw:0,0")

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

    def test_capture_timeout_stops_arecord(self) -> None:
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
                with self.assertRaisesRegex(AudioDeviceError, "capture timed out"):
                    await audio.capture_utterance(max_duration_seconds=1.0)

            elapsed = asyncio.get_running_loop().time() - start
            self.assertTrue(process.terminated)
            self.assertEqual(process.stdout.count, settings.capture_frame_bytes)
            self.assertLess(elapsed, 1.5)

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
