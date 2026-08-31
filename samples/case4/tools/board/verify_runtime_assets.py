#!/usr/bin/env python3
"""Validate the synchronized palmprint runtime package without Torch, ATC, or export."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

from palmprint_workbench.config import ROOT

from palmprint_workbench.domain.registry import ModelRegistry, ModelSpec


MIXED_FP16 = "mixed_fp16"
# Synchronized release artifacts from the verified 2026-08-14 board build.
# Keeping these values here lets a deployment verify the package even when
# report files are intentionally excluded from a smaller runtime bundle.
RECORDED_HASHES = {
    ("ccnet", MIXED_FP16): "8465fbf483524a1b373618e5e67b2903c522a32f0fc3b3ee6a4b40881295b7f1",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def conversion_hashes() -> dict[tuple[str, str], str]:
    """Read locally captured OM and CompNet ONNX hashes when available."""
    hashes = dict(RECORDED_HASHES)
    manifest = ROOT / "reports" / "model_conversion.json"
    try:
        records = json.loads(manifest.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return hashes
    if not isinstance(records, list):
        return hashes
    for record in records:
        if not isinstance(record, dict):
            continue
        model_id = record.get("model_id")
        precision = record.get("precision")
        digest = record.get("sha256")
        if isinstance(model_id, str) and isinstance(precision, str) and isinstance(digest, str):
            hashes[(model_id, precision)] = digest.lower()
        onnx_digest = record.get("onnx_sha256")
        if isinstance(model_id, str) and isinstance(onnx_digest, str):
            hashes[(model_id, "onnx")] = onnx_digest.lower()
    return hashes


def onnx_contract(path: Path, spec: ModelSpec) -> dict[str, Any]:
    try:
        import onnx
    except ImportError as exc:
        raise RuntimeError(
            "onnx is required for contract checks; install the documented "
            "export/verification requirements in the active environment"
        ) from exc

    model = onnx.load(str(path))
    onnx.checker.check_model(model)
    initializers = {item.name for item in model.graph.initializer}
    inputs = [item for item in model.graph.input if item.name not in initializers]
    if len(inputs) != 1 or not model.graph.output:
        raise ValueError("expected exactly one ONNX input and at least one output")
    dimensions = [int(dim.dim_value) for dim in inputs[0].type.tensor_type.shape.dim]
    expected = list(spec.input_shape)
    if len(dimensions) != len(expected) or dimensions[1:] != expected[1:] or dimensions[0] not in (0, 1):
        raise ValueError(f"unexpected ONNX input shape {dimensions}; expected {expected}")
    output_dimensions = [
        int(dim.dim_value) for dim in model.graph.output[0].type.tensor_type.shape.dim
    ]
    if spec.feature_dim and (not output_dimensions or output_dimensions[-1] != spec.feature_dim):
        raise ValueError(
            f"unexpected ONNX output shape {output_dimensions}; expected final dimension {spec.feature_dim}"
        )
    return {
        "input_name": inputs[0].name,
        "input_shape": dimensions,
        "output_name": model.graph.output[0].name,
        "output_shape": output_dimensions,
    }


def add_hash_result(result: dict[str, Any], path: Path, expected: str | None, strict: bool) -> None:
    actual = sha256_file(path)
    result["sha256"] = actual
    if expected:
        result["expected_sha256"] = expected
        if actual.lower() != expected.lower():
            raise ValueError(f"SHA-256 mismatch for {path}")
    elif strict:
        raise ValueError(f"No recorded SHA-256 is available for {path}")
    else:
        result["hash_recorded"] = False


def add_size_result(result: dict[str, Any], path: Path, expected: Any) -> None:
    """Record and enforce a manifest size when one is available."""

    actual = path.stat().st_size
    result["bytes"] = actual
    if expected is None:
        return
    if isinstance(expected, bool) or not isinstance(expected, int) or expected <= 0:
        raise ValueError(f"Invalid expected byte count for {path}")
    result["expected_bytes"] = expected
    if actual != expected:
        raise ValueError(f"Byte-size mismatch for {path}: expected {expected}, got {actual}")


def validate_embedding(
    spec: ModelSpec,
    hashes: dict[tuple[str, str], str],
    strict: bool,
    registry: ModelRegistry,
    require_onnx_contract: bool,
) -> list[dict[str, Any]]:
    candidate_id = spec.raw.get("candidate_id")
    if isinstance(candidate_id, str):
        decision = registry.candidate_admission(candidate_id, verify_assets=True)
        if not decision.admitted:
            raise ValueError(
                f"Production admission failed for {candidate_id}: "
                f"{'; '.join(decision.reasons)}"
            )
    assets = spec.raw.get("assets", {})
    if not isinstance(assets, dict):
        assets = {}
    reference_asset = assets.get("reference_onnx", {})
    if not isinstance(reference_asset, dict):
        reference_asset = {}
    om_asset = assets.get("mixed_fp16_om", {})
    if not isinstance(om_asset, dict):
        om_asset = {}

    reference_onnx = spec.path("reference_onnx")
    if reference_onnx is None or not reference_onnx.is_file():
        if require_onnx_contract:
            raise FileNotFoundError(f"ONNX model is missing: {reference_onnx}")
        onnx_result = {
            "model_id": spec.id,
            "asset": "reference_onnx",
            "path": str(reference_onnx) if reference_onnx is not None else None,
            "status": "optional_missing",
            "optional": True,
            "contract_checked": False,
            "contract_reason": (
                "Reference ONNX is optional for board runtime; use "
                "--require-onnx-contract in an export environment."
            ),
        }
    else:
        onnx_result = {
            "model_id": spec.id,
            "asset": "reference_onnx",
            "path": str(reference_onnx),
        }
        expected_onnx = (
            reference_asset.get("sha256")
            or spec.raw.get("onnx_sha256")
            or spec.raw.get("sha256")
            or hashes.get((spec.id, "onnx"))
        )
        if not isinstance(expected_onnx, str):
            expected_onnx = None
        add_hash_result(onnx_result, reference_onnx, expected_onnx, strict)
        add_size_result(
            onnx_result,
            reference_onnx,
            reference_asset.get("bytes", spec.raw.get("onnx_bytes")),
        )
        if require_onnx_contract:
            onnx_result.update(onnx_contract(reference_onnx, spec))
            onnx_result["contract_checked"] = True
        else:
            onnx_result["contract_checked"] = False
            onnx_result["contract_reason"] = (
                "Use --require-onnx-contract in the export environment; the board "
                "runtime verifies bytes and SHA-256 without installing ONNX."
            )

    om_model = spec.om_path(MIXED_FP16)
    if om_model is None or not om_model.is_file():
        raise FileNotFoundError(f"mixed FP16 OM model is missing: {om_model}")
    om_result: dict[str, Any] = {
        "model_id": spec.id,
        "asset": "om",
        "precision": MIXED_FP16,
        "path": str(om_model),
    }
    expected_om = (
        om_asset.get("sha256")
        or spec.raw.get("mixed_fp16_sha256")
        or hashes.get((spec.id, MIXED_FP16))
    )
    add_hash_result(om_result, om_model, expected_om, strict)
    add_size_result(
        om_result,
        om_model,
        om_asset.get("bytes", spec.raw.get("mixed_fp16_bytes")),
    )
    return [onnx_result, om_result]


def validate_datasets(strict: bool) -> list[dict[str, Any]]:
    manifest_path = ROOT / "dataset_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    records = payload.get("datasets", [])
    if not isinstance(records, list):
        raise ValueError("dataset manifest has no dataset list")
    results = []
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("dataset manifest contains an invalid record")
        path = (ROOT / str(record["archive"])).resolve()
        if not path.is_file():
            raise FileNotFoundError(f"dataset archive is missing: {path}")
        expected_size = int(record["size"])
        if path.stat().st_size != expected_size:
            raise ValueError(f"unexpected archive size for {path}: {path.stat().st_size}")
        result: dict[str, Any] = {"dataset_id": str(record["id"]), "path": str(path), "size": expected_size}
        expected_hash = record.get("sha256")
        add_hash_result(result, path, str(expected_hash) if expected_hash else None, strict)
        results.append(result)
    return results


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model",
        default="all",
        help="all or an explicitly admitted production model ID (offline models are excluded)",
    )
    parser.add_argument("--datasets", action="store_true", help="also verify the three dataset archives")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="fail when a synchronized model has no recorded SHA-256 in metadata",
    )
    parser.add_argument(
        "--require-onnx-contract",
        action="store_true",
        help="load ONNX and enforce its graph I/O contract (export environment only)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    registry = ModelRegistry()
    if args.model == "all":
        selected = [spec.id for spec in registry.all()]
    else:
        selected = [args.model]
    hashes = conversion_hashes()
    output: dict[str, Any] = {"models": [], "datasets": []}
    failures: list[str] = []

    for model_id in selected:
        try:
            spec = registry.get_runtime(model_id)
            if spec.kind != "embedding":
                raise ValueError(f"unsupported production model kind: {spec.kind}")
            output["models"].extend(
                validate_embedding(
                    spec,
                    hashes,
                    args.strict,
                    registry,
                    args.require_onnx_contract,
                )
            )
        except Exception as exc:  # Keep diagnostics usable in unattended board setup.
            failures.append(f"{model_id}: {exc}")

    if args.datasets:
        try:
            output["datasets"] = validate_datasets(args.strict)
        except Exception as exc:
            failures.append(f"datasets: {exc}")

    output["ok"] = not failures
    output["failures"] = failures
    print(json.dumps(output, indent=2, ensure_ascii=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
