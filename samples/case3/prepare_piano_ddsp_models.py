"""Convert the pinned Piano-DDSP ONNX suite to an audited Ascend OM bundle."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time
from typing import Iterable


RELEASE = "model-suite-v1.0.1"
SOURCE_COMMIT = "c41911aa7de454aeacf0b3edbb2d06a0801fb3ff"
PRECISION_MODE_V2 = "origin"
ATC_COMPILE_ENVIRONMENT = {
    "MULTI_THREAD_COMPILE": "0",
    "TE_PARALLEL_COMPILER": "1",
}
MODEL_ORDER = (
    "gru_ir_96_64",
    "film_fdn_128_96",
    "gru_ir_fullwet_96_64",
    "film_ir_fullwet_96_64",
)
PRIMARY_MODEL_ID = MODEL_ORDER[0]
INPUT_SHAPE = (
    "conditioning:1,1,16,2;pedal:1,1,4;piano_model:1;"
    "extended_pitch:1,1,16,1;context_state:1,1,64;"
    "monophonic_state:1,16,192"
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run_capture(command: list[str]) -> dict[str, object]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, check=False)
        return {
            "command": command,
            "exit_code": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": command, "error": str(exc)}


def atc_subprocess_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(ATC_COMPILE_ENVIRONMENT)
    return environment


def environment_summary(soc_version: str) -> dict[str, object]:
    version_paths = (
        Path("/usr/local/Ascend/ascend-toolkit/latest/version.cfg"),
        Path("/etc/Ascend/ascend_cann_install.info"),
    )
    versions: dict[str, str] = {}
    for path in version_paths:
        if path.is_file():
            versions[str(path)] = path.read_text(encoding="utf-8", errors="replace")
    return {
        "captured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "hostname": platform.node(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "python": sys.version,
        "python_executable": sys.executable,
        "soc_version": soc_version,
        "atc_compile_environment": ATC_COMPILE_ENVIRONMENT,
        "cann_environment": {
            name: os.environ.get(name)
            for name in ("ASCEND_HOME_PATH", "ASCEND_OPP_PATH", "LD_LIBRARY_PATH")
        },
        "version_files": versions,
        "npu_smi": run_capture(["npu-smi", "info"]),
        "uname": run_capture(["uname", "-a"]),
    }


def load_release(model_root: Path) -> dict[str, object]:
    manifest_path = model_root / "model-suite.json"
    if not manifest_path.is_file():
        raise FileNotFoundError(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("release") != RELEASE:
        raise ValueError(f"Expected release {RELEASE}, received {manifest.get('release')!r}")
    contract = manifest.get("deployment_contract", {})
    expected = {
        "dtype": "FP32",
        "opset": 13,
        "batch_size": 1,
        "frames_per_call": 1,
        "frame_rate": 250,
        "sample_rate": 16000,
        "max_polyphony": 16,
    }
    if not isinstance(contract, dict) or any(contract.get(k) != v for k, v in expected.items()):
        raise ValueError(f"Unexpected Piano-DDSP deployment contract: {contract!r}")
    return manifest


def model_assets(
    release: dict[str, object],
    model_root: Path,
    model_id: str,
    variant: str,
    variant_root: Path,
) -> tuple[Path, Path, dict[str, object]]:
    models = release.get("models", {})
    if not isinstance(models, dict) or model_id not in models:
        raise KeyError(model_id)
    entry = models[model_id]
    if not isinstance(entry, dict):
        raise ValueError(f"Invalid model entry {model_id}")
    assets = entry.get("assets", {})
    if not isinstance(assets, dict):
        raise ValueError(f"Invalid asset map for {model_id}")
    onnx_name = next((str(name) for name in assets if str(name).endswith(".onnx")), "")
    json_name = next((str(name) for name in assets if str(name).endswith(".json")), "")
    if variant == "origin":
        onnx_path, metadata_path = model_root / onnx_name, model_root / json_name
        expected_hashes = {
            onnx_path: str(assets[onnx_name].get("sha256", "")),
            metadata_path: str(assets[json_name].get("sha256", "")),
        }
    else:
        stem = Path(onnx_name).stem + f"-{variant}"
        onnx_path = variant_root / f"{stem}.onnx"
        metadata_path = variant_root / f"{stem}.json"
        expected_hashes = {}
    for path in (onnx_path, metadata_path):
        if not path.is_file():
            raise FileNotFoundError(path)
        expected_hash = expected_hashes.get(path)
        if expected_hash is not None and sha256_file(path) != expected_hash:
            raise ValueError(f"SHA256 mismatch for {path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if variant != "origin":
        validation_path = onnx_path.with_suffix(".validation.json")
        validation = json.loads(validation_path.read_text(encoding="utf-8"))
        original_hash = str(assets[onnx_name].get("sha256", ""))
        if (
            metadata.get("export_variant") != variant
            or metadata.get("original_onnx_sha256") != original_hash
            or metadata.get("onnx_sha256") != sha256_file(onnx_path)
            or validation.get("candidate_sha256") != sha256_file(onnx_path)
            or validation.get("frames", 0) < 10_000
            or validation.get("passed") is not True
        ):
            raise ValueError(f"Unverified {variant} asset for {model_id}")
    return onnx_path, metadata_path, metadata


def convert_one(
    model_id: str,
    onnx_path: Path,
    metadata_path: Path,
    metadata: dict[str, object],
    bundle_root: Path,
    soc_version: str,
    variant: str,
) -> dict[str, object]:
    models_root = bundle_root / "models"
    logs_root = bundle_root / "logs"
    models_root.mkdir(parents=True, exist_ok=True)
    logs_root.mkdir(parents=True, exist_ok=True)
    om_base = models_root / onnx_path.stem
    om_path = om_base.with_suffix(".om")
    log_path = logs_root / f"{model_id}.atc.log"
    command_path = logs_root / f"{model_id}.command.json"
    command = [
        "atc",
        f"--model={onnx_path}",
        "--framework=5",
        f"--output={om_base}",
        "--input_format=ND",
        f"--input_shape={INPUT_SHAPE}",
        f"--soc_version={soc_version}",
        f"--precision_mode_v2={PRECISION_MODE_V2}",
        "--enable_graph_parallel=0",
        "--log=info",
    ]
    source_onnx_sha256 = sha256_file(onnx_path)
    if om_path.is_file():
        if not command_path.is_file() or not log_path.is_file():
            raise RuntimeError(
                f"Refusing to reuse {om_path}: conversion record or raw ATC log is missing"
            )
        command_record = json.loads(command_path.read_text(encoding="utf-8"))
        expected_record = {
            "schema": "piano-ddsp-atc-conversion/v1",
            "command": command,
            "environment": ATC_COMPILE_ENVIRONMENT,
            "source_onnx_sha256": source_onnx_sha256,
            "om_sha256": sha256_file(om_path),
            "atc_log_sha256": sha256_file(log_path),
        }
        if any(command_record.get(key) != value for key, value in expected_record.items()):
            raise RuntimeError(
                f"Refusing to reuse {om_path}: ONNX, ATC command, OM, or log provenance changed"
            )
    else:
        with log_path.open("w", encoding="utf-8") as output:
            output.write("COMMAND " + json.dumps(command) + "\n")
            output.write("ENVIRONMENT " + json.dumps(ATC_COMPILE_ENVIRONMENT) + "\n")
            output.flush()
            result = subprocess.run(
                command,
                stdout=output,
                stderr=subprocess.STDOUT,
                text=True,
                check=False,
                env=atc_subprocess_environment(),
            )
        if result.returncode != 0 or not om_path.is_file():
            raise RuntimeError(
                f"ATC failed for {model_id} with exit code {result.returncode}; see {log_path}"
            )
        command_record = {
            "schema": "piano-ddsp-atc-conversion/v1",
            "command": command,
            "environment": ATC_COMPILE_ENVIRONMENT,
            "source_onnx_sha256": source_onnx_sha256,
            "om_sha256": sha256_file(om_path),
            "atc_log_sha256": sha256_file(log_path),
        }
        command_tmp = command_path.with_suffix(".json.part")
        command_tmp.write_text(
            json.dumps(command_record, indent=2) + "\n", encoding="utf-8"
        )
        os.replace(command_tmp, command_path)
    copied_metadata = models_root / metadata_path.name
    if copied_metadata.is_file() and sha256_file(copied_metadata) != sha256_file(metadata_path):
        raise RuntimeError(f"Refusing to replace immutable metadata {copied_metadata}")
    if not copied_metadata.is_file():
        shutil.copy2(metadata_path, copied_metadata)
    from piano_ddsp_runtime.acl_model import PianoAclModel

    with PianoAclModel(om_path, metadata) as model:
        io_validation = model.contract_report()
    return {
        "model_id": model_id,
        "display_name": metadata.get("display_name", model_id),
        "precision": "FP32",
        "precision_mode_v2": PRECISION_MODE_V2,
        "export_variant": variant,
        "source_onnx": onnx_path.name,
        "source_onnx_sha256": source_onnx_sha256,
        "metadata": str(copied_metadata.relative_to(bundle_root).as_posix()),
        "metadata_sha256": sha256_file(copied_metadata),
        "om": str(om_path.relative_to(bundle_root).as_posix()),
        "om_sha256": sha256_file(om_path),
        "om_bytes": om_path.stat().st_size,
        "reverb_output": metadata.get("reverb_output"),
        "inputs": metadata.get("inputs"),
        "outputs": metadata.get("outputs"),
        "io_contract_validation": io_validation,
        "atc_log": str(log_path.relative_to(bundle_root).as_posix()),
        "atc_log_sha256": sha256_file(log_path),
        "atc_command_record": str(command_path.relative_to(bundle_root).as_posix()),
        "atc_command_record_sha256": sha256_file(command_path),
        "atc_command": command,
        "atc_compile_environment": ATC_COMPILE_ENVIRONMENT,
    }


def validate_existing_model_result(
    model_id: str,
    result: dict[str, object],
    onnx_path: Path,
    metadata_path: Path,
    bundle_root: Path,
    soc_version: str,
) -> None:
    source_hash = sha256_file(onnx_path)
    if result.get("source_onnx_sha256") != source_hash:
        raise RuntimeError(f"Existing {model_id} result was built from a different ONNX file")
    expected_command = [
        "atc",
        f"--model={onnx_path}",
        "--framework=5",
        f"--output={bundle_root / 'models' / onnx_path.stem}",
        "--input_format=ND",
        f"--input_shape={INPUT_SHAPE}",
        f"--soc_version={soc_version}",
        f"--precision_mode_v2={PRECISION_MODE_V2}",
        "--enable_graph_parallel=0",
        "--log=info",
    ]
    if result.get("atc_command") != expected_command:
        raise RuntimeError(f"Existing {model_id} result used a different ATC command")
    paths_and_hashes = (
        ("om", "om_sha256"),
        ("metadata", "metadata_sha256"),
        ("atc_log", "atc_log_sha256"),
        ("atc_command_record", "atc_command_record_sha256"),
    )
    resolved: dict[str, Path] = {}
    for path_key, hash_key in paths_and_hashes:
        raw_path = result.get(path_key)
        if not isinstance(raw_path, str) or not raw_path:
            raise RuntimeError(f"Existing {model_id} result is missing {path_key}")
        path = (bundle_root / raw_path).resolve()
        if bundle_root.resolve() not in path.parents or not path.is_file():
            raise RuntimeError(f"Existing {model_id} result has invalid {path_key}: {raw_path}")
        if result.get(hash_key) != sha256_file(path):
            raise RuntimeError(f"Existing {model_id} result has a changed {path_key}")
        resolved[path_key] = path
    if sha256_file(metadata_path) != result.get("metadata_sha256"):
        raise RuntimeError(f"Existing {model_id} metadata no longer matches the source release")
    record = json.loads(resolved["atc_command_record"].read_text(encoding="utf-8"))
    expected_record = {
        "schema": "piano-ddsp-atc-conversion/v1",
        "command": expected_command,
        "environment": ATC_COMPILE_ENVIRONMENT,
        "source_onnx_sha256": source_hash,
        "om_sha256": result["om_sha256"],
        "atc_log_sha256": result["atc_log_sha256"],
    }
    if any(record.get(key) != value for key, value in expected_record.items()):
        raise RuntimeError(f"Existing {model_id} conversion record does not match its artifacts")


def parse_models(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        for model_id in value.split(","):
            model_id = model_id.strip()
            if model_id == "all":
                return list(MODEL_ORDER)
            if model_id not in MODEL_ORDER:
                raise ValueError(f"Unknown Piano-DDSP model: {model_id}")
            if model_id not in result:
                result.append(model_id)
    if not result:
        return [PRIMARY_MODEL_ID]
    return sorted(result, key=MODEL_ORDER.index)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--model-root", type=Path, default=Path("models/piano_ddsp/model-suite-v1.0.1")
    )
    parser.add_argument(
        "--bundle-root",
        type=Path,
        default=None,
    )
    parser.add_argument("--variant", choices=("origin", "gru-unrolled"), default="origin")
    parser.add_argument(
        "--variant-root",
        type=Path,
        default=Path("models/piano_ddsp/model-suite-v1.0.1-gru-unrolled"),
    )
    parser.add_argument("--models", nargs="+", default=[PRIMARY_MODEL_ID])
    parser.add_argument("--soc-version", default="Ascend310B4")
    parser.add_argument("--activate", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if platform.machine().lower() not in {"aarch64", "arm64"}:
        raise RuntimeError("ATC conversion must run on the Ascend 310B board")
    if shutil.which("atc") is None:
        raise RuntimeError("atc is unavailable; source the existing CANN environment first")
    model_root = args.model_root.resolve()
    bundle_root = (
        args.bundle_root
        or Path(
            "models/piano_ddsp/bundles/"
            + (
                "model-suite-v1.0.1-fp32-origin"
                if args.variant == "origin"
                else "model-suite-v1.0.1-gru-unrolled-fp32-origin"
            )
        )
    ).resolve()
    variant_root = args.variant_root.resolve()
    release = load_release(model_root)
    selected = parse_models(args.models)
    if selected != [PRIMARY_MODEL_ID] and PRIMARY_MODEL_ID not in selected:
        raise ValueError(
            f"{PRIMARY_MODEL_ID} must be converted and validated before other models"
        )
    manifest_path = bundle_root / "manifest.json"
    existing: dict[str, object] = {}
    loaded: dict[str, object] | None = None
    if manifest_path.is_file():
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (
            loaded.get("release") != RELEASE
            or loaded.get("precision") != "FP32"
            or loaded.get("precision_mode_v2") != PRECISION_MODE_V2
            or loaded.get("export_variant", "origin") != args.variant
        ):
            raise RuntimeError(f"Refusing to update incompatible bundle {manifest_path}")
        raw_models = loaded.get("models", {})
        if isinstance(raw_models, dict):
            existing = raw_models

    if any(model_id != PRIMARY_MODEL_ID for model_id in selected):
        primary = existing.get(PRIMARY_MODEL_ID)
        validation = primary.get("validation") if isinstance(primary, dict) else None
        if not isinstance(validation, dict) or validation.get("passed") is not True:
            raise RuntimeError(
                f"{PRIMARY_MODEL_ID} must pass the 10,000-frame OM validation "
                "before converting other models"
            )

    if loaded is not None and loaded.get("complete") is True:
        missing = [model_id for model_id in selected if model_id not in existing]
        if missing:
            raise RuntimeError(f"Immutable completed bundle is missing models: {missing}")
        for model_id in selected:
            onnx_path, metadata_path, _ = model_assets(
                release, model_root, model_id, args.variant, variant_root
            )
            raw_result = existing[model_id]
            if not isinstance(raw_result, dict):
                raise RuntimeError(f"Invalid existing model result: {model_id}")
            validate_existing_model_result(
                model_id,
                raw_result,
                onnx_path,
                metadata_path,
                bundle_root,
                args.soc_version,
            )
        print(f"Completed bundle provenance verified; leaving files unchanged: {manifest_path}")
        if args.activate:
            write_active_pointer(bundle_root, loaded)
        return

    bundle_root.mkdir(parents=True, exist_ok=True)
    environment = environment_summary(args.soc_version)
    (bundle_root / "environment.json").write_text(
        json.dumps(environment, indent=2) + "\n", encoding="utf-8"
    )

    converted = dict(existing)
    for model_id in selected:
        onnx_path, metadata_path, metadata = model_assets(
            release, model_root, model_id, args.variant, variant_root
        )
        if model_id in converted:
            raw_result = converted[model_id]
            if not isinstance(raw_result, dict):
                raise RuntimeError(f"Invalid existing model result: {model_id}")
            validate_existing_model_result(
                model_id,
                raw_result,
                onnx_path,
                metadata_path,
                bundle_root,
                args.soc_version,
            )
            print(f"Keeping verified immutable model result: {model_id}")
            continue
        converted[model_id] = convert_one(
            model_id,
            onnx_path,
            metadata_path,
            metadata,
            bundle_root,
            args.soc_version,
            args.variant,
        )
        print(f"Converted {model_id}: {converted[model_id]['om']}")

    manifest = {
        "schema": "piano-ddsp-om-bundle/v1",
        "id": bundle_root.name,
        "release": RELEASE,
        "precision": "FP32",
        "precision_mode_v2": PRECISION_MODE_V2,
        "export_variant": args.variant,
        "soc_version": args.soc_version,
        "source_manifest_sha256": sha256_file(model_root / "model-suite.json"),
        "source_commit": SOURCE_COMMIT,
        "models": converted,
        "complete": all(
            model_id in converted
            and isinstance(converted[model_id], dict)
            and dict(converted[model_id]).get("validation", {}).get("passed") is True
            for model_id in MODEL_ORDER
        ),
        "environment": "environment.json",
    }
    temporary = manifest_path.with_suffix(".json.part")
    temporary.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, manifest_path)
    if args.activate:
        write_active_pointer(bundle_root, manifest)
    print(f"Wrote bundle manifest: {manifest_path}")


def write_active_pointer(bundle_root: Path, manifest: dict[str, object]) -> None:
    models = manifest.get("models", {})
    if not isinstance(models, dict) or not any(
        isinstance(item, dict) and dict(item).get("validation", {}).get("passed") is True
        for item in models.values()
    ):
        raise RuntimeError("Refusing to activate a bundle without a passed OM validation")
    active = bundle_root.parent.parent / "active-bundle.json"
    active_tmp = active.with_suffix(".json.part")
    active_tmp.write_text(
        json.dumps(
            {
                "schema": "piano-ddsp-active-bundle/v1",
                "bundle_id": manifest["id"],
                "manifest": f"bundles/{bundle_root.name}/manifest.json",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.replace(active_tmp, active)


if __name__ == "__main__":
    main()
