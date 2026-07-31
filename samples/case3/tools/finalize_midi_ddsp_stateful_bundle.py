#!/usr/bin/env python3
"""Create an origin-only runtime manifest from converted stateful OMs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export-manifest", type=Path, required=True)
    parser.add_argument("--bundle-dir", type=Path, required=True)
    parser.add_argument("--conversion-log-dir", type=Path)
    parser.add_argument("--recommended", action="store_true")
    parser.add_argument(
        "--voice-batch-sizes",
        help="Comma-separated static batches to publish; defaults to every exported batch",
    )
    parser.add_argument("--quality-status", default="om_converted_unverified")
    return parser.parse_args()


def runtime_specs(
    items: list[dict[str, object]], logical_names: list[str]
) -> list[dict[str, object]]:
    return [
        {
            **item,
            "onnx_name": item["name"],
            "name": logical_name,
        }
        for item, logical_name in zip(items, logical_names)
    ]


def main() -> int:
    args = parse_args()
    source = json.loads(args.export_manifest.read_text(encoding="utf-8"))
    exported_batch_sizes = {
        int(component.get("voice_batch_size", 1))
        for component in source["components"].values()
    }
    selected_batch_sizes = (
        {
            int(value.strip())
            for value in args.voice_batch_sizes.split(",")
            if value.strip()
        }
        if args.voice_batch_sizes
        else exported_batch_sizes
    )
    if not selected_batch_sizes or 1 not in selected_batch_sizes:
        raise ValueError("Published voice batches must include batch 1")
    unknown_batch_sizes = selected_batch_sizes - exported_batch_sizes
    if unknown_batch_sizes:
        raise ValueError(
            f"Voice batches were not exported: {sorted(unknown_batch_sizes)}"
        )

    bundle_dir = args.bundle_dir.resolve()
    bundle_dir.mkdir(parents=True, exist_ok=True)
    conversion_log_dir = (args.conversion_log_dir or bundle_dir / "logs").resolve()
    published_log_dir = bundle_dir / "logs"
    published_log_dir.mkdir(parents=True, exist_ok=True)
    components: dict[str, dict[str, object]] = {}
    for name, component in source["components"].items():
        batch_size = int(component.get("voice_batch_size", 1))
        if batch_size not in selected_batch_sizes:
            continue
        filename = f"{name}_origin.om"
        model = bundle_dir / filename
        if not model.is_file():
            raise FileNotFoundError(model)
        logical_inputs = component.get("logical_inputs", [])
        logical_outputs = component.get("logical_outputs", [])
        if len(logical_inputs) != len(component["inputs"]):
            raise ValueError(f"Logical input count mismatch for {name}")
        if len(logical_outputs) != len(component["outputs"]):
            raise ValueError(f"Logical output count mismatch for {name}")
        provenance_source = conversion_log_dir / f"{name}_origin.provenance.json"
        if not provenance_source.is_file():
            raise FileNotFoundError(provenance_source)
        provenance = json.loads(provenance_source.read_text(encoding="utf-8"))
        expected_onnx_hash = str(component["sha256"])
        expected_input_shape = ";".join(
            f"{item['name']}:{','.join(str(value) for value in item['shape'])}"
            for item in component["inputs"]
        )
        if (
            provenance.get("schema") != "midi-ddsp-atc-conversion/v1"
            or provenance.get("source_onnx") != component["file"]
            or provenance.get("source_onnx_sha256") != expected_onnx_hash
            or provenance.get("om") != filename
            or provenance.get("om_sha256") != sha256(model)
            or provenance.get("soc_version") != "Ascend310B4"
            or provenance.get("input_shape") != expected_input_shape
            or provenance.get("precision_mode_v2") != "origin"
        ):
            raise ValueError(f"Conversion provenance mismatch for {name}")
        artifact_names = (
            ("atc_log", "atc_log_sha256"),
            ("atc_summary", "atc_summary_sha256"),
        )
        published_artifacts: dict[str, dict[str, str]] = {}
        for path_key, hash_key in artifact_names:
            artifact_name = provenance.get(path_key)
            if not isinstance(artifact_name, str) or Path(artifact_name).name != artifact_name:
                raise ValueError(f"Invalid {path_key} in conversion provenance for {name}")
            source_path = conversion_log_dir / artifact_name
            if not source_path.is_file() or sha256(source_path) != provenance.get(hash_key):
                raise ValueError(f"Changed {path_key} for {name}")
            if path_key == "atc_summary":
                summary_lines = source_path.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
                required = {"ATC_EXIT_CODE=0", "OM_UPDATED=yes", "ERROR_LINES=none"}
                if not required.issubset(summary_lines):
                    raise ValueError(f"Unsuccessful ATC summary for {name}")
            target_path = published_log_dir / artifact_name
            if source_path.resolve() != target_path.resolve():
                shutil.copy2(source_path, target_path)
            published_artifacts[path_key] = {
                "path": target_path.relative_to(bundle_dir).as_posix(),
                "sha256": sha256(target_path),
            }
        provenance_target = published_log_dir / provenance_source.name
        if provenance_source.resolve() != provenance_target.resolve():
            shutil.copy2(provenance_source, provenance_target)
        components[name] = {
            "file": filename,
            "sha256": sha256(model),
            "logical_name": str(component.get("logical_name", name)),
            "voice_batch_size": batch_size,
            "inputs": runtime_specs(component["inputs"], logical_inputs),
            "outputs": runtime_specs(component["outputs"], logical_outputs),
            "onnx_sha256": component["sha256"],
            "onnx_metrics": component["metrics"],
            "conversion": {
                "path": provenance_target.relative_to(bundle_dir).as_posix(),
                "sha256": sha256(provenance_target),
                "source_onnx_sha256": expected_onnx_hash,
                "om_sha256": sha256(model),
                "soc_version": provenance["soc_version"],
                "input_shape": expected_input_shape,
                "precision_mode_v2": "origin",
                **published_artifacts,
            },
        }

    manifest = {
        **{
            key: source[key]
            for key in (
                "schema_version",
                "architecture",
                "source_commit",
                "seed",
                "expression_block",
                "synthesis_block",
                "timbre_max_frames",
                "checkpoints",
            )
        },
        "id": "google-urmp-stateful-v2-batched-origin",
        "name": "Google URMP stateful v2 batched (origin)",
        "recommended": bool(args.recommended),
        "onnx_dtype": "float32",
        "precision": "origin",
        "quality_status": args.quality_status,
        "voice_batch_sizes": sorted(selected_batch_sizes),
        "components": components,
    }
    target = bundle_dir / "manifest.json"
    target.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
