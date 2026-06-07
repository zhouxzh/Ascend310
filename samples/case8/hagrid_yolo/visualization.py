import cv2


def draw_detections(image, detections, labels):
    for detection in detections:
        x1, y1, x2, y2 = detection["box"]
        score = detection["score"]
        class_id = detection["class_id"]
        label = labels[class_id] if 0 <= class_id < len(labels) else str(class_id)
        text = f"{label} {score:.2f}"

        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 220, 0), 2)
        text_size, baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
        text_y = max(y1, text_size[1] + baseline + 4)
        cv2.rectangle(
            image,
            (x1, text_y - text_size[1] - baseline - 4),
            (x1 + text_size[0] + 6, text_y + baseline - 2),
            (0, 220, 0),
            -1,
        )
        cv2.putText(image, text, (x1 + 3, text_y - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 0), 2)

