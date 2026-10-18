#!/usr/bin/env python3
"""Measure board-side output-budget behavior through the teaching UI.

This standard-library client records an observed result for every requested
budget. It does not infer a hardware limit from a configured maximum or from
a response that stops early with EOS.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_PROMPT = "请从 1 开始逐行输出连续整数，不要解释，不要提前结束。"
CHAT_TEMPLATE_MARKERS = (
    "<|user|>",
    "<|assistant|>",
    "<|im_start|>user",
    "<|im_start|>assistant",
)


class ProbeError(RuntimeError):
    """Raised for a transport or protocol error during an output-budget probe."""


def _parse_budgets(value: str) -> tuple[int, ...]:
    try:
        budgets = tuple(int(item.strip()) for item in value.split(",") if item.strip())
    except ValueError as exc:
        raise argparse.ArgumentTypeError("budgets must be comma-separated integers") from exc
    if not budgets or any(item < 1 or item > 256 for item in budgets):
        raise argparse.ArgumentTypeError("each budget must be between 1 and 256")
    if tuple(sorted(set(budgets))) != budgets:
        raise argparse.ArgumentTypeError("budgets must be unique and ascending")
    return budgets


def _get_json(url: str, timeout: float) -> dict[str, Any]:
    try:
        with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (HTTPError, URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeError("GET %s failed: %s" % (url, exc)) from exc
    if not isinstance(payload, dict):
        raise ProbeError("GET %s returned a non-object JSON response" % url)
    return payload


def _snapshot_npu() -> str:
    completed = subprocess.run(
        ["npu-smi", "info"], capture_output=True, text=True, check=False
    )
    return completed.stdout + completed.stderr


def _health_or_unavailable(url: str, timeout: float) -> dict[str, Any]:
    """Retain a worker-exit observation instead of discarding the whole run."""

    try:
        return _get_json(url, timeout)
    except ProbeError as exc:
        return {"status": "unavailable", "error": str(exc)}


def _write_report(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _stream_completion(
    url: str, profile: str, prompt: str, budget: int, timeout: float
) -> dict[str, Any]:
    """Measure the browser's SSE path and retain its terminal event."""

    body = json.dumps(
        {"message": prompt, "stream": True, "profile": profile, "max_tokens": budget},
        ensure_ascii=False,
    ).encode("utf-8")
    request = Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    started = time.monotonic()
    try:
        with urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            events: list[dict[str, Any]] = []
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                event = json.loads(line[5:].strip())
                if not isinstance(event, dict):
                    raise ProbeError("budget %d emitted a non-object SSE event" % budget)
                events.append(event)
    except HTTPError as exc:
        status = int(exc.code)
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            payload = {"error": {"message": "non-JSON error response"}}
    except (URLError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProbeError("budget %d request failed: %s" % (budget, exc)) from exc
    elapsed_ms = round((time.monotonic() - started) * 1000.0, 3)
    row: dict[str, Any] = {"requested_max_tokens": budget, "http_status": status, "elapsed_ms": elapsed_ms}
    if status != 200:
        row["outcome"] = "request_failed"
        row["response"] = payload
        return row
    terminal = next((item for item in reversed(events) if item.get("type") in {"done", "error"}), None)
    if terminal is None:
        raise ProbeError("budget %d stream ended without a terminal event" % budget)
    if terminal.get("type") == "error":
        row["outcome"] = "worker_error"
        row["response"] = terminal
        return row
    text = terminal.get("text") if isinstance(terminal.get("text"), str) else ""
    finish_reason = terminal.get("finish_reason")
    row.update(
        {
            "finish_reason": finish_reason,
            "text": text,
            "contains_replacement_character": "\ufffd" in text,
            "contains_chat_template_marker": any(
                marker in text for marker in CHAT_TEMPLATE_MARKERS
            ),
            "sse_event_count": len(events),
        }
    )
    if finish_reason == "length":
        row["outcome"] = "budget_reached"
    elif finish_reason == "stop":
        row["outcome"] = "stopped_before_budget"
    else:
        row["outcome"] = "protocol_anomaly"
    return row


def main() -> int:
    parser = argparse.ArgumentParser(description="measure Ascend Edge Chat output budgets")
    parser.add_argument("--ui", default="http://127.0.0.1:7865")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--budgets", type=_parse_budgets, default=(128, 192, 256))
    parser.add_argument("--prompt", default=DEFAULT_PROMPT)
    parser.add_argument("--timeout", type=float, default=330.0)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")

    base = args.ui.rstrip("/")
    report: dict[str, Any] = {
        "probe": "output-budget",
        "started_at_epoch": time.time(),
        "ui": base,
        "profile": args.profile,
        "prompt": args.prompt,
        "requested_budgets": list(args.budgets),
        "ui_config_before": _health_or_unavailable(base + "/api/config", args.timeout),
        "ui_health_before": _health_or_unavailable(base + "/health", args.timeout),
        "worker_health_before": _health_or_unavailable("http://127.0.0.1:8090/health", args.timeout),
        "npu_before": _snapshot_npu(),
        "measurements": [],
    }
    _write_report(args.output, report)
    for budget in args.budgets:
        try:
            measurement = _stream_completion(
                base + "/api/chat", args.profile, args.prompt, budget, args.timeout
            )
        except ProbeError as exc:
            measurement = {
                "requested_max_tokens": budget,
                "outcome": "collector_error",
                "error": str(exc),
            }
        worker_health = _health_or_unavailable("http://127.0.0.1:8090/health", args.timeout)
        measurement["worker_health"] = worker_health
        measurement["completion_tokens"] = worker_health.get("last_completion_tokens")
        report["measurements"].append(measurement)
        _write_report(args.output, report)
        if worker_health.get("status") == "unavailable":
            break
    report["worker_health_after"] = _health_or_unavailable("http://127.0.0.1:8090/health", args.timeout)
    report["ui_health_after"] = _health_or_unavailable(base + "/health", args.timeout)
    report["npu_after"] = _snapshot_npu()
    report["finished_at_epoch"] = time.time()
    report["observed_max_budget_reached"] = max(
        (item["requested_max_tokens"] for item in report["measurements"] if item.get("outcome") == "budget_reached"),
        default=None,
    )
    _write_report(args.output, report)
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
