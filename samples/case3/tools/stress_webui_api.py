"""Run a bounded read-only load test against the case3 WebUI API."""

from __future__ import annotations

import argparse
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import statistics
import time
from typing import Any, Iterable, Mapping
import urllib.error
import urllib.parse
import urllib.request


REPORT_SCHEMA = "case3-webui-api-load/v1"
DEFAULT_ENDPOINTS = (
    "/api/v1/status",
    "/api/v1/catalog",
    "/api/v1/audio-inputs",
    "/api/v1/speaker-outputs",
    "/api/v1/midi-ports",
    "/api/v1/bluetooth-audio",
    "/api/v1/ddsp-vst-effect/catalog",
    "/api/v1/ddsp-vst-effect/status",
    "/api/v1/realtime/catalog",
    "/api/v1/realtime/status",
    "/api/v1/speaker-test/status",
    "/api/v1/audio-input-test/status",
    "/api/v1/midi-ddsp/library",
    "/api/v1/jobs",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def percentile(values: Iterable[float], quantile: float) -> float:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return 0.0
    if not 0.0 <= quantile <= 1.0:
        raise ValueError("quantile must be between zero and one")
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def request_sample(base_url: str, endpoint: str, timeout_seconds: float) -> dict[str, Any]:
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))
    started = time.perf_counter()
    status = 0
    error: str | None = None
    response_bytes = 0
    try:
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            status = int(response.status)
            body = response.read()
            response_bytes = len(body)
            json.loads(body)
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        error = f"HTTPError: {exc.read().decode('utf-8', errors='replace')[:500]}"
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    return {
        "endpoint": endpoint,
        "status": status,
        "elapsed_ms": round(elapsed_ms, 3),
        "response_bytes": response_bytes,
        "ok": status == 200 and error is None,
        "error": error,
    }


def summarize_samples(samples: list[Mapping[str, Any]]) -> dict[str, Any]:
    latencies = [float(sample["elapsed_ms"]) for sample in samples]
    passed = [sample for sample in samples if sample.get("ok") is True]
    endpoint_summaries: dict[str, dict[str, Any]] = {}
    for endpoint in sorted({str(sample["endpoint"]) for sample in samples}):
        endpoint_samples = [sample for sample in samples if sample["endpoint"] == endpoint]
        endpoint_latencies = [float(sample["elapsed_ms"]) for sample in endpoint_samples]
        endpoint_summaries[endpoint] = {
            "requests": len(endpoint_samples),
            "errors": sum(sample.get("ok") is not True for sample in endpoint_samples),
            "p50_ms": round(percentile(endpoint_latencies, 0.50), 3),
            "p95_ms": round(percentile(endpoint_latencies, 0.95), 3),
            "p99_ms": round(percentile(endpoint_latencies, 0.99), 3),
            "maximum_ms": round(max(endpoint_latencies, default=0.0), 3),
        }
    request_count = len(samples)
    error_count = request_count - len(passed)
    return {
        "requests": request_count,
        "successful_requests": len(passed),
        "errors": error_count,
        "error_rate": error_count / request_count if request_count else 1.0,
        "mean_ms": round(statistics.fmean(latencies), 3) if latencies else 0.0,
        "p50_ms": round(percentile(latencies, 0.50), 3),
        "p95_ms": round(percentile(latencies, 0.95), 3),
        "p99_ms": round(percentile(latencies, 0.99), 3),
        "maximum_ms": round(max(latencies, default=0.0), 3),
        "endpoints": endpoint_summaries,
    }


def atomic_write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run_load_test(
    *,
    base_url: str,
    endpoints: tuple[str, ...],
    duration_seconds: float,
    requests_per_second: float,
    workers: int,
    timeout_seconds: float,
    output: Path,
) -> dict[str, Any]:
    started_at = utc_now()
    started = time.monotonic()
    deadline = started + duration_seconds
    futures: list[Future[dict[str, Any]]] = []
    sequence = 0
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="case3-api-load") as executor:
        while True:
            target = started + sequence / requests_per_second
            if target >= deadline:
                break
            remaining = target - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            endpoint = endpoints[sequence % len(endpoints)]
            futures.append(executor.submit(request_sample, base_url, endpoint, timeout_seconds))
            sequence += 1
        samples = [future.result() for future in as_completed(futures)]
    samples.sort(key=lambda sample: (str(sample["endpoint"]), float(sample["elapsed_ms"])))
    elapsed_seconds = time.monotonic() - started
    summary = summarize_samples(samples)
    checks = {
        "error_rate_zero": summary["error_rate"] == 0.0,
        "p95_under_500_ms": float(summary["p95_ms"]) < 500.0,
        "p99_under_1000_ms": float(summary["p99_ms"]) < 1000.0,
        "requested_duration_completed": elapsed_seconds >= duration_seconds,
    }
    report = {
        "schema": REPORT_SCHEMA,
        "started_at": started_at,
        "completed_at": utc_now(),
        "base_url": base_url,
        "configuration": {
            "duration_seconds": duration_seconds,
            "requests_per_second": requests_per_second,
            "workers": workers,
            "timeout_seconds": timeout_seconds,
            "endpoints": list(endpoints),
        },
        "elapsed_seconds": round(elapsed_seconds, 3),
        "summary": summary,
        "checks": checks,
        "passed": all(checks.values()),
        "samples": samples,
    }
    atomic_write_json(output, report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://192.168.1.90:8765")
    parser.add_argument("--duration-seconds", type=float, default=300.0)
    parser.add_argument("--requests-per-second", type=float, default=10.0)
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--timeout-seconds", type=float, default=5.0)
    parser.add_argument("--endpoint", action="append", dest="endpoints")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/webui/stress/api-load.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.duration_seconds <= 0 or args.requests_per_second <= 0:
        raise ValueError("duration and request rate must be positive")
    if args.workers <= 0 or args.timeout_seconds <= 0:
        raise ValueError("workers and timeout must be positive")
    endpoints = tuple(args.endpoints or DEFAULT_ENDPOINTS)
    if not endpoints or any(not endpoint.startswith("/api/v1/") for endpoint in endpoints):
        raise ValueError("every endpoint must be a case3 /api/v1/ path")
    report = run_load_test(
        base_url=args.base_url,
        endpoints=endpoints,
        duration_seconds=args.duration_seconds,
        requests_per_second=args.requests_per_second,
        workers=args.workers,
        timeout_seconds=args.timeout_seconds,
        output=args.output.resolve(),
    )
    print(json.dumps({"summary": report["summary"], "checks": report["checks"]}, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
