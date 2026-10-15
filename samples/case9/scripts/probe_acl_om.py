#!/usr/bin/env python3
"""Record a non-destructive ACL load and descriptor probe for one OM file.

This utility deliberately stops before ``acl.mdl.execute``.  Model-specific
runtime tests own execution because an OM descriptor alone cannot establish
the semantic meaning of inputs, outputs, KV cache, masks, or tokenization.
It is intended to establish the first board-side evidence gate consistently
for every Case9 LLM OM artifact.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple


class ProbeError(RuntimeError):
    """An ACL operation did not complete successfully."""


def _split(value: Any) -> Tuple[Any, Any]:
    if isinstance(value, tuple) and len(value) == 2:
        return value[0], value[1]
    return value, 0


def _check(value: Any, operation: str) -> None:
    if isinstance(value, tuple) and len(value) == 2:
        value = value[1]
    if value is None:
        return
    if isinstance(value, bool):
        if not value:
            raise ProbeError(f"{operation} failed")
        return
    if isinstance(value, int) and value != 0:
        raise ProbeError(f"{operation} failed with ACL status {value}")


def _first(value: Any, operation: str) -> Any:
    if isinstance(value, tuple) and len(value) == 2:
        _check(value[1], operation)
        return value[0]
    return value


def _shape(value: Any) -> List[int]:
    value = _first(value, "ACL descriptor dimensions")
    if hasattr(value, "dims"):
        value = value.dims
    if isinstance(value, dict):
        value = value.get("dims", value.get("shape"))
    if not isinstance(value, (list, tuple)):
        raise ProbeError("ACL descriptor dimensions are unreadable")
    result: List[int] = []
    for item in value:
        item = getattr(item, "dim", item)
        if isinstance(item, bool) or not isinstance(item, int):
            raise ProbeError("ACL descriptor has a non-integer dimension")
        result.append(int(item))
    return result


def _dtype_name(acl: Any, code: int) -> str:
    known = {
        int(getattr(acl, "ACL_FLOAT", 0)): "float32",
        int(getattr(acl, "ACL_FLOAT16", 1)): "float16",
        int(getattr(acl, "ACL_INT8", 2)): "int8",
        int(getattr(acl, "ACL_INT32", 3)): "int32",
        int(getattr(acl, "ACL_UINT8", 4)): "uint8",
        int(getattr(acl, "ACL_INT16", 6)): "int16",
        int(getattr(acl, "ACL_UINT16", 7)): "uint16",
        int(getattr(acl, "ACL_UINT32", 8)): "uint32",
        int(getattr(acl, "ACL_INT64", 9)): "int64",
        int(getattr(acl, "ACL_UINT64", 10)): "uint64",
        int(getattr(acl, "ACL_DOUBLE", 11)): "float64",
        int(getattr(acl, "ACL_BOOL", 12)): "bool",
    }
    return known.get(int(code), f"acl_dtype_{int(code)}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _npu_smi() -> Dict[str, Any]:
    try:
        completed = subprocess.run(
            ["npu-smi", "info"],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    return {
        "available": completed.returncode == 0,
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _describe(acl: Any, desc: Any, is_input: bool) -> List[Dict[str, Any]]:
    kind = "input" if is_input else "output"
    count_fn = getattr(acl.mdl, f"get_num_{kind}s", None)
    size_fn = getattr(acl.mdl, f"get_{kind}_size_by_index", None)
    name_fn = getattr(acl.mdl, f"get_{kind}_name_by_index", None)
    dims_fn = getattr(acl.mdl, f"get_{kind}_dims", None)
    dtype_fn = getattr(acl.mdl, f"get_{kind}_data_type", None)
    if not all(callable(item) for item in (count_fn, size_fn, dims_fn, dtype_fn)):
        raise ProbeError(f"ACL Python binding cannot read {kind} descriptors")
    count = int(_first(count_fn(desc), f"get_num_{kind}s"))
    tensors: List[Dict[str, Any]] = []
    for index in range(count):
        raw_name = _first(name_fn(desc, index), f"get_{kind}_name_by_index") if callable(name_fn) else f"{kind}_{index}"
        if isinstance(raw_name, bytes):
            raw_name = raw_name.decode("utf-8", errors="replace")
        dtype = int(_first(dtype_fn(desc, index), f"get_{kind}_data_type"))
        tensors.append(
            {
                "index": index,
                "name": str(raw_name),
                "shape": _shape(dims_fn(desc, index)),
                "acl_dtype_code": dtype,
                "dtype": _dtype_name(acl, dtype),
                "byte_size": int(_first(size_fn(desc, index), f"get_{kind}_size_by_index")),
            }
        )
    return tensors


def _write_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def probe(om_path: Path, device_id: int) -> Dict[str, Any]:
    if not om_path.is_file():
        raise ProbeError(f"OM file does not exist: {om_path}")
    started = datetime.now(timezone.utc).isoformat()
    result: Dict[str, Any] = {
        "schema_version": 1,
        "kind": "acl_descriptor_probe",
        "started_at": started,
        "om": {
            "path": str(om_path.resolve()),
            "bytes": om_path.stat().st_size,
            "sha256": _sha256(om_path),
        },
        "device_id": device_id,
        "npu_smi_before": _npu_smi(),
        "status": "failed",
    }
    acl: Optional[Any] = None
    context: Any = None
    stream: Any = None
    model_id: Any = None
    desc: Any = None
    try:
        import acl as acl_module  # type: ignore

        acl = acl_module
        _check(acl.init(), "acl.init")
        _check(acl.rt.set_device(device_id), "acl.rt.set_device")
        context, status = _split(acl.rt.create_context(device_id))
        _check(status, "acl.rt.create_context")
        stream, status = _split(acl.rt.create_stream())
        _check(status, "acl.rt.create_stream")
        model_id, status = _split(acl.mdl.load_from_file(str(om_path)))
        _check(status, "acl.mdl.load_from_file")
        desc = acl.mdl.create_desc()
        if desc is None:
            raise ProbeError("acl.mdl.create_desc returned no descriptor")
        _check(acl.mdl.get_desc(desc, model_id), "acl.mdl.get_desc")
        result["inputs"] = _describe(acl, desc, True)
        result["outputs"] = _describe(acl, desc, False)
        result["status"] = "passed"
    except Exception as exc:
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}
    finally:
        cleanup: List[Dict[str, Any]] = []
        for label, func, args in (
            ("acl.mdl.unload", getattr(getattr(acl, "mdl", None), "unload", None) if acl is not None else None, (model_id,)),
            ("acl.mdl.destroy_desc", getattr(getattr(acl, "mdl", None), "destroy_desc", None) if acl is not None else None, (desc,)),
            ("acl.rt.destroy_stream", getattr(getattr(acl, "rt", None), "destroy_stream", None) if acl is not None else None, (stream,)),
            ("acl.rt.destroy_context", getattr(getattr(acl, "rt", None), "destroy_context", None) if acl is not None else None, (context,)),
            ("acl.finalize", getattr(acl, "finalize", None) if acl is not None else None, ()),
        ):
            if func is None or (bool(args) and args[0] is None):
                continue
            try:
                _check(func(*args), label)
                cleanup.append({"operation": label, "status": "passed"})
            except Exception as exc:  # Preserve the original load failure too.
                cleanup.append({"operation": label, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        result["cleanup"] = cleanup
        result["npu_smi_after"] = _npu_smi()
        result["finished_at"] = datetime.now(timezone.utc).isoformat()
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--om", required=True, type=Path)
    parser.add_argument("--report", required=True, type=Path)
    parser.add_argument("--device-id", type=int, default=0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = probe(args.om.expanduser(), args.device_id)
    _write_json(args.report.expanduser(), result)
    print(json.dumps({"status": result["status"], "report": str(args.report.expanduser())}, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
