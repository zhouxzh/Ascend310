from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
import io
import unittest
import wave

from fastapi.testclient import TestClient
import httpx

from ascend_edge_chat.ui.web import (
    LoopbackVoiceAgentClient,
    TextChatSettings,
    create_text_chat_app,
)


class _FakeLLM:
    last_finish_reason = "stop"

    async def stream(
        self, messages: list[dict[str, str]], *, max_tokens=None
    ) -> AsyncIterator[str]:
        del messages, max_tokens
        yield "文字路径仍可用。"

    async def aclose(self) -> None:
        return None


class _FakeVoiceAgent:
    def __init__(self) -> None:
        self.config_payload = {
            "mode": "board",
            "input_id": "usb-mic-1",
            "output_id": "usb-speaker-1",
            "auto_listen": False,
            "inputs": [
                {"id": "usb-mic-1", "label": "USB 麦克风"},
                {"id": "usb-mic-2", "label": "备用麦克风"},
            ],
            "outputs": [
                {"id": "usb-speaker-1", "label": "USB 音箱"},
            ],
            "capture": {"sample_rate_hz": 16000, "channels": 1, "frame_ms": 20},
            "playback": {"sample_rate_hz": 22050},
            "vad": {"enabled": True},
            "gateway": {"loopback": True, "configured": True},
        }
        self.status_payload = {
            "enabled": False,
            "ready": True,
            "state": "idle",
            "active_turn": None,
            "latest_turn": None,
            "speech": {
                "backend": "sherpa-onnx",
                "asr_ready": True,
                "tts_ready": True,
            },
        }
        self.configure_calls: list[dict[str, object]] = []
        self.transcribed: list[bytes] = []
        self.synthesized: list[str] = []

    async def health(self) -> Mapping[str, object]:
        return {"status": "ready", "mode": self.config_payload["mode"]}

    async def config(self) -> Mapping[str, object]:
        return self.config_payload

    async def status(self) -> Mapping[str, object]:
        return self.status_payload

    async def configure(self, payload: Mapping[str, object]) -> Mapping[str, object]:
        copied = dict(payload)
        self.configure_calls.append(copied)
        self.config_payload.update(copied)
        mode = self.config_payload["mode"]
        auto_listen = self.config_payload.get("auto_listen") is True
        self.status_payload["enabled"] = auto_listen if mode == "board" else False
        self.status_payload["state"] = "busy" if self.status_payload["enabled"] else "idle"
        return self.config_payload

    async def transcribe_browser_wav(self, wav: bytes) -> Mapping[str, object]:
        self.transcribed.append(wav)
        return {"transcript": "浏览器语音转写"}

    async def synthesize_browser_wav(self, text: str) -> bytes:
        self.synthesized.append(text)
        output = io.BytesIO()
        with wave.open(output, "wb") as sink:
            sink.setnchannels(1)
            sink.setsampwidth(2)
            sink.setframerate(22050)
            sink.writeframes(b"\x00\x00" * 128)
        return output.getvalue()

    async def aclose(self) -> None:
        return None


class _UnavailableVoiceAgent(_FakeVoiceAgent):
    async def health(self) -> Mapping[str, object]:
        raise RuntimeError("agent offline")


