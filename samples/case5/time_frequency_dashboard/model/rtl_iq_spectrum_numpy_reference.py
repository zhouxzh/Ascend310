"""Numerical reference for the batched RTL-SDR complex-IQ NPU spectrum model.

This module is only a model-construction and test baseline.  The runnable
RTL-SDR demo always obtains its displayed spectrum from the Ascend OM model.
"""

from __future__ import annotations

import numpy as np


DEFAULT_SAMPLE_RATE_HZ = 2_048_000.0
DEFAULT_WINDOW_SAMPLES = 1_024
DEFAULT_BATCH_SIZE = 16


def validate_iq_contract(*, batch_size: int, window_samples: int, sample_rate_hz: float) -> None:
    """Validate the static accelerator contract used by ONNX and ATC."""
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if window_samples <= 1 or window_samples % 2:
        raise ValueError("window_samples must be an even integer greater than one")
    if sample_rate_hz <= 0:
        raise ValueError("sample_rate_hz must be positive")


def shifted_frequency_bins(*, window_samples: int) -> np.ndarray:
    """Return full-spectrum DFT bins ordered from negative to positive frequency."""
    if window_samples <= 1 or window_samples % 2:
        raise ValueError("window_samples must be an even integer greater than one")
    return np.arange(-window_samples // 2, window_samples // 2, dtype=np.int64)


def shifted_frequency_axis_hz(*, sample_rate_hz: float, window_samples: int) -> np.ndarray:
    """Return the FFT-shifted frequency axis used by the model output."""
    validate_iq_contract(batch_size=1, window_samples=window_samples, sample_rate_hz=sample_rate_hz)
    return (shifted_frequency_bins(window_samples=window_samples) * sample_rate_hz / window_samples).astype(
        np.float32
    )


def complex_dft_projection_weights(*, window_samples: int) -> tuple[np.ndarray, np.ndarray]:
    """Build normalized real/imaginary fixed DFT projection matrices.

    The input is flattened in C order from ``[batch, 2, samples]`` as all I
    samples followed by all Q samples.  For ``x = I + jQ``, the graph computes
    ``sum(x * hann * exp(-j*2*pi*k*n/N))`` for every shifted DFT bin.
    """
    if window_samples <= 1 or window_samples % 2:
        raise ValueError("window_samples must be an even integer greater than one")
    sample_index = np.arange(window_samples, dtype=np.float64)
    bins = shifted_frequency_bins(window_samples=window_samples)
    angle = 2.0 * np.pi * sample_index[:, None] * bins[None, :] / float(window_samples)
    hann = np.hanning(window_samples).astype(np.float64)
    scale = hann / (float(window_samples) * np.sqrt(float(np.mean(hann * hann))))
    cosine = np.cos(angle) * scale[:, None]
    sine = np.sin(angle) * scale[:, None]
    real_weights = np.vstack((cosine, sine)).astype(np.float32)
    imaginary_weights = np.vstack((-sine, cosine)).astype(np.float32)
    return real_weights, imaginary_weights


def iq_windows_from_complex(samples: np.ndarray) -> np.ndarray:
    """Convert complex windows to de-meaned float32 ``[batch, I/Q, samples]``."""
    values = np.asarray(samples, dtype=np.complex64)
    if values.ndim != 2 or values.shape[1] <= 1:
        raise ValueError("complex samples must have shape [batch, samples] with samples > 1")
    values = values - values.mean(axis=1, keepdims=True, dtype=np.complex64)
    return np.stack((values.real, values.imag), axis=1).astype(np.float32, copy=False)


def shifted_hann_periodogram_power(iq_windows: np.ndarray) -> np.ndarray:
    """Return the normalized full complex periodogram for ONNX/OM comparison."""
    values = np.asarray(iq_windows, dtype=np.float32)
    if values.ndim != 3 or values.shape[1] != 2 or values.shape[2] <= 1:
        raise ValueError("iq_windows must have shape [batch, 2, samples]")
    window_samples = values.shape[2]
    if window_samples % 2:
        raise ValueError("iq_windows sample dimension must be even")
    complex_values = values[:, 0, :] + 1j * values[:, 1, :]
    hann = np.hanning(window_samples).astype(np.float32)
    normalization = float(window_samples) * np.sqrt(float(np.mean(hann * hann)))
    spectrum = np.fft.fftshift(np.fft.fft(complex_values * hann[None, :], axis=1), axes=1)
    return (np.abs(spectrum / normalization) ** 2).astype(np.float32)
