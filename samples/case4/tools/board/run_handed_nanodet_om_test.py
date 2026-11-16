#!/usr/bin/env python3
"""Run one seed43 dual-class NanoDet OM on the full held-out test split.

This board-only program uses the exact ACE2 CUDA reference pack.  It verifies
copied JPEGs and board preprocessing against every reference input hash, then
runs ACL inference and writes only aggregate/raw-agreement diagnostics and COCO
prediction JSON.  COCO AP calculation happens separately on ACE2 so boards do
not need an extra pycocotools dependency.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from itertools import chain
import json
from pathlib import Path
import sys
import time
import traceback
from typing import Any

import numpy as np

# This tool is copied alongside the shared offline module into a board workspace.
from handed_nanodet_fulltest import (
    CLASS_NAMES,
    COCO_SCORE_FLOOR,
    NMS_IOU_THRESHOLD,
    OUTPUT_SHAPE,
    RawAgreement,
    TASK_SCORE_THRESHOLD,
    compact_time_summary,
    decode_output,
    detections_to_coco,
    iter_reference_samples,
    json_hash,
    load_test_split,
    preprocess_bgr,
    sha256_array,
    sha256_file,
    write_json,
)


EXPECTED_INPUT = (1, 3, 320, 320)


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def unwrap(value: Any, operation: str) -> Any:
    if isinstance(value, tuple):
        if len(value) != 2:
            raise RuntimeError(f"{operation} returned unexpected tuple {value!r}")
        payload, code = value
        if int(code) != 0:
            raise RuntimeError(f"{operation} failed: {code}")
        return payload
    if value not in (None, 0):
        raise RuntimeError(f"{operation} failed: {value}")
    return None


def cleanup_call(acl: Any, events: list[dict[str, Any]], phase: str, operation: Any, *args: Any) -> None:
    event: dict[str, Any] = {"phase": phase}
    try:
        result = operation(*args)
        code = result[-1] if isinstance(result, tuple) else result
        code = 0 if code is None else int(code)
        event.update({"ok": code == 0, "return_code": code})
    except BaseException as exc:  # noqa: BLE001 - cleanup evidence must be retained
        event.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
    events.append(event)


def dims(acl: Any, desc: Any, getter: Any, index: int) -> tuple[int, ...]:
    result = unwrap(getter(desc, index), "get tensor dimensions")
    if not isinstance(result, dict):
        raise ValueError(f"invalid tensor dimension result: {result!r}")
    values = tuple(int(value) for value in result["dims"][: int(result["dimCount"])])
    if not values or any(value <= 0 for value in values):
        raise ValueError(f"invalid tensor dimensions: {result!r}")
    return values


def dtype(acl: Any, value: Any) -> np.dtype:
    mapping = {int(getattr(acl, "ACL_FLOAT", 0)): np.dtype(np.float32), int(getattr(acl, "ACL_FLOAT16", 1)): np.dtype(np.float16)}
    if int(value) not in mapping:
        raise ValueError(f"unsupported OM dtype {value}")
    return mapping[int(value)]


def host_pointer(acl: Any, value: np.ndarray) -> Any:
    converter = getattr(getattr(acl, "util", None), "numpy_to_ptr", None)
    return converter(value) if callable(converter) else value.ctypes.data


class AclDetectorRunner:
    """One-input/one-output static OM runner with deterministic cleanup."""

    def __init__(self, model: Path, device: int) -> None:
        import acl

        self.acl = acl
        self.device = device
        self.context: Any | None = None
        self.model_id: Any | None = None
        self.desc: Any | None = None
        self.input_dataset: Any | None = None
        self.output_dataset: Any | None = None
        self.input_pointer: Any | None = None
        self.input_buffer: Any | None = None
        self.output_pointer: Any | None = None
        self.output_buffer: Any | None = None
        self.input_size = 0
        self.output_size = 0
        self.input_shape: tuple[int, ...] = ()
        self.output_shape: tuple[int, ...] = ()
        self.input_dtype: np.dtype | None = None
        self.output_dtype: np.dtype | None = None
        self.cleanup: list[dict[str, Any]] = []
        self.owns_runtime = False
        try:
            init = acl.init()
            code = init[-1] if isinstance(init, tuple) else init
            if int(code) not in (0, 100002):
                raise RuntimeError(f"acl.init failed: {code}")
            self.owns_runtime = int(code) == 0
            unwrap(acl.rt.set_device(device), "acl.rt.set_device")
            self.context = unwrap(acl.rt.create_context(device), "acl.rt.create_context")
            self.model_id = unwrap(acl.mdl.load_from_file(str(model)), "acl.mdl.load_from_file")
            self.desc = acl.mdl.create_desc()
            if self.desc is None:
                raise RuntimeError("acl.mdl.create_desc returned no descriptor")
            unwrap(acl.mdl.get_desc(self.desc, self.model_id), "acl.mdl.get_desc")
            if int(acl.mdl.get_num_inputs(self.desc)) != 1 or int(acl.mdl.get_num_outputs(self.desc)) != 1:
                raise ValueError("dual-class fulltest evaluator requires one input and one output")
            self.input_shape = dims(acl, self.desc, acl.mdl.get_input_dims, 0)
            self.output_shape = dims(acl, self.desc, acl.mdl.get_output_dims, 0)
            self.input_dtype = dtype(acl, acl.mdl.get_input_data_type(self.desc, 0))
            self.output_dtype = dtype(acl, acl.mdl.get_output_data_type(self.desc, 0))
            if self.input_shape != EXPECTED_INPUT or self.output_shape != OUTPUT_SHAPE:
                raise ValueError(f"OM contract mismatch: input={self.input_shape}, output={self.output_shape}")
            if self.input_dtype != np.dtype(np.float32) or self.output_dtype != np.dtype(np.float32):
                raise ValueError(f"OM dtype contract mismatch: input={self.input_dtype}, output={self.output_dtype}")
            self.input_size = int(acl.mdl.get_input_size_by_index(self.desc, 0))
            self.output_size = int(acl.mdl.get_output_size_by_index(self.desc, 0))
            expected_input_bytes = int(np.prod(self.input_shape)) * self.input_dtype.itemsize
            expected_output_bytes = int(np.prod(self.output_shape)) * self.output_dtype.itemsize
            if self.input_size != expected_input_bytes or self.output_size != expected_output_bytes:
                raise ValueError(f"OM byte contract mismatch: input={self.input_size}/{expected_input_bytes}, output={self.output_size}/{expected_output_bytes}")
            self.input_dataset = acl.mdl.create_dataset()
            self.output_dataset = acl.mdl.create_dataset()
            self.input_pointer = unwrap(acl.rt.malloc(self.input_size, 0), "acl.rt.malloc input")
            self.output_pointer = unwrap(acl.rt.malloc(self.output_size, 0), "acl.rt.malloc output")
            self.input_buffer = acl.create_data_buffer(self.input_pointer, self.input_size)
            self.output_buffer = acl.create_data_buffer(self.output_pointer, self.output_size)
            if self.input_buffer is None or self.output_buffer is None:
                raise RuntimeError("ACL data buffer allocation failed")
            unwrap(acl.mdl.add_dataset_buffer(self.input_dataset, self.input_buffer), "acl.mdl.add_dataset_buffer input")
            unwrap(acl.mdl.add_dataset_buffer(self.output_dataset, self.output_buffer), "acl.mdl.add_dataset_buffer output")
        except BaseException:
            self.close()
            raise

    def run(self, tensor: np.ndarray) -> tuple[np.ndarray, float]:
        value = np.ascontiguousarray(tensor, dtype=np.float32)
        if tuple(value.shape) != self.input_shape or value.nbytes != self.input_size:
            raise ValueError(f"input tensor contract mismatch: {value.shape}, {value.nbytes}")
        unwrap(self.acl.rt.set_context(self.context), "acl.rt.set_context")
        started = time.perf_counter_ns()
        unwrap(self.acl.rt.memcpy(self.input_pointer, self.input_size, host_pointer(self.acl, value), value.nbytes, int(getattr(self.acl, "ACL_MEMCPY_HOST_TO_DEVICE", 1))), "host_to_device")
        unwrap(self.acl.mdl.execute(self.model_id, self.input_dataset, self.output_dataset), "acl.mdl.execute")
        synchronize = getattr(self.acl.rt, "synchronize_device", None)
        if callable(synchronize):
            unwrap(synchronize(), "acl.rt.synchronize_device")
        output = np.empty(self.output_shape, dtype=np.float32)
        unwrap(self.acl.rt.memcpy(host_pointer(self.acl, output), output.nbytes, self.output_pointer, self.output_size, int(getattr(self.acl, "ACL_MEMCPY_DEVICE_TO_HOST", 2))), "device_to_host")
        return output, (time.perf_counter_ns() - started) / 1e6

    def close(self) -> list[dict[str, Any]]:
        acl = getattr(self, "acl", None)
        if acl is None:
            return []
        if self.context is not None:
            cleanup_call(acl, self.cleanup, "set_context_before_cleanup", acl.rt.set_context, self.context)
        if self.output_buffer is not None:
            cleanup_call(acl, self.cleanup, "destroy_output_data_buffer", acl.destroy_data_buffer, self.output_buffer)
            self.output_buffer = None
        if self.input_buffer is not None:
            cleanup_call(acl, self.cleanup, "destroy_input_data_buffer", acl.destroy_data_buffer, self.input_buffer)
            self.input_buffer = None
        if self.output_dataset is not None:
            cleanup_call(acl, self.cleanup, "destroy_output_dataset", acl.mdl.destroy_dataset, self.output_dataset)
            self.output_dataset = None
        if self.input_dataset is not None:
            cleanup_call(acl, self.cleanup, "destroy_input_dataset", acl.mdl.destroy_dataset, self.input_dataset)
            self.input_dataset = None
        if self.output_pointer is not None:
            cleanup_call(acl, self.cleanup, "free_output_buffer", acl.rt.free, self.output_pointer)
            self.output_pointer = None
        if self.input_pointer is not None:
            cleanup_call(acl, self.cleanup, "free_input_buffer", acl.rt.free, self.input_pointer)
            self.input_pointer = None
        if self.desc is not None:
            cleanup_call(acl, self.cleanup, "destroy_model_desc", acl.mdl.destroy_desc, self.desc)
            self.desc = None
        if self.model_id is not None:
            cleanup_call(acl, self.cleanup, "unload_model", acl.mdl.unload, self.model_id)
            self.model_id = None
        if self.context is not None:
            cleanup_call(acl, self.cleanup, "destroy_context", acl.rt.destroy_context, self.context)
            self.context = None
        if self.owns_runtime:
            cleanup_call(acl, self.cleanup, "acl_reset_device", acl.rt.reset_device, self.device)
            cleanup_call(acl, self.cleanup, "acl_finalize", acl.finalize)
            self.owns_runtime = False
        else:
            self.cleanup.append({"phase": "runtime_left_intact", "ok": True, "status": "shared_runtime"})
        return self.cleanup


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--reference-dir", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--images-root", type=Path, required=True)
    parser.add_argument("--source-onnx-sha256", required=True)
    parser.add_argument("--tier", choices=("8t", "20t"), required=True)
    parser.add_argument("--precision-mode", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--overwrite", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.warmup < 0:
        raise SystemExit("--warmup must be non-negative")
    model = args.model.resolve()
    reference_dir = args.reference_dir.resolve()
    annotations_path = args.annotations.resolve()
    images_root = args.images_root.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "board-evaluation-report.v1.json"
    if report_path.exists() and not args.overwrite:
        raise SystemExit(f"existing report: {report_path}; use a new output directory")
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "handed_nanodet_acl_om_fulltest",
        "started_at": now(),
        "execution_status": "failed",
        "quality_gate": "metrics_pending_ace2",
        "board": {"tier": args.tier, "device": args.device},
        "artifact": {"om_path": str(model), "om_sha256": sha256_file(model), "om_bytes": model.stat().st_size, "precision_mode": args.precision_mode},
        "source": {"onnx_sha256": args.source_onnx_sha256, "annotations_path": str(annotations_path), "annotations_sha256": sha256_file(annotations_path)},
        "decoder": {"score_floor_for_coco": COCO_SCORE_FLOOR, "score_threshold_for_task": TASK_SCORE_THRESHOLD, "class_aware_nms_iou": NMS_IOU_THRESHOLD, "module_sha256": sha256_file(Path(__file__).with_name("handed_nanodet_fulltest.py"))},
        "raw_agreement": None,
    }
    runner: AclDetectorRunner | None = None
    diagnostics_file = None
    try:
        annotations, records = load_test_split(annotations_path, images_root, verify_image_hashes=True)
        records_by_id = {item.image_id: item for item in records}
        if len(records) != 1600 or len(annotations["annotations"]) != 1601:
            raise ValueError(f"unexpected copied test split size: images={len(records)}, annotations={len(annotations['annotations'])}")
        reference_iter = iter_reference_samples(reference_dir, expected_onnx_sha256=args.source_onnx_sha256, expected_annotations_sha256=report["source"]["annotations_sha256"])
        try:
            first_reference = next(reference_iter)
        except StopIteration as exc:
            raise ValueError("reference pack is empty") from exc
        first_id = int(first_reference[0]["image_id"])
        if first_id not in records_by_id:
            raise ValueError(f"reference pack contains unknown image ID {first_id}")
        runner = AclDetectorRunner(model, args.device)
        report["om_contract"] = {"input_shape": list(runner.input_shape), "output_shape": list(runner.output_shape), "input_dtype": str(runner.input_dtype), "output_dtype": str(runner.output_dtype)}
        # Warm the exact model/input contract once before timing the held-out set.
        if args.warmup:
            warmup_tensor = first_reference[1]
            for _ in range(args.warmup):
                runner.run(warmup_tensor)
        agreement = RawAgreement(atol=0.02, rtol=0.02)
        predictions: list[dict[str, Any]] = []
        timings: list[float] = []
        diagnostics_path = output_dir / "per-image-diagnostics.jsonl"
        diagnostics_file = diagnostics_path.open("w", encoding="utf-8")
        seen_reference_ids: set[int] = set()
        for index, (metadata, expected_input, expected_output) in enumerate(chain((first_reference,), reference_iter), start=1):
            image_id = int(metadata["image_id"])
            if image_id in seen_reference_ids or image_id not in records_by_id:
                raise ValueError(f"reference pack has duplicate or unknown image ID {image_id}")
            seen_reference_ids.add(image_id)
            record = records_by_id[image_id]
            try:
                import cv2
            except ImportError as exc:  # pragma: no cover - board environment only
                raise RuntimeError("OpenCV is required to validate board preprocessing") from exc
            image = cv2.imread(str(images_root / record.file_name), cv2.IMREAD_COLOR)
            if image is None or image.shape[:2] != (record.height, record.width):
                raise ValueError(f"board cannot decode copied test image {record.file_name}")
            tensor = preprocess_bgr(image)
            if sha256_array(tensor) != metadata["input_sha256"] or not np.array_equal(tensor, expected_input):
                raise ValueError(f"board preprocessing input mismatch for image {record.image_id}")
            output, elapsed_ms = runner.run(tensor)
            per_image = agreement.compare(expected_output, output)
            detections = decode_output(output, image_width=record.width, image_height=record.height)
            predictions.extend(detections_to_coco(record.image_id, detections))
            timings.append(elapsed_ms)
            diagnostic = {"image_id": record.image_id, "image_sha256": record.image_sha256, "input_sha256": metadata["input_sha256"], "om_output_sha256": sha256_array(output), "acl_total_ms": elapsed_ms, "coco_detection_count": len(detections), **per_image}
            diagnostics_file.write(json.dumps(diagnostic, ensure_ascii=False, separators=(",", ":")) + "\n")
            if index % 100 == 0:
                print(f"processed {index}/{len(records)}")
        if len(seen_reference_ids) != len(records) or seen_reference_ids != set(records_by_id):
            raise ValueError(f"reference pack coverage mismatch: {len(seen_reference_ids)}/{len(records)}")
        diagnostics_file.close()
        diagnostics_file = None
        predictions_path = output_dir / "predictions.coco.json"
        write_json(predictions_path, predictions)
        diagnostics_path = output_dir / "per-image-diagnostics.jsonl"
        report.update({
            "execution_status": "completed",
            "dataset": {"images": len(records), "annotations": len(annotations["annotations"]), "image_ids_sha256": json_hash(sorted(records_by_id))},
            "predictions": {"path": predictions_path.name, "sha256": sha256_file(predictions_path), "count": len(predictions)},
            "diagnostics": {"path": diagnostics_path.name, "sha256": sha256_file(diagnostics_path), "images": len(records)},
            "reference_pack": {"path": str(reference_dir), "manifest_sha256": sha256_file(reference_dir / "manifest.json")},
            "raw_agreement": agreement.summary(),
            "performance": {"scope": "H2D + ACL execute + device synchronize + D2H; excludes JPEG decode, preprocessing, DFL/NMS and metrics", "warmup": args.warmup, **compact_time_summary(timings)},
        })
    except BaseException as exc:  # noqa: BLE001 - persist exact board failure
        report["error"] = f"{type(exc).__name__}: {exc}"
        report["traceback"] = traceback.format_exc()
    finally:
        if diagnostics_file is not None:
            diagnostics_file.close()
        if runner is not None:
            report["cleanup"] = runner.close()
            report["cleanup_ok"] = all(item.get("ok") for item in report["cleanup"])
        report["finished_at"] = now()
        write_json(report_path, report)
    print(json.dumps({key: value for key, value in report.items() if key not in {"traceback"}}, ensure_ascii=False, indent=2))
    return 0 if report["execution_status"] == "completed" and report.get("cleanup_ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
