"""Stateful NumPy inharmonic oscillator bank for Piano-DDSP."""

from __future__ import annotations

import math

import numpy as np


def scale_function(value: np.ndarray) -> np.ndarray:
    value = np.asarray(value, dtype=np.float32)
    sigmoid = 1.0 / (1.0 + np.exp(-np.clip(value, -60.0, 60.0)))
    return (2.0 * np.power(sigmoid, math.log(10.0)) + 1e-7).astype(np.float32)


class HarmonicSynthesizer:
    def __init__(
        self,
        voices: int,
        harmonics: int,
        substrings: int = 1,
        sample_rate: int = 16_000,
        samples_per_frame: int = 64,
    ) -> None:
        self.voices = voices
        self.harmonics = harmonics
        self.substrings = substrings
        self.sample_rate = sample_rate
        self.samples_per_frame = samples_per_frame
        self.ratios = np.arange(1, harmonics + 1, dtype=np.float32)
        self.phase = np.zeros((voices, substrings, harmonics), dtype=np.float32)

    def reset(self) -> None:
        self.phase.fill(0.0)

    def render(
        self,
        amplitudes: np.ndarray,
        distribution: np.ndarray,
        inharmonicity: np.ndarray,
        f0_hz: np.ndarray,
        envelopes: np.ndarray | None = None,
    ) -> np.ndarray:
        frames = int(amplitudes.shape[0])
        samples = frames * self.samples_per_frame
        scaled_amplitudes = scale_function(amplitudes)
        scaled_distribution = scale_function(distribution)
        inharmonic_factor = np.sqrt(
            1.0
            + inharmonicity
            * np.square(self.ratios)[None, None, :]
        ).astype(np.float32)
        reference = f0_hz[:, :, :1] * self.ratios[None, None, :] * inharmonic_factor
        scaled_distribution *= (reference < self.sample_rate / 2).astype(np.float32) + 1e-4
        scaled_distribution /= np.sum(scaled_distribution, axis=-1, keepdims=True)
        scaled_amplitudes *= (f0_hz[:, :, :1] > 20.0).astype(np.float32) + 1e-4
        partial_amplitudes = scaled_amplitudes * scaled_distribution / self.substrings

        voice_audio = np.zeros(
            (frames, self.samples_per_frame, self.voices), dtype=np.float32
        )
        for substring in range(self.substrings):
            frequencies = (
                f0_hz[:, :, substring : substring + 1]
                * self.ratios[None, None, :]
                * inharmonic_factor
            )
            active_amplitudes = partial_amplitudes * (
                (frequencies < self.sample_rate / 2).astype(np.float32) + 1e-4
            )
            increments = frequencies * np.float32(2.0 * math.pi / self.sample_rate)

            # Build every frame's starting phase once, then advance all voices and
            # partials together. This replaces hundreds of thousands of scalar
            # transcendental calls with one rotor per control frame.
            frame_advance = increments.astype(np.float64) * self.samples_per_frame
            cumulative_advance = np.cumsum(frame_advance, axis=0)
            frame_start = np.empty_like(cumulative_advance)
            frame_start[0] = self.phase[:, substring]
            if frames > 1:
                frame_start[1:] = self.phase[:, substring] + cumulative_advance[:-1]
            first_phase = frame_start + increments.astype(np.float64)
            phasor = (
                np.cos(first_phase) + np.complex64(1j) * np.sin(first_phase)
            ).astype(np.complex64)
            rotor = (
                np.cos(increments) + np.complex64(1j) * np.sin(increments)
            ).astype(np.complex64)
            for sample in range(self.samples_per_frame):
                voice_audio[:, sample, :] += np.einsum(
                    "fvh,fvh->fv",
                    active_amplitudes,
                    phasor.imag,
                    dtype=np.float32,
                    optimize=False,
                )
                if sample != self.samples_per_frame - 1:
                    phasor *= rotor

            final_phase = self.phase[:, substring] + cumulative_advance[-1].astype(
                np.float32
            )
            self.phase[:, substring] = np.remainder(
                final_phase, np.float32(2.0 * math.pi)
            )

        by_voice = voice_audio.transpose(2, 0, 1).reshape(self.voices, samples)
        if envelopes is not None:
            by_voice *= envelopes
        return np.sum(by_voice, axis=0, dtype=np.float32)
