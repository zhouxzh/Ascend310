#!/usr/bin/env python3
"""Create deterministic PCM WAV fixtures for board audio-path checks."""

from __future__ import annotations

import argparse
import sys
import wave
from pathlib import Path

import numpy as np

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from midi_ddsp_webui.speaker import build_test_signal


def write_wav(path: Path, signal: np.ndarray, sample_rate: int) -> None:
    pcm = np.clip(signal * 32767.0, -32768, 32767).astype("<i2")
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(signal.shape[1])
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm.tobytes())


def create_fixtures(output_dir: Path, sample_rate: int, duration: float) -> list[Path]:
    mono = build_test_signal(sample_rate, duration, 440.0, -18.0, 1, "both")
    stereo = build_test_signal(sample_rate, duration, 440.0, -18.0, 2, "both")
    paths = [
        output_dir / "speaker-test-mono.wav",
        output_dir / "speaker-test-stereo.wav",
    ]
    write_wav(paths[0], mono, sample_rate)
    write_wav(paths[1], stereo, sample_rate)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT_DIR / "reports" / "audio-fixtures"
    )
    parser.add_argument("--sample-rate", type=int, default=48_000)
    parser.add_argument("--duration", type=float, default=3.0)
    args = parser.parse_args()
    if args.sample_rate <= 0 or args.duration <= 0:
        parser.error("sample rate and duration must be positive")
    for path in create_fixtures(args.output_dir, args.sample_rate, args.duration):
        print(f"[WAV] Wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
