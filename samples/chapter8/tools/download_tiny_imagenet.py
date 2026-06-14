#!/usr/bin/env python3
"""Download Tiny-ImageNet train/validation images for chapter 8."""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
CHAPTER_DIR = SCRIPT_DIR.parent
if str(CHAPTER_DIR) not in sys.path:
    sys.path.insert(0, str(CHAPTER_DIR))

from chapter8_utils import DATA_DIR, DEFAULT_CALIB_LIST, DEFAULT_VAL_LIST, resolve_chapter_path  # noqa: E402


DATASET_ID = "zh-plus/tiny-imagenet"
DEFAULT_HF_ENDPOINT = "https://hf-mirror.com"
DEFAULT_TRAIN_DIR = DATA_DIR / "tiny_imagenet_train"
DEFAULT_VAL_DIR = DATA_DIR / "tiny_imagenet_val"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
TRAIN_IMAGE_SUFFIX = ".png"
VAL_IMAGE_SUFFIX = ".png"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download Tiny-ImageNet train calibration images or validation images."
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)
    add_common_args(subparsers.add_parser("train", help="Download train split images and write calib_list.txt."))
    add_common_args(subparsers.add_parser("val", help="Download validation split images and write val_list.txt."))
    return parser.parse_args()


def add_common_args(parser: argparse.ArgumentParser) -> None:
    is_train = parser.prog.endswith(" train")
    default_split = "train" if is_train else "valid"
    default_output_dir = DEFAULT_TRAIN_DIR if is_train else DEFAULT_VAL_DIR
    default_list = DEFAULT_CALIB_LIST if is_train else DEFAULT_VAL_LIST
    default_per_class = 2 if is_train else 0
    list_arg = "--calib-list" if is_train else "--val-list"

    parser.add_argument("--dataset", default=DATASET_ID, help=f"Hugging Face dataset id. Default: {DATASET_ID}.")
    parser.add_argument("--split", default=default_split, help=f"Dataset split name. Default: {default_split}.")
    parser.add_argument("--output-dir", default=str(default_output_dir), help="Directory for extracted images.")
    parser.add_argument(list_arg, dest="list_path", default=str(default_list), help="Output list path.")
    parser.add_argument("--force-download", action="store_true", help="Re-create images and list.")
    parser.add_argument("--offline", action="store_true", help="Use the local Hugging Face dataset cache only.")
    parser.add_argument(
        "--per-class",
        type=int,
        default=default_per_class,
        help=f"Images to save per class. 0 = all. Default: {default_per_class}.",
    )
    parser.add_argument(
        "--hf-endpoint",
        default=os.environ.get("HF_ENDPOINT") or os.environ.get("HF_DATASETS_ENDPOINT") or DEFAULT_HF_ENDPOINT,
        help=f"Hugging Face endpoint or mirror. Default: {DEFAULT_HF_ENDPOINT}.",
    )
    parser.add_argument(
        "--no-hf-mirror",
        action="store_true",
        help="Do not set HF_ENDPOINT/HF_DATASETS_ENDPOINT from --hf-endpoint.",
    )


def resolve_output_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return resolve_chapter_path(path)


def configure_hf_endpoint(endpoint: str, *, disabled: bool) -> str:
    if disabled:
        return ""
    endpoint = endpoint.rstrip("/")
    if not endpoint:
        return ""
    os.environ.setdefault("HF_ENDPOINT", endpoint)
    os.environ.setdefault("HF_DATASETS_ENDPOINT", endpoint)
    return endpoint


def configure_offline(enabled: bool) -> None:
    if enabled:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("HF_DATASETS_OFFLINE", "1")


def split_candidates(split: str) -> list[str]:
    candidates = [split]
    if split == "valid":
        candidates.extend(["validation", "val"])
    elif split == "validation":
        candidates.extend(["valid", "val"])
    elif split == "val":
        candidates.extend(["valid", "validation"])

    seen: set[str] = set()
    result: list[str] = []
    for item in candidates:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result


def load_dataset_split(dataset_id: str, split: str):
    from datasets import load_dataset

    last_error: Exception | None = None
    for candidate in split_candidates(split):
        try:
            return load_dataset(dataset_id, split=candidate, download_mode="reuse_dataset_if_exists"), candidate
        except Exception as exc:  # pragma: no cover - depends on network/dataset metadata.
            last_error = exc
            continue
    raise RuntimeError(f"Unable to load split {split!r} from {dataset_id}.") from last_error


