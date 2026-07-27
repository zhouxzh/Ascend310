#!/usr/bin/env python3
"""Play an existing WAV through PulseAudio with WebUI progress events."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import wave


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--pulse-sink", required=True)
    parser.add_argument("--latency-ms", type=float, default=40.0)
    parser.add_argument("--output-gain-db", type=float, default=0.0)
    parser.add_argument("--json-events", action="store_true")
    return parser.parse_args()


def wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as source:
        rate = source.getframerate()
        if rate <= 0:
            raise ValueError(f"Invalid WAV sample rate: {rate}")
        return source.getnframes() / rate


def pulse_volume(output_gain_db: float) -> int:
    gain_db = min(0.0, max(-60.0, output_gain_db))
    return round(65_536 * math.pow(10.0, gain_db / 20.0))


def paplay_command(
    path: Path,
    sink: str,
    latency_ms: float,
    output_gain_db: float,
) -> list[str]:
    if not sink:
        raise ValueError("PulseAudio sink is required")
    return [
        "paplay",
        f"--device={sink}",
        f"--latency-msec={max(5, round(latency_ms))}",
        f"--volume={pulse_volume(output_gain_db)}",
        "--client-name=MIDI-DDSP Studio",
        "--stream-name=Existing MIDI-DDSP WAV",
        str(path),
    ]


def emit(enabled: bool, payload: dict[str, object]) -> None:
    if enabled:
        print(f"WEBUI_EVENT {json.dumps(payload, ensure_ascii=False)}", flush=True)


def run(
    path: Path,
    sink: str,
    latency_ms: float,
    output_gain_db: float,
    json_events: bool,
) -> int:
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    if shutil.which("paplay") is None:
        raise RuntimeError("paplay is required for existing WAV playback")
    duration = wav_duration_seconds(path)
    process = subprocess.Popen(
        paplay_command(path, sink, latency_ms, output_gain_db),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    started = time.monotonic()
    paused_since: float | None = None
    paused_total = 0.0

    def set_paused(paused: bool) -> None:
        nonlocal paused_since, paused_total
        if process.poll() is not None:
            return
        now = time.monotonic()
        if paused and paused_since is None:
            process.send_signal(signal.SIGSTOP)
            paused_since = now
        elif not paused and paused_since is not None:
            process.send_signal(signal.SIGCONT)
            paused_total += now - paused_since
            paused_since = None

    if hasattr(signal, "SIGUSR1"):
        signal.signal(signal.SIGUSR1, lambda *_: set_paused(True))
        signal.signal(signal.SIGUSR2, lambda *_: set_paused(False))

    last_event = -1
    try:
        while process.poll() is None:
            now = time.monotonic()
            paused_time = (now - paused_since) if paused_since is not None else 0.0
            elapsed = max(0.0, now - started - paused_total - paused_time)
            event_second = int(elapsed)
            if event_second != last_event:
                progress = min(1.0, elapsed / duration) if duration > 0 else 0.0
                emit(
                    json_events,
                    {
                        "event": "progress",
                        "stage": "playback",
                        "stage_progress": progress,
                        "overall_progress": progress,
                        "completed": min(elapsed, duration),
                        "total": duration,
                        "elapsed_seconds": elapsed,
                        "eta_seconds": max(0.0, duration - elapsed),
                        "heartbeat_at": time.time(),
                        "paused": paused_since is not None,
                        "activity": "playing_existing_wav",
                    },
                )
                last_event = event_second
            time.sleep(0.1)
        return_code = process.wait()
    except KeyboardInterrupt:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=3)
        return 130
    if return_code != 0:
        error = process.stderr.read().strip() if process.stderr is not None else ""
        raise RuntimeError(error or f"paplay exited with code {return_code}")
    emit(
        json_events,
        {
            "event": "progress",
            "stage": "playback",
            "stage_progress": 1.0,
            "overall_progress": 1.0,
            "completed": duration,
            "total": duration,
            "elapsed_seconds": duration,
            "eta_seconds": 0.0,
            "heartbeat_at": time.time(),
            "activity": "playing_existing_wav",
        },
    )
    return 0


def main() -> int:
    args = parse_args()
    return run(
        args.input,
        args.pulse_sink,
        args.latency_ms,
        args.output_gain_db,
        args.json_events,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
