#!/usr/bin/env python3
"""Create an Ascend-deployable INT8 ONNX model with AMCT static PTQ."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Any


os.environ.setdefault("TORCH_DEVICE_BACKEND_AUTOLOAD", "0")

SCRIPT_DIR = Path(__file__).resolve().parent
CHAPTER_DIR = SCRIPT_DIR.parent
if str(CHAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(CHAPTER_DIR))

from chapter8_utils import (  # noqa: E402
    DEFAULT_CALIB_LIST,
    MODEL_DIR,
    load_rgb_frame,
    preprocess_resnet_rgb,
    read_calibration_list,
    resolve_chapter_path,
)


DEFAULT_ONNX = MODEL_DIR / "resnet18_tiny_imagenet.onnx"
DEFAULT_WORK_DIR = CHAPTER_DIR / "outputs/int8_amct"
DEFAULT_DEPLOY_ONNX = MODEL_DIR / "resnet18_tiny_imagenet_int8_deploy.onnx"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Quantize ResNet18-TinyImageNet with AMCT ONNX PTQ.")
    parser.add_argument("--onnx", default=str(DEFAULT_ONNX), help="Input FP32 ONNX model.")
    parser.add_argument("--deploy-onnx", default=str(DEFAULT_DEPLOY_ONNX), help="Output deploy ONNX for ATC.")
    parser.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR), help="AMCT intermediate output directory.")
    parser.add_argument("--calib-list", default=str(DEFAULT_CALIB_LIST), help="Calibration list path.")
    parser.add_argument("--calib-root", default="", help="Calibration root directory. Defaults to list parent.")
    parser.add_argument("--samples", type=int, default=50, help="Number of calibration samples to use.")
    parser.add_argument("--input-name", default="input.1", help="ONNX input tensor name.")
    parser.add_argument(
        "--amct-opset",
        type=int,
        default=11,
        help="Convert FP32 ONNX to this opset before AMCT. Use 0 to disable. Default: 11.",
    )
    parser.add_argument(
        "--skip-layers",
        nargs="*",
        default=[],
        help="Layer names skipped by AMCT quantization.",
    )
    parser.add_argument(
        "--activation-offset",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Whether AMCT uses activation offset in generated config.",
    )
    return parser.parse_args()


def import_amct():
    try:
        import amct_onnx as amct  # type: ignore
        import onnx  # type: ignore
        import onnxruntime as ort  # type: ignore
    except Exception as exc:
        raise SystemExit(
            "Missing AMCT ONNX quantization environment.\n"
            "Install onnx, onnxruntime, and the AMCT package that matches your CANN version and CPU architecture.\n"
            "For AMCT, for example:\n"
            "  1. Download Ascend-cann-amct_<version>_linux-aarch64.tar.gz from the Ascend CANN page.\n"
            "  2. Extract it on the board.\n"
            "  3. Install amct_onnx-<version>-py3-none-linux_aarch64.whl into this Python environment.\n"
            "  4. Install/build the AMCT ONNX custom-op package if the AMCT package provides one.\n"
            f"Original error: {type(exc).__name__}: {exc}"
        ) from exc
    return amct, onnx, ort


def get_default_opset(model: Any) -> int | None:
    for opset in model.opset_import:
        if opset.domain in ("", "ai.onnx"):
            return int(opset.version)
    return None


def prepare_amct_model(onnx_path: Path, work_dir: Path, target_opset: int, onnx: Any) -> Path:
    if target_opset <= 0:
        return onnx_path

    model = onnx.load(str(onnx_path))
    current_opset = get_default_opset(model)
    if current_opset == target_opset:
        return onnx_path

    converted_model = work_dir / f"{onnx_path.stem}_opset{target_opset}.onnx"
    print(f"  convert opset: {current_opset} -> {target_opset}")
    try:
        converted = onnx.version_converter.convert_version(model, target_opset)
        onnx.checker.check_model(converted)
        onnx.save(converted, str(converted_model))
    except Exception as exc:
        raise RuntimeError(
            f"Failed to convert {onnx_path.name} from opset {current_opset} to opset {target_opset}. "
            "AMCT ONNX requires an opset it supports; export the source ONNX with that opset "
            "or pass --amct-opset 0 if your AMCT version supports the current model."
        ) from exc

    return converted_model


def find_deploy_model(work_dir: Path, save_prefix: Path) -> Path:
    candidates = sorted(work_dir.glob(f"{save_prefix.name}*deploy*.onnx"))
    if not candidates:
        candidates = sorted(work_dir.glob("*deploy*.onnx"))
    if not candidates:
        raise FileNotFoundError(f"AMCT did not generate a deploy ONNX under {work_dir}")
    return candidates[0]


def main() -> int:
    args = parse_args()
    if args.samples <= 0:
        raise ValueError("--samples must be positive")

    onnx_path = resolve_chapter_path(args.onnx)
    deploy_onnx = resolve_chapter_path(args.deploy_onnx)
    work_dir = resolve_chapter_path(args.work_dir)
    calib_list = resolve_chapter_path(args.calib_list)
    calib_root = resolve_chapter_path(args.calib_root) if args.calib_root else calib_list.parent

    if not onnx_path.exists():
        raise FileNotFoundError(f"ONNX model not found: {onnx_path}")

    paths = read_calibration_list(calib_list, root=calib_root, limit=args.samples)
    work_dir.mkdir(parents=True, exist_ok=True)
    deploy_onnx.parent.mkdir(parents=True, exist_ok=True)

    config_file = work_dir / "config.json"
    updated_model = work_dir / "updated_model.onnx"
    modified_model = work_dir / "modified_model.onnx"
    record_file = work_dir / "scale_offset_record.txt"
    save_prefix = work_dir / "resnet18_tiny_imagenet_int8"

    amct, onnx, ort = import_amct()

    print("AMCT INT8 PTQ quantization")
    print(f"  onnx:        {onnx_path}")
    print(f"  deploy onnx: {deploy_onnx}")
    print(f"  work dir:    {work_dir}")
    print(f"  calib list:  {calib_list}")
    print(f"  samples:     {len(paths)}")
    print(f"  input name:  {args.input_name}")
    print(f"  skip layers: {','.join(args.skip_layers) if args.skip_layers else '(none)'}")

    amct_onnx = prepare_amct_model(onnx_path, work_dir, args.amct_opset, onnx)

    amct.create_quant_config(
        config_file=str(config_file),
        model_file=str(amct_onnx),
        skip_layers=args.skip_layers or None,
        batch_num=len(paths),
        activation_offset=args.activation_offset,
        updated_model=str(updated_model),
    )

    model_for_quant = updated_model if updated_model.exists() else amct_onnx
    amct.quantize_model(str(config_file), str(model_for_quant), str(modified_model), str(record_file))

    amct.AMCT_SO.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    session = ort.InferenceSession(str(modified_model), amct.AMCT_SO)
    for index, path in enumerate(paths):
        frame = load_rgb_frame(path)
        input_tensor = preprocess_resnet_rgb(frame)
        session.run(None, {args.input_name: input_tensor})
        if (index + 1) % 10 == 0 or index + 1 == len(paths):
            print(f"  calibrated {index + 1}/{len(paths)}")

    amct.save_model(str(modified_model), str(record_file), str(save_prefix))

    generated_deploy = find_deploy_model(work_dir, save_prefix)
    if generated_deploy.resolve() != deploy_onnx.resolve():
        shutil.copy2(generated_deploy, deploy_onnx)

    print(f"AMCT deploy ONNX generated: {deploy_onnx}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
