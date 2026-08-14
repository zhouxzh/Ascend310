"""Compare the generated ONNX and OM NPU DFT spectrum on a deterministic window.

Run only on the Ascend board after ``python -m time_frequency_dashboard.model.prepare_models``
has generated the OM.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import time

import numpy as np
import onnxruntime as ort

from ..config import Case5Config
from ..npu import AscendOmRunner


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onnx", type=Path, default=Path("models/generated/npu_dft_1ms_10000_20khz.onnx"))
    parser.add_argument("--om", type=Path, default=Path("models/generated/npu_dft_1ms_10000_20khz.om"))
    parser.add_argument("--rtol", type=float, default=2.0e-3)
    parser.add_argument("--atol", type=float, default=2.0e-3)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    config = Case5Config()
    index = np.arange(config.analysis_samples, dtype=np.float32) / config.sample_rate_hz
    window = np.stack(
        (
            np.sin(2.0 * np.pi * 1_000.0 * index),
            0.2 * np.sin(2.0 * np.pi * 1_000.0 * index - 0.35),
        )
    )[None, :, :].astype(np.float32)
    session = ort.InferenceSession(str(args.onnx), providers=["CPUExecutionProvider"])
    onnx_output = session.run(["spectrum_power"], {"waveforms": window})[0]
    runner = AscendOmRunner(args.om)
    status = runner.initialize()
    if not status.ready:
        raise RuntimeError(status.message)
    om_output = runner.run(window)
    runner.close()
    if om_output.shape != onnx_output.shape:
        raise AssertionError(f"shape mismatch: ONNX {onnx_output.shape}, OM {om_output.shape}")
    difference = float(np.max(np.abs(onnx_output - om_output)))
    reference_peak = float(np.max(np.abs(onnx_output)))
    normalized_difference = difference / max(reference_peak, args.atol)
    np.testing.assert_allclose(om_output, onnx_output, rtol=args.rtol, atol=args.atol)
    print(
        "NPU verification passed; "
        f"shape={tuple(om_output.shape)}; "
        f"onnx_range=[{float(onnx_output.min()):.6g}, {float(onnx_output.max()):.6g}]; "
        f"max_abs_difference={difference:.6g}; "
        f"normalized_max_difference={normalized_difference:.6g}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
