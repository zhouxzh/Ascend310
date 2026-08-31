#!/usr/bin/env python3
"""Board-side performance, numerical consistency, and dataset evaluation."""

from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time
from typing import Any, Callable

try:
    import resource
except ImportError:  # Windows development hosts do not provide POSIX resource.
    resource = None

import cv2
import numpy as np

from palmprint_workbench.config import BENCHMARK_SEED, REPORT_DIR, ROOT, ensure_runtime_dirs
from palmprint_workbench.domain.registry import CandidateSpec, ModelRegistry, ModelSpec
from palmprint_workbench.runtime.adapters import (
    PalmAdapter,
    create_adapter,
    shutdown_acl_runtime,
)
from palmprint_workbench.domain.datasets import audit_archive, audit_extracted, audit_palmmatchdb_zip, records
from palmprint_workbench.domain.metrics import (
    benchmark_call,
    compare_embeddings,
    rank1_decision,
    timing_as_dict,
    verification_metrics,
)
from palmprint_workbench.domain.preprocessing import PalmPreprocessor


def _read_roi(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"Could not read image: {path}")
    return cv2.resize(image, (128, 128), interpolation=cv2.INTER_AREA)


def _offline_benchmark_spec(
    model_id: str, registry: ModelRegistry | None = None
) -> tuple[ModelSpec, CandidateSpec | None]:
    """Resolve a production model or a strictly bounded offline candidate.

    Candidates are never injected into ``ModelRegistry.all()``.  This helper
    exists solely for CLI/data evaluation and makes the candidate adapter
    contract explicit at the point where an offline backend is created.
    """

    registry = registry or ModelRegistry()
    try:
        return registry.get(model_id), None
    except KeyError:
        candidate = registry.get_candidate(model_id)
        return registry.offline_candidate_embedding_spec(candidate.id), candidate


def _build_adapter(model_id: str, backend: str, precision: str, threads: int) -> PalmAdapter:
    spec, _candidate = _offline_benchmark_spec(model_id)
    if backend == "npu":
        # Use the strict runtime factory for the NPU branch.  CPU/EDCC
        # adapters remain lazy and are loaded only for explicit offline
        # backend requests below.
        return create_adapter(spec, backend, precision, threads=threads)
    from .adapters import create_offline_adapter

    return create_offline_adapter(spec, backend, precision, threads=threads)


