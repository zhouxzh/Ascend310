"""Assemble a runtime manifest for a fully validated Piano-DDSP OM bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any


MINIMUM_VALIDATION_FRAMES = 10_000


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return data


def validated_model(
    bundle_root: Path,
    metadata_path: Path,
    release: str,
    reports: dict[str, dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    metadata = load_json(metadata_path)
    model_id = str(metadata.get("model_id", ""))
    artifact_name = str(metadata.get("artifact_name", ""))
    if metadata.get("schema") != "ddsp-piano-model/v1" or not model_id or not artifact_name:
        raise ValueError(f"Invalid model metadata: {metadata_path}")
    if metadata.get("model_suite_release") != release:
        raise ValueError(f"Metadata release mismatch for {model_id}")

    om_path = metadata_path.with_name(f"{artifact_name}-fp32-origin.om")
    if not om_path.is_file():
        raise FileNotFoundError(om_path)
    report_path = bundle_root / "validation" / "full-10000" / f"{model_id}.json"
    report = load_json(report_path)
    if report.get("schema") != "piano-ddsp-om-validation/v2":
        raise ValueError(f"Unsupported validation report: {report_path}")
    if report.get("release") != release or report.get("model_id") != model_id:
        raise ValueError(f"Validation identity mismatch for {model_id}")
    om_sha256 = sha256_file(om_path)
    metadata_sha256 = sha256_file(metadata_path)
    qualification = report.get("qualification")
    contract = report.get("contract")
    if (
        report.get("om") != om_path.name
        or report.get("om_sha256") != om_sha256
        or report.get("metadata_sha256") != metadata_sha256
        or int(report.get("frames", 0)) < MINIMUM_VALIDATION_FRAMES
        or report.get("passed") is not True
        or not isinstance(contract, dict)
        or contract.get("validated") is not True
        or not isinstance(qualification, dict)
        or not all(
            qualification.get(name) is True
            for name in ("numerical", "reverb", "determinism", "realtime")
        )
    ):
        raise ValueError(f"Validation did not qualify {model_id}")
    summary = reports.get(model_id)
    if not isinstance(summary, dict) or summary.get("om_sha256") != om_sha256:
        raise ValueError(f"Validation summary mismatch for {model_id}")

    return model_id, {
        "model_id": model_id,
        "display_name": metadata.get("display_name", model_id),
        "precision": "FP32",
        "precision_mode_v2": "origin",
        "export_variant": "gru-unrolled",
        "source_onnx": metadata.get("onnx"),
        "metadata": metadata_path.relative_to(bundle_root).as_posix(),
        "metadata_sha256": metadata_sha256,
        "om": om_path.relative_to(bundle_root).as_posix(),
        "om_sha256": om_sha256,
        "om_bytes": om_path.stat().st_size,
        "validation": {
            "path": report_path.relative_to(bundle_root).as_posix(),
            "sha256": sha256_file(report_path),
            "frames": int(report["frames"]),
            "passed": True,
            "om_sha256": om_sha256,
        },
    }


def assemble_manifest(bundle_root: Path) -> dict[str, Any]:
    bundle_root = bundle_root.resolve()
    release_path = bundle_root / "source-model-suite.json"
    summary_path = bundle_root / "validation" / "full-10000" / "summary.json"
    release_data = load_json(release_path)
    summary = load_json(summary_path)
    release = str(release_data.get("release", ""))
    if release_data.get("schema") != "ddsp-piano-release/v1" or not release:
        raise ValueError(f"Unsupported release source: {release_path}")
    if summary.get("release") != release or summary.get("passed") is not True:
        raise ValueError(f"Validation summary did not pass: {summary_path}")
    target = summary.get("target")
    if not isinstance(target, dict) or target.get("soc_version") != "Ascend310B4":
        raise ValueError(f"Validation target is not Ascend310B4: {summary_path}")

    summary_models = summary.get("models")
    if not isinstance(summary_models, list):
        raise ValueError(f"Validation summary has no models: {summary_path}")
    reports = {
        str(item.get("model_id")): item
        for item in summary_models
        if isinstance(item, dict) and item.get("passed") is True
    }
    metadata_paths = sorted(
        path
        for path in (bundle_root / "models").glob("*.json")
        if not path.name.endswith(".validation.json")
    )
    if not metadata_paths:
        raise ValueError(f"No model metadata found under {bundle_root / 'models'}")
    models = dict(validated_model(bundle_root, path, release, reports) for path in metadata_paths)
    if set(models) != set(reports):
        raise ValueError("Model metadata and passed validation reports do not match")

    return {
        "schema": "piano-ddsp-om-bundle/v1",
        "id": bundle_root.name,
        "release": release,
        "precision": "FP32",
        "precision_mode_v2": "origin",
        "export_variant": "gru-unrolled",
        "soc_version": "Ascend310B4",
        "source_manifest_sha256": sha256_file(release_path),
        "source_commit": summary.get("source_hf_commit"),
        "models": models,
        "complete": True,
    }


def write_json_if_unchanged(path: Path, data: dict[str, Any]) -> None:
    encoded = json.dumps(data, indent=2, sort_keys=True) + "\n"
    if path.is_file() and path.read_text(encoding="utf-8") == encoded:
        return
    if path.exists():
        raise FileExistsError(f"Refusing to replace immutable manifest: {path}")
    temporary = path.with_suffix(f"{path.suffix}.part")
    temporary.write_text(encoded, encoding="utf-8")
    os.replace(temporary, path)


def activate_bundle(bundle_root: Path, manifest: dict[str, Any]) -> Path:
    if bundle_root.parent.name != "bundles":
        raise ValueError("Bundle root must be directly under models/piano_ddsp/bundles")
    active_path = bundle_root.parent.parent / "active-bundle.json"
    pointer = {
        "schema": "piano-ddsp-active-bundle/v1",
        "bundle_id": manifest["id"],
        "manifest": f"bundles/{bundle_root.name}/manifest.json",
    }
    temporary = active_path.with_suffix(".json.part")
    temporary.write_text(json.dumps(pointer, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, active_path)
    return active_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--activate", action="store_true")
    args = parser.parse_args()

    manifest = assemble_manifest(args.bundle_root)
    manifest_path = args.bundle_root / "manifest.json"
    write_json_if_unchanged(manifest_path, manifest)
    print(f"Manifest: {manifest_path}")
    if args.activate:
        print(f"Active pointer: {activate_bundle(args.bundle_root, manifest)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
