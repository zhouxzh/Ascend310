#!/usr/bin/env python3
"""Compute provenance-bound COCO metrics for complete trained-PicoDet reports."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

EXPECTED_MODELS = {"picodet-xs-320-seed-42", "picodet-xs-416-seed-42"}
DATASET_FIELDS = ("images", "annotations", "annotations_sha256", "image_ids_sha256")


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


def require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value.lower()):
        raise ValueError(f"{label} must be a SHA-256 hex digest")
    return value.lower()


def summary_source(summary: dict[str, Any], summary_path: Path, annotations_sha256: str) -> dict[str, Any]:
    kind = summary.get("kind")
    if kind not in {"picodet_palm_onnx_fulltest", "picodet_palm_om_fulltest"}:
        raise ValueError("unsupported PicoDet summary")
    if summary.get("schema_version") != 1:
        raise ValueError("unsupported summary schema")
    dataset = summary.get("dataset")
    if not isinstance(dataset, dict) or any(field not in dataset for field in DATASET_FIELDS):
        raise ValueError("summary dataset provenance is incomplete")
    if dataset["annotations_sha256"] != annotations_sha256:
        raise ValueError("summary annotations SHA-256 mismatch")
    if not isinstance(dataset["images"], int) or not isinstance(dataset["annotations"], int):
        raise ValueError("summary dataset counts are invalid")
    image_ids_sha256 = require_sha256(dataset["image_ids_sha256"], "image_ids_sha256")
    manifest = summary.get("manifest")
    if not isinstance(manifest, dict):
        raise ValueError("summary manifest provenance is missing")
    manifest_sha256 = require_sha256(manifest.get("sha256"), "manifest SHA-256")
    contracts_sha256 = require_sha256(summary.get("contracts_sha256"), "contracts SHA-256")
    source: dict[str, Any] = {
        "summary_kind": kind,
        "summary_path": str(summary_path.resolve()),
        "summary_sha256": sha256_file(summary_path),
        "dataset": {
            "images": dataset["images"],
            "annotations": dataset["annotations"],
            "annotations_sha256": annotations_sha256,
            "image_ids_sha256": image_ids_sha256,
        },
        "manifest_sha256": manifest_sha256,
        "contracts_sha256": contracts_sha256,
    }
    if kind == "picodet_palm_om_fulltest":
        if summary.get("runtime_cleanup_ok") is not True:
            raise ValueError("OM runtime cleanup is missing or failed")
        atc_summary = summary.get("atc_summary")
        atc_recipe = summary.get("atc_recipe")
        if not isinstance(atc_summary, dict) or not isinstance(atc_recipe, dict):
            raise ValueError("OM ATC provenance is missing")
        if atc_recipe.get("precision_mode") != "allow_mix_precision":
            raise ValueError("OM was not converted with allow_mix_precision")
        if atc_recipe.get("soc_version") not in {"Ascend310B1", "Ascend310B4"}:
            raise ValueError("OM ATC SoC is invalid")
        source["atc"] = {
            "summary_sha256": require_sha256(atc_summary.get("sha256"), "ATC summary SHA-256"),
            "recipe": atc_recipe,
        }
    return source


def resolve_prediction(root: Path, recorded: dict[str, Any]) -> Path:
    relative_path = recorded.get("relative_path")
    if isinstance(relative_path, str) and relative_path:
        candidate = Path(relative_path)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise ValueError(f"unsafe relative prediction path: {relative_path!r}")
        path = (root / candidate).resolve()
    else:
        path = Path(recorded.get("path", "")).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"prediction path escapes root: {path}") from exc
    return path


def main() -> int:
    args = parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from handed_nanodet_fulltest import coco_metrics

    if sha256_file(args.annotations) != args.annotations_sha256:
        raise ValueError("annotations SHA-256 mismatch")
    summary_path = args.summary.resolve()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    source = summary_source(summary, summary_path, args.annotations_sha256)
    expected = summary.get("expected_models")
    models = summary.get("models", [])
    if not isinstance(expected, list) or set(expected) != EXPECTED_MODELS or len(expected) != len(EXPECTED_MODELS):
        raise ValueError("summary must declare exactly the two expected PicoDet models")
    names = [item.get("name") for item in models]
    if len(names) != len(set(names)) or set(names) != EXPECTED_MODELS:
        raise ValueError("summary model set does not match expected_models")
    if any(item.get("status") != "completed" for item in models):
        raise ValueError("summary contains incomplete models")
    if source["summary_kind"] == "picodet_palm_om_fulltest":
        if any(item.get("cleanup_ok") is not True for item in models):
            raise ValueError("OM summary contains failed or missing per-model ACL cleanup")
        for model in models:
            smoke = model.get("reference_smoke")
            agreement = smoke.get("agreement") if isinstance(smoke, dict) else None
            preprocess = smoke.get("preprocess_agreement") if isinstance(smoke, dict) else None
            if not isinstance(agreement, dict) or agreement.get("samples") != 16:
                raise ValueError(f"{model['name']} lacks complete OM numerical smoke")
            if not isinstance(preprocess, dict) or preprocess.get("samples") != 16:
                raise ValueError(f"{model['name']} lacks complete preprocessing smoke")
    root = args.predictions_root.resolve()
    results: list[dict[str, Any]] = []
    for model in models:
        name = model["name"]
        onnx_sha256 = require_sha256(model.get("onnx_sha256"), f"{name} ONNX SHA-256")
        recorded = model.get("predictions")
        if not isinstance(recorded, dict):
            raise ValueError(f"{name} predictions provenance is missing")
        path = resolve_prediction(root, recorded)
        prediction_sha256 = require_sha256(recorded.get("sha256"), f"{name} prediction SHA-256")
        if not path.is_file() or sha256_file(path) != prediction_sha256:
            raise ValueError(f"prediction integrity mismatch: {path}")
        predictions = json.loads(path.read_text(encoding="utf-8"))
        if len(predictions) != recorded.get("count"):
            raise ValueError(f"prediction count mismatch: {path}")
        result: dict[str, Any] = {
            "name": name,
            "status": "completed",
            "onnx_sha256": onnx_sha256,
            "predictions_path": str(path),
            "predictions_sha256": prediction_sha256,
            "predictions_count": recorded["count"],
            "metrics": coco_metrics(args.annotations, predictions),
            "task_metrics": model.get("task_metrics"),
            "performance": model.get("performance"),
        }
        if source["summary_kind"] == "picodet_palm_om_fulltest":
            result["om_sha256"] = require_sha256(model.get("om_sha256"), f"{name} OM SHA-256")
            result["numerical_gate_ok"] = model.get("numerical_gate_ok")
            result["reference_smoke"] = model.get("reference_smoke")
        results.append(result)
    result = {"schema_version": 1, "kind": "picodet_palm_map", "source": source, "models": results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"models": len(results), "failures": []}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
