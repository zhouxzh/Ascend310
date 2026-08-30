"""Unauthenticated, board-local Chinese chat service.

This service is intentionally separate from :mod:`app`.  It owns the browser
WebSocket and local microphone/speaker path, while the existing case9 gateway
remains the authenticated OpenAI-compatible boundary used by XiaoZhi.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import string
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Optional, Protocol, TypeVar
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles

from audio_io import (
    AudioBackend,
    AudioError,
    AudioSettings,
    CaptureHandle,
    PulseAudioBackend,
    SherpaOnnxRecognizer,
    SherpaOnnxSynthesizer,
    SpeechRecognizer,
    SpeechRuntimeError,
    SpeechSynthesizer,
)
from local_session import (
    Conversation,
    ConversationStore,
    LocalLLMError,
    OpenAIChatClient,
    SessionLimitError,
    StreamingLLM,
)


LOGGER = logging.getLogger("case9.local_chat")
BASE_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = BASE_DIR / "frontend" / "dist"
SECURITY_WARNING = (
    "实验模式：此本地聊天接口未启用 API 鉴权；同一局域网内可访问主机的客户端 "
    "可能控制开发板麦克风和 USB 喇叭。请仅在可信实验网络中使用。"
)


class LocalServiceError(RuntimeError):
    """A user-visible local chat operation failure."""


class ClientDisconnectedError(LocalServiceError):
    """The browser went away while a local operation was running."""


_OperationResult = TypeVar("_OperationResult")


_LATENCY_FIELDS = (
    "asr_completion",
    "llm_first_token",
    "llm_completion",
    "tts_first_audio",
    "total",
)
_LATENCY_SAMPLE_LIMIT = 256


@dataclass
class OperationLatency:
    """Timing state for one successful text or PTT reply.

    The object contains only monotonic timestamps and derived durations.  It
    never receives transcript text, synthesized PCM, prompts, or responses.
    """

    started_at: float
    asr_completion: Optional[float] = None
    llm_first_token: Optional[float] = None
    llm_completion: Optional[float] = None
    tts_first_audio: Optional[float] = None

    def mark(self, field: str) -> None:
        if getattr(self, field) is None:
            setattr(self, field, _elapsed_milliseconds(self.started_at))

    def completed(self) -> dict[str, Optional[float]]:
        values = {
            "asr_completion": self.asr_completion,
            "llm_first_token": self.llm_first_token,
            "llm_completion": self.llm_completion,
            "tts_first_audio": self.tts_first_audio,
            "total": _elapsed_milliseconds(self.started_at),
        }
        return {
            name: None if value is None else round(value, 3)
            for name, value in values.items()
        }


class LatencyMetrics:
    """Bounded aggregate timing samples without user or audio content."""

    def __init__(self, sample_limit: int = _LATENCY_SAMPLE_LIMIT):
        if sample_limit < 1:
            raise ValueError("sample_limit must be positive")
        self._sample_limit = sample_limit
        self._completed_operations = 0
        self._samples = {
            field: deque(maxlen=sample_limit) for field in _LATENCY_FIELDS
        }
        self._lock = threading.Lock()

    def record(self, operation: dict[str, Optional[float]]) -> None:
        """Store numeric durations for one completed operation only."""

        with self._lock:
            self._completed_operations += 1
            for field in _LATENCY_FIELDS:
                value = operation.get(field)
                if value is not None:
                    self._samples[field].append(float(value))

    def snapshot(self) -> dict[str, Any]:
        """Return counts and p50/p95 values without any raw operation data."""

        with self._lock:
            latency: dict[str, dict[str, Optional[float]]] = {}
            for field in _LATENCY_FIELDS:
                values = sorted(self._samples[field])
                latency[field] = {
                    "count": len(values),
                    "p50": _percentile(values, 0.50),
                    "p95": _percentile(values, 0.95),
                }
            return {
                "completed_operations": self._completed_operations,
                "sample_limit": self._sample_limit,
                "latency_ms": latency,
            }


def _elapsed_milliseconds(started_at: float) -> float:
    return (time.perf_counter() - started_at) * 1000.0


def _percentile(values: list[float], fraction: float) -> Optional[float]:
    if not values:
        return None
    index = max(0, math.ceil(len(values) * fraction) - 1)
    return round(values[index], 3)


@dataclass(frozen=True)
class LocalSettings:
    host: str = "0.0.0.0"
    port: int = 7862
    gateway_url: str = "http://127.0.0.1:7861/v1"
    gateway_api_key: str = ""
    model: str = "case9-rag"
    llm_timeout_seconds: float = 90.0
    # Four messages (two user/assistant turns) leave room for TinyLlama's
    # role template inside its fixed 1024-token context.  A larger character
    # budget alone is not enough because each message adds template tokens.
    max_messages: int = 4
    # The active TinyLlama gateway accepts at most 768 aggregate characters;
    # keep audio transcripts and committed turns inside that budget too.
    max_characters: int = 700
    max_capture_seconds: float = 30.0
    max_websocket_message_bytes: int = 65_536
    frontend_dir: Path = FRONTEND_DIR

    def __post_init__(self) -> None:
        """Validate direct construction as strictly as environment loading.

        Tests may inject a fake LLM, but production callers can also construct
        settings programmatically. Keeping the loopback and TinyLlama bounds
        here prevents that path from bypassing the startup checks.
        """

        gateway_url = self.gateway_url.strip().rstrip("/")
        try:
            parsed_gateway = urlparse(gateway_url)
            gateway_port = parsed_gateway.port
        except ValueError as exc:
            raise ValueError("gateway_url has an invalid port or host") from exc
        if (
            parsed_gateway.scheme != "http"
            or parsed_gateway.hostname not in {"127.0.0.1", "::1", "localhost"}
            or parsed_gateway.path != "/v1"
            or gateway_port is None
            or not 1 <= gateway_port <= 65_535
            or parsed_gateway.username
            or parsed_gateway.password
            or parsed_gateway.query
            or parsed_gateway.fragment
        ):
            raise ValueError("gateway_url must be a loopback http URL without credentials")
        if self.model.strip() != "case9-rag":
            raise ValueError("model must be the fixed public model case9-rag")
        if self.gateway_api_key:
            allowed_key_characters = string.ascii_letters + string.digits + "-_.~"
            if (
                len(self.gateway_api_key) < 24
                or not self.gateway_api_key.isascii()
                or any(character not in allowed_key_characters for character in self.gateway_api_key)
                or self.gateway_api_key.lower().startswith("replace-with")
            ):
                raise ValueError("gateway_api_key must be a real ASCII token with at least 24 characters")
        if not 1 <= self.port <= 65_535:
            raise ValueError("port must be between 1 and 65535")
        if not 0.1 <= self.llm_timeout_seconds <= 300.0 or not math.isfinite(self.llm_timeout_seconds):
            raise ValueError("llm_timeout_seconds must be between 0.1 and 300")
        if not 2 <= self.max_messages <= 4:
            raise ValueError("max_messages must be between 2 and 4 for TinyLlama")
        if not 1 <= self.max_characters <= 768:
            raise ValueError("max_characters must be between 1 and 768 for TinyLlama")
        if not 0.1 <= self.max_capture_seconds <= 30.0 or not math.isfinite(self.max_capture_seconds):
            raise ValueError("max_capture_seconds must be between 0.1 and 30")
        if not 4_096 <= self.max_websocket_message_bytes <= 262_144:
            raise ValueError("max_websocket_message_bytes must be between 4096 and 262144")
        object.__setattr__(self, "gateway_url", gateway_url)

    @classmethod
    def from_environ(cls) -> "LocalSettings":
        def integer(name: str, default: int, minimum: int) -> int:
            raw = os.environ.get(name, str(default)).strip()
            try:
                value = int(raw)
            except ValueError as exc:
                raise ValueError(f"{name} must be an integer") from exc
            if value < minimum:
                raise ValueError(f"{name} must be at least {minimum}")
            return value

        def decimal(name: str, default: float, minimum: float) -> float:
            raw = os.environ.get(name, str(default)).strip()
            try:
                value = float(raw)
            except ValueError as exc:
                raise ValueError(f"{name} must be a number") from exc
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
            if value < minimum:
                raise ValueError(f"{name} must be at least {minimum}")
            return value

        gateway_url = os.environ.get("LOCAL_GATEWAY_URL", "http://127.0.0.1:7861/v1").strip().rstrip("/")
        try:
            parsed_gateway = urlparse(gateway_url)
            gateway_port = parsed_gateway.port
        except ValueError as exc:
            raise ValueError("LOCAL_GATEWAY_URL has an invalid port or host") from exc
        if (
            parsed_gateway.scheme != "http"
            or parsed_gateway.hostname not in {"127.0.0.1", "::1", "localhost"}
            or parsed_gateway.path != "/v1"
            or gateway_port is None
            or not 1 <= gateway_port <= 65_535
            or parsed_gateway.username
            or parsed_gateway.password
            or parsed_gateway.query
            or parsed_gateway.fragment
        ):
            raise ValueError("LOCAL_GATEWAY_URL must be a loopback http URL without credentials")
        gateway_api_key = os.environ.get(
            "LOCAL_GATEWAY_API_KEY", os.environ.get("GATEWAY_API_KEY", "")
        ).strip()
        if not gateway_api_key:
            raise ValueError("LOCAL_GATEWAY_API_KEY or GATEWAY_API_KEY must be set")
        frontend = os.environ.get("LOCAL_FRONTEND_DIR", str(FRONTEND_DIR)).strip()
        max_capture_seconds = decimal("AUDIO_MAX_DURATION_SECONDS", 30.0, 0.1)
        if max_capture_seconds > 30.0:
            raise ValueError("AUDIO_MAX_DURATION_SECONDS must not exceed 30")
        max_characters = integer("LOCAL_MAX_CHARACTERS", 700, 1)
        if max_characters > 768:
            raise ValueError("LOCAL_MAX_CHARACTERS must not exceed 768 for TinyLlama")
        max_websocket_message_bytes = integer("LOCAL_WS_MAX_MESSAGE_BYTES", 65_536, 4_096)
        if max_websocket_message_bytes > 262_144:
            raise ValueError("LOCAL_WS_MAX_MESSAGE_BYTES must not exceed 262144")
        llm_timeout_seconds = decimal("LOCAL_LLM_TIMEOUT_SECONDS", 90.0, 0.1)
        if llm_timeout_seconds > 300.0:
            raise ValueError("LOCAL_LLM_TIMEOUT_SECONDS must not exceed 300")
        return cls(
            host=os.environ.get("LOCAL_CHAT_HOST", "0.0.0.0").strip() or "0.0.0.0",
            port=integer("LOCAL_CHAT_PORT", 7862, 1),
            gateway_url=gateway_url,
            gateway_api_key=gateway_api_key,
            model=os.environ.get("LOCAL_GATEWAY_MODEL", "case9-rag").strip() or "case9-rag",
            llm_timeout_seconds=llm_timeout_seconds,
            max_messages=integer("LOCAL_MAX_MESSAGES", 4, 2),
            max_characters=max_characters,
            max_capture_seconds=max_capture_seconds,
            max_websocket_message_bytes=max_websocket_message_bytes,
            frontend_dir=Path(frontend),
        )


class _TTS(Protocol):
    @property
    def sample_rate(self) -> int:
        ...

    async def synthesize(self, text: str) -> bytes:
        ...


@dataclass
class _Connection:
    websocket: WebSocket
    session_id: str
    conversation: Conversation
    capture: Optional[CaptureHandle] = None
    capture_timeout_task: Optional[asyncio.Task[Any]] = None
    operation_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    busy: bool = False


async def _safe_send(websocket: WebSocket, payload: dict[str, Any]) -> bool:
    try:
        await websocket.send_json(payload)
        return True
    except Exception:
        return False


async def _send_or_raise(websocket: WebSocket, payload: dict[str, Any]) -> None:
    """Send one event and stop hardware/LLM work when the peer is gone."""

    if not await _safe_send(websocket, payload):
        raise ClientDisconnectedError("浏览器连接已断开")


async def _send_state_or_raise(connection: _Connection, state: str, **extra: Any) -> None:
    payload: dict[str, Any] = {"type": "state", "state": state}
    payload.update(extra)
    await _send_or_raise(connection.websocket, payload)


def _error_payload(code: str, message: str) -> dict[str, Any]:
    return {"type": "error", "code": code, "message": message}


async def _send_error(connection: _Connection, code: str, message: str) -> None:
    await _safe_send(connection.websocket, _error_payload(code, message))


async def _send_state(connection: _Connection, state: str, **extra: Any) -> None:
    payload: dict[str, Any] = {"type": "state", "state": state}
    payload.update(extra)
    await _safe_send(connection.websocket, payload)


def _safe_runtime_message(exc: Exception) -> str:
    if isinstance(
        exc,
        (AudioError, SpeechRuntimeError, LocalLLMError, LocalServiceError, SessionLimitError),
    ):
        return str(exc)[:500]
    return "本地聊天运行时失败，请检查开发板服务日志和模型配置。"


def _extract_sentence(buffer: str, force: bool = False) -> tuple[Optional[str], str]:
    if not buffer:
        return None, ""
    boundaries = set("。！？；\n.!?;\r")
    boundary_index: Optional[int] = None
    for index, character in enumerate(buffer):
        if character in boundaries:
            boundary_index = index + 1
            break
        if index + 1 >= 160:
            boundary_index = index + 1
            break
    if boundary_index is None and force:
        boundary_index = len(buffer)
    if boundary_index is None:
        return None, buffer
    sentence = buffer[:boundary_index].strip()
    return (sentence or None), buffer[boundary_index:]


def create_local_app(
    settings: Optional[LocalSettings] = None,
    audio: Optional[AudioBackend] = None,
    recognizer: Optional[SpeechRecognizer] = None,
    synthesizer: Optional[SpeechSynthesizer] = None,
    llm: Optional[StreamingLLM] = None,
    sessions: Optional[ConversationStore] = None,
) -> FastAPI:
    """Build the local chat app; all hardware/runtime objects are injectable."""

    runtime_settings = settings or LocalSettings.from_environ()
    runtime_audio = audio or PulseAudioBackend(
        AudioSettings.from_environ()
    )
    runtime_recognizer = recognizer or SherpaOnnxRecognizer()
    runtime_synthesizer: _TTS = synthesizer or SherpaOnnxSynthesizer()
    if llm is None and not runtime_settings.gateway_api_key:
        raise ValueError("gateway_api_key is required when using the real gateway client")
    runtime_llm = llm or OpenAIChatClient(
        base_url=runtime_settings.gateway_url,
        api_key=runtime_settings.gateway_api_key,
        model=runtime_settings.model,
        timeout_seconds=runtime_settings.llm_timeout_seconds,
    )
    runtime_sessions = sessions or ConversationStore(
        max_messages=runtime_settings.max_messages,
        max_characters=runtime_settings.max_characters,
    )
    runtime_metrics = LatencyMetrics()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            close = getattr(runtime_llm, "aclose", None)
            if close is not None:
                await close()

    app = FastAPI(
        title="Case9 Local Chinese Chat",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = runtime_settings
    app.state.audio = runtime_audio
    app.state.recognizer = runtime_recognizer
    app.state.synthesizer = runtime_synthesizer
    app.state.llm = runtime_llm
    app.state.sessions = runtime_sessions
    app.state.metrics = runtime_metrics

    @app.middleware("http")
    async def disable_dynamic_response_caching(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        # Do not let memory-only history, metrics, or device metadata survive
        # in a browser/intermediary cache.  Static hashed assets may still cache.
        if request.url.path == "/" or request.url.path.startswith(("/api", "/health")):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.get("/health")
    @app.get("/healthz")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "case9-local-chat",
            "mode": "unauthenticated-lan-experiment",
            "warning": SECURITY_WARNING,
            "audio": {
                "source": getattr(getattr(runtime_audio, "settings", None), "source", None),
                "sink": getattr(getattr(runtime_audio, "settings", None), "sink", None),
                "sample_rate": getattr(
                    getattr(runtime_audio, "settings", None), "sample_rate", 16_000
                ),
            },
            "gateway": runtime_settings.gateway_url,
            "model": runtime_settings.model,
            "frontend": runtime_settings.frontend_dir.is_dir(),
        }

    @app.get("/api/metrics")
    async def metrics() -> dict[str, Any]:
        return runtime_metrics.snapshot()

    @app.get("/")
    async def index() -> Any:
        index_path = runtime_settings.frontend_dir / "index.html"
        if index_path.is_file():
            return FileResponse(index_path)
        return PlainTextResponse(
            "Case9 local chat backend is running. Build frontend/dist to use the browser UI.\n",
            status_code=200,
        )

    assets_path = runtime_settings.frontend_dir / "assets"
    if assets_path.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_path), name="assets")

    async def speak_sentence(
        connection: _Connection, sentence: str, latency: OperationLatency
    ) -> None:
        if not sentence.strip():
            return
        await _send_state_or_raise(connection, "playing")
        pcm = await runtime_synthesizer.synthesize(sentence)
        if pcm:
            latency.mark("tts_first_audio")
        await runtime_audio.play_pcm(pcm, runtime_synthesizer.sample_rate)

    async def generate_reply(
        connection: _Connection, user_text: str, latency: OperationLatency
    ) -> None:
        try:
            prompt = connection.conversation.preview_user(user_text)
        except ValueError as exc:
            raise LocalServiceError(str(exc)) from exc
        assistant_budget = connection.conversation.max_characters - len(user_text.strip())
        if assistant_budget < 1:
            raise LocalServiceError("输入文本过长，请为模型回复预留字符空间")
        await _send_state_or_raise(connection, "generating")
        assistant_parts: list[str] = []
        assistant_characters = 0
        tts_buffer = ""
        iterator = None
        try:
            iterator = runtime_llm.stream(prompt).__aiter__()
            deadline = time.monotonic() + runtime_settings.llm_timeout_seconds
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LocalLLMError("本地模型生成超时")
                try:
                    delta = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
                except StopAsyncIteration:
                    break
                except asyncio.TimeoutError as exc:
                    raise LocalLLMError("本地模型生成超时") from exc
                if not delta:
                    continue
                # Enforce the session budget as deltas arrive.  Waiting until
                # add_turn() would let a malicious/failed upstream accumulate
                # megabytes of text and queue speech before rejecting it.
                if assistant_characters + len(delta) > assistant_budget:
                    raise SessionLimitError("模型回复超过会话字符限制")
                latency.mark("llm_first_token")
                assistant_parts.append(delta)
                assistant_characters += len(delta)
                await _send_or_raise(connection.websocket, {"type": "delta", "text": delta})
                tts_buffer += delta
                while True:
                    sentence, tts_buffer = _extract_sentence(tts_buffer)
                    if sentence is None:
                        break
                    await speak_sentence(connection, sentence, latency)
            latency.mark("llm_completion")
            sentence, tts_buffer = _extract_sentence(tts_buffer, force=True)
            if sentence is not None:
                await speak_sentence(connection, sentence, latency)
            assistant_text = "".join(assistant_parts).strip()
            try:
                connection.conversation.add_turn(user_text, assistant_text)
            except ValueError as exc:
                raise LocalServiceError(str(exc)) from exc
            await _send_state_or_raise(connection, "idle")
            completed_latency = latency.completed()
            runtime_metrics.record(completed_latency)
            await _send_or_raise(
                connection.websocket,
                {
                    "type": "done",
                    "text": assistant_text,
                    "latency_ms": completed_latency,
                },
            )
        finally:
            close = getattr(iterator, "aclose", None)
            if close is not None:
                try:
                    await close()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    LOGGER.debug("failed to close local LLM stream", exc_info=True)

    async def finish_capture(connection: _Connection, timed_out: bool = False) -> None:
        async with connection.operation_lock:
            capture = connection.capture
            if capture is None:
                return
            timeout_task = connection.capture_timeout_task
            connection.capture_timeout_task = None
            try:
                if timeout_task is not None and timeout_task is not asyncio.current_task():
                    timeout_task.cancel()
                    try:
                        await timeout_task
                    except asyncio.CancelledError:
                        pass
                if timed_out:
                    await _send_state_or_raise(connection, "recording_timeout")
                await _send_state_or_raise(connection, "recognizing")
                latency = OperationLatency(started_at=time.perf_counter())
                pcm = await capture.stop()
                connection.capture = None
                text = await runtime_recognizer.transcribe(
                    pcm,
                    getattr(getattr(runtime_audio, "settings", None), "sample_rate", 16_000),
                )
                latency.mark("asr_completion")
                text = text.strip()
                if not text:
                    await _send_state_or_raise(connection, "idle")
                    await _send_error(connection, "empty_transcript", "没有识别到语音，请重试。")
                    return
                await _send_or_raise(connection.websocket, {"type": "transcript", "text": text})
                await generate_reply(connection, text, latency)
            finally:
                if connection.capture is not None:
                    try:
                        await connection.capture.stop()
                    except Exception:
                        LOGGER.warning("failed to release capture after cancellation", exc_info=True)
                    finally:
                        connection.capture = None
                connection.busy = False

    async def auto_finish_capture(connection: _Connection) -> None:
        try:
            await asyncio.sleep(runtime_settings.max_capture_seconds)
            await finish_capture(connection, timed_out=True)
        except ClientDisconnectedError:
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            connection.busy = False
            await _send_state(connection, "idle")
            await _send_error(connection, "runtime_error", _safe_runtime_message(exc))

    async def _watch_for_disconnect(websocket: WebSocket) -> None:
        """Consume incoming frames while a long operation runs.

        The normal event loop cannot call ``receive_json`` while awaiting ASR,
        TTS, playback, or the LLM.  A small watcher lets a silent browser close
        cancel that operation instead of waiting for its next output token.
        Commands received while busy are intentionally discarded; the UI will
        send a fresh command after the operation completes.
        """

        try:
            while True:
                message = await websocket.receive()
                if message.get("type") == "websocket.disconnect":
                    return
                if message.get("type") != "websocket.receive":
                    continue
                payload = message.get("text")
                if payload is None:
                    payload = message.get("bytes")
                    size = len(payload) if isinstance(payload, bytes) else 0
                else:
                    size = len(payload.encode("utf-8"))
                if size > runtime_settings.max_websocket_message_bytes:
                    try:
                        await websocket.close(code=1009, reason="WebSocket message too large")
                    except Exception:
                        pass
                    return
        except WebSocketDisconnect:
            return

    async def _run_with_disconnect(
        connection: _Connection, operation: Awaitable[_OperationResult]
    ) -> _OperationResult:
        operation_task = asyncio.create_task(operation)
        watcher_task = asyncio.create_task(_watch_for_disconnect(connection.websocket))
        done, _ = await asyncio.wait(
            {operation_task, watcher_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if watcher_task in done:
            operation_task.cancel()
            await asyncio.gather(operation_task, return_exceptions=True)
            # Retrieve a watcher exception as well.  A transport-level
            # receive error is treated like a disconnect, but must not become
            # an unhandled task exception after the endpoint returns.
            await asyncio.gather(watcher_task, return_exceptions=True)
            raise ClientDisconnectedError("浏览器连接已断开")
        watcher_task.cancel()
        await asyncio.gather(watcher_task, return_exceptions=True)
        return await operation_task

    @app.websocket("/api/ws")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        session_id, conversation = runtime_sessions.get_or_create()
        connection = _Connection(websocket, session_id, conversation)
        audio_settings = getattr(runtime_audio, "settings", None)
        if not await _safe_send(
            websocket,
            {
                "type": "ready",
                "session_id": session_id,
                "warning": SECURITY_WARNING,
                "capabilities": {"text": True, "ptt": True, "tts": True},
                "microphone": getattr(audio_settings, "source", None),
                "speaker": getattr(audio_settings, "sink", None),
            },
        ):
            runtime_sessions.remove(connection.session_id)
            return
        try:
            while True:
                try:
                    message = await websocket.receive()
                    if message.get("type") == "websocket.disconnect":
                        return
                    if message.get("type") != "websocket.receive":
                        continue
                    raw_event = message.get("text")
                    if raw_event is None:
                        raw_bytes = message.get("bytes")
                        if isinstance(raw_bytes, bytes) and len(raw_bytes) > runtime_settings.max_websocket_message_bytes:
                            try:
                                await websocket.close(code=1009, reason="WebSocket message too large")
                            except Exception:
                                pass
                            return
                        await _send_error(connection, "invalid_event", "消息必须是 UTF-8 JSON 文本。")
                        continue
                    if len(raw_event.encode("utf-8")) > runtime_settings.max_websocket_message_bytes:
                        try:
                            await websocket.close(code=1009, reason="WebSocket message too large")
                        except Exception:
                            pass
                        return
                    event = json.loads(raw_event)
                except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError):
                    await _send_error(connection, "invalid_json", "消息必须是 JSON 对象。")
                    continue
                if not isinstance(event, dict):
                    await _send_error(connection, "invalid_event", "消息必须是 JSON 对象。")
                    continue
                event_type = str(event.get("type", "")).strip().lower()
                if event_type == "hello":
                    requested = event.get("session_id")
                    if requested is not None:
                        try:
                            sid, conv = runtime_sessions.get_or_create(str(requested))
                        except ValueError as exc:
                            await _send_error(connection, "invalid_session", str(exc))
                        else:
                            if sid != connection.session_id:
                                runtime_sessions.remove(connection.session_id)
                            connection.session_id = sid
                            connection.conversation = conv
                            await _safe_send(
                                websocket,
                                {
                                    "type": "ready",
                                    "session_id": sid,
                                    "warning": SECURITY_WARNING,
                                    "microphone": getattr(audio_settings, "source", None),
                                    "speaker": getattr(audio_settings, "sink", None),
                                },
                            )
                    else:
                        await _safe_send(websocket, {"type": "ready", "session_id": connection.session_id})
                    continue
                if event_type == "clear":
                    if connection.busy:
                        await _send_error(connection, "busy", "当前语音或回复尚未完成。")
                        continue
                    connection.conversation.clear()
                    await _send_state(connection, "idle")
                    await _safe_send(websocket, {"type": "cleared"})
                    continue
                if event_type == "text":
                    if connection.busy:
                        await _send_error(connection, "busy", "当前语音或回复尚未完成。")
                        continue
                    text = event.get("text")
                    if not isinstance(text, str) or not text.strip():
                        await _send_error(connection, "invalid_text", "text 不能为空。")
                        continue
                    if len(text.strip()) > runtime_settings.max_characters:
                        await _send_error(connection, "text_too_large", "输入文本超过会话限制。")
                        continue
                    connection.busy = True
                    try:
                        await _send_or_raise(websocket, {"type": "transcript", "text": text.strip()})
                        await _run_with_disconnect(
                            connection,
                            generate_reply(
                                connection,
                                text.strip(),
                                OperationLatency(started_at=time.perf_counter()),
                            ),
                        )
                    except ClientDisconnectedError:
                        return
                    except Exception as exc:
                        await _send_state(connection, "idle")
                        await _send_error(connection, "runtime_error", _safe_runtime_message(exc))
                    finally:
                        connection.busy = False
                    continue
                if event_type == "ptt_start":
                    if connection.busy or connection.capture is not None:
                        await _send_error(connection, "busy", "当前语音或回复尚未完成。")
                        continue
                    connection.busy = True
                    capture: Optional[CaptureHandle] = None
                    try:
                        capture = await runtime_audio.start_capture()
                        connection.capture = capture
                        await _send_state_or_raise(
                            connection,
                            "recording",
                            sample_rate=getattr(
                                getattr(runtime_audio, "settings", None), "sample_rate", 16_000
                            ),
                            max_seconds=runtime_settings.max_capture_seconds,
                        )
                        connection.capture_timeout_task = asyncio.create_task(
                            auto_finish_capture(connection)
                        )
                    except ClientDisconnectedError:
                        if capture is not None and connection.capture is not None:
                            try:
                                await capture.stop()
                            except Exception:
                                LOGGER.warning("failed to release capture after send failure", exc_info=True)
                        connection.capture = None
                        return
                    except Exception as exc:
                        connection.busy = False
                        if capture is not None and connection.capture is not None:
                            try:
                                await capture.stop()
                            except Exception:
                                LOGGER.warning("failed to release capture after start failure", exc_info=True)
                        connection.capture = None
                        await _send_state(connection, "idle")
                        await _send_error(connection, "audio_error", _safe_runtime_message(exc))
                    continue
                if event_type == "ptt_stop":
                    if connection.capture is None:
                        await _send_error(connection, "not_recording", "当前没有进行录音。")
                        continue
                    try:
                        await _run_with_disconnect(connection, finish_capture(connection))
                    except ClientDisconnectedError:
                        return
                    except Exception as exc:
                        connection.busy = False
                        await _send_state(connection, "idle")
                        await _send_error(connection, "runtime_error", _safe_runtime_message(exc))
                    continue
                await _send_error(connection, "unknown_event", f"不支持的事件类型: {event_type or '<empty>'}")
        except WebSocketDisconnect:
            pass
        finally:
            timeout_task = connection.capture_timeout_task
            if timeout_task is not None:
                timeout_task.cancel()
                try:
                    await timeout_task
                except asyncio.CancelledError:
                    pass
            capture = connection.capture
            connection.capture = None
            if capture is not None:
                try:
                    await capture.stop()
                except Exception:
                    LOGGER.warning("failed to release capture after websocket disconnect", exc_info=True)
            connection.busy = False
            # A local session is intentionally ephemeral: browser disconnect,
            # clear, or process restart discards its conversation.
            runtime_sessions.remove(connection.session_id)

    LOGGER.warning(SECURITY_WARNING)
    return app


def main() -> None:
    parser = argparse.ArgumentParser(description="Case9 local Chinese chat service")
    parser.add_argument("--host", default=os.environ.get("LOCAL_CHAT_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("LOCAL_CHAT_PORT", "7862")))
    args = parser.parse_args()
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO").upper())
    settings = LocalSettings.from_environ()
    LOGGER.warning(SECURITY_WARNING)
    uvicorn.run(
        create_local_app(settings),
        host=args.host,
        port=args.port,
        log_level="info",
        ws_max_size=settings.max_websocket_message_bytes,
    )


if __name__ == "__main__":
    main()
