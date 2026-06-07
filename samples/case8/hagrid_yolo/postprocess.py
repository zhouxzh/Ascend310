import cv2
import numpy as np


def xywh_to_xyxy(boxes):
    converted = np.empty_like(boxes)
    converted[:, 0] = boxes[:, 0] - boxes[:, 2] / 2
    converted[:, 1] = boxes[:, 1] - boxes[:, 3] / 2
    converted[:, 2] = boxes[:, 0] + boxes[:, 2] / 2
    converted[:, 3] = boxes[:, 1] + boxes[:, 3] / 2
    return converted


def normalize_output(output):
    detections = np.squeeze(output)
    if detections.ndim == 1:
        if detections.size % 6 == 0:
            detections = detections.reshape(-1, 6)
        else:
            detections = detections[None, :]
    if detections.shape[0] in (5, 6) and detections.shape[1] > detections.shape[0]:
        detections = detections.T
    return detections


def decode_detections(output, image_shape, preprocess_info, conf_threshold, iou_threshold):
    detections = normalize_output(output)
    if detections.size == 0 or detections.shape[1] < 6:
        return []

    boxes = detections[:, :4].astype(np.float32)
    scores = detections[:, 4].astype(np.float32)
    class_ids = detections[:, 5].astype(np.int32)

    keep = scores >= conf_threshold
    boxes = boxes[keep]
    scores = scores[keep]
    class_ids = class_ids[keep]
    if boxes.size == 0:
        return []

    # Current YOLOv10 exports return xyxy boxes. This keeps the sample usable
    # if another export returns xywh-like boxes.
    if np.any(boxes[:, 2] <= boxes[:, 0]) or np.any(boxes[:, 3] <= boxes[:, 1]):
        boxes = xywh_to_xyxy(boxes)

    boxes[:, [0, 2]] = (boxes[:, [0, 2]] - preprocess_info.pad_left) / preprocess_info.scale
    boxes[:, [1, 3]] = (boxes[:, [1, 3]] - preprocess_info.pad_top) / preprocess_info.scale

    height, width = image_shape[:2]
    boxes[:, [0, 2]] = boxes[:, [0, 2]].clip(0, width - 1)
    boxes[:, [1, 3]] = boxes[:, [1, 3]].clip(0, height - 1)

    nms_boxes = []
    for box in boxes:
        x1, y1, x2, y2 = box
        nms_boxes.append([int(x1), int(y1), int(x2 - x1), int(y2 - y1)])

    indices = cv2.dnn.NMSBoxes(nms_boxes, scores.tolist(), conf_threshold, iou_threshold)
    if len(indices) == 0:
        return []

    results = []
    for index in np.array(indices).reshape(-1):
        x, y, w, h = nms_boxes[index]
        results.append(
            {
                "box": (x, y, x + w, y + h),
                "score": float(scores[index]),
                "class_id": int(class_ids[index]),
            }
        )
    return results

