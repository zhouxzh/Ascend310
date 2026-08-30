from __future__ import annotations

from collections.abc import AsyncIterator
import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from local_app import LatencyMetrics, LocalSettings, create_local_app


class FakeCapture:
    def __init__(self) -> None:
        self.stopped = False

    async def stop(self) -> bytes:
        self.stopped = True
        return b"\x00\x00" * 20


@dataclass
class FakeAudioSettings:
    source: str = "fake-source"
    sink: str = "fake-sink"
    sample_rate: int = 16_000


class FakeAudio:
    def __init__(self) -> None:
        self.settings = FakeAudioSettings()
        self.capture = FakeCapture()
        self.played: list[tuple[bytes, int]] = []

    async def start_capture(self) -> FakeCapture:
        return self.capture

    async def play_pcm(self, pcm: bytes, sample_rate: int = None) -> None:
        self.played.append((pcm, sample_rate))


class FakeRecognizer:
    async def transcribe(self, pcm: bytes, sample_rate: int) -> str:
        if not pcm:
            return ""
        return "语音输入"


class FakeSynthesizer:
    sample_rate = 22_050

    async def synthesize(self, text: str) -> bytes:
        return b"\x00\x00" * max(1, len(text))


class FakeLLM:
    def __init__(self) -> None:
        self.requests: list[list[dict[str, str]]] = []
        self.closed = False

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        self.requests.append(messages)
        yield "本地回复。"
        yield "第二句"

    async def aclose(self) -> None:
        self.closed = True


def receive_until(websocket, event_type: str) -> list[dict]:
    events = []
    for _ in range(12):
        event = websocket.receive_json()
        events.append(event)
        if event.get("type") == event_type:
            return events
    raise AssertionError("did not receive event " + event_type)


class LocalAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.audio = FakeAudio()
        self.llm = FakeLLM()
        self.app = create_local_app(
            LocalSettings(
                frontend_dir=Path(self.temp_dir.name),
                max_capture_seconds=30.0,
            ),
            audio=self.audio,
            recognizer=FakeRecognizer(),
            synthesizer=FakeSynthesizer(),
            llm=self.llm,
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_health_marks_the_service_as_unauthenticated_experiment(self) -> None:
        with TestClient(self.app) as client:
            response = client.get("/health")
            metrics = client.get("/api/metrics")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"), "no-store")
        self.assertEqual(metrics.headers.get("cache-control"), "no-store")
        self.assertEqual(response.json()["mode"], "unauthenticated-lan-experiment")
        self.assertIn("未启用 API 鉴权", response.json()["warning"])
        self.assertTrue(self.llm.closed)

    def test_environment_requires_a_loopback_gateway_and_server_side_token(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "GATEWAY_API_KEY"):
                LocalSettings.from_environ()
        with patch.dict(
            os.environ,
            {"GATEWAY_API_KEY": "test-token", "LOCAL_GATEWAY_URL": "https://example.test/v1"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "loopback"):
                LocalSettings.from_environ()
        with patch.dict(
            os.environ,
            {
                "GATEWAY_API_KEY": "valid-token-0123456789abcdef",
                "LOCAL_GATEWAY_URL": "http://127.0.0.1:7861/evil",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "loopback"):
                LocalSettings.from_environ()
        with patch.dict(
            os.environ,
            {
                "GATEWAY_API_KEY": "valid-token-0123456789abcdef",
                "LOCAL_GATEWAY_URL": "http://127.0.0.1:70000/v1",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "invalid port"):
                LocalSettings.from_environ()

    def test_direct_settings_validate_a_real_gateway_token(self) -> None:
        with self.assertRaisesRegex(ValueError, "real ASCII token"):
            LocalSettings(gateway_api_key="short-token")
        settings = LocalSettings(gateway_api_key="valid-token-0123456789abcdef")
        self.assertEqual(settings.gateway_url, "http://127.0.0.1:7861/v1")

    def test_environment_caps_tinyllama_prompt_budget(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GATEWAY_API_KEY": "valid-token-0123456789abcdef",
                "LOCAL_MAX_CHARACTERS": "769",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "768"):
                LocalSettings.from_environ()

        with patch.dict(
            os.environ,
            {
                "GATEWAY_API_KEY": "valid-token-0123456789abcdef",
                "LOCAL_MAX_MESSAGES": "5",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "between 2 and 4"):
                LocalSettings.from_environ()

    def test_text_message_streams_deltas_plays_sentences_and_keeps_memory(self) -> None:
        with TestClient(self.app) as client:
            with client.websocket_connect("/api/ws") as websocket:
                ready = websocket.receive_json()
                self.assertEqual(ready["type"], "ready")
                websocket.send_json({"type": "text", "text": "你好"})
                events = receive_until(websocket, "done")
                self.assertEqual(events[-1]["text"], "本地回复。第二句")
                latency = events[-1]["latency_ms"]
                self.assertIsNone(latency["asr_completion"])
                self.assertIsNotNone(latency["llm_first_token"])
                self.assertIsNotNone(latency["llm_completion"])
                self.assertIsNotNone(latency["tts_first_audio"])
                self.assertIsNotNone(latency["total"])
                self.assertTrue(any(event.get("type") == "delta" for event in events))
                self.assertTrue(any(event.get("state") == "playing" for event in events))
                websocket.send_json({"type": "clear"})
                clear_events = receive_until(websocket, "cleared")
            metrics = client.get("/api/metrics").json()

        self.assertEqual(clear_events[-1]["type"], "cleared")
        self.assertEqual(self.llm.requests[0], [{"role": "user", "content": "你好"}])
        self.assertEqual(len(self.audio.played), 2)
        self.assertEqual(metrics["completed_operations"], 1)
        self.assertEqual(metrics["latency_ms"]["asr_completion"]["count"], 0)
        self.assertEqual(metrics["latency_ms"]["total"]["count"], 1)

    def test_failed_text_generation_does_not_commit_a_partial_turn(self) -> None:
        class FailingLLM(FakeLLM):
            async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
                self.requests.append(messages)
                yield "partial"
                raise RuntimeError("synthetic stream failure")

        failing = FailingLLM()
        app = create_local_app(
            LocalSettings(frontend_dir=Path(self.temp_dir.name)),
            audio=self.audio,
            recognizer=FakeRecognizer(),
            synthesizer=FakeSynthesizer(),
            llm=failing,
        )
        with TestClient(app) as client:
            with client.websocket_connect("/api/ws") as websocket:
                websocket.receive_json()
                websocket.send_json({"type": "text", "text": "不会提交"})
                events = receive_until(websocket, "error")
                self.assertEqual(events[-1]["type"], "error")
                self.assertEqual(len(app.state.sessions._sessions), 1)
                conversation = next(iter(app.state.sessions._sessions.values()))
                self.assertEqual(conversation.snapshot(), [])

    def test_oversized_text_delta_is_rejected_before_tts_or_commit(self) -> None:
        class OversizedLLM(FakeLLM):
            async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
                self.requests.append(messages)
                yield "x" * 701

        oversized = OversizedLLM()
        app = create_local_app(
            LocalSettings(frontend_dir=Path(self.temp_dir.name), max_characters=700),
            audio=self.audio,
            recognizer=FakeRecognizer(),
            synthesizer=FakeSynthesizer(),
            llm=oversized,
        )
        with TestClient(app) as client:
            with client.websocket_connect("/api/ws") as websocket:
                websocket.receive_json()
                websocket.send_json({"type": "text", "text": "超长回复"})
                events = receive_until(websocket, "error")
                self.assertEqual(events[-1]["type"], "error")
                self.assertIn("会话字符限制", events[-1]["message"])
                conversation = next(iter(app.state.sessions._sessions.values()))
                self.assertEqual(conversation.snapshot(), [])
        self.assertEqual(self.audio.played, [])

    def test_silent_disconnect_cancels_a_stalled_text_generation(self) -> None:
        class BlockingLLM(FakeLLM):
            def __init__(self) -> None:
                super().__init__()
                self.started = Event()
                self.cancelled = Event()

            async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
                self.requests.append(messages)
                self.started.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    self.cancelled.set()
                    raise
                if False:
                    yield ""

        blocking = BlockingLLM()
        app = create_local_app(
            LocalSettings(frontend_dir=Path(self.temp_dir.name)),
            audio=self.audio,
            recognizer=FakeRecognizer(),
            synthesizer=FakeSynthesizer(),
            llm=blocking,
        )
        with TestClient(app) as client:
            with client.websocket_connect("/api/ws") as websocket:
                websocket.receive_json()
                websocket.send_json({"type": "text", "text": "等待取消"})
                self.assertTrue(blocking.started.wait(2))
                websocket.close()

        self.assertTrue(blocking.cancelled.wait(2))
        self.assertTrue(blocking.closed)

    def test_total_llm_deadline_rolls_back_text_turn(self) -> None:
        class SlowLLM(FakeLLM):
            async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
                self.requests.append(messages)
                await asyncio.sleep(0.2)
                yield "不会到达"

        slow = SlowLLM()
        app = create_local_app(
            LocalSettings(
                frontend_dir=Path(self.temp_dir.name),
                llm_timeout_seconds=0.1,
            ),
            audio=self.audio,
            recognizer=FakeRecognizer(),
            synthesizer=FakeSynthesizer(),
            llm=slow,
        )
        with TestClient(app) as client:
            with client.websocket_connect("/api/ws") as websocket:
                websocket.receive_json()
                websocket.send_json({"type": "text", "text": "超时"})
                events = receive_until(websocket, "error")
                self.assertIn("超时", events[-1]["message"])
                conversation = next(iter(app.state.sessions._sessions.values()))
                self.assertEqual(conversation.snapshot(), [])

    def test_ptt_stops_capture_then_transcribes_and_generates(self) -> None:
        with TestClient(self.app) as client:
            with client.websocket_connect("/api/ws") as websocket:
                websocket.receive_json()
                websocket.send_json({"type": "ptt_start"})
                recording_events = receive_until(websocket, "state")
                self.assertEqual(recording_events[-1]["state"], "recording")
                websocket.send_json({"type": "ptt_stop"})
                events = receive_until(websocket, "done")

        self.assertTrue(self.audio.capture.stopped)
        self.assertTrue(any(event.get("type") == "transcript" for event in events))
        self.assertEqual(self.llm.requests[0][0]["content"], "语音输入")
        self.assertIsNotNone(events[-1]["latency_ms"]["asr_completion"])

    def test_disconnect_releases_an_active_capture(self) -> None:
        with TestClient(self.app) as client:
            with client.websocket_connect("/api/ws") as websocket:
                websocket.receive_json()
                websocket.send_json({"type": "ptt_start"})
                receive_until(websocket, "state")

        self.assertTrue(self.audio.capture.stopped)

    def test_websocket_rejects_an_oversized_raw_frame_before_json_parse(self) -> None:
        app = create_local_app(
            LocalSettings(
                frontend_dir=Path(self.temp_dir.name),
                max_websocket_message_bytes=4_096,
            ),
            audio=self.audio,
            recognizer=FakeRecognizer(),
            synthesizer=FakeSynthesizer(),
            llm=self.llm,
        )
        with TestClient(app) as client:
            with self.assertRaises(WebSocketDisconnect) as context:
                with client.websocket_connect("/api/ws") as websocket:
                    websocket.receive_json()
                    websocket.send_text("{" + ("x" * 8_000) + "}")
                    websocket.receive_json()
        self.assertEqual(context.exception.code, 1009)


class LatencyMetricsTests(unittest.TestCase):
    def test_keeps_only_a_bounded_numeric_window(self) -> None:
        metrics = LatencyMetrics(sample_limit=3)
        for total in (1.0, 2.0, 3.0, 4.0):
            metrics.record(
                {
                    "asr_completion": None,
                    "llm_first_token": total,
                    "llm_completion": total,
                    "tts_first_audio": total,
                    "total": total,
                }
            )

        snapshot = metrics.snapshot()
        self.assertEqual(snapshot["completed_operations"], 4)
        self.assertEqual(snapshot["latency_ms"]["total"], {"count": 3, "p50": 3.0, "p95": 4.0})
        self.assertEqual(snapshot["latency_ms"]["asr_completion"], {"count": 0, "p50": None, "p95": None})
