#!/usr/bin/env python3
"""Validate every OM currently staged on the Ascend board.

This is a board-only orchestration helper.  It launches the existing ACL
smoke tools in independent processes so each cycle exercises ACL init, model
load, inference, resource cleanup, device reset, and finalize.  It does not
import PyTorch, change the production registry, or copy model assets.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any
from urllib.request import urlopen


RELEASE_ROOT = Path("/home/HwHiAiUser/Documents/case4/releases/candidate-campaign-20260910")
RESEARCH_ROOT = Path("/home/HwHiAiUser/Documents/case4/research")
VALIDATION_ROOT = RESEARCH_ROOT / "om-deployment-validation-20260911"
SINGLE_TOOL = VALIDATION_ROOT / "acl_om_smoke.py"
MULTI_TOOL = VALIDATION_ROOT / "acl_multi_om_smoke.py"


def _stamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _device_snapshot() -> str:
    result = subprocess.run(
        ["bash", "-lc", "npu-smi info 2>&1"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout[-12000:]


def _service_health() -> dict[str, Any]:
    try:
        with urlopen("http://127.0.0.1:7860/api/health", timeout=5) as response:
            payload = response.read().decode("utf-8", errors="replace")
            return {"http_status": response.status, "body": json.loads(payload)}
    except Exception as exc:  # noqa: BLE001 - board evidence should survive a down service
        return {"error": f"{type(exc).__name__}: {exc}"}


def _specs() -> list[dict[str, Any]]:
    production = RELEASE_ROOT / "models" / "om"
    return [
        {
            "id": "ccnet",
            "path": production / "palm_ccnet_mixed_fp16.om",
            "runner": "single",
            "workbench_contract": True,
            "reason": "current manual_test workbench model",
        },
        {
            "id": "compnet_tongji_600",
            "path": production / "compnet_tongji_600_mixed_fp16.om",
            "runner": "single",
            "workbench_contract": True,
            "reason": "current manual_test workbench model",
        },
        {
            "id": "compnet_iitd_460",
            "path": production / "compnet_iitd_460_mixed_fp16.om",
            "runner": "single",
            "workbench_contract": True,
            "reason": "current manual_test workbench model",
        },
        {
            "id": "compnet_rest_358",
            "path": production / "compnet_rest_358_mixed_fp16.om",
            "runner": "single",
            "workbench_contract": True,
            "reason": "current manual_test workbench model",
        },
        {
            "id": "compnet_xjtu_flash_200",
            "path": production / "compnet_xjtu_flash_200_mixed_fp16.om",
            "runner": "single",
            "workbench_contract": True,
            "reason": "current manual_test workbench model",
        },
        {
            "id": "compnet_xjtu_natural_200",
            "path": production / "compnet_xjtu_natural_200_mixed_fp16.om",
            "runner": "single",
            "workbench_contract": True,
            "reason": "current manual_test workbench model",
        },
        {
            "id": "holzweber_resnet18_tongji",
            "path": RESEARCH_ROOT / "holzweber-20260911" / "om" / "resnet18_tongji_classifier_mixed_fp16.om",
            "runner": "single",
            "workbench_contract": False,
            "reason": "600-class RGB classifier, not 512-d embedding",
        },
        {
            "id": "holzweber_mobilenet_v2_tongji",
            "path": RESEARCH_ROOT / "holzweber-20260911" / "om" / "mobilenet_v2_tongji_classifier_mixed_fp16.om",
            "runner": "single",
            "workbench_contract": False,
            "reason": "600-class RGB classifier, not 512-d embedding",
        },
        {
            "id": "holzweber_roi_lanet",
            "path": RESEARCH_ROOT / "holzweber-20260911" / "om" / "roi_lanet_mixed_fp16_linux_aarch64.om",
            "runner": "single",
            "workbench_contract": False,
            "reason": "ROI transform output, not an embedding",
        },
        {
            "id": "lin_dxin_resnet18_pair",
            "path": RESEARCH_ROOT / "lin-dxin-20260911" / "om" / "lin_dxin_resnet18_pair_mixed_fp16.om",
            "runner": "multi",
            "workbench_contract": False,
            "reason": "two inputs and two outputs; requires a pair-comparison adapter",
        },
        {
            "id": "ppnet",
            "path": RESEARCH_ROOT / "ppnet-20260911" / "om" / "ppnet_tongji_600_mixed_fp16.om",
            "runner": "single",
            "workbench_contract": False,
            "reason": "512-d feature path is not admitted or quality-validated",
        },
        {
            "id": "compnet_static_gabor",
            "path": VALIDATION_ROOT / "compnet_static_gabor_mixed_fp16.om",
            "runner": "single",
            "workbench_contract": False,
            "reason": "legacy static-gabor OM alias; canonical CompNet variants are tested separately",
        },
        {
            "id": "kenan_cnn_palmar_veins",
            "path": RESEARCH_ROOT / "kenan-veins-20260911" / "om" / "kenan_cnn_palmar_veins_sanitized_mixed_fp16.om",
            "runner": "single",
            "workbench_contract": False,
            "reason": "500-class NIR palmar-vein classifier",
        },
    ]


def _run_cycle(spec: dict[str, Any], cycle: int, output: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        str(MULTI_TOOL if spec["runner"] == "multi" else SINGLE_TOOL),
        "--model",
        str(spec["path"]),
    ]
    if spec["runner"] == "single":
        command.extend(["--loops", "1"])
    command.extend(["--output", str(output)])
    started = time.perf_counter()
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
        env=os.environ.copy(),
    )
    record: dict[str, Any] = {
        "cycle": cycle,
        "started_at": _stamp(),
        "elapsed_s": round(time.perf_counter() - started, 3),
        "return_code": result.returncode,
        "output_path": str(output),
    }
    if output.exists():
        try:
            record["smoke"] = json.loads(output.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            record["parse_error"] = f"{type(exc).__name__}: {exc}"
    if result.stderr:
        record["stderr_tail"] = result.stderr[-4000:]
    if result.returncode != 0 and result.stdout:
        record["stdout_tail"] = result.stdout[-4000:]
    return record


def main() -> int:
    VALIDATION_ROOT.mkdir(parents=True, exist_ok=True)
    cycles_root = VALIDATION_ROOT / "cycles"
    report: dict[str, Any] = {
        "schema_version": 1,
        "started_at": _stamp(),
        "board": "Ascend 310B4 / 8T",
        "host": "orangepiaipro",
        "framework": "ACL + NumPy only; no PyTorch on board",
        "cycles_per_model": 10,
        "production_registry_modified": False,
        "before_service_health": _service_health(),
        "before_npu_smi": _device_snapshot(),
        "models": [],
    }
    failures = 0
    for spec in _specs():
        path = Path(spec["path"])
        item: dict[str, Any] = {
            "id": spec["id"],
            "om_path": str(path),
            "runner": spec["runner"],
            "workbench_contract": spec["workbench_contract"],
            "deployment_reason": spec["reason"],
            "started_at": _stamp(),
            "status": "blocked_asset",
        }
        if not path.is_file():
            item["error"] = "OM file does not exist on board"
            report["models"].append(item)
            failures += 1
            continue
        item["bytes"] = path.stat().st_size
        item["sha256"] = _sha256(path)
        model_root = cycles_root / spec["id"]
        model_root.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, Any]] = []
        for cycle in range(1, 11):
            record = _run_cycle(spec, cycle, model_root / f"cycle-{cycle:02d}.json")
            records.append(record)
            smoke = record.get("smoke")
            if record["return_code"] != 0 or not isinstance(smoke, dict) or smoke.get("status") != "passed":
                failures += 1
                break
        passed = len(records) == 10 and all(
            record.get("return_code") == 0
            and isinstance(record.get("smoke"), dict)
            and record["smoke"].get("status") == "passed"
            for record in records
        )
        item["cycles_completed"] = len(records)
        item["cycles"] = records
        item["acl_lifecycle"] = "passed" if passed else "failed"
        item["runtime_deployable"] = passed
        item["deployment_gate"] = (
            "current_workbench_runtime_candidate"
            if passed and spec["workbench_contract"]
            else "research_only_contract_requires_separate_adapter"
            if passed
            else "blocked_acl_or_asset"
        )
        item["quality_gate"] = "not_run"
        item["finished_at"] = _stamp()
        report["models"].append(item)
    report["after_npu_smi"] = _device_snapshot()
    report["after_service_health"] = _service_health()
    report["finished_at"] = _stamp()
    report["status"] = "passed" if failures == 0 else "failed"
    report["failure_count"] = failures
    target = VALIDATION_ROOT / "validation.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
