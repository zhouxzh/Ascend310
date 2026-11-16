#!/usr/bin/env python3
"""Convert the two trained hand PicoDet ONNX models to one 310B tier."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="milliseconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_models(bundle: Path, manifest_name: str) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    path = bundle / manifest_name
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != 1 or manifest.get("kind") != "picodet_palm_onnx_bundle":
        raise ValueError("unsupported PicoDet manifest")
    models = manifest.get("models")
    if not isinstance(models, list) or len(models) != 2:
        raise ValueError("PicoDet manifest must contain exactly two models")
    names = [item.get("name") for item in models]
    if len(set(names)) != 2 or set(names) != {"picodet-xs-320-seed-42", "picodet-xs-416-seed-42"}:
        raise ValueError(f"unexpected PicoDet model set: {names}")
    for item in models:
        name = item["name"]
        if Path(name).name != name:
            raise ValueError(f"unsafe model name: {name!r}")
        inputs = item.get("inputs")
        outputs = item.get("outputs")
        if not isinstance(inputs, list) or len(inputs) != 1 or inputs[0][0] != "image":
            raise ValueError(f"{name}: expected one image input")
        if list(inputs[0][1]) not in ([1, 3, 320, 320], [1, 3, 416, 416]):
            raise ValueError(f"{name}: unexpected input shape {inputs[0][1]}")
        if not isinstance(outputs, list) or len(outputs) != 8:
            raise ValueError(f"{name}: expected eight PicoDet outputs")
    return path, manifest, models


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle-root", type=Path, required=True)
    parser.add_argument("--manifest", default="manifest.json")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--soc-version", required=True, choices=("Ascend310B1", "Ascend310B4"))
    parser.add_argument("--precision-mode", default="allow_mix_precision")
    parser.add_argument("--model", action="append", default=[])
    parser.add_argument("--timeout", type=int, default=1800)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.precision_mode != "allow_mix_precision":
        raise ValueError("PicoDet campaign only permits allow_mix_precision")
    bundle = args.bundle_root.resolve()
    manifest_path, _manifest, models = load_models(bundle, args.manifest)
    manifest_sha256 = sha256_file(manifest_path)
    selected = set(args.model)
    names = [item["name"] for item in models]
    if selected - set(names):
        raise ValueError(f"unknown PicoDet model: {sorted(selected - set(names))}")
    target_models = [item for item in models if not selected or item["name"] in selected]
    version_path = Path("/usr/local/Ascend/ascend-toolkit/latest/version.cfg")
    try:
        atc_version = {
            "path": str(version_path),
            "sha256": sha256_file(version_path),
            "content": version_path.read_text(encoding="utf-8", errors="replace").strip(),
        }
    except OSError as exc:
        atc_version = {"path": str(version_path), "error": f"{type(exc).__name__}: {exc}"}
    output_dir = args.output_dir.resolve()
    om_dir = output_dir / "om"
    log_dir = output_dir / "logs"
    om_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "atc-summary.json"
    recipe = {
        "manifest_sha256": manifest_sha256,
        "soc_version": args.soc_version,
        "precision_mode": args.precision_mode,
        "atc_version": atc_version,
    }
    previous: dict[str, dict[str, Any]] = {}
    if summary_path.is_file():
        try:
            old = json.loads(summary_path.read_text(encoding="utf-8"))
            if old.get("kind") == "picodet_palm_atc" and old.get("recipe") == recipe:
                previous = {item["name"]: item for item in old.get("models", [])}
        except (OSError, ValueError, KeyError):
            pass
    environment = dict(os.environ)
    environment.update({"TE_PARALLEL_COMPILER": "1", "MAX_COMPILE_CORE_NUMBER": "1"})
    report: dict[str, Any] = {
        "schema_version": 1,
        "kind": "picodet_palm_atc",
        "started_at": now(),
        "recipe": recipe,
        "manifest": {"path": str(manifest_path), "sha256": manifest_sha256},
        "expected_models": names,
        "requested_models": [item["name"] for item in target_models],
        "models": [],
    }
    current: dict[str, dict[str, Any]] = {}

    def ordered_records() -> list[dict[str, Any]]:
        return [
            current.get(name, previous.get(name))
            for name in names
            if current.get(name, previous.get(name)) is not None
        ]

    for item in target_models:
        name = item["name"]
        source = (bundle / item["relative_path"]).resolve()
        om_path = om_dir / f"{name}.om"
        log_path = log_dir / f"{name}.log"
        input_name, input_shape = item["inputs"][0]
        record: dict[str, Any] = {
            "name": name,
            "status": "pending",
            "source": {"path": str(source), "sha256": item["sha256"], "bytes": int(item["bytes"])},
            "om_path": str(om_path),
            "log_path": str(log_path),
            "conversion": {**recipe, "input_name": input_name, "input_shape": input_shape},
        }
        command = [
            "atc", "--framework=5", f"--model={source}", f"--output={om_path.with_suffix('')}",
            "--input_format=NCHW",
            f"--input_shape={input_name}:{','.join(str(value) for value in input_shape)}",
            f"--soc_version={args.soc_version}",
            f"--precision_mode={args.precision_mode}", "--log=error",
        ]
        record["command"] = command
        if not source.is_file() or source.stat().st_size != int(item["bytes"]) or sha256_file(source) != item["sha256"]:
            record.update({"status": "failed", "reason": "source ONNX bytes or SHA-256 mismatch"})
        else:
            prior = previous.get(name)
            if (
                prior and prior.get("status") == "success" and prior.get("source") == record["source"]
                and prior.get("conversion") == record["conversion"] and prior.get("command") == command
                and om_path.is_file() and om_path.stat().st_size == prior.get("om_bytes")
                and sha256_file(om_path) == prior.get("om_sha256")
            ):
                record = prior
                print(f"skip {name}", flush=True)
            else:
                started = time.monotonic()
                try:
                    result = subprocess.run(
                        command, cwd=bundle, env=environment, text=True,
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        timeout=args.timeout, check=False,
                    )
                    log_path.write_text(result.stdout, encoding="utf-8")
                    record["exit_code"] = result.returncode
                    record["elapsed_seconds"] = time.monotonic() - started
                    if result.returncode == 0 and om_path.is_file() and om_path.stat().st_size > 0:
                        record.update({"status": "success", "om_bytes": om_path.stat().st_size, "om_sha256": sha256_file(om_path)})
                    else:
                        record.update({"status": "failed", "reason": f"atc exit code {result.returncode} or missing OM"})
                except subprocess.TimeoutExpired as exc:
                    output = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
                    log_path.write_text(output, encoding="utf-8")
                    record.update({"status": "failed", "reason": f"atc timeout after {args.timeout}s", "elapsed_seconds": time.monotonic() - started})
                except OSError as exc:
                    log_path.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
                    record.update({"status": "failed", "reason": f"atc could not start: {exc}"})
        current[name] = record
        report["models"] = ordered_records()
        write_json(summary_path, report)
        print(f"{record['status']} {name}", flush=True)
    report["finished_at"] = now()
    report["models"] = ordered_records()
    write_json(summary_path, report)
    requested_failures = [
        name for name in report["requested_models"]
        if not current.get(name) or current[name]["status"] != "success"
    ]
    complete = len(report["models"]) == len(names) and not [item["name"] for item in report["models"] if item["status"] != "success"]
    report["campaign_complete"] = complete
    write_json(summary_path, report)
    print(json.dumps({
        "expected_models": len(names),
        "observed_models": len(report["models"]),
        "requested_failures": requested_failures,
        "campaign_complete": complete,
    }, ensure_ascii=False))
    return 0 if not requested_failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
