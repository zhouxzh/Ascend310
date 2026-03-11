import numpy as np

class KalmanFilter:
    def __init__(self):
        # 状态转移矩阵
        self.F = np.array([[1, 0, 1, 0], [0, 1, 0, 1], [0, 0, 1, 0], [0, 0, 0, 1]])
        # 观测矩阵
        self.H = np.array([[1, 0, 0, 0], [0, 1, 0, 0]])
        # 过程噪声协方差
        self.Q = np.eye(4) * 0.05
        # 观测噪声协方差
        self.R = np.eye(2) * 0.5
        # 状态向量 [x, y, vx, vy]
        self.x = np.zeros((4, 1))
        # 状态协方差矩阵
        self.P = np.eye(4)

    def predict(self):
        self.x = np.dot(self.F, self.x)
        self.P = np.dot(np.dot(self.F, self.P), self.F.T) + self.Q
        return self.x

    def update(self, z):
        y = z - np.dot(self.H, self.x)
        S = np.dot(self.H, np.dot(self.P, self.H.T)) + self.R
        K = np.dot(np.dot(self.P, self.H.T), np.linalg.inv(S))
        self.x = self.x + np.dot(K, y)
        self.P = self.P - np.dot(np.dot(K, self.H), self.P)