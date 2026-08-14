"""Numerical reference for the fixed NPU DFT spectrum model.

The dashboard never calls this module.  It exists only to verify that the
ONNX graph generated for Ascend uses the intended Hann-windowed, one-sided
periodogram convention.
"""

from __future__ import annotations

import numpy as np


def spectrum_bin_indices(
    *, sample_rate_hz: float, samples: int, max_frequency_hz: float
) -> np.ndarray:
    """Return DFT bins from DC through the requested NPU spectrum span."""
    if sample_rate_hz <= 0 or samples <= 1:
        raise ValueError("sample_rate_hz must be positive and samples must exceed one")
    nyquist_hz = sample_rate_hz / 2.0
    if not 0.0 < max_frequency_hz <= nyquist_hz:
        raise ValueError("max_frequency_hz must be in (0, Nyquist]")
    resolution_hz = sample_rate_hz / samples
    return np.arange(int(np.floor(max_frequency_hz / resolution_hz)) + 1, dtype=np.int64)


def spectrum_axis_hz(
    *, sample_rate_hz: float, samples: int, max_frequency_hz: float
) -> np.ndarray:
    """Return the exact DFT centre frequency for every exported bin."""
    bins = spectrum_bin_indices(
        sample_rate_hz=sample_rate_hz,
        samples=samples,
        max_frequency_hz=max_frequency_hz,
    )
    return (bins * (sample_rate_hz / samples)).astype(np.float32)


def dft_projection_weights(
    *, sample_rate_hz: float, samples: int, max_frequency_hz: float
) -> tuple[np.ndarray, np.ndarray]:
    """Build interleaved cosine/sine matrix weights for the NPU MatMul.

    Each adjacent pair is normalized so squaring and averaging that pair in
    the graph yields a one-sided Hann-windowed periodogram power bin.
    """
    bins = spectrum_bin_indices(
        sample_rate_hz=sample_rate_hz,
        samples=samples,
        max_frequency_hz=max_frequency_hz,
    )
    index = np.arange(samples, dtype=np.float64)
    window = np.hanning(samples).astype(np.float64)
    scale = window / np.sqrt(float(samples * samples) * float(np.mean(window * window)))
    angles = 2.0 * np.pi * bins[:, None] * index[None, :] / float(samples)
    one_sided = np.sqrt(2.0) * np.ones(bins.size, dtype=np.float64)
    one_sided[bins == 0] = 1.0
    if samples % 2 == 0:
        one_sided[bins == samples // 2] = 1.0
    projections = np.empty((samples, bins.size * 2), dtype=np.float32)
    projections[:, 0::2] = (one_sided[:, None] * np.cos(angles) * scale).T
    projections[:, 1::2] = (-one_sided[:, None] * np.sin(angles) * scale).T
    return projections, bins


def hann_periodogram_power(
    waveforms: np.ndarray,
    *,
    sample_rate_hz: float,
    max_frequency_hz: float,
) -> np.ndarray:
    """Reference output with shape ``[1, channels, bins, 1]``.

    Inputs must already be de-meaned, exactly like the dashboard's NPU input
    contract.  This function is intentionally for NumPy/ONNX tests only.
    """
    values = np.asarray(waveforms, dtype=np.float32)
    if values.ndim != 3 or values.shape[0] != 1:
        raise ValueError("waveforms must have shape [1, channels, samples]")
    samples = values.shape[2]
    weights, bins = dft_projection_weights(
        sample_rate_hz=sample_rate_hz,
        samples=samples,
        max_frequency_hz=max_frequency_hz,
    )
    projected = np.matmul(values, weights)
    power = np.square(projected).reshape(1, values.shape[1], bins.size, 2).sum(axis=3)
    return power[..., None].astype(np.float32, copy=False)
