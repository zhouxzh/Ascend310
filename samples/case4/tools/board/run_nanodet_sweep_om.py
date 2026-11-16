#!/usr/bin/env python3
"""Run converted NanoDet sweep OMs over the held-out two-class test split."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
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
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def infer_contract(item: dict[str, Any]) -> dict[str, Any]:
    inputs = item.get("inputs", [])
    outputs = item.get("outputs", [])
    if len(inputs) != 1 or len(outputs) != 1:
        raise ValueError("expected one input and one output")
    input_shape = tuple(int(value) for value in inputs[0][1])
    output_shape = tuple(int(value) for value in outputs[0][1])
    if len(input_shape) != 4 or input_shape[:2] != (1, 3):
        raise ValueError(f"unsupported input shape {input_shape}")
    if len(output_shape) != 3 or output_shape[0] != 1:
        raise ValueError(f"unsupported output shape {output_shape}")
    height, width = input_shape[2:]
    channels = output_shape[2]
    if channels < 34 or (channels - 2) % 4:
        raise ValueError(f"unsupported two-class output channels {channels}")
    reg_max = (channels - 2) // 4 - 1
    prior_count = output_shape[1]
    for strides in ((8, 16, 32), (8, 16, 32, 64)):
        count = sum(
            math.ceil(height / stride) * math.ceil(width / stride)
            for stride in strides
        )
        if count == prior_count:
            return {
                "input_shape": input_shape,
                "output_shape": output_shape,
                "reg_max": reg_max,
                "strides": strides,
                "prior_count": prior_count,
                "class_count": 2,
            }
    raise ValueError(f"cannot infer strides for input={input_shape}, priors={prior_count}")


def priors(input_shape: tuple[int, ...], strides: tuple[int, ...],
           center_offset: float = 0.0) -> np.ndarray:
    if center_offset not in (0.0, 0.5):
        raise ValueError("center_offset must be 0.0 or 0.5")
    height, width = input_shape[2:]
    rows = []
    for stride in strides:
        yy, xx = np.meshgrid(
            np.arange(math.ceil(height / stride)),
            np.arange(math.ceil(width / stride)),
            indexing="ij",
        )
        rows.append(np.stack(((xx.ravel() + center_offset) * stride,
                              (yy.ravel() + center_offset) * stride,
                              np.full(xx.size, stride)), axis=1))
    return np.concatenate(rows).astype(np.float32)


def unwrap(value: Any, operation: str) -> Any:
    if isinstance(value, tuple):
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


def tensor_dims(acl: Any, desc: Any, getter: Any, index: int) -> tuple[int, ...]:
    result = unwrap(getter(desc, index), "get tensor dimensions")
    values = tuple(int(value) for value in result["dims"][: int(result["dimCount"])])
    if not values or any(value <= 0 for value in values):
        raise ValueError(f"invalid tensor dimensions: {result!r}")
    return values


def tensor_dtype(acl: Any, value: Any) -> np.dtype:
    mapping = {
        int(getattr(acl, "ACL_FLOAT", 0)): np.dtype(np.float32),
        int(getattr(acl, "ACL_FLOAT16", 1)): np.dtype(np.float16),
    }
    if int(value) not in mapping:
        raise ValueError(f"unsupported OM dtype {value}")
    return mapping[int(value)]


def host_pointer(acl: Any, value: np.ndarray) -> Any:
    converter = getattr(getattr(acl, "util", None), "numpy_to_ptr", None)
    return converter(value) if callable(converter) else value.ctypes.data


_OWNS_RUNTIME = False


class AclRunner:
    def __init__(self, model: Path, expected_input: tuple[int, ...],
                 expected_output: tuple[int, ...], device: int) -> None:
        import acl

        global _OWNS_RUNTIME
        self.acl = acl
        self.device = device
        self.context = None
        self.model_id = None
        self.desc = None
        self.input_dataset = None
        self.output_dataset = None
        self.input_pointer = None
        self.output_pointer = None
        self.input_buffer = None
        self.output_buffer = None
        self.cleanup: list[dict[str, Any]] = []
        self.owns_runtime = False
        try:
            init = acl.init()
            code = init[-1] if isinstance(init, tuple) else init
            if int(code) not in (0, 100002):
                raise RuntimeError(f"acl.init failed: {code}")
            self.owns_runtime = int(code) == 0
            if self.owns_runtime:
                _OWNS_RUNTIME = True
            unwrap(acl.rt.set_device(device), "acl.rt.set_device")
            self.context = unwrap(acl.rt.create_context(device), "acl.rt.create_context")
            self.model_id = unwrap(acl.mdl.load_from_file(str(model)), "acl.mdl.load_from_file")
            self.desc = acl.mdl.create_desc()
            unwrap(acl.mdl.get_desc(self.desc, self.model_id), "acl.mdl.get_desc")
            if int(acl.mdl.get_num_inputs(self.desc)) != 1 or int(acl.mdl.get_num_outputs(self.desc)) != 1:
                raise ValueError("runner requires one input and one output")
            self.input_shape = tensor_dims(acl, self.desc, acl.mdl.get_input_dims, 0)
            self.output_shape = tensor_dims(acl, self.desc, acl.mdl.get_output_dims, 0)
            self.input_dtype = tensor_dtype(acl, acl.mdl.get_input_data_type(self.desc, 0))
            self.output_dtype = tensor_dtype(acl, acl.mdl.get_output_data_type(self.desc, 0))
            if self.input_shape != expected_input or self.output_shape != expected_output:
                raise ValueError(
                    f"OM contract mismatch: input={self.input_shape}, output={self.output_shape}"
                )
            if self.input_dtype != np.dtype(np.float32) or self.output_dtype != np.dtype(np.float32):
                raise ValueError(
                    f"OM dtype contract mismatch: input={self.input_dtype}, output={self.output_dtype}"
                )
            self.input_size = int(acl.mdl.get_input_size_by_index(self.desc, 0))
            self.output_size = int(acl.mdl.get_output_size_by_index(self.desc, 0))
            expected_input_bytes = int(np.prod(self.input_shape)) * self.input_dtype.itemsize
            expected_output_bytes = int(np.prod(self.output_shape)) * self.output_dtype.itemsize
            if self.input_size != expected_input_bytes or self.output_size != expected_output_bytes:
                raise ValueError("OM byte contract mismatch")
            self.input_dataset = acl.mdl.create_dataset()
            self.output_dataset = acl.mdl.create_dataset()
            self.input_pointer = unwrap(acl.rt.malloc(self.input_size, 0), "acl.rt.malloc input")
            self.output_pointer = unwrap(acl.rt.malloc(self.output_size, 0), "acl.rt.malloc output")
            self.input_buffer = acl.create_data_buffer(self.input_pointer, self.input_size)
            self.output_buffer = acl.create_data_buffer(self.output_pointer, self.output_size)
            unwrap(acl.mdl.add_dataset_buffer(self.input_dataset, self.input_buffer), "add input buffer")
            unwrap(acl.mdl.add_dataset_buffer(self.output_dataset, self.output_buffer), "add output buffer")
        except BaseException:
            self.close()
            raise

    def run(self, tensor: np.ndarray) -> tuple[np.ndarray, float]:
        value = np.ascontiguousarray(tensor, dtype=np.float32)
        if tuple(value.shape) != self.input_shape or value.nbytes != self.input_size:
            raise ValueError(f"input contract mismatch: {value.shape}, {value.nbytes}")
        unwrap(self.acl.rt.set_context(self.context), "acl.rt.set_context")
        started = time.perf_counter_ns()
        unwrap(self.acl.rt.memcpy(
            self.input_pointer, self.input_size, host_pointer(self.acl, value), value.nbytes,
            int(getattr(self.acl, "ACL_MEMCPY_HOST_TO_DEVICE", 1)),
        ), "host_to_device")
        unwrap(self.acl.mdl.execute(self.model_id, self.input_dataset, self.output_dataset), "acl.mdl.execute")
        synchronize = getattr(self.acl.rt, "synchronize_device", None)
        if callable(synchronize):
            unwrap(synchronize(), "acl.rt.synchronize_device")
        output = np.empty(self.output_shape, dtype=np.float32)
        unwrap(self.acl.rt.memcpy(
            host_pointer(self.acl, output), output.nbytes, self.output_pointer, self.output_size,
            int(getattr(self.acl, "ACL_MEMCPY_DEVICE_TO_HOST", 2)),
        ), "device_to_host")
        return output, (time.perf_counter_ns() - started) / 1e6

    def close(self) -> list[dict[str, Any]]:
        acl = getattr(self, "acl", None)
        if acl is None:
            return []
        if self.context is not None:
            cleanup_call(acl, self.cleanup, "set_context", acl.rt.set_context, self.context)
        for attr, operation, phase in (
            ("output_buffer", acl.destroy_data_buffer, "destroy_output_buffer"),
            ("input_buffer", acl.destroy_data_buffer, "destroy_input_buffer"),
            ("output_dataset", acl.mdl.destroy_dataset, "destroy_output_dataset"),
            ("input_dataset", acl.mdl.destroy_dataset, "destroy_input_dataset"),
            ("output_pointer", acl.rt.free, "free_output"),
            ("input_pointer", acl.rt.free, "free_input"),
            ("desc", acl.mdl.destroy_desc, "destroy_desc"),
            ("model_id", acl.mdl.unload, "unload_model"),
        ):
            value = getattr(self, attr, None)
            if value is not None:
                cleanup_call(acl, self.cleanup, phase, operation, value)
                setattr(self, attr, None)
        if self.context is not None:
            cleanup_call(acl, self.cleanup, "destroy_context", acl.rt.destroy_context, self.context)
            self.context = None
        if self.owns_runtime:
            self.cleanup.append({
                "phase": "runtime_retained_for_batch",
                "ok": True,
                "status": "shared_runtime",
            })
            self.owns_runtime = False
        return self.cleanup


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--contracts", default="contracts.json")
    parser.add_argument("--atc-summary", type=Path, required=True)
    parser.add_argument("--soc-version", required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--images-root", type=Path, required=True)
    parser.add_argument("--annotations-sha256", required=True)
    parser.add_argument("--expected-images", type=int, default=1600)
    parser.add_argument("--expected-annotations", type=int, default=1601)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--device", type=int, default=0)
    parser.add_argument("--warmup", type=int, default=10)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bundle = args.bundle_root.resolve()
    manifest_path = bundle / args.manifest
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest_sha256 = sha256_file(manifest_path)
    by_name = {item["name"]: item for item in manifest["models"]}
    contracts = json.loads((bundle / args.contracts).read_text(encoding="utf-8"))
    contracts_by_name = {item["name"]: item for item in contracts.get("models", [])}
    atc = json.loads(args.atc_summary.read_text(encoding="utf-8"))
    if atc.get("kind") != "nanodet_sweep_atc":
        raise ValueError("unexpected ATC summary kind")
    if atc.get("manifest", {}).get("sha256") != manifest_sha256:
        raise ValueError("ATC summary was produced from a different manifest")
    if atc.get("soc_version") != args.soc_version:
        raise ValueError(f"ATC SoC {atc.get('soc_version')} != expected {args.soc_version}")
    if atc.get("precision_mode") != "allow_mix_precision":
        raise ValueError("ATC summary is not allow_mix_precision")
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    import sys
    tools_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(tools_root / "offline"))
    sys.path.insert(0, str(tools_root / "board"))
    import handed_nanodet_fulltest as full

    annotations_sha256 = sha256_file(args.annotations)
    if annotations_sha256 != args.annotations_sha256:
        raise ValueError("annotations SHA-256 mismatch")
    annotations, records = full.load_test_split(args.annotations, args.images_root, verify_image_hashes=True)
    if len(records) != args.expected_images or len(annotations["annotations"]) != args.expected_annotations:
        raise ValueError(
            f"fulltest size mismatch: images={len(records)}, annotations={len(annotations['annotations'])}"
        )
    selected = set(args.model)
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "nanodet_sweep_om_fulltest",
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
        "atc_summary": {"path": str(args.atc_summary), "sha256": sha256_file(args.atc_summary)},
        "models": [],
    }
    report_path = output_dir / "om-evaluation-summary.json"
    expected_names = [item["name"] for item in manifest["models"] if item.get("group") == "sweep"]
    unknown = selected - set(expected_names)
    if unknown:
        raise ValueError(f"unknown or out-of-scope models: {sorted(unknown)}")
    target_names = [name for name in expected_names if not selected or name in selected]
    atc_by_name = {item["name"]: item for item in atc["models"]}
    report["expected_models"] = expected_names
    report["requested_models"] = target_names
    current: dict[str, dict[str, Any]] = {}
    for name in target_names:
        atc_item = atc_by_name.get(name)
        model_dir = output_dir / name
        model_dir.mkdir(parents=True, exist_ok=True)
        model_report: dict[str, Any] = {"name": name, "status": "running"}
        current[name] = model_report
        if atc_item is None:
            model_report.update({"status": "failed", "error": "missing ATC result"})
            report["models"] = [current[item] for item in target_names if item in current]
            write_json(report_path, report)
            continue
        if atc_item.get("status") != "success":
            model_report.update({
                "status": "failed",
                "error": f"ATC status is {atc_item.get('status')}",
                "atc_error": atc_item.get("reason"),
            })
            report["models"] = [current[item] for item in target_names if item in current]
            write_json(report_path, report)
            continue
        runner = None
        try:
            item = by_name[name]
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
            om_path = Path(atc_item["om_path"])
            if not om_path.is_file() or om_path.stat().st_size != atc_item.get("om_bytes"):
                raise ValueError("ATC OM bytes mismatch")
            observed_om_sha256 = sha256_file(om_path)
            if observed_om_sha256 != atc_item.get("om_sha256"):
                raise ValueError("ATC OM SHA-256 mismatch")
            model_report.update({
                "onnx_sha256": item["sha256"],
                "om_path": str(om_path),
                "om_bytes": om_path.stat().st_size,
                "om_sha256": observed_om_sha256,
                "contract": contract,
            })
            full.INPUT_SHAPE = contract["input_shape"]
            full.OUTPUT_SHAPE = contract["output_shape"]
            full.REG_MAX = contract["reg_max"]
            full.STRIDES = contract["strides"]
            full.PRIORS = priors(full.INPUT_SHAPE, full.STRIDES, contract["center_offset"])
            runner = AclRunner(om_path, full.INPUT_SHAPE, full.OUTPUT_SHAPE, args.device)
            import cv2
            warm_image = args.images_root / records[0].file_name
            warm = cv2.imread(str(warm_image), cv2.IMREAD_COLOR)
            if warm is None:
                raise ValueError(f"cannot read warmup image {warm_image}")
            warm_tensor = full.preprocess_bgr(warm)
            for _ in range(args.warmup):
                runner.run(warm_tensor)
            predictions = []
            timings = []
            for index, record in enumerate(records, start=1):
                image = cv2.imread(str(args.images_root / record.file_name), cv2.IMREAD_COLOR)
                if image is None or image.shape[:2] != (record.height, record.width):
                    raise ValueError(f"cannot decode {record.file_name}")
                tensor = full.preprocess_bgr(image)
                output, elapsed_ms = runner.run(tensor)
                detections = full.decode_output(output, image_width=record.width, image_height=record.height)
                predictions.extend(full.detections_to_coco(record.image_id, detections))
                timings.append(elapsed_ms)
                if index % 100 == 0:
                    print(f"{name}: {index}/{len(records)}", flush=True)
            predictions_path = model_dir / "predictions.coco.json"
            write_json(predictions_path, predictions)
            ordered = sorted(timings)
            model_report.update({
                "status": "completed",
                "predictions": {"path": str(predictions_path), "sha256": sha256_file(predictions_path), "count": len(predictions)},
                "task_metrics": full.task_metrics(annotations, predictions),
                "performance": {
                    "scope": "H2D + ACL execute + synchronize + D2H; excludes JPEG decode, preprocessing, DFL/NMS and metrics",
                    "warmup": args.warmup,
                    "count": len(timings),
                    "p50_ms": ordered[len(ordered) // 2],
                    "p95_ms": ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)],
                },
            })
        except BaseException as exc:  # noqa: BLE001 - persist each model failure
            model_report.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()})
        finally:
            if runner is not None:
                cleanup = runner.close()
                model_report["cleanup"] = cleanup
                model_report["cleanup_ok"] = all(item.get("ok") for item in cleanup)
                if not model_report["cleanup_ok"] and model_report["status"] == "completed":
                    model_report["status"] = "failed"
                    model_report["error"] = "ACL cleanup failed"
        report["models"] = [current[model_name] for model_name in target_names if model_name in current]
        write_json(report_path, report)
        print(f"{model_report['status']} {name}", flush=True)
    if _OWNS_RUNTIME:
        import acl
        runtime_cleanup: list[dict[str, Any]] = []
        cleanup_call(acl, runtime_cleanup, "set_device", acl.rt.set_device, args.device)
        cleanup_call(acl, runtime_cleanup, "reset_device", acl.rt.reset_device, args.device)
        cleanup_call(acl, runtime_cleanup, "finalize", acl.finalize)
        report["runtime_cleanup"] = runtime_cleanup
        report["runtime_cleanup_ok"] = all(item.get("ok") for item in runtime_cleanup)
    report["finished_at"] = now()
    report["models"] = [current[model_name] for model_name in target_names if model_name in current]
    write_json(report_path, report)
    failures = [item["name"] for item in report["models"] if item["status"] != "completed"]
    complete = len(report["models"]) == len(expected_names) and not failures
    print(json.dumps({
        "expected_models": len(expected_names),
        "observed_models": len(report["models"]),
        "failures": failures,
    }, ensure_ascii=False))
    return 0 if complete and report.get("runtime_cleanup_ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
