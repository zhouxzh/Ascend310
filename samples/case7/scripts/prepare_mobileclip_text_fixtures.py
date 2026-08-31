#!/usr/bin/env python3
"""Generate fixed MobileCLIP text references from COCO-CN English queries.

The generated ``.npz`` files are isolated numerical evidence for an arbitrary
candidate OM.  They contain the exact ``[1, 77]`` int64 token input and a
normalized ONNX reference vector, but they never modify the canonical model
reference directory, model registry, or model artifacts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple, Union

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
MODEL_ID = "mobileclip_s0__npu__mixed_fp16"
COMPONENT = "text"
CONTEXT_LENGTH = 77
PAD_TOKEN_ID = 0
EMBEDDING_DIM = 512
FIXED_QUERY_COUNT = 20
DEFAULT_MANIFEST = ROOT / "reports" / "datasets" / "coco_cn_case7_manifest.json"
DEFAULT_ONNX = ROOT / "models" / "onnx" / "mobileclip_s0_text.onnx"
DEFAULT_TOKENIZER = ROOT / "models" / "tokenizers" / "mobileclip_s0" / "tokenizer.json"


class FixtureError(RuntimeError):
    """Raised when the fixed fixture protocol cannot be satisfied."""


def sha256_file(path: Path) -> str:
    """Return the SHA-256 digest of a complete artifact."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def _as_path(value: Union[str, Path]) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (ROOT / path).resolve()


def _load_queries(manifest_path: Path) -> list[dict[str, Any]]:
    """Read the fixed 20 English query records from the COCO-CN manifest."""
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FixtureError(f"cannot read COCO-CN manifest {manifest_path}: {exc}") from exc
    if payload.get("dataset_id") != "COCO-CN":
        raise FixtureError("fixture manifest must identify the COCO-CN dataset")
    queries = payload.get("queries", {}).get("en")
    if not isinstance(queries, list) or len(queries) != FIXED_QUERY_COUNT:
        raise FixtureError(
            f"COCO-CN manifest must contain exactly {FIXED_QUERY_COUNT} English queries"
        )
    normalized: list[dict[str, Any]] = []
    seen = set()
    for index, raw in enumerate(queries, start=1):
        if not isinstance(raw, Mapping):
            raise FixtureError(f"English query {index} is not an object")
        text = str(raw.get("query", "")).strip()
        if not text:
            raise FixtureError(f"English query {index} is empty")
        if text in seen:
            raise FixtureError(f"COCO-CN English query is duplicated: {text!r}")
        seen.add(text)
        keywords = raw.get("keywords", [])
        if not isinstance(keywords, list) or not keywords:
            raise FixtureError(f"English query {index} has no fixed keywords")
        normalized.append(
            {
                "index": index,
                "query": text,
                "keywords": [str(value) for value in keywords],
                "relevant_image_ids": [
                    str(value) for value in raw.get("relevant_image_ids", [])
                ],
            }
        )
    return normalized


def _load_tokenizer(path: Path):
    try:
        from tokenizers import Tokenizer
    except ImportError as exc:
        raise FixtureError("the tokenizers package is required to build MobileCLIP text fixtures") from exc
    try:
        return Tokenizer.from_file(str(path))
    except Exception as exc:
        raise FixtureError(f"cannot load MobileCLIP tokenizer {path}: {exc}") from exc


def _tokenize(tokenizer, text: str) -> np.ndarray:
    ids = list(tokenizer.encode(text, add_special_tokens=True).ids[:CONTEXT_LENGTH])
    ids.extend([PAD_TOKEN_ID] * (CONTEXT_LENGTH - len(ids)))
    value = np.asarray([ids], dtype=np.int64)
    if value.shape != (1, CONTEXT_LENGTH):
        raise FixtureError(f"MobileCLIP token shape is {value.shape}, expected (1, {CONTEXT_LENGTH})")
    return value


def _load_onnx_session(path: Path) -> Tuple[Any, str, str]:
    """Return an offline CPU reference session, backend, and input name."""
    ort_error = None
    try:
        import onnxruntime as ort

        session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        inputs = session.get_inputs()
        if len(inputs) != 1:
            raise FixtureError(f"MobileCLIP text ONNX exposes {len(inputs)} inputs, expected 1")
        return session, "onnxruntime", str(inputs[0].name)
    except Exception as exc:  # The board may lack an ORT kernel.
        ort_error = exc
    try:
        import onnx
        from onnx.reference import ReferenceEvaluator

        graph = onnx.load(str(path))
        inputs = list(graph.graph.input)
        if len(inputs) != 1:
            raise FixtureError(f"MobileCLIP text ONNX exposes {len(inputs)} inputs, expected 1")
        return ReferenceEvaluator(graph), "onnx.reference.ReferenceEvaluator", str(inputs[0].name)
    except Exception as exc:
        raise FixtureError(
            "unable to execute MobileCLIP text ONNX reference: "
            f"onnxruntime={ort_error}; reference={exc}"
        ) from exc


def _run_reference(session, input_name: str, value: np.ndarray) -> np.ndarray:
    try:
        output = np.asarray(session.run(None, {input_name: value})[0], dtype=np.float32).reshape(-1)
    except Exception as exc:
        raise FixtureError(f"ONNX text reference execution failed: {exc}") from exc
    if output.size != EMBEDDING_DIM:
        raise FixtureError(f"ONNX text output has {output.size} values, expected {EMBEDDING_DIM}")
    if not np.isfinite(output).all():
        raise FixtureError("ONNX text reference output contains NaN or infinity")
    norm = float(np.linalg.norm(output))
    if norm <= 0:
        raise FixtureError("ONNX text reference output has zero L2 norm")
    return (output / norm).astype(np.float32, copy=False)


