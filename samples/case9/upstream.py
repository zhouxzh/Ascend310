"""OpenAI-compatible upstream client with safe error boundaries."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
import json
from typing import Any, Optional, Protocol

import httpx

from config import Settings


class UpstreamError(RuntimeError):
    """A sanitized upstream failure safe to return to gateway clients."""

    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


class CompletionStream(Protocol):
    """Common streaming handle used by the real client and test doubles."""

    def iter_bytes(self) -> AsyncIterator[bytes]:
        """Yield raw Server-Sent Event bytes."""

    async def aclose(self) -> None:
        """Release resources after the response is sent or cancelled."""


class UpstreamClient(Protocol):
    async def complete(self, payload: dict[str, Any], request_id: str) -> dict[str, Any]:
        """Return a non-streaming OpenAI response."""

    async def stream(self, payload: dict[str, Any], request_id: str) -> CompletionStream:
        """Open and return a streaming OpenAI response."""

    async def aclose(self) -> None:
        """Close any transport resources."""


@dataclass
class _HttpxCompletionStream:
    response: httpx.Response
    _closed: bool = False

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self.response.aclose()

    async def _iterate(self) -> AsyncIterator[bytes]:
        async for chunk in self.response.aiter_raw():
            if chunk:
                yield chunk

    def iter_bytes(self) -> AsyncIterator[bytes]:
        return self._iterate()


class OpenAICompatibleUpstream:
    """Forwards a normalized request to an OpenAI Chat Completions endpoint."""

    def __init__(self, settings: Settings):
        self._chat_url = _chat_completions_url(settings.upstream_base_url)
        self._api_key = settings.upstream_api_key
        # Reuse the bounded stream budget for a non-streaming response.  The
        # endpoint must never accept an unbounded JSON body from an upstream
        # process, even when the client requested a regular completion.
        self._max_completion_bytes = settings.stream_max_bytes
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.upstream_timeout_seconds),
            follow_redirects=False,
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def complete(self, payload: dict[str, Any], request_id: str) -> dict[str, Any]:
        request = self._client.build_request(
            "POST", self._chat_url, headers=self._headers(request_id), json=payload
        )
        try:
            response = await self._client.send(request, stream=True)
        except httpx.TimeoutException as exc:
            raise UpstreamError("The upstream LLM timed out", 504) from exc
        except httpx.RequestError as exc:
            raise UpstreamError("The upstream LLM is unavailable") from exc

        try:
            if not response.is_success:
                raise UpstreamError(f"The upstream LLM returned HTTP {response.status_code}")
            if not _is_json_content_type(response.headers.get("content-type")):
                raise UpstreamError("The upstream LLM did not return JSON")
            declared_length = response.headers.get("content-length")
            if declared_length is not None:
                try:
                    declared_size = int(declared_length)
                    if declared_size < 0:
                        raise UpstreamError("The upstream LLM returned an invalid Content-Length")
                    if declared_size > self._max_completion_bytes:
                        raise UpstreamError("The upstream LLM response exceeded its size limit")
                except ValueError as exc:
                    raise UpstreamError("The upstream LLM returned an invalid Content-Length") from exc
            body = bytearray()
            try:
                async for chunk in response.aiter_raw():
                    body.extend(chunk)
                    if len(body) > self._max_completion_bytes:
                        raise UpstreamError("The upstream LLM response exceeded its size limit")
            except httpx.TimeoutException as exc:
                raise UpstreamError("The upstream LLM timed out", 504) from exc
            except httpx.RequestError as exc:
                raise UpstreamError("The upstream LLM is unavailable") from exc
            try:
                result = json.loads(bytes(body))
            except (TypeError, ValueError) as exc:
                raise UpstreamError("The upstream LLM returned invalid JSON") from exc
        finally:
            await response.aclose()
        if not isinstance(result, dict) or not isinstance(result.get("choices"), list):
            raise UpstreamError("The upstream LLM returned an invalid completion")
        return result

    async def stream(self, payload: dict[str, Any], request_id: str) -> CompletionStream:
        request = self._client.build_request(
            "POST", self._chat_url, headers=self._headers(request_id), json=payload
        )
        try:
            response = await self._client.send(request, stream=True)
        except httpx.TimeoutException as exc:
            raise UpstreamError("The upstream LLM timed out", 504) from exc
        except httpx.RequestError as exc:
            raise UpstreamError("The upstream LLM is unavailable") from exc

        if response.status_code != 200:
            status = response.status_code
            await response.aclose()
            raise UpstreamError(f"The upstream LLM returned HTTP {status}")
        if not _is_sse_content_type(response.headers.get("content-type")):
            await response.aclose()
            raise UpstreamError("The upstream LLM did not return a Server-Sent Event stream")
        return _HttpxCompletionStream(response)

    def _headers(self, request_id: str) -> dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "X-Request-Id": request_id,
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers


def _chat_completions_url(base_url: str) -> str:
    suffix = "/chat/completions"
    if base_url.endswith(suffix):
        return base_url
    return f"{base_url}{suffix}"


def _is_json_content_type(content_type: Optional[str]) -> bool:
    return content_type is not None and "application/json" in content_type.lower()


def _is_sse_content_type(content_type: Optional[str]) -> bool:
    return content_type is not None and content_type.lower().startswith("text/event-stream")
