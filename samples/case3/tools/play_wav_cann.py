#!/usr/bin/env python3
"""Play a 48 kHz, mono, signed 16-bit WAV through CANN Audio MPI."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import tempfile
import wave


ROOT_DIR = Path(__file__).resolve().parents[1]
SAMPLE_RATE_HZ = 48_000
# CANN 8.3 accepts 1024 samples per AO frame. The CANN 7 sample used 960.
FRAME_SAMPLES = 1024
SAMPLE_WIDTH_BYTES = 2
FRAME_BYTES = FRAME_SAMPLES * SAMPLE_WIDTH_BYTES
DEFAULT_PLAYER = (
    ROOT_DIR
    / "_upstream"
    / "ascend-cann-samples"
    / "cplusplus"
    / "level1_single_api"
    / "6_media"
    / "1_audio"
    / "audio_gitee"
    / "build"
    / "sample_audio"
)


def read_pcm_frames(wav_path: Path) -> bytes:
    try:
        with wave.open(str(wav_path), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            sample_rate = source.getframerate()
            compression = source.getcomptype()
            if compression != "NONE":
                raise ValueError(f"WAV compression must be PCM, got {compression}")
            if channels != 1 or sample_width != SAMPLE_WIDTH_BYTES or sample_rate != SAMPLE_RATE_HZ:
                raise ValueError(
                    "CANN audio sample requires 48 kHz, 16-bit, mono PCM; "
                    f"got {sample_rate} Hz, {sample_width * 8}-bit, {channels} channel(s)"
                )
            pcm = source.readframes(source.getnframes())
    except wave.Error as exc:
        raise ValueError(f"Cannot parse WAV file {wav_path}: {exc}") from exc

    tail_size = len(pcm) % FRAME_BYTES
    if tail_size:
        pcm += bytes(FRAME_BYTES - tail_size)
    return pcm


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path, help="48 kHz, 16-bit, mono PCM WAV")
    parser.add_argument(
        "--player",
        type=Path,
        default=DEFAULT_PLAYER,
        help="Compiled CANN audio_gitee sample_audio executable",
    )
    parser.add_argument(
        "--keep-pcm",
        type=Path,
        help="Optionally keep the padded raw PCM file instead of using a temporary file",
    )
    parser.add_argument(
        "--audio-device",
        type=int,
        default=2,
        help="CANN AO device number (default: 2, the upstream sample setting)",
    )
    parser.add_argument(
        "--frame-samples",
        type=int,
        choices=(480, 1024),
        default=FRAME_SAMPLES,
        help="Samples per AO frame accepted by the CANN 8.3 header (default: 1024)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.wav.is_file():
        raise FileNotFoundError(f"WAV file not found: {args.wav}")
    if not args.player.is_file():
        raise FileNotFoundError(
            f"CANN player executable not found: {args.player}. "
            "Build audio_gitee/sample_audio first."
        )

    global FRAME_SAMPLES, FRAME_BYTES
    FRAME_SAMPLES = args.frame_samples
    FRAME_BYTES = FRAME_SAMPLES * SAMPLE_WIDTH_BYTES

    pcm = read_pcm_frames(args.wav)
    player_args = [
        str(args.player),
        "play",
        None,
        str(args.audio_device),
        str(FRAME_SAMPLES),
    ]
    print(
        f"[CANN-AUDIO] {args.wav}: {len(pcm)} bytes, "
        f"{len(pcm) // FRAME_BYTES} frames of {FRAME_SAMPLES / SAMPLE_RATE_HZ * 1_000:.3f} ms, "
        f"device {args.audio_device}"
    )

    if args.keep_pcm is not None:
        args.keep_pcm.parent.mkdir(parents=True, exist_ok=True)
        args.keep_pcm.write_bytes(pcm)
        pcm_path = args.keep_pcm
        print(f"[CANN-AUDIO] PCM: {pcm_path}")
        player_args[2] = str(pcm_path)
        return subprocess.run(player_args).returncode

    with tempfile.TemporaryDirectory(prefix="cann_audio_") as directory:
        pcm_path = Path(directory) / "audio_48k_mono_s16.pcm"
        pcm_path.write_bytes(pcm)
        player_args[2] = str(pcm_path)
        return subprocess.run(player_args).returncode


if __name__ == "__main__":
    raise SystemExit(main())
