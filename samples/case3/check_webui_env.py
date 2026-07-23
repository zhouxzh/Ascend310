from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent
CANN_HOME = Path("/usr/local/Ascend/ascend-toolkit/latest")

EXPECTED_PATHS = {
    "ASCEND_TOOLKIT_HOME": CANN_HOME,
    "ASCEND_AICPU_PATH": CANN_HOME,
    "ASCEND_OPP_PATH": CANN_HOME / "opp",
    "TOOLCHAIN_HOME": CANN_HOME / "toolkit",
    "ASCEND_HOME_PATH": CANN_HOME,
}

EXPECTED_LIST_ENTRIES = {
    "PATH": [CANN_HOME / "bin"],
    "PYTHONPATH": [CANN_HOME / "python" / "site-packages"],
    "LD_LIBRARY_PATH": [CANN_HOME / "lib64", Path("/var/davinci/driver/lib64")],
}

REQUIRED_MODULES = (
    "fastapi",
    "pydantic",
    "uvicorn",
    "websockets",
    "numpy",
    "mido",
    "rtmidi",
    "sounddevice",
    "acl",
)


def main() -> int:
    failures: list[str] = []

    if os.environ.get("CONDA_DEFAULT_ENV") != "base":
        failures.append("CONDA_DEFAULT_ENV must be base")
    else:
        print(f"[OK] conda environment: {os.environ['CONDA_DEFAULT_ENV']}")

    print(f"[OK] Python: {sys.version.split()[0]} ({sys.executable})")

    for name, expected in EXPECTED_PATHS.items():
        value = os.environ.get(name)
        if not value:
            failures.append(f"{name} is not set")
        elif Path(value).resolve() != expected.resolve():
            failures.append(f"{name}={value}, expected {expected}")
        else:
            print(f"[OK] {name}: {value}")

    for name, expected_entries in EXPECTED_LIST_ENTRIES.items():
        entries = {Path(item).resolve() for item in os.environ.get(name, "").split(os.pathsep) if item}
        missing = [str(path) for path in expected_entries if path.resolve() not in entries]
        if missing:
            failures.append(f"{name} is missing: {', '.join(missing)}")
        else:
            print(f"[OK] {name}")

    for module in REQUIRED_MODULES:
        if importlib.util.find_spec(module) is None:
            failures.append(f"Python module is missing: {module}")
        else:
            print(f"[OK] Python module: {module}")

    frontend = ROOT / "webui" / "dist" / "index.html"
    if not frontend.is_file():
        failures.append(f"Frontend build is missing: {frontend}")
    else:
        print(f"[OK] Frontend build: {frontend}")

    if failures:
        print("\nEnvironment check failed:", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    print("\nEnvironment check passed. Run: python run_webui.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
