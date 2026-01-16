import torch
import torch_npu
import pytest
import numpy as np

# Helper function to compare tensors
def assert_tensors_close(t1, t2, rtol=1e-3, atol=1e-3):
    # Convert NPU tensor to numpy for comparison with CPU tensor
    assert np.allclose(t1.cpu().detach().numpy(), t2.cpu().detach().numpy(), rtol=rtol, atol=atol)

@pytest.mark.skipif(not torch.npu.is_available(), reason="NPU not available")
class TestNNLayersFloat32:
    # Add teardown to clean up NPU memory after each test to prevent OOM (Killed)
    def teardown_method(self):
        if torch.npu.is_available():
            torch.npu.empty_cache()

    @pytest.mark.parametrize("in_features, out_features", [
        (120, 84),      # LeNet FC2
        (84, 10),       # LeNet FC3
        (4096, 4096),   # VGG/AlexNet FC1/FC2
        (4096, 1000),   # VGG/AlexNet FC3
        (2048, 1000),   # ResNet50 FC
        (512, 1000),    # ResNet18 FC
    ])
    def test_linear_float32(self, in_features, out_features):
        print(f"\nTesting nn.Linear (float32) [in={in_features}, out={out_features}]...")
        # Reduce batch size to avoid OOM on embedded devices
        input_tensor = torch.randn(4, in_features, dtype=torch.float32)
        layer = torch.nn.Linear(in_features, out_features)
        
        # CPU run (float32)
        out_cpu = layer(input_tensor)
        
        # NPU run (float32)
        input_npu = input_tensor.npu()
        layer_npu = layer.npu()
        out_npu = layer_npu(input_npu)
        
        assert_tensors_close(out_cpu, out_npu, rtol=1e-3, atol=1e-3)
        print("nn.Linear (float32) test passed!")

    @pytest.mark.parametrize("in_channels, out_channels, kernel_size, stride, padding", [
        (3, 6, 5, 1, 0),    # LeNet5 Conv1
        (6, 16, 5, 1, 0),   # LeNet5 Conv2
        (3, 64, 3, 1, 1),   # VGG16 Conv1_1 / ResNet BasicBlock
        (64, 128, 3, 1, 1), # VGG16 Conv2_1
        (3, 96, 11, 4, 2),  # AlexNet Conv1
        (256, 384, 3, 1, 1),# AlexNet Conv3
        (64, 64, 1, 1, 0),  # ResNet Bottleneck 1x1
        (64, 256, 1, 1, 0), # ResNet Bottleneck expansion
        (64, 64, 7, 2, 3),  # ResNet Initial Conv
    ])
    def test_conv2d_float32(self, in_channels, out_channels, kernel_size, stride, padding):
        print(f"\nTesting nn.Conv2d (float32) [in={in_channels}, out={out_channels}, k={kernel_size}, s={stride}, p={padding}]...")
        # Adjust input size to be large enough for the operation but small enough to avoid OOM
        # 224x224 is too large for unit testing on edge devices with large channels
        h, w = 32, 32 
        input_tensor = torch.randn(1, in_channels, h, w, dtype=torch.float32)
        layer = torch.nn.Conv2d(in_channels, out_channels, kernel_size, stride=stride, padding=padding)
        
        # CPU run
        out_cpu = layer(input_tensor)
        
        # NPU run
        input_npu = input_tensor.npu()
        layer_npu = layer.npu()
        out_npu = layer_npu(input_npu)
        
        assert_tensors_close(out_cpu, out_npu)
        print("nn.Conv2d (float32) test passed!")

    @pytest.mark.parametrize("num_features", [64, 128, 256, 512, 1024, 2048])
    def test_batchnorm_float32(self, num_features):
        print(f"\nTesting nn.BatchNorm2d (float32) [C={num_features}]...")
        input_tensor = torch.randn(2, num_features, 14, 14, dtype=torch.float32)
        layer = torch.nn.BatchNorm2d(num_features)
        layer.eval() # Fix running stats for comparison

        # CPU run
        out_cpu = layer(input_tensor)

        # NPU run
        input_npu = input_tensor.npu()
        layer_npu = layer.npu()
        out_npu = layer_npu(input_npu)

        assert_tensors_close(out_cpu, out_npu)
        print("nn.BatchNorm2d (float32) test passed!")

    def test_relu_float32(self):
        print("\nTesting nn.ReLU (float32)...")
        input_tensor = torch.randn(2, 3, dtype=torch.float32)
        layer = torch.nn.ReLU()

        out_cpu = layer(input_tensor)
        
        input_npu = input_tensor.npu()
        layer_npu = layer.npu()
        out_npu = layer_npu(input_npu)
        
        assert_tensors_close(out_cpu, out_npu)
        print("nn.ReLU (float32) test passed!")

    def test_sigmoid_float32(self):
        print("\nTesting nn.Sigmoid (float32)...")
        input_tensor = torch.randn(2, 3, dtype=torch.float32)
        layer = torch.nn.Sigmoid()

        out_cpu = layer(input_tensor)
        
        input_npu = input_tensor.npu()
        layer_npu = layer.npu()
        out_npu = layer_npu(input_npu)
        
        assert_tensors_close(out_cpu, out_npu)
        print("nn.Sigmoid (float32) test passed!")

    def test_tanh_float32(self):
        print("\nTesting nn.Tanh (float32)...")
        input_tensor = torch.randn(2, 3, dtype=torch.float32)
        layer = torch.nn.Tanh()

        out_cpu = layer(input_tensor)
        
        input_npu = input_tensor.npu()
        layer_npu = layer.npu()
        out_npu = layer_npu(input_npu)
        
        assert_tensors_close(out_cpu, out_npu)
        print("nn.Tanh (float32) test passed!")

    def test_leaky_relu_float32(self):
        print("\nTesting nn.LeakyReLU (float32)...")
        input_tensor = torch.randn(2, 3, dtype=torch.float32)
        layer = torch.nn.LeakyReLU(0.1)

        out_cpu = layer(input_tensor)
        
        input_npu = input_tensor.npu()
        layer_npu = layer.npu()
        out_npu = layer_npu(input_npu)
        
        assert_tensors_close(out_cpu, out_npu)
        print("nn.LeakyReLU (float32) test passed!")

    def test_softmax_float32(self):
        print("\nTesting nn.Softmax (float32)...")
        input_tensor = torch.randn(2, 3, dtype=torch.float32)
        layer = torch.nn.Softmax(dim=1)

        out_cpu = layer(input_tensor)
        
        input_npu = input_tensor.npu()
        layer_npu = layer.npu()
        out_npu = layer_npu(input_npu)
        
        assert_tensors_close(out_cpu, out_npu)
        print("nn.Softmax (float32) test passed!")

    def test_gelu_float32(self):
        print("\nTesting nn.GELU (float32)...")
        input_tensor = torch.randn(2, 3, dtype=torch.float32)
        layer = torch.nn.GELU()

        out_cpu = layer(input_tensor)
        
        input_npu = input_tensor.npu()
        layer_npu = layer.npu()
        out_npu = layer_npu(input_npu)
        
        assert_tensors_close(out_cpu, out_npu)
        print("nn.GELU (float32) test passed!")

    @pytest.mark.parametrize("kernel_size, stride, padding", [
        (2, 2, 0), # LeNet Subsampling
        (3, 1, 1), # Generic AvgPool
        (3, 2, 1), # Downsampling
    ])
    def test_avgpool_float32_default_behavior(self, kernel_size, stride, padding):
        print(f"\nTesting nn.AvgPool2d (float32) [k={kernel_size}, s={stride}, p={padding}]...")
        input_tensor = torch.randn(1, 64, 32, 32, dtype=torch.float32)
        layer = torch.nn.AvgPool2d(kernel_size, stride=stride, padding=padding)

        out_cpu = layer(input_tensor)

        input_npu = input_tensor.npu()
        layer_npu = layer.npu()
        out_npu = layer_npu(input_npu)

        assert_tensors_close(out_cpu, out_npu)
        print("nn.AvgPool2d (float32) default behavior test passed!")

    @pytest.mark.parametrize("kernel_size, stride, padding", [
        (2, 2, 0), # LeNet Subsampling
        (3, 1, 1), # Generic AvgPool
        (3, 2, 1), # Downsampling
    ])
    @pytest.mark.parametrize("count_include_pad", [False, True])
    def test_avgpool_float32(self, kernel_size, stride, padding, count_include_pad):
        print(f"\nTesting nn.AvgPool2d (float32) [k={kernel_size}, s={stride}, p={padding}, count_include_pad={count_include_pad}]...")
        input_tensor = torch.randn(1, 64, 32, 32, dtype=torch.float32)
        layer = torch.nn.AvgPool2d(kernel_size, stride=stride, padding=padding, count_include_pad=count_include_pad)

        out_cpu = layer(input_tensor)

        input_npu = input_tensor.npu()
        layer_npu = layer.npu()
        out_npu = layer_npu(input_npu)

        assert_tensors_close(out_cpu, out_npu)
        print("nn.AvgPool2d (float32) test passed!")

    @pytest.mark.parametrize("p", [0.5, 0.2])
    def test_dropout_float32(self, p):
        print(f"\nTesting nn.Dropout (float32) [p={p}]...")
        input_tensor = torch.randn(2, 100, dtype=torch.float32)
        layer = torch.nn.Dropout(p)
        layer.eval() # Deterministic behavior for comparison

        out_cpu = layer(input_tensor)

        input_npu = input_tensor.npu()
        layer_npu = layer.npu()
        out_npu = layer_npu(input_npu)

        assert_tensors_close(out_cpu, out_npu)
        print("nn.Dropout (float32) test passed!")

    @pytest.mark.parametrize("input_shape", [
        (2, 512, 7, 7),   # VGG/AlexNet before FC
        (2, 2048, 1, 1),  # ResNet after Global Pool
        (2, 16, 5, 5),    # LeNet before FC
    ])
    def test_flatten_float32(self, input_shape):
        print(f"\nTesting nn.Flatten (float32) [input={input_shape}]...")
        input_tensor = torch.randn(*input_shape, dtype=torch.float32)
        layer = torch.nn.Flatten()

        out_cpu = layer(input_tensor)

        input_npu = input_tensor.npu()
        layer_npu = layer.npu()
        out_npu = layer_npu(input_npu)

        assert_tensors_close(out_cpu, out_npu)
        print("nn.Flatten (float32) test passed!")

    @pytest.mark.parametrize("kernel_size, stride, padding", [
        (2, 2, 0), # LeNet/VGG MaxPool
        (3, 2, 0), # AlexNet MaxPool
        (3, 2, 1), # ResNet MaxPool
    ])
    def test_maxpool_float32_default_behavior(self, kernel_size, stride, padding):
        print(f"\nTesting nn.MaxPool2d (float32) [k={kernel_size}, s={stride}, p={padding}]...")
        input_tensor = torch.randn(1, 64, 112, 112, dtype=torch.float32)
        # MaxPool2d does not support count_include_pad, using ceil_mode as variant
        layer = torch.nn.MaxPool2d(kernel_size, stride=stride, padding=padding)

        out_cpu = layer(input_tensor)

        input_npu = input_tensor.npu()
        layer_npu = layer.npu()
        out_npu = layer_npu(input_npu)

        assert_tensors_close(out_cpu, out_npu)
        print("nn.MaxPool2d (float32) default behavior test passed!")

    @pytest.mark.parametrize("kernel_size, stride, padding", [
        (2, 2, 0), # LeNet/VGG MaxPool
        (3, 2, 0), # AlexNet MaxPool
        (3, 2, 1), # ResNet MaxPool
    ])
    @pytest.mark.parametrize("ceil_mode", [False, True])
    def test_maxpool_float32(self, kernel_size, stride, padding, ceil_mode):
        print(f"\nTesting nn.MaxPool2d (float32) [k={kernel_size}, s={stride}, p={padding}, ceil_mode={ceil_mode}]...")
        input_tensor = torch.randn(1, 64, 112, 112, dtype=torch.float32)
        # MaxPool2d does not support count_include_pad, using ceil_mode as variant
        layer = torch.nn.MaxPool2d(kernel_size, stride=stride, padding=padding, ceil_mode=ceil_mode)

        out_cpu = layer(input_tensor)

        input_npu = input_tensor.npu()
        layer_npu = layer.npu()
        out_npu = layer_npu(input_npu)

        assert_tensors_close(out_cpu, out_npu)
        print("nn.MaxPool2d (float32) test passed!")

if __name__ == "__main__":
    pytest.main([__file__])
