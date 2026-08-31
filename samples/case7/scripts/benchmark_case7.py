#!/usr/bin/env python3
"""Measure the admitted Case7 NPU workflow against the fixed COCO-CN manifest."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import time
import urllib.request
from pathlib import Path

from embedding_backend import CHINESE_CLIP_ID, MOBILECLIP_ID, RESNET50_ID, ModelManager
from model_registry import ModelRegistry, sha256_file
from photo_index import AlbumIndex


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * q / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def measure(operation, warmup, loops, repeats):
    for _ in range(warmup):
        operation()
    samples = []
    repeat_values = []
    for _ in range(repeats):
        run = []
        for _ in range(loops):
            started = time.perf_counter()
            operation()
            run.append((time.perf_counter() - started) * 1000.0)
        samples.extend(run)
        repeat_values.append(
            {
                "average_ms": statistics.mean(run),
                "p50_ms": percentile(run, 50),
                "p95_ms": percentile(run, 95),
            }
        )
    return {
        "warmup": warmup,
        "loops": loops,
        "repeats": repeats,
        "samples": len(samples),
        "average_ms": statistics.mean(samples),
        "p50_ms": percentile(samples, 50),
        "p95_ms": percentile(samples, 95),
        "repeat_metrics": repeat_values,
    }


def memory_snapshot():
    page_size = os.sysconf("SC_PAGE_SIZE")
    rss_pages = int(Path("/proc/self/statm").read_text(encoding="ascii").split()[1])
    fields = {}
    for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
        key, value = line.split(":", 1)
        fields[key] = int(value.strip().split()[0]) * 1024
    return {
        "pid": os.getpid(),
        "rss_bytes": rss_pages * page_size,
        "mem_available_bytes": fields.get("MemAvailable"),
        "mem_total_bytes": fields.get("MemTotal"),
        "swap_total_bytes": fields.get("SwapTotal"),
    }


def npu_snapshot():
    try:
        completed = subprocess.run(
            ["npu-smi", "info"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=10,
            check=False,
        )
        return {"exit_code": completed.returncode, "output": completed.stdout}
    except OSError as exc:
        return {"error": str(exc)}


def api_text(url, query, expected_model):
    payload = json.dumps({"query": query, "model": "auto", "top_k": 3}).encode("utf-8")
    request = urllib.request.Request(
        url.rstrip("/") + "/api/search/text",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.loads(response.read().decode("utf-8"))
    if result.get("model_id") != expected_model or not result.get("results"):
        raise RuntimeError(f"API text search returned an invalid result: {result}")


def run(args):
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = manifest.get("records", [])
    if not records:
        raise RuntimeError("COCO-CN manifest has no records")
    registry = ModelRegistry(path=Path(args.registry).resolve(), require_artifacts=True)
    required = {MOBILECLIP_ID, CHINESE_CLIP_ID, RESNET50_ID}
    missing = required.difference(registry.ids())
    if missing:
        raise RuntimeError(f"benchmark requires admitted models: {sorted(missing)}")
    manager = ModelManager(registry=registry)
    index = AlbumIndex(manager=manager, allow_numpy_fallback=False)
    errors = []
    report = {
        "schema_version": 1,
        "generated_at": time.time(),
        "dataset_manifest": str(manifest_path),
        "dataset_manifest_sha256": sha256_file(manifest_path),
        "image_count": len(records),
        "protocol": {
            "single_thread": True,
            "warmup": args.warmup,
            "loops": args.loops,
            "repeats": args.repeats,
            "api_url": args.api_url,
        },
        "before": {"memory": memory_snapshot(), "npu": npu_snapshot()},
        "metrics": {},
        "errors": errors,
    }
    try:
        image = index._decode_bgr(Path(records[0]["path"]))
        vectors = {}
        for model_id in (MOBILECLIP_ID, CHINESE_CLIP_ID, RESNET50_ID):
            report["metrics"][f"image_encode:{model_id}"] = measure(
                lambda model_id=model_id: manager.encode_image(model_id, image),
                args.warmup,
                args.loops,
                args.repeats,
            )
        vectors[MOBILECLIP_ID] = manager.encode_image(MOBILECLIP_ID, image)
        report["metrics"][f"text_encode:{MOBILECLIP_ID}"] = measure(
            lambda: manager.encode_text(MOBILECLIP_ID, manifest["queries"]["en"][0]["query"]),
            args.warmup,
            args.loops,
            args.repeats,
        )
        report["metrics"][f"text_encode:{CHINESE_CLIP_ID}"] = measure(
            lambda: manager.encode_text(CHINESE_CLIP_ID, manifest["queries"]["zh"][0]["query"]),
            args.warmup,
            args.loops,
            args.repeats,
        )
        report["metrics"][f"faiss_search:{MOBILECLIP_ID}"] = measure(
            lambda: index.search_vector(vectors[MOBILECLIP_ID], MOBILECLIP_ID, 3),
            args.warmup,
            args.loops,
            args.repeats,
        )
        report["metrics"][f"similar_image:{MOBILECLIP_ID}"] = measure(
            lambda: index.search_image(image, MOBILECLIP_ID, 3),
            args.warmup,
            args.loops,
            args.repeats,
        )
        report["metrics"]["api_text:auto_en"] = measure(
            lambda: api_text(args.api_url, manifest["queries"]["en"][0]["query"], MOBILECLIP_ID),
            args.warmup,
            args.loops,
            args.repeats,
        )
        report["metrics"]["api_text:auto_zh"] = measure(
            lambda: api_text(args.api_url, manifest["queries"]["zh"][0]["query"], CHINESE_CLIP_ID),
            args.warmup,
            args.loops,
            args.repeats,
        )
    except Exception as exc:
        errors.append(str(exc))
        raise
    finally:
        report["after"] = {"memory": memory_snapshot(), "npu": npu_snapshot()}
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        index.close()
        manager.release()
    print(
        json.dumps(
            {"output": str(output), "metrics": list(report["metrics"])},
            ensure_ascii=False,
        )
    )


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--manifest", default="reports/datasets/coco_cn_case7_manifest.json")
    value.add_argument("--registry", default="models/registry.json")
    value.add_argument("--output", default="reports/benchmarks/coco_cn_case7_performance.json")
    value.add_argument("--api-url", default="http://127.0.0.1:7860")
    value.add_argument("--warmup", type=int, default=20)
    value.add_argument("--loops", type=int, default=100)
    value.add_argument("--repeats", type=int, default=3)
    return value


if __name__ == "__main__":
    run(parser().parse_args())
