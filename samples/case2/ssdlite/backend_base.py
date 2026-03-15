import time
from pathlib import Path
from collections.abc import Mapping, Sequence

import numpy as np

from ssdlite.decoder import SSDDecoder, _nms, dboxes300_coco, dboxes320_coco
from utils.opencv_runtime import cv2

def infer_input_size_from_model(model_path) -> int:
	model_name = Path(model_path).name.lower()
	if "resnet" in model_name or "ssd300" in model_name:
		return 300
	if "mobilenet" in model_name or "ssd320" in model_name:
		return 320
	raise ValueError(
		f"无法根据模型名识别模型类型: {model_path}。"
		"当前仅支持 resnet/ssd300 和 mobilenet/ssd320 两种固定形式。"
	)


def preprocess_frame(frame: np.ndarray, input_hw: tuple[int, int]) -> np.ndarray:
	input_h, input_w = input_hw
	rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
	resized = cv2.resize(rgb, (input_w, input_h), interpolation=cv2.INTER_LINEAR)
	image = resized.astype(np.float32) / 255.0
	image = image.transpose(2, 0, 1)

	mean = np.array([0.485, 0.456, 0.406], dtype=np.float32).reshape(3, 1, 1)
	std = np.array([0.229, 0.224, 0.225], dtype=np.float32).reshape(3, 1, 1)
	image = (image - mean) / std
	return np.expand_dims(image, axis=0).astype(np.float32)


def get_model_spec(input_size: int) -> dict[str, object]:
	specs = {
		300: {
			"input_hw": (300, 300),
			"num_boxes": 8732,
			"decoder_name": "ssd300_resnet",
		},
		320: {
			"input_hw": (320, 320),
			"num_boxes": 3234,
			"decoder_name": "ssd320_mobilenet",
		},
	}
	spec = specs.get(int(input_size))
	if spec is None:
		raise ValueError(f"Unsupported SSD input size: {input_size}")
	return spec


def build_ssd_decoder(input_size: int) -> dict[str, object]:
	spec = get_model_spec(input_size)
	if int(input_size) == 300:
		dboxes = dboxes300_coco()
	elif int(input_size) == 320:
		dboxes = dboxes320_coco()
	else:
		raise ValueError(f"Unsupported SSD input size: {input_size}")

	return {
		"name": spec["decoder_name"],
		"num_boxes": spec["num_boxes"],
		"decoder": SSDDecoder(dboxes),
	}


def summarize_outputs(outputs) -> str:
	parts = []
	if isinstance(outputs, Mapping):
		for key, value in outputs.items():
			parts.append(f"{key}:{np.shape(value)}")
	elif isinstance(outputs, Sequence):
		for index, value in enumerate(outputs):
			parts.append(f"output[{index}]:{np.shape(value)}")
	else:
		raise TypeError(f"Unsupported outputs container: {type(outputs)!r}")
	return ", ".join(parts)


def _find_boxes_scores_outputs(outputs):
	boxes = None
	scores = None
	if isinstance(outputs, Mapping):
		output_values = outputs.values()
	elif isinstance(outputs, Sequence):
		output_values = outputs
	else:
		raise TypeError(f"Unsupported outputs container: {type(outputs)!r}")

	for value in output_values:
		array = np.asarray(value)
		squeezed = np.squeeze(array)
		if squeezed.ndim != 2:
			continue

		candidate_num_boxes = None
		if squeezed.shape[0] == 4:
			candidate_num_boxes = squeezed.shape[1]
		elif squeezed.shape[1] == 4:
			candidate_num_boxes = squeezed.shape[0]

		if boxes is None and candidate_num_boxes is not None:
			boxes = array
			continue

		if scores is None and all(dim > 4 for dim in squeezed.shape):
			scores = array

	return boxes, scores


def _build_detections(boxes, classes, scores) -> list[dict[str, object]]:
	detections = []
	for box, class_id, score in zip(boxes, classes, scores):
		detections.append(
			{
				"box": np.asarray(box, dtype=np.float32),
				"score": float(score),
				"class_id": int(class_id),
			}
		)

	detections.sort(key=lambda item: item["score"], reverse=True)
	return detections


