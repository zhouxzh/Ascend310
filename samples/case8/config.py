"""
Case 8: Gesture Recognition — configuration constants.
"""

# ---------------------------------------------------------------------------
# Gesture classes (10)
# Index must match training dataset label order.
# ---------------------------------------------------------------------------

GESTURE_CLASSES = [
    {"id": 0, "en": "call",        "cn": "打电话"},
    {"id": 1, "en": "dislike",     "cn": "不喜欢"},
    {"id": 2, "en": "fist",        "cn": "握拳"},
    {"id": 3, "en": "like",        "cn": "点赞"},
    {"id": 4, "en": "ok",          "cn": "OK"},
    {"id": 5, "en": "palm",        "cn": "手掌"},
    {"id": 6, "en": "peace",       "cn": "剪刀手"},
    {"id": 7, "en": "rock",        "cn": "摇滚"},
    {"id": 8, "en": "stop",        "cn": "停止"},
    {"id": 9, "en": "no_gesture",  "cn": "无手势"},
]

NUM_CLASSES = len(GESTURE_CLASSES)

# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------

IMAGE_SIZE = 224          # MobileNetV3 input size (square)
MODEL_NAME = "mobilenet_v3_small"

# File paths (relative to project root)
MODEL_DIR = "models"
OM_MODEL_PATH = f"{MODEL_DIR}/gesture_mobilenetv3.om"
ONNX_MODEL_PATH = f"{MODEL_DIR}/gesture_mobilenetv3.onnx"
PTH_MODEL_PATH = f"{MODEL_DIR}/gesture_mobilenetv3.pth"

# ImageNet normalization (used by torchvision pre-trained models)
IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]

# ---------------------------------------------------------------------------
# Inference
# ---------------------------------------------------------------------------

CONFIDENCE_THRESHOLD = 0.70   # minimum confidence to report a prediction
NPU_DEVICE_ID = 0

# ---------------------------------------------------------------------------
# HaGRID dataset (10-class subset)
# ---------------------------------------------------------------------------

HAGRID_GESTURES = [
    "call", "dislike", "fist", "like", "ok",
    "palm", "peace", "rock", "stop", "no_gesture",
]

# HaGRID repo URL (Git LFS)
HAGRID_REPO = "https://github.com/hukenovs/hagrid"

# Training defaults
BATCH_SIZE = 32
NUM_EPOCHS = 30
LEARNING_RATE = 1e-3
TRAIN_SPLIT = 0.85
