#!/usr/bin/env python3
"""Play an existing WAV through a selected board output with progress events."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import wave

import numpy as np

from .speaker import configure_alsa_output_route


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    output = parser.add_mutually_exclusive_group(required=True)
    output.add_argument("--pulse-sink")
    output.add_argument("--alsa-device")
    parser.add_argument("--alsa-card", type=int, default=0)
    parser.add_argument("--alsa-route-device-id", type=int, default=2)
    parser.add_argument("--alsa-playback-level", type=int, default=10)
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


def aplay_command(path: Path, device: str) -> list[str]:
    if not device:
        raise ValueError("ALSA playback device is required")
    return ["aplay", "-q", "-D", device, str(path)]


def prepare_alsa_mono_wav(
    source_path: Path,
    target_path: Path,
    output_gain_db: float,
) -> None:
    """Create the 48 kHz mono PCM required by the vendor onboard route."""
    gain = math.pow(10.0, min(0.0, max(-60.0, output_gain_db)) / 20.0)
    with wave.open(str(source_path), "rb") as source:
        channels = source.getnchannels()
        sample_width = source.getsampwidth()
        sample_rate = source.getframerate()
        if channels not in {1, 2}:
            raise ValueError(f"Onboard playback supports mono/stereo WAV input, got {channels} channels")
        if sample_width != 2:
            raise ValueError(f"Onboard playback requires 16-bit PCM WAV input, got {sample_width * 8}-bit")
        if sample_rate != 48_000:
            raise ValueError(f"Onboard playback requires 48 kHz WAV input, got {sample_rate} Hz")
        with wave.open(str(target_path), "wb") as target:
            target.setnchannels(1)
            target.setsampwidth(2)
            target.setframerate(48_000)
            while True:
                frames = source.readframes(4096)
                if not frames:
                    break
                samples = np.frombuffer(frames, dtype="<i2")
                if channels == 2:
                    samples = np.rint(
                        samples.reshape(-1, 2).astype(np.float32).mean(axis=1)
                    )
                else:
                    samples = samples.astype(np.float32)
                samples = np.clip(np.rint(samples * gain), -32768, 32767).astype("<i2")
                target.writeframesraw(samples.tobytes())


def emit(enabled: bool, payload: dict[str, object]) -> None:
    if enabled:
        print(f"WEBUI_EVENT {json.dumps(payload, ensure_ascii=False)}", flush=True)


def run(
    path: Path,
    sink: str | None,
    latency_ms: float,
    output_gain_db: float,
    json_events: bool,
    *,
    alsa_device: str | None = None,
    alsa_card: int = 0,
    alsa_route_device_id: int = 2,
    alsa_playback_level: int = 10,
) -> int:
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(path)
    duration = wav_duration_seconds(path)
    temporary_path: Path | None = None
    if sink:
        if shutil.which("paplay") is None:
            raise RuntimeError("paplay is required for PulseAudio WAV playback")
        command = paplay_command(path, sink, latency_ms, output_gain_db)
    elif alsa_device:
        if shutil.which("aplay") is None:
            raise RuntimeError("aplay is required for onboard WAV playback")
        configure_alsa_output_route(
            alsa_card,
            alsa_route_device_id,
            alsa_playback_level,
        )
        with tempfile.NamedTemporaryFile(
            prefix="midi-ddsp-mono-",
            suffix=".wav",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
        try:
            prepare_alsa_mono_wav(path, temporary_path, output_gain_db)
        except BaseException:
            temporary_path.unlink(missing_ok=True)
            raise
        command = aplay_command(temporary_path, alsa_device)
    else:
        raise ValueError("A PulseAudio sink or ALSA playback device is required")
    process = subprocess.Popen(
        command,
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
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    if return_code != 0:
        error = process.stderr.read().strip() if process.stderr is not None else ""
        raise RuntimeError(error or f"Audio player exited with code {return_code}")
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
        alsa_device=args.alsa_device,
        alsa_card=args.alsa_card,
        alsa_route_device_id=args.alsa_route_device_id,
        alsa_playback_level=args.alsa_playback_level,
    )


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
