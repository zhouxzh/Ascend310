"""Probe the existing board PyACL runtime without loading a model."""

from __future__ import annotations

import json
import platform
import time


def main() -> int:
    if platform.machine().lower() not in {"aarch64", "arm64"}:
        raise RuntimeError("The ACL probe must run on the Ascend board")
    try:
        import acl
    except ImportError as exc:
        raise RuntimeError("PyACL is unavailable in the active board environment") from exc

    report: dict[str, object] = {
        "schema": "piano-ddsp-acl-probe/v1",
        "captured_at_unix": time.time(),
    }
    init_status = acl.init()
    report["acl_init"] = init_status
    device_status: int | None = None
    reset_status: int | None = None
    finalize_status: int | None = None
    try:
        if init_status in (0, 100002):
            device_status = acl.rt.set_device(0)
            report["acl_rt_set_device"] = device_status
            if device_status == 0:
                reset_status = acl.rt.reset_device(0)
                report["acl_rt_reset_device"] = reset_status
    finally:
        if init_status in (0, 100002):
            finalize_status = acl.finalize()
            report["acl_finalize"] = finalize_status
    report["passed"] = (
        init_status in (0, 100002)
        and device_status == 0
        and reset_status == 0
        and finalize_status == 0
    )
    print(json.dumps(report, indent=2), flush=True)
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
