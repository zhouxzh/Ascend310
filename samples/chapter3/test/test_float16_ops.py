import torch
import torch_npu
import pytest
import numpy as np

# Helper function to compare tensors
def assert_tensors_close(t1, t2, rtol=1e-2, atol=1e-2):
    # Convert NPU float16 tensor to float32 for comparison with CPU float32 tensor
    assert np.allclose(t1.cpu().detach().float().numpy(), t2.cpu().detach().float().numpy(), rtol=rtol, atol=atol)

@pytest.mark.skipif(not torch.npu.is_available(), reason="NPU not available")
class TestFloat16Ops:
    def test_float16_add(self):
        print("\nTesting float16 addition precision...")
        # Test float16 (half) precision
        a = torch.randn(50, 50, dtype=torch.float32) # Generate in float32
        b = torch.randn(50, 50, dtype=torch.float32)
        
        # CPU calculation in float32 as ground truth
        res_cpu = a + b
        
        # NPU calculation in float16
        a_npu = a.half().npu()
        b_npu = b.half().npu()
        res_npu = a_npu + b_npu
        
        # Tolerance needs to be higher for float16
        assert_tensors_close(res_cpu, res_npu, rtol=1e-2, atol=1e-2)
        print("Float16 addition precision test passed!")

    def test_float16_matmul(self):
        print("\nTesting float16 matmul precision...")
        a = torch.randn(50, 50, dtype=torch.float32)
        b = torch.randn(50, 50, dtype=torch.float32)
        
        res_cpu = torch.matmul(a, b)
        
        a_npu = a.half().npu()
        b_npu = b.half().npu()
        res_npu = torch.matmul(a_npu, b_npu)
        
        assert_tensors_close(res_cpu, res_npu, rtol=1e-2, atol=1e-2)
        print("Float16 matmul precision test passed!")

if __name__ == "__main__":
    pytest.main([__file__])
