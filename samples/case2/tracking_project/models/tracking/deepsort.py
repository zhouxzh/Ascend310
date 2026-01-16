import numpy as np
from .utils.kalman_filter import KalmanFilter

class Track:
    """
    表示单个跟踪目标的类。

    Attributes:
        track_id (int): 轨迹的唯一ID。
        bbox (list): 目标的边界框 [x1, y1, x2, y2]。
        kalman_filter (KalmanFilter): 用于预测目标状态的卡尔曼滤波器。
        time_since_update (int): 自上次成功更新以来的帧数。
        hits (int): 连续匹配的次数。
    """
    def __init__(self, track_id, initial_bbox):
        """
        初始化一个轨迹对象。

        Args:
            track_id (int): 轨迹的ID。
            initial_bbox (list): 初始边界框。
        """
        self.track_id = track_id
        self.bbox = initial_bbox
        self.kalman_filter = KalmanFilter()
        self.time_since_update = 0
        self.hits = 1

    def predict(self):
        """
        使用卡尔曼滤波器预测目标的下一个状态。
        """
        self.kalman_filter.predict()
        self.time_since_update += 1

    def update(self, bbox):
        """
        使用新的检测结果更新轨迹状态。

        Args:
            bbox (list): 新的边界框。
        """
        self.kalman_filter.update(self._bbox_to_z(bbox))
        self.bbox = bbox
        self.time_since_update = 0
        self.hits += 1

    def _bbox_to_z(self, bbox):
        """
        将边界框 [x1, y1, x2, y2] 转换为卡尔曼滤波器测量值 [center_x, center_y, aspect_ratio, height]。
        """
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        x = bbox[0] + w / 2.
        y = bbox[1] + h / 2.
        a = w / h
        return np.array([x, y, a, h]).reshape((4, 1))

class DeepSORT:
    """
    一个简化的DeepSORT跟踪器实现。

    Attributes:
        max_age (int): 在删除轨迹之前，允许的最大未匹配帧数。
        min_hits (int): 在将轨迹确认为活动状态之前，所需的最小连续匹配次数。
        tracks (list): 当前跟踪的所有轨迹列表。
        next_id (int): 用于分配给新轨迹的下一个ID。
    """
    def __init__(self, max_age=30, min_hits=3):
        """
        初始化DeepSORT跟踪器。

        Args:
            max_age (int): 最大允许的未匹配帧数。
            min_hits (int): 最小连续匹配次数。
        """
        self.max_age = max_age
        self.min_hits = min_hits
        self.tracks = []
        self.next_id = 1

    def update(self, detections, frame):
        """
        更新跟踪器状态。

        Args:
            detections (np.ndarray): 当前帧的检测结果，格式为 [[x1, y1, x2, y2, score], ...]。
            frame (np.ndarray): 当前帧的图像。

        Returns:
            list: 当前帧的活动轨迹列表。
        """
        # 1. 预测现有轨迹的下一位置
        for track in self.tracks:
            track.predict()

        # 2. 关联检测与轨迹
        matched, unmatched_detections, unmatched_tracks = self._associate(detections)

        # 3. 更新匹配的轨迹
        for track_idx, detection_idx in matched:
            self.tracks[track_idx].update(detections[detection_idx])

        # 4. 为未匹配的检测创建新轨迹
        for detection_idx in unmatched_detections:
            self._create_track(detections[detection_idx])

        # 5. 移除长时间未更新的轨迹
        self.tracks = [t for t in self.tracks if t.time_since_update <= self.max_age]

        # 6. 返回达到最小匹配次数的活动轨迹
        active_tracks = [t for t in self.tracks if t.hits >= self.min_hits]
        return active_tracks

    def _create_track(self, detection):
        """
        根据新的检测结果创建一个新轨迹。
        """
        new_track = Track(self.next_id, detection)
        self.tracks.append(new_track)
        self.next_id += 1

    def _associate(self, detections):
        """
        将检测结果与现有轨迹进行关联。
        (这是一个简化的关联实现，仅使用IOU。实际DeepSORT使用更复杂的级联匹配和外观特征)。
        """
        if not self.tracks:
            return [], list(range(len(detections))), []

        # 计算IOU矩阵
        iou_matrix = self._calculate_iou_matrix(detections)
        
        # 使用匈牙利算法进行线性分配
        matched_indices = self._linear_assignment(iou_matrix)

        unmatched_detections = [d for d in range(len(detections)) 
                                if d not in matched_indices[:, 1]]
        unmatched_tracks = [t for t in range(len(self.tracks)) 
                              if t not in matched_indices[:, 0]]

        return matched_indices, unmatched_detections, unmatched_tracks

    def _calculate_iou_matrix(self, detections):
        """
        计算轨迹和检测之间的IOU矩阵。
        """
        num_tracks = len(self.tracks)
        num_detections = len(detections)
        iou_matrix = np.zeros((num_tracks, num_detections))

        for t, track in enumerate(self.tracks):
            for d, det in enumerate(detections):
                iou_matrix[t, d] = self._iou(track.bbox, det)
        return iou_matrix

    def _iou(self, boxA, boxB):
        """
        计算两个边界框的交并比 (IOU)。
        """
        xA = max(boxA[0], boxB[0])
        yA = max(boxA[1], boxB[1])
        xB = min(boxA[2], boxB[2])
        yB = min(boxA[3], boxB[3])
        interArea = max(0, xB - xA) * max(0, yB - yA)
        boxAArea = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
        boxBArea = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
        iou = interArea / float(boxAArea + boxBArea - interArea)
        return iou

    def _linear_assignment(self, cost_matrix):
        """
        使用匈牙利算法解决线性分配问题。
        """
        try:
            from scipy.optimize import linear_sum_assignment
            # 我们希望最大化IOU，所以传递负的代价矩阵
            row_ind, col_ind = linear_sum_assignment(-cost_matrix) 
            return np.array(list(zip(row_ind, col_ind)))
        except ImportError:
            print("Scipy未安装，无法进行最佳匹配。请运行 'pip install scipy'")
            return np.empty((0, 2))