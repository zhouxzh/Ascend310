#!/usr/bin/env python3
"""Exercise loopback OpenAI-compatible JSON and SSE endpoints without requests."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Dict, List, Optional, Tuple
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _request(url: str, body: Optional[Dict[str, Any]] = None) -> Tuple[int, Dict[str, str], bytes, float]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    request = Request(url, data=data, headers={"Content-Type": "application/json"} if data is not None else {}, method="POST" if data is not None else "GET")
    started = time.perf_counter()
    try:
        with urlopen(request, timeout=90) as response:
            return response.status, dict(response.headers.items()), response.read(), time.perf_counter() - started
    except HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read(), time.perf_counter() - started
    except URLError as exc:
        raise RuntimeError(f"request failed: {exc.reason}") from exc


def _json_response(url: str, body: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    status, headers, payload, elapsed = _request(url, body)
    text = payload.decode("utf-8", errors="replace")
    try:
        decoded: Any = json.loads(text)
    except json.JSONDecodeError:
        decoded = {"raw": text}
    return {"http_status": status, "headers": headers, "body": decoded, "elapsed_seconds": elapsed, "utf8_replacement": "\ufffd" in text}


def _sse_response(url: str, body: Dict[str, Any]) -> Dict[str, Any]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    request = Request(url, data=data, headers={"Content-Type": "application/json", "Accept": "text/event-stream"}, method="POST")
    started = time.perf_counter()
    events: List[Dict[str, Any]] = []
    fragments: List[str] = []
    try:
        with urlopen(request, timeout=90) as response:
            for raw_line in response:
                line = raw_line.decode("utf-8", errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                data_line = line[5:].strip()
                if data_line == "[DONE]":
                    events.append({"done": True})
                    break
                try:
                    event = json.loads(data_line)
                except json.JSONDecodeError:
                    events.append({"raw": data_line, "parse_error": True})
                    continue
                events.append(event)
                choices = event.get("choices") if isinstance(event, dict) else None
                if isinstance(choices, list) and choices and isinstance(choices[0], dict):
                    delta = choices[0].get("delta")
                    if isinstance(delta, dict) and isinstance(delta.get("content"), str):
                        fragments.append(delta["content"])
            status = response.status
    except HTTPError as exc:
        return {"http_status": exc.code, "body": exc.read().decode("utf-8", errors="replace"), "elapsed_seconds": time.perf_counter() - started, "events": []}
    except URLError as exc:
        raise RuntimeError(f"SSE request failed: {exc.reason}") from exc
    text = "".join(fragments)
    return {
        "http_status": status,
        "elapsed_seconds": time.perf_counter() - started,
        "events": events,
        "text": text,
        "event_count": len(events),
        "utf8_replacement": "\ufffd" in text,
    }


def _write(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Loopback service URL, for example http://127.0.0.1:18101")
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt", default="请用一句话介绍你自己。")
    parser.add_argument("--max-tokens", type=int, default=8)
    parser.add_argument("--report", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    base = args.base_url.rstrip("/")
    payload = {
        "model": args.model,
        "messages": [{"role": "user", "content": args.prompt}],
        "temperature": 0,
        "top_p": 1,
        "max_tokens": args.max_tokens,
    }
    result: Dict[str, Any] = {
        "schema_version": 1,
        "kind": "openai_compatibility_probe",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "base_url": base,
        "model": args.model,
        "health": _json_response(base + "/health"),
        "models": _json_response(base + "/v1/models"),
    }
    result["json"] = _json_response(base + "/v1/chat/completions", payload)
    stream_payload = dict(payload)
    stream_payload["stream"] = True
    result["sse"] = _sse_response(base + "/v1/chat/completions", stream_payload)
    result["finished_at"] = datetime.now(timezone.utc).isoformat()
    result["status"] = "passed" if all(
        item.get("http_status") == 200 for item in (result["health"], result["models"], result["json"], result["sse"])
    ) else "failed"
    _write(args.report.expanduser(), result)
    print(json.dumps({"status": result["status"], "report": str(args.report.expanduser())}, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
