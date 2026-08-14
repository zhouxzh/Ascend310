import numpy as np
import numpy.testing as npt
import onnx
import onnxruntime as ort

from time_frequency_dashboard.model.export_npu_spectrum import build_npu_spectrum_onnx_model
from time_frequency_dashboard.model.npu_spectrum_numpy_reference import hann_periodogram_power


def test_npu_spectrum_uses_only_standard_ascend_friendly_operators():
    model, metadata = build_npu_spectrum_onnx_model(
        sample_rate_hz=1_000_000.0,
        samples=10_000,
        channels=2,
        max_frequency_hz=20_000.0,
    )

    onnx.checker.check_model(model)
    assert metadata["input_shape"] == [1, 2, 10_000]
    assert metadata["output_shape"] == [1, 2, 201, 1]
    assert metadata["frequency_resolution_hz"] == 100.0
    assert {node.op_type for node in model.graph.node} == {"MatMul", "Mul", "Reshape", "AveragePool"}


def test_npu_spectrum_onnx_runtime_matches_numpy_periodogram_reference():
    sample_rate_hz = 20_000.0
    samples = 256
    max_frequency_hz = 2_000.0
    model, _metadata = build_npu_spectrum_onnx_model(
        sample_rate_hz=sample_rate_hz,
        samples=samples,
        channels=2,
        max_frequency_hz=max_frequency_hz,
    )
    rng = np.random.default_rng(17)
    waveforms = rng.standard_normal((1, 2, samples)).astype(np.float32)
    waveforms -= waveforms.mean(axis=2, keepdims=True, dtype=np.float32)

    session = ort.InferenceSession(model.SerializeToString(), providers=["CPUExecutionProvider"])
    actual = session.run(["spectrum_power"], {"waveforms": waveforms})[0]
    expected = hann_periodogram_power(
        waveforms, sample_rate_hz=sample_rate_hz, max_frequency_hz=max_frequency_hz
    )

    npt.assert_allclose(actual, expected, rtol=2.0e-5, atol=2.0e-5)
