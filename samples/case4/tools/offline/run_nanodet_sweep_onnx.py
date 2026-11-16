#!/usr/bin/env python3
"""Run the NanoDet sweep ONNX models on CUDA over the held-out test split."""

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


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
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
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    import cv2
    import numpy as np
    import onnxruntime as ort

    tools_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(tools_root / "offline"))
    sys.path.insert(0, str(tools_root / "board"))
    import handed_nanodet_fulltest as full
    from run_nanodet_sweep_om import infer_contract, priors

    bundle = args.bundle_root.resolve()
    manifest_path = bundle / args.manifest
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_sha256 = sha256_file(manifest_path)
    contracts = json.loads((bundle / args.contracts).read_text(encoding="utf-8"))
    contracts_by_name = {item["name"]: item for item in contracts.get("models", [])}
    selected = set(args.model)
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    annotations_sha256 = sha256_file(args.annotations)
    if annotations_sha256 != args.annotations_sha256:
        raise ValueError("annotations SHA-256 mismatch")
    annotations, records = full.load_test_split(args.annotations, args.images_root, verify_image_hashes=True)
    if len(records) != args.expected_images or len(annotations["annotations"]) != args.expected_annotations:
        raise ValueError(
            f"fulltest size mismatch: images={len(records)}, annotations={len(annotations['annotations'])}"
        )
    available = ort.get_available_providers()
    if "CUDAExecutionProvider" not in available:
        raise RuntimeError(f"CUDAExecutionProvider is unavailable: {available}")
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "nanodet_sweep_onnx_cuda_fulltest",
        "started_at": now(),
        "dataset": {
            "images": len(records),
            "annotations": len(annotations["annotations"]),
            "annotations_sha256": annotations_sha256,
            "image_ids_sha256": hashlib.sha256(
                json.dumps(sorted(record.image_id for record in records), separators=(",", ":")).encode()
            ).hexdigest(),
        },
        "manifest": {"path": str(manifest_path), "sha256": manifest_sha256},
        "contracts_sha256": sha256_file(bundle / args.contracts),
        "providers": available,
        "models": [],
    }
    expected_names = [item["name"] for item in manifest["models"] if item.get("group") == "sweep"]
    unknown = selected - set(expected_names)
    if unknown:
        raise ValueError(f"unknown or out-of-scope models: {sorted(unknown)}")
    target_names = [name for name in expected_names if not selected or name in selected]
    report["expected_models"] = expected_names
    report["requested_models"] = target_names
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
            if declared["onnx_sha256"] != item["sha256"]:
                raise ValueError("contract ONNX SHA does not match manifest")
            if declared["input_name"] != item["inputs"][0][0] or declared["output_name"] != item["outputs"][0][0]:
                raise ValueError("contract tensor names do not match manifest")
            if declared["input_shape"] != item["inputs"][0][1] or declared["output_shape"] != item["outputs"][0][1]:
                raise ValueError("contract tensor shapes do not match manifest")
            expected_literals = {
                "input_layout": "NCHW",
                "output_layout": "NAC",
                "input_dtype": "float32",
                "output_dtype": "float32",
                "resize_mode": "direct_stretch",
                "color_order": "BGR",
                "score_transform": "sigmoid_in_graph",
                "dfl_order": ["left", "top", "right", "bottom"],
                "class_names": ["left_palm", "right_palm"],
                "class_ids": [1, 2],
                "mean": [103.53, 116.28, 123.675],
                "std": [57.375, 57.12, 58.395],
            }
            for key, value in expected_literals.items():
                if declared.get(key) != value:
                    raise ValueError(f"unsupported decoder contract {key}: {declared.get(key)!r}")
            inferred = infer_contract(item)
            contract = {
                "input_shape": tuple(declared["input_shape"]),
                "output_shape": tuple(declared["output_shape"]),
                "reg_max": int(declared["reg_max"]),
                "strides": tuple(int(value) for value in declared["strides"]),
                "prior_count": int(declared["output_shape"][1]),
                "class_count": len(declared["class_names"]),
                "input_name": declared["input_name"],
                "output_name": declared["output_name"],
                "class_names": declared["class_names"],
                "resize_mode": declared["resize_mode"],
                "color_order": declared["color_order"],
                "center_offset": float(declared["center_offset"]),
            }
            if inferred != {key: contract[key] for key in inferred}:
                raise ValueError(f"manifest/contract mismatch: inferred={inferred}, declared={contract}")
            if contract["class_names"] != ["left_palm", "right_palm"]:
                raise ValueError("sweep mAP requires the fixed two-class contract")
            model_report.update({
                "onnx_path": str(bundle / item["relative_path"]),
                "onnx_sha256": item["sha256"],
                "contract": contract,
            })
            options = ort.SessionOptions()
            session = ort.InferenceSession(
                str(bundle / item["relative_path"]),
                sess_options=options,
                providers=["CUDAExecutionProvider"],
            )
            active = session.get_providers()
            if not active or active[0] != "CUDAExecutionProvider":
                raise RuntimeError(f"CUDA is not the primary provider: {active}")
            input_name = item["inputs"][0][0]
            output_name = item["outputs"][0][0]
            full.INPUT_SHAPE = contract["input_shape"]
            full.OUTPUT_SHAPE = contract["output_shape"]
            full.REG_MAX = contract["reg_max"]
            full.STRIDES = contract["strides"]
            full.PRIORS = priors(full.INPUT_SHAPE, full.STRIDES, contract["center_offset"])
            warm_image = cv2.imread(str(args.images_root / records[0].file_name), cv2.IMREAD_COLOR)
            if warm_image is None:
                raise ValueError("cannot read warmup image")
            warm_tensor = full.preprocess_bgr(warm_image)
            for _ in range(args.warmup):
                session.run([output_name], {input_name: warm_tensor})
            predictions = []
            timings = []
            for index, record in enumerate(records, start=1):
                image = cv2.imread(str(args.images_root / record.file_name), cv2.IMREAD_COLOR)
                if image is None or image.shape[:2] != (record.height, record.width):
                    raise ValueError(f"cannot decode {record.file_name}")
                tensor = full.preprocess_bgr(image)
                started = time.perf_counter_ns()
                raw = np.asarray(session.run([output_name], {input_name: tensor})[0], dtype=np.float32)
                timings.append((time.perf_counter_ns() - started) / 1e6)
                detections = full.decode_output(raw, image_width=record.width, image_height=record.height)
                predictions.extend(full.detections_to_coco(record.image_id, detections))
                if index % 100 == 0:
                    print(f"{name}: {index}/{len(records)}", flush=True)
            prediction_path = model_dir / "predictions.coco.json"
            write_json(prediction_path, predictions)
            ordered = sorted(timings)
            model_report.update({
                "status": "completed",
                "active_providers": active,
                "predictions": {"path": str(prediction_path), "sha256": sha256_file(prediction_path), "count": len(predictions)},
                "task_metrics": full.task_metrics(annotations, predictions),
                "performance": {
                    "scope": "ONNX Runtime session.run only; excludes JPEG decode, preprocessing, DFL/NMS and metrics",
                    "warmup": args.warmup,
                    "count": len(timings),
                    "p50_ms": ordered[len(ordered) // 2],
                    "p95_ms": ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)],
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
    complete = len(report["models"]) == len(expected_names) and not failures
    print(json.dumps({
        "expected_models": len(expected_names),
        "observed_models": len(report["models"]),
        "failures": failures,
    }, ensure_ascii=False))
    return 0 if complete else 1


if __name__ == "__main__":
    raise SystemExit(main())
