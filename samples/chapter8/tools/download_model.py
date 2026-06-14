#!/usr/bin/env python3
"""Download chapter 8 ResNet18-TinyImageNet ONNX model from Hugging Face."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen


DEFAULT_REPO = "zhouxzh/resnet18_tiny_imagenet"
DEFAULT_ENDPOINT = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com").rstrip("/")
FALLBACK_ENDPOINT = "https://huggingface.co"
SCRIPT_DIR = Path(__file__).resolve().parent
CHAPTER_DIR = SCRIPT_DIR.parent
MODEL_DIR = CHAPTER_DIR / "model"
MODEL_FILE = "resnet18_tiny_imagenet.onnx"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download chapter 8 ResNet18-TinyImageNet ONNX model.")
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"Hugging Face repo id (default: {DEFAULT_REPO}).")
    parser.add_argument(
        "--endpoint",
        default=DEFAULT_ENDPOINT,
        help=f"Hugging Face endpoint or mirror (default: {DEFAULT_ENDPOINT}).",
    )
    parser.add_argument("--timeout", type=float, default=20.0, help="Network timeout in seconds.")
    parser.add_argument("--output-dir", default=str(MODEL_DIR), help="Directory to save the ONNX file.")
    parser.add_argument("--force", action="store_true", help="Overwrite if already exists.")
    return parser.parse_args()


def build_api_url(endpoint: str, repo_id: str) -> str:
    return f"{endpoint.rstrip('/')}/api/models/{repo_id}"


def build_download_url(endpoint: str, repo_id: str, filename: str) -> str:
    return f"{endpoint.rstrip('/')}/{repo_id}/resolve/main/{quote(filename, safe='/')}?download=true"


def candidate_endpoints(endpoint: str) -> list[str]:
    endpoints = [endpoint.rstrip("/")]
    if endpoints[0] != FALLBACK_ENDPOINT:
        endpoints.append(FALLBACK_ENDPOINT)
    return endpoints


def download_file(repo_id: str, endpoint: str, output_dir: Path, *, force: bool, timeout: float) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / MODEL_FILE
    if target.exists() and not force:
        print(f"exists: {target}")
        return target

    url = build_download_url(endpoint, repo_id, MODEL_FILE)
    print(f"download: {MODEL_FILE} -> {target}")
    request = Request(url, headers={"User-Agent": "ascend310-chapter8/1.0"})
    tmp_target = target.with_name(target.name + ".tmp")
    with urlopen(request, timeout=timeout) as response, tmp_target.open("wb") as output:
        shutil.copyfileobj(response, output)
    tmp_target.replace(target)
    return target


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve()
    endpoints = candidate_endpoints(args.endpoint)

    try:
        download_file(args.repo, endpoints[0], output_dir, force=args.force, timeout=args.timeout)
    except Exception as exc:
        print(f"failed from {endpoints[0]}: {exc}", file=sys.stderr)
        if endpoints[1:]:
            print(f"retry from {endpoints[1]} ...", file=sys.stderr)
            try:
                download_file(args.repo, endpoints[1], output_dir, force=args.force, timeout=args.timeout)
            except Exception as exc2:
                print(f"failed from {endpoints[1]}: {exc2}", file=sys.stderr)
                return 2
        else:
            return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
