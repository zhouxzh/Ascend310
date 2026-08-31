"""Compatibility launcher for ``scripts/check_onnx.py``."""

from pathlib import Path
import runpy


if __name__ == "__main__":
    runpy.run_path(str(Path(__file__).parent / "scripts" / "check_onnx.py"), run_name="__main__")
