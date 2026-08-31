#!/usr/bin/env python3
"""Index the fixed COCO-CN gallery and report bilingual retrieval metrics."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path

from embedding_backend import CHINESE_CLIP_ID, MOBILECLIP_ID, ModelManager, resolve_text_model
from model_registry import ModelRegistry
from photo_index import AlbumIndex


def percentile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * q / 100.0
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = index - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def retrieval_metrics(index, query_defs, model_id, image_ids_by_path, top_k=(1, 3, 5)):
    rows = []
    for query in query_defs:
        relevant = set(query["relevant_image_ids"])
        started = time.perf_counter()
        results = index.search_text(query["query"], model_id, max(top_k))
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        ranked = [image_ids_by_path.get(Path(result.filepath).resolve()) for result in results]
        ranked = [value for value in ranked if value]
        rows.append(
            {
                "query": query["query"],
                "model_id": model_id,
                "relevant_count": len(relevant),
                "latency_ms": elapsed_ms,
                "recall": {
                    str(k): float(bool(set(ranked[:k]) & relevant)) for k in top_k
                },
                "result_image_ids": ranked,
            }
        )
    return rows


def timed_query(index, query, model_id, warmup, loops, repeats):
    for _ in range(warmup):
        index.search_text(query, model_id, 3)
    runs = []
    for _ in range(repeats):
        started = time.perf_counter()
        for _ in range(loops):
            index.search_text(query, model_id, 3)
        runs.append((time.perf_counter() - started) * 1000.0 / loops)
    return {
        "query": query,
        "model_id": model_id,
        "warmup": warmup,
        "loops": loops,
        "repeats": repeats,
        "p50_ms": percentile(runs, 50),
        "p95_ms": percentile(runs, 95),
        "repeat_average_ms": statistics.mean(runs),
        "repeat_values_ms": runs,
    }


def evaluate(args):
    manifest_path = Path(args.manifest).resolve()
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = payload.get("records", [])
    if len(records) != int(payload.get("limit", 0)):
        raise RuntimeError("manifest image count does not match its fixed limit")
    registry = ModelRegistry(path=Path(args.registry).resolve(), require_artifacts=True)
    manager = ModelManager(registry=registry)
    index = AlbumIndex(manager=manager, allow_numpy_fallback=False)
    try:
        paths = [Path(record["path"]).resolve() for record in records]
        rebuilds = {}
        for model_id in args.rebuild_model:
            if model_id not in registry.ids():
                raise RuntimeError(f"cannot rebuild a non-admitted model: {model_id}")
            rebuilds[model_id] = index.clear_model_embeddings(model_id, confirmed=True)
        summary = index.index_paths(paths, model_ids=registry.ids())
        image_ids_by_path = {
            Path(record["path"]).resolve(): record["image_id"] for record in records
        }
        retrieval = {
            "en": retrieval_metrics(
                index, payload["queries"]["en"], MOBILECLIP_ID, image_ids_by_path
            ),
            "zh": retrieval_metrics(
                index, payload["queries"]["zh"], CHINESE_CLIP_ID, image_ids_by_path
            ),
        }
        metrics = {}
        for language, rows in retrieval.items():
            metrics[language] = {
                f"recall_at_{k}": sum(row["recall"][str(k)] for row in rows) / len(rows)
                for k in (1, 3, 5)
            }
        acceptance = {
            language: {
                "recall_at_3": values["recall_at_3"],
                "minimum_recall_at_3": args.min_recall_at_3,
                "passed": values["recall_at_3"] >= args.min_recall_at_3,
            }
            for language, values in metrics.items()
        }
        representative = {
            "en": timed_query(
                index, payload["queries"]["en"][0]["query"], MOBILECLIP_ID,
                args.warmup, args.loops, args.repeats,
            ),
            "zh": timed_query(
                index, payload["queries"]["zh"][0]["query"], CHINESE_CLIP_ID,
                args.warmup, args.loops, args.repeats,
            ),
        }
        report = {
            "schema_version": 1,
            "dataset_manifest": str(manifest_path),
            "image_count": len(records),
            "index_summary": summary.to_dict(),
            "rebuilds": rebuilds,
            "metrics": metrics,
            "acceptance": acceptance,
            "queries": retrieval,
            "performance": representative,
            "protocol": {
                "warmup": args.warmup,
                "loops": args.loops,
                "repeats": args.repeats,
                "single_thread": True,
                "backend": "npu",
            },
        }
        output = Path(args.output).resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {"metrics": metrics, "acceptance": acceptance, "output": str(output)},
                ensure_ascii=False,
                indent=2,
            )
        )
        failed_languages = [language for language, result in acceptance.items() if not result["passed"]]
        if failed_languages:
            raise RuntimeError(
                "COCO-CN Recall@3 acceptance failed for: " + ", ".join(failed_languages)
            )
    finally:
        index.close()
        manager.release()


def parser():
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--manifest", default="reports/datasets/coco_cn_case7_manifest.json")
    value.add_argument("--registry", default="models/registry.json")
    value.add_argument("--output", default="reports/datasets/coco_cn_case7_retrieval.json")
    value.add_argument("--warmup", type=int, default=20)
    value.add_argument("--loops", type=int, default=100)
    value.add_argument("--repeats", type=int, default=3)
    value.add_argument("--min-recall-at-3", type=float, default=0.80)
    value.add_argument(
        "--rebuild-model",
        action="append",
        default=[],
        help="explicitly clear and regenerate one admitted model's derived vectors",
    )
    return value


if __name__ == "__main__":
    evaluate(parser().parse_args())
