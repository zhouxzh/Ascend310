"""NPU-only evaluation services used by the production API.

This module intentionally contains the small evaluation surface needed by the
interactive service. Research baselines, candidate adapters, and numerical
cross-backend comparisons stay outside the serving package. Every entry point
revalidates the production backend, precision, and registry admission so a
caller cannot bypass the HTTP validation layer by invoking the service
directly.
"""

from __future__ import annotations

import csv
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

from ..config import (
    BENCHMARK_SEED,
    PRODUCTION_BACKEND,
    PRODUCTION_PRECISION,
    REPORT_DIR,
    ROOT,
    ensure_runtime_dirs,
)
from ..domain.admission import resolve_runtime_model
from ..domain.datasets import audit_archive, audit_extracted, records
from ..domain.metrics import benchmark_call, rank1_decision, timing_as_dict, verification_metrics
from ..domain.preprocessing import PalmPreprocessor
from ..runtime.adapters import (
    PalmAdapter,
    acl_runtime_status,
    create_adapter,
    shutdown_acl_runtime,
)


_SAFE_STEM = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def _require_npu(backend: str, precision: str) -> None:
    """Enforce the exact production inference contract."""

    if backend != PRODUCTION_BACKEND or precision != PRODUCTION_PRECISION:
        raise ValueError(
            "Production evaluation supports only backend=npu and precision=mixed_fp16"
        )


def _production_spec(model_id: str):
    """Resolve an admitted embedding without consulting offline inventory."""

    spec = resolve_runtime_model(model_id, verify_assets=True)
    if spec.kind != "embedding":
        raise ValueError(f"Production evaluation requires an embedding model: {model_id}")
    if spec.input_shape != (1, 1, 128, 128):
        raise ValueError(f"Unsupported production input shape for {model_id}: {spec.input_shape}")
    if spec.metric != "cosine":
        raise ValueError(f"Unsupported production metric for {model_id}: {spec.metric}")
    return spec


def _build_adapter(model_id: str, backend: str, precision: str, threads: int) -> PalmAdapter:
    """Create only the admitted mixed-FP16 OM adapter.

    ``threads`` is retained for API compatibility; NPU execution does not
    select a CPU execution provider.
    """

    _require_npu(backend, precision)
    if int(threads) < 1:
        raise ValueError("threads must be >= 1")
    return _create_npu_adapter(_production_spec(model_id), threads)


def _create_npu_adapter(spec: Any, threads: int) -> PalmAdapter:
    if int(threads) < 1:
        raise ValueError("threads must be >= 1")
    return create_adapter(spec, PRODUCTION_BACKEND, PRODUCTION_PRECISION, threads=threads)


def _read_roi(path: Path) -> np.ndarray:
    image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise ValueError(f"Could not read image: {path}")
    return cv2.resize(image, (128, 128), interpolation=cv2.INTER_AREA)


