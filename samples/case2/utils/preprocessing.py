import re
from pathlib import Path

from utils.opencv_runtime import cv2

MODEL_DIR = Path(__file__).resolve().parents[1] / "models"
MODEL_PATTERNS = {
	"cpu": re.compile(r"^ssd(?P<size>300|320)_(?P<backbone>.+)\.onnx$"),
	"npu": re.compile(r"^ssd(?P<size>300|320)_(?P<backbone>.+)\.om$"),
}

COCO_LABELS = [
	"background",
	"person",
	"bicycle",
	"car",
	"motorcycle",
	"airplane",
	"bus",
	"train",
	"truck",
	"boat",
	"traffic light",
	"fire hydrant",
	"street sign",
	"stop sign",
	"parking meter",
	"bench",
	"bird",
	"cat",
	"dog",
	"horse",
	"sheep",
	"cow",
	"elephant",
	"bear",
	"zebra",
	"giraffe",
	"hat",
	"backpack",
	"umbrella",
	"shoe",
	"eye glasses",
	"handbag",
	"tie",
	"suitcase",
	"frisbee",
	"skis",
	"snowboard",
	"sports ball",
	"kite",
	"baseball bat",
	"baseball glove",
	"skateboard",
	"surfboard",
	"tennis racket",
	"bottle",
	"plate",
	"wine glass",
	"cup",
	"fork",
	"knife",
	"spoon",
	"bowl",
	"banana",
	"apple",
	"sandwich",
	"orange",
	"broccoli",
	"carrot",
	"hot dog",
	"pizza",
	"donut",
	"cake",
	"chair",
	"couch",
	"potted plant",
	"bed",
	"mirror",
	"dining table",
	"window",
	"desk",
	"toilet",
	"door",
	"tv",
	"laptop",
	"mouse",
	"remote",
	"keyboard",
	"cell phone",
	"microwave",
	"oven",
	"toaster",
	"sink",
	"refrigerator",
	"blender",
	"book",
	"clock",
	"vase",
	"scissors",
	"teddy bear",
	"hair drier",
	"toothbrush",
]


def discover_models(model_dir: Path, device: str) -> dict[str, Path]:
	pattern = MODEL_PATTERNS[device]
	suffix = ".onnx" if device == "cpu" else ".om"
	models: dict[str, Path] = {}
	for model_path in sorted(model_dir.glob(f"*{suffix}")):
		match = pattern.match(model_path.name)
		if not match:
			continue
		models[match.group("backbone")] = model_path
	return models


def normalize_backbone_name(name: str) -> str:
	for prefix in ("ssd300_", "ssd320_"):
		if name.startswith(prefix):
			return name[len(prefix):].removesuffix(".onnx").removesuffix(".om")
	return name.removesuffix(".onnx").removesuffix(".om")


def resolve_model_path(model_arg: str, backbone: str, model_dir: Path, device: str) -> Path:
	if model_arg:
		return Path(model_arg).expanduser().resolve()

	available = discover_models(model_dir, device)
	backbone_key = normalize_backbone_name(backbone)
	model_path = available.get(backbone_key)
	if model_path is None:
		available_names = ", ".join(sorted(available)) if available else "<none>"
		raise FileNotFoundError(
			f"Cannot find {device.upper()} model for backbone '{backbone_key}' in {model_dir}. "
			f"Available backbones: {available_names}"
		)
	return model_path.resolve()


def load_labels(label_path: str) -> list[str]:
	if not label_path:
		return COCO_LABELS

	path = Path(label_path).expanduser().resolve()
	if not path.exists():
		raise FileNotFoundError(f"Label file does not exist: {path}")

	with path.open("r", encoding="utf-8") as handle:
		labels = [line.strip() for line in handle if line.strip()]

	if not labels:
		raise ValueError(f"Label file is empty: {path}")

	return labels


def parse_source(source):
	if isinstance(source, int):
		return source
	if isinstance(source, str) and source.isdigit():
		return int(source)
	return source


def open_capture(source, width=None, height=None, fps=None, use_mjpeg=False):
	video_source = parse_source(source)
	cap = cv2.VideoCapture(video_source)
	if isinstance(video_source, int):
		if use_mjpeg:
			cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
		if fps is not None and fps > 0:
			cap.set(cv2.CAP_PROP_FPS, float(fps))
	if width:
		cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
	if height:
		cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
	return cap


def create_video_writer(output_path, fps, frame_size):
	path = Path(output_path).expanduser().resolve()
	path.parent.mkdir(parents=True, exist_ok=True)

	if len(frame_size) == 3:
		height, width = frame_size[:2]
	else:
		width, height = frame_size

	writer_fps = fps if fps and fps > 0 else 25.0
	fourcc = cv2.VideoWriter_fourcc(*"mp4v")
	return cv2.VideoWriter(str(path), fourcc, writer_fps, (width, height))