"""Compatibility import for older Case 1 commands."""

from face_attendance.database import *  # noqa: F401,F403


if __name__ == "__main__":
    init_db()
