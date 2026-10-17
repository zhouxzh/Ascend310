"""Small text-only browser UI for the Ascend Edge Chat gateway.

This process is deliberately independent from the paused audio route. It
imports no audio, ASR, TTS, Torch, or Ascend runtime package, so it can verify
the text path without touching the board's microphone or speaker.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import os
import secrets
import string
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Mapping, Optional, Tuple
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from ..version import __version__
from ..runtime.config import load_release_environment

from .session import (
    Conversation,
    ConversationStore,
    LocalLLMError,
    OpenAIChatClient,
    SessionLimitError,
    StreamingLLM,
)

LOGGER = logging.getLogger("ascend_edge_chat.ui")
SESSION_COOKIE = "ascend_edge_chat_session"
SECURITY_WARNING = (
    "实验模式：文字聊天页面未启用浏览器鉴权；同一局域网内可访问此端口的主机都能发送请求。"
    "请只在可信实验网络使用。网关密钥只保留在服务端。"
)


class TextChatConfigurationError(ValueError):
    """Raised when the text-only service cannot be started safely."""


class TextChatRequestError(ValueError):
    """A bounded, user-facing request validation failure."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


@dataclass(frozen=True)
class TextChatSettings:
    """Configuration for the browser-facing text service."""

    host: str = "0.0.0.0"
    port: int = 7865
    gateway_url: str = "http://127.0.0.1:7861/v1"
    gateway_api_key: str = ""
    model: str = "ascend-edge-chat"
    # The MindSpore worker is serial and has a board-side watchdog.
    llm_timeout_seconds: float = 300.0
    max_messages: int = 4
    max_characters: int = 4000
    max_characters_cap: int = 4000
    max_body_bytes: int = 65_536
    body_timeout_seconds: float = 15.0
    client_write_timeout_seconds: float = 30.0

    def __post_init__(self) -> None:
        gateway_url = self.gateway_url.strip().rstrip("/")
        parsed_gateway = urlparse(gateway_url)
        try:
            gateway_port = parsed_gateway.port
        except ValueError as exc:
            raise TextChatConfigurationError("gateway_url has an invalid port") from exc
        if (
            parsed_gateway.scheme != "http"
            or parsed_gateway.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed_gateway.path.rstrip("/") != "/v1"
            or gateway_port is None
            or not 1 <= gateway_port <= 65_535
            or parsed_gateway.username
            or parsed_gateway.password
            or parsed_gateway.query
            or parsed_gateway.fragment
        ):
            raise TextChatConfigurationError(
                "gateway_url must be a loopback http URL without credentials"
            )
        if self.model.strip() != "ascend-edge-chat":
            raise TextChatConfigurationError("model must be the fixed public model ascend-edge-chat")
        if self.gateway_api_key:
            allowed_key_characters = string.ascii_letters + string.digits + "-_.~"
            if (
                len(self.gateway_api_key) < 24
                or not self.gateway_api_key.isascii()
                or any(character not in allowed_key_characters for character in self.gateway_api_key)
                or self.gateway_api_key.lower().startswith("replace-with")
            ):
                raise TextChatConfigurationError(
                    "gateway_api_key must be a real ASCII token with at least 24 characters"
                )
        object.__setattr__(self, "gateway_url", gateway_url)
        if not 1 <= self.port <= 65_535:
            raise TextChatConfigurationError("port must be between 1 and 65535")
        if not 0.1 <= self.llm_timeout_seconds <= 300.0:
            raise TextChatConfigurationError("llm_timeout_seconds must be between 0.1 and 300")
        if not 2 <= self.max_messages <= 4:
            raise TextChatConfigurationError("max_messages must be between 2 and 4")
        if not 1 <= self.max_characters_cap <= 4000:
            raise TextChatConfigurationError("max_characters_cap must be between 1 and 4000")
        if not 1 <= self.max_characters <= self.max_characters_cap:
            raise TextChatConfigurationError(
                "max_characters must be between 1 and %d" % self.max_characters_cap
            )
        if not 1_024 <= self.max_body_bytes <= 262_144:
            raise TextChatConfigurationError("max_body_bytes must be between 1024 and 262144")
        if not 0.1 <= self.body_timeout_seconds <= 120.0:
            raise TextChatConfigurationError("body_timeout_seconds must be between 0.1 and 120")
        if not 0.1 <= self.client_write_timeout_seconds <= 120.0:
            raise TextChatConfigurationError(
                "client_write_timeout_seconds must be between 0.1 and 120"
            )

    @classmethod
    def from_environ(cls) -> "TextChatSettings":
        load_release_environment()
        def integer(name: str, default: int, minimum: int) -> int:
            raw = os.environ.get(name, str(default)).strip()
            try:
                value = int(raw)
            except ValueError as exc:
                raise TextChatConfigurationError(f"{name} must be an integer") from exc
            if value < minimum:
                raise TextChatConfigurationError(f"{name} must be at least {minimum}")
            return value

        def decimal(name: str, default: float, minimum: float) -> float:
            raw = os.environ.get(name, str(default)).strip()
            try:
                value = float(raw)
            except ValueError as exc:
                raise TextChatConfigurationError(f"{name} must be a number") from exc
            if not math.isfinite(value) or value < minimum:
                raise TextChatConfigurationError(f"{name} must be a finite number >= {minimum}")
            return value

        gateway_url = os.environ.get(
            "ASCEND_EDGE_CHAT_UI_GATEWAY_URL", "http://127.0.0.1:7861/v1"
        ).strip().rstrip("/")
        parsed = urlparse(gateway_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise TextChatConfigurationError(
                "ASCEND_EDGE_CHAT_UI_GATEWAY_URL must be a loopback http URL without credentials"
            )
        api_key = os.environ.get(
            "ASCEND_EDGE_CHAT_UI_GATEWAY_API_KEY",
            os.environ.get("ASCEND_EDGE_CHAT_GATEWAY_API_KEY", ""),
        ).strip()
        if not api_key:
            raise TextChatConfigurationError(
                "ASCEND_EDGE_CHAT_UI_GATEWAY_API_KEY or ASCEND_EDGE_CHAT_GATEWAY_API_KEY must be set"
            )
        allowed_key_characters = string.ascii_letters + string.digits + "-_.~"
        if (
            len(api_key) < 24
            or not api_key.isascii()
            or any(character not in allowed_key_characters for character in api_key)
            or api_key.lower().startswith("replace-with")
        ):
            raise TextChatConfigurationError(
                "ASCEND_EDGE_CHAT_UI_GATEWAY_API_KEY must be a real ASCII token with at least 24 characters"
            )
        return cls(
            host=os.environ.get("ASCEND_EDGE_CHAT_UI_HOST", "0.0.0.0").strip() or "0.0.0.0",
            port=integer("ASCEND_EDGE_CHAT_UI_PORT", 7865, 1),
            gateway_url=gateway_url,
            gateway_api_key=api_key,
            model="ascend-edge-chat",
            llm_timeout_seconds=decimal("ASCEND_EDGE_CHAT_UI_LLM_TIMEOUT_SECONDS", 300.0, 0.1),
            max_messages=integer("ASCEND_EDGE_CHAT_UI_MAX_MESSAGES", 4, 2),
            max_characters=integer("ASCEND_EDGE_CHAT_UI_MAX_CHARACTERS", 4000, 1),
            max_characters_cap=integer("ASCEND_EDGE_CHAT_UI_MAX_CHARACTERS_CAP", 4000, 1),
            max_body_bytes=integer("ASCEND_EDGE_CHAT_UI_MAX_BODY_BYTES", 65_536, 1024),
            body_timeout_seconds=decimal("ASCEND_EDGE_CHAT_UI_BODY_TIMEOUT_SECONDS", 15.0, 0.1),
            client_write_timeout_seconds=decimal(
                "ASCEND_EDGE_CHAT_UI_WRITE_TIMEOUT_SECONDS", 30.0, 0.1
            ),
        )


def _error_response(
    message: str, status_code: int = 400, error_type: Optional[str] = None
) -> JSONResponse:
    if error_type is None:
        error_type = "server_error" if status_code >= 500 else "invalid_request_error"
    headers = {"Connection": "close"} if status_code in {408, 413} else None
    return JSONResponse(
        status_code=status_code,
        content={"error": {"message": message, "type": error_type}},
        headers=headers,
    )


def _sse(event: Mapping[str, Any]) -> str:
    return "data: " + json.dumps(dict(event), ensure_ascii=False, separators=(",", ":")) + "\n\n"


def _new_session_id() -> str:
    return "text-" + secrets.token_hex(18)


def _safe_runtime_message(exc: Exception) -> str:
    if isinstance(exc, (LocalLLMError, SessionLimitError, TextChatRequestError)):
        return str(exc)[:400]
    LOGGER.exception("text chat request failed")
    return "文字聊天请求失败，请检查网关和板端 LLM 服务日志。"


async def _stream_with_deadline(
    llm: StreamingLLM,
    prompt: list[dict[str, str]],
    timeout_seconds: float,
) -> AsyncIterator[str]:
    """Consume one upstream stream with a total generation deadline.

    ``httpx`` applies per-read timeouts, but a total deadline is still needed
    so a stalled or unexpectedly slow upstream iterator cannot hold the single
    NPU generation slot indefinitely.
    """

    iterator = llm.stream(prompt).__aiter__()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise LocalLLMError("文字模型生成超时")
            try:
                delta = await asyncio.wait_for(iterator.__anext__(), timeout=remaining)
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError as exc:
                raise LocalLLMError("文字模型生成超时") from exc
            yield delta
    finally:
        close = getattr(iterator, "aclose", None)
        if close is not None:
            try:
                await close()
            except Exception:
                LOGGER.debug("failed to close upstream text iterator", exc_info=True)


def _validated_finish_reason(llm: StreamingLLM) -> str:
    """Reject a silently truncated upstream completion.

    The browser receives deltas before the terminal SSE chunk.  Checking the
    reason only after the iterator closes lets us preserve those deltas for
    diagnostics while preventing an incomplete answer from entering session
    history or being reported as a successful turn.
    """

    reason = getattr(llm, "last_finish_reason", None)
    if reason is None:
        # Small test doubles and older compatible providers may send [DONE]
        # without a terminal reason; a clean stream is the least surprising
        # interpretation in that case.
        return "stop"
    reason = str(reason)
    if reason == "length":
        raise LocalLLMError(
            "模型达到 max_tokens 上限，回复被截断；请减少输入或提高输出上限后重试。"
        )
    if reason != "stop":
        raise LocalLLMError(f"模型以 {reason} 结束，回复未完整生成。")
    return reason


class _TimedStreamingResponse(StreamingResponse):
    """Streaming response that bounds each ASGI socket write."""

    def __init__(
        self,
        *args: Any,
        write_timeout_seconds: float,
        on_close: Optional[Callable[[], None]] = None,
        **kwargs: Any,
    ):
        super().__init__(*args, **kwargs)
        self._write_timeout_seconds = write_timeout_seconds
        self._on_close = on_close or (lambda: None)

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            close = getattr(self.body_iterator, "aclose", None)
            try:
                if close is not None:
                    await close()
            except BaseException:
                # Preserve cancellation/transport errors, but never skip the
                # idempotent lock-release callback on the way out.
                LOGGER.debug("failed to close text response iterator", exc_info=True)
                raise
            finally:
                self._on_close()

    async def stream_response(self, send: Any) -> None:
        async def timed_send(message: Any) -> None:
            await asyncio.wait_for(
                send(message), timeout=self._write_timeout_seconds
            )

        await super().stream_response(timed_send)


def _ensure_session(
    request: Request, sessions: ConversationStore
) -> Tuple[str, Conversation, bool]:
    """Return a bounded session and whether a replacement cookie is needed."""

    supplied = request.cookies.get(SESSION_COOKIE, "").strip()
    if supplied:
        try:
            session_id, conversation = sessions.get_or_create(supplied)
            return session_id, conversation, False
        except ValueError:
            LOGGER.info("discarding malformed text-chat session cookie")
    session_id, conversation = sessions.get_or_create(_new_session_id())
    return session_id, conversation, True


def _set_session_cookie(response: Any, session_id: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=3600,
        httponly=True,
        samesite="lax",
        secure=False,
        path="/",
    )


async def _read_bounded_body(request: Request, max_body_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        if not isinstance(chunk, (bytes, bytearray)):
            raise TextChatRequestError("请求体必须是二进制数据。")
        total += len(chunk)
        if total > max_body_bytes:
            raise TextChatRequestError(
                f"请求体超过 {max_body_bytes} 字节限制。", 413
            )
        chunks.append(bytes(chunk))
    return b"".join(chunks)


async def _parse_chat_request(
    request: Request,
    max_body_bytes: int,
    max_characters: int,
    body_timeout_seconds: float,
) -> Tuple[str, bool]:
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise TextChatRequestError("Content-Length 无效。") from exc
        if declared_length < 0:
            raise TextChatRequestError("Content-Length 无效。")
        if declared_length > max_body_bytes:
            raise TextChatRequestError(
                f"请求体超过 {max_body_bytes} 字节限制。", 413
            )
    # Use the ASGI stream rather than request.body(). A client may omit
    # Content-Length or use chunked transfer encoding; each chunk is bounded
    # before it is retained, and the total read has a deadline.
    try:
        body = await asyncio.wait_for(
            _read_bounded_body(request, max_body_bytes), timeout=body_timeout_seconds
        )
    except asyncio.TimeoutError as exc:
        raise TextChatRequestError("请求体读取超时。", 408) from exc
    except Exception as exc:
        if isinstance(exc, TextChatRequestError):
            raise
        raise TextChatRequestError("无法读取请求体。") from exc
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise TextChatRequestError("请求体必须是 UTF-8 JSON。") from exc
    if not isinstance(payload, dict):
        raise TextChatRequestError("请求体必须是 JSON 对象。")
    unknown = set(payload) - {"message", "stream"}
    if unknown:
        raise TextChatRequestError("请求只支持 message 和 stream 字段。")
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise TextChatRequestError("message 不能为空。")
    message = message.strip()
    if len(message) > max_characters:
        raise TextChatRequestError("输入文本超过会话字符限制。", 413)
    stream = payload.get("stream", True)
    if not isinstance(stream, bool):
        raise TextChatRequestError("stream 必须是布尔值。")
    return message, stream


def _pending_prompt(conversation: Conversation, message: str) -> list[dict[str, str]]:
    """Build the next prompt without mutating the committed conversation."""

    return conversation.preview_user(message)


def _assistant_budget(conversation: Conversation, message: str) -> int:
    """Reserve enough of the bounded turn for the user message itself."""

    budget = conversation.max_characters - len(message.strip())
    if budget < 1:
        raise SessionLimitError("输入文本过长，请为模型回复预留字符空间")
    return budget


def _commit_turn(conversation: Conversation, message: str, assistant_text: str) -> None:
    """Commit a completed turn only after the upstream stream succeeds."""

    conversation.add_turn(message, assistant_text)


TEXT_CHAT_HTML = r"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Ascend Edge Chat 文字聊天测试</title>
  <style>
    :root { color-scheme: light; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: #f3f5f7; color: #17212b; }
    .shell { width: min(920px, 100%); margin: 0 auto; min-height: 100vh; display: flex; flex-direction: column; }
    header { display: flex; align-items: center; justify-content: space-between; gap: 16px; padding: 22px 20px 14px; }
    h1 { font-size: 1.25rem; margin: 0; letter-spacing: 0; }
    .subtle { color: #64717d; font-size: .86rem; margin: 5px 0 0; }
    .status { border: 1px solid #c9d2da; border-radius: 999px; padding: 6px 11px; font-size: .82rem; white-space: nowrap; background: #fff; }
    .status[data-kind="ok"] { color: #176b45; border-color: #9bd3b5; background: #effaf3; }
    .status[data-kind="busy"] { color: #7a5311; border-color: #e7c77e; background: #fff9e9; }
    .status[data-kind="error"] { color: #9d2c2c; border-color: #e1aaaa; background: #fff2f2; }
    .warning { margin: 0 20px 12px; padding: 10px 12px; border-left: 3px solid #c98516; background: #fff8e8; color: #63470f; font-size: .86rem; line-height: 1.5; }
    main { flex: 1; display: flex; flex-direction: column; min-height: 0; padding: 0 20px 20px; }
    .messages { flex: 1; min-height: 300px; overflow-y: auto; padding: 8px 0 18px; }
    .empty { color: #71808d; text-align: center; padding: 80px 16px; }
    .message { display: flex; margin: 10px 0; }
    .message.user { justify-content: flex-end; }
    .bubble { max-width: min(760px, 88%); padding: 11px 14px; border-radius: 8px; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; }
    .message.user .bubble { color: #fff; background: #1f5f8b; }
    .message.assistant .bubble { background: #fff; border: 1px solid #d7dfe5; }
    .composer { border-top: 1px solid #d5dde3; padding-top: 14px; }
    textarea { width: 100%; min-height: 84px; max-height: 220px; resize: vertical; border: 1px solid #b9c5ce; border-radius: 6px; padding: 11px 12px; font: inherit; line-height: 1.5; background: #fff; }
    textarea:focus { outline: 2px solid #7eb3d5; outline-offset: 1px; border-color: #4f8fb8; }
    .controls { display: flex; align-items: center; justify-content: space-between; gap: 12px; margin-top: 10px; }
    .hint { color: #687783; font-size: .82rem; }
    .buttons { display: flex; gap: 8px; }
    button { border: 1px solid #aebbc5; border-radius: 6px; padding: 8px 14px; font: inherit; cursor: pointer; background: #fff; color: #21313d; }
    button:hover:not(:disabled) { background: #edf3f7; }
    button.primary { border-color: #1f5f8b; background: #1f5f8b; color: #fff; }
    button.primary:hover:not(:disabled) { background: #174b70; }
    button:disabled { opacity: .55; cursor: not-allowed; }
    @media (max-width: 600px) {
      header { align-items: flex-start; flex-direction: column; padding: 18px 14px 12px; }
      .warning, main { margin-left: 14px; margin-right: 14px; padding-left: 0; padding-right: 0; }
      .warning { margin-left: 14px; margin-right: 14px; }
      .controls { align-items: flex-start; flex-direction: column; }
      .buttons { width: 100%; }
      button { flex: 1; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <header>
      <div>
        <h1>Ascend Edge Chat</h1>
        <p class="subtle">Qwen1.5-0.5B Chat · 20T 技术预览 · <span id="model">读取模型中…</span></p>
      </div>
      <div id="status" class="status" data-kind="busy" role="status">连接中…</div>
    </header>
    <div class="warning" role="note">实验页面未启用浏览器鉴权，请只在可信局域网使用。</div>
    <main>
      <section id="messages" class="messages" aria-live="polite"><div id="empty" class="empty">输入一句话开始测试</div></section>
      <form id="composer" class="composer">
        <textarea id="input" maxlength="700" placeholder="输入消息…" aria-label="消息"></textarea>
        <div class="controls">
          <span class="hint">Enter 发送，Shift+Enter 换行</span>
          <div class="buttons">
            <button id="clear" type="button">清空</button>
            <button id="send" class="primary" type="submit">发送</button>
          </div>
        </div>
      </form>
    </main>
  </div>
  <script>
    (() => {
      const state = { busy: false, assistant: null, terminal: false };
      const messages = document.getElementById('messages');
      const empty = document.getElementById('empty');
      const input = document.getElementById('input');
      const send = document.getElementById('send');
      const clear = document.getElementById('clear');
      const status = document.getElementById('status');
      const model = document.getElementById('model');

      function setStatus(text, kind) {
        status.textContent = text;
        status.dataset.kind = kind || 'busy';
      }

      function setBusy(value) {
        state.busy = value;
        send.disabled = value;
        clear.disabled = value;
        input.disabled = value;
      }

      function addMessage(role, text) {
        empty.hidden = true;
        const row = document.createElement('div');
        row.className = 'message ' + role;
        const bubble = document.createElement('div');
        bubble.className = 'bubble';
        bubble.textContent = text || '';
        row.appendChild(bubble);
        messages.appendChild(row);
        messages.scrollTop = messages.scrollHeight;
        return bubble;
      }

      function renderHistory(items) {
        messages.querySelectorAll('.message').forEach((node) => node.remove());
        state.assistant = null;
        if (!Array.isArray(items) || items.length === 0) {
          empty.hidden = false;
          return;
        }
        empty.hidden = true;
        items.forEach((item) => {
          if (item && (item.role === 'user' || item.role === 'assistant')) {
            addMessage(item.role, typeof item.content === 'string' ? item.content : '');
          }
        });
      }

      function handleEvent(event) {
        if (!event || typeof event.type !== 'string') return;
        if (event.type === 'start') {
          setStatus('生成中…', 'busy');
        } else if (event.type === 'delta' && typeof event.text === 'string') {
          if (!state.assistant) state.assistant = addMessage('assistant', '');
          state.assistant.textContent += event.text;
          messages.scrollTop = messages.scrollHeight;
        } else if (event.type === 'done') {
          // The terminal event carries the authoritative cumulative text.
          // Reconcile it so a browser/proxy chunk boundary cannot leave a
          // visually truncated answer after a successful stream.
          if (typeof event.text === 'string') {
            if (!state.assistant) state.assistant = addMessage('assistant', '');
            state.assistant.textContent = event.text;
          }
          state.terminal = true;
          setStatus('就绪', 'ok');
        } else if (event.type === 'error') {
          state.terminal = true;
          setStatus('请求失败', 'error');
          if (!state.assistant) state.assistant = addMessage('assistant', '');
          state.assistant.textContent = '错误：' + (event.message || '未知错误');
        }
      }

      function parseSseBlock(block) {
        const data = block.split(/\r?\n/)
          .filter((line) => line.startsWith('data:'))
          .map((line) => line.slice(5).trimStart())
          .join('\n');
        if (!data || data === '[DONE]') return;
        try { handleEvent(JSON.parse(data)); } catch (_) { setStatus('响应格式错误', 'error'); }
      }

      async function sendMessage(event) {
        event.preventDefault();
        if (state.busy) return;
        const text = input.value.trim();
        if (!text) return;
        addMessage('user', text);
        input.value = '';
        state.assistant = null;
        state.terminal = false;
        setBusy(true);
        setStatus('请求中…', 'busy');
        try {
          const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
            body: JSON.stringify({ message: text, stream: true })
          });
          if (!response.ok) {
            let detail = 'HTTP ' + response.status;
            try { const body = await response.json(); detail = body.error?.message || detail; } catch (_) {}
            throw new Error(detail);
          }
          if (!response.body) throw new Error('浏览器不支持流式响应');
            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            while (true) {
              const part = await reader.read();
              if (part.done) break;
              buffer += decoder.decode(part.value, { stream: true });
              // SSE permits LF or CRLF line endings. Normalize complete CRLF
              // pairs before looking for the blank-line event delimiter.
              buffer = buffer.replace(/\r\n/g, '\n');
              let boundary;
              while ((boundary = buffer.indexOf('\n\n')) !== -1) {
                parseSseBlock(buffer.slice(0, boundary));
                buffer = buffer.slice(boundary + 2);
            }
          }
          buffer += decoder.decode();
          if (buffer.trim()) parseSseBlock(buffer);
          if (!state.terminal) throw new Error('响应在完成前中断');
          if (status.dataset.kind !== 'error') setStatus('就绪', 'ok');
        } catch (error) {
          setStatus('请求失败', 'error');
          if (!state.assistant) state.assistant = addMessage('assistant', '');
          state.assistant.textContent = '错误：' + (error instanceof Error ? error.message : '请求失败');
        } finally {
          setBusy(false);
          input.focus();
        }
      }

      async function clearConversation() {
        if (state.busy) return;
        try {
          const response = await fetch('/api/clear', { method: 'POST' });
          if (!response.ok) throw new Error('HTTP ' + response.status);
          renderHistory([]);
          setStatus('已清空', 'ok');
        } catch (error) {
          setStatus('清空失败', 'error');
        }
      }

      async function initialize() {
        try {
          const [configResponse, historyResponse] = await Promise.all([
            fetch('/api/config'), fetch('/api/history')
          ]);
          if (!configResponse.ok || !historyResponse.ok) throw new Error('服务不可用');
          const config = await configResponse.json();
          const history = await historyResponse.json();
          model.textContent = config.model || 'ascend-edge-chat';
          if (Number.isInteger(config.max_characters)) input.maxLength = config.max_characters;
          renderHistory(history.messages);
          setStatus('就绪', 'ok');
        } catch (_) {
          setStatus('连接失败', 'error');
        }
      }

      document.getElementById('composer').addEventListener('submit', sendMessage);
      clear.addEventListener('click', clearConversation);
      input.addEventListener('keydown', (event) => {
        if (event.key === 'Enter' && !event.shiftKey) {
          event.preventDefault();
          document.getElementById('composer').requestSubmit();
        }
      });
      initialize();
    })();
  </script>
</body>
</html>"""


def create_text_chat_app(
    settings: Optional[TextChatSettings] = None,
    llm: Optional[StreamingLLM] = None,
    sessions: Optional[ConversationStore] = None,
) -> FastAPI:
    """Build the text-only app with injectable client and session store."""

    runtime_settings = settings or TextChatSettings.from_environ()
    if llm is None and not runtime_settings.gateway_api_key:
        raise TextChatConfigurationError(
            "gateway_api_key must be set when no test LLM is injected"
        )
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
    # Python 3.9 binds asyncio synchronization primitives to the current
    # event loop at construction time.  App factories are commonly called
    # before TestClient/uvicorn starts that loop, so initialize lazily on the
    # first lifespan/request callback instead of importing the loop at module
    # construction time.
    body_read_slots: Optional[asyncio.Semaphore] = None
    request_lock: Optional[asyncio.Lock] = None

    def ensure_async_primitives() -> None:
        nonlocal body_read_slots, request_lock
        if body_read_slots is None or request_lock is None:
            asyncio.get_running_loop()
            body_read_slots = asyncio.Semaphore(8)
            request_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        ensure_async_primitives()
        try:
            yield
        finally:
            close = getattr(runtime_llm, "aclose", None)
            if close is not None:
                await close()

    app = FastAPI(
        title="Ascend Edge Chat text UI",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.settings = runtime_settings
    app.state.llm = runtime_llm
    app.state.sessions = runtime_sessions

    @app.middleware("http")
    async def disable_browser_caching(request: Request, call_next: Any) -> Any:
        response = await call_next(request)
        # Conversation history is process memory only; never let a browser or
        # intermediary retain a page/API response after clear or restart.
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.get("/")
    async def index(request: Request) -> HTMLResponse:
        response = HTMLResponse(TEXT_CHAT_HTML)
        session_id, _, is_new = _ensure_session(request, runtime_sessions)
        if is_new:
            _set_session_cookie(response, session_id)
        return response

    @app.get("/health")
    @app.get("/healthz")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "ascend-edge-chat-ui",
            "mode": "unauthenticated-lan-experiment",
            "warning": SECURITY_WARNING,
            "gateway": runtime_settings.gateway_url,
            "model": runtime_settings.model,
            "sessions": runtime_sessions.session_count,
        }

    @app.get("/api/config")
    async def api_config() -> dict[str, Any]:
        return {
            "model": runtime_settings.model,
            "streaming": True,
            "max_messages": runtime_settings.max_messages,
            "max_characters": runtime_settings.max_characters,
            "warning": SECURITY_WARNING,
        }

    @app.get("/api/history")
    async def history(request: Request) -> JSONResponse:
        session_id, conversation, is_new = _ensure_session(request, runtime_sessions)
        response = JSONResponse({"messages": conversation.snapshot()})
        if is_new:
            _set_session_cookie(response, session_id)
        return response

    @app.post("/api/clear")
    async def clear(request: Request) -> JSONResponse:
        ensure_async_primitives()
        assert request_lock is not None
        if request_lock.locked():
            return _error_response("当前回复尚未完成。", 409)
        session_id, conversation, is_new = _ensure_session(request, runtime_sessions)
        conversation.clear()
        response = JSONResponse({"cleared": True})
        if is_new:
            _set_session_cookie(response, session_id)
        return response

    async def stream_reply(
        request: Request,
        conversation: Conversation,
        message: str,
        release_lock: Callable[[], None],
    ) -> AsyncIterator[str]:
        parts: list[str] = []
        assistant_characters = 0
        try:
            prompt = _pending_prompt(conversation, message)
            assistant_budget = _assistant_budget(conversation, message)
            if await request.is_disconnected():
                return
            yield _sse({"type": "start"})
            async for delta in _stream_with_deadline(
                runtime_llm, prompt, runtime_settings.llm_timeout_seconds
            ):
                if await request.is_disconnected():
                    return
                if not delta:
                    continue
                assistant_characters += len(delta)
                if assistant_characters > assistant_budget:
                    raise SessionLimitError("模型回复超过会话字符限制")
                parts.append(delta)
                yield _sse({"type": "delta", "text": delta})
            finish_reason = _validated_finish_reason(runtime_llm)
            assistant_text = "".join(parts).strip()
            _commit_turn(conversation, message, assistant_text)
            yield _sse(
                {
                    "type": "done",
                    "text": assistant_text,
                    "finish_reason": finish_reason,
                }
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            yield _sse({"type": "error", "message": _safe_runtime_message(exc)})
        finally:
            release_lock()

    @app.post("/api/chat")
    async def chat(request: Request) -> Any:
        ensure_async_primitives()
        assert body_read_slots is not None
        assert request_lock is not None
        body_slot_acquired = False
        try:
            await asyncio.wait_for(
                body_read_slots.acquire(), timeout=runtime_settings.body_timeout_seconds
            )
            body_slot_acquired = True
        except asyncio.TimeoutError:
            return _error_response("当前请求读取并发已满，请稍后重试。", 429)
        try:
            message, stream = await _parse_chat_request(
                request,
                runtime_settings.max_body_bytes,
                runtime_settings.max_characters,
                runtime_settings.body_timeout_seconds,
            )
        except TextChatRequestError as exc:
            return _error_response(exc.message, exc.status_code)
        finally:
            if body_slot_acquired:
                body_read_slots.release()
        if request_lock.locked():
            return _error_response("已有一个文字请求正在生成，请稍后重试。", 409)
        await request_lock.acquire()
        try:
            session_id, conversation, is_new = _ensure_session(request, runtime_sessions)
        except Exception:
            request_lock.release()
            return _error_response("无法建立文字会话。", 500)

        lock_released = False

        def release_lock() -> None:
            nonlocal lock_released
            if lock_released:
                return
            lock_released = True
            request_lock.release()

        if stream:
            try:
                response = _TimedStreamingResponse(
                    stream_reply(
                        request,
                        conversation,
                        message,
                        release_lock,
                    ),
                    media_type="text/event-stream",
                    headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
                    write_timeout_seconds=runtime_settings.client_write_timeout_seconds,
                    on_close=release_lock,
                )
            except Exception:
                release_lock()
                raise
            if is_new:
                _set_session_cookie(response, session_id)
            return response

        try:
            prompt = _pending_prompt(conversation, message)
            assistant_budget = _assistant_budget(conversation, message)
            parts: list[str] = []
            assistant_characters = 0
            async for delta in _stream_with_deadline(
                runtime_llm, prompt, runtime_settings.llm_timeout_seconds
            ):
                if delta:
                    assistant_characters += len(delta)
                    if assistant_characters > assistant_budget:
                        raise SessionLimitError("模型回复超过会话字符限制")
                    parts.append(delta)
            finish_reason = _validated_finish_reason(runtime_llm)
            assistant_text = "".join(parts).strip()
            _commit_turn(conversation, message, assistant_text)
            response = JSONResponse(
                {
                    "message": assistant_text,
                    "model": runtime_settings.model,
                    "finish_reason": finish_reason,
                }
            )
            if is_new:
                _set_session_cookie(response, session_id)
            return response
        except Exception as exc:
            return _error_response(_safe_runtime_message(exc), 502, "server_error")
        finally:
            release_lock()

    LOGGER.warning(SECURITY_WARNING)
    return app


def main(argv: Optional[list[str]] = None) -> None:
    parser = argparse.ArgumentParser(description="Ascend Edge Chat text-only browser chat UI")
    parser.add_argument("--host", default=os.environ.get("ASCEND_EDGE_CHAT_UI_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ASCEND_EDGE_CHAT_UI_PORT", "7865")))
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.environ.get("ASCEND_EDGE_CHAT_LOG_LEVEL", "INFO").upper())
    settings = TextChatSettings.from_environ()
    uvicorn.run(create_text_chat_app(settings), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
