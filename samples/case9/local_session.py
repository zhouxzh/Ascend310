"""Bounded local conversations and the internal OpenAI streaming client."""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from typing import Any, Optional, Protocol

import httpx


LOGGER = logging.getLogger("case9.local_chat")


class SessionLimitError(ValueError):
    """A local conversation would exceed its in-memory bounds."""


class LocalLLMError(RuntimeError):
    """A local gateway or LLM response could not be consumed."""


@dataclass(frozen=True)
class SessionMessage:
    role: str
    content: str

    def as_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


class Conversation:
    """A bounded, process-memory-only conversation.

    Older messages are evicted when the count or character budget is reached.
    Turn-aware callers use :meth:`add_turn` so user/assistant pairs are retained
    together; the low-level :meth:`add` method remains available for importing
    individual legacy messages. A single message larger than the budget is
    rejected rather than silently truncating user input.
    """

    def __init__(self, max_messages: int = 20, max_characters: int = 12_000):
        if max_messages < 2:
            raise ValueError("max_messages must be at least 2")
        if max_characters < 1:
            raise ValueError("max_characters must be positive")
        self.max_messages = max_messages
        self.max_characters = max_characters
        self._messages: list[SessionMessage] = []
        self._characters = 0

    @property
    def character_count(self) -> int:
        return self._characters

    def add(self, role: str, content: str) -> None:
        role = role.strip().lower()
        if role not in {"user", "assistant", "system"}:
            raise SessionLimitError("Unsupported conversation role")
        if not isinstance(content, str) or not content.strip():
            raise SessionLimitError("Conversation content must not be empty")
        content = content.strip()
        if len(content) > self.max_characters:
            raise SessionLimitError("One message exceeds the conversation character limit")
        message = SessionMessage(role=role, content=content)
        self._messages.append(message)
        self._characters += len(content)
        while len(self._messages) > self.max_messages or self._characters > self.max_characters:
            removed = self._messages.pop(0)
            self._characters -= len(removed.content)

    def _validated_message(self, role: str, content: str) -> SessionMessage:
        """Validate and normalize a message without changing this conversation."""

        normalized_role = role.strip().lower()
        if normalized_role not in {"user", "assistant", "system"}:
            raise SessionLimitError("Unsupported conversation role")
        if not isinstance(content, str) or not content.strip():
            raise SessionLimitError("Conversation content must not be empty")
        normalized_content = content.strip()
        if len(normalized_content) > self.max_characters:
            raise SessionLimitError("One message exceeds the conversation character limit")
        return SessionMessage(role=normalized_role, content=normalized_content)

    @staticmethod
    def _evict_complete_prefix(
        messages: list[SessionMessage], max_messages: int, max_characters: int
    ) -> list[SessionMessage]:
        """Bound a candidate while removing whole user/assistant turns first.

        A legacy or externally supplied history may begin with an orphan message;
        those are removed individually.  For a normal history, the oldest user
        message and its immediately following assistant are evicted together.
        The returned list is a new list, so callers can commit atomically.
        """

        candidate = list(messages)
        characters = sum(len(item.content) for item in candidate)
        while len(candidate) > max_messages or characters > max_characters:
            if not candidate:
                break
            remove_count = 1
            first = candidate[0]
            if (
                first.role == "user"
                and len(candidate) > 1
                and candidate[1].role == "assistant"
            ):
                remove_count = 2
            removed = candidate[:remove_count]
            del candidate[:remove_count]
            characters -= sum(len(item.content) for item in removed)
        return candidate

    def preview_user(self, content: str) -> list[dict[str, str]]:
        """Build a bounded prompt with a pending user message, without mutation."""

        message = self._validated_message("user", content)
        candidate = self._evict_complete_prefix(
            [*self._messages, message], self.max_messages, self.max_characters
        )
        return [item.as_dict() for item in candidate]

    def add_turn(self, user_content: str, assistant_content: str = "") -> None:
        """Atomically commit a user/assistant turn.

        The newest turn is never split by character or message eviction.  If a
        complete turn cannot fit the configured character budget, the operation
        fails before changing the conversation.
        """

        user = self._validated_message("user", user_content)
        assistant: Optional[SessionMessage] = None
        if assistant_content and assistant_content.strip():
            assistant = self._validated_message("assistant", assistant_content)
            if len(user.content) + len(assistant.content) > self.max_characters:
                raise SessionLimitError("User and assistant turn exceeds the conversation character limit")
        candidate = [*self._messages, user]
        if assistant is not None:
            candidate.append(assistant)
        bounded = self._evict_complete_prefix(
            candidate, self.max_messages, self.max_characters
        )
        expected_suffix = [user] + ([assistant] if assistant is not None else [])
        if bounded[-len(expected_suffix) :] != expected_suffix:
            raise SessionLimitError("User and assistant turn could not be retained together")
        self._messages = bounded
        self._characters = sum(len(item.content) for item in bounded)

    def clear(self) -> None:
        self._messages.clear()
        self._characters = 0

    def snapshot(self) -> list[dict[str, str]]:
        return [message.as_dict() for message in self._messages]

    def __len__(self) -> int:
        return len(self._messages)


