from __future__ import annotations

import asyncio
import io
import unittest
import wave
from unittest.mock import patch

from fastapi.testclient import TestClient

from ascend_edge_chat.voice.agent import (
    VoiceAgent,
    VoiceAgentError,
    create_default_agent,
    create_voice_agent_app,
)
from ascend_edge_chat.voice.adapters import UnavailableRecognizer, UnavailableSynthesizer
from ascend_edge_chat.voice.alsa import AudioDeviceError, NoSpeechDetected
from ascend_edge_chat.voice.config import VoiceSettings
from ascend_edge_chat.voice.protocol import (
    SynthesizedAudio,
    VoiceDevice,
    VoiceMode,
    VoiceTurn,
    VoiceTurnState,
)


class _Recognizer:
    async def transcribe(self, pcm: bytes, *, sample_rate_hz: int) -> str:
        assert sample_rate_hz == 16_000
        assert pcm
        return "你好，开发板"


class _EmptyRecognizer(_Recognizer):
    async def transcribe(self, pcm: bytes, *, sample_rate_hz: int) -> str:
        assert sample_rate_hz == 16_000
        assert pcm
        return ""


class _Synthesizer:
    async def synthesize(self, text: str) -> SynthesizedAudio:
        assert text
        return SynthesizedAudio(pcm=b"\x00\x00" * 160, sample_rate_hz=22_050)


class _Chat:
    async def complete(self, text: str) -> str:
        return "收到：" + text


class _FailingAudio:
    async def capture_utterance(self, *, max_duration_seconds: float) -> bytes:
        del max_duration_seconds
        raise AudioDeviceError("capture failed")

    async def play_pcm(self, audio: SynthesizedAudio) -> None:
        del audio


class _NoSpeechAudio(_FailingAudio):
    async def capture_utterance(self, *, max_duration_seconds: float) -> bytes:
        del max_duration_seconds
        raise NoSpeechDetected("no speech detected before listen window ended")


class _PushToTalkRecorder:
    def __init__(self) -> None:
        self.started = False
        self.stopped = False
        self.cancelled = False

    async def start(self) -> None:
        self.started = True

    async def stop(self) -> SynthesizedAudio:
        self.stopped = True
        return SynthesizedAudio(pcm=b"\x00\x00" * 8_000, sample_rate_hz=16_000)

    async def cancel(self) -> None:
        self.cancelled = True


class _AudioTestIO:
    def __init__(self) -> None:
        self.recorder = _PushToTalkRecorder()
        self.played = []

    def begin_push_to_talk(self, *, max_duration_seconds: float) -> _PushToTalkRecorder:
        assert max_duration_seconds == 10.0
        return self.recorder

    async def play_pcm(self, audio: SynthesizedAudio) -> None:
        self.played.append(audio)


class _BlockingPushToTalkRecorder(_PushToTalkRecorder):
    def __init__(self) -> None:
        super().__init__()
        self.start_entered = asyncio.Event()
        self.release_start = asyncio.Event()

    async def start(self) -> None:
        self.start_entered.set()
        await self.release_start.wait()
        await super().start()


class _BlockingAudioTestIO(_AudioTestIO):
    def __init__(self) -> None:
        super().__init__()
        self.playback_started = asyncio.Event()
        self.release_playback = asyncio.Event()
        self.playback_cancelled = False

    async def play_pcm(self, audio: SynthesizedAudio) -> None:
        self.played.append(audio)
        self.playback_started.set()
        try:
            await self.release_playback.wait()
        except asyncio.CancelledError:
            self.playback_cancelled = True
            raise


def _wav() -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16_000)
        stream.writeframes(b"\x01\x00" * 320)
    return output.getvalue()


def _agent() -> VoiceAgent:
    settings = VoiceSettings(gateway_api_key="gateway-token-0123456789")
    devices = [
        VoiceDevice("input-0", "USB microphone", "capture", "hw:1,0"),
        VoiceDevice("output-0", "USB speaker", "playback", "hw:1,0"),
    ]
    return VoiceAgent(
        settings,
        recognizer=_Recognizer(),
        synthesizer=_Synthesizer(),
        chat=_Chat(),
        devices=devices,
        device_reader=lambda: list(devices),
    )


