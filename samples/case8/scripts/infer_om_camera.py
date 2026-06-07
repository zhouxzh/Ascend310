#!/usr/bin/env python3
import argparse
import sys
import time
from pathlib import Path

import cv2
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_MODEL = ROOT / "models" / "YOLOv10n_gestures.om"

from hagrid_yolo.backends.acl_backend import AclModel
from hagrid_yolo.camera import infer_camera, infer_image
from hagrid_yolo.detector import YoloDetector
from hagrid_yolo.metadata import load_labels, resolve_imgsz


def parse_args():
    parser = argparse.ArgumentParser(description="Run YOLO OM inference with Ascend ACL and OpenCV.")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL, type=Path)
    parser.add_argument("-l", "--labels", default=None, type=Path)
    parser.add_argument("-s", "--source", default="/dev/video0", type=str)
    parser.add_argument("--imgsz", default=0, type=int, help="Use 0 to read metadata, then fallback to 640.")
    parser.add_argument("--conf", default=0.25, type=float)
    parser.add_argument("--iou", default=0.45, type=float)
    parser.add_argument("--device-id", default=0, type=int)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--save", default=None, type=Path)
    parser.add_argument("--camera-width", default=640, type=int)
    parser.add_argument("--camera-height", default=480, type=int)
    parser.add_argument("--camera-fps", default=30, type=int)
    parser.add_argument("--infer-every-n", default=1, type=int)
    parser.add_argument("--sync-infer", action="store_true")
    parser.add_argument("--max-frames", default=0, type=int)
    parser.add_argument("--no-window", action="store_true")
    parser.add_argument("--opencv-threads", default=1, type=int)
    parser.add_argument("--benchmark-runs", default=0, type=int)
    parser.add_argument("--warmup-runs", default=2, type=int)
    parser.add_argument("--print-model-info", action="store_true")
    return parser.parse_args()


def run_benchmark(args, backend):
    tensor = np.zeros((1, 3, args.imgsz, args.imgsz), dtype=np.float32)

    for _ in range(max(0, args.warmup_runs)):
        backend.infer(tensor)

    runs = max(1, args.benchmark_runs)
    latencies = []
    last_outputs = None
    for _ in range(runs):
        start_t = time.time()
        last_outputs = backend.infer(tensor)
        latencies.append((time.time() - start_t) * 1000.0)

    latency_array = np.asarray(latencies, dtype=np.float32)
    print(
        f"[OM] benchmark runs={runs}, "
        f"avg={latency_array.mean():.2f} ms, "
        f"min={latency_array.min():.2f} ms, "
        f"max={latency_array.max():.2f} ms"
    )
    if last_outputs:
        for index, output in enumerate(last_outputs):
            print(f"[OM] output[{index}] shape={output.shape} dtype={output.dtype}")


def main():
    args = parse_args()
    if not args.model.is_absolute() and not args.model.exists():
        candidate = ROOT / args.model
        if candidate.exists():
            args.model = candidate
    args.imgsz = resolve_imgsz(args.model, args.imgsz)
    cv2.setNumThreads(max(1, args.opencv_threads))

    if not args.model.exists():
        raise FileNotFoundError(f"OM model not found: {args.model}")

    labels = None
    if args.benchmark_runs <= 0:
        labels = load_labels(args.model, args.labels)

    backend = AclModel(args.model, device_id=args.device_id)
    detector = YoloDetector(backend, imgsz=args.imgsz, conf=args.conf, iou=args.iou)

    try:
        if args.print_model_info:
            backend.print_model_info()
        if args.benchmark_runs > 0:
            run_benchmark(args, backend)
            return
        if args.once:
            infer_image(args.source, detector, labels, save=args.save, no_window=args.no_window, window_title="OM YOLO")
        else:
            infer_camera(
                args.source,
                detector,
                labels,
                camera_width=args.camera_width,
                camera_height=args.camera_height,
                camera_fps=args.camera_fps,
                infer_every_n=args.infer_every_n,
                sync_infer=args.sync_infer,
                max_frames=args.max_frames,
                no_window=args.no_window,
                window_title="OM YOLO",
                latency_label="NPU",
            )
    finally:
        backend.release()


if __name__ == "__main__":
    main()
