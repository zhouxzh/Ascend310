from __future__ import annotations

import json
import math
import os
import platform
import socket
import statistics
import sys
import time
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = REPO_ROOT / "samples/chapter7/model/resnet18_tiny_imagenet.om"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "samples/chapter7/outputs"

IMAGENET_MEAN = np.asarray([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
IMAGENET_INV_STD = (1.0 / np.asarray([0.229, 0.224, 0.225], dtype=np.float32)).reshape(3, 1, 1)
PIXEL_SCALE = np.float32(1.0 / 255.0)


class StageRecorder:
    def __init__(self) -> None:
        self.samples: dict[str, list[float]] = defaultdict(list)

    @contextmanager
    def time(self, stage: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.samples[stage].append((time.perf_counter() - start) * 1000.0)

    def add(self, stage: str, ms: float) -> None:
        self.samples[stage].append(float(ms))

    def extend(self, values: dict[str, float]) -> None:
        for key, value in values.items():
            self.add(key, value)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def host_info() -> dict[str, str]:
    return {
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": sys.version.split()[0],
    }


def command_line() -> str:
    return " ".join(sys.argv)


def percentile(values: Iterable[float], pct: float) -> float:
    data = sorted(float(value) for value in values)
    if not data:
        return 0.0
    if len(data) == 1:
        return data[0]

    rank = (len(data) - 1) * pct / 100.0
    low = int(math.floor(rank))
    high = int(math.ceil(rank))
    if low == high:
        return data[low]
    weight = rank - low
    return data[low] * (1.0 - weight) + data[high] * weight


def summarize_ms(values: Iterable[float]) -> dict[str, float | int]:
    data = [float(value) for value in values]
    if not data:
        return {
            "count": 0,
            "mean_ms": 0.0,
            "p50_ms": 0.0,
            "p95_ms": 0.0,
            "min_ms": 0.0,
            "max_ms": 0.0,
            "fps": 0.0,
        }
    mean_ms = statistics.fmean(data)
    return {
        "count": len(data),
        "mean_ms": round(mean_ms, 4),
        "p50_ms": round(percentile(data, 50), 4),
        "p95_ms": round(percentile(data, 95), 4),
        "min_ms": round(min(data), 4),
        "max_ms": round(max(data), 4),
        "fps": round(1000.0 / mean_ms, 4) if mean_ms > 0 else 0.0,
    }


def summarize_stages(samples: dict[str, list[float]]) -> dict[str, dict[str, float | int]]:
    return {stage: summarize_ms(values) for stage, values in sorted(samples.items())}


def make_summary_row(
    case: str,
    variant: str,
    total_values: Iterable[float],
    *,
    speedup: float | None = None,
    note: str = "",
) -> dict[str, Any]:
    summary = summarize_ms(total_values)
    row = {
        "case": case,
        "variant": variant,
        "mean_ms": summary["mean_ms"],
        "p95_ms": summary["p95_ms"],
        "fps": summary["fps"],
        "runs": summary["count"],
        "speedup": "" if speedup is None else round(float(speedup), 3),
        "note": note,
    }
    return row


def write_report(path: str | os.PathLike[str], report: dict[str, Any]) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    report.setdefault("generated_at", now_iso())
    report.setdefault("host", host_info())
    report.setdefault("command", command_line())
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return output_path


def print_stage_table(metrics: dict[str, dict[str, float | int]]) -> None:
    print(f"{'stage':<18} {'count':>7} {'mean_ms':>10} {'p50_ms':>10} {'p95_ms':>10} {'fps':>10}")
    print("-" * 72)
    for stage, item in metrics.items():
        print(
            f"{stage:<18} {item['count']:>7} {item['mean_ms']:>10.4f} "
            f"{item['p50_ms']:>10.4f} {item['p95_ms']:>10.4f} {item['fps']:>10.2f}"
        )


def deterministic_rgb_frame(index: int, height: int = 64, width: int = 64) -> np.ndarray:
    y = np.arange(height, dtype=np.uint16)[:, None]
    x = np.arange(width, dtype=np.uint16)[None, :]
    frame = np.empty((height, width, 3), dtype=np.uint8)
    frame[..., 0] = ((x + index * 3) % 256).astype(np.uint8)
    frame[..., 1] = ((y * 2 + index * 5) % 256).astype(np.uint8)
    frame[..., 2] = (((x // 2) + (y // 3) + index * 7) % 256).astype(np.uint8)
    return frame


def resize_nearest_rgb(frame: np.ndarray, out_h: int, out_w: int) -> np.ndarray:
    in_h, in_w = frame.shape[:2]
    y_idx = np.linspace(0, in_h - 1, out_h).astype(np.int32)
    x_idx = np.linspace(0, in_w - 1, out_w).astype(np.int32)
    return frame[y_idx[:, None], x_idx[None, :], :]


def preprocess_resnet_rgb(frame: np.ndarray, out_h: int = 64, out_w: int = 64) -> np.ndarray:
    if frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError(f"Expected HWC RGB frame, got shape={frame.shape!r}")
    resized = resize_nearest_rgb(frame, out_h, out_w)
    image_chw = resized.transpose(2, 0, 1).astype(np.float32)
    image_chw *= PIXEL_SCALE
    np.subtract(image_chw, IMAGENET_MEAN, out=image_chw)
    np.multiply(image_chw, IMAGENET_INV_STD, out=image_chw)
    return np.expand_dims(np.ascontiguousarray(image_chw), axis=0)


class ResNetPreprocessWorkspace:
    def __init__(self, in_h: int, in_w: int, out_h: int = 64, out_w: int = 64) -> None:
        self.y_idx = np.linspace(0, in_h - 1, out_h).astype(np.int32)
        self.x_idx = np.linspace(0, in_w - 1, out_w).astype(np.int32)
        self.resized = np.empty((out_h, out_w, 3), dtype=np.uint8)
        self.batch = np.empty((1, 3, out_h, out_w), dtype=np.float32)

    def __call__(self, frame: np.ndarray) -> np.ndarray:
        self.resized[...] = frame[self.y_idx[:, None], self.x_idx[None, :], :]
        chw = self.batch[0]
        np.multiply(self.resized.transpose(2, 0, 1), PIXEL_SCALE, out=chw, casting="unsafe")
        np.subtract(chw, IMAGENET_MEAN, out=chw)
        np.multiply(chw, IMAGENET_INV_STD, out=chw)
        return self.batch


def deterministic_nv12_frame(index: int, width: int, height: int) -> np.ndarray:
    y_axis = np.arange(height, dtype=np.uint16)[:, None]
    x_axis = np.arange(width, dtype=np.uint16)[None, :]
    y_plane = ((x_axis + y_axis * 2 + index * 5) % 256).astype(np.uint8)
    bar_x = int((math.sin(index / 11.0) + 1.0) * 0.5 * max(width - 32, 1))
    y_plane[:, bar_x : min(bar_x + 32, width)] = 240
    uv_plane = np.full((height // 2, width), 128, dtype=np.uint8)
    return np.vstack([y_plane, uv_plane])


def parse_resolution(text: str) -> tuple[int, int]:
    width_text, height_text = text.lower().split("x", 1)
    return int(width_text), int(height_text)
