from __future__ import annotations

from pathlib import Path

from .config import DEFAULT_BACKBONE, DEFAULT_MODEL_DIRS


def model_filename(backbone: str, suffix: str) -> str:
    return f"ssd320_{backbone}.{suffix}"


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
