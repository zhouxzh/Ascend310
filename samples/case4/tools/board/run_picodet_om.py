#!/usr/bin/env python3
"""Run two trained hand PicoDet OMs over the fixed full held-out test split."""

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

_RUNTIME_OWNED = False
INPUT_REFERENCE_ATOL = 0.02
INPUT_REFERENCE_RTOL = 1e-5
OUTPUT_REFERENCE_ATOL = 0.05
OUTPUT_REFERENCE_RTOL = 0.05


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_array(value: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


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
    except BaseException as exc:  # noqa: BLE001 - retain cleanup evidence
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


class InputAgreement:
    """Aggregate board-versus-CUDA preprocessing agreement for fixed smoke images."""

    def __init__(self) -> None:
        self.samples = 0
        self.sha256_matches = 0
        self.elements = 0
        self.absolute_sum = 0.0
        self.max_abs_error = 0.0

    def add(self, value: dict[str, float]) -> None:
        self.samples += 1
        self.sha256_matches += int(bool(value["input_sha256_match"]))
        self.absolute_sum += float(value["input_absolute_sum"])
        self.elements += int(value["input_elements"])
        self.max_abs_error = max(self.max_abs_error, float(value["input_max_abs_error"]))

    def summary(self) -> dict[str, Any]:
        return {
            "samples": self.samples,
            "sha256_matches": self.sha256_matches,
            "sha256_match_rate": self.sha256_matches / self.samples if self.samples else None,
            "max_abs_error": self.max_abs_error if self.samples else None,
            "mean_abs_error": self.absolute_sum / self.elements if self.elements else None,
        }


class MultiOutputAgreement:
    """Aggregate bounded eight-output numerical evidence against CUDA reference values."""

    def __init__(self, *, output_count: int, atol: float, rtol: float) -> None:
        self.atol = float(atol)
        self.rtol = float(rtol)
        self.samples = 0
        self.allclose_samples = 0
        self.non_finite_samples = 0
        self.outputs = [
            {"elements": 0, "absolute_sum": 0.0, "max_abs_error": 0.0, "allclose_samples": 0}
            for _ in range(output_count)
        ]

    def compare(self, expected: tuple[np.ndarray, ...], actual: tuple[np.ndarray, ...]) -> None:
        if len(expected) != len(actual) or len(actual) != len(self.outputs):
            raise ValueError("reference/output count mismatch")
        sample_allclose = True
        for index, (golden, observed) in enumerate(zip(expected, actual)):
            golden = np.asarray(golden, dtype=np.float32)
            observed = np.asarray(observed, dtype=np.float32)
            if golden.shape != observed.shape:
                raise ValueError(f"reference/output shape mismatch at index {index}: {golden.shape} != {observed.shape}")
            if not np.isfinite(observed).all():
                self.non_finite_samples += 1
                raise ValueError(f"non-finite OM output at index {index}")
            error = np.abs(observed - golden)
            allclose = bool(np.allclose(observed, golden, atol=self.atol, rtol=self.rtol, equal_nan=False))
            result = self.outputs[index]
            result["elements"] += int(error.size)
            result["absolute_sum"] += float(error.sum(dtype=np.float64))
            result["max_abs_error"] = max(float(result["max_abs_error"]), float(error.max()))
            result["allclose_samples"] += int(allclose)
            sample_allclose = sample_allclose and allclose
        self.samples += 1
        self.allclose_samples += int(sample_allclose)

    def summary(self) -> dict[str, Any]:
        values = []
        for index, value in enumerate(self.outputs):
            values.append({
                "index": index,
                "elements": value["elements"],
                "max_abs_error": value["max_abs_error"] if self.samples else None,
                "mean_abs_error": value["absolute_sum"] / value["elements"] if value["elements"] else None,
                "allclose_samples": value["allclose_samples"],
                "allclose_rate": value["allclose_samples"] / self.samples if self.samples else None,
            })
        return {
            "samples": self.samples,
            "atol": self.atol,
            "rtol": self.rtol,
            "non_finite_samples": self.non_finite_samples,
            "allclose_samples": self.allclose_samples,
            "allclose_rate": self.allclose_samples / self.samples if self.samples else None,
            "outputs": values,
        }


class ReferenceSmoke:
    """Validate fixed preprocessed inputs and expose matching eight-output CUDA golden values."""

    def __init__(
        self,
        root: Path,
        *,
        model_name: str,
        onnx_sha256: str,
        annotations_sha256: str,
        input_shape: tuple[int, ...],
        output_shapes: tuple[tuple[int, ...], ...],
    ) -> None:
        root = root.resolve()
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("kind") != "picodet_palm_cuda_reference_smoke":
            raise ValueError("unsupported PicoDet numerical reference manifest")
        source = manifest.get("source", {})
        if source.get("annotations_sha256") != annotations_sha256:
            raise ValueError("reference annotation SHA-256 mismatch")
        selection = manifest.get("selection", {})
        if int(selection.get("count", 0)) <= 0:
            raise ValueError("reference selection is empty")
        entry = next((item for item in manifest.get("models", []) if item.get("name") == model_name), None)
        if entry is None:
            raise ValueError(f"reference model is missing: {model_name}")
        if entry.get("onnx_sha256") != onnx_sha256:
            raise ValueError("reference ONNX SHA-256 mismatch")
        if tuple(entry.get("input_shape", [])) != input_shape:
            raise ValueError("reference input shape mismatch")
        if tuple(tuple(value) for value in entry.get("output_shapes", [])) != output_shapes:
            raise ValueError("reference output shape mismatch")
        relative = Path(str(entry.get("path", "")))
        if not relative.name or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("unsafe reference pack path")
        pack_path = (root / relative).resolve()
        try:
            pack_path.relative_to(root)
        except ValueError as exc:
            raise ValueError("reference pack escapes root") from exc
        if not pack_path.is_file() or sha256_file(pack_path) != entry.get("sha256"):
            raise ValueError("reference pack integrity mismatch")
        self.manifest_path = manifest_path
        self.manifest_sha256 = sha256_file(manifest_path)
        self.pack_path = pack_path
        self.pack_sha256 = sha256_file(pack_path)
        self.image_to_index: dict[int, int] = {}
        self.input_hashes: list[str] = []
        self.inputs: np.ndarray
        self.outputs: tuple[np.ndarray, ...]
        with np.load(pack_path, allow_pickle=False) as values:
            image_ids = values["image_ids"]
            input_hashes = values["input_sha256"]
            expected_count = int(selection["count"])
            if image_ids.shape != (expected_count,) or input_hashes.shape != (expected_count,):
                raise ValueError("reference image/input hash shape mismatch")
            self.image_to_index = {int(value): index for index, value in enumerate(image_ids)}
            if len(self.image_to_index) != expected_count:
                raise ValueError("reference image IDs are not unique")
            self.input_hashes = [str(value) for value in input_hashes]
            inputs = np.asarray(values["inputs"], dtype=np.float32)
            if inputs.shape != (expected_count, *input_shape[1:]) or not np.isfinite(inputs).all():
                raise ValueError(f"reference input contract mismatch: {inputs.shape}")
            self.inputs = inputs
            arrays = []
            for index, output_shape in enumerate(output_shapes):
                key = f"output_{index}"
                if key not in values:
                    raise ValueError(f"reference output is missing: {key}")
                value = np.asarray(values[key], dtype=np.float32)
                if value.shape != (expected_count, *output_shape[1:]) or not np.isfinite(value).all():
                    raise ValueError(f"reference output contract mismatch: {key} {value.shape}")
                arrays.append(value)
            self.outputs = tuple(arrays)
        self.seen: set[int] = set()

    def expected(self, image_id: int, tensor: np.ndarray) -> tuple[tuple[np.ndarray, ...], dict[str, float]] | None:
        index = self.image_to_index.get(int(image_id))
        if index is None:
            return None
        observed = np.asarray(tensor, dtype=np.float32)
        golden = np.ascontiguousarray(self.inputs[index][None, ...], dtype=np.float32)
        if observed.shape != golden.shape:
            raise ValueError(f"board preprocessing shape mismatch for image {image_id}")
        error = np.abs(observed - golden)
        max_error = float(error.max())
        mean_error = float(error.mean(dtype=np.float64))
        if not np.allclose(observed, golden, atol=INPUT_REFERENCE_ATOL, rtol=INPUT_REFERENCE_RTOL, equal_nan=False):
            raise ValueError(
                f"board preprocessing differs from CUDA reference beyond tolerance for image {image_id}: "
                f"max_abs={max_error}, mean_abs={mean_error}"
            )
        self.seen.add(int(image_id))
        return (
            tuple(np.ascontiguousarray(value[index][None, ...], dtype=np.float32) for value in self.outputs),
            {
                "input_max_abs_error": max_error,
                "input_mean_abs_error": mean_error,
                "input_absolute_sum": float(error.sum(dtype=np.float64)),
                "input_elements": float(error.size),
                "input_sha256_match": float(sha256_array(observed) == self.input_hashes[index]),
            },
        )

    def finish(self) -> None:
        if set(self.image_to_index) != self.seen:
            raise ValueError(f"reference coverage mismatch: {len(self.seen)}/{len(self.image_to_index)}")


class AclMultiOutputRunner:
    """One-input/eight-output ACL runner with explicit buffer lifecycle evidence."""

    def __init__(
        self,
        model: Path,
        expected_input: tuple[int, ...],
        expected_outputs: tuple[tuple[int, ...], ...],
        device: int,
    ) -> None:
        import acl

        global _RUNTIME_OWNED
        self.acl = acl
        self.device = device
        self.context = None
        self.model_id = None
        self.desc = None
        self.input_dataset = None
        self.output_dataset = None
        self.input_pointer = None
        self.input_buffer = None
        self.output_pointers: list[Any] = []
        self.output_buffers: list[Any] = []
        self.output_sizes: list[int] = []
        self.output_shapes: tuple[tuple[int, ...], ...] = ()
        self.output_dtypes: tuple[np.dtype, ...] = ()
        self.cleanup: list[dict[str, Any]] = []
        self.owns_runtime = False
        try:
            init = acl.init()
            code = init[-1] if isinstance(init, tuple) else init
            if int(code) not in (0, 100002):
                raise RuntimeError(f"acl.init failed: {code}")
            self.owns_runtime = int(code) == 0
            if self.owns_runtime:
                _RUNTIME_OWNED = True
            unwrap(acl.rt.set_device(device), "acl.rt.set_device")
            self.context = unwrap(acl.rt.create_context(device), "acl.rt.create_context")
            self.model_id = unwrap(acl.mdl.load_from_file(str(model)), "acl.mdl.load_from_file")
            self.desc = acl.mdl.create_desc()
            unwrap(acl.mdl.get_desc(self.desc, self.model_id), "acl.mdl.get_desc")
            if int(acl.mdl.get_num_inputs(self.desc)) != 1:
                raise ValueError("PicoDet runner requires one input")
            if int(acl.mdl.get_num_outputs(self.desc)) != len(expected_outputs):
                raise ValueError("PicoDet OM must expose eight outputs")
            self.input_shape = tensor_dims(acl, self.desc, acl.mdl.get_input_dims, 0)
            self.input_dtype = tensor_dtype(acl, acl.mdl.get_input_data_type(self.desc, 0))
            self.output_shapes = tuple(
                tensor_dims(acl, self.desc, acl.mdl.get_output_dims, index)
                for index in range(len(expected_outputs))
            )
            self.output_dtypes = tuple(
                tensor_dtype(acl, acl.mdl.get_output_data_type(self.desc, index))
                for index in range(len(expected_outputs))
            )
            if self.input_shape != expected_input or self.output_shapes != expected_outputs:
                raise ValueError(
                    f"OM shape contract mismatch: input={self.input_shape}, outputs={self.output_shapes}"
                )
            if self.input_dtype != np.dtype(np.float32) or any(value != np.dtype(np.float32) for value in self.output_dtypes):
                raise ValueError(f"OM dtype contract mismatch: input={self.input_dtype}, outputs={self.output_dtypes}")
            self.input_size = int(acl.mdl.get_input_size_by_index(self.desc, 0))
            expected_input_bytes = int(np.prod(expected_input)) * self.input_dtype.itemsize
            if self.input_size != expected_input_bytes:
                raise ValueError("OM input byte contract mismatch")
            self.output_sizes = [int(acl.mdl.get_output_size_by_index(self.desc, index)) for index in range(len(expected_outputs))]
            for index, (shape, dtype, size) in enumerate(zip(expected_outputs, self.output_dtypes, self.output_sizes)):
                if size != int(np.prod(shape)) * dtype.itemsize:
                    raise ValueError(f"OM output byte contract mismatch at index {index}")
            self.input_dataset = acl.mdl.create_dataset()
            self.output_dataset = acl.mdl.create_dataset()
            self.input_pointer = unwrap(acl.rt.malloc(self.input_size, 0), "acl.rt.malloc input")
            self.input_buffer = acl.create_data_buffer(self.input_pointer, self.input_size)
            if self.input_buffer is None:
                raise RuntimeError("acl.create_data_buffer input returned no buffer")
            unwrap(acl.mdl.add_dataset_buffer(self.input_dataset, self.input_buffer), "add input buffer")
            for index, size in enumerate(self.output_sizes):
                pointer = unwrap(acl.rt.malloc(size, 0), f"acl.rt.malloc output {index}")
                self.output_pointers.append(pointer)
                buffer = acl.create_data_buffer(pointer, size)
                if buffer is None:
                    raise RuntimeError(f"acl.create_data_buffer output {index} returned no buffer")
                self.output_buffers.append(buffer)
                unwrap(acl.mdl.add_dataset_buffer(self.output_dataset, buffer), f"add output buffer {index}")
        except BaseException:
            self.close()
            raise

    def run(self, tensor: np.ndarray) -> tuple[tuple[np.ndarray, ...], float]:
        value = np.ascontiguousarray(tensor, dtype=np.float32)
        if tuple(value.shape) != self.input_shape or value.nbytes != self.input_size:
            raise ValueError(f"input contract mismatch: {value.shape}, {value.nbytes}")
        unwrap(self.acl.rt.set_context(self.context), "acl.rt.set_context")
        started = time.perf_counter_ns()
        unwrap(
            self.acl.rt.memcpy(
                self.input_pointer,
                self.input_size,
                host_pointer(self.acl, value),
                value.nbytes,
                int(getattr(self.acl, "ACL_MEMCPY_HOST_TO_DEVICE", 1)),
            ),
            "host_to_device",
        )
        unwrap(self.acl.mdl.execute(self.model_id, self.input_dataset, self.output_dataset), "acl.mdl.execute")
        synchronize = getattr(self.acl.rt, "synchronize_device", None)
        if callable(synchronize):
            unwrap(synchronize(), "acl.rt.synchronize_device")
        outputs: list[np.ndarray] = []
        for index, (shape, pointer, size) in enumerate(zip(self.output_shapes, self.output_pointers, self.output_sizes)):
            output = np.empty(shape, dtype=np.float32)
            unwrap(
                self.acl.rt.memcpy(
                    host_pointer(self.acl, output),
                    output.nbytes,
                    pointer,
                    size,
                    int(getattr(self.acl, "ACL_MEMCPY_DEVICE_TO_HOST", 2)),
                ),
                f"device_to_host output {index}",
            )
            outputs.append(output)
        return tuple(outputs), (time.perf_counter_ns() - started) / 1e6

    def close(self) -> list[dict[str, Any]]:
        acl = getattr(self, "acl", None)
        if acl is None:
            return []
        if self.context is not None:
            cleanup_call(acl, self.cleanup, "set_context", acl.rt.set_context, self.context)
        for index, buffer in reversed(list(enumerate(self.output_buffers))):
            cleanup_call(acl, self.cleanup, f"destroy_output_buffer_{index}", acl.destroy_data_buffer, buffer)
        self.output_buffers = []
        if self.input_buffer is not None:
            cleanup_call(acl, self.cleanup, "destroy_input_buffer", acl.destroy_data_buffer, self.input_buffer)
            self.input_buffer = None
        if self.output_dataset is not None:
            cleanup_call(acl, self.cleanup, "destroy_output_dataset", acl.mdl.destroy_dataset, self.output_dataset)
            self.output_dataset = None
        if self.input_dataset is not None:
            cleanup_call(acl, self.cleanup, "destroy_input_dataset", acl.mdl.destroy_dataset, self.input_dataset)
            self.input_dataset = None
        for index, pointer in reversed(list(enumerate(self.output_pointers))):
            cleanup_call(acl, self.cleanup, f"free_output_{index}", acl.rt.free, pointer)
        self.output_pointers = []
        if self.input_pointer is not None:
            cleanup_call(acl, self.cleanup, "free_input", acl.rt.free, self.input_pointer)
            self.input_pointer = None
        if self.desc is not None:
            cleanup_call(acl, self.cleanup, "destroy_desc", acl.mdl.destroy_desc, self.desc)
            self.desc = None
        if self.model_id is not None:
            cleanup_call(acl, self.cleanup, "unload_model", acl.mdl.unload, self.model_id)
            self.model_id = None
        if self.context is not None:
            cleanup_call(acl, self.cleanup, "destroy_context", acl.rt.destroy_context, self.context)
            self.context = None
        if self.owns_runtime:
            self.cleanup.append({"phase": "runtime_retained_for_batch", "ok": True, "status": "shared_runtime"})
            self.owns_runtime = False
        return self.cleanup


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--contracts", default="contracts.json")
    parser.add_argument("--atc-summary", type=Path, required=True)
    parser.add_argument("--reference-root", type=Path, required=True)
    parser.add_argument("--soc-version", required=True, choices=("Ascend310B1", "Ascend310B4"))
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--annotations-sha256", required=True)
    parser.add_argument("--images-root", type=Path, required=True)
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
    contracts_path = bundle / args.contracts
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    contracts = json.loads(contracts_path.read_text(encoding="utf-8"))
    if manifest.get("kind") != "picodet_palm_onnx_bundle" or contracts.get("kind") != "picodet_palm_onnx_contracts":
        raise ValueError("unsupported PicoDet bundle")
    models = manifest.get("models", [])
    expected_names = [item.get("name") for item in models]
    if len(expected_names) != 2 or set(expected_names) != {"picodet-xs-320-seed-42", "picodet-xs-416-seed-42"}:
        raise ValueError("unexpected PicoDet manifest model set")
    contracts_by_name = {item["name"]: item for item in contracts.get("models", [])}
    if set(contracts_by_name) != set(expected_names):
        raise ValueError("contract model set differs from manifest")
    manifest_sha256 = sha256_file(manifest_path)
    atc = json.loads(args.atc_summary.read_text(encoding="utf-8"))
    if atc.get("kind") != "picodet_palm_atc":
        raise ValueError("unexpected ATC summary kind")
    recipe = atc.get("recipe", {})
    if recipe.get("manifest_sha256") != manifest_sha256:
        raise ValueError("ATC summary was produced from a different manifest")
    if recipe.get("soc_version") != args.soc_version or recipe.get("precision_mode") != "allow_mix_precision":
        raise ValueError("ATC recipe does not match this board test")
    selected = set(args.model)
    if selected - set(expected_names):
        raise ValueError(f"unknown PicoDet model: {sorted(selected - set(expected_names))}")
    target_names = [name for name in expected_names if not selected or name in selected]
    tools_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(tools_root / "offline"))
    from handed_nanodet_fulltest import detections_to_coco, load_test_split, task_metrics
    from handed_picodet_fulltest import decode_outputs, preprocess_bgr, validate_contract
    import cv2

    annotations_sha256 = sha256_file(args.annotations)
    if annotations_sha256 != args.annotations_sha256:
        raise ValueError("annotations SHA-256 mismatch")
    annotations, records = load_test_split(args.annotations, args.images_root, verify_image_hashes=True)
    if len(records) != args.expected_images or len(annotations["annotations"]) != args.expected_annotations:
        raise ValueError(f"fulltest size mismatch: images={len(records)}, annotations={len(annotations['annotations'])}")
    output_dir = args.output_dir.resolve()
    reference_root = args.reference_root.resolve()
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "picodet_palm_om_fulltest",
        "started_at": now(),
        "dataset": {
            "images": len(records),
            "annotations": len(annotations["annotations"]),
            "annotations_sha256": annotations_sha256,
            "image_ids_sha256": hashlib.sha256(json.dumps(sorted(record.image_id for record in records), separators=(",", ":")).encode()).hexdigest(),
        },
        "manifest": {"path": str(manifest_path), "sha256": manifest_sha256},
        "contracts_sha256": sha256_file(contracts_path),
        "atc_summary": {"path": str(args.atc_summary), "sha256": sha256_file(args.atc_summary)},
        "atc_recipe": atc.get("recipe"),
        "expected_models": expected_names,
        "requested_models": target_names,
        "models": [],
    }
    atc_by_name = {item["name"]: item for item in atc.get("models", [])}
    report_path = output_dir / "om-evaluation-summary.json"
    current: dict[str, dict[str, Any]] = {}
    for name in target_names:
        model_report: dict[str, Any] = {"name": name, "status": "running"}
        current[name] = model_report
        runner = None
        try:
            item = next(value for value in models if value["name"] == name)
            declared = contracts_by_name[name]
            if declared.get("sha256") != item.get("sha256"):
                raise ValueError("contract ONNX SHA does not match manifest")
            contract = validate_contract(declared)
            atc_item = atc_by_name.get(name)
            if atc_item is None or atc_item.get("status") != "success":
                raise ValueError(f"ATC status is {None if atc_item is None else atc_item.get('status')}")
            om_path = Path(atc_item["om_path"])
            if not om_path.is_file() or om_path.stat().st_size != atc_item.get("om_bytes"):
                raise ValueError("ATC OM bytes mismatch")
            observed_om_sha256 = sha256_file(om_path)
            if observed_om_sha256 != atc_item.get("om_sha256"):
                raise ValueError("ATC OM SHA-256 mismatch")
            reference = ReferenceSmoke(
                reference_root,
                model_name=name,
                onnx_sha256=item["sha256"],
                annotations_sha256=annotations_sha256,
                input_shape=contract.input_shape,
                output_shapes=contract.output_shapes,
            )
            input_agreement = InputAgreement()
            agreement = MultiOutputAgreement(output_count=8, atol=OUTPUT_REFERENCE_ATOL, rtol=OUTPUT_REFERENCE_RTOL)
            runner = AclMultiOutputRunner(om_path, contract.input_shape, contract.output_shapes, args.device)
            warm = cv2.imread(str(args.images_root / records[0].file_name), cv2.IMREAD_COLOR)
            if warm is None:
                raise ValueError("cannot read warmup image")
            warm_tensor = preprocess_bgr(warm, contract)
            for _ in range(args.warmup):
                runner.run(warm_tensor)
            predictions: list[dict[str, Any]] = []
            timings: list[float] = []
            for index, record in enumerate(records, start=1):
                image = cv2.imread(str(args.images_root / record.file_name), cv2.IMREAD_COLOR)
                if image is None or image.shape[:2] != (record.height, record.width):
                    raise ValueError(f"cannot decode {record.file_name}")
                tensor = preprocess_bgr(image, contract)
                raw_outputs, elapsed_ms = runner.run(tensor)
                reference_item = reference.expected(record.image_id, tensor)
                if reference_item is not None:
                    expected_outputs, input_item = reference_item
                    input_agreement.add(input_item)
                    agreement.compare(expected_outputs, raw_outputs)
                detections = decode_outputs(raw_outputs, contract, image_width=record.width, image_height=record.height)
                predictions.extend(detections_to_coco(record.image_id, detections))
                timings.append(elapsed_ms)
                if index % 100 == 0:
                    print(f"{name}: {index}/{len(records)}", flush=True)
            reference.finish()
            numerical_gate_ok = agreement.allclose_samples == agreement.samples
            prediction_path = output_dir / name / "predictions.coco.json"
            write_json(prediction_path, predictions)
            ordered = sorted(timings)
            model_report.update({
                "status": "completed",
                "onnx_sha256": item["sha256"],
                "om_path": str(om_path),
                "om_bytes": om_path.stat().st_size,
                "om_sha256": observed_om_sha256,
                "numerical_gate_ok": numerical_gate_ok,
                "reference_smoke": {
                    "manifest_path": str(reference.manifest_path),
                    "manifest_sha256": reference.manifest_sha256,
                    "pack_path": str(reference.pack_path),
                    "pack_sha256": reference.pack_sha256,
                    "preprocess_agreement": {
                        "atol": INPUT_REFERENCE_ATOL,
                        "rtol": INPUT_REFERENCE_RTOL,
                        **input_agreement.summary(),
                    },
                    "agreement": agreement.summary(),
                },
                "contract": {
                    "input_shape": list(contract.input_shape),
                    "output_shapes": [list(shape) for shape in contract.output_shapes],
                    "reg_max": 7,
                    "strides": [8, 16, 32, 64],
                    "cell_offset": 0.5,
                },
                "predictions": {
                    "path": str(prediction_path),
                    "relative_path": str(prediction_path.relative_to(output_dir)),
                    "sha256": sha256_file(prediction_path),
                    "count": len(predictions),
                },
                "task_metrics": task_metrics(annotations, predictions),
                "performance": {
                    "scope": "H2D + ACL execute + synchronize + D2H for all eight outputs; excludes JPEG decode, preprocessing, DFL/NMS and metrics",
                    "warmup": args.warmup,
                    "count": len(timings),
                    "p50_ms": ordered[len(ordered) // 2],
                    "p95_ms": ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)],
                    "mean_ms": sum(timings) / len(timings),
                },
            })
        except BaseException as exc:  # noqa: BLE001 - persist all per-model failures
            model_report.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}", "traceback": traceback.format_exc()})
        finally:
            if runner is not None:
                cleanup = runner.close()
                model_report["cleanup"] = cleanup
                model_report["cleanup_ok"] = all(event.get("ok") for event in cleanup)
                if not model_report["cleanup_ok"] and model_report["status"] == "completed":
                    model_report.update({"status": "failed", "error": "ACL cleanup failed"})
        report["models"] = [current[key] for key in target_names if key in current]
        write_json(report_path, report)
        print(f"{model_report['status']} {name}", flush=True)
    if _RUNTIME_OWNED:
        import acl
        runtime_cleanup: list[dict[str, Any]] = []
        cleanup_call(acl, runtime_cleanup, "set_device", acl.rt.set_device, args.device)
        cleanup_call(acl, runtime_cleanup, "reset_device", acl.rt.reset_device, args.device)
        cleanup_call(acl, runtime_cleanup, "finalize", acl.finalize)
        report["runtime_cleanup"] = runtime_cleanup
        report["runtime_cleanup_ok"] = all(event.get("ok") for event in runtime_cleanup)
    report["finished_at"] = now()
    report["models"] = [current[key] for key in target_names if key in current]
    write_json(report_path, report)
    failures = [item["name"] for item in report["models"] if item["status"] != "completed"]
    complete = len(report["models"]) == len(expected_names) and not failures
    print(json.dumps({"expected_models": len(expected_names), "observed_models": len(report["models"]), "failures": failures}, ensure_ascii=False))
    return 0 if complete and report.get("runtime_cleanup_ok", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
