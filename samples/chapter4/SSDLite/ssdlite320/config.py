from __future__ import annotations

from pathlib import Path

DEFAULT_IMAGE_SIZE = 320
DEFAULT_DATASET_NAME = "detection-datasets/coco"
DEFAULT_VAL_DATA_FILES = "data/val-*.parquet"
DEFAULT_MODEL_REPO_ID = "zhouxzh/SSDLite320"
DEFAULT_BACKBONE = "mobilenetv4_conv_small"

PACKAGE_DIR = Path(__file__).resolve().parent
CASE_DIR = PACKAGE_DIR.parent
DEFAULT_MODEL_DIRS = (
    CASE_DIR / "weights",
    CASE_DIR / "models",
    CASE_DIR / "logs",
)


def model_filename(backbone: str, suffix: str) -> str:
    return f"ssd320_{backbone}.{suffix}"


def backbone_from_model_path(model_path: str | Path) -> str:
    stem = Path(model_path).stem
    for prefix in ("ssd320_", "ssd_"):
        if stem.startswith(prefix):
            return stem[len(prefix) :]
    return stem


def list_model_paths(
    suffix: str,
    model_dirs=DEFAULT_MODEL_DIRS,
) -> list[Path]:
    models = []
    seen_backbones = set()

    for model_dir in model_dirs:
        if not model_dir.exists():
            continue
        for path in sorted(model_dir.glob(f"*.{suffix}")):
            if not path.stem.startswith(("ssd320_", "ssd_")):
                continue
            backbone = backbone_from_model_path(path)
            if backbone in seen_backbones:
                continue
            seen_backbones.add(backbone)
            models.append(path)

    return models


def resolve_model_path(
    explicit_path: str | None,
    backbone: str = DEFAULT_BACKBONE,
    suffix: str = "onnx",
    model_dirs=DEFAULT_MODEL_DIRS,
) -> Path:
    if explicit_path:
        path = Path(explicit_path).expanduser()
        if path.exists():
            return path
        raise FileNotFoundError(f"Model file does not exist: {path}")

    candidate_names = [
        model_filename(backbone, suffix),
        f"ssd_{backbone}.{suffix}",
    ]
    for model_dir in model_dirs:
        for name in candidate_names:
            candidate = model_dir / name
            if candidate.exists():
                return candidate

    searched = ", ".join(str(model_dir / candidate_names[0]) for model_dir in model_dirs)
    raise FileNotFoundError(f"Cannot find {candidate_names[0]}. Searched: {searched}")
