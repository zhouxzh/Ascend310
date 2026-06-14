#!/usr/bin/env python3
"""INT8 量化准备：opset 转换 + 创建量化配置。

这一步生成 AMCT PTQ 所需的中间文件：
  - config.json         量化方案（每层的 scale / zero-point 计划）
  - updated_model.onnx  AMCT 更新后的 ONNX 中间模型

本章原始 ONNX 是 opset 18；CANN 8.3.RC1 对应的 AMCT ONNX 配套表使用
ONNX 1.14.0 / opset 16 / ONNX Runtime 1.16.0，因此默认先转换到 opset 16。

产物保存在 --work-dir（默认 outputs/int8_amct/），供下一步
04_calibrate_quantization.py 校准使用。

默认使用 calib_list.txt 中的全部校准图片；传入 --samples N 时才只取前 N 张。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


os.environ.setdefault("TORCH_DEVICE_BACKEND_AUTOLOAD", "0")

SCRIPT_DIR = Path(__file__).resolve().parent
CHAPTER_DIR = SCRIPT_DIR
if str(CHAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(CHAPTER_DIR))

from chapter8_utils import (  # noqa: E402
    DEFAULT_CALIB_LIST,
    MODEL_DIR,
    read_calibration_list,
    resolve_chapter_path,
)


DEFAULT_ONNX = MODEL_DIR / "resnet18_tiny_imagenet.onnx"
DEFAULT_WORK_DIR = CHAPTER_DIR / "outputs/int8_amct"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="INT8 preparation: opset conversion + AMCT config.")
    parser.add_argument("--onnx", default=str(DEFAULT_ONNX), help="Input FP32 ONNX model.")
    parser.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR), help="AMCT intermediate output directory.")
    parser.add_argument("--calib-list", default=str(DEFAULT_CALIB_LIST), help="Calibration list path.")
    parser.add_argument(
        "--samples",
        type=int,
        default=0,
        help="Number of calibration samples for AMCT batch_num preset. 0 = all images in calib_list. Default: 0.",
    )
    parser.add_argument(
        "--amct-opset",
        type=int,
        default=16,
        help=(
            "Convert ONNX to this opset before AMCT. Default: 16 for the "
            "CANN 8.3.RC1 AMCT ONNX dependency set. Use 0 to skip."
        ),
    )
    parser.add_argument(
        "--skip-layers",
        nargs="*",
        default=[],
        help="Layer names to skip during quantization.",
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
    except Exception as exc:
        raise SystemExit(
            "Missing AMCT ONNX quantization environment.\n"
            "Install onnx, onnxruntime, and the AMCT package that matches your CANN version.\n"
            f"Original error: {type(exc).__name__}: {exc}"
        ) from exc
    return amct, onnx


def get_default_opset(model) -> int | None:
    for opset in model.opset_import:
        if opset.domain in ("", "ai.onnx"):
            return int(opset.version)
    return None


def prepare_opset(onnx_path: Path, work_dir: Path, target_opset: int, onnx):
    """Step 1: convert ONNX to target opset if needed."""
    if target_opset <= 0:
        print("[1/2] Skip opset conversion (--amct-opset 0)")
        return onnx_path

    model = onnx.load(str(onnx_path))
    current_opset = get_default_opset(model)
    print(f"[1/2] Current opset: {current_opset}, target: {target_opset}")

    if current_opset == target_opset:
        print("[1/2] Opset already matches, skip conversion")
        return onnx_path

    converted_model = work_dir / f"{onnx_path.stem}_opset{target_opset}.onnx"
    print(f"[1/2] Converting opset {current_opset} -> {target_opset} ...")
    try:
        converted = onnx.version_converter.convert_version(model, target_opset)
        onnx.checker.check_model(converted)
        onnx.save(converted, str(converted_model))
    except Exception as exc:
        raise RuntimeError(
            f"Failed to convert opset {current_opset} -> {target_opset}. "
            "Pass --amct-opset 0 if your AMCT version supports the current opset."
        ) from exc
    print(f"[1/2] Saved: {converted_model}")
    return converted_model


def main() -> int:
    args = parse_args()
    if args.samples < 0:
        raise ValueError("--samples must not be negative")

    onnx_path = resolve_chapter_path(args.onnx)
    work_dir = resolve_chapter_path(args.work_dir)
    calib_list = resolve_chapter_path(args.calib_list)

    if not onnx_path.exists():
        raise FileNotFoundError(f"ONNX model not found: {onnx_path}")

    paths = read_calibration_list(calib_list, limit=args.samples or None)
    work_dir.mkdir(parents=True, exist_ok=True)

    config_file = work_dir / "config.json"
    updated_model = work_dir / "updated_model.onnx"

    amct, onnx = import_amct()

    print("=== INT8 Preparation: opset + config ===")
    print(f"  onnx:        {onnx_path}")
    print(f"  work dir:    {work_dir}")
    print(f"  calib list:  {calib_list}")
    print(f"  samples:     {len(paths)}")
    print(f"  AMCT opset:  {args.amct_opset}")
    print(f"  skip layers: {','.join(args.skip_layers) if args.skip_layers else '(none)'}")
    print()

    # Step 1: opset conversion
    amct_onnx = prepare_opset(onnx_path, work_dir, args.amct_opset, onnx)

    # Step 2: create quantization config
    print("[2/2] Creating quantization config (amct.create_quant_config) ...")
    amct.create_quant_config(
        config_file=str(config_file),
        model_file=str(amct_onnx),
        skip_layers=args.skip_layers or None,
        batch_num=len(paths),
        activation_offset=args.activation_offset,
        updated_model=str(updated_model),
    )
    print(f"[2/2] Config saved: {config_file}")
    print(f"       This file defines which layers to quantize and how.")

    print()
    print("Preparation done. Next step:")
    print(f"  python 04_calibrate_quantization.py --work-dir {work_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
