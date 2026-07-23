#!/usr/bin/env python3
"""Batch-export all official DDSP-VST timbre models to verified ONNX."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil

from export_ddsp_vst_onnx import (
    _import_onnx,
    build_model,
    compare_tflite_onnx,
    load_parameters,
    verify_onnx,
)


ROOT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE_DIR = ROOT_DIR / "_upstream" / "ddsp-vst" / "models" / "ddsp"
DEFAULT_OUTPUT_DIR = ROOT_DIR / "models" / "ddsp_vst"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--instruments",
        nargs="*",
        help="Optional model stems to export, for example Flute Trumpet",
    )
    parser.add_argument("--opset", type=int, default=11)
    parser.add_argument("--verify-steps", type=int, default=8)
    parser.add_argument(
        "--skip-tflite-compare",
        action="store_true",
        help="Skip TensorFlow Lite versus ONNX numerical parity checks",
    )
    return parser.parse_args()


def select_models(source_dir: Path, instruments: list[str] | None) -> list[Path]:
    models = sorted(source_dir.glob("*.tflite"), key=lambda path: path.stem.lower())
    if not instruments:
        return models
    requested = {name.lower() for name in instruments}
    selected = [path for path in models if path.stem.lower() in requested]
    found = {path.stem.lower() for path in selected}
    missing = sorted(requested - found)
    if missing:
        raise FileNotFoundError(f"Unknown DDSP-VST instruments: {', '.join(missing)}")
    return selected


def export_model(
    source_tflite: Path,
    output_dir: Path,
    opset: int,
    verify_steps: int,
    compare_tflite: bool,
) -> dict[str, object]:
    instrument = source_tflite.stem
    target_tflite = output_dir / source_tflite.name
    target_onnx = output_dir / f"{instrument}.onnx"
    shutil.copy2(source_tflite, target_tflite)

    params = load_parameters(target_tflite)
    model = build_model(params, opset=opset)
    onnx, *_ = _import_onnx()
    onnx.save(model, str(target_onnx))

    verification = verify_onnx(target_onnx, steps=verify_steps)
    metadata: dict[str, object] = {
        "instrument": instrument,
        "source_tflite": str(target_tflite),
        "onnx_model": str(target_onnx),
        "opset": opset,
        "input_order": ["state", "f0_scaled", "pw_scaled"],
        "output_order": ["amplitude", "harmonics", "noise_amps", "state_out"],
        "state_size": 512,
        "num_harmonics": 60,
        "num_noise_amps": 65,
        "sample_rate": 16000,
        "frame_rate": 50,
        "hop_size": 320,
        "verification": verification,
    }
    if compare_tflite:
        metadata["tflite_parity"] = compare_tflite_onnx(
            target_tflite, target_onnx, steps=verify_steps
        )

    metadata_path = target_onnx.with_suffix(".json")
    metadata_path.write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return metadata


def main() -> int:
    args = parse_args()
    if not args.source_dir.exists():
        raise FileNotFoundError(f"DDSP-VST model directory not found: {args.source_dir}")
    if args.verify_steps < 1:
        raise ValueError("--verify-steps must be at least 1")

    models = select_models(args.source_dir, args.instruments)
    if not models:
        raise FileNotFoundError(f"No .tflite models found in {args.source_dir}")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    license_path = args.source_dir / "LICENSE"
    if license_path.exists():
        shutil.copy2(license_path, args.output_dir / "LICENSE")

    reports: list[dict[str, object]] = []
    failures: list[dict[str, str]] = []
    for index, source_model in enumerate(models, start=1):
        print(f"[{index}/{len(models)}] Exporting {source_model.stem} ...")
        try:
            report = export_model(
                source_model,
                args.output_dir,
                args.opset,
                args.verify_steps,
                compare_tflite=not args.skip_tflite_compare,
            )
            reports.append(report)
            parity = report.get("tflite_parity", {})
            max_abs = parity.get("max_absolute_error", {}) if isinstance(parity, dict) else {}
            print(f"[PASS] {source_model.stem}: max_abs={max_abs}")
        except Exception as exc:
            failures.append({"instrument": source_model.stem, "error": str(exc)})
            print(f"[FAIL] {source_model.stem}: {exc}")

    summary = {
        "source_directory": str(args.source_dir),
        "output_directory": str(args.output_dir),
        "opset": args.opset,
        "verify_steps": args.verify_steps,
        "tflite_comparison": not args.skip_tflite_compare,
        "models": reports,
        "failures": failures,
    }
    summary_path = args.output_dir / "models.json"
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        f"[SUMMARY] {len(reports)} passed, {len(failures)} failed; "
        f"report={summary_path}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