class ConversationStore:
    """Small bounded store for optional reconnectable local sessions."""

    def __init__(
        self,
        max_sessions: int = 128,
        max_messages: int = 20,
        max_characters: int = 12_000,
    ):
        if max_sessions < 1:
            raise ValueError("max_sessions must be positive")
        self.max_sessions = max_sessions
        self.max_messages = max_messages
        self.max_characters = max_characters
        # The HTTP layer normally serializes chat turns, but history/clear
        # requests and profile switches may arrive from different ASGI worker
        # threads.  Keep replacement of the session table atomic.
        self._lock = threading.RLock()
        self._sessions: dict[str, Conversation] = {}
        self._created: dict[str, float] = {}

    def get_or_create(self, session_id: Optional[str] = None) -> tuple[str, Conversation]:
        with self._lock:
            sid = (session_id or "").strip()
            if not sid:
                sid = f"local-{time.monotonic_ns():x}"
            if len(sid) > 128 or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.:" for ch in sid):
                raise ValueError("session_id contains unsupported characters")
            conversation = self._sessions.get(sid)
            if conversation is not None:
                return sid, conversation
            if len(self._sessions) >= self.max_sessions:
                oldest = min(self._created, key=self._created.get)
                self._sessions.pop(oldest, None)
                self._created.pop(oldest, None)
            conversation = Conversation(self.max_messages, self.max_characters)
            self._sessions[sid] = conversation
            self._created[sid] = time.monotonic()
            return sid, conversation

    def clear(self, session_id: str) -> None:
        with self._lock:
            conversation = self._sessions.get(session_id)
        if conversation is not None:
            conversation.clear()

    def remove(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)
            self._created.pop(session_id, None)

    def clear_all(self) -> int:
        """Drop every session as one atomic namespace replacement.

        Replacing the dictionaries, instead of iterating and clearing each
        conversation in place, also detaches an in-flight request that still
        holds an old ``Conversation`` object.  A generation check in the HTTP
        layer prevents that request from committing its result into the new
        namespace.
        """

        with self._lock:
            count = len(self._sessions)
            self._sessions = {}
            self._created = {}
            return count

    @property
    def session_count(self) -> int:
        """Return the current number of live sessions."""

        with self._lock:
            return len(self._sessions)


class StreamingLLM(Protocol):
    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        """Yield assistant text deltas."""

    # OpenAI-compatible implementations should set this after the stream's
    # terminal chunk.  Test doubles may omit it; callers then treat a clean
    # [DONE] without a reason as a normal stop.
    last_finish_reason: Optional[str]

    async def aclose(self) -> None:
        """Release the HTTP client."""


def sentence_chunks(text: str, max_chunk_characters: int = 160) -> Iterator[str]:
    """Yield punctuation-terminated chunks suitable for incremental TTS."""

    if max_chunk_characters < 1:
        raise ValueError("max_chunk_characters must be positive")
    buffer: list[str] = []
    length = 0
    boundaries = set("。！？；\n.!?;\r")
    for character in text:
        buffer.append(character)
        length += 1
        if character in boundaries or length >= max_chunk_characters:
            chunk = "".join(buffer).strip()
            if chunk:
                yield chunk
            buffer.clear()
            length = 0
    chunk = "".join(buffer).strip()
    if chunk:
        yield chunk


