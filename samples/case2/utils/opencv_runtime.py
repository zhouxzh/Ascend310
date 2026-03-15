import os
from pathlib import Path
from typing import Optional


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

	qt_fonts_dir.parent.mkdir(parents=True, exist_ok=True)
	try:
		qt_fonts_dir.symlink_to(font_dir, target_is_directory=True)
	except (AttributeError, NotImplementedError, OSError):
		qt_fonts_dir.mkdir(exist_ok=True)
		for font_file in font_dir.glob("*.ttf"):
			link_path = qt_fonts_dir / font_file.name
			if link_path.exists():
				continue
			try:
				link_path.symlink_to(font_file)
			except (AttributeError, NotImplementedError, OSError):
				break


_ensure_cv2_qt_fonts_dir()