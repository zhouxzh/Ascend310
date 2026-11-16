"""Compare final OM palm boxes to a pinned ONNX reference on one real image."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

import cv2
import numpy as np

from palmprint_workbench.config import REPORT_DIR
from palmprint_workbench.services.workbench import WORKBENCH


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _iou(first: list[float], second: list[float]) -> float:
    left = max(first[0], second[0])
    top = max(first[1], second[1])
    right = min(first[2], second[2])
    bottom = min(first[3], second[3])
    intersection = max(0.0, right - left) * max(0.0, bottom - top)
    first_area = max(0.0, first[2] - first[0]) * max(0.0, first[3] - first[1])
    second_area = max(0.0, second[2] - second[0]) * max(0.0, second[3] - second[1])
    return intersection / max(first_area + second_area - intersection, 1e-8)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, type=Path)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    args = parser.parse_args()
    report_path = args.report.resolve()
    report_path.relative_to(REPORT_DIR.resolve())
    reference = json.loads(args.reference.read_text(encoding="utf-8"))
    image_sha = _digest(args.image)
    if image_sha != reference["image_sha256"]:
        raise ValueError("reference image SHA-256 does not match")
    bgr = cv2.imread(str(args.image), cv2.IMREAD_COLOR)
    if bgr is None:
        raise ValueError("unable to decode reference image")
    rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
    if list(rgb.shape) != reference["image_shape"]:
        raise ValueError("reference image dimensions do not match")

    result = {"image_sha256": image_sha, "onnx_model_sha256": reference["model_sha256"],
              "reference_boxes": reference["boxes"], "om_boxes": [], "hands": []}
    started = time.perf_counter()
    try:
        boxes = WORKBENCH.detector.detect(rgb)
        result["om_boxes"] = [{"xyxy": list(box.xyxy), "score": box.score} for box in boxes]
        for index, box in enumerate(boxes, start=1):
            roi = WORKBENCH.preprocessor.extract_box(rgb, box.xyxy)
            hand = {"index": index, "bbox_xyxy": list(box.xyxy), "roi_ok": roi.ok,
                    "quality": roi.quality, "reason": roi.reason}
            if roi.ok and roi.roi is not None:
                adapter = WORKBENCH.adapter("ccnet", "npu", "mixed_fp16")
                code = np.asarray(adapter.encode(roi.roi).code)
                hand["embedding_finite"] = bool(np.all(np.isfinite(code)))
                hand["embedding_dim"] = int(code.size)
            result["hands"].append(hand)
        pairs = zip(reference["boxes"], result["om_boxes"])
        result["box_comparison"] = [
            {"iou": _iou(item[0]["xyxy"], item[1]["xyxy"]),
             "score_abs_delta": abs(item[0]["score"] - item[1]["score"])} for item in pairs
        ]
        result["box_parity"] = (len(reference["boxes"]) == len(boxes) and
                                all(item["iou"] >= 0.9 and item["score_abs_delta"] <= 0.1
                                    for item in result["box_comparison"]))
        result["ok"] = bool(result["box_parity"] and result["hands"] and
                            all(hand["roi_ok"] and hand.get("embedding_finite") and
                                hand.get("embedding_dim") == 2048 for hand in result["hands"]))
    except BaseException as exc:
        result["ok"] = False
        result["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        try:
            result["close"] = WORKBENCH.close()
        except BaseException as exc:
            result["ok"] = False
            result["close_error"] = f"{type(exc).__name__}: {exc}"
        result["total_ms"] = round((time.perf_counter() - started) * 1000, 3)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
