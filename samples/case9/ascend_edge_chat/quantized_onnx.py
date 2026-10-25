from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable, Mapping

DEFAULT_CANDIDATES_PATH = (
    Path(__file__).resolve().parents[1]
    / "configs"
    / "online-quantized-qwen-candidates.json"
)
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class QuantizedCandidateError(ValueError):
    """Raised when a public quantized-ONNX candidate lock is invalid."""


@dataclass(frozen=True)
class CandidateFile:
    role: str
    filename: str
    expected_bytes: int
    sha256: str

    @property
    def basename(self) -> str:
        return PurePosixPath(self.filename).name


@dataclass(frozen=True)
class QuantizedCandidate:
    id: str
    family: str
    repository: str
    revision: str
    base_model_repository: str
    base_model_revision: str
    license_status: str
    screening_priority: int
    aliases: tuple[str, ...]
    files: tuple[CandidateFile, ...]

    def graph_file(self) -> CandidateFile:
        graphs = [item for item in self.files if item.role == "graph"]
        if len(graphs) != 1:
            raise QuantizedCandidateError(
                "%s must contain exactly one role=graph file" % self.id
            )
        return graphs[0]


@dataclass(frozen=True)
class QuantizedCandidateLock:
    candidates: tuple[QuantizedCandidate, ...]
    allowed_domains: frozenset[str]
    blocked_operator_types: frozenset[str]


def _require_string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise QuantizedCandidateError("%s must be a non-empty string" % field)
    return value


def _validate_relative_path(value: object, field: str) -> str:
    text = _require_string(value, field)
    path = PurePosixPath(text)
    if path.is_absolute() or ".." in path.parts:
        raise QuantizedCandidateError("%s must stay inside the candidate root" % field)
    return text


