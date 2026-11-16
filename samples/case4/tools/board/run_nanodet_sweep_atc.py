#!/usr/bin/env python3
"""Convert a manifest of static NanoDet ONNX models to Ascend 310B4 OMs."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def validate_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != 1 or not isinstance(manifest.get("models"), list):
        raise ValueError("unsupported manifest schema")
    seen: set[str] = set()
    for item in manifest["models"]:
        name = item.get("name")
        if not isinstance(name, str) or not name or Path(name).name != name:
            raise ValueError(f"unsafe model name {name!r}")
        if name in seen:
            raise ValueError(f"duplicate model name {name}")
        seen.add(name)
        inputs = item.get("inputs")
        if not isinstance(inputs, list) or len(inputs) != 1:
            raise ValueError(f"{name}: expected exactly one input")
        input_name, input_shape = inputs[0]
        if not isinstance(input_name, str) or not input_name:
            raise ValueError(f"{name}: invalid input name")
        if len(input_shape) != 4 or input_shape[:2] != [1, 3]:
            raise ValueError(f"{name}: unsupported input shape {input_shape}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--soc-version", default="Ascend310B4")
    parser.add_argument("--precision-mode", default="allow_mix_precision")
    parser.add_argument("--model", action="append", default=[], help="Only convert this model; may repeat")
    parser.add_argument("--group", action="append", choices=("sweep", "reference", "all"), default=[])
    parser.add_argument("--timeout", type=int, default=1800)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    bundle = args.bundle_root.resolve()
    manifest_path = bundle / args.manifest
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    validate_manifest(manifest)
    manifest_sha256 = sha256_file(manifest_path)
    version_path = Path("/usr/local/Ascend/ascend-toolkit/latest/version.cfg")
    try:
        atc_version = {
            "path": str(version_path),
            "sha256": sha256_file(version_path),
            "content": version_path.read_text(encoding="utf-8", errors="replace").strip(),
        }
    except OSError as exc:
        atc_version = {"path": str(version_path), "error": f"{type(exc).__name__}: {exc}"}
    selected = set(args.model)
    groups = set(args.group) or {"sweep"}
    if "all" in groups:
        groups = {"sweep", "reference"}
    output_dir = args.output_dir.resolve()
    log_dir = output_dir / "logs"
    om_dir = output_dir / "om"
    log_dir.mkdir(parents=True, exist_ok=True)
    om_dir.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment.update({
        "TE_PARALLEL_COMPILER": "1",
        "MAX_COMPILE_CORE_NUMBER": "1",
    })
    summary_path = output_dir / "atc-summary.json"
    previous: dict[str, dict[str, Any]] = {}
    if summary_path.is_file():
        try:
            old = json.loads(summary_path.read_text(encoding="utf-8"))
            same_recipe = (
                old.get("kind") == "nanodet_sweep_atc"
                and old.get("manifest", {}).get("sha256") == manifest_sha256
                and old.get("soc_version") == args.soc_version
                and old.get("precision_mode") == args.precision_mode
                and old.get("atc_version") == atc_version
            )
            if same_recipe:
                previous = {item["name"]: item for item in old.get("models", [])}
        except (OSError, ValueError, KeyError):
            previous = {}
    summary: dict[str, Any] = {
        "schema_version": 1,
        "kind": "nanodet_sweep_atc",
        "soc_version": args.soc_version,
        "precision_mode": args.precision_mode,
        "atc_version": atc_version,
        "manifest": {"path": str(manifest_path), "sha256": manifest_sha256},
        "models": [],
    }
    current: dict[str, dict[str, Any]] = {}
    for item in manifest["models"]:
        if item.get("group") not in groups:
            continue
        name = item["name"]
        if selected and name not in selected:
            continue
        source = bundle / item["relative_path"]
        om_path = om_dir / f"{name}.om"
        log_path = log_dir / f"{name}.log"
        input_name, input_shape = item["inputs"][0]
        record: dict[str, Any] = {
            "name": name,
            "source": {"path": str(source), "sha256": item["sha256"], "bytes": item["bytes"]},
            "om_path": str(om_path),
            "log_path": str(log_path),
            "status": "pending",
            "conversion": {
                "soc_version": args.soc_version,
                "precision_mode": args.precision_mode,
                "atc_version": atc_version,
                "manifest_sha256": manifest_sha256,
                "input_name": input_name,
                "input_shape": input_shape,
            },
        }
        command = [
            "atc", "--framework=5",
            f"--model={source}",
            f"--output={om_path.with_suffix('')}",
            "--input_format=NCHW",
            f"--input_shape={input_name}:{input_shape[0]},{input_shape[1]},{input_shape[2]},{input_shape[3]}",
            f"--soc_version={args.soc_version}",
            f"--precision_mode={args.precision_mode}",
            "--log=error",
        ]
        record["command"] = command
        prior = previous.get(name)
        if (
            prior
            and prior.get("status") == "success"
            and prior.get("source", {}).get("sha256") == item["sha256"]
            and prior.get("conversion") == record["conversion"]
            and prior.get("command") == command
            and Path(prior.get("om_path", "")).is_file()
            and Path(prior.get("om_path", "")).stat().st_size == prior.get("om_bytes")
            and sha256_file(Path(prior["om_path"])) == prior.get("om_sha256")
        ):
            current[name] = prior
            print(f"skip {name}", flush=True)
            summary["models"] = [
                current.get(entry["name"], previous.get(entry["name"]))
                for entry in manifest["models"]
                if current.get(entry["name"], previous.get(entry["name"]))
            ]
            write_json(summary_path, summary)
            continue
        current[name] = record
        started = time.time()
        try:
            result = subprocess.run(
                command,
                cwd=bundle,
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=args.timeout,
                check=False,
            )
            log_path.write_text(result.stdout, encoding="utf-8")
            elapsed = time.time() - started
            record["exit_code"] = result.returncode
            record["elapsed_seconds"] = elapsed
            if result.returncode == 0 and om_path.is_file() and om_path.stat().st_size > 0:
                record.update({
                    "status": "success",
                    "om_bytes": om_path.stat().st_size,
                    "om_sha256": sha256_file(om_path),
                })
            else:
                record.update({
                    "status": "failed",
                    "reason": f"atc exit code {result.returncode} or missing OM",
                })
        except subprocess.TimeoutExpired as exc:
            elapsed = time.time() - started
            output = exc.stdout or ""
            if isinstance(output, bytes):
                output = output.decode("utf-8", errors="replace")
            log_path.write_text(output, encoding="utf-8")
            record.update({
                "status": "failed",
                "reason": f"atc timeout after {args.timeout}s",
                "elapsed_seconds": elapsed,
            })
        except OSError as exc:
            log_path.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
            record.update({"status": "failed", "reason": f"atc could not start: {exc}"})
        summary["models"] = [
            current.get(entry["name"], previous.get(entry["name"]))
            for entry in manifest["models"]
            if current.get(entry["name"], previous.get(entry["name"]))
        ]
        write_json(summary_path, summary)
        print(f"{record['status']} {name}", flush=True)
    write_json(summary_path, summary)
    failures = [item["name"] for item in summary["models"] if item["status"] != "success"]
    print(json.dumps({"models": len(summary["models"]), "failures": failures}, ensure_ascii=False))
    return 0 if summary["models"] and not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
