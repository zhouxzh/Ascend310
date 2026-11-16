#!/usr/bin/env python3
"""Unified ONNX-reference and ACL-OM benchmark for static detector models."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


WARMUP_DEFAULT = 30
LOOPS_DEFAULT = 100
REPEATS_DEFAULT = 5
SEED_DEFAULT = 20261113
PRECISION_TOLERANCES = {
    "fp32": {"rtol": 0.001, "atol": 0.001},
    "mixed_fp16": {"rtol": 0.03, "atol": 0.06},
}
SUMMARY_FIELDS = [
    "board", "soc", "model_id", "precision", "status", "input_shape",
    "output_count", "max_abs_error", "mean_abs_error", "rtol", "atol",
    "p50_ms", "p95_ms", "fps_from_p50", "warmup", "loops_per_repeat",
    "repeats", "onnx_sha256", "om_sha256", "reference_sha256", "report",
]


def timestamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_array(value: np.ndarray) -> str:
    array = np.ascontiguousarray(value)
    return hashlib.sha256(array.view(np.uint8)).hexdigest()


def write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_suite(path: Path) -> Dict[str, Any]:
    suite = json.loads(path.read_text(encoding="utf-8"))
    if suite.get("schema_version") != 1 or not isinstance(suite.get("models"), list):
        raise ValueError("unsupported suite manifest")
    return suite


def environment_info() -> Dict[str, Any]:
    import acl
    cann_version = None
    for candidate in (
        Path("/usr/local/Ascend/ascend-toolkit/latest/version.cfg"),
        Path("/usr/local/Ascend/ascend-toolkit/latest/version.info"),
    ):
        if candidate.is_file():
            cann_version = candidate.read_text(encoding="utf-8", errors="replace").strip()[:4000]
            break
    return {
        "python": sys.version,
        "python_executable": sys.executable,
        "platform": platform.platform(),
        "numpy": np.__version__,
        "conda_default_env": os.environ.get("CONDA_DEFAULT_ENV"),
        "acl_origin": getattr(acl, "__file__", None),
        "cann_version_file": cann_version,
        "cann_home": os.environ.get("ASCEND_HOME_PATH"),
    }


def validate_board_environment() -> None:
    if os.environ.get("CONDA_DEFAULT_ENV") != "ascend":
        raise RuntimeError("CONDA_DEFAULT_ENV must be ascend")
    if os.environ.get("PYTHONNOUSERSITE") != "1":
        raise RuntimeError("PYTHONNOUSERSITE must be 1")


def prepare_references(args: argparse.Namespace) -> int:
    suite = load_suite(args.suite)
    import onnxruntime as ort

    args.output_dir.mkdir(parents=True, exist_ok=True)
    entries: Dict[str, Any] = {}
    for model in suite["models"]:
        model_id = model["id"]
        onnx_by_board = model["onnx_path"]
        onnx_path = Path(onnx_by_board[args.onnx_board])
        if not onnx_path.is_file():
            raise FileNotFoundError("ONNX not found for %s: %s" % (model_id, onnx_path))
        actual_onnx_hash = sha256_file(onnx_path)
        expected_hash = model.get("onnx_sha256")
        if expected_hash and actual_onnx_hash != expected_hash:
            raise ValueError("ONNX hash mismatch for %s: %s" % (model_id, actual_onnx_hash))
        session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
        inputs = session.get_inputs()
        if len(inputs) != 1:
            raise ValueError("%s must have exactly one ONNX input, got %d" % (model_id, len(inputs)))
        meta = inputs[0]
        input_type_map = {"tensor(float)": np.float32, "tensor(float16)": np.float16}
        if meta.type not in input_type_map or not all(isinstance(dim, int) and dim > 0 for dim in meta.shape):
            raise ValueError("%s input must be static float32/float16, got %r %s" % (model_id, meta.type, meta.shape))
        shape = tuple(int(dim) for dim in meta.shape)
        model_seed = (args.seed + int(actual_onnx_hash[:8], 16)) % (2 ** 32)
        rng = np.random.default_rng(model_seed)
        tensor = rng.random(shape, dtype=np.float32).astype(input_type_map[meta.type])
        outputs = session.run(None, {meta.name: tensor})
        if not outputs:
            raise ValueError("%s ONNX produced no outputs" % model_id)
        for index, output in enumerate(outputs):
            if not isinstance(output, np.ndarray) or not np.issubdtype(output.dtype, np.number):
                raise TypeError("%s output %d is not a numeric ndarray" % (model_id, index))
        reference_path = args.output_dir / (model_id + ".npz")
        arrays = {"input": tensor}
        arrays.update({"output_%03d" % index: output for index, output in enumerate(outputs)})
        np.savez_compressed(reference_path, **arrays)
        entries[model_id] = {
            "path": str(reference_path),
            "bytes": reference_path.stat().st_size,
            "sha256": sha256_file(reference_path),
            "input": {
                "name": meta.name,
                "shape": list(shape),
                "dtype": str(tensor.dtype),
                "sha256": sha256_array(tensor),
                "seed": model_seed,
                "min": float(np.min(tensor)),
                "max": float(np.max(tensor)),
            },
            "outputs": [
                {
                    "shape": list(output.shape),
                    "dtype": str(output.dtype),
                    "sha256": sha256_array(output),
                    "finite": bool(np.isfinite(output).all()),
                }
                for output in outputs
            ],
            "onnx": {
                "path": str(onnx_path),
                "bytes": onnx_path.stat().st_size,
                "sha256": actual_onnx_hash,
            },
        }
        print("prepared %s input=%s outputs=%d" % (model_id, shape, len(outputs)), flush=True)
    manifest = {
        "schema_version": 1,
        "kind": "shared_synthetic_onnx_reference",
        "created_at": timestamp(),
        "generator_board": args.onnx_board,
        "generator_runtime": {"onnxruntime": ort.__version__, "numpy": np.__version__, "python": sys.version},
        "seed_base": args.seed,
        "input_protocol": "independent fixed-seed uniform[0,1) NCHW tensor per exact ONNX SHA-256, stored in its declared float32/float16 input dtype; no image preprocessing",
        "contains_biometric_data": False,
        "models": entries,
    }
    write_json(args.manifest, manifest)
    print(json.dumps({"manifest": str(args.manifest), "model_count": len(entries)}, indent=2))
    return 0


def unwrap(value: Any, name: str) -> Any:
    if isinstance(value, tuple):
        if len(value) != 2:
            raise RuntimeError("%s returned unexpected tuple %r" % (name, value))
        payload, status = value
        if status != 0:
            raise RuntimeError("%s failed with status %r" % (name, status))
        return payload
    if value not in (None, 0):
        raise RuntimeError("%s failed with status %r" % (name, value))
    return value


def acl_dims(acl: Any, desc: Any, getter: Any, index: int) -> Tuple[int, ...]:
    result = unwrap(getter(desc, index), "get tensor dimensions")
    dims = tuple(int(value) for value in result["dims"][: int(result["dimCount"])])
    if not dims or any(value <= 0 for value in dims):
        raise ValueError("invalid ACL dimensions %r" % (result,))
    return dims


def acl_dtype(acl: Any, value: Any) -> np.dtype:
    mapping = {
        int(getattr(acl, "ACL_FLOAT", 0)): np.dtype(np.float32),
        int(getattr(acl, "ACL_FLOAT16", 1)): np.dtype(np.float16),
    }
    if int(value) not in mapping:
        raise ValueError("unsupported ACL tensor dtype %s" % value)
    return mapping[int(value)]


def host_pointer(acl: Any, value: np.ndarray) -> Any:
    converter = getattr(getattr(acl, "util", None), "numpy_to_ptr", None)
    return converter(value) if callable(converter) else value.ctypes.data


def cleanup_call(steps: List[Dict[str, Any]], phase: str, operation: Any, *args: Any) -> None:
    event: Dict[str, Any] = {"phase": phase, "timestamp": timestamp()}
    try:
        unwrap(operation(*args), phase)
        event["ok"] = True
    except BaseException as exc:
        event.update({"ok": False, "error": "%s: %s" % (type(exc).__name__, exc)})
    steps.append(event)


def compare_outputs(actual: Sequence[np.ndarray], expected: Sequence[np.ndarray], atol: float, rtol: float) -> List[Dict[str, Any]]:
    comparisons: List[Dict[str, Any]] = []
    for index, (actual_value, expected_value) in enumerate(zip(actual, expected)):
        a = actual_value.astype(np.float32, copy=False)
        e = expected_value.astype(np.float32, copy=False)
        item: Dict[str, Any] = {
            "index": index,
            "actual_shape": list(a.shape),
            "expected_shape": list(e.shape),
            "actual_dtype": str(actual_value.dtype),
            "expected_dtype": str(expected_value.dtype),
            "actual_finite": bool(np.isfinite(a).all()),
            "expected_finite": bool(np.isfinite(e).all()),
        }
        if a.shape != e.shape:
            item.update({"passed": False, "reason": "shape_mismatch"})
        elif not item["actual_finite"] or not item["expected_finite"]:
            item.update({"passed": False, "reason": "non_finite"})
        else:
            error = np.abs(a - e)
            item.update({
                "passed": bool(np.allclose(a, e, atol=atol, rtol=rtol, equal_nan=False)),
                "max_abs_error": float(np.max(error)),
                "mean_abs_error": float(np.mean(error)),
                "max_rel_error": float(np.max(error / np.maximum(np.abs(e), 1e-12))),
            })
        comparisons.append(item)
    if len(actual) != len(expected):
        comparisons.append({
            "index": "count",
            "passed": False,
            "reason": "output_count_mismatch",
            "actual_count": len(actual),
            "expected_count": len(expected),
        })
    return comparisons


def run_one(args: argparse.Namespace) -> int:
    report: Dict[str, Any] = {
        "schema_version": 1,
        "kind": "unified_static_detector_onnx_om_benchmark",
        "started_at": timestamp(),
        "board": args.board,
        "soc": args.soc,
        "model_id": args.model_id,
        "precision": args.precision,
        "backend": "PyACL",
        "status": "failed",
        "tolerance": PRECISION_TOLERANCES[args.precision],
        "protocol": {
            "warmup": args.warmup,
            "loops_per_repeat": args.loops,
            "repeats": args.repeats,
            "timing_scope": "H2D + ACL execute + device sync + D2H for all outputs",
            "postprocessing": False,
        },
        "environment": {},
        "artifacts": {},
        "cleanup": [],
    }
    cleanup: List[Dict[str, Any]] = report["cleanup"]
    failures: List[str] = []
    acl = None
    acl_initialized = False
    context = None
    model_id = None
    desc = None
    input_dataset = None
    output_dataset = None
    allocations: List[Dict[str, Any]] = []
    try:
        validate_board_environment()
        if not args.om.is_file():
            raise FileNotFoundError("OM not found: %s" % args.om)
        if not args.onnx.is_file():
            raise FileNotFoundError("ONNX not found: %s" % args.onnx)
        if not args.reference.is_file():
            raise FileNotFoundError("reference not found: %s" % args.reference)
        input_file = np.load(args.reference, allow_pickle=False)
        input_tensor = input_file["input"]
        expected = [input_file[key] for key in sorted(key for key in input_file.files if key.startswith("output_"))]
        report["artifacts"] = {
            "onnx": {"path": str(args.onnx), "bytes": args.onnx.stat().st_size, "sha256": sha256_file(args.onnx)},
            "om": {"path": str(args.om), "bytes": args.om.stat().st_size, "sha256": sha256_file(args.om)},
            "reference": {"path": str(args.reference), "bytes": args.reference.stat().st_size, "sha256": sha256_file(args.reference)},
            "input": {"shape": list(input_tensor.shape), "dtype": str(input_tensor.dtype), "sha256": sha256_array(input_tensor)},
        }
        if args.expected_onnx_sha256 and report["artifacts"]["onnx"]["sha256"] != args.expected_onnx_sha256:
            raise ValueError("ONNX SHA-256 does not match suite manifest")
        if args.expected_reference_sha256 and report["artifacts"]["reference"]["sha256"] != args.expected_reference_sha256:
            raise ValueError("reference SHA-256 does not match reference manifest")

        import acl as acl_module

        report["environment"] = environment_info()
        report["environment"]["python_no_user_site"] = os.environ.get("PYTHONNOUSERSITE")
        report["environment"]["numpy"] = np.__version__
        acl = acl_module
        unwrap(acl.init(), "acl.init")
        acl_initialized = True
        device = args.device
        unwrap(acl.rt.set_device(device), "acl.rt.set_device")
        context = unwrap(acl.rt.create_context(device), "acl.rt.create_context")
        model_id = unwrap(acl.mdl.load_from_file(str(args.om)), "acl.mdl.load_from_file")
        desc = acl.mdl.create_desc()
        if desc is None:
            raise RuntimeError("acl.mdl.create_desc returned None")
        unwrap(acl.mdl.get_desc(desc, model_id), "acl.mdl.get_desc")
        input_count = int(acl.mdl.get_num_inputs(desc))
        output_count = int(acl.mdl.get_num_outputs(desc))
        if input_count != 1:
            raise ValueError("expected one OM input, got %d" % input_count)
        input_shape = acl_dims(acl, desc, acl.mdl.get_input_dims, 0)
        input_dtype = acl_dtype(acl, acl.mdl.get_input_data_type(desc, 0))
        output_shapes = [acl_dims(acl, desc, acl.mdl.get_output_dims, index) for index in range(output_count)]
        output_dtypes = [acl_dtype(acl, acl.mdl.get_output_data_type(desc, index)) for index in range(output_count)]
        if len(expected) != output_count:
            raise ValueError("OM output count %d differs from ONNX reference %d" % (output_count, len(expected)))
        value = np.ascontiguousarray(input_tensor, dtype=input_dtype)
        if tuple(value.shape) != input_shape:
            raise ValueError("input tensor shape %r, OM expects %r" % (value.shape, input_shape))
        input_size = int(acl.mdl.get_input_size_by_index(desc, 0))
        if value.nbytes != input_size:
            raise ValueError("input bytes %d, OM expects %d" % (value.nbytes, input_size))
        for index, (shape, dtype, expected_output) in enumerate(zip(output_shapes, output_dtypes, expected)):
            if tuple(shape) != tuple(expected_output.shape):
                raise ValueError("output %d shape %r differs from ONNX %r" % (index, shape, expected_output.shape))
        input_dataset = acl.mdl.create_dataset()
        output_dataset = acl.mdl.create_dataset()
        if input_dataset is None or output_dataset is None:
            raise RuntimeError("ACL dataset creation failed")
        input_pointer = unwrap(acl.rt.malloc(input_size, 0), "acl.rt.malloc input")
        allocations.append({"pointer": input_pointer, "buffer": None})
        input_buffer = acl.create_data_buffer(input_pointer, input_size)
        if input_buffer is None:
            raise RuntimeError("ACL input data buffer creation failed")
        allocations[0]["buffer"] = input_buffer
        unwrap(acl.mdl.add_dataset_buffer(input_dataset, input_buffer), "acl.mdl.add_dataset_buffer input")
        output_hosts: List[np.ndarray] = []
        for index in range(output_count):
            size = int(acl.mdl.get_output_size_by_index(desc, index))
            host = np.empty(output_shapes[index], dtype=output_dtypes[index])
            if host.nbytes != size:
                raise ValueError("output %d host bytes %d, OM expects %d" % (index, host.nbytes, size))
            pointer = unwrap(acl.rt.malloc(size, 0), "acl.rt.malloc output %d" % index)
            allocation = {"pointer": pointer, "buffer": None, "size": size}
            allocations.append(allocation)
            data_buffer = acl.create_data_buffer(pointer, size)
            if data_buffer is None:
                raise RuntimeError("ACL output data buffer creation failed at %d" % index)
            allocation["buffer"] = data_buffer
            unwrap(acl.mdl.add_dataset_buffer(output_dataset, data_buffer), "acl.mdl.add_dataset_buffer output")
            output_hosts.append(host)

        def infer() -> List[np.ndarray]:
            unwrap(acl.rt.memcpy(input_pointer, input_size, host_pointer(acl, value), value.nbytes, int(getattr(acl, "ACL_MEMCPY_HOST_TO_DEVICE", 1))), "host_to_device")
            unwrap(acl.mdl.execute(model_id, input_dataset, output_dataset), "acl.mdl.execute")
            sync = getattr(acl.rt, "synchronize_device", None)
            if callable(sync):
                unwrap(sync(), "acl.rt.synchronize_device")
            for index, (host, allocation) in enumerate(zip(output_hosts, allocations[1:])):
                unwrap(acl.rt.memcpy(host_pointer(acl, host), host.nbytes, allocation["pointer"], allocation["size"], int(getattr(acl, "ACL_MEMCPY_DEVICE_TO_HOST", 2))), "device_to_host_%d" % index)
            return output_hosts

        for _ in range(args.warmup):
            infer()
        all_timings: List[float] = []
        repeat_timings: List[Dict[str, Any]] = []
        last_outputs: List[np.ndarray] = []
        for repeat in range(args.repeats):
            current: List[float] = []
            for _ in range(args.loops):
                start = time.perf_counter()
                last_outputs = infer()
                current.append((time.perf_counter() - start) * 1000.0)
            all_timings.extend(current)
            repeat_timings.append({
                "repeat": repeat + 1,
                "p50_ms": float(np.percentile(current, 50)),
                "p95_ms": float(np.percentile(current, 95)),
                "mean_ms": float(np.mean(current)),
                "fps_from_p50": float(1000.0 / np.percentile(current, 50)),
            })
        tolerance = PRECISION_TOLERANCES[args.precision]
        comparisons = compare_outputs(last_outputs, expected, tolerance["atol"], tolerance["rtol"])
        if args.capture_npz:
            args.capture_npz.parent.mkdir(parents=True, exist_ok=True)
            captured = {"input": input_tensor}
            captured.update({"output_%03d" % index: output for index, output in enumerate(last_outputs)})
            np.savez_compressed(args.capture_npz, **captured)
        timings_array = np.asarray(all_timings, dtype=np.float64)
        p50 = float(np.percentile(timings_array, 50))
        p95 = float(np.percentile(timings_array, 95))
        report.update({
            "om_contract": {
                "input_shape": list(input_shape),
                "input_dtype": str(input_dtype),
                "output_shapes": [list(shape) for shape in output_shapes],
                "output_dtypes": [str(dtype) for dtype in output_dtypes],
            },
            "comparison": {
                "output_count_matches": len(last_outputs) == len(expected),
                "passed": bool(comparisons) and all(item.get("passed", False) for item in comparisons),
                "outputs": comparisons,
                "max_abs_error": max((item.get("max_abs_error", 0.0) for item in comparisons), default=0.0),
                "mean_abs_error": float(np.mean([item["mean_abs_error"] for item in comparisons if "mean_abs_error" in item])) if any("mean_abs_error" in item for item in comparisons) else None,
            },
            "performance": {
                "scope": "H2D + ACL execute + device sync + D2H for all outputs",
                "sample_count": len(all_timings),
                "p50_ms": p50,
                "p95_ms": p95,
                "mean_ms": float(np.mean(timings_array)),
                "min_ms": float(np.min(timings_array)),
                "max_ms": float(np.max(timings_array)),
                "fps_from_p50": float(1000.0 / p50),
                "per_repeat": repeat_timings,
            },
        })
        if not report["comparison"]["passed"]:
            failures.append("one or more outputs failed the precision tolerance")
        # Keep cleanup references local until the finally block runs.
        report["status"] = "passed" if not failures else "observed_fail_numerical"
    except BaseException as exc:
        failures.append("%s: %s" % (type(exc).__name__, exc))
        report["error"] = failures[-1]
    finally:
        if acl is not None and context is not None:
            cleanup_call(cleanup, "set_context_before_cleanup", acl.rt.set_context, context)
        if acl is not None:
            for allocation in reversed(allocations):
                if allocation["buffer"] is not None:
                    cleanup_call(cleanup, "destroy_data_buffer", acl.destroy_data_buffer, allocation["buffer"])
            if output_dataset is not None:
                cleanup_call(cleanup, "destroy_output_dataset", acl.mdl.destroy_dataset, output_dataset)
            if input_dataset is not None:
                cleanup_call(cleanup, "destroy_input_dataset", acl.mdl.destroy_dataset, input_dataset)
            for allocation in reversed(allocations):
                cleanup_call(cleanup, "free_device_buffer", acl.rt.free, allocation["pointer"])
            if desc is not None:
                cleanup_call(cleanup, "destroy_model_desc", acl.mdl.destroy_desc, desc)
            if model_id is not None:
                cleanup_call(cleanup, "unload_model", acl.mdl.unload, model_id)
            if context is not None:
                cleanup_call(cleanup, "destroy_context", acl.rt.destroy_context, context)
            if acl_initialized:
                cleanup_call(cleanup, "acl_finalize", acl.finalize)
        report["failures"] = failures
        if failures and report.get("status") == "failed":
            report["status"] = "observed_fail_runtime"
        if any(not step["ok"] for step in cleanup):
            report["status"] = "observed_fail_cleanup"
            failures.append("one or more ACL cleanup operations failed")
            report["failures"] = failures
        report["finished_at"] = timestamp()
        write_json(args.output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "passed" else 1


def missing_report(path: Path, board: str, soc: str, model: Dict[str, Any], precision: str) -> Dict[str, Any]:
    return {
        "schema_version": 1,
        "kind": "unified_static_detector_onnx_om_benchmark",
        "created_at": timestamp(),
        "board": board,
        "soc": soc,
        "model_id": model["id"],
        "precision": precision,
        "status": "blocked_missing_artifact",
        "artifacts": {"om": {"path": str(path), "exists": False}},
        "reason": "suite manifest has no existing OM path or file is absent; no conversion was attempted",
    }


def write_summary(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=SUMMARY_FIELDS, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def run_suite(args: argparse.Namespace) -> int:
    suite = load_suite(args.suite)
    board = suite["boards"][args.board]
    reference_manifest = json.loads(args.reference_manifest.read_text(encoding="utf-8"))
    args.report_dir.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []
    failures = 0
    for model in suite["models"]:
        if args.model and args.model != model["id"]:
            continue
        reference_info = reference_manifest["models"].get(model["id"])
        for precision in ("fp32", "mixed_fp16"):
            base = {
                "board": args.board,
                "soc": board["soc"],
                "model_id": model["id"],
                "precision": precision,
                "status": "failed_runner",
                "input_shape": "",
                "output_count": "",
                "max_abs_error": "",
                "mean_abs_error": "",
                "rtol": PRECISION_TOLERANCES[precision]["rtol"],
                "atol": PRECISION_TOLERANCES[precision]["atol"],
                "p50_ms": "",
                "p95_ms": "",
                "fps_from_p50": "",
                "warmup": args.warmup,
                "loops_per_repeat": args.loops,
                "repeats": args.repeats,
                "onnx_sha256": model.get("onnx_sha256", ""),
                "om_sha256": "",
                "reference_sha256": reference_info.get("sha256", "") if reference_info else "",
            }
            report_path = args.report_dir / (model["id"] + "__" + precision + ".json")
            base["report"] = str(report_path)
            om_value = model["om_path"][args.board].get(precision)
            om_path = Path(om_value) if om_value else Path("<missing OM path>")
            if not om_value or not om_path.is_file():
                report = missing_report(om_path, args.board, board["soc"], model, precision)
                write_json(report_path, report)
                base["status"] = report["status"]
                rows.append(base)
                continue
            if reference_info is None:
                report = {"board": args.board, "soc": board["soc"], "model_id": model["id"], "precision": precision, "status": "blocked_missing_reference", "reason": "reference manifest has no model entry"}
                write_json(report_path, report)
                base["status"] = report["status"]
                rows.append(base)
                failures += 1
                continue
            onnx_path = Path(model["onnx_path"][args.board])
            reference_path = args.reference_dir / (model["id"] + ".npz")
            command = [
                sys.executable, str(Path(__file__).resolve()), "run-one",
                "--board", args.board,
                "--soc", board["soc"],
                "--model-id", model["id"],
                "--precision", precision,
                "--onnx", str(onnx_path),
                "--om", str(om_path),
                "--reference", str(reference_path),
                "--expected-onnx-sha256", model.get("onnx_sha256", ""),
                "--expected-reference-sha256", reference_info["sha256"],
                "--output", str(report_path),
                "--warmup", str(args.warmup),
                "--loops", str(args.loops),
                "--repeats", str(args.repeats),
            ]
            log_path = args.report_dir / (model["id"] + "__" + precision + ".stdout.log")
            with log_path.open("w", encoding="utf-8") as log:
                result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
            if not report_path.is_file():
                write_json(report_path, {
                    "board": args.board,
                    "soc": board["soc"],
                    "model_id": model["id"],
                    "precision": precision,
                    "status": "failed_runner",
                    "process_exit_code": result.returncode,
                    "stdout_log": str(log_path),
                })
            report = json.loads(report_path.read_text(encoding="utf-8"))
            comparison = report.get("comparison", {})
            performance = report.get("performance", {})
            artifacts = report.get("artifacts", {})
            contract = report.get("om_contract", {})
            base.update({
                "status": report.get("status", "failed_runner"),
                "input_shape": "x".join(str(value) for value in contract.get("input_shape", [])),
                "output_count": len(contract.get("output_shapes", [])) if contract else "",
                "max_abs_error": comparison.get("max_abs_error", ""),
                "mean_abs_error": comparison.get("mean_abs_error", ""),
                "p50_ms": performance.get("p50_ms", ""),
                "p95_ms": performance.get("p95_ms", ""),
                "fps_from_p50": performance.get("fps_from_p50", ""),
                "onnx_sha256": artifacts.get("onnx", {}).get("sha256", base["onnx_sha256"]),
                "om_sha256": artifacts.get("om", {}).get("sha256", ""),
                "reference_sha256": artifacts.get("reference", {}).get("sha256", base["reference_sha256"]),
            })
            if base["status"] not in ("passed", "blocked_missing_artifact"):
                failures += 1
            rows.append(base)
            print("%s %s %s: %s" % (args.board, model["id"], precision, base["status"]), flush=True)
            write_summary(args.summary, rows)
    write_summary(args.summary, rows)
    print("summary=%s rows=%d failures=%d" % (args.summary, len(rows), failures), flush=True)
    return 0 if failures == 0 else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare-references")
    prepare.add_argument("--suite", type=Path, required=True)
    prepare.add_argument("--onnx-board", choices=("20t", "8t"), default="20t")
    prepare.add_argument("--output-dir", type=Path, required=True)
    prepare.add_argument("--manifest", type=Path, required=True)
    prepare.add_argument("--seed", type=int, default=SEED_DEFAULT)
    prepare.set_defaults(func=prepare_references)
    run = sub.add_parser("run-suite")
    run.add_argument("--suite", type=Path, required=True)
    run.add_argument("--board", choices=("20t", "8t"), required=True)
    run.add_argument("--reference-manifest", type=Path, required=True)
    run.add_argument("--reference-dir", type=Path, required=True)
    run.add_argument("--report-dir", type=Path, required=True)
    run.add_argument("--summary", type=Path, required=True)
    run.add_argument("--model")
    run.add_argument("--warmup", type=int, default=WARMUP_DEFAULT)
    run.add_argument("--loops", type=int, default=LOOPS_DEFAULT)
    run.add_argument("--repeats", type=int, default=REPEATS_DEFAULT)
    run.set_defaults(func=run_suite)
    one = sub.add_parser("run-one")
    one.add_argument("--board", required=True)
    one.add_argument("--soc", required=True)
    one.add_argument("--model-id", required=True)
    one.add_argument("--precision", choices=tuple(PRECISION_TOLERANCES), required=True)
    one.add_argument("--onnx", type=Path, required=True)
    one.add_argument("--om", type=Path, required=True)
    one.add_argument("--reference", type=Path, required=True)
    one.add_argument("--expected-onnx-sha256", default="")
    one.add_argument("--expected-reference-sha256", default="")
    one.add_argument("--output", type=Path, required=True)
    one.add_argument("--capture-npz", type=Path)
    one.add_argument("--warmup", type=int, default=WARMUP_DEFAULT)
    one.add_argument("--loops", type=int, default=LOOPS_DEFAULT)
    one.add_argument("--repeats", type=int, default=REPEATS_DEFAULT)
    one.add_argument("--device", type=int, default=0)
    one.set_defaults(func=run_one)
    args = parser.parse_args()
    if getattr(args, "warmup", 1) < 0 or getattr(args, "loops", 1) < 1 or getattr(args, "repeats", 1) < 1:
        parser.error("warmup must be non-negative and loops/repeats must be positive")
    return args


def main() -> int:
    args = parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
