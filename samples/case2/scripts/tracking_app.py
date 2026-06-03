import argparse
import os
import sys
import time
from pathlib import Path
from typing import List, Optional


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from utils.opencv_runtime import (
    cv2,
    add_camera_arguments,
    compute_average_timings,
    compute_display_fps,
    create_backend,
    create_timing_totals,
    list_available_models,
    open_capture_context,
    print_capture_summary,
    print_runtime_banner,
    read_frame,
    resolve_writer_fps,
    update_timing_totals,
)
from utils.preprocessing import (
    MODEL_DIR,
    create_video_writer,
    load_labels,
    resolve_model_path,
)
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
    parser.add_argument("--backbone", default="mobilenetv3_large_100", help="Model backbone name used for auto-discovery.")
    parser.add_argument("--model", default="", help="Explicit model path. Overrides --backbone.")
    parser.add_argument("--model-dir", default=str(MODEL_DIR), help="Directory that stores SSD model files.")
    parser.add_argument("--source", default="0", help="Camera index or video path.")
    parser.add_argument("--score-threshold", type=float, default=0.35, help="Minimum confidence for tracking detections.")
    parser.add_argument("--nms-threshold", type=float, default=0.45, help="NMS IoU threshold for detection.")
    parser.add_argument("--max-detections", type=int, default=100, help="Maximum detections per frame.")
    add_camera_arguments(parser)
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


def build_tracker(args: argparse.Namespace) -> DeepSORT:
    return DeepSORT(
        max_age=args.track_max_age,
        min_hits=args.track_min_hits,
        iou_threshold=args.track_iou_threshold,
        center_distance_threshold=args.track_center_distance_threshold,
        size_smoothing=args.track_size_smoothing,
        score_smoothing=args.track_score_smoothing,
    )


def prepare_tracking_runtime(args: argparse.Namespace, model_dir: Path):
    labels = load_labels(args.labels)
    allowed_track_class_ids = parse_track_classes(args.track_classes, labels)
    model_path = resolve_model_path(args.model, args.backbone, model_dir, args.device)
    backend = create_backend(args.device, model_path, args.device_id)
    capture_context = open_capture_context(args.source, args.camera_profile, args.camera_mjpeg)
    tracker = build_tracker(args)
    return labels, allowed_track_class_ids, model_path, backend, capture_context, tracker


def print_tracking_startup(
    args: argparse.Namespace,
    labels: List[str],
    allowed_track_class_ids: Optional[List[int]],
    model_path: Path,
    backend,
    capture_context,
) -> None:
    print_runtime_banner(args.device, model_path)
    print_capture_summary(args.camera_profile, args.camera_mjpeg, capture_context)
    if allowed_track_class_ids is not None:
        selected_labels = ", ".join(labels[class_id] for class_id in allowed_track_class_ids)
        print(f"Tracking classes: {selected_labels}")
    backend.print_model_io()
    print("Press 'q' to quit.")


def render_tracking_frame(
    args: argparse.Namespace,
    frame,
    read_ms: float,
    labels: List[str],
    model_path: Path,
    backend,
    tracker: DeepSORT,
    allowed_track_class_ids: Optional[List[int]],
    timing_totals: dict[str, float],
    frame_count: int,
    capture_context,
):
    detections, profile_ms = backend.infer_with_profile(
        frame,
        args.score_threshold,
        args.nms_threshold,
        args.max_detections,
        allowed_class_ids=allowed_track_class_ids,
    )
    update_timing_totals(timing_totals, read_ms, profile_ms)
    draw_start = time.perf_counter()
    tracker_inputs = detections_to_tracker_inputs(detections)
    tracks = tracker.update(tracker_inputs)
    avg_timings_ms = compute_average_timings(timing_totals, frame_count)
    fps = compute_display_fps(avg_timings_ms, capture_context.capture_fps)
    annotated = draw_tracks(frame, tracks, labels, fps, model_path.name, args.device, len(detections), avg_timings_ms)
    draw_ms = (time.perf_counter() - draw_start) * 1000.0
    timing_totals["draw"] += draw_ms
    return annotated, fps


def main() -> int:
    args = parse_args()
    model_dir = Path(args.model_dir).expanduser().resolve()
    backend = None
    capture_context = None

    if args.list_models:
        return list_available_models(model_dir, args.device)

    try:
        labels, allowed_track_class_ids, model_path, backend, capture_context, tracker = prepare_tracking_runtime(args, model_dir)
    except Exception as exc:
        print(f"Failed to prepare tracking pipeline: {exc}")
        if backend is not None:
            backend.release()
        return 1

    writer = None
    frame_count = 0
    timing_totals = create_timing_totals()
    pending_frame = capture_context.first_frame
    pending_read_ms = capture_context.first_read_ms

    try:
        print_tracking_startup(args, labels, allowed_track_class_ids, model_path, backend, capture_context)

        while True:
            frame, read_ms, pending_frame, pending_read_ms = read_frame(capture_context, pending_frame, pending_read_ms)
            if frame is None:
                print("Video stream ended or camera frame read failed.")
                break

            frame_count += 1
            annotated, fps = render_tracking_frame(
                args,
                frame,
                read_ms,
                labels,
                model_path,
                backend,
                tracker,
                allowed_track_class_ids,
                timing_totals,
                frame_count,
                capture_context,
            )

            if args.save:
                if writer is None:
                    writer_fps = resolve_writer_fps(capture_context.capture_fps, fps)
                    writer = create_video_writer(args.save, writer_fps, annotated.shape)
                writer.write(annotated)

            if not args.no_display:
                cv2.imshow(args.window_name, annotated)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    finally:
        capture_context.cap.release()
        if writer is not None:
            writer.release()
        if not args.no_display:
            cv2.destroyAllWindows()
        backend.release()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
