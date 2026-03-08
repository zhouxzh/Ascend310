import onnx

def check_onnx_output(model_path):
    model = onnx.load(model_path)
    print(f"Model: {model_path}")
    for output in model.graph.output:
        print(f"Output Name: {output.name}")
        print(f"Output Shape: {output.type.tensor_type.shape}")

if __name__ == "__main__":
    check_onnx_output("models/det_500m.onnx")
    check_onnx_output("models/w600k_mbf.onnx")
