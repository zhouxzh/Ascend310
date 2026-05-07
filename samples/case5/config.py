"""
Case 5: Smart Data Acquisition — multi-motor condition monitoring.

Configuration constants for sensors, fault classes, model paths,
and UART communication with STM32.
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE_DIR, "models")
DATA_DIR = os.path.join(BASE_DIR, "data")

# ---------------------------------------------------------------------------
# Motor fleet
# ---------------------------------------------------------------------------
NUM_MOTORS = 4
MOTOR_NAMES = ["电机1", "电机2", "电机3", "电机4"]

# Sensor thresholds per motor
TEMP_WARN_THRESHOLD = 65.0     # degrees C
TEMP_CRITICAL_THRESHOLD = 80.0
CURRENT_WARN_THRESHOLD = 2.0   # amps
CURRENT_CRITICAL_THRESHOLD = 3.5
RPM_MIN = 100                  # stall detection
RPM_MAX = 6000

# ---------------------------------------------------------------------------
# Motor fault classes (NPU: vibration spectrogram → fault type)
# ---------------------------------------------------------------------------
FAULT_CLASSES = [
    {"id": 0, "en": "normal", "cn": "正常运行",
     "desc": "振动频谱均匀，各频率分量正常"},
    {"id": 1, "en": "bearing_wear", "cn": "轴承磨损",
     "desc": "高频分量明显增加，可能伴有宽带噪声"},
    {"id": 2, "en": "unbalance", "cn": "动平衡不良",
     "desc": "1× 转速频率处出现显著峰值"},
    {"id": 3, "en": "misalignment", "cn": "对中不良",
     "desc": "2× 转速频率处出现显著峰值，轴向振动增大"},
    {"id": 4, "en": "looseness", "cn": "机械松动",
     "desc": "多次谐波分量 + 基底噪声整体抬高"},
]
NUM_FAULT_CLASSES = len(FAULT_CLASSES)

# ---------------------------------------------------------------------------
# Mel-spectrogram parameters (vibration → image for NPU)
# ---------------------------------------------------------------------------
SAMPLE_RATE = 5000          # FPGA sampling rate (Hz)
FFT_WINDOW = 256            # samples per FFT frame
HOP_LENGTH = 64             # samples between frames
N_MELS = 128                # mel filterbank bands
SPEC_TIME_STEPS = 128       # time steps in output spectrogram
SPEC_SIZE = 128             # final spectrogram size (SPEC_SIZE × SPEC_SIZE)

# EfficientNet-B0 input
IMAGE_SIZE = 224

# ---------------------------------------------------------------------------
# Model paths
# ---------------------------------------------------------------------------
ONNX_MODEL_PATH = os.path.join(MODEL_DIR, "efficientnet_b0_fault.onnx")
OM_MODEL_PATH = os.path.join(MODEL_DIR, "efficientnet_b0_fault.om")
PTH_MODEL_PATH = os.path.join(MODEL_DIR, "efficientnet_b0_fault.pth")

# ImageNet normalization (same as all other cases)
IMAGENET_MEAN = [0.485, 0.456, 0.406]
IMAGENET_STD = [0.229, 0.224, 0.225]

# ---------------------------------------------------------------------------
# Anomaly detection
# ---------------------------------------------------------------------------
WINDOW_SIZE = 30            # sliding window for trend analysis (samples)
SIGMA_THRESHOLD = 3.0       # 3-sigma rule for outlier detection

# ---------------------------------------------------------------------------
# UART / STM32 communication
# ---------------------------------------------------------------------------
UART_PORT = "/dev/ttyUSB0"
UART_BAUDRATE = 115200
UART_TIMEOUT = 1.0           # seconds

# ---------------------------------------------------------------------------
# Data logging
# ---------------------------------------------------------------------------
CSV_LOG_PATH = os.path.join(DATA_DIR, "motor_data.csv")
LOG_INTERVAL = 1.0            # seconds between log writes

# NPU
NPU_DEVICE_ID = 0
