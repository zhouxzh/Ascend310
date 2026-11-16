#!/usr/bin/env python3
"""Compute COCO bbox metrics for board NanoDet sweep prediction files."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--annotations-sha256", required=True)
    parser.add_argument("--predictions-root", type=Path, required=True)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from handed_nanodet_fulltest import coco_metrics

    if sha256_file(args.annotations) != args.annotations_sha256:
        raise ValueError("annotations SHA-256 mismatch")
    summary = json.loads(args.summary.read_text(encoding="utf-8"))
    if summary.get("schema_version") != 1:
        raise ValueError("unsupported summary schema")
    datasets = summary.get("dataset", {})
    if datasets.get("annotations_sha256") != args.annotations_sha256:
        raise ValueError("summary annotations SHA-256 mismatch")
    expected = summary.get("expected_models")
    models = summary.get("models", [])
    if not isinstance(expected, list) or len(expected) != 16 or len(set(expected)) != 16:
        raise ValueError("summary must declare 16 unique expected models")
    names = [item.get("name") for item in models]
    if len(names) != len(set(names)) or set(names) != set(expected):
        raise ValueError("summary model set does not match expected_models")
    if any(item.get("status") != "completed" for item in models):
        raise ValueError("summary contains incomplete models")
    root = args.predictions_root.resolve()
    results: list[dict[str, Any]] = []
    for model in summary.get("models", []):
        name = model.get("name")
        recorded = model.get("predictions", {})
        if model.get("status") != "completed" or not recorded.get("path"):
            results.append({"name": name, "status": "skipped", "reason": "missing completed predictions"})
            continue
        predictions_path = Path(recorded["path"]).resolve()
        try:
            predictions_path.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"prediction path escapes root: {predictions_path}") from exc
        if not predictions_path.is_file():
            raise ValueError(f"missing predictions: {predictions_path}")
        if sha256_file(predictions_path) != recorded.get("sha256"):
            raise ValueError(f"prediction SHA-256 mismatch: {predictions_path}")
        predictions = json.loads(predictions_path.read_text(encoding="utf-8"))
        if len(predictions) != recorded.get("count"):
            raise ValueError(f"prediction count mismatch: {predictions_path}")
        metrics = coco_metrics(args.annotations, predictions)
        results.append({
            "name": name,
            "status": "completed",
            "predictions_path": str(predictions_path),
            "metrics": metrics,
            "task_metrics": model.get("task_metrics"),
            "performance": model.get("performance"),
        })
    output = {"schema_version": 1, "kind": "nanodet_sweep_map", "models": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    failures = [item["name"] for item in results if item["status"] != "completed"]
    print(json.dumps({"models": len(results), "failures": failures}, ensure_ascii=False))
    return 0 if results and not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
