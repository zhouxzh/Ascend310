from datetime import datetime
from types import SimpleNamespace

import numpy as np
import numpy.testing as npt
import onnx
import onnxruntime as ort
import pytest

from time_frequency_dashboard.benchmark_rtl_iq_efficiency import (
    cpu_dense_dft_power,
    cpu_fft_periodogram_power,
)
from time_frequency_dashboard.model.export_rtl_iq_spectrum import build_rtl_iq_spectrum_onnx_model
from time_frequency_dashboard.model.rtl_iq_spectrum_numpy_reference import (
    complex_dft_projection_weights,
    iq_windows_from_complex,
    shifted_frequency_axis_hz,
    shifted_hann_periodogram_power,
)
import time_frequency_dashboard.rtl_sdr_npu_demo as rtl_sdr_npu_demo
from time_frequency_dashboard.rtl_sdr_npu_demo import (
    capture_rtl_sdr_cu8,
    decode_rtl_sdr_cu8,
    generate_tone_iq,
    iter_iq_batches,
    run_demo,
)


class _FixedDemoDatetime:
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 1, 2, 3, 4, 5, tzinfo=tz)


def _rtl_demo_args(output_dir):
    return SimpleNamespace(
        source="rtl",
        om=output_dir / "model.om",
        sample_rate=2_048_000.0,
        center_frequency=100_000_000.0,
        batch_size=1,
        window_samples=2,
        batches=1,
        device="0",
        gain_db=None,
        ppm_error=0,
        capture_timeout_seconds=1.0,
        tone_offset_hz=1.0,
        measure_cpu_reference=False,
        output_dir=output_dir,
    )


def test_rtl_iq_model_uses_static_accelerator_friendly_operators():
    model, metadata = build_rtl_iq_spectrum_onnx_model(
        sample_rate_hz=2_048_000.0,
        batch_size=4,
        window_samples=128,
    )

    onnx.checker.check_model(model)
    assert metadata["input_shape"] == [4, 2, 128]
    assert metadata["output_shape"] == [4, 128]
    assert metadata["frequency_order"] == "fftshift_negative_to_positive"
    assert {node.op_type for node in model.graph.node} == {"Reshape", "MatMul", "Mul", "Add"}


def test_rtl_iq_onnx_matches_numpy_complex_periodogram_and_frequency_order():
    sample_rate_hz = 25_600.0
    batch_size = 3
    window_samples = 256
    tone_offset_hz = 3_200.0
    model, _metadata = build_rtl_iq_spectrum_onnx_model(
        sample_rate_hz=sample_rate_hz,
        batch_size=batch_size,
        window_samples=window_samples,
    )
    samples = generate_tone_iq(
        sample_rate_hz=sample_rate_hz,
        total_samples=batch_size * window_samples,
        tone_offset_hz=tone_offset_hz,
    )
    windows = iq_windows_from_complex(samples.reshape(batch_size, window_samples))

    session = ort.InferenceSession(model.SerializeToString(), providers=["CPUExecutionProvider"])
    actual = session.run(["spectrum_power"], {"iq_samples": windows})[0]
    expected = shifted_hann_periodogram_power(windows)

    npt.assert_allclose(actual, expected, rtol=2.0e-5, atol=2.0e-5)
    axis = shifted_frequency_axis_hz(
        sample_rate_hz=sample_rate_hz, window_samples=window_samples
    )
    npt.assert_allclose(axis[np.argmax(actual, axis=1)], tone_offset_hz)


def test_cu8_decode_and_batching_preserve_iq_order():
    raw = bytes((0, 255, 127, 128, 255, 0, 64, 192))
    decoded = decode_rtl_sdr_cu8(raw, complex_samples=4)
    npt.assert_allclose(decoded.real, np.asarray([-1.0, -1.0 / 255.0, 1.0, -63.5 / 127.5]))
    npt.assert_allclose(decoded.imag, np.asarray([1.0, 1.0 / 255.0, -1.0, 64.5 / 127.5]))

    batches = list(iter_iq_batches(decoded, batch_size=1, window_samples=4))
    assert len(batches) == 1
    assert batches[0].shape == (1, 2, 4)
    npt.assert_allclose(batches[0].mean(axis=2), 0.0, atol=1.0e-7)


@pytest.mark.parametrize("complex_samples", [0, -1, True])
def test_cu8_decode_rejects_invalid_requested_sample_counts(complex_samples):
    with pytest.raises(ValueError, match="complex_samples"):
        decode_rtl_sdr_cu8(b"\x00\x00", complex_samples=complex_samples)


