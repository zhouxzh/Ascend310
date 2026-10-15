#!/usr/bin/env python3
"""Isolated Qwen2.5 static-KV ACL/OM benchmark.

This program owns one service process for the duration of one benchmark.  It
refuses to start when another Case9 process is listening on the selected port,
and terminates only the process group it created.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any, Dict, List
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def snapshot() -> Dict[str, Any]:
    try:
        p = subprocess.run(["npu-smi", "info"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, errors="replace", timeout=8, check=False)
        return {"returncode": p.returncode, "output": p.stdout}
    except Exception as exc:
        return {"returncode": None, "output": repr(exc)}


def get_json(url: str, timeout: float = 10) -> Dict[str, Any]:
    with urlopen(Request(url, method="GET"), timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def post_json(url: str, body: Dict[str, Any], timeout: float) -> Dict[str, Any]:
    request = Request(url, data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
                      headers={"Content-Type": "application/json"}, method="POST")
    with urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def percentile(values: List[float], q: float) -> float:
    values = sorted(values)
    if not values:
        return 0.0
    position = (len(values) - 1) * q
    lower, upper = int(position), min(int(position) + 1, len(values) - 1)
    return values[lower] + (values[upper] - values[lower]) * (position - lower)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=os.environ.get("QWEN25_ROOT", "/home/HwHiAiUser/case9-qwen25-kv1024"))
    parser.add_argument("--launcher", default="scripts/run_qwen25_kv_acl_service.sh")
    parser.add_argument("--port", type=int, default=8084)
    parser.add_argument("--output", required=True)
    parser.add_argument("--prompt", default="你是谁？")
    parser.add_argument("--max-tokens", type=int, default=2)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--loops", type=int, default=5)
    parser.add_argument("--startup-timeout", type=float, default=120)
    parser.add_argument("--request-timeout", type=float, default=120)
    args = parser.parse_args()
    if not 1 <= args.max_tokens <= 8 or args.warmup < 0 or args.loops < 1:
        parser.error("max_tokens must be 1..8, warmup >= 0, loops >= 1")
    root = Path(args.root).resolve()
    output = Path(args.output).resolve(); output.parent.mkdir(parents=True, exist_ok=True)
    report: Dict[str, Any] = {"schema_version": 1, "started_at": now(), "mode": "acl_om_service_isolated",
                              "root": str(root), "port": args.port, "prompt": args.prompt,
                              "max_tokens": args.max_tokens, "warmup": args.warmup, "loops": args.loops,
                              "status": "failed", "npu_before": snapshot()}
    url = "http://127.0.0.1:%d" % args.port
    proc = None
    log = output.with_suffix(".service.log")
    trace = output.with_suffix(".trace.jsonl")
    try:
        # Never attach to an already-running service.  A responsive endpoint
        # may belong to another campaign and must not be killed on cleanup.
        try:
            get_json(url + "/health", timeout=2)
            raise RuntimeError("selected ACL port already has a responsive service")
        except (URLError, HTTPError, TimeoutError):
            pass
        startup_started = time.perf_counter()
        service_env = os.environ.copy()
        service_env["CASE9_ACL_TRACE_FILE"] = str(trace)
        proc = subprocess.Popen(["bash", args.launcher, "--port", str(args.port)], cwd=str(root),
                                stdout=log.open("w", encoding="utf-8"), stderr=subprocess.STDOUT,
                                start_new_session=True, env=service_env, text=True)
        deadline = time.monotonic() + args.startup_timeout
        health = None
        while time.monotonic() < deadline:
            try:
                health = get_json(url + "/health", timeout=3)
                break
            except (URLError, HTTPError, TimeoutError):
                if proc.poll() is not None:
                    raise RuntimeError("ACL service exited during startup: %s" % proc.returncode)
                time.sleep(0.5)
        if health is None:
            raise TimeoutError("ACL service did not become healthy")
        report["startup_seconds"] = time.perf_counter() - startup_started
        models = get_json(url + "/v1/models")
        model = models["data"][0]["id"]
        report["health"] = health; report["model"] = model
        timings: List[Dict[str, Any]] = []
        for index in range(args.warmup + args.loops):
            t0 = time.perf_counter()
            response = post_json(url + "/v1/chat/completions", {
                "model": model, "messages": [{"role": "user", "content": args.prompt}],
                "max_tokens": args.max_tokens, "temperature": 0, "top_p": 1,
            }, args.request_timeout)
            elapsed = time.perf_counter() - t0
            choice = response["choices"][0]
            usage = response.get("usage", {})
            row = {"elapsed_seconds": elapsed, "text": choice["message"]["content"],
                   "finish_reason": choice.get("finish_reason"),
                   "prompt_tokens": usage.get("prompt_tokens"),
                   "completion_tokens": usage.get("completion_tokens")}
            if index >= args.warmup:
                timings.append(row)
        report["measurements"] = timings
        report["performance"] = {
            "count": len(timings),
            "elapsed_p50_seconds": percentile([r["elapsed_seconds"] for r in timings], .5),
            "elapsed_p95_seconds": percentile([r["elapsed_seconds"] for r in timings], .95),
            "tokens_per_second": [r["completion_tokens"] / max(r["elapsed_seconds"], 1e-9) for r in timings],
        }
        # Capability flags such as device_cache_update are lazy and are only
        # known after the first execute. Keep both startup and post-request
        # health so a preflight false value is not misread as a failed path.
        report["health_after"] = get_json(url + "/health")
        report["status"] = "diagnostic_passed"
    except BaseException as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        if proc is not None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
                proc.wait(timeout=15)
            except Exception:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=10)
                except Exception:
                    pass
            report["service_returncode"] = proc.returncode
            report["service_log"] = str(log)
            report["trace_file"] = str(trace)
        report["npu_after"] = snapshot(); report["finished_at"] = now()
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "report": str(output),
                      "service_returncode": report.get("service_returncode")}, ensure_ascii=False))
    return 0 if report["status"] == "diagnostic_passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
