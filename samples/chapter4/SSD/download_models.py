#!/usr/bin/env python3
"""Download SSD300 ONNX/OM models from Hugging Face.

The default repository is zhouxzh/SSD300. Model filenames use the
ssd300_{backbone}.{suffix} convention so the 300x300 input size is explicit.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlretrieve


DEFAULT_REPO = "zhouxzh/SSD300"
DEFAULT_ENDPOINT = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com").rstrip("/")
DEFAULT_BACKBONE = "resnet50"
DEFAULT_BACKBONES = ("resnet18", "resnet34", "resnet50", "resnet101", "resnet152")
SCRIPT_DIR = Path(__file__).resolve().parent
MODEL_DIR = SCRIPT_DIR / "models"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download SSD300 models from Hugging Face.")
    parser.add_argument(
        "--backbone",
        default=DEFAULT_BACKBONE,
        help=(
            "Backbone name, or 'all' for all default backbones "
            f"(default: {DEFAULT_BACKBONE})."
        ),
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--onnx", action="store_true", help="Download ONNX model files. This is the default.")
    group.add_argument("--om", action="store_true", help="Download OM model files for Ascend NPU inference.")
    group.add_argument("--all", action="store_true", help="Download both ONNX and OM model files.")
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"Hugging Face repo id (default: {DEFAULT_REPO}).")
    parser.add_argument(
        "--endpoint",
        default=DEFAULT_ENDPOINT,
        help=f"Hugging Face endpoint or mirror (default: {DEFAULT_ENDPOINT}).",
    )
    parser.add_argument(
        "--output-dir",
        default=str(MODEL_DIR),
        help="Directory to save downloaded models (default: models/).",
    )
    parser.add_argument("--force", action="store_true", help="Overwrite files that already exist.")
    parser.add_argument("--dry-run", action="store_true", help="Print download URLs without downloading files.")
    return parser.parse_args()


def resolve_backbones(selector: str) -> list[str]:
    if selector == "all":
        return list(DEFAULT_BACKBONES)
    return [selector]


def resolve_suffixes(args: argparse.Namespace) -> list[str]:
    if args.all:
        return ["onnx", "om"]
    if args.om:
        return ["om"]
    return ["onnx"]


def model_filename(backbone: str, suffix: str) -> str:
    return f"ssd300_{backbone}.{suffix}"


def build_download_url(endpoint: str, repo_id: str, filename: str) -> str:
    quoted_filename = quote(filename, safe="/")
    return f"{endpoint.rstrip('/')}/{repo_id}/resolve/main/{quoted_filename}?download=true"


def download_file(repo_id: str, endpoint: str, filename: str, output_dir: Path, *, force: bool, dry_run: bool) -> Path:
    output_path = output_dir / filename
    url = build_download_url(endpoint, repo_id, filename)

    if dry_run:
        print(f"{filename}: {url}")
        return output_path

    output_dir.mkdir(parents=True, exist_ok=True)
    if output_path.exists() and not force:
        print(f"Model already exists: {output_path}")
        return output_path

    print(f"Downloading {url}")
    print(f"Saving to {output_path}")
    urlretrieve(url, output_path)
    return output_path


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve()
    backbones = resolve_backbones(args.backbone)
    suffixes = resolve_suffixes(args)

    success = 0
    failed = 0

    for backbone in backbones:
        for suffix in suffixes:
            filename = model_filename(backbone, suffix)
            try:
                download_file(
                    args.repo,
                    args.endpoint,
                    filename,
                    output_dir,
                    force=args.force,
                    dry_run=args.dry_run,
                )
                success += 1
            except Exception as exc:
                failed += 1
                print(f"Failed to download {filename}: {exc}")

    total = success + failed
    print(f"Done. success={success}, failed={failed}, total={total}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
