"""Fail-closed runtime lock checks for the MindSpore worker."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import site
import sysconfig
from typing import Any, Dict, Mapping, Optional

from .mindspore_qwen import _cann_version


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RUNTIME_LOCK_PATH = PROJECT_ROOT / "configs" / "release-artifacts.lock.json"
UNIFIED_CONDA_ENV = "ascend"
UNIFIED_SOABI = "cpython-39-aarch64-linux-gnu"


class RuntimePreflightError(RuntimeError):
    """A locked board runtime prerequisite is absent or has drifted."""

    def __init__(self, details: Mapping[str, Any]) -> None:
        self.details = dict(details)
        errors = self.details.get("errors", [])
        summary = "; ".join(
            str(item.get("field", "runtime"))
            for item in errors
            if isinstance(item, Mapping)
        )
        super().__init__("runtime_lock_failed" + (": " + summary if summary else ""))

    def as_json(self) -> str:
        return json.dumps(self.details, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _error(field: str, expected: Any, observed: Any) -> Dict[str, Any]:
    return {"field": field, "expected": expected, "observed": observed}


def _load_lock(path: Path) -> Mapping[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimePreflightError(
            {
                "status": "failed",
                "code": "runtime_lock_unreadable",
                "lock_path": str(path),
                "errors": [{"field": "runtime_lock", "expected": "valid JSON", "observed": str(exc)}],
            }
        ) from exc
    if not isinstance(document, Mapping) or not isinstance(document.get("target"), Mapping):
        raise RuntimePreflightError(
            {
                "status": "failed",
                "code": "runtime_lock_invalid",
                "lock_path": str(path),
                "errors": [{"field": "target", "expected": "object", "observed": type(document).__name__}],
            }
        )
    return document


def _package_version(name: str) -> Optional[str]:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    except Exception:
        return None


def _observe_runtime() -> Dict[str, Any]:
    imports: Dict[str, bool] = {}
    for name in ("acl", "mindspore", "mindnlp"):
        try:
            importlib.import_module(name)
        except Exception:
            imports[name] = False
        else:
            imports[name] = True
    return {
        "python": platform.python_version(),
        "soabi": sysconfig.get_config_var("SOABI"),
        "conda_env": os.environ.get("CONDA_DEFAULT_ENV"),
        "python_no_user_site": os.environ.get("PYTHONNOUSERSITE"),
        "user_site_enabled": bool(site.ENABLE_USER_SITE),
        "imports": imports,
        "package_versions": {
            "mindspore": _package_version("mindspore"),
            "mindnlp": _package_version("mindnlp"),
        },
        "cann": _cann_version(),
    }


def validate_runtime_lock_data(
    lock: Mapping[str, Any],
    observed: Mapping[str, Any],
    *,
    strict_target: bool = True,
) -> Dict[str, Any]:
    """Compare observed process facts against the locked release runtime.

    ``observed`` is an explicit data boundary so controller tests can validate
    the policy without importing board-only libraries. Production callers use
    :func:`validate_runtime_lock` to collect those facts in the worker process.
    """

    target = lock.get("target")
    if not isinstance(target, Mapping):
        raise RuntimePreflightError(
            {
                "status": "failed",
                "code": "runtime_lock_invalid",
                "errors": [{"field": "target", "expected": "object", "observed": type(target).__name__}],
            }
        )
    required = ("cann", "mindspore", "mindnlp", "python")
    missing = [name for name in required if not isinstance(target.get(name), str) or not target[name]]
    if missing:
        raise RuntimePreflightError(
            {
                "status": "failed",
                "code": "runtime_lock_invalid",
                "errors": [{"field": "target", "expected": "required runtime keys", "observed": missing}],
            }
        )

    errors = []
    if observed.get("conda_env") != UNIFIED_CONDA_ENV:
        errors.append(_error("conda_env", UNIFIED_CONDA_ENV, observed.get("conda_env")))
    if observed.get("python_no_user_site") != "1":
        errors.append(_error("PYTHONNOUSERSITE", "1", observed.get("python_no_user_site")))
    if observed.get("user_site_enabled") is not False:
        errors.append(_error("user_site_enabled", False, observed.get("user_site_enabled")))
    if observed.get("python") != target["python"]:
        errors.append(_error("python", target["python"], observed.get("python")))
    if observed.get("soabi") != UNIFIED_SOABI:
        errors.append(_error("soabi", UNIFIED_SOABI, observed.get("soabi")))

    imports = observed.get("imports")
    if not isinstance(imports, Mapping):
        imports = {}
    for name in ("acl", "mindspore", "mindnlp"):
        if imports.get(name) is not True:
            errors.append(_error("import." + name, True, imports.get(name)))

    versions = observed.get("package_versions")
    if not isinstance(versions, Mapping):
        versions = {}
    if strict_target:
        for name in ("mindspore", "mindnlp"):
            if versions.get(name) != target[name]:
                errors.append(_error(name, target[name], versions.get(name)))
        if observed.get("cann") != target["cann"]:
            errors.append(_error("cann", target["cann"], observed.get("cann")))
    else:
        for name in ("mindspore", "mindnlp"):
            if not isinstance(versions.get(name), str) or not versions[name]:
                errors.append(_error(name, "installed package", versions.get(name)))
        if not isinstance(observed.get("cann"), str) or not observed["cann"]:
            errors.append(_error("cann", "sourced CANN runtime", observed.get("cann")))

    result: Dict[str, Any] = {
        "status": "passed" if not errors else "failed",
        "code": "runtime_lock_passed" if not errors else "runtime_lock_failed",
        "strict_target": strict_target,
        "expected": {
            "conda_env": UNIFIED_CONDA_ENV,
            "python": target["python"],
            "soabi": UNIFIED_SOABI,
            "cann": target["cann"] if strict_target else "sourced CANN runtime",
            "mindspore": target["mindspore"] if strict_target else "installed package",
            "mindnlp": target["mindnlp"] if strict_target else "installed package",
        },
        "observed": dict(observed),
        "errors": errors,
    }
    if errors:
        raise RuntimePreflightError(result)
    return result


def validate_runtime_lock(
    path: Path = DEFAULT_RUNTIME_LOCK_PATH,
    *,
    strict_target: bool = True,
) -> Dict[str, Any]:
    """Verify unified runtime facts, optionally against the release target lock.

    Formal 20T release runs use ``strict_target=True``. Teaching profiles still
    require the unified Python/ABI/CANN/ACL gate, but permit their separately
    documented CANN 7 or CANN 8 framework pairing.
    """

    lock_path = Path(path).expanduser().resolve()
    lock = _load_lock(lock_path)
    try:
        result = validate_runtime_lock_data(
            lock, _observe_runtime(), strict_target=strict_target
        )
    except RuntimePreflightError as exc:
        details = dict(exc.details)
        details.setdefault("lock_path", str(lock_path))
        raise RuntimePreflightError(details) from exc
    result["lock_path"] = str(lock_path)
    return result


__all__ = [
    "DEFAULT_RUNTIME_LOCK_PATH",
    "RuntimePreflightError",
    "UNIFIED_CONDA_ENV",
    "UNIFIED_SOABI",
    "validate_runtime_lock",
    "validate_runtime_lock_data",
]
