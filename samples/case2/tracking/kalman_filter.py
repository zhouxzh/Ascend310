import numpy as np


class KalmanFilter:
    def __init__(
        self,
        dt=1.0,
        process_noise_position=1.0,
        process_noise_velocity=0.25,
        measurement_noise=4.0,
        initial_position_variance=16.0,
        initial_velocity_variance=100.0,
    ):
        self.dt = float(dt)
        self._dtype = np.float32

        # 状态向量 [x, y, vx, vy]
        self.x = np.zeros((4, 1), dtype=self._dtype)

        # 状态转移矩阵
        self.F = np.array(
            [[1.0, 0.0, self.dt, 0.0], [0.0, 1.0, 0.0, self.dt], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]],
            dtype=self._dtype,
        )
        # 观测矩阵，只观测目标中心点
        self.H = np.array([[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]], dtype=self._dtype)

        # 过程噪声：位置和速度使用不同的先验强度
        self.Q = np.diag(
            [process_noise_position, process_noise_position, process_noise_velocity, process_noise_velocity]
        ).astype(self._dtype)
        # 观测噪声协方差
        self.R = np.eye(2, dtype=self._dtype) * np.float32(measurement_noise)

        # 初始状态协方差：速度比位置更不确定
        self.P = np.diag(
            [initial_position_variance, initial_position_variance, initial_velocity_variance, initial_velocity_variance]
        ).astype(self._dtype)
        self._identity = np.eye(4, dtype=self._dtype)

    def initialize(self, center_x, center_y):
        self.x.fill(0.0)
        self.x[0, 0] = np.float32(center_x)
        self.x[1, 0] = np.float32(center_y)

    def predict(self):
        self.x = self.F @ self.x
        self.P = self.F @ self.P @ self.F.T + self.Q
        return self.x

    def update(self, z):
        measurement = np.asarray(z, dtype=self._dtype).reshape((2, 1))
        innovation = measurement - self.H @ self.x
        innovation_covariance = self.H @ self.P @ self.H.T + self.R
        kalman_gain = np.linalg.solve(innovation_covariance.T, (self.P @ self.H.T).T).T

        self.x = self.x + kalman_gain @ innovation

        # Joseph form keeps P symmetric and numerically stable.
        correction = self._identity - kalman_gain @ self.H
        self.P = correction @ self.P @ correction.T + kalman_gain @ self.R @ kalman_gain.T
        return self.x