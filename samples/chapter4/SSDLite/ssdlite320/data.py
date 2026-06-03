from __future__ import annotations

from io import BytesIO
from glob import glob
import inspect
import os
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np
from PIL import Image

from .config import CASE_DIR, DEFAULT_DATASET_NAME, DEFAULT_IMAGE_SIZE, DEFAULT_VAL_DATA_FILES

IMAGENET_MEAN = np.asarray([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
IMAGENET_STD = np.asarray([0.229, 0.224, 0.225], dtype=np.float32).reshape(3, 1, 1)


def _resolve_local_data_files(data_files: str) -> list[str]:
    data_path = Path(data_files).expanduser()
    patterns = [str(data_path)]
    if not data_path.is_absolute():
        patterns.extend(
            [
                str(CASE_DIR / data_files),
                str(CASE_DIR / "data" / data_files),
            ]
        )

    matches = []
    for pattern in patterns:
        matches.extend(sorted(glob(pattern)))
    return sorted({str(Path(path).expanduser().resolve()) for path in matches})


def _download_data_files(dataset_name: str, data_files: str, cache_dir: str) -> list[str]:
    from huggingface_hub import snapshot_download

    local_dir = CASE_DIR / "data"
    local_dir.mkdir(parents=True, exist_ok=True)
    print(f"Local validation parquet files not found. Downloading {dataset_name}:{data_files} to {local_dir}")
    download_kwargs = {
        "repo_id": dataset_name,
        "repo_type": "dataset",
        "allow_patterns": data_files,
        "local_dir": str(local_dir),
        "cache_dir": cache_dir,
    }
    snapshot_params = inspect.signature(snapshot_download).parameters
    if os.environ.get("HF_ENDPOINT") and "endpoint" in snapshot_params:
        download_kwargs["endpoint"] = os.environ["HF_ENDPOINT"]
    if "local_dir_use_symlinks" in snapshot_params:
        download_kwargs["local_dir_use_symlinks"] = False

    snapshot_download(**download_kwargs)
    return _resolve_local_data_files(data_files)


def load_coco_val(
    dataset_name: str = DEFAULT_DATASET_NAME,
    cache_dir: str = "./data/hf_cache",
    streaming: bool = False,
    data_files: str | None = DEFAULT_VAL_DATA_FILES,
):
    from datasets import load_dataset

    data_files = data_files or None
    mode = "streaming" if streaming else "cached"
    if not streaming:
        if not data_files:
            raise ValueError(
                "Cached mode requires --data-files so the validation parquet shards can be downloaded. "
                "Use --streaming if you want to read the Hugging Face dataset online without local parquet files."
            )

        local_files = _resolve_local_data_files(data_files)
        if not local_files:
            local_files = _download_data_files(dataset_name, data_files, cache_dir)
        if not local_files:
            raise FileNotFoundError(f"No validation parquet files matched --data-files {data_files!r} after download.")

        print(f"Loading local validation dataset: parquet ({mode}, files={len(local_files)})")
        return load_dataset(
            "parquet",
            data_files={"val": local_files},
            split="val",
            cache_dir=cache_dir,
        )

    if data_files:
        print(f"Loading validation dataset: {dataset_name} ({mode}, data_files={data_files})")
        return load_dataset(
            dataset_name,
            data_files={"val": data_files},
            split="val",
            cache_dir=cache_dir,
            streaming=streaming,
        )

    print(f"Loading validation dataset: {dataset_name} ({mode})")
    return load_dataset(dataset_name, split="val", cache_dir=cache_dir, streaming=streaming)


def decode_image(image: Any) -> Image.Image:
    if hasattr(image, "mode") and hasattr(image, "size"):
        return image
    if isinstance(image, dict):
        image_bytes = image.get("bytes")
        if image_bytes is not None:
            return Image.open(BytesIO(image_bytes)).copy()
        image_path = image.get("path")
        if image_path:
            return Image.open(image_path).copy()
    if isinstance(image, (bytes, bytearray)):
        return Image.open(BytesIO(image)).copy()
    raise TypeError(f"Unsupported image value type: {type(image).__name__}")


def normalize_item(item: Any) -> Any:
    if isinstance(item, dict) and "image" in item:
        item = dict(item)
        item["image"] = decode_image(item["image"])
    return item


def iter_dataset(dataset: Iterable[Any], max_samples: int = 0) -> Iterator[Any]:
    for index, item in enumerate(dataset):
        if max_samples > 0 and index >= max_samples:
            break
        yield normalize_item(item)


def preprocess_image(image, img_size: int = DEFAULT_IMAGE_SIZE) -> tuple[np.ndarray, np.ndarray]:
    if image.mode != "RGB":
        image = image.convert("RGB")

    resized_image = image.resize((img_size, img_size))
    image_chw = np.asarray(resized_image, dtype=np.float32).transpose(2, 0, 1) / 255.0
    image_chw = (image_chw - IMAGENET_MEAN) / IMAGENET_STD
    image_batch = np.expand_dims(image_chw, axis=0).astype(np.float32, copy=False)
    return np.ascontiguousarray(image_batch), image_chw.astype(np.float32, copy=False)


def extract_category_names(dataset: Any) -> list[str] | None:
    try:
        objects_feature = dataset.features["objects"]
        category_feature = getattr(objects_feature, "feature", objects_feature)["category"]
        names = getattr(category_feature, "feature", category_feature).names
        return ["BACKGROUND", *names] if isinstance(names, list) else None
    except Exception:
        return None


def extract_ground_truth(item: dict[str, Any], img_size: int = DEFAULT_IMAGE_SIZE) -> tuple[np.ndarray, np.ndarray]:
    objects = item["objects"]
    if len(objects["bbox"]) == 0:
        return np.empty((0, 4), dtype=np.float32), np.empty((0,), dtype=np.int64)

    image_width, image_height = item["image"].size
    boxes = np.asarray(objects["bbox"], dtype=np.float32)
    labels = np.asarray(objects["category"], dtype=np.int64) + 1
    boxes[:, [0, 2]] *= img_size / image_width
    boxes[:, [1, 3]] *= img_size / image_height
    return boxes, labels
