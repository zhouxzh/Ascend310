"""Prepare, publish, and verify Ascend OM assets on Hugging Face.

This tool deliberately has no implicit download, delete, or model activation
behavior.  The ``manifest`` command only hashes local files.  ``upload``
uploads exactly the paths listed in that manifest and refuses to overwrite a
remote file whose digest differs.  ``verify`` downloads each listed file to a
temporary directory and checks its byte count and SHA-256 against the manifest.

The Hugging Face client is an optional dependency.  Keep this module usable in
the board lock without installing it unless an operator explicitly wants to
publish or verify a Hub repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple


DEFAULT_FILES = (
    "palm_ccnet_mixed_fp16.om",
    "compnet_tongji_600_mixed_fp16.om",
    "compnet_iitd_460_mixed_fp16.om",
    "compnet_rest_358_mixed_fp16.om",
    "compnet_xjtu_flash_200_mixed_fp16.om",
    "compnet_xjtu_natural_200_mixed_fp16.om",
)
DEFAULT_HUB_PREFIX = "models/om"
SHA256_LENGTH = 64

MODEL_METADATA: Dict[str, Dict[str, Any]] = {
    "palm_ccnet_mixed_fp16.om": {
        "model_id": "ccnet",
        "feature_dim": 2048,
        "source": "https://huggingface.co/kyereboatengcaleb/palm-ccnet-onnx",
        "source_revision": "a20685bdc844e153227a0d7bf7b6bdb3d2da4ff6",
        "license": "other",
        "production_enabled": True,
    },
    "compnet_tongji_600_mixed_fp16.om": {
        "model_id": "compnet_tongji_600",
        "feature_dim": 512,
        "source": "https://github.com/JonnyLewis/compnet",
        "source_revision": "21f8b56bcbcb620eafa85eaff5ea1f5a9675f194",
        "license": "BSD-3-Clause",
        "production_enabled": False,
    },
    "compnet_iitd_460_mixed_fp16.om": {
        "model_id": "compnet_iitd_460",
        "feature_dim": 512,
        "source": "https://github.com/JonnyLewis/compnet",
        "source_revision": "21f8b56bcbcb620eafa85eaff5ea1f5a9675f194",
        "license": "BSD-3-Clause",
        "production_enabled": False,
    },
    "compnet_rest_358_mixed_fp16.om": {
        "model_id": "compnet_rest_358",
        "feature_dim": 512,
        "source": "https://github.com/JonnyLewis/compnet",
        "source_revision": "21f8b56bcbcb620eafa85eaff5ea1f5a9675f194",
        "license": "BSD-3-Clause",
        "production_enabled": False,
    },
    "compnet_xjtu_flash_200_mixed_fp16.om": {
        "model_id": "compnet_xjtu_flash_200",
        "feature_dim": 512,
        "source": "https://github.com/JonnyLewis/compnet",
        "source_revision": "21f8b56bcbcb620eafa85eaff5ea1f5a9675f194",
        "license": "BSD-3-Clause",
        "production_enabled": False,
    },
    "compnet_xjtu_natural_200_mixed_fp16.om": {
        "model_id": "compnet_xjtu_natural_200",
        "feature_dim": 512,
        "source": "https://github.com/JonnyLewis/compnet",
        "source_revision": "21f8b56bcbcb620eafa85eaff5ea1f5a9675f194",
        "license": "BSD-3-Clause",
        "production_enabled": False,
    },
}


class AssetError(RuntimeError):
    """Raised when an asset or remote repository fails a release check."""


def _sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative_name(path: str) -> str:
    """Return a safe basename and reject path traversal in user manifests."""
    candidate = Path(path)
    if candidate.name != path or candidate.name in {"", ".", ".."}:
        raise AssetError(f"asset name must be a simple filename: {path!r}")
    if candidate.suffix.lower() != ".om":
        raise AssetError(f"asset is not an OM file: {path!r}")
    return candidate.name


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AssetError(f"cannot read manifest {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise AssetError("manifest root must be an object")
    assets = payload.get("assets")
    if not isinstance(assets, list) or not assets:
        raise AssetError("manifest.assets must be a non-empty list")
    seen: set[str] = set()
    for index, item in enumerate(assets):
        if not isinstance(item, dict):
            raise AssetError(f"manifest.assets[{index}] must be an object")
        name = _relative_name(str(item.get("filename", "")))
        if name in seen:
            raise AssetError(f"duplicate asset filename: {name}")
        seen.add(name)
        sha = item.get("sha256")
        if not isinstance(sha, str) or len(sha) != SHA256_LENGTH:
            raise AssetError(f"{name}: sha256 must be a 64-character string")
        try:
            int(sha, 16)
        except ValueError as exc:
            raise AssetError(f"{name}: sha256 is not hexadecimal") from exc
        size = item.get("bytes")
        if isinstance(size, bool) or not isinstance(size, int) or size <= 0:
            raise AssetError(f"{name}: bytes must be a positive integer")
        hub_path = item.get("hub_path")
        if not isinstance(hub_path, str) or not hub_path.startswith(f"{DEFAULT_HUB_PREFIX}/"):
            raise AssetError(f"{name}: hub_path must be below {DEFAULT_HUB_PREFIX}/")
        if Path(hub_path).name != name:
            raise AssetError(f"{name}: hub_path basename does not match filename")
    return payload


def _iter_assets(manifest: Mapping[str, Any]) -> Iterable[Mapping[str, Any]]:
    assets = manifest.get("assets")
    if not isinstance(assets, list):
        raise AssetError("manifest.assets must be a list")
    return (item for item in assets if isinstance(item, Mapping))


def _check_license(manifest: Mapping[str, Any]) -> None:
    """Block publication unless the manifest records explicit approval.

    A permissive source-code license is not automatically a permission to
    redistribute a trained weight.  The manifest therefore needs an explicit
    ``redistribution_status`` of ``approved`` for every asset.
    """
    blocked = [
        str(item.get("filename"))
        for item in _iter_assets(manifest)
        if item.get("redistribution_status") != "approved"
    ]
    if blocked:
        joined = ", ".join(blocked)
        raise AssetError(
            "public redistribution is not approved for: "
            f"{joined}. Obtain and record upstream permission first."
        )


def make_manifest(
    input_dir: Path,
    *,
    repo_id: str,
    release_id: str,
    redistribution: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Hash the six expected OM files without copying them into the repo."""
    input_dir = input_dir.resolve()
    if not input_dir.is_dir():
        raise AssetError(f"input directory does not exist: {input_dir}")
    redistribution = redistribution or {}
    assets: list[dict[str, Any]] = []
    for filename in DEFAULT_FILES:
        path = input_dir / filename
        if not path.is_file():
            raise AssetError(f"missing OM asset: {path}")
        assets.append(
            {
                "filename": filename,
                "hub_path": f"{DEFAULT_HUB_PREFIX}/{filename}",
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
                "redistribution_status": redistribution.get(filename, "review_required"),
                **MODEL_METADATA[filename],
            }
        )
    return {
        "schema_version": 1,
        "repo_id": repo_id,
        "release_id": release_id,
        "hardware": {
            "soc": "Ascend310B4",
            "compute_tier": "8T",
            "cann": "8.0.0",
        },
        "contract": {
            "input": "float32[1,1,128,128]",
            "output": "CCNet 2048-D; CompNet 512-D L2-normalized embedding",
            "precision": "mixed_fp16",
        },
        "assets": assets,
        "excluded": [
            "FP32 origin OM: ATC MaxPoolV3 DT_FLOAT unsupported on Ascend 310B4/CANN 8.0.0",
            "legacy compnet_static_gabor_mixed_fp16.om",
            "checkpoints, ONNX files, datasets, templates, reports, and real biometric images",
        ],
    }


