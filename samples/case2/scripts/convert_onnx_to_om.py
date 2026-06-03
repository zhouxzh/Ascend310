#!/usr/bin/env python3
"""Convert SSDLite ONNX models to Ascend OM with ATC.

Run this script on an Ascend 310B device after CANN environment variables have
been loaded, for example:

  source /usr/local/Ascend/ascend-toolkit/set_env.sh
  python scripts/convert_onnx_to_om.py --soc-version Ascend310B4
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional


SCRIPT_DIR = Path(__file__).resolve().parent
CASE_DIR = SCRIPT_DIR.parent
MODEL_DIR = CASE_DIR / "models"
DEFAULT_SOC_VERSION = os.environ.get("ASCEND_SOC_VERSION", "Ascend310B4")
DEFAULT_INPUT_NAME = os.environ.get("ATC_INPUT_NAME", "input")
DEFAULT_INPUT_SHAPE = os.environ.get("ATC_INPUT_SHAPE", "auto")


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Convert ONNX models in models/ to Ascend OM files with atc.")
	parser.add_argument(
		"models",
		nargs="*",
		help="Specific ONNX files to convert. If omitted, all *.onnx files in --model-dir are converted.",
	)
	parser.add_argument(
		"--model-dir",
		default=str(MODEL_DIR),
		help=f"Directory containing ONNX models (default: {MODEL_DIR}).",
	)
	parser.add_argument(
		"--output-dir",
		default="",
		help="Directory for generated OM files (default: same as --model-dir).",
	)
	parser.add_argument(
		"--soc-version",
		default=DEFAULT_SOC_VERSION,
		help=f"ATC soc_version, for example Ascend310B4 or Ascend310B1 (default: {DEFAULT_SOC_VERSION}).",
	)
	parser.add_argument("--framework", type=int, default=5, help="ATC framework id for ONNX (default: 5).")
	parser.add_argument("--input-format", default="NCHW", help="ATC input format (default: NCHW).")
	parser.add_argument("--input-name", default=DEFAULT_INPUT_NAME, help=f"Model input name used when --input-shape=auto (default: {DEFAULT_INPUT_NAME}).")
	parser.add_argument(
		"--input-shape",
		default=DEFAULT_INPUT_SHAPE,
		help="ATC input_shape value. Use 'auto' to infer 300x300 or 320x320 from the file name. "
		f"Use --no-input-shape to omit it (default: {DEFAULT_INPUT_SHAPE}).",
	)
	parser.add_argument("--no-input-shape", action="store_true", help="Do not pass --input_shape to atc.")
	parser.add_argument("--atc", default=os.environ.get("ATC_BIN", "atc"), help="Path to atc binary (default: atc).")
	parser.add_argument("--force", action="store_true", help="Overwrite existing .om files.")
	parser.add_argument("--dry-run", action="store_true", help="Print atc commands without running them.")
	parser.add_argument(
		"--extra-arg",
		action="append",
		default=[],
		help="Additional raw ATC argument. Repeat as needed, for example --extra-arg=--precision_mode=allow_fp32_to_fp16.",
	)
	return parser.parse_args()


def resolve_model_files(model_args: list[str], model_dir: Path) -> list[Path]:
	if model_args:
		return [Path(item).expanduser().resolve() for item in model_args]
	return sorted(model_dir.glob("*.onnx"))


def ensure_atc_available(atc: str) -> None:
	if os.sep in atc:
		if Path(atc).exists():
			return
	elif shutil.which(atc):
		return

	raise FileNotFoundError(
		f"Cannot find ATC binary '{atc}'. Run this script on the Ascend device after sourcing "
		"/usr/local/Ascend/ascend-toolkit/set_env.sh, or pass --atc /path/to/atc."
	)


def infer_input_size(onnx_path: Path) -> int:
	name = onnx_path.name.lower()
	if "ssd300" in name or "resnet" in name:
		return 300
	if "ssd320" in name or "mobilenet" in name:
		return 320
	raise ValueError(
		f"Cannot infer input size from model name: {onnx_path.name}. "
		"Pass --input-shape explicitly or use --no-input-shape."
	)


def resolve_input_shape(onnx_path: Path, args: argparse.Namespace) -> Optional[str]:
	if args.no_input_shape or not args.input_shape:
		return None
	if str(args.input_shape).lower() != "auto":
		return args.input_shape

	input_size = infer_input_size(onnx_path)
	return f"{args.input_name}:1,3,{input_size},{input_size}"


def build_atc_command(
	atc: str,
	onnx_path: Path,
	output_base: Path,
	args: argparse.Namespace,
) -> list[str]:
	input_shape = resolve_input_shape(onnx_path, args)
	command = [
		atc,
		f"--framework={args.framework}",
		f"--model={onnx_path}",
		f"--output={output_base}",
		f"--soc_version={args.soc_version}",
		f"--input_format={args.input_format}",
	]
	if input_shape:
		command.append(f"--input_shape={input_shape}")
	command.extend(args.extra_arg)
	return command


def convert_one(onnx_path: Path, output_dir: Path, args: argparse.Namespace) -> bool:
	if not onnx_path.exists():
		print(f"Missing ONNX model: {onnx_path}")
		return False
	if onnx_path.suffix.lower() != ".onnx":
		print(f"Skip non-ONNX file: {onnx_path}")
		return True

	output_base = output_dir / onnx_path.stem
	om_path = output_base.with_suffix(".om")
	if om_path.exists() and not args.force:
		print(f"Skip existing OM: {om_path} (use --force to regenerate)")
		return True

	output_dir.mkdir(parents=True, exist_ok=True)
	try:
		command = build_atc_command(args.atc, onnx_path, output_base, args)
	except ValueError as exc:
		print(exc)
		return False
	print(shlex.join(command))
	if args.dry_run:
		return True

	result = subprocess.run(command, check=False)
	if result.returncode != 0:
		print(f"ATC failed for {onnx_path.name}, returncode={result.returncode}")
		return False

	if not om_path.exists():
		print(f"ATC finished but OM file was not found: {om_path}")
		return False

	print(f"Generated: {om_path}")
	return True


def main() -> int:
	args = parse_args()
	model_dir = Path(args.model_dir).expanduser().resolve()
	output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else model_dir
	models = resolve_model_files(args.models, model_dir)

	if not models:
		print(f"No ONNX files found in {model_dir}")
		return 1

	if not args.dry_run:
		try:
			ensure_atc_available(args.atc)
		except FileNotFoundError as exc:
			print(exc)
			return 1

	success = 0
	failed = 0
	for onnx_path in models:
		if convert_one(onnx_path, output_dir, args):
			success += 1
		else:
			failed += 1

	print(f"Done. success={success}, failed={failed}, total={len(models)}")
	return 0 if failed == 0 else 2


if __name__ == "__main__":
	sys.exit(main())
