#!/usr/bin/env python3
"""Drive the Waveshare e-Paper frame from the indexed photo directory.

Examples on the AIpro board::

    python3 epaper_album.py --photo photos/example.jpg --backend orangepi
    python3 epaper_album.py --directory photos --slideshow --interval 3600 \
        --backend orangepi

Use ``--backend dry-run`` on the development workstation.  It writes the
quantized PNG and packed frame to ``data/`` without importing GPIO/SPI code.
"""

from __future__ import annotations

import argparse
import random
import time
from dataclasses import replace
from pathlib import Path

from config import EPAPER_FRAME_PATH, EPAPER_PREVIEW_PATH
from epaper_display import EpaperConfig, EpaperDisplay, EpaperError


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def _photo_files(directory: Path):
    return sorted(
        path for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS
    )


def _parser():
    parser = argparse.ArgumentParser(description="Smart album e-Paper output")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--photo", type=Path, help="one photo to render")
    source.add_argument("--directory", type=Path, help="photo directory for slideshow")
    parser.add_argument("--slideshow", action="store_true", help="keep cycling the directory")
    parser.add_argument("--interval", type=float, default=3600.0, help="seconds between refreshes")
    parser.add_argument("--count", type=int, default=1, help="number of photos; 0 means forever")
    parser.add_argument("--random", action="store_true", help="shuffle the directory order")
    parser.add_argument("--backend", choices=("dry-run", "orangepi"), default=None)
    parser.add_argument("--no-dither", action="store_true", help="disable Floyd-Steinberg dithering")
    parser.add_argument(
        "--orientation-mode",
        choices=("auto", "match_display"),
        default="auto",
        help="normalize EXIF and preserve photo direction, or opt in to matching the panel aspect",
    )
    parser.add_argument(
        "--rotation",
        type=int,
        choices=(0, 90, 180, 270),
        default=0,
        help="physical panel mounting rotation in clockwise degrees",
    )
    parser.add_argument("--preview-path", type=Path, default=Path(EPAPER_PREVIEW_PATH))
    parser.add_argument("--frame-path", type=Path, default=Path(EPAPER_FRAME_PATH))
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    if args.interval < 0:
        raise SystemExit("--interval must be non-negative")
    if args.count < 0:
        raise SystemExit("--count must be non-negative")

    if args.photo:
        photos = [args.photo]
    else:
        if not args.directory.is_dir():
            raise SystemExit(f"photo directory does not exist: {args.directory}")
        photos = _photo_files(args.directory)
        if not photos:
            raise SystemExit(f"no supported images in {args.directory}")
        if args.random:
            random.shuffle(photos)

    config = EpaperConfig.from_environment()
    if args.backend:
        config = replace(config, backend=args.backend)
    display = EpaperDisplay(config)
    limit = args.count if args.slideshow else 1
    if limit == 0:
        limit = None

    shown = 0
    try:
        while limit is None or shown < limit:
            photo = photos[shown % len(photos)]
            result = display.show(
                photo,
                preview_path=args.preview_path,
                frame_path=args.frame_path,
                dither=not args.no_dither,
                orientation_mode=args.orientation_mode,
                rotation=args.rotation,
            )
            print(
                f"[e-Paper] {photo} -> {result.backend}, "
                f"{result.width}x{result.height}, {result.frame_bytes} bytes"
            )
            shown += 1
            if not args.slideshow or (limit is not None and shown >= limit):
                break
            if args.interval:
                time.sleep(args.interval)
    except (EpaperError, KeyboardInterrupt) as exc:
        if isinstance(exc, EpaperError):
            raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
