"""Compile one reviewed fixed-shape inference ONNX model and retain ATC evidence."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

import onnx

from .cann_metadata import cann_version
from .safe_json import write_new_json


def parse_shape(value: str) -> tuple[int, ...]:
    parts = value.split(",")
    if not parts or any(not item.strip() for item in parts):
        raise argparse.ArgumentTypeError("shape must contain positive dimensions")
    try:
        shape = tuple(int(item.strip()) for item in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("shape must contain positive dimensions") from exc
    if not shape or any(item <= 0 for item in shape):
        raise argparse.ArgumentTypeError("shape must contain positive dimensions")
    return shape


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onnx", type=Path, required=True)
    parser.add_argument("--input-shape", type=parse_shape, required=True)
    parser.add_argument("--output-prefix", type=Path)
    parser.add_argument("--input-name", default="input_tensor")
    parser.add_argument("--soc-version", default="Ascend310B4")
    parser.add_argument(
        "--precision-mode",
        choices=("force_fp16", "allow_mix_precision", "allow_fp32_to_fp16"),
        default="allow_mix_precision",
        help="ATC arithmetic mode; defaults to mixed precision",
    )
    parser.add_argument(
        "--keep-dtype",
        type=Path,
        help="optional ATC keep_dtype operator list, one original ONNX opname per line",
    )
    parser.add_argument(
        "--check-report",
        type=Path,
        help="optional ATC precheck report path retained with conversion evidence",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    onnx_path = args.onnx.resolve()
    if not onnx_path.is_file():
        raise FileNotFoundError(onnx_path)
    onnx.checker.check_model(onnx.load(onnx_path))
    atc = shutil.which("atc")
    if atc is None:
        raise RuntimeError("atc is unavailable; source the CANN environment first")
    output_prefix = (args.output_prefix or onnx_path.with_suffix("")).resolve()
    om_path = output_prefix.with_suffix(".om")
    evidence_path = output_prefix.with_suffix(".atc.json")
    if om_path == onnx_path or evidence_path == onnx_path:
        raise ValueError("--output-prefix must not overwrite the ONNX input")
    if om_path.exists():
        raise FileExistsError(f"refusing to overwrite existing OM artifact: {om_path}")
    if evidence_path.exists():
        raise FileExistsError(f"refusing to overwrite existing ATC evidence: {evidence_path}")
    if not isinstance(args.input_name, str) or not args.input_name.strip() or any(
        character in args.input_name for character in ":,\r\n"
    ):
        raise ValueError("input-name must be a non-empty ATC input identifier")
    if not isinstance(args.soc_version, str) or not args.soc_version.strip():
        raise ValueError("soc-version must be non-empty")
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    command = [
        atc,
        "--model",
        str(onnx_path),
        "--framework",
        "5",
        "--output",
        str(output_prefix),
        "--input_shape",
        f"{args.input_name}:{','.join(str(value) for value in args.input_shape)}",
        "--soc_version",
        args.soc_version,
    ]
    if args.precision_mode is not None:
        command.extend(["--precision_mode", args.precision_mode])
    if args.keep_dtype is not None:
        keep_dtype = args.keep_dtype.resolve()
        if not keep_dtype.is_file():
            raise FileNotFoundError(keep_dtype)
        command.extend(["--keep_dtype", str(keep_dtype)])
    if args.check_report is not None:
        check_report = args.check_report.resolve()
        if check_report.exists():
            raise FileExistsError(f"refusing to overwrite existing ATC check report: {check_report}")
        check_report.parent.mkdir(parents=True, exist_ok=True)
        command.extend(["--check_report", str(check_report)])
    completed = subprocess.run(command, text=True, capture_output=True, check=False)
    record = {
        "schema_version": 1,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "path_base": str(Path.cwd().resolve()),
        "onnx_path": str(onnx_path),
        "onnx_sha256": sha256(onnx_path),
        "onnx_checker": "passed",
        "cann_version": cann_version(),
        "input_name": args.input_name,
        "input_shape": list(args.input_shape),
        "soc_version": args.soc_version,
        "check_report": None if args.check_report is None else str(check_report),
        "atc_command": command,
        "atc_returncode": completed.returncode,
        "stdout_tail": completed.stdout[-8_000:],
        "stderr_tail": completed.stderr[-8_000:],
        "status": "om_ready" if completed.returncode == 0 and om_path.is_file() else "atc_failed",
        "om_path": str(om_path) if om_path.is_file() else None,
        "om_sha256": sha256(om_path) if om_path.is_file() else None,
    }
    write_new_json(evidence_path, record)
    print(json.dumps(record, indent=2, ensure_ascii=True, allow_nan=False))
    if record["status"] != "om_ready":
        raise RuntimeError(f"ATC failed; evidence written to {evidence_path}")
    print(f"wrote {om_path} and {evidence_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
