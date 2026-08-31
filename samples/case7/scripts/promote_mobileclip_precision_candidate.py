#!/usr/bin/env python3
"""Promote a passed MobileCLIP image precision candidate on the board.

The command is deliberately conservative: without ``--apply`` it only
validates the sweep evidence.  Applying a candidate stops the identified
Case7 service before snapshotting the production OM, registry,
database/WAL/SHM, and MobileCLIP FAISS file.  A failed rebuild or health
check restores the snapshot and restarts the previous service.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Optional, Tuple


MODEL_ID = "mobileclip_s0__npu__mixed_fp16"
OM_NAME = "mobileclip_s0_image.om"
EXPECTED_BOARD_ROOT = Path("/home/HwHiAiUser/Documents/ai-album")
EXPECTED_SOC = "Ascend310B4"
EXPECTED_COMPONENT = "image"
EXPECTED_CANDIDATES = {"C0", "C1", "C2", "C3", "C4"}
EXPECTED_REFERENCE_COUNT = 36
EXPECTED_GALLERY_COUNT = 500
EXPECTED_QUERY_COUNT = 20
EXPECTED_WARMUP = 20
EXPECTED_LOOPS = 100
EXPECTED_REPEATS = 3
NUMERICAL_THRESHOLD = 0.995


class PromotionError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve(path_value, base: Path) -> Path:
    path = Path(str(path_value))
    return path if path.is_absolute() else (base / path).resolve()


def _inside(path: Path, root: Path) -> bool:
    try:
        return os.path.commonpath((str(path.resolve()), str(root.resolve()))) == str(root.resolve())
    except ValueError:
        return False


def _managed_path(path: Path, root: Path) -> bool:
    """Allow release files and the release's managed shared-asset symlinks."""
    resolved = path.resolve()
    roots = [root.resolve()]
    for name in ("models", "data", "photos", "reports", "secrets"):
        link = root / name
        if link.exists():
            roots.append(link.resolve())
    return any(_inside(resolved, candidate) for candidate in roots)


def _candidate_entry(summary: dict, candidate_id: Optional[str]) -> Tuple[str, dict]:
    candidates = summary.get("candidates")
    if not isinstance(candidates, dict):
        raise PromotionError("sweep summary has no candidates map")
    selected = candidate_id or summary.get("selected_candidate")
    if not selected:
        raise PromotionError("sweep summary does not select a candidate; pass --candidate")
    value = candidates.get(selected)
    if not isinstance(value, dict):
        raise PromotionError(f"candidate is absent from sweep summary: {selected}")
    return str(selected), value


def _gate_passed(value: dict, *names: str) -> bool:
    """Accept the explicit gate fields used by the sweep, never truthiness of a path."""
    for name in names:
        if name in value:
            return value[name] is True
    return False


