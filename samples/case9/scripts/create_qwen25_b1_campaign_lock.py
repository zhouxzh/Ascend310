#!/usr/bin/env python3
"""Create a strict, campaign-local B1 Qwen2.5 Static-KV OM lock.

The historical B1 lock records the controller/export contract hash.  The ACL
service, however, binds the ATC-produced descriptor contract at runtime.  This
tool preserves the historical lock unchanged and creates a new schema-v2 lock
that records both hashes explicitly.

It intentionally has no ACL, ATC, download, package-install, or network path.
All artifact arguments are POSIX-relative paths below one explicitly supplied
campaign root.  The known B1 Qwen2.5 artifacts are allowlisted by exact byte
size and SHA-256, and the output is published atomically without overwriting an
existing file.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple


MODEL_ID = "qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om"
SOC_VERSION = "Ascend310B1"
CHIP_TIER = "20T"

# This is deliberately a single-artifact tool, not a generic lock generator.
# The values are the immutable controller-side reproducibility bundle records.
EXPECTED_B1: Dict[str, Dict[str, Any]] = {
    "om": {
        "bytes": 1266009438,
        "sha256": "6bca884fbce746efdb02f8c9294cad5b2faa6c8b96cac9ec8c83730126298609",
    },
    "controller_contract": {
        "bytes": 34912,
        "sha256": "fcc423f24fdf36c5d364ad7bd535943fb0c8545ab546a5724ab0261330639498",
    },
    "runtime_contract": {
        "bytes": 32225,
        "sha256": "3ff15ccf682e85e32f521417aa7204347ff5ab4fb0608edd18f3c9a639af3c21",
    },
    "historical_lock": {
        "bytes": 793,
        "sha256": "47a4030202342132d5794bea1e1e5ae19316f6c132b2fa00895df87b4b9d3272",
    },
}

SHA256_RE = re.compile(r"[0-9a-f]{64}")
TIMESTAMP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


class CampaignLockError(ValueError):
    """Raised when immutable B1 campaign-lock admission fails."""


def sha256_file(path: Path) -> Tuple[int, str]:
    """Return byte size and SHA-256 without reading an artifact into memory."""

    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            digest.update(block)
    return size, digest.hexdigest()


def _safe_relative(value: str, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value:
        raise CampaignLockError("%s must be a non-empty POSIX relative path" % label)
    if "\\" in value or ":" in value:
        raise CampaignLockError("%s must be a POSIX relative path" % label)
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in ("", ".", "..") for part in path.parts):
        raise CampaignLockError("%s escapes the campaign root: %s" % (label, value))
    return path


def _root_path(value: str) -> Path:
    source = Path(value).expanduser()
    if source.is_symlink():
        raise CampaignLockError("campaign root must not be a symlink: %s" % source)
    if not source.is_dir():
        raise CampaignLockError("campaign root is not a directory: %s" % source)
    return source.resolve(strict=True)


def _path_below_root(root: Path, relative: PurePosixPath, label: str, *, exists: bool) -> Path:
    candidate = root.joinpath(*relative.parts)
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise CampaignLockError("%s may not traverse a symlink: %s" % (label, relative.as_posix()))
    if exists:
        if not candidate.is_file():
            raise CampaignLockError("%s is not a regular file: %s" % (label, relative.as_posix()))
        resolved = candidate.resolve(strict=True)
        if root not in resolved.parents:
            raise CampaignLockError("%s escapes the campaign root: %s" % (label, relative.as_posix()))
    else:
        if candidate.exists() or candidate.is_symlink():
            raise CampaignLockError("refusing to overwrite existing %s: %s" % (label, relative.as_posix()))
        parent = candidate.parent
        if not parent.is_dir():
            raise CampaignLockError("%s parent does not exist: %s" % (label, parent))
        resolved_parent = parent.resolve(strict=True)
        if resolved_parent != root and root not in resolved_parent.parents:
            raise CampaignLockError("%s parent escapes the campaign root: %s" % (label, relative.as_posix()))
    return candidate


def _load_json_object(path: Path, label: str) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CampaignLockError("%s is not valid JSON: %s" % (label, path)) from exc
    if not isinstance(value, dict):
        raise CampaignLockError("%s JSON root must be an object" % label)
    return value


def _verify_exact(path: Path, expected: Mapping[str, Any], label: str) -> Dict[str, Any]:
    size, digest = sha256_file(path)
    expected_size = expected.get("bytes")
    expected_sha = str(expected.get("sha256", "")).lower()
    if size != expected_size:
        raise CampaignLockError("%s byte size mismatch: expected=%s actual=%s" % (label, expected_size, size))
    if digest != expected_sha:
        raise CampaignLockError("%s SHA-256 mismatch: expected=%s actual=%s" % (label, expected_sha, digest))
    return {"bytes": size, "sha256": digest}


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CampaignLockError("%s must be an object" % label)
    return value


def _require_exact(value: Any, expected: Any, label: str) -> None:
    if value != expected:
        raise CampaignLockError("%s mismatch: expected=%r actual=%r" % (label, expected, value))


def _validate_controller_contract(document: Mapping[str, Any]) -> None:
    model = _require_mapping(document.get("model"), "controller contract model")
    _require_exact(model.get("model_id"), MODEL_ID, "controller contract model_id")
    _require_exact(model.get("family"), "qwen2.5", "controller contract model family")
    static = _require_mapping(document.get("static_onnx"), "controller static_onnx")
    _require_exact(static.get("batch_size"), 1, "controller batch_size")
    _require_exact(static.get("sequence_length"), 1024, "controller sequence_length")
    _require_exact(static.get("cache_layout"), "split", "controller cache_layout")
    _require_exact(static.get("cache_input_shape"), [1, 2, 1024, 64], "controller cache_input_shape")
    _require_exact(static.get("cache_output_shape"), [1, 1, 2, 64], "controller cache_output_shape")
    _validate_acl_layout(_require_mapping(document.get("acl_om"), "controller acl_om"), "controller")


def _validate_acl_layout(layout: Mapping[str, Any], label: str) -> None:
    _require_exact(layout.get("cache_layout"), "split", "%s cache_layout" % label)
    _require_exact(layout.get("cache_shape"), [1, 2, 1024, 64], "%s cache_shape" % label)
    _require_exact(layout.get("static_sequence_length"), 1024, "%s static_sequence_length" % label)
    _require_exact(layout.get("vocabulary_size"), 151936, "%s vocabulary_size" % label)
    _require_exact(layout.get("supported_static_qwen25_layout"), True, "%s static KV admission" % label)
    _require_exact(layout.get("input_order_verified"), True, "%s input order admission" % label)
    inputs = layout.get("inputs")
    outputs = layout.get("outputs")
    if not isinstance(inputs, list) or len(inputs) != 51:
        raise CampaignLockError("%s must contain 51 input descriptors" % label)
    if not isinstance(outputs, list) or len(outputs) != 49:
        raise CampaignLockError("%s must contain 49 output descriptors" % label)


def _validate_runtime_contract(document: Mapping[str, Any], om_meta: Mapping[str, Any]) -> None:
    model = _require_mapping(document.get("model"), "runtime contract model")
    _require_exact(model.get("model_id"), MODEL_ID, "runtime contract model_id")
    _require_exact(model.get("family"), "qwen2.5", "runtime contract model family")
    _validate_acl_layout(_require_mapping(document.get("acl_om"), "runtime acl_om"), "runtime")
    artifact = _require_mapping(document.get("om_artifact"), "runtime om_artifact")
    _require_exact(artifact.get("bytes"), om_meta["bytes"], "runtime OM byte size")
    _require_exact(str(artifact.get("sha256", "")).lower(), om_meta["sha256"], "runtime OM SHA-256")


def _validate_historical_lock(document: Mapping[str, Any], om_meta: Mapping[str, Any], controller_meta: Mapping[str, Any]) -> None:
    _require_exact(document.get("bytes"), om_meta["bytes"], "historical lock OM byte size")
    _require_exact(str(document.get("sha256", "")).lower(), om_meta["sha256"], "historical lock OM SHA-256")
    _require_exact(document.get("soc_version"), SOC_VERSION, "historical lock SoC")
    _require_exact(
        str(document.get("contract_sha256", "")).lower(),
        controller_meta["sha256"],
        "historical lock controller contract SHA-256",
    )


def _normalize_timestamp(value: str) -> str:
    if not isinstance(value, str) or not TIMESTAMP_RE.fullmatch(value):
        raise CampaignLockError("--recorded-at-utc must use YYYY-MM-DDTHH:MM:SSZ")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise CampaignLockError("--recorded-at-utc is not a valid UTC timestamp") from exc
    return value


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _publish_new_file(path: Path, payload: bytes) -> None:
    """Atomically create ``path`` without replacing a pre-existing lock."""

    descriptor, temporary_name = tempfile.mkstemp(prefix=".%s." % path.name, suffix=".part", dir=str(path.parent))
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # ``link`` creates the final directory entry only when it does not
            # already exist.  It is an atomic publish on one filesystem and
            # avoids the overwrite behavior of os.replace.
            os.link(str(temporary), str(path))
        except FileExistsError as exc:
            raise CampaignLockError("refusing to overwrite existing output lock: %s" % path) from exc
        finally:
            if temporary.exists():
                temporary.unlink()
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise


def create_campaign_lock(
    *,
    root_value: str,
    om_value: str,
    controller_contract_value: str,
    runtime_contract_value: str,
    historical_lock_value: str,
    output_value: str,
    recorded_at_utc: str,
    expected_output_sha256: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Verify the known B1 artifact set and atomically publish one new lock."""

    root = _root_path(root_value)
    om_relative = _safe_relative(om_value, "--om")
    controller_relative = _safe_relative(controller_contract_value, "--controller-contract")
    runtime_relative = _safe_relative(runtime_contract_value, "--runtime-contract")
    historical_relative = _safe_relative(historical_lock_value, "--historical-lock")
    output_relative = _safe_relative(output_value, "--output")
    timestamp = _normalize_timestamp(recorded_at_utc)
    om = _path_below_root(root, om_relative, "OM", exists=True)
    controller = _path_below_root(root, controller_relative, "controller contract", exists=True)
    runtime = _path_below_root(root, runtime_relative, "runtime contract", exists=True)
    historical = _path_below_root(root, historical_relative, "historical lock", exists=True)
    output = _path_below_root(root, output_relative, "output lock", exists=False)

    om_meta = _verify_exact(om, EXPECTED_B1["om"], "OM")
    controller_meta = _verify_exact(controller, EXPECTED_B1["controller_contract"], "controller contract")
    runtime_meta = _verify_exact(runtime, EXPECTED_B1["runtime_contract"], "runtime contract")
    historical_meta = _verify_exact(historical, EXPECTED_B1["historical_lock"], "historical lock")
    controller_document = _load_json_object(controller, "controller contract")
    runtime_document = _load_json_object(runtime, "runtime contract")
    historical_document = _load_json_object(historical, "historical lock")
    _validate_controller_contract(controller_document)
    _validate_runtime_contract(runtime_document, om_meta)
    _validate_historical_lock(historical_document, om_meta, controller_meta)

    payload: Dict[str, Any] = {
        "bytes": om_meta["bytes"],
        "chip_tier": CHIP_TIER,
        "contract_path": controller_relative.as_posix(),
        "contract_sha256": controller_meta["sha256"],
        "controller_contract_path": controller_relative.as_posix(),
        "controller_contract_sha256": controller_meta["sha256"],
        "historical_lock": {
            "bytes": historical_meta["bytes"],
            "path": historical_relative.as_posix(),
            "sha256": historical_meta["sha256"],
        },
        "kind": "case9_qwen25_b1_campaign_om_lock",
        "model_id": MODEL_ID,
        "om_path": om_relative.as_posix(),
        "recorded_at_utc": timestamp,
        "runtime_contract_path": runtime_relative.as_posix(),
        "runtime_contract_sha256": runtime_meta["sha256"],
        "schema_version": 2,
        "sha256": om_meta["sha256"],
        "soc_version": SOC_VERSION,
    }
    encoded = _json_bytes(payload)
    output_sha = hashlib.sha256(encoded).hexdigest()
    if expected_output_sha256 is not None:
        expected = expected_output_sha256.lower()
        if not SHA256_RE.fullmatch(expected):
            raise CampaignLockError("--expected-output-sha256 must be a lowercase SHA-256 value")
        if output_sha != expected:
            raise CampaignLockError("output lock SHA-256 mismatch: expected=%s actual=%s" % (expected, output_sha))
    if not dry_run:
        _publish_new_file(output, encoded)
    return {
        "status": "verified" if dry_run else "created",
        "output": output_relative.as_posix(),
        "bytes": len(encoded),
        "sha256": output_sha,
        "om": om_meta,
        "controller_contract": controller_meta,
        "runtime_contract": runtime_meta,
        "historical_lock": historical_meta,
        "soc_version": SOC_VERSION,
        "model_id": MODEL_ID,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="existing isolated campaign root")
    parser.add_argument("--om", required=True, help="POSIX-relative B1 OM path below --root")
    parser.add_argument("--controller-contract", required=True, help="POSIX-relative controller contract below --root")
    parser.add_argument("--runtime-contract", required=True, help="POSIX-relative ACL descriptor contract below --root")
    parser.add_argument("--historical-lock", required=True, help="POSIX-relative immutable historical B1 lock below --root")
    parser.add_argument("--output", required=True, help="new POSIX-relative campaign lock path below --root")
    parser.add_argument("--recorded-at-utc", required=True, help="fixed UTC timestamp: YYYY-MM-DDTHH:MM:SSZ")
    parser.add_argument("--expected-output-sha256", help="optional exact output hash assertion")
    parser.add_argument("--dry-run", action="store_true", help="verify inputs and print the new lock hash without writing")
    return parser


def main(argv: Iterable[str] = ()) -> int:
    args = _parser().parse_args(list(argv) or None)
    try:
        result = create_campaign_lock(
            root_value=args.root,
            om_value=args.om,
            controller_contract_value=args.controller_contract,
            runtime_contract_value=args.runtime_contract,
            historical_lock_value=args.historical_lock,
            output_value=args.output,
            recorded_at_utc=args.recorded_at_utc,
            expected_output_sha256=args.expected_output_sha256,
            dry_run=args.dry_run,
        )
    except CampaignLockError as exc:
        print("Case9 B1 campaign lock refused: %s" % exc, file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
