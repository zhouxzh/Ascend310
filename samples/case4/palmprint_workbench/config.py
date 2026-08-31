"""Runtime configuration for the palmprint recognition workbench."""

from __future__ import annotations

import os
from pathlib import Path


# ``config.py`` now lives inside the installable package.  Resolve assets from
# the explicit release root (when supplied) or the package's parent directory;
# never use the package directory itself as the project root.
_PACKAGE_DIR = Path(__file__).resolve().parent
_CONFIGURED_ROOT = os.environ.get("PALMPRINT_ROOT")
ROOT = (
    Path(_CONFIGURED_ROOT).expanduser().resolve()
    if _CONFIGURED_ROOT
    else _PACKAGE_DIR.parent.resolve()
)
MODEL_DIR = ROOT / "models"
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
TEMPLATE_DIR = DATA_DIR / "templates"
CAPTURE_DIR = DATA_DIR / "captures"
DATASET_DIR = DATA_DIR / "datasets"
REGISTRY_PATH = MODEL_DIR / "registry.json"

ROI_SIZE = 128
MIN_HAND_AREA_RATIO = 0.08
MIN_SHARPNESS = 40.0
MIN_ENROLL_SAMPLES = 3
MAX_ENROLL_SAMPLES = 5
TOP_K = 5
DEFAULT_THRESHOLD = 0.75


def _environment(name: str, default: str) -> str:
    """Read a runtime setting using the public palmprint prefix."""

    return os.environ.get(name, default)


def _integer_environment(name: str, default: str, *, minimum: int | None = None) -> int:
    value = _environment(name, default)
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer, got {value!r}") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {parsed}")
    return parsed


def _float_environment(name: str, default: str, *, minimum: float | None = None) -> float:
    value = _environment(name, default)
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number, got {value!r}") from exc
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {parsed}")
    return parsed


# HTTP evaluation is deliberately bounded. Full-dataset campaigns belong to
# ``tools.offline`` and must not occupy the interactive NPU worker indefinitely
# or grow ``reports/runs`` without limit.
JOB_TIMEOUT_SECONDS = _integer_environment(
    "PALMPRINT_JOB_TIMEOUT_SECONDS", "900", minimum=10
)
MAX_JOB_TIMEOUT_SECONDS = _integer_environment(
    "PALMPRINT_MAX_JOB_TIMEOUT_SECONDS", "3600", minimum=JOB_TIMEOUT_SECONDS
)
MAX_API_REPORT_FILES = _integer_environment(
    "PALMPRINT_MAX_API_REPORT_FILES", "120", minimum=1
)
MAX_API_REPORT_BYTES = _integer_environment(
    "PALMPRINT_MAX_API_REPORT_BYTES", str(512 * 1024 * 1024), minimum=1
)


# The production contract is intentionally explicit.  Offline CPU/EDCC tools
# may still import the compatibility constants below, but the FastAPI boundary
# must only accept this pair.
PRODUCTION_BACKEND = "npu"
PRODUCTION_PRECISION = "mixed_fp16"
RELEASE_PROFILE = _environment("PALMPRINT_PROFILE", "production").strip().lower()
MANUAL_TEST_PROFILE = "manual_test"
MANUAL_TEST_MODEL_IDS = (
    "compnet_tongji_600",
    "compnet_iitd_460",
    "compnet_rest_358",
    "compnet_xjtu_flash_200",
    "compnet_xjtu_natural_200",
)


def manual_test_enabled() -> bool:
    """Return whether the frozen manual-test release channel is active."""

    return RELEASE_PROFILE == MANUAL_TEST_PROFILE

NPU_DEVICE_ID = _integer_environment("PALMPRINT_NPU_DEVICE", "0", minimum=0)
SERVER_HOST = _environment("PALMPRINT_HOST", "0.0.0.0")
SERVER_PORT = _integer_environment("PALMPRINT_PORT", "7860", minimum=1)
# CPU_THREADS is retained as an offline compatibility setting only.  It is not
# read by the production API, which always resolves an NPU adapter.
OFFLINE_CPU_THREADS = _integer_environment("PALMPRINT_CPU_THREADS", "4", minimum=1)
CPU_THREADS = OFFLINE_CPU_THREADS
BENCHMARK_SEED = 20260814

# Board-side V4L2 camera defaults. The UI can select another detected node.
CAMERA_DEFAULT_DEVICE = _environment(
    "PALMPRINT_CAMERA_DEVICE", "/dev/video0"
)
CAMERA_DEFAULT_WIDTH = _integer_environment("PALMPRINT_CAMERA_WIDTH", "1280", minimum=1)
CAMERA_DEFAULT_HEIGHT = _integer_environment("PALMPRINT_CAMERA_HEIGHT", "720", minimum=1)
CAMERA_DEFAULT_FPS = _float_environment("PALMPRINT_CAMERA_FPS", "30", minimum=0.1)

# V4L2 drivers do not expose a portable, read-only mode query without opening
# the device.  Advertise the common USB-camera modes here and let the driver
# negotiate the requested mode when a frame is captured.  The list can be
# narrowed/extended per board without changing the UI or API contract.
_CAMERA_RESOLUTION_DEFAULTS = ("1280x720", "1920x1080", "640x480")
_CAMERA_RESOLUTION_ENV = "PALMPRINT_CAMERA_RESOLUTIONS"


def camera_resolution_options() -> tuple[str, ...]:
    """Return validated resolution choices advertised by the camera API."""

    raw = _environment(_CAMERA_RESOLUTION_ENV, "")
    values = tuple(item.strip() for item in raw.split(",") if item.strip())
    candidates = values or _CAMERA_RESOLUTION_DEFAULTS
    valid: list[str] = []
    for value in candidates:
        try:
            width_text, height_text = value.lower().split("x", 1)
            width, height = int(width_text), int(height_text)
        except (TypeError, ValueError):
            continue
        if width <= 0 or height <= 0 or width > 4096 or height > 2160:
            continue
        normalized = f"{width}x{height}"
        if normalized not in valid:
            valid.append(normalized)
    return tuple(valid or _CAMERA_RESOLUTION_DEFAULTS)


def ensure_runtime_dirs() -> None:
    for path in (MODEL_DIR, DATA_DIR, REPORT_DIR, TEMPLATE_DIR, CAPTURE_DIR, DATASET_DIR):
        path.mkdir(parents=True, exist_ok=True)
