"""Add current provenance fields to an already admitted board manifest.

This utility is intentionally conservative: it verifies the existing ONNX and
OM digests, checks the candidate's immutable source identity, and only fills
fields that older Case 5 manifests did not retain.  It does not run ATC or
change the numerical admission record.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .candidate_catalog import CANDIDATES, candidate_preprocessing_contract
from .inference_manifest import (
    _resolve_artifact,
    load_inference_manifest,
    load_strict_json_object,
    sha256_file,
)
from .safe_json import write_validated_new_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--candidate", choices=tuple(CANDIDATES), required=True)
    parser.add_argument(
        "--output",
        type=Path,
        help="new manifest path; defaults to an .upgraded.manifest.json sibling",
    )
    return parser.parse_args()


def _artifact_path(manifest_path: Path, value: object) -> Path:
    return _resolve_artifact(manifest_path, value).resolve()


def main() -> int:
    args = parse_args()
    manifest_path = args.manifest.resolve()
    raw = load_strict_json_object(manifest_path, label="manifest")
    source = raw.get("source")
    input_spec = raw.get("input")
    artifacts = raw.get("artifacts")
    admission = raw.get("admission")
    if not all(isinstance(value, dict) for value in (source, input_spec, artifacts, admission)):
        raise ValueError("manifest must contain source, input, artifacts, and admission objects")
    spec = CANDIDATES[args.candidate]
    if source.get("url") != spec.source_url or source.get("revision") != spec.source_revision:
        raise ValueError("candidate source URL/revision does not match the existing manifest")
    if input_spec.get("name") != spec.input_name or input_spec.get("normalization") != spec.normalization:
        raise ValueError("candidate input contract does not match the existing manifest")
    input_shape_raw = input_spec.get("shape", [])
    if not isinstance(input_shape_raw, list):
        raise ValueError("manifest input shape must be a list")
    input_shape = tuple(input_shape_raw)
    if not input_shape or any(
        isinstance(value, bool) or not isinstance(value, int) or value <= 0
        for value in input_shape
    ):
        raise ValueError("manifest input shape must contain positive integer dimensions")
    existing_weight_digest = source.get("upstream_weight_sha256")
    if existing_weight_digest is not None and (
        not isinstance(existing_weight_digest, str)
        or existing_weight_digest.lower() != spec.upstream_weight_sha256
    ):
        raise ValueError("candidate upstream weight digest does not match the existing manifest")
    for label, digest_key, path_key in (
        ("ONNX", "onnx_sha256", "onnx_path"),
        ("OM", "om_sha256", "om_path"),
    ):
        expected = str(artifacts.get(digest_key, "")).lower()
        path = _artifact_path(manifest_path, artifacts.get(path_key, ""))
        if len(expected) != 64 or sha256_file(path) != expected:
            raise RuntimeError(f"existing {label} artifact digest does not match its manifest")
    expected_preprocessing = candidate_preprocessing_contract(spec, input_shape)
    for key, expected in (
        ("upstream_weight_sha256", spec.upstream_weight_sha256),
        ("sampling_convention", spec.sampling_convention),
    ):
        container = source if key == "upstream_weight_sha256" else input_spec
        if key in container and container[key] != expected:
            raise ValueError(f"existing manifest {key} conflicts with the reviewed candidate")
        container[key] = expected
    if "preprocessing" in input_spec and input_spec["preprocessing"] != expected_preprocessing:
        raise ValueError("existing manifest preprocessing conflicts with the reviewed candidate")
    input_spec["preprocessing"] = expected_preprocessing
    if "sample_rate_hz" in input_spec and input_spec["sample_rate_hz"] != spec.sample_rate_hz:
        raise ValueError("existing manifest sample rate conflicts with the reviewed candidate")
    input_spec.setdefault("sample_rate_hz", spec.sample_rate_hz)
    conversion = raw.setdefault("conversion", {})
    if not isinstance(conversion, dict):
        raise ValueError("manifest conversion must be an object")
    # An upgrade may happen long after ATC.  Preserve the conversion version
    # recorded by the original manifest rather than attributing an old OM to
    # the toolkit currently running this utility.
    if not isinstance(conversion.get("cann_version"), str) or not conversion["cann_version"].strip():
        conversion["cann_version"] = "unknown"
        conversion["cann_version_provenance"] = "not retained by the legacy manifest"
    if "npu_p95_meets_window_budget" not in admission:
        legacy_budget = admission.get("p95_meets_real_time")
        if legacy_budget is not True and legacy_budget is not False:
            raise ValueError(
                "existing manifest has no boolean NPU window-budget admission result"
            )
        admission["npu_p95_meets_window_budget"] = legacy_budget
        admission["npu_p95_meets_window_budget_provenance"] = (
            "migrated from legacy p95_meets_real_time, which measured the OM boundary"
        )
    schema_version = raw.get("schema_version", 1)
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version <= 0:
        raise ValueError("manifest schema_version must be a positive integer")
    raw["schema_version"] = max(schema_version, 3)
    default_output = manifest_path.with_name(
        manifest_path.name.removesuffix(".manifest.json") + ".upgraded.manifest.json"
    )
    output = (args.output or default_output).resolve()
    if output == manifest_path:
        raise ValueError("--output must be a new manifest path")
    if output.parent != manifest_path.parent:
        raise ValueError(
            "--output must remain beside the source manifest so relative artifacts stay bound"
        )
    if output.exists():
        raise FileExistsError(f"refusing to overwrite existing upgraded manifest: {output}")
    write_validated_new_json(
        output,
        raw,
        validator=lambda path: load_inference_manifest(path, require_accepted=False),
    )
    print(f"wrote {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
