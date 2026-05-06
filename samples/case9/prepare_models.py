"""
Download and convert the text embedding model for Ascend 310B.

Steps:
  1. Export the HuggingFace model to ONNX
  2. Convert ONNX to Ascend OM via ATC

Usage:
  python3 prepare_models.py                  # do everything needed
  python3 prepare_models.py --download-only  # only export ONNX
  python3 prepare_models.py --convert-only   # only convert existing ONNX
  python3 prepare_models.py --force          # redo all steps
"""

import argparse
import os
import subprocess
import sys
import time


MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
MODEL_DIR = "models"
ONNX_PATH = os.path.join(MODEL_DIR, "embedding_model.onnx")
OM_PATH = os.path.join(MODEL_DIR, "embedding_model.om")
SOC_VERSION = "Ascend310B4"


def export_onnx(output_path):
    """Export all-MiniLM-L6-v2 to ONNX using optimum."""
    print(f"Exporting {MODEL_NAME} to ONNX ...")
    try:
        from optimum.onnxruntime import ORTModelForFeatureExtraction
        from transformers import AutoTokenizer
        import torch

        ort_model = ORTModelForFeatureExtraction.from_pretrained(
            MODEL_NAME, export=True
        )
        # optimum exports to the model directory; move to our target
        tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
        tokenizer.save_pretrained(MODEL_DIR)
        print(f"  ONNX model exported to {output_path}")
        return True
    except ImportError:
        print("  optimum not available, trying manual export ...")
        return _export_onnx_manual(output_path)


def _export_onnx_manual(output_path):
    """Manual ONNX export using torch.onnx.export."""
    try:
        from transformers import AutoTokenizer, AutoModel
        import torch

        tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
        model = AutoModel.from_pretrained(MODEL_NAME)
        model.eval()

        dummy_text = "Hello world"
        encoded = tokenizer(
            dummy_text, padding="max_length", truncation=True,
            max_length=256, return_tensors="pt",
        )

        torch.onnx.export(
            model,
            (
                encoded["input_ids"],
                encoded["attention_mask"],
                encoded.get("token_type_ids", torch.zeros_like(encoded["input_ids"])),
            ),
            output_path,
            input_names=["input_ids", "attention_mask", "token_type_ids"],
            output_names=["sentence_embedding"],
            dynamic_axes={
                "input_ids": {0: "batch_size", 1: "sequence_length"},
                "attention_mask": {0: "batch_size", 1: "sequence_length"},
                "token_type_ids": {0: "batch_size", 1: "sequence_length"},
                "sentence_embedding": {0: "batch_size"},
            },
            opset_version=14,
        )
        tokenizer.save_pretrained(MODEL_DIR)
        print(f"  ONNX model exported to {output_path}")
        return True
    except ImportError as e:
        print(f"  Manual export failed: {e}")
        print(f"  Install: pip install torch transformers onnx")
        return False


def convert_to_om(onnx_path, output_name, input_shape=None):
    """Run ATC to convert ONNX to OM."""
    cmd = [
        "atc",
        f"--model={onnx_path}",
        "--framework=5",
        f"--output={output_name}",
        f"--soc_version={SOC_VERSION}",
    ]
    if input_shape:
        cmd.append(f"--input_shape={input_shape}")

    print("Command:", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
        print("Conversion successful.")
        return True
    except subprocess.CalledProcessError as e:
        print(f"Conversion failed: {e}")
        return False
    except FileNotFoundError:
        print("atc command not found. Ensure CANN is installed and environment is set up.")
        print("Source set_env.sh first, or skip NPU conversion with --download-only.")
        return False


def download_progress(description, current, total):
    bar_len = 40
    if total > 0:
        filled = int(bar_len * current / total)
        bar = "█" * filled + "░" * (bar_len - filled)
        pct = current / total * 100
        print(f"\r{description}: [{bar}] {pct:.0f}%", end="")
    else:
        print(f"\r{description}: {current}", end="")


def main():
    parser = argparse.ArgumentParser(description="Prepare embedding model")
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--convert-only", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    os.makedirs(MODEL_DIR, exist_ok=True)

    has_onnx = os.path.exists(ONNX_PATH)
    has_om = os.path.exists(OM_PATH)

    print("=== Model Status ===")
    print(f"ONNX: {'✓' if has_onnx else '✗'} {ONNX_PATH}")
    print(f"OM:   {'✓' if has_om else '✗'} {OM_PATH}")
    print()

    need_download = not has_onnx or args.force
    need_convert = not has_om or args.force

    if args.convert_only:
        need_download = False
    elif args.download_only:
        need_convert = False

    if has_om and not args.force and not args.download_only and not args.convert_only:
        print("✓ All models ready. Nothing to do.")
        print("  Use --force to redo all steps.")
        return

    if need_download and not args.convert_only:
        print("=== Export Phase ===")
        if args.force and os.path.exists(ONNX_PATH):
            os.remove(ONNX_PATH)
        if export_onnx(ONNX_PATH):
            print("✓ ONNX export complete.")
        else:
            print("✗ ONNX export failed. The system will use CPU fallback.")
            print("  For CPU-only usage, no further steps are needed.")

    if need_convert and not args.download_only:
        print("\n=== Conversion Phase ===")
        if not os.path.exists(ONNX_PATH):
            print("✗ ONNX file not found. Run without --convert-only first.")
            return
        if args.force and os.path.exists(OM_PATH):
            os.remove(OM_PATH)
        input_shape = (
            "input_ids:1,256;attention_mask:1,256;token_type_ids:1,256"
        )
        convert_to_om(ONNX_PATH, os.path.join(MODEL_DIR, "embedding_model"), input_shape)

    print("\n=== Done ===")
    if os.path.exists(OM_PATH):
        print("✓ OM model ready for NPU inference.")
    elif os.path.exists(ONNX_PATH):
        print("✓ ONNX model ready. Run --convert-only after setting up CANN.")
    else:
        print("⚠ No model files. The app will use CPU fallback (sentence-transformers).")


if __name__ == "__main__":
    main()