def relative_to_root(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def iter_images(image_dir: Path) -> list[Path]:
    if not image_dir.exists():
        return []
    return sorted(
        path for path in image_dir.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )


def clear_images(image_dir: Path) -> None:
    for path in iter_images(image_dir):
        path.unlink()


def label_from_filename(path: Path) -> int:
    try:
        return int(path.stem.split("_", 1)[0])
    except ValueError as exc:
        raise ValueError(f"Cannot parse label from image name: {path.name}") from exc


def write_calibration_list(list_path: Path, image_paths: list[Path]) -> None:
    list_path.parent.mkdir(parents=True, exist_ok=True)
    with list_path.open("w", encoding="utf-8") as f:
        for path in image_paths:
            f.write(f"{relative_to_root(path, DATA_DIR)}\n")


def write_validation_list(list_path: Path, records: list[tuple[Path, int]]) -> None:
    list_path.parent.mkdir(parents=True, exist_ok=True)
    root = list_path.parent
    with list_path.open("w", encoding="utf-8") as f:
        for image_path, label in records:
            f.write(f"{relative_to_root(image_path, root)} {int(label)}\n")


def count_list_entries(list_path: Path) -> int:
    with list_path.open("r", encoding="utf-8") as f:
        return sum(1 for line in f if line.strip() and not line.startswith("#"))


def list_suffix_counts(list_path: Path) -> Counter[str]:
    counts: Counter[str] = Counter()
    with list_path.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            image_path = Path(line.split()[0])
            counts[image_path.suffix.lower() or "<none>"] += 1
    return counts


def ensure_existing_list_suffix(list_path: Path, expected_suffix: str, *, mode: str) -> None:
    suffix_counts = list_suffix_counts(list_path)
    unexpected = {suffix: count for suffix, count in suffix_counts.items() if suffix != expected_suffix}
    if not unexpected:
        return

    image_format = expected_suffix.lstrip(".").upper()
    details = ", ".join(f"{suffix}: {count}" for suffix, count in sorted(unexpected.items()))
    raise RuntimeError(
        f"Existing {mode} list uses non-{image_format} image entries ({details}): {list_path}\n"
        f"Run `python tools/download_tiny_imagenet.py {mode} --force-download` "
        f"from samples/chapter8 to regenerate {mode} images as {image_format}."
    )


def ensure_existing_validation_list_is_png(list_path: Path) -> None:
    ensure_existing_list_suffix(list_path, VAL_IMAGE_SUFFIX, mode="val")


def take_per_class(dataset, per_class: int) -> dict[int, list[int]]:
    class_images: dict[int, list[int]] = {}
    for index, example in enumerate(dataset):
        if "image" not in example or "label" not in example:
            raise KeyError("Expected dataset examples to contain 'image' and 'label'.")
        label = int(example["label"])
        class_images.setdefault(label, [])
        if per_class <= 0 or len(class_images[label]) < per_class:
            class_images[label].append(index)
    return class_images


def save_images_by_class(
    dataset,
    class_images: dict[int, list[int]],
    output_dir: Path,
    *,
    image_format: str,
    suffix: str,
) -> tuple[list[tuple[Path, int]], Counter[int]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[tuple[Path, int]] = []
    counts: Counter[int] = Counter()
    for label in sorted(class_images):
        for index in class_images[label]:
            image = dataset[index]["image"].convert("RGB")
            image_path = output_dir / f"{label}_{index:05d}{suffix}"
            image.save(image_path, format=image_format)
            records.append((image_path, label))
            counts[label] += 1
    if not records:
        raise ValueError("No Tiny-ImageNet images were saved.")
    return records, counts


def prepare_train(args: argparse.Namespace, output_dir: Path, list_path: Path) -> dict[str, Any]:
    if args.force_download:
        clear_images(output_dir)
        if list_path.exists():
            list_path.unlink()

    if list_path.exists() and not args.force_download:
        ensure_existing_list_suffix(list_path, TRAIN_IMAGE_SUFFIX, mode="train")
        image_paths = iter_images(output_dir)
        if not image_paths:
            raise ValueError(f"Calibration list exists but no images were found under {output_dir}")
        return {
            "mode": "train",
            "image_dir": str(output_dir),
            "list_path": str(list_path),
            "available_images": count_list_entries(list_path),
            "reused_existing": True,
        }

    if iter_images(output_dir) and not args.force_download:
        image_paths = sorted(path for path in iter_images(output_dir) if path.suffix.lower() == TRAIN_IMAGE_SUFFIX)
        if not image_paths:
            raise ValueError(
                f"No PNG calibration images found under {output_dir}. "
                "Run `python tools/download_tiny_imagenet.py train --force-download` to regenerate them."
            )
        write_calibration_list(list_path, image_paths)
        return {
            "mode": "train",
            "image_dir": str(output_dir),
            "list_path": str(list_path),
            "available_images": len(image_paths),
            "rebuilt_list_entries": len(image_paths),
            "reused_existing": True,
        }

    dataset, actual_split = load_dataset_split(args.dataset, args.split)
    class_images = take_per_class(dataset, args.per_class)
    records, counts = save_images_by_class(dataset, class_images, output_dir, image_format="PNG", suffix=TRAIN_IMAGE_SUFFIX)
    write_calibration_list(list_path, [path for path, _ in records])
    return {
        "mode": "train",
        "dataset_id": args.dataset,
        "requested_split": args.split,
        "actual_split": actual_split,
        "image_dir": str(output_dir),
        "list_path": str(list_path),
        "saved_images": len(records),
        "available_images": len(records),
        "class_count": len(counts),
        "per_class": args.per_class,
        "image_format": "png",
        "reused_existing": False,
    }


def prepare_val(args: argparse.Namespace, output_dir: Path, list_path: Path) -> dict[str, Any]:
    if args.force_download:
        clear_images(output_dir)
        if list_path.exists():
            list_path.unlink()

    if list_path.exists() and not args.force_download:
        ensure_existing_validation_list_is_png(list_path)
        return {
            "mode": "val",
            "image_dir": str(output_dir),
            "list_path": str(list_path),
            "available_images": count_list_entries(list_path),
            "reused_existing": True,
        }

    if iter_images(output_dir) and not args.force_download:
        records = [(path, label_from_filename(path)) for path in iter_images(output_dir)]
        if not records:
            raise ValueError(f"No validation images found under {output_dir}")
        write_validation_list(list_path, records)
        ensure_existing_validation_list_is_png(list_path)
        return {
            "mode": "val",
            "image_dir": str(output_dir),
            "list_path": str(list_path),
            "available_images": len(records),
            "rebuilt_list_entries": len(records),
            "reused_existing": True,
        }

    dataset, actual_split = load_dataset_split(args.dataset, args.split)
    class_images = take_per_class(dataset, args.per_class)
    records, counts = save_images_by_class(dataset, class_images, output_dir, image_format="PNG", suffix=".png")
    write_validation_list(list_path, records)
    return {
        "mode": "val",
        "dataset_id": args.dataset,
        "requested_split": args.split,
        "actual_split": actual_split,
        "image_dir": str(output_dir),
        "list_path": str(list_path),
        "saved_images": len(records),
        "available_images": len(records),
        "class_count": len(counts),
        "per_class": args.per_class,
        "image_format": "png",
        "reused_existing": False,
    }


def print_summary(metadata: dict[str, Any], hf_endpoint: str, offline: bool) -> None:
    mode = str(metadata["mode"])
    noun = "calibration" if mode == "train" else "validation"
    print(f"{noun} list: {metadata['list_path']}")
    print(f"{noun} images: {metadata['image_dir']}")
    print(f"{noun} images available: {metadata['available_images']}")
    if hf_endpoint:
        print(f"hugging face endpoint: {hf_endpoint}")
    if offline:
        print("offline mode: enabled")
    if metadata.get("reused_existing"):
        print(f"reused existing {noun} data")
    else:
        print(f"downloaded images: {metadata.get('saved_images', 0)}")
        print(f"classes: {metadata.get('class_count', 0)}")


def main() -> int:
    args = parse_args()
    if args.per_class < 0:
        raise ValueError("--per-class must not be negative")

    hf_endpoint = configure_hf_endpoint(args.hf_endpoint, disabled=args.no_hf_mirror)
    configure_offline(args.offline)

    output_dir = resolve_output_path(args.output_dir)
    list_path = resolve_output_path(args.list_path)
    if args.mode == "train":
        metadata = prepare_train(args, output_dir, list_path)
    elif args.mode == "val":
        metadata = prepare_val(args, output_dir, list_path)
    else:  # pragma: no cover - argparse enforces valid choices.
        raise ValueError(f"Unsupported mode: {args.mode}")

    print_summary(metadata, hf_endpoint, args.offline)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
