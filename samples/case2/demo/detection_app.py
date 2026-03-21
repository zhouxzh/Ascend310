import argparse
import os
import sys
import time
from pathlib import Path


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
from utils.postprocessing import draw_detections


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Run real-time SSD detection on CPU or Ascend NPU.")
	parser.add_argument("--device", choices=["cpu", "npu"], default="cpu", help="Inference backend.")
	parser.add_argument("--device-id", type=int, default=0, help="Ascend device id when --device=npu.")
	parser.add_argument("--backbone", default="mobilenetv3", help="Model backbone name used for auto-discovery.")
	parser.add_argument("--model", default="", help="Explicit model path. Overrides --backbone.")
	parser.add_argument("--model-dir", default=str(MODEL_DIR), help="Directory that stores SSD model files.")
	parser.add_argument("--source", default="0", help="Camera index or video path.")
	parser.add_argument("--score-threshold", type=float, default=0.35, help="Minimum confidence for drawing detections.")
	parser.add_argument("--nms-threshold", type=float, default=0.45, help="NMS IoU threshold.")
	parser.add_argument("--max-detections", type=int, default=100, help="Maximum detections per frame.")
	add_camera_arguments(parser)
	parser.add_argument("--labels", default="", help="Optional label file path. Defaults to COCO labels.")
	parser.add_argument("--window-name", default="SSD Detection", help="OpenCV display window name.")
	parser.add_argument("--save", default="", help="Optional output video path.")
	parser.add_argument("--no-display", action="store_true", help="Disable cv2.imshow for headless environments.")
	parser.add_argument("--list-models", action="store_true", help="List available models for the selected device and exit.")
	return parser.parse_args()


def prepare_detection_runtime(args: argparse.Namespace, model_dir: Path):
	labels = load_labels(args.labels)
	model_path = resolve_model_path(args.model, args.backbone, model_dir, args.device)
	backend = create_backend(args.device, model_path, args.device_id)
	capture_context = open_capture_context(args.source, args.camera_profile, args.camera_mjpeg)
	return labels, model_path, backend, capture_context


def print_detection_startup(args: argparse.Namespace, model_path: Path, backend, capture_context) -> None:
	print_runtime_banner(args.device, model_path)
	print_capture_summary(args.camera_profile, args.camera_mjpeg, capture_context)
	backend.print_model_io()
	print("Press 'q' to quit.")


def render_detection_frame(
	args: argparse.Namespace,
	frame,
	read_ms: float,
	labels,
	model_path: Path,
	backend,
	timing_totals: dict[str, float],
	frame_count: int,
	capture_context,
):
	detections, profile_ms = backend.infer_with_profile(frame, args.score_threshold, args.nms_threshold, args.max_detections)
	update_timing_totals(timing_totals, read_ms, profile_ms)
	draw_start = time.perf_counter()
	avg_timings_ms = compute_average_timings(timing_totals, frame_count)
	fps = compute_display_fps(avg_timings_ms, capture_context.capture_fps)
	annotated = draw_detections(frame, detections, labels, fps, model_path.name, args.device, avg_timings_ms)
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
		labels, model_path, backend, capture_context = prepare_detection_runtime(args, model_dir)
	except Exception as exc:
		print(f"Failed to prepare backend: {exc}")
		if backend is not None:
			backend.release()
		return 1

	writer = None
	frame_count = 0
	timing_totals = create_timing_totals()
	pending_frame = capture_context.first_frame
	pending_read_ms = capture_context.first_read_ms

	try:
		print_detection_startup(args, model_path, backend, capture_context)

		while True:
			frame, read_ms, pending_frame, pending_read_ms = read_frame(capture_context, pending_frame, pending_read_ms)
			if frame is None:
				print("Video stream ended or camera frame read failed.")
				break

			frame_count += 1
			annotated, fps = render_detection_frame(
				args,
				frame,
				read_ms,
				labels,
				model_path,
				backend,
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