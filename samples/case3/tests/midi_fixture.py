from __future__ import annotations

from pathlib import Path

from tools.create_test_midi import create_test_midi


def create_ddsp_fixture(directory: str | Path) -> Path:
    path = Path(directory) / "ddsp-test.mid"
    create_test_midi(path)
    return path
