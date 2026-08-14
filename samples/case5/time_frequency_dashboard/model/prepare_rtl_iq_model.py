"""Export and compile the batched RTL-SDR IQ NPU spectrum model on Ascend 310B.

Run only on the board after sourcing CANN and activating the existing ``base``
environment.  This script does not install system or Python packages.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from .rtl_iq_spectrum_numpy_reference import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_SAMPLE_RATE_HZ,
    DEFAULT_WINDOW_SAMPLES,
    validate_iq_contract,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("models/generated"))
    parser.add_argument("--sample-rate", type=float, default=DEFAULT_SAMPLE_RATE_HZ)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--window-samples", type=int, default=DEFAULT_WINDOW_SAMPLES)
    parser.add_argument("--soc-version", default="Ascend310B4")
    parser.add_argument("--skip-atc", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    validate_iq_contract(
        batch_size=args.batch_size,
        window_samples=args.window_samples,
        sample_rate_hz=args.sample_rate,
    )
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    model_stem = f"rtl_iq_dft_{int(args.sample_rate / 1_000)}ksps_b{args.batch_size}_n{args.window_samples}"
    onnx_path = output_dir / f"{model_stem}.onnx"
    export_command = [
        sys.executable,
        "-m",
        "time_frequency_dashboard.model.export_rtl_iq_spectrum",
        "--output",
        str(onnx_path),
        "--sample-rate",
        str(args.sample_rate),
        "--batch-size",
        str(args.batch_size),
        "--window-samples",
        str(args.window_samples),
    ]
    subprocess.run(export_command, check=True)
    if args.skip_atc:
        return 0
    atc = shutil.which("atc")
    if atc is None:
        raise RuntimeError("atc is unavailable; source /usr/local/Ascend/ascend-toolkit/set_env.sh")
    output_prefix = output_dir / model_stem
    atc_command = [
        atc,
        "--model",
        str(onnx_path),
        "--framework",
        "5",
        "--output",
        str(output_prefix),
        "--input_shape",
        f"iq_samples:{args.batch_size},2,{args.window_samples}",
        "--soc_version",
        args.soc_version,
    ]
    subprocess.run(atc_command, check=True)
    om_path = output_prefix.with_suffix(".om")
    if not om_path.is_file():
        raise RuntimeError(f"ATC completed without expected OM output: {om_path}")
    metadata = {
        "model": "fixed_batched_rtl_iq_dft_periodogram",
        "onnx_path": str(onnx_path),
        "om_path": str(om_path),
        "onnx_sha256": sha256(onnx_path),
        "om_sha256": sha256(om_path),
        "input_shape": [args.batch_size, 2, args.window_samples],
        "output_shape": [args.batch_size, args.window_samples],
        "sample_rate_hz": args.sample_rate,
        "frequency_resolution_hz": args.sample_rate / args.window_samples,
        "frequency_order": "fftshift_negative_to_positive",
        "window": "hann",
        "inference_backend": "aclruntime.InferenceSession on Ascend 310B",
        "atc_command": atc_command,
        "soc_version": args.soc_version,
    }
    om_path.with_suffix(om_path.suffix + ".json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=True), encoding="utf-8"
    )
    print(f"wrote {om_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
