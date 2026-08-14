import numpy as np
import numpy.testing as npt
import onnx
import onnxruntime as ort
import pytest

from time_frequency_dashboard.model.export_volk_kernels import build_volk_kernel_onnx_model
from time_frequency_dashboard.model.volk_kernel_reference import (
    VOLK_KERNELS,
    deterministic_input,
    volk_kernel_numpy,
)


@pytest.mark.parametrize("kernel", VOLK_KERNELS)
def test_volk_comparison_onnx_matches_numpy(kernel):
    model, metadata = build_volk_kernel_onnx_model(
        kernel,
        batch_size=3,
        vector_length=32,
    )
    onnx.checker.check_model(model)
    values = deterministic_input(kernel, batch_size=3, vector_length=32)
    expected = volk_kernel_numpy(kernel, values)
    session = ort.InferenceSession(model.SerializeToString(), providers=["CPUExecutionProvider"])
    actual = session.run(["output_tensor"], {"input_tensor": values})[0]

    assert metadata["input_shape"] == list(values.shape)
    assert metadata["output_shape"] == list(expected.shape)
    npt.assert_allclose(actual, expected, rtol=2.0e-6, atol=2.0e-6)


def test_volk_graphs_use_only_standard_accelerator_friendly_operators():
    observed = set()
    for kernel in VOLK_KERNELS:
        model, _metadata = build_volk_kernel_onnx_model(
            kernel,
            batch_size=1,
            vector_length=16,
        )
        observed.update(node.op_type for node in model.graph.node)

    assert observed <= {"Gather", "Mul", "Add", "Sub", "ReduceSum", "Unsqueeze", "Concat"}


def test_volk_reference_rejects_wrong_channel_count():
    with pytest.raises(ValueError, match="shape"):
        volk_kernel_numpy("multiply_conjugate", np.zeros((1, 2, 16), dtype=np.float32))