def _scale_boxes_to_frame(boxes, frame_shape):
	height, width = frame_shape[:2]
	scaled = np.asarray(boxes, dtype=np.float32).copy()

	if scaled.size == 0:
		return scaled

	if float(np.max(scaled)) <= 1.5:
		scaled[:, [0, 2]] *= width
		scaled[:, [1, 3]] *= height

	scaled[:, [0, 2]] = np.clip(scaled[:, [0, 2]], 0, width - 1)
	scaled[:, [1, 3]] = np.clip(scaled[:, [1, 3]], 0, height - 1)
	return scaled


def _class_wise_nms(boxes, scores, classes, iou_threshold):
	if boxes is None or len(boxes) == 0:
		return []

	selected_boxes = []
	selected_scores = []
	selected_classes = []
	for class_id in np.unique(classes):
		class_mask = classes == class_id
		class_boxes = boxes[class_mask]
		class_scores = scores[class_mask]
		keep = _nms(class_boxes, class_scores, iou_threshold)
		if keep.size == 0:
			continue
		selected_boxes.append(class_boxes[keep])
		selected_scores.append(class_scores[keep])
		selected_classes.append(np.full(keep.shape, class_id, dtype=np.int64))

	if not selected_boxes:
		return []

	return _build_detections(
		np.concatenate(selected_boxes, axis=0),
		np.concatenate(selected_classes, axis=0),
		np.concatenate(selected_scores, axis=0),
	)


def decode_detections(outputs, frame_shape, score_threshold, nms_threshold, max_detections, ssd_decoder, strict_ssd=False):
	boxes_raw, scores_raw = _find_boxes_scores_outputs(outputs)
	if boxes_raw is not None and scores_raw is not None:
		boxes_shape = np.squeeze(boxes_raw).shape
		if len(boxes_shape) == 2:
			if boxes_shape[0] == 4:
				num_boxes = boxes_shape[1]
			elif boxes_shape[1] == 4:
				num_boxes = boxes_shape[0]
			else:
				num_boxes = None

			if num_boxes is not None:
				expected_num_boxes = int(ssd_decoder["num_boxes"])
				if num_boxes == expected_num_boxes:
					decoded = ssd_decoder["decoder"].decode_batch(
						np.asarray(boxes_raw, dtype=np.float32).copy(),
						np.asarray(scores_raw, dtype=np.float32).copy(),
						criteria=nms_threshold,
						max_output=max_detections,
					)
					if not decoded:
						return []

					boxes_out, labels_out, scores_out = decoded[0]
					if scores_out.size == 0:
						return []

					keep = scores_out >= score_threshold
					if not np.any(keep):
						return []

					boxes_out = _scale_boxes_to_frame(boxes_out[keep], frame_shape)
					labels_out = labels_out[keep]
					scores_out = scores_out[keep]
					return _build_detections(boxes_out, labels_out, scores_out)

				raise RuntimeError(
					f"SSD prior count mismatch: decoder={ssd_decoder['name']}, expected={expected_num_boxes}, actual={num_boxes}. "
					f"Outputs: {summarize_outputs(outputs)}"
				)
			if strict_ssd:
				raise RuntimeError(f"Cannot parse SSD boxes shape: {boxes_shape}. Outputs: {summarize_outputs(outputs)}")

		elif strict_ssd:
			raise RuntimeError(f"Unexpected SSD boxes shape: {boxes_shape}. Outputs: {summarize_outputs(outputs)}")

	elif strict_ssd:
		raise RuntimeError(f"Cannot identify SSD boxes/scores outputs: {summarize_outputs(outputs)}")

	if isinstance(outputs, Mapping):
		name_map = {str(name).lower(): name for name in outputs}
		boxes_name = next((name_map[key] for key in name_map if "detection_boxes" in key), None)
		scores_name = next((name_map[key] for key in name_map if "detection_scores" in key), None)
		classes_name = next((name_map[key] for key in name_map if "detection_classes" in key), None)
		count_name = next((name_map[key] for key in name_map if "num_detections" in key), None)

		if boxes_name and scores_name and classes_name:
			boxes = np.squeeze(outputs[boxes_name], axis=0)
			scores = np.squeeze(outputs[scores_name], axis=0)
			classes = np.squeeze(outputs[classes_name], axis=0).astype(np.int64)
			valid_count = len(scores)

			if count_name:
				valid_count = int(np.squeeze(outputs[count_name]).item())

			boxes = boxes[:valid_count]
			scores = scores[:valid_count]
			classes = classes[:valid_count]

			boxes = _scale_boxes_to_frame(boxes, frame_shape)
			keep = scores >= score_threshold
			return _class_wise_nms(boxes[keep], scores[keep], classes[keep], nms_threshold)

	if boxes_raw is not None and scores_raw is not None:
		boxes = np.squeeze(boxes_raw)
		scores = np.squeeze(scores_raw)
		if boxes.ndim == 2 and scores.ndim != 0:
			if scores.ndim > 1 and scores.shape[0] != boxes.shape[0] and scores.shape[-1] == boxes.shape[0]:
				scores = scores.transpose(1, 0)

			if scores.shape[0] == boxes.shape[0]:
				boxes = _scale_boxes_to_frame(boxes, frame_shape)
				if scores.ndim == 1:
					classes = np.ones(scores.shape[0], dtype=np.int64)
					confs = scores.astype(np.float32)
				elif scores.shape[1] > 1:
					confs = np.max(scores[:, 1:], axis=1)
					classes = np.argmax(scores[:, 1:], axis=1).astype(np.int64) + 1
				else:
					confs = scores[:, 0].astype(np.float32)
					classes = np.ones(scores.shape[0], dtype=np.int64)

				keep = confs >= score_threshold
				return _class_wise_nms(boxes[keep], confs[keep], classes[keep], nms_threshold)

	if isinstance(outputs, Mapping):
		output_values = outputs.values()
	elif isinstance(outputs, Sequence):
		output_values = outputs
	else:
		raise TypeError(f"Unsupported outputs container: {type(outputs)!r}")

	for value in output_values:
		tensor = np.squeeze(value)
		if tensor.ndim != 2 or tensor.shape[-1] not in (6, 7):
			continue

		if tensor.shape[-1] == 6:
			boxes = tensor[:, :4]
			scores = tensor[:, 4]
			classes = tensor[:, 5].astype(np.int64)
		elif np.allclose(tensor[:, 0], 0):
			boxes = tensor[:, 3:7]
			scores = tensor[:, 2]
			classes = tensor[:, 1].astype(np.int64)
		else:
			boxes = tensor[:, :4]
			scores = tensor[:, 4]
			classes = tensor[:, 5].astype(np.int64)

		boxes = _scale_boxes_to_frame(boxes, frame_shape)
		keep = scores >= score_threshold
		return _class_wise_nms(boxes[keep], scores[keep], classes[keep], nms_threshold)

	raise RuntimeError(f"无法识别模型输出格式: {summarize_outputs(outputs)}")


