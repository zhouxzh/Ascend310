"""Board-only launcher for the tracked Qwen2.5 StaticKV ACL runtime.

This module deliberately accepts every artifact path from its caller. It does
not read repository configuration, download a model, or fall back to a
MindSpore, Torch, CPU, or remote backend.
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import platform
import re
import site
import sys
import sysconfig
from typing import Any, Dict, Mapping, Optional, Tuple, Union

from .runtime import (
    FORCED_DECODE_MAX_GENERATION_TOKENS,
    FORCED_DECODE_MAX_TIMEOUT_SECONDS,
    HARD_MAX_GENERATION_TOKENS,
    MAX_EXECUTION_TIMEOUT_SECONDS,
    Qwen25AclRuntime,
    RuntimeUnavailable,
    detect_soc_version,
    verify_artifact_locks,
)
from .service import Qwen25StaticKvService, make_server


RELEASE_CONDA_ENV = "ascend"
RELEASE_PYTHON_VERSION = "3.9.25"
RELEASE_SOABI = "cpython-39-aarch64-linux-gnu"
RELEASE_CANN_VERSION = "8.0.0"
RELEASE_SOC_VERSION = "Ascend310B4"
LEGACY_ACL_CONDA_ENV = "case" + "9-acl-om"
_PROHIBITED_IMPORTED_MODULES = (
    "torch",
    "torch_npu",
    "torchaudio",
    "transformers",
    "onnxruntime",
    "mindspore",
    "mindnlp",
    "mindtorch",
    "vllm",
    "mindie",
)


class StaticKvLaunchError(RuntimeError):
    """The current process cannot safely launch the StaticKV ACL service."""


def _cann_version() -> Optional[str]:
    roots = []
    for name in ("ASCEND_TOOLKIT_HOME", "ASCEND_HOME_PATH", "ASCEND_INSTALL_PATH"):
        raw = os.environ.get(name, "").strip()
        if raw:
            path = Path(raw).expanduser()
            if path not in roots:
                roots.append(path)
    roots.extend(
        path
        for path in (Path("/usr/local/Ascend/ascend-toolkit"), Path("/usr/local/Ascend/latest"))
        if path not in roots
    )
    for root in roots:
        for relative in ("version.cfg", "latest/version.cfg", "version.info"):
            path = root / relative
            if not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for pattern in (
                r"version_dir\s*[=:]\s*([0-9][^\s\r\n]*)",
                r"running_version\s*=\s*\[[^:]+:([0-9][^\],\s]*)",
                r"(?:version|Version)\s*[=:]\s*([0-9][^\s\r\n]*)",
            ):
                match = re.search(pattern, content)
                if match:
                    return match.group(1).strip("\"'")
    return None


def _loaded_prohibited_modules() -> Tuple[str, ...]:
    loaded = []
    for root in _PROHIBITED_IMPORTED_MODULES:
        if root in sys.modules or any(name.startswith(root + ".") for name in sys.modules):
            loaded.append(root)
    return tuple(loaded)


def _observe_environment() -> Dict[str, Any]:
    try:
        importlib.import_module("acl")
    except Exception as exc:
        acl_import = {"ok": False, "error": type(exc).__name__}
    else:
        acl_import = {"ok": True}
    return {
        "python": platform.python_version(),
        "soabi": sysconfig.get_config_var("SOABI"),
        "conda_env": os.environ.get("CONDA_DEFAULT_ENV"),
        "conda_prefix": os.environ.get("CONDA_PREFIX"),
        "sys_prefix": sys.prefix,
        "python_executable": sys.executable,
        "python_no_user_site": os.environ.get("PYTHONNOUSERSITE"),
        "user_site_enabled": bool(site.ENABLE_USER_SITE),
        "cann": _cann_version(),
        "acl_import": acl_import,
        "prohibited_imports": list(_loaded_prohibited_modules()),
    }


def validate_release_environment(
    observed: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Require the unified board process policy without package-presence scans.

    The shared ``ascend`` environment may legitimately *contain* MindSpore or
    Torch for other release routes. This static runtime rejects only modules
    already imported into this process, so its own ACL/Numpy/tokenizers route
    remains isolated without treating another installed package as a failure.
    """

    facts = dict(_observe_environment() if observed is None else observed)
    errors = []
    expected = {
        "conda_env": RELEASE_CONDA_ENV,
        "python": RELEASE_PYTHON_VERSION,
        "soabi": RELEASE_SOABI,
        "cann": RELEASE_CANN_VERSION,
        "python_no_user_site": "1",
        "user_site_enabled": False,
        "acl_import": True,
    }
    for field, value in (
        ("conda_env", facts.get("conda_env")),
        ("python", facts.get("python")),
        ("soabi", facts.get("soabi")),
        ("cann", facts.get("cann")),
        ("python_no_user_site", facts.get("python_no_user_site")),
        ("user_site_enabled", facts.get("user_site_enabled")),
    ):
        if value != expected[field]:
            errors.append({"field": field, "expected": expected[field], "observed": value})
    conda_prefix = facts.get("conda_prefix")
    sys_prefix = facts.get("sys_prefix")
    executable = facts.get("python_executable")
    if not isinstance(conda_prefix, str) or not conda_prefix:
        errors.append({"field": "CONDA_PREFIX", "expected": "active ascend prefix", "observed": conda_prefix})
    elif not isinstance(sys_prefix, str) or Path(sys_prefix).resolve() != Path(conda_prefix).resolve():
        errors.append({"field": "sys.prefix", "expected": conda_prefix, "observed": sys_prefix})
    elif (
        not isinstance(executable, str)
        or Path(executable).resolve().parent != (Path(conda_prefix).resolve() / "bin")
    ):
        errors.append(
            {
                "field": "python_executable",
                "expected": str(Path(conda_prefix) / "bin" / "python"),
                "observed": executable,
            }
        )
    acl_import = facts.get("acl_import")
    acl_ok = acl_import.get("ok") if isinstance(acl_import, Mapping) else False
    if acl_ok is not True:
        errors.append({"field": "acl_import", "expected": True, "observed": acl_import})
    imported = facts.get("prohibited_imports", ())
    if not isinstance(imported, (list, tuple)):
        imported = ("invalid-value",)
    if imported:
        errors.append(
            {
                "field": "process_imports",
                "expected": "no MindSpore/Torch/Transformers/ONNX Runtime imports",
                "observed": list(imported),
            }
        )
    report = {
        "status": "passed" if not errors else "failed",
        "launch_policy": "PASS" if not errors else "FAIL",
        "expected": expected,
        "observed": facts,
        "errors": errors,
    }
    if errors:
        raise StaticKvLaunchError("static KV environment gate failed: %s" % errors)
    return report


