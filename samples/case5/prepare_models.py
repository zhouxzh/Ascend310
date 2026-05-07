#!/usr/bin/env python3
"""
Case 5: Model preparation — EfficientNet-B0 fault classifier → ONNX →
Ascend OM.

Exports an EfficientNet-B0 with 5-class motor-fault head to ONNX, then
optionally converts to OM via ATC.

Usage:
    python3 prepare_models.py              # Full pipeline: ONNX + ATC
    python3 prepare_models.py --onnx-only  # ONNX only (no CANN needed)
    python3 prepare_models.py --force      # Overwrite existing files
"""

import argparse
import os
import subprocess
import sys

import numpy as np
import torch
import torchvision.models as models

from config import (
    IMAGE_SIZE,
    NUM_FAULT_CLASSES,
    ONNX_MODEL_PATH,
    OM_MODEL_PATH,
    MODEL_DIR,
    PTH_MODEL_PATH,
)


def get_soc_version():
    """Detect Ascend chip version via npu-smi, fall back to Ascend310B4."""
    try:
        out = subprocess.check_output(
            ["npu-smi", "info"], stderr=subprocess.STDOUT, timeout=10
        )
        for line in out.decode().splitlines():
            if "310B" in line:
                return "Ascend310B4"
            if "910" in line:
                return "Ascend910"
    except Exception:
        pass
    return "Ascend310B4"


def save_pretrained_efficientnet(pth_path):
    """Save a torchvision EfficientNet-B0 with 5-class fault head."""
    print("[PTH] Creating baseline EfficientNet-B0 "
          "(5-class fault head) ...")
    model = models.efficientnet_b0(
        weights=models.EfficientNet_B0_Weights.IMAGENET1K_V1
    )
    in_features = model.classifier[1].in_features
    model.classifier[1] = torch.nn.Linear(in_features, NUM_FAULT_CLASSES)
    model.eval()

    os.makedirs(os.path.dirname(pth_path), exist_ok=True)
    torch.save(model.state_dict(), pth_path)
    print(f"[PTH] Saved {pth_path}")


def export_onnx(onnx_path, opset=11):
    """Export EfficientNet-B0 (5-class fault head) to ONNX."""
    print("[ONNX] Building EfficientNet-B0 (5-class fault classifier) ...")

    model = models.efficientnet_b0(
        weights=models.EfficientNet_B0_Weights.IMAGENET1K_V1
    )
    in_features = model.classifier[1].in_features
    model.classifier[1] = torch.nn.Linear(in_features, NUM_FAULT_CLASSES)

    if os.path.exists(PTH_MODEL_PATH):
        state = torch.load(PTH_MODEL_PATH, map_location="cpu",
                           weights_only=True)
        model.load_state_dict(state)
        print(f"[ONNX] Loaded weights from {PTH_MODEL_PATH}")
    else:
        save_pretrained_efficientnet(PTH_MODEL_PATH)
        state = torch.load(PTH_MODEL_PATH, map_location="cpu",
                           weights_only=True)
        model.load_state_dict(state)
        print("[ONNX] Using baseline ImageNet+random-head weights "
              "(needs training for real accuracy)")

    model.eval()
    dummy = torch.randn(1, 3, IMAGE_SIZE, IMAGE_SIZE)

    print(f"[ONNX] Exporting to {onnx_path} ...")
    torch.onnx.export(
        model,
        dummy,
        onnx_path,
        input_names=["input"],
        output_names=["output"],
        opset_version=opset,
        dynamic_axes={},
    )

    import onnx
    onnx_model = onnx.load(onnx_path)
    onnx.checker.check_model(onnx_model)
    print(f"[ONNX] OK  — {onnx_path}")
    print(f"[ONNX]       input : {onnx_model.graph.input[0].name}  "
          f"{[d.dim_value for d in onnx_model.graph.input[0].type.tensor_type.shape.dim]}")  # noqa: E501
    print(f"[ONNX]       output: {onnx_model.graph.output[0].name} "
          f"{[d.dim_value for d in onnx_model.graph.output[0].type.tensor_type.shape.dim]}")  # noqa: E501


def convert_om(onnx_path, om_path):
    """ATC: convert ONNX to Ascend offline model."""
    soc = get_soc_version()
    print(f"[ATC] Converting {onnx_path} -> {om_path}")
    print(f"[ATC] SOC version: {soc}")

    cmd = [
        "atc",
        f"--model={onnx_path}",
        "--framework=5",
        f"--output={os.path.splitext(om_path)[0]}",
        f"--soc_version={soc}",
        "--input_format=NCHW",
        "--input_shape=input:1,3,224,224",
    ]

    print(f"[ATC] Running: {' '.join(cmd)}")
    subprocess.run(cmd, check=True)
    print(f"[ATC] OK  — {om_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Case 5: EfficientNet-B0 fault classifier — ONNX + OM"
    )
    parser.add_argument("--onnx-only", action="store_true",
                        help="Export ONNX only, skip ATC conversion")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing files")
    args = parser.parse_args()

    os.makedirs(MODEL_DIR, exist_ok=True)

    print("=" * 50)
    print("Case 5 — Model Preparation (Motor Fault Classifier)")
    print("=" * 50)
    print(f"  PTH  : {'V' if os.path.exists(PTH_MODEL_PATH) else 'X'} "
          f"{PTH_MODEL_PATH}")
    print(f"  ONNX : {'V' if os.path.exists(ONNX_MODEL_PATH) else 'X'} "
          f"{ONNX_MODEL_PATH}")
    print(f"  OM   : {'V' if os.path.exists(OM_MODEL_PATH) else 'X'} "
          f"{OM_MODEL_PATH}")
    print("-" * 50)

    # --- .pth baseline ---
    if not os.path.exists(PTH_MODEL_PATH) or args.force:
        save_pretrained_efficientnet(PTH_MODEL_PATH)

    # --- ONNX export ---
    if os.path.exists(ONNX_MODEL_PATH) and not args.force:
        print("[SKIP] ONNX already exists (use --force to overwrite)")
    else:
        try:
            export_onnx(ONNX_MODEL_PATH)
        except ImportError as e:
            print(f"[ERROR] Missing dependency: {e}")
            print("  Install: pip install onnx")
            sys.exit(1)

    # --- ATC conversion ---
    if args.onnx_only:
        print("[SKIP] ATC conversion (--onnx-only)")
        print("\nDone. To convert on the Ascend device, run:")
        print("  python3 prepare_models.py")
        return

    if os.path.exists(OM_MODEL_PATH) and not args.force:
        print("[SKIP] OM already exists (use --force to overwrite)")
    else:
        try:
            convert_om(ONNX_MODEL_PATH, OM_MODEL_PATH)
        except FileNotFoundError:
            print("[INFO] atc not found — this is expected on non-Ascend "
                  "machines.")
            print("  ONNX model is ready. Run this script on the 310B "
                  "device for OM conversion.")
        except Exception as exc:
            print(f"[ERROR] ATC conversion failed: {exc}")

    print("\nDone.")


if __name__ == "__main__":
    main()
