#!/usr/bin/env python3
"""Export CompNet and convert fixed-shape ONNX models on the Ascend board."""

from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

import numpy as np

from palmprint_workbench.config import BENCHMARK_SEED, MODEL_DIR, REGISTRY_PATH, REPORT_DIR, ROOT, ensure_runtime_dirs
from palmprint_workbench.domain.registry import ModelRegistry


SOC_VERSION = "Ascend310B4"
COMPNET_INPUT_SHAPE = (1, 1, 128, 128)
COMPNET_FEATURE_DIM = 512
PRECISION_MODES = {
    "origin": "must_keep_origin_dtype",
    "mixed_fp16": "allow_fp32_to_fp16",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(4 * 1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def validate_onnx(path: Path, expected_dim: int) -> dict:
    import onnx

    model = onnx.load(str(path))
    onnx.checker.check_model(model)
    inputs = [item for item in model.graph.input if item.name not in {x.name for x in model.graph.initializer}]
    if len(inputs) != 1 or not model.graph.output:
        raise ValueError("Expected one ONNX input and at least one output")
    dimensions = [value.dim_value for value in inputs[0].type.tensor_type.shape.dim]
    if len(dimensions) != 4 or dimensions[1:] != [1, 128, 128] or dimensions[0] not in (0, 1):
        raise ValueError(f"Unexpected ONNX input dimensions: {dimensions}")
    output_value = model.graph.output[0]
    output_dims = [
        dimension.dim_value
        for dimension in output_value.type.tensor_type.shape.dim
    ]
    if expected_dim > 0 and output_dims and output_dims[-1] not in (0, expected_dim):
        raise ValueError(
            f"Unexpected ONNX output dimensions for {path}: {output_dims}; "
            f"expected final dimension {expected_dim}"
        )
    return {
        "path": str(path),
        "sha256": sha256_file(path),
        "size": path.stat().st_size,
        "input_name": inputs[0].name,
        "input_shape": [1, 1, 128, 128],
        "onnx_declared_input_shape": dimensions,
        "output_name": output_value.name,
        "output_shape": output_dims,
        "expected_feature_dim": expected_dim,
    }


def _export_onnx_compat(torch_module, model, dummy, output: Path, opset: int) -> None:
    """Export through Torch's legacy ONNX path without requiring onnxscript.

    Recent Torch releases expose a dynamo exporter whose optional dependency
    is not part of the palmprint local runtime.  Explicitly selecting ``dynamo=False``
    keeps the graph deterministic and works with the CANN ONNX toolchain.  The
    signature check preserves compatibility with older Torch versions that do
    not expose the keyword at all.
    """

    kwargs = {
        "input_names": ["input"],
        "output_names": ["embedding"],
        "opset_version": opset,
        "do_constant_folding": True,
        "dynamic_axes": None,
    }
    try:
        supports_dynamo = "dynamo" in inspect.signature(torch_module.onnx.export).parameters
    except (TypeError, ValueError):
        supports_dynamo = False
    if supports_dynamo:
        kwargs["dynamo"] = False
    try:
        torch_module.onnx.export(model, dummy, str(output), **kwargs)
    except TypeError as error:
        # A few vendor-patched Torch builds accept the callable but reject the
        # keyword at runtime. Retry only for that specific compatibility case;
        # all graph/export errors should remain visible to the caller.
        if "dynamo" not in kwargs or "dynamo" not in str(error).lower():
            raise
        kwargs.pop("dynamo", None)
        torch_module.onnx.export(model, dummy, str(output), **kwargs)


def _export_compnet_checkpoint(
    *,
    checkpoint: Path | None,
    output: Path,
    marker: Path,
    seed: int,
    opset: int,
    checkpoint_sha256: str | None,
    checkpoint_size_bytes: int | None = None,
    candidate_id: str | None = None,
    source_revision: str = "21f8b56bcbcb620eafa85eaff5ea1f5a9675f194",
    force: bool = True,
) -> dict:
    """Export one verified CompNet checkpoint and write an ignored marker.

    This helper intentionally knows nothing about ATC or OM files.  A caller
    must provide the expected checkpoint hash when using a candidate manifest;
    this prevents accidentally exporting a similarly named but different
    checkpoint.
    """

    import torch

    # Import the Torch model only for export commands.  Runtime checks and
    # board-side ATC helpers can still run in the lightweight environment.
    from .compnet_static import build_static_compnet

    ensure_runtime_dirs()
    checkpoint = Path(checkpoint).resolve() if checkpoint is not None else None
    output = Path(output).resolve()
    marker = Path(marker).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    marker.parent.mkdir(parents=True, exist_ok=True)
    if checkpoint is not None and not checkpoint.is_file():
        raise FileNotFoundError(checkpoint)
    checkpoint_hash = sha256_file(checkpoint) if checkpoint is not None else None
    if checkpoint_sha256 and checkpoint is None:
        raise ValueError("checkpoint_sha256 cannot be supplied without --checkpoint")
    if checkpoint_sha256 and checkpoint_hash and checkpoint_hash.lower() != checkpoint_sha256.lower():
        raise ValueError(
            f"Checkpoint SHA-256 mismatch for {checkpoint}: "
            f"expected {checkpoint_sha256}, got {checkpoint_hash}"
        )
    if checkpoint is not None and checkpoint_size_bytes is not None and checkpoint.stat().st_size != checkpoint_size_bytes:
        raise ValueError(
            f"Checkpoint size mismatch for {checkpoint}: expected "
            f"{checkpoint_size_bytes}, got {checkpoint.stat().st_size}"
        )

    # Existing files are never silently replaced by a batch conversion.  The
    # caller can opt into replacement explicitly with ``--force``.
    if output.is_file() and not force:
        metadata = validate_onnx(output, COMPNET_FEATURE_DIM)
        status = {
            "status": "exists",
            "candidate_id": candidate_id,
            "source_revision": source_revision,
            "seed": seed,
            "checkpoint_present": checkpoint is not None,
            "checkpoint_sha256": checkpoint_hash,
            "checkpoint_hash_verified": bool(checkpoint_sha256 and checkpoint_hash),
            "accuracy_eligible": bool(checkpoint_sha256 and checkpoint_hash),
            "mode": "official" if checkpoint_sha256 else "conversion-only",
            "onnx": metadata,
        }
        marker.write_text(json.dumps(status, indent=2), encoding="utf-8")
        return status

    model, checkpoint_present = build_static_compnet(checkpoint, seed)
    dummy = torch.zeros(*COMPNET_INPUT_SHAPE, dtype=torch.float32)
    with torch.no_grad():
        probe = model(dummy).cpu().numpy()
    if probe.shape != (1, COMPNET_FEATURE_DIM) or not np.all(np.isfinite(probe)):
        raise RuntimeError(f"Invalid CompNet probe output: {probe.shape}")
    if not np.allclose(np.linalg.norm(probe, axis=1), 1.0, atol=1e-5):
        raise RuntimeError("CompNet probe output is not L2 normalized")
    _export_onnx_compat(torch, model, dummy, output, opset)
    metadata = validate_onnx(output, COMPNET_FEATURE_DIM)
    verified = bool(checkpoint_present and checkpoint_sha256 and checkpoint_hash)
    status = {
        "status": "exported",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "candidate_id": candidate_id,
        "source_revision": source_revision,
        "seed": seed,
        "checkpoint_present": checkpoint_present,
        "checkpoint_sha256": checkpoint_hash,
        "checkpoint_hash_verified": verified,
        "accuracy_eligible": verified,
        "mode": "official" if verified else "conversion-only",
        "input_shape": list(COMPNET_INPUT_SHAPE),
        "output_shape": list(probe.shape),
        "onnx": metadata,
    }
    marker.write_text(json.dumps(status, indent=2), encoding="utf-8")
    return status


def export_compnet(args: argparse.Namespace) -> dict:
    """Backward-compatible single-checkpoint export command."""

    checkpoint = Path(args.checkpoint).resolve() if args.checkpoint else None
    return _export_compnet_checkpoint(
        checkpoint=checkpoint,
        output=Path(args.output),
        marker=Path(args.marker),
        seed=args.seed,
        opset=args.opset,
        checkpoint_sha256=args.checkpoint_sha256,
        force=True,
    )


COMPNET_VARIANT_IDS = (
    "compnet_tongji_600",
    "compnet_iitd_460",
    "compnet_rest_358",
    "compnet_xjtu_flash_200",
    "compnet_xjtu_natural_200",
)


def compnet_variant_output_path(output_dir: Path, candidate_id: str) -> Path:
    """Return the deterministic ONNX path for a registered variant.

    Restricting the ID to the manifest-backed allow-list prevents a command
    line value such as ``../../outside`` from becoming an output filename.
    ``output_dir`` itself may be a caller-owned temporary directory for local
    tests; only the generated basename is controlled here.
    """

    if candidate_id not in COMPNET_VARIANT_IDS:
        raise ValueError(f"Unknown CompNet variant: {candidate_id}")
    return Path(output_dir).resolve() / f"{candidate_id}.onnx"


def compnet_variant_marker_path(marker_dir: Path, candidate_id: str) -> Path:
    """Return the deterministic ignored metadata marker path."""

    if candidate_id not in COMPNET_VARIANT_IDS:
        raise ValueError(f"Unknown CompNet variant: {candidate_id}")
    return Path(marker_dir).resolve() / f"{candidate_id}_export.json"


def _project_asset_path(value: object, label: str) -> Path:
    """Resolve a manifest asset path without allowing it to escape the project."""

    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty project-relative path")
    normalized = value.replace("\\", "/")
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:/", normalized):
        raise ValueError(f"{label} must be project-relative")
    if any(part in {"", ".", ".."} for part in normalized.split("/")):
        raise ValueError(f"{label} must not contain traversal components")
    path = (ROOT / Path(normalized)).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise ValueError(f"{label} resolves outside the palmprint project") from exc
    return path


def compnet_variant_conversion_paths(candidate_id: str) -> dict[str, Path]:
    """Read the expected ONNX/OM locations from a verified candidate entry."""

    if candidate_id not in COMPNET_VARIANT_IDS:
        raise ValueError(f"Unknown CompNet variant: {candidate_id}")
    candidate = ModelRegistry().get_candidate(candidate_id)
    conversion = candidate.raw.get("conversion")
    if not isinstance(conversion, dict):
        raise ValueError(f"{candidate_id} has no conversion metadata")
    om_paths = conversion.get("om_paths")
    if not isinstance(om_paths, dict):
        raise ValueError(f"{candidate_id}.conversion.om_paths must be an object")
    return {
        "onnx": _project_asset_path(
            conversion.get("onnx_path"), f"{candidate_id}.conversion.onnx_path"
        ),
        "marker": _project_asset_path(
            conversion.get("marker_path"), f"{candidate_id}.conversion.marker_path"
        ),
        "origin": _project_asset_path(
            om_paths.get("origin"), f"{candidate_id}.conversion.om_paths.origin"
        ),
        "mixed_fp16": _project_asset_path(
            om_paths.get("mixed_fp16"),
            f"{candidate_id}.conversion.om_paths.mixed_fp16",
        ),
    }


def export_compnet_variants(args: argparse.Namespace) -> list[dict]:
    """Export one or all manifest-registered CompNet variants locally.

    Output names are derived from the immutable candidate ID, so checkpoints
    trained on different datasets cannot overwrite one another.  This command
    intentionally stops after ONNX validation; OM conversion belongs on the
    Ascend board and is handled by ``convert-compnet-variants`` there.
    """

    registry = ModelRegistry()
    selected = COMPNET_VARIANT_IDS if args.variant == "all" else (args.variant,)
    output_dir = Path(args.output_dir).resolve()
    marker_dir = Path(args.marker_dir).resolve()
    results: list[dict] = []
    for candidate_id in selected:
        candidate = registry.get_candidate(candidate_id)
        if candidate.family != "CompNet Static Gabor":
            raise ValueError(f"{candidate_id} is not a CompNet Static Gabor candidate")
        checkpoint = candidate.checkpoint_path
        if checkpoint is None:
            raise FileNotFoundError(f"No checkpoint declared for {candidate_id}")
        results.append(
            _export_compnet_checkpoint(
                checkpoint=checkpoint,
                output=compnet_variant_output_path(output_dir, candidate_id),
                marker=compnet_variant_marker_path(marker_dir, candidate_id),
                seed=args.seed,
                opset=args.opset,
                checkpoint_sha256=candidate.checkpoint_sha256,
                checkpoint_size_bytes=candidate.checkpoint_size_bytes,
                candidate_id=candidate.id,
                source_revision=candidate.revision,
                force=args.force,
            )
        )
    return results


def _convert_fixed_shape_onnx(
    *,
    model_id: str,
    onnx_path: Path,
    output_path: Path,
    feature_dim: int,
    precision: str,
    force: bool,
) -> dict:
    """Run ATC for a validated fixed-shape encoder on an Ascend board.

    Callers resolve their model paths before reaching this function.  It is
    intentionally not called by local export/check commands, so a development
    machine never requires CANN or an NPU runtime.
    """

    if precision not in PRECISION_MODES:
        raise ValueError(f"Unsupported precision: {precision}")
    if not onnx_path.is_file():
        raise FileNotFoundError(f"ONNX model is missing: {onnx_path}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    log_dir = REPORT_DIR / "atc"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{model_id}_{precision}.log"
    if output_path.is_file() and not force:
        return {
            "model_id": model_id,
            "precision": precision,
            "status": "exists",
            "path": str(output_path),
            "size": output_path.stat().st_size,
            "sha256": sha256_file(output_path),
            "onnx_sha256": sha256_file(onnx_path),
            "soc_version": SOC_VERSION,
            "precision_mode": PRECISION_MODES[precision],
            "log": str(log_path),
        }
    onnx_info = validate_onnx(onnx_path, feature_dim)
    command = [
        "atc",
        f"--model={onnx_path}",
        "--framework=5",
        f"--output={output_path.with_suffix('')}",
        f"--soc_version={SOC_VERSION}",
        "--input_format=NCHW",
        f"--input_shape={onnx_info['input_name']}:1,1,128,128",
        f"--precision_mode={PRECISION_MODES[precision]}",
    ]
    environment = os.environ.copy()
    # Ascend 310B development boards have limited host RAM; CANN defaults to eight workers.
    environment.setdefault("TE_PARALLEL_COMPILER", "1")
    environment.setdefault("MAX_COMPILE_CORE_NUMBER", "1")
    with log_path.open("w", encoding="utf-8") as log:
        try:
            process = subprocess.run(
                command, stdout=log, stderr=subprocess.STDOUT, text=True, env=environment
            )
        except OSError as exc:
            raise RuntimeError(
                f"ATC could not start for {model_id}/{precision}; inspect {log_path}: {exc}"
            ) from exc
    if process.returncode != 0 or not output_path.is_file():
        raise RuntimeError(f"ATC failed for {model_id}/{precision}; inspect {log_path}")
    return {
        "model_id": model_id,
        "precision": precision,
        "status": "converted",
        "path": str(output_path),
        "size": output_path.stat().st_size,
        "sha256": sha256_file(output_path),
        "onnx_sha256": sha256_file(onnx_path),
        "soc_version": SOC_VERSION,
        "precision_mode": PRECISION_MODES[precision],
        "te_parallel_compiler": environment["TE_PARALLEL_COMPILER"],
        "max_compile_core_number": environment["MAX_COMPILE_CORE_NUMBER"],
        "command": command,
        "log": str(log_path),
    }


def convert_one(model_id: str, precision: str, force: bool) -> dict:
    """Convert a production-registry model; candidates use their own command."""

    spec = ModelRegistry().get(model_id)
    if spec.kind != "embedding":
        raise ValueError(f"{model_id} does not convert to OM")
    onnx_path = spec.path("reference_onnx")
    output_path = spec.om_path(precision)
    if onnx_path is None:
        raise FileNotFoundError(f"ONNX model is missing: {onnx_path}")
    if output_path is None:
        raise ValueError(f"No OM output registered for {model_id}/{precision}")
    return _convert_fixed_shape_onnx(
        model_id=model_id,
        onnx_path=onnx_path,
        output_path=output_path,
        feature_dim=int(spec.feature_dim or 0),
        precision=precision,
        force=force,
    )


def _write_conversion_report(results: list[dict]) -> None:
    """Merge board-side ATC outcomes without making them source artifacts."""

    manifest = REPORT_DIR / "model_conversion.json"
    try:
        existing = json.loads(manifest.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        existing = []
    if not isinstance(existing, list):
        existing = []
    merged = {
        (str(item.get("model_id")), str(item.get("precision"))): item
        for item in existing
        if isinstance(item, dict)
    }
    for result in results:
        result["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        merged[(result["model_id"], result["precision"])] = result
    manifest.write_text(
        json.dumps(
            sorted(merged.values(), key=lambda item: (item["model_id"], item["precision"])),
            indent=2,
        ),
        encoding="utf-8",
    )


def convert_compnet_variant_one(candidate_id: str, precision: str, force: bool) -> dict:
    """Convert one audited CompNet candidate on an Ascend board.

    The candidate remains out of the production registry even when ATC
    succeeds.  ACL smoke tests and numerical comparison still gate promotion
    to the NPU-only service.
    """

    registry = ModelRegistry()
    candidate = registry.get_candidate(candidate_id)
    if candidate.id not in COMPNET_VARIANT_IDS or candidate.family != "CompNet Static Gabor":
        raise ValueError(f"{candidate_id} is not a supported CompNet variant")
    if candidate.kind != "embedding" or candidate.input_shape != COMPNET_INPUT_SHAPE:
        raise ValueError(f"{candidate_id} does not have the fixed CompNet embedding contract")
    if candidate.feature_dim != COMPNET_FEATURE_DIM:
        raise ValueError(f"{candidate_id} does not have a {COMPNET_FEATURE_DIM}-D feature contract")
    paths = compnet_variant_conversion_paths(candidate.id)
    onnx_path = paths["onnx"]
    if not onnx_path.is_file():
        raise FileNotFoundError(f"ONNX model is missing: {onnx_path}")
    conversion = candidate.raw["conversion"]
    expected_sha = conversion.get("onnx_sha256")
    if expected_sha and sha256_file(onnx_path).lower() != expected_sha.lower():
        raise ValueError(
            f"ONNX SHA-256 mismatch for {candidate.id}: expected {expected_sha}, "
            f"got {sha256_file(onnx_path)}"
        )
    expected_bytes = conversion.get("onnx_bytes")
    if expected_bytes is not None and onnx_path.stat().st_size != expected_bytes:
        raise ValueError(
            f"ONNX size mismatch for {candidate.id}: expected {expected_bytes}, "
            f"got {onnx_path.stat().st_size}"
        )
    return _convert_fixed_shape_onnx(
        model_id=candidate.id,
        onnx_path=onnx_path,
        output_path=paths[precision],
        feature_dim=COMPNET_FEATURE_DIM,
        precision=precision,
        force=force,
    )


def convert_compnet_variants(args: argparse.Namespace) -> list[dict]:
    """Board-only ATC conversion for manifest-audited CompNet variants."""

    precision_values = list(PRECISION_MODES) if args.precision == "both" else [args.precision]
    candidate_ids = COMPNET_VARIANT_IDS if args.variant == "all" else (args.variant,)
    results: list[dict] = []
    failures: list[str] = []
    for candidate_id in candidate_ids:
        for precision in precision_values:
            try:
                results.append(convert_compnet_variant_one(candidate_id, precision, args.force))
            except (OSError, RuntimeError, ValueError) as error:
                failures.append(str(error))
                results.append(
                    {
                        "model_id": candidate_id,
                        "precision": precision,
                        "status": "failed",
                        "soc_version": SOC_VERSION,
                        "precision_mode": PRECISION_MODES[precision],
                        "error": str(error),
                        "log": str(REPORT_DIR / "atc" / f"{candidate_id}_{precision}.log"),
                    }
                )
    _write_conversion_report(results)
    if failures:
        raise RuntimeError("; ".join(failures))
    return results


def check_compnet_variants(args: argparse.Namespace) -> list[dict]:
    """Validate exported ONNX and declared OM paths without calling CANN."""

    candidate_ids = COMPNET_VARIANT_IDS if args.variant == "all" else (args.variant,)
    results: list[dict] = []
    for candidate_id in candidate_ids:
        try:
            candidate = ModelRegistry().get_candidate(candidate_id)
            paths = compnet_variant_conversion_paths(candidate.id)
            onnx_path = paths["onnx"]
            if not onnx_path.is_file():
                results.append(
                    {
                        "model_id": candidate.id,
                        "status": "missing",
                        "path": str(onnx_path),
                        "reason": "ONNX file is absent",
                    }
                )
                continue
            conversion = candidate.raw["conversion"]
            actual_sha = sha256_file(onnx_path)
            expected_sha = conversion.get("onnx_sha256")
            expected_bytes = conversion.get("onnx_bytes")
            if expected_sha and actual_sha.lower() != expected_sha.lower():
                raise ValueError(
                    f"ONNX SHA-256 mismatch: expected {expected_sha}, got {actual_sha}"
                )
            if expected_bytes is not None and onnx_path.stat().st_size != expected_bytes:
                raise ValueError(
                    f"ONNX size mismatch: expected {expected_bytes}, got {onnx_path.stat().st_size}"
                )
            results.append(
                {
                    "model_id": candidate.id,
                    "status": "ready",
                    **validate_onnx(onnx_path, COMPNET_FEATURE_DIM),
                    "om_origin": "ready" if paths["origin"].is_file() else "missing",
                    "om_mixed_fp16": "ready" if paths["mixed_fp16"].is_file() else "missing",
                }
            )
        except (OSError, RuntimeError, ValueError) as error:
            results.append(
                {"model_id": candidate_id, "status": "invalid", "error": str(error)}
            )
    return results


def convert_models(args: argparse.Namespace) -> list[dict]:
    precision_values = list(PRECISION_MODES) if args.precision == "both" else [args.precision]
    model_values = ["ccnet", "compnet"] if args.model == "all" else [args.model]
    results = []
    failures = []
    for model_id in model_values:
        for precision in precision_values:
            try:
                results.append(convert_one(model_id, precision, args.force))
            except RuntimeError as error:
                failures.append(str(error))
                results.append(
                    {
                        "model_id": model_id,
                        "precision": precision,
                        "status": "failed",
                        "soc_version": SOC_VERSION,
                        "precision_mode": PRECISION_MODES[precision],
                        "error": str(error),
                        "log": str(REPORT_DIR / "atc" / f"{model_id}_{precision}.log"),
                    }
                )

    _write_conversion_report(results)
    if failures:
        raise RuntimeError("; ".join(failures))
    return results


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    export = subparsers.add_parser("export-compnet", help="freeze Gabor kernels and export ONNX")
    export.add_argument("--checkpoint", default=None)
    export.add_argument("--checkpoint-sha256", default=None)
    export.add_argument("--output", default=str(MODEL_DIR / "onnx" / "compnet_static_gabor.onnx"))
    export.add_argument(
        "--marker",
        default=str(MODEL_DIR / "checkpoints" / "compnet_conversion_only.json"),
    )
    export.add_argument("--seed", type=int, default=BENCHMARK_SEED)
    export.add_argument("--opset", type=int, default=13)
    variants = subparsers.add_parser(
        "export-compnet-variants",
        help="export one or all verified CompNet checkpoint variants to distinct ONNX files",
    )
    variants.add_argument(
        "--variant",
        choices=["all", *COMPNET_VARIANT_IDS],
        default="all",
        help="candidate ID to export (default: all five)",
    )
    variants.add_argument(
        "--output-dir",
        default=str(MODEL_DIR / "onnx"),
        help="directory for generated ONNX files",
    )
    variants.add_argument(
        "--marker-dir",
        default=str(MODEL_DIR / "checkpoints"),
        help="directory for ignored per-variant export markers",
    )
    variants.add_argument("--seed", type=int, default=BENCHMARK_SEED)
    variants.add_argument("--opset", type=int, default=13)
    variants.add_argument(
        "--force",
        action="store_true",
        help="replace existing ONNX files; without this flag they are validated and reused",
    )
    convert_variants = subparsers.add_parser(
        "convert-compnet-variants",
        help="BOARD ONLY: run ATC for one or all exported CompNet candidate ONNX files",
    )
    convert_variants.add_argument(
        "--variant",
        choices=["all", *COMPNET_VARIANT_IDS],
        default="all",
    )
    convert_variants.add_argument(
        "--precision", choices=["origin", "mixed_fp16", "both"], default="both"
    )
    convert_variants.add_argument("--force", action="store_true")
    check_variants = subparsers.add_parser(
        "check-compnet-variants",
        help="validate CompNet candidate ONNX and declared OM paths without calling CANN",
    )
    check_variants.add_argument(
        "--variant",
        choices=["all", *COMPNET_VARIANT_IDS],
        default="all",
    )
    convert = subparsers.add_parser("convert", help="run ATC for registered ONNX models")
    convert.add_argument("--model", choices=["ccnet", "compnet", "all"], default="all")
    convert.add_argument(
        "--precision", choices=["origin", "mixed_fp16", "both"], default="both"
    )
    convert.add_argument("--force", action="store_true")
    check = subparsers.add_parser("check", help="validate registered ONNX files")
    check.add_argument("--model", choices=["ccnet", "compnet", "all"], default="all")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_runtime_dirs()
    if args.command == "export-compnet":
        print(json.dumps(export_compnet(args), indent=2))
        return 0
    if args.command == "export-compnet-variants":
        print(json.dumps(export_compnet_variants(args), indent=2))
        return 0
    if args.command == "convert-compnet-variants":
        print(json.dumps(convert_compnet_variants(args), indent=2))
        return 0
    if args.command == "check-compnet-variants":
        output = check_compnet_variants(args)
        print(json.dumps(output, indent=2))
        return int(any(item.get("status") != "ready" for item in output))
    if args.command == "convert":
        print(json.dumps(convert_models(args), indent=2))
        return 0
    registry = ModelRegistry(REGISTRY_PATH)
    model_ids = ["ccnet", "compnet"] if args.model == "all" else [args.model]
    output = []
    for model_id in model_ids:
        spec = registry.get(model_id)
        path = spec.path("reference_onnx")
        if path is None or not path.is_file():
            output.append({"model_id": model_id, "status": "missing", "path": str(path)})
        else:
            output.append({"model_id": model_id, **validate_onnx(path, int(spec.feature_dim or 0))})
    print(json.dumps(output, indent=2))
    return int(any(item.get("status") == "missing" for item in output))


if __name__ == "__main__":
    sys.exit(main())
