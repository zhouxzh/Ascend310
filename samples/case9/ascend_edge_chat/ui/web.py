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
from pathlib import Path
import secrets
import signal
import string
import subprocess
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Mapping, Optional, Tuple
from urllib.parse import urlparse

import uvicorn
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

from ..version import __version__
from ..runtime.config import load_release_environment
from ..model_catalog import DEFAULT_CATALOG_PATH, CatalogError, catalog_by_id, load_catalog
from ..artifacts.model_store import ArtifactError, data_root
from ..backends.model_profiles import ProfileError, load_profiles, resolve_safe_path

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
    "实验模式：文字聊天页面未启用浏览器鉴权；同一局域网内可访问此端口的主机都能发送请求和切换教学模型。"
    "切换会清空会话；生成期间页面会暂时禁用模型选择器。"
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


class ModelSwitchError(RuntimeError):
    """Raised when a controlled tutorial worker switch cannot complete."""

    def __init__(self, message: str, *, rollback: str = "unknown") -> None:
        super().__init__(message)
        self.rollback = rollback


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _worker_registry_path(catalog_entry: Mapping[str, Any]) -> Path:
    """Resolve the catalog's fixed registry reference below the project root."""

    relative = catalog_entry.get("worker_registry")
    if not isinstance(relative, str) or not relative.strip():
        relative = "configs/tutorial-models.json"
    root = _project_root()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ModelSwitchError("模型注册表路径越过项目目录。") from exc
    if candidate.is_symlink() or not candidate.is_file():
        raise ModelSwitchError("模型注册表不存在：%s" % relative)
    return candidate


def _verify_switch_cache(root: Path, profile: Any) -> None:
    """Reject missing/partial teaching artifacts before stopping the worker."""

    try:
        cache_dir = resolve_safe_path(profile.cache_dir, root, allow_missing=True)
    except ProfileError as exc:
        raise ModelSwitchError("模型缓存路径无效。") from exc
    if not cache_dir.is_dir() or cache_dir.is_symlink():
        raise ModelSwitchError("目标模型缓存不存在：%s" % profile.id)
    if not (cache_dir / "config.json").is_file():
        raise ModelSwitchError("目标模型缺少 config.json：%s" % profile.id)
    if not any(
        (cache_dir / name).is_file()
        for name in ("tokenizer.json", "tokenizer.model", "sentencepiece.model")
    ):
        raise ModelSwitchError("目标模型缺少 tokenizer：%s" % profile.id)
    if not any(
        item.is_file()
        and not item.name.startswith(".")
        and item.name.lower().endswith((".safetensors", ".bin", ".ckpt"))
        for item in cache_dir.iterdir()
    ):
        raise ModelSwitchError("目标模型缺少权重文件：%s" % profile.id)


