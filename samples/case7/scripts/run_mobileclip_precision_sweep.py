#!/usr/bin/env python3
"""Run an isolated, serial MobileCLIP image precision sweep on an Ascend board.

The command deliberately treats every candidate as disposable evidence.  It
never changes ``models/om/mobileclip_s0_image.om``, ``models/registry.json`` or
the production FAISS/SQLite files.  ATC, ACL, and the optional retrieval and
performance gates are run only on the board after CANN has been sourced.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import numpy as np

# A script launched as ``python scripts/<name>.py`` gets ``scripts/`` as
# sys.path[0].  Insert the case root explicitly so the runtime modules resolve
# identically from the release root and from a board shell.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from embedding_backend import MOBILECLIP_ID, NpuEmbeddingBackend, l2_normalize
from model_registry import ModelRegistry, load_candidates, sha256_file
from photo_index import AlbumIndex


MODEL_ID = MOBILECLIP_ID
COMPONENT = "image"
FIXED_GALLERY_COUNT = 500
DEFAULT_MANIFEST = ROOT / "reports" / "datasets" / "coco_cn_case7_manifest.json"
# ``reports/model_pipeline`` is reserved for production conversion/admission
# evidence.  Candidate artifacts must live in a sibling tree so the pipeline's
# overwrite guard can prove they cannot replace production reports.
DEFAULT_OUTPUT_DIR = ROOT / "reports" / "precision_sweep" / "mobileclip_s0_image_precision"
DEFAULT_SOC = "Ascend310B4"
NUMERICAL_THRESHOLD = 0.995
FIXTURE_IMAGE_COUNT = 32
RANDOM_SEEDS = (310, 311, 312, 313)

# The order is part of the experiment protocol.  Do not sort this mapping by
# node count: C0/C1 are diagnostic gates and C2/C3 are the competing minimal
# exception sets.
CANDIDATE_IDS = ("C0", "C1", "C2", "C3", "C4")
CONFIG_DIR = ROOT / "atc_configs" / "mobileclip_s0_image_precision"

SERIAL_ENV = {
    "MAX_COMPILE_CORE_NUMBER": "1",
    # CANN 8.x defaults model conversion to multi-thread mode.  Keep this
    # explicit even when TE_PARALLEL_COMPILER is limited to one process.
    "MULTI_THREAD_COMPILE": "0",
    "TBE_PARALLEL_COMPILER": "0",
    "TE_PARALLEL_COMPILER": "1",
    "ASCENDC_PAR_COMPILE_JOB": "0",
    "TILINGKEY_PAR_COMPILE": "0",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "CMAKE_BUILD_PARALLEL_LEVEL": "1",
    "MAKEFLAGS": "-j1",
    "VECLIB_MAXIMUM_THREADS": "1",
    "BLIS_NUM_THREADS": "1",
    "GOMP_NUM_THREADS": "1",
}


class SweepError(RuntimeError):
    """A sweep precondition or gate failure."""


def percentile(values: Sequence[float], q: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * float(q) / 100.0
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def serial_environment(base: Optional[Mapping[str, str]] = None) -> Dict[str, str]:
    """Return an environment with every compiler/library worker pool bounded."""
    environment = dict(base or os.environ)
    environment.update(SERIAL_ENV)
    # Reproducible fixture generation and no implicit compiler parallelism.
    environment["PYTHONHASHSEED"] = "0"
    return environment


def _read_nodes(path: Path) -> List[str]:
    if not path.is_file():
        raise SweepError(f"missing keep-dtype configuration: {path}")
    nodes = []
    seen = set()
    for raw in path.read_text(encoding="utf-8").splitlines():
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        if not value.startswith("/"):
            raise SweepError(f"keep-dtype node must be an absolute ONNX name: {value!r}")
        if value in seen:
            raise SweepError(f"duplicate keep-dtype node in {path}: {value}")
        if value not in seen:
            nodes.append(value)
            seen.add(value)
    return nodes


def candidate_configs(config_dir: Path = CONFIG_DIR) -> Dict[str, Dict[str, object]]:
    """Load and validate the versioned C0-C4 candidate definitions."""
    result: Dict[str, Dict[str, object]] = {}
    for candidate_id in CANDIDATE_IDS:
        path = (Path(config_dir) / f"{candidate_id}.keep_dtype.cfg").resolve()
        result[candidate_id] = {
            "candidate_id": candidate_id,
            "keep_dtype_path": path,
            "keep_dtype_nodes": _read_nodes(path),
        }
    if result["C0"]["keep_dtype_nodes"]:
        raise SweepError("C0 must contain no FP32 keep-dtype nodes")
    if not set(result["C1"]["keep_dtype_nodes"]).issubset(
        set(result["C2"]["keep_dtype_nodes"])
    ):
        raise SweepError("C2 must include all C1 nodes")
    if not set(result["C1"]["keep_dtype_nodes"]).issubset(
        set(result["C3"]["keep_dtype_nodes"])
    ):
        raise SweepError("C3 must include all C1 nodes")
    if not set(result["C2"]["keep_dtype_nodes"]).issubset(
        set(result["C4"]["keep_dtype_nodes"])
    ):
        raise SweepError("C4 must include all C2 nodes")
    if not set(result["C3"]["keep_dtype_nodes"]).issubset(
        set(result["C4"]["keep_dtype_nodes"])
    ):
        raise SweepError("C4 must include all C3 nodes")
    for value in result.values():
        value["keep_dtype_sha256"] = sha256_file(value["keep_dtype_path"])
        value["keep_dtype_node_count"] = len(value["keep_dtype_nodes"])
    return result


def _manifest_payload(path: Path) -> Mapping[str, object]:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SweepError(f"cannot read COCO-CN manifest {path}: {exc}") from exc
    records = payload.get("records")
    if not isinstance(records, list) or not records:
        raise SweepError("COCO-CN manifest has no records")
    return payload


def fixture_records(
    payload: Mapping[str, object], count: int = FIXTURE_IMAGE_COUNT
) -> Tuple[List[Mapping[str, object]], List[int]]:
    records = list(payload.get("records", []))
    ordered = sorted(records, key=lambda value: str(value.get("image_id", "")))
    if len(ordered) < count:
        raise SweepError(f"manifest contains {len(ordered)} images; need {count}")
    return ordered[:count], list(RANDOM_SEEDS)


def _record_path(record: Mapping[str, object]) -> Path:
    value = record.get("path")
    if not value:
        raise SweepError(f"manifest record has no image path: {record.get('image_id')}")
    return Path(str(value)).expanduser().resolve()


def _verify_manifest_file(record: Mapping[str, object], path: Path) -> str:
    """Verify the immutable dataset hash before using a photo as evidence."""
    if not path.is_file():
        raise SweepError(f"blocked_missing_dataset: {path}")
    actual = sha256_file(path)
    expected = record.get("sha256")
    if expected and str(expected).lower() != actual.lower():
        raise SweepError(
            f"dataset SHA-256 mismatch for {record.get('image_id')}: {actual}"
        )
    return actual


def _load_image(path: Path) -> np.ndarray:
    try:
        import cv2
    except ImportError as exc:
        raise SweepError("OpenCV is required for the board-side precision sweep") from exc
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        raise SweepError(f"image cannot be decoded: {path}")
    return image


def _preprocess(record, image_bgr: np.ndarray) -> np.ndarray:
    # Use the production preprocessing implementation, without constructing an
    # ACL resource.  This keeps the ONNX references byte-compatible with the
    # runtime while allowing reference generation to happen before candidate
    # OM loading.
    backend = object.__new__(NpuEmbeddingBackend)
    backend.record = record
    return backend.preprocess_image(image_bgr)


def _onnx_session(path: Path):
    try:
        import onnxruntime as ort

        session = ort.InferenceSession(
            str(path), providers=["CPUExecutionProvider"],
        )
        return session, "onnxruntime"
    except Exception as ort_error:
        try:
            import onnx
            from onnx.reference import ReferenceEvaluator

            return ReferenceEvaluator(onnx.load(str(path))), "onnx.reference.ReferenceEvaluator"
        except Exception as reference_error:
            raise SweepError(
                "unable to execute MobileCLIP ONNX reference: "
                f"onnxruntime={ort_error}; reference={reference_error}"
            ) from reference_error


def _onnx_output(session, input_name: str, value: np.ndarray) -> np.ndarray:
    output = np.asarray(session.run(None, {input_name: value})[0], dtype=np.float32).reshape(-1)
    if not np.isfinite(output).all():
        raise SweepError("ONNX reference output contains NaN or infinity")
    return l2_normalize(output)


def write_fixture_references(
    record,
    payload: Mapping[str, object],
    reference_dir: Path,
    fixture_count: int = FIXTURE_IMAGE_COUNT,
    seeds: Iterable[int] = RANDOM_SEEDS,
) -> Dict[str, object]:
    """Write the fixed image and production-domain synthetic references.

    The seed cases are deterministic uint8 BGR images which go through the
    same preprocessing function as uploaded photographs.  Generating a raw
    standard-normal tensor here would test an out-of-domain input contract and
    could make the production baseline fail for reasons unrelated to ATC
    precision.
    """
    component = record.components[COMPONENT]
    reference_dir.mkdir(parents=True, exist_ok=True)
    # A rerun must represent exactly this fixed fixture, not stale NPZ files
    # from an interrupted sweep.  Only the managed candidate reference names
    # are removed; production references and photo assets are untouched.
    for stale in reference_dir.glob(f"{MODEL_ID}__image__*.npz"):
        stale.unlink()
    fixture, random_seeds = fixture_records(payload, fixture_count)
    session, reference_backend = _onnx_session(component.onnx_path)
    metadata = []
    for index, item in enumerate(fixture, 1):
        path = _record_path(item)
        digest = _verify_manifest_file(item, path)
        image = _load_image(path)
        value = _preprocess(record, image).astype(np.float32, copy=False)
        expected = _onnx_output(session, component.input_name, value)
        filename = f"{MODEL_ID}__image__sample-{index:04d}.npz"
        np.savez_compressed(reference_dir / filename, input=value, output=expected)
        metadata.append(
            {
                "kind": "image",
                "image_id": item.get("image_id"),
                "path": str(path),
                "sha256": digest,
                "reference": filename,
            }
        )
    random_metadata = []
    for seed in random_seeds:
        rng = np.random.default_rng(int(seed))
        # Keep the synthetic case in the camera/photo domain while retaining
        # deterministic pressure coverage.  The dimensions match the fixed
        # MobileCLIP input contract; preprocessing performs the final resize,
        # channel conversion, scaling, and dtype conversion.
        height = int(component.input_shape[-2])
        width = int(component.input_shape[-1])
        synthetic_bgr = rng.integers(
            0, 256, size=(height, width, 3), dtype=np.uint8
        )
        value = _preprocess(record, synthetic_bgr).astype(
            np.dtype(component.input_dtype), copy=False
        )
        expected = _onnx_output(session, component.input_name, value)
        filename = f"{MODEL_ID}__image__seed-{int(seed)}.npz"
        np.savez_compressed(reference_dir / filename, input=value, output=expected)
        random_metadata.append(
            {
                "kind": "synthetic_image_seed",
                "seed": int(seed),
                "reference": filename,
                "distribution": "uint8_uniform_bgr_then_production_preprocess",
                "preprocess": "NpuEmbeddingBackend.preprocess_image",
                "input_min": float(np.min(value)),
                "input_max": float(np.max(value)),
                "input_mean": float(np.mean(value)),
                "input_std": float(np.std(value)),
                "outside_production_range_fraction": float(
                    np.mean((value < 0.0) | (value > 1.0))
                ),
            }
        )
    return {
        "count": len(metadata) + len(random_metadata),
        "image_count": len(metadata),
        "random_seeds": [int(seed) for seed in random_seeds],
        "random_distribution": "uint8_uniform_bgr_then_production_preprocess",
        "random_preprocess": "NpuEmbeddingBackend.preprocess_image",
        "reference_backend": reference_backend,
        "images": metadata,
        "random": random_metadata,
    }


def _candidate_record():
    for record in load_candidates():
        if record.model_id == MODEL_ID:
            return record
    raise SweepError(f"candidate manifest is missing {MODEL_ID}")


def _active_cann_version() -> str:
    """Read the toolkit version without initializing ACL or an NPU device."""
    try:
        # ``prepare_models`` already centralizes the version-file lookup.  The
        # helper is read-only and does not import/initialize an ACL resource.
        import prepare_models

        return str(prepare_models._detect_cann_version())
    except Exception as exc:
        # A sweep report should remain useful even when a partially configured
        # shell cannot expose the toolkit version.  Conversion/ACL gates still
        # fail independently; this value is explicitly marked unknown.
        return f"unknown ({type(exc).__name__})"


def _production_record(registry_path: Path):
    registry = ModelRegistry(path=registry_path, require_artifacts=True)
    return registry.get(MODEL_ID)


def _onnx_identity(candidate_record, production_record) -> Dict[str, object]:
    """Prove that candidates and production use the exact same ONNX bytes."""
    candidate = candidate_record.components[COMPONENT]
    production = production_record.components[COMPONENT]
    candidate_path = candidate.onnx_path.resolve()
    production_path = production.onnx_path.resolve()
    if candidate_path != production_path:
        raise SweepError(
            "candidate/production ONNX path mismatch: "
            f"candidate={candidate_path}; production={production_path}"
        )
    if not candidate_path.is_file():
        raise SweepError(f"missing canonical MobileCLIP ONNX: {candidate_path}")
    actual = sha256_file(candidate_path).lower()
    declared_production = str(production.onnx_sha256 or "").lower()
    if not declared_production:
        raise SweepError("production registry has no declared MobileCLIP ONNX SHA-256")
    if actual != declared_production:
        raise SweepError(
            "canonical MobileCLIP ONNX SHA-256 mismatch: "
            f"declared={declared_production}; actual={actual}"
        )
    declared_candidate = str(candidate.onnx_sha256 or "").lower() or None
    if declared_candidate and actual != declared_candidate:
        raise SweepError(
            "candidate manifest MobileCLIP ONNX SHA-256 mismatch: "
            f"declared={declared_candidate}; actual={actual}"
        )
    return {
        "onnx_path": str(candidate_path),
        "onnx_sha256": actual,
        "production_declared_onnx_sha256": declared_production,
        "candidate_declared_onnx_sha256": declared_candidate,
        "same_path": True,
        "same_bytes": True,
    }


def _assert_isolated(output_dir: Path, production_om: Path) -> None:
    output_dir = output_dir.resolve()
    production_om = production_om.resolve()
    production_dir = production_om.parent
    if output_dir == production_om:
        raise SweepError(f"candidate output resolves to production OM: {output_dir}")
    try:
        # Only the canonical ``models/om`` directory is protected.  A unit
        # test or an isolated release may place a synthetic production OM in a
        # generic temporary directory; its sibling candidate directories are
        # safe and must not be rejected merely because they share a parent.
        output_dir.relative_to(production_dir)
        # In production the parent is specifically ``models/om``.  For a
        # synthetic OM such as ``<tmp>/production.om``, sibling candidate
        # folders under ``<tmp>`` are valid and are covered by the exact-path
        # check below instead.
        if output_dir == production_dir or (
            production_dir.name == "om" and production_dir.parent.name == "models"
        ):
            raise SweepError(f"candidate output is inside production OM directory: {output_dir}")
    except ValueError:
        pass
    # Never let a caller redirect candidate artifacts into the active album's
    # registry, database/photo, or production-report trees.  Synthetic unit
    # tests may use a temporary production OM, so only protect these paths when
    # they belong to this case root.
    protected = (
        (ROOT / "models" / "registry.json").resolve(),
        (ROOT / "data").resolve(),
        (ROOT / "photos").resolve(),
        (ROOT / "reports" / "model_pipeline").resolve(),
    )
    for path in protected:
        try:
            output_dir.relative_to(path)
        except ValueError:
            continue
        raise SweepError(f"candidate output overlaps protected production path: {output_dir}")


def _conversion_command(
    candidate_id: str,
    candidate_dir: Path,
    config: Mapping[str, object],
    soc_version: str,
    python_executable: str,
) -> List[str]:
    command = [
        python_executable,
        str(ROOT / "prepare_models.py"),
        "convert",
        "--model",
        MODEL_ID,
        "--component",
        COMPONENT,
        "--soc-version",
        soc_version,
        "--enable-graph-parallel",
        "0",
        "--precision-mode",
        "allow_fp32_to_fp16",
        "--op-select-implmode",
        "high_precision_for_all",
        "--output-om-dir",
        str((candidate_dir / "om").resolve()),
        "--report-dir",
        str(candidate_dir.resolve()),
        "--allow-low-memory-single-thread",
    ]
    if candidate_id == "C0":
        command.append("--without-keep-dtype")
    else:
        command.extend(["--keep-dtype-file", str(config["keep_dtype_path"])])
    return command


def run_conversion(
    candidate_id: str,
    candidate_dir: Path,
    config: Mapping[str, object],
    production_om: Path,
    soc_version: str = DEFAULT_SOC,
    python_executable: str = sys.executable,
    dry_run: bool = False,
) -> Dict[str, object]:
    """Run one serial ATC conversion into a candidate-only directory."""
    candidate_dir = candidate_dir.resolve()
    _assert_isolated(candidate_dir, production_om)
    candidate_dir.mkdir(parents=True, exist_ok=True)
    om_path = candidate_dir / "om" / production_om.name
    command = _conversion_command(candidate_id, candidate_dir, config, soc_version, python_executable)
    # Keep a candidate's exception file beside its immutable report.  A
    # production registry must never depend on the source checkout or an old
    # release that may later be garbage-collected.
    persisted_keep_dtype = None
    if candidate_id != "C0" and not dry_run:
        source_keep_dtype = Path(str(config["keep_dtype_path"])).resolve()
        persisted_keep_dtype = candidate_dir / "keep_dtype.cfg"
        shutil.copy2(source_keep_dtype, persisted_keep_dtype)
        if sha256_file(persisted_keep_dtype) != str(config["keep_dtype_sha256"]):
            raise SweepError(f"keep-dtype copy hash mismatch for {candidate_id}")
    result: Dict[str, object] = {
        "candidate_id": candidate_id,
        "model_id": MODEL_ID,
        "component": COMPONENT,
        "soc_version": soc_version,
        "om_path": str(om_path),
        "keep_dtype_path": str(persisted_keep_dtype or config["keep_dtype_path"]),
        "keep_dtype_sha256": config["keep_dtype_sha256"],
        # Keep both spellings for consumers written against the sweep plan
        # (``hash``) and the repository's artifact conventions (``sha256``).
        "keep_dtype_hash": config["keep_dtype_sha256"],
        "keep_dtype_nodes": list(config["keep_dtype_nodes"]),
        "keep_dtype_node_count": config["keep_dtype_node_count"],
        "command": command,
        "atc_report": str((candidate_dir / "atc_conversion.json").resolve()),
        "conversion_log": str((candidate_dir / "conversion_stdout.log").resolve()),
        "cache_policy": "--op_compiler_cache_mode=disable",
        "single_thread": True,
        "serial_environment": dict(SERIAL_ENV),
        "status": "pending",
        "om_sha256": None,
        "om_size": None,
        "numerical": {"status": "not_run", "passed": False},
        "retrieval": {"status": "not_run", "passed": False},
        "performance": {"status": "not_run", "passed": False},
        "passed": False,
    }
    if dry_run:
        result["status"] = "dry_run"
        return result
    if om_path.exists():
        result["status"] = "failed"
        result["error"] = f"refusing to reuse existing candidate OM: {om_path}"
        return result
    completed = subprocess.run(
        command,
        cwd=str(ROOT),
        env=serial_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    (candidate_dir / "conversion_stdout.log").write_text(
        f"command: {' '.join(command)}\nexit_code: {completed.returncode}\n\n{completed.stdout or ''}",
        encoding="utf-8",
    )
    result["exit_code"] = int(completed.returncode)
    if completed.returncode != 0:
        # ATC may leave a partial file after a non-zero exit.  Remove only the
        # exact candidate destination; production assets are never touched.
        if om_path.is_file():
            om_path.unlink()
            result["partial_om_removed"] = True
        result["status"] = "failed"
        result["error"] = "ATC conversion failed; see conversion_stdout.log"
        return result
    if not om_path.is_file():
        result["status"] = "failed"
        result["error"] = f"ATC returned success but OM is missing: {om_path}"
        return result
    result.update(
        {
            "status": "passed",
            "om_sha256": sha256_file(om_path),
            "om_size": om_path.stat().st_size,
        }
    )
    return result


def run_numerical_gate(
    candidate_om: Path,
    report_path: Path,
    reference_dir: Path,
    record=None,
    expected_count: Optional[int] = None,
    worker: bool = False,
    worker_python: Optional[str] = None,
) -> Dict[str, object]:
    """Use prepare_models' strict ACL candidate helper without registry writes."""
    record = record or _candidate_record()
    if worker:
        result = _invoke_gate_worker(
            "numerical",
            report_path,
            candidate_om=candidate_om,
            reference_dir=reference_dir,
            python_executable=worker_python,
        )
    else:
        import prepare_models

        try:
            result = prepare_models.validate_candidate(
                [record],
                component_kind=COMPONENT,
                om_path=str(candidate_om),
                report_path=str(report_path),
                reference_dir=str(reference_dir),
            )
        except Exception as exc:
            if report_path.is_file():
                try:
                    result = json.loads(report_path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    result = {"passed": False}
            else:
                result = {"passed": False}
            result["error"] = str(exc)
    # ``prepare_models.validate_candidate`` intentionally reports the raw
    # reference list (it is also used by the ordinary one-reference check).
    # Derive the count before applying the sweep's fixed 32+4 fixture gate;
    # checking a missing field first would reject every otherwise valid run.
    expected_count = expected_count or (FIXTURE_IMAGE_COUNT + len(RANDOM_SEEDS))
    result["reference_count"] = len(result.get("references", []))
    result["expected_reference_count"] = expected_count
    result["passed"] = bool(
        result.get("passed") and result["reference_count"] == expected_count
    )
    result["status"] = "passed" if result["passed"] else "failed"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def _registry_payload(registry_path: Path, candidate_om: Optional[Path] = None) -> dict:
    payload = json.loads(Path(registry_path).read_text(encoding="utf-8"))
    payload = copy.deepcopy(payload)
    target = next((value for value in payload.get("models", []) if value.get("model_id") == MODEL_ID), None)
    if target is None:
        raise SweepError(f"production registry is missing {MODEL_ID}")
    target["status"] = "admitted"
    component = target.setdefault("components", {}).setdefault(COMPONENT, {})
    if candidate_om is not None:
        component["om"] = str(Path(candidate_om).resolve())
        component["om_sha256"] = sha256_file(Path(candidate_om))
    return payload


def _retrieval_once(
    manifest_payload: Mapping[str, object],
    registry_path: Path,
    candidate_om: Optional[Path],
    work_dir: Path,
) -> Dict[str, object]:
    records = list(manifest_payload.get("records", []))
    if not records:
        raise SweepError("manifest has no retrieval records")
    declared_limit = int(manifest_payload.get("limit", len(records)))
    if declared_limit != FIXED_GALLERY_COUNT or len(records) != FIXED_GALLERY_COUNT:
        raise SweepError(
            f"retrieval gate requires the fixed {FIXED_GALLERY_COUNT}-image gallery; "
            f"manifest has limit={declared_limit}, records={len(records)}"
        )
    paths = [_record_path(value) for value in records]
    for record, path in zip(records, paths):
        _verify_manifest_file(record, path)
    work_dir.parent.mkdir(parents=True, exist_ok=True)
    # Keep all SQLite/FAISS artifacts below a TemporaryDirectory.  The report
    # retains metrics only; no derived image vectors survive the gate.
    with tempfile.TemporaryDirectory(prefix="mobileclip-retrieval-", dir=str(work_dir.parent)) as temporary:
        temporary_root = Path(temporary)
        temporary_registry = temporary_root / "registry.json"
        temporary_registry.write_text(
            json.dumps(_registry_payload(registry_path, candidate_om), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        registry = ModelRegistry(path=temporary_registry, require_artifacts=True)
        manager = None
        index = None
        try:
            manager = __import__("embedding_backend").ModelManager(registry=registry)
            roots = sorted({str(path.parent) for path in paths})
            index = AlbumIndex(
                manager=manager,
                db_path=temporary_root / "album.sqlite3",
                index_dir=temporary_root / "indexes",
                photo_roots=roots,
                allow_numpy_fallback=False,
            )
            # AlbumIndex performs the legacy metadata import on first open.
            # A candidate run must contain exactly the fixed manifest, so
            # discard any rows imported from the controller's legacy JSON
            # before inserting the 500 immutable dataset paths.  This is a
            # temporary database and never touches production metadata.
            with index._connection:
                index._connection.execute("DELETE FROM photos")
            index._indexes.clear()
            summary = index.index_paths(paths, model_ids=[MODEL_ID])
            stats = index.stats()
            if stats["available_photos"] != len(paths) or stats["embeddings_by_model"].get(MODEL_ID) != len(paths):
                raise SweepError(
                    f"temporary index incomplete: {summary.to_dict()} / {stats}"
                )
            by_path = {path: str(value.get("image_id")) for path, value in zip(paths, records)}
            query_rows = []
            query_defs = list((manifest_payload.get("queries") or {}).get("en", []))
            if not query_defs:
                raise SweepError("manifest has no English retrieval queries")
            if len(query_defs) != 20:
                raise SweepError(
                    f"fixed COCO-CN protocol requires 20 English queries, got {len(query_defs)}"
                )
            for query_def in query_defs:
                query = str(query_def.get("query", "")).strip()
                relevant = {str(value) for value in query_def.get("relevant_image_ids", [])}
                if not query or not relevant:
                    raise SweepError("retrieval query must have text and relevant_image_ids")
                started = time.perf_counter()
                results = index.search_text(query, MODEL_ID, 5)
                latency_ms = (time.perf_counter() - started) * 1000.0
                ranked = [by_path.get(Path(result.filepath).resolve()) for result in results]
                ranked = [value for value in ranked if value is not None]
                query_rows.append(
                    {
                        "query": query,
                        "relevant_count": len(relevant),
                        "result_image_ids": ranked,
                        "latency_ms": latency_ms,
                        "recall": {
                            str(k): float(bool(set(ranked[:k]) & relevant)) for k in (1, 3, 5)
                        },
                    }
                )
            metrics = {
                f"recall_at_{k}": sum(row["recall"][str(k)] for row in query_rows) / len(query_rows)
                for k in (1, 3, 5)
            }
            latencies = [float(row["latency_ms"]) for row in query_rows]
            return {
                "status": "passed",
                "query_count": len(query_rows),
                "metrics": metrics,
                "latency_ms": {"p50": percentile(latencies, 50), "p95": percentile(latencies, 95)},
                "queries": query_rows,
                "index_summary": summary.to_dict(),
            }
        finally:
            if index is not None:
                index.close()
            if manager is not None:
                manager.release()


def run_retrieval_gate(
    manifest_payload: Mapping[str, object],
    registry_path: Path,
    candidate_om: Optional[Path],
    work_dir: Path,
    baseline: Optional[Mapping[str, object]] = None,
    worker: bool = False,
    manifest_path: Optional[Path] = None,
    worker_python: Optional[str] = None,
) -> Dict[str, object]:
    try:
        if worker:
            if manifest_path is None:
                raise SweepError("manifest_path is required for a retrieval worker")
            current = _invoke_gate_worker(
                "retrieval",
                work_dir.with_name("retrieval.json"),
                registry_path=registry_path,
                candidate_om=candidate_om,
                manifest_path=manifest_path,
                work_dir=work_dir,
                python_executable=worker_python,
            )
        else:
            current = _retrieval_once(manifest_payload, registry_path, candidate_om, work_dir)
        if baseline is None:
            current["passed"] = True
            return current
        baseline_metrics = baseline.get("metrics", {})
        current_metrics = current.get("metrics", {})
        comparisons = {
            key: {
                "candidate": current_metrics.get(key),
                "baseline": baseline_metrics.get(key),
                "passed": current_metrics.get(key, -1) >= baseline_metrics.get(key, 0),
            }
            for key in ("recall_at_1", "recall_at_3", "recall_at_5")
        }
        current["comparisons"] = comparisons
        current["passed"] = bool(all(value["passed"] for value in comparisons.values()))
        current["status"] = "passed" if current["passed"] else "failed"
        return current
    except Exception as exc:
        return {"status": "blocked_missing_dataset" if "blocked_missing_dataset" in str(exc) else "failed", "passed": False, "error": str(exc)}
    finally:
        # ``work_dir`` is a report label, not a storage location.  Remove only
        # an empty directory left by an interrupted/older implementation; the
        # TemporaryDirectory used by the gate owns and removes all derived
        # SQLite/FAISS files itself.
        try:
            work_dir.rmdir()
        except OSError:
            pass


def _performance_once(
    record,
    registry_path: Path,
    candidate_om: Optional[Path],
    image_path: Path,
    warmup: int,
    loops: int,
    repeats: int,
) -> Dict[str, object]:
    with tempfile.TemporaryDirectory(prefix="mobileclip-perf-") as temporary:
        temporary_registry = Path(temporary) / "registry.json"
        temporary_registry.write_text(
            json.dumps(_registry_payload(registry_path, candidate_om), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        registry = ModelRegistry(path=temporary_registry, require_artifacts=True)
        from embedding_backend import ModelManager

        manager = ModelManager(registry=registry)
        try:
            image = _load_image(image_path)
            for _ in range(warmup):
                manager.encode_image(MODEL_ID, image)
            samples = []
            repeat_metrics = []
            for _ in range(repeats):
                run = []
                for _ in range(loops):
                    started = time.perf_counter()
                    manager.encode_image(MODEL_ID, image)
                    run.append((time.perf_counter() - started) * 1000.0)
                samples.extend(run)
                repeat_metrics.append(
                    {
                        "p50_ms": percentile(run, 50),
                        "p95_ms": percentile(run, 95),
                        "average_ms": statistics.mean(run),
                    }
                )
            return {
                "status": "passed",
                "warmup": warmup,
                "loops": loops,
                "repeats": repeats,
                "samples": len(samples),
                "p50_ms": percentile(samples, 50),
                "p95_ms": percentile(samples, 95),
                "average_ms": statistics.mean(samples),
                "repeat_metrics": repeat_metrics,
            }
        finally:
            manager.release()


def run_performance_gate(
    record,
    registry_path: Path,
    candidate_om: Optional[Path],
    image_path: Path,
    baseline: Optional[Mapping[str, object]],
    warmup: int = 20,
    loops: int = 100,
    repeats: int = 3,
    worker: bool = False,
    worker_python: Optional[str] = None,
    persist_dir: Optional[Path] = None,
) -> Dict[str, object]:
    try:
        if worker:
            report_path = Path(tempfile.gettempdir()) / (
                f"case7-mobileclip-performance-{os.getpid()}-{int(time.time() * 1000)}.json"
            )
            try:
                current = _invoke_gate_worker(
                    "performance",
                    report_path,
                    registry_path=registry_path,
                    candidate_om=candidate_om,
                    image_path=image_path,
                    warmup=warmup,
                    loops=loops,
                    repeats=repeats,
                    python_executable=worker_python,
                )
                if persist_dir is not None:
                    source_log = Path(str(current.get("worker_log", "")))
                    if not source_log.is_file():
                        raise SweepError(
                            f"performance worker log is missing: {source_log}"
                        )
                    destination_log = Path(persist_dir).resolve() / "performance.worker.log"
                    destination_log.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source_log, destination_log)
                    current["worker_log"] = str(destination_log)
                    current["worker_log_sha256"] = sha256_file(destination_log)
            finally:
                report_path.unlink(missing_ok=True)
                report_path.with_name(report_path.name + ".worker.log").unlink(missing_ok=True)
        else:
            current = _performance_once(record, registry_path, candidate_om, image_path, warmup, loops, repeats)
        if baseline is None:
            current["passed"] = True
            return current
        baseline_p50 = float(baseline.get("p50_ms") or 0.0)
        baseline_p95 = float(baseline.get("p95_ms") or 0.0)
        current_p50 = float(current.get("p50_ms") or float("inf"))
        current_p95 = float(current.get("p95_ms") or float("inf"))
        current["thresholds"] = {
            "p50_max_ms": baseline_p50 * 0.90,
            "p95_max_ms": baseline_p95,
            "baseline_p50_ms": baseline_p50,
            "baseline_p95_ms": baseline_p95,
        }
        current["passed"] = bool(current_p50 <= baseline_p50 * 0.90 and current_p95 <= baseline_p95)
        current["status"] = "passed" if current["passed"] else "failed"
        return current
    except Exception as exc:
        return {"status": "failed", "passed": False, "error": str(exc)}


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    return value if isinstance(value, dict) else {}


def _invoke_gate_worker(
    stage: str,
    report_path: Path,
    *,
    registry_path: Optional[Path] = None,
    candidate_om: Optional[Path] = None,
    reference_dir: Optional[Path] = None,
    manifest_path: Optional[Path] = None,
    work_dir: Optional[Path] = None,
    image_path: Optional[Path] = None,
    warmup: int = 20,
    loops: int = 100,
    repeats: int = 3,
    python_executable: Optional[str] = None,
) -> Dict[str, object]:
    """Run one ACL-owning gate in a fresh serial process.

    CANN releases on the 310B can reject ``acl.init`` after a process has
    called ``acl.finalize`` and ``rt.reset_device``.  Keeping each gate in its
    own process avoids that runtime-global state leak while retaining strictly
    serial execution and a complete per-stage log.
    """
    report_path = Path(report_path).resolve()
    command = [
        str(python_executable or sys.executable),
        str(Path(__file__).resolve()),
        "--worker-stage",
        stage,
        "--worker-report",
        str(report_path),
    ]
    if registry_path is not None:
        command.extend(["--worker-registry", str(Path(registry_path).resolve())])
    if candidate_om is not None:
        command.extend(["--worker-candidate-om", str(Path(candidate_om).resolve())])
    if reference_dir is not None:
        command.extend(["--worker-reference-dir", str(Path(reference_dir).resolve())])
    if manifest_path is not None:
        command.extend(["--worker-manifest", str(Path(manifest_path).resolve())])
    if work_dir is not None:
        command.extend(["--worker-work-dir", str(Path(work_dir).resolve())])
    if image_path is not None:
        command.extend(["--worker-image", str(Path(image_path).resolve())])
    command.extend(
        [
            "--worker-warmup",
            str(int(warmup)),
            "--worker-loops",
            str(int(loops)),
            "--worker-repeats",
            str(int(repeats)),
        ]
    )
    log_path = report_path.with_name(report_path.name + ".worker.log")
    completed = subprocess.run(
        command,
        cwd=str(ROOT),
        env=serial_environment(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        f"command: {' '.join(command)}\nexit_code: {completed.returncode}\n\n{completed.stdout or ''}",
        encoding="utf-8",
    )
    result = _load_json(report_path)
    if not result:
        result = {
            "status": "failed",
            "passed": False,
            "error": f"gate worker produced no report (exit {completed.returncode})",
        }
    result["worker_exit_code"] = int(completed.returncode)
    result["worker_log"] = str(log_path)
    return result


def _worker_main(args: argparse.Namespace) -> int:
    """Execute a single ACL/retrieval/performance stage and write JSON."""
    report_path = Path(args.worker_report).resolve()
    candidate_om = Path(args.worker_candidate_om).resolve() if args.worker_candidate_om else None
    registry_path = Path(args.worker_registry).resolve()
    try:
        if args.worker_stage == "numerical":
            import prepare_models

            record = _candidate_record()
            try:
                result = prepare_models.validate_candidate(
                    [record],
                    component_kind=COMPONENT,
                    om_path=str(candidate_om or record.components[COMPONENT].om_path),
                    report_path=str(report_path),
                    reference_dir=str(Path(args.worker_reference_dir).resolve()),
                )
            except Exception as exc:
                result = _load_json(report_path)
                result.setdefault("passed", False)
                result["error"] = str(exc)
        elif args.worker_stage == "retrieval":
            payload = _manifest_payload(Path(args.worker_manifest).resolve())
            result = _retrieval_once(
                payload,
                registry_path,
                candidate_om,
                Path(args.worker_work_dir).resolve(),
            )
            _write_json(report_path, result)
        elif args.worker_stage == "performance":
            result = _performance_once(
                _candidate_record(),
                registry_path,
                candidate_om,
                Path(args.worker_image).resolve(),
                args.worker_warmup,
                args.worker_loops,
                args.worker_repeats,
            )
            _write_json(report_path, result)
        else:
            raise SweepError(f"unknown gate worker stage: {args.worker_stage}")
    except Exception as exc:
        result = _load_json(report_path)
        result.update({"status": "failed", "passed": False, "error": str(exc)})
        _write_json(report_path, result)
        return 1
    return 0


def _annotate_candidate_report(
    candidate_dir: Path,
    candidate_id: str,
    onnx_evidence: Mapping[str, object],
    cann_version: Optional[str],
) -> None:
    """Add provenance to an isolated ATC report when one was produced."""
    report_path = Path(candidate_dir) / "atc_conversion.json"
    if not report_path.is_file():
        return
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Preserve the raw compiler evidence; the sweep summary still carries
        # the provenance when a compiler aborts before writing valid JSON.
        return
    report["sweep_candidate_id"] = str(candidate_id)
    report["onnx_identity"] = dict(onnx_evidence)
    report["cann_version"] = cann_version
    report["cache_policy"] = "--op_compiler_cache_mode=disable"
    report["single_thread"] = True
    report["serial_environment"] = dict(SERIAL_ENV)
    _write_json(report_path, report)


def choose_candidate(candidates: Mapping[str, Mapping[str, object]]) -> Optional[str]:
    passed = [value for value in candidates.values() if value.get("passed")]
    if not passed:
        return None
    # C2/C3 are competing minimal exception sets.  Prefer fewer FP32 nodes;
    # use measured P50 only as the deterministic tie-breaker.
    def key(value):
        node_count = int(value.get("keep_dtype_node_count", 10**9))
        p50 = float((value.get("performance") or {}).get("p50_ms") or float("inf"))
        return node_count, p50, str(value.get("candidate_id"))

    return str(min(passed, key=key)["candidate_id"])


def _candidate_gate_passed(value: Mapping[str, object]) -> bool:
    return bool(
        value.get("status") == "passed"
        and (value.get("numerical") or {}).get("passed")
        and (value.get("retrieval") or {}).get("passed")
        and (value.get("performance") or {}).get("passed")
    )


def run(args: argparse.Namespace) -> dict:
    manifest_path = Path(args.manifest).resolve()
    output_dir = Path(args.output_dir).resolve()
    registry_path = Path(args.registry).resolve()
    payload = _manifest_payload(manifest_path)
    candidate_record = _candidate_record()
    production_record = _production_record(registry_path)
    production_om = production_record.components[COMPONENT].om_path.resolve()
    # Resolve and hash the ONNX once before any board work.  Both manifests
    # must point at the same immutable bytes; candidates are never allowed to
    # silently sweep a different export than the production baseline.
    onnx_evidence = _onnx_identity(candidate_record, production_record)
    cann_version = None if args.dry_run else _active_cann_version()
    _assert_isolated(output_dir, production_om)
    configs = candidate_configs()
    output_dir.mkdir(parents=True, exist_ok=True)

    # Dry-run is intentionally board-independent: it validates candidate
    # definitions and prints the exact serial ATC commands without requiring
    # the dataset, ACL, or an installed ``atc`` binary.
    if args.dry_run:
        dry_summary = {
            "schema_version": 1,
            "evidence_schema_version": 2,
            "generated_at": time.time(),
            "status": "dry_run",
            "passed": False,
            "selected_candidate": None,
            "model_id": MODEL_ID,
            "component": COMPONENT,
            "soc_version": args.soc_version,
            "onnx": onnx_evidence,
            "cann_version": cann_version,
            "manifest": str(manifest_path),
            "manifest_sha256": sha256_file(manifest_path) if manifest_path.is_file() else None,
            "production_baseline": {
                **onnx_evidence,
                "cann_version": cann_version,
                "om_path": str(production_om),
                "om_sha256": sha256_file(production_om) if production_om.is_file() else None,
                "numerical": {"status": "not_run", "passed": False},
                "retrieval": {"status": "not_run", "passed": False},
                "performance": {"status": "not_run", "passed": False},
            },
            "candidates": {},
        }
        for candidate_id in CANDIDATE_IDS:
            if args.candidate and candidate_id not in args.candidate:
                continue
            value = run_conversion(
                candidate_id,
                output_dir / candidate_id,
                configs[candidate_id],
                production_om,
                soc_version=args.soc_version,
                python_executable=args.python_executable,
                dry_run=True,
            )
            value.update(onnx_evidence)
            value["cann_version"] = cann_version
            dry_summary["candidates"][candidate_id] = value
        _write_json(output_dir / "summary.json", dry_summary)
        return dry_summary

    fixture_dir = output_dir / "references"
    fixture_report = output_dir / "fixture.json"
    try:
        fixture = write_fixture_references(candidate_record, payload, fixture_dir, args.fixture_count)
        _write_json(fixture_report, fixture)
    except Exception as exc:
        blocked = "blocked_missing_dataset" if "blocked_missing_dataset" in str(exc) else "failed"
        summary = {
            "schema_version": 1,
            "status": blocked,
            "passed": False,
            "selected_candidate": None,
            "model_id": MODEL_ID,
            "component": COMPONENT,
            "soc_version": args.soc_version,
            "onnx": onnx_evidence,
            "cann_version": cann_version,
            "manifest": str(manifest_path),
            "manifest_sha256": sha256_file(manifest_path),
            "error": str(exc),
            "candidates": {},
            "production_baseline": {
                **onnx_evidence,
                "cann_version": cann_version,
                "om_path": str(production_om),
                "om_sha256": sha256_file(production_om) if production_om.is_file() else None,
                "numerical": {"status": "not_run", "passed": False},
                "retrieval": {"status": "not_run", "passed": False},
                "performance": {"status": "not_run", "passed": False},
            },
        }
        _write_json(output_dir / "summary.json", summary)
        return summary

    baseline_dir = output_dir / "production_baseline"
    baseline_numerical = run_numerical_gate(
        production_om,
        baseline_dir / "acl_numerical_validation.json",
        fixture_dir,
        record=candidate_record,
        expected_count=args.fixture_count + len(RANDOM_SEEDS),
        worker=True,
        worker_python=args.python_executable,
    )
    if args.skip_recall:
        baseline_retrieval = {"status": "skipped", "passed": False}
    else:
        baseline_retrieval = run_retrieval_gate(
            payload,
            registry_path,
            None,
            baseline_dir / "retrieval-work",
            baseline=None,
            worker=True,
            manifest_path=manifest_path,
            worker_python=args.python_executable,
        )
    _write_json(baseline_dir / "recall.json", baseline_retrieval)
    baseline_performance = {"status": "skipped", "passed": False}
    if baseline_retrieval.get("passed") and not args.skip_performance:
        fixture_items, _ = fixture_records(payload, args.fixture_count)
        baseline_performance = run_performance_gate(
            candidate_record,
            registry_path,
            None,
            _record_path(fixture_items[0]),
            baseline=None,
            warmup=args.warmup,
            loops=args.loops,
            repeats=args.repeats,
            worker=True,
            worker_python=args.python_executable,
            persist_dir=baseline_dir,
        )
    elif args.skip_performance:
        baseline_performance = {"status": "skipped", "passed": True}
    _write_json(baseline_dir / "performance.json", baseline_performance)

    baseline = {
        "om_path": str(production_om),
        "om_sha256": sha256_file(production_om),
        **onnx_evidence,
        "cann_version": cann_version,
        "numerical": baseline_numerical,
        "retrieval": baseline_retrieval,
        "performance": baseline_performance,
    }
    _write_json(baseline_dir / "baseline.json", baseline)

    summary = {
        "schema_version": 1,
        "evidence_schema_version": 2,
        "generated_at": time.time(),
        "status": "running",
        "passed": False,
        "selected_candidate": None,
        "model_id": MODEL_ID,
        "component": COMPONENT,
        "soc_version": args.soc_version,
        "onnx": onnx_evidence,
        "cann_version": cann_version,
        "manifest": str(manifest_path),
        "manifest_sha256": sha256_file(manifest_path),
        "fixture": {"path": str(fixture_report), **fixture},
        "protocol": {
            "single_thread": True,
            "cache_disabled": True,
            "serial_environment": dict(SERIAL_ENV),
            "numerical_threshold": NUMERICAL_THRESHOLD,
            "performance_warmup": args.warmup,
            "performance_loops": args.loops,
            "performance_repeats": args.repeats,
            "random_seeds": list(RANDOM_SEEDS),
        },
        "production_baseline": baseline,
        "candidates": {},
    }

    if not baseline_numerical.get("passed"):
        # A pressure input may expose a known production-OM numerical gap.
        # Keep that failure as explicit baseline evidence, but do not prevent
        # candidate diagnosis: each candidate is independently required to
        # pass the full ONNX cosine gate before it can be selected.
        summary["baseline_numerical_warning"] = "production baseline did not pass all fixed references"
    if not args.skip_recall and not baseline_retrieval.get("passed"):
        summary["status"] = "blocked_baseline_retrieval"
        _write_json(output_dir / "summary.json", summary)
        return summary
    if not args.skip_performance and not baseline_performance.get("passed"):
        summary["status"] = "blocked_baseline_performance"
        _write_json(output_dir / "summary.json", summary)
        return summary

    executed = []
    for candidate_id in CANDIDATE_IDS:
        if args.candidate and candidate_id not in args.candidate:
            continue
        candidate_dir = output_dir / candidate_id
        value = run_conversion(
            candidate_id,
            candidate_dir,
            configs[candidate_id],
            production_om,
            soc_version=args.soc_version,
            python_executable=args.python_executable,
            dry_run=args.dry_run,
        )
        value["candidate_id"] = candidate_id
        value.update(onnx_evidence)
        value["cann_version"] = cann_version
        _annotate_candidate_report(candidate_dir, candidate_id, onnx_evidence, cann_version)
        if value.get("status") == "passed" and not args.dry_run:
            value["numerical"] = run_numerical_gate(
                Path(value["om_path"]),
                candidate_dir / "acl_numerical_validation.json",
                fixture_dir,
                record=candidate_record,
                expected_count=args.fixture_count + len(RANDOM_SEEDS),
                worker=True,
                worker_python=args.python_executable,
            )
            if value["numerical"].get("passed") and not args.skip_recall:
                value["retrieval"] = run_retrieval_gate(
                    payload,
                    registry_path,
                    Path(value["om_path"]),
                    candidate_dir / "retrieval-work",
                    baseline=baseline_retrieval,
                    worker=True,
                    manifest_path=manifest_path,
                    worker_python=args.python_executable,
                )
                _write_json(candidate_dir / "recall.json", value["retrieval"])
            elif args.skip_recall:
                value["retrieval"] = {"status": "skipped", "passed": False}
                _write_json(candidate_dir / "recall.json", value["retrieval"])
            else:
                value["retrieval"] = {"status": "blocked_numerical", "passed": False}
                _write_json(candidate_dir / "recall.json", value["retrieval"])
            if value["retrieval"].get("passed") and not args.skip_performance:
                fixture_items, _ = fixture_records(payload, args.fixture_count)
                value["performance"] = run_performance_gate(
                    candidate_record,
                    registry_path,
                    Path(value["om_path"]),
                    _record_path(fixture_items[0]),
                    baseline=baseline_performance,
                    warmup=args.warmup,
                    loops=args.loops,
                    repeats=args.repeats,
                    worker=True,
                    worker_python=args.python_executable,
                    persist_dir=candidate_dir,
                )
                _write_json(candidate_dir / "performance.json", value["performance"])
            elif args.skip_performance:
                value["performance"] = {"status": "skipped", "passed": False}
                _write_json(candidate_dir / "performance.json", value["performance"])
            else:
                value["performance"] = {"status": "blocked_retrieval", "passed": False}
                _write_json(candidate_dir / "performance.json", value["performance"])
            value["passed"] = _candidate_gate_passed(value)
        else:
            value["numerical"] = {"status": "not_run", "passed": False}
            value["retrieval"] = {"status": "not_run", "passed": False}
            value["performance"] = {"status": "not_run", "passed": False}
            value["passed"] = False
        summary["candidates"][candidate_id] = value
        executed.append(candidate_id)
        _write_json(output_dir / "summary.json", summary)
        if value.get("passed") and not args.force_all and candidate_id in {"C1", "C3", "C4"}:
            # C2 and C3 are competing minimal exception sets; always run both
            # before selecting between them.
            break
        # C4 is only needed when neither competing C2/C3 candidate passed.
        if candidate_id == "C3" and not args.force_all:
            c2 = summary["candidates"].get("C2", {})
            c3 = summary["candidates"].get("C3", {})
            if c2.get("passed") or c3.get("passed"):
                break

    selected = choose_candidate(summary["candidates"])
    summary["selected_candidate"] = selected
    summary["passed"] = selected is not None
    summary["status"] = "passed" if summary["passed"] else ("dry_run" if args.dry_run else "failed")
    if selected is None and "C4" in summary["candidates"]:
        # A graph-rewrite fallback is intentionally not attempted implicitly:
        # it needs a separately audited ONNX equivalence proof.  Keep the
        # explicit boundary in the evidence so a failed sweep cannot be
        # mistaken for a full-precision admission.
        summary["group_conv_fallback"] = {
            "status": "not_run",
            "reason": "requires an isolated ONNX rewrite and equivalence gate",
        }
    summary["executed_candidates"] = executed
    _write_json(output_dir / "summary.json", summary)
    return summary


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--manifest", default=str(DEFAULT_MANIFEST))
    value.add_argument("--registry", default=str(ROOT / "models" / "registry.json"))
    value.add_argument(
        "--output-dir",
        "--report-dir",
        dest="output_dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help="isolated candidate/report root (never models/om or reports/model_pipeline)",
    )
    value.add_argument("--soc-version", default=DEFAULT_SOC)
    value.add_argument("--python-executable", default=sys.executable)
    # The production protocol is intentionally fixed at 32 image records.
    # Keep the option for report compatibility, but reject alternate counts in
    # the normal CLI so a caller cannot silently weaken the 36-input gate.
    value.add_argument("--fixture-count", type=int, default=FIXTURE_IMAGE_COUNT)
    value.add_argument("--warmup", type=int, default=20)
    value.add_argument("--loops", type=int, default=100)
    value.add_argument("--repeats", type=int, default=3)
    value.add_argument("--candidate", action="append", choices=CANDIDATE_IDS)
    value.add_argument("--skip-recall", action="store_true", help="skip temporary 500-image recall gate")
    value.add_argument("--skip-performance", action="store_true", help="skip NPU performance gate")
    value.add_argument("--force-all", action="store_true", help="evaluate all five candidates")
    value.add_argument("--dry-run", action="store_true", help="write commands without invoking ATC/ACL")
    # Internal serial gate-worker options.  They are intentionally hidden from
    # the normal experiment help; the parent invokes one fresh process per ACL
    # lifecycle to avoid CANN global-state reuse after acl.finalize().
    value.add_argument("--worker-stage", choices=("numerical", "retrieval", "performance"), help=argparse.SUPPRESS)
    value.add_argument("--worker-report", help=argparse.SUPPRESS)
    value.add_argument("--worker-registry", default=str(ROOT / "models" / "registry.json"), help=argparse.SUPPRESS)
    value.add_argument("--worker-candidate-om", help=argparse.SUPPRESS)
    value.add_argument("--worker-reference-dir", help=argparse.SUPPRESS)
    value.add_argument("--worker-manifest", default=str(DEFAULT_MANIFEST), help=argparse.SUPPRESS)
    value.add_argument("--worker-work-dir", help=argparse.SUPPRESS)
    value.add_argument("--worker-image", help=argparse.SUPPRESS)
    value.add_argument("--worker-warmup", type=int, default=20, help=argparse.SUPPRESS)
    value.add_argument("--worker-loops", type=int, default=100, help=argparse.SUPPRESS)
    value.add_argument("--worker-repeats", type=int, default=3, help=argparse.SUPPRESS)
    return value


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.worker_stage:
        if not args.worker_report:
            print("--worker-report is required for a gate worker", file=sys.stderr)
            return 2
        return _worker_main(args)
    if args.fixture_count != FIXTURE_IMAGE_COUNT:
        print(
            f"--fixture-count is fixed at {FIXTURE_IMAGE_COUNT} for the production sweep",
            file=sys.stderr,
        )
        return 2
    if args.warmup < 0 or args.loops <= 0 or args.repeats <= 0:
        print("warmup must be >=0 and loops/repeats must be positive", file=sys.stderr)
        return 2
    try:
        summary = run(args)
    except (SweepError, OSError, ValueError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    print(json.dumps({
        "status": summary.get("status"),
        "passed": summary.get("passed"),
        "selected_candidate": summary.get("selected_candidate"),
        "summary": str(Path(args.output_dir).resolve() / "summary.json"),
    }, ensure_ascii=False))
    return 0 if summary.get("passed") or summary.get("status") == "dry_run" else 1


if __name__ == "__main__":
    raise SystemExit(main())
