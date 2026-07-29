"""Streaming learned-IR and FDN reverb for Piano-DDSP."""

from __future__ import annotations

import math

import numpy as np
from scipy import fft as scipy_fft


FDN_DELAYS = (149, 211, 263, 293)


def fdn_impulse_response(
    controls: np.ndarray, sample_rate: int = 16_000, length: int = 24_000
) -> tuple[np.ndarray, float]:
    controls = np.asarray(controls, dtype=np.float32).reshape(-1)
    if controls.shape != (9,):
        raise ValueError("FDN controls must have nine values")
    sigmoid = 1.0 / (1.0 + np.exp(-np.clip(controls, -60.0, 60.0)))
    gains = np.float32(0.35) * sigmoid[:4]
    feedback = np.float32(0.92) * sigmoid[4:8]
    wet_mix = float(np.float32(0.8) * sigmoid[8])
    sample_index = np.arange(length, dtype=np.float32)
    damping = np.exp(-sample_index / np.float32(sample_rate * 2.4)).astype(np.float32)
    impulse = np.zeros(length, dtype=np.float32)
    for index, delay in enumerate(FDN_DELAYS):
        pulse = (np.remainder(sample_index, delay) == 0).astype(np.float32)
        repeat = np.floor(sample_index / delay)
        impulse += gains[index] * pulse * np.power(feedback[index], repeat) * damping
    impulse /= max(float(np.max(np.abs(impulse))), 1e-4)
    return impulse, wet_mix


class PartitionedConvolver:
    def __init__(self, impulse_response: np.ndarray, block_size: int) -> None:
        impulse_response = np.asarray(impulse_response, dtype=np.float32).reshape(-1)
        if not impulse_response.size or block_size <= 0:
            raise ValueError("Impulse response and block size must be non-empty")
        self.block_size = int(block_size)
        self.fft_size = self.block_size * 2
        partitions = math.ceil(impulse_response.size / self.block_size)
        padded = np.zeros(partitions * self.block_size, dtype=np.float32)
        padded[: impulse_response.size] = impulse_response
        partition_time = np.zeros((partitions, self.fft_size), dtype=np.float32)
        partition_time[:, : self.block_size] = padded.reshape(partitions, self.block_size)
        self.responses = scipy_fft.rfft(partition_time, axis=-1)
        self.history = np.zeros_like(self.responses)
        self.overlap = np.zeros(self.block_size, dtype=np.float32)

    def process(self, audio: np.ndarray) -> np.ndarray:
        audio = np.asarray(audio, dtype=np.float32).reshape(-1)
        if audio.size != self.block_size:
            raise ValueError(f"Expected {self.block_size} samples, received {audio.size}")
        block = np.zeros(self.fft_size, dtype=np.float32)
        block[: self.block_size] = audio
        if self.history.shape[0] > 1:
            self.history[1:] = self.history[:-1].copy()
        self.history[0] = scipy_fft.rfft(block)
        spectrum = np.sum(self.responses * self.history, axis=0)
        convolved = scipy_fft.irfft(spectrum, n=self.fft_size).astype(
            np.float32, copy=False
        )
        output = convolved[: self.block_size] + self.overlap
        self.overlap = convolved[self.block_size :].copy()
        return output

    def reset(self) -> None:
        self.history.fill(0.0)
        self.overlap.fill(0.0)


class StreamingReverb:
    def __init__(
        self,
        metadata: dict[str, object],
        condition: np.ndarray,
        block_size: int,
        mix: float = 1.0,
    ) -> None:
        reverb_type = str(
            dict(metadata.get("reverb_ir_postprocess", {})).get("type", "ir")
        )
        if reverb_type == "fdn":
            impulse, native_wet = fdn_impulse_response(condition)
        elif reverb_type in {"ir", "exponential_decay"}:
            impulse = np.asarray(condition, dtype=np.float32).reshape(-1).copy()
            native_wet = float(metadata.get("reverb_wet_gain", 1.0))
        else:
            raise ValueError(f"Unsupported Piano-DDSP reverb: {reverb_type}")
        impulse[0] = 0.0
        self.native_wet = native_wet
        self.mix = min(1.0, max(0.0, float(mix)))
        self.convolver = PartitionedConvolver(impulse, block_size)

    def process(self, dry: np.ndarray) -> np.ndarray:
        dry = np.asarray(dry, dtype=np.float32)
        return dry + np.float32(self.native_wet * self.mix) * self.convolver.process(dry)

    def reset(self) -> None:
        self.convolver.reset()
