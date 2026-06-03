#!/usr/bin/env python3
"""Download SSDLite320 models from Hugging Face repo zhouxzh/SSDLite320.

Usage examples:
  python scripts/download_models.py
  python scripts/download_models.py --onnx
  python scripts/download_models.py --all
  python scripts/download_models.py ssd320_mobilenetv4_conv_small.onnx
  python scripts/download_models.py mobilenetv4_conv_small
  python scripts/download_models.py --onnx mobilenetv4_conv_small mobilenetv3_small_050
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
DEFAULT_MODEL = "ssd320_mobilenetv4_conv_small.onnx"
DEFAULT_ENDPOINT = os.environ.get("HF_ENDPOINT", "https://hf-mirror.com").rstrip("/")
SCRIPT_DIR = Path(__file__).resolve().parent
CASE_DIR = SCRIPT_DIR.parent
MODEL_DIR = CASE_DIR / "models"
WEIGHTS_DIR = CASE_DIR / "weights"


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Download models from Hugging Face repository.")
	parser.add_argument(
		"selectors",
		nargs="*",
		help=(
			"Specific model files or backbone names to download. Examples: "
			"ssd320_mobilenetv4_conv_small.onnx, mobilenetv4_conv_small."
		),
	)
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
		default="",
		help="Directory to save downloaded files. Defaults to weights/ for ONNX and models/ for OM.",
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


def build_local_name(file_path: str) -> str:
	name = Path(file_path).name
	if name.startswith("ssd_mobilenet"):
		return name.replace("ssd_mobilenet", "ssd320_mobilenet", 1)
	if name.startswith("ssd_resnet"):
		return name.replace("ssd_resnet", "ssd300_resnet", 1)
	return name


def strip_model_prefix(stem: str) -> str:
	for prefix in ("ssd320_", "ssd300_", "ssd_"):
		if stem.startswith(prefix):
			return stem[len(prefix) :]
	return stem


def selector_matches(file_path: str, selector: str) -> bool:
	selector_name = Path(selector).name
	repo_name = Path(file_path).name
	local_name = build_local_name(file_path)

	if Path(selector_name).suffix.lower() in {".onnx", ".om"}:
		return selector_name in {repo_name, local_name, file_path}

	selector_stem = Path(selector_name).stem
	repo_stem = Path(repo_name).stem
	local_stem = Path(local_name).stem
	return selector_stem in {
		repo_stem,
		local_stem,
		strip_model_prefix(repo_stem),
		strip_model_prefix(local_stem),
	}


def filter_models_by_mode(files: list[str], mode: str) -> list[str]:
	if mode == "om":
		return [f for f in files if f.endswith(".om")]
	if mode == "onnx":
		return [f for f in files if f.endswith(".onnx")]
	return files


def choose_selected_models(files: list[str], mode: str, selectors: list[str]) -> tuple[list[str], list[str]]:
	selected = [f for f in files if is_target_model(f)]
	matched: list[str] = []
	missing: list[str] = []

	for selector in selectors:
		selector_matches_list = [f for f in selected if selector_matches(f, selector)]
		selector_matches_list = filter_models_by_mode(selector_matches_list, mode)
		if selector_matches_list:
			matched.extend(selector_matches_list)
		else:
			missing.append(selector)

	unique_matches = sorted(dict.fromkeys(matched))
	return unique_matches, missing


def choose_models(files: list[str], mode: str, selectors: list[str]) -> tuple[list[str], list[str]]:
	if selectors:
		return choose_selected_models(files, mode, selectors)

	if mode == "default":
		for file_path in files:
			if Path(file_path).name == DEFAULT_MODEL:
				return [file_path], []
		return [], []

	selected = [f for f in files if is_target_model(f)]
	selected = filter_models_by_mode(selected, mode)

	return sorted(selected), []


def resolve_output_dir(args: argparse.Namespace, mode: str, file_path: str | None = None) -> Path:
	if args.script_dir:
		return SCRIPT_DIR
	if args.output_dir:
		return Path(args.output_dir).expanduser().resolve()
	if mode == "om" or (file_path and file_path.endswith(".om")):
		return MODEL_DIR.resolve()
	return WEIGHTS_DIR.resolve()


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

	try:
		repo_files = fetch_repo_files(args.repo, args.endpoint)
	except Exception as exc:
		print(f"Failed to query repository '{args.repo}' via '{args.endpoint}': {exc}")
		return 1

	targets, missing_selectors = choose_models(repo_files, mode, args.selectors)
	if not targets:
		if args.selectors:
			print(f"No model files matched selectors: {', '.join(args.selectors)}")
			if missing_selectors:
				print(f"Unmatched selectors: {', '.join(missing_selectors)}")
		elif mode == "default":
			print(
				f"Default model '{DEFAULT_MODEL}' not found in '{args.repo}'. "
				"No file downloaded."
			)
		else:
			print(f"No model files matched mode '{mode}' in '{args.repo}'.")
			if mode == "om":
				print("The current SSDLite320 repository publishes ONNX files. Download ONNX first, then run scripts/convert_onnx_to_om.py on the Ascend device.")
		return 1

	if missing_selectors:
		print(f"Warning: unmatched selectors: {', '.join(missing_selectors)}")

	success = 0
	failed = 0

	for file_path in targets:
		try:
			output_dir = resolve_output_dir(args, mode, file_path)
			download_file(args.repo, file_path, output_dir, args.endpoint)
			success += 1
		except Exception as exc:
			failed += 1
			print(f"Failed to download {file_path}: {exc}")

	print(f"Done. success={success}, failed={failed}, total={len(targets)}")
	return 0 if failed == 0 else 2


if __name__ == "__main__":
	sys.exit(main())
