"""CPU FFTW preprocessing for TorchDSP-style wideband spectrogram models."""

from __future__ import annotations

import ctypes
from ctypes.util import find_library
from dataclasses import dataclass

import numpy as np


FFTW_FORWARD = -1
FFTW_ESTIMATE = 1 << 6


def periodic_blackman(length: int) -> np.ndarray:
    if isinstance(length, bool) or not isinstance(length, int) or length <= 1:
        raise ValueError("Blackman window length must be greater than one")
    return np.blackman(length + 1)[:-1].astype(np.float32)


def spectrogram_image_from_fft(fft_values: np.ndarray) -> np.ndarray:
    """Apply gr-spectrumdetect frequency ordering, dB scale, and black-hot mapping."""
    transformed = np.asarray(fft_values, dtype=np.complex64)
    if transformed.ndim == 2:
        transformed = transformed[None, ...]
    if transformed.ndim != 3 or transformed.shape[1] != transformed.shape[2]:
        raise ValueError("FFT spectrogram must have square [batch, time, frequency] shape")
    if not np.all(np.isfinite(transformed)):
        raise ValueError("FFT spectrogram must contain only finite values")
    power = np.square(np.abs(transformed), dtype=np.float32).transpose(0, 2, 1)
    peak = np.maximum(power.max(axis=(1, 2), keepdims=True), np.float32(1.0e-12))
    power /= peak
    ordered = np.flip(np.fft.fftshift(power, axes=1), axis=1)
    decibels = 10.0 * np.log10(ordered + np.float32(1.0e-12))
    low = decibels.min(axis=(1, 2), keepdims=True)
    high = decibels.max(axis=(1, 2), keepdims=True)
    span = high - low
    normalized = np.divide(
        decibels - low,
        span,
        out=np.zeros_like(decibels, dtype=np.float32),
        where=span > 0,
    )
    black_hot = np.where(span > 0, 1.0 - normalized, 0.0).astype(np.float32)
    channels = np.repeat(black_hot[:, None, :, :], 3, axis=1)
    return np.ascontiguousarray(channels, dtype=np.float32)


def wideband_spectrogram_numpy(samples: np.ndarray, nfft: int) -> np.ndarray:
    """Numerical reference matching the FFTW path; not a deployment fallback."""
    values = np.asarray(samples, dtype=np.complex64)
    if values.ndim != 1:
        raise ValueError("wideband spectrogram samples must be a 1-D array")
    if not np.all(np.isfinite(values)):
        raise ValueError("wideband spectrogram samples must be finite")
    samples_per_image = nfft * nfft
    if values.size == 0 or values.size % samples_per_image:
        raise ValueError(
            f"wideband spectrogram requires a multiple of {samples_per_image} complex samples"
        )
    frames = values.reshape(-1, nfft, nfft) * periodic_blackman(nfft)[None, None, :]
    transformed = np.fft.fft(frames, axis=2).astype(np.complex64)
    return spectrogram_image_from_fft(transformed)


@dataclass
class FftwSpectrogram:
    """Reusable single-precision FFTW plan for an nfft-by-nfft spectrogram."""

    nfft: int

    def __post_init__(self) -> None:
        if isinstance(self.nfft, bool) or not isinstance(self.nfft, int) or self.nfft <= 1:
            raise ValueError("nfft must be greater than one")
        library_name = find_library("fftw3f")
        if not library_name:
            raise RuntimeError(
                "libfftw3f is unavailable; install libfftw3-single3 and libfftw3-dev manually"
            )
        self._library = ctypes.CDLL(library_name)
        complex_pointer = ctypes.c_void_p
        self._library.fftwf_plan_many_dft.restype = ctypes.c_void_p
        self._library.fftwf_plan_many_dft.argtypes = [
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_int,
            complex_pointer,
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_int,
            ctypes.c_int,
            complex_pointer,
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_uint,
        ]
        self._library.fftwf_execute.argtypes = [ctypes.c_void_p]
        self._library.fftwf_destroy_plan.argtypes = [ctypes.c_void_p]
        self._input = np.empty((self.nfft, self.nfft), dtype=np.complex64)
        self._output = np.empty_like(self._input)
        dimension = ctypes.c_int(self.nfft)
        self._plan = self._library.fftwf_plan_many_dft(
            1,
            ctypes.byref(dimension),
            self.nfft,
            self._input.ctypes.data_as(complex_pointer),
            None,
            1,
            self.nfft,
            self._output.ctypes.data_as(complex_pointer),
            None,
            1,
            self.nfft,
            FFTW_FORWARD,
            FFTW_ESTIMATE,
        )
        if not self._plan:
            raise RuntimeError("fftwf_plan_many_dft returned a null plan")
        self._window = periodic_blackman(self.nfft)

    def compute(self, samples: np.ndarray) -> np.ndarray:
        values = np.asarray(samples, dtype=np.complex64)
        if values.ndim != 1:
            raise ValueError("wideband spectrogram samples must be a 1-D array")
        if not np.all(np.isfinite(values)):
            raise ValueError("wideband spectrogram samples must be finite")
        samples_per_image = self.nfft * self.nfft
        if values.size == 0 or values.size % samples_per_image:
            raise ValueError(
                f"wideband spectrogram requires a multiple of {samples_per_image} complex samples"
            )
        images: list[np.ndarray] = []
        for frame in values.reshape(-1, self.nfft, self.nfft):
            np.multiply(frame, self._window[None, :], out=self._input)
            self._library.fftwf_execute(self._plan)
            images.append(spectrogram_image_from_fft(self._output))
        return np.concatenate(images, axis=0)

    def close(self) -> None:
        if getattr(self, "_plan", None):
            self._library.fftwf_destroy_plan(self._plan)
            self._plan = None

    def __enter__(self) -> "FftwSpectrogram":
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.close()
