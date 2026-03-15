import numpy as np
from scipy.optimize import linear_sum_assignment

from .kalman_filter import KalmanFilter


class Track:
    """表示单个跟踪目标。"""

    def __init__(self, track_id, initial_detection, trail_size=30):
        detection = self._normalize_detection(initial_detection)
        self.track_id = track_id
        self.bbox = detection[:4].copy()
        self.score = float(detection[4])
        self.class_id = int(detection[5])
        self.trail_size = trail_size
        self.trail = []
        self.kalman_filter = KalmanFilter()
        center_x, center_y = self._bbox_center(self.bbox)
        self.kalman_filter.x[0, 0] = center_x
        self.kalman_filter.x[1, 0] = center_y
        self.time_since_update = 0
        self.hits = 1
        self._append_trail_point(self.bbox)

    def predict(self):
        """预测轨迹中心位置，并回写到边界框。"""
        predicted_state = self.kalman_filter.predict()
        center_x = float(predicted_state[0, 0])
        center_y = float(predicted_state[1, 0])
        width, height = self._bbox_size(self.bbox)
        self.bbox = self._center_to_bbox(center_x, center_y, width, height)
        self.time_since_update += 1

    def update(self, detection):
        """使用新的检测框更新轨迹状态。"""
        detection = self._normalize_detection(detection)
        measurement = np.array(self._bbox_center(detection[:4]), dtype=np.float32).reshape((2, 1))
        self.kalman_filter.update(measurement)

        center_x = float(self.kalman_filter.x[0, 0])
        center_y = float(self.kalman_filter.x[1, 0])
        width, height = self._bbox_size(detection[:4])
        self.bbox = self._center_to_bbox(center_x, center_y, width, height)
        self.score = float(detection[4])
        self.class_id = int(detection[5])
        self.time_since_update = 0
        self.hits += 1
        self._append_trail_point(self.bbox)

    @staticmethod
    def _normalize_detection(detection):
        array = np.asarray(detection, dtype=np.float32).reshape(-1)
        if array.size < 5:
            raise ValueError("Detection must contain at least [x1, y1, x2, y2, score].")
        if array.size == 5:
            array = np.concatenate([array, np.array([-1.0], dtype=np.float32)])
        return array

    @staticmethod
    def _bbox_center(bbox):
        x1, y1, x2, y2 = bbox[:4]
        return (float(x1 + x2) / 2.0, float(y1 + y2) / 2.0)

    @staticmethod
    def _bbox_size(bbox):
        x1, y1, x2, y2 = bbox[:4]
        width = max(float(x2 - x1), 1.0)
        height = max(float(y2 - y1), 1.0)
        return width, height

    @staticmethod
    def _center_to_bbox(center_x, center_y, width, height):
        half_width = width / 2.0
        half_height = height / 2.0
        return np.array(
            [
                center_x - half_width,
                center_y - half_height,
                center_x + half_width,
                center_y + half_height,
            ],
            dtype=np.float32,
        )

    def _append_trail_point(self, bbox):
        center = self._bbox_center(bbox)
        self.trail.append(center)
        if len(self.trail) > self.trail_size:
            self.trail = self.trail[-self.trail_size :]

class DeepSORT:
    """一个基于 IOU 和简单卡尔曼预测的轻量级多目标跟踪器。"""

    def __init__(self, max_age=30, min_hits=3, iou_threshold=0.3, trail_size=30):
        self.max_age = max_age
        self.min_hits = min_hits
        self.iou_threshold = iou_threshold
        self.trail_size = trail_size
        self.tracks = []
        self.next_id = 1

    def update(self, detections, frame=None):
        """使用当前帧的 SSD 检测框更新跟踪状态。"""
        detections = self._normalize_detections(detections)

        for track in self.tracks:
            track.predict()

        matched, unmatched_detections, _ = self._associate(detections)

        for track_idx, detection_idx in matched:
            self.tracks[track_idx].update(detections[detection_idx])

        for detection_idx in unmatched_detections:
            self._create_track(detections[detection_idx])

        self.tracks = [track for track in self.tracks if track.time_since_update <= self.max_age]
        return [track for track in self.tracks if track.time_since_update == 0 and track.hits >= self.min_hits]

    def _create_track(self, detection):
        """根据新的检测结果创建轨迹。"""
        new_track = Track(self.next_id, detection, trail_size=self.trail_size)
        self.tracks.append(new_track)
        self.next_id += 1

    def _associate(self, detections):
        """使用 IOU 将检测结果与现有轨迹进行关联。"""
        if not self.tracks:
            return [], list(range(len(detections))), []

        if len(detections) == 0:
            return [], [], list(range(len(self.tracks)))

        iou_matrix = self._calculate_iou_matrix(detections)
        matched_indices = self._linear_assignment(iou_matrix)

        matched = []
        unmatched_detections = set(range(len(detections)))
        unmatched_tracks = set(range(len(self.tracks)))

        for track_idx, detection_idx in matched_indices:
            if iou_matrix[track_idx, detection_idx] < self.iou_threshold:
                continue

            track = self.tracks[track_idx]
            detection = detections[detection_idx]
            if not self._is_class_compatible(track.class_id, int(detection[5])):
                continue

            matched.append((track_idx, detection_idx))
            unmatched_tracks.discard(track_idx)
            unmatched_detections.discard(detection_idx)

        return matched, sorted(unmatched_detections), sorted(unmatched_tracks)

    def _calculate_iou_matrix(self, detections):
        """计算轨迹和检测之间的 IOU 矩阵。"""
        num_tracks = len(self.tracks)
        num_detections = len(detections)
        iou_matrix = np.zeros((num_tracks, num_detections), dtype=np.float32)

        for t, track in enumerate(self.tracks):
            for d, det in enumerate(detections):
                iou_matrix[t, d] = self._iou(track.bbox, det)
        return iou_matrix

    def _iou(self, boxA, boxB):
        """计算两个边界框的交并比。"""
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2])
        yB = min(boxA[3], boxB[3])
        interArea = max(0, xB - xA) * max(0, yB - yA)
        boxAArea = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
        boxBArea = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
        denominator = float(boxAArea + boxBArea - interArea)
        if denominator <= 0:
            return 0.0
        iou = interArea / denominator
        return iou

    def _linear_assignment(self, cost_matrix):
        """使用 scipy 的匈牙利算法完成检测框与轨迹的线性分配。"""
        if cost_matrix.size == 0:
            return np.empty((0, 2), dtype=np.int64)

        row_ind, col_ind = linear_sum_assignment(-cost_matrix)
        return np.array(list(zip(row_ind, col_ind)), dtype=np.int64)

    @staticmethod
    def _is_class_compatible(track_class_id, detection_class_id):
        if track_class_id == -1 or detection_class_id == -1:
            return True
        return track_class_id == detection_class_id

    @staticmethod
    def _normalize_detections(detections):
        if detections is None:
            return np.empty((0, 6), dtype=np.float32)

        array = np.asarray(detections, dtype=np.float32)
        if array.size == 0:
            return np.empty((0, 6), dtype=np.float32)
        if array.ndim == 1:
            array = array.reshape(1, -1)
        if array.shape[1] < 5:
            raise ValueError("Detections must contain at least [x1, y1, x2, y2, score].")
        if array.shape[1] == 5:
            class_ids = np.full((array.shape[0], 1), -1.0, dtype=np.float32)
            array = np.concatenate([array, class_ids], axis=1)
        return array