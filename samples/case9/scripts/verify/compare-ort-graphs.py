#!/usr/bin/env python3
"""Compare logits and KV outputs from two ONNX graphs with fixed inputs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

import numpy as np
import onnxruntime as ort


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare original and migrated Qwen ONNX graphs on CPU."
    )
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--token-id", type=int, default=1)
    parser.add_argument("--past-sequence-length", type=int, default=0)
    parser.add_argument("--kv-heads", type=int, default=16)
    parser.add_argument("--head-dim", type=int, default=64)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _feed(session: ort.InferenceSession, args: argparse.Namespace) -> dict[str, np.ndarray]:
    feed: dict[str, np.ndarray] = {}
    total_length = args.past_sequence_length + 1
    for item in session.get_inputs():
        if item.name == "input_ids":
            value = np.array([[args.token_id]], dtype=np.int64)
        elif item.name == "attention_mask":
            value = np.ones((1, total_length), dtype=np.int64)
        elif item.name == "position_ids":
            value = np.array([[args.past_sequence_length]], dtype=np.int64)
        elif item.name.startswith("past_key_values."):
            dtype = np.float16 if item.type == "tensor(float16)" else np.float32
            value = np.zeros(
                (1, args.kv_heads, args.past_sequence_length, args.head_dim),
                dtype=dtype,
            )
        else:
            raise RuntimeError("unmapped ONNX input %s: %s" % (item.name, item.type))
        feed[item.name] = value
    return feed


def _run(path: Path, args: argparse.Namespace) -> tuple[dict[str, np.ndarray], float, float]:
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_DISABLE_ALL
    load_started = time.perf_counter()
    session = ort.InferenceSession(
        str(path),
        sess_options=options,
        providers=["CPUExecutionProvider"],
    )
    load_seconds = time.perf_counter() - load_started
    feed = _feed(session, args)
    names = [item.name for item in session.get_outputs()]
    run_started = time.perf_counter()
    values = session.run(None, feed)
    run_seconds = time.perf_counter() - run_started
    return dict(zip(names, values)), load_seconds, run_seconds


def main() -> int:
    args = parse_args()
    if args.past_sequence_length < 0:
        raise ValueError("past sequence length must not be negative")
    original_values, original_load, original_run = _run(
        args.original.resolve(), args
    )
    candidate_values, candidate_load, candidate_run = _run(
        args.candidate.resolve(), args
    )

    original_logits = np.asarray(original_values["logits"], dtype=np.float32).reshape(-1)
    candidate_logits = np.asarray(candidate_values["logits"], dtype=np.float32).reshape(-1)
    difference = np.abs(original_logits - candidate_logits)
    denominator = np.linalg.norm(original_logits) * np.linalg.norm(candidate_logits)
    cosine = float(np.dot(original_logits, candidate_logits) / denominator)
    original_top1 = int(np.argmax(original_logits))
    candidate_top1 = int(np.argmax(candidate_logits))
    original_top5 = np.argsort(original_logits)[-5:].tolist()
    candidate_top5 = np.argsort(candidate_logits)[-5:].tolist()
    cache_results = []
    for name in sorted(key for key in original_values if key.startswith("present.")):
        left = np.asarray(original_values[name], dtype=np.float32)
        right = np.asarray(candidate_values[name], dtype=np.float32)
        delta = np.abs(left - right)
        cache_results.append(
            {
                "name": name,
                "shape": list(left.shape),
                "max_abs_diff": float(delta.max()),
                "mean_abs_diff": float(delta.mean()),
            }
        )
    report = {
        "schema_version": 1,
        "original": {
            "path": str(args.original.resolve()),
            "bytes": args.original.stat().st_size,
            "sha256": _sha256(args.original),
            "session_load_s": original_load,
            "run_s": original_run,
        },
        "candidate": {
            "path": str(args.candidate.resolve()),
            "bytes": args.candidate.stat().st_size,
            "sha256": _sha256(args.candidate),
            "session_load_s": candidate_load,
            "run_s": candidate_run,
        },
        "input": {
            "token_id": args.token_id,
            "past_sequence_length": args.past_sequence_length,
            "kv_heads": args.kv_heads,
            "head_dim": args.head_dim,
        },
        "logits": {
            "max_abs_diff": float(difference.max()),
            "mean_abs_diff": float(difference.mean()),
            "cosine": cosine,
            "top1_original": original_top1,
            "top1_candidate": candidate_top1,
            "top1_match": original_top1 == candidate_top1,
            "top5_original": original_top5,
            "top5_candidate": candidate_top5,
            "top5_intersection": len(set(original_top5) & set(candidate_top5)),
        },
        "present_kv": cache_results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(args.output.name + ".tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    print(
        json.dumps(
            {
                "logits": report["logits"],
                "kv_max_abs_diff": max(
                    item["max_abs_diff"] for item in cache_results
                ),
                "kv_mean_abs_diff": max(
                    item["mean_abs_diff"] for item in cache_results
                ),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
