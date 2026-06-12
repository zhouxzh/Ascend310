from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import Iterable

import numpy as np


CHAPTER_DIR = Path(__file__).resolve().parent
SAMPLES_DIR = CHAPTER_DIR.parent
REPO_ROOT = CHAPTER_DIR.parents[1]
CHAPTER7_DIR = SAMPLES_DIR / "chapter7"

if str(CHAPTER7_DIR) not in sys.path:
    sys.path.insert(0, str(CHAPTER7_DIR))

from perf_utils import (  # noqa: E402
    StageRecorder,
    deterministic_rgb_frame,
    make_summary_row,
    preprocess_resnet_rgb,
    print_stage_table,
    summarize_stages,
    write_report,
)


MODEL_DIR = CHAPTER_DIR / "model"
CALIBRATION_DIR = CHAPTER_DIR / "calibration"
OUTPUT_DIR = CHAPTER_DIR / "outputs"

DEFAULT_BASE_MODEL = MODEL_DIR / "resnet18_tiny_imagenet.om"
DEFAULT_FP16_MODEL = MODEL_DIR / "resnet18_tiny_imagenet_fp16.om"
DEFAULT_INT8_MODEL = MODEL_DIR / "resnet18_tiny_imagenet_int8.om"
DEFAULT_CALIB_LIST = CALIBRATION_DIR / "calib_list.txt"
DEFAULT_OUTPUT_COMPARE = OUTPUT_DIR / "output_compare.json"
DEFAULT_PERF_COMPARE = OUTPUT_DIR / "perf_compare.json"


def resolve_chapter_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    return CHAPTER_DIR / path


def sha256_file(path: str | Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def model_record(path: str | Path) -> dict[str, object]:
    model_path = Path(path)
    record: dict[str, object] = {
        "path": str(model_path),
        "exists": model_path.exists(),
    }
    if model_path.exists():
        record["size_bytes"] = model_path.stat().st_size
        record["sha256"] = sha256_file(model_path)
    return record


def require_models(paths: Iterable[str | Path]) -> None:
    missing = [str(Path(path)) for path in paths if not Path(path).exists()]
    if not missing:
        return

    message = [
        "Missing OM model file(s):",
        *[f"  - {path}" for path in missing],
        "",
        "Run from samples/chapter8:",
        "  python3 tools/download_model.py",
        "  bash tools/convert_fp16_resnet18.sh",
    ]
    raise FileNotFoundError("\n".join(message))


def write_text_lines(path: str | Path, lines: Iterable[str]) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        for line in lines:
            f.write(line.rstrip("\n") + "\n")
    return output_path


def read_calibration_list(
    list_path: str | Path,
    *,
    root: str | Path | None = None,
    limit: int | None = None,
) -> list[Path]:
    list_file = Path(list_path)
    if not list_file.exists():
        raise FileNotFoundError(
            f"Calibration list not found: {list_file}. "
            "Run `python3 01_collect_calibration_list.py --count 50` first."
        )

    base_dir = Path(root) if root is not None else list_file.parent
    items: list[Path] = []
    with list_file.open("r", encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            item = Path(line).expanduser()
            if not item.is_absolute():
                item = base_dir / item
            items.append(item)
            if limit is not None and len(items) >= limit:
                break

    if not items:
        raise ValueError(f"Calibration list is empty: {list_file}")
    return items


def load_rgb_frame(path: str | Path) -> np.ndarray:
    frame_path = Path(path)
    suffix = frame_path.suffix.lower()
    if suffix == ".npy":
        frame = np.load(frame_path)
    elif suffix in {".jpg", ".jpeg", ".png", ".bmp"}:
        try:
            from PIL import Image
        except ImportError as exc:
            raise RuntimeError("Install pillow to load image calibration files.") from exc
        frame = np.asarray(Image.open(frame_path).convert("RGB"), dtype=np.uint8)
    else:
        raise ValueError(f"Unsupported calibration file type: {frame_path}")

    if frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError(f"Expected HWC RGB frame, got {frame_path} with shape={frame.shape!r}")
    if frame.dtype != np.uint8:
        frame = np.clip(frame, 0, 255).astype(np.uint8)
    return np.ascontiguousarray(frame)


def topk_indices(output: np.ndarray, k: int = 5) -> list[int]:
    logits = np.asarray(output).reshape(-1)
    k = min(int(k), logits.size)
    if k <= 0:
        return []
    return [int(index) for index in np.argsort(logits)[-k:][::-1]]


def compare_logits(base_output: np.ndarray, candidate_output: np.ndarray, *, topk: int = 5) -> dict[str, object]:
    base = np.asarray(base_output, dtype=np.float32).reshape(-1)
    candidate = np.asarray(candidate_output, dtype=np.float32).reshape(-1)
    if base.shape != candidate.shape:
        raise ValueError(f"Output shape mismatch: base={base.shape}, candidate={candidate.shape}")

    diff = np.abs(base - candidate)
    base_top = topk_indices(base, topk)
    candidate_top = topk_indices(candidate, topk)
    base_top1 = base_top[0] if base_top else -1
    candidate_top1 = candidate_top[0] if candidate_top else -1
    topk_overlap = len(set(base_top) & set(candidate_top))

    return {
        "base_top1": base_top1,
        "candidate_top1": candidate_top1,
        "top1_match": base_top1 == candidate_top1,
        "base_topk": base_top,
        "candidate_topk": candidate_top,
        "topk_overlap": topk_overlap,
        "max_abs_diff": float(np.max(diff)) if diff.size else 0.0,
        "mean_abs_diff": float(np.mean(diff)) if diff.size else 0.0,
    }


def summarize_output_diffs(items: list[dict[str, object]]) -> dict[str, object]:
    if not items:
        return {
            "count": 0,
            "top1_match_rate": 0.0,
            "mean_topk_overlap": 0.0,
            "max_abs_diff": 0.0,
            "mean_abs_diff": 0.0,
        }

    return {
        "count": len(items),
        "top1_match_rate": round(sum(bool(item["top1_match"]) for item in items) / len(items), 6),
        "mean_topk_overlap": round(
            sum(float(item["topk_overlap"]) for item in items) / len(items),
            6,
        ),
        "max_abs_diff": max(float(item["max_abs_diff"]) for item in items),
        "mean_abs_diff": round(
            sum(float(item["mean_abs_diff"]) for item in items) / len(items),
            8,
        ),
    }
