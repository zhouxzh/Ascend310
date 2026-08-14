"""Numerically compare the batched RTL-SDR IQ ONNX model and board OM model.

Run this only on Ascend 310B after ``prepare_rtl_iq_model`` has generated the
OM.  The comparison is a validation baseline, not a runtime CPU fallback.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import onnxruntime as ort

from ..npu import AscendOmRunner
from .rtl_iq_spectrum_numpy_reference import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_SAMPLE_RATE_HZ,
    DEFAULT_WINDOW_SAMPLES,
    iq_windows_from_complex,
    shifted_frequency_axis_hz,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--onnx",
        type=Path,
        default=Path("models/generated/rtl_iq_dft_2048ksps_b16_n1024.onnx"),
    )
    parser.add_argument(
        "--om",
        type=Path,
        default=Path("models/generated/rtl_iq_dft_2048ksps_b16_n1024.om"),
    )
    parser.add_argument("--sample-rate", type=float, default=DEFAULT_SAMPLE_RATE_HZ)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--window-samples", type=int, default=DEFAULT_WINDOW_SAMPLES)
    parser.add_argument("--rtol", type=float, default=3.0e-3)
    parser.add_argument("--atol", type=float, default=3.0e-3)
    return parser.parse_args()


def deterministic_iq_windows(*, sample_rate_hz: float, batch_size: int, window_samples: int) -> np.ndarray:
    """Create repeatable positive/negative bin-aligned tones for validation."""
    time_axis = np.arange(window_samples, dtype=np.float32) / sample_rate_hz
    bin_hz = sample_rate_hz / window_samples
    selected_bins = np.where(np.arange(batch_size) % 2 == 0, 64, -176)
    phases = np.arange(batch_size, dtype=np.float32) * 0.11
    tones = np.exp(
        1j
        * (
            2.0 * np.pi * selected_bins[:, None].astype(np.float32) * bin_hz * time_axis[None, :]
            + phases[:, None]
        )
    )
    return iq_windows_from_complex(tones)


def main() -> int:
    args = parse_args()
    window = deterministic_iq_windows(
        sample_rate_hz=args.sample_rate,
        batch_size=args.batch_size,
        window_samples=args.window_samples,
    )
    session = ort.InferenceSession(str(args.onnx), providers=["CPUExecutionProvider"])
    onnx_output = session.run(["spectrum_power"], {"iq_samples": window})[0]
    runner = AscendOmRunner(args.om)
    status = runner.initialize()
    if not status.ready:
        raise RuntimeError(status.message)
    om_output = runner.run(window)
    runner.close()
    np.testing.assert_allclose(om_output, onnx_output, rtol=args.rtol, atol=args.atol)
    axis = shifted_frequency_axis_hz(
        sample_rate_hz=args.sample_rate, window_samples=args.window_samples
    )
    peak_hz = axis[np.argmax(om_output, axis=1)]
    difference = float(np.max(np.abs(onnx_output - om_output)))
    print(
        "RTL IQ NPU verification passed; "
        f"shape={tuple(om_output.shape)}; "
        f"peak_offsets_hz={peak_hz.tolist()}; "
        f"max_abs_difference={difference:.6g}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
