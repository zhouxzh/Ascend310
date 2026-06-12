#!/usr/bin/env python3
"""Download chapter 8 ResNet18-TinyImageNet model assets from Hugging Face."""

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
MODEL_FILES = {
    "om": "resnet18_tiny_imagenet.om",
    "onnx": "resnet18_tiny_imagenet.onnx",
    "aipp_om": "resnet18_tiny_imagenet_aipp.om",
    "aipp_cfg": "resnet18_rgb_static_aipp.cfg",
    "fp16": "resnet18_tiny_imagenet_fp16.om",
    "int8": "resnet18_tiny_imagenet_int8.om",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download chapter 8 ResNet18-TinyImageNet model files.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--base", action="store_true", help="Download baseline OM and ONNX. This is the default.")
    group.add_argument("--om", action="store_true", help="Download only the baseline OM file.")
    group.add_argument("--onnx", action="store_true", help="Download only the ONNX file.")
    group.add_argument("--aipp", action="store_true", help="Download AIPP OM and config files if needed.")
    group.add_argument("--fp16", action="store_true", help="Download only the converted FP16 OM file.")
    group.add_argument("--int8", action="store_true", help="Download only the converted INT8 OM file.")
    group.add_argument("--converted", action="store_true", help="Download FP16 and INT8 OM files.")
    group.add_argument(
        "--all",
        action="store_true",
        help="Download baseline/ONNX/AIPP files and any converted files already visible in the repo.",
    )
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"Hugging Face repo id (default: {DEFAULT_REPO}).")
    parser.add_argument(
        "--endpoint",
        default=DEFAULT_ENDPOINT,
        help=f"Hugging Face endpoint or mirror (default: {DEFAULT_ENDPOINT}).",
    )
    parser.add_argument("--timeout", type=float, default=20.0, help="Network timeout in seconds for each request.")
    parser.add_argument("--output-dir", default=str(MODEL_DIR), help="Directory to save model files.")
    parser.add_argument("--force", action="store_true", help="Overwrite files that already exist.")
    parser.add_argument("--list", action="store_true", help="List files in the remote repository and exit.")
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


def fetch_repo_files(repo_id: str, endpoint: str, timeout: float) -> list[str]:
    request = Request(build_api_url(endpoint, repo_id), headers={"User-Agent": "ascend310-chapter8/1.0"})
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return sorted(item["rfilename"] for item in payload.get("siblings", []) if "rfilename" in item)


def fetch_repo_files_with_fallback(repo_id: str, endpoints: list[str], timeout: float) -> tuple[list[str], str | None]:
    errors = []
    for endpoint in endpoints:
        try:
            return fetch_repo_files(repo_id, endpoint, timeout), endpoint
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
    print("failed to query remote file list:", "; ".join(errors), file=sys.stderr)
    return [], None


def selected_files(args: argparse.Namespace) -> tuple[list[str], set[str]]:
    if args.all:
        return (
            [
                MODEL_FILES["om"],
                MODEL_FILES["onnx"],
                MODEL_FILES["aipp_om"],
                MODEL_FILES["aipp_cfg"],
                MODEL_FILES["fp16"],
                MODEL_FILES["int8"],
            ],
            {MODEL_FILES["fp16"], MODEL_FILES["int8"]},
        )
    if args.converted:
        return [MODEL_FILES["fp16"], MODEL_FILES["int8"]], set()
    if args.aipp:
        return [MODEL_FILES["aipp_om"], MODEL_FILES["aipp_cfg"]], set()
    if args.fp16:
        return [MODEL_FILES["fp16"]], set()
    if args.int8:
        return [MODEL_FILES["int8"]], set()
    if args.om:
        return [MODEL_FILES["om"]], set()
    if args.onnx:
        return [MODEL_FILES["onnx"]], set()
    return [MODEL_FILES["om"], MODEL_FILES["onnx"]], set()


def download_file(repo_id: str, endpoint: str, filename: str, output_dir: Path, *, force: bool, timeout: float) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / filename
    if target.exists() and not force:
        print(f"exists: {target}")
        return target

    url = build_download_url(endpoint, repo_id, filename)
    print(f"download: {filename} -> {target}")
    request = Request(url, headers={"User-Agent": "ascend310-chapter8/1.0"})
    tmp_target = target.with_name(target.name + ".tmp")
    with urlopen(request, timeout=timeout) as response, tmp_target.open("wb") as output:
        shutil.copyfileobj(response, output)
    tmp_target.replace(target)
    return target


def download_with_fallback(
    repo_id: str,
    endpoints: list[str],
    filename: str,
    output_dir: Path,
    *,
    force: bool,
    timeout: float,
) -> Path:
    errors = []
    for endpoint in endpoints:
        try:
            return download_file(repo_id, endpoint, filename, output_dir, force=force, timeout=timeout)
        except Exception as exc:
            errors.append(f"{endpoint}: {exc}")
    raise RuntimeError("; ".join(errors))


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir).expanduser().resolve()
    endpoints = candidate_endpoints(args.endpoint)
    repo_files, list_endpoint = fetch_repo_files_with_fallback(args.repo, endpoints, args.timeout)

    if args.list:
        for filename in repo_files:
            print(filename)
        return 0 if list_endpoint else 1

    targets, optional_targets = selected_files(args)
    if repo_files and optional_targets:
        skipped = [filename for filename in targets if filename in optional_targets and filename not in repo_files]
        if skipped:
            print(
                "optional converted file(s) not visible in remote repo, skip: " + ", ".join(skipped),
                file=sys.stderr,
            )
            targets = [filename for filename in targets if filename not in skipped]

    missing = [filename for filename in targets if repo_files and filename not in repo_files]
    if missing:
        print(
            "not shown by remote file list, trying direct download anyway: " + ", ".join(missing),
            file=sys.stderr,
        )

    failed = 0
    for filename in targets:
        try:
            download_with_fallback(args.repo, endpoints, filename, output_dir, force=args.force, timeout=args.timeout)
        except Exception as exc:
            failed += 1
            print(f"failed to download {filename}: {exc}", file=sys.stderr)

    if failed:
        print(
            "Tip: baseline OM/ONNX are enough for this chapter. "
            "FP16 and INT8 OM files can also be generated by the scripts in tools/.",
            file=sys.stderr,
        )
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
