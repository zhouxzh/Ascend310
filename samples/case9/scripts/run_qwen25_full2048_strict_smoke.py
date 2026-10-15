#!/usr/bin/env python3
"""Run repeatable loopback API checks for the fixed-2048 Qwen ACL service."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Dict, List, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _request(url: str, body: Dict[str, Any] | None = None) -> Tuple[int, bytes, float]:
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = Request(
        url,
        data=data,
        method="GET" if data is None else "POST",
        headers={} if data is None else {"Content-Type": "application/json"},
    )
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=120) as response:
            return response.status, response.read(), time.perf_counter() - started
    except HTTPError as exc:
        return exc.code, exc.read(), time.perf_counter() - started
    except URLError as exc:
        raise RuntimeError(f"request failed: {exc.reason}") from exc


def _decode(payload: bytes) -> Tuple[Dict[str, Any], bool]:
    text = payload.decode("utf-8", errors="replace")
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        value = {"raw": text}
    return value if isinstance(value, dict) else {"value": value}, "\ufffd" in text


def _json_check(base_url: str, model: str, prompt: str, max_tokens: int) -> Dict[str, Any]:
    request = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "top_p": 1,
        "max_tokens": max_tokens,
    }
    status, payload, elapsed = _request(base_url + "/v1/chat/completions", request)
    body, replacement = _decode(payload)
    choices = body.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    content = message.get("content")
    finish_reason = choice.get("finish_reason")
    passed = (
        status == 200
        and isinstance(content, str)
        and not replacement
        and finish_reason in {"length", "stop"}
    )
    return {
        "status": "passed" if passed else "failed",
        "http_status": status,
        "elapsed_seconds": elapsed,
        "content": content,
        "finish_reason": finish_reason,
        "utf8_replacement": replacement,
        "body": body,
    }


def _sse_check(base_url: str, model: str, prompt: str, max_tokens: int) -> Dict[str, Any]:
    request_body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "top_p": 1,
        "max_tokens": max_tokens,
        "stream": True,
    }
    request = Request(
        base_url + "/v1/chat/completions",
        data=json.dumps(request_body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    started = time.perf_counter()
    events: List[Dict[str, Any]] = []
    fragments: List[str] = []
    try:
        with urlopen(request, timeout=120) as response:
            status = response.status
            for raw_line in response:
                text = raw_line.decode("utf-8", errors="replace").strip()
                if not text.startswith("data:"):
                    continue
                event_payload = text[5:].strip()
                if event_payload == "[DONE]":
                    events.append({"done": True})
                    break
                event, replacement = _decode(event_payload.encode("utf-8"))
                event["utf8_replacement"] = replacement
                events.append(event)
                choices = event.get("choices")
                if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                    delta = choices[0].get("delta")
                    if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                        fragments.append(delta["content"])
    except HTTPError as exc:
        status = exc.code
        events = [{"error": exc.read().decode("utf-8", errors="replace")}]
    except URLError as exc:
        raise RuntimeError(f"SSE request failed: {exc.reason}") from exc
    elapsed = time.perf_counter() - started
    text = "".join(fragments)
    finished = any(event.get("done") is True for event in events)
    replacements = any(event.get("utf8_replacement") is True for event in events)
    passed = status == 200 and finished and isinstance(text, str) and not replacements and "\ufffd" not in text
    return {
        "status": "passed" if passed else "failed",
        "http_status": status,
        "elapsed_seconds": elapsed,
        "text": text,
        "event_count": len(events),
        "done_received": finished,
        "utf8_replacement": replacements or "\ufffd" in text,
        "events": events,
    }


def _write_report(path: Path, report: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--prompt", default="你好")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--max-tokens", type=int, default=1)
    args = parser.parse_args()
    if args.repeats < 1 or args.repeats > 10:
        raise SystemExit("--repeats must be between 1 and 10")
    if args.max_tokens < 1 or args.max_tokens > 64:
        raise SystemExit("--max-tokens must be between 1 and 64")

    base_url = args.base_url.rstrip("/")
    started_at = datetime.now(timezone.utc).isoformat()
    health_status, health_payload, health_elapsed = _request(base_url + "/health")
    models_status, models_payload, models_elapsed = _request(base_url + "/v1/models")
    health, health_replacement = _decode(health_payload)
    models, models_replacement = _decode(models_payload)
    json_runs = [
        _json_check(base_url, args.model, args.prompt, args.max_tokens)
        for _ in range(args.repeats)
    ]
    sse = _sse_check(base_url, args.model, args.prompt, args.max_tokens)
    checks = [
        health_status == 200 and health.get("ready") is True and not health_replacement,
        models_status == 200 and not models_replacement,
        all(run["status"] == "passed" for run in json_runs),
        sse["status"] == "passed",
    ]
    report = {
        "schema_version": 1,
        "kind": "qwen25_full2048_strict_api_smoke",
        "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base_url,
        "model": args.model,
        "prompt": args.prompt,
        "constraints": {
            "max_tokens": args.max_tokens,
            "temperature": 0,
            "top_p": 1,
            "json_repeats": args.repeats,
        },
        "health": {"http_status": health_status, "elapsed_seconds": health_elapsed, "body": health, "utf8_replacement": health_replacement},
        "models": {"http_status": models_status, "elapsed_seconds": models_elapsed, "body": models, "utf8_replacement": models_replacement},
        "json_runs": json_runs,
        "sse": sse,
        "status": "passed" if all(checks) else "failed",
    }
    _write_report(args.report, report)
    print(json.dumps({"status": report["status"], "report": str(args.report)}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
