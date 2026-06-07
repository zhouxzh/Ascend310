import time

from .postprocess import decode_detections
from .preprocess import preprocess_image


class YoloDetector:
    def __init__(self, backend, imgsz=640, conf=0.25, iou=0.45):
        self.backend = backend
        self.imgsz = imgsz
        self.conf = conf
        self.iou = iou

    def infer_frame(self, frame):
        tensor, preprocess_info = preprocess_image(frame, self.imgsz)
        start_t = time.time()
        outputs = self.backend.infer(tensor)
        latency_ms = (time.time() - start_t) * 1000.0
        if not outputs:
            return [], latency_ms
        detections = decode_detections(outputs[0], frame.shape, preprocess_info, self.conf, self.iou)
        return detections, latency_ms

