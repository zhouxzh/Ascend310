#!/usr/bin/env python3
"""Create a checksum-locked runtime manifest from converted stateful OMs."""

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
    parser.add_argument("--precision", default="mixed_float16")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = json.loads(args.export_manifest.read_text(encoding="utf-8"))
    bundle_dir = args.bundle_dir.resolve()
    bundle_dir.mkdir(parents=True, exist_ok=True)
    components = {}
    for name, component in source["components"].items():
        filename = f"{name}_{args.precision}.om"
        model = bundle_dir / filename
        if not model.is_file():
            raise FileNotFoundError(model)
        logical_inputs = component.get("logical_inputs", [])
        logical_outputs = component.get("logical_outputs", [])
        if len(logical_inputs) != len(component["inputs"]):
            raise ValueError(f"Logical input count mismatch for {name}")
        if len(logical_outputs) != len(component["outputs"]):
            raise ValueError(f"Logical output count mismatch for {name}")

        def runtime_specs(items, logical_names):
            return [
                {
                    **item,
                    "onnx_name": item["name"],
                    "name": logical_name,
                }
                for item, logical_name in zip(items, logical_names)
            ]

        components[name] = {
            "file": filename,
            "sha256": sha256(model),
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
                "id",
                "name",
                "architecture",
                "recommended",
                "source_commit",
                "seed",
                "expression_block",
                "synthesis_block",
                "timbre_halo",
                "checkpoints",
            )
        },
        "precision": args.precision,
        "quality_status": "om_converted_unverified",
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
