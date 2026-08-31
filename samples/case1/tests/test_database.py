"""SQLite process-local serialization tests (no board dependencies)."""

from concurrent.futures import ThreadPoolExecutor
import sys
from pathlib import Path

CASE_ROOT = Path(__file__).resolve().parents[1]
if str(CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(CASE_ROOT))

from face_attendance import database


def test_concurrent_database_writes_are_serialized(tmp_path, monkeypatch):
    db_path = tmp_path / "attendance.db"
    monkeypatch.setattr(database, "DB_PATH", db_path)
    monkeypatch.setattr(database, "DB_NAME", str(db_path))
    database.init_db()

    def add(index):
        return database.add_user(f"user-{index}", b"embedding", None)

    with ThreadPoolExecutor(max_workers=8) as executor:
        ids = list(executor.map(add, range(32)))

    assert len(set(ids)) == 32
    assert len(database.get_users()) == 32
