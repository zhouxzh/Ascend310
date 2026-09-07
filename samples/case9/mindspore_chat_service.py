"""Loopback-only OpenAI-compatible service for MindSpore chat profiles.

The service is intentionally implemented with Python's standard library.  A
board worker can therefore run it in the existing MindSpore base environment
without adding another web framework or importing a Torch runtime.  Model
loading and generation are delegated to an injected provider (normally one
created by :func:`mindspore_chat_providers.create_provider`).

Only one request is allowed at a time.  The service is a candidate endpoint;
the public case9 gateway remains responsible for authentication and the
``case9-rag`` public model name.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import json
import logging
import math
from numbers import Integral
import os
import queue
import select
import socket
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Sequence, Tuple
import uuid

from mindspore_chat_providers import (
    DEFAULT_MAX_TOKENS,
    MAX_MAX_TOKENS,
    GenerationResult,
    ProviderBusy,
    ProviderRequestError,
    ProviderTimeout,
    ProviderUnavailable,
    ProviderError,
    create_provider,
)
from case9_model_profiles import quality_admission_check


LOGGER = logging.getLogger("case9.mindspore_chat.service")

# Stable internal endpoint/model contract.  The browser and public gateway do
# not receive profile IDs or model paths from request bodies.
MODEL_ID = "case9-active"
MAX_GENERATION_TOKENS = MAX_MAX_TOKENS
DEFAULT_GENERATION_TOKENS = DEFAULT_MAX_TOKENS
# The candidate MindSpore contract is deliberately narrower than a model's
# advertised context window.  Keeping this boundary in the HTTP/service layer
# prevents an injected provider (or a future loader) from bypassing the board
# acceptance contract.
MAX_CONTEXT_TOKENS = 1024
MAX_REQUEST_BYTES = 256 * 1024
MAX_MESSAGES = 32
MAX_MESSAGE_CHARACTERS = 24_000
REQUEST_BODY_TIMEOUT_SECONDS = 15.0
REQUEST_BODY_READ_CHUNK = 64 * 1024
CLIENT_WRITE_TIMEOUT_SECONDS = 30.0
JSON_COMPLETION_POLL_SECONDS = 0.1
JSON_COMPLETION_GRACE_SECONDS = 1.0
# A JSON request is executed by a daemon worker so that the HTTP handler can
# observe a peer FIN.  Keep a short, bounded replay window for the cancellation
# hook: the provider may not have installed its own generation thread when the
# handler first notices the disconnect.
REQUEST_CANCEL_REPLAY_INTERVAL_SECONDS = 0.05
REQUEST_CANCEL_REPLAY_DEADLINE_SECONDS = 2.0

# These are the only registry states that may be used to start a candidate
# worker.  A per-SoC validation record is authoritative when the board identity
# is known; aggregate metadata must not be promoted merely because another
# board passed its checks.
_ACTIVATABLE_ADMISSION_STATUSES = frozenset({"admitted", "experimental_dirty_base"})
_TECHNICAL_ADMISSION_STATUSES = frozenset(
    {
        "artifact_verified",
        "environment_verified",
        "load_passed",
        "json_passed",
        "sse_passed",
        "stability_passed",
        "performance_recorded",
        "experimental_dirty_base",
        "admitted",
    }
)


class _ManagedHTTPServer(HTTPServer):
    """Track ``serve_forever`` ownership so service teardown stays safe.

    ``HTTPServer.shutdown`` waits for the serving loop and therefore deadlocks
    before that loop has started or when called by its own handler thread.
    The small bit of explicit state here lets ``MindSporeChatService.close``
    distinguish those cases before closing the listener.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._case9_serving = threading.Event()
        self._case9_serving_thread_id: Optional[int] = None

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        self._case9_serving_thread_id = threading.get_ident()
        self._case9_serving.set()
        try:
            super().serve_forever(poll_interval=poll_interval)
        finally:
            self._case9_serving.clear()
            self._case9_serving_thread_id = None


@dataclass(frozen=True)
class CompletionRequest:
    model: str
    messages: List[Dict[str, str]]
    stream: bool
    max_tokens: int


class _RequestCancelled(Exception):
    """Internal signal used when a JSON peer closes before generation starts."""


