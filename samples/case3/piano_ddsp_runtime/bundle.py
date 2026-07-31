"""Validation and discovery for immutable Piano-DDSP OM bundles."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Any


EXPECTED_RELEASE = "model-suite-v1.0.0"
EXPECTED_PRECISION = "FP32"
EXPECTED_PRECISION_MODE_V2 = "origin"
EXPECTED_SOC = "Ascend310B4"
MINIMUM_VALIDATION_FRAMES = 10_000


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class PianoModelAsset:
    model_id: str
    display_name: str
    om_path: Path
    metadata_path: Path
    metadata: dict[str, Any]
    om_sha256: str
    validation_passed: bool = False

    @property
    def piano_years(self) -> tuple[int, ...]:
        return tuple(int(value) for value in self.metadata.get("piano_model_index_to_maestro_year", ()))


@dataclass(frozen=True)
class PianoBundle:
    id: str
    release: str
    precision: str
    soc_version: str
    manifest_path: Path
    models: dict[str, PianoModelAsset]
    complete: bool


def _resolved_child(root: Path, relative: object, label: str) -> Path:
    if not isinstance(relative, str) or not relative:
        raise ValueError(f"Bundle {label} must be a relative path")
    path = (root / relative).resolve()
    if root.resolve() not in path.parents:
        raise ValueError(f"Bundle {label} escapes its directory: {relative}")
    if not path.is_file():
        raise FileNotFoundError(path)
    return path


def _validate_metadata(metadata: dict[str, Any], model_id: str) -> None:
    if metadata.get("schema") != "ddsp-piano-model/v1":
        raise ValueError(f"Unsupported Piano-DDSP metadata schema for {model_id}")
    if metadata.get("model_id") != model_id:
        raise ValueError(f"Piano-DDSP metadata model mismatch for {model_id}")
    expected = {
        "dtype": "FP32",
        "opset": 13,
        "sample_rate": 16_000,
        "frame_rate": 250,
        "frames_per_call": 1,
        "audio_samples_per_call": 64,
        "release_frames": 250,
    }
    for name, value in expected.items():
        if metadata.get(name) != value:
            raise ValueError(
                f"Unexpected {name} for {model_id}: {metadata.get(name)!r}, expected {value!r}"
            )
    inputs = metadata.get("inputs")
    required_inputs = {
        "conditioning": [1, 1, 16, 2],
        "pedal": [1, 1, 4],
        "piano_model": [1],
        "extended_pitch": [1, 1, 16, 1],
        "context_state": [1, 1, 64],
        "monophonic_state": [1, 16, 192],
    }
    if inputs != required_inputs:
        raise ValueError(f"Unexpected input contract for {model_id}: {inputs!r}")


def load_bundle(
    path: Path,
    *,
    validate_qualification: bool = True,
) -> PianoBundle:
    manifest_path = Path(path).resolve()
    if manifest_path.is_dir():
        manifest_path = manifest_path / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    if data.get("schema") != "piano-ddsp-om-bundle/v1":
        raise ValueError(f"Unsupported Piano-DDSP bundle schema: {manifest_path}")
    if data.get("release") != EXPECTED_RELEASE:
        raise ValueError(f"Unexpected Piano-DDSP release: {data.get('release')!r}")
    if data.get("precision") != EXPECTED_PRECISION:
        raise ValueError("Only the FP32 Piano-DDSP baseline is accepted")
    if data.get("precision_mode_v2") != EXPECTED_PRECISION_MODE_V2:
        raise ValueError(
            "Piano-DDSP bundle must be converted with precision_mode_v2=origin"
        )
    if data.get("soc_version") != EXPECTED_SOC:
        raise ValueError(f"Unexpected Piano-DDSP SoC: {data.get('soc_version')!r}")
    raw_models = data.get("models")
    if not isinstance(raw_models, dict) or not raw_models:
        raise ValueError(f"Piano-DDSP bundle has no models: {manifest_path}")
    root = manifest_path.parent
    models: dict[str, PianoModelAsset] = {}
    for model_id, raw in raw_models.items():
        if not isinstance(raw, dict):
            raise ValueError(f"Invalid bundle model {model_id!r}")
        om_path = _resolved_child(root, raw.get("om"), f"{model_id} OM")
        metadata_path = _resolved_child(root, raw.get("metadata"), f"{model_id} metadata")
        expected_om_hash = str(raw.get("om_sha256", ""))
        actual_om_hash = sha256_file(om_path)
        if expected_om_hash != actual_om_hash:
            raise ValueError(f"SHA256 mismatch for {om_path}")
        expected_metadata_hash = str(raw.get("metadata_sha256", ""))
        if expected_metadata_hash != sha256_file(metadata_path):
            raise ValueError(f"SHA256 mismatch for {metadata_path}")
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        _validate_metadata(metadata, str(model_id))
        validation_passed = False
        validation = raw.get("validation")
        if validation is not None and validate_qualification:
            if not isinstance(validation, dict):
                raise ValueError(f"Invalid validation record for {model_id}")
            validation_path = _resolved_child(
                root, validation.get("path"), f"{model_id} validation report"
            )
            if str(validation.get("sha256", "")) != sha256_file(validation_path):
                raise ValueError(f"SHA256 mismatch for {validation_path}")
            report = json.loads(validation_path.read_text(encoding="utf-8"))
            report_frames = int(report.get("frames", 0))
            if (
                report.get("schema") != "piano-ddsp-om-validation/v1"
                or report.get("bundle_id") != data.get("id", manifest_path.parent.name)
                or report.get("model_id") != model_id
                or int(validation.get("frames", 0)) != report_frames
                or bool(validation.get("passed", False)) != bool(report.get("passed", False))
                or validation.get("om_sha256") != actual_om_hash
                or report.get("om_sha256") != actual_om_hash
            ):
                raise ValueError(f"Validation report mismatch for {model_id}")
            validation_passed = bool(report.get("passed", False)) and (
                report_frames >= MINIMUM_VALIDATION_FRAMES
            )
        models[str(model_id)] = PianoModelAsset(
            model_id=str(model_id),
            display_name=str(raw.get("display_name", metadata.get("display_name", model_id))),
            om_path=om_path,
            metadata_path=metadata_path,
            metadata=metadata,
            om_sha256=actual_om_hash,
            validation_passed=validation_passed,
        )
    return PianoBundle(
        id=str(data.get("id", manifest_path.parent.name)),
        release=str(data["release"]),
        precision=str(data["precision"]),
        soc_version=str(data["soc_version"]),
        manifest_path=manifest_path,
        models=models,
        complete=bool(data.get("complete", False)),
    )


def scan_bundles(root: Path) -> tuple[list[PianoBundle], list[str]]:
    bundles: list[PianoBundle] = []
    errors: list[str] = []
    root = Path(root)
    if not root.is_dir():
        return bundles, errors
    for manifest in sorted(root.glob("*/manifest.json")):
        try:
            bundles.append(load_bundle(manifest))
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            errors.append(f"{manifest}: {exc}")
    return bundles, errors
