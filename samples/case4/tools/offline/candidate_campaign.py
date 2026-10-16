"""Audit and orchestrate the heterogeneous candidate model campaign.

This command deliberately separates inventory/audit from board execution.  It
never downloads weights, runs ATC, changes ``models/registry.json`` or exposes
an audit candidate through the serving API.  Board execution is limited to a
single candidate ACL smoke and is expected to be wrapped by the board trace
collector when thermal and driver evidence is required.

Examples::

    python -m tools.offline.candidate_campaign inventory --all
    python -m tools.offline.candidate_campaign local --candidate compnet_tongji_600
    python -m tools.offline.candidate_campaign report --all
    python -m tools.offline.candidate_campaign board --candidate compnet_tongji_600 --image roi.png
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any, Mapping

from palmprint_workbench.config import REPORT_DIR, ROOT
from palmprint_workbench.domain.candidates import CandidateManifest, CandidateSpec
from palmprint_workbench.domain.registry import ModelRegistry


CAMPAIGN_SCHEMA_VERSION = 1
STATUS_VALUES = {
    "not_started",
    "audit_pass",
    "blocked_source",
    "blocked_license",
    "blocked_asset",
    "needs_reimplementation",
    "onnx_failed",
    "atc_failed",
    "acl_failed",
    "numeric_failed",
    "quality_failed",
    "research_ready",
    "not_applicable",
}
_SAFE_ID = re.compile(r"[^A-Za-z0-9_.-]+")
_FIXED_REVISION = re.compile(r"^[0-9a-f]{40,64}$", re.IGNORECASE)
_BLOCKING_FAULTS = (
    "rc_139",
    "err_ret_minus_512",
    "aicore",
    "lpm",
    "ras",
    "device_reset",
    "resource_leak",
)


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _safe_id(value: str) -> str:
    return _SAFE_ID.sub("-", value).strip("-.") or "candidate"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return "<outside-root>"


def _artifact_record(
    *,
    path: Path,
    root: Path,
    expected_bytes: Any = None,
    expected_sha256: Any = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "path": _relative(path, root),
        "present": path.is_file(),
        "bytes": None,
        "sha256": None,
        "expected_bytes": expected_bytes,
        "expected_sha256": expected_sha256,
        "valid": False,
    }
    if not path.is_file():
        return result
    result["bytes"] = path.stat().st_size
    result["sha256"] = _sha256(path)
    size_ok = expected_bytes is None or result["bytes"] == expected_bytes
    hash_ok = expected_sha256 is None or result["sha256"].lower() == str(expected_sha256).lower()
    result["valid"] = bool(size_ok and hash_ok)
    if not size_ok:
        result["error"] = "byte_count_mismatch"
    elif not hash_ok:
        result["error"] = "sha256_mismatch"
    return result


def _license_blocked(candidate: CandidateSpec) -> bool:
    license_info = candidate.raw.get("license", {})
    spdx = str(
        license_info.get("spdx", "") if isinstance(license_info, Mapping) else ""
    ).strip().lower()
    return spdx in {
        "noassertion",
        "licenseproprietary",
        "licenseref-proprietary",
        "licenseref-cloud-service",
    }


def _declared_artifacts(candidate: CandidateSpec, root: Path) -> list[dict[str, Any]]:
    weights = candidate.raw.get("weights", {})
    artifacts = weights.get("artifacts", []) if isinstance(weights, Mapping) else []
    result: list[dict[str, Any]] = []
    if isinstance(artifacts, list):
        for artifact in artifacts:
            if not isinstance(artifact, Mapping) or not artifact.get("local_path"):
                continue
            path = (root / str(artifact["local_path"])).resolve()
            result.append(
                {
                    "kind": "checkpoint_or_reference",
                    **_artifact_record(
                        path=path,
                        root=root,
                        expected_bytes=artifact.get("bytes"),
                        expected_sha256=artifact.get("sha256"),
                    ),
                }
            )
    conversion = candidate.raw.get("conversion", {})
    if isinstance(conversion, Mapping):
        onnx_path = conversion.get("onnx_path")
        if onnx_path:
            result.append(
                {
                    "kind": "onnx",
                    **_artifact_record(
                        path=(root / str(onnx_path)).resolve(),
                        root=root,
                        expected_bytes=conversion.get("onnx_bytes"),
                        expected_sha256=conversion.get("onnx_sha256"),
                    ),
                }
            )
        om_paths = conversion.get("om_paths", {})
        if isinstance(om_paths, Mapping):
            mixed = om_paths.get("mixed_fp16")
            if mixed:
                result.append(
                    {
                        "kind": "mixed_fp16_om",
                        **_artifact_record(
                            path=(root / str(mixed)).resolve(),
                            root=root,
                            expected_bytes=conversion.get("mixed_fp16_bytes"),
                            expected_sha256=conversion.get("mixed_fp16_sha256"),
                        ),
                    }
                )
    return result


def _runtime_artifacts(candidate: CandidateSpec, root: Path) -> list[dict[str, Any]]:
    """Include the active CCNet runtime asset in the candidate audit."""

    if candidate.id != "ccnet":
        return []
    try:
        runtime = ModelRegistry().get_runtime("ccnet")
    except (KeyError, ValueError):
        return []
    result: list[dict[str, Any]] = []
    runtime_assets = runtime.raw.get("assets", {})
    if not isinstance(runtime_assets, Mapping):
        runtime_assets = {}
    expected_assets = (
        ("reference_onnx", runtime_assets.get("reference_onnx", {})),
        ("mixed_fp16_om", runtime_assets.get("mixed_fp16_om", {})),
    )
    for name, expected in expected_assets:
        if not isinstance(expected, Mapping):
            expected = {}
        path = runtime.path(name) if name == "reference_onnx" else runtime.om_path("mixed_fp16")
        if path is not None:
            result.append({
                "kind": name,
                **_artifact_record(
                    path=path,
                    root=root,
                    expected_bytes=expected.get("bytes"),
                    expected_sha256=expected.get("sha256"),
                ),
            })
    return result


def _contract_record(candidate: CandidateSpec) -> dict[str, Any]:
    raw = candidate.raw
    input_contract = raw.get("input_contract")
    input_shape = raw.get("input_shape")
    if input_shape is None and isinstance(input_contract, str):
        match = re.search(r"\[\s*([0-9]+(?:\s*,\s*[0-9]+)*)\s*\]", input_contract)
        if match:
            input_shape = [int(item.strip()) for item in match.group(1).split(",")]
    output_contract = raw.get("output_contract")
    feature_dim = raw.get("feature_dim")
    if feature_dim is None and isinstance(output_contract, str):
        match = re.search(r"([0-9]+)\s*[- ]?D", output_contract, flags=re.IGNORECASE)
        if match:
            feature_dim = int(match.group(1))
    return {
        "task_type": raw.get("task_type", raw.get("kind", "candidate")),
        "modality": candidate.modality,
        "task": candidate.task,
        "input_contract": input_contract,
        "input_shape": input_shape,
        "input_range": raw.get("input_range"),
        "output_contract": output_contract,
        "feature_dim": feature_dim,
        "metric": raw.get("metric"),
        "contract_complete": bool(input_shape and output_contract),
    }


def _onnx_contract_gate(
    candidate: CandidateSpec, artifacts: list[dict[str, Any]], *, root: Path = ROOT
) -> tuple[str, dict[str, Any]]:
    """Run a bounded ONNX graph check when the export dependency is present."""

    onnx_artifact = next((item for item in artifacts if item.get("kind") == "onnx"), None)
    if onnx_artifact is None:
        return "not_applicable", {"status": "not_applicable", "reason": "no local ONNX declared"}
    if not onnx_artifact.get("present"):
        return "blocked_asset", {"status": "blocked_asset", "reason": "ONNX file is missing"}
    try:
        import onnx
    except ImportError:
        return "not_run_dependency", {
            "status": "not_run_dependency",
            "reason": "onnx package is not installed in this environment",
        }
    path = root / str(onnx_artifact["path"])
    try:
        model = onnx.load(str(path))
        onnx.checker.check_model(model)
        initializers = {item.name for item in model.graph.initializer}
        inputs = [item for item in model.graph.input if item.name not in initializers]
        if len(inputs) != 1 or not model.graph.output:
            raise ValueError("expected one non-initializer input and at least one output")
        shape = []
        for dimension in inputs[0].type.tensor_type.shape.dim:
            shape.append(int(dimension.dim_value) if dimension.dim_value else 0)
        contract = _contract_record(candidate)
        expected_shape = contract.get("input_shape")
        if expected_shape and len(shape) == len(expected_shape):
            if shape[1:] != expected_shape[1:] or shape[0] not in (0, 1):
                raise ValueError(f"input shape {shape} does not match {expected_shape}")
        output_shape = []
        for dimension in model.graph.output[0].type.tensor_type.shape.dim:
            output_shape.append(int(dimension.dim_value) if dimension.dim_value else 0)
        feature_dim = contract.get("feature_dim")
        if feature_dim and (not output_shape or output_shape[-1] != feature_dim):
            raise ValueError(f"output shape {output_shape} does not end in {feature_dim}")
    except Exception as exc:  # noqa: BLE001 - report the candidate-specific gate
        return "failed", {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"}
    return "passed", {
        "status": "passed",
        "input_name": inputs[0].name,
        "input_shape": shape,
        "output_name": model.graph.output[0].name,
        "output_shape": output_shape,
    }


def build_inventory_record(candidate: CandidateSpec, *, root: Path = ROOT) -> dict[str, Any]:
    """Build a deterministic, non-invasive audit record for one candidate."""

    source_info = candidate.source if isinstance(candidate.source, Mapping) else {}
    source_revision = str(source_info.get("revision", "")).strip()
    artifacts = _declared_artifacts(candidate, root) + _runtime_artifacts(candidate, root)
    declared_local = bool(artifacts)
    artifacts_valid = bool(artifacts) and all(item["valid"] for item in artifacts)
    missing_assets = [item["path"] for item in artifacts if not item["present"]]
    invalid_assets = [item["path"] for item in artifacts if item["present"] and not item["valid"]]
    task_type = str(
        candidate.raw.get("task_type", candidate.raw.get("kind", "candidate"))
    ).lower()
    reasons: list[str] = []
    onnx_status, onnx_detail = _onnx_contract_gate(candidate, artifacts, root=root)

    if task_type in {"code", "sdk"}:
        status = "not_applicable"
        reasons.append(
            "no downloadable NPU model contract; use the task-specific offline or vendor API path"
        )
    elif _license_blocked(candidate):
        status = "blocked_license"
        reasons.append("license or redistribution terms are not sufficiently identified")
    elif not _FIXED_REVISION.fullmatch(source_revision):
        status = "blocked_source"
        reasons.append("source revision is mutable; pin an immutable commit before comparison")
    elif missing_assets or invalid_assets or not declared_local:
        status = "blocked_asset"
        if missing_assets:
            reasons.append("declared local artifact is missing")
        if invalid_assets:
            reasons.append("declared artifact bytes or SHA-256 do not match")
        if not declared_local:
            reasons.append("no local checkpoint, ONNX, or OM is available")
    elif onnx_status == "failed":
        status = "onnx_failed"
        reasons.append(onnx_detail["reason"])
    elif candidate.npu_status == "needs_reimplementation":
        status = "needs_reimplementation"
        reasons.append("no reproducible exporter or runtime adapter is declared")
    else:
        status = "audit_pass"
        reasons.append(
            "source, task metadata, and declared local artifacts passed the static audit"
        )

    return {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "candidate_id": candidate.id,
        "display_name": candidate.display_name,
        "family": candidate.family,
        "task_type": task_type,
        "modality": candidate.modality,
        "comparison_scope": candidate.comparison_scope,
        "source": {
            "url": candidate.source.get("url"),
            "revision": source_revision,
            "reproducible": candidate.reproducible,
        },
        "license": (
            dict(candidate.raw.get("license", {}))
            if isinstance(candidate.raw.get("license"), Mapping)
            else {}
        ),
        "contract": _contract_record(candidate),
        "declared_npu_status": candidate.npu_status,
        "artifacts": artifacts,
        "gates": {
            "source": candidate.reproducible,
            "license": not _license_blocked(candidate),
            "artifact_integrity": artifacts_valid,
            "npu_contract": task_type not in {"code", "sdk"},
            "onnx": onnx_status,
            "atc": "not_started",
            "acl_smoke": "not_started",
            "numeric_consistency": "not_started",
            "quality": "not_started",
            "performance": "not_started",
            "lifecycle": "not_started",
            "faults": {key: "not_started" for key in _BLOCKING_FAULTS},
        },
        "status": status,
        "reasons": reasons,
        "onnx_detail": onnx_detail,
        "production_enabled": False,
        "runtime_integration": "none; candidate inventory is read-only",
        "generated_at": _now(),
    }


def inventory_candidates(
    *, candidate_id: str | None = None, manifest_path: Path | None = None, root: Path = ROOT
) -> list[dict[str, Any]]:
    manifest = CandidateManifest.load(manifest_path) if manifest_path else CandidateManifest.load()
    candidates = manifest.all()
    if candidate_id:
        candidates = [manifest.get(candidate_id)]
    return [build_inventory_record(item, root=root) for item in candidates]


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _report_files(record: Mapping[str, Any], report_dir: Path) -> None:
    sections = {
        "manifest.json": record,
        "source-and-license.json": {
            "candidate_id": record["candidate_id"],
            "source": record["source"],
            "license": record["license"],
        },
        "artifact-hashes.json": {
            "candidate_id": record["candidate_id"],
            "artifacts": record["artifacts"],
        },
        "contract.json": {
            "candidate_id": record["candidate_id"],
            "contract": record["contract"],
        },
        "local-reference.json": {"status": record["status"], "reasons": record["reasons"]},
        "atc.json": {
            "status": record["gates"]["atc"],
            "message": "not run by local audit",
        },
        "acl-smoke.json": {
            "status": record["gates"]["acl_smoke"],
            "message": "not run by local audit",
        },
        "numeric-consistency.json": {"status": record["gates"]["numeric_consistency"]},
        "quality.json": {"status": record["gates"]["quality"]},
        "performance.json": {"status": record["gates"]["performance"]},
        "lifecycle.json": {
            "status": record["gates"]["lifecycle"],
            "faults": record["gates"]["faults"],
        },
        "final-status.json": {
            "schema_version": CAMPAIGN_SCHEMA_VERSION,
            "candidate_id": record["candidate_id"],
            "status": record["status"],
            "reasons": record["reasons"],
            "generated_at": record["generated_at"],
        },
    }
    for name, payload in sections.items():
        _write_json(report_dir / name, payload)


def write_local_report(
    record: Mapping[str, Any], *, report_root: Path = REPORT_DIR / "candidates"
) -> Path:
    run_dir = report_root / _safe_id(str(record["candidate_id"])) / _run_id()
    _report_files(record, run_dir)
    return run_dir


def _load_latest_status(report_root: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not report_root.is_dir():
        return result
    for candidate_dir in report_root.iterdir():
        if not candidate_dir.is_dir():
            continue
        runs = sorted((item for item in candidate_dir.iterdir() if item.is_dir()), reverse=True)
        for run in runs:
            path = run / "final-status.json"
            if path.is_file():
                try:
                    value = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict) and isinstance(value.get("candidate_id"), str):
                    result[value["candidate_id"]] = value
                    break
    return result


def write_campaign_index(
    records: list[Mapping[str, Any]], *, report_root: Path = REPORT_DIR / "candidates"
) -> Path:
    latest = _load_latest_status(report_root)
    for record in records:
        latest[str(record["candidate_id"])] = {
            "candidate_id": record["candidate_id"],
            "status": record["status"],
            "reasons": record["reasons"],
            "generated_at": record["generated_at"],
        }
    payload = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "updated_at": _now(),
        "production_registry_unchanged": True,
        "items": sorted(latest.values(), key=lambda item: str(item.get("candidate_id", ""))),
    }
    path = report_root / "index.json"
    _write_json(path, payload)
    return path


def local_campaign(
    candidate_id: str | None, *, report_root: Path = REPORT_DIR / "candidates"
) -> list[dict[str, Any]]:
    records = inventory_candidates(candidate_id=candidate_id)
    for record in records:
        write_local_report(record, report_root=report_root)
    write_campaign_index(records, report_root=report_root)
    return records


def report_campaign(
    candidate_id: str | None, *, report_root: Path = REPORT_DIR / "candidates"
) -> dict[str, Any]:
    records = inventory_candidates(candidate_id=candidate_id)
    existing = _load_latest_status(report_root)
    items = []
    for record in records:
        item = dict(record)
        item["previous_status"] = existing.get(record["candidate_id"], {}).get("status")
        items.append(item)
    index = write_campaign_index(records, report_root=report_root)
    return {"schema_version": CAMPAIGN_SCHEMA_VERSION, "index": str(index), "items": items}


def _board_smoke(candidate_id: str, image: Path, *, cycles: int = 1) -> dict[str, Any]:
    """Run a real NPU smoke on the board; never silently uses CPU."""

    import cv2
    import numpy as np

    from palmprint_workbench.runtime.adapters import create_adapter, shutdown_acl_runtime

    registry = ModelRegistry()
    if candidate_id == "ccnet":
        spec = registry.get_runtime(candidate_id)
    else:
        # Candidate smoke is deliberately independent from the serving
        # profile. The manual-test resolver validates the mixed-FP16 asset
        # contract without adding the candidate to the production registry.
        spec = registry.manual_test_candidate_embedding_spec(candidate_id)
    if spec.kind != "embedding":
        raise ValueError(
            f"{candidate_id} has task kind {spec.kind!r}; use its task-specific adapter, "
            "not embedding ACL smoke"
        )
    frame = cv2.imread(str(image), cv2.IMREAD_GRAYSCALE)
    if frame is None:
        raise ValueError(f"could not read ROI image: {image}")
    frame = cv2.resize(frame, (128, 128), interpolation=cv2.INTER_AREA)
    cycles = max(1, int(cycles))
    results: list[dict[str, Any]] = []
    failures: list[str] = []
    for index in range(cycles):
        adapter = None
        try:
            adapter = create_adapter(spec, "npu", "mixed_fp16", threads=1)
            output = adapter.encode(frame)
            results.append({
                "cycle": index + 1,
                "feature_dim": int(output.code.size),
                "finite": bool(np.all(np.isfinite(output.code))),
                "inference_ms": float(output.inference_ms),
            })
        except BaseException as exc:
            failures.append(f"cycle {index + 1}: {type(exc).__name__}: {exc}")
            break
        finally:
            if adapter is not None:
                try:
                    adapter.close()
                except BaseException as exc:
                    failures.append(f"close: {type(exc).__name__}: {exc}")
    runtime = shutdown_acl_runtime()
    return {
        "candidate_id": candidate_id,
        "backend": "npu",
        "precision": "mixed_fp16",
        "cycles_requested": cycles,
        "cycles_completed": len(results),
        "results": results,
        "failures": failures,
        "runtime_shutdown": runtime,
        "status": (
            "passed"
            if len(results) == cycles and not failures and runtime.get("ok", False)
            else "acl_failed"
        ),
        "generated_at": _now(),
    }


def _board_preflight(candidate_id: str) -> dict[str, Any] | None:
    """Validate the board runtime package before importing ACL/OpenCV.

    The board staging package intentionally contains OM files only.  It does
    not contain research checkpoints or reference ONNX exports, so the board
    gate must not reuse the full local-audit status for those optional inputs.
    """

    try:
        registry = ModelRegistry()
        candidate = registry.get_candidate(candidate_id)
    except (KeyError, ValueError, OSError, IndexError) as exc:
        return {
            "candidate_id": candidate_id,
            "backend": "npu",
            "precision": "mixed_fp16",
            "status": "blocked_source",
            "failures": [f"candidate inventory failed: {type(exc).__name__}: {exc}"],
            "generated_at": _now(),
        }
    task_type = str(candidate.raw.get("task_type", candidate.raw.get("kind", "candidate"))).lower()
    if task_type != "embedding":
        return {
            "candidate_id": candidate_id,
            "backend": "npu",
            "precision": "mixed_fp16",
            "status": "not_applicable",
            "failures": [
                f"task_type={task_type} requires a task-specific adapter; "
                "embedding ACL smoke is not applicable"
            ],
            "generated_at": _now(),
        }
    try:
        if candidate_id == "ccnet":
            spec = registry.get_runtime(candidate_id)
        else:
            spec = registry.manual_test_candidate_embedding_spec(candidate_id)
        asset_status = registry.runtime_asset_status(spec, verify_hash=True)
    except (KeyError, ValueError, OSError) as exc:
        return {
            "candidate_id": candidate_id,
            "backend": "npu",
            "precision": "mixed_fp16",
            "status": "blocked_asset",
            "failures": [f"runtime asset gate failed: {type(exc).__name__}: {exc}"],
            "generated_at": _now(),
        }
    if not asset_status.get("ok"):
        return {
            "candidate_id": candidate_id,
            "backend": "npu",
            "precision": "mixed_fp16",
            "status": "blocked_asset",
            "failures": list(asset_status.get("reasons", [])),
            "runtime_asset_status": asset_status,
            "generated_at": _now(),
        }
    return None


def board_campaign(
    candidate_id: str,
    image: Path,
    *,
    cycles: int = 1,
    report_root: Path = REPORT_DIR / "candidates",
) -> dict[str, Any]:
    started = time.monotonic()
    try:
        base_record = inventory_candidates(candidate_id=candidate_id)[0]
    except (KeyError, ValueError, OSError, IndexError):
        base_record = None
    payload = _board_preflight(candidate_id)
    if payload is None:
        try:
            payload = _board_smoke(candidate_id, image, cycles=cycles)
        except BaseException as exc:
            payload = {
                "candidate_id": candidate_id,
                "backend": "npu",
                "precision": "mixed_fp16",
                "status": "acl_failed",
                "failures": [f"{type(exc).__name__}: {exc}"],
                "generated_at": _now(),
            }
    payload["elapsed_seconds"] = time.monotonic() - started
    run_dir = report_root / _safe_id(candidate_id) / _run_id()
    if base_record is not None:
        _report_files(base_record, run_dir)
    _write_json(run_dir / "acl-smoke.json", payload)
    final = {
        "schema_version": CAMPAIGN_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "status": payload["status"],
        "reasons": payload.get("failures", []),
        "generated_at": payload["generated_at"],
    }
    _write_json(run_dir / "final-status.json", final)
    write_campaign_index([final], report_root=report_root)
    return payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("inventory", "local", "report"):
        sub = subparsers.add_parser(command)
        group = sub.add_mutually_exclusive_group(required=True)
        group.add_argument("--all", action="store_true", dest="all_candidates")
        group.add_argument("--candidate", dest="candidate_id")
        sub.add_argument("--report-root", type=Path, default=REPORT_DIR / "candidates")
    board = subparsers.add_parser("board", help="run one real board-side NPU smoke")
    board.add_argument("--candidate", required=True, dest="candidate_id")
    board.add_argument("--image", required=True, type=Path)
    board.add_argument("--cycles", type=int, default=1)
    board.add_argument("--report-root", type=Path, default=REPORT_DIR / "candidates")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "inventory":
        records = inventory_candidates(candidate_id=args.candidate_id)
        print(json.dumps({"count": len(records), "items": records}, ensure_ascii=False, indent=2))
        return 0
    if args.command == "local":
        records = local_campaign(
            None if args.all_candidates else args.candidate_id,
            report_root=args.report_root,
        )
        print(json.dumps({"count": len(records), "items": records}, ensure_ascii=False, indent=2))
        # Blocked and not-applicable candidates are valid audit outcomes.  The
        # complete campaign is successful when every candidate has a record;
        # model execution gates are reported separately by later commands.
        return 0
    if args.command == "report":
        payload = report_campaign(
            None if args.all_candidates else args.candidate_id,
            report_root=args.report_root,
        )
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    payload = board_campaign(
        args.candidate_id,
        args.image,
        cycles=args.cycles,
        report_root=args.report_root,
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