def _catalog_with_availability(
    catalog: list[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Attach local-cache availability without treating quality as a gate."""

    try:
        model_root = data_root()
    except ArtifactError as exc:
        return [
            dict(item, available=False, availability_reason="模型目录不可用：%s" % exc)
            for item in catalog
        ]
    result: list[dict[str, Any]] = []
    for item in catalog:
        public = dict(item)
        if public.get("selectable") is not True:
            public["available"] = False
            public["availability_reason"] = "该模型尚未完成技术启动门。"
            result.append(public)
            continue
        try:
            registry = load_profiles(_worker_registry_path(public))
            profile = registry.get(str(public["id"]))
            _verify_switch_cache(model_root, profile)
        except (ModelSwitchError, OSError, ProfileError) as exc:
            public["available"] = False
            public["availability_reason"] = str(exc)
        else:
            public["available"] = True
            public["availability_reason"] = "本机缓存完整，可由页面启动。"
        result.append(public)
    return result


async def _worker_health() -> Mapping[str, Any]:
    """Read the loopback worker health without exposing its endpoint."""

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            response = await client.get("http://127.0.0.1:8090/health")
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise ModelSwitchError("无法读取当前 worker 健康状态。") from exc
    if not isinstance(payload, Mapping):
        raise ModelSwitchError("worker 健康响应格式无效。")
    return payload


async def _acquire_request_slot(lock: asyncio.Lock, timeout_seconds: float) -> bool:
    """Wait briefly for an SSE response to finish its server-side cleanup."""

    try:
        await asyncio.wait_for(lock.acquire(), timeout=timeout_seconds)
    except asyncio.TimeoutError:
        return False
    return True


def _worker_command_line(pid: int) -> str:
    try:
        return Path("/proc/%d/cmdline" % pid).read_bytes().replace(b"\0", b" ").decode(
            "utf-8", errors="replace"
        )
    except (OSError, ValueError):
        return ""


def _is_managed_worker(pid: Any) -> bool:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 1:
        return False
    command_line = _worker_command_line(pid)
    return "ascend_edge_chat" in command_line and " worker " in (" %s " % command_line)


def _worker_descendants(root_pid: int) -> set[int]:
    """Return only processes that are descendants of one verified worker PID."""

    parents: dict[int, int] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            status = (entry / "status").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        parent: Optional[int] = None
        for line in status.splitlines():
            if line.startswith("PPid:"):
                try:
                    parent = int(line.split(":", 1)[1].strip())
                except ValueError:
                    pass
                break
        if parent is not None:
            parents[int(entry.name)] = parent
    descendants = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, parent in parents.items():
            if parent in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True
    return descendants


def _pid_alive(pid: int) -> bool:
    # A native worker can leave a short-lived forkserver as a zombie after the
    # parent exits. `kill(pid, 0)` succeeds for zombies, but they hold neither
    # a port nor NPU resources and cannot be stopped again.
    try:
        status = Path("/proc") / str(pid) / "status"
        for line in status.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("State:"):
                return "Z" not in line.split(":", 1)[1]
    except OSError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


async def _stop_managed_worker(pid: int) -> None:
    """Stop only the worker PID reported by its own health endpoint."""

    if not _is_managed_worker(pid):
        raise ModelSwitchError("当前 worker PID 未通过受控进程校验。")
    # A historic foreground launch can put the gateway and worker in the same
    # process group. Capture just the worker tree, never a shared group.
    owned_pids = _worker_descendants(pid)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = asyncio.get_running_loop().time() + 20.0
    while asyncio.get_running_loop().time() < deadline:
        if not any(_pid_alive(candidate) for candidate in owned_pids):
            return
        await asyncio.sleep(0.25)
    # Ask remaining provider/forkserver children to stop, then fail closed if
    # native code ignores SIGTERM. None of these PIDs can be a sibling gateway.
    for candidate in owned_pids:
        if _pid_alive(candidate):
            try:
                os.kill(candidate, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except PermissionError as exc:
                raise ModelSwitchError("没有权限停止当前 worker 子进程。") from exc
    await asyncio.sleep(1.0)
    for candidate in owned_pids:
        if _pid_alive(candidate):
            try:
                os.kill(candidate, signal.SIGKILL)
            except ProcessLookupError:
                pass
    if not any(_pid_alive(candidate) for candidate in owned_pids):
        return
    raise ModelSwitchError("当前 worker 及其子进程在 20 秒内没有退出。")


async def _wait_for_worker_profile(
    profile_id: str,
    process: Optional[subprocess.Popen[bytes]],
    timeout_seconds: float,
) -> Mapping[str, Any]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last_error = ""
    while asyncio.get_running_loop().time() < deadline:
        if process is not None and process.poll() is not None:
            raise ModelSwitchError("目标 worker 提前退出，退出码为 %s。" % process.returncode)
        try:
            payload = await _worker_health()
            if (
                payload.get("ready") is True
                and payload.get("healthy") is True
                and payload.get("profile") == profile_id
            ):
                return payload
            last_error = str(payload.get("last_error") or "worker 尚未就绪")
        except ModelSwitchError as exc:
            last_error = str(exc)
        await asyncio.sleep(1.0)
    raise ModelSwitchError("目标 worker 启动超时：%s" % last_error)


async def _start_managed_worker(
    profile_id: str,
    registry_path: Path,
    *,
    timeout_seconds: float,
) -> tuple[subprocess.Popen[bytes], Mapping[str, Any]]:
    """Start one fixed-profile worker and wait for its NPU health signal."""

    data_dir = data_root()
    log_dir = data_dir / "model-switch-logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / ("worker-%s-%d.log" % (profile_id, int(time.time())))
    environment = os.environ.copy()
    environment["PYTHONNOUSERSITE"] = "1"
    environment["ASCEND_EDGE_CHAT_PROFILE"] = profile_id
    environment["ASCEND_EDGE_CHAT_MODEL_PROFILES"] = str(registry_path)
    environment["ASCEND_EDGE_CHAT_LAUNCHER_VERIFIED"] = "1"
    launcher = _project_root() / "scripts" / "run" / "start-worker.sh"
    if launcher.is_symlink() or not launcher.is_file():
        raise ModelSwitchError("worker 启动脚本不存在。")
    command = [
        "bash",
        str(launcher),
        "--host",
        "127.0.0.1",
        "--port",
        "8090",
        "--profile",
        profile_id,
        "--registry",
        str(registry_path),
    ]
    with log_path.open("ab") as log_stream:
        process = subprocess.Popen(
            command,
            cwd=str(_project_root()),
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    try:
        health = await _wait_for_worker_profile(profile_id, process, timeout_seconds)
    except Exception:
        if process.poll() is None:
            process.terminate()
        raise
    return process, health


@dataclass(frozen=True)
class TextChatSettings:
    """Configuration for the browser-facing text service."""

    host: str = "0.0.0.0"
    port: int = 7865
    gateway_url: str = "http://127.0.0.1:7861/v1"
    gateway_api_key: str = ""
    model: str = "ascend-edge-chat"
    active_profile: str = "qwen1.5-0.5b-chat"
    catalog_path: str = str(DEFAULT_CATALOG_PATH)
    # The MindSpore worker is serial and has a board-side watchdog.
    llm_timeout_seconds: float = 300.0
    max_messages: int = 4
    max_characters: int = 4000
    max_characters_cap: int = 4000
    max_body_bytes: int = 65_536
    body_timeout_seconds: float = 15.0
    client_write_timeout_seconds: float = 30.0
    model_switch_wait_seconds: float = 5.0

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
        if not self.active_profile.strip():
            raise TextChatConfigurationError("active_profile must not be empty")
        catalog = Path(self.catalog_path).expanduser()
        if not catalog.is_absolute():
            raise TextChatConfigurationError("catalog_path must be an absolute path")
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
        if not 0.1 <= self.model_switch_wait_seconds <= 30.0:
            raise TextChatConfigurationError(
                "model_switch_wait_seconds must be between 0.1 and 30"
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
            active_profile=os.environ.get("ASCEND_EDGE_CHAT_PROFILE", "qwen1.5-0.5b-chat").strip()
            or "qwen1.5-0.5b-chat",
            catalog_path=os.environ.get(
                "ASCEND_EDGE_CHAT_MODEL_CATALOG", str(DEFAULT_CATALOG_PATH)
            ).strip()
            or str(DEFAULT_CATALOG_PATH),
            llm_timeout_seconds=decimal("ASCEND_EDGE_CHAT_UI_LLM_TIMEOUT_SECONDS", 300.0, 0.1),
            max_messages=integer("ASCEND_EDGE_CHAT_UI_MAX_MESSAGES", 4, 2),
            max_characters=integer("ASCEND_EDGE_CHAT_UI_MAX_CHARACTERS", 4000, 1),
            max_characters_cap=integer("ASCEND_EDGE_CHAT_UI_MAX_CHARACTERS_CAP", 4000, 1),
            max_body_bytes=integer("ASCEND_EDGE_CHAT_UI_MAX_BODY_BYTES", 65_536, 1024),
            body_timeout_seconds=decimal("ASCEND_EDGE_CHAT_UI_BODY_TIMEOUT_SECONDS", 15.0, 0.1),
            client_write_timeout_seconds=decimal(
                "ASCEND_EDGE_CHAT_UI_WRITE_TIMEOUT_SECONDS", 30.0, 0.1
            ),
            model_switch_wait_seconds=decimal(
                "ASCEND_EDGE_CHAT_UI_MODEL_SWITCH_WAIT_SECONDS", 5.0, 0.1
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
    max_tokens: int,
    timeout_seconds: float,
) -> AsyncIterator[str]:
    """Consume one upstream stream with a total generation deadline.

    ``httpx`` applies per-read timeouts, but a total deadline is still needed
    so a stalled or unexpectedly slow upstream iterator cannot hold the single
    NPU generation slot indefinitely.
    """

    iterator = llm.stream(prompt, max_tokens=max_tokens).__aiter__()
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
    """Validate the upstream terminal reason without hiding partial output.

    A length-limited completion means the worker ran successfully but did not
    emit EOS before its configured output bound.  The UI must show that text
    and label it as truncated; treating it as a transport failure makes a
    healthy model appear unavailable.  The caller keeps truncated text out of
    session history, because it is not a complete assistant turn.
    """

    reason = getattr(llm, "last_finish_reason", None)
    if reason is None:
        # Small test doubles and older compatible providers may send [DONE]
        # without a terminal reason; a clean stream is the least surprising
        # interpretation in that case.
        return "stop"
    reason = str(reason)
    if reason not in {"stop", "length"}:
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


async def _read_json_object(
    request: Request,
    max_body_bytes: int,
    body_timeout_seconds: float,
) -> dict[str, Any]:
    """Read one small JSON object without allowing an unbounded UI request."""

    content_length = request.headers.get("content-length")
    if content_length:
        try:
            declared_length = int(content_length)
        except ValueError as exc:
            raise TextChatRequestError("Content-Length 无效。") from exc
        if declared_length < 0:
            raise TextChatRequestError("Content-Length 无效。")
        if declared_length > max_body_bytes:
            raise TextChatRequestError("请求体超过 %d 字节限制。" % max_body_bytes, 413)
    try:
        body = await asyncio.wait_for(
            _read_bounded_body(request, max_body_bytes), timeout=body_timeout_seconds
        )
    except asyncio.TimeoutError as exc:
        raise TextChatRequestError("请求体读取超时。", 408) from exc
    except TextChatRequestError:
        raise
    except Exception as exc:
        raise TextChatRequestError("无法读取请求体。") from exc
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise TextChatRequestError("请求体必须是 UTF-8 JSON。") from exc
    if not isinstance(payload, dict):
        raise TextChatRequestError("请求体必须是 JSON 对象。")
    return payload


async def _parse_chat_request(
    request: Request,
    max_body_bytes: int,
    max_characters: int,
    body_timeout_seconds: float,
) -> Tuple[str, bool, Optional[str], Optional[int]]:
    payload = await _read_json_object(request, max_body_bytes, body_timeout_seconds)
    unknown = set(payload) - {"message", "stream", "profile", "max_tokens"}
    if unknown:
        raise TextChatRequestError("请求只支持 message、stream、profile 和 max_tokens 字段。")
    message = payload.get("message")
    if not isinstance(message, str) or not message.strip():
        raise TextChatRequestError("message 不能为空。")
    message = message.strip()
    if len(message) > max_characters:
        raise TextChatRequestError("输入文本超过会话字符限制。", 413)
    stream = payload.get("stream", True)
    if not isinstance(stream, bool):
        raise TextChatRequestError("stream 必须是布尔值。")
    profile = payload.get("profile")
    if profile is not None and (not isinstance(profile, str) or not profile.strip()):
        raise TextChatRequestError("profile 必须是非空字符串。")
    max_tokens = payload.get("max_tokens")
    if max_tokens is not None and (
        isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1
    ):
        raise TextChatRequestError("max_tokens 必须是正整数。")
    return (
        message,
        stream,
        profile.strip() if isinstance(profile, str) else None,
        max_tokens,
    )


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
    :root {
      color-scheme: light;
      font-family: "Noto Sans SC", "Microsoft YaHei", system-ui, -apple-system, "Segoe UI", sans-serif;
      font-size: 18px;
    }
    * { box-sizing: border-box; }
    body { margin: 0; min-height: 100vh; background: #f3f5f7; color: #17212b; font-size: 1rem; line-height: 1.5; }
    .shell { width: min(1100px, 100%); margin: 0 auto; min-height: 100vh; display: flex; flex-direction: column; }
    header { display: flex; align-items: flex-start; justify-content: space-between; gap: 24px; padding: 30px 32px 18px; }
    h1 { font-size: 2rem; line-height: 1.2; margin: 0; letter-spacing: 0; }
    .subtle { color: #52616d; font-size: 1rem; margin: 8px 0 0; }
    .model-picker { display: flex; align-items: center; flex-wrap: wrap; gap: 10px 16px; margin-top: 16px; color: #3b4c59; font-size: 1rem; }
    .model-picker select { min-width: 470px; min-height: 44px; border: 1px solid #aebbc5; border-radius: 6px; padding: 8px 12px; background: #fff; color: #17212b; font: inherit; }
    .token-picker { display: inline-flex; align-items: center; gap: 8px; font-weight: 600; }
    .token-picker select { width: 9.5rem; min-height: 44px; border: 1px solid #aebbc5; border-radius: 6px; padding: 8px 10px; background: #fff; color: #17212b; font: inherit; }
    .token-hint { flex-basis: 100%; color: #52616d; font-size: .94rem; }
    .model-note { color: #704b0c; font-size: .94rem; margin: 10px 0 0; max-width: 800px; line-height: 1.55; }
    .status { border: 1px solid #c1ccd4; border-radius: 999px; padding: 8px 14px; font-size: .94rem; white-space: nowrap; background: #fff; }
    .status[data-kind="ok"] { color: #176b45; border-color: #9bd3b5; background: #effaf3; }
    .status[data-kind="busy"] { color: #7a5311; border-color: #e7c77e; background: #fff9e9; }
    .status[data-kind="error"] { color: #9d2c2c; border-color: #e1aaaa; background: #fff2f2; }
    .status[data-kind="warning"] { color: #7a5311; border-color: #e7c77e; background: #fff9e9; }
    .warning { margin: 0 32px 16px; padding: 13px 15px; border-left: 4px solid #c98516; background: #fff8e8; color: #63470f; font-size: .98rem; line-height: 1.55; }
    main { flex: 1; display: flex; flex-direction: column; min-height: 0; padding: 0 32px 26px; }
    .messages { flex: 1; min-height: 380px; overflow-y: auto; padding: 14px 0 22px; }
    .empty { color: #63737f; text-align: center; padding: 96px 16px; font-size: 1.08rem; }
    .message { display: flex; margin: 14px 0; }
    .message.user { justify-content: flex-end; }
    .bubble { max-width: min(860px, 90%); padding: 14px 18px; border-radius: 8px; font-size: 1.06rem; line-height: 1.72; white-space: pre-wrap; overflow-wrap: anywhere; }
    .message.user .bubble { color: #fff; background: #1f5f8b; }
    .message.assistant .bubble { background: #fff; border: 1px solid #d7dfe5; }
    .composer { border-top: 1px solid #cbd5dc; padding-top: 18px; }
    textarea { width: 100%; min-height: 118px; max-height: 300px; resize: vertical; border: 1px solid #aebbc5; border-radius: 6px; padding: 14px 16px; font: inherit; font-size: 1.06rem; line-height: 1.6; background: #fff; }
    textarea:focus { outline: 2px solid #7eb3d5; outline-offset: 1px; border-color: #4f8fb8; }
    .controls { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-top: 12px; }
    .hint { color: #52616d; font-size: .94rem; }
    .buttons { display: flex; gap: 10px; }
    button { min-height: 44px; border: 1px solid #9eafbb; border-radius: 6px; padding: 9px 18px; font: inherit; font-size: 1rem; cursor: pointer; background: #fff; color: #17212b; }
    button:hover:not(:disabled) { background: #edf3f7; }
    button.primary { border-color: #1f5f8b; background: #1f5f8b; color: #fff; }
    button.primary:hover:not(:disabled) { background: #174b70; }
    button:disabled { opacity: .55; cursor: not-allowed; }
    @media (max-width: 760px) {
      :root { font-size: 17px; }
      header { align-items: stretch; flex-direction: column; gap: 16px; padding: 22px 18px 14px; }
      h1 { font-size: 1.65rem; }
      .model-picker { align-items: stretch; flex-direction: column; gap: 6px; }
      .model-picker select { min-width: 0; width: 100%; }
      .token-picker { align-items: stretch; flex-direction: column; }
      .token-picker select { width: 100%; }
      .warning { margin: 0 18px 14px; }
      main { padding: 0 18px 18px; }
      .messages { min-height: 300px; }
      .bubble { max-width: 94%; }
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
        <p class="subtle">教学实验模式 · 技术可运行性与中文质量分开记录 · <span id="model">读取模型中…</span></p>
        <div class="model-picker">
          <label for="profile-select">测试模型</label>
          <select id="profile-select" aria-label="选择测试模型"></select>
          <label class="token-picker" for="max-tokens">最大输出 token
            <select id="max-tokens" aria-label="选择最大输出 token" aria-describedby="token-hint"></select>
          </label>
          <span id="token-hint" class="token-hint">正在读取当前模型的输出预算…</span>
        </div>
        <p id="model-note" class="model-note">读取模型目录中…</p>
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
      const state = { busy: false, assistant: null, terminal: false, truncated: false, activeProfile: '', selectedProfile: '', profiles: [] };
      const messages = document.getElementById('messages');
      const empty = document.getElementById('empty');
      const input = document.getElementById('input');
      const send = document.getElementById('send');
      const clear = document.getElementById('clear');
      const status = document.getElementById('status');
      const model = document.getElementById('model');
      const profileSelect = document.getElementById('profile-select');
      const modelNote = document.getElementById('model-note');
      const maxTokens = document.getElementById('max-tokens');
      const tokenHint = document.getElementById('token-hint');
      const TOKEN_BUDGETS = [32, 64, 96, 128, 192, 256];

      function setStatus(text, kind) {
        status.textContent = text;
        status.dataset.kind = kind || 'busy';
      }

      function setBusy(value) {
        state.busy = value;
        send.disabled = value;
        clear.disabled = value;
        input.disabled = value;
        profileSelect.disabled = value;
        maxTokens.disabled = value;
      }

      function selectedProfileItem() {
        return state.profiles.find((candidate) => candidate.id === state.selectedProfile) || null;
      }

      function updateTokenControl(resetValue) {
        const item = selectedProfileItem();
        const maximum = Math.max(1, Number(item?.max_tokens) || 256);
        const fallback = Math.min(maximum, Math.max(1, Number(item?.default_max_tokens) || Math.min(128, maximum)));
        const current = Number(maxTokens.value);
        const budgets = TOKEN_BUDGETS.filter((budget) => budget <= maximum);
        if (!budgets.includes(maximum)) budgets.push(maximum);
        const selected = !resetValue && budgets.includes(current) ? current :
          (budgets.includes(fallback) ? fallback : budgets[budgets.length - 1]);
        maxTokens.replaceChildren();
        budgets.forEach((budget) => {
          const option = document.createElement('option');
          option.value = String(budget);
          option.textContent = budget + ' token';
          maxTokens.appendChild(option);
        });
        maxTokens.value = String(selected);
        tokenHint.textContent = '当前模型默认 ' + fallback + '，可选 ' + budgets.join(' / ') + ' token；token 不是汉字计数，达到上限时会保留已生成文本并标记截断。';
      }

      function requestedTokenBudget() {
        const item = selectedProfileItem();
        const maximum = Math.max(1, Number(item?.max_tokens) || 256);
        const value = Number(maxTokens.value);
        const budgets = TOKEN_BUDGETS.filter((budget) => budget <= maximum);
        if (!budgets.includes(maximum)) budgets.push(maximum);
        if (!budgets.includes(value)) {
          throw new Error('请选择当前模型允许的输出 token 档位。');
        }
        return value;
      }

      function updateModelNote() {
        const item = state.profiles.find((candidate) => candidate.id === state.selectedProfile);
        if (!item) return;
        const status = item.quality_status === 'failed' ? '中文质量未通过，但仍保留技术测试入口。' :
          item.quality_status === 'pending' ? '中文质量待人工审核；这不是运行门槛。' : '';
        const integrity = item.text_integrity_status === 'observed-issue' ?
          '历史文本完整性异常待复核；它不等同于中文语义质量失败。' : '';
        const availability = item.available === false ? ('当前工件不可用：' + (item.availability_reason || '请先导入完整本地缓存。')) : '';
        if (state.selectedProfile !== state.activeProfile) {
          modelNote.textContent = '已选择“' + item.display_name + '”。' + status + integrity + availability + '网页会停止当前 worker、加载该模型并清空会话；切换完成后才能发送消息。';
        } else {
          modelNote.textContent = '当前正在运行“' + item.display_name + '”。' + status + integrity + availability + (item.reason || '');
        }
      }

      function populateProfiles(items, active) {
        state.profiles = Array.isArray(items) ? items : [];
        state.activeProfile = active || '';
        profileSelect.replaceChildren();
        state.profiles.forEach((item) => {
          if (!item || typeof item.id !== 'string') return;
          const option = document.createElement('option');
          option.value = item.id;
          option.textContent = item.display_name + ' · ' + item.technical_status +
            (item.quality_status === 'failed' ? ' · 中文质量失败' : '') +
            (item.available === false && item.selectable ? ' · 工件未安装' : '');
          option.disabled = !item.selectable || item.available === false;
          option.title = item.reason || item.availability_reason || '';
          profileSelect.appendChild(option);
        });
        profileSelect.value = state.activeProfile;
        state.selectedProfile = state.activeProfile;
        updateTokenControl(true);
        updateModelNote();
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
          state.truncated = event.finish_reason === 'length';
          if (state.truncated) {
            setStatus('达到 ' + maxTokens.value + ' token 输出上限，已保留当前文本', 'warning');
          } else {
            setStatus('就绪', 'ok');
          }
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
        if (state.selectedProfile !== state.activeProfile) {
          setStatus('请先完成模型切换', 'error');
          return;
        }
        const text = input.value.trim();
        if (!text) return;
        addMessage('user', text);
        input.value = '';
        state.assistant = null;
        state.terminal = false;
        state.truncated = false;
        setBusy(true);
        setStatus('请求中…', 'busy');
        try {
          const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
            body: JSON.stringify({ message: text, stream: true, profile: state.selectedProfile, max_tokens: requestedTokenBudget() })
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
          if (status.dataset.kind !== 'error' && status.dataset.kind !== 'warning') {
            setStatus('就绪', 'ok');
          }
        } catch (error) {
          setStatus('请求失败', 'error');
          if (!state.assistant) state.assistant = addMessage('assistant', '');
          state.assistant.textContent = '错误：' + (error instanceof Error ? error.message : '请求失败');
        } finally {
          setBusy(false);
          input.focus();
        }
      }

      async function switchProfile() {
        const target = profileSelect.value;
        if (!target || target === state.activeProfile) {
          updateModelNote();
          return;
        }
        if (state.busy) {
          state.selectedProfile = state.activeProfile;
          profileSelect.value = state.activeProfile;
          setStatus('当前回复正在生成，完成后可切换模型。', 'busy');
          updateModelNote();
          return;
        }
        const item = state.profiles.find((candidate) => candidate.id === target);
        if (!item || !item.selectable || item.available === false) {
          profileSelect.value = state.activeProfile;
          state.selectedProfile = state.activeProfile;
          updateModelNote();
          return;
        }
        state.selectedProfile = target;
        updateModelNote();
        profileSelect.disabled = true;
        setBusy(true);
        setStatus('切换模型中…', 'busy');
        try {
          const response = await fetch('/api/select-model', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Accept': 'application/json' },
            body: JSON.stringify({ profile: target })
          });
          let payload = {};
          try { payload = await response.json(); } catch (_) {}
          if (!response.ok) {
            throw new Error(payload.error?.message || 'HTTP ' + response.status);
          }
          state.activeProfile = payload.active_profile || target;
          state.selectedProfile = state.activeProfile;
          profileSelect.value = state.activeProfile;
          updateTokenControl(true);
          renderHistory([]);
          setStatus('就绪', 'ok');
          updateModelNote();
        } catch (error) {
          state.selectedProfile = state.activeProfile;
          profileSelect.value = state.activeProfile;
          updateTokenControl(true);
          setStatus('切换失败', 'error');
          modelNote.textContent = (error instanceof Error ? error.message : '模型切换失败') + ' 当前模型未改变。';
        } finally {
          profileSelect.disabled = false;
          setBusy(false);
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
          populateProfiles(config.profiles, config.active_profile);
          if (Number.isInteger(config.max_characters)) input.maxLength = config.max_characters;
          renderHistory(history.messages);
          setStatus('就绪', 'ok');
        } catch (_) {
          setStatus('连接失败', 'error');
        }
      }

      document.getElementById('composer').addEventListener('submit', sendMessage);
      clear.addEventListener('click', clearConversation);
      profileSelect.addEventListener('change', switchProfile);
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
    try:
        catalog = load_catalog(Path(runtime_settings.catalog_path))
        catalog_index = catalog_by_id(Path(runtime_settings.catalog_path))
    except (CatalogError, OSError) as exc:
        raise TextChatConfigurationError("无法加载教学模型目录: %s" % exc) from exc
    if runtime_settings.active_profile not in catalog_index:
        raise TextChatConfigurationError(
            "active_profile 不在教学模型目录中: %s" % runtime_settings.active_profile
        )
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
    active_profile_state = {"value": runtime_settings.active_profile}
    managed_worker = llm is None

    def ensure_async_primitives() -> None:
        nonlocal body_read_slots, request_lock
        if body_read_slots is None or request_lock is None:
            asyncio.get_running_loop()
            body_read_slots = asyncio.Semaphore(8)
            request_lock = asyncio.Lock()

    async def refresh_active_profile() -> str:
        """Synchronize UI metadata with the worker after an operator restart."""

        if not managed_worker:
            return str(active_profile_state["value"])
        try:
            health = await _worker_health()
        except ModelSwitchError:
            return str(active_profile_state["value"])
        profile_id = health.get("profile")
        if (
            health.get("ready") is True
            and health.get("healthy") is True
            and isinstance(profile_id, str)
            and profile_id in catalog_index
        ):
            active_profile_state["value"] = profile_id
        return str(active_profile_state["value"])

    async def switch_worker_profile(target_profile: str) -> Mapping[str, Any]:
        """Switch between validated teaching workers with rollback on failure."""

        await refresh_active_profile()
        current_profile = str(active_profile_state["value"])
        if not managed_worker:
            if target_profile == current_profile:
                return {"active_profile": current_profile, "switched": False}
            raise ModelSwitchError("当前 UI 未连接受控 worker，不能切换模型。")
        entry = catalog_index.get(target_profile)
        if not isinstance(entry, Mapping) or entry.get("selectable") is not True:
            raise ModelSwitchError("该模型没有通过技术运行门，不能启动。")
        registry_path = _worker_registry_path(entry)
        try:
            registry = load_profiles(registry_path)
            target = registry.get(target_profile)
        except (OSError, ProfileError) as exc:
            raise ModelSwitchError("目标模型注册表无效。") from exc
        if target.is_conditional:
            raise ModelSwitchError("条件候选模型不能启动。")
        try:
            model_root = data_root()
            _verify_switch_cache(model_root, target)
        except (ArtifactError, OSError, ModelSwitchError) as exc:
            raise ModelSwitchError("目标模型缓存不可用：%s" % exc) from exc

        current_health: Optional[Mapping[str, Any]]
        try:
            current_health = await _worker_health()
        except ModelSwitchError:
            current_health = None
        if current_health is not None:
            if current_health.get("profile") != current_profile:
                raise ModelSwitchError("页面记录的模型与 worker 实际模型不一致，请刷新页面。")
            if current_health.get("busy") is True:
                raise ModelSwitchError("当前模型正在生成回复，请等待本轮完成。")
            if target_profile == current_profile:
                return {"active_profile": current_profile, "switched": False}
            worker_pid = current_health.get("worker_pid")
            if not isinstance(worker_pid, int) or not _is_managed_worker(worker_pid):
                raise ModelSwitchError("当前 worker 未通过受控进程校验。")
            await _stop_managed_worker(worker_pid)
        try:
            _, health_payload = await _start_managed_worker(
                target_profile,
                registry_path,
                timeout_seconds=runtime_settings.llm_timeout_seconds,
            )
        except Exception as switch_error:
            rollback_status = "not-needed" if current_health is None else "failed"
            rollback_error = ""
            if current_health is not None:
                try:
                    old_entry = catalog_index.get(current_profile)
                    if not isinstance(old_entry, Mapping):
                        raise ModelSwitchError("旧模型不在教学目录中。")
                    old_registry_path = _worker_registry_path(old_entry)
                    old_registry = load_profiles(old_registry_path)
                    old_profile = old_registry.get(current_profile)
                    _verify_switch_cache(model_root, old_profile)
                    await _start_managed_worker(
                        current_profile,
                        old_registry_path,
                        timeout_seconds=runtime_settings.llm_timeout_seconds,
                    )
                    rollback_status = "succeeded"
                except Exception as rollback_error_value:
                    rollback_error = str(rollback_error_value)
            if current_health is None:
                raise ModelSwitchError(
                    "模型启动失败，当前没有可回滚的 worker：%s" % switch_error,
                    rollback=rollback_status,
                ) from switch_error
            if rollback_status == "succeeded":
                raise ModelSwitchError(
                    "模型切换失败，已自动恢复 %s：%s" % (current_profile, switch_error),
                    rollback=rollback_status,
                ) from switch_error
            raise ModelSwitchError(
                "模型切换失败，旧模型恢复失败，服务已停止：%s%s"
                % (switch_error, ("；恢复错误：" + rollback_error) if rollback_error else ""),
                rollback=rollback_status,
            ) from switch_error

        active_profile_state["value"] = target_profile
        runtime_sessions.clear_all()
        return {
            "active_profile": target_profile,
            "switched": True,
            "health": dict(health_payload),
        }

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        ensure_async_primitives()
        await refresh_active_profile()
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
    app.state.model_catalog = catalog

    def public_catalog() -> list[dict[str, Any]]:
        return _catalog_with_availability(catalog)

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
        active_profile = await refresh_active_profile()
        return {
            "status": "ok",
            "service": "ascend-edge-chat-ui",
            "mode": "unauthenticated-lan-experiment",
            "warning": SECURITY_WARNING,
            "gateway": runtime_settings.gateway_url,
            "model": runtime_settings.model,
            "active_profile": active_profile,
            "model_selection": "controlled-worker-switch",
            "sessions": runtime_sessions.session_count,
        }

    @app.get("/api/config")
    async def api_config() -> dict[str, Any]:
        active_profile = await refresh_active_profile()
        return {
            "model": runtime_settings.model,
            "active_profile": active_profile,
            "model_selection": "controlled-worker-switch",
            "model_switching": True,
            "profiles": public_catalog(),
            "streaming": True,
            "max_messages": runtime_settings.max_messages,
            "max_characters": runtime_settings.max_characters,
            "warning": SECURITY_WARNING,
        }

    @app.get("/api/models")
    async def api_models() -> dict[str, Any]:
        """Return all classroom entries, including failed-quality experiments."""

        active_profile = await refresh_active_profile()
        return {
            "active_profile": active_profile,
            "selection": "controlled-worker-switch",
            "models": public_catalog(),
        }

    @app.post("/api/select-model")
    async def select_model(request: Request) -> JSONResponse:
        ensure_async_primitives()
        assert request_lock is not None
        try:
            body = await _read_json_object(
                request,
                runtime_settings.max_body_bytes,
                runtime_settings.body_timeout_seconds,
            )
        except TextChatRequestError as exc:
            return _error_response(exc.message, exc.status_code)
        if not isinstance(body, dict) or set(body) != {"profile"}:
            return _error_response("模型切换请求只支持 profile 字段。")
        target_profile = body.get("profile")
        if not isinstance(target_profile, str) or not target_profile.strip():
            return _error_response("profile 必须是非空字符串。")
        acquired = await _acquire_request_slot(
            request_lock, runtime_settings.model_switch_wait_seconds
        )
        if not acquired:
            return _error_response(
                "当前回复仍在生成，请等待回复完成后再切换模型。",
                409,
                "model_switch_busy",
            )
        try:
            result = await switch_worker_profile(target_profile.strip())
            return JSONResponse(result)
        except ModelSwitchError as exc:
            status = 502 if exc.rollback in {"succeeded", "failed"} else 409
            return _error_response(str(exc), status, "model_switch_failed")
        finally:
            request_lock.release()

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
        max_tokens: int,
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
                runtime_llm, prompt, max_tokens, runtime_settings.llm_timeout_seconds
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
            if finish_reason == "stop":
                _commit_turn(conversation, message, assistant_text)
            yield _sse(
                {
                    "type": "done",
                    "text": assistant_text,
                    "finish_reason": finish_reason,
                    "truncated": finish_reason == "length",
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
            message, stream, requested_profile, requested_max_tokens = await _parse_chat_request(
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
        if requested_profile is not None and requested_profile != active_profile_state["value"]:
            return _error_response(
                "当前 worker 运行的是 %s；请先在网页模型选择器中完成切换，再发送消息。"
                % active_profile_state["value"],
                409,
                "model_switch_required",
            )
        active_entry = catalog_index.get(active_profile_state["value"])
        if active_entry is None:
            return _error_response("当前模型不在教学目录中，拒绝发送请求。", 503)
        profile_max_tokens = int(active_entry["max_tokens"])
        profile_default_tokens = int(active_entry["default_max_tokens"])
        max_tokens = profile_default_tokens if requested_max_tokens is None else requested_max_tokens
        if max_tokens > profile_max_tokens:
            return _error_response(
                "当前模型只允许 max_tokens <= %d。" % profile_max_tokens,
                400,
            )
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
                        max_tokens,
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
                runtime_llm, prompt, max_tokens, runtime_settings.llm_timeout_seconds
            ):
                if delta:
                    assistant_characters += len(delta)
                    if assistant_characters > assistant_budget:
                        raise SessionLimitError("模型回复超过会话字符限制")
                    parts.append(delta)
            finish_reason = _validated_finish_reason(runtime_llm)
            assistant_text = "".join(parts).strip()
            if finish_reason == "stop":
                _commit_turn(conversation, message, assistant_text)
            response = JSONResponse(
                {
                    "message": assistant_text,
                    "model": runtime_settings.model,
                    "finish_reason": finish_reason,
                    "truncated": finish_reason == "length",
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
