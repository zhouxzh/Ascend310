"""Measure live rule-based palm ROI behavior without retaining camera frames."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import time
from typing import Any

import cv2

from palmprint_workbench.domain.preprocessing import PalmPreprocessor
from palmprint_workbench.runtime.camera import CameraCapture


def _range(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    return {
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "mean": round(sum(values) / len(values), 3),
    }


def _integer_range(values: list[int]) -> dict[str, float] | None:
    return _range([float(value) for value in values])


def _mask_summary(mask, frame_area: int) -> dict[str, float | int]:
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return {"coverage": 0.0, "largest_area_ratio": 0.0, "largest_width": 0, "largest_height": 0}
    contour = max(contours, key=cv2.contourArea)
    _x, _y, width, height = cv2.boundingRect(contour)
    return {
        "coverage": float(cv2.countNonZero(mask) / frame_area),
        "largest_area_ratio": float(cv2.contourArea(contour) / frame_area),
        "largest_width": width,
        "largest_height": height,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default="/dev/video0")
    parser.add_argument("--width", type=int, default=1280)
    parser.add_argument("--height", type=int, default=720)
    parser.add_argument("--frames", type=int, default=20)
    parser.add_argument("--interval", type=float, default=0.1)
    args = parser.parse_args()
    if args.frames < 1:
        parser.error("--frames must be positive")

    preprocessor = PalmPreprocessor()
    reasons: Counter[str] = Counter()
    areas: list[float] = []
    sharpness: list[float] = []
    center_x: list[float] = []
    center_y: list[float] = []
    edge_padding: list[float] = []
    contour_width: list[int] = []
    contour_height: list[int] = []
    contour_x: list[int] = []
    contour_y: list[int] = []
    valley_counts: Counter[int] = Counter()
    component_observations: list[list[dict[str, float]]] = []
    mask_candidates: dict[str, list[dict[str, float | int]]] = {
        "otsu": [],
        "skin_ycrcb_standard": [],
        "skin_ycrcb_broad": [],
    }
    captured_shapes: Counter[str] = Counter()
    accepted = 0

    with CameraCapture(args.device, width=args.width, height=args.height) as camera:
        for index in range(args.frames):
            frame = camera.capture_rgb()
            captured_shapes[f"{frame.shape[1]}x{frame.shape[0]}"] += 1
            gray = cv2.cvtColor(frame, cv2.COLOR_RGB2GRAY)
            mask = preprocessor._segment(gray)
            ycrcb = cv2.cvtColor(frame, cv2.COLOR_RGB2YCrCb)
            mask_candidates["otsu"].append(_mask_summary(mask, gray.size))
            mask_candidates["skin_ycrcb_standard"].append(
                _mask_summary(cv2.inRange(ycrcb, (0, 133, 77), (255, 180, 135)), gray.size)
            )
            mask_candidates["skin_ycrcb_broad"].append(
                _mask_summary(cv2.inRange(ycrcb, (0, 120, 70), (255, 190, 150)), gray.size)
            )
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if contours:
                ranked = sorted(contours, key=cv2.contourArea, reverse=True)
                component_observations.append(
                    [
                        {
                            "area_ratio": round(float(cv2.contourArea(item) / gray.size), 4),
                            "x": float(cv2.boundingRect(item)[0]),
                            "y": float(cv2.boundingRect(item)[1]),
                            "width": float(cv2.boundingRect(item)[2]),
                            "height": float(cv2.boundingRect(item)[3]),
                        }
                        for item in ranked[:3]
                    ]
                )
                contour = ranked[0]
                x, y, width, height = cv2.boundingRect(contour)
                contour_x.append(x)
                contour_y.append(y)
                contour_width.append(width)
                contour_height.append(height)
                valley_counts[len(preprocessor._finger_valleys(contour))] += 1
            result = preprocessor.extract(frame)
            if result.ok:
                accepted += 1
            else:
                reasons[result.reason or "unknown"] += 1
            if "hand_area_ratio" in result.quality:
                areas.append(float(result.quality["hand_area_ratio"]))
            if "sharpness" in result.quality:
                sharpness.append(float(result.quality["sharpness"]))
            if "center_x" in result.geometry:
                center_x.append(float(result.geometry["center_x"]))
            if "center_y" in result.geometry:
                center_y.append(float(result.geometry["center_y"]))
            if "edge_padding_px" in result.geometry:
                edge_padding.append(float(result.geometry["edge_padding_px"]))
            if index + 1 < args.frames:
                time.sleep(args.interval)

    payload: dict[str, Any] = {
        "device": args.device,
        "requested_resolution": f"{args.width}x{args.height}",
        "captured_shapes": dict(captured_shapes),
        "frames": args.frames,
        "roi_accepted": accepted,
        "rejections": dict(reasons),
        "hand_area_ratio": _range(areas),
        "sharpness": _range(sharpness),
        "center_x": _range(center_x),
        "center_y": _range(center_y),
        "edge_padding_px": _range(edge_padding),
        "contour_x_px": _integer_range(contour_x),
        "contour_y_px": _integer_range(contour_y),
        "contour_width_px": _integer_range(contour_width),
        "contour_height_px": _integer_range(contour_height),
        "finger_valley_count": {str(key): value for key, value in sorted(valley_counts.items())},
        "top_components_first_frame": component_observations[0] if component_observations else [],
        "mask_candidates": {
            name: {
                field: _range([float(item[field]) for item in values])
                for field in ("coverage", "largest_area_ratio", "largest_width", "largest_height")
            }
            for name, values in mask_candidates.items()
        },
        "images_saved": False,
    }
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
