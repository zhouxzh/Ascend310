"""Run and record a publication-grade DDSP-VST Effect long-duration test."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping
import urllib.error
import urllib.parse
import urllib.request


REPORT_SCHEMA = "case3-ddsp-vst-long-run/v1"
MINIMUM_QUALIFYING_SECONDS = 600.0
MAX_COMBINED_MODEL_P95_MS = 20.0
MAX_TOTAL_LATENCY_MS = 150.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def request_json(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    payload: Mapping[str, object] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{method} {path} failed with HTTP {exc.code}: {detail}") from exc
    if not isinstance(result, dict):
        raise ValueError(f"{method} {path} returned a non-object JSON response")
    return result


def _select_named(items: object, marker: str, kind: str) -> dict[str, Any]:
    if not isinstance(items, list):
        raise ValueError(f"Catalog {kind} list is invalid")
    matches = [
        item
        for item in items
        if isinstance(item, dict) and marker.casefold() in str(item.get("name", "")).casefold()
    ]
    if len(matches) != 1:
        raise ValueError(f"Expected exactly one {kind} containing {marker!r}, found {len(matches)}")
    return dict(matches[0])


def select_catalog(catalog: Mapping[str, object]) -> dict[str, dict[str, Any]]:
    if catalog.get("available") is not True or catalog.get("backend") != "acl/om":
        raise ValueError(f"DDSP-VST catalog is unavailable: {catalog.get('error')}")
    models = catalog.get("models")
    if not isinstance(models, list) or len(models) != 11:
        raise ValueError("DDSP-VST catalog must contain exactly 11 Control OM models")
    model = _select_named(models, "Violin", "Violin model")
    audio_input = _select_named(catalog.get("audio_inputs"), "UGREEN", "capture input")
    audio_output = _select_named(catalog.get("audio_outputs"), "EDIFIER", "audio output")
    if (
        model.get("backend") != "om"
        or audio_input.get("backend") != "pulse"
        or audio_input.get("type") != "capture"
        or audio_input.get("available") is not True
        or audio_output.get("backend") != "pulse"
    ):
        raise ValueError("Selected model or physical audio device does not satisfy the OM/Pulse contract")
    return {"model": model, "audio_input": audio_input, "audio_output": audio_output}


def _metrics(sample: Mapping[str, object]) -> Mapping[str, object]:
    value = sample.get("metrics")
    if not isinstance(value, dict):
        raise ValueError("DDSP-VST status has no metrics object")
    return value


def evaluate_samples(
    samples: list[dict[str, Any]],
    *,
    requested_seconds: float,
    stimulus_description: str,
    independent_stimulus_confirmed: bool,
) -> dict[str, object]:
    if not samples:
        raise ValueError("No DDSP-VST status samples were captured")
    metrics = [_metrics(sample) for sample in samples]
    frames = [int(item.get("frames", 0)) for item in metrics]
    final = metrics[-1]
    checks = {
        "duration_at_least_600_seconds": requested_seconds >= MINIMUM_QUALIFYING_SECONDS
        and float(final.get("elapsed_seconds", 0.0)) >= MINIMUM_QUALIFYING_SECONDS,
        "independent_monophonic_stimulus_recorded": independent_stimulus_confirmed
        and bool(stimulus_description.strip()),
        "frames_advanced_monotonically": frames[-1] > frames[0]
        and all(current >= previous for previous, current in zip(frames, frames[1:])),
        "non_silent_physical_input": any(
            float(item.get("input_peak_dbfs", -96.0)) > -90.0 for item in metrics
        ),
        "non_silent_output": any(
            float(item.get("output_peak_dbfs", -96.0)) > -90.0 for item in metrics
        ),
        "valid_f0_observed": any(float(item.get("f0_hz", 0.0)) > 0.0 for item in metrics),
        "combined_model_p95_under_20_ms": (
            float(final.get("feature_p95_ms", float("inf")))
            + float(final.get("control_p95_ms", float("inf")))
        )
        < MAX_COMBINED_MODEL_P95_MS,
        "total_latency_under_150_ms": float(final.get("total_latency_ms", float("inf")))
        < MAX_TOTAL_LATENCY_MS,
        "capture_overflows_zero": max(int(item.get("capture_overflows", 0)) for item in metrics)
        == 0,
        "playback_underruns_zero": max(int(item.get("playback_underruns", 0)) for item in metrics)
        == 0,
        "clipped_samples_zero": max(int(item.get("clipped_samples", 0)) for item in metrics) == 0,
        "safety_mute_not_triggered": not any(bool(item.get("safety_muted")) for item in metrics),
    }
    return {
        "thresholds": {
            "minimum_duration_seconds": MINIMUM_QUALIFYING_SECONDS,
            "maximum_combined_model_p95_ms": MAX_COMBINED_MODEL_P95_MS,
            "maximum_total_latency_ms": MAX_TOTAL_LATENCY_MS,
            "non_silent_peak_dbfs": -90.0,
        },
        "checks": checks,
        "passed": all(checks.values()),
    }


def atomic_write_json(path: Path, value: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".part")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run_benchmark(
    *,
    base_url: str,
    duration_seconds: float,
    poll_interval_seconds: float,
    stimulus_description: str,
    independent_stimulus_confirmed: bool,
    output: Path,
    api: Callable[..., dict[str, Any]] = request_json,
    monotonic: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, object]:
    catalog = api(base_url, "/api/v1/ddsp-vst-effect/catalog")
    selected = select_catalog(catalog)
    payload = {
        "model_id": selected["model"]["id"],
        "audio_input_id": selected["audio_input"]["id"],
        "audio_output_id": selected["audio_output"]["id"],
        "parameters": {},
        "device_id": 0,
    }
    started_at = utc_now()
    samples: list[dict[str, Any]] = []
    error: str | None = None
    stop_response: dict[str, Any] | None = None
    try:
        start_response = api(
            base_url, "/api/v1/ddsp-vst-effect/start", method="POST", payload=payload
        )
        if start_response.get("state") != "running" or start_response.get("running") is not True:
            raise RuntimeError(f"DDSP-VST Effect did not enter running state: {start_response}")
        started = monotonic()
        while True:
            status = api(base_url, "/api/v1/ddsp-vst-effect/status")
            status["observed_at"] = utc_now()
            samples.append(status)
            elapsed = monotonic() - started
            if elapsed >= duration_seconds:
                break
            sleep(min(poll_interval_seconds, max(0.0, duration_seconds - elapsed)))
    except BaseException as exc:
        error = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            stop_response = api(
                base_url, "/api/v1/ddsp-vst-effect/stop", method="POST", payload={}
            )
        except BaseException as stop_exc:
            stop_error = f"{type(stop_exc).__name__}: {stop_exc}"
            error = f"{error}; stop failed: {stop_error}" if error else f"stop failed: {stop_error}"

    qualification: dict[str, object]
    try:
        qualification = evaluate_samples(
            samples,
            requested_seconds=duration_seconds,
            stimulus_description=stimulus_description,
            independent_stimulus_confirmed=independent_stimulus_confirmed,
        )
    except BaseException as exc:
        qualification = {"checks": {}, "passed": False, "error": str(exc)}
    if error:
        qualification["passed"] = False
    report = {
        "schema": REPORT_SCHEMA,
        "started_at": started_at,
        "completed_at": utc_now(),
        "base_url": base_url,
        "requested_duration_seconds": duration_seconds,
        "poll_interval_seconds": poll_interval_seconds,
        "stimulus": {
            "type": "independent-monophonic-acoustic-source",
            "description": stimulus_description,
            "confirmed": independent_stimulus_confirmed,
            "feedback_loop_prohibited": True,
        },
        "catalog": {
            "model_count": len(catalog["models"]),
            "feature_model": catalog.get("feature_model"),
            **selected,
        },
        "request": payload,
        "samples": samples,
        "final_running_status": samples[-1] if samples else None,
        "stop_response": stop_response,
        "qualification": qualification,
        "error": error,
    }
    atomic_write_json(output, report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--duration-seconds", type=float, default=600.0)
    parser.add_argument("--poll-interval-seconds", type=float, default=10.0)
    parser.add_argument("--stimulus-description", required=True)
    parser.add_argument(
        "--confirm-independent-monophonic-stimulus",
        action="store_true",
        help="Confirm the source is independent of the Effect playback sink.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/publication/ddsp-vst-effect-long-run.json"),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.duration_seconds <= 0 or args.poll_interval_seconds <= 0:
        raise ValueError("duration and poll interval must be positive")
    report = run_benchmark(
        base_url=args.base_url,
        duration_seconds=args.duration_seconds,
        poll_interval_seconds=args.poll_interval_seconds,
        stimulus_description=args.stimulus_description,
        independent_stimulus_confirmed=args.confirm_independent_monophonic_stimulus,
        output=args.output.resolve(),
    )
    print(json.dumps(report["qualification"], ensure_ascii=False, indent=2))
    return 0 if report["qualification"].get("passed") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
