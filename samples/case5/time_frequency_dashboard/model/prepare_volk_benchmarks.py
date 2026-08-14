"""Export and compile the fixed VOLK comparison models on Ascend 310B."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from .cann_metadata import cann_version
from .safe_json import write_new_json
from .volk_kernel_reference import DEFAULT_BATCH_SIZES, DEFAULT_VECTOR_LENGTH, VOLK_KERNELS


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_csv_ints(value: str) -> tuple[int, ...]:
    parts = value.split(",")
    if not parts or any(not item.strip() for item in parts):
        raise argparse.ArgumentTypeError("expected comma-separated positive integers")
    try:
        result = tuple(int(item.strip()) for item in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected comma-separated positive integers") from exc
    if not result or any(item <= 0 for item in result):
        raise argparse.ArgumentTypeError("expected comma-separated positive integers")
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("models/generated/volk"))
    parser.add_argument("--kernels", default=",".join(VOLK_KERNELS))
    parser.add_argument(
        "--batch-sizes", type=parse_csv_ints, default=DEFAULT_BATCH_SIZES
    )
    parser.add_argument("--vector-length", type=int, default=DEFAULT_VECTOR_LENGTH)
    parser.add_argument("--soc-version", default="Ascend310B4")
    parser.add_argument(
        "--precision-mode",
        choices=("force_fp16", "allow_mix_precision", "allow_fp32_to_fp16"),
        default="allow_mix_precision",
        help="ATC arithmetic mode; defaults to mixed precision",
    )
    parser.add_argument("--skip-atc", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    kernels = tuple(item.strip() for item in args.kernels.split(",") if item.strip())
    unknown = set(kernels) - set(VOLK_KERNELS)
    if unknown:
        raise ValueError(f"unknown kernels: {sorted(unknown)}")
    if args.vector_length <= 0:
        raise ValueError("--vector-length must be positive")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = args.output_dir / "manifest.json"
    if manifest.exists():
        raise FileExistsError(f"refusing to overwrite existing VOLK manifest: {manifest}")
    atc = None if args.skip_atc else shutil.which("atc")
    if not args.skip_atc and atc is None:
        raise RuntimeError("atc is unavailable; source /usr/local/Ascend/ascend-toolkit/set_env.sh")

    prepared: list[dict[str, object]] = []
    for kernel in kernels:
        for batch_size in args.batch_sizes:
            stem = f"volk_{kernel}_b{batch_size}_n{args.vector_length}"
            onnx_path = args.output_dir / f"{stem}.onnx"
            om_path = args.output_dir / f"{stem}.om"
            evidence_path = om_path.with_suffix(".om.json")
            if onnx_path.exists() or om_path.exists() or evidence_path.exists():
                raise FileExistsError(
                    "refusing to overwrite existing VOLK model/evidence artifact: "
                    f"{onnx_path}, {om_path}, or {evidence_path}"
                )
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "time_frequency_dashboard.model.export_volk_kernels",
                    "--kernel",
                    kernel,
                    "--batch-size",
                    str(batch_size),
                    "--vector-length",
                    str(args.vector_length),
                    "--output",
                    str(onnx_path),
                ],
                check=True,
            )
            record: dict[str, object] = {
                "kernel": kernel,
                "batch_size": batch_size,
                "vector_length": args.vector_length,
                "onnx_path": str(onnx_path),
                "onnx_sha256": sha256(onnx_path),
                "status": "onnx_only" if args.skip_atc else "pending_atc",
            }
            if not args.skip_atc:
                output_prefix = args.output_dir / stem
                atc_command = [
                    str(atc),
                    "--model",
                    str(onnx_path),
                    "--framework",
                    "5",
                    "--output",
                    str(output_prefix),
                    "--input_shape",
                    f"input_tensor:{batch_size},{2 if kernel in {'magnitude_squared', 'dot_product'} else 4},{args.vector_length}",
                    "--soc_version",
                    args.soc_version,
                    "--precision_mode",
                    args.precision_mode,
                ]
                subprocess.run(atc_command, check=True)
                if not om_path.is_file():
                    raise RuntimeError(f"ATC completed without expected OM output: {om_path}")
                record.update(
                    {
                        "status": "om_ready",
                        "om_path": str(om_path),
                        "om_sha256": sha256(om_path),
                        "atc_command": atc_command,
                        "soc_version": args.soc_version,
                        "cann_version": cann_version(),
                    }
                )
                write_new_json(evidence_path, record)
            prepared.append(record)

    write_new_json(manifest, {"schema_version": 1, "models": prepared})
    print(f"wrote {manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
