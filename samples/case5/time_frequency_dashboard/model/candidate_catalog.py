"""Reviewed metadata for upstream SDR-model candidates; no weights are stored here."""

from __future__ import annotations

from dataclasses import dataclass

from .preprocessing_contract import (
    IQ_CU8_PREPROCESSING_ID,
    SPECTROGRAM_PREPROCESSING_ID,
    preprocessing_contract_for,
)


TORCHSIG_XCIT_CLASSES = (
    "ook", "bpsk", "4pam", "4ask", "qpsk", "8pam", "8ask", "8psk",
    "16qam", "16pam", "16ask", "16psk", "32qam", "32qam_cross", "32pam",
    "32ask", "32psk", "64qam", "64pam", "64ask", "64psk", "128qam_cross",
    "256qam", "512qam_cross", "1024qam", "2fsk", "2gfsk", "2msk", "2gmsk",
    "4fsk", "4gfsk", "4msk", "4gmsk", "8fsk", "8gfsk", "8msk", "8gmsk",
    "16fsk", "16gfsk", "16msk", "16gmsk", "ofdm-64", "ofdm-72", "ofdm-128",
    "ofdm-180", "ofdm-256", "ofdm-300", "ofdm-512", "ofdm-600", "ofdm-900",
    "ofdm-1024", "ofdm-1200", "ofdm-2048", "fm", "am-dsb-sc", "am-dsb",
    "am-lsb", "am-usb", "lfm_data", "lfm_radar", "chirpss",
)

TORCHSIG_YOLO_CLASSES = (
    "bpsk", "qpsk", "8psk", "16qam", "16psk", "32qam", "32qam_cross",
    "32psk", "64qam", "64psk", "128qam_cross", "256qam", "512qam_cross",
    "1024qam", "2fsk", "2gfsk", "2msk", "2gmsk", "4fsk", "4gfsk", "4msk",
    "4gmsk", "8fsk", "8gfsk", "8msk", "8gmsk", "16fsk", "16gfsk", "16msk",
    "16gmsk", "ofdm-64", "ofdm-72", "ofdm-128", "ofdm-180", "ofdm-256",
    "ofdm-300", "ofdm-512", "ofdm-600", "ofdm-900", "ofdm-1024", "ofdm-1200",
    "ofdm-2048", "fm", "am-dsb-sc", "am-dsb", "am-lsb", "am-usb", "lfm_data",
    "lfm_radar", "chirpss", "tone",
)

SIGNALIQ_CLASSES = (
    "AM-DSB", "AM-SSB", "WBFM", "QPSK", "QAM16", "QAM64", "8PSK", "BPSK",
    "CPFSK", "GFSK", "PAM4",
)

IQ_SAMPLING_CONVENTION = (
    "complex baseband IQ; RTL-SDR CU8 uses interleaved I,Q decoded with "
    "(value-127.5)/127.5; each [2,1024] window removes its complex DC mean "
    "before I/Q channel split and declared normalization"
)

SPECTROGRAM_SAMPLING_CONVENTION = (
    "complex baseband IQ; RTL-SDR CU8 is decoded with (value-127.5)/127.5; "
    "each 1024x1024 image applies a periodic Blackman window to each "
    "1024-sample FFT row, uses FFTW, power, peak-relative "
    "normalization, fftshift plus vertical flip, dB min-max black-hot mapping, "
    "then replicates RGB"
)


@dataclass(frozen=True)
class CandidateSpec:
    model_id_prefix: str
    task: str
    source_url: str
    source_revision: str
    license: str
    input_name: str
    normalization: str
    class_names: tuple[str, ...]
    output_names: tuple[str, ...]
    sample_rate_hz: float | None
    sampling_convention: str
    preprocessing_id: str
    source_contract_verified: bool
    blockers: tuple[str, ...]
    upstream_weight_sha256: str


CANDIDATES = {
    "torchsig_xcit": CandidateSpec(
        model_id_prefix="torchsig-xcit-v1.1.0",
        task="iq_classification",
        source_url="https://github.com/TorchDSP/torchsig",
        source_revision="58bf300c912ac6094a17e1720c48be9a8897ceee",
        license="MIT (TorchSig v1.1.0 pyproject and README)",
        input_name="input_tensor",
        normalization="infinity_norm",
        class_names=TORCHSIG_XCIT_CLASSES,
        output_names=("logits",),
        sample_rate_hz=None,
        sampling_convention=IQ_SAMPLING_CONVENTION,
        preprocessing_id=IQ_CU8_PREPROCESSING_ID,
        source_contract_verified=True,
        blockers=(),
        upstream_weight_sha256="c92ee780c080c1a22dabfa0b15049991dee94e6fe840bc0c8376a6485c720e0c",
    ),
    "signaliq_cldnn": CandidateSpec(
        model_id_prefix="signaliq-cldnn-v1.0.0-reconstructed",
        task="iq_classification",
        source_url="https://huggingface.co/alirezaaminzadeh/radio-modulation-classifier",
        source_revision="839f80a8de05d5f3506aa326a8c870bf77be180e",
        license="MIT (model card claim; source-contract review incomplete)",
        input_name="input_tensor",
        normalization="per_channel_zscore",
        class_names=SIGNALIQ_CLASSES,
        output_names=("logits",),
        sample_rate_hz=None,
        sampling_convention=IQ_SAMPLING_CONVENTION,
        preprocessing_id=IQ_CU8_PREPROCESSING_ID,
        source_contract_verified=False,
        blockers=(
            "upstream repository publishes a state_dict but no matching model forward source",
            "reconstructed CLDNN is limited to ONNX/ATC operator feasibility and cannot establish upstream accuracy",
        ),
        upstream_weight_sha256="0318db9df50a0b971449ac0146de2dc35691c858c35ec2e33dfa9d1a5be6b055",
    ),
    "torchsig_yolo11": CandidateSpec(
        model_id_prefix="torchsig-yolo11s-v1.1.0",
        task="spectrogram_detection",
        source_url="https://github.com/TorchDSP/gr-spectrumdetect",
        source_revision="868cb381e1fdd7d13ad70ecaf271e5060c43308d",
        license="gr-spectrumdetect MIT; exported ONNX metadata reports Ultralytics AGPL-3.0, review before redistribution",
        input_name="images",
        normalization="none",
        class_names=TORCHSIG_YOLO_CLASSES,
        output_names=("output0",),
        sample_rate_hz=2_048_000.0,
        sampling_convention=SPECTROGRAM_SAMPLING_CONVENTION,
        preprocessing_id=SPECTROGRAM_PREPROCESSING_ID,
        source_contract_verified=True,
        blockers=(),
        upstream_weight_sha256="09b774d8f90aad1ad1947df2d26ebac191ef0d400d1ac38c58bf23a91bf26df2",
    ),
}


def candidate_preprocessing_contract(spec: CandidateSpec, input_shape: tuple[int, ...]) -> dict:
    """Build and cross-check the executable preprocessing contract for a catalog entry."""
    contract = preprocessing_contract_for(spec.task, input_shape)
    if contract["id"] != spec.preprocessing_id:
        raise ValueError(
            f"candidate {spec.model_id_prefix} preprocessing id does not match its task contract"
        )
    return contract
