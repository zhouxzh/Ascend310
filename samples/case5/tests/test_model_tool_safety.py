from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from time_frequency_dashboard.model.candidate_catalog import CANDIDATES
from time_frequency_dashboard.model.compile_inference_candidate import parse_shape as parse_atc_shape
from time_frequency_dashboard.model.inference_manifest import load_inference_manifest
from time_frequency_dashboard.model.materialize_inference_manifest import main as materialize_main
from time_frequency_dashboard.model.materialize_inference_manifest import parse_shape as parse_manifest_shape
from time_frequency_dashboard.model.preprocessing_contract import (
    iq_preprocessing_contract,
    spectrogram_preprocessing_contract,
)
from time_frequency_dashboard.model.safe_json import write_new_json, write_validated_new_json
from time_frequency_dashboard.model.upgrade_inference_manifest import main as upgrade_main
from time_frequency_dashboard.model.verify_inference_model import (
    _load_finite_input,
    benchmark,
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_candidate_manifest(directory: Path) -> Path:
    onnx = directory / "model.onnx"
    om = directory / "model.om"
    onnx.write_bytes(b"onnx")
    om.write_bytes(b"om")
    manifest = directory / "legacy.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "model_id": "torchsig-xcit-v1.1.0-b1",
                "task": "iq_classification",
                "source": {
                    "url": CANDIDATES["torchsig_xcit"].source_url,
                    "revision": CANDIDATES["torchsig_xcit"].source_revision,
                    "license": CANDIDATES["torchsig_xcit"].license,
                    "upstream_weight_sha256": CANDIDATES["torchsig_xcit"].upstream_weight_sha256,
                },
                "input": {
                    "name": "input_tensor",
                    "shape": [1, 2, 1024],
                    "dtype": "float32",
                    "normalization": "infinity_norm",
                    "sample_rate_hz": None,
                    "sampling_convention": CANDIDATES["torchsig_xcit"].sampling_convention,
                    "preprocessing": iq_preprocessing_contract([1, 2, 1024]),
                },
                "output": {
                    "names": ["logits"],
                    "shape": [1, len(CANDIDATES["torchsig_xcit"].class_names)],
                    "class_names": list(CANDIDATES["torchsig_xcit"].class_names),
                },
                "artifacts": {
                    "onnx_path": onnx.name,
                    "om_path": om.name,
                    "onnx_sha256": _digest(onnx),
                    "om_sha256": _digest(om),
                },
                "conversion": {"atc_command": ["atc"], "cann_version": "CANN test"},
                "admission": {"status": "candidate", "p95_meets_real_time": True},
            }
        ),
        encoding="utf-8",
    )
    return manifest


def test_shape_parsers_reject_empty_dimensions_and_accept_positive_dimensions() -> None:
    assert parse_atc_shape("1,2,1024") == (1, 2, 1024)
    assert parse_manifest_shape("1,3,64,64") == [1, 3, 64, 64]
    for value in ("", "1,,2", ",1", "1,0", "1,-2", "one,2"):
        with pytest.raises((ValueError, TypeError)):
            parse_manifest_shape(value)
        with pytest.raises((ValueError, TypeError, argparse.ArgumentTypeError)):
            parse_atc_shape(value)


def test_preprocessing_contracts_reject_bool_and_string_shape_values() -> None:
    with pytest.raises(ValueError, match="positive integers"):
        iq_preprocessing_contract([1, True, 1024])
    with pytest.raises(ValueError, match="positive integers"):
        spectrogram_preprocessing_contract([1, 3, "64", 64])


def test_safe_json_refuses_overwrite_and_removes_invalid_tempfile(tmp_path) -> None:
    output = tmp_path / "evidence.json"
    write_new_json(output, {"value": 1})
    with pytest.raises(FileExistsError):
        write_new_json(output, {"value": 2})
    assert json.loads(output.read_text(encoding="utf-8")) == {"value": 1}

    invalid = tmp_path / "invalid.json"
    with pytest.raises(ValueError, match="validator"):
        write_validated_new_json(
            invalid,
            {"value": 1},
            validator=lambda _path: (_ for _ in ()).throw(ValueError("validator failed")),
        )
    assert not invalid.exists()
    assert not list(tmp_path.glob(".invalid.json.*.tmp"))


def test_materialize_requires_matching_atc_input_name_and_does_not_write(tmp_path, monkeypatch) -> None:
    onnx = tmp_path / "candidate.onnx"
    om = tmp_path / "candidate.om"
    onnx.write_bytes(b"onnx")
    om.write_bytes(b"om")
    evidence = tmp_path / "candidate.atc.json"
    evidence.write_text(
        json.dumps(
            {
                "status": "om_ready",
                "input_name": "wrong_input",
                "input_shape": [1, 2, 1024],
                "onnx_path": onnx.name,
                "om_path": om.name,
                "onnx_sha256": _digest(onnx),
                "om_sha256": _digest(om),
                "atc_command": ["atc"],
                "cann_version": "CANN test",
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "candidate.manifest.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "materialize_inference_manifest",
            "--candidate",
            "torchsig_xcit",
            "--atc-evidence",
            str(evidence),
            "--output-shape",
            f"1,{len(CANDIDATES['torchsig_xcit'].class_names)}",
            "--output",
            str(output),
        ],
    )
    with pytest.raises(ValueError, match="input_name"):
        materialize_main()
    assert not output.exists()


def test_upgrade_preserves_conversion_version_and_refuses_conflicting_contract(tmp_path, monkeypatch) -> None:
    manifest = _write_candidate_manifest(tmp_path)
    output = tmp_path / "upgraded.manifest.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "upgrade_inference_manifest",
            "--manifest",
            str(manifest),
            "--candidate",
            "torchsig_xcit",
            "--output",
            str(output),
        ],
    )
    assert upgrade_main() == 0
    upgraded = load_inference_manifest(output, require_accepted=False)
    assert upgraded.cann_version == "CANN test"

    raw = json.loads(manifest.read_text(encoding="utf-8"))
    raw["input"]["sampling_convention"] = "conflict"
    manifest.write_text(json.dumps(raw), encoding="utf-8")
    conflict = tmp_path / "conflict.manifest.json"
    monkeypatch.setattr(
        "sys.argv",
        [
            "upgrade_inference_manifest",
            "--manifest",
            str(manifest),
            "--candidate",
            "torchsig_xcit",
            "--output",
            str(conflict),
        ],
    )
    with pytest.raises(ValueError, match="input contract|sampling_convention"):
        upgrade_main()
    assert not conflict.exists()


def test_verify_input_and_benchmark_reject_nonfinite_or_invalid_values(tmp_path) -> None:
    source = tmp_path / "source.npy"
    np.save(source, np.full((1, 2, 4), np.nan, dtype=np.float32))
    with pytest.raises(ValueError, match="NaN or Inf"):
        _load_finite_input(source, (1, 2, 4))

    with pytest.raises(ValueError, match="positive integer"):
        benchmark(lambda values: [values], np.zeros((1,), dtype=np.float32), warmup=0, iterations=0)
