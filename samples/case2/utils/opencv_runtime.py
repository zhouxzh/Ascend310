import os
import argparse
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


def _candidate_font_dirs() -> list[Path]:
	home = Path.home()
	seen: set[Path] = set()
	candidates = [
		home / ".local/share/fonts",
		home / ".fonts",
		Path("/usr/share/fonts/truetype/dejavu"),
		Path("/usr/share/fonts/dejavu"),
		Path("/usr/share/fonts/truetype"),
		Path("/usr/local/share/fonts"),
		Path("/usr/share/fonts"),
	]
	for path in candidates:
		resolved = path.expanduser()
		if resolved in seen:
			continue
		seen.add(resolved)
		yield resolved


def _configure_qt_fontdir() -> None:
	configured_fontdir = os.environ.get("QT_QPA_FONTDIR")
	if configured_fontdir and Path(configured_fontdir).expanduser().is_dir():
		return

	for font_dir in _candidate_font_dirs():
		if font_dir.is_dir():
			os.environ["QT_QPA_FONTDIR"] = str(font_dir)
			return


def _first_available_font_dir() -> Optional[Path]:
	for font_dir in _candidate_font_dirs():
		if font_dir.is_dir():
			return font_dir
	return None


_configure_qt_fontdir()

import cv2 as cv2


def _ensure_cv2_qt_fonts_dir() -> None:
	font_dir = _first_available_font_dir()
	if font_dir is None:
		return

	qt_fonts_dir = Path(cv2.__file__).resolve().parent / "qt" / "fonts"
	if qt_fonts_dir.is_dir():
		return

	try:
		qt_fonts_dir.parent.mkdir(parents=True, exist_ok=True)
		qt_fonts_dir.symlink_to(font_dir, target_is_directory=True)
	except (AttributeError, NotImplementedError, OSError):
		try:
			qt_fonts_dir.mkdir(exist_ok=True)
			for font_file in font_dir.glob("*.ttf"):
				link_path = qt_fonts_dir / font_file.name
				if link_path.exists():
					continue
				try:
					link_path.symlink_to(font_file)
				except (AttributeError, NotImplementedError, OSError):
					break
		except OSError:
			return


_ensure_cv2_qt_fonts_dir()


@dataclass
class CaptureContext:
	cap: Any
	first_frame: Any
	first_read_ms: float
	requested_width: Optional[int]
	requested_height: Optional[int]
	requested_fps: Optional[float]
	capture_width: int
	capture_height: int
	capture_fps: Optional[float]
	backend_name: str
	buffer_size: Optional[int]


def add_camera_arguments(parser: argparse.ArgumentParser) -> None:
	parser.add_argument(
		"--camera-profile",
		default="auto",
		help="Preferred camera capture profile, for example '1280x720@60', '1280x720', '@60', or 'auto'.",
	)
	camera_mjpeg_group = parser.add_mutually_exclusive_group()
	camera_mjpeg_group.add_argument(
		"--camera-mjpeg",
		dest="camera_mjpeg",
		action="store_true",
		help="Use MJPEG camera output. Enabled by default for live camera sources.",
	)
	camera_mjpeg_group.add_argument(
		"--no-camera-mjpeg",
		dest="camera_mjpeg",
		action="store_false",
		help="Disable MJPEG camera output.",
	)
	parser.set_defaults(camera_mjpeg=True)


def list_available_models(model_dir: Path, device: str) -> int:
	from utils.preprocessing import discover_models

	available = discover_models(model_dir, device)
	if not available:
		print(f"No {device.upper()} models found in {model_dir}")
		return 1

	for backbone, path in available.items():
		print(f"{backbone}: {path.name}")
	return 0


def create_backend(device: str, model_path: Path, device_id: int):
	if device == "cpu":
		try:
			from ssdlite.cpu_backend import CpuBackend
		except ImportError as exc:
			raise ImportError("CPU backend requires onnxruntime to be installed.") from exc
		return CpuBackend(model_path)

	if device == "npu":
		try:
			from ssdlite.npu_backend import NpuBackend
		except ImportError as exc:
			raise ImportError("NPU backend requires Ascend ACL Python runtime to be installed.") from exc
		return NpuBackend(model_path, device_id=device_id)

	raise ValueError(f"Unsupported device: {device}")


