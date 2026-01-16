import torch
import torch_npu
import pytest
import numpy as np

# Helper function to compare tensors
def assert_tensors_close(t1, t2, rtol=1e-3, atol=1e-3):
    assert np.allclose(t1.cpu().detach().numpy(), t2.cpu().detach().numpy(), rtol=rtol, atol=atol)

@pytest.mark.skipif(not torch.npu.is_available(), reason="NPU not available")
class TestFloat32Ops:
    def test_float32_add(self):
        print("\nTesting float32 addition precision...")
        # Test float32 addition
        a = torch.randn(100, 100, dtype=torch.float32)
        b = torch.randn(100, 100, dtype=torch.float32)
        
        res_cpu = a + b
        res_npu = a.npu() + b.npu()
        
        assert_tensors_close(res_cpu, res_npu, rtol=1e-4, atol=1e-4)
        print("Float32 addition precision test passed!")

    def test_float32_matmul(self):
        print("\nTesting float32 matmul precision...")
        # Test float32 matrix multiplication
        a = torch.randn(50, 50, dtype=torch.float32)
        b = torch.randn(50, 50, dtype=torch.float32)
        
        res_cpu = torch.matmul(a, b)
        res_npu = torch.matmul(a.npu(), b.npu())
        
        # Matmul might have slightly larger error due to hardware differences
        assert_tensors_close(res_cpu, res_npu, rtol=1e-3, atol=1e-3)
        print("Float32 matmul precision test passed!")

if __name__ == "__main__":
    pytest.main([__file__])
