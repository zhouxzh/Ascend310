import numpy as np

from utils.opencv_runtime import cv2


CAPTION_FONT_SCALE = 0.45
CAPTION_THICKNESS = 1
STATUS_FONT_SCALE = 0.5
STATUS_THICKNESS = 1
STATUS_LINE_HEIGHT = 18


def _draw_detection_status(frame, device, model_name, fps, detection_count, avg_timings_ms=None) -> None:
	lines = [
		f"Device: {device.upper()}  FPS: {fps:.2f}",
		f"Model: {model_name}",
		f"Detections: {detection_count}",
	]
	if avg_timings_ms:
		preprocess_ms = avg_timings_ms.get("preprocess", 0.0)
		inference_ms = avg_timings_ms.get("inference", 0.0)
		decode_ms = avg_timings_ms.get("decode", 0.0)
		draw_ms = avg_timings_ms.get("draw", 0.0)
		lines.append(f"Pre: {preprocess_ms:.1f} ms")
		lines.append(f"Infer: {inference_ms:.1f} ms")
		lines.append(f"Decode: {decode_ms:.1f} ms")
		lines.append(f"Draw: {draw_ms:.1f} ms")

	for index, line in enumerate(lines):
		y = 20 + index * STATUS_LINE_HEIGHT
		cv2.putText(frame, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, STATUS_FONT_SCALE, (0, 0, 255), STATUS_THICKNESS)


def _draw_tracking_status(frame, device, model_name, fps, detection_count, track_count, avg_timings_ms=None) -> None:
	lines = [
		f"Device: {device.upper()}  FPS: {fps:.2f}",
		f"Model: {model_name}",
		f"Detections: {detection_count}  Tracks: {track_count}",
	]
	if avg_timings_ms:
		preprocess_ms = avg_timings_ms.get("preprocess", 0.0)
		inference_ms = avg_timings_ms.get("inference", 0.0)
		decode_ms = avg_timings_ms.get("decode", 0.0)
		draw_ms = avg_timings_ms.get("draw", 0.0)
		lines.append(f"Pre: {preprocess_ms:.1f} ms")
		lines.append(f"Infer: {inference_ms:.1f} ms")
		lines.append(f"Decode: {decode_ms:.1f} ms")
		lines.append(f"Draw: {draw_ms:.1f} ms")

	for index, line in enumerate(lines):
		y = 20 + index * STATUS_LINE_HEIGHT
		cv2.putText(frame, line, (10, y), cv2.FONT_HERSHEY_SIMPLEX, STATUS_FONT_SCALE, (0, 0, 255), STATUS_THICKNESS)


def detections_to_tracker_inputs(detections: list[dict[str, object]]) -> np.ndarray:
	tracker_inputs = []
	for detection in detections:
		box = np.asarray(detection["box"], dtype=np.float32)
		tracker_inputs.append(
			[
				float(box[0]),
				float(box[1]),
				float(box[2]),
				float(box[3]),
				float(detection["score"]),
				float(detection["class_id"]),
			]
		)

	if not tracker_inputs:
		return np.empty((0, 6), dtype=np.float32)
	return np.asarray(tracker_inputs, dtype=np.float32)


def draw_detections(frame, detections, labels, fps, model_name, device, avg_timings_ms=None):
	annotated = frame.copy()
	for det in detections:
		box = np.asarray(det["box"], dtype=np.int32)
		class_id = int(det["class_id"])
		score = float(det["score"])
		x1, y1, x2, y2 = box.tolist()
		label = labels[class_id] if 0 <= class_id < len(labels) else f"cls_{class_id}"
		caption = f"{label} {score:.2f}"
		cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 200, 0), 2)
		cv2.putText(annotated, caption, (x1, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, CAPTION_FONT_SCALE, (0, 200, 0), CAPTION_THICKNESS)

	_draw_detection_status(annotated, device, model_name, fps, len(detections), avg_timings_ms)
	return annotated


def _track_color(track_id: int) -> tuple[int, int, int]:
	base = int(track_id) * 97
	return (
		80 + (base * 3) % 175,
		80 + (base * 5) % 175,
		80 + (base * 7) % 175,
	)


def _draw_fading_trail(frame, trail, color):
	if len(trail) < 2:
		return

	segments = list(zip(trail[:-1], trail[1:]))
	total_segments = len(segments)
	for index, (start, end) in enumerate(segments, start=1):
		strength = index / total_segments
		segment_color = tuple(int(channel * strength) for channel in color)
		thickness = max(1, int(1 + 3 * strength))
		cv2.line(
			frame,
			tuple(np.asarray(start, dtype=np.int32).tolist()),
			tuple(np.asarray(end, dtype=np.int32).tolist()),
			segment_color,
			thickness,
			cv2.LINE_AA,
		)


def draw_tracks(frame, tracks, labels, fps, model_name, device, detection_count, avg_timings_ms=None):
	annotated = frame.copy()
	for track in tracks:
		color = _track_color(track.track_id)
		_draw_fading_trail(annotated, getattr(track, "trail", []), color)

		x1, y1, x2, y2 = np.asarray(track.bbox[:4], dtype=np.int32).tolist()
		class_id = int(track.class_id)
		label = labels[class_id] if 0 <= class_id < len(labels) else f"cls_{class_id}"
		caption = f"ID {track.track_id} | {label} | {track.score:.2f}"
		cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)
		cv2.putText(annotated, caption, (x1, max(18, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, CAPTION_FONT_SCALE, color, CAPTION_THICKNESS)

	_draw_tracking_status(annotated, device, model_name, fps, detection_count, len(tracks), avg_timings_ms)
	return annotated
