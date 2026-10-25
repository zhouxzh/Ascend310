#!/usr/bin/env python3
"""Benchmark one fixed-shape Qwen decoder OM through native PyACL."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys
import time

CASE_ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = Path(
    os.environ.get("ASCEND_EDGE_CHAT_PACKAGE_ROOT", str(CASE_ROOT))
).resolve()
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

import numpy as np  # noqa: E402

from ascend_edge_chat.static_kv.runtime import (  # noqa: E402
    NativeQwen25Backend,
    RuntimeTensorDescriptor,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Benchmark one fixed-shape decoder OM with native PyACL."
    )
    parser.add_argument("--om", type=Path, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument(
        "--mode",
        choices=("end-to-end", "execute-only"),
        default="end-to-end",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--device-id", type=int, default=0)
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _dtype(name: str) -> np.dtype:
    mapping = {
        "float16": np.float16,
        "float32": np.float32,
        "int32": np.int32,
        "int64": np.int64,
        "uint8": np.uint8,
        "int8": np.int8,
    }
    try:
        return np.dtype(mapping[name])
    except KeyError as exc:
        raise ValueError("unsupported descriptor dtype: %s" % name) from exc


def _zeros(descriptor: RuntimeTensorDescriptor) -> np.ndarray:
    return np.zeros(descriptor.shape, dtype=_dtype(descriptor.dtype))


def _stats(seconds: list[float]) -> dict[str, float]:
    ordered = sorted(seconds)
    p95_index = min(len(ordered) - 1, max(0, int(round(0.95 * (len(ordered) - 1)))))
    mean = statistics.fmean(seconds)
    return {
        "mean_s": mean,
        "median_s": statistics.median(seconds),
        "p95_s": ordered[p95_index],
        "min_s": min(seconds),
        "max_s": max(seconds),
        "tokens_per_s": 1.0 / mean,
    }


def main() -> int:
    args = parse_args()
    if args.warmup < 0 or args.runs <= 0:
        raise ValueError("warmup must be non-negative and runs must be positive")
    om = args.om.expanduser().resolve()
    if not om.is_file():
        raise FileNotFoundError("OM not found: %s" % om)

    backend = NativeQwen25Backend(device_id=args.device_id)
    descriptor = backend.open(om)
    inputs = list(descriptor.inputs)
    outputs = list(descriptor.outputs)
    if len(inputs) != 51 or len(outputs) != 49:
        raise RuntimeError(
            "expected 51 inputs/49 outputs, got %s/%s" % (len(inputs), len(outputs))
        )
    if [item.name for item in inputs[:3]] != [
        "input_ids",
        "attention_mask",
        "position_ids",
    ]:
        raise RuntimeError("unexpected first three decoder inputs")
    if len(inputs[3:]) != 48:
        raise RuntimeError("expected 48 KV cache inputs")
    logits_items = [
        item
        for item in outputs
        if len(item.shape) == 3 and int(item.shape[-1]) == 151936
    ]
    if len(logits_items) != 1:
        raise RuntimeError("expected exactly one vocabulary logits output")
    logits_name = logits_items[0].name

    cache = {item.name: _zeros(item) for item in inputs[3:]}
    base = {
        "input_ids": np.array([[1]], dtype=_dtype(inputs[0].dtype)),
        "attention_mask": np.ones(inputs[1].shape, dtype=_dtype(inputs[1].dtype)),
        "position_ids": np.zeros(inputs[2].shape, dtype=_dtype(inputs[2].dtype)),
    }
    request = backend.begin_request(cache)
    timings: list[float] = []
    top1: int | None = None
    try:
        validation = backend.run_request_step(
            request,
            base,
            real_length=0,
            cache_values=None,
        )
        logits = np.asarray(validation.outputs[logits_name]).reshape(-1)
        if not np.isfinite(logits).all():
            raise RuntimeError("logits contain NaN or Inf")
        top1 = int(np.argmax(logits))

        for index in range(args.warmup + args.runs):
            started = time.perf_counter()
            if args.mode == "end-to-end":
                backend.run_request_step(
                    request,
                    base,
                    real_length=0,
                    cache_values=None,
                )
            else:
                execute = backend.acl.mdl.execute(
                    backend.model_id,
                    request.input_dataset,
                    request.output_dataset,
                )
                if int(execute) != 0:
                    raise RuntimeError("acl.mdl.execute returned %s" % execute)
            elapsed = time.perf_counter() - started
            if index >= args.warmup:
                timings.append(elapsed)
    finally:
        backend.end_request(request)
        backend.close()

    report = {
        "schema_version": 1,
        "label": args.label,
        "om": {
            "path": str(om),
            "bytes": om.stat().st_size,
            "sha256": _sha256(om),
        },
        "environment": {
            "conda_env": os.environ.get("CONDA_DEFAULT_ENV"),
            "python": sys.executable,
            "device_id": args.device_id,
        },
        "contract": {
            "input_count": len(inputs),
            "output_count": len(outputs),
            "logits_name": logits_name,
            "logits_shape": list(logits_items[0].shape),
            "cache_input_count": 48,
            "cache_input_shape": list(inputs[3].shape),
            "cache_input_dtype": inputs[3].dtype,
            "top1_token": top1,
        },
        "protocol": {
            "mode": args.mode,
            "warmup": args.warmup,
            "runs": args.runs,
            "fixed_input_token": 1,
            "zero_cache": True,
            "timing": (
                "host input copies + synchronous acl.mdl.execute + output D2H"
                if args.mode == "end-to-end"
                else "synchronous acl.mdl.execute with resident input/output buffers"
            ),
        },
        "timings": timings,
        "stats": _stats(timings),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    print(json.dumps(report["stats"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
