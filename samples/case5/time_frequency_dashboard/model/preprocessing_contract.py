"""Versioned CPU preprocessing contracts for reviewed live SDR models."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


IQ_CU8_PREPROCESSING_ID = "case5.rtl_cu8_iq.v1"
SPECTROGRAM_PREPROCESSING_ID = "case5.gr_spectrumdetect_fftw.v1"


def _strict_shape(input_shape: Sequence[int]) -> tuple[int, ...]:
    """Reject coercible shape values before they become a deployment contract."""
    if isinstance(input_shape, (str, bytes)):
        raise ValueError("preprocessing input shape must be a sequence of positive integers")
    values = tuple(input_shape)
    if not values or any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in values
    ):
        raise ValueError("preprocessing input shape must be a sequence of positive integers")
    return values


def _cu8_decode_contract() -> dict[str, Any]:
    return {
        "interleave": "IQ",
        "offset": 127.5,
        "scale": 127.5,
    }


def iq_preprocessing_contract(input_shape: Sequence[int]) -> dict[str, Any]:
    shape = _strict_shape(input_shape)
    if len(shape) != 3 or shape[1] != 2:
        raise ValueError("IQ preprocessing requires input shape [batch, 2, samples]")
    return {
        "id": IQ_CU8_PREPROCESSING_ID,
        "cu8_decode": _cu8_decode_contract(),
        "complex_dc_removal": "per_window",
        "layout": "B,C,N with C=[I,Q]",
    }


def spectrogram_preprocessing_contract(input_shape: Sequence[int]) -> dict[str, Any]:
    shape = _strict_shape(input_shape)
    if (
        len(shape) != 4
        or shape[1] != 3
        or shape[2] != shape[3]
    ):
        raise ValueError(
            "spectrogram preprocessing requires input shape [batch, 3, nfft, nfft]"
        )
    nfft = shape[2]
    return {
        "id": SPECTROGRAM_PREPROCESSING_ID,
        "cu8_decode": _cu8_decode_contract(),
        "complex_dc_removal": False,
        "frames": {
            "length": nfft,
            "hop": nfft,
            "window": {"name": "blackman", "periodic": True},
        },
        "fft": {"backend": "fftwf", "direction": "forward", "length": nfft},
        "power": "magnitude_squared",
        "normalization": "per_image_peak",
        "frequency_order": ["fftshift", "vertical_flip"],
        "image_mapping": "db_minmax_black_hot",
        "channels": "rgb_repeat",
    }


def preprocessing_contract_for(
    task: str, input_shape: Sequence[int]
) -> dict[str, Any]:
    if task == "iq_classification":
        return iq_preprocessing_contract(input_shape)
    if task == "spectrogram_detection":
        return spectrogram_preprocessing_contract(input_shape)
    raise ValueError(f"unsupported preprocessing task: {task}")


def validate_preprocessing_contract(
    task: str, input_shape: Sequence[int], value: object
) -> dict[str, Any]:
    """Require an exact executable contract, not a descriptive approximation."""
    if not isinstance(value, Mapping):
        raise ValueError("manifest input.preprocessing must be an object")
    expected = preprocessing_contract_for(task, input_shape)
    observed = dict(value)
    if observed != expected:
        raise ValueError(
            f"{task} preprocessing contract must exactly match {expected['id']}"
        )
    return expected