def _lifecycle_event(events: list[dict[str, Any]], phase: str, **details: Any) -> None:
    events.append({"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "phase": phase, **details})


def _persist_lifecycle_trace(label: str, trace: dict[str, Any]) -> str | None:
    """Persist cleanup phases even when the primary benchmark fails.

    Run reports are intentionally ignored deployment evidence.  A short,
    sanitized label keeps this helper safe for model and dataset identifiers,
    while the nanosecond suffix prevents concurrent jobs from overwriting one
    another's teardown trace.
    """

    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(label)).strip("-.") or "benchmark"
    try:
        ensure_runtime_dirs()
        run_dir = REPORT_DIR / "runs"
        run_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        suffix = time.time_ns() % 1_000_000
        path = run_dir / f"lifecycle_{safe_label}_{stamp}_{suffix:06d}.json"
        path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
        return str(path)
    except (OSError, TypeError, ValueError):
        # Cleanup diagnostics must never mask the inference exception that
        # caused the benchmark to fail.
        return None


def _close_adapters_with_lifecycle(
    adapters: list[tuple[str, PalmAdapter | None]],
) -> dict[str, Any]:
    """Close every adapter, then explicitly release the owned ACL runtime.

    Each cleanup action is independent.  A failed runner close must not stop a
    second runner from closing or skip the final runtime diagnostic.
    """

    events: list[dict[str, Any]] = []
    errors: list[str] = []
    _lifecycle_event(events, "before_close")
    try:
        for label, adapter in adapters:
            if adapter is None:
                _lifecycle_event(events, "adapter_close", adapter=label, ok=True, status="not_created")
                continue
            try:
                adapter.close()
                _lifecycle_event(events, "adapter_close", adapter=label, ok=True)
            except BaseException as exc:
                detail = f"{label}: {type(exc).__name__}: {exc}"
                errors.append(detail)
                _lifecycle_event(events, "adapter_close", adapter=label, ok=False, error=detail)
    finally:
        _lifecycle_event(events, "after_runner_close", errors=len(errors))
        _lifecycle_event(events, "before_runtime_shutdown")
        try:
            runtime = shutdown_acl_runtime()
            _lifecycle_event(
                events,
                "after_runtime_shutdown",
                ok=bool(runtime.get("ok", False)),
                status=runtime.get("status"),
                runtime=runtime,
            )
            if not runtime.get("ok", False):
                errors.append(f"runtime shutdown: {runtime.get('status', 'unknown failure')}")
        except BaseException as exc:
            detail = f"{type(exc).__name__}: {exc}"
            errors.append(f"runtime shutdown: {detail}")
            _lifecycle_event(events, "after_runtime_shutdown", ok=False, status="exception", error=detail)
    return {"ok": not errors, "events": events, "errors": errors}


def _raise_lifecycle_error(trace: dict[str, Any]) -> None:
    if trace["ok"]:
        return
    raise RuntimeError("ACL lifecycle cleanup failed: " + "; ".join(trace["errors"]))


def performance_benchmark(
    model_id: str,
    backend: str,
    precision: str,
    image_path: Path,
    *,
    threads: int,
    warmup: int,
    loops: int,
    repeats: int,
) -> dict[str, Any]:
    cv2.setNumThreads(max(1, int(threads)))
    adapter: PalmAdapter | None = None
    result: dict[str, Any] | None = None
    primary_error: BaseException | None = None
    try:
        adapter = _build_adapter(model_id, backend, precision, threads)
        roi = _read_roi(image_path)
        prepared = adapter.preprocess(roi)
        preprocessor = PalmPreprocessor()

        def pipeline() -> np.ndarray:
            rgb = cv2.cvtColor(roi, cv2.COLOR_GRAY2RGB)
            extracted = preprocessor.extract(rgb, assume_roi=True)
            if not extracted.ok or extracted.roi is None:
                raise RuntimeError(extracted.reason)
            return adapter.encode(extracted.roi).code

        pure_rounds, pure_cpu = _timed_rounds(
            lambda: adapter.encode_preprocessed(prepared), warmup, loops, repeats
        )
        pipeline_rounds, pipeline_cpu = _timed_rounds(pipeline, warmup, loops, repeats)
        probe = adapter.encode(roi)
        result = {
            "model_id": model_id,
            "backend": backend,
            "precision": precision if backend == "npu" else "fp32",
            "cpu_threads": threads if backend == "cpu" else None,
            "warmup": warmup,
            "loops": loops,
            "repeats": repeats,
            "pure_model_rounds": pure_rounds,
            "pipeline_rounds": pipeline_rounds,
            "pure_model": _median_round(pure_rounds),
            "pipeline": _median_round(pipeline_rounds),
            "pure_model_cpu_percent": pure_cpu,
            "pipeline_cpu_percent": pipeline_cpu,
            "sample_preprocess_ms": probe.preprocess_ms,
            "sample_inference_ms": probe.inference_ms,
            "peak_rss_mb": _peak_rss_mb(),
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        trace = _close_adapters_with_lifecycle([("benchmark", adapter)])
        trace_path = _persist_lifecycle_trace(model_id, trace)
        if result is not None:
            result["acl_lifecycle"] = trace
            if trace_path:
                result["acl_lifecycle_trace"] = trace_path
        elif primary_error is not None and trace_path:
            print(f"ACL lifecycle trace: {trace_path}", file=sys.stderr)
        if primary_error is None:
            _raise_lifecycle_error(trace)
    if result is None:
        raise RuntimeError("Performance benchmark did not produce a result")
    return result


def _peak_rss_mb() -> float | None:
    if resource is None:
        return None
    return float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0)


def _median_round(rounds: list[dict[str, Any]]) -> dict[str, float]:
    keys = ("mean_ms", "p50_ms", "p95_ms", "fps")
    return {key: float(np.median([float(item[key]) for item in rounds])) for key in keys}


def _timed_rounds(
    function: Callable[[], object], warmup: int, loops: int, repeats: int
) -> tuple[list[dict[str, Any]], float]:
    wall_started = time.perf_counter()
    cpu_started = time.process_time()
    rounds = [
        timing_as_dict(benchmark_call(function, warmup=warmup, loops=loops))
        for _ in range(repeats)
    ]
    wall_seconds = max(time.perf_counter() - wall_started, 1e-9)
    cpu_percent = 100.0 * (time.process_time() - cpu_started) / wall_seconds
    return rounds, float(cpu_percent)


def numerical_consistency(
    model_id: str,
    precision: str,
    dataset_id: str,
    spectrum: str,
    samples: int,
    threads: int,
) -> dict[str, Any]:
    all_records = records(dataset_id, spectrum)[:samples]
    cpu: PalmAdapter | None = None
    npu: PalmAdapter | None = None
    result: dict[str, Any] | None = None
    primary_error: BaseException | None = None
    try:
        cpu = _build_adapter(model_id, "cpu", precision, threads)
        npu = _build_adapter(model_id, "npu", precision, threads)
        cpu_codes = np.stack([cpu.encode(_read_roi(item.path)).code for item in all_records])
        npu_codes = np.stack([npu.encode(_read_roi(item.path)).code for item in all_records])
        metrics = compare_embeddings(cpu_codes, npu_codes)
        metrics.update(
            {
                "model_id": model_id,
                "precision": precision,
                "dataset_id": dataset_id,
                "samples": len(all_records),
                "pass": metrics["mean_cosine"] >= 0.999 and metrics["min_cosine"] >= 0.995,
            }
        )
        result = metrics
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        trace = _close_adapters_with_lifecycle([("cpu_reference", cpu), ("npu", npu)])
        trace_path = _persist_lifecycle_trace(f"{model_id}_numeric", trace)
        if result is not None:
            result["acl_lifecycle"] = trace
            if trace_path:
                result["acl_lifecycle_trace"] = trace_path
        elif primary_error is not None and trace_path:
            print(f"ACL lifecycle trace: {trace_path}", file=sys.stderr)
        if primary_error is None:
            _raise_lifecycle_error(trace)
    if result is None:
        raise RuntimeError("Numerical consistency check did not produce a result")
    return result


def _marker_accuracy_allowed(
    marker: Path | None,
    *,
    model_id: str,
    expected_checkpoint_sha256: str | None = None,
) -> tuple[bool, str]:
    """Require an exporter marker before reporting candidate accuracy.

    Conversion itself is not evidence of recognition accuracy.  Candidate
    checkpoints must carry a hash-verified local exporter marker, and the
    marker must name the same candidate when it records an ID.
    """

    if marker is None or not marker.is_file():
        return False, f"{model_id} export status marker is missing"
    try:
        status = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return False, f"{model_id} export status marker is unreadable: {exc}"
    if not isinstance(status, dict):
        return False, f"{model_id} export status marker is invalid"
    marker_id = status.get("candidate_id")
    if marker_id is not None and marker_id != model_id:
        return False, f"{model_id} export marker belongs to {marker_id}"
    marker_sha = status.get("checkpoint_sha256")
    if expected_checkpoint_sha256 and marker_sha != expected_checkpoint_sha256:
        return False, f"{model_id} export marker checkpoint hash does not match the candidate manifest"
    if not status.get("checkpoint_hash_verified", False):
        return False, f"{model_id} checkpoint hash was not verified during export"
    if not status.get("accuracy_eligible", False):
        return False, f"{model_id} is marked conversion-only"
    return True, "eligible"


def _accuracy_allowed(model_id: str) -> tuple[bool, str]:
    """Apply marker-based eligibility to any offline candidate embedding."""

    registry = ModelRegistry()
    try:
        spec = registry.get(model_id)
    except KeyError:
        spec = None
    if spec is not None:
        marker = spec.path("conversion_only_marker")
        if marker is None:
            return True, "eligible"
        return _marker_accuracy_allowed(marker, model_id=spec.id)

    try:
        candidate = registry.get_candidate(model_id)
    except KeyError:
        return False, f"Unknown model: {model_id}"

    try:
        registry.offline_candidate_embedding_spec(candidate.id)
    except ValueError as exc:
        return False, str(exc)
    return _marker_accuracy_allowed(
        candidate.path("conversion_marker"),
        model_id=candidate.id,
        expected_checkpoint_sha256=candidate.checkpoint_sha256,
    )


def _aggregate_identity_scores(
    raw_scores: np.ndarray,
    template_ids: list[str],
) -> dict[str, float]:
    grouped: dict[str, list[float]] = {}
    for score, identity in zip(raw_scores, template_ids):
        grouped.setdefault(identity, []).append(float(score))
    return {
        identity: float(np.mean(sorted(values, reverse=True)[:3]))
        for identity, values in grouped.items()
    }


def _score_split(
    adapter: PalmAdapter,
    encoded: dict[str, dict[int, list[np.ndarray]]],
    identities: list[str],
) -> dict[str, Any]:
    templates_list: list[np.ndarray] = []
    template_ids: list[str] = []
    for identity in identities:
        for code in encoded[identity][1][:2]:
            templates_list.append(code)
            template_ids.append(identity)
    templates = np.stack(templates_list)
    template_id_array = np.asarray(template_ids, dtype=object)
    genuine: list[float] = []
    impostor: list[float] = []
    correct = 0
    total = 0
    tied_queries = 0
    tied_candidate_total = 0
    for identity in identities:
        for query in encoded[identity][2]:
            raw = np.asarray(adapter.compare(query, templates), dtype=np.float64).reshape(-1)
            if raw.size != template_id_array.size:
                raise RuntimeError(
                    f"Backend returned {raw.size} comparison scores for {template_id_array.size} templates"
                )
            own_mask = template_id_array == identity
            genuine.extend(raw[own_mask].tolist())
            impostor.extend(raw[~own_mask].tolist())
            scores = _aggregate_identity_scores(raw, template_ids)
            predicted_identity, tied_identities = rank1_decision(scores)
            correct += predicted_identity == identity
            if len(tied_identities) > 1:
                tied_queries += 1
                tied_candidate_total += len(tied_identities)
            total += 1
    return {
        "genuine": np.asarray(genuine, dtype=np.float64),
        "impostor": np.asarray(impostor, dtype=np.float64),
        "rank1": correct / total,
        "rank1_tied_queries": tied_queries,
        "rank1_tie_rate": tied_queries / total,
        "rank1_mean_tied_candidates": tied_candidate_total / tied_queries if tied_queries else 0.0,
        "rank1_tie_break_policy": "lexicographic_identity",
        "query_count": total,
        "templates_per_identity": 2,
    }


def _identity_hash(identities: list[str]) -> str:
    return hashlib.sha256("\n".join(identities).encode("utf-8")).hexdigest()


def _domain_relation(model_id: str, dataset_id: str) -> dict[str, str]:
    registry = ModelRegistry()
    try:
        candidate = registry.get_candidate(model_id)
    except KeyError:
        candidate = None
    if candidate is not None:
        training_domain = str(candidate.raw.get("training_domain", "")).strip()
        if dataset_id.lower() in training_domain.lower():
            return {
                "classification": "in_domain_pretrained",
                "warning": (
                    f"{candidate.display_name} was trained on {training_domain}; "
                    f"{dataset_id} is in-domain evidence only."
                ),
            }
        return {
            "classification": "cross_domain_evaluation",
            "warning": (
                f"{candidate.display_name} training domain is "
                f"{training_domain or 'not recorded'}; {dataset_id} is cross-domain."
            ),
        }
    if model_id == "ccnet" and dataset_id == "tongji":
        return {
            "classification": "in_domain_pretrained",
            "warning": "CCNet Tongji results are in-domain and are not evidence of cross-domain generalization.",
        }
    if model_id == "ccnet":
        return {
            "classification": "cross_domain_evaluation",
            "warning": "This is a research-only evaluation of a restricted upstream weight.",
        }
    if model_id == "edcc":
        return {
            "classification": "training_free_baseline",
            "warning": "EDCC is a traditional baseline, not a learned pretrained model.",
        }
    return {
        "classification": "conversion_only",
        "warning": "No hash-verified official pretrained checkpoint is available.",
    }


def _project_revision() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return "unavailable"
    return completed.stdout.strip() if completed.returncode == 0 else "unavailable"


def evaluate_dataset(
    model_id: str,
    backend: str,
    precision: str,
    dataset_id: str,
    spectrum: str,
    threads: int,
    max_identities: int | None,
    progress: Callable[[float, str], None] | None = None,
) -> dict[str, Any]:
    allowed, reason = _accuracy_allowed(model_id)
    if not allowed:
        raise RuntimeError(reason)
    archive_audit = audit_archive(dataset_id)
    extracted_audit = audit_extracted(dataset_id, spectrum)
    if not archive_audit.get("integrity_ok"):
        raise RuntimeError(f"Dataset archive integrity check failed: {dataset_id}")
    if not extracted_audit.get("ready"):
        raise RuntimeError(f"Dataset structure audit failed: {extracted_audit}")
    parsed = records(dataset_id, spectrum)
    identities = sorted({item.identity for item in parsed})
    rng = np.random.default_rng(BENCHMARK_SEED)
    rng.shuffle(identities)
    if max_identities:
        identities = identities[:max_identities]
    # The 20% calibration split needs at least two identities so that a
    # genuine query also has an impostor identity for verification metrics.
    if len(identities) < 10:
        raise ValueError("At least ten palm identities are required for the calibration split")
    selected = set(identities)
    chosen = [item for item in parsed if item.identity in selected]
    adapter: PalmAdapter | None = None
    result: dict[str, Any] | None = None
    primary_error: BaseException | None = None
    encoded: dict[str, dict[int, list[np.ndarray]]] = {
        identity: {1: [], 2: []} for identity in identities
    }
    started = time.perf_counter()
    try:
        adapter = _build_adapter(model_id, backend, precision, threads)
        for index, item in enumerate(chosen):
            encoded[item.identity][item.session].append(adapter.encode(_read_roi(item.path)).code)
            if progress and (index % 20 == 0 or index + 1 == len(chosen)):
                progress((index + 1) / len(chosen), f"Encoding {index + 1}/{len(chosen)}")
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
                "closed_set_rank1_tied_queries": int(test_scores["rank1_tied_queries"]),
                "closed_set_rank1_tie_rate": float(test_scores["rank1_tie_rate"]),
                "closed_set_rank1_mean_tied_candidates": float(
                    test_scores["rank1_mean_tied_candidates"]
                ),
                "closed_set_rank1_tie_break_policy": str(test_scores["rank1_tie_break_policy"]),
                "known_genuine_reject_rate_at_verification_threshold": float(
                    (test_scores["genuine"] < threshold).mean()
                ),
            }
        )
        result = {
            "model_id": model_id,
            "backend": backend,
            "precision": precision if backend == "npu" else "fp32",
            "dataset_id": dataset_id,
            "spectrum": spectrum if dataset_id == "polyu" else None,
            "seed": BENCHMARK_SEED,
            "project_git_revision": _project_revision(),
            "domain_relation": _domain_relation(model_id, dataset_id),
            "protocol_version": "palmprint-verification-v2",
            "identity_definition": "archive-derived palm index; person/left-right mapping is not independently verified",
            "verification_protocol": "1:1 all-pair session-2 queries against two session-1 templates per identity",
            "identification_protocol": "closed-set 1:N Rank-1 over test identities only",
            "open_set_rejection": {
                "status": "not_evaluated",
                "reason": "No same-gallery-size, disjoint unknown-probe protocol is available in the current archives.",
            },
            "registration_samples_per_identity": 2,
            "identities": len(identities),
            "calibration_identities": len(calibration_ids),
            "test_identities": len(test_ids),
            "calibration_identity_ids": calibration_ids,
            "test_identity_ids": test_ids,
            "calibration_identity_sha256": _identity_hash(calibration_ids),
            "test_identity_sha256": _identity_hash(test_ids),
            "query_samples": int(test_scores["query_count"]),
            "dataset_audit": {"archive": archive_audit, "extracted": extracted_audit},
            "metrics": metrics,
            "calibration_metrics": calibration,
            "elapsed_seconds": time.perf_counter() - started,
        }
    except BaseException as exc:
        primary_error = exc
        raise
    finally:
        trace = _close_adapters_with_lifecycle([("evaluation", adapter)])
        trace_path = _persist_lifecycle_trace(f"{model_id}_{dataset_id}_evaluation", trace)
        if result is not None:
            result["acl_lifecycle"] = trace
            if trace_path:
                result["acl_lifecycle_trace"] = trace_path
        elif primary_error is not None and trace_path:
            print(f"ACL lifecycle trace: {trace_path}", file=sys.stderr)
        if primary_error is None:
            _raise_lifecycle_error(trace)
    if result is None:
        raise RuntimeError("Dataset evaluation did not produce a result")
    return result


