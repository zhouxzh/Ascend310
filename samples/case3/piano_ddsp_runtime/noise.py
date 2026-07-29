"""Deterministic per-voice filtered-noise synthesis with overlap state."""

from __future__ import annotations

import math

import numpy as np
from scipy import fft as scipy_fft

from .harmonic import scale_function


def next_power_of_two(value: int) -> int:
    return 1 << max(0, math.ceil(math.log2(value)))


def frequency_impulse_response(magnitudes: np.ndarray) -> np.ndarray:
    magnitudes = np.asarray(magnitudes, dtype=np.float32)
    ir = scipy_fft.irfft(magnitudes, axis=-1).astype(np.float32, copy=False)
    size = ir.shape[-1]
    periodic_hann = np.hanning(size + 1)[:-1].astype(np.float32)
    window = np.roll(periodic_hann, (size + 1) // 2)
    ir *= window
    return np.roll(ir, (size + 1) // 2, axis=-1)


class NoiseSynthesizer:
    def __init__(
        self,
        voices: int,
        bands: int,
        samples_per_frame: int = 64,
        seed: int = 0,
    ) -> None:
        self.voices = voices
        self.bands = bands
        self.samples_per_frame = samples_per_frame
        self.seed = seed
        self.ir_size = 2 * (bands - 1)
        self.fft_size = next_power_of_two(samples_per_frame + self.ir_size - 1)
        self.rng = [np.random.RandomState(seed + voice) for voice in range(voices)]
        self.tail = np.zeros(
            (voices, self.fft_size - self.samples_per_frame), dtype=np.float32
        )

    def reset(self) -> None:
        self.rng = [np.random.RandomState(self.seed + voice) for voice in range(self.voices)]
        self.tail.fill(0.0)

    def render(
        self,
        magnitudes: np.ndarray,
        envelopes: np.ndarray | None = None,
        white_noise: np.ndarray | None = None,
    ) -> np.ndarray:
        frames = int(magnitudes.shape[0])
        samples = frames * self.samples_per_frame
        if white_noise is None:
            white = np.stack(
                [
                    generator.rand(frames, self.samples_per_frame).astype(np.float32)
                    for generator in self.rng
                ],
                axis=1,
            )
            white = white * 2.0 - 1.0
        else:
            white = np.asarray(white_noise, dtype=np.float32)
            expected = (frames, self.voices, self.samples_per_frame)
            if white.shape != expected:
                raise ValueError(f"white_noise must have shape {expected}")

        impulse = frequency_impulse_response(scale_function(magnitudes))
        filtered = scipy_fft.irfft(
            scipy_fft.rfft(white, self.fft_size, axis=-1)
            * scipy_fft.rfft(impulse, self.fft_size, axis=-1),
            n=self.fft_size,
            axis=-1,
        ).astype(np.float32, copy=False)
        overlap = np.zeros(
            (self.voices, samples + self.tail.shape[1]), dtype=np.float32
        )
        for frame in range(frames):
            start = frame * self.samples_per_frame
            overlap[:, start : start + self.fft_size] += filtered[frame]
        overlap[:, : self.tail.shape[1]] += self.tail
        self.tail[:] = overlap[:, samples:]
        voice_audio = overlap[:, :samples]
        if envelopes is not None:
            voice_audio *= envelopes
        return np.sum(voice_audio, axis=0, dtype=np.float32)
