#!/usr/bin/env python3
"""Capture a few board-camera frames and save non-biometric diagnostic metadata."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time


from palmprint_workbench.runtime.camera import CameraCapture, CameraError, list_v4l2_devices
from palmprint_workbench.config import CAMERA_DEFAULT_DEVICE, CAMERA_DEFAULT_HEIGHT, CAMERA_DEFAULT_WIDTH, REPORT_DIR


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--device", default=CAMERA_DEFAULT_DEVICE)
    parser.add_argument("--width", type=int, default=CAMERA_DEFAULT_WIDTH)
    parser.add_argument("--height", type=int, default=CAMERA_DEFAULT_HEIGHT)
    parser.add_argument("--frames", type=int, default=3)
    parser.add_argument("--report", type=Path)
    parser.add_argument(
        "--callback",
        action="store_true",
        help="exercise the service-layer camera callback without retaining a raw image",
    )
    args = parser.parse_args()

    if args.frames < 1:
        parser.error("--frames must be at least 1")
    known_devices = {device.path: device for device in list_v4l2_devices()}
    if args.device not in known_devices:
        parser.error(f"{args.device} is not a detected V4L2 device")

    started = time.perf_counter_ns()
    if args.callback:
        from palmprint_workbench.services.workbench import WORKBENCH, capture_board_camera

        try:
            image, status = capture_board_camera(
                args.device, f"{args.width}x{args.height}"
            )
        finally:
            WORKBENCH.close()
        if image is None:
            print(json.dumps({"ok": False, "device": args.device, "error": status}))
            return 1
        elapsed_ms = (time.perf_counter_ns() - started) / 1e6
        result = {
            "ok": True,
            "callback": "palmprint_workbench.services.workbench.capture_board_camera",
            "device": args.device,
            "device_name": known_devices[args.device].name,
            "requested_resolution": [args.width, args.height],
            "rgb_shape": [int(value) for value in image.shape],
            "callback_status": status,
            "elapsed_ms": round(elapsed_ms, 3),
            "raw_images_saved": False,
        }
        return _write_result(result, args.report)

    shapes: list[list[int]] = []
    jpeg_bytes: list[int] = []
    try:
        with CameraCapture(args.device, width=args.width, height=args.height) as camera:
            for _ in range(args.frames):
                frame = camera.capture()
                shapes.append([int(value) for value in frame.rgb.shape])
                jpeg_bytes.append(len(frame.jpeg))
    except CameraError as exc:
        print(json.dumps({"ok": False, "device": args.device, "error": str(exc)}))
        return 1

    elapsed_ms = (time.perf_counter_ns() - started) / 1e6
    result = {
        "ok": True,
        "device": args.device,
        "device_name": known_devices[args.device].name,
        "requested_resolution": [args.width, args.height],
        "frames": args.frames,
        "rgb_shapes": shapes,
        "jpeg_bytes": jpeg_bytes,
        "elapsed_ms": round(elapsed_ms, 3),
        "mean_capture_ms": round(elapsed_ms / args.frames, 3),
        "raw_images_saved": False,
    }
    return _write_result(result, args.report)


def _write_result(result: dict, report: Path | None) -> int:
    if report is None:
        report = REPORT_DIR / "runs" / f"camera_smoke_{time.strftime('%Y%m%d_%H%M%S')}.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    result["report"] = str(report)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
