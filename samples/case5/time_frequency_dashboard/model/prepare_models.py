"""Export the fixed Case 5 NPU DFT spectrum model for Ascend 310B.

Run this script only on the Ascend board with CANN sourced and the existing
``base`` Conda environment activated.  It never installs dependencies.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

from ..config import Case5Config


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path("models/generated"))
    parser.add_argument("--soc-version", default="Ascend310B4")
    parser.add_argument("--skip-atc", action="store_true")
    return parser.parse_args()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    args = parse_args()
    config = Case5Config()
    config.validate()
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = output_dir / "npu_dft_1ms_10000_20khz.onnx"
    export_command = [
        sys.executable,
        "-m",
        "time_frequency_dashboard.model.export_npu_spectrum",
        "--output",
        str(onnx_path),
        "--sample-rate",
        str(int(config.sample_rate_hz)),
        "--samples",
        str(config.analysis_samples),
        "--channels",
        "2",
        "--max-frequency-hz",
        str(config.spectrum_max_frequency_hz),
    ]
    subprocess.run(export_command, check=True)
    if args.skip_atc:
        return 0
    atc = shutil.which("atc")
    if atc is None:
        raise RuntimeError("atc is unavailable; source /usr/local/Ascend/ascend-toolkit/set_env.sh")
    output_prefix = output_dir / "npu_dft_1ms_10000_20khz"
    atc_command = [
        atc,
        "--model",
        str(onnx_path),
        "--framework",
        "5",
        "--output",
        str(output_prefix),
        "--input_shape",
        f"waveforms:1,2,{config.analysis_samples}",
        "--soc_version",
        args.soc_version,
    ]
    subprocess.run(atc_command, check=True)
    om_path = output_prefix.with_suffix(".om")
    if not om_path.is_file():
        raise RuntimeError(f"ATC completed without expected OM output: {om_path}")
    metadata = {
        "model": "fixed_npu_dft_periodogram",
        "onnx_path": str(onnx_path),
        "om_path": str(om_path),
        "onnx_sha256": sha256(onnx_path),
        "om_sha256": sha256(om_path),
        "input_shape": [1, 2, config.analysis_samples],
        "output_shape": [1, 2, config.spectrum_bins, 1],
        "sample_rate_hz": config.sample_rate_hz,
        "frequency_scale": "linear_hz",
        "frequency_resolution_hz": config.spectrum_resolution_hz,
        "max_frequency_hz": config.spectrum_max_frequency_hz,
        "frequency_bins": config.spectrum_bins,
        "window": "hann",
        "display_transform": "10*log10(max(spectrum_power, 1e-12) / 1 V^2)",
        "atc_command": atc_command,
        "soc_version": args.soc_version,
    }
    om_path.with_suffix(om_path.suffix + ".json").write_text(
        json.dumps(metadata, indent=2), encoding="utf-8"
    )
    print(f"wrote {om_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