def _json_file(path: Path, label: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise PromotionError(f"cannot read {label}: {path}") from exc
    if not isinstance(value, dict):
        raise PromotionError(f"{label} is not a JSON object: {path}")
    return value


def _finite(value, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise PromotionError(f"{label} is not numeric") from exc
    if not math.isfinite(result):
        raise PromotionError(f"{label} is not finite")
    return result


def _same_value(actual, expected, label: str) -> None:
    if actual != expected:
        raise PromotionError(f"{label} mismatch: {actual!r} != {expected!r}")


def _candidate_dir(root: Path, candidate_id: str) -> Path:
    sweep_root = (root / "reports" / "precision_sweep" / "mobileclip_s0_image_precision").resolve()
    if candidate_id not in EXPECTED_CANDIDATES:
        raise PromotionError(f"unsupported candidate id: {candidate_id}")
    return (sweep_root / candidate_id).resolve()


def _report_in_candidate(candidate_dir: Path, value, default_name: str, label: str) -> Path:
    path = _resolve(value or (candidate_dir / default_name), candidate_dir)
    expected_root = candidate_dir.resolve()
    if not _inside(path, expected_root):
        raise PromotionError(f"{label} is outside the candidate report directory: {path}")
    if not path.is_file():
        raise PromotionError(f"{label} is missing: {path}")
    return path


def _verify_registry_identity(root: Path, summary: dict) -> dict:
    """Verify the candidate against the immutable production ONNX contract."""
    registry_path = root / "models" / "registry.json"
    registry = _json_file(registry_path, "production registry")
    model = next(
        (item for item in registry.get("models", []) if item.get("model_id") == MODEL_ID),
        None,
    )
    if not isinstance(model, dict):
        raise PromotionError(f"production registry lacks {MODEL_ID}")
    component = (model.get("components") or {}).get(EXPECTED_COMPONENT)
    if not isinstance(component, dict):
        raise PromotionError("production registry lacks MobileCLIP image component")
    onnx_value = component.get("onnx")
    onnx_path = _resolve(onnx_value, root)
    if not onnx_path.is_file():
        raise PromotionError(f"production ONNX is missing: {onnx_path}")
    actual = sha256_file(onnx_path).lower()
    declared = str(component.get("onnx_sha256") or "").lower()
    if not declared or actual != declared:
        raise PromotionError(f"production ONNX SHA-256 mismatch: {actual}")
    summary_onnx = summary.get("onnx")
    if not isinstance(summary_onnx, dict):
        raise PromotionError("sweep summary has no ONNX identity")
    for key in ("onnx_sha256", "production_declared_onnx_sha256", "candidate_declared_onnx_sha256"):
        value = summary_onnx.get(key)
        if value and str(value).lower() != actual:
            raise PromotionError(f"summary ONNX identity mismatch for {key}")
    if summary_onnx.get("same_bytes") is not True or summary_onnx.get("same_path") is not True:
        raise PromotionError("summary does not prove candidate/production ONNX identity")
    return {"path": onnx_path, "sha256": actual}


def validate_evidence(summary_path: Path, candidate_id: Optional[str], root: Path) -> dict:
    summary_path = Path(summary_path).resolve()
    root = Path(root).resolve()
    summary = _json_file(summary_path, "sweep summary")
    expected_sweep_root = (root / "reports" / "precision_sweep" / "mobileclip_s0_image_precision").resolve()
    if not _inside(summary_path, expected_sweep_root):
        raise PromotionError(f"sweep summary is outside the precision report directory: {summary_path}")
    if summary_path.name != "summary.json":
        raise PromotionError("sweep evidence must be summary.json")
    _same_value(summary.get("status"), "passed", "sweep status")
    if summary.get("passed") is not True:
        raise PromotionError("sweep summary is not marked passed")
    _same_value(summary.get("model_id"), MODEL_ID, "sweep model_id")
    _same_value(summary.get("component"), EXPECTED_COMPONENT, "sweep component")
    _same_value(summary.get("soc_version"), EXPECTED_SOC, "sweep soc_version")
    protocol = summary.get("protocol")
    if not isinstance(protocol, dict):
        raise PromotionError("sweep summary has no protocol")
    if protocol.get("single_thread") is not True or protocol.get("cache_disabled") is not True:
        raise PromotionError("sweep protocol is not serial and cache-disabled")
    _same_value(protocol.get("performance_warmup"), EXPECTED_WARMUP, "performance warmup")
    _same_value(protocol.get("performance_loops"), EXPECTED_LOOPS, "performance loops")
    _same_value(protocol.get("performance_repeats"), EXPECTED_REPEATS, "performance repeats")
    threshold = _finite(protocol.get("numerical_threshold", NUMERICAL_THRESHOLD), "numerical threshold")
    if threshold < NUMERICAL_THRESHOLD:
        raise PromotionError(f"numerical threshold is below {NUMERICAL_THRESHOLD}")
    selected, candidate = _candidate_entry(summary, candidate_id)
    if selected not in EXPECTED_CANDIDATES:
        raise PromotionError(f"unsupported candidate id: {selected}")
    _same_value(summary.get("selected_candidate"), selected, "selected candidate")
    _same_value(candidate.get("candidate_id"), selected, "candidate_id")
    _same_value(candidate.get("model_id"), MODEL_ID, "candidate model_id")
    _same_value(candidate.get("component"), EXPECTED_COMPONENT, "candidate component")
    _same_value(candidate.get("soc_version"), EXPECTED_SOC, "candidate soc_version")
    if candidate.get("passed") is not True:
        raise PromotionError(f"candidate {selected} did not pass the complete sweep")

    candidate_dir = _candidate_dir(root, selected)
    om_value = candidate.get("om") or candidate.get("om_path")
    om_sha = candidate.get("om_sha256") or candidate.get("sha256")
    if not om_value or not om_sha:
        raise PromotionError(f"candidate {selected} has no OM path and SHA-256")
    candidate_om = _resolve(om_value, root)
    canonical = (root / "models" / "om" / OM_NAME).resolve()
    if candidate_om == canonical:
        raise PromotionError("candidate OM resolves to the production canonical OM")
    expected_om = (candidate_dir / "om" / OM_NAME).resolve()
    if candidate_om != expected_om:
        raise PromotionError(f"candidate OM is not the expected isolated artifact: {candidate_om}")
    if not _managed_path(candidate_om, root) or not candidate_om.is_file():
        raise PromotionError(f"candidate OM is missing or outside the release root: {candidate_om}")
    actual_sha = sha256_file(candidate_om)
    if actual_sha.lower() != str(om_sha).lower():
        raise PromotionError(f"candidate OM SHA-256 mismatch: {actual_sha}")

    identity = _verify_registry_identity(root, summary)
    if str(candidate.get("onnx_sha256") or "").lower() != identity["sha256"]:
        raise PromotionError("candidate ONNX SHA-256 does not match production registry")
    if candidate.get("same_bytes") is not True or candidate.get("same_path") is not True:
        raise PromotionError("candidate does not prove immutable ONNX identity")

    # Validate the raw conversion report rather than trusting only summary
    # booleans.  This catches a hand-edited summary before any file replacement.
    atc_path = _report_in_candidate(candidate_dir, candidate.get("atc_report"), "atc_conversion.json", "ATC report")
    atc = _json_file(atc_path, "ATC report")
    _same_value(atc.get("soc_version"), EXPECTED_SOC, "ATC soc_version")
    _same_value(atc.get("precision_mode"), "allow_fp32_to_fp16", "ATC precision mode")
    _same_value(atc.get("op_select_implmode"), "high_precision_for_all", "ATC operator policy")
    if atc.get("cache_policy") not in (None, "--op_compiler_cache_mode=disable"):
        raise PromotionError("ATC cache policy is not disabled")
    atc_model = (atc.get("models") or {}).get(MODEL_ID, {})
    atc_component = (atc_model.get("components") or {}).get(EXPECTED_COMPONENT, {})
    if not isinstance(atc_component, dict):
        raise PromotionError("ATC report lacks MobileCLIP image component")
    _same_value(atc_component.get("om_sha256"), actual_sha, "ATC OM SHA-256")
    command = [str(value) for value in atc_component.get("command", [])]
    if "--op_compiler_cache_mode=disable" not in command:
        raise PromotionError("ATC command does not disable compiler cache")
    if not any(value in command for value in ("--enable_graph_parallel=0", "--ac_parallel_enable=0")):
        raise PromotionError("ATC command does not disable graph parallelism")
    _report_in_candidate(candidate_dir, candidate.get("conversion_log"), "conversion_stdout.log", "ATC conversion log")

    numerical = candidate.get("numerical", candidate.get("numeric", {}))
    retrieval = candidate.get("retrieval", {})
    performance = candidate.get("performance", {})
    if not isinstance(numerical, dict) or not _gate_passed(numerical, "passed"):
        raise PromotionError(f"candidate {selected} is missing a passed numerical gate")
    if not isinstance(retrieval, dict) or not _gate_passed(retrieval, "passed"):
        raise PromotionError(f"candidate {selected} is missing a passed retrieval gate")
    if not isinstance(performance, dict) or not _gate_passed(performance, "passed"):
        raise PromotionError(f"candidate {selected} is missing a passed performance gate")

    numerical_path = _report_in_candidate(candidate_dir, None, "acl_numerical_validation.json", "numerical report")
    numerical_raw = _json_file(numerical_path, "numerical report")
    _same_value(numerical_raw.get("model_id"), MODEL_ID, "numerical model_id")
    _same_value(numerical_raw.get("component"), EXPECTED_COMPONENT, "numerical component")
    _same_value(numerical_raw.get("candidate_om_sha256"), actual_sha, "numerical OM SHA-256")
    _same_value(numerical_raw.get("reference_count"), EXPECTED_REFERENCE_COUNT, "numerical reference count")
    _same_value(numerical_raw.get("expected_reference_count"), EXPECTED_REFERENCE_COUNT, "numerical expected count")
    raw_threshold = _finite(numerical_raw.get("threshold"), "numerical report threshold")
    if raw_threshold < NUMERICAL_THRESHOLD:
        raise PromotionError("numerical report threshold is below the admission threshold")
    refs = numerical_raw.get("references")
    if not isinstance(refs, list) or len(refs) != EXPECTED_REFERENCE_COUNT:
        raise PromotionError("numerical report does not contain exactly 36 references")
    for index, ref in enumerate(refs):
        if not isinstance(ref, dict) or ref.get("passed") is not True or ref.get("finite") is not True:
            raise PromotionError(f"numerical reference {index} is not finite/passed")
        _same_value(ref.get("output_dim"), 512, f"numerical reference {index} dimension")
        if _finite(ref.get("cosine_similarity"), f"numerical reference {index} cosine") < max(NUMERICAL_THRESHOLD, raw_threshold):
            raise PromotionError(f"numerical reference {index} is below the cosine threshold")
    if numerical_raw.get("passed") is not True:
        raise PromotionError("raw numerical report is not passed")
    if numerical.get("reference_count") != numerical_raw.get("reference_count") or numerical.get("candidate_om_sha256") != numerical_raw.get("candidate_om_sha256"):
        raise PromotionError("summary numerical evidence differs from raw report")

    retrieval_path = _report_in_candidate(candidate_dir, None, "retrieval.json", "retrieval report")
    retrieval_raw = _json_file(retrieval_path, "retrieval report")
    _same_value(retrieval_raw.get("query_count"), EXPECTED_QUERY_COUNT, "retrieval query count")
    metrics = retrieval_raw.get("metrics")
    baseline_block = (summary.get("production_baseline") or {}).get("retrieval")
    baseline_metrics = baseline_block.get("metrics") if isinstance(baseline_block, dict) else None
    if not isinstance(metrics, dict) or not isinstance(baseline_metrics, dict):
        raise PromotionError("retrieval report lacks candidate/baseline metrics")
    for key in ("recall_at_1", "recall_at_3", "recall_at_5"):
        current_metric = _finite(metrics.get(key), f"candidate {key}")
        baseline_metric = _finite(baseline_metrics.get(key), f"baseline {key}")
        if current_metric < 0.8 or current_metric < baseline_metric:
            raise PromotionError(f"candidate {key} is below baseline or 0.80")
        comparison = (retrieval.get("comparisons") or {}).get(key, {})
        if comparison.get("passed") is not True or _finite(comparison.get("candidate"), f"comparison {key}") != current_metric:
            raise PromotionError(f"summary retrieval comparison is inconsistent for {key}")
    if retrieval_raw.get("passed") is not True:
        raise PromotionError("raw retrieval report is not passed")
    _same_value(retrieval.get("query_count"), retrieval_raw.get("query_count"), "summary retrieval query count")
    if retrieval.get("metrics") != retrieval_raw.get("metrics"):
        raise PromotionError("summary retrieval metrics differ from raw report")

    performance_path = _report_in_candidate(candidate_dir, None, "performance.json", "performance report")
    performance_raw = _json_file(performance_path, "performance report")
    for key, expected in (("warmup", EXPECTED_WARMUP), ("loops", EXPECTED_LOOPS), ("repeats", EXPECTED_REPEATS), ("samples", EXPECTED_WARMUP * EXPECTED_LOOPS if EXPECTED_REPEATS == 1 else EXPECTED_LOOPS * EXPECTED_REPEATS)):
        _same_value(performance_raw.get(key), expected, f"performance {key}")
    if performance_raw.get("samples") != EXPECTED_LOOPS * EXPECTED_REPEATS:
        raise PromotionError("performance sample count is inconsistent")
    baseline_perf = (summary.get("production_baseline") or {}).get("performance")
    if not isinstance(baseline_perf, dict):
        raise PromotionError("sweep has no performance baseline")
    baseline_p50 = _finite(baseline_perf.get("p50_ms"), "baseline performance p50")
    baseline_p95 = _finite(baseline_perf.get("p95_ms"), "baseline performance p95")
    current_p50 = _finite(performance_raw.get("p50_ms"), "candidate performance p50")
    current_p95 = _finite(performance_raw.get("p95_ms"), "candidate performance p95")
    thresholds = performance_raw.get("thresholds")
    if not isinstance(thresholds, dict):
        raise PromotionError("performance report has no thresholds")
    if abs(_finite(thresholds.get("p50_max_ms"), "p50 threshold") - baseline_p50 * 0.90) > 1e-6 or abs(_finite(thresholds.get("p95_max_ms"), "p95 threshold") - baseline_p95) > 1e-6:
        raise PromotionError("performance thresholds do not match the same-round baseline")
    if current_p50 > baseline_p50 * 0.90 or current_p95 > baseline_p95 or performance_raw.get("passed") is not True:
        raise PromotionError("candidate performance does not pass the admission thresholds")
    if performance.get("p50_ms") != performance_raw.get("p50_ms") or performance.get("p95_ms") != performance_raw.get("p95_ms"):
        raise PromotionError("summary performance differs from raw report")
    # New sweeps persist the worker stdout beside the candidate.  Legacy
    # reports created before this check are accepted only with an explicit
    # warning; all future evidence (schema >= 2) must be independently
    # replayable.
    if int(summary.get("evidence_schema_version", 1)) >= 2:
        worker_log = _report_in_candidate(candidate_dir, performance_raw.get("worker_log"), "performance.worker.log", "performance worker log")
        worker_hash = performance_raw.get("worker_log_sha256")
        if worker_hash and sha256_file(worker_log).lower() != str(worker_hash).lower():
            raise PromotionError("performance worker log SHA-256 mismatch")

    keep = candidate.get("keep_dtype") or candidate.get("keep_dtype_file") or candidate.get("keep_dtype_path")
    keep_path = None
    if selected != "C0":
        keep_path = _resolve(keep or (candidate_dir / "keep_dtype.cfg"), root)
        if not keep_path.is_file():
            raise PromotionError(f"candidate keep-dtype file is missing: {keep_path}")
        keep_hash = candidate.get("keep_dtype_sha256") or candidate.get("keep_dtype_hash")
        if not keep_hash or sha256_file(keep_path).lower() != str(keep_hash).lower():
            raise PromotionError("candidate keep-dtype SHA-256 mismatch")
    elif int(candidate.get("keep_dtype_node_count", 0)) != 0 or candidate.get("keep_dtype_nodes") not in (None, []):
        raise PromotionError("C0 must have an empty keep-dtype whitelist")
    return {
        "summary": summary,
        "candidate_id": selected,
        "candidate": candidate,
        "candidate_dir": candidate_dir,
        "candidate_om": candidate_om,
        "candidate_om_sha256": actual_sha,
        "canonical_om": canonical,
        "keep_dtype_path": keep_path,
        "expected_photo_count": EXPECTED_GALLERY_COUNT,
    }


def _copy_snapshot(source: Path, target: Path, root: Path, backup: Path) -> dict:
    entry = {"path": str(source.relative_to(root)).replace("\\", "/"), "present": source.is_file()}
    if source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        entry["sha256"] = sha256_file(source)
        entry["backup"] = str(target.relative_to(backup)).replace("\\", "/")
    return entry


def _snapshot(root: Path, backup: Path) -> dict:
    files = [
        root / "models" / "om" / OM_NAME,
        root / "models" / "registry.json",
        root / "candidate_manifest.json",
        root / "data" / "album.sqlite3",
        root / "data" / "album.sqlite3-wal",
        root / "data" / "album.sqlite3-shm",
        root / "data" / "indexes" / f"{MODEL_ID}.faiss",
    ]
    manifest = {"created_at": time.time(), "files": []}
    for source in files:
        manifest["files"].append(_copy_snapshot(source, backup / source.relative_to(root), root, backup))
    (backup / "snapshot.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest


def _restore(root: Path, backup: Path) -> None:
    snapshot_path = backup / "snapshot.json"
    payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
    for entry in payload.get("files", []):
        destination = root / entry["path"]
        backup_file = backup / entry.get("backup", entry["path"])
        if entry.get("present"):
            if not backup_file.is_file():
                raise PromotionError(f"rollback asset is missing: {backup_file}")
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".rollback.tmp")
            shutil.copy2(backup_file, temporary)
            os.replace(temporary, destination)
            if entry.get("sha256") and sha256_file(destination).lower() != entry["sha256"].lower():
                raise PromotionError(f"rollback SHA-256 mismatch: {destination}")
        else:
            destination.unlink(missing_ok=True)


def _atomic_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.name + ".candidate.tmp")
    shutil.copy2(source, temporary)
    with temporary.open("rb") as handle:
        os.fsync(handle.fileno())
    os.replace(temporary, destination)


def _relative_asset(value, root: Path) -> Optional[str]:
    if not value:
        return None
    path = _resolve(value, root)
    if not _managed_path(path, root):
        raise PromotionError(f"candidate keep-dtype file is outside release root: {path}")
    return str(path.relative_to(root)).replace("\\", "/")


def _write_registry(root: Path, evidence: dict) -> None:
    registry_path = root / "models" / "registry.json"
    try:
        payload = json.loads(registry_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PromotionError(f"cannot read production registry: {registry_path}") from exc
    models = payload.get("models")
    if not isinstance(models, list):
        raise PromotionError("production registry has no models list")
    target = next((item for item in models if item.get("model_id") == MODEL_ID), None)
    if not isinstance(target, dict):
        raise PromotionError(f"production registry lacks {MODEL_ID}")
    component = target.get("components", {}).get("image")
    if not isinstance(component, dict):
        raise PromotionError("production registry lacks MobileCLIP image component")
    candidate = evidence["candidate"]
    component["precision_mode"] = "allow_fp32_to_fp16"
    component["om_sha256"] = evidence["candidate_om_sha256"]
    keep = evidence.get("keep_dtype_path") or (
        candidate.get("keep_dtype")
        or candidate.get("keep_dtype_file")
        or candidate.get("keep_dtype_path")
    )
    # C0 deliberately has no FP32 exception.  Its config file is only an
    # audit placeholder and must never become a production keep-dtype input.
    if evidence["candidate_id"] == "C0":
        component.pop("atc_keep_dtype", None)
    elif keep:
        component["atc_keep_dtype"] = _relative_asset(keep, root)
    target["precision_mode"] = "allow_fp32_to_fp16"
    target["precision_strategy"] = {
        "kind": "selective_mixed_precision",
        "candidate_id": evidence["candidate_id"],
        "admitted_at": time.time(),
        "numerical_threshold": 0.995,
    }
    payload["generated_at"] = time.time()
    temporary = registry_path.with_name(registry_path.name + ".candidate.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, registry_path)


def _write_candidate_manifest(root: Path, evidence: dict) -> None:
    path = root / "candidate_manifest.json"
    if not path.is_file():
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    for value in payload.get("models", []):
        if value.get("model_id") != MODEL_ID:
            continue
        component = value.setdefault("components", {}).setdefault("image", {})
        component["precision_mode"] = "allow_fp32_to_fp16"
        keep = evidence.get("keep_dtype_path") or (
            evidence["candidate"].get("keep_dtype")
            or evidence["candidate"].get("keep_dtype_file")
            or evidence["candidate"].get("keep_dtype_path")
        )
        if evidence["candidate_id"] == "C0":
            component.pop("atc_keep_dtype", None)
        elif keep:
            component["atc_keep_dtype"] = _relative_asset(keep, root)
        component["om_sha256"] = evidence["candidate_om_sha256"]
        value["precision_strategy"] = {
            "kind": "selective_mixed_precision",
            "candidate_id": evidence["candidate_id"],
            "om_sha256": evidence["candidate_om_sha256"],
        }
        break
    temporary = path.with_name(path.name + ".candidate.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _service_running(pid_file: Path, root: Path) -> bool:
    try:
        pid = int(pid_file.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return False
    proc_cmdline = Path(f"/proc/{pid}/cmdline")
    if not proc_cmdline.is_file():
        return False
    try:
        command = proc_cmdline.read_bytes().replace(b"\0", b" ").decode(errors="ignore")
    except OSError:
        return False
    return str(root / "app.py") in command


def _service(service_script: Path, root: Path, pid_file: Path, log_file: Path, stop: bool = False) -> None:
    command = ["bash", str(service_script), "--root", str(root), "--pid-file", str(pid_file), "--log-file", str(log_file)]
    if stop:
        command.append("--stop")
    completed = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    if completed.returncode:
        raise PromotionError(f"service command failed ({completed.returncode}): {completed.stdout[-2000:]}")


def _health(base_url: str, timeout_seconds: float = 30.0, retry_interval: float = 0.5) -> dict:
    """Wait for the newly started service to accept health requests.

    Model registry loading and ACL initialization can take several seconds
    after the process has been written to the PID file.  A bounded retry keeps
    promotion atomic while avoiding a false rollback on the normal startup
    race.
    """
    if timeout_seconds <= 0:
        raise PromotionError("health timeout must be positive")
    deadline = time.monotonic() + float(timeout_seconds)
    endpoint = base_url.rstrip("/") + "/api/health"
    last_error: Optional[Exception] = None
    while True:
        try:
            with urllib.request.urlopen(endpoint, timeout=min(5.0, timeout_seconds)) as response:
                if response.status != 200:
                    raise PromotionError(f"health returned HTTP {response.status}")
                return json.loads(response.read().decode("utf-8"))
        except (OSError, ValueError, urllib.error.URLError, PromotionError) as exc:
            last_error = exc
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise PromotionError(f"health check failed after {timeout_seconds:.1f}s: {last_error}") from last_error
        time.sleep(min(float(retry_interval), remaining))


def _validate_post_promotion_health(payload: dict, expected_photo_count: int) -> dict:
    """Reject a degraded/CPU service after replacing the production OM."""
    if not isinstance(payload, dict):
        raise PromotionError("health response is not an object")
    _same_value(payload.get("status"), "ready", "post-promotion health status")
    _same_value(payload.get("backend"), "npu", "post-promotion health backend")
    admitted = payload.get("admitted_models")
    if not isinstance(admitted, list) or MODEL_ID not in admitted:
        raise PromotionError("post-promotion health does not admit MobileCLIP")
    missing = payload.get("missing_required_models")
    if missing not in (None, [], ()):  # older health payloads may omit it
        raise PromotionError(f"post-promotion health reports missing models: {missing}")
    index = payload.get("index")
    if not isinstance(index, dict):
        raise PromotionError("post-promotion health has no index status")
    available = int(index.get("available_photos", -1))
    embeddings = int((index.get("embeddings_by_model") or {}).get(MODEL_ID, -1))
    if expected_photo_count > 0 and (available != expected_photo_count or embeddings != expected_photo_count):
        raise PromotionError(
            "post-promotion index count mismatch: "
            f"available={available}, embeddings={embeddings}, expected={expected_photo_count}"
        )
    return payload


def rebuild_mobileclip(root: Path) -> dict:
    """Rebuild only MobileCLIP vectors using the newly admitted OM."""
    # Imports are delayed so a dry-run remains usable on a controller without
    # CANN/PyACL.  The apply path is intentionally NPU-only.
    from embedding_backend import MOBILECLIP_ID, ModelManager
    from model_registry import ModelRegistry
    from photo_index import AlbumIndex

    registry = ModelRegistry(path=root / "models" / "registry.json", require_artifacts=True)
    manager = ModelManager(registry=registry)
    index = AlbumIndex(
        manager=manager,
        db_path=root / "data" / "album.sqlite3",
        index_dir=root / "data" / "indexes",
        import_dir=root / "photos" / "imports",
        photo_roots=(root / "photos",),
        allow_numpy_fallback=False,
    )
    try:
        removed = index.clear_model_embeddings(MOBILECLIP_ID, confirmed=True)
        rows = index.list_photos(limit=None)
        paths = [Path(row["filepath"]) for row in rows]
        summary = index.index_paths(paths, model_ids=(MOBILECLIP_ID,))
        stats = index.stats()
        return {"removed": removed, "indexed": summary.to_dict(), "stats": stats}
    finally:
        index.close()
        manager.release()


def promote(args) -> dict:
    root = Path(args.root).resolve()
    summary_path = _resolve(args.summary, root)
    evidence = validate_evidence(summary_path, args.candidate, root)
    result = {
        "candidate_id": evidence["candidate_id"],
        "candidate_om": str(evidence["candidate_om"]),
        "candidate_om_sha256": evidence["candidate_om_sha256"],
        "apply": bool(args.apply),
        "status": "validated",
    }
    if not args.apply:
        return result
    if root == Path("/") or (root / "models" / "om").resolve() == root:
        raise PromotionError("unsafe project root")
    # ``reports`` is a managed symlink to the shared report volume on a
    # release deployment.  Writing below it keeps the rollback evidence out
    # of the immutable release source while still working in local tests.
    backup_parent = root / "reports" / "mobileclip_precision_promotions"
    backup_parent.mkdir(parents=True, exist_ok=True)
    backup = backup_parent / time.strftime("%Y%m%d-%H%M%S")
    if backup.exists():
        raise PromotionError(f"rollback directory already exists: {backup}")
    canonical = evidence["canonical_om"]
    pid_file = _resolve(args.pid_file, root)
    log_file = _resolve(args.log_file, root)
    service_script = _resolve(args.service_script, root)
    was_running = _service_running(pid_file, root)
    service_stopped = False
    snapshot_ready = False
    try:
        # Stop first so album.sqlite3, its WAL/SHM companions, FAISS and the
        # model registry form a coherent rollback snapshot.  Taking this
        # snapshot while SQLite is live can capture files from different
        # transactions and make rollback unrecoverable.
        if was_running:
            _service(service_script, root, pid_file, log_file, stop=True)
            service_stopped = True
        backup.mkdir()
        manifest = _snapshot(root, backup)
        snapshot_ready = True
        result["rollback_dir"] = str(backup)
        _atomic_copy(evidence["candidate_om"], canonical)
        _write_registry(root, evidence)
        _write_candidate_manifest(root, evidence)
        rebuild = rebuild_mobileclip(root)
        result["rebuild"] = rebuild
        expected = int(rebuild["stats"].get("available_photos", 0))
        actual = int(rebuild["stats"].get("embeddings_by_model", {}).get(MODEL_ID, 0))
        if actual != expected:
            raise PromotionError(f"MobileCLIP embedding count {actual} != available photos {expected}")
        if was_running:
            _service(service_script, root, pid_file, log_file)
            service_stopped = False
            result["health"] = _validate_post_promotion_health(
                _health(args.base_url, timeout_seconds=args.health_timeout), expected
            )
        result["status"] = "promoted"
    except Exception as exc:
        # If the new process was started before health validation failed,
        # stop it before restoring the old files.  A snapshot failure itself
        # has no files to restore; just restart the previously running service.
        if was_running and not service_stopped:
            try:
                _service(service_script, root, pid_file, log_file, stop=True)
            except Exception:
                pass
        if snapshot_ready:
            _restore(root, backup)
        if was_running:
            _service(service_script, root, pid_file, log_file)
        if backup.exists() and not args.keep_backup:
            # This is always the freshly-created path below our managed
            # promotion directory; do not accept a user-supplied deletion
            # target here.
            if backup.parent.resolve() == backup_parent.resolve():
                shutil.rmtree(backup)
        raise PromotionError(f"promotion failed and rollback restored previous assets: {exc}") from exc
    if not args.keep_backup:
        # The target is the newly-created, validated rollback directory; never
        # recursively remove a user-supplied or broad path.
        if backup.parent.resolve() != backup_parent.resolve():
            raise PromotionError("refusing to remove an unexpected rollback path")
        shutil.rmtree(backup)
        result["rollback_removed"] = True
    else:
        result["rollback_removed"] = False
    return result


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--summary", required=True, help="completed precision sweep summary JSON")
    value.add_argument("--candidate", help="candidate id; defaults to summary.selected_candidate")
    value.add_argument("--root", default=".", help="active Case7 release root")
    value.add_argument("--base-url", default="http://127.0.0.1:7860")
    value.add_argument(
        "--health-timeout",
        type=float,
        default=30.0,
        help="seconds to wait for /api/health after restart",
    )
    value.add_argument("--pid-file", default="shared/run/smart_album.pid")
    value.add_argument("--log-file", default="shared/logs/smart_album.log")
    value.add_argument("--service-script", default="scripts/run_smart_album_service.sh")
    value.add_argument("--apply", action="store_true", help="perform the guarded promotion")
    value.add_argument("--keep-backup", action="store_true", help="retain the rollback snapshot after health passes")
    return value


def main(argv=None) -> int:
    try:
        args = parser().parse_args(argv)
        if args.health_timeout <= 0:
            raise PromotionError("--health-timeout must be positive")
        result = promote(args)
    except PromotionError as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
