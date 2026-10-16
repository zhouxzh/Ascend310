"""采集板端环境快照：npu-smi、CANN 版本、内存、磁盘与 Python 包。

快照只作 provenance 记录，不作为样例通过与否的判定门。

    python tools/collect_board_env.py
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

CHAPTER_DIR = Path(__file__).resolve().parent.parent
OUTPUT_DIR = CHAPTER_DIR / "outputs"
VERSION_FILE = Path("/usr/local/Ascend/ascend-toolkit/latest/version.cfg")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect an Ascend 310B environment snapshot.")
    parser.add_argument("--device-id", type=int, default=0, help="Ascend device id for npu-smi.")
    return parser.parse_args()


def run(command: list[str], timeout: int = 60) -> dict[str, object]:
    if shutil.which(command[0]) is None and not Path(command[0]).exists():
        return {"command": " ".join(command), "available": False}
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return {"command": " ".join(command), "available": True, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "command": " ".join(command),
        "available": True,
        "returncode": completed.returncode,
        "stdout": completed.stdout.strip(),
        "stderr": completed.stderr.strip(),
    }


def main() -> int:
    args = parse_args()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    snapshot: dict[str, object] = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "hostname": platform.node(),
        "machine": platform.machine(),
        "python": {"version": sys.version.split()[0], "executable": sys.executable},
    }

    snapshot["npu_smi"] = run(["npu-smi", "info"])
    snapshot["npu_smi_proc"] = run(["npu-smi", "info", "-t", "proc-mem", "-i", "0", "-c", str(args.device_id)])
    snapshot["memory"] = run(["free", "-m"])
    snapshot["disk"] = run(["df", "-h", "/home"])
    snapshot["pip_freeze_filtered"] = run(
        [sys.executable, "-m", "pip", "list"],
    )
    if VERSION_FILE.exists():
        snapshot["cann_version_cfg"] = VERSION_FILE.read_text(encoding="utf-8", errors="replace").strip()

    if isinstance(snapshot.get("pip_freeze_filtered"), dict):
        raw = str(snapshot["pip_freeze_filtered"].get("stdout", ""))
        keep = ("mindspore", "mindnlp", "numpy", "torch", "acl")
        snapshot["pip_freeze_filtered"]["stdout"] = "\n".join(
            line for line in raw.splitlines() if any(key in line.lower() for key in keep)
        )

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = OUTPUT_DIR / f"board-env-{stamp}.json"
    json_path.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    text_path = OUTPUT_DIR / f"board-env-{stamp}.txt"
    lines: list[str] = [f"# board environment snapshot {snapshot['timestamp']}", ""]
    for key, value in snapshot.items():
        lines.append(f"## {key}")
        if isinstance(value, dict):
            for sub_key, sub_value in value.items():
                lines.append(f"{sub_key}: {sub_value}")
        else:
            lines.append(str(value))
        lines.append("")
    text_path.write_text("\n".join(lines), encoding="utf-8")

    print(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True))
    print(f"\nsnapshot written: {json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