def write_report(result: dict[str, Any], stem: str) -> dict[str, str]:
    ensure_runtime_dirs()
    run_dir = REPORT_DIR / "runs"
    run_dir.mkdir(parents=True, exist_ok=True)
    json_path = run_dir / f"{stem}.json"
    csv_path = run_dir / f"{stem}.csv"
    markdown_path = run_dir / f"{stem}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    flat = _flatten(result)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["field", "value"])
        writer.writerows(sorted(flat.items()))
    rows = [f"| {key} | {value} |" for key, value in sorted(flat.items())]
    markdown_path.write_text(
        "# Palmprint Run Report\n\n| Field | Value |\n| --- | --- |\n" + "\n".join(rows) + "\n",
        encoding="utf-8",
    )
    return {"json": str(json_path), "csv": str(csv_path), "markdown": str(markdown_path)}


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else str(key)
            result.update(_flatten(item, path))
        return result
    if isinstance(value, list):
        return {prefix: json.dumps(value, ensure_ascii=False)}
    return {prefix: value}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("--dataset", choices=["tongji", "polyu", "palmmatchdb", "all"], default="all")
    audit.add_argument("--spectrum", default="B")
    perf = sub.add_parser("performance")
    _common_model_args(perf)
    perf.add_argument("--image", required=True)
    perf.add_argument("--warmup", type=int, default=50)
    perf.add_argument("--loops", type=int, default=500)
    perf.add_argument("--repeats", type=int, default=5)
    compare = sub.add_parser("compare")
    _common_model_args(compare, include_backend=False)
    compare.add_argument("--dataset", choices=["tongji", "polyu"], required=True)
    compare.add_argument("--spectrum", default="B")
    compare.add_argument("--samples", type=int, default=100)
    evaluate = sub.add_parser("evaluate")
    _common_model_args(evaluate)
    evaluate.add_argument("--dataset", choices=["tongji", "polyu"], required=True)
    evaluate.add_argument("--spectrum", default="B")
    evaluate.add_argument("--max-identities", type=int, default=None)
    return parser.parse_args()


