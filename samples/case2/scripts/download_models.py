#!/usr/bin/env python3
"""Download SSDLite320 models from Hugging Face repo zhouxzh/SSDLite320.

Usage examples:
  python scripts/download_models.py
  python scripts/download_models.py --onnx
  python scripts/download_models.py --all
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen, urlretrieve


DEFAULT_REPO = "zhouxzh/SSDLite320"
DEFAULT_MODEL = "ssd320_mobilenetv3_large_100.onnx"
DEFAULT_ENDPOINT = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com").rstrip("/")
SCRIPT_DIR = Path(__file__).resolve().parent
CASE_DIR = SCRIPT_DIR.parent
MODEL_DIR = CASE_DIR / "models"


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Download models from Hugging Face repository.")
	group = parser.add_mutually_exclusive_group()
	group.add_argument("--all", action="store_true", help="Download all matched models (.om + .onnx).")
	group.add_argument("--om", action="store_true", help="Download all .om models.")
	group.add_argument("--onnx", action="store_true", help="Download all .onnx models.")

	parser.add_argument("--repo", default=DEFAULT_REPO, help=f"Hugging Face repo id (default: {DEFAULT_REPO}).")
	parser.add_argument(
		"--endpoint",
		default=DEFAULT_ENDPOINT,
		help=f"Mirror endpoint for Hugging Face API and file downloads (default: {DEFAULT_ENDPOINT}).",
	)
	parser.add_argument(
		"--script-dir",
		action="store_true",
		help="Save models into the directory where this script is located.",
	)
	parser.add_argument(
		"--output-dir",
		default=str(MODEL_DIR),
		help=f"Directory to save downloaded files (default: {MODEL_DIR}).",
	)
	return parser.parse_args()


def build_api_url(endpoint: str, repo_id: str) -> str:
	return f"{endpoint.rstrip('/')}/api/models/{repo_id}"


def build_download_url(endpoint: str, repo_id: str, file_path: str) -> str:
	quoted_file_path = quote(file_path, safe="/")
	return f"{endpoint.rstrip('/')}/{repo_id}/resolve/main/{quoted_file_path}?download=true"


def fetch_repo_files(repo_id: str, endpoint: str) -> list[str]:
	api_url = build_api_url(endpoint, repo_id)
	request = Request(api_url, headers={"User-Agent": "ssd-model-downloader/1.0"})

	with urlopen(request) as response:
		payload = json.loads(response.read().decode("utf-8"))

	siblings = payload.get("siblings", [])
	return [item["rfilename"] for item in siblings if "rfilename" in item]


def is_target_model(filename: str) -> bool:
	name = Path(filename).name
	patterns = [
		r"^ssd320_mobilenetv.+\.(onnx|om)$",
		r"^ssd_mobilenetv.+\.(onnx|om)$",
		r"^ssd300_resnet\d+.*\.(onnx|om)$",
		r"^ssd_resnet\d+.+\.(onnx|om)$",
		r"^ssd_resnet\d+\.(onnx|om)$",
	]
	return any(re.match(pattern, name) for pattern in patterns)


def choose_models(files: list[str], mode: str) -> list[str]:
	if mode == "default":
		for file_path in files:
			if Path(file_path).name == DEFAULT_MODEL:
				return [file_path]
		return []

	selected = [f for f in files if is_target_model(f)]
	if mode == "om":
		selected = [f for f in selected if f.endswith(".om")]
	elif mode == "onnx":
		selected = [f for f in selected if f.endswith(".onnx")]

	return sorted(selected)


def build_local_name(file_path: str) -> str:
	name = Path(file_path).name
	if name.startswith("ssd_mobilenet"):
		return name.replace("ssd_mobilenet", "ssd320_mobilenet", 1)
	if name.startswith("ssd_resnet"):
		return name.replace("ssd_resnet", "ssd300_resnet", 1)
	return name


def download_file(repo_id: str, file_path: str, output_dir: Path, endpoint: str) -> Path:
	output_dir.mkdir(parents=True, exist_ok=True)

	local_name = build_local_name(file_path)
	local_path = output_dir / local_name

	url = build_download_url(endpoint, repo_id, file_path)

	print(f"Downloading {file_path} -> {local_path}")
	urlretrieve(url, local_path)
	return local_path


def resolve_mode(args: argparse.Namespace) -> str:
	if args.all:
		return "all"
	if args.om:
		return "om"
	if args.onnx:
		return "onnx"
	return "default"


def main() -> int:
	args = parse_args()
	mode = resolve_mode(args)
	output_dir = SCRIPT_DIR if args.script_dir else Path(args.output_dir).expanduser().resolve()

	try:
		repo_files = fetch_repo_files(args.repo, args.endpoint)
	except Exception as exc:
		print(f"Failed to query repository '{args.repo}' via '{args.endpoint}': {exc}")
		return 1

	targets = choose_models(repo_files, mode)
	if not targets:
		if mode == "default":
			print(
				f"Default model '{DEFAULT_MODEL}' not found in '{args.repo}'. "
				"No file downloaded."
			)
		else:
			print(f"No model files matched mode '{mode}' in '{args.repo}'.")
			if mode == "om":
				print("The current SSDLite320 repository publishes ONNX files. Download ONNX first, then run scripts/convert_onnx_to_om.py on the Ascend device.")
		return 1

	success = 0
	failed = 0

	for file_path in targets:
		try:
			download_file(args.repo, file_path, output_dir, args.endpoint)
			success += 1
		except Exception as exc:
			failed += 1
			print(f"Failed to download {file_path}: {exc}")

	print(f"Done. success={success}, failed={failed}, total={len(targets)}")
	return 0 if failed == 0 else 2


if __name__ == "__main__":
	sys.exit(main())
