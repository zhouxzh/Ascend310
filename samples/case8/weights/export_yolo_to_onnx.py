import argparse
import json
import os
import shutil
import sys
from pathlib import Path


def restart_without_system_cuda_ld_library_path():
    ld_library_path = os.environ.get("LD_LIBRARY_PATH")
    if not ld_library_path:
        return
    paths = [
        path
        for path in ld_library_path.split(":")
        if path and not path.startswith("/usr/local/cuda")
    ]
    cleaned = ":".join(paths)
    if cleaned == ld_library_path:
        return
    os.environ["LD_LIBRARY_PATH"] = cleaned
    os.environ["HAGRID_CLEANED_LD_LIBRARY_PATH"] = "1"
    os.execv(sys.executable, [sys.executable, *sys.argv])


if os.environ.get("HAGRID_CLEANED_LD_LIBRARY_PATH") != "1":
    restart_without_system_cuda_ld_library_path()

import onnx
from ultralytics import YOLO


def parse_args():
    parser = argparse.ArgumentParser(description="Export HaGRID YOLO weights to ONNX for Ascend conversion.")
    parser.add_argument(
        "-w",
        "--weights",
        required=True,
        type=Path,
        help="Path to YOLO .pt weights.",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=Path("models"),
        type=Path,
        help="Directory for exported ONNX and metadata.",
    )
    parser.add_argument(
        "--imgsz",
        default=640,
        type=int,
        help="Static square input size for export.",
    )
    parser.add_argument(
        "--batch",
        default=1,
        type=int,
        help="Static batch size for export.",
    )
    parser.add_argument(
        "--opset",
        default=13,
        type=int,
        help="ONNX opset. Start with 13 for Ascend ATC; try 17 if export/conversion needs it.",
    )
    parser.add_argument(
        "--device",
        default="cpu",
        type=str,
        help="Export device. CPU is recommended for reproducible ONNX export.",
    )
    parser.add_argument(
        "--dynamic",
        action="store_true",
        help="Export dynamic shapes. Static shape is recommended for Ascend 310B.",
    )
    parser.add_argument(
        "--simplify",
        action="store_true",
        help="Ask Ultralytics to simplify the ONNX graph.",
    )
    parser.add_argument(
        "--half",
        action="store_true",
        help="Export FP16 ONNX. For Ascend, FP32 ONNX plus ATC precision settings is usually safer.",
    )
    parser.add_argument(
        "--skip-check",
        action="store_true",
        help="Skip ONNX checker validation.",
    )
    return parser.parse_args()


def sorted_names(names):
    if isinstance(names, dict):
        return [names[index] for index in sorted(names)]
    return list(names)


def write_labels(output_dir, stem, names):
    labels_path = output_dir / f"{stem}_labels.txt"
    labels_path.write_text("\n".join(names) + "\n", encoding="utf-8")
    return labels_path


def get_onnx_io(onnx_path):
    model = onnx.load(onnx_path)
    inputs = [node.name for node in model.graph.input]
    outputs = [node.name for node in model.graph.output]
    input_shapes = {}
    for node in model.graph.input:
        dims = []
        for dim in node.type.tensor_type.shape.dim:
            dims.append(dim.dim_value if dim.dim_value else dim.dim_param)
        input_shapes[node.name] = dims
    return inputs, outputs, input_shapes


def main():
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    model = YOLO(args.weights)
    names = sorted_names(model.names)

    exported = model.export(
        format="onnx",
        imgsz=args.imgsz,
        batch=args.batch,
        opset=args.opset,
        device=args.device,
        dynamic=args.dynamic,
        simplify=args.simplify,
        half=args.half,
    )

    exported_path = Path(exported)
    if not exported_path.exists():
        exported_path = args.weights.with_suffix(".onnx")
    if not exported_path.exists():
        raise FileNotFoundError(f"Ultralytics export finished, but ONNX file was not found: {exported}")

    destination = args.output_dir / exported_path.name
    if exported_path.resolve() != destination.resolve():
        shutil.copy2(exported_path, destination)

    if not args.skip_check:
        onnx.checker.check_model(onnx.load(destination))

    inputs, outputs, input_shapes = get_onnx_io(destination)
    labels_path = write_labels(args.output_dir, destination.stem, names)

    metadata = {
        "weights": str(args.weights),
        "onnx": str(destination),
        "labels": str(labels_path),
        "num_classes": len(names),
        "names": names,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "opset": args.opset,
        "dynamic": args.dynamic,
        "half": args.half,
        "inputs": inputs,
        "outputs": outputs,
        "input_shapes": input_shapes,
    }
    metadata_path = args.output_dir / f"{destination.stem}_metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    print(f"ONNX: {destination}")
    print(f"Labels: {labels_path}")
    print(f"Metadata: {metadata_path}")
    print(f"Inputs: {inputs}")
    print(f"Outputs: {outputs}")


if __name__ == "__main__":
    main()
