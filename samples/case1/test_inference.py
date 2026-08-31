"""Compatibility launcher for the board-only Case 1 smoke test."""

from pathlib import Path
import runpy


if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).parent / "tests" / "test_inference.py"), run_name="__main__")