def _ensure_isolated_output(output_dir: Path) -> None:
    """Keep optional fixtures outside canonical references and photo/model trees."""
    protected_roots = (
        ROOT / "models",
        ROOT / "data",
        ROOT / "photos",
        ROOT / "reports" / "model_pipeline" / "references",
    )
    resolved = output_dir.resolve()
    for protected in protected_roots:
        root = protected.resolve()
        if resolved == root or root in resolved.parents:
            raise FixtureError(f"output directory is protected: {resolved}")
    output_dir.mkdir(parents=True, exist_ok=True)
    expected = {
        f"{MODEL_ID}__{COMPONENT}__query-{index:02d}.npz"
        for index in range(1, FIXED_QUERY_COUNT + 1)
    }
    stale = [
        path.name
        for path in output_dir.glob(f"{MODEL_ID}__{COMPONENT}*.npz")
        if path.name not in expected
    ]
    if stale:
        raise FixtureError(
            "output directory contains unexpected MobileCLIP text reference(s): "
            + ", ".join(sorted(stale))
        )


def _atomic_npz(path: Path, input_value: np.ndarray, output_value: np.ndarray) -> None:
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            np.savez_compressed(handle, input=input_value, output=output_value)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    temporary.replace(path)


def _atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + ".", delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(encoded)
        except Exception:
            temporary.unlink(missing_ok=True)
            raise
    temporary.replace(path)


def generate(
    manifest_path: Path,
    onnx_path: Path,
    tokenizer_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    """Generate exactly 20 COCO-CN English text references in ``output_dir``."""
    for label, path in (("manifest", manifest_path), ("ONNX", onnx_path), ("tokenizer", tokenizer_path)):
        if not path.is_file():
            raise FixtureError(f"missing {label} artifact: {path}")
    _ensure_isolated_output(output_dir)
    queries = _load_queries(manifest_path)
    tokenizer = _load_tokenizer(tokenizer_path)
    session, reference_backend, input_name = _load_onnx_session(onnx_path)
    if input_name != "text":
        raise FixtureError(f"MobileCLIP text ONNX input is {input_name!r}, expected 'text'")

    entries = []
    for query in queries:
        input_value = _tokenize(tokenizer, query["query"])
        output_value = _run_reference(session, input_name, input_value)
        filename = f"{MODEL_ID}__{COMPONENT}__query-{query['index']:02d}.npz"
        target = output_dir / filename
        _atomic_npz(target, input_value, output_value)
        entries.append(
            {
                **query,
                "reference": filename,
                "reference_sha256": sha256_file(target),
                "reference_size_bytes": target.stat().st_size,
                "token_summary": {
                    "shape": list(input_value.shape),
                    "dtype": str(input_value.dtype),
                    "sha256": sha256_bytes(input_value.tobytes()),
                    "non_pad_token_count": int(np.count_nonzero(input_value != PAD_TOKEN_ID)),
                    "first_12_ids": [int(value) for value in input_value.reshape(-1)[:12]],
                },
                "reference_output": {
                    "shape": [int(output_value.size)],
                    "dtype": str(output_value.dtype),
                    "sha256": sha256_bytes(output_value.tobytes()),
                    "finite": True,
                    "l2_normalized": True,
                },
            }
        )

    result = {
        "schema_version": 1,
        "generated_at": time.time(),
        "fixture_id": "mobileclip_s0_text_coco_cn_en_20",
        "model_id": MODEL_ID,
        "component": COMPONENT,
        "query_count": len(entries),
        "source_manifest": {
            "path": _display_path(manifest_path),
            "sha256": sha256_file(manifest_path),
            "dataset_id": "COCO-CN",
        },
        "onnx": {
            "path": _display_path(onnx_path),
            "sha256": sha256_file(onnx_path),
            "size_bytes": onnx_path.stat().st_size,
            "input_name": input_name,
            "input_shape": [1, CONTEXT_LENGTH],
            "input_dtype": "int64",
            "output_dim": EMBEDDING_DIM,
            "output_dtype": "float32",
        },
        "tokenizer": {
            "path": _display_path(tokenizer_path),
            "sha256": sha256_file(tokenizer_path),
            "context_length": CONTEXT_LENGTH,
            "pad_token_id": PAD_TOKEN_ID,
        },
        "reference_backend": reference_backend,
        "output_directory": _display_path(output_dir),
        "references": entries,
    }
    _atomic_json(output_dir / "mobileclip_s0_text_fixture_manifest.json", result)
    return result


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="fixed COCO-CN manifest JSON")
    value.add_argument("--onnx", default=str(DEFAULT_ONNX), help="MobileCLIP text FP32 ONNX")
    value.add_argument("--tokenizer", default=str(DEFAULT_TOKENIZER), help="pinned MobileCLIP tokenizer.json")
    value.add_argument(
        "--output-dir",
        required=True,
        help="fresh isolated directory for 20 candidate-validation NPZ files",
    )
    return value


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = generate(
            _as_path(args.manifest),
            _as_path(args.onnx),
            _as_path(args.tokenizer),
            _as_path(args.output_dir),
        )
    except FixtureError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    print(
        "[mobileclip-text-fixtures] wrote "
        f"{result['query_count']} references to {result['output_directory']} "
        f"using {result['reference_backend']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
