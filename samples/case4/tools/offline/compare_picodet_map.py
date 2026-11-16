#!/usr/bin/env python3
"""Join provenance-compatible PicoDet CUDA ONNX and one-tier OM mAP reports."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

EXPECTED_MODELS = {"picodet-xs-320-seed-42", "picodet-xs-416-seed-42"}
TIER_SOC = {"8t": "Ascend310B4", "20t": "Ascend310B1"}
DATASET_FIELDS = ("images", "annotations", "annotations_sha256", "image_ids_sha256")


def require_sha256(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(character not in "0123456789abcdef" for character in value.lower()):
        raise ValueError(f"{label} must be a SHA-256 hex digest")
    return value.lower()


def load_map(path: Path) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != 1 or value.get("kind") != "picodet_palm_map":
        raise ValueError(f"unexpected map kind in {path}: {value.get('kind')!r}")
    source = value.get("source")
    if not isinstance(source, dict):
        raise ValueError(f"map source provenance is missing: {path}")
    if source.get("summary_kind") not in {"picodet_palm_onnx_fulltest", "picodet_palm_om_fulltest"}:
        raise ValueError(f"map source summary kind is invalid: {path}")
    dataset = source.get("dataset")
    if not isinstance(dataset, dict) or any(field not in dataset for field in DATASET_FIELDS):
        raise ValueError(f"map dataset provenance is incomplete: {path}")
    if not isinstance(dataset["images"], int) or not isinstance(dataset["annotations"], int):
        raise ValueError(f"map dataset count provenance is invalid: {path}")
    for field in ("annotations_sha256", "image_ids_sha256"):
        require_sha256(dataset[field], f"{path} {field}")
    for field in ("summary_sha256", "manifest_sha256", "contracts_sha256"):
        require_sha256(source.get(field), f"{path} {field}")
    models = value.get("models", [])
    if len(models) != 2 or {item.get("name") for item in models} != EXPECTED_MODELS:
        raise ValueError(f"unexpected PicoDet model set in {path}")
    if any(item.get("status") != "completed" for item in models):
        raise ValueError(f"incomplete PicoDet map in {path}")
    for item in models:
        require_sha256(item.get("onnx_sha256"), f"{item.get('name')} ONNX SHA-256")
    if source["summary_kind"] == "picodet_palm_om_fulltest":
        atc = source.get("atc")
        if not isinstance(atc, dict) or not isinstance(atc.get("recipe"), dict):
            raise ValueError(f"OM ATC provenance is missing: {path}")
        require_sha256(atc.get("summary_sha256"), f"{path} ATC summary SHA-256")
        recipe = atc["recipe"]
        if recipe.get("precision_mode") != "allow_mix_precision" or recipe.get("soc_version") not in set(TIER_SOC.values()):
            raise ValueError(f"invalid OM ATC recipe: {path}")
        for item in models:
            require_sha256(item.get("om_sha256"), f"{item.get('name')} OM SHA-256")
    return source, {item["name"]: item for item in models}


def metrics(item: dict[str, Any]) -> dict[str, Any]:
    return item.get("metrics", {}).get("metrics", {})


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--onnx-map", type=Path, required=True)
    parser.add_argument("--om-map", type=Path, required=True)
    parser.add_argument("--tier", choices=("8t", "20t"), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    onnx_source, onnx = load_map(args.onnx_map)
    om_source, om = load_map(args.om_map)
    if onnx_source["summary_kind"] != "picodet_palm_onnx_fulltest":
        raise ValueError("--onnx-map is not an ONNX full-test map")
    if om_source["summary_kind"] != "picodet_palm_om_fulltest":
        raise ValueError("--om-map is not an OM full-test map")
    for field in DATASET_FIELDS:
        if onnx_source["dataset"][field] != om_source["dataset"][field]:
            raise ValueError(f"ONNX/OM dataset provenance mismatch for {field}")
    for field in ("manifest_sha256", "contracts_sha256"):
        if onnx_source[field] != om_source[field]:
            raise ValueError(f"ONNX/OM bundle provenance mismatch for {field}")
    recipe = om_source["atc"]["recipe"]
    expected_soc = TIER_SOC[args.tier]
    if recipe["soc_version"] != expected_soc:
        raise ValueError(f"requested tier {args.tier} requires {expected_soc}, got {recipe['soc_version']}")
    rows = []
    for name in sorted(EXPECTED_MODELS):
        if onnx[name]["onnx_sha256"] != om[name]["onnx_sha256"]:
            raise ValueError(f"ONNX identity mismatch for {name}")
        reference = metrics(onnx[name])
        observed = metrics(om[name])
        if reference.get("mAP50_95") is None or observed.get("mAP50_95") is None:
            raise ValueError(f"mAP50_95 is missing for {name}")
        rows.append({
            "name": name,
            "tier": args.tier,
            "soc_version": recipe["soc_version"],
            "precision_mode": recipe["precision_mode"],
            "onnx_mAP50_95": reference["mAP50_95"],
            "om_mAP50_95": observed["mAP50_95"],
            "delta_mAP50_95": observed["mAP50_95"] - reference["mAP50_95"],
            "onnx_AP50": reference.get("AP50"),
            "om_AP50": observed.get("AP50"),
            "delta_AP50": observed.get("AP50") - reference.get("AP50"),
            "onnx_AP75": reference.get("AP75"),
            "om_AP75": observed.get("AP75"),
            "delta_AP75": observed.get("AP75") - reference.get("AP75"),
            "onnx_sha256": onnx[name]["onnx_sha256"],
            "om_sha256": om[name]["om_sha256"],
            "om_numerical_gate_ok": om[name].get("numerical_gate_ok"),
            "om_smoke_allclose_samples": (
                om[name].get("reference_smoke", {}).get("agreement", {}).get("allclose_samples")
            ),
            "om_smoke_samples": (
                om[name].get("reference_smoke", {}).get("agreement", {}).get("samples")
            ),
        })
    args.output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": 1,
        "kind": "picodet_palm_onnx_vs_om_map",
        "tier": args.tier,
        "dataset": onnx_source["dataset"],
        "onnx_map_sha256": require_sha256(onnx_source["summary_sha256"], "ONNX summary SHA-256"),
        "om_map_sha256": require_sha256(om_source["summary_sha256"], "OM summary SHA-256"),
        "manifest_sha256": onnx_source["manifest_sha256"],
        "contracts_sha256": onnx_source["contracts_sha256"],
        "atc": om_source["atc"],
        "rows": rows,
    }
    (args.output_dir / "comparison.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with (args.output_dir / "comparison.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    def fmt(value: Any) -> str:
        return "--" if value is None else f"{value:.6f}"
    lines = [
        f"# PicoDet Palm ONNX vs {args.tier.upper()} OM",
        "",
        "| Model | ONNX mAP | OM mAP | Delta | ONNX AP50 | OM AP50 | ONNX AP75 | OM AP75 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        lines.append(
            f"| {row['name']} | {fmt(row['onnx_mAP50_95'])} | {fmt(row['om_mAP50_95'])} | {fmt(row['delta_mAP50_95'])} | "
            f"{fmt(row['onnx_AP50'])} | {fmt(row['om_AP50'])} | {fmt(row['onnx_AP75'])} | {fmt(row['om_AP75'])} |"
        )
    (args.output_dir / "comparison.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"models": len(rows), "failures": []}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
