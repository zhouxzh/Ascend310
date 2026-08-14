"""Create a versioned board manifest from an ATC evidence record and reviewed catalog entry."""

from __future__ import annotations

import argparse
from pathlib import Path

from .cann_metadata import cann_version
from .candidate_catalog import CANDIDATES, candidate_preprocessing_contract
from .inference_manifest import (
    _resolve_artifact,
    _strict_positive_int,
    load_inference_manifest,
    load_strict_json_object,
    sha256_file,
)
from .safe_json import write_validated_new_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate", choices=tuple(CANDIDATES), required=True)
    parser.add_argument("--atc-evidence", type=Path, required=True)
    parser.add_argument("--output-shape", required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def parse_shape(value: str) -> list[int]:
    parts = value.split(",")
    if not parts or any(not item.strip() for item in parts):
        raise ValueError("shape must contain positive integer dimensions")
    try:
        result = [int(item.strip()) for item in parts]
    except ValueError as exc:
        raise ValueError("shape must contain positive integer dimensions") from exc
    if not result or any(item <= 0 for item in result):
        raise ValueError("shape must contain positive dimensions")
    return result


def _resolve_evidence_artifact(evidence_path: Path, value: object, field: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"ATC evidence {field} must be a non-empty path")
    artifact = _resolve_artifact(evidence_path, value).resolve()
    if not artifact.is_file():
        raise FileNotFoundError(f"ATC evidence {field} does not exist: {artifact}")
    return artifact


def _relative_or_absolute(path: Path, parent: Path) -> str:
    try:
        return str(path.relative_to(parent))
    except ValueError:
        return str(path)


def main() -> int:
    args = parse_args()
    spec = CANDIDATES[args.candidate]
    evidence_path = args.atc_evidence.resolve()
    evidence = load_strict_json_object(evidence_path, label="ATC evidence")
    if evidence.get("status") != "om_ready":
        raise ValueError("ATC evidence does not declare an OM artifact")
    raw_input_shape = evidence.get("input_shape")
    if not isinstance(raw_input_shape, list) or not raw_input_shape:
        raise ValueError("ATC evidence input_shape must be a non-empty list")
    input_shape = [
        _strict_positive_int(value, f"ATC evidence input_shape[{index}]")
        for index, value in enumerate(raw_input_shape)
    ]
    candidate_preprocessing_contract(spec, tuple(input_shape))
    if evidence.get("input_name") != spec.input_name:
        raise ValueError("ATC evidence input_name does not match the reviewed candidate")
    output_shape = parse_shape(args.output_shape)
    onnx_path = _resolve_evidence_artifact(evidence_path, evidence.get("onnx_path"), "onnx_path")
    om_path = _resolve_evidence_artifact(evidence_path, evidence.get("om_path"), "om_path")
    for key, artifact in (("onnx_sha256", onnx_path), ("om_sha256", om_path)):
        expected = evidence.get(key)
        if not isinstance(expected, str) or sha256_file(artifact) != expected.lower():
            raise ValueError(f"ATC evidence {key} does not match {artifact}")
    command = evidence.get("atc_command")
    if not isinstance(command, list) or not command or not all(
        isinstance(part, str) and part for part in command
    ):
        raise ValueError("ATC evidence atc_command must be a non-empty list of strings")
    evidence_cann_version = evidence.get("cann_version", cann_version())
    if not isinstance(evidence_cann_version, str) or not evidence_cann_version.strip():
        raise ValueError("ATC evidence cann_version must be a non-empty string")
    stem = om_path.stem
    output = args.output or evidence_path.parent / f"{stem}.manifest.json"
    output = output.resolve()
    if output in {evidence_path, onnx_path, om_path}:
        raise ValueError("--output must not overwrite an ATC evidence or model artifact")
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing manifest: {output}")
    payload = {
        "schema_version": 3,
        "model_id": f"{spec.model_id_prefix}-b{input_shape[0]}",
        "task": spec.task,
        "source": {
            "url": spec.source_url,
            "revision": spec.source_revision,
            "license": spec.license,
            "upstream_weight_sha256": spec.upstream_weight_sha256,
        },
        "input": {
            "name": spec.input_name,
            "shape": input_shape,
            "dtype": "float32",
            "normalization": spec.normalization,
            "sample_rate_hz": spec.sample_rate_hz,
            "sampling_convention": spec.sampling_convention,
            "preprocessing": candidate_preprocessing_contract(spec, tuple(input_shape)),
        },
        "output": {
            "names": list(spec.output_names),
            "shape": output_shape,
            "class_names": list(spec.class_names),
        },
        "artifacts": {
            "onnx_path": _relative_or_absolute(onnx_path, output.parent),
            "om_path": _relative_or_absolute(om_path, output.parent),
            "onnx_sha256": evidence["onnx_sha256"],
            "om_sha256": evidence["om_sha256"],
        },
        "conversion": {
            "atc_command": command,
            "cann_version": evidence_cann_version,
        },
        "admission": {
            "status": "candidate",
            "source_contract_verified": spec.source_contract_verified,
            "blockers": list(spec.blockers),
            "atc_status": evidence["status"],
            "live_demo_eligible": not (
                spec.task == "spectrogram_detection" and input_shape[0] != 1
            ),
        },
    }
    write_validated_new_json(
        output,
        payload,
        validator=lambda path: load_inference_manifest(path, require_accepted=False),
    )
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
