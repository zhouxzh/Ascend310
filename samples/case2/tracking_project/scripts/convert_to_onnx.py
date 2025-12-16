import torch
import argparse


def convert_to_onnx(model_path, output_path):
    # 加载PyTorch模型
    model = torch.load(model_path)
    model.eval()

    # 创建一个虚拟输入
    dummy_input = torch.randn(1, 3, 640, 640)

    # 导出为ONNX
    torch.onnx.export(model, dummy_input, output_path,
                      input_names=['images'], output_names=['output'],
                      dynamic_axes={'images': {0: 'batch_size'}, 'output': {0: 'batch_size'}},
                      opset_version=11)
    print(f"模型已成功转换为ONNX格式: {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', type=str, required=True, help='PyTorch模型文件路径 (.pt)')
    parser.add_argument('--output', type=str, required=True, help='输出ONNX文件路径 (.onnx)')
    args = parser.parse_args()

    convert_to_onnx(args.model, args.output)