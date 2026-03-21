import re
from pathlib import Path

from utils.opencv_runtime import cv2

MODEL_DIR = Path(__file__).resolve().parents[1] / "models"
MODEL_PATTERNS = {
	"cpu": re.compile(r"^ssd(?P<size>300|320)_(?P<backbone>.+)\.onnx$"),
	"npu": re.compile(r"^ssd(?P<size>300|320)_(?P<backbone>.+)\.om$"),
}
CAMERA_PROFILE_PATTERN = re.compile(
	r"^(?:(?P<width>\d+)\s*[x,]\s*(?P<height>\d+))?(?:\s*@\s*(?P<fps>\d+(?:\.\d+)?))?$"
)

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


def parse_camera_profile(camera_profile):
	if camera_profile is None:
		return None, None, None

	normalized = str(camera_profile).strip().lower()
	if not normalized or normalized in {"auto", "default", "native"}:
		return None, None, None

	fps_only_match = re.fullmatch(r"\d+(?:\.\d+)?", normalized)
	if fps_only_match:
		return None, None, float(normalized)

	match = CAMERA_PROFILE_PATTERN.fullmatch(normalized)
	if not match:
		raise ValueError(
			"Camera profile must be 'WIDTHxHEIGHT', 'WIDTHxHEIGHT@FPS', '@FPS', or 'auto'."
		)

	width = match.group("width")
	height = match.group("height")
	fps = match.group("fps")
	if width is None and fps is None:
		raise ValueError(
			"Camera profile must include a resolution, an FPS value, or both."
		)

	return (
		int(width) if width is not None else None,
		int(height) if height is not None else None,
		float(fps) if fps is not None else None,
	)




def resolve_camera_capture_request(camera_profile=""):
	requested_width, requested_height, requested_fps = parse_camera_profile(camera_profile)
	return requested_width, requested_height, requested_fps


def get_capture_stream_info(cap, frame=None):
	if frame is not None:
		height, width = frame.shape[:2]
	else:
		width = int(round(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0))
		height = int(round(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0))

	fps = cap.get(cv2.CAP_PROP_FPS)
	if fps is None or fps <= 1e-3:
		fps = None

	return width, height, fps


def get_capture_backend_name(cap):
	get_backend_name = getattr(cap, "getBackendName", None)
	if callable(get_backend_name):
		try:
			return get_backend_name()
		except Exception:
			return "unknown"
	return "unknown"


def get_capture_buffer_size(cap):
	buffer_prop = getattr(cv2, "CAP_PROP_BUFFERSIZE", None)
	if buffer_prop is None:
		return None

	try:
		buffer_size = cap.get(buffer_prop)
	except Exception:
		return None

	if buffer_size is None or buffer_size < 0:
		return None
	return int(round(buffer_size))


def open_video_capture(video_source):
	prefer_v4l2 = isinstance(video_source, int) and hasattr(cv2, "CAP_V4L2")
	if prefer_v4l2:
		cap = cv2.VideoCapture(video_source, cv2.CAP_V4L2)
		if cap.isOpened():
			return cap
		cap.release()

	return cv2.VideoCapture(video_source)


def open_capture(source, width=None, height=None, fps=None, use_mjpeg=True):
	video_source = parse_source(source)
	cap = open_video_capture(video_source)
	if isinstance(video_source, int):
		if use_mjpeg:
			cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
		buffer_prop = getattr(cv2, "CAP_PROP_BUFFERSIZE", None)
		if buffer_prop is not None:
			cap.set(buffer_prop, 1)
	if width:
		cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
	if height:
		cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
	if isinstance(video_source, int) and fps is not None and fps > 0:
		cap.set(cv2.CAP_PROP_FPS, float(fps))
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