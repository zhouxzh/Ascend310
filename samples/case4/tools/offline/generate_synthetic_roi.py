#!/usr/bin/env python3
"""Write a deterministic 128x128 geometric test ROI, never a palm image."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


ROI_SIZE = 128


def synthetic_roi() -> np.ndarray:
    """Return a fixed geometric pattern for UI and inference smoke tests."""
    y, x = np.indices((ROI_SIZE, ROI_SIZE), dtype=np.float32)
    checker = ((x // 16 + y // 16) % 2) * 38.0
    diagonal = ((x + 2.0 * y) % 29.0) * 2.1
    radial = 30.0 * np.cos(np.hypot(x - 63.5, y - 63.5) / 7.0)
    image = np.clip(45.0 + checker + diagonal + radial, 0.0, 255.0)
    return image.astype(np.uint8)


def write_pgm(path: Path, image: np.ndarray) -> None:
    path.write_bytes(f"P5\n{ROI_SIZE} {ROI_SIZE}\n255\n".encode("ascii") + image.tobytes())


def write_png(path: Path, image: np.ndarray) -> None:
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("opencv-python-headless is required to write PNG output") from exc
    if not cv2.imwrite(str(path), image):
        raise RuntimeError(f"Could not write PNG output: {path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, help="explicit PGM or PNG destination")
    parser.add_argument("--format", choices=("auto", "pgm", "png"), default="auto")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output_format = output.suffix.lower().lstrip(".") if args.format == "auto" else args.format
    if output_format not in {"pgm", "png"}:
        raise ValueError("--output must end in .pgm or .png when --format=auto")
    image = synthetic_roi()
    if output_format == "pgm":
        write_pgm(output, image)
    else:
        write_png(output, image)
    print(
        json.dumps(
            {
                "path": str(output),
                "format": output_format,
                "shape": list(image.shape),
                "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
                "synthetic": True,
                "contains_biometric_image": False,
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
