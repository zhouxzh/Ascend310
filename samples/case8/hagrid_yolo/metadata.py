import json
from pathlib import Path


DEFAULT_IMAGE_SIZE = 640


def load_labels(model_path, labels_path=None):
    model_path = Path(model_path)
    labels_path = Path(labels_path) if labels_path else model_path.with_name(f"{model_path.stem}_labels.txt")
    if not labels_path.exists():
        raise FileNotFoundError(f"Labels file not found: {labels_path}")
    return [line.strip() for line in labels_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def resolve_imgsz(model_path, imgsz=0, fallback=DEFAULT_IMAGE_SIZE):
    if imgsz and imgsz > 0:
        return imgsz

    model_path = Path(model_path)
    metadata_path = model_path.with_name(f"{model_path.stem}_metadata.json")
    if not metadata_path.exists():
        return fallback

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata_imgsz = metadata.get("imgsz")
    if isinstance(metadata_imgsz, int) and metadata_imgsz > 0:
        return metadata_imgsz

    input_shapes = metadata.get("input_shapes") or {}
    for shape in input_shapes.values():
        if isinstance(shape, list) and len(shape) == 4:
            try:
                height = int(shape[2])
                width = int(shape[3])
            except (TypeError, ValueError):
                continue
            if height == width and height > 0:
                return height

    return fallback


def get_source(source):
    source = str(source)
    if source.isdigit():
        return int(source)
    return source


def available_video_devices():
    return sorted(str(path) for path in Path("/dev").glob("video*"))

