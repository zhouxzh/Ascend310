"""Filesystem locations shared by the Case 1 runtime components."""

from pathlib import Path


CASE_ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = CASE_ROOT / "models"
DATA_DIR = CASE_ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "attendance.db"
