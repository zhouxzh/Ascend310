import onnx

def check_onnx_input(model_path):
    model = onnx.load(model_path)
    print(f"Model: {model_path}")
    for input in model.graph.input:
        print(f"Input Name: {input.name}")
        print(f"Input Shape: {input.type.tensor_type.shape}")

if __name__ == "__main__":
    check_onnx_input("models/det_500m.onnx")
    check_onnx_input("models/w600k_mbf.onnx")