def _benchmark_model_choices() -> list[str]:
    """Expose runtime models plus explicit offline models and candidates."""

    registry = ModelRegistry()
    runtime_ids = [spec.id for spec in registry.all()]
    offline_ids = [spec.id for spec in registry.offline_models()]
    candidate_ids = registry.offline_candidate_embedding_ids()
    choices = runtime_ids + [item for item in offline_ids if item not in runtime_ids]
    return choices + [item for item in candidate_ids if item not in choices]


def _common_model_args(parser: argparse.ArgumentParser, include_backend: bool = True) -> None:
    parser.add_argument("--model", choices=_benchmark_model_choices(), required=True)
    if include_backend:
        parser.add_argument("--backend", choices=["cpu", "npu"], default="cpu")
    parser.add_argument("--precision", choices=["origin", "mixed_fp16"], default="mixed_fp16")
    parser.add_argument("--threads", type=int, choices=[1, 4], default=4)


def main() -> int:
    args = parse_args()
    ensure_runtime_dirs()
    if args.command == "audit":
        ids = ["tongji", "polyu", "palmmatchdb"] if args.dataset == "all" else [args.dataset]
        result = []
        for dataset_id in ids:
            item = audit_archive(dataset_id)
            if dataset_id == "palmmatchdb":
                item["zip_audit"] = audit_palmmatchdb_zip(archive_audit=item)
            else:
                item["extracted"] = audit_extracted(dataset_id, args.spectrum)
            result.append(item)
        payload: Any = result
        stem = "dataset_audit"
    elif args.command == "performance":
        payload = performance_benchmark(
            args.model,
            args.backend,
            args.precision,
            Path(args.image),
            threads=args.threads,
            warmup=args.warmup,
            loops=args.loops,
            repeats=args.repeats,
        )
        precision_label = args.precision if args.backend == "npu" else "fp32"
        stem = f"perf_{args.model}_{args.backend}_{precision_label}_t{args.threads}"
    elif args.command == "compare":
        payload = numerical_consistency(
            args.model, args.precision, args.dataset, args.spectrum, args.samples, args.threads
        )
        stem = f"compare_{args.model}_{args.precision}_{args.dataset}"
    else:
        payload = evaluate_dataset(
            args.model,
            args.backend,
            args.precision,
            args.dataset,
            args.spectrum,
            args.threads,
            args.max_identities,
        )
        stem = f"eval_{args.model}_{args.backend}_{args.dataset}"
    report = write_report(payload, stem)
    print(json.dumps({"result": payload, "reports": report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