def validate_local_assets(input_dir: Path, manifest: Mapping[str, Any]) -> None:
    input_dir = input_dir.resolve()
    for item in _iter_assets(manifest):
        filename = _relative_name(str(item["filename"]))
        path = input_dir / filename
        if not path.is_file():
            raise AssetError(f"missing local asset: {path}")
        expected_size = int(item["bytes"])
        actual_size = path.stat().st_size
        actual_hash = _sha256(path)
        expected_hash = str(item["sha256"]).lower()
        if actual_size != expected_size or actual_hash != expected_hash:
            raise AssetError(
                f"{filename}: bytes {actual_size} != {expected_size}; "
                f"SHA-256 {actual_hash} != {expected_hash}"
            )


def _hub_api(endpoint: Optional[str] = None) -> Any:
    try:
        from huggingface_hub import HfApi
    except ImportError as exc:
        raise AssetError(
            "huggingface_hub is required for upload/verify; install it explicitly "
            "in the operator environment"
        ) from exc
    kwargs: dict[str, Any] = {}
    configured_endpoint = endpoint or os.environ.get("HF_ENDPOINT") or os.environ.get("PALMPRINT_HF_ENDPOINT")
    if configured_endpoint:
        kwargs["endpoint"] = configured_endpoint.rstrip("/")
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if token:
        kwargs["token"] = token
    return HfApi(**kwargs)


def _remote_file_hash(api: Any, repo_id: str, hub_path: str, revision: Optional[str]) -> Tuple[int, str]:
    """Download one remote file into a temp directory and hash it."""
    from huggingface_hub import hf_hub_download

    kwargs: dict[str, Any] = {"repo_id": repo_id, "filename": hub_path, "repo_type": "model"}
    if revision:
        kwargs["revision"] = revision
    token = os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")
    if token:
        kwargs["token"] = token
    temp_root = Path(tempfile.mkdtemp(prefix="hf-om-verify-"))
    try:
        downloaded = Path(hf_hub_download(local_dir=str(temp_root), **kwargs))
        return downloaded.stat().st_size, _sha256(downloaded)
    finally:
        shutil.rmtree(temp_root, ignore_errors=True)


