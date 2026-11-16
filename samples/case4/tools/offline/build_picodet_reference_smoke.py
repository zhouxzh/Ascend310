#!/usr/bin/env python3
"""Build a compact CUDA ONNX reference pack for PicoDet board numerical smoke tests."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from typing import Any

import numpy as np


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_array(value: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(value).tobytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--annotations", type=Path, required=True)
    parser.add_argument("--annotations-sha256", required=True)
    parser.add_argument("--images-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--count", type=int, default=16)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.count <= 0:
        raise ValueError("--count must be positive")
    import cv2
    import onnxruntime as ort

    tools_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(tools_root / "offline"))
    from handed_nanodet_fulltest import load_test_split
    from handed_picodet_fulltest import preprocess_bgr, validate_contract

    bundle = args.bundle_root.resolve()
    output_dir = args.output_dir.resolve()
    if output_dir.exists():
        raise FileExistsError(f"reference output already exists: {output_dir}")
    manifest_path = bundle / "manifest.json"
    contracts_path = bundle / "contracts.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    contracts = json.loads(contracts_path.read_text(encoding="utf-8"))
    if manifest.get("kind") != "picodet_palm_onnx_bundle" or contracts.get("kind") != "picodet_palm_onnx_contracts":
        raise ValueError("unsupported PicoDet bundle")
    models = manifest.get("models", [])
    if len(models) != 2:
        raise ValueError("expected two trained PicoDet models")
    contract_by_name = {item["name"]: item for item in contracts.get("models", [])}
    annotations_sha256 = sha256_file(args.annotations)
    if annotations_sha256 != args.annotations_sha256:
        raise ValueError("annotations SHA-256 mismatch")
    annotations, records = load_test_split(args.annotations, args.images_root, verify_image_hashes=True)
    if len(records) != 1600 or len(annotations["annotations"]) != 1601:
        raise ValueError("reference pack requires the fixed 1600/1601 test split")
    if args.count > len(records):
        raise ValueError("reference sample count exceeds test split")
    selected_indices = np.linspace(0, len(records) - 1, num=args.count, dtype=np.int64)
    if len(set(int(value) for value in selected_indices)) != args.count:
        raise ValueError("reference selection contains duplicate positions")
    selected = [records[int(index)] for index in selected_indices]
    available = ort.get_available_providers()
    if "CUDAExecutionProvider" not in available:
        raise RuntimeError(f"CUDAExecutionProvider is unavailable: {available}")
    output_dir.mkdir(parents=True, exist_ok=False)
    entries: list[dict[str, Any]] = []
    for item in models:
        name = item["name"]
        declared = contract_by_name.get(name)
        if declared is None or declared.get("sha256") != item.get("sha256"):
            raise ValueError(f"manifest/contract ONNX mismatch for {name}")
        contract = validate_contract(declared)
        model_path = (bundle / item["relative_path"]).resolve()
        if not model_path.is_file() or model_path.stat().st_size != int(item["bytes"]) or sha256_file(model_path) != item["sha256"]:
            raise ValueError(f"ONNX integrity mismatch for {name}")
        session = ort.InferenceSession(str(model_path), providers=["CUDAExecutionProvider"])
        active = session.get_providers()
        if not active or active[0] != "CUDAExecutionProvider":
            raise RuntimeError(f"CUDA is not primary for {name}: {active}")
        images: list[int] = []
        image_hashes: list[str] = []
        input_hashes: list[str] = []
        inputs: list[np.ndarray] = []
        outputs: list[list[np.ndarray]] = [[] for _ in contract.output_names]
        for record in selected:
            image = cv2.imread(str(args.images_root / record.file_name), cv2.IMREAD_COLOR)
            if image is None or image.shape[:2] != (record.height, record.width):
                raise ValueError(f"cannot decode reference image {record.file_name}")
            tensor = preprocess_bgr(image, contract)
            inputs.append(tensor[0])
            raw_outputs = tuple(np.asarray(value, dtype=np.float32) for value in session.run(list(contract.output_names), {contract.input_name: tensor}))
            if len(raw_outputs) != len(contract.output_shapes):
                raise ValueError(f"output count mismatch for {name}")
            for index, (value, shape) in enumerate(zip(raw_outputs, contract.output_shapes)):
                if tuple(value.shape) != shape or not np.isfinite(value).all():
                    raise ValueError(f"output contract mismatch for {name} index {index}: {value.shape}")
                outputs[index].append(value[0])
            images.append(record.image_id)
            image_hashes.append(record.image_sha256)
            input_hashes.append(sha256_array(tensor))
        pack_path = output_dir / f"{name}.npz"
        temporary = output_dir / f".{name}.tmp.npz"
        payload: dict[str, Any] = {
            "image_ids": np.asarray(images, dtype=np.int64),
            "image_sha256": np.asarray(image_hashes, dtype="U64"),
            "input_sha256": np.asarray(input_hashes, dtype="U64"),
            "inputs": np.stack(inputs, axis=0),
        }
        for index, values in enumerate(outputs):
            payload[f"output_{index}"] = np.stack(values, axis=0)
        np.savez_compressed(temporary, **payload)
        os.replace(temporary, pack_path)
        entries.append({
            "name": name,
            "path": pack_path.name,
            "sha256": sha256_file(pack_path),
            "bytes": pack_path.stat().st_size,
            "onnx_sha256": item["sha256"],
            "input_shape": list(contract.input_shape),
            "output_shapes": [list(shape) for shape in contract.output_shapes],
            "images": images,
            "input_hashes_sha256": hashlib.sha256("".join(input_hashes).encode()).hexdigest(),
            "active_providers": active,
        })
        del session
    report = {
        "schema_version": 1,
        "kind": "picodet_palm_cuda_reference_smoke",
        "created_at": now(),
        "source": {
            "manifest_sha256": sha256_file(manifest_path),
            "contracts_sha256": sha256_file(contracts_path),
            "annotations_sha256": annotations_sha256,
            "images": len(records),
            "annotations": len(annotations["annotations"]),
            "onnxruntime": ort.__version__,
            "available_providers": available,
        },
        "selection": {"count": len(selected), "positions": [int(value) for value in selected_indices]},
        "models": entries,
    }
    write_json(output_dir / "manifest.json", report)
    print(json.dumps({"models": len(entries), "samples": len(selected), "manifest": str(output_dir / "manifest.json")}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