def load_candidate_lock(path: Path | str = DEFAULT_CANDIDATES_PATH) -> QuantizedCandidateLock:
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise QuantizedCandidateError("candidate lock not found: %s" % path) from exc
    except json.JSONDecodeError as exc:
        raise QuantizedCandidateError("candidate lock is not valid JSON: %s" % exc) from exc

    if raw.get("schema_version") != 1:
        raise QuantizedCandidateError("schema_version must be 1")
    policy = raw.get("screening_policy")
    if not isinstance(policy, dict):
        raise QuantizedCandidateError("screening_policy must be an object")
    allowed_domains = policy.get("allowed_domains")
    blocked_types = policy.get("blocked_operator_types")
    if not isinstance(allowed_domains, list) or not all(
        isinstance(item, str) for item in allowed_domains
    ):
        raise QuantizedCandidateError("allowed_domains must be a string list")
    if "" not in allowed_domains:
        raise QuantizedCandidateError("allowed_domains must include the default ONNX domain")
    if not isinstance(blocked_types, list) or not all(
        isinstance(item, str) and item for item in blocked_types
    ):
        raise QuantizedCandidateError("blocked_operator_types must be a string list")

    raw_candidates = raw.get("candidates")
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise QuantizedCandidateError("candidates must be a non-empty list")

    candidates: list[QuantizedCandidate] = []
    ids: set[str] = set()
    graph_hashes: set[str] = set()
    for index, item in enumerate(raw_candidates):
        prefix = "candidates[%d]" % index
        if not isinstance(item, dict):
            raise QuantizedCandidateError("%s must be an object" % prefix)
        candidate_id = _require_string(item.get("id"), "%s.id" % prefix)
        if not _ID_RE.fullmatch(candidate_id):
            raise QuantizedCandidateError("%s.id must be lower-kebab-case" % prefix)
        if candidate_id in ids:
            raise QuantizedCandidateError("duplicate candidate id: %s" % candidate_id)
        ids.add(candidate_id)

        repository = _require_string(item.get("repository"), "%s.repository" % prefix)
        revision = _require_string(item.get("revision"), "%s.revision" % prefix)
        if not _SHA1_RE.fullmatch(revision):
            raise QuantizedCandidateError("%s.revision must be a full 40-char SHA" % prefix)

        raw_files = item.get("files")
        if not isinstance(raw_files, list) or not raw_files:
            raise QuantizedCandidateError("%s.files must be a non-empty list" % prefix)
        files: list[CandidateFile] = []
        seen_basenames: set[str] = set()
        for file_index, raw_file in enumerate(raw_files):
            file_prefix = "%s.files[%d]" % (prefix, file_index)
            if not isinstance(raw_file, dict):
                raise QuantizedCandidateError("%s must be an object" % file_prefix)
            filename = _validate_relative_path(
                raw_file.get("filename"), "%s.filename" % file_prefix
            )
            role = _require_string(raw_file.get("role"), "%s.role" % file_prefix)
            expected_bytes = raw_file.get("bytes")
            if not isinstance(expected_bytes, int) or expected_bytes <= 0:
                raise QuantizedCandidateError("%s.bytes must be positive" % file_prefix)
            digest = _require_string(raw_file.get("sha256"), "%s.sha256" % file_prefix)
            if not _SHA256_RE.fullmatch(digest):
                raise QuantizedCandidateError("%s.sha256 must be lowercase SHA-256" % file_prefix)
            local_name = PurePosixPath(filename).name
            if local_name in seen_basenames:
                raise QuantizedCandidateError(
                    "%s has duplicate local basename %s" % (prefix, local_name)
                )
            seen_basenames.add(local_name)
            files.append(CandidateFile(role, filename, expected_bytes, digest))

        graph_files = [item for item in files if item.role == "graph"]
        if len(graph_files) != 1:
            raise QuantizedCandidateError(
                "%s must contain exactly one role=graph file" % prefix
            )
        if graph_files[0].sha256 in graph_hashes:
            raise QuantizedCandidateError(
                "duplicate graph SHA-256 in %s" % graph_files[0].sha256
            )
        graph_hashes.add(graph_files[0].sha256)

        aliases = item.get("aliases", [])
        if not isinstance(aliases, list) or not all(isinstance(alias, str) for alias in aliases):
            raise QuantizedCandidateError("%s.aliases must be a string list" % prefix)
        priority = item.get("screening_priority")
        if not isinstance(priority, int) or priority < 1:
            raise QuantizedCandidateError(
                "%s.screening_priority must be a positive integer" % prefix
            )

        candidates.append(
            QuantizedCandidate(
                id=candidate_id,
                family=_require_string(item.get("family"), "%s.family" % prefix),
                repository=repository,
                revision=revision,
                base_model_repository=_require_string(
                    item.get("base_model_repository"),
                    "%s.base_model_repository" % prefix,
                ),
                base_model_revision=_require_string(
                    item.get("base_model_revision"),
                    "%s.base_model_revision" % prefix,
                ),
                license_status=_require_string(
                    item.get("license_status"), "%s.license_status" % prefix
                ),
                screening_priority=priority,
                aliases=tuple(aliases),
                files=tuple(files),
            )
        )

    return QuantizedCandidateLock(
        candidates=tuple(candidates),
        allowed_domains=frozenset(allowed_domains),
        blocked_operator_types=frozenset(blocked_types),
    )


def classify_operator_risks(
    domains: Iterable[str],
    operator_types: Iterable[str],
    lock: QuantizedCandidateLock,
) -> list[dict[str, str]]:
    reasons: list[dict[str, str]] = []
    blocked_domains = sorted({domain for domain in domains if domain not in lock.allowed_domains})
    for domain in blocked_domains:
        reasons.append(
            {
                "code": "domain-not-allowed",
                "value": domain or "<empty>",
                "detail": "ATC domain allowlist rejects this operator domain",
            }
        )
    blocked_ops = sorted(set(operator_types) & lock.blocked_operator_types)
    for operator in blocked_ops:
        reasons.append(
            {
                "code": "operator-not-eligible",
                "value": operator,
                "detail": "known unsupported or vendor-specific operator form",
            }
        )
    return reasons


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _candidate_path(root: Path, candidate: QuantizedCandidate, item: CandidateFile) -> Path:
    return root / candidate.id / item.basename


def _verify_file(path: Path, expected: CandidateFile) -> dict[str, Any]:
    result = {
        "role": expected.role,
        "filename": expected.filename,
        "path": str(path),
        "expected_bytes": expected.expected_bytes,
        "expected_sha256": expected.sha256,
        "present": path.is_file(),
    }
    if not path.is_file():
        return result
    result["bytes"] = path.stat().st_size
    result["sha256"] = _sha256(path)
    result["verified"] = (
        result["bytes"] == expected.expected_bytes
        and result["sha256"] == expected.sha256
    )
    return result


