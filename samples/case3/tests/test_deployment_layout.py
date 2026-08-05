from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_deployment_syncs_the_vendored_partitura_package_recursively() -> None:
    deployment = (ROOT / "tools" / "deploy_midi_ddsp_webui.ps1").read_text(encoding="utf-8")
    assert 'Copy-Tree "midi_ddsp_webui"' in deployment
    assert "top-level *.py glob" in deployment
    assert (ROOT / "midi_ddsp_webui" / "vendor" / "partitura" / "__init__.py").is_file()
    assert (ROOT / "midi_ddsp_webui" / "vendor" / "partitura" / "NOTICE.md").is_file()


def test_deployment_stages_hashes_and_switches_the_frontend_atomically() -> None:
    deployment = (ROOT / "tools" / "deploy_midi_ddsp_webui.ps1").read_text(encoding="utf-8")
    assert "sha256sum -c .case3-sha256s" in deployment
    assert "dist-releases" in deployment
    assert "mv -Tf" in deployment
    assert ".dist-rollback-" in deployment


def test_deployment_includes_the_verified_ddsp_vst_feature_runtime() -> None:
    deployment = (ROOT / "tools" / "deploy_midi_ddsp_webui.ps1").read_text(encoding="utf-8")
    assert '"models/om/ddsp_vst_feature_mixed_float16.om"' in deployment
    assert '"models/manifests/SHA256SUMS.txt"' in deployment
    assert "a1973830eca98111642dcb331e0a1a163f7a664d871e6d15f40fdc70f9b98db4" in (
        ROOT / "midi_ddsp_webui" / "ddsp_vst_effect.py"
    ).read_text(encoding="utf-8")


@pytest.mark.skipif(
    os.environ.get("CASE3_REQUIRE_BOARD_ASSETS") != "1",
    reason="ignored board runtime assets are checked only in an explicit board-asset job",
)
def test_installed_ddsp_vst_feature_runtime_matches_the_manifest() -> None:
    feature = ROOT / "models" / "om" / "ddsp_vst_feature_mixed_float16.om"
    manifest_path = ROOT / "models" / "manifests" / "SHA256SUMS.txt"
    assert (ROOT / "models" / "om" / "ddsp_vst_feature_mixed_float16.om").is_file()
    manifest = manifest_path.read_text(encoding="utf-8")
    assert (
        "a1973830eca98111642dcb331e0a1a163f7a664d871e6d15f40fdc70f9b98db4  "
        "om/ddsp_vst_feature_mixed_float16.om"
    ) in manifest
    assert hashlib.sha256(feature.read_bytes()).hexdigest() == (
        "a1973830eca98111642dcb331e0a1a163f7a664d871e6d15f40fdc70f9b98db4"
    )


def test_deployment_uses_the_complete_v101_piano_bundle_atomically() -> None:
    deployment = (ROOT / "tools" / "deploy_midi_ddsp_webui.ps1").read_text(encoding="utf-8")
    assert '$PianoReleaseId = "model-suite-v1.0.1"' in deployment
    assert '$PianoBundleId = "model-suite-v1.0.1-gru-unrolled-fp32-origin"' in deployment
    assert "1a4a2500ae357577a4a6f7378c28d54235f543663b9b69cc3cf5938929c458d7" in deployment
    assert "Refusing to overwrite an existing Piano-DDSP bundle" in deployment
    assert ".active-bundle-" in deployment


def test_requirements_keep_runtime_validation_without_export_stack() -> None:
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
    requirement_lines = {
        line.split("=", 1)[0].split("<", 1)[0].split(">", 1)[0].strip().lower()
        for line in requirements
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert "pytest" in requirement_lines
    for package in ("onnx", "onnxruntime"):
        line = next(item for item in requirements if item.startswith(package))
        assert 'platform_machine != "aarch64"' in line
    for package in (
        "tensorflow",
        "tf2onnx",
        "ddsp",
        "pretty_midi",
        "tflite",
        "flatbuffers",
        "pandas",
        "soundfile",
    ):
        assert package not in requirement_lines
