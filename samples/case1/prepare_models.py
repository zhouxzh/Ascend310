"""Compatibility launcher for ``scripts/prepare_models.py``."""

from pathlib import Path
import runpy


if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).parent / "scripts" / "prepare_models.py"), run_name="__main__")
