#!/usr/bin/env python3
"""
Case 4: Model preparation — PyTorch → ONNX → Ascend OM.

Two modes:
  1. --onnx-only    Export .pth to .onnx (can run on dev machine)
  2. default        Export ONNX then ATC-convert to .om (needs Ascend device)

Usage:
    python3 prepare_models.py              # full pipeline
    python3 prepare_models.py --onnx-only  # only .pth → .onnx
"""

import argparse
import os
import subprocess
import sys

import numpy as np
import torch

from config import (
    IMAGE_SIZE,
    MODEL_DIR,
    ONNX_MODEL_PATH,
    OM_MODEL_PATH,
    PTH_MODEL_PATH,
)
from ghostnet import ghostnet_1x


# ---------------------------------------------------------------------------
# PyTorch → ONNX
# ---------------------------------------------------------------------------

def export_onnx(pth_path=PTH_MODEL_PATH, onnx_path=ONNX_MODEL_PATH,
                opset=11):
    """Load trained GhostNet .pth and export to ONNX."""
    if not os.path.exists(pth_path):
        print(f"[ONNX] ERROR: {pth_path} not found.")
        print("  Run train.py first to train the model.")
        return None

    print(f"[ONNX] Loading weights from {pth_path} ...")
    model = ghostnet_1x()
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
        dynamic_axes={},
        export_params=True,
    )

    # Verify
    try:
        import onnx
        onnx.checker.check_model(onnx_path)
        print(f"[ONNX] Verified: {onnx_path}")
    except ImportError:
        print("[ONNX] (install onnx to verify the exported model)")

    # Sanity check: compare PyTorch vs ONNX Runtime output
    _sanity_check(model, onnx_path, dummy)

    print("[ONNX] Export complete.")
    return model


def _sanity_check(model, onnx_path, dummy):
    """Compare torch and ONNX outputs to catch conversion issues."""
    try:
        import onnxruntime as ort
    except ImportError:
        return

    with torch.no_grad():
        torch_out = model(dummy).numpy()

    session = ort.InferenceSession(onnx_path)
    ort_out = session.run(None, {"input": dummy.numpy()})[0]

    diff = np.abs(torch_out - ort_out).max()
    print(f"[ONNX] Sanity check: max |torch - onnx| = {diff:.2e}")


# ---------------------------------------------------------------------------
# ONNX → Ascend OM (ATC)
# ---------------------------------------------------------------------------

def convert_om(onnx_path=ONNX_MODEL_PATH, om_path=OM_MODEL_PATH):
    """Run ATC to convert ONNX to Ascend offline model."""
    if not os.path.exists(onnx_path):
        print(f"[ATC] ERROR: {onnx_path} not found.")
        return False

    soc = "Ascend310B4"
    try:
        result = subprocess.run(["npu-smi", "info"], capture_output=True,
                                text=True, timeout=10)
        for line in result.stdout.splitlines():
            if "310B" in line:
                soc = "Ascend310B4"
                break
    except Exception:
        pass

    om_base = om_path.replace(".om", "")
    cmd = [
        "atc",
        f"--model={onnx_path}",
        "--framework=5",
        f"--output={om_base}",
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
        description="Prepare palmprint recognition model (ONNX + OM)")
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
    print("Case 4 — Palmprint Model Preparation")
    print("=" * 50)
    print(f"  PTH:  {args.pth}  {'OK' if os.path.exists(args.pth) else 'MISSING'}")
    print(f"  ONNX: {args.onnx}  {'OK' if os.path.exists(args.onnx) else 'MISSING'}")
    print(f"  OM:   {args.om}  {'OK' if os.path.exists(args.om) else 'MISSING'}")
    print()

    if not os.path.exists(args.pth):
        print("[Prepare] No .pth weights found.")
        print("  Run:  python3 train.py --data-dir /path/to/palmprint/dataset")
        print("  Then re-run prepare_models.py")
        sys.exit(1)

    if not os.path.exists(args.onnx):
        export_onnx(args.pth, args.onnx)

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
        print("Model preparation complete!")
    else:
        print("ONNX ready, OM conversion skipped (no CANN / no ATC?).")
        print("  Run on the Ascend 310B device to get the .om file.")


if __name__ == "__main__":
    main()
