from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from chapter8_utils import (
    DEFAULT_CALIB_LIST,
    deterministic_rgb_frame,
    resolve_chapter_path,
    sha256_file,
    write_text_lines,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create deterministic RGB calibration samples for chapter 8.")
    parser.add_argument("--count", type=int, default=50, help="Number of calibration samples to create.")
    parser.add_argument("--height", type=int, default=64, help="Sample image height.")
    parser.add_argument("--width", type=int, default=64, help="Sample image width.")
    parser.add_argument(
        "--output-dir",
        default="calibration/generated_rgb",
        help="Directory for generated .npy RGB samples.",
    )
    parser.add_argument("--list", default=str(DEFAULT_CALIB_LIST), help="Output calibration list path.")
    parser.add_argument(
        "--manifest",
        default="calibration/calibration_manifest.json",
        help="Output manifest JSON path.",
    )
    parser.add_argument("--seed-offset", type=int, default=0, help="Index offset for deterministic samples.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing sample files.")
    return parser.parse_args()


def relative_to_root(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def main() -> int:
    args = parse_args()
    if args.count <= 0:
        raise ValueError("--count must be positive")
    if args.height <= 0 or args.width <= 0:
        raise ValueError("--height and --width must be positive")

    output_dir = resolve_chapter_path(args.output_dir)
    list_path = resolve_chapter_path(args.list)
    manifest_path = resolve_chapter_path(args.manifest)
    list_root = list_path.parent

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    list_lines: list[str] = []
    manifest_items: list[dict[str, object]] = []
    for index in range(args.count):
        sample_index = index + args.seed_offset
        frame = deterministic_rgb_frame(sample_index, args.height, args.width)
        sample_path = output_dir / f"{index:06d}.npy"
        if sample_path.exists() and not args.overwrite:
            frame = np.load(sample_path)
        else:
            np.save(sample_path, frame)

        rel_path = relative_to_root(sample_path, list_root)
        list_lines.append(rel_path)
        manifest_items.append(
            {
                "index": index,
                "source_index": sample_index,
                "path": rel_path,
                "shape": list(frame.shape),
                "dtype": str(frame.dtype),
                "sha256": sha256_file(sample_path),
            }
        )

    write_text_lines(list_path, list_lines)
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "count": args.count,
                "height": args.height,
                "width": args.width,
                "list": str(list_path),
                "items": manifest_items,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
        f.write("\n")

    print(f"calibration samples: {output_dir}")
    print(f"calibration list:    {list_path}")
    print(f"manifest:            {manifest_path}")
    print(f"count:               {args.count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