def test_tone_generation_and_batching_reject_invalid_numeric_inputs():
    with pytest.raises(ValueError, match="sample_rate_hz"):
        generate_tone_iq(sample_rate_hz=float("nan"), total_samples=4, tone_offset_hz=1.0)
    with pytest.raises(ValueError, match="total_samples"):
        generate_tone_iq(sample_rate_hz=8.0, total_samples=0, tone_offset_hz=1.0)
    with pytest.raises(ValueError, match="complex_samples must be a 1-D"):
        list(iter_iq_batches(np.ones((2, 2), dtype=np.complex64), batch_size=1, window_samples=2))
    with pytest.raises(ValueError, match="finite and non-empty"):
        list(iter_iq_batches(np.asarray([np.nan + 0.0j], dtype=np.complex64), batch_size=1, window_samples=1))


@pytest.mark.parametrize(
    ("sample_rate_hz", "center_frequency_hz", "field"),
    [
        (2_048_000.5, 100_000_000.0, "sample_rate_hz"),
        (2_048_000.0, 100_000_000.5, "center_frequency_hz"),
    ],
)
def test_rtl_capture_rejects_nonintegral_hz_before_starting_recorder(
    tmp_path, sample_rate_hz, center_frequency_hz, field
):
    output_path = tmp_path / "capture.cu8"

    with pytest.raises(ValueError, match=rf"{field} must be a positive whole number"):
        capture_rtl_sdr_cu8(
            output_path=output_path,
            sample_rate_hz=sample_rate_hz,
            center_frequency_hz=center_frequency_hz,
            complex_samples=2,
            device="0",
            gain_db=None,
            ppm_error=0,
            timeout_seconds=1.0,
        )

    assert not output_path.exists()


@pytest.mark.parametrize(
    ("existing_name", "description"),
    [
        ("rtl_iq_npu_20260102T030405Z.jsonl", "RTL-SDR NPU result"),
        ("rtl_iq_20260102T030405Z.cu8", "CU8 capture"),
    ],
)
def test_rtl_demo_preflights_existing_artifacts_before_capture_or_npu(
    tmp_path, monkeypatch, existing_name, description
):
    args = _rtl_demo_args(tmp_path)
    (tmp_path / existing_name).write_bytes(b"already exists")
    monkeypatch.setattr(rtl_sdr_npu_demo, "datetime", _FixedDemoDatetime)
    monkeypatch.setattr(
        rtl_sdr_npu_demo,
        "capture_rtl_sdr_cu8",
        lambda **_kwargs: pytest.fail("capture must not start after artifact preflight failure"),
    )
    monkeypatch.setattr(
        rtl_sdr_npu_demo,
        "AscendOmRunner",
        lambda *_args: pytest.fail("NPU runner must not be constructed after artifact preflight failure"),
    )

    with pytest.raises(FileExistsError, match=rf"existing {description}"):
        run_demo(args)


@pytest.mark.parametrize(
    ("field", "value"),
    [("sample_rate", 2_048_000.5), ("center_frequency", 100_000_000.5)],
)
def test_rtl_demo_rejects_nonintegral_hz_before_capture_or_npu(tmp_path, monkeypatch, field, value):
    output_dir = tmp_path / "new-run"
    args = _rtl_demo_args(output_dir)
    setattr(args, field, value)
    monkeypatch.setattr(
        rtl_sdr_npu_demo,
        "capture_rtl_sdr_cu8",
        lambda **_kwargs: pytest.fail("capture must not start with fractional RTL tuning"),
    )
    monkeypatch.setattr(
        rtl_sdr_npu_demo,
        "AscendOmRunner",
        lambda *_args: pytest.fail("NPU runner must not be constructed with fractional RTL tuning"),
    )

    with pytest.raises(ValueError, match="positive whole number"):
        run_demo(args)

    assert not output_dir.exists()


def test_cpu_fft_and_matching_dense_dft_share_the_npu_power_contract():
    rng = np.random.default_rng(310)
    iq_windows = rng.standard_normal((3, 2, 128), dtype=np.float32)
    iq_windows -= iq_windows.mean(axis=2, keepdims=True, dtype=np.float32)
    hann = np.hanning(128).astype(np.float32)
    normalization = 128.0 * np.sqrt(float(np.mean(hann * hann)))
    real_weights, imaginary_weights = complex_dft_projection_weights(window_samples=128)

    fft_power = cpu_fft_periodogram_power(iq_windows, hann=hann, normalization=normalization)
    dense_dft_power = cpu_dense_dft_power(
        iq_windows,
        real_weights=real_weights,
        imaginary_weights=imaginary_weights,
    )

    npt.assert_allclose(dense_dft_power, fft_power, rtol=3.0e-5, atol=3.0e-5)
