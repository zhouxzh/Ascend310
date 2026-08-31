"""CPU, EDCC, and candidate adapters for offline research commands only."""

from __future__ import annotations

import ctypes
from typing import Any

import cv2
import numpy as np

from palmprint_workbench.config import OFFLINE_CPU_THREADS
from palmprint_workbench.domain.registry import ModelSpec
from palmprint_workbench.runtime.adapters import (
    OmEmbeddingAdapter,
    PalmAdapter,
    l2_normalize,
    preprocess_embedding_roi,
)


class OnnxEmbeddingAdapter(PalmAdapter):
    """ONNX Runtime reference implementation used for numerical audits."""

    backend = "cpu"

    def __init__(self, spec: ModelSpec, threads: int = OFFLINE_CPU_THREADS) -> None:
        super().__init__(spec)
        path = spec.path("reference_onnx")
        if path is None or not path.is_file():
            raise FileNotFoundError(f"ONNX model not found: {path}")
        try:
            import onnxruntime as ort
        except ImportError as exc:
            raise RuntimeError("onnxruntime is required for the offline CPU backend") from exc

        options = ort.SessionOptions()
        options.intra_op_num_threads = max(1, int(threads))
        options.inter_op_num_threads = 1
        options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        self.session = ort.InferenceSession(
            str(path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        inputs = self.session.get_inputs()
        outputs = self.session.get_outputs()
        if len(inputs) != 1 or not outputs:
            raise ValueError("Palm ONNX model must have one input and at least one output")
        self.input_name = inputs[0].name
        self.output_name = outputs[0].name

    def preprocess(self, roi: np.ndarray) -> np.ndarray:
        return preprocess_embedding_roi(roi, self.spec.input_range)

    def encode_preprocessed(self, value: np.ndarray) -> np.ndarray:
        tensor = np.ascontiguousarray(value, dtype=np.float32)
        output = self.session.run([self.output_name], {self.input_name: tensor})[0]
        code = l2_normalize(output)
        if self.spec.feature_dim and code.size != self.spec.feature_dim:
            raise ValueError(f"Unexpected {self.spec.id} output size: {code.size}")
        return code

    def compare(self, query: np.ndarray, references: np.ndarray) -> np.ndarray:
        refs = np.asarray(references, dtype=np.float32)
        return refs @ np.asarray(query, dtype=np.float32)


class EdccAdapter(PalmAdapter):
    """Offline wrapper around the research-only EDCC shared library."""

    backend = "cpu"

    def __init__(self, spec: ModelSpec) -> None:
        super().__init__(spec)
        path = spec.path("library")
        if path is None or not path.is_file():
            raise FileNotFoundError(f"EDCC library not found: {path}")
        self.lib = ctypes.cdll.LoadLibrary(str(path))
        self._bind()
        status = ctypes.create_string_buffer(128)
        self.encoder_id = self.lib.new_encoder_with_config(29, 5, 5, 10, status)
        self._check_status(status)
        self.code_size = int(self.lib.get_size_of_code_buffer_required(self.encoder_id))

    def _bind(self) -> None:
        self.lib.new_encoder_with_config.argtypes = [
            ctypes.c_uint8,
            ctypes.c_uint8,
            ctypes.c_uint8,
            ctypes.c_uint8,
            ctypes.c_char_p,
        ]
        self.lib.new_encoder_with_config.restype = ctypes.c_int
        self.lib.get_size_of_code_buffer_required.argtypes = [ctypes.c_int]
        self.lib.get_size_of_code_buffer_required.restype = ctypes.c_ulong
        self.lib.encode_palmprint_using_bytes.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_ulong,
            ctypes.c_char_p,
            ctypes.c_ulong,
            ctypes.c_char_p,
        ]
        self.lib.calculate_codes_similarity.argtypes = [
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_char_p,
        ]
        self.lib.calculate_codes_similarity.restype = ctypes.c_double

    @staticmethod
    def _check_status(status: ctypes.Array[Any]) -> None:
        if status.raw[0] != 0:
            message = status.raw[1:].split(b"\0", 1)[0].decode("utf-8", errors="replace")
            raise RuntimeError(f"EDCC failed: {message}")

    def preprocess(self, roi: np.ndarray) -> bytes:
        gray = np.asarray(roi, dtype=np.uint8)
        if gray.shape != (128, 128):
            gray = cv2.resize(gray, (128, 128), interpolation=cv2.INTER_AREA)
        ok, encoded = cv2.imencode(".bmp", gray)
        if not ok:
            raise RuntimeError("Could not BMP-encode palm ROI")
        return encoded.tobytes()

    def encode_preprocessed(self, value: bytes) -> np.ndarray:
        payload = bytes(value)
        status = ctypes.create_string_buffer(128)
        code = ctypes.create_string_buffer(self.code_size)
        self.lib.encode_palmprint_using_bytes(
            self.encoder_id, payload, len(payload), code, self.code_size, status
        )
        self._check_status(status)
        return np.frombuffer(code.raw, dtype=np.uint8).copy()

    def compare(self, query: np.ndarray, references: np.ndarray) -> np.ndarray:
        query_bytes = ctypes.create_string_buffer(np.asarray(query, dtype=np.uint8).tobytes())
        scores = np.empty(len(references), dtype=np.float32)
        for index, reference in enumerate(references):
            reference_bytes = ctypes.create_string_buffer(
                np.asarray(reference, dtype=np.uint8).tobytes()
            )
            status = ctypes.create_string_buffer(128)
            scores[index] = self.lib.calculate_codes_similarity(
                query_bytes, reference_bytes, status
            )
            self._check_status(status)
        return scores


def create_offline_adapter(
    spec: ModelSpec,
    backend: str,
    precision: str = "mixed_fp16",
    *,
    threads: int = OFFLINE_CPU_THREADS,
) -> PalmAdapter:
    """Create an explicitly offline CPU/EDCC adapter or an NPU audit adapter."""

    if spec.id == "edcc":
        if backend != "cpu":
            raise ValueError("EDCC is a CPU-only algorithm")
        return EdccAdapter(spec)
    if backend == "cpu":
        return OnnxEmbeddingAdapter(spec, threads=threads)
    if backend == "npu":
        return OmEmbeddingAdapter(spec, precision)
    raise ValueError(f"Unsupported offline backend: {backend}")


__all__ = ["EdccAdapter", "OnnxEmbeddingAdapter", "create_offline_adapter"]
