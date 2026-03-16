import numpy as np
from scipy.optimize import linear_sum_assignment

from .kalman_filter import KalmanFilter


class Track:
    """表示单个跟踪目标。"""

    def __init__(self, track_id, initial_detection, trail_size=30, size_smoothing=0.8, score_smoothing=0.7):
        detection = self._normalize_detection(initial_detection)
        self.track_id = track_id
        self.bbox = detection[:4].copy()
        self.score = float(detection[4])
        self.class_id = int(detection[5])
        self.trail_size = trail_size
        self.trail = []
        self.size_smoothing = size_smoothing
        self.score_smoothing = score_smoothing
        self.kalman_filter = KalmanFilter()
        self.class_scores = {}
        center_x, center_y = self._bbox_center(self.bbox)
        self.width, self.height = self._bbox_size(self.bbox)
        self.kalman_filter.initialize(center_x, center_y)
        self.time_since_update = 0
        self.hits = 1
        self._update_class_state(self.class_id, self.score)
        self._append_trail_point(self.bbox)

    def predict(self):
        """预测轨迹中心位置，并回写到边界框。"""
        predicted_state = self.kalman_filter.predict()
        center_x = float(predicted_state[0, 0])
        center_y = float(predicted_state[1, 0])
        self.bbox = self._center_to_bbox(center_x, center_y, self.width, self.height)
        self.time_since_update += 1

    def update(self, detection):
        """使用新的检测框更新轨迹状态。"""
        detection = self._normalize_detection(detection)
        measurement = np.array(self._bbox_center(detection[:4]), dtype=np.float32).reshape((2, 1))
        self.kalman_filter.update(measurement)

        center_x = float(self.kalman_filter.x[0, 0])
        center_y = float(self.kalman_filter.x[1, 0])
        detection_width, detection_height = self._bbox_size(detection[:4])
        self.width = self.size_smoothing * self.width + (1.0 - self.size_smoothing) * detection_width
        self.height = self.size_smoothing * self.height + (1.0 - self.size_smoothing) * detection_height
        self.bbox = self._center_to_bbox(center_x, center_y, self.width, self.height)
        self.score = self.score_smoothing * self.score + (1.0 - self.score_smoothing) * float(detection[4])
        self._update_class_state(int(detection[5]), float(detection[4]))
        self.time_since_update = 0
        self.hits += 1
        self._append_trail_point(self.bbox)

    def _update_class_state(self, class_id, score):
        self.class_scores[class_id] = self.class_scores.get(class_id, 0.0) + max(score, 0.0)
        self.class_id = max(self.class_scores, key=self.class_scores.get)

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

    def __init__(
        self,
        max_age=30,
        min_hits=3,
        iou_threshold=0.3,
        trail_size=30,
        center_distance_threshold=1.8,
        size_smoothing=0.8,
        score_smoothing=0.7,
    ):
        self.max_age = max_age
        self.min_hits = min_hits
        self.iou_threshold = iou_threshold
        self.trail_size = trail_size
        self.center_distance_threshold = center_distance_threshold
        self.size_smoothing = size_smoothing
        self.score_smoothing = score_smoothing
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
        new_track = Track(
            self.next_id,
            detection,
            trail_size=self.trail_size,
            size_smoothing=self.size_smoothing,
            score_smoothing=self.score_smoothing,
        )
        self.tracks.append(new_track)
        self.next_id += 1

    def _associate(self, detections):
        """使用 IOU 将检测结果与现有轨迹进行关联。"""
        if not self.tracks:
            return [], list(range(len(detections))), []

        if len(detections) == 0:
            return [], [], list(range(len(self.tracks)))

        iou_matrix = self._calculate_iou_matrix(detections)
        class_mask = self._calculate_class_compatibility_matrix(detections)
        assignment_scores = np.where(class_mask, iou_matrix, -1.0)
        matched_indices = self._linear_assignment(assignment_scores)

        matched = []
        unmatched_detections = set(range(len(detections)))
        unmatched_tracks = set(range(len(self.tracks)))

        for track_idx, detection_idx in matched_indices:
            if assignment_scores[track_idx, detection_idx] < self.iou_threshold:
                continue

            matched.append((track_idx, detection_idx))
            unmatched_tracks.discard(track_idx)
            unmatched_detections.discard(detection_idx)

        if unmatched_tracks and unmatched_detections:
            fallback_matches = self._match_by_center_distance(
                detections,
                sorted(unmatched_tracks),
                sorted(unmatched_detections),
            )
            for track_idx, detection_idx in fallback_matches:
                matched.append((track_idx, detection_idx))
                unmatched_tracks.discard(track_idx)
                unmatched_detections.discard(detection_idx)

        return matched, sorted(unmatched_detections), sorted(unmatched_tracks)

    def _calculate_iou_matrix(self, detections):
        """计算轨迹和检测之间的 IOU 矩阵。"""
        num_tracks = len(self.tracks)
        num_detections = len(detections)
        if num_tracks == 0 or num_detections == 0:
            return np.zeros((num_tracks, num_detections), dtype=np.float32)

        track_boxes = np.asarray([track.bbox[:4] for track in self.tracks], dtype=np.float32)
        detection_boxes = np.asarray(detections[:, :4], dtype=np.float32)

        top_left = np.maximum(track_boxes[:, None, :2], detection_boxes[None, :, :2])
        bottom_right = np.minimum(track_boxes[:, None, 2:], detection_boxes[None, :, 2:])
        overlap_size = np.clip(bottom_right - top_left, 0.0, None)
        intersection = overlap_size[..., 0] * overlap_size[..., 1]

        track_size = np.clip(track_boxes[:, 2:] - track_boxes[:, :2], 0.0, None)
        detection_size = np.clip(detection_boxes[:, 2:] - detection_boxes[:, :2], 0.0, None)
        track_area = track_size[:, 0] * track_size[:, 1]
        detection_area = detection_size[:, 0] * detection_size[:, 1]

        union = track_area[:, None] + detection_area[None, :] - intersection
        return intersection / np.maximum(union, 1e-10)

    def _calculate_class_compatibility_matrix(self, detections):
        track_classes = np.asarray([track.class_id for track in self.tracks], dtype=np.int64)
        detection_classes = detections[:, 5].astype(np.int64)
        return (
            (track_classes[:, None] == -1)
            | (detection_classes[None, :] == -1)
            | (track_classes[:, None] == detection_classes[None, :])
        )

    def _match_by_center_distance(self, detections, unmatched_tracks, unmatched_detections):
        if not unmatched_tracks or not unmatched_detections:
            return []

        track_indices = np.asarray(unmatched_tracks, dtype=np.int64)
        detection_indices = np.asarray(unmatched_detections, dtype=np.int64)

        track_boxes = np.asarray([self.tracks[index].bbox[:4] for index in track_indices], dtype=np.float32)
        detection_boxes = np.asarray(detections[detection_indices, :4], dtype=np.float32)
        track_classes = np.asarray([self.tracks[index].class_id for index in track_indices], dtype=np.int64)
        detection_classes = detections[detection_indices, 5].astype(np.int64)

        track_centers = 0.5 * (track_boxes[:, :2] + track_boxes[:, 2:])
        detection_centers = 0.5 * (detection_boxes[:, :2] + detection_boxes[:, 2:])
        center_distance = np.linalg.norm(track_centers[:, None, :] - detection_centers[None, :, :], axis=2)

        track_diagonal = np.linalg.norm(np.clip(track_boxes[:, 2:] - track_boxes[:, :2], 1.0, None), axis=1)
        detection_diagonal = np.linalg.norm(np.clip(detection_boxes[:, 2:] - detection_boxes[:, :2], 1.0, None), axis=1)
        reference_scale = np.maximum(track_diagonal[:, None], detection_diagonal[None, :])
        normalized_distance = center_distance / np.maximum(reference_scale, 1.0)

        class_mask = (
            (track_classes[:, None] == -1)
            | (detection_classes[None, :] == -1)
            | (track_classes[:, None] == detection_classes[None, :])
        )
        valid_mask = class_mask & (normalized_distance <= self.center_distance_threshold)
        if not np.any(valid_mask):
            return []

        distance_cost = np.where(valid_mask, normalized_distance, self.center_distance_threshold + 1.0)
        row_ind, col_ind = linear_sum_assignment(distance_cost)

        matched = []
        for row, col in zip(row_ind, col_ind):
            if distance_cost[row, col] > self.center_distance_threshold:
                continue
            matched.append((int(track_indices[row]), int(detection_indices[col])))
        return matched

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