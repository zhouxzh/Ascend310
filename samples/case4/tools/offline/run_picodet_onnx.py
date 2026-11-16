#!/usr/bin/env python3
"""Run the trained two-class PicoDet ONNX models over the fixed test split."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
import traceback
from typing import Any

import numpy as np


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--contracts", default="contracts.json")
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--annotations-sha256", required=True)
    parser.add_argument("--images-root", type=Path, required=True)
    parser.add_argument("--expected-images", type=int, default=1600)
    parser.add_argument("--expected-annotations", type=int, default=1601)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--warmup", type=int, default=10)
    parser.add_argument("--provider", choices=("cuda", "cpu"), default="cuda")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    import cv2
    import onnxruntime as ort

    tools_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(tools_root / "offline"))
    from handed_nanodet_fulltest import (
        detections_to_coco,
        load_test_split,
        task_metrics,
    )
    from handed_picodet_fulltest import decode_outputs, preprocess_bgr, validate_contract

    bundle = args.bundle_root.resolve()
    manifest_path = bundle / args.manifest
    contracts_path = bundle / args.contracts
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    contracts = json.loads(contracts_path.read_text(encoding="utf-8"))
    contracts_by_name = {item["name"]: item for item in contracts.get("models", [])}
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    annotations_sha256 = sha256_file(args.annotations)
    if annotations_sha256 != args.annotations_sha256:
        raise ValueError("annotations SHA-256 mismatch")
    annotations, records = load_test_split(args.annotations, args.images_root, verify_image_hashes=True)
    if len(records) != args.expected_images or len(annotations["annotations"]) != args.expected_annotations:
        raise ValueError(
            f"fulltest size mismatch: images={len(records)}, annotations={len(annotations['annotations'])}"
        )
    available = ort.get_available_providers()
    provider = "CUDAExecutionProvider" if args.provider == "cuda" else "CPUExecutionProvider"
    if provider not in available:
        raise RuntimeError(f"{provider} is unavailable: {available}")
    expected_names = [item["name"] for item in manifest["models"]]
    selected = set(args.model)
    unknown = selected - set(expected_names)
    if unknown:
        raise ValueError(f"unknown PicoDet models: {sorted(unknown)}")
    target_names = [name for name in expected_names if not selected or name in selected]
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "picodet_palm_onnx_fulltest",
        "started_at": now(),
        "provider_requested": provider,
        "providers_available": available,
        "dataset": {
            "images": len(records),
            "annotations": len(annotations["annotations"]),
            "annotations_sha256": annotations_sha256,
            "image_ids_sha256": hashlib.sha256(
                json.dumps(sorted(record.image_id for record in records), separators=(",", ":")).encode()
            ).hexdigest(),
        },
        "manifest": {"path": str(manifest_path), "sha256": sha256_file(manifest_path)},
        "contracts_sha256": sha256_file(contracts_path),
        "expected_models": expected_names,
        "requested_models": target_names,
        "models": [],
    }
    report_path = output_dir / "onnx-evaluation-summary.json"
    for item in manifest["models"]:
        name = item["name"]
        if name not in target_names:
            continue
        model_dir = output_dir / name
        model_dir.mkdir(parents=True, exist_ok=True)
        model_report: dict[str, Any] = {"name": name, "status": "running"}
        session = None
        try:
            declared = contracts_by_name[name]
            if declared.get("sha256") != item.get("sha256"):
                raise ValueError("contract ONNX SHA does not match manifest")
            contract = validate_contract(declared)
            model_path = (bundle / item["relative_path"]).resolve()
            if not model_path.is_file():
                raise FileNotFoundError(model_path)
            if model_path.stat().st_size != int(item["bytes"]):
                raise ValueError("ONNX byte count mismatch")
            if sha256_file(model_path) != item["sha256"]:
                raise ValueError("ONNX SHA-256 mismatch")
            options = ort.SessionOptions()
            session = ort.InferenceSession(str(model_path), sess_options=options, providers=[provider])
            active = session.get_providers()
            if not active or active[0] != provider:
                raise RuntimeError(f"requested provider is not primary: {active}")
            session_inputs = session.get_inputs()
            session_outputs = session.get_outputs()
            if len(session_inputs) != 1 or session_inputs[0].name != contract.input_name:
                raise ValueError(f"ONNX input contract mismatch: {[value.name for value in session_inputs]}")
            observed_output_names = tuple(value.name for value in session_outputs)
            if observed_output_names != contract.output_names:
                raise ValueError(f"ONNX output names mismatch: {observed_output_names} != {contract.output_names}")
            observed_shapes = tuple(tuple(int(dim) for dim in value.shape) for value in session_outputs)
            if observed_shapes != contract.output_shapes:
                raise ValueError(f"ONNX output shapes mismatch: {observed_shapes} != {contract.output_shapes}")
            warm_image = cv2.imread(str(args.images_root / records[0].file_name), cv2.IMREAD_COLOR)
            if warm_image is None:
                raise ValueError("cannot read warmup image")
            warm_tensor = preprocess_bgr(warm_image, contract)
            for _ in range(args.warmup):
                session.run(list(contract.output_names), {contract.input_name: warm_tensor})
            predictions: list[dict[str, Any]] = []
            timings: list[float] = []
            for index, record in enumerate(records, start=1):
                image = cv2.imread(str(args.images_root / record.file_name), cv2.IMREAD_COLOR)
                if image is None or image.shape[:2] != (record.height, record.width):
                    raise ValueError(f"cannot decode {record.file_name}")
                tensor = preprocess_bgr(image, contract)
                started = time.perf_counter_ns()
                raw_outputs = session.run(list(contract.output_names), {contract.input_name: tensor})
                timings.append((time.perf_counter_ns() - started) / 1e6)
                detections = decode_outputs(raw_outputs, contract, image_width=record.width, image_height=record.height)
                predictions.extend(detections_to_coco(record.image_id, detections))
                if index % 100 == 0:
                    print(f"{name}: {index}/{len(records)}", flush=True)
            prediction_path = model_dir / "predictions.coco.json"
            write_json(prediction_path, predictions)
            ordered = sorted(timings)
            model_report.update({
                "status": "completed",
                "onnx_path": str(model_path),
                "onnx_sha256": item["sha256"],
                "contract": {
                    "input_shape": list(contract.input_shape),
                    "output_names": list(contract.output_names),
                    "output_shapes": [list(shape) for shape in contract.output_shapes],
                    "reg_max": 7,
                    "strides": [8, 16, 32, 64],
                    "cell_offset": 0.5,
                },
                "active_providers": active,
                "predictions": {
                    "path": str(prediction_path),
                    "relative_path": str(prediction_path.relative_to(output_dir)),
                    "sha256": sha256_file(prediction_path),
                    "count": len(predictions),
                },
                "task_metrics": task_metrics(annotations, predictions),
                "performance": {
                    "scope": "ONNX Runtime session.run only; excludes JPEG decode, preprocessing, DFL/NMS and metrics",
                    "warmup": args.warmup,
                    "count": len(timings),
                    "p50_ms": ordered[len(ordered) // 2],
                    "p95_ms": ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)],
                    "mean_ms": sum(timings) / len(timings),
                },
            })
        except BaseException as exc:  # noqa: BLE001 - persist each model failure
            model_report.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()})
        finally:
            if session is not None:
                del session
        report["models"].append(model_report)
        write_json(report_path, report)
        print(f"{model_report['status']} {name}", flush=True)
    report["finished_at"] = now()
    write_json(report_path, report)
    failures = [item["name"] for item in report["models"] if item["status"] != "completed"]
    print(json.dumps({"expected_models": len(expected_names), "observed_models": len(report["models"]), "failures": failures}, ensure_ascii=False))
    return 0 if len(report["models"]) == len(expected_names) and not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
