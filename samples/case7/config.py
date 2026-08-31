"""
Case 7: Smart Album — configuration constants.
"""

import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODEL_DIR = os.path.join(BASE_DIR, "models")
DATA_DIR = os.path.join(BASE_DIR, "data")


def _is_within(path: str, root: str) -> bool:
    try:
        return os.path.commonpath((os.path.realpath(path), os.path.realpath(root))) == os.path.realpath(root)
    except ValueError:
        return False


# ``PHOTO_DIR`` is the release/shared photo root kept for datasets and legacy
# records.  It is intentionally not the destination for ordinary uploads.
# New user photographs live below a managed directory in the operating
# system user's Pictures folder so a release archive cannot accidentally
# contain personal originals.
PHOTO_DIR = os.path.join(BASE_DIR, "photos")
_DEFAULT_PICTURES_DIR = os.path.join(os.path.expanduser("~"), "Pictures")
PHOTO_LIBRARY_DIR = os.path.realpath(
    os.path.expanduser(
        os.environ.get(
            "SMART_ALBUM_PHOTO_DIR",
            os.path.join(_DEFAULT_PICTURES_DIR, "ai-album"),
        )
    )
)
IMPORT_DIR = os.path.realpath(
    os.path.expanduser(
        os.environ.get("SMART_ALBUM_IMPORT_DIR", os.path.join(PHOTO_LIBRARY_DIR, "imports"))
    )
)
UPLOAD_TMP_DIR = os.path.realpath(
    os.path.expanduser(
        os.environ.get("SMART_ALBUM_UPLOAD_TMP_DIR", os.path.join(PHOTO_LIBRARY_DIR, ".upload-tmp"))
    )
)
if _is_within(PHOTO_LIBRARY_DIR, BASE_DIR):
    raise RuntimeError("SMART_ALBUM_PHOTO_DIR must be outside the Case7 release directory")
if not _is_within(IMPORT_DIR, PHOTO_LIBRARY_DIR):
    raise RuntimeError("SMART_ALBUM_IMPORT_DIR must be inside SMART_ALBUM_PHOTO_DIR")
if not _is_within(UPLOAD_TMP_DIR, PHOTO_LIBRARY_DIR):
    raise RuntimeError("SMART_ALBUM_UPLOAD_TMP_DIR must be inside SMART_ALBUM_PHOTO_DIR")
SECRETS_DIR = os.environ.get("SMART_ALBUM_SECRETS_DIR", os.path.join(BASE_DIR, "secrets"))

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
ALBUM_DB_PATH = os.path.join(DATA_DIR, "album.sqlite3")
INDEX_DIR = os.path.join(DATA_DIR, "indexes")

# Import and decoding safety. Uploads have no artificial byte/count cap; the
# ``None`` marker keeps imports from older integrations source-compatible.
# SMART_ALBUM_PHOTO_ROOTS uses the platform path separator (":" on Linux,
# ";" on Windows).
MAX_IMAGE_BYTES = None
MAX_IMAGE_PIXELS = 50_000_000
SUPPORTED_IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".bmp", ".webp")
# Do not add the whole ``~/Pictures`` tree: it may contain unrelated private
# images.  Only the legacy release root and our explicitly managed library
# are discoverable by the index.
_DEFAULT_ROOTS = [PHOTO_DIR, PHOTO_LIBRARY_DIR]
PHOTO_ROOTS = tuple(
    os.path.realpath(os.path.expanduser(item))
    for item in os.environ.get(
        "SMART_ALBUM_PHOTO_ROOTS", os.pathsep.join(_DEFAULT_ROOTS)
    ).split(os.pathsep)
    if item
)

# Search
TOP_K_RESULTS = 12

# Face detection (OpenCV Haar Cascade)
HAAR_SCALE_FACTOR = 1.1
HAAR_MIN_NEIGHBORS = 5

# NPU
NPU_DEVICE_ID = 0

# Waveshare 7.3-inch e-Paper HAT (E), 800x480, Spectra 6 colors. GPIO offsets
# are intentionally unset: Orange Pi AIpro revisions and device-tree overlays
# use different gpiochip/line assignments.  Set them on the board instead of
# copying Raspberry Pi BCM numbers.
EPAPER_WIDTH = 800
EPAPER_HEIGHT = 480
EPAPER_BACKEND = os.environ.get("SMART_ALBUM_EPAPER_BACKEND", "dry-run")
EPAPER_SPI_DEVICE = os.environ.get("SMART_ALBUM_EPAPER_SPI", "/dev/spidev0.0")
EPAPER_GPIOCHIP = os.environ.get("SMART_ALBUM_EPAPER_GPIOCHIP", "/dev/gpiochip0")
EPAPER_DC_LINE = os.environ.get("SMART_ALBUM_EPAPER_DC_LINE")
EPAPER_RST_LINE = os.environ.get("SMART_ALBUM_EPAPER_RST_LINE")
EPAPER_BUSY_LINE = os.environ.get("SMART_ALBUM_EPAPER_BUSY_LINE")
EPAPER_PWR_LINE = os.environ.get("SMART_ALBUM_EPAPER_PWR_LINE")
EPAPER_SPI_HZ = int(os.environ.get("SMART_ALBUM_EPAPER_SPI_HZ", "4000000"))
EPAPER_BUSY_TIMEOUT_S = float(
    os.environ.get("SMART_ALBUM_EPAPER_BUSY_TIMEOUT_S", "60")
)
EPAPER_PREVIEW_PATH = os.path.join(DATA_DIR, "epaper_preview.png")
EPAPER_FRAME_PATH = os.path.join(DATA_DIR, "epaper_frame.bin")