def _shape(value: Any) -> list[int | str]:
    dimensions = value.type.tensor_type.shape.dim
    return [
        int(dimension.dim_value)
        if dimension.HasField("dim_value") and dimension.dim_value > 0
        else (dimension.dim_param or "?")
        for dimension in dimensions
    ]


def _inspect_graph(path: Path, lock: QuantizedCandidateLock) -> dict[str, Any]:
    try:
        import onnx
    except ImportError as exc:
        raise QuantizedCandidateError(
            "onnx is required for graph inspection; install it in the board ascend environment"
        ) from exc

    model = onnx.load(str(path), load_external_data=False)
    onnx.checker.check_model(model, full_check=False)
    graph = model.graph
    domains = Counter(node.domain or "ai.onnx" for node in graph.node)
    operators = Counter(node.op_type for node in graph.node)
    risks = classify_operator_risks(domains, operators, lock)
    inputs = [
        {
            "name": value.name,
            "shape": _shape(value),
            "dtype": onnx.TensorProto.DataType.Name(
                value.type.tensor_type.elem_type
            ),
        }
        for value in graph.input
    ]
    outputs = [
        {
            "name": value.name,
            "shape": _shape(value),
            "dtype": onnx.TensorProto.DataType.Name(
                value.type.tensor_type.elem_type
            ),
        }
        for value in graph.output
    ]
    return {
        "ir_version": int(model.ir_version),
        "opsets": [
            {"domain": item.domain or "ai.onnx", "version": int(item.version)}
            for item in model.opset_import
        ],
        "node_count": len(graph.node),
        "input_count": len(graph.input),
        "output_count": len(graph.output),
        "dynamic_input_count": sum(
            any(isinstance(dimension, str) for dimension in item["shape"])
            for item in inputs
        ),
        "external_initializer_count": sum(
            item.data_location == onnx.TensorProto.EXTERNAL
            for item in graph.initializer
        ),
        "operator_counts": dict(sorted(operators.items())),
        "domain_counts": dict(sorted(domains.items())),
        "inputs": inputs,
        "outputs": outputs,
        "risk_reasons": risks,
    }


def screen_candidates(
    artifacts_root: Path | str,
    lock: QuantizedCandidateLock,
    candidate_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    root = Path(artifacts_root)
    requested = set(candidate_ids) if candidate_ids is not None else None
    known = {candidate.id for candidate in lock.candidates}
    if requested is not None:
        missing = sorted(requested - known)
        if missing:
            raise QuantizedCandidateError("unknown candidate id: %s" % ", ".join(missing))

    reports: list[dict[str, Any]] = []
    for candidate in sorted(
        lock.candidates, key=lambda item: (item.screening_priority, item.id)
    ):
        if requested is not None and candidate.id not in requested:
            continue
        file_results = [
            _verify_file(_candidate_path(root, candidate, item), item)
            for item in candidate.files
        ]
        graph = next(
            result
            for result, item in zip(file_results, candidate.files)
            if item.role == "graph"
        )
        report: dict[str, Any] = {
            "id": candidate.id,
            "family": candidate.family,
            "repository": candidate.repository,
            "revision": candidate.revision,
            "license_status": candidate.license_status,
            "files": file_results,
            "state": "untested",
            "risk_reasons": [],
        }
        if not graph["present"]:
            report["reason"] = "graph file is not present"
            reports.append(report)
            continue
        if not graph.get("verified", False):
            report["state"] = "artifact-mismatch"
            report["reason"] = "graph bytes or SHA-256 do not match the lock"
            reports.append(report)
            continue

        graph_report = _inspect_graph(Path(graph["path"]), lock)
        report.update(graph_report)
        if graph_report["risk_reasons"]:
            codes = {item["code"] for item in graph_report["risk_reasons"]}
            report["state"] = (
                "blocked-domain"
                if "domain-not-allowed" in codes
                else "blocked-operator"
            )
        else:
            report["state"] = "atc-eligible"
        reports.append(report)

    return {
        "schema_version": 1,
        "artifacts_root": str(root.resolve()),
        "candidate_count": len(reports),
        "state_counts": dict(sorted(Counter(item["state"] for item in reports).items())),
        "candidates": reports,
    }