class DetectionBackend:
	def __init__(self, model_path, strict_ssd: bool):
		self.model_path = Path(model_path)
		input_size = infer_input_size_from_model(self.model_path)
		self.input_hw = get_model_spec(input_size)["input_hw"]
		self.output_shapes: list[object] = []
		self.strict_ssd = strict_ssd
		self.ssd_decoder = build_ssd_decoder(self.input_hw[0])
		self.last_profile_ms = {
			"preprocess": 0.0,
			"inference": 0.0,
			"decode": 0.0,
		}

	def infer(self, frame: np.ndarray, score_threshold: float, nms_threshold: float, max_detections: int) -> list[dict[str, object]]:
		detections, _ = self.infer_with_profile(frame, score_threshold, nms_threshold, max_detections)
		return detections

	def infer_with_profile(
		self,
		frame: np.ndarray,
		score_threshold: float,
		nms_threshold: float,
		max_detections: int,
	) -> tuple[list[dict[str, object]], dict[str, float]]:
		preprocess_start = time.perf_counter()
		input_tensor = preprocess_frame(frame, self.input_hw)
		preprocess_ms = (time.perf_counter() - preprocess_start) * 1000.0

		inference_start = time.perf_counter()
		outputs = self._run_model(input_tensor)
		inference_ms = (time.perf_counter() - inference_start) * 1000.0

		decode_start = time.perf_counter()
		detections = decode_detections(
			outputs,
			frame.shape,
			score_threshold,
			nms_threshold,
			max_detections,
			self.ssd_decoder,
			self.strict_ssd,
		)
		decode_ms = (time.perf_counter() - decode_start) * 1000.0

		self.last_profile_ms = {
			"preprocess": preprocess_ms,
			"inference": inference_ms,
			"decode": decode_ms,
		}
		return detections, dict(self.last_profile_ms)

	def _run_model(self, input_tensor: np.ndarray):
		raise NotImplementedError

	def print_model_io(self) -> None:
		raise NotImplementedError

	def release(self) -> None:
		raise NotImplementedError