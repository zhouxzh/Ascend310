#!/usr/bin/env python3
"""Isolated Qwen2.5 MindSpore benchmark for an Ascend board.

The parent process never imports MindSpore.  It starts one worker, polls a
small JSON-lines event file, and kills only that worker on timeout.  This is
deliberately a diagnostic benchmark; it does not promote a model or start an
HTTP service.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional


def now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def command(argv: List[str], timeout: float = 10.0) -> Dict[str, Any]:
    try:
        p = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, errors="replace", timeout=timeout, check=False)
        return {"argv": argv, "returncode": p.returncode, "output": p.stdout}
    except Exception as exc:
        return {"argv": argv, "returncode": None, "output": repr(exc)}


def append_event(path: Path, event: Dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        stream.flush()
        os.fsync(stream.fileno())


def worker(args: argparse.Namespace) -> int:
    # All MindSpore imports remain inside the isolated worker.
    root = Path(args.root).resolve()
    os.environ.update({
        "CASE9_MODEL_ROOT": str(root),
        "CASE9_DEVICE_TARGET": "Ascend",
        "CASE9_DEVICE_ID": "0",
        "CASE9_REQUIRE_PLACEMENT_EVIDENCE": "0",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    })
    sys.path.insert(0, str(root))
    try:
        from case9_model_profiles import load_profiles
        from mindspore_chat_providers import create_provider
        profile = load_profiles(root / "configs" / "chat_model_profiles.json").get(args.profile)
        provider = create_provider(profile, generation_timeout=args.generation_timeout)
        started = time.perf_counter()
        provider.load()
        append_event(Path(args.events), {"event": "ready", "at": now(),
                                         "load_seconds": time.perf_counter() - started,
                                         "status": provider.status()})
        for raw in sys.stdin:
            request = json.loads(raw)
            if request.get("op") == "close":
                break
            if request.get("op") != "generate":
                append_event(Path(args.events), {"event": "error", "message": "unknown operation"})
                continue
            t0 = time.perf_counter()
            result = provider.complete(
                [{"role": "user", "content": str(request.get("prompt", args.prompt))}],
                int(request.get("max_tokens", args.max_tokens)),
            )
            append_event(Path(args.events), {"event": "result", "at": now(),
                                             "elapsed_seconds": time.perf_counter() - t0,
                                             "text": result.text,
                                             "prompt_tokens": result.prompt_tokens,
                                             "completion_tokens": result.completion_tokens,
                                             "finish_reason": result.finish_reason})
        provider.close()
        append_event(Path(args.events), {"event": "closed", "at": now()})
        return 0
    except BaseException as exc:
        append_event(Path(args.events), {"event": "error", "at": now(),
                                         "type": type(exc).__name__, "message": str(exc)})
        return 1


def snapshot() -> Dict[str, Any]:
    return command(["npu-smi", "info"], timeout=8)


def read_events(path: Path, offset: int) -> tuple[List[Dict[str, Any]], int]:
    if not path.exists():
        return [], offset
    with path.open("r", encoding="utf-8") as stream:
        stream.seek(offset)
        rows = [json.loads(line) for line in stream if line.strip()]
        return rows, stream.tell()


def run_parent(args: argparse.Namespace) -> int:
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    events = Path(tempfile.mktemp(prefix="case9-ms-events-", suffix=".jsonl"))
    log = output.with_suffix(".worker.log")
    report: Dict[str, Any] = {"schema_version": 1, "started_at": now(),
                              "mode": "mindspore_worker_isolated", "profile": args.profile,
                              "root": str(Path(args.root).resolve()), "prompt": args.prompt,
                              "max_tokens": args.max_tokens, "warmup": args.warmup,
                              "loops": args.loops, "status": "failed", "events": [],
                              "npu_before": snapshot()}
    script = str(Path(__file__).resolve())
    env = os.environ.copy()
    env["PYTHONNOUSERSITE"] = "0"
    cmd = [sys.executable, script, "--worker", "--root", str(args.root),
           "--profile", args.profile, "--prompt", args.prompt,
           "--max-tokens", str(args.max_tokens), "--generation-timeout", str(args.generation_timeout),
           "--events", str(events)]
    with log.open("w", encoding="utf-8") as log_stream:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=log_stream,
                                stderr=subprocess.STDOUT, text=True, env=env,
                                start_new_session=True)
        offset = 0
        deadline = time.monotonic() + args.load_timeout
        ready: Optional[Dict[str, Any]] = None
        try:
            while time.monotonic() < deadline and ready is None:
                rows, offset = read_events(events, offset)
                report["events"].extend(rows)
                ready = next((row for row in rows if row.get("event") == "ready"), None)
                if any(row.get("event") == "error" for row in rows):
                    break
                time.sleep(0.2)
            if ready is None:
                raise TimeoutError("MindSpore worker did not become ready before load timeout")
            timings: List[Dict[str, Any]] = []
            for index in range(args.warmup + args.loops):
                if proc.stdin is None:
                    raise RuntimeError("worker stdin is closed")
                proc.stdin.write(json.dumps({"op": "generate", "prompt": args.prompt,
                                             "max_tokens": args.max_tokens}) + "\n")
                proc.stdin.flush()
                deadline = time.monotonic() + args.request_timeout
                result = None
                while time.monotonic() < deadline and result is None:
                    rows, offset = read_events(events, offset)
                    report["events"].extend(rows)
                    result = next((row for row in rows if row.get("event") == "result"), None)
                    error = next((row for row in rows if row.get("event") == "error"), None)
                    if error:
                        raise RuntimeError(error.get("message", "worker error"))
                    time.sleep(0.05)
                if result is None:
                    raise TimeoutError("MindSpore generation timed out")
                if index >= args.warmup:
                    timings.append(result)
            report["load_seconds"] = ready.get("load_seconds")
            report["measurements"] = timings
            report["performance"] = {
                "count": len(timings),
                "elapsed_seconds": [row["elapsed_seconds"] for row in timings],
                "tokens_per_second": [row["completion_tokens"] / max(row["elapsed_seconds"], 1e-9) for row in timings],
            }
            report["status"] = "diagnostic_passed"
        except BaseException as exc:
            report["error"] = {"type": type(exc).__name__, "message": str(exc)}
        finally:
            try:
                if proc.stdin and proc.poll() is None:
                    proc.stdin.write('{"op":"close"}\n'); proc.stdin.flush()
            except Exception:
                pass
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=10)
            report["worker_returncode"] = proc.returncode
            report["worker_log"] = str(log)
            report["npu_after"] = snapshot()
            report["finished_at"] = now()
            output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            try:
                events.unlink()
            except OSError:
                pass
    print(json.dumps({"status": report["status"], "report": str(output),
                      "worker_returncode": report.get("worker_returncode")}, ensure_ascii=False))
    return 0 if report["status"] == "diagnostic_passed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", action="store_true")
    parser.add_argument("--root", default=os.environ.get("CASE9_MODEL_ROOT", "."))
    parser.add_argument("--profile", default="qwen2.5-0.5b-mindspore")
    parser.add_argument("--output", default="qwen25-mindspore-benchmark.json")
    parser.add_argument("--prompt", default="你是谁？")
    parser.add_argument("--max-tokens", type=int, default=2)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--loops", type=int, default=5)
    parser.add_argument("--load-timeout", type=float, default=300)
    parser.add_argument("--request-timeout", type=float, default=120)
    parser.add_argument("--generation-timeout", type=float, default=100)
    parser.add_argument("--events", default="")
    args = parser.parse_args()
    if args.max_tokens < 1 or args.max_tokens > 8 or args.warmup < 0 or args.loops < 1:
        parser.error("max_tokens must be 1..8, warmup >= 0, loops >= 1")
    if args.worker:
        if not args.events:
            parser.error("--events is required in worker mode")
        return worker(args)
    return run_parent(args)


if __name__ == "__main__":
    raise SystemExit(main())
