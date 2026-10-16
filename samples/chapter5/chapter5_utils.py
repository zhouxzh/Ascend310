from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
import os
import platform
import signal
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

CHAPTER_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = CHAPTER_DIR / "outputs"

PASS = "pass"
FAIL = "fail"
BLOCKED = "blocked"

DEVICE_TARGETS = ("Ascend", "CPU")


def timestamp_slug() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def ensure_output_dir() -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR


def print_section(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def emit_result(example: str, status: str, metrics: dict[str, Any] | None = None, note: str = "") -> None:
    """Print the one-line machine-readable contract every sample must end with."""

    payload: dict[str, Any] = {"example": example, "status": status, "metrics": metrics or {}}
    if note:
        payload["note"] = note
    print("RESULT " + json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def write_json(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def module_probe(name: str) -> dict[str, Any]:
    """Report version and import origin without importing heavy modules twice."""

    try:
        spec = importlib.util.find_spec(name)
    except (ImportError, ValueError) as exc:
        return {"name": name, "found": False, "error": str(exc)}
    if spec is None:
        return {"name": name, "found": False}
    try:
        module = importlib.import_module(name)
    except Exception as exc:  # import-time ABI failures are evidence, not crashes of this script
        return {"name": name, "found": True, "imported": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "name": name,
        "found": True,
        "imported": True,
        "version": str(getattr(module, "__version__", "unknown")),
        "file": str(getattr(module, "__file__", "") or ""),
    }


def sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_record(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    return {
        "path": str(path),
        "exists": True,
        "bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }


def set_context(ms: Any, device_target: str = "Ascend", device_id: int = 0, graph: bool = False) -> str:
    """Configure the MindSpore context and return the mode name that was set."""

    mode = ms.GRAPH_MODE if graph else ms.PYNATIVE_MODE
    if device_target.upper() == "CPU":
        ms.set_context(mode=mode, device_target="CPU")
    else:
        ms.set_context(mode=mode, device_target=device_target, device_id=device_id)
    return "GRAPH_MODE" if graph else "PYNATIVE_MODE"


def env_snapshot() -> dict[str, Any]:
    return {
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "machine": platform.machine(),
        "platform": platform.platform(),
        "cwd": os.getcwd(),
        "python_no_user_site": os.environ.get("PYTHONNOUSERSITE", ""),
        "ld_library_path_set": bool(os.environ.get("LD_LIBRARY_PATH", "")),
        "cann_toolkit_env": os.environ.get("ASCEND_TOOLKIT_HOME", "") or os.environ.get("ASCEND_HOME_PATH", ""),
    }


def first_ascend_message(output: str) -> str:
    """Extract the first CANN/Ascend diagnostic line from a traceback or child log."""

    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("EZ") or "not support" in stripped.lower():
            return stripped[:400]
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(("RuntimeError", "ValueError", "TypeError", "KeyError")):
            return stripped[:400]
    return ""


def run_isolated(code: str, timeout: int = 600) -> dict[str, Any]:
    """Run a snippet in a child interpreter so a crash or hang cannot stop the sample.

    Output goes to a temporary file instead of a pipe: a grandchild process (for
    example a multiprocessing forkserver) can inherit a pipe and keep it open,
    which would make the parent wait forever even after a timeout. The child also
    gets its own session so a timeout can terminate the whole process group.
    """

    with tempfile.TemporaryDirectory(prefix="chapter5-child-") as temp_dir:
        log_path = Path(temp_dir) / "child.log"
        with log_path.open("w+b") as log_file:
            process = subprocess.Popen(
                [sys.executable, "-c", code],
                stdout=log_file,
                stderr=subprocess.STDOUT,
                start_new_session=hasattr(os, "killpg"),
            )
            timed_out = False
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                timed_out = True
                _kill_process_group(process)
                try:
                    process.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    pass
        output = log_path.read_text(encoding="utf-8", errors="replace")

    return {
        "returncode": None if timed_out else process.returncode,
        "stdout": output,
        "stderr": "",
        "timeout": timed_out,
    }


def _kill_process_group(process: subprocess.Popen) -> None:
    if hasattr(os, "killpg"):
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    process.kill()


def tail_lines(text: str, limit: int = 12) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-limit:])


def parse_last_result(output: str) -> dict[str, Any] | None:
    """Read the RESULT line printed by an isolated child process."""

    for line in reversed(output.splitlines()):
        if line.startswith("RESULT "):
            try:
                payload = json.loads(line[len("RESULT ") :])
            except json.JSONDecodeError:
                return None
            return payload if isinstance(payload, dict) else None
    return None
