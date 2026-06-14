#!/usr/bin/env python3
"""INT8 校准与保存：用真实校准数据统计激活范围，冻结为 deploy ONNX。

前置条件：已运行 03_prepare_quantization.py 生成了中间文件。
本脚本会在同一进程内重新调用 amct.quantize_model() 生成 modified_model.onnx，
再用校准数据运行 ONNX Runtime 推理收集每层激活统计信息，最后调用
amct.save_model 冻结为 ATC 可直接转换的 deploy ONNX。

AMCT 0.23.x 会把部分量化配置和融合信息保存在进程内单例中，因此
quantize_model() 与 save_model() 需要在同一 Python 进程里执行。

产物：
  - scale_offset_record.txt  每层实际的 scale / zero-point
  - deploy ONNX              冻结后的 INT8 模型，可直接用 ATC 转 OM

默认使用 calib_list.txt 中的全部校准图片；传入 --samples N 时才只取前 N 张。
"""

from __future__ import annotations

import argparse
import os
import shutil
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
    load_rgb_frame,
    preprocess_resnet_rgb,
    read_calibration_list,
    resolve_chapter_path,
)


DEFAULT_ONNX = MODEL_DIR / "resnet18_tiny_imagenet.onnx"
DEFAULT_WORK_DIR = CHAPTER_DIR / "outputs/int8_amct"
DEFAULT_DEPLOY_ONNX = MODEL_DIR / "resnet18_tiny_imagenet_int8_deploy.onnx"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="INT8 calibration: run ONNX Runtime with calibration data.")
    parser.add_argument("--onnx", default=str(DEFAULT_ONNX), help="Original FP32 ONNX (for reference only).")
    parser.add_argument("--deploy-onnx", default=str(DEFAULT_DEPLOY_ONNX), help="Output deploy ONNX for ATC.")
    parser.add_argument("--work-dir", default=str(DEFAULT_WORK_DIR), help="AMCT intermediate directory from step 03.")
    parser.add_argument("--config-file", default="", help="AMCT config.json from step 03. Defaults to work-dir/config.json.")
    parser.add_argument("--calib-list", default=str(DEFAULT_CALIB_LIST), help="Calibration list path.")
    parser.add_argument("--calib-root", default="", help="Calibration root directory.")
    parser.add_argument(
        "--samples",
        type=int,
        default=0,
        help="Number of calibration samples to use. 0 = all images in calib_list. Default: 0.",
    )
    parser.add_argument("--input-name", default="input.1", help="ONNX input tensor name.")
    return parser.parse_args()


def import_amct_ort():
    try:
        import amct_onnx as amct  # type: ignore
        import onnxruntime as ort  # type: ignore
    except Exception as exc:
        raise SystemExit(
            "Missing AMCT ONNX quantization environment.\n"
            "Install onnx, onnxruntime, and the AMCT package.\n"
            f"Original error: {type(exc).__name__}: {exc}"
        ) from exc
    return amct, ort


def find_deploy_model(work_dir: Path, save_prefix: Path) -> Path:
    candidates = sorted(work_dir.glob(f"{save_prefix.name}*deploy*.onnx"))
    if not candidates:
        candidates = sorted(work_dir.glob("*deploy*.onnx"))
    if not candidates:
        raise FileNotFoundError(f"AMCT did not generate a deploy ONNX under {work_dir}")
    return candidates[0]


def main() -> int:
    args = parse_args()
    if args.samples < 0:
        raise ValueError("--samples must not be negative")

    deploy_onnx = resolve_chapter_path(args.deploy_onnx)
    work_dir = resolve_chapter_path(args.work_dir)
    config_file = resolve_chapter_path(args.config_file) if args.config_file else work_dir / "config.json"
    calib_list = resolve_chapter_path(args.calib_list)
    calib_root = resolve_chapter_path(args.calib_root) if args.calib_root else calib_list.parent

    modified_model = work_dir / "modified_model.onnx"
    updated_model = work_dir / "updated_model.onnx"
    record_file = work_dir / "scale_offset_record.txt"
    save_prefix = work_dir / "resnet18_tiny_imagenet_int8"
    model_for_quant = updated_model if updated_model.exists() else resolve_chapter_path(args.onnx)

    if not model_for_quant.exists():
        raise FileNotFoundError(
            f"Model for AMCT quantization not found: {model_for_quant}\n"
            "Run 03_prepare_quantization.py first."
        )
    if not config_file.exists():
        raise FileNotFoundError(
            f"AMCT config not found: {config_file}\n"
            "Run 03_prepare_quantization.py first."
        )

    paths = read_calibration_list(calib_list, root=calib_root, limit=args.samples or None)
    deploy_onnx.parent.mkdir(parents=True, exist_ok=True)

    amct, ort = import_amct_ort()

    print("=== INT8 Calibration: collect activation ranges ===")
    print(f"  modified model: {modified_model}")
    print(f"  deploy onnx:    {deploy_onnx}")
    print(f"  work dir:       {work_dir}")
    print(f"  config file:    {config_file}")
    print(f"  calib list:     {calib_list}")
    print(f"  samples:        {len(paths)}")
    print()

    # Step 3: re-create modified ONNX in this process.
    # AMCT keeps fusion/config metadata in process-local state that save_model()
    # reads later, so this call must happen in the same process as save_model().
    print("[3/5] Rebuilding AMCT modified model in this process ...")
    amct.quantize_model(str(config_file), str(model_for_quant), str(modified_model), str(record_file))
    print(f"[3/5] Modified model saved: {modified_model}")

    # Step 4: calibrate — run ONNX Runtime with AMCT session options
    # AMCT_SO disables graph optimizations so fake-quant nodes stay in the graph
    print("[4/5] Calibrating with ONNX Runtime (amct.AMCT_SO) ...")
    amct.AMCT_SO.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    amct.AMCT_SO.intra_op_num_threads = 1
    amct.AMCT_SO.inter_op_num_threads = 1
    session = ort.InferenceSession(
        str(modified_model),
        amct.AMCT_SO,
        providers=["CPUExecutionProvider"],
    )

    for index, path in enumerate(paths):
        frame = load_rgb_frame(path)
        input_tensor = preprocess_resnet_rgb(frame)
        session.run(None, {args.input_name: input_tensor})
        if (index + 1) % 10 == 0 or index + 1 == len(paths):
            print(f"  calibrated {index + 1}/{len(paths)}")

    print(f"[4/5] Scale/offset records: {record_file}")
    print(f"       Contains the actual scale and zero-point for each quantized tensor.")

    # Step 5: freeze — save deploy ONNX with calibrated scales baked in
    print("[5/5] Freezing deploy model (amct.save_model) ...")
    amct.save_model(str(modified_model), str(record_file), str(save_prefix))

    generated_deploy = find_deploy_model(work_dir, save_prefix)
    if generated_deploy.resolve() != deploy_onnx.resolve():
        shutil.copy2(generated_deploy, deploy_onnx)

    print(f"[5/5] Deploy ONNX saved: {deploy_onnx}")
    print()
    print("AMCT PTQ done. Next step:")
    print("  Run the INT8 ATC command in README.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
