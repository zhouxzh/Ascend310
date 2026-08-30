#!/usr/bin/env python3
"""Run a bounded, read-only acceptance campaign against a Qwen2.5 ACL API.

The program intentionally uses only the Python standard library.  It does not
start or stop a model process and it never writes model/audio data.  A report
is written only to the path supplied by ``--output``.  This makes it suitable
for execution on either Ascend board through ``run_qwen25_dual_board_acceptance.sh``.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import hashlib
import http.client
import importlib.util
import json
import math
import os
from pathlib import Path
import platform
import socket
import statistics
import subprocess
import sys
import threading
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import urllib.error
import urllib.parse
import urllib.request


FORBIDDEN_PACKAGES = (
    "torch",
    "torch_npu",
    "torchaudio",
    "transformers",
    "onnxruntime",
    "mindspore",
    "mindtorch",
    "vllm",
    "mindie",
)
MAX_REQUEST_BYTES = 256 * 1024
OVER_CONTEXT_TERM_COUNT = 2048
MAX_ARTIFACTS_TIMEOUT_SECONDS = 600.0
MAX_CAMPAIGN_LOOPS = 1000
EXPECTED_SOC_BY_TIER = {
    "8t": "Ascend310B4",
    "board8t": "Ascend310B4",
    "20t": "Ascend310B1",
    "board20t": "Ascend310B1",
}
DEFAULT_PROBES = [
    {"id": "identity", "prompt": "你是谁？请用一句话回答。"},
    {"id": "capability", "prompt": "请用中文说明你能做什么。"},
    {"id": "summarize", "prompt": "请把“昇腾开发板运行本地模型”概括成一句话。"},
    {"id": "math", "prompt": "请计算 12 加 30 等于多少，只回答结果。"},
    {"id": "list", "prompt": "请列出两个保持软件可复现的要点。"},
    {"id": "translation", "prompt": "把 hello 翻译成中文，只回答译文。"},
    {"id": "safety", "prompt": "如果不知道答案，你应该怎么做？请简短回答。"},
    {"id": "hardware", "prompt": "Ascend 310B 是 CPU 还是 NPU？请简短回答。"},
    {"id": "format", "prompt": "请用中文回答：静态 KV cache 的主要作用是什么？"},
    {"id": "closing", "prompt": "请写一句不超过二十字的中文问候。"},
]


def utc_now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat()


def digest(path: Path) -> Tuple[int, str]:
    state = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            state.update(block)
    return size, state.hexdigest()


def percentile(values: Iterable[float], fraction: float) -> Optional[float]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    index = max(0, min(len(ordered) - 1, math.floor((len(ordered) - 1) * fraction)))
    return round(ordered[index], 3)


def summary(values: Sequence[float]) -> Dict[str, Optional[float]]:
    numbers = [float(value) for value in values]
    return {
        "count": len(numbers),
        "min": round(min(numbers), 3) if numbers else None,
        "mean": round(statistics.fmean(numbers), 3) if numbers else None,
        "p50": percentile(numbers, 0.50),
        "p95": percentile(numbers, 0.95),
        "max": round(max(numbers), 3) if numbers else None,
    }


def _read_json(path: Optional[Path], fallback: Any) -> Any:
    if path is None:
        return fallback
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, list):
        raise ValueError("probe file must contain a JSON array")
    return value


def _proc_snapshot() -> Dict[str, Any]:
    result: Dict[str, Any] = {}
    status = Path("/proc/self/status")
    if status.is_file():
        for line in status.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith(("VmRSS:", "VmHWM:", "VmSize:")):
                parts = line.split()
                if len(parts) >= 2:
                    result[parts[0].rstrip(":").lower()] = int(parts[1])
    fd_dir = Path("/proc/self/fd")
    if fd_dir.is_dir():
        try:
            result["fd_count"] = len(list(fd_dir.iterdir()))
        except OSError:
            result["fd_count"] = None
    return result


def _npu_smi() -> str:
    try:
        completed = subprocess.run(
            ["npu-smi", "info"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=10,
            check=False,
        )
        return completed.stdout[-16 * 1024 :]
    except (OSError, subprocess.SubprocessError) as exc:
        return "unavailable: %s: %s" % (type(exc).__name__, exc)


def system_snapshot(label: str) -> Dict[str, Any]:
    return {
        "label": label,
        "recorded_at_utc": utc_now(),
        "hostname": platform.node(),
        "python": sys.version.replace("\n", " "),
        "machine": platform.machine(),
        "process": _proc_snapshot(),
        "npu_smi": _npu_smi(),
    }


class Sampler:
    """Collect small process/NPU snapshots while one request is running."""

    def __init__(self, interval: float = 1.0) -> None:
        self.interval = max(0.2, float(interval))
        self.samples: List[Dict[str, Any]] = []
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def _run(self) -> None:
        while not self._stop.is_set() and len(self.samples) < 120:
            sample = system_snapshot("during")
            self.samples.append(sample)
            self._stop.wait(self.interval)

    def __enter__(self) -> "Sampler":
        self._thread = threading.Thread(target=self._run, name="qwen25-npu-sampler", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=12)


def _payload(model: str, prompt: str, max_tokens: int, stream: bool) -> bytes:
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
        "temperature": 0,
        "top_p": 1,
        "stream": stream,
    }
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _request(
    endpoint: str,
    model: str,
    prompt: str,
    max_tokens: int,
    timeout: float,
    stream: bool,
) -> Dict[str, Any]:
    request = urllib.request.Request(
        endpoint,
        data=_payload(model, prompt, max_tokens, stream),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream" if stream else "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if stream:
                return _read_sse(response, started)
            raw = response.read(512 * 1024)
            document = json.loads(raw.decode("utf-8"))
            choices = document.get("choices") or []
            choice = choices[0] if choices else {}
            message = choice.get("message") or {}
            usage = document.get("usage") or {}
            content = str(message.get("content") or "")
            elapsed = (time.perf_counter() - started) * 1000.0
            return {
                "status": "ok",
                "http_status": int(response.status),
                "elapsed_ms": round(elapsed, 3),
                "first_event_ms": round(elapsed, 3),
                "text": content,
                "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                "completion_tokens": int(usage.get("completion_tokens") or 0),
                "finish_reason": choice.get("finish_reason"),
            }
    except urllib.error.HTTPError as exc:
        detail = exc.read(16 * 1024).decode("utf-8", errors="replace")
        return {
            "status": "error",
            "http_status": int(exc.code),
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "error": "HTTP %d: %s" % (exc.code, detail),
        }
    except (OSError, ValueError, TypeError, KeyError, UnicodeError, json.JSONDecodeError) as exc:
        return {
            "status": "error",
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "error": "%s: %s" % (type(exc).__name__, exc),
        }


def _read_sse(response: Any, started: float) -> Dict[str, Any]:
    first_event: Optional[float] = None
    finish_reason: Optional[str] = None
    prompt_tokens = 0
    completion_tokens = 0
    deltas: List[str] = []
    malformed = 0
    saw_done = False
    while True:
        raw_line = response.readline()
        if not raw_line:
            break
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if first_event is None:
            first_event = (time.perf_counter() - started) * 1000.0
        if data == "[DONE]":
            saw_done = True
            break
        try:
            document = json.loads(data)
            choices = document.get("choices") or []
            if choices:
                choice = choices[0] or {}
                delta = choice.get("delta") or {}
                content = delta.get("content")
                if content:
                    deltas.append(str(content))
                if choice.get("finish_reason") is not None:
                    finish_reason = choice.get("finish_reason")
            usage = document.get("usage") or {}
            prompt_tokens = max(prompt_tokens, int(usage.get("prompt_tokens") or 0))
            completion_tokens = max(completion_tokens, int(usage.get("completion_tokens") or 0))
        except (ValueError, TypeError, KeyError, json.JSONDecodeError):
            malformed += 1
    elapsed = (time.perf_counter() - started) * 1000.0
    text = "".join(deltas)
    duplicate_delta = False
    # A streaming API must send increments.  Identical cumulative chunks are
    # accepted for reconstruction but recorded as a protocol defect.
    cumulative = ""
    for delta in deltas:
        if delta and (delta == cumulative or (cumulative and delta.startswith(cumulative))):
            duplicate_delta = True
        cumulative += delta
    if first_event is None:
        return {"status": "error", "elapsed_ms": round(elapsed, 3), "error": "SSE response contained no data event"}
    return {
        "status": "ok" if saw_done and malformed == 0 else "error",
        "http_status": int(getattr(response, "status", 200)),
        "elapsed_ms": round(elapsed, 3),
        "first_event_ms": round(first_event, 3),
        "text": text,
        "deltas": deltas,
        "duplicate_delta": duplicate_delta,
        "malformed_events": malformed,
        "saw_done": saw_done,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "finish_reason": finish_reason,
    }


def get_json(url: str, timeout: float) -> Dict[str, Any]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            raw = response.read(128 * 1024)
            return {
                "status": "ok",
                "http_status": int(response.status),
                "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
                "body": json.loads(raw.decode("utf-8")),
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read(128 * 1024)
        try:
            body: Any = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError, TypeError, json.JSONDecodeError):
            body = raw.decode("utf-8", errors="replace")
        return {
            "status": "error",
            "http_status": int(exc.code),
            "elapsed_ms": round((time.perf_counter() - started) * 1000.0, 3),
            "body": body,
            "error": str(exc),
        }
    except (OSError, ValueError, TypeError, UnicodeError, json.JSONDecodeError) as exc:
        return {"status": "error", "error": "%s: %s" % (type(exc).__name__, exc)}


def _request_raw_bytes(url: str, raw: bytes, timeout: float) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=raw,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {
                "status": int(response.status),
                "body": response.read(64 * 1024).decode("utf-8", errors="replace"),
                "request_bytes": len(raw),
            }
    except urllib.error.HTTPError as exc:
        return {
            "status": int(exc.code),
            "body": exc.read(64 * 1024).decode("utf-8", errors="replace"),
            "request_bytes": len(raw),
        }
    except (OSError, urllib.error.URLError) as exc:
        return {"status": None, "error": "%s: %s" % (type(exc).__name__, exc), "request_bytes": len(raw)}


def _request_raw(url: str, body: Mapping[str, Any], timeout: float) -> Dict[str, Any]:
    return _request_raw_bytes(url, json.dumps(body, ensure_ascii=False).encode("utf-8"), timeout)


def _over_context_payload(model: str) -> bytes:
    """Build a deterministic token-heavy request that must exceed 1024 tokens."""

    terms = " ".join("token%04d" % number for number in range(OVER_CONTEXT_TERM_COUNT))
    body = {
        "model": model,
        "messages": [{"role": "user", "content": terms}],
        "max_tokens": 1,
        "temperature": 0,
        "top_p": 1,
        "stream": False,
    }
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _over_context_request(url: str, model: str, timeout: float) -> Dict[str, Any]:
    result = _request_raw_bytes(url, _over_context_payload(model), timeout)
    result.update(
        {
            "declared_prompt_terms": OVER_CONTEXT_TERM_COUNT,
            "max_tokens": 1,
            "expected_http_status": 400,
        }
    )
    return result


def _local_http_target(url: str) -> Tuple[str, int, str, str]:
    """Return a loopback HTTP target for intentionally aborted test requests."""

    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost"}:
        raise ValueError("client-abort probe only permits http loopback endpoints")
    host = str(parsed.hostname)
    port = int(parsed.port or 80)
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    host_header = host if port == 80 else "%s:%d" % (host, port)
    return host, port, path, host_header


def _oversized_content_length_request(url: str, timeout: float) -> Dict[str, Any]:
    """Exercise the service's Content-Length guard without uploading a large body.

    The service must reject the declared size before attempting to read the body.
    This avoids retaining or transmitting a 256 KiB payload during acceptance.
    """

    connection: Optional[http.client.HTTPConnection] = None
    declared_length = MAX_REQUEST_BYTES + 1
    try:
        host, port, path, host_header = _local_http_target(url)
        connection = http.client.HTTPConnection(host, port, timeout=timeout)
        connection.putrequest("POST", path, skip_host=True)
        connection.putheader("Host", host_header)
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(declared_length))
        connection.putheader("Connection", "close")
        connection.endheaders()
        response = connection.getresponse()
        return {
            "status": int(response.status),
            "body": response.read(64 * 1024).decode("utf-8", errors="replace"),
            "declared_content_length": declared_length,
            "transmitted_body_bytes": 0,
            "expected_http_status": 400,
        }
    except (OSError, ValueError, http.client.HTTPException) as exc:
        return {
            "status": None,
            "error": "%s: %s" % (type(exc).__name__, exc),
            "declared_content_length": declared_length,
            "transmitted_body_bytes": 0,
            "expected_http_status": 400,
        }
    finally:
        if connection is not None:
            connection.close()


def _client_abort_sse(url: str, model: str, timeout: float, max_tokens: int) -> Dict[str, Any]:
    """Send one SSE request to loopback and close the client before reading it."""

    sock: Optional[socket.socket] = None
    try:
        host, port, path, host_header = _local_http_target(url)
        body = _payload(model, "Return one short greeting.", max_tokens, True)
        request = (
            "POST %s HTTP/1.1\r\n" % path
            + "Host: %s\r\n" % host_header
            + "Content-Type: application/json\r\n"
            + "Accept: text/event-stream\r\n"
            + "Content-Length: %d\r\n" % len(body)
            + "Connection: close\r\n\r\n"
        ).encode("ascii")
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.sendall(request + body)
        try:
            sock.shutdown(socket.SHUT_WR)
        except OSError:
            # A peer may already close after it observes the deliberate abort.
            pass
        return {
            "status": "sent_and_closed",
            "request_bytes": len(body),
            "expected": "client connection closes before reading SSE data",
        }
    except (OSError, ValueError) as exc:
        return {"status": "error", "error": "%s: %s" % (type(exc).__name__, exc)}
    finally:
        if sock is not None:
            sock.close()


def _health_after_abort(url: str, timeout: float, wait_seconds: float) -> Dict[str, Any]:
    """Accept a live ready service or a documented 503 fail-closed state."""

    deadline = time.monotonic() + max(0.0, wait_seconds)
    attempts: List[Dict[str, Any]] = []
    while True:
        response = get_json(url, min(timeout, 5.0))
        body = response.get("body")
        attempt = {
            "status": response.get("status"),
            "http_status": response.get("http_status"),
            "ready": body.get("ready") if isinstance(body, Mapping) else None,
            "healthy": body.get("healthy") if isinstance(body, Mapping) else None,
        }
        attempts.append(attempt)
        if response.get("status") == "ok" and response.get("http_status") == 200 and isinstance(body, Mapping) and body.get("ready") is True:
            return {"status": "healthy", "contract_status": "passed", "attempts": attempts}
        if response.get("http_status") == 503 and isinstance(body, Mapping) and body.get("ready") is False:
            return {"status": "fail_closed", "contract_status": "passed", "attempts": attempts}
        if time.monotonic() >= deadline:
            return {"status": "unavailable", "contract_status": "failed", "attempts": attempts}
        time.sleep(min(0.25, max(0.01, deadline - time.monotonic())))


def _mark_rejection_contract(item: Dict[str, Any]) -> Dict[str, Any]:
    item["contract_status"] = "passed" if item.get("status") == 400 else "failed"
    return item


def _artifact_info(path: Optional[Path]) -> Optional[Dict[str, Any]]:
    if path is None:
        return None
    value: Dict[str, Any] = {"path": str(path)}
    if path.is_file():
        size, sha = digest(path)
        value.update({"exists": True, "bytes": size, "sha256": sha})
    else:
        value["exists"] = False
    return value


def _required_artifacts(
    om: Optional[Path],
    om_lock: Optional[Path],
    contract: Optional[Path],
    tokenizer: Optional[Path],
    tokenizer_lock: Optional[Path],
) -> Dict[str, Optional[Dict[str, Any]]]:
    """Describe every file required before an ACL request is admitted."""

    return {
        "om": _artifact_info(om),
        "om_lock": _artifact_info(om_lock),
        "contract": _artifact_info(contract),
        "tokenizer": _artifact_info(tokenizer),
        "tokenizer_lock": _artifact_info(tokenizer_lock),
    }


def _required_artifacts_ok(artifacts: Mapping[str, Any]) -> bool:
    """Return true only when all five required paths are regular files."""

    required = ("om", "om_lock", "contract", "tokenizer", "tokenizer_lock")
    return all(
        isinstance(artifacts.get(name), Mapping)
        and artifacts[name].get("exists") is True
        for name in required
    )


def _expected_soc(board_tier: Optional[str]) -> Optional[str]:
    if board_tier is None:
        return None
    tier = str(board_tier).strip()
    return EXPECTED_SOC_BY_TIER.get(tier, tier if tier.startswith("Ascend310") else None)


def _health_model_contract(
    health: Mapping[str, Any],
    models: Mapping[str, Any],
    expected_model: str,
    board_tier: Optional[str],
) -> Dict[str, Any]:
    """Check readiness, artifact admission, SoC identity and model listing."""

    health_body = health.get("body")
    models_body = models.get("body")
    health_body = health_body if isinstance(health_body, Mapping) else {}
    models_body = models_body if isinstance(models_body, Mapping) else {}
    expected_soc = _expected_soc(board_tier)
    target_soc = health_body.get("target_soc")
    board_soc = health_body.get("board_soc")
    listed_ids = [
        item.get("id")
        for item in (models_body.get("data") or [])
        if isinstance(item, Mapping)
    ]
    checks = {
        "health_http": health.get("status") == "ok" and health.get("http_status") == 200,
        "ready": health_body.get("ready") is True,
        "healthy": health_body.get("healthy") is True,
        "model": health_body.get("model") == expected_model,
        "artifact_lock_verified": health_body.get("artifact_lock_verified") is True,
        "artifact_verified": health_body.get("artifact_verified") is True,
        "restart_required": health_body.get("restart_required") is False,
        "cleanup_failed": health_body.get("cleanup_failed") is False,
        "watchdog_triggered": health_body.get("watchdog_triggered") is False,
        "expected_soc_known": expected_soc is not None,
        "target_soc": expected_soc is not None and target_soc == expected_soc,
        "board_soc": expected_soc is not None and board_soc == expected_soc,
        "soc_match": target_soc is not None and target_soc == board_soc,
        "models_http": models.get("status") == "ok" and models.get("http_status") == 200,
        "model_listed": expected_model in listed_ids,
    }
    return {
        "status": "passed" if all(checks.values()) else "failed",
        "expected_model": expected_model,
        "expected_soc": expected_soc,
        "target_soc": target_soc,
        "board_soc": board_soc,
        "listed_model_ids": listed_ids,
        "checks": checks,
    }


def _valid_long_output(item: Mapping[str, Any], budget: int) -> bool:
    """Validate text, finish reason and usage against one requested budget."""

    if not _valid_response(item) or item.get("utf8_valid") is not True:
        return False
    completion_tokens = item.get("completion_tokens")
    if isinstance(completion_tokens, bool) or not isinstance(completion_tokens, int):
        return False
    if not 1 <= completion_tokens <= budget:
        return False
    finish_reason = item.get("finish_reason")
    if finish_reason not in {"stop", "length"}:
        return False
    # A length stop must account for the entire requested budget.  A semantic
    # stop may occur earlier (EOS or the runtime's sentence boundary policy).
    return finish_reason != "length" or completion_tokens == budget


def validate_options(args: argparse.Namespace) -> None:
    """Validate campaign bounds before any network request is attempted."""

    def integer(name: str, minimum: int, maximum: int) -> None:
        value = getattr(args, name)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise ValueError(f"{name} must be an integer between {minimum} and {maximum}")

    timeout = getattr(args, "timeout", None)
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(float(timeout)):
        raise ValueError("timeout must be finite")
    if not 0.001 <= float(timeout) <= MAX_ARTIFACTS_TIMEOUT_SECONDS:
        raise ValueError(f"timeout must be between 0.001 and {MAX_ARTIFACTS_TIMEOUT_SECONDS:g} seconds")
    integer("max_tokens", 1, 80)
    integer("stability_max_tokens", 1, 80)
    integer("probe_max_tokens", 1, 80)
    integer("perf_max_tokens", 1, 80)
    integer("abort_max_tokens", 1, 80)
    integer("stability_loops", 1, MAX_CAMPAIGN_LOOPS)
    integer("perf_warmup", 0, MAX_CAMPAIGN_LOOPS)
    integer("perf_loops", 1, MAX_CAMPAIGN_LOOPS)
    wait_seconds = getattr(args, "abort_health_wait_seconds", None)
    if (
        isinstance(wait_seconds, bool)
        or not isinstance(wait_seconds, (int, float))
        or not math.isfinite(float(wait_seconds))
        or not 0 <= float(wait_seconds) <= 60
    ):
        raise ValueError("abort-health-wait-seconds must be finite and between 0 and 60")
    budgets = getattr(args, "long_budgets", None)
    if not isinstance(budgets, (list, tuple)) or not budgets:
        raise ValueError("long-budgets must contain at least one integer")
    if any(isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 80 for value in budgets):
        raise ValueError("long-budgets values must be integers between 1 and 80")


def _lock_check(om: Optional[Path], lock: Optional[Path]) -> Dict[str, Any]:
    result: Dict[str, Any] = {"status": "not_requested"}
    if om is None and lock is None:
        return result
    if om is None or lock is None:
        return {"status": "error", "error": "--om and --lock must be supplied together"}
    if not om.is_file() or not lock.is_file():
        return {"status": "error", "error": "OM or lock file is missing", "om": _artifact_info(om), "lock": str(lock)}
    try:
        lock_doc = json.loads(lock.read_text(encoding="utf-8"))
        size, sha = digest(om)
        expected_size = lock_doc.get("bytes")
        expected_sha = lock_doc.get("sha256")
        result = {
            "status": "ok" if size == expected_size and sha == expected_sha else "error",
            "om": {"path": str(om), "bytes": size, "sha256": sha},
            "lock": str(lock),
            "expected_bytes": expected_size,
            "expected_sha256": expected_sha,
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        result = {"status": "error", "error": "%s: %s" % (type(exc).__name__, exc)}
    return result


def environment_report() -> Dict[str, Any]:
    packages: Dict[str, Any] = {}
    for name in FORBIDDEN_PACKAGES + ("acl", "numpy", "tokenizers"):
        try:
            spec = importlib.util.find_spec(name)
            packages[name] = {"present": spec is not None, "origin": getattr(spec, "origin", None) if spec else None}
        except (ImportError, AttributeError, ValueError) as exc:
            packages[name] = {"present": None, "error": "%s: %s" % (type(exc).__name__, exc)}
    return {
        "executable": sys.executable,
        "prefix": sys.prefix,
        "python": sys.version.replace("\n", " "),
        "machine": platform.machine(),
        "forbidden_packages": {name: packages[name] for name in FORBIDDEN_PACKAGES},
        "packages": packages,
    }


def _valid_response(item: Mapping[str, Any]) -> bool:
    text = item.get("text")
    return item.get("status") == "ok" and isinstance(text, str) and bool(text.strip()) and "\ufffd" not in text


def _run_campaign(args: argparse.Namespace) -> Dict[str, Any]:
    endpoint = args.endpoint
    health_url = args.health_url or endpoint.rsplit("/v1/", 1)[0] + "/health"
    models_url = args.models_url or endpoint.rsplit("/v1/", 1)[0] + "/v1/models"
    probes = _read_json(args.probe_file, DEFAULT_PROBES)
    forbidden_present = {
        name: bool(report_value.get("present"))
        for name, report_value in environment_report()["forbidden_packages"].items()
    }
    report: Dict[str, Any] = {
        "schema_version": 1,
        "recorded_at_utc": utc_now(),
        "board": {"ip": args.board_ip, "tier": args.board_tier, "hostname": platform.node()},
        "service": {"endpoint": endpoint, "health_url": health_url, "models_url": models_url, "model": args.model},
        "request_policy": {
            "timeout_seconds": args.timeout,
            "long_budgets": args.long_budgets,
            "stability_loops": args.stability_loops,
            "performance_warmup": args.perf_warmup,
            "performance_loops": args.perf_loops,
            "abort_max_tokens": args.abort_max_tokens,
            "abort_health_wait_seconds": args.abort_health_wait_seconds,
        },
        "environment": environment_report(),
        "artifacts": {
            "om_lock": _lock_check(args.om, args.lock),
            "tokenizer_lock": _lock_check(args.tokenizer, args.tokenizer_lock),
            "contract": _artifact_info(args.contract),
            "tokenizer": _artifact_info(args.tokenizer),
            "required_files": _required_artifacts(
                args.om,
                args.lock,
                args.contract,
                args.tokenizer,
                args.tokenizer_lock,
            ),
        },
        "snapshots": {"before": system_snapshot("before")},
        "gates": {},
    }
    health = get_json(health_url, args.timeout)
    models = get_json(models_url, args.timeout)
    report["api"] = {"health": health, "models": models}
    clean_environment = all(
        report["environment"]["forbidden_packages"].get(name, {}).get("present") is False
        for name in FORBIDDEN_PACKAGES
    )
    report["environment"]["dirty_base_override"] = bool(args.allow_dirty_base)
    report["environment"]["forbidden_present"] = forbidden_present
    report["gates"]["G0_environment"] = clean_environment or bool(args.allow_dirty_base)
    report["gates"]["G0_artifacts"] = _required_artifacts_ok(
        report["artifacts"]["required_files"]
    ) and report["artifacts"]["om_lock"].get("status") == "ok" and report["artifacts"]["tokenizer_lock"].get("status") == "ok"
    if args.allow_dirty_base and not clean_environment:
        report.setdefault("warnings", []).append("dirty-base override: pre-existing forbidden packages were detected")
    report["api"]["admission"] = _health_model_contract(
        health,
        models,
        args.model,
        args.board_tier,
    )
    report["gates"]["G1_health_models"] = report["api"]["admission"]["status"] == "passed"
    if args.tokenizer_lock is not None:
        report["gates"]["G0_tokenizer_lock"] = report["artifacts"]["tokenizer_lock"].get("status") == "ok"
    else:
        report["gates"]["G0_tokenizer_lock"] = False

    prompt = "你好，请用一句话介绍你自己。"
    with Sampler() as sampler:
        json_smoke = _request(endpoint, args.model, prompt, min(2, args.max_tokens), args.timeout, False)
        sse_smoke = _request(endpoint, args.model, prompt, min(2, args.max_tokens), args.timeout, True)
    report["smoke"] = {"json": json_smoke, "sse": sse_smoke, "during_samples": sampler.samples}
    report["gates"]["G2_json_sse"] = _valid_response(json_smoke) and _valid_response(sse_smoke) and not bool(sse_smoke.get("duplicate_delta"))

    long_results: List[Dict[str, Any]] = []
    for budget in args.long_budgets:
        item = _request(endpoint, args.model, "请用中文回答：静态 KV cache 的作用是什么？", budget, args.timeout, False)
        item["max_tokens"] = budget
        item["utf8_valid"] = "\ufffd" not in str(item.get("text", ""))
        long_results.append(item)
        if item.get("status") != "ok" and budget <= 32:
            break
    report["long_output"] = long_results
    for item in long_results:
        budget = item.get("max_tokens")
        item["finish_consistent"] = (
            isinstance(budget, int) and _valid_long_output(item, budget)
        )
    report["gates"]["G3_long_output"] = (
        len(long_results) == len(args.long_budgets)
        and all(
            isinstance(item.get("max_tokens"), int)
            and _valid_long_output(item, int(item["max_tokens"]))
            for item in long_results
        )
    )

    stability: List[Dict[str, Any]] = []
    for number in range(1, args.stability_loops + 1):
        item = _request(endpoint, args.model, "请回答：1+1 等于几？", args.stability_max_tokens, args.timeout, False)
        item["round"] = number
        stability.append(item)
    report["stability"] = stability
    report["gates"]["G4_stability"] = len(stability) == args.stability_loops and all(_valid_response(item) for item in stability)

    probe_results: List[Dict[str, Any]] = []
    for probe in probes:
        if not isinstance(probe, Mapping) or not isinstance(probe.get("prompt"), str):
            probe_results.append({"status": "error", "error": "invalid probe entry"})
            continue
        item = _request(endpoint, args.model, str(probe["prompt"]), args.probe_max_tokens, args.timeout, False)
        probe_results.append({"id": probe.get("id"), "prompt": probe["prompt"], **item, "machine_valid": _valid_response(item), "human_understandable": None})
    report["chinese_probes"] = probe_results
    report["gates"]["G5_chinese_probe_machine"] = sum(1 for item in probe_results if item.get("machine_valid")) >= max(1, len(probe_results) - 2)

    perf_results: List[Dict[str, Any]] = []
    for _ in range(args.perf_warmup):
        _request(endpoint, args.model, "你好", args.perf_max_tokens, args.timeout, True)
    for number in range(1, args.perf_loops + 1):
        item = _request(endpoint, args.model, "你好", args.perf_max_tokens, args.timeout, True)
        item["round"] = number
        if item.get("status") == "ok" and item.get("completion_tokens", 0):
            item["tokens_per_second"] = round(float(item["completion_tokens"]) / max(0.001, float(item["elapsed_ms"]) / 1000.0), 6)
        perf_results.append(item)
    report["performance"] = {
        "warmup": args.perf_warmup,
        "loops": args.perf_loops,
        "results": perf_results,
        "elapsed_ms": summary([item["elapsed_ms"] for item in perf_results if item.get("status") == "ok"]),
        "first_event_ms": summary([item["first_event_ms"] for item in perf_results if item.get("status") == "ok" and item.get("first_event_ms") is not None]),
        "tokens_per_second": summary([item["tokens_per_second"] for item in perf_results if item.get("tokens_per_second") is not None]),
    }
    report["gates"]["G6_performance"] = len(perf_results) == args.perf_loops and all(item.get("status") == "ok" for item in perf_results)

    invalid_cases = [
        ("wrong_model", {"model": "not-admitted", "messages": [{"role": "user", "content": "x"}]}),
        ("wrong_role", {"model": args.model, "messages": [{"role": "developer", "content": "x"}]}),
        ("sampling", {"model": args.model, "messages": [{"role": "user", "content": "x"}], "temperature": 0.1}),
    ]
    invalid_results = []
    for name, body in invalid_cases:
        item = _mark_rejection_contract(_request_raw(endpoint, body, args.timeout))
        item["case"] = name
        invalid_results.append(item)
    report["invalid_requests"] = invalid_results
    over_context = _mark_rejection_contract(_over_context_request(endpoint, args.model, args.timeout))
    oversized = _mark_rejection_contract(_oversized_content_length_request(endpoint, args.timeout))
    abort_request = _client_abort_sse(endpoint, args.model, args.timeout, args.abort_max_tokens)
    health_after_abort = _health_after_abort(health_url, args.timeout, args.abort_health_wait_seconds)
    abort_passed = abort_request.get("status") == "sent_and_closed" and health_after_abort.get("contract_status") == "passed"
    abort_request["contract_status"] = "passed" if abort_passed else "failed"
    report["protocol_boundary_tests"] = {
        "over_context": over_context,
        "oversized_content_length": oversized,
        "client_abort": abort_request,
        "health_after_abort": health_after_abort,
    }
    report["gates"]["G5_protocol_boundaries"] = all(
        item.get("contract_status") == "passed" for item in (over_context, oversized, abort_request, health_after_abort)
    )
    report["gates"]["G7_validation_errors"] = all(item.get("contract_status") == "passed" for item in invalid_results)
    report["snapshots"]["after"] = system_snapshot("after")
    required = (
        "G0_environment",
        "G0_artifacts",
        "G0_tokenizer_lock",
        "G1_health_models",
        "G2_json_sse",
        "G3_long_output",
        "G4_stability",
        "G5_protocol_boundaries",
        "G6_performance",
        "G7_validation_errors",
    )
    report["status"] = "passed" if all(report["gates"].get(name) for name in required) else "failed"
    report["notes"] = [
        "human_understandable is intentionally null; annotate Chinese quality after manual review",
        "Health: Alarm is retained in npu-smi snapshots and is not an automatic gate",
        "protocol boundary cases reject over-context and oversized requests; the client-abort case accepts a live ready service or explicit 503 fail-closed state",
        "this campaign never starts/stops services or changes model artifacts",
    ]
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--health-url", default=None)
    parser.add_argument("--models-url", default=None)
    parser.add_argument("--model", required=True)
    parser.add_argument("--board-ip", default=None)
    parser.add_argument("--board-tier", default=None)
    parser.add_argument("--om", type=Path, default=None)
    parser.add_argument("--lock", type=Path, default=None)
    parser.add_argument("--tokenizer-lock", type=Path, default=None)
    parser.add_argument("--contract", type=Path, default=None)
    parser.add_argument("--tokenizer", type=Path, default=None)
    parser.add_argument("--allow-dirty-base", action="store_true", help="record, but do not fail on, pre-existing forbidden packages")
    parser.add_argument("--probe-file", type=Path, default=None)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--max-tokens", type=int, default=2)
    parser.add_argument("--long-budgets", default="8,16,24,32,48,64,80")
    parser.add_argument("--stability-loops", type=int, default=10)
    parser.add_argument("--stability-max-tokens", type=int, default=2)
    parser.add_argument("--probe-max-tokens", type=int, default=8)
    parser.add_argument("--perf-warmup", type=int, default=2)
    parser.add_argument("--perf-loops", type=int, default=30)
    parser.add_argument("--perf-max-tokens", type=int, default=2)
    parser.add_argument("--abort-max-tokens", type=int, default=1)
    parser.add_argument("--abort-health-wait-seconds", type=float, default=8.0)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.long_budgets = [int(value) for value in str(args.long_budgets).split(",") if value.strip()]
    except ValueError:
        parser.error("--long-budgets must be a comma-separated integer list")
    try:
        validate_options(args)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        report = _run_campaign(args)
    except Exception as exc:  # keep a machine-readable failure when a board is unavailable
        report = {
            "schema_version": 1,
            "recorded_at_utc": utc_now(),
            "status": "error",
            "board": {"ip": args.board_ip, "tier": args.board_tier},
            "error": "%s: %s" % (type(exc).__name__, exc),
            "environment": environment_report(),
            "snapshots": {"before": system_snapshot("failure")},
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".part")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(args.output)
    print(json.dumps({"status": report.get("status"), "output": str(args.output), "gates": report.get("gates", {})}, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
