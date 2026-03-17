import argparse
import os
import sys
import time
from pathlib import Path
from typing import List, Optional


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from utils.opencv_runtime import cv2
from utils.preprocessing import MODEL_DIR, create_video_writer, discover_models, load_labels, open_capture, resolve_model_path
from tracking.deepsort import DeepSORT
from utils.postprocessing import detections_to_tracker_inputs, draw_tracks


def parse_track_classes(track_classes_arg: str, labels: List[str]) -> Optional[List[int]]:
    if not track_classes_arg.strip():
        return None

    label_to_id = {label.strip().lower(): index for index, label in enumerate(labels)}
    class_ids = []
    for item in track_classes_arg.split(","):
        token = item.strip()
        if not token:
            continue
        if token.isdigit():
            class_id = int(token)
        else:
            class_id = label_to_id.get(token.lower())
            if class_id is None:
                available = ", ".join(labels[:20])
                raise ValueError(f"Unknown tracking class: {token}. Examples: {available}")
        if class_id <= 0 or class_id >= len(labels):
            raise ValueError(f"Tracking class id out of range: {class_id}")
        class_ids.append(class_id)

    if not class_ids:
        return None
    return sorted(set(class_ids))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run real-time SSD detection with simple IOU-based tracking.")
    parser.add_argument("--device", choices=["cpu", "npu"], default="npu", help="Inference backend.")
    parser.add_argument("--device-id", type=int, default=0, help="Ascend device id when --device=npu.")
    parser.add_argument("--backbone", default="mobilenetv3", help="Model backbone name used for auto-discovery.")
    parser.add_argument("--model", default="", help="Explicit model path. Overrides --backbone.")
    parser.add_argument("--model-dir", default=str(MODEL_DIR), help="Directory that stores SSD model files.")
    parser.add_argument("--source", default="0", help="Camera index or video path.")
    parser.add_argument("--score-threshold", type=float, default=0.35, help="Minimum confidence for tracking detections.")
    parser.add_argument("--nms-threshold", type=float, default=0.45, help="NMS IoU threshold for detection.")
    parser.add_argument("--max-detections", type=int, default=100, help="Maximum detections per frame.")
    parser.add_argument("--camera-width", type=int, default=640, help="Preferred camera width.")
    parser.add_argument("--camera-height", type=int, default=480, help="Preferred camera height.")
    parser.add_argument("--camera-fps", type=float, default=0.0, help="Requested camera FPS for live sources. Use 0 to keep the backend default.")
    parser.add_argument("--camera-mjpeg", action="store_true", help="Request MJPEG camera output to reduce capture latency on some USB cameras.")
    parser.add_argument("--labels", default="", help="Optional label file path. Defaults to COCO labels.")
    parser.add_argument("--window-name", default="SSD Tracking", help="OpenCV display window name.")
    parser.add_argument("--save", default="", help="Optional output video path.")
    parser.add_argument("--no-display", action="store_true", help="Disable cv2.imshow for headless environments.")
    parser.add_argument("--list-models", action="store_true", help="List available models for the selected device and exit.")
    parser.add_argument("--track-max-age", type=int, default=90, help="Maximum frames to keep unmatched tracks alive.")
    parser.add_argument("--track-min-hits", type=int, default=1, help="Minimum matched detections before a track is displayed.")
    parser.add_argument("--track-iou-threshold", type=float, default=0.3, help="Minimum IoU required to associate detections to tracks.")
    parser.add_argument("--track-center-distance-threshold", type=float, default=1.8, help="Maximum normalized center distance used for fallback association.")
    parser.add_argument("--track-size-smoothing", type=float, default=0.8, help="Track box size smoothing factor in [0, 1). Higher means more stable but less responsive.")
    parser.add_argument("--track-score-smoothing", type=float, default=0.7, help="Track score smoothing factor in [0, 1). Higher means less score flicker.")
    parser.add_argument("--track-classes", default="", help="Comma-separated class names or ids to track, for example 'person,bus' or '1,6'.")
    return parser.parse_args()

