import json
from pathlib import Path

import pytest

from tools.board.hf_om_publish import (
    AssetError,
    DEFAULT_FILES,
    make_manifest,
    validate_local_assets,
)


def _staged_assets(root: Path) -> Path:
    for index, filename in enumerate(DEFAULT_FILES):
        (root / filename).write_bytes((bytes([index]) * (32 + index)))
    return root


def test_manifest_hashes_only_expected_om_files(tmp_path: Path) -> None:
    asset_dir = _staged_assets(tmp_path)
    manifest = make_manifest(
        asset_dir,
        repo_id="zhouxzh/ascend310-palmprint",
        release_id="test",
        redistribution={name: "approved" for name in DEFAULT_FILES},
    )

    assert [item["filename"] for item in manifest["assets"]] == list(DEFAULT_FILES)
    assert all(item["hub_path"].startswith("models/om/") for item in manifest["assets"])
    validate_local_assets(asset_dir, manifest)


def test_local_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    asset_dir = _staged_assets(tmp_path)
    manifest = make_manifest(
        asset_dir,
        repo_id="test/repo",
        release_id="test",
        redistribution={name: "approved" for name in DEFAULT_FILES},
    )
    (asset_dir / DEFAULT_FILES[0]).write_bytes(b"changed")

    with pytest.raises(AssetError, match="SHA-256"):
        validate_local_assets(asset_dir, manifest)


def test_manifest_rejects_nested_or_non_om_remote_names(tmp_path: Path) -> None:
    asset_dir = _staged_assets(tmp_path)
    manifest = make_manifest(
        asset_dir,
        repo_id="test/repo",
        release_id="test",
        redistribution={name: "approved" for name in DEFAULT_FILES},
    )
    manifest["assets"][0]["hub_path"] = "../outside.om"

    with pytest.raises(AssetError, match="hub_path"):
        # Reuse the public local validator after triggering manifest parsing in
        # the same way the upload path does; a malformed remote path must never
        # be accepted as an upload target.
        from tools.board.hf_om_publish import _load_manifest

        manifest_path = tmp_path / "manifest.json"
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
        _load_manifest(manifest_path)
