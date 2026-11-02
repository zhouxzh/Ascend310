#!/usr/bin/env python3
"""Measure fixed-KV B1 token requests through an isolated loopback worker."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


MODEL_ID = "qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om"
PROMPT = "请逐行重复输出“固定KV容量测试”，不要解释，直到达到输出上限。"


def get_json(url: str, *, timeout: float) -> dict[str, Any]:
    with urlopen(Request(url, headers={"Accept": "application/json"}), timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("health response is not an object")
    return payload


def post_json(url: str, payload: dict[str, Any], *, timeout: float) -> tuple[int, dict[str, Any]]:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status = response.status
            body = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        status = exc.code
        try:
            body = json.loads(exc.read().decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = {"error": {"message": "non-JSON HTTP error"}}
    if not isinstance(body, dict):
        body = {"error": {"message": "response is not an object"}}
    return status, body


def board_snapshot() -> dict[str, Any]:
    snapshot: dict[str, Any] = {}
    try:
        memory: dict[str, int] = {}
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            if ":" not in line:
                continue
            name, raw = line.split(":", 1)
            value = raw.strip().split()[0]
            if name in {"MemTotal", "MemAvailable"}:
                memory[name] = int(value) // 1024
        if "MemTotal" in memory and "MemAvailable" in memory:
            snapshot["system_memory_total_mb"] = memory["MemTotal"]
            snapshot["system_memory_available_mb"] = memory["MemAvailable"]
            snapshot["system_memory_used_mb"] = memory["MemTotal"] - memory["MemAvailable"]
    except (OSError, ValueError):
        pass
    try:
        snapshot["boot_id"] = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    except OSError:
        snapshot["boot_id"] = None
    try:
        completed = subprocess.run(
            ["npu-smi", "info"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10.0,
        )
        matches = re.findall(r"(\d+)\s*/\s*(\d+)", completed.stdout)
        if matches:
            used, total = matches[-1]
            snapshot["npu_memory_used_mb"] = int(used)
            snapshot["npu_memory_total_mb"] = int(total)
        snapshot["npu_smi_exit_code"] = completed.returncode
    except (OSError, subprocess.SubprocessError):
        snapshot["npu_smi_exit_code"] = None
    return snapshot


def write_report(path: Path, report: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--kind", default="20t-b1-static-kv-token-ceiling")
    parser.add_argument("--levels", default="8,16,24,32,40,48,56,64,72,80")
    parser.add_argument("--prompt", default=PROMPT)
    parser.add_argument("--request-timeout", type=float, default=260.0)
    parser.add_argument("--require-force-continue", action="store_true")
    args = parser.parse_args()
    levels = [int(value) for value in args.levels.split(",") if value.strip()]
    if not levels or any(value < 1 or value > 1024 for value in levels) or levels != sorted(set(levels)):
        parser.error("levels must be unique ascending integers from 1 through 1024")
    base_url = args.url.rstrip("/")
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": args.kind,
        "started_at_epoch_s": time.time(),
        "url": base_url,
        "model": MODEL_ID,
        "prompt": args.prompt,
        "levels": levels,
        "results": [],
        "stopped_reason": "running",
        "require_force_continue": args.require_force_continue,
        "board_before": board_snapshot(),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    health = get_json(base_url + "/health", timeout=10.0)
    report["initial_health"] = health
    if args.require_force_continue:
        if (
            health.get("force_continue") is not True
            or health.get("sentence_stop_enabled") is not False
            or not isinstance(health.get("eos_masked_ids"), list)
            or not health.get("eos_masked_ids")
        ):
            raise RuntimeError("worker health does not prove force-continue mode")
    write_report(args.out, report)
    for level in levels:
        started = time.monotonic()
        row: dict[str, Any] = {
            "requested_max_tokens": level,
            "board_before": board_snapshot(),
        }
        try:
            status, body = post_json(
                base_url + "/v1/chat/completions",
                {"model": MODEL_ID, "messages": [{"role": "user", "content": args.prompt}], "stream": False, "max_tokens": level},
                timeout=args.request_timeout,
            )
            row["http_status"] = status
            row["elapsed_seconds"] = round(time.monotonic() - started, 3)
            usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
            row["prompt_tokens"] = usage.get("prompt_tokens")
            row["completion_tokens"] = usage.get("completion_tokens")
            choices = body.get("choices") if isinstance(body.get("choices"), list) else []
            choice = choices[0] if choices and isinstance(choices[0], dict) else {}
            row["finish_reason"] = choice.get("finish_reason")
            content = choice.get("message", {}).get("content") if isinstance(choice.get("message"), dict) else ""
            row["response_characters"] = len(content) if isinstance(content, str) else 0
            if status != 200:
                row["error"] = body.get("error")
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            row["http_status"] = None
            row["elapsed_seconds"] = round(time.monotonic() - started, 3)
            row["error"] = type(exc).__name__
        row["board_after"] = board_snapshot()
        report["results"].append(row)
        try:
            health = get_json(base_url + "/health", timeout=10.0)
            row["health_after"] = {key: health.get(key) for key in ("ready", "healthy", "restart_required", "watchdog_triggered", "runtime_error")}
        except (URLError, TimeoutError, OSError, ValueError) as exc:
            row["health_after"] = {"error": type(exc).__name__}
        write_report(args.out, report)
        failed = row.get("http_status") != 200 or row["health_after"].get("healthy") is not True
        if args.require_force_continue:
            failed = failed or row.get("completion_tokens") != level or row.get("finish_reason") != "length"
            failed = failed or row["board_before"].get("boot_id") != row["board_after"].get("boot_id")
        if failed:
            report["stopped_reason"] = "request_or_health_failure_at_%d" % level
            report["board_after"] = board_snapshot()
            write_report(args.out, report)
            return 1
    report["stopped_reason"] = "completed"
    report["board_after"] = board_snapshot()
    write_report(args.out, report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
