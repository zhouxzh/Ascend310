"""Versioned deployment manifest for reviewed third-party ONNX/OM models."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping

from .preprocessing_contract import validate_preprocessing_contract


SUPPORTED_TASKS = {"iq_classification", "spectrogram_detection"}
SUPPORTED_NORMALIZATIONS = {"none", "per_channel_zscore", "infinity_norm"}


@dataclass(frozen=True)
class InferenceModelManifest:
    schema_version: int
    model_id: str
    task: str
    source_url: str
    source_revision: str
    license: str
    upstream_weight_sha256: str
    input_name: str
    input_shape: tuple[int, ...]
    output_names: tuple[str, ...]
    output_shape: tuple[int, ...] | None
    input_dtype: str
    normalization: str
    sampling_convention: str
    preprocessing: Mapping[str, Any]
    class_names: tuple[str, ...]
    sample_rate_hz: float | None
    onnx_path: Path
    om_path: Path
    onnx_sha256: str
    om_sha256: str
    atc_command: tuple[str, ...]
    cann_version: str
    conversion_metadata: Mapping[str, Any]
    admission: Mapping[str, Any]
    manifest_path: Path

    @property
    def batch_size(self) -> int:
        return self.input_shape[0]

    @property
    def window_samples(self) -> int:
        if self.task == "iq_classification":
            return self.input_shape[2]
        return self.input_shape[2] * self.input_shape[3]

    def to_dict(self) -> dict[str, Any]:
        def relative_or_absolute(path: Path) -> str:
            try:
                return str(path.relative_to(self.manifest_path.parent))
            except ValueError:
                return str(path)

        return {
            "schema_version": self.schema_version,
            "model_id": self.model_id,
            "task": self.task,
            "source": {
                "url": self.source_url,
                "revision": self.source_revision,
                "license": self.license,
                "upstream_weight_sha256": self.upstream_weight_sha256,
            },
            "input": {
                "name": self.input_name,
                "shape": list(self.input_shape),
                "dtype": self.input_dtype,
                "normalization": self.normalization,
                "sample_rate_hz": self.sample_rate_hz,
                "sampling_convention": self.sampling_convention,
                "preprocessing": dict(self.preprocessing),
            },
            "output": {
                "names": list(self.output_names),
                "shape": None if self.output_shape is None else list(self.output_shape),
                "class_names": list(self.class_names),
            },
            "artifacts": {
                "onnx_path": relative_or_absolute(self.onnx_path),
                "om_path": relative_or_absolute(self.om_path),
                "onnx_sha256": self.onnx_sha256,
                "om_sha256": self.om_sha256,
            },
            "conversion": {
                **dict(self.conversion_metadata),
                "atc_command": list(self.atc_command),
                "cann_version": self.cann_version,
            },
            "admission": dict(self.admission),
        }


def _required(mapping: Mapping[str, Any], key: str, context: str) -> Any:
    if key not in mapping:
        raise ValueError(f"manifest is missing {context}.{key}")
    return mapping[key]


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """Build a JSON object while rejecting ambiguous duplicate member names."""
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate key in JSON object: {key!r}")
        result[key] = value
    return result


def _reject_nonstandard_json_constant(value: str) -> Any:
    raise ValueError(f"manifest contains unsupported non-standard constant: {value}")


def load_strict_json_object(path: Path, *, label: str = "manifest") -> dict[str, Any]:
    """Load one portable JSON object without duplicate-key ambiguity."""
    try:
        raw = json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonstandard_json_constant,
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid {label} JSON: {exc}") from exc
    except ValueError as exc:
        raise ValueError(f"invalid {label} JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{label} root must be an object")
    return raw


def _strict_positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"manifest {field} must be a positive integer")
    return value


def _nonempty_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"manifest {field} must be a non-empty string")
    return value


def _parse_atc_command(value: object) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or any(
        not isinstance(part, str) or not part for part in value
    ):
        raise ValueError("manifest conversion.atc_command must be a non-empty list of strings")
    return tuple(value)


def _parse_shape(value: object, field: str) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"manifest {field} must be a non-empty list of positive integers")
    return tuple(
        _strict_positive_int(item, f"{field}[{index}]")
        for index, item in enumerate(value)
    )


def _resolve_artifact(manifest_path: Path, value: object) -> Path:
    path = Path(str(value))
    return path if path.is_absolute() else manifest_path.parent / path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def model_contract_sha256(manifest: InferenceModelManifest) -> str:
    """Hash the immutable deployment contract independently of admission evidence.

    A later manifest revision can attach a board-run report without changing the
    source/model/input/output/OM contract that produced that report.  This
    digest is deliberately independent of the manifest path, schema version,
    and ``admission`` object so it remains stable across that evidence-only
    revision.
    """
    contract = {
        "model_id": manifest.model_id,
        "task": manifest.task,
        "source": {
            "url": manifest.source_url,
            "revision": manifest.source_revision,
            "license": manifest.license,
            "upstream_weight_sha256": manifest.upstream_weight_sha256,
        },
        "input": {
            "name": manifest.input_name,
            "shape": list(manifest.input_shape),
            "dtype": manifest.input_dtype,
            "normalization": manifest.normalization,
            "sample_rate_hz": manifest.sample_rate_hz,
            "sampling_convention": manifest.sampling_convention,
            "preprocessing": dict(manifest.preprocessing),
        },
        "output": {
            "names": list(manifest.output_names),
            "shape": None if manifest.output_shape is None else list(manifest.output_shape),
            "class_names": list(manifest.class_names),
        },
        "artifacts": {
            "onnx_sha256": manifest.onnx_sha256,
            "om_sha256": manifest.om_sha256,
        },
        "conversion": {
            "atc_command": list(manifest.atc_command),
            "cann_version": manifest.cann_version,
        },
    }
    encoded = json.dumps(
        contract,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_sha256(value: str, field: str) -> str:
    normalized = value.lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise ValueError(f"manifest {field} must be a SHA256 hex digest")
    return normalized


def _finite_nonnegative(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite non-negative number")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite non-negative number") from exc
    if not math.isfinite(numeric) or numeric < 0.0:
        raise ValueError(f"{field} must be a finite non-negative number")
    return numeric


def _validate_pipeline_realtime_evidence(manifest: InferenceModelManifest) -> None:
    """Validate optional, externally recorded short-run pipeline evidence.

    Loading a normal accepted model never requires this evidence: it is created
    only after a separate live run has produced a JSONL report.  When present,
    bind it to the immutable model contract so an evidence-only manifest cannot
    silently be repointed at a different OM or preprocessing contract.
    """
    evidence = manifest.admission.get("pipeline_realtime")
    if evidence is None:
        return
    if not isinstance(evidence, Mapping):
        raise ValueError("manifest admission.pipeline_realtime must be an object")
    schema_version = _required(evidence, "schema_version", "admission.pipeline_realtime")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int) or schema_version != 1:
        raise ValueError("unsupported pipeline realtime evidence schema")
    for key in (
        "verified",
        "pipeline_real_time_passed",
        "continuous_pipeline_soak_verified",
    ):
        if not isinstance(_required(evidence, key, "admission.pipeline_realtime"), bool):
            raise ValueError(f"pipeline realtime evidence {key} must be boolean")
    if evidence["verified"] is not True:
        raise ValueError("pipeline realtime evidence must be structurally verified")
    if evidence["continuous_pipeline_soak_verified"] is not False:
        raise ValueError(
            "short-run pipeline evidence cannot claim a continuous pipeline soak"
        )
    validation_level = _required(evidence, "validation_level", "admission.pipeline_realtime")
    if validation_level != "structurally_validated_self_report":
        raise ValueError("unsupported pipeline realtime validation level")
    source = _required(evidence, "source", "admission.pipeline_realtime")
    if source != "rtl":
        raise ValueError("pipeline realtime evidence source must be rtl")
    rf_input_context = evidence.get("rf_input_context")
    if rf_input_context is not None and (
        not isinstance(rf_input_context, str) or rf_input_context not in {
            "unknown",
            "disconnected",
            "antenna_connected",
            "lab_cabled",
        }
    ):
        raise ValueError("unsupported pipeline realtime rf_input_context")
    for key in (
        "model_contract_sha256",
        "source_run_manifest_sha256",
        "report_sha256",
        "capture_sha256",
    ):
        digest = _required(evidence, key, "admission.pipeline_realtime")
        _validate_sha256(
            digest if isinstance(digest, str) else "",
            f"admission.pipeline_realtime.{key}",
        )
    if str(evidence["model_contract_sha256"]).lower() != model_contract_sha256(manifest):
        raise ValueError(
            "pipeline realtime evidence is bound to a different model contract"
        )
    for key in (
        "sample_rate_hz",
        "batch_duration_ms",
        "post_capture_pipeline_p50_ms",
        "post_capture_pipeline_p95_ms",
        "post_capture_pipeline_max_ms",
        "observation_duration_ms",
    ):
        _finite_nonnegative(
            _required(evidence, key, "admission.pipeline_realtime"),
            f"pipeline realtime evidence {key}",
        )
    if float(evidence["sample_rate_hz"]) <= 0.0 or float(evidence["batch_duration_ms"]) <= 0.0:
        raise ValueError("pipeline realtime evidence sample rate and batch duration must be positive")
    if manifest.task == "iq_classification":
        required_samples = manifest.input_shape[0] * manifest.input_shape[2]
    else:
        required_samples = manifest.input_shape[0] * manifest.input_shape[2] * manifest.input_shape[3]
    expected_duration = 1_000.0 * required_samples / float(evidence["sample_rate_hz"])
    if abs(float(evidence["batch_duration_ms"]) - expected_duration) > 1.0e-6:
        raise ValueError(
            "pipeline realtime evidence batch duration does not match the fixed input window"
        )
    for key in ("produced_batches", "completed_batches", "dropped_batches", "minimum_batches"):
        value = _required(evidence, key, "admission.pipeline_realtime")
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"pipeline realtime evidence {key} must be a non-negative integer")
    if evidence["minimum_batches"] != 2:
        raise ValueError("pipeline realtime evidence minimum_batches must be 2")
    if evidence["completed_batches"] > evidence["produced_batches"]:
        raise ValueError("pipeline realtime evidence completed_batches exceeds produced_batches")
    p50 = float(evidence["post_capture_pipeline_p50_ms"])
    p95 = float(evidence["post_capture_pipeline_p95_ms"])
    maximum = float(evidence["post_capture_pipeline_max_ms"])
    if not p50 <= p95 <= maximum:
        raise ValueError("pipeline realtime latency percentiles are not ordered")
    if evidence["pipeline_real_time_passed"] and not (
        evidence["produced_batches"] == evidence["completed_batches"]
        and evidence["completed_batches"] >= evidence["minimum_batches"]
        and evidence["dropped_batches"] == 0
        and maximum <= float(evidence["batch_duration_ms"])
    ):
        raise ValueError("pipeline realtime passing verdict is inconsistent with its metrics")
    for key in ("report_path", "capture_path", "source_manifest_path", "evidence_scope"):
        value = _required(evidence, key, "admission.pipeline_realtime")
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"admission.pipeline_realtime.{key} must be non-empty")
    if evidence["evidence_scope"] != "short_run_pipeline_window_check":
        raise ValueError("unsupported pipeline realtime evidence scope")
    capture_bytes = _required(evidence, "capture_bytes", "admission.pipeline_realtime")
    if isinstance(capture_bytes, bool) or not isinstance(capture_bytes, int) or capture_bytes < 0:
        raise ValueError("pipeline realtime evidence capture_bytes must be a non-negative integer")
    continuous = _required(evidence, "continuous_pipeline_realtime", "admission.pipeline_realtime")
    if not isinstance(continuous, Mapping):
        raise ValueError("continuous pipeline realtime evidence must be an object")
    for key in (
        "short_run_pipeline_real_time_passed",
        "continuous_pipeline_realtime_passed",
    ):
        if not isinstance(_required(continuous, key, "continuous_pipeline_realtime"), bool):
            raise ValueError(f"continuous pipeline evidence {key} must be boolean")
    minimum = _finite_nonnegative(
        _required(continuous, "minimum_observation_ms", "continuous_pipeline_realtime"),
        "continuous pipeline evidence minimum_observation_ms",
    )
    observed = _finite_nonnegative(
        _required(continuous, "observation_duration_ms", "continuous_pipeline_realtime"),
        "continuous pipeline evidence observation_duration_ms",
    )
    if minimum != 600_000.0:
        raise ValueError("continuous pipeline evidence has an invalid observation duration")
    context = _required(continuous, "rf_input_context", "continuous_pipeline_realtime")
    if not isinstance(context, str) or context not in {
        "unknown",
        "disconnected",
        "antenna_connected",
        "lab_cabled",
    }:
        raise ValueError("continuous pipeline evidence has an invalid RF input context")
    if rf_input_context is None or context != rf_input_context:
        raise ValueError("continuous pipeline RF input context does not match short-run evidence")
    if continuous["short_run_pipeline_real_time_passed"] is not evidence["pipeline_real_time_passed"]:
        raise ValueError("continuous pipeline short-run verdict does not match short-run evidence")
    if not math.isclose(
        observed,
        float(evidence["observation_duration_ms"]),
        rel_tol=0.0,
        abs_tol=1.0e-6,
    ):
        raise ValueError("continuous pipeline observation duration does not match short-run evidence")
    expected_continuous = bool(
        context in {"antenna_connected", "lab_cabled"}
        and observed >= minimum
        and continuous["short_run_pipeline_real_time_passed"] is True
    )
    if continuous["continuous_pipeline_realtime_passed"] is not expected_continuous:
        raise ValueError("continuous pipeline passing verdict is inconsistent with its metrics")


def load_inference_manifest(
    path: Path, *, require_accepted: bool = True, require_artifacts: bool = True
) -> InferenceModelManifest:
    """Load and validate a model manifest without importing training frameworks."""
    manifest_path = Path(path).resolve()
    raw = load_strict_json_object(manifest_path, label="manifest")
    source = _required(raw, "source", "root")
    input_spec = _required(raw, "input", "root")
    output_spec = _required(raw, "output", "root")
    artifacts = _required(raw, "artifacts", "root")
    conversion = _required(raw, "conversion", "root")
    admission = _required(raw, "admission", "root")
    for name, value in (
        ("source", source),
        ("input", input_spec),
        ("output", output_spec),
        ("artifacts", artifacts),
        ("conversion", conversion),
        ("admission", admission),
    ):
        if not isinstance(value, dict):
            raise ValueError(f"manifest {name} must be an object")

    schema_version = _strict_positive_int(_required(raw, "schema_version", "root"), "schema_version")
    task_raw = _required(raw, "task", "root")
    if not isinstance(task_raw, str):
        raise ValueError("manifest task must be a string")
    task = task_raw
    if task not in SUPPORTED_TASKS:
        raise ValueError(f"unsupported manifest task: {task}")
    shape = _parse_shape(_required(input_spec, "shape", "input"), "input.shape")
    if task == "iq_classification" and (len(shape) != 3 or shape[1] != 2):
        raise ValueError("iq_classification input must have shape [batch, 2, samples]")
    if task == "spectrogram_detection" and (
        len(shape) != 4 or shape[1] != 3 or shape[2] != shape[3]
    ):
        raise ValueError(
            "spectrogram_detection input must have shape [batch, 3, nfft, nfft]"
        )
    normalization_raw = _required(input_spec, "normalization", "input")
    if not isinstance(normalization_raw, str):
        raise ValueError("manifest input.normalization must be a string")
    normalization = normalization_raw
    if normalization not in SUPPORTED_NORMALIZATIONS:
        raise ValueError(f"unsupported normalization: {normalization}")
    if _required(input_spec, "dtype", "input") != "float32":
        raise ValueError("only float32 model inputs are supported")
    sampling_convention_raw = _required(input_spec, "sampling_convention", "input")
    if not isinstance(sampling_convention_raw, str):
        raise ValueError("manifest input.sampling_convention must be a string")
    sampling_convention = sampling_convention_raw.strip()
    if not sampling_convention:
        raise ValueError("manifest input.sampling_convention must be non-empty")
    if task == "spectrogram_detection" and normalization != "none":
        raise ValueError(
            "spectrogram_detection normalization must be none for the fixed FFTW image path"
        )
    preprocessing = validate_preprocessing_contract(
        task, shape, _required(input_spec, "preprocessing", "input")
    )
    output_names_raw = _required(output_spec, "names", "output")
    if not isinstance(output_names_raw, list) or not output_names_raw or any(
        not isinstance(value, str) or not value for value in output_names_raw
    ):
        raise ValueError("manifest must declare at least one output name")
    output_names = tuple(output_names_raw)
    output_shape_raw = output_spec.get("shape")
    declared_output_shape = (
        None if output_shape_raw is None else _parse_shape(output_shape_raw, "output.shape")
    )
    class_names_raw = output_spec.get("class_names", [])
    if not isinstance(class_names_raw, list) or not class_names_raw or any(
        not isinstance(value, str) or not value for value in class_names_raw
    ):
        raise ValueError("inference manifests must declare non-empty class_names")
    class_names = tuple(class_names_raw)
    if task == "iq_classification":
        if (
            declared_output_shape is None
            or len(declared_output_shape) != 2
            or declared_output_shape[0] != shape[0]
            or declared_output_shape[1] != len(class_names)
        ):
            raise ValueError(
                "iq_classification output must have shape [batch, class_count] matching class_names"
            )
    if require_accepted and admission.get("status") != "accepted":
        raise ValueError(
            f"model admission status is {admission.get('status', 'missing')!r}, not 'accepted'"
        )

    onnx_path = _resolve_artifact(
        manifest_path, _required(artifacts, "onnx_path", "artifacts")
    )
    om_path = _resolve_artifact(
        manifest_path, _required(artifacts, "om_path", "artifacts")
    )
    if require_artifacts:
        for artifact in (onnx_path, om_path):
            if not artifact.is_file():
                raise FileNotFoundError(f"manifest artifact not found: {artifact}")

    sample_rate = input_spec.get("sample_rate_hz")
    if sample_rate is not None:
        if isinstance(sample_rate, bool):
            raise ValueError("sample_rate_hz must be a finite positive number when declared")
        try:
            sample_rate_value = float(sample_rate)
        except (TypeError, ValueError) as exc:
            raise ValueError("sample_rate_hz must be a finite positive number when declared") from exc
        if not math.isfinite(sample_rate_value) or sample_rate_value <= 0:
            raise ValueError("sample_rate_hz must be a finite positive number when declared")
    else:
        sample_rate_value = None
    manifest = InferenceModelManifest(
        schema_version=schema_version,
        model_id=_nonempty_string(_required(raw, "model_id", "root"), "model_id"),
        task=task,
        source_url=_nonempty_string(_required(source, "url", "source"), "source.url"),
        source_revision=_nonempty_string(
            _required(source, "revision", "source"), "source.revision"
        ),
        license=_nonempty_string(_required(source, "license", "source"), "source.license"),
        upstream_weight_sha256=_validate_sha256(
            str(_required(source, "upstream_weight_sha256", "source")),
            "source.upstream_weight_sha256",
        ),
        input_name=_nonempty_string(_required(input_spec, "name", "input"), "input.name"),
        input_shape=shape,
        output_names=output_names,
        output_shape=declared_output_shape,
        input_dtype="float32",
        normalization=normalization,
        sampling_convention=sampling_convention,
        preprocessing=preprocessing,
        class_names=class_names,
        sample_rate_hz=sample_rate_value,
        onnx_path=onnx_path,
        om_path=om_path,
        onnx_sha256=_validate_sha256(
            str(_required(artifacts, "onnx_sha256", "artifacts")), "artifacts.onnx_sha256"
        ),
        om_sha256=_validate_sha256(
            str(_required(artifacts, "om_sha256", "artifacts")), "artifacts.om_sha256"
        ),
        atc_command=_parse_atc_command(conversion.get("atc_command", [])),
        cann_version=_nonempty_string(conversion.get("cann_version", "unknown"), "conversion.cann_version"),
        conversion_metadata={
            str(key): value
            for key, value in conversion.items()
            if key not in {"atc_command", "cann_version"}
        },
        admission=admission,
        manifest_path=manifest_path,
    )
    _validate_pipeline_realtime_evidence(manifest)
    return manifest


def verify_artifact_hashes(manifest: InferenceModelManifest) -> None:
    """Fail closed when a deployed ONNX or OM differs from its admission record."""
    for label, artifact, expected in (
        ("ONNX", manifest.onnx_path, manifest.onnx_sha256),
        ("OM", manifest.om_path, manifest.om_sha256),
    ):
        observed = sha256_file(artifact)
        if observed != expected:
            raise RuntimeError(
                f"{label} SHA256 mismatch for {artifact}: expected {expected}, got {observed}"
            )


def ensure_live_deployment_ready(manifest: InferenceModelManifest) -> None:
    """Require all admission gates before a live NPU source is opened."""
    admission = manifest.admission
    failed = [
        key
        for key in (
            "numerical_passed",
            "source_contract_verified",
            "npu_p95_meets_window_budget",
            "live_demo_eligible",
        )
        if admission.get(key) is not True
    ]
    if admission.get("status") != "accepted":
        failed.insert(0, "status=accepted")
    if len(manifest.output_names) != 1:
        failed.append("exactly_one_primary_output")
    for key in ("npu_speedup_over_cpu", "npu_p95_ms"):
        try:
            _finite_nonnegative(admission.get(key), f"admission.{key}")
        except ValueError:
            failed.append(key)
    if failed:
        raise ValueError(
            "manifest is not eligible for live NPU inference: " + ", ".join(failed)
        )


def select_default_manifest(models_dir: Path) -> Path:
    """Select an accepted model with a passing NPU window-budget measurement."""
    candidates: list[tuple[float, float, Path]] = []
    for path in sorted(Path(models_dir).rglob("*.manifest.json")):
        try:
            manifest = load_inference_manifest(path, require_accepted=True)
            ensure_live_deployment_ready(manifest)
            verify_artifact_hashes(manifest)
            admission = manifest.admission
            speedup = _finite_nonnegative(
                admission.get("npu_speedup_over_cpu"),
                "admission.npu_speedup_over_cpu",
            )
            p95 = _finite_nonnegative(
                admission.get("npu_p95_ms"), "admission.npu_p95_ms"
            )
        except (OSError, ValueError, RuntimeError):
            continue
        # Pipeline evidence is intentionally not part of automatic model
        # selection: the published policy is maximum admitted speedup, then
        # NPU P95.  It remains an independent, auditable live-run conclusion.
        candidates.append((-speedup, p95, path))
    if not candidates:
        raise FileNotFoundError(f"no accepted real-time model manifest found in {models_dir}")
    candidates.sort()
    return candidates[0][2]
