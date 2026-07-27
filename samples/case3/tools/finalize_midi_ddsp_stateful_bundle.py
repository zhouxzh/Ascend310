#!/usr/bin/env python3
"""Create an origin-only runtime manifest from converted stateful OMs."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


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
        components[name] = {
            "file": filename,
            "sha256": sha256(model),
            "logical_name": str(component.get("logical_name", name)),
            "voice_batch_size": batch_size,
            "inputs": runtime_specs(component["inputs"], logical_inputs),
            "outputs": runtime_specs(component["outputs"], logical_outputs),
            "onnx_sha256": component["sha256"],
            "onnx_metrics": component["metrics"],
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