class OpenAIChatClient:
    """Minimal internal client for the case9 gateway's SSE endpoint."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:7861/v1",
        api_key: str = "",
        model: str = "case9-rag",
        timeout_seconds: float = 90.0,
    ):
        base_url = base_url.strip().rstrip("/")
        if not base_url.startswith(("http://", "https://")):
            raise ValueError("LOCAL_GATEWAY_URL must be an http(s) URL")
        self.base_url = base_url
        self.api_key = api_key.strip()
        self.model = model.strip() or "case9-rag"
        self.timeout_seconds = timeout_seconds
        self.last_finish_reason: Optional[str] = None
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        )

    @property
    def endpoint(self) -> str:
        if self.base_url.endswith("/chat/completions"):
            return self.base_url
        return self.base_url + "/chat/completions"

    async def aclose(self) -> None:
        await self._client.aclose()

    async def stream(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        # A client instance is serialized by the text UI, so this per-stream
        # field is safe and prevents a previous request's reason leaking into
        # the next turn.
        self.last_finish_reason = None
        payload = {"model": self.model, "messages": messages, "stream": True}
        headers = {
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            async with self._client.stream(
                "POST", self.endpoint, headers=headers, json=payload
            ) as response:
                if response.status_code != 200:
                    await self._raise_response_error(response)
                content_type = response.headers.get("content-type", "").lower()
                if not content_type.startswith("text/event-stream"):
                    raise LocalLLMError("The local gateway did not return an SSE stream")
                data_lines: list[str] = []
                line_buffer = ""
                saw_done = False
                async for chunk in response.aiter_text():
                    line_buffer += chunk
                    while "\n" in line_buffer:
                        line, line_buffer = line_buffer.split("\n", 1)
                        parsed = self._consume_sse_line(line.rstrip("\r"), data_lines)
                        if parsed is not None:
                            if parsed == "__DONE__":
                                saw_done = True
                                if self.last_finish_reason is None:
                                    self.last_finish_reason = "stop"
                                return
                            yield parsed
                if line_buffer:
                    parsed = self._consume_sse_line(line_buffer.rstrip("\r"), data_lines)
                    if parsed is not None:
                        if parsed == "__DONE__":
                            saw_done = True
                            if self.last_finish_reason is None:
                                self.last_finish_reason = "stop"
                        else:
                            yield parsed
                if data_lines:
                    parsed = self._decode_event(data_lines)
                    if parsed == "__DONE__":
                        saw_done = True
                        if self.last_finish_reason is None:
                            self.last_finish_reason = "stop"
                    elif parsed is not None:
                        yield parsed
                if not saw_done:
                    raise LocalLLMError("The local gateway ended the SSE stream before [DONE]")
        except httpx.TimeoutException as exc:
            raise LocalLLMError("The local LLM request timed out") from exc
        except httpx.RequestError as exc:
            raise LocalLLMError("The local gateway is unavailable") from exc

    async def _raise_response_error(self, response: httpx.Response) -> None:
        detail = ""
        try:
            body = response.json()
            if isinstance(body, dict):
                error = body.get("error")
                if isinstance(error, dict) and isinstance(error.get("message"), str):
                    detail = error["message"][:200]
        except (ValueError, json.JSONDecodeError):
            pass
        raise LocalLLMError(
            f"The local gateway returned HTTP {response.status_code}"
            + (f": {detail}" if detail else "")
        )

    def _consume_sse_line(self, line: str, data_lines: list[str]) -> Optional[str]:
        if line == "":
            if not data_lines:
                return None
            parsed = self._decode_event(data_lines)
            data_lines.clear()
            return parsed
        if line.startswith(":"):
            return None
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        return None
    def _decode_event(self, data_lines: list[str]) -> Optional[str]:
        payload_text = "\n".join(data_lines).strip()
        if payload_text == "[DONE]":
            return "__DONE__"
        if not payload_text:
            return None
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError as exc:
            raise LocalLLMError("The local gateway returned malformed SSE data") from exc
        if not isinstance(payload, dict):
            return None
        if isinstance(payload.get("error"), dict):
            message = payload["error"].get("message", "The local LLM returned an error")
            raise LocalLLMError(str(message)[:300])
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        choice = choices[0]
        if not isinstance(choice, dict):
            return None
        finish_reason = choice.get("finish_reason")
        if isinstance(finish_reason, str) and finish_reason:
            self.last_finish_reason = finish_reason
        delta = choice.get("delta")
        if isinstance(delta, dict) and isinstance(delta.get("content"), str):
            return delta["content"]
        message = choice.get("message")
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"]
        return None
