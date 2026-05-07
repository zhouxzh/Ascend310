#!/usr/bin/env python3
"""
Case 8: Model preparation — PyTorch → ONNX → Ascend OM.

Two modes:
  1. --download   Fetch a pre-trained gesture model (skip training)
  2. --convert    Export .pth to .onnx, then ATC-convert to .om

Usage:
    python3 prepare_models.py                     # convert local .pth
    python3 prepare_models.py --download          # download pre-trained first
    python3 prepare_models.py --onnx-only         # only .pth → .onnx
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
    MODEL_DIR,
    NUM_CLASSES,
    ONNX_MODEL_PATH,
    OM_MODEL_PATH,
    PTH_MODEL_PATH,
)

# ---------------------------------------------------------------------------
# Pre-trained model download URL
# (Placeholder — in a real release this would point to a hosted model file)
# ---------------------------------------------------------------------------

PRETRAINED_URL = "https://example.com/gesture_mobilenetv3.pth"


def download_pretrained():
    """Download a pre-trained gesture model."""
    import requests

    os.makedirs(MODEL_DIR, exist_ok=True)

    print(f"Downloading pre-trained model from {PRETRAINED_URL}...")
    try:
        resp = requests.get(PRETRAINED_URL, stream=True, timeout=120)
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        with open(PTH_MODEL_PATH, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded += len(chunk)
                if total:
                    pct = downloaded / total * 100
                    print(f"\r  {downloaded}/{total} ({pct:.1f}%)", end="")
        print("\n[Download] Done.")
        return True
    except Exception as exc:
        print(f"\n[Download] Failed: {exc}")
        print("[Download] Please train the model manually with train.py, "
              "or place a .pth file in models/")
        return False


# ---------------------------------------------------------------------------
# PyTorch → ONNX
# ---------------------------------------------------------------------------

def export_onnx(pth_path=PTH_MODEL_PATH, onnx_path=ONNX_MODEL_PATH,
                opset=11):
    """Load trained .pth and export to ONNX."""
    if not os.path.exists(pth_path):
        print(f"[ONNX] ERROR: {pth_path} not found.")
        print("  Run train.py first, or use --download to get a pre-trained "
              "model.")
        return False

    print(f"[ONNX] Loading weights from {pth_path} ...")
    model = models.mobilenet_v3_small()
    in_features = model.classifier[3].in_features
    model.classifier[3] = torch.nn.Linear(in_features, NUM_CLASSES)

    state = torch.load(pth_path, map_location="cpu", weights_only=True)
    model.load_state_dict(state)
    model.eval()

    print(f"[ONNX] Exporting to {onnx_path} (opset={opset}) ...")
    dummy = torch.randn(1, 3, IMAGE_SIZE, IMAGE_SIZE)

    os.makedirs(os.path.dirname(onnx_path), exist_ok=True)

    torch.onnx.export(
        model,
        dummy,
        onnx_path,
        input_names=["input"],
        output_names=["output"],
        opset_version=opset,
        dynamic_axes={},  # fixed shape for Ascend OM
        export_params=True,
    )
    print("[ONNX] Export complete.")
    return True


# ---------------------------------------------------------------------------
# ONNX → Ascend OM (ATC)
# ---------------------------------------------------------------------------

def convert_om(onnx_path=ONNX_MODEL_PATH, om_path=OM_MODEL_PATH):
    """Run ATC to convert ONNX to Ascend offline model."""
    if not os.path.exists(onnx_path):
        print(f"[ATC] ERROR: {onnx_path} not found.")
        return False

    soc = "Ascend310B4"
    # Try to detect actual soc version
    try:
        result = subprocess.run(["npu-smi", "info"], capture_output=True,
                                text=True, timeout=10)
        for line in result.stdout.splitlines():
            if "310B" in line:
                soc = "Ascend310B4"
                break
    except Exception:
        pass

    cmd = [
        "atc",
        f"--model={onnx_path}",
        "--framework=5",           # 5 = ONNX
        f"--output={om_path.replace('.om', '')}",
        f"--soc_version={soc}",
        "--input_format=NCHW",
        "--input_shape=input:1,3,224,224",
    ]

    print(f"[ATC] Converting ONNX → OM (soc={soc}) ...")
    print(f"[ATC] Command: {' '.join(cmd)}")

    try:
        subprocess.run(cmd, check=True)
        print(f"[ATC] Conversion complete: {om_path}")
        return True
    except subprocess.CalledProcessError as exc:
        print(f"[ATC] ERROR: {exc}")
        return False
    except FileNotFoundError:
        print("[ATC] ERROR: 'atc' command not found.")
        print("  Make sure CANN is installed and environment is set up:")
        print("  source /usr/local/Ascend/ascend-toolkit/set_env.sh")
        return False


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Prepare gesture recognition model (ONNX + OM)")
    parser.add_argument("--download", action="store_true",
                        help="Download pre-trained model first")
    parser.add_argument("--onnx-only", action="store_true",
                        help="Only export ONNX, skip ATC")
    parser.add_argument("--pth", default=PTH_MODEL_PATH,
                        help="Path to .pth weights")
    parser.add_argument("--onnx", default=ONNX_MODEL_PATH,
                        help="Path for output .onnx")
    parser.add_argument("--om", default=OM_MODEL_PATH,
                        help="Path for output .om")
    args = parser.parse_args()

    print("=" * 50)
    print("Case 8 — Gesture Model Preparation")
    print("=" * 50)
    print(f"  PTH:  {args.pth}  {'✓' if os.path.exists(args.pth) else '✗'}")
    print(f"  ONNX: {args.onnx}  {'✓' if os.path.exists(args.onnx) else '✗'}")
    print(f"  OM:   {args.om}  {'✓' if os.path.exists(args.om) else '✗'}")
    print()

    # -- Step 1: Get .pth weights
    if args.download and not os.path.exists(args.pth):
        download_pretrained()

    if not os.path.exists(args.pth):
        print("[Prepare] No .pth weights found.")
        print("  Options:")
        print("    1. python3 train.py           (train the model)")
        print("    2. python3 prepare_models.py --download  (download pre-trained)")
        sys.exit(1)

    # -- Step 2: Export ONNX
    if not os.path.exists(args.onnx) or not args.onnx_only:
        if not export_onnx(args.pth, args.onnx):
            sys.exit(1)

    # -- Step 3: Convert to OM
    if args.onnx_only:
        print("[Prepare] Skipping ATC (--onnx-only).")
        return

    if not os.path.exists(args.om):
        convert_om(args.onnx, args.om)
    else:
        print(f"[Prepare] {args.om} already exists. Delete it to re-convert.")

    print()
    print("=" * 50)
    if os.path.exists(args.om):
        print("✓ Model preparation complete!")
    else:
        print("⚠ ONNX ready, OM conversion skipped (no ATC / no CANN?)")
        print("  Run on the Ascend 310B device with CANN to get the .om file.")


if __name__ == "__main__":
    main()
