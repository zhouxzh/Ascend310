#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

import cv2


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_MODEL = ROOT / "models" / "YOLOv10n_gestures.onnx"

from hagrid_yolo.backends.onnx_backend import OnnxBackend
from hagrid_yolo.camera import infer_camera, infer_image
from hagrid_yolo.detector import YoloDetector
from hagrid_yolo.metadata import load_labels, resolve_imgsz


def parse_args():
    parser = argparse.ArgumentParser(description="Run YOLO ONNX inference with OpenCV camera or image input.")
    parser.add_argument("-m", "--model", default=DEFAULT_MODEL, type=Path)
    parser.add_argument("-l", "--labels", default=None, type=Path)
    parser.add_argument("-s", "--source", default="/dev/video0", type=str)
    parser.add_argument("--imgsz", default=0, type=int, help="Use 0 to read metadata, then fallback to 640.")
    parser.add_argument("--conf", default=0.25, type=float)
    parser.add_argument("--iou", default=0.45, type=float)
    parser.add_argument("--provider", default="CPUExecutionProvider", type=str)
    parser.add_argument("--intra-op-threads", default=3, type=int)
    parser.add_argument("--inter-op-threads", default=1, type=int)
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
    return parser.parse_args()


def main():
    args = parse_args()
    if not args.model.is_absolute() and not args.model.exists():
        candidate = ROOT / args.model
        if candidate.exists():
            args.model = candidate
    args.imgsz = resolve_imgsz(args.model, args.imgsz)
    cv2.setNumThreads(max(1, args.opencv_threads))

    if args.provider == "CPUExecutionProvider" and "yolov10x" in args.model.name.lower():
        print("WARNING: YOLOv10x is very slow with ONNX Runtime CPU on Ascend 310B. Use YOLOv10n for CPU testing.")

    labels = load_labels(args.model, args.labels)
    backend = OnnxBackend(
        args.model,
        provider=args.provider,
        intra_op_threads=args.intra_op_threads,
        inter_op_threads=args.inter_op_threads,
    )
    detector = YoloDetector(backend, imgsz=args.imgsz, conf=args.conf, iou=args.iou)

    try:
        if args.once:
            infer_image(args.source, detector, labels, save=args.save, no_window=args.no_window, window_title="ONNX YOLO")
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
                window_title="ONNX YOLO",
                latency_label="ONNX",
            )
    finally:
        backend.release()


if __name__ == "__main__":
    main()