class _RequestCancellation:
    """Per-request cancellation state shared by the HTTP and worker threads.

    ``threading.Thread.is_alive`` and a provider's generation pointer are not
    synchronized with the HTTP handler.  The lock makes the cancellation check
    and the transition to ``provider_call_started`` atomic, while the bounded
    replay watcher covers the tiny provider-initialization window after that
    transition.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cancelled = False
        self._provider_call_started = False
        self._replay_started = False
        self.done = threading.Event()

    def request_cancel(self) -> None:
        with self._lock:
            self._cancelled = True

    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def mark_provider_call_started(self) -> bool:
        """Atomically claim the right to enter the provider call."""

        with self._lock:
            if self._cancelled:
                return False
            self._provider_call_started = True
            return True

    @property
    def provider_call_started(self) -> bool:
        with self._lock:
            return self._provider_call_started

    def begin_replay(self) -> bool:
        with self._lock:
            if self._replay_started:
                return False
            self._replay_started = True
            return True


class RequestError(ValueError):
    """An OpenAI request failed validation."""

    def __init__(
        self,
        message: str,
        status_code: int = 400,
        code: str = "invalid_request_error",
    ) -> None:
        super().__init__(message)
        self.message = str(message)
        self.status_code = int(status_code)
        self.code = str(code)


def _strict_nonnegative_int(value: Any, name: str, *, upper: Optional[int] = None) -> int:
    """Convert an integer-like protocol value without truncation or booleans."""

    if isinstance(value, bool) or not isinstance(value, Integral):
        raise ValueError("%s must be an integer" % name)
    result = int(value)
    if result < 0:
        raise ValueError("%s must be non-negative" % name)
    if upper is not None and result > upper:
        raise ValueError("%s exceeds the configured limit" % name)
    return result


class MindSporeChatService:
    """Own a single provider and expose the candidate HTTP contract."""

    def __init__(
        self,
        provider: Any,
        *,
        profile: Any = None,
        auto_start: bool = True,
        model_id: str = MODEL_ID,
    ) -> None:
        self.provider = provider
        self.runtime = provider  # compatibility with the ACL service helpers
        self.profile = profile if profile is not None else getattr(provider, "profile", None)
        self.model_id = str(model_id)
        self.auto_start = bool(auto_start)
        self._request_lock = threading.Lock()
        self._failed_closed = False
        self._failure_reason: Optional[str] = None
        self._server: Optional[HTTPServer] = None
        # Avoid a second close after successful startup-error teardown while
        # still allowing a deliberately restarted service to own the provider
        # again.
        self._provider_closed = False

    @property
    def started(self) -> bool:
        ready = getattr(self.provider, "ready", None)
        if ready is not None:
            return bool(ready)
        return bool(getattr(self.provider, "started", False))

    @property
    def healthy(self) -> bool:
        if self._failed_closed:
            return False
        value = getattr(self.provider, "healthy", None)
        if value is not None:
            return bool(value)
        try:
            status = self.provider.status()
            return bool(status.get("healthy", status.get("ready", False)))
        except Exception:
            return False

    def make_server(self, host: str = "127.0.0.1", port: int = 8090) -> HTTPServer:
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("MindSpore chat service must remain loopback-only")
        server: Optional[HTTPServer] = None
        try:
            if self.auto_start:
                self.start()
            # Binding is part of service startup.  If it fails after a model
            # has been loaded, the provider must not remain resident without a
            # reachable owner (and a later model switch must not inherit it).
            server = _ManagedHTTPServer((host, int(port)), _handler_class())
            server.mindspore_chat_service = self  # type: ignore[attr-defined]
            self._server = server
            return server
        except BaseException:
            if server is not None:
                try:
                    server.server_close()
                except Exception:
                    LOGGER.warning("HTTP server cleanup failed after startup error", exc_info=True)
            # ``start`` can fail during model loading, and HTTPServer can fail
            # while binding an occupied/invalid port.  Both paths own the
            # provider at this point and therefore share the same cleanup.
            self.close()
            raise

    def start(self) -> None:
        if self._failed_closed:
            raise ProviderUnavailable(self._failure_reason or "MindSpore worker is unhealthy")
        self._provider_closed = False
        profile_status = _profile_value(self.profile, "status", default="")
        # Resolve the board before loading any MindSpore model so a profile
        # that passed on a different SoC cannot reach the provider.
        effective_status, observed_soc, admission_reason = _profile_admission(
            self.profile, observed_soc=_probe_current_soc()
        )
        if effective_status in {"blocked", "not-run"}:
            suffix = " for %s" % observed_soc if observed_soc else ""
            message = "selected MindSpore profile is %s%s" % (effective_status, suffix)
            if admission_reason:
                message += ": %s" % admission_reason
            self._mark_failure(message)
            self._close_provider_after_start_failure()
            raise ProviderUnavailable(message)
        if effective_status == "experimental_dirty_base" and os.environ.get("CASE9_ALLOW_EXPERIMENTAL") != "1":
            message = "selected MindSpore profile is experimental_dirty_base; explicit opt-in is required"
            self._mark_failure(message)
            self._close_provider_after_start_failure()
            raise ProviderUnavailable(message)
        if effective_status not in {"admitted", "experimental_dirty_base"}:
            message = "selected MindSpore profile is not activatable: %s" % (profile_status or "missing status")
            self._mark_failure(message)
            self._close_provider_after_start_failure()
            raise ProviderUnavailable(message)
        runtime_provider = _profile_value(self.profile, "runtime_provider", "provider", default="mindspore")
        if str(runtime_provider).strip().lower() != "mindspore":
            message = "unsupported runtime provider: %s" % runtime_provider
            self._mark_failure(message)
            self._close_provider_after_start_failure()
            raise ProviderUnavailable(message)
        load = getattr(self.provider, "load", None)
        try:
            if callable(load):
                load()
            else:
                start = getattr(self.provider, "start", None)
                if callable(start):
                    start()
            if not self.started or not self.healthy:
                raise ProviderUnavailable("MindSpore chat model is not ready")
            # Loading a model is not itself admission. Re-check the dynamic
            # board/placement gate after provider initialization, before the
            # worker can serve an endpoint.
            self._require_ready()
        except ProviderError as exc:
            self._mark_failure(str(exc))
            self._close_provider_after_start_failure()
            raise
        except BaseException as exc:
            # A loader may fail after allocating model/device resources.  Do
            # not leave a half-loaded worker alive merely because the original
            # exception is being translated for the HTTP launcher.  Catch
            # BaseException here so an interrupted startup also releases the
            # provider; native process faults still terminate the process and
            # cannot be handled by Python.
            self._mark_failure(str(exc))
            self._close_provider_after_start_failure()
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            raise ProviderUnavailable("MindSpore chat model failed to start: %s" % exc) from exc

    def _close_provider_after_start_failure(self) -> None:
        """Best-effort teardown for a provider that failed during ``start``.

        Keep the failure latch intact while releasing any partially allocated
        model/device state.  Provider teardown is deliberately isolated from
        the original exception so a cleanup defect cannot hide the startup
        failure that caused the worker to fail closed.
        """

        close = getattr(self.provider, "close", None)
        if not callable(close):
            return
        try:
            close()
            self._provider_closed = True
        except BaseException:
            LOGGER.warning("provider cleanup failed after startup error", exc_info=True)

    def close(self) -> None:
        # Release the listener even when the caller does not retain the server
        # object (for example, a model controller aborting a failed switch).
        server = self._server
        self._server = None
        if server is not None:
            try:
                self._close_server(server)
            except Exception:
                LOGGER.warning("HTTP server close failed", exc_info=True)
        self.cancel()
        close = getattr(self.provider, "close", None)
        if callable(close) and not self._provider_closed:
            try:
                close()
                self._provider_closed = True
            except Exception:
                LOGGER.warning("provider close failed", exc_info=True)

    @staticmethod
    def _close_server(server: HTTPServer) -> None:
        """Stop a running loop before closing its listener, without deadlock."""

        managed = isinstance(server, _ManagedHTTPServer)
        serving = managed and server._case9_serving.is_set()
        same_thread = managed and server._case9_serving_thread_id == threading.get_ident()
        if serving and same_thread:
            # ``shutdown`` waits for this handler to return.  Move it to a
            # helper so the current request can complete, then release the
            # socket from that helper after the serving loop exits.
            def stop_from_helper() -> None:
                try:
                    server.shutdown()
                finally:
                    server.server_close()

            threading.Thread(
                target=stop_from_helper,
                name="case9-ms-server-close",
                daemon=True,
            ).start()
            return
        if serving:
            server.shutdown()
        server.server_close()

    def cancel(self, *, force: bool = False) -> None:
        cancel = getattr(self.provider, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                LOGGER.warning("provider cancellation failed", exc_info=True)
        if force:
            watchdog = getattr(self.provider, "cancel_and_watchdog", None)
            if callable(watchdog):
                try:
                    watchdog()
                except Exception:
                    LOGGER.warning("provider disconnect watchdog failed", exc_info=True)

    def _cancel_request(self, cancellation: _RequestCancellation) -> None:
        """Cancel one detached JSON request and replay across provider startup.

        The first cancellation can legitimately observe no provider generation
        thread: the HTTP worker may still be between ``start()`` and the
        provider's own thread registration.  Recording the request cancellation
        prevents a late call from starting, and the short replay window catches
        a provider thread registered immediately after the first hook returned.
        """

        cancellation.request_cancel()
        # Preserve the existing immediate cancellation behavior for providers
        # that expose a cooperative hook.
        self.cancel(force=True)
        if not cancellation.begin_replay():
            return

        def replay() -> None:
            deadline = time.monotonic() + REQUEST_CANCEL_REPLAY_DEADLINE_SECONDS
            while not cancellation.done.wait(REQUEST_CANCEL_REPLAY_INTERVAL_SECONDS):
                # ``force=True`` is intentionally repeated only while the
                # request is still alive.  It is harmless before provider
                # registration and invokes the provider watchdog afterwards.
                self.cancel(force=True)
                if time.monotonic() >= deadline:
                    self._mark_failure(
                        "MindSpore generation did not stop after client disconnect"
                    )
                    self.cancel(force=True)
                    return

        threading.Thread(
            target=replay,
            name="case9-ms-cancel-replay",
            daemon=True,
        ).start()

    def health(self) -> Dict[str, Any]:
        try:
            value = self.provider.status() if callable(getattr(self.provider, "status", None)) else {}
            status = dict(value) if isinstance(value, Mapping) else {}
        except Exception as exc:
            status = {"last_error": str(exc)}
        status.setdefault("provider", "mindspore")
        status.setdefault("profile", _profile_value(self.profile, "id", "profile_id", default=None))
        status.setdefault("model", self.model_id)
        status["model_id"] = self.model_id
        status["worker_pid"] = _worker_pid()
        status["busy"] = bool(status.get("busy", False) or self._request_lock.locked())
        status.setdefault("cache_cleanup", "idle" if not status["busy"] else "in_progress")
        status.setdefault("cache_cleared", not status["busy"])
        status["healthy"] = bool(status.get("healthy", status.get("ready", False))) and not self._failed_closed
        status["ready"] = bool(status.get("ready", self.started)) and status["healthy"]
        # Report both the aggregate registry status and the status for the
        # device that is actually visible to this worker.  A profile may be
        # blocked on one SoC while experimentally usable on another; exposing
        # the aggregate value alone made the health endpoint misleading.
        observed_hint = _status_soc(status)
        if not observed_hint and (_profile_board_targets(self.profile) or _profile_value(self.profile, "validation", default={})):
            observed_hint = _probe_current_soc()
        effective_status, observed_soc, admission_reason = _profile_admission(
            self.profile, observed_soc=observed_hint
        )
        aggregate_status = str(_profile_value(self.profile, "status", default="")).strip().lower()
        status["profile_status"] = aggregate_status
        status["admission"] = effective_status or aggregate_status or status.get("admission", "unknown")
        status["admission_status"] = status["admission"]
        status["admission_reason"] = admission_reason
        status["board_soc"] = observed_soc or status.get("board_soc") or "unknown"
        target = _profile_board_for_soc(self.profile, observed_soc)
        if target is not None:
            status["board_tier"] = target.get("tier")
        candidate_kind = str(
            _profile_value(self.profile, "candidate_kind", default="native_mindspore")
        ).strip().lower() or "native_mindspore"
        status["candidate_kind"] = candidate_kind
        # Keep this identity authoritative from the validated registry rather
        # than trusting an arbitrary provider status field.  ``modelctl`` uses
        # the explicit boolean as a fail-closed admission check.
        profile_conditional = _profile_flag(
            _profile_value(self.profile, "is_conditional", default=False)
        )
        runtime_provider = str(
            _profile_value(self.profile, "runtime_provider", "provider", default="mindspore")
        ).strip().lower()
        status["conditional"] = (
            bool(profile_conditional)
            or candidate_kind not in {"native_mindspore", "conditional"}
            or candidate_kind == "conditional"
            or runtime_provider != "mindspore"
        )
        status["admission_soc"] = observed_soc or None
        status["admission_allowed"] = status["admission"] in _ACTIVATABLE_ADMISSION_STATUSES
        if status["admission"] == "experimental_dirty_base" and os.environ.get("CASE9_ALLOW_EXPERIMENTAL") != "1":
            status["admission_allowed"] = False
        # A MindSpore context targeting Ascend is necessary but not sufficient
        # evidence: a custom model can still expose CPU/GPU tensors.  The
        # provider reports explicit placement when it can inspect it.  Keep
        # lightweight injected providers compatible when the field is absent,
        # but fail closed for an actual provider that reports an unverified or
        # rejected placement.
        placement_status = str(status.get("placement_status", "")).strip().lower()
        if placement_status and placement_status not in {"verified", "not_applicable"}:
            status["admission_allowed"] = False
            status["healthy"] = False
            status["ready"] = False
            status["admission_reason"] = (
                str(status.get("admission_reason", "")).rstrip(". ")
                + "; model placement is not verified"
            ).lstrip("; ")
        if (
            effective_status in {"blocked", "not-run"}
            or (observed_soc and not status["admission_allowed"])
        ):
            status["healthy"] = False
            status["ready"] = False
        if self._failure_reason:
            status["last_error"] = self._failure_reason
        return status

    def models(self) -> Dict[str, Any]:
        # Re-evaluate the board-specific registry gate for every admission
        # surface. ``provider.healthy`` alone cannot distinguish a model that
        # loaded on the wrong SoC (or lost placement evidence) from one that
        # is actually eligible on this board.
        self._require_ready()
        return {
            "object": "list",
            "data": [
                {
                    "id": self.model_id,
                    "object": "model",
                    "owned_by": "case9-mindspore",
                    "permission": [],
                }
            ],
        }

    def complete(self, request: CompletionRequest) -> Dict[str, Any]:
        """Complete a request using the public, backwards-compatible API."""

        return self._complete_impl(request, cancellation=None)

    def _complete_impl(
        self,
        request: CompletionRequest,
        *,
        cancellation: Optional[_RequestCancellation],
    ) -> Dict[str, Any]:
        """Internal completion path with an optional HTTP cancellation barrier."""

        if cancellation is not None and cancellation.is_cancelled():
            raise _RequestCancelled()
        self._require_ready()
        try:
            with self._serial_request():
                self._validate_budget(request)
                if cancellation is not None and not cancellation.mark_provider_call_started():
                    raise _RequestCancelled()
                result = self.provider.complete(request.messages, request.max_tokens)
        except _RequestCancelled:
            raise
        except ProviderBusy as exc:
            raise RequestError(str(exc), 429, "rate_limit_exceeded") from exc
        except ProviderRequestError as exc:
            raise RequestError(str(exc), 400, "invalid_request_error") from exc
        except ProviderTimeout as exc:
            self._mark_failure(str(exc))
            raise RequestError(str(exc), 504, "timeout") from exc
        except ProviderUnavailable as exc:
            self._mark_failure(str(exc))
            raise RequestError(str(exc), 503, "model_unavailable") from exc
        except Exception as exc:
            self._mark_failure(str(exc))
            LOGGER.exception("MindSpore completion failed")
            raise RequestError("MindSpore model inference failed", 500, "server_error") from exc
        normalized = _normalize_result(result)
        return _completion_payload(_new_id(), normalized, self.model_id)

    def stream(self, request: CompletionRequest) -> Iterable[Dict[str, Any]]:
        request_id = _new_id()
        first = True
        previous = ""
        prompt_tokens = 0
        completion_tokens = 0
        finish_reason = "length"
        self._require_ready()
        try:
            prompt_tokens = self._validate_budget(request)
            with self._serial_request():
                stream_method = getattr(self.provider, "stream", None)
                if not callable(stream_method):
                    result = self.provider.complete(request.messages, request.max_tokens)
                    events: Iterable[Any] = [_normalize_result(result)]
                else:
                    events = stream_method(request.messages, request.max_tokens)
                provider_stream_mode = _provider_stream_mode(self.provider)
                for event in events:
                    text, count, reason = _stream_event(event)
                    event_mode = _stream_mode(event)
                    if event_mode == "auto":
                        event_mode = provider_stream_mode
                    completion_tokens = max(completion_tokens, count)
                    if reason:
                        finish_reason = reason
                    # The provider contract is cumulative snapshots.  A
                    # provider that emits tokenizer fragments must explicitly
                    # declare ``stream_mode=fragment``; this avoids guessing
                    # when a model revises an already-emitted UTF-8 boundary.
                    delta_text, previous = _stream_delta(previous, text, event_mode)
                    delta: Dict[str, str] = {"role": "assistant"} if first else {}
                    if delta_text:
                        delta["content"] = delta_text
                    if first or delta_text:
                        yield _chunk_payload(request_id, self.model_id, delta, None)
                    first = False
                provider_status = self.provider.status() if callable(getattr(self.provider, "status", None)) else {}
                if isinstance(provider_status, Mapping):
                    # A streaming provider may yield decoded text fragments,
                    # which are not a token-counting unit. Prefer its final
                    # sequence-derived count for the OpenAI terminal usage.
                    reported_tokens = provider_status.get("last_completion_tokens")
                    if isinstance(reported_tokens, int) and not isinstance(reported_tokens, bool):
                        completion_tokens = max(0, reported_tokens)
                    reason = provider_status.get("last_finish_reason")
                    if reason in {"stop", "length"}:
                        finish_reason = str(reason)
                    reported_prompt = provider_status.get("last_prompt_tokens")
                    if isinstance(reported_prompt, int) and not isinstance(reported_prompt, bool):
                        prompt_tokens = max(0, reported_prompt)
                if completion_tokens >= request.max_tokens and finish_reason != "stop":
                    finish_reason = "length"
        except ProviderBusy as exc:
            yield _stream_error(str(exc), "rate_limit_exceeded")
            return
        except ProviderRequestError as exc:
            yield _stream_error(str(exc), "invalid_request_error")
            return
        except ProviderTimeout as exc:
            self._mark_failure(str(exc))
            yield _stream_error(str(exc), "timeout")
            return
        except ProviderUnavailable as exc:
            self._mark_failure(str(exc))
            yield _stream_error(str(exc), "model_unavailable")
            return
        except (BrokenPipeError, ConnectionResetError, socket.timeout) as exc:
            self.cancel()
            LOGGER.info("MindSpore client disconnected: %s", type(exc).__name__)
            return
        except Exception:
            self._mark_failure("MindSpore streaming inference failed")
            LOGGER.exception("MindSpore streaming completion failed")
            yield _stream_error("MindSpore model inference failed", "server_error")
            return
        if first:
            yield _chunk_payload(request_id, self.model_id, {"role": "assistant"}, None)
        yield _chunk_payload(
            request_id,
            self.model_id,
            {},
            finish_reason,
            usage={
                "prompt_tokens": int(prompt_tokens),
                "completion_tokens": int(completion_tokens),
                "total_tokens": int(prompt_tokens + completion_tokens),
            },
        )

    def _require_ready(self) -> None:
        if self._failed_closed:
            raise ProviderUnavailable(self._failure_reason or "MindSpore chat model is not ready")

        # ``health`` is the single source of truth for the observed SoC,
        # per-board validation status, candidate kind, and placement evidence.
        # The registry always supplies board_targets/validation for production
        # profiles. Tiny injected providers used by protocol tests may omit
        # those fields; their ordinary ready/healthy contract remains valid.
        status = self.health()
        explicit_board_contract = bool(
            _profile_board_targets(self.profile)
            or _profile_value(self.profile, "validation", default={})
        )
        admission = str(status.get("admission", "")).strip().lower()
        admission_allowed = status.get("admission_allowed") is True
        placement = str(status.get("placement_status", "")).strip().lower()
        placement_bad = bool(placement) and placement not in {"verified", "not_applicable"}
        base_ready = bool(status.get("ready")) and bool(status.get("healthy"))
        admission_bad = explicit_board_contract and not admission_allowed
        terminal_bad = admission in {"blocked", "not-run"} or bool(status.get("conditional"))

        if not self.started or not self.healthy or not base_ready or admission_bad or terminal_bad or placement_bad:
            reasons: List[str] = []
            if not self.started or not base_ready or not self.healthy:
                reasons.append("MindSpore chat model is not ready")
            if admission_bad or terminal_bad:
                reason = str(status.get("admission_reason") or "profile admission is not allowed")
                reasons.append(
                    "profile admission %s%s" % (
                        admission or "blocked",
                        ": " + reason if reason else "",
                    )
                )
            if placement_bad:
                reasons.append("model placement is not verified")
            message = "; ".join(dict.fromkeys(reasons)) or "MindSpore chat model is not ready"
            # Once an explicit board/placement gate fails, fail closed for the
            # lifetime of this worker. This prevents a later request from
            # bypassing a wrong-SoC or unsafe placement decision.
            self._mark_failure(message)
            raise ProviderUnavailable(message)

    def _validate_budget(self, request: CompletionRequest) -> int:
        """Run the authoritative tokenizer/context check before inference."""

        # ``CompletionRequest`` is also a public compatibility type and can be
        # constructed without going through ``_parse_request``.  Re-apply the
        # wire constraints here so direct callers cannot bypass the same
        # max-token boundary used by the HTTP endpoint.
        if not isinstance(request, CompletionRequest):
            raise ProviderRequestError("invalid completion request")
        if request.model != MODEL_ID:
            raise ProviderRequestError("model is not available")
        try:
            requested_max_tokens = _strict_nonnegative_int(
                request.max_tokens, "max_tokens", upper=MAX_GENERATION_TOKENS
            )
        except ValueError as exc:
            raise ProviderRequestError(str(exc)) from exc
        if requested_max_tokens < 1:
            raise ProviderRequestError("max_tokens must be between 1 and %d" % MAX_GENERATION_TOKENS)

        counter = getattr(self.provider, "count_tokens", None)
        if not callable(counter):
            # The context boundary must be checked with the selected
            # tokenizer.  Treat a provider without that contract as
            # unavailable instead of allowing an unbounded request through.
            raise ProviderUnavailable("MindSpore provider does not expose count_tokens")
        try:
            prompt_value = counter(request.messages)
            prompt_tokens = _strict_nonnegative_int(prompt_value, "prompt_tokens")
        except ProviderRequestError:
            raise
        except ProviderUnavailable:
            raise
        except ValueError as exc:
            raise ProviderRequestError(str(exc)) from exc
        except Exception as exc:
            raise ProviderRequestError("tokenizer could not encode the request") from exc
        context = getattr(self.provider, "context_length", None)
        if context is None:
            context = _profile_value(
                self.profile, "context_length", "context_window", default=MAX_CONTEXT_TOKENS
            )
        try:
            context = _strict_nonnegative_int(context, "context_length")
        except ValueError as exc:
            raise ProviderUnavailable("invalid MindSpore context length") from exc
        # A profile may advertise a larger model context, but the candidate
        # service contract is fixed at 1024 until a separately audited graph
        # and acceptance record expands it.
        context = min(context, MAX_CONTEXT_TOKENS)
        if prompt_tokens + requested_max_tokens > context:
            raise ProviderRequestError(
                "prompt plus max_tokens exceeds the %d-token context limit" % context
            )
        return prompt_tokens

    @contextmanager
    def _serial_request(self) -> Iterator[None]:
        if not self._request_lock.acquire(blocking=False):
            raise ProviderBusy("MindSpore model is busy")
        try:
            yield
        finally:
            self._request_lock.release()

    def _mark_failure(self, reason: str) -> None:
        self._failed_closed = True
        self._failure_reason = str(reason)
        cancel = getattr(self.provider, "cancel", None)
        if callable(cancel):
            try:
                cancel()
            except Exception:
                LOGGER.debug("provider cancel after failure failed", exc_info=True)

    def generation_deadline(self) -> float:
        """Return a bounded total deadline for a synchronous completion."""

        value = getattr(self.provider, "generation_timeout", 300.0)
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = 300.0
        if not math.isfinite(value):
            value = 300.0
        return max(0.1, min(value + JSON_COMPLETION_GRACE_SECONDS, 605.0))


# Kept as an alias for callers following the naming used by the ACL services.
MindSporeChatHttpService = MindSporeChatService
MindSporeHttpService = MindSporeChatService


def _handler_class() -> Any:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "Case9MindSpore/1.0"

        @property
        def service(self) -> MindSporeChatService:
            return self.server.mindspore_chat_service  # type: ignore[attr-defined]

        def log_message(self, fmt: str, *args: Any) -> None:
            LOGGER.info("peer=%s " + fmt, self.client_address[0], *args)

        def do_GET(self) -> None:
            if self.path in {"/health", "/healthz"}:
                status = self.service.health()
                self._json(HTTPStatus.OK if status.get("ready") else HTTPStatus.SERVICE_UNAVAILABLE, status)
            elif self.path == "/v1/models":
                try:
                    self._json(HTTPStatus.OK, self.service.models())
                except ProviderUnavailable as exc:
                    self._error(str(exc), 503, "model_unavailable")
            else:
                self._error("Not found", 404, "not_found")

        def do_POST(self) -> None:
            if self.path != "/v1/chat/completions":
                self._error("Not found", 404, "not_found")
                return
            try:
                request = _parse_request(self._body())
                if request.stream:
                    self._sse(request)
                else:
                    outcome, value = self._complete_with_disconnect_watchdog(request)
                    if outcome == "disconnected":
                        return
                    if outcome == "timeout":
                        # The detached generation thread may still be inside a
                        # non-interruptible MindSpore call.  Mark the service
                        # failed-closed before returning the HTTP error so a
                        # second request cannot enter while cancellation is
                        # being replayed or the process watchdog is pending.
                        self.service._mark_failure(
                            "MindSpore JSON completion exceeded total deadline"
                        )
                        raise RequestError(
                            "MindSpore model inference timed out",
                            504,
                            "timeout",
                        )
                    if outcome == "error":
                        raise value
                    self._json(HTTPStatus.OK, value)
            except RequestError as exc:
                self._error(exc.message, exc.status_code, exc.code)
            except ProviderUnavailable as exc:
                self._error(str(exc), 503, "model_unavailable")
            except (BrokenPipeError, ConnectionResetError):
                self.service.cancel(force=True)

        def _socket_disconnected(self) -> bool:
            """Probe for a peer FIN without consuming a pipelined byte."""

            try:
                readable, _, _ = select.select([self.connection], [], [], 0)
            except (OSError, ValueError):
                return True
            if not readable:
                return False
            try:
                data = self.connection.recv(1, socket.MSG_PEEK)
            except (BlockingIOError, InterruptedError):
                return False
            except OSError:
                return True
            return data == b""

        def _complete_with_disconnect_watchdog(
            self, request: CompletionRequest
        ) -> Tuple[str, Any]:
            """Run blocking generation off the HTTP handler thread.

            The provider's generation itself remains serialized.  Moving just
            the call to a daemon thread lets this handler observe a client
            close and invoke the provider's process-level watchdog instead of
            holding an abandoned NPU request until the model returns.
            """

            result_queue: "queue.Queue[Tuple[str, Any]]" = queue.Queue(maxsize=1)
            cancellation = _RequestCancellation()

            def run() -> None:
                # Mark the worker as observable before it can enter the service
                # path.  The request token then closes the race where the
                # handler observes a FIN before ``provider.complete`` starts.
                try:
                    result = self.service._complete_impl(request, cancellation=cancellation)
                    result_queue.put(("ok", result))
                except _RequestCancelled:
                    try:
                        result_queue.put(("cancelled", None))
                    except queue.Full:
                        LOGGER.debug("cancelled completion result was abandoned")
                except BaseException as exc:  # propagate provider failures
                    try:
                        result_queue.put(("error", exc))
                    except queue.Full:
                        LOGGER.debug("completion result was abandoned", exc_info=True)
                finally:
                    cancellation.done.set()

            thread = threading.Thread(
                target=run,
                name="case9-ms-json-complete",
                daemon=True,
            )
            thread.start()
            deadline = time.monotonic() + self.service.generation_deadline()
            while True:
                try:
                    outcome, value = result_queue.get(timeout=JSON_COMPLETION_POLL_SECONDS)
                    if outcome == "cancelled":
                        return "disconnected", None
                    return outcome, value
                except queue.Empty:
                    if self._socket_disconnected():
                        self.close_connection = True
                        self.service._cancel_request(cancellation)
                        LOGGER.info("MindSpore JSON client disconnected")
                        return "disconnected", None
                    if time.monotonic() >= deadline:
                        self.close_connection = True
                        self.service._cancel_request(cancellation)
                        LOGGER.error("MindSpore JSON completion exceeded total deadline")
                        return "timeout", None

        def _body(self) -> Mapping[str, Any]:
            if self.headers.get("Transfer-Encoding", "").lower() not in {"", "identity"}:
                raise RequestError("Chunked request bodies are not supported", 411, "length_required")
            try:
                length = int(self.headers.get("Content-Length", "-1"))
            except ValueError as exc:
                raise RequestError("Invalid Content-Length") from exc
            if length < 0:
                raise RequestError("Content-Length is required", 411, "length_required")
            if length > MAX_REQUEST_BYTES:
                raise RequestError("Request body exceeds the configured limit", 413, "request_too_large")
            if "application/json" not in self.headers.get("Content-Type", "").lower():
                raise RequestError("Content-Type must be application/json", 415, "unsupported_media_type")
            data = _read_request_body(self.rfile, self.connection, length)
            if len(data) != length:
                raise RequestError("Incomplete request body", 400, "invalid_request_error")
            try:
                value = json.loads(data.decode("utf-8"), parse_constant=_reject_constant)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise RequestError("Request body is not valid JSON") from exc
            if not isinstance(value, Mapping):
                raise RequestError("Request body must be a JSON object")
            return value

        @contextmanager
        def _write_deadline(self) -> Iterator[None]:
            previous = self.connection.gettimeout()
            self.connection.settimeout(CLIENT_WRITE_TIMEOUT_SECONDS)
            try:
                yield
            finally:
                try:
                    self.connection.settimeout(previous)
                except OSError:
                    self.close_connection = True

        def _sse(self, request: CompletionRequest) -> None:
            # Preflight before sending headers so malformed/unready requests
            # receive a normal JSON error rather than a half-open SSE stream.
            try:
                self.service._require_ready()
                self.service._validate_budget(request)
            except ProviderRequestError as exc:
                self._error(str(exc), 400, "invalid_request_error")
                return
            except ProviderUnavailable as exc:
                self._error(str(exc), 503, "model_unavailable")
                return
            self.close_connection = True
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store, private")
            self.send_header("X-Accel-Buffering", "no")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                with self._write_deadline():
                    for payload in self.service.stream(request):
                        self.wfile.write(b"data: " + _json_bytes(payload) + b"\n\n")
                        self.wfile.flush()
                    # Always terminate an HTTP-200 SSE stream, including when
                    # the provider emitted a structured error event.  Without
                    # this sentinel clients can wait indefinitely after a
                    # recoverable generation failure.
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, socket.timeout, TimeoutError) as exc:
                self.close_connection = True
                self.service.cancel(force=True)
                LOGGER.info("MindSpore SSE client disconnected: %s", type(exc).__name__)

        def _json(self, status: Any, payload: Mapping[str, Any]) -> None:
            body = _json_bytes(payload)
            self.close_connection = True
            self.send_response(int(status))
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store, private")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                with self._write_deadline():
                    self.wfile.write(body)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, socket.timeout, TimeoutError) as exc:
                self.close_connection = True
                self.service.cancel(force=True)
                LOGGER.info("MindSpore JSON client disconnected: %s", type(exc).__name__)

        def _error(self, message: str, status: int, code: str) -> None:
            self._json(status, _error_payload(message, code, status))

    return Handler


def make_server(host: str, port: int, service: MindSporeChatService) -> HTTPServer:
    """Compatibility factory matching the older ACL service modules."""

    return service.make_server(host, port)


def _parse_request(raw: Mapping[str, Any]) -> CompletionRequest:
    allowed = {"model", "messages", "stream", "max_tokens", "temperature", "top_p"}
    if set(raw) - allowed:
        raise RequestError("Unsupported chat completion field")
    model = raw.get("model")
    if not isinstance(model, str) or not model.strip():
        raise RequestError("model must be a non-empty string")
    if model != MODEL_ID:
        raise RequestError("Model %r is not available" % model, 404, "model_not_found")
    messages = raw.get("messages")
    if not isinstance(messages, list) or not messages or len(messages) > MAX_MESSAGES:
        raise RequestError("messages must contain between 1 and %d items" % MAX_MESSAGES)
    normalized: List[Dict[str, str]] = []
    total_chars = 0
    for index, item in enumerate(messages):
        if not isinstance(item, Mapping) or set(item) - {"role", "content"}:
            raise RequestError("messages[%d] is invalid" % index)
        role, content = item.get("role"), item.get("content")
        if not isinstance(role, str) or role not in {"system", "user", "assistant"}:
            raise RequestError("messages[%d].role is not supported" % index)
        if not isinstance(content, str) or not content:
            raise RequestError("messages[%d].content must be non-empty text" % index)
        if len(content) > MAX_MESSAGE_CHARACTERS:
            raise RequestError("message content is too long", 413, "request_too_large")
        normalized.append({"role": role, "content": content})
        total_chars += len(content)
    if total_chars > MAX_MESSAGE_CHARACTERS * 2:
        raise RequestError("message content is too long", 413, "request_too_large")
    if not any(item["role"] == "user" for item in normalized):
        raise RequestError("at least one user message is required")
    stream = raw.get("stream", False)
    if not isinstance(stream, bool):
        raise RequestError("stream must be a boolean")
    max_tokens = raw.get("max_tokens", DEFAULT_GENERATION_TOKENS)
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= MAX_GENERATION_TOKENS:
        raise RequestError("max_tokens must be between 1 and %d" % MAX_GENERATION_TOKENS)
    _greedy_parameter(raw.get("temperature"), "temperature", 0.0)
    _greedy_parameter(raw.get("top_p"), "top_p", 1.0)
    return CompletionRequest(MODEL_ID, normalized, stream, int(max_tokens))


def _greedy_parameter(value: Any, name: str, expected: float) -> None:
    if value is None:
        return
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RequestError("only greedy decoding is supported; %s must be %s" % (name, expected))
    if not math.isfinite(float(value)) or float(value) != expected:
        raise RequestError("only greedy decoding is supported; %s must be %s" % (name, expected))


def _read_request_body(stream: Any, connection: Any, length: int) -> bytes:
    previous = connection.gettimeout()
    deadline = time.monotonic() + REQUEST_BODY_TIMEOUT_SECONDS
    chunks: List[bytes] = []
    remaining = int(length)
    try:
        while remaining:
            left = deadline - time.monotonic()
            if left <= 0:
                raise RequestError("Request body read timed out", 408, "request_timeout")
            connection.settimeout(left)
            reader = getattr(stream, "read1", None)
            if not callable(reader):
                reader = stream.read
            try:
                chunk = reader(min(remaining, REQUEST_BODY_READ_CHUNK))
            except (socket.timeout, TimeoutError) as exc:
                raise RequestError("Request body read timed out", 408, "request_timeout") from exc
            if not chunk:
                break
            chunks.append(bytes(chunk))
            remaining -= len(chunk)
    finally:
        try:
            connection.settimeout(previous)
        except OSError:
            LOGGER.debug("could not restore request socket timeout", exc_info=True)
    return b"".join(chunks)


def _completion_payload(request_id: str, result: GenerationResult, model: str) -> Dict[str, Any]:
    return {
        "id": request_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": result.text},
                "finish_reason": result.finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": int(result.prompt_tokens),
            "completion_tokens": int(result.completion_tokens),
            "total_tokens": int(result.prompt_tokens + result.completion_tokens),
        },
    }


def _chunk_payload(
    request_id: str,
    model: str,
    delta: Mapping[str, str],
    finish_reason: Optional[str],
    usage: Optional[Mapping[str, int]] = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "id": request_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": dict(delta), "finish_reason": finish_reason}],
    }
    if usage is not None:
        payload["usage"] = {key: int(value) for key, value in usage.items()}
    return payload


def _stream_error(message: str, code: str) -> Dict[str, Any]:
    return {"error": {"message": str(message), "type": "invalid_request_error" if code.startswith("invalid") else "server_error", "code": code}}


def _error_payload(message: str, code: str, status: int) -> Dict[str, Any]:
    return {"error": {"message": str(message), "type": "invalid_request_error" if status < 500 else "server_error", "code": code}}


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _reject_constant(value: str) -> Any:
    raise ValueError("JSON constant %s is not allowed" % value)


def _new_id() -> str:
    return "chatcmpl-" + uuid.uuid4().hex


def _prefix_delta(previous: str, current: str) -> str:
    if not current or current == previous:
        return ""
    if current.startswith(previous):
        return current[len(previous) :]
    # A provider may revise a tokenizer boundary.  There is no reversible way
    # to edit text already delivered to an OpenAI SSE client, so suppress a
    # non-prefix snapshot until the provider emits a true prefix extension.
    return ""


def _stream_mode(value: Any) -> str:
    """Return an explicitly declared stream mode, or ``auto``.

    MindNLP's streamer returns fragments, while Case9 providers normally
    expose cumulative snapshots.  Accept an optional marker from a provider
    without changing the legacy three-item event tuple API.  Unknown markers
    are deliberately treated as cumulative/auto rather than trusted.
    """

    raw: Any = None
    if isinstance(value, Mapping):
        raw = value.get("stream_mode", value.get("mode"))
        if raw is None and isinstance(value.get("cumulative"), bool):
            raw = "cumulative" if value["cumulative"] else "fragment"
    elif isinstance(value, (tuple, list)) and len(value) > 3:
        raw = value[3]
    if isinstance(raw, str):
        normalized = raw.strip().lower()
        if normalized in {"cumulative", "snapshot"}:
            return "cumulative"
        if normalized in {"fragment", "delta", "suffix"}:
            return "fragment"
    return "auto"


def _provider_stream_mode(provider: Any) -> str:
    """Read a provider-level stream mode declaration without raising."""

    try:
        status_method = getattr(provider, "status", None)
        status = status_method() if callable(status_method) else {}
        if isinstance(status, Mapping):
            mode = _stream_mode(status)
            if mode != "auto":
                return mode
            raw = status.get("stream_mode")
            if isinstance(raw, str) and raw.strip().lower() in {"fragment", "delta", "suffix"}:
                return "fragment"
    except Exception:
        LOGGER.debug("could not read provider stream mode", exc_info=True)
    return "cumulative"


def _stream_delta(previous: str, current: str, mode: str = "auto") -> Tuple[str, str]:
    """Normalize a cumulative snapshot or explicit fragment to one delta.

    Cumulative mode only emits a true unseen prefix extension.  A non-prefix
    snapshot is suppressed because HTTP/SSE cannot revise text already sent.
    Fragment mode appends each fragment exactly once, including repeated
    characters (for example ``"ha"``, ``"ha"``); callers opt into it via an
    explicit event/provider marker.
    """

    current = str(current or "")
    previous = str(previous or "")
    if not current:
        return "", previous
    if mode == "fragment":
        return current, previous + current
    # ``auto`` is intentionally the conservative cumulative contract.
    delta = _prefix_delta(previous, current)
    if delta:
        return delta, current
    return "", previous


def _normalize_result(value: Any) -> GenerationResult:
    if isinstance(value, GenerationResult):
        return value
    if isinstance(value, Mapping):
        return GenerationResult(
            str(value.get("text", value.get("content", ""))),
            int(value.get("prompt_tokens", 0)),
            int(value.get("completion_tokens", value.get("tokens", 0))),
            str(value.get("finish_reason", "stop")),
        )
    text = str(getattr(value, "text", value if value is not None else ""))
    return GenerationResult(
        text,
        int(getattr(value, "prompt_tokens", 0)),
        int(getattr(value, "completion_tokens", 0)),
        str(getattr(value, "finish_reason", "stop")),
    )


def _stream_event(value: Any) -> Tuple[str, int, Optional[str]]:
    if isinstance(value, GenerationResult):
        return value.text, int(value.completion_tokens), value.finish_reason
    if isinstance(value, Mapping):
        return (
            str(value.get("text", value.get("content", ""))),
            int(value.get("completion_tokens", value.get("tokens", 0))),
            value.get("finish_reason"),
        )
    if isinstance(value, (tuple, list)):
        if not value:
            return "", 0, None
        text = str(value[0])
        count = int(value[1]) if len(value) > 1 and value[1] is not None else 0
        reason = value[2] if len(value) > 2 else None
        return text, count, reason
    return str(value), 0, None


def _profile_value(profile: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if isinstance(profile, Mapping) and name in profile:
            return profile[name]
        if profile is not None and hasattr(profile, name):
            return getattr(profile, name)
    # Keep raw schema-v2 mappings equivalent to the validated profile object.
    # In particular, runtime.provider must not be skipped when a caller passes
    # JSON directly during recovery or protocol testing.
    runtime = profile.get("runtime") if isinstance(profile, Mapping) else None
    if isinstance(runtime, Mapping):
        aliases = {
            "runtime_provider": "provider",
            "provider": "provider",
            "context_window": "context_length",
            "max_context_tokens": "context_length",
        }
        for name in names:
            runtime_name = aliases.get(name, name)
            if runtime_name in runtime:
                return runtime[runtime_name]
    return default


def _profile_flag(value: Any, *, unknown_is_true: bool = True) -> bool:
    """Parse a profile boolean without treating the string ``false`` as true.

    Registry values are validated before they reach the service, but health and
    admission also accept lightweight mappings during recovery and tests.
    Unknown textual values fail closed for security-sensitive flags.
    """

    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"", "0", "false", "no", "off", "none", "null"}:
            return False
        return bool(unknown_is_true)
    if value is None:
        return False
    return bool(value)


def _normalize_soc(value: Any) -> str:
    """Normalize the spellings emitted by ``npu-smi`` and the registry."""

    text = str(value or "").strip().lower()
    if text.startswith("ascend"):
        text = text[len("ascend") :]
    return text


def _canonical_soc(value: Any) -> str:
    normalized = _normalize_soc(value)
    return "Ascend" + normalized.upper() if normalized else ""


def _probe_current_soc() -> str:
    """Return the visible NPU SoC without importing MindSpore or Torch."""

    try:
        # The provider helper only invokes npu-smi and is safe before the
        # optional MindSpore runtime is loaded.
        from mindspore_chat_providers import _probe_npu_model

        value = _probe_npu_model()
    except Exception:
        value = None
    if value:
        return _canonical_soc(value)
    # Environment values are useful in copied board deployments where the
    # launcher has already recorded the SoC, but they are only a fallback when
    # npu-smi is unavailable.
    for name in ("CASE9_NPU_MODEL", "ASCEND_SOC_VERSION"):
        value = os.environ.get(name, "").strip()
        if value:
            return _canonical_soc(value)
    return ""


def _status_soc(status: Mapping[str, Any]) -> str:
    """Extract a valid SoC hint from a provider health payload."""

    for name in ("board_soc", "npu_model", "board_soc_version", "soc"):
        value = status.get(name)
        normalized = _normalize_soc(value)
        if normalized.startswith("310b"):
            return _canonical_soc(value)
    return ""


def _profile_board_targets(profile: Any) -> List[Mapping[str, Any]]:
    value = _profile_value(profile, "board_targets", default=())
    if isinstance(value, Mapping):
        value = (value,)
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _profile_board_for_soc(profile: Any, soc: str) -> Optional[Mapping[str, Any]]:
    if not soc:
        return None
    lookup = getattr(profile, "board_for_soc", None)
    if callable(lookup):
        try:
            value = lookup(soc)
            if isinstance(value, Mapping):
                return value
        except Exception:
            pass
    expected = _normalize_soc(soc)
    for item in _profile_board_targets(profile):
        if _normalize_soc(item.get("soc")) == expected:
            return item
    return None


def _profile_validation_entry(profile: Any, soc: str) -> Optional[Mapping[str, Any]]:
    validation = _profile_value(profile, "validation", default={})
    if not isinstance(validation, Mapping) or not soc:
        return None
    expected = _normalize_soc(soc)
    for key, item in validation.items():
        if _normalize_soc(key) == expected and isinstance(item, Mapping):
            return item
    return None


def _profile_quality_admission(
    profile: Any,
    *,
    soc: Optional[str] = None,
) -> Tuple[bool, str]:
    """Apply the canonical human-quality gate to an admitted profile.

    The normal path receives a validated ``ChatModelProfile``.  The mapping
    fallback keeps recovery/tests fail-closed when a caller supplies raw JSON
    instead of the canonical object; it never treats ``reviewed=true`` or a
    machine count as approval by itself.
    """

    method = getattr(profile, "quality_admission_for_soc", None)
    if callable(method):
        try:
            result = method(soc) if soc else method()
            if isinstance(result, tuple) and len(result) == 2:
                return bool(result[0]), str(result[1] or "")
        except Exception as exc:
            return False, "quality admission check failed: %s" % exc
        return False, "quality admission check returned an invalid result"

    quality: Any = _profile_value(profile, "quality", default={})
    languages = _profile_value(profile, "languages", default=())
    if soc:
        entry = _profile_validation_entry(profile, soc)
        if not isinstance(entry, Mapping):
            return False, "board validation record is missing"
        quality = entry.get("quality")
    if not isinstance(languages, (list, tuple)):
        languages = ()
    try:
        return quality_admission_check(quality, languages=languages)
    except Exception as exc:
        return False, "quality admission check failed: %s" % exc


def _profile_admission(
    profile: Any, *, observed_soc: Optional[str] = None
) -> Tuple[str, str, str]:
    """Resolve aggregate/per-SoC admission for a candidate profile.

    The aggregate status is used only when no board identity is available. If
    a SoC is known, a target mismatch is blocked and the matching validation
    record is authoritative. This prevents a pass on B4 from accidentally
    admitting the same profile on B1 (or vice versa).
    """

    if profile is None:
        return "", "", "profile metadata is missing"
    candidate_kind = str(_profile_value(profile, "candidate_kind", default="native_mindspore")).strip().lower()
    if candidate_kind not in {"native_mindspore", "conditional"}:
        return (
            "blocked",
            _canonical_soc(observed_soc),
            "unsupported candidate kind: %s" % candidate_kind,
        )
    conditional = _profile_flag(_profile_value(profile, "is_conditional", default=False)) or candidate_kind == "conditional"
    if conditional:
        return "blocked", _canonical_soc(observed_soc), "profile is conditional"
    runtime_provider = str(
        _profile_value(profile, "runtime_provider", "provider", default="mindspore")
    ).strip().lower()
    if runtime_provider != "mindspore":
        return (
            "blocked",
            _canonical_soc(observed_soc),
            "unsupported runtime provider: %s" % runtime_provider,
        )

    soc = _canonical_soc(observed_soc)
    if not soc:
        status = str(_profile_value(profile, "status", default="")).strip().lower()
        if status == "admitted":
            approved, quality_reason = _profile_quality_admission(profile)
            if not approved:
                return "blocked", "", quality_reason
        return status, "", str(_profile_value(profile, "admission_reason", default=""))

    targets = _profile_board_targets(profile)
    target_socs = {_normalize_soc(item.get("soc")) for item in targets if item.get("soc")}
    if target_socs and _normalize_soc(soc) not in target_socs:
        return "blocked", soc, "profile does not target %s" % soc

    resolver = getattr(profile, "activation_status_for_soc", None)
    if callable(resolver):
        try:
            status = str(resolver(soc)).strip().lower()
        except Exception:
            status = "blocked"
    else:
        entry = _profile_validation_entry(profile, soc)
        status = str(entry.get("status", "")) if entry else str(_profile_value(profile, "status", default=""))
        status = status.strip().lower()
    entry = _profile_validation_entry(profile, soc)
    reason = ""
    if entry is not None:
        reason = str(entry.get("reason", ""))
    if not reason:
        reason = str(_profile_value(profile, "admission_reason", default=""))
    if status == "admitted":
        approved, quality_reason = _profile_quality_admission(profile, soc=soc)
        if not approved:
            return "blocked", soc, quality_reason
    return status, soc, reason


def _worker_pid() -> int:
    try:
        import os

        return int(os.getpid())
    except Exception:
        return 0


def create_service(profile: Any, **provider_kwargs: Any) -> MindSporeChatService:
    """Build a service from a validated profile without exposing paths in API."""

    provider = create_provider(profile, **provider_kwargs)
    return MindSporeChatService(provider, profile=profile)


def _main() -> int:
    parser = argparse.ArgumentParser(description="Case9 MindSpore chat service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    parser.add_argument("--profile", required=False)
    args = parser.parse_args()
    # The launcher performs the environment, artifact, and process-group
    # preflight before it invokes this module.  Refuse direct Python starts so
    # an incomplete cache (or a different port/interface) cannot look like an
    # admitted candidate service.
    if os.environ.get("CASE9_LAUNCHER_VERIFIED") != "1":
        parser.error(
            "direct start is disabled; use scripts/run_mindspore_chat_service.sh "
            "after its preflight"
        )
    if args.host != "127.0.0.1" or args.port != 8090:
        parser.error("MindSpore chat service is fixed to 127.0.0.1:8090")
    if not args.profile:
        parser.error("--profile is required; use the modelctl/launcher to select a profile")
    try:
        from case9_model_profiles import load_profiles

        # The board launcher exports the audited registry path so a copied
        # deployment can use its local lock/manifest without falling back to
        # the controller checkout's default registry.
        registry_path = os.environ.get("CASE9_MODEL_PROFILES", "").strip()
        profiles = load_profiles(registry_path) if registry_path else load_profiles()
        profile = profiles.get(args.profile)
        if profile is None:
            raise ValueError("unknown profile %r" % args.profile)
        status, observed_soc, reason = _profile_admission(
            profile, observed_soc=_probe_current_soc()
        )
        if status in {"blocked", "not-run"}:
            suffix = " for %s" % observed_soc if observed_soc else ""
            raise ValueError("profile %r is %s%s and cannot be started%s" % (
                args.profile, status, suffix, (": " + reason) if reason else ""
            ))
        if status == "experimental_dirty_base" and os.environ.get("CASE9_ALLOW_EXPERIMENTAL") != "1":
            raise ValueError(
                "profile %r is experimental_dirty_base; set CASE9_ALLOW_EXPERIMENTAL=1 "
                "for an explicit candidate start" % args.profile
            )
        if status not in _ACTIVATABLE_ADMISSION_STATUSES:
            raise ValueError("profile %r is not activatable (status=%s)" % (args.profile, status))
    except Exception as exc:
        parser.error("could not load profile: %s" % exc)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    service = create_service(profile)
    server = service.make_server(args.host, args.port)
    try:
        LOGGER.info("MindSpore chat service listening on %s:%d profile=%s", args.host, args.port, args.profile)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        service.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())


__all__ = [
    "MODEL_ID",
    "MAX_GENERATION_TOKENS",
    "DEFAULT_GENERATION_TOKENS",
    "MAX_REQUEST_BYTES",
    "CompletionRequest",
    "RequestError",
    "MindSporeChatService",
    "MindSporeChatHttpService",
    "make_server",
    "create_service",
    "_parse_request",
    "_prefix_delta",
]