def verify_remote(repo_id: str, manifest: Mapping[str, Any], revision: Optional[str] = None) -> str:
    api = _hub_api()
    info = api.model_info(repo_id, revision=revision)
    resolved_revision = getattr(info, "sha", None) or revision or "main"
    for item in _iter_assets(manifest):
        hub_path = str(item["hub_path"])
        expected_size = int(item["bytes"])
        expected_hash = str(item["sha256"]).lower()
        actual_size, actual_hash = _remote_file_hash(api, repo_id, hub_path, resolved_revision)
        if actual_size != expected_size or actual_hash != expected_hash:
            raise AssetError(
                f"remote {hub_path}: {actual_size}/{actual_hash} != "
                f"{expected_size}/{expected_hash}"
            )
        print(f"verified {hub_path} {actual_size} {actual_hash}")
    return str(resolved_revision)


def upload(repo_id: str, input_dir: Path, manifest: Mapping[str, Any], commit_message: str) -> str:
    _check_license(manifest)
    validate_local_assets(input_dir, manifest)
    api = _hub_api()
    api.create_repo(repo_id=repo_id, repo_type="model", private=False, exist_ok=True)

    existing = set(api.list_repo_files(repo_id=repo_id, repo_type="model"))
    operations: list[tuple[Path, str]] = []
    for item in _iter_assets(manifest):
        filename = _relative_name(str(item["filename"]))
        hub_path = str(item["hub_path"])
        local_path = input_dir.resolve() / filename
        if hub_path in existing:
            remote_size, remote_hash = _remote_file_hash(api, repo_id, hub_path, None)
            if remote_size != int(item["bytes"]) or remote_hash != str(item["sha256"]).lower():
                raise AssetError(
                    f"refusing to overwrite existing remote file with different hash: {hub_path}"
                )
            print(f"unchanged {hub_path} {remote_hash}")
            continue
        operations.append((local_path, hub_path))

    if operations:
        # upload_file creates/updates only the listed paths; it never deletes
        # files that are already in the repository.
        try:
            from huggingface_hub import CommitOperationAdd
        except ImportError as exc:  # pragma: no cover - guarded by _hub_api
            raise AssetError("huggingface_hub does not expose CommitOperationAdd") from exc
        commit = api.create_commit(
            repo_id=repo_id,
            repo_type="model",
            operations=[
                CommitOperationAdd(path_in_repo=hub_path, path_or_fileobj=str(local_path))
                for local_path, hub_path in operations
            ],
            commit_message=commit_message,
        )
        revision = getattr(commit, "oid", None) or getattr(commit, "commit_hash", None)
    else:
        revision = None
    return verify_remote(repo_id, manifest, revision=revision)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    manifest_parser = subparsers.add_parser("manifest", help="hash local OM files")
    manifest_parser.add_argument("--input-dir", type=Path, required=True)
    manifest_parser.add_argument("--output", type=Path, required=True)
    manifest_parser.add_argument("--repo-id", default="zhouxzh/ascend310-palmprint")
    manifest_parser.add_argument("--release-id", default="1.0.0")
    manifest_parser.add_argument(
        "--approve", action="append", default=[], metavar="FILENAME",
        help="mark one filename redistribution_status=approved; repeat per file",
    )

    upload_parser = subparsers.add_parser("upload", help="upload approved files, then verify")
    upload_parser.add_argument("--repo-id", default="zhouxzh/ascend310-palmprint")
    upload_parser.add_argument("--input-dir", type=Path, required=True)
    upload_parser.add_argument("--manifest", type=Path, required=True)
    upload_parser.add_argument("--commit-message", default="Publish verified Ascend 310B4 mixed-FP16 OM assets")
    upload_parser.add_argument("--endpoint", default=None)

    verify_parser = subparsers.add_parser("verify", help="download and verify remote files")
    verify_parser.add_argument("--repo-id", default="zhouxzh/ascend310-palmprint")
    verify_parser.add_argument("--manifest", type=Path, required=True)
    verify_parser.add_argument("--revision", default=None)
    verify_parser.add_argument("--endpoint", default=None)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        if arguments.command == "manifest":
            approved = {_relative_name(name) for name in arguments.approve}
            payload = make_manifest(
                arguments.input_dir,
                repo_id=arguments.repo_id,
                release_id=arguments.release_id,
                redistribution={name: "approved" for name in approved},
            )
            _write_json(arguments.output, payload)
            print(f"wrote {arguments.output}")
            for item in _iter_assets(payload):
                print(f"{item['filename']} {item['bytes']} {item['sha256']}")
            return 0
        manifest = _load_manifest(arguments.manifest)
        if arguments.command == "upload":
            if arguments.endpoint:
                os.environ["HF_ENDPOINT"] = arguments.endpoint.rstrip("/")
            revision = upload(arguments.repo_id, arguments.input_dir, manifest, arguments.commit_message)
            print(f"uploaded and verified {arguments.repo_id}@{revision}")
            return 0
        if arguments.command == "verify":
            if arguments.endpoint:
                os.environ["HF_ENDPOINT"] = arguments.endpoint.rstrip("/")
            revision = verify_remote(arguments.repo_id, manifest, arguments.revision)
            print(f"verified {arguments.repo_id}@{revision}")
            return 0
        raise AssetError(f"unknown command: {arguments.command}")
    except AssetError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