class VoiceUiControlPlaneTests(unittest.TestCase):
    def _headers(self, origin: str = "http://localhost") -> dict[str, str]:
        return {"Origin": origin}

    def _wav(self) -> bytes:
        output = io.BytesIO()
        with wave.open(output, "wb") as sink:
            sink.setnchannels(1)
            sink.setsampwidth(2)
            sink.setframerate(16000)
            sink.writeframes(b"\x00\x00" * 16000)
        return output.getvalue()

    def _app(self, voice_agent: _FakeVoiceAgent) -> object:
        return create_text_chat_app(
            TextChatSettings(gateway_api_key="server-only-token-0123456789"),
            llm=_FakeLLM(),
            voice_agent=voice_agent,
        )

    def test_ui_exposes_feature_gated_browser_voice_data_plane(self) -> None:
        agent = _FakeVoiceAgent()
        with TestClient(self._app(agent)) as client:
            page = client.get("/")
            config = client.get("/api/config")
            voice = client.get("/api/voice/config")

        self.assertEqual(page.status_code, 200)
        self.assertIn('id="voice-mode"', page.text)
        self.assertIn('id="voice-input"', page.text)
        self.assertIn('id="voice-output"', page.text)
        self.assertIn('id="voice-auto-listen"', page.text)
        self.assertIn('id="voice-self-test"', page.text)
        self.assertIn("录音回放自检（2 秒）", page.text)
        self.assertIn("playLocalBrowserWav", page.text)
        self.assertIn("purpose === 'self-test'", page.text)
        self.assertIn("captureDurationMs", page.text)
        self.assertIn("正在录音，2 秒后自动回放", page.text)
        self.assertIn("仅录音自检可用", page.text)
        self.assertIn("录音识别可用", page.text)
        self.assertIn("语音识别未配置", page.text)
        self.assertIn("浏览器录音和回放自检可用", page.text)
        self.assertIn("浏览器录音与识别可用", page.text)
        self.assertIn("asrUnavailable", page.text)
        self.assertIn("asr_ready", page.text)
        self.assertIn("browserSpeechOutputSupported", page.text)
        self.assertIn("SpeechSynthesisUtterance", page.text)
        self.assertIn("speakWithBrowser", page.text)
        self.assertIn("浏览器本地中文语音合成", page.text)
        self.assertIn("文字回复完成，浏览器语音播报完成", page.text)
        self.assertIn("TTS 模型未配置；本轮回复已完整显示为文字", page.text)
        self.assertIn("minimumBytes", page.text)
        self.assertIn("浏览器录音回放完成", page.text)
        self.assertIn("getUserMedia", page.text)
        self.assertIn("AudioContext", page.text)
        self.assertIn("audio/wav", page.text)
        self.assertIn("/api/voice/browser/transcribe", page.text)
        self.assertIn("/api/voice/browser/synthesize", page.text)
        self.assertIn("isSecureContext", page.text)
        self.assertEqual(config.status_code, 200)
        self.assertIn("voice", config.json())
        self.assertEqual(voice.status_code, 200)
        self.assertEqual(voice.json()["mode"], "board")
        self.assertEqual(voice.json()["input_devices"][0]["id"], "usb-mic-1")
        self.assertTrue(voice.json()["speech"]["asr_ready"])
        self.assertTrue(voice.json()["speech"]["tts_ready"])
        self.assertTrue(voice.json()["browser_audio"]["enabled"] is False)

    def test_board_control_accepts_only_agent_allowlist_ids(self) -> None:
        agent = _FakeVoiceAgent()
        with TestClient(self._app(agent)) as client:
            accepted = client.put(
                "/api/voice/config",
                headers=self._headers(),
                json={
                    "mode": "board",
                    "input_id": "usb-mic-2",
                    "output_id": "usb-speaker-1",
                    "auto_listen": True,
                },
            )
            forbidden = client.put(
                "/api/voice/config",
                headers=self._headers(),
                json={"mode": "board", "input_id": "/dev/snd/pcmC0D1c"},
            )
            unknown = client.put(
                "/api/voice/config",
                headers=self._headers(),
                json={"mode": "board", "input_id": "not-in-agent-allowlist"},
            )
            command = client.put(
                "/api/voice/config",
                headers=self._headers(),
                json={"mode": "board", "command": "arecord -D hw:0,1"},
            )

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["mode"], "board")
        self.assertTrue(accepted.json()["auto_listen"])
        self.assertTrue(accepted.json()["enabled"])
        self.assertEqual(agent.configure_calls[0]["input_id"], "usb-mic-2")
        self.assertEqual(forbidden.status_code, 400)
        self.assertEqual(unknown.status_code, 409)
        self.assertEqual(unknown.json()["error"]["type"], "voice_device_not_allowed")
        self.assertEqual(command.status_code, 400)
        self.assertEqual(len(agent.configure_calls), 1)

    def test_browser_mode_cannot_start_board_capture(self) -> None:
        agent = _FakeVoiceAgent()
        with TestClient(self._app(agent)) as client:
            rejected = client.put(
                "/api/voice/config",
                headers=self._headers(),
                json={"mode": "browser", "auto_listen": True},
            )
            accepted = client.put(
                "/api/voice/config",
                headers=self._headers(),
                json={"mode": "browser", "auto_listen": False},
            )

        self.assertEqual(rejected.status_code, 400)
        self.assertIn("不会启动板端采集", rejected.json()["error"]["message"])
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json()["mode"], "browser")
        self.assertFalse(accepted.json()["auto_listen"])
        self.assertFalse(accepted.json()["enabled"])
        self.assertTrue(accepted.json()["browser_audio"]["requires_https"])
        self.assertEqual(agent.configure_calls[-1]["mode"], "browser")
        self.assertFalse(agent.configure_calls[-1]["auto_listen"])

    def test_voice_agent_http_error_detail_is_preserved(self) -> None:
        response = httpx.Response(
            409,
            json={"detail": "ASR backend is unavailable; no speech model was loaded"},
        )
        self.assertEqual(
            LoopbackVoiceAgentClient._response_detail(response, "fallback"),
            "ASR backend is unavailable; no speech model was loaded",
        )
        self.assertEqual(
            LoopbackVoiceAgentClient._response_detail(
                httpx.Response(502, text="not-json"),
                "fallback",
            ),
            "fallback",
        )

    def test_browser_wav_proxy_requires_https_and_browser_mode(self) -> None:
        agent = _FakeVoiceAgent()
        wav = self._wav()
        with TestClient(self._app(agent)) as client:
            board_blocked = client.post(
                "/api/voice/browser/transcribe",
                headers={
                    **self._headers("https://voice.example"),
                    "Content-Type": "audio/wav",
                },
                content=wav,
            )
            switch = client.put(
                "/api/voice/config",
                headers=self._headers(),
                json={"mode": "browser", "auto_listen": False},
            )
            remote_http = client.post(
                "/api/voice/browser/transcribe",
                headers={
                    **self._headers("http://192.168.3.4"),
                    "Content-Type": "audio/wav",
                },
                content=wav,
            )
            transcript = client.post(
                "/api/voice/browser/transcribe",
                headers={
                    **self._headers("https://voice.example"),
                    "Content-Type": "audio/wav",
                },
                content=wav,
            )
            synth = client.post(
                "/api/voice/browser/synthesize",
                headers=self._headers("https://voice.example"),
                json={"text": "浏览器播报测试"},
            )

        self.assertEqual(board_blocked.status_code, 409)
        self.assertEqual(switch.status_code, 200)
        self.assertEqual(remote_http.status_code, 403)
        self.assertEqual(transcript.status_code, 200)
        self.assertEqual(transcript.json()["transcript"], "浏览器语音转写")
        self.assertNotIn("access-control-allow-origin", transcript.headers)
        self.assertEqual(synth.status_code, 200)
        self.assertEqual(synth.headers["content-type"], "audio/wav")
        self.assertNotIn("access-control-allow-origin", synth.headers)
        self.assertEqual(synth.headers["cache-control"], "no-store")
        self.assertEqual(agent.transcribed, [wav])
        self.assertEqual(agent.synthesized, ["浏览器播报测试"])

    def test_unavailable_voice_agent_does_not_break_existing_text_api(self) -> None:
        agent = _UnavailableVoiceAgent()
        with TestClient(self._app(agent)) as client:
            voice = client.get("/api/voice/status")
            chat = client.post("/api/chat", json={"message": "文字路径", "stream": False})

        self.assertEqual(voice.status_code, 200)
        self.assertFalse(voice.json()["available"])
        self.assertEqual(voice.json()["state"], "disabled")
        self.assertEqual(chat.status_code, 200)
        self.assertEqual(chat.json()["model"], "ascend-edge-chat")


if __name__ == "__main__":
    unittest.main()
