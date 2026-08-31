"""Copy legacy Case 1 runtime data into the structured data directory.

The command is intentionally plan-only unless ``--apply`` is supplied. It
never removes the legacy database or upload directory.
"""

import argparse
import shutil
import sys
from pathlib import Path

CASE_ROOT = Path(__file__).resolve().parents[1]
if str(CASE_ROOT) not in sys.path:
    sys.path.insert(0, str(CASE_ROOT))

from face_attendance.config import DB_PATH, UPLOAD_DIR


def copy_database(source: Path, target: Path, apply: bool) -> str:
    if not source.exists():
        return f"database: no legacy file at {source}"
    if target.exists():
        return f"database: skipped because target exists at {target}"
    if apply:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        return f"database: copied {source} -> {target}"
    return f"database: would copy {source} -> {target}"


def copy_uploads(source: Path, target: Path, apply: bool) -> str:
    if not source.exists():
        return f"uploads: no legacy directory at {source}"
    target.mkdir(parents=True, exist_ok=True) if apply else None
    files = [path for path in source.rglob("*") if path.is_file()]
    if apply:
        for path in files:
            relative = path.relative_to(source)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not destination.exists():
                shutil.copy2(path, destination)
    action = "copied" if apply else "would copy"
    return f"uploads: {action} {len(files)} file(s) from {source} -> {target}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy Case 1 runtime data")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="perform the copy; without this flag only print the migration plan",
    )
    args = parser.parse_args()

    legacy_db = CASE_ROOT / "attendance.db"
    legacy_uploads = CASE_ROOT / "uploads"
    print(copy_database(legacy_db, DB_PATH, args.apply))
    print(copy_uploads(legacy_uploads, UPLOAD_DIR, args.apply))
    print("legacy files are preserved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