def _lifecycle_event(events: list[dict[str, Any]], phase: str, **details: Any) -> None:
    events.append({"timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "phase": phase, **details})


def _persist_lifecycle_trace(label: str, trace: dict[str, Any]) -> str | None:
    """Persist teardown diagnostics without masking an inference failure."""

    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(label)).strip("-.") or "evaluation"
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
        return None


def _close_adapters_with_lifecycle(
    adapters: list[tuple[str, PalmAdapter | None]],
    *,
    baseline_active_runners: int | None = None,
) -> dict[str, Any]:
    """Release every runner independently before process ACL shutdown."""

    events: list[dict[str, Any]] = []
    errors: list[str] = []
    _lifecycle_event(
        events,
        "before_close",
        baseline_active_runners=baseline_active_runners,
    )
    try:
        for label, adapter in adapters:
            if adapter is None:
                _lifecycle_event(
                    events, "adapter_close", adapter=label, ok=True, status="not_created"
                )
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
            # A long-lived Workbench recognition adapter may legitimately own
            # the process runtime.  Defer global shutdown in that case, but
            # treat any count above the baseline as a leaked evaluation runner.
            if (
                not runtime.get("ok", False)
                and runtime.get("status") == "blocked_active_runners"
                and baseline_active_runners is not None
            ):
                current_active = int(acl_runtime_status().get("active_runners", 0))
                if current_active == int(baseline_active_runners):
                    runtime = {
                        **runtime,
                        "ok": True,
                        "status": "deferred_active_runners",
                        "deferred": True,
                    }
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
            _lifecycle_event(
                events, "after_runtime_shutdown", ok=False, status="exception", error=detail
            )
    return {"ok": not errors, "events": events, "errors": errors}


def _raise_lifecycle_error(trace: dict[str, Any]) -> None:
    if not trace["ok"]:
        raise RuntimeError("ACL lifecycle cleanup failed: " + "; ".join(trace["errors"]))


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
    if warmup < 0 or loops < 1 or repeats < 1:
        raise ValueError("warmup must be >= 0; loops and repeats must be >= 1")
    wall_started = time.perf_counter()
    cpu_started = time.process_time()
    rounds = [
        timing_as_dict(benchmark_call(function, warmup=warmup, loops=loops))
        for _ in range(repeats)
    ]
    wall_seconds = max(time.perf_counter() - wall_started, 1e-9)
    cpu_percent = 100.0 * (time.process_time() - cpu_started) / wall_seconds
    return rounds, float(cpu_percent)


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
    """Measure the admitted OM's model and end-to-end NPU pipeline latency."""

    _require_npu(backend, precision)
    spec = _production_spec(model_id)
    cv2.setNumThreads(max(1, int(threads)))
    adapter: PalmAdapter | None = None
    result: dict[str, Any] | None = None
    primary_error: BaseException | None = None
    baseline_active_runners = int(acl_runtime_status().get("active_runners", 0))
    try:
        adapter = _create_npu_adapter(spec, threads)
        roi = _read_roi(Path(image_path))
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
            "backend": PRODUCTION_BACKEND,
            "precision": PRODUCTION_PRECISION,
            "cpu_threads": None,
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
        trace = _close_adapters_with_lifecycle(
            [("benchmark", adapter)], baseline_active_runners=baseline_active_runners
        )
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


def _aggregate_identity_scores(raw_scores: np.ndarray, template_ids: list[str]) -> dict[str, float]:
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
                    f"Backend returned {raw.size} comparison scores for "
                    f"{template_id_array.size} templates"
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
    if total == 0:
        raise ValueError("At least one query is required for the evaluation split")
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
    if model_id == "ccnet" and dataset_id == "tongji":
        return {
            "classification": "in_domain_pretrained",
            "warning": (
                "CCNet Tongji results are in-domain and are not evidence of "
                "cross-domain generalization."
            ),
        }
    return {
        "classification": "cross_domain_evaluation",
        "warning": (
            "The production NPU model's training-domain relation must be interpreted "
            "from its registry evidence."
        ),
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
    """Run the bounded verification protocol with an admitted NPU OM."""

    _require_npu(backend, precision)
    # Resolve before touching the dataset so an unknown or unadmitted model
    # cannot produce a report that looks like a production result.
    production_spec = _production_spec(model_id)
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
    if len(identities) < 10:
        raise ValueError("At least ten palm identities are required for the calibration split")
    selected = set(identities)
    chosen = [item for item in parsed if item.identity in selected]
    adapter: PalmAdapter | None = None
    result: dict[str, Any] | None = None
    primary_error: BaseException | None = None
    baseline_active_runners = int(acl_runtime_status().get("active_runners", 0))
    encoded: dict[str, dict[int, list[np.ndarray]]] = {
        identity: {1: [], 2: []} for identity in identities
    }
    started = time.perf_counter()
    try:
        adapter = _create_npu_adapter(production_spec, threads)
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
            "backend": PRODUCTION_BACKEND,
            "precision": PRODUCTION_PRECISION,
            "dataset_id": dataset_id,
            "spectrum": spectrum if dataset_id == "polyu" else None,
            "seed": BENCHMARK_SEED,
            "project_git_revision": _project_revision(),
            "domain_relation": _domain_relation(model_id, dataset_id),
            "protocol_version": "palmprint-verification-v2",
            "identity_definition": (
                "archive-derived palm index; person/left-right mapping is not "
                "independently verified"
            ),
            "verification_protocol": (
                "1:1 all-pair session-2 queries against two session-1 templates "
                "per identity"
            ),
            "identification_protocol": "closed-set 1:N Rank-1 over test identities only",
            "open_set_rejection": {
                "status": "not_evaluated",
                "reason": (
                    "No same-gallery-size, disjoint unknown-probe protocol is "
                    "available in the current archives."
                ),
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
        trace = _close_adapters_with_lifecycle(
            [("evaluation", adapter)], baseline_active_runners=baseline_active_runners
        )
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


def _validate_report_contract(value: Any) -> None:
    """Reject a report carrying a non-production inference contract."""

    if isinstance(value, dict):
        if "backend" in value and value["backend"] != PRODUCTION_BACKEND:
            raise ValueError("Production reports may contain only backend=npu")
        if "precision" in value and value["precision"] != PRODUCTION_PRECISION:
            raise ValueError("Production reports may contain only precision=mixed_fp16")
        for item in value.values():
            _validate_report_contract(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_report_contract(item)


def write_report(result: dict[str, Any], stem: str) -> dict[str, str]:
    """Write an API-owned report using a path-safe stem."""

    if not isinstance(result, dict):
        raise TypeError("result must be a mapping")
    _validate_report_contract(result)
    if not isinstance(stem, str) or not _SAFE_STEM.fullmatch(stem):
        raise ValueError("report stem must be a simple ASCII filename component")
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
        "# Palmprint Run Report\n\n| Field | Value |\n| --- | --- |\n"
        + "\n".join(rows)
        + "\n",
        encoding="utf-8",
    )
    return {"json": str(json_path), "csv": str(csv_path), "markdown": str(markdown_path)}


__all__ = ["evaluate_dataset", "performance_benchmark", "write_report"]
