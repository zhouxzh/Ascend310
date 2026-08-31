import argparse
from pathlib import Path

import onnx

CASE_ROOT = Path(__file__).resolve().parents[1]

def check_onnx_input(model_path):
    model = onnx.load(model_path)
    print(f"Model: {model_path}")
    for input in model.graph.input:
        print(f"Input Name: {input.name}")
        print(f"Input Shape: {input.type.tensor_type.shape}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inspect Case 1 ONNX inputs")
    parser.add_argument("models", nargs="*", type=Path)
    args = parser.parse_args()
    paths = args.models or [
        CASE_ROOT / "models" / "det_500m.onnx",
        CASE_ROOT / "models" / "w600k_mbf.onnx",
    ]
    for model_path in paths:
        check_onnx_input(model_path)