def validate_legacy_experimental_environment(
    observed: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Admit the historic 8T ACL env only for explicitly labeled experiments."""

    facts = dict(_observe_environment() if observed is None else observed)
    errors = []
    if facts.get("conda_env") != LEGACY_ACL_CONDA_ENV:
        errors.append({"field": "conda_env", "expected": LEGACY_ACL_CONDA_ENV, "observed": facts.get("conda_env")})
    if not str(facts.get("python", "")).startswith("3.9."):
        errors.append({"field": "python", "expected": "3.9.x", "observed": facts.get("python")})
    if facts.get("cann") != RELEASE_CANN_VERSION:
        errors.append({"field": "cann", "expected": RELEASE_CANN_VERSION, "observed": facts.get("cann")})
    if facts.get("python_no_user_site") != "1" or facts.get("user_site_enabled") is not False:
        errors.append({"field": "user_site", "expected": "disabled", "observed": facts.get("user_site_enabled")})
    acl_import = facts.get("acl_import")
    if not isinstance(acl_import, Mapping) or acl_import.get("ok") is not True:
        errors.append({"field": "acl_import", "expected": True, "observed": acl_import})
    imported = facts.get("prohibited_imports", ())
    if imported:
        errors.append({"field": "process_imports", "expected": [], "observed": list(imported)})
    report = {
        "status": "passed" if not errors else "failed",
        "launch_policy": "EXPERIMENTAL_LEGACY_ENV" if not errors else "FAIL",
        "observed": facts,
        "errors": errors,
    }
    if errors:
        raise StaticKvLaunchError("legacy experimental environment gate failed: %s" % errors)
    return report


def _regular_path(value: Union[str, Path], label: str) -> Path:
    path = Path(value).expanduser()
    if path.is_symlink() or not path.is_file():
        raise StaticKvLaunchError("%s must be a regular file: %s" % (label, path))
    return path


def validate_release_paths(
    *,
    om: Union[str, Path],
    tokenizer: Union[str, Path],
    tokenizer_config: Union[str, Path],
    controller_contract: Union[str, Path],
    runtime_contract: Union[str, Path],
    om_lock: Union[str, Path],
    tokenizer_lock: Union[str, Path],
    expected_soc_version: str = RELEASE_SOC_VERSION,
) -> Dict[str, Any]:
    """Validate explicit schema-2 artifact paths before opening ACL."""

    if expected_soc_version not in {"Ascend310B4", "Ascend310B1"}:
        raise StaticKvLaunchError(
            "StaticKV target must be Ascend310B4 or Ascend310B1"
        )
    actual_soc = detect_soc_version()
    if actual_soc != expected_soc_version:
        raise StaticKvLaunchError(
            "npu-smi reported %r; formal StaticKV target is %s"
            % (actual_soc, expected_soc_version)
        )
    paths = {
        "om": _regular_path(om, "OM"),
        "tokenizer": _regular_path(tokenizer, "tokenizer"),
        "tokenizer_config": _regular_path(tokenizer_config, "tokenizer config"),
        "controller_contract": _regular_path(controller_contract, "controller contract"),
        "runtime_contract": _regular_path(runtime_contract, "runtime contract"),
        "om_lock": _regular_path(om_lock, "OM lock"),
        "tokenizer_lock": _regular_path(tokenizer_lock, "tokenizer lock"),
    }
    try:
        report = verify_artifact_locks(
            paths["om"],
            paths["tokenizer"],
            paths["controller_contract"],
            paths["runtime_contract"],
            lock_path=paths["om_lock"],
            tokenizer_lock_path=paths["tokenizer_lock"],
            expected_soc_version=expected_soc_version,
        )
    except RuntimeUnavailable as exc:
        raise StaticKvLaunchError("static KV artifact gate failed: %s" % exc) from exc
    report["paths"] = {name: str(path) for name, path in paths.items()}
    return report


def create_release_service(
    *,
    om: Union[str, Path],
    tokenizer: Union[str, Path],
    tokenizer_config: Union[str, Path],
    controller_contract: Union[str, Path],
    runtime_contract: Union[str, Path],
    om_lock: Union[str, Path],
    tokenizer_lock: Union[str, Path],
    max_tokens: int = 80,
    execution_timeout_seconds: float = MAX_EXECUTION_TIMEOUT_SECONDS,
    device_id: int = 0,
    expected_soc_version: str = RELEASE_SOC_VERSION,
    sentence_stop_enabled: bool = True,
    force_continue: bool = False,
    experimental_candidate: bool = False,
    allow_legacy_environment: bool = False,
) -> Qwen25StaticKvService:
    """Build one validated, stopped StaticKV service from explicit paths."""

    if not isinstance(allow_legacy_environment, bool):
        raise StaticKvLaunchError("allow_legacy_environment must be boolean")
    environment = (
        validate_legacy_experimental_environment()
        if allow_legacy_environment
        else validate_release_environment()
    )
    validate_release_paths(
        om=om,
        tokenizer=tokenizer,
        tokenizer_config=tokenizer_config,
        controller_contract=controller_contract,
        runtime_contract=runtime_contract,
        om_lock=om_lock,
        tokenizer_lock=tokenizer_lock,
        expected_soc_version=expected_soc_version,
    )
    if not isinstance(force_continue, bool) or not isinstance(experimental_candidate, bool):
        raise StaticKvLaunchError("force_continue and experimental_candidate must be boolean")
    if force_continue and not experimental_candidate:
        raise StaticKvLaunchError("force_continue is only admitted for an isolated candidate worker")
    if force_continue and expected_soc_version not in {"Ascend310B1", "Ascend310B4"}:
        raise StaticKvLaunchError("force_continue requires an isolated Ascend310B1 or Ascend310B4 candidate")
    max_generation_tokens = (
        FORCED_DECODE_MAX_GENERATION_TOKENS
        if force_continue
        else HARD_MAX_GENERATION_TOKENS
    )
    if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or not 1 <= max_tokens <= max_generation_tokens:
        raise StaticKvLaunchError(
            "max_tokens must be between 1 and %d" % max_generation_tokens
        )
    max_timeout = (
        FORCED_DECODE_MAX_TIMEOUT_SECONDS
        if force_continue
        else MAX_EXECUTION_TIMEOUT_SECONDS
    )
    if (
        isinstance(execution_timeout_seconds, bool)
        or not isinstance(execution_timeout_seconds, (int, float))
        or not 1.0 <= float(execution_timeout_seconds) <= max_timeout
    ):
        raise StaticKvLaunchError(
            "execution_timeout_seconds must be between 1 and %d"
            % int(max_timeout)
        )
    runtime = Qwen25AclRuntime(
        om,
        tokenizer,
        contract_path=runtime_contract,
        controller_contract_path=controller_contract,
        tokenizer_config_path=tokenizer_config,
        lock_path=om_lock,
        tokenizer_lock_path=tokenizer_lock,
        expected_soc_version=expected_soc_version,
        max_tokens=max_tokens,
        execution_timeout_seconds=float(execution_timeout_seconds),
        device_id=device_id,
        sentence_stop_enabled=sentence_stop_enabled,
        force_continue=force_continue,
    )
    return Qwen25StaticKvService(runtime, launch_policy=environment)


def start_release_service(
    *,
    host: str,
    port: int,
    om: Union[str, Path],
    tokenizer: Union[str, Path],
    tokenizer_config: Union[str, Path],
    controller_contract: Union[str, Path],
    runtime_contract: Union[str, Path],
    om_lock: Union[str, Path],
    tokenizer_lock: Union[str, Path],
    max_tokens: int = 80,
    execution_timeout_seconds: float = MAX_EXECUTION_TIMEOUT_SECONDS,
    device_id: int = 0,
    expected_soc_version: str = RELEASE_SOC_VERSION,
    sentence_stop_enabled: bool = True,
    force_continue: bool = False,
    experimental_candidate: bool = False,
    allow_legacy_environment: bool = False,
) -> Tuple[Any, Qwen25StaticKvService]:
    """Open one explicitly validated B1/B4 StaticKV OM on loopback."""

    if host != "127.0.0.1":
        raise StaticKvLaunchError("StaticKV service must remain loopback-only")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise StaticKvLaunchError("port must be between 1 and 65535")
    service = create_release_service(
        om=om,
        tokenizer=tokenizer,
        tokenizer_config=tokenizer_config,
        controller_contract=controller_contract,
        runtime_contract=runtime_contract,
        om_lock=om_lock,
        tokenizer_lock=tokenizer_lock,
        max_tokens=max_tokens,
        execution_timeout_seconds=execution_timeout_seconds,
        device_id=device_id,
        expected_soc_version=expected_soc_version,
        sentence_stop_enabled=sentence_stop_enabled,
        force_continue=force_continue,
        experimental_candidate=experimental_candidate,
        allow_legacy_environment=allow_legacy_environment,
    )
    try:
        service.runtime.start()
        server = make_server(host, port, service)
    except Exception:
        service.runtime.close()
        raise
    return server, service


__all__ = [
    "RELEASE_CANN_VERSION",
    "RELEASE_CONDA_ENV",
    "RELEASE_PYTHON_VERSION",
    "RELEASE_SOC_VERSION",
    "StaticKvLaunchError",
    "create_release_service",
    "start_release_service",
    "validate_release_environment",
    "validate_release_paths",
]
