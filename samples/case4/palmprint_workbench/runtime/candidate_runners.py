"""ACL-only runners for the heterogeneous candidate experiment channel.

Candidate models are deliberately isolated from the template/recognition
adapter.  They may be classifiers, ROI regressors, a pair comparator, or a
vein model, so this module returns task-specific output summaries rather than
pretending every graph is a 512-D palmprint embedding.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
from typing import Any, Mapping

import cv2
import numpy as np

from ..config import ROOT
from .adapters import _AclEnvironment, _acl_check, _acl_result_code


MANIFEST_PATH = ROOT / "candidate_runtime_manifest.json"


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _load_manifest() -> dict[str, dict[str, Any]]:
    payload = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    items = payload.get("models", []) if isinstance(payload, dict) else []
    return {str(item["id"]): dict(item) for item in items if isinstance(item, dict) and item.get("id")}


def candidate_specs() -> list[dict[str, Any]]:
    return list(_load_manifest().values())


def candidate_spec(candidate_id: str) -> dict[str, Any]:
    try:
        return _load_manifest()[candidate_id]
    except KeyError as exc:
        raise KeyError(f"Unknown candidate runtime model: {candidate_id}") from exc


def verify_candidate_asset(spec: Mapping[str, Any]) -> Path:
    value = spec.get("om_path")
    if not isinstance(value, str) or not value:
        raise ValueError("candidate runtime entry has no OM path")
    path = (ROOT / value).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError("candidate OM path escapes project root") from exc
    if not path.is_file():
        raise FileNotFoundError(f"candidate OM is missing: {value}")
    expected_bytes = spec.get("bytes")
    expected_sha = spec.get("sha256")
    if path.stat().st_size != expected_bytes:
        raise ValueError(f"candidate OM byte count mismatch: {value}")
    if _digest(path) != expected_sha:
        raise ValueError(f"candidate OM SHA-256 mismatch: {value}")
    return path


def _dtype(acl: Any, value: Any) -> np.dtype:
    mapping = {
        int(getattr(acl, "ACL_FLOAT", 0)): np.dtype(np.float32),
        int(getattr(acl, "ACL_FLOAT16", 1)): np.dtype(np.float16),
    }
    if int(value) not in mapping:
        raise ValueError(f"unsupported OM dtype: {value}")
    return mapping[int(value)]


def _dims(acl: Any, desc: Any, getter: Any, index: int) -> tuple[int, ...]:
    info = _acl_check(getter(desc, index), "get tensor dimensions")
    return tuple(int(item) for item in info["dims"][: int(info["dimCount"])])


def _preprocess(image: np.ndarray, kind: str, shape: tuple[int, ...]) -> np.ndarray:
    value = np.asarray(image)
    if value.ndim == 2:
        gray = value
        rgb = cv2.cvtColor(gray.astype(np.uint8), cv2.COLOR_GRAY2RGB)
    elif value.ndim == 3 and value.shape[2] >= 3:
        rgb = value[:, :, :3].astype(np.uint8)
        gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    else:
        raise ValueError("image must be grayscale or RGB")
    height, width = shape[-2:]
    if kind == "gray":
        resized = cv2.resize(gray, (width, height), interpolation=cv2.INTER_AREA)
        tensor = resized.astype(np.float32) / 255.0
        return np.ascontiguousarray(tensor[None, None, :, :], dtype=np.float32)
    resized = cv2.resize(rgb, (width, height), interpolation=cv2.INTER_AREA)
    tensor = resized.astype(np.float32) / 255.0
    tensor = (tensor - np.asarray([0.485, 0.456, 0.406], dtype=np.float32)) / np.asarray(
        [0.229, 0.224, 0.225], dtype=np.float32
    )
    return np.ascontiguousarray(np.transpose(tensor, (2, 0, 1))[None, :, :, :], dtype=np.float32)


class _CandidateRunner:
    def __init__(self, path: Path) -> None:
        self.acl = _AclEnvironment.acquire_runner()
        self._runtime_registered = True
        self.context = None
        self.model_id = None
        self.desc = None
        self.input_dataset = None
        self.output_dataset = None
        self.buffers: list[dict[str, Any]] = []
        self.input_shapes: list[tuple[int, ...]] = []
        self.output_shapes: list[tuple[int, ...]] = []
        self.input_dtypes: list[np.dtype] = []
        self.output_dtypes: list[np.dtype] = []
        self.closed = False
        self.closing = False
        try:
            self.context = _acl_check(self.acl.rt.create_context(0), "acl.rt.create_context")
            self.model_id = _acl_check(self.acl.mdl.load_from_file(str(path)), "acl.mdl.load_from_file")
            self.desc = self.acl.mdl.create_desc()
            _acl_check(self.acl.mdl.get_desc(self.desc, self.model_id), "acl.mdl.get_desc")
            self.input_count = int(self.acl.mdl.get_num_inputs(self.desc))
            self.output_count = int(self.acl.mdl.get_num_outputs(self.desc))
            self.input_shapes = [_dims(self.acl, self.desc, self.acl.mdl.get_input_dims, i) for i in range(self.input_count)]
            self.output_shapes = [_dims(self.acl, self.desc, self.acl.mdl.get_output_dims, i) for i in range(self.output_count)]
            self.input_dtypes = [_dtype(self.acl, self.acl.mdl.get_input_data_type(self.desc, i)) for i in range(self.input_count)]
            self.output_dtypes = [_dtype(self.acl, self.acl.mdl.get_output_data_type(self.desc, i)) for i in range(self.output_count)]
            self.input_dataset = self.acl.mdl.create_dataset()
            self.output_dataset = self.acl.mdl.create_dataset()
            for index in range(self.input_count):
                self._allocate(index, True)
            for index in range(self.output_count):
                self._allocate(index, False)
        except BaseException:
            self.close(suppress_errors=True)
            raise

    def _allocate(self, index: int, is_input: bool) -> None:
        size_getter = self.acl.mdl.get_input_size_by_index if is_input else self.acl.mdl.get_output_size_by_index
        size = int(size_getter(self.desc, index))
        pointer = _acl_check(self.acl.rt.malloc(size, 0), "acl.rt.malloc")
        data_buffer = self.acl.create_data_buffer(pointer, size)
        dataset = self.input_dataset if is_input else self.output_dataset
        _acl_check(self.acl.mdl.add_dataset_buffer(dataset, data_buffer), "acl.mdl.add_dataset_buffer")
        self.buffers.append({"pointer": pointer, "data_buffer": data_buffer, "size": size, "input": is_input})

    def run(self, values: list[np.ndarray]) -> list[np.ndarray]:
        if len(values) != self.input_count:
            raise ValueError(f"candidate expects {self.input_count} inputs, got {len(values)}")
        for value, buffer, dtype, shape in zip(values, self.buffers[: self.input_count], self.input_dtypes, self.input_shapes):
            array = np.ascontiguousarray(value, dtype=dtype)
            if tuple(array.shape) != shape or array.nbytes != buffer["size"]:
                raise ValueError(f"candidate input contract mismatch: {array.shape} != {shape}")
            pointer = self.acl.util.numpy_to_ptr(array)
            _acl_check(self.acl.rt.memcpy(buffer["pointer"], buffer["size"], pointer, array.nbytes, int(getattr(self.acl, "ACL_MEMCPY_HOST_TO_DEVICE", 1))), "host_to_device")
        _acl_check(self.acl.rt.set_context(self.context), "acl.rt.set_context")
        started = time.perf_counter_ns()
        _acl_check(self.acl.mdl.execute(self.model_id, self.input_dataset, self.output_dataset), "acl.mdl.execute")
        outputs: list[np.ndarray] = []
        for shape, dtype, buffer in zip(self.output_shapes, self.output_dtypes, self.buffers[self.input_count:]):
            value = np.empty(shape, dtype=dtype)
            pointer = self.acl.util.numpy_to_ptr(value)
            _acl_check(self.acl.rt.memcpy(pointer, value.nbytes, buffer["pointer"], buffer["size"], int(getattr(self.acl, "ACL_MEMCPY_DEVICE_TO_HOST", 2))), "device_to_host")
            outputs.append(value)
        self.last_inference_ms = (time.perf_counter_ns() - started) / 1e6
        return outputs

    def _cleanup_call(self, diagnostics: list[dict[str, Any]], phase: str, operation: Any, *args: Any) -> bool:
        event: dict[str, Any] = {"phase": phase}
        try:
            result = operation(*args)
            ret = _acl_result_code(result, none_is_success=True)
            event.update({"ok": ret == 0, "return_code": ret})
        except BaseException as exc:
            event.update({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        diagnostics.append(event)
        return bool(event["ok"])

    def close(self, *, suppress_errors: bool = False) -> list[dict[str, Any]]:
        """Retryable, dependency-ordered candidate cleanup.

        A failed ACL destroy/free retains its handle and runner reference so a
        later close attempt cannot let process-wide runtime shutdown reset a
        device while candidate resources are still live.
        """

        if self.closed:
            return []
        self.closing = True
        diagnostics: list[dict[str, Any]] = []
        ready = True
        if self.context is not None:
            context_ok = self._cleanup_call(diagnostics, "set_context_before_cleanup", self.acl.rt.set_context, self.context)
            ready = context_ok
            sync = getattr(self.acl.rt, "synchronize_device", None)
            if context_ok and callable(sync):
                ready = self._cleanup_call(diagnostics, "synchronize_device_before_cleanup", sync) and ready
        if ready:
            for buffer in reversed(self.buffers):
                data_buffer = buffer.get("data_buffer")
                if data_buffer is not None:
                    destroyed = self._cleanup_call(diagnostics, "destroy_data_buffer", self.acl.destroy_data_buffer, data_buffer)
                    if destroyed:
                        buffer["data_buffer"] = None
                    else:
                        ready = False
                pointer = buffer.get("pointer")
                if pointer is not None and buffer.get("data_buffer") is None:
                    freed = self._cleanup_call(diagnostics, "free_device_buffer", self.acl.rt.free, pointer)
                    if freed:
                        buffer["pointer"] = None
                    else:
                        ready = False
            if any(buffer.get("data_buffer") is not None or buffer.get("pointer") is not None for buffer in self.buffers):
                ready = False
        if ready:
            for label, attr in (("output", "output_dataset"), ("input", "input_dataset")):
                value = getattr(self, attr)
                if value is not None:
                    if self._cleanup_call(diagnostics, f"destroy_{label}_dataset", self.acl.mdl.destroy_dataset, value):
                        setattr(self, attr, None)
                    else:
                        ready = False
            if self.desc is not None:
                if self._cleanup_call(diagnostics, "destroy_model_desc", self.acl.mdl.destroy_desc, self.desc):
                    self.desc = None
                else:
                    ready = False
            if self.model_id is not None:
                if self._cleanup_call(diagnostics, "unload_model", self.acl.mdl.unload, self.model_id):
                    self.model_id = None
                else:
                    ready = False
            if self.context is not None:
                if self._cleanup_call(diagnostics, "destroy_context", self.acl.rt.destroy_context, self.context):
                    self.context = None
                else:
                    ready = False
        live = any(
            buffer.get("data_buffer") is not None or buffer.get("pointer") is not None
            for buffer in self.buffers
        ) or any(value is not None for value in (self.output_dataset, self.input_dataset, self.desc, self.model_id, self.context))
        if not live:
            self.closed = True
            self.closing = False
            if self._runtime_registered:
                _AclEnvironment.release_runner()
                self._runtime_registered = False
        else:
            self.closing = False
            if not suppress_errors:
                raise RuntimeError("candidate ACL cleanup retained live resources")
        if not suppress_errors and any(not item.get("ok", False) for item in diagnostics):
            raise RuntimeError("candidate ACL cleanup failed")
        return diagnostics


def _output_summary(spec: Mapping[str, Any], outputs: list[np.ndarray]) -> dict[str, Any]:
    finite = all(bool(np.all(np.isfinite(item))) for item in outputs)
    summary: dict[str, Any] = {
        "output_shapes": [list(item.shape) for item in outputs],
        "output_dtypes": [str(item.dtype) for item in outputs],
        "output_finite": finite,
    }
    kind = spec.get("output_kind")
    if kind == "logits":
        logits = outputs[0].reshape(-1).astype(np.float32)
        top = np.argsort(logits)[-5:][::-1]
        summary["top_k"] = [{"class_id": int(index), "logit": float(logits[index])} for index in top]
    elif kind == "theta":
        summary["theta"] = outputs[0].reshape(-1).astype(np.float32).tolist()
    elif kind == "embedding":
        vector = outputs[0].reshape(-1).astype(np.float32)
        summary["embedding_dim"] = int(vector.size)
        summary["embedding_l2_norm"] = float(np.linalg.norm(vector))
    elif kind == "embedding_pair":
        vectors = [item.reshape(-1).astype(np.float32) for item in outputs]
        summary["embedding_dims"] = [int(item.size) for item in vectors]
        if len(vectors) == 2 and np.linalg.norm(vectors[0]) > 0 and np.linalg.norm(vectors[1]) > 0:
            summary["cosine_similarity"] = float(np.dot(vectors[0], vectors[1]) / (np.linalg.norm(vectors[0]) * np.linalg.norm(vectors[1])))
    return summary


def run_candidate(candidate_id: str, image: np.ndarray, reference: np.ndarray | None = None) -> dict[str, Any]:
    spec = candidate_spec(candidate_id)
    path = verify_candidate_asset(spec)
    declared_shape = spec.get("input_shape")
    input_shapes = [tuple(int(item) for item in shape) for shape in declared_shape] if spec.get("input_kind") == "rgb_pair" else [tuple(int(item) for item in declared_shape)]
    values = [_preprocess(image, str(spec["input_kind"]).replace("_pair", ""), input_shapes[0])]
    if spec.get("input_kind") == "rgb_pair":
        values.append(_preprocess(reference if reference is not None else image, "rgb", input_shapes[1]))
    runner = _CandidateRunner(path)
    actual_input_shapes = [tuple(shape) for shape in runner.input_shapes]
    if actual_input_shapes != input_shapes:
        runner.close()
        raise ValueError(
            f"candidate input contract mismatch: manifest {input_shapes}, OM {actual_input_shapes}"
        )
    started = time.perf_counter_ns()
    try:
        outputs = runner.run(values)
        result = {
            "candidate_id": candidate_id,
            "display_name": spec["display_name"],
            "task_type": spec["task_type"],
            "modality": spec["modality"],
            "backend": "npu",
            "precision": "mixed_fp16",
            "om_path": spec["om_path"],
            "om_bytes": spec["bytes"],
            "om_sha256": spec["sha256"],
            "input_shapes": [list(item.shape) for item in values],
            "inference_ms": float(getattr(runner, "last_inference_ms", 0.0)),
            "total_ms": (time.perf_counter_ns() - started) / 1e6,
            **_output_summary(spec, outputs),
        }
        if not result["output_finite"]:
            raise ValueError("candidate output contains NaN or infinity")
        return result
    finally:
        runner.close()


__all__ = ["candidate_specs", "candidate_spec", "run_candidate", "verify_candidate_asset"]