def main() -> int:
    args = parse_args()
    model_dir = Path(args.model_dir).expanduser().resolve()
    backend = None

    if args.list_models:
        available = discover_models(model_dir, args.device)
        if not available:
            print(f"No {args.device.upper()} models found in {model_dir}")
            return 1
        for backbone, path in available.items():
            print(f"{backbone}: {path.name}")
        return 0

    try:
        labels = load_labels(args.labels)
        allowed_track_class_ids = parse_track_classes(args.track_classes, labels)
        model_path = resolve_model_path(args.model, args.backbone, model_dir, args.device)
        if args.device == "cpu":
            try:
                from ssdlite.cpu_backend import CpuBackend
            except ImportError:
                print("CPU backend requires onnxruntime to be installed.")
                return 1
            backend = CpuBackend(model_path)
        elif args.device == "npu":
            try:
                from ssdlite.npu_backend import NpuBackend
            except ImportError:
                print("NPU backend requires Ascend ACL Python runtime to be installed.")
                return 1
            backend = NpuBackend(model_path, device_id=args.device_id)
        else:
            print(f"Unsupported device: {args.device}")
            return 1
        tracker = DeepSORT(
            max_age=args.track_max_age,
            min_hits=args.track_min_hits,
            iou_threshold=args.track_iou_threshold,
            center_distance_threshold=args.track_center_distance_threshold,
            size_smoothing=args.track_size_smoothing,
            score_smoothing=args.track_score_smoothing,
        )
    except Exception as exc:
        print(f"Failed to prepare tracking pipeline: {exc}")
        return 1

    cap = open_capture(
        args.source,
        args.camera_width,
        args.camera_height,
        fps=args.camera_fps,
        use_mjpeg=args.camera_mjpeg,
    )
    if not cap.isOpened():
        print(f"Failed to open video source: {args.source}")
        backend.release()
        return 1

    # Query camera-reported FPS once and use it to bound displayed FPS.
    capture_fps = cap.get(cv2.CAP_PROP_FPS)

    writer = None
    frame_count = 0
    timing_totals = {
        "read": 0.0,
        "preprocess": 0.0,
        "inference": 0.0,
        "decode": 0.0,
        "draw": 0.0,
    }

    try:
        print(f"Using device: {args.device}")
        print(f"Using model: {model_path}")
        if allowed_track_class_ids is not None:
            selected_labels = ", ".join(labels[class_id] for class_id in allowed_track_class_ids)
            print(f"Tracking classes: {selected_labels}")
        backend.print_model_io()
        print("Press 'q' to quit.")

        while True:
            read_start = time.perf_counter()
            ok, frame = cap.read()
            read_ms = (time.perf_counter() - read_start) * 1000.0
            if not ok:
                print("Video stream ended or camera frame read failed.")
                break

            detections, profile_ms = backend.infer_with_profile(
                frame,
                args.score_threshold,
                args.nms_threshold,
                args.max_detections,
                allowed_class_ids=allowed_track_class_ids,
            )
            draw_start = time.perf_counter()
            tracker_inputs = detections_to_tracker_inputs(detections)
            tracks = tracker.update(tracker_inputs)
            frame_count += 1
            timing_totals["read"] += read_ms
            timing_totals["preprocess"] += profile_ms["preprocess"]
            timing_totals["inference"] += profile_ms["inference"]
            timing_totals["decode"] += profile_ms["decode"]

            avg_timings_ms = {
                key: timing_totals[key] / frame_count
                for key in ("read", "preprocess", "inference", "decode", "draw")
            }
            avg_frame_ms = sum(avg_timings_ms.values())
            processing_fps = 1000.0 / max(avg_frame_ms, 1e-6)
            # If the capture device reports a max FPS (>0), cap the displayed FPS to it.
            if capture_fps and capture_fps > 1e-3:
                fps = min(processing_fps, capture_fps)
            else:
                fps = processing_fps
            annotated = draw_tracks(frame, tracks, labels, fps, model_path.name, args.device, len(detections), avg_timings_ms)
            draw_ms = (time.perf_counter() - draw_start) * 1000.0
            timing_totals["draw"] += draw_ms

            if args.save:
                if writer is None:
                    # Use camera-reported FPS when available, otherwise fall back to measured fps.
                    writer_fps = capture_fps if (capture_fps and capture_fps > 1e-3) else fps
                    writer = create_video_writer(args.save, writer_fps, annotated.shape)
                writer.write(annotated)

            if not args.no_display:
                cv2.imshow(args.window_name, annotated)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    finally:
        cap.release()
        if writer is not None:
            writer.release()
        if not args.no_display:
            cv2.destroyAllWindows()
        backend.release()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())