def open_capture_context(source, camera_profile: str, use_mjpeg: bool) -> CaptureContext:
	from utils.preprocessing import (
		get_capture_backend_name,
		get_capture_buffer_size,
		get_capture_stream_info,
		open_capture,
		resolve_camera_capture_request,
	)

	requested_width, requested_height, requested_fps = resolve_camera_capture_request(camera_profile)
	cap = open_capture(
		source,
		requested_width,
		requested_height,
		fps=requested_fps,
		use_mjpeg=use_mjpeg,
	)
	if not cap.isOpened():
		raise RuntimeError(f"Failed to open video source: {source}")

	read_start = time.perf_counter()
	ok, first_frame = cap.read()
	first_read_ms = (time.perf_counter() - read_start) * 1000.0
	if not ok:
		cap.release()
		raise RuntimeError("Video source opened but failed to read the first frame.")

	capture_width, capture_height, capture_fps = get_capture_stream_info(cap, first_frame)
	backend_name = get_capture_backend_name(cap)
	buffer_size = get_capture_buffer_size(cap)
	return CaptureContext(
		cap=cap,
		first_frame=first_frame,
		first_read_ms=first_read_ms,
		requested_width=requested_width,
		requested_height=requested_height,
		requested_fps=requested_fps,
		capture_width=capture_width,
		capture_height=capture_height,
		capture_fps=capture_fps,
		backend_name=backend_name,
		buffer_size=buffer_size,
	)


def print_runtime_banner(device: str, model_path: Path) -> None:
	print(f"Using device: {device}")
	print(f"Using model: {model_path}")


def print_capture_summary(camera_profile: str, use_mjpeg: bool, capture_context: CaptureContext) -> None:
	requested_profile_text = str(camera_profile).strip() or "auto"
	capture_fps_text = f"{capture_context.capture_fps:.2f}" if capture_context.capture_fps else "unavailable"
	buffer_text = str(capture_context.buffer_size) if capture_context.buffer_size is not None else "unavailable"
	print(f"Capture request: profile={requested_profile_text}, mjpeg={'on' if use_mjpeg else 'off'}")
	print(f"Capture backend: {capture_context.backend_name}, buffer={buffer_text}")
	print(
		f"Capture stream: size={capture_context.capture_width}x{capture_context.capture_height}, "
		f"fps={capture_fps_text}"
	)

	resolution_mismatch = (
		capture_context.requested_width
		and capture_context.requested_height
		and (
			capture_context.capture_width != capture_context.requested_width
			or capture_context.capture_height != capture_context.requested_height
		)
	)
	if resolution_mismatch:
		print("Warning: camera did not apply the requested capture resolution exactly.")

	fps_mismatch = (
		capture_context.requested_fps
		and capture_context.capture_fps
		and abs(capture_context.capture_fps - capture_context.requested_fps)
		> max(1.0, capture_context.requested_fps * 0.05)
	)
	if fps_mismatch:
		print("Warning: camera did not apply the requested FPS exactly.")


def create_timing_totals() -> dict[str, float]:
	return {
		"read": 0.0,
		"preprocess": 0.0,
		"inference": 0.0,
		"decode": 0.0,
		"draw": 0.0,
	}


def compute_display_fps(avg_timings_ms: dict[str, float], capture_fps: Optional[float]) -> float:
	avg_frame_ms = sum(avg_timings_ms.values())
	processing_fps = 1000.0 / max(avg_frame_ms, 1e-6)
	if capture_fps and capture_fps > 1e-3:
		return min(processing_fps, capture_fps)
	return processing_fps


def read_frame(capture_context: CaptureContext, pending_frame, pending_read_ms: float):
	if pending_frame is not None:
		return pending_frame, pending_read_ms, None, 0.0

	read_start = time.perf_counter()
	ok, frame = capture_context.cap.read()
	read_ms = (time.perf_counter() - read_start) * 1000.0
	if not ok:
		return None, read_ms, None, 0.0
	return frame, read_ms, None, 0.0


def update_timing_totals(timing_totals: dict[str, float], read_ms: float, profile_ms: dict[str, float]) -> None:
	timing_totals["read"] += read_ms
	timing_totals["preprocess"] += profile_ms["preprocess"]
	timing_totals["inference"] += profile_ms["inference"]
	timing_totals["decode"] += profile_ms["decode"]


def compute_average_timings(timing_totals: dict[str, float], frame_count: int) -> dict[str, float]:
	return {
		key: timing_totals[key] / frame_count
		for key in ("read", "preprocess", "inference", "decode", "draw")
	}


def resolve_writer_fps(capture_fps: Optional[float], fallback_fps: float) -> float:
	if capture_fps and capture_fps > 1e-3:
		return capture_fps
	return fallback_fps
