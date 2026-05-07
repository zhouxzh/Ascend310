"""
Case 4: Smart Palmprint Recognition — configuration constants.
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE_DIR, "models")
DATA_DIR = os.path.join(BASE_DIR, "data")

# ---------------------------------------------------------------------------
# Image / model
# ---------------------------------------------------------------------------

IMAGE_SIZE = 224
FEATURE_DIM = 1280  # GhostNet 1.0x output dim

IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]

PTH_MODEL_PATH = os.path.join(MODEL_DIR, "ghostnet_palmprint.pth")
ONNX_MODEL_PATH = os.path.join(MODEL_DIR, "ghostnet_palmprint.onnx")
OM_MODEL_PATH = os.path.join(MODEL_DIR, "ghostnet_palmprint.om")

# ---------------------------------------------------------------------------
# Palmprint preprocessing
# ---------------------------------------------------------------------------

ROI_SIZE = 224
CLAHE_CLIP_LIMIT = 2.0
CLAHE_GRID_SIZE = (8, 8)
LAPLACIAN_BLUR_THRESHOLD = 100.0  # below this → re-capture

# ---------------------------------------------------------------------------
# FAISS index
# ---------------------------------------------------------------------------

FAISS_INDEX_PATH = os.path.join(DATA_DIR, "palm_index.faiss")
METADATA_PATH = os.path.join(DATA_DIR, "palm_metadata.json")
VERIFICATION_THRESHOLD = 0.75  # cosine similarity
TOP_K_RESULTS = 5

# ---------------------------------------------------------------------------
# NPU
# ---------------------------------------------------------------------------

NPU_DEVICE_ID = 0

# ---------------------------------------------------------------------------
# Training defaults
# ---------------------------------------------------------------------------

BATCH_SIZE = 64
NUM_EPOCHS = 60
LEARNING_RATE = 1e-3
CONTRASTIVE_MARGIN = 1.0
TRAIN_SPLIT = 0.85
