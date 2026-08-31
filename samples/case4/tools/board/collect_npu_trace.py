#!/usr/bin/env python3
"""Run one board command while recording NPU, thermal, and fault evidence.

This helper is intentionally board-only.  It never changes driver, CANN,
firmware, cooling, model assets, datasets, or templates.  Its reports live
under the ignored ``reports/system`` directory so a failed ACL process still
leaves a time-correlated diagnostic trail outside source control.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any

from palmprint_workbench.config import ROOT


DEFAULT_OUTPUT_DIR = ROOT / "reports" / "system"
_LABEL = re.compile(r"[^a-zA-Z0-9_.-]+")
FAULT_BLOCKED_EXIT_CODE = 70
_BLOCKING_PATTERNS = {
    "exit_139": re.compile(r"(?:exit(?:ed)?\s+(?:code\s*)?139|segmentation fault)", re.IGNORECASE),
    "err_ret_minus_512": re.compile(r"err_ret\s*=\s*-512", re.IGNORECASE),
    "lpm": re.compile(
        r"(?:DRV_LPM_FAULT|LPM_EXCEPTION|\[LPM\]|0xA6193215|0x80E3A203|lpm get current error)",
        re.IGNORECASE,
    ),
    "aicore": re.compile(r"AI\s*Core|AICore", re.IGNORECASE),
    "ras": re.compile(r"\bRAS\b|Hardware Error", re.IGNORECASE),
    "device_reset": re.compile(r"reset_core_mk|device\s+reset|reset\s+device", re.IGNORECASE),
    "driver_release_failure": re.compile(
        r"Kthread_create not up to expectations|cleanup failed|resource leak",
        re.IGNORECASE,
    ),
}


def _timestamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def _run(command: list[str], *, timeout: float = 15.0) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout,
        )
        return {
            "command": command,
            "return_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": command, "return_code": None, "error": f"{type(exc).__name__}: {exc}"}


def _parse_npu_smi(raw: str) -> dict[str, Any]:
    """Extract stable fields when the installed npu-smi layout permits it."""

    parsed: dict[str, Any] = {}
    for line in raw.splitlines():
        if "310B" in line and "|" in line:
            fields = [field.strip() for field in line.strip().strip("|").split("|")]
            # npu-smi 25.2.0 prints three cells here:
            # ``0  310B4 | Alarm | 0.0  80  15 / 15``.  Earlier code
            # assumed an extra cell and therefore mislabeled Alarm as the
            # model, which makes thermal/fault correlation unreliable.
            if len(fields) >= 3:
                device_model = re.match(r"^(\d+)\s+(\S+)", fields[0])
                if device_model:
                    parsed["device"] = int(device_model.group(1))
                    parsed["npu_model"] = device_model.group(2)
                else:
                    parsed["device"] = fields[0]
                parsed["health"] = fields[1]
                metrics = fields[2]
                numbers = re.findall(r"\d+(?:\.\d+)?", metrics)
                if len(numbers) >= 2:
                    parsed["power_w"] = float(numbers[0])
                    parsed["temperature_c"] = float(numbers[1])
                huge = re.search(r"(\d+)\s*/\s*(\d+)", metrics)
                if huge:
                    parsed["hugepages_used"] = int(huge.group(1))
                    parsed["hugepages_total"] = int(huge.group(2))
        elif "Memory-Usage" not in line and "|" in line and re.search(r"\d+\s*/\s*\d+", line):
            fields = [field.strip() for field in line.strip().strip("|").split("|")]
            if fields and re.match(r"^\d+\s+\d+$", fields[0]):
                memory = re.search(r"(\d+)\s*/\s*(\d+)", fields[-1])
                if memory:
                    parsed["memory_mb_used"] = int(memory.group(1))
                    parsed["memory_mb_total"] = int(memory.group(2))
    return parsed


def _sample(phase: str) -> dict[str, Any]:
    snapshot = _run(["npu-smi", "info"])
    raw = str(snapshot.get("stdout", ""))
    return {
        "timestamp": _timestamp(),
        "phase": phase,
        "npu_smi": _parse_npu_smi(raw),
        "npu_smi_return_code": snapshot.get("return_code"),
        "npu_smi_raw": raw,
        "npu_smi_stderr": snapshot.get("stderr", snapshot.get("error", "")),
    }


def _tail(path: Path, lines: int = 300) -> dict[str, Any]:
    if not path.is_file():
        return {"path": str(path), "status": "missing"}
    try:
        content = path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
        return {"path": str(path), "status": "ok", "lines": content}
    except OSError as exc:
        return {"path": str(path), "status": "error", "error": str(exc)}


def _fault_snapshot() -> dict[str, Any]:
    return {
        "timestamp": _timestamp(),
        "dmesg_tail": _run(["dmesg", "-T"], timeout=10.0),
        "lpm_history_tail": _tail(Path("/var/log/npu/hisi_logs/device-0/history.log")),
    }


def _snapshot_lines(snapshot: dict[str, Any]) -> dict[str, list[str]]:
    dmesg = snapshot.get("dmesg_tail", {})
    history = snapshot.get("lpm_history_tail", {})
    dmesg_text = dmesg.get("stdout", "") if isinstance(dmesg, dict) else ""
    history_lines = history.get("lines", []) if isinstance(history, dict) else []
    return {
        "dmesg": str(dmesg_text).splitlines(),
        "lpm_history": [str(line) for line in history_lines] if isinstance(history_lines, list) else [],
    }


def _new_lines(before: list[str], after: list[str]) -> list[str]:
    """Return the ordered multiset difference so recurring old faults are ignored."""

    remaining = Counter(before)
    delta: list[str] = []
    for line in after:
        if remaining[line] > 0:
            remaining[line] -= 1
        else:
            delta.append(line)
    return delta


def _fault_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_lines = _snapshot_lines(before)
    after_lines = _snapshot_lines(after)
    events: list[dict[str, Any]] = []
    new_line_count = 0
    for source, lines in after_lines.items():
        new_lines = _new_lines(before_lines.get(source, []), lines)
        new_line_count += len(new_lines)
        for line in new_lines:
            categories = [name for name, pattern in _BLOCKING_PATTERNS.items() if pattern.search(line)]
            if categories:
                events.append({"source": source, "categories": categories, "line": line})
    categories = sorted({category for event in events for category in event["categories"]})
    return {
        "new_line_count": new_line_count,
        "has_blocking_faults": bool(categories),
        "blocking_categories": categories,
        "blocking_events": events,
    }


def _safe_label(value: str) -> str:
    result = _LABEL.sub("-", value.strip()).strip("-.")
    return result or "npu-task"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True, help="short task label used in ignored report names")
    parser.add_argument("--interval", type=float, default=1.0, help="npu-smi sample interval in seconds")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="command to run after '--'; it inherits the already activated board environment",
    )
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be positive")
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("provide a command after '--'")
    return args


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    stem = f"{_safe_label(args.label)}_{stamp}"
    trace_path = output_dir / f"{stem}.jsonl"
    summary_path = output_dir / f"{stem}.json"
    command = [str(item) for item in args.command]
    summary: dict[str, Any] = {
        "label": args.label,
        "command": command,
        "started_at": _timestamp(),
        "trace_path": str(trace_path),
        "faults_before": _fault_snapshot(),
    }

    process: subprocess.Popen[str] | None = None
    with trace_path.open("w", encoding="utf-8") as trace:
        def write(sample: dict[str, Any]) -> None:
            trace.write(json.dumps(sample, ensure_ascii=False) + "\n")
            trace.flush()

        write(_sample("before_command"))
        try:
            process = subprocess.Popen(command, cwd=ROOT)
            next_sample = time.monotonic()
            while process.poll() is None:
                now = time.monotonic()
                if now >= next_sample:
                    write(_sample("running"))
                    next_sample = now + float(args.interval)
                time.sleep(min(0.1, max(0.01, next_sample - time.monotonic())))
            summary["command_return_code"] = process.returncode
        except BaseException as exc:
            summary["command_return_code"] = None
            summary["command_exception"] = f"{type(exc).__name__}: {exc}"
        finally:
            write(_sample("after_command"))

    summary["finished_at"] = _timestamp()
    summary["faults_after"] = _fault_snapshot()
    summary["fault_delta"] = _fault_delta(summary["faults_before"], summary["faults_after"])
    return_code = summary.get("command_return_code")
    if not isinstance(return_code, int) or return_code != 0:
        summary["status"] = "failed_command"
    elif summary["fault_delta"]["has_blocking_faults"]:
        summary["status"] = "blocked_faults"
    else:
        summary["status"] = "passed"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "summary": str(summary_path),
                "trace": str(trace_path),
                "status": summary["status"],
                "command_return_code": return_code,
                "blocking_categories": summary["fault_delta"]["blocking_categories"],
            },
            ensure_ascii=False,
        )
    )
    if not isinstance(return_code, int):
        return 1
    if return_code != 0:
        return int(return_code)
    return FAULT_BLOCKED_EXIT_CODE if summary["fault_delta"]["has_blocking_faults"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
