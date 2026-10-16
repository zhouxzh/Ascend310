"""第5章样例 0：报告 MindSpore 环境、版本与导入来源。

本脚本只读取版本信息和导入路径，不训练、不加载模型、不修改环境。板端需要
先加载 CANN 环境，再使用与 MindSpore 配套的 Python 解释器运行：

    source /usr/local/Ascend/ascend-toolkit/set_env.sh
    python check_mindspore_env.py
"""

from __future__ import annotations

import argparse

from chapter5_utils import (
    BLOCKED,
    PASS,
    emit_result,
    ensure_output_dir,
    env_snapshot,
    module_probe,
    run_isolated,
    parse_last_result,
    print_section,
    tail_lines,
    write_json,
)

DEVICE_PROBE = """
import json
import mindspore as ms
from mindspore import Tensor, ops
ms.set_context(mode=ms.PYNATIVE_MODE, device_target="Ascend", device_id={device_id})
a = Tensor([1.0, 2.0], ms.float32)
value = ops.add(a, a).asnumpy().tolist()
print("RESULT " + json.dumps({{"value": value}}))
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Report MindSpore runtime versions and import origins.")
    parser.add_argument("--device-id", type=int, default=0, help="Ascend device id for the optional device probe.")
    parser.add_argument("--skip-device-probe", action="store_true", help="Only report versions, do not touch the device.")
    parser.add_argument("--output", default="outputs/env-report.json", help="JSON report path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    print_section("interpreter")
    snapshot = env_snapshot()
    for key in ("python_version", "python_executable", "machine", "python_no_user_site", "cann_toolkit_env"):
        print(f"{key}: {snapshot[key]}")

    print_section("modules")
    modules = {}
    for name in ("mindspore", "mindspore_lite", "mindnlp", "acl"):
        probe = module_probe(name)
        modules[name] = probe
        if not probe.get("found"):
            print(f"{name}: NOT FOUND")
        elif not probe.get("imported"):
            print(f"{name}: import failed -> {probe.get('error')}")
        else:
            print(f"{name}: {probe.get('version')} <- {probe.get('file')}")

    device_probe: dict[str, object] = {"skipped": True}
    if not args.skip_device_probe:
        print_section("ascend device probe")
        child = run_isolated(DEVICE_PROBE.format(device_id=args.device_id), timeout=600)
        result = parse_last_result(child["stdout"] or "")
        if child["returncode"] == 0 and result:
            device_probe = {"skipped": False, "status": PASS, "value": result.get("value")}
            print(f"ascend add fp32: {result.get('value')}")
        else:
            device_probe = {
                "skipped": False,
                "status": BLOCKED,
                "returncode": child["returncode"],
                "message": tail_lines(child["stderr"] or child["stdout"] or "", 6),
            }
            print("ascend add fp32: BLOCKED")
            print(device_probe["message"])

    report = {
        "environment": snapshot,
        "modules": modules,
        "device_probe": device_probe,
    }
    report_path = ensure_output_dir() / args.output.split("/")[-1]
    write_json(report_path, report)

    mindspore_ok = bool(modules["mindspore"].get("imported"))
    status = PASS if mindspore_ok and device_probe.get("status") == PASS else BLOCKED
    if device_probe.get("skipped"):
        status = PASS if mindspore_ok else BLOCKED

    emit_result(
        "check_mindspore_env",
        status,
        {
            "report": str(report_path),
            "mindspore": modules["mindspore"].get("version", "missing"),
            "mindspore_file": modules["mindspore"].get("file", ""),
            "mindspore_lite": modules["mindspore_lite"].get("version", "missing"),
            "mindnlp": modules["mindnlp"].get("version", "missing"),
            "acl": modules["acl"].get("version", "missing"),
            "device_probe": device_probe.get("status", "skipped"),
        },
    )
    return 0 if status == PASS else 1


if __name__ == "__main__":
    raise SystemExit(main())
