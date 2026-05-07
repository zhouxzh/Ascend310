"""
Case 7: Smart Album — configuration constants.
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE_DIR, "models")
DATA_DIR = os.path.join(BASE_DIR, "data")
PHOTO_DIR = os.path.join(BASE_DIR, "photos")

# ResNet50 feature extraction
FEATURE_DIM = 2048
IMAGE_SIZE = 224
ONNX_MODEL_PATH = os.path.join(MODEL_DIR, "resnet50_feature.onnx")
OM_MODEL_PATH = os.path.join(MODEL_DIR, "resnet50_feature.om")

# ImageNet normalization (standard for torchvision ResNet50)
IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]

# FAISS
FAISS_INDEX_PATH = os.path.join(DATA_DIR, "photo_index.faiss")
METADATA_PATH = os.path.join(DATA_DIR, "photo_metadata.json")

# Search
TOP_K_RESULTS = 12

# Face detection (OpenCV Haar Cascade)
HAAR_SCALE_FACTOR = 1.1
HAAR_MIN_NEIGHBORS = 5

# NPU
NPU_DEVICE_ID = 0
