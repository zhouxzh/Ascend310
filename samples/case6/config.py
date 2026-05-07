"""
Case 6: Smart Car Perception — configuration constants.
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE_DIR, "models")
DATA_DIR = os.path.join(BASE_DIR, "data")

# Driving scene classes
SCENE_CLASSES = [
    {"id": 0, "en": "highway", "cn": "高速公路", "advice": "保持车道，注意车速"},
    {"id": 1, "en": "urban", "cn": "城市道路", "advice": "注意行人和交叉口"},
    {"id": 2, "en": "intersection", "cn": "交叉路口", "advice": "减速观察"},
    {"id": 3, "en": "parking", "cn": "停车场", "advice": "低速行驶"},
    {"id": 4, "en": "tunnel", "cn": "隧道", "advice": "开启车灯"},
]
NUM_SCENES = len(SCENE_CLASSES)

# ResNet18 scene classifier
IMAGE_SIZE = 224
ONNX_MODEL_PATH = os.path.join(MODEL_DIR, "resnet18_scene.onnx")
OM_MODEL_PATH = os.path.join(MODEL_DIR, "resnet18_scene.om")
PTH_MODEL_PATH = os.path.join(MODEL_DIR, "resnet18_scene.pth")

# ImageNet normalization
IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]

# Lane detection parameters
CANNY_LOW = 50
CANNY_HIGH = 150
GAUSSIAN_KERNEL = (5, 5)
HOUGH_RHO = 2
HOUGH_THETA = 1.0
HOUGH_THRESHOLD = 50
HOUGH_MIN_LINE_LEN = 40
HOUGH_MAX_LINE_GAP = 100

# ROI as fraction of image height (keep bottom portion)
ROI_BOTTOM = 1.0
ROI_TOP = 0.6

# Lane overlay colors (BGR)
LANE_COLOR = (0, 255, 0)       # green
LANE_OVERLAY_ALPHA = 0.4

# NPU
NPU_DEVICE_ID = 0
