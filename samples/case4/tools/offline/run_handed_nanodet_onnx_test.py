#!/usr/bin/env python3
"""Evaluate the seed43 two-class NanoDet ONNX on the complete held-out test split.

The runner is intentionally CUDA-only: it refuses to create an ONNX Runtime
session unless CUDAExecutionProvider is the first active provider, then records
node providers from ORT profiling.  It writes exact preprocessed float32 inputs
and ONNX raw outputs in bounded shards for independent 310B ACL comparison.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import traceback
from typing import Any

import numpy as np

from handed_nanodet_fulltest import (
    CLASS_NAMES,
    COCO_SCORE_FLOOR,
    NMS_IOU_THRESHOLD,
    OUTPUT_SHAPE,
    ReferencePackWriter,
    TASK_SCORE_THRESHOLD,
    coco_metrics,
    compact_time_summary,
    decode_output,
    detections_to_coco,
    json_hash,
    load_test_split,
    preprocess_bgr,
    sha256_array,
    sha256_file,
    task_metrics,
    write_json,
)

RUN_ID = "nanodet-plus-m-320-portable-handed__data-v001__seed-43__20261114-1300"
ONNX_SHA256 = "35f71933a0629df4338673e1c9a3b0411e778fbc85c22b973cca33f69a267991"
ANNOTATION_SHA256 = "5db886eb40a75789b62b708445141081e2ca2c5276d445174efa3446b6b70b8a"


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--shard-size", type=int, default=50)
    parser.add_argument("--overwrite", action="store_true", help="Allow replacing a prior failed evaluation directory")
    return parser.parse_args()


def _gpu_summary() -> dict[str, Any]:
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total,memory.free,utilization.gpu", "--format=csv,noheader"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=15,
            check=False,
        )
        return {"returncode": result.returncode, "output": result.stdout.strip()}
    except BaseException as exc:  # pragma: no cover - host specific
        return {"error": f"{type(exc).__name__}: {exc}"}


def _profile_provider_summary(profile_path: Path) -> dict[str, Any]:
    events = json.loads(profile_path.read_text(encoding="utf-8"))
    providers: dict[str, int] = {}
    node_count = 0
    for event in events:
        if event.get("cat") != "Node":
            continue
        node_count += 1
        provider = str(event.get("args", {}).get("provider", "unknown"))
        providers[provider] = providers.get(provider, 0) + 1
    return {"path": str(profile_path), "sha256": sha256_file(profile_path), "node_events": node_count, "node_providers": providers}


def _safe_clear_failed_output(output_dir: Path, reference_dir: Path, overwrite: bool) -> None:
    for path in (output_dir / "evaluation-report.v1.json", reference_dir / "manifest.json"):
        if path.exists() and not overwrite:
            raise FileExistsError(f"existing completed artifact: {path}; use a new output directory")


def main() -> int:
    args = parse_args()
    if args.shard_size <= 0:
        raise SystemExit("--shard-size must be positive")
    root = args.repo_root.resolve()
    output_dir = args.output_dir.resolve()
    reference_dir = args.reference_dir.resolve()
    run_dir = root / "artifacts/runs" / RUN_ID
    annotations_path = root / "data/portable-hagridv2-mediapipe-hand/coco-handed-v001/annotations/instances_test.json"
    images_root = root / "data/portable-hagridv2-mediapipe-hand/coco-handed-v001/images"
    onnx_path = run_dir / "nanodet-plus-m-320-palm.onnx"
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "handed_nanodet_onnx_cuda_fulltest",
        "started_at": now(),
        "execution_status": "failed",
        "quality_gate": "not_evaluated",
        "run_id": RUN_ID,
        "backend": {"kind": "onnxruntime_cuda", "gpu": _gpu_summary()},
        "source": {
            "onnx": {"path": str(onnx_path), "expected_sha256": ONNX_SHA256},
            "annotations": {"path": str(annotations_path), "expected_sha256": ANNOTATION_SHA256},
            "contract": {"input": [1, 3, 320, 320], "output": list(OUTPUT_SHAPE), "classes": list(CLASS_NAMES)},
        },
        "decoder": {
            "id": "nanodet_plus_dfl_v1",
            "module_sha256": sha256_file(Path(__file__).with_name("handed_nanodet_fulltest.py")),
            "score_floor_for_coco": COCO_SCORE_FLOOR,
            "score_threshold_for_task": TASK_SCORE_THRESHOLD,
            "class_aware_nms_iou": NMS_IOU_THRESHOLD,
            "max_detections": 100,
        },
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    diagnostics_path = output_dir / "per-image-diagnostics.jsonl"
    diagnostics_handle = None
    session = None
    try:
        _safe_clear_failed_output(output_dir, reference_dir, args.overwrite)
        if not onnx_path.is_file() or sha256_file(onnx_path) != ONNX_SHA256:
            raise ValueError("seed43 ONNX SHA-256 mismatch")
        if not annotations_path.is_file() or sha256_file(annotations_path) != ANNOTATION_SHA256:
            raise ValueError("held-out test annotation SHA-256 mismatch")
        annotations, records = load_test_split(annotations_path, images_root, verify_image_hashes=True)
        if len(records) != 1600 or len(annotations["annotations"]) != 1601:
            raise ValueError(f"unexpected held-out split size: images={len(records)}, annotations={len(annotations['annotations'])}")
        try:
            import cv2
            import onnxruntime as ort
        except ImportError as exc:  # pragma: no cover - environment-specific
            raise RuntimeError("OpenCV and onnxruntime-gpu are required") from exc
        available = ort.get_available_providers()
        if "CUDAExecutionProvider" not in available:
            raise RuntimeError(f"CUDAExecutionProvider is unavailable: {available}")
        options = ort.SessionOptions()
        options.enable_profiling = True
        session = ort.InferenceSession(str(onnx_path), sess_options=options, providers=["CUDAExecutionProvider"])
        actual_providers = session.get_providers()
        if not actual_providers or actual_providers[0] != "CUDAExecutionProvider":
            raise RuntimeError(f"CUDA is not the primary active provider: {actual_providers}")
        report["backend"].update({"onnxruntime": ort.__version__, "available_providers": available, "active_providers": actual_providers})
        writer = ReferencePackWriter(
            reference_dir,
            source={"run_id": RUN_ID, "onnx_sha256": ONNX_SHA256, "annotations_sha256": ANNOTATION_SHA256, "backend": "onnxruntime_cuda", "onnxruntime": ort.__version__},
            shard_size=args.shard_size,
        )
        # One CUDA warmup is intentionally excluded from per-image timing and reference packs.
        warm_image = cv2.imread(str(images_root / records[0].file_name), cv2.IMREAD_COLOR)
        if warm_image is None:
            raise ValueError("cannot read warmup image")
        warm_input = preprocess_bgr(warm_image)
        session.run(["output"], {"data": warm_input})
        predictions: list[dict[str, Any]] = []
        timings_ms: list[float] = []
        diagnostics_handle = diagnostics_path.open("w", encoding="utf-8")
        for position, record in enumerate(records, start=1):
            image = cv2.imread(str(images_root / record.file_name), cv2.IMREAD_COLOR)
            if image is None:
                raise ValueError(f"cannot decode test image {record.file_name}")
            if image.shape[:2] != (record.height, record.width):
                raise ValueError(f"decoded dimensions differ from annotation for {record.file_name}: {image.shape[:2]}")
            tensor = preprocess_bgr(image)
            started = time.perf_counter_ns()
            output = np.asarray(session.run(["output"], {"data": tensor})[0], dtype=np.float32)
            elapsed_ms = (time.perf_counter_ns() - started) / 1e6
            if output.shape != OUTPUT_SHAPE or not np.isfinite(output).all():
                raise ValueError(f"ONNX output contract failed for image {record.image_id}: {output.shape}")
            detections = decode_output(output, image_width=record.width, image_height=record.height)
            predictions.extend(detections_to_coco(record.image_id, detections))
            writer.append(record, tensor, output)
            timings_ms.append(elapsed_ms)
            diagnostic = {
                "image_id": record.image_id,
                "image_sha256": record.image_sha256,
                "input_sha256": sha256_array(tensor),
                "output_sha256": sha256_array(output),
                "inference_ms": elapsed_ms,
                "coco_detection_count": len(detections),
            }
            diagnostics_handle.write(json.dumps(diagnostic, ensure_ascii=False, separators=(",", ":")) + "\n")
            if position % 100 == 0:
                print(f"processed {position}/{len(records)}")
        diagnostics_handle.close()
        diagnostics_handle = None
        manifest = writer.finalize(annotations_sha256=ANNOTATION_SHA256, annotations_count=len(annotations["annotations"]))
        image_ids = {record.image_id for record in records}
        predictions_path = output_dir / "predictions.coco.json"
        write_json(predictions_path, predictions)
        metrics = coco_metrics(annotations_path, predictions)
        task = task_metrics(annotations, predictions)
        profile_path = Path(session.end_profiling())
        profile = _profile_provider_summary(profile_path)
        if profile["node_providers"].get("CPUExecutionProvider", 0):
            raise RuntimeError(f"ORT profile found CPU node fallback: {profile['node_providers']}")
        if not profile["node_providers"].get("CUDAExecutionProvider", 0):
            raise RuntimeError(f"ORT profile did not record CUDA node execution: {profile['node_providers']}")
        prediction_manifest = {
            "schema_version": 1,
            "kind": "handed_nanodet_prediction_manifest",
            "source_onnx_sha256": ONNX_SHA256,
            "annotation_sha256": ANNOTATION_SHA256,
            "decoder_sha256": report["decoder"]["module_sha256"],
            "predictions": {"path": predictions_path.name, "sha256": sha256_file(predictions_path), "count": len(predictions)},
            "diagnostics": {"path": diagnostics_path.name, "sha256": sha256_file(diagnostics_path), "images": len(records)},
            "reference_pack": {"path": str(reference_dir), "manifest_sha256": sha256_file(reference_dir / "manifest.json"), "images": manifest["images"]},
            "backend": report["backend"],
        }
        write_json(output_dir / "prediction-manifest.v1.json", prediction_manifest)
        report.update({
            "execution_status": "completed",
            "quality_gate": "not_human_reviewed",
            "dataset": {"images": len(records), "annotations": len(annotations["annotations"]), "image_ids_sha256": json_hash(sorted(image_ids))},
            "performance": {"scope": "ORT session.run CUDA inference only; excludes image reading, preprocessing, decode and metric calculation", "warmup": 1, **compact_time_summary(timings_ms)},
            "predictions": prediction_manifest["predictions"],
            "metrics": {"coco": metrics, "task": task},
            "profile": profile,
            "reference_pack": {"manifest_path": str(reference_dir / "manifest.json"), "manifest_sha256": prediction_manifest["reference_pack"]["manifest_sha256"]},
        })
    except BaseException as exc:  # noqa: BLE001 - preserve exact evaluation blocker
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
        if session is not None:
            try:
                profile_path = Path(session.end_profiling())
                report["profile"] = _profile_provider_summary(profile_path)
            except BaseException as profile_exc:  # pragma: no cover - failure diagnostics only
                report["profile_error"] = f"{type(profile_exc).__name__}: {profile_exc}"
    finally:
        if diagnostics_handle is not None:
            diagnostics_handle.close()
        report["finished_at"] = now()
        write_json(output_dir / "evaluation-report.v1.json", report)
    print(json.dumps({key: value for key, value in report.items() if key not in {"traceback"}}, ensure_ascii=False, indent=2))
    return 0 if report["execution_status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
