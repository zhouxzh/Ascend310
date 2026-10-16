#!/usr/bin/env python3
"""Research-only PPNet Tongji probe for an existing 512-D OM asset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

import cv2
import numpy as np

from palmprint_workbench.config import BENCHMARK_SEED, ROOT
from palmprint_workbench.domain.datasets import records
from palmprint_workbench.domain.metrics import verification_metrics
from palmprint_workbench.domain.registry import ModelSpec
from palmprint_workbench.runtime.adapters import OmEmbeddingAdapter, shutdown_acl_runtime
from tools.offline.benchmark import _score_split


def _read(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"unable to read image: {path}")
    return cv2.resize(image, (128, 128), interpolation=cv2.INTER_AREA)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-identities", type=int, default=100)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    all_records = records("tongji")
    identities = sorted({item.identity for item in all_records})
    rng = np.random.default_rng(BENCHMARK_SEED)
    rng.shuffle(identities)
    identities = identities[: max(10, int(args.max_identities))]
    selected = set(identities)
    encoded: dict[str, dict[int, list[np.ndarray]]] = {
        identity: {1: [], 2: []} for identity in identities
    }
    spec = ModelSpec(
        id="ppnet",
        display_name="PPNet Tongji feature probe",
        kind="embedding",
        license="CC-BY-NC-4.0",
        research_only=True,
        source="https://github.com/xuliangcs/ppnet",
        revision="main",
        input_shape=(1, 1, 128, 128),
        input_range="nonzero_standardize",
        feature_dim=512,
        metric="cosine",
        raw={
            "om_models": {
                "mixed_fp16": "models/candidates/ppnet/om/ppnet_tongji_600_mixed_fp16.om"
            }
        },
    )
    adapter = None
    started = time.perf_counter()
    result: dict[str, object]
    try:
        adapter = OmEmbeddingAdapter(spec, "mixed_fp16")
        chosen = [item for item in all_records if item.identity in selected]
        for item in chosen:
            encoded[item.identity][item.session].append(adapter.encode(_read(item.path)).code)
        split = max(1, int(round(len(identities) * 0.2)))
        calibration_ids = identities[:split]
        test_ids = identities[split:]
        calibration_scores = _score_split(adapter, encoded, calibration_ids)
        calibration = verification_metrics(
            calibration_scores["genuine"], calibration_scores["impostor"]
        )
        test_scores = _score_split(adapter, encoded, test_ids)
        metrics = verification_metrics(test_scores["genuine"], test_scores["impostor"])
        threshold = float(calibration["threshold"])
        metrics.update(
            {
                "calibrated_threshold": threshold,
                "verification_far_at_calibrated_threshold": float(
                    (test_scores["impostor"] >= threshold).mean()
                ),
                "verification_frr_at_calibrated_threshold": float(
                    (test_scores["genuine"] < threshold).mean()
                ),
                "closed_set_rank1": float(test_scores["rank1"]),
            }
        )
        result = {
            "schema_version": 1,
            "candidate_id": "ppnet",
            "backend": "npu",
            "precision": "mixed_fp16",
            "dataset_id": "tongji",
            "identities": len(identities),
            "query_samples": int(test_scores["query_count"]),
            "calibration_metrics": calibration,
            "metrics": metrics,
            "elapsed_seconds": time.perf_counter() - started,
            "qualification": "research-only OM probe; not production admission",
        }
    finally:
        if adapter is not None:
            adapter.close()
        shutdown_acl_runtime()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