class VoiceAgentTests(unittest.TestCase):
    def test_initial_browser_mode_is_process_owned_and_needs_no_device(self) -> None:
        settings = VoiceSettings(
            gateway_api_key="gateway-token-0123456789",
            initial_mode="browser",
        )
        agent = VoiceAgent(
            settings,
            recognizer=_Recognizer(),
            synthesizer=_Synthesizer(),
            chat=_Chat(),
            devices=[],
        )
        self.assertEqual(agent.public_config()["mode"], "browser")
        self.assertEqual(agent.health()["mode"], "browser")

    def test_status_keeps_last_completed_turn_visible_during_next_listen(self) -> None:
        agent = _agent()
        completed_id = "completed-turn"
        completed = VoiceTurn(
            completed_id,
            VoiceTurnState.COMPLETED,
            1,
            2,
            transcript="你好",
            reply="你好，有什么可以帮你？",
        )
        active = VoiceTurn("active-turn", VoiceTurnState.LISTENING, 3, 3)
        agent._turns[completed_id] = completed
        agent._turns[active.id] = active
        agent._active_turn_id = active.id
        status = agent.status()
        self.assertEqual(status["active_turn"]["state"], "listening")
        self.assertEqual(
            status["last_completed_turn"]["transcript"],
            "你好",
        )

    def test_default_sherpa_selection_reports_unavailable_without_importing_models(self) -> None:
        settings = VoiceSettings(
            gateway_api_key="gateway-token-0123456789",
            speech_backend="sherpa-onnx",
            sherpa_asr_model_dir="C:/not-admitted/asr",
            sherpa_tts_model_dir="C:/not-admitted/tts",
        )
        agent = create_default_agent(settings)
        config = agent.public_config()
        self.assertFalse(config["ready"])
        self.assertEqual(config["speech"]["backend"], "sherpa-onnx")
        self.assertFalse(config["speech"]["asr_ready"])
        self.assertIn("sherpa", config["speech"]["asr_reason"].lower())
        self.assertNotIn("C:/not-admitted", config["speech"]["asr_reason"])

    def test_unavailable_speech_backend_returns_controlled_error(self) -> None:
        settings = VoiceSettings(
            gateway_api_key="gateway-token-0123456789",
            initial_mode="browser",
        )
        agent = VoiceAgent(
            settings,
            recognizer=UnavailableRecognizer(),
            synthesizer=UnavailableSynthesizer(),
            chat=_Chat(),
            devices=[],
        )
        app = create_voice_agent_app(agent=agent)
        with TestClient(app, raise_server_exceptions=False) as client:
            transcript = client.post(
                "/v1/voice/browser/transcribe",
                content=_wav(),
                headers={"content-type": "audio/wav"},
            )
            synthesis = client.post(
                "/v1/voice/browser/synthesize",
                json={"text": "测试"},
            )
        self.assertEqual(transcript.status_code, 409)
        self.assertIn("ASR backend is unavailable", transcript.json()["detail"])
        self.assertEqual(synthesis.status_code, 409)
        self.assertIn("TTS backend is unavailable", synthesis.json()["detail"])

    def test_device_list_refreshes_after_usb_hotplug(self) -> None:
        agent = _agent()
        replacement = [
            VoiceDevice(
                "input-new",
                "New Camera / USB Audio (hw:3,0)",
                "capture",
                "hw:3,0",
                model="New Camera",
            ),
            VoiceDevice(
                "output-new",
                "USB Speaker / USB Audio (hw:2,0)",
                "playback",
                "hw:2,0",
                model="USB Speaker",
            ),
        ]
        agent._device_reader = lambda: list(replacement)
        config = agent.public_config()

        self.assertEqual(
            [item["id"] for item in config["inputs"]],
            ["input-new"],
        )
        self.assertEqual(config["inputs"][0]["model"], "New Camera")

    def test_config_only_accepts_mode_device_ids_and_auto_listen(self) -> None:
        agent = _agent()
        app = create_voice_agent_app(agent=agent)
        with TestClient(app) as client:
            initial = client.get("/api/config")
            selected = client.put(
                "/api/config",
                json={"mode": "browser", "input_id": "input-0", "output_id": "output-0", "auto_listen": True},
            )
            rejected = client.put("/api/config", json={"mode": "browser", "capture_device": "hw:99,0"})
            unknown = client.put("/api/config", json={"mode": "browser", "input_id": "not-enumerated"})
        self.assertEqual(initial.status_code, 200)
        self.assertEqual(initial.json()["mode"], "disabled")
        self.assertEqual(selected.status_code, 200)
        self.assertEqual(selected.json()["mode"], "browser")
        self.assertFalse(selected.json()["auto_listen"])
        self.assertEqual({item["id"] for item in selected.json()["inputs"]}, {"input-0"})
        self.assertEqual(rejected.status_code, 422)
        self.assertEqual(unknown.status_code, 409)

    def test_browser_mode_allows_bounded_wav_but_board_mode_rejects_it(self) -> None:
        agent = _agent()
        app = create_voice_agent_app(agent=agent)
        with TestClient(app) as client:
            client.put("/api/config", json={"mode": "browser"})
            transcript = client.post(
                "/v1/voice/browser/transcribe", content=_wav(), headers={"content-type": "audio/wav"}
            )
            audio = client.post("/v1/voice/browser/synthesize", json={"text": "你好"})
            board = client.put(
                "/api/config",
                json={"mode": "board", "input_id": "input-0", "output_id": "output-0", "auto_listen": False},
            )
            blocked = client.post(
                "/v1/voice/browser/transcribe", content=_wav(), headers={"content-type": "audio/wav"}
            )
        self.assertEqual(transcript.status_code, 200)
        self.assertEqual(transcript.json()["transcript"], "你好，开发板")
        self.assertEqual(audio.status_code, 200)
        self.assertEqual(audio.headers["content-type"], "audio/wav")
        self.assertGreater(len(audio.content), 44)
        self.assertEqual(board.status_code, 200)
        self.assertEqual(board.json()["mode"], "board")
        self.assertEqual(blocked.status_code, 409)

    def test_board_mode_rejects_unknown_device_and_status_stays_sanitized(self) -> None:
        agent = _agent()
        app = create_voice_agent_app(agent=agent)
        with TestClient(app) as client:
            rejected = client.put("/api/config", json={"mode": "board", "input_id": "unsafe", "output_id": "output-0"})
            status = client.get("/api/status")
            health = client.get("/health")
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(status.status_code, 200)
        self.assertNotIn("hw:1,0", str(status.json()))
        self.assertEqual(health.status_code, 200)
        self.assertEqual(health.json()["mode"], "disabled")

    def test_loopback_config_needs_no_voice_operator_token(self) -> None:
        agent = _agent()
        app = create_voice_agent_app(agent=agent)
        with TestClient(app) as client:
            accepted = client.put("/api/config", json={"mode": "browser"})
            rejected = client.put(
                "/api/config",
                json={"mode": "browser", "operator_token": "must-not-be-a-public-field"},
            )
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(rejected.status_code, 422)

    def test_turn_history_evicts_old_terminal_turns(self) -> None:
        agent = _agent()
        latest_id = ""
        for _ in range(36):
            turn = agent._new_turn()
            latest_id = turn.id
            agent._update(turn, VoiceTurnState.COMPLETED)
            agent._active_turn_id = None

        self.assertEqual(len(agent._turns), 32)
        self.assertIn(latest_id, agent._turns)
        self.assertEqual(agent.status()["latest_turn"]["id"], latest_id)

    def test_push_to_talk_records_and_replays_without_asr_or_tts(self) -> None:
        async def exercise() -> None:
            agent = _agent()
            agent._mode = VoiceMode.BOARD
            agent._input_id = "input-0"
            agent._output_id = "output-0"
            io = _AudioTestIO()
            agent._selected_audio = lambda: io  # type: ignore[method-assign]

            started = await agent.start_audio_test()
            self.assertEqual(started["state"], "recording")
            self.assertTrue(io.recorder.started)
            self.assertFalse(agent._auto_listen)

            completed = await agent.stop_audio_test()
            self.assertEqual(completed["state"], "completed")
            self.assertTrue(completed["played"])
            self.assertEqual(completed["duration_seconds"], 0.5)
            self.assertTrue(io.recorder.stopped)
            self.assertEqual(len(io.played), 1)

        asyncio.run(exercise())

    def test_concurrent_audio_test_start_owns_only_one_recorder(self) -> None:
        async def exercise() -> None:
            agent = _agent()
            agent._mode = VoiceMode.BOARD
            agent._input_id = "input-0"
            agent._output_id = "output-0"
            io = _AudioTestIO()
            recorder = _BlockingPushToTalkRecorder()
            io.recorder = recorder
            agent._selected_audio = lambda: io  # type: ignore[method-assign]

            first = asyncio.create_task(agent.start_audio_test())
            await recorder.start_entered.wait()
            second = asyncio.create_task(agent.start_audio_test())
            recorder.release_start.set()
            self.assertEqual((await first)["state"], "recording")
            with self.assertRaises(VoiceAgentError):
                await second
            self.assertIs(agent._audio_test_recorder, recorder)
            await agent.cancel_audio_test()
            self.assertTrue(recorder.cancelled)

        asyncio.run(exercise())

    def test_cancel_audio_test_cancels_active_playback(self) -> None:
        async def exercise() -> None:
            agent = _agent()
            agent._mode = VoiceMode.BOARD
            agent._input_id = "input-0"
            agent._output_id = "output-0"
            io = _BlockingAudioTestIO()
            agent._selected_audio = lambda: io  # type: ignore[method-assign]
            await agent.start_audio_test()
            stop = asyncio.create_task(agent.stop_audio_test())
            await io.playback_started.wait()
            await agent.cancel_audio_test()
            with self.assertRaises(VoiceAgentError):
                await stop
            self.assertTrue(io.playback_cancelled)
            self.assertIsNone(agent._audio_playback_task)

        asyncio.run(exercise())

    def test_hotplug_clears_selected_devices_and_disables_board_listen(self) -> None:
        agent = _agent()
        agent._mode = VoiceMode.BOARD
        agent._input_id = "input-0"
        agent._output_id = "output-0"
        agent._auto_listen = True
        agent._device_reader = lambda: []
        config = agent.public_config()
        self.assertIsNone(config["input_id"])
        self.assertIsNone(config["output_id"])
        self.assertFalse(config["auto_listen"])
        self.assertIn("disconnected", config["device_error"])

    def test_audio_test_endpoints_expose_start_stop_lifecycle(self) -> None:
        agent = _agent()
        io = _AudioTestIO()
        agent._selected_audio = lambda: io  # type: ignore[method-assign]
        app = create_voice_agent_app(agent=agent)
        with TestClient(app) as client:
            configured = client.put(
                "/api/config",
                json={
                    "mode": "board",
                    "input_id": "input-0",
                    "output_id": "output-0",
                    "auto_listen": False,
                },
            )
            started = client.post("/v1/voice/audio-test/start")
            stopped = client.post("/v1/voice/audio-test/stop")
        self.assertEqual(configured.status_code, 200)
        self.assertEqual(started.status_code, 200)
        self.assertEqual(started.json()["state"], "recording")
        self.assertEqual(stopped.status_code, 200)
        self.assertEqual(stopped.json()["state"], "completed")
        self.assertEqual(len(io.played), 1)

    def test_auto_listen_stops_after_repeated_capture_failures(self) -> None:
        async def exercise() -> None:
            agent = _agent()
            agent._mode = VoiceMode.BOARD
            agent._input_id = "input-0"
            agent._output_id = "output-0"
            agent._auto_listen = True
            agent._selected_audio = lambda: _FailingAudio()  # type: ignore[method-assign]
            with patch(
                "ascend_edge_chat.voice.agent._AUTO_LISTEN_RETRY_BASE_SECONDS", 0.0
            ), patch(
                "ascend_edge_chat.voice.agent._AUTO_LISTEN_IDLE_SECONDS", 0.0
            ):
                await asyncio.wait_for(agent._run_forever(), timeout=1.0)

            self.assertFalse(agent._auto_listen)
            self.assertEqual(agent._auto_listen_failures, 3)
            self.assertEqual(len(agent._turns), 3)
            self.assertTrue(
                all(turn.state == VoiceTurnState.FAILED for turn in agent._turns.values())
            )

        asyncio.run(exercise())

    def test_auto_listen_keeps_waiting_after_a_silent_window(self) -> None:
        async def exercise() -> None:
            agent = _agent()
            agent._mode = VoiceMode.BOARD
            agent._input_id = "input-0"
            agent._output_id = "output-0"
            agent._auto_listen = True
            agent._selected_audio = lambda: _NoSpeechAudio()  # type: ignore[method-assign]
            with patch(
                "ascend_edge_chat.voice.agent._AUTO_LISTEN_RETRY_BASE_SECONDS", 0.0
            ), patch(
                "ascend_edge_chat.voice.agent._AUTO_LISTEN_IDLE_SECONDS", 0.0
            ):
                task = asyncio.create_task(agent._run_forever())
                await asyncio.sleep(0.05)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task

            self.assertTrue(agent._auto_listen)
            self.assertEqual(agent._auto_listen_failures, 0)
            terminal_turns = [turn for turn in agent._turns.values() if turn.terminal]
            self.assertTrue(terminal_turns)
            self.assertTrue(
                all(turn.error_code == "no_speech" for turn in terminal_turns)
            )

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
