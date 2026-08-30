#!/usr/bin/env python3
"""Fail-closed preflight for the board-side MindSpore chat worker.

This checker is intentionally standard-library only.  It does not install or
remove packages and it does not load a model.  The launcher runs it after the
requested conda and CANN environment have been sourced, so an SSH command that
forgot to activate the board environment cannot accidentally start a worker
with a different Python or on CPU.
"""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import site
import shutil
import subprocess
import sys
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence


FORBIDDEN_PACKAGES = (
    "torch",
    "torch_npu",
    "torchaudio",
    "transformers",
    "onnxruntime",
    "mindtorch",
    "vllm",
    "mindie",
)
ADAPTER_FILES = ("mindspore_chat_providers.py", "mindspore_chat_service.py")
SOC_RE = re.compile(r"\b(Ascend\s*)?(310B[0-9A-Za-z]+)\b", re.IGNORECASE)
IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", re.MULTILINE)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _version(distribution: str) -> Optional[str]:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None
    except Exception:
        return None


def _find_spec(name: str) -> Optional[str]:
    try:
        spec = importlib.util.find_spec(name)
    except Exception:
        return None
    return str(spec.origin) if spec is not None and spec.origin else "present"


def _command(command: Sequence[str], timeout: float = 8.0) -> Dict[str, Any]:
    try:
        completed = subprocess.run(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": list(command), "returncode": None, "output": str(exc)}
    return {
        "command": list(command),
        "returncode": int(completed.returncode),
        "output": completed.stdout[-16_384:],
    }


def _detect_soc() -> Dict[str, Any]:
    executable = shutil.which("npu-smi")
    if not executable:
        return {"ok": False, "reason": "npu-smi was not found on PATH", "chip": None}
    result = _command([executable, "info"])
    matches = SOC_RE.findall(str(result.get("output", "")))
    chips = sorted({str(match[1]).upper() for match in matches})
    result.update({"ok": result.get("returncode") == 0 and bool(chips), "chip": chips[0] if len(chips) == 1 else None, "chips": chips})
    if not chips:
        result["reason"] = "npu-smi output did not contain an Ascend 310B chip"
    elif len(chips) > 1:
        result["reason"] = "multiple Ascend 310B chip identifiers were reported"
    return result


def _cann_info() -> Dict[str, Any]:
    candidates = []
    for name in ("ASCEND_TOOLKIT_HOME", "ASCEND_HOME_PATH", "ASCEND_INSTALL_PATH"):
        value = os.environ.get(name, "").strip()
        if value:
            candidates.append(value)
    sourced_script = os.environ.get("CANN_ENV_SCRIPT", "").strip()
    if sourced_script:
        candidates.append(str(Path(sourced_script).expanduser().parent))
    candidates.extend(("/usr/local/Ascend/ascend-toolkit", "/usr/local/Ascend/latest"))
    roots = []
    for raw in candidates:
        path = Path(raw).expanduser()
        if path.exists() and path.is_dir() and path not in roots:
            roots.append(path)
    version_files = []
    versions = []
    for root in roots:
        for relative in ("version.cfg", "latest/version.cfg", "version.info"):
            candidate = root / relative
            if candidate.is_file():
                version_files.append(str(candidate))
                try:
                    content = candidate.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    content = ""
                # ``version.cfg`` starts with a wrapper version (often 1.0);
                # report the actual toolkit version exposed by version_dir or
                # the running-version tuples instead.
                match = re.search(r"(?:version_dir)\s*[=:]\s*([0-9][^\s\r\n]*)", content)
                if match:
                    versions.append(match.group(1).strip('"\''))
                else:
                    match = re.search(r"running_version\s*=\s*\[[^:]+:([0-9][^\],\s]*)", content)
                    if match:
                        versions.append(match.group(1).strip('"\''))
                    else:
                        match = re.search(r"(?:version|Version)\s*[=:]\s*([0-9][^\s\r\n]*)", content)
                        if match:
                            versions.append(match.group(1).strip('"\''))
    return {
        "ok": bool(roots),
        "roots": [str(root) for root in roots],
        "version_files": version_files,
        "versions": sorted(set(versions)),
        "env": {
            name: os.environ.get(name)
            for name in ("ASCEND_TOOLKIT_HOME", "ASCEND_HOME_PATH", "ASCEND_OPP_PATH")
            if os.environ.get(name)
        },
    }


def _adapter_import_audit(root: Path) -> Dict[str, Any]:
    forbidden = set(FORBIDDEN_PACKAGES)
    found: Dict[str, list[str]] = {}
    missing: list[str] = []
    for filename in ADAPTER_FILES:
        path = root / filename
        if not path.is_file():
            missing.append(filename)
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            missing.append(filename)
            continue
        for name in IMPORT_RE.findall(text):
            if name in forbidden:
                found.setdefault(name, []).append(filename)
    return {"ok": not found and not missing, "forbidden_imports": found, "missing_files": missing}


def run(profile: str, registry_path: Path, root: Path) -> Dict[str, Any]:
    checks: Dict[str, Any] = {}
    failures: list[str] = []
    try:
        from case9_model_profiles import load_profiles

        registry = load_profiles(registry_path)
        selected = registry.get(profile)
        checks["profile"] = {
            "id": selected.id,
            "board_soc": selected.board_soc,
            "board_tier": selected.board_tier,
            "status": selected.status,
        }
        if selected.status in {"blocked", "not-run"}:
            failures.append("selected profile is %s" % selected.status)
    except Exception as exc:
        return {"ok": False, "failures": ["profile registry: %s" % exc], "checks": {}}

    checks["python"] = {
        "executable": sys.executable,
        "version": "%d.%d.%d" % sys.version_info[:3],
        "conda_prefix": os.environ.get("CONDA_PREFIX"),
        # The verified board image keeps MindSpore/MindNLP in the base user
        # site.  Record the interpreter boundary so a later clean-environment
        # run cannot be mistaken for the same deployment.
        "user_site": site.getusersitepackages(),
        "user_site_enabled": bool(site.ENABLE_USER_SITE),
        "python_no_user_site": os.environ.get("PYTHONNOUSERSITE"),
    }
    if sys.version_info < (3, 9):
        failures.append("Python 3.9 or newer is required")
    conda_prefix = os.environ.get("CONDA_PREFIX", "").strip()
    if not conda_prefix:
        failures.append("CONDA_PREFIX is empty; activate the intended conda environment")
    elif not str(Path(sys.executable).resolve()).startswith(str(Path(conda_prefix).resolve()) + os.sep):
        failures.append("Python executable is outside CONDA_PREFIX")

    cann = _cann_info()
    checks["cann"] = cann
    if not cann["ok"]:
        failures.append("CANN toolkit root is not visible after sourcing set_env.sh")

    soc = _detect_soc()
    checks["npu"] = soc
    expected_soc = str(checks["profile"]["board_soc"]).upper()
    observed_soc = str(soc.get("chip") or "").upper()
    if not soc.get("ok"):
        failures.append("npu-smi could not prove a visible 310B device")
    elif observed_soc != expected_soc.replace("ASCEND", ""):
        failures.append("profile expects %s but npu-smi reported %s" % (expected_soc, observed_soc or "unknown"))

    forbidden: Dict[str, Optional[str]] = {}
    for package in FORBIDDEN_PACKAGES:
        origin = _find_spec(package)
        if origin:
            forbidden[package] = origin
    checks["forbidden_packages"] = {"present": forbidden, "dirty_base": bool(forbidden)}

    adapter = _adapter_import_audit(root)
    checks["adapter_import_audit"] = adapter
    if not adapter["ok"]:
        failures.append("adapter source imports a forbidden package or is missing")

    try:
        mindspore = importlib.import_module("mindspore")
        mindnlp = importlib.import_module("mindnlp")
        checks["packages"] = {
            "mindspore": _version("mindspore"),
            "mindnlp": _version("mindnlp"),
            "numpy": _version("numpy"),
            "mindspore_import": True,
            "mindnlp_import": True,
            "mindspore_module": getattr(mindspore, "__file__", None),
            "mindnlp_module": getattr(mindnlp, "__file__", None),
        }
        set_context = getattr(mindspore, "set_context", None)
        get_context = getattr(mindspore, "get_context", None)
        if not callable(set_context) or not callable(get_context):
            raise RuntimeError("MindSpore context API is unavailable")
        device_id = int(os.environ.get("CASE9_DEVICE_ID", "0"))
        set_context(device_target="Ascend", device_id=device_id)
        target = str(get_context("device_target"))
        checks["mindspore_context"] = {"device_target": target, "device_id": device_id}
        if target.lower() != "ascend":
            failures.append("MindSpore selected %s instead of Ascend" % target)
    except Exception as exc:
        checks["packages"] = {
            "mindspore": _version("mindspore"),
            "mindnlp": _version("mindnlp"),
            "mindspore_import": False,
            "mindnlp_import": False,
            "mindspore_module": None,
            "mindnlp_module": None,
        }
        failures.append("MindSpore/MindNLP Ascend import failed: %s" % exc)

    checks["disk"] = _command(["df", "-Pk", str(root)])
    result = {"ok": not failures, "profile_id": profile, "failures": failures, "checks": checks}
    return _json_safe(result)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--registry", type=Path, default=None)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--json", action="store_true", help="emit JSON only")
    args = parser.parse_args(argv)
    root = args.root.expanduser().resolve()
    registry = args.registry or (root / "configs" / "chat_model_profiles.json")
    result = run(args.profile, registry.expanduser().resolve(), root)
    if args.json:
        print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
