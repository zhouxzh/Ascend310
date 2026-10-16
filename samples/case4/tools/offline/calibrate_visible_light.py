"""Calibrate the visible-light palmprint matcher from private embeddings.

The input intentionally contains embeddings rather than biometric images.  It
is generated on the target board after the five enrollment and two independent
probe sessions have been encoded with the admitted NPU model.  This keeps the
calibration report reproducible without creating a second image archive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time
from typing import Any

import numpy as np

from palmprint_workbench.config import ROI_PIPELINE_ID
from palmprint_workbench.domain.metrics import rank1_decision, verification_metrics


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Unable to read embeddings manifest: {path}") from exc
    if not isinstance(value, dict) or not isinstance(value.get("records"), list):
        raise ValueError("Embeddings manifest must contain a records list")
    return value


def _normalized_embedding(value: Any) -> np.ndarray:
    array = np.asarray(value, dtype=np.float32).reshape(-1)
    if array.size == 0 or not np.isfinite(array).all():
        raise ValueError("Each embedding must be a finite non-empty vector")
    norm = float(np.linalg.norm(array))
    if norm <= 1e-12:
        raise ValueError("Embedding norm must be non-zero")
    return array / norm


def _aggregate(scores: np.ndarray) -> float:
    return float(np.mean(np.sort(scores)[-min(3, scores.size) :]))


def _distribution(values: np.ndarray) -> dict[str, Any]:
    values = np.asarray(values, dtype=np.float64).reshape(-1)
    counts, edges = np.histogram(values, bins=20, range=(-1.0, 1.0))
    return {
        "count": int(values.size),
        "min": float(values.min()),
        "max": float(values.max()),
        "mean": float(values.mean()),
        "p05": float(np.percentile(values, 5)),
        "p50": float(np.percentile(values, 50)),
        "p95": float(np.percentile(values, 95)),
        "histogram": {
            "edges": [float(value) for value in edges],
            "counts": [int(value) for value in counts],
        },
    }


def calibrate(payload: dict[str, Any]) -> dict[str, Any]:
    """Calculate verification and 1:N metrics from a checked manifest."""

    templates: dict[str, list[np.ndarray]] = {}
    probes: dict[str, list[np.ndarray]] = {}
    dimensions: set[int] = set()
    for record in payload["records"]:
        if not isinstance(record, dict):
            raise ValueError("Embedding records must be objects")
        palm_id = str(record.get("palm_id", "")).strip()
        role = str(record.get("role", "")).strip().lower()
        if not palm_id or role not in {"enroll", "probe"}:
            raise ValueError("Every record requires palm_id and role=enroll|probe")
        code = _normalized_embedding(record.get("embedding"))
        dimensions.add(int(code.size))
        (templates if role == "enroll" else probes).setdefault(palm_id, []).append(code)
    if len(dimensions) != 1:
        raise ValueError("All embeddings must have the same dimension")
    identities = sorted(set(templates) & set(probes))
    if len(identities) < 20:
        raise ValueError("Calibration requires at least 20 independent palm identities")
    missing_templates = [identity for identity in identities if len(templates[identity]) < 5]
    missing_probes = [identity for identity in identities if len(probes[identity]) < 10]
    if missing_templates:
        raise ValueError(f"Each palm needs 5 enrollment samples; missing: {missing_templates[:3]}")
    if missing_probes:
        raise ValueError(f"Each palm needs 10 probe samples; missing: {missing_probes[:3]}")
    template_matrix = {
        identity: np.stack(templates[identity], axis=0) for identity in identities
    }
    genuine: list[float] = []
    impostor: list[float] = []
    top1_hits = 0
    probe_count = 0
    for actual in identities:
        for probe in probes[actual]:
            identity_scores = {
                candidate: _aggregate(template_matrix[candidate] @ probe)
                for candidate in identities
            }
            genuine.append(identity_scores[actual])
            impostor.extend(
                score for candidate, score in identity_scores.items() if candidate != actual
            )
            winner, _ = rank1_decision(identity_scores)
            top1_hits += int(winner == actual)
            probe_count += 1
    genuine_array = np.asarray(genuine, dtype=np.float64)
    impostor_array = np.asarray(impostor, dtype=np.float64)
    verification = verification_metrics(genuine_array, impostor_array)
    threshold = float(verification["threshold"])
    return {
        "protocol_version": "visible-light-palm-calibration-v1",
        "identity_definition": "palm_id is person_id:left or person_id:right; both sides are separate non-match identities",
        "identity_count": len(identities),
        "enrollment_samples_per_palm_min": min(len(templates[item]) for item in identities),
        "probe_samples_per_palm_min": min(len(probes[item]) for item in identities),
        "embedding_dimension": dimensions.pop(),
        "verification": verification,
        "recommended_threshold": threshold,
        "far_at_recommended_threshold": float((impostor_array >= threshold).mean()),
        "frr_at_recommended_threshold": float((genuine_array < threshold).mean()),
        "top1": float(top1_hits / max(probe_count, 1)),
        "probe_count": probe_count,
        "score_distributions": {
            "genuine": _distribution(genuine_array),
            "impostor": _distribution(impostor_array),
        },
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--embeddings", type=Path, required=True, help="Private JSON embeddings manifest")
    parser.add_argument("--output", type=Path, required=True, help="Write calibration JSON here")
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--camera-resolution", required=True, help="Actual runtime capture resolution, e.g. 1280x720")
    parser.add_argument("--om", type=Path, required=True, help="Admitted mixed-FP16 OM used to create embeddings")
    parser.add_argument("--approve", action="store_true", help="Mark this report eligible for runtime threshold decisions")
    args = parser.parse_args(argv)
    if not args.om.is_file():
        raise SystemExit(f"OM does not exist: {args.om}")
    source = _read_json(args.embeddings)
    report = calibrate(source)
    report.update(
        {
            "approved": bool(args.approve),
            "model_id": args.model_id,
            "backend": "npu",
            "precision": "mixed_fp16",
            "roi_pipeline_id": ROI_PIPELINE_ID,
            "camera_resolution": args.camera_resolution,
            "om": {
                "path": str(args.om),
                "bytes": args.om.stat().st_size,
                "sha256": _sha256(args.om),
            },
            "source_manifest_sha256": _sha256(args.embeddings),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "approved": report["approved"], "top1": report["top1"], "eer": report["verification"]["eer"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
