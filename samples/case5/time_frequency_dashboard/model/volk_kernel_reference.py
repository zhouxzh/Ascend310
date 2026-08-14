"""NumPy references and fixed tensor contracts for VOLK/NPU comparisons."""

from __future__ import annotations

from typing import Final

import numpy as np


DEFAULT_VECTOR_LENGTH: Final = 1024
DEFAULT_BATCH_SIZES: Final = (1, 16, 64)
VOLK_KERNELS: Final = (
    "magnitude_squared",
    "multiply_conjugate",
    "dot_product",
    "conjugate_dot_product",
)


def input_channels(kernel: str) -> int:
    """Return the planar float channel count for a benchmark kernel."""
    if kernel in {"magnitude_squared", "dot_product"}:
        return 2
    if kernel in {"multiply_conjugate", "conjugate_dot_product"}:
        return 4
    raise ValueError(f"unsupported VOLK benchmark kernel: {kernel}")


def output_shape(kernel: str, batch_size: int, vector_length: int) -> tuple[int, ...]:
    if kernel == "magnitude_squared":
        return batch_size, vector_length
    if kernel == "multiply_conjugate":
        return batch_size, 2, vector_length
    if kernel == "dot_product":
        return batch_size, 1
    if kernel == "conjugate_dot_product":
        return batch_size, 2
    raise ValueError(f"unsupported VOLK benchmark kernel: {kernel}")


def validate_contract(kernel: str, batch_size: int, vector_length: int) -> None:
    input_channels(kernel)
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if vector_length <= 0:
        raise ValueError("vector_length must be positive")


def deterministic_input(
    kernel: str,
    *,
    batch_size: int,
    vector_length: int,
    seed: int = 310_005,
) -> np.ndarray:
    """Create repeatable non-degenerate planar float inputs."""
    validate_contract(kernel, batch_size, vector_length)
    kernel_offset = VOLK_KERNELS.index(kernel) * 10_000
    rng = np.random.default_rng(seed + kernel_offset + batch_size)
    values = rng.uniform(
        -0.85,
        0.85,
        size=(batch_size, input_channels(kernel), vector_length),
    ).astype(np.float32)
    # A deterministic low-frequency component prevents reductions from being
    # dominated by cancellation while retaining mixed signs.
    phase = np.arange(vector_length, dtype=np.float32) * np.float32(0.013)
    values[:, 0, :] += np.float32(0.15) * np.sin(phase)[None, :]
    values[:, -1, :] += np.float32(0.11) * np.cos(phase)[None, :]
    return np.ascontiguousarray(values, dtype=np.float32)


def volk_kernel_numpy(kernel: str, values: np.ndarray) -> np.ndarray:
    """Evaluate one VOLK-equivalent operation using float32 NumPy math."""
    source = np.ascontiguousarray(values, dtype=np.float32)
    if source.ndim != 3 or source.shape[1] != input_channels(kernel):
        raise ValueError(
            f"{kernel} input must have shape [batch, {input_channels(kernel)}, vector_length]"
        )

    if kernel == "magnitude_squared":
        return np.add(np.square(source[:, 0]), np.square(source[:, 1]), dtype=np.float32)

    if kernel == "multiply_conjugate":
        ar, ai, br, bi = (source[:, index] for index in range(4))
        real = ar * br + ai * bi
        imaginary = ai * br - ar * bi
        return np.ascontiguousarray(np.stack((real, imaginary), axis=1), dtype=np.float32)

    if kernel == "dot_product":
        result = np.sum(source[:, 0] * source[:, 1], axis=1, keepdims=True, dtype=np.float32)
        return np.ascontiguousarray(result, dtype=np.float32)

    if kernel == "conjugate_dot_product":
        ar, ai, br, bi = (source[:, index] for index in range(4))
        real = np.sum(ar * br + ai * bi, axis=1, dtype=np.float32)
        imaginary = np.sum(ai * br - ar * bi, axis=1, dtype=np.float32)
        return np.ascontiguousarray(np.stack((real, imaginary), axis=1), dtype=np.float32)

    raise ValueError(f"unsupported VOLK benchmark kernel: {kernel}")
