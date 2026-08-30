#!/usr/bin/env python3
"""Measure an OpenAI-compatible local service without third-party packages.

The script is intentionally controller/board neutral.  It only sends the
given request to an already running service and writes timing evidence.  It
does not import a model runtime, inspect devices, or change board state.
"""

from __future__ import annotations

import argparse
import datetime as _datetime
import json
import math
from pathlib import Path
import statistics
import time
from typing import Any, Dict, Iterable, List, Optional
import urllib.error
import urllib.request


def _utc_now() -> str:
    return _datetime.datetime.now(_datetime.timezone.utc).isoformat()


def _percentile(values: Iterable[float], fraction: float) -> Optional[float]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    # This is the lower-index method used by the archived 8T reports:
    # floor((n - 1) * q).  Keeping it here makes a five-sample p95 directly
    # comparable instead of silently selecting a different order statistic.
    index = max(0, min(len(ordered) - 1, math.floor((len(ordered) - 1) * fraction)))
    return round(ordered[index], 3)


def _summary(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    successful = [item for item in results if item.get("status") == "ok"]
    elapsed = [float(item["elapsed_ms"]) for item in successful]
    first_event = [
        float(item["first_event_ms"])
        for item in successful
        if item.get("first_event_ms") is not None
    ]
    token_rates = [
        float(item["completion_tokens"]) / (float(item["elapsed_ms"]) / 1000.0)
        for item in successful
        if int(item.get("completion_tokens", 0)) > 0
        and float(item.get("elapsed_ms", 0.0)) > 0.0
    ]

    def stats(values: List[float]) -> Dict[str, Optional[float]]:
        return {
            "count": len(values),
            "min": round(min(values), 3) if values else None,
            "mean": round(statistics.fmean(values), 3) if values else None,
            "p50": _percentile(values, 0.50),
            "p95": _percentile(values, 0.95),
            "max": round(max(values), 3) if values else None,
        }

    return {
        "successful": len(successful),
        "failed": len(results) - len(successful),
        "elapsed_ms": stats(elapsed),
        "first_event_ms": stats(first_event),
        "completion_tokens_per_second": stats(token_rates),
    }


def _payload(args: argparse.Namespace, stream: bool) -> bytes:
    body = {
        "model": args.model,
        "messages": [{"role": "user", "content": args.prompt}],
        "max_tokens": args.max_tokens,
        "temperature": 0,
        "top_p": 1,
        "stream": stream,
    }
    return json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _request_json(args: argparse.Namespace, round_number: int) -> Dict[str, Any]:
    request = urllib.request.Request(
        args.endpoint,
        data=_payload(args, stream=False),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    started = time.perf_counter()
    item: Dict[str, Any] = {"round": round_number, "status": "error"}
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            raw = response.read()
            elapsed = (time.perf_counter() - started) * 1000.0
            document = json.loads(raw.decode("utf-8"))
            choice = (document.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            usage = document.get("usage") or {}
            item.update(
                {
                    "status": "ok",
                    "http_status": int(response.status),
                    "elapsed_ms": round(elapsed, 3),
                    "text": str(message.get("content") or ""),
                    "prompt_tokens": int(usage.get("prompt_tokens") or 0),
                    "completion_tokens": int(usage.get("completion_tokens") or 0),
                    "finish_reason": choice.get("finish_reason"),
                }
            )
    except urllib.error.HTTPError as exc:
        item["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        detail = exc.read(4096).decode("utf-8", errors="replace")
        item["error"] = f"HTTP {exc.code}: {detail}"
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        item["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        item["error"] = f"{type(exc).__name__}: {exc}"
    return item


def _sse_data(line: str) -> Optional[str]:
    if not line.startswith("data:"):
        return None
    return line[5:].strip()


def _request_sse(args: argparse.Namespace, round_number: int) -> Dict[str, Any]:
    request = urllib.request.Request(
        args.endpoint,
        data=_payload(args, stream=True),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    started = time.perf_counter()
    first_event: Optional[float] = None
    text_parts: List[str] = []
    prompt_tokens = 0
    completion_tokens = 0
    finish_reason: Optional[str] = None
    item: Dict[str, Any] = {"round": round_number, "status": "error"}
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            while True:
                raw_line = response.readline()
                if not raw_line:
                    break
                data = _sse_data(raw_line.decode("utf-8", errors="replace").rstrip("\r\n"))
                if data is None:
                    continue
                if first_event is None:
                    first_event = (time.perf_counter() - started) * 1000.0
                if data == "[DONE]":
                    break
                document = json.loads(data)
                choices = document.get("choices") or []
                if choices:
                    choice = choices[0] or {}
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    if content:
                        text_parts.append(str(content))
                    if choice.get("finish_reason") is not None:
                        finish_reason = choice.get("finish_reason")
                usage = document.get("usage") or {}
                prompt_tokens = max(prompt_tokens, int(usage.get("prompt_tokens") or 0))
                completion_tokens = max(
                    completion_tokens, int(usage.get("completion_tokens") or 0)
                )
            elapsed = (time.perf_counter() - started) * 1000.0
            if first_event is None:
                raise RuntimeError("SSE response contained no data event")
            item.update(
                {
                    "status": "ok",
                    "http_status": int(response.status),
                    "elapsed_ms": round(elapsed, 3),
                    "first_event_ms": round(first_event, 3),
                    "text": "".join(text_parts),
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "finish_reason": finish_reason,
                }
            )
    except urllib.error.HTTPError as exc:
        item["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        item["first_event_ms"] = round(first_event, 3) if first_event is not None else None
        detail = exc.read(4096).decode("utf-8", errors="replace")
        item["error"] = f"HTTP {exc.code}: {detail}"
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, json.JSONDecodeError) as exc:
        item["elapsed_ms"] = round((time.perf_counter() - started) * 1000.0, 3)
        item["first_event_ms"] = round(first_event, 3) if first_event is not None else None
        item["error"] = f"{type(exc).__name__}: {exc}"
    return item


def _probe_get(url: Optional[str], timeout: float) -> Dict[str, Any]:
    if not url:
        return {"status": "not_requested"}
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(64 * 1024).decode("utf-8", errors="replace")
            return {"status": "ok", "http_status": int(response.status), "body": body}
    except (OSError, urllib.error.HTTPError) as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def _run(args: argparse.Namespace, mode: str) -> Dict[str, Any]:
    request_fn = _request_sse if mode == "sse" else _request_json
    warmup = request_fn(args, 0)
    if warmup.get("status") != "ok":
        raise SystemExit(f"warmup failed: {warmup.get('error', warmup)}")
    results = [request_fn(args, number) for number in range(1, args.loops + 1)]
    document: Dict[str, Any] = {
        "schema_version": 1,
        "recorded_at_utc": _utc_now(),
        "protocol": mode,
        "endpoint": args.endpoint,
        "model": args.model,
        "board_ip": args.board_ip,
        "board_tier": args.board_tier,
        "om_sha256": args.om_sha256,
        "prompt": args.prompt,
        "request": {
            "max_tokens": args.max_tokens,
            "temperature": 0,
            "top_p": 1,
            "warmup": 1,
            "loops": args.loops,
            "timeout_seconds": args.timeout,
        },
        "health": _probe_get(args.health_url, args.timeout),
        "models": _probe_get(args.models_url, args.timeout),
        "warmup": warmup,
        "results": results,
        "summary": _summary(results),
        "percentile_method": "lower_index: floor((n-1)*fraction), zero based",
    }
    return document


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default="http://127.0.0.1:8084/v1/chat/completions")
    parser.add_argument("--health-url", default=None)
    parser.add_argument("--models-url", default=None)
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt", default="你好，请用一句话介绍你自己。")
    parser.add_argument("--max-tokens", type=int, default=2)
    parser.add_argument("--warmup", type=int, default=1, help="reserved for protocol clarity; exactly one warmup is used")
    parser.add_argument("--loops", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--mode", choices=("json", "sse"), default="json")
    parser.add_argument("--board-ip", default=None)
    parser.add_argument("--board-tier", default=None)
    parser.add_argument("--om-sha256", default=None)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.warmup != 1:
        parser.error("the comparable protocol requires exactly one warmup")
    if args.max_tokens < 1 or args.loops < 1:
        parser.error("max-tokens and loops must be positive")
    document = _run(args, args.mode)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(document["summary"], ensure_ascii=False, sort_keys=True))
    return 0 if document["summary"]["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
