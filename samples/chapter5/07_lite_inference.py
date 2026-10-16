"""第5章样例 7：MindSpore Lite 端侧推理边界。

MindSpore Lite 是与全量 MindSpore 独立的运行时，有自己的版本与模型格式要求。
本样例先用 **CPU 后端**确认 MindIR 工件本身是否被当前 Lite 接受（这一步会给出
可读的版本诊断），再用 **Ascend 后端**在独立子进程里尝试构图；即使 Lite 让进程
消失，父进程也能保留退出码并给出 `blocked` 判定。
"""

from __future__ import annotations

import argparse
from pathlib import Path

from chapter5_utils import (
    BLOCKED,
    PASS,
    emit_result,
    ensure_output_dir,
    first_ascend_message,
    module_probe,
    parse_last_result,
    print_section,
    run_isolated,
    tail_lines,
    write_json,
)

LITE_PROBE = """
import json
import mindspore_lite as mslite

path = {mindir!r}
context = mslite.Context()
context.target = ["{target}"]
{device_line}

model = mslite.Model()
try:
    model.build_from_file(path, mslite.ModelType.MINDIR, context)
except Exception as exc:
    print("RESULT " + json.dumps({{"status": "fail", "stage": "build_from_file",
                                   "error": (type(exc).__name__ + ": " + str(exc))[:400]}}))
    raise SystemExit(0)

inputs = model.get_inputs()
info = [{{"name": item.name, "shape": list(item.shape)}} for item in inputs]
print("RESULT " + json.dumps({{"status": "pass", "stage": "build_from_file", "inputs": info,
                               "version": getattr(mslite, "__version__", "unknown")}}))
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe the MindSpore Lite MindIR/Ascend boundary.")
    parser.add_argument("--mindir", default="outputs/chapter5_linear.mindir", help="MindIR path (default: 06 output).")
    parser.add_argument("--device-id", type=int, default=0, help="Ascend device id.")
    parser.add_argument("--output", default="07_lite_inference.json", help="JSON report file name under outputs/.")
    return parser.parse_args()


def run_lite_probe(mindir: Path, target: str, device_id: int, timeout: int) -> dict[str, object]:
    device_line = f"context.ascend.device_id = {device_id}" if target == "ascend" else ""
    code = LITE_PROBE.format(mindir=str(mindir), target=target, device_line=device_line)
    child = run_isolated(code, timeout=timeout)
    payload = parse_last_result(child["stdout"] or "")
    combined = (child["stderr"] or "") + (child["stdout"] or "")
    if child["returncode"] == 0 and payload and payload.get("status") == PASS:
        return {"target": target, "status": PASS, "inputs": payload.get("inputs")}
    return {
        "target": target,
        "status": BLOCKED,
        "returncode": child["returncode"],
        "timeout": child.get("timeout", False),
        "error": (payload or {}).get("error", ""),
        "message": tail_lines(combined, 6),
        "diagnostic": first_ascend_message(combined),
    }


def find_diagnostic(probe: dict[str, object]) -> str:
    text = " ".join(str(probe.get(key, "")) for key in ("error", "message", "diagnostic"))
    for marker in ("mind ir version", "MindIR", "not support", "Fail to support"):
        if marker.lower() in text.lower():
            for line in text.replace(" | ", "\n").splitlines():
                if marker.lower() in line.lower():
                    return line.strip()[:300]
    return str(probe.get("error") or probe.get("diagnostic") or probe.get("message") or "")[:300]


def main() -> int:
    args = parse_args()

    output_dir = ensure_output_dir()
    mindir_path = Path(args.mindir)
    if not mindir_path.is_absolute():
        mindir_path = Path(__file__).resolve().parent / mindir_path

    print_section("lite runtime")
    lite_probe = module_probe("mindspore_lite")
    print(f"mindspore_lite: {lite_probe.get('version', 'missing')} <- {lite_probe.get('file', '')}")
    print(f"mindir: {mindir_path} (exists={mindir_path.exists()})")

    if not mindir_path.exists():
        report = {
            "mindir": str(mindir_path),
            "mindir_exists": False,
            "lite": lite_probe,
            "status": BLOCKED,
            "note": "run 06_checkpoint_mindir.py first to produce the MindIR artifact",
        }
        report_path = write_json(output_dir / args.output, report)
        emit_result("07_lite_inference", BLOCKED, {"report": str(report_path)}, note=report["note"])
        return 1

    print_section("cpu backend (artifact/format check)")
    cpu_probe = run_lite_probe(mindir_path, "cpu", args.device_id, timeout=180)
    print(f"cpu build: {cpu_probe['status'].upper()} rc={cpu_probe.get('returncode')} {find_diagnostic(cpu_probe)}")

    print_section("ascend backend")
    ascend_probe = run_lite_probe(mindir_path, "ascend", args.device_id, timeout=180)
    print(f"ascend build: {ascend_probe['status'].upper()} rc={ascend_probe.get('returncode')}")
    if ascend_probe["status"] != PASS:
        print(str(ascend_probe.get("message", ""))[:400])

    passed = ascend_probe["status"] == PASS
    status = PASS if passed else BLOCKED
    note = "" if passed else (find_diagnostic(cpu_probe) or find_diagnostic(ascend_probe))

    report = {
        "mindir": str(mindir_path),
        "mindir_exists": True,
        "mindir_bytes": mindir_path.stat().st_size,
        "lite": lite_probe,
        "cpu_probe": cpu_probe,
        "ascend_probe": ascend_probe,
        "status": status,
        "note": note,
    }
    report_path = write_json(output_dir / args.output, report)

    emit_result(
        "07_lite_inference",
        status,
        {
            "report": str(report_path),
            "lite_version": lite_probe.get("version", "missing"),
            "mindir_bytes": mindir_path.stat().st_size,
            "cpu_build": cpu_probe["status"],
            "ascend_build": ascend_probe["status"],
            "ascend_returncode": ascend_probe.get("returncode"),
        },
        note=str(note)[:300],
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
