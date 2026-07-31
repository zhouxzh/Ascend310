"""Relocate the validated OM release beside the existing HF model artifacts."""

from __future__ import annotations

from io import BytesIO
import hashlib
import os
from pathlib import Path

from dotenv import load_dotenv
from huggingface_hub import (
    CommitOperationAdd,
    CommitOperationCopy,
    CommitOperationDelete,
    HfApi,
)


REPO_ID = "zhouxzh/piano-ddsp-ascend310"
ROOT = Path(__file__).resolve().parents[1]
BUNDLE = (
    ROOT
    / "models"
    / "piano_ddsp"
    / "bundles"
    / "model-suite-v1.0.1-gru-unrolled-fp32-origin"
)
OLD_PREFIX = "om/model-suite-v1.0.1-gru-unrolled-fp32-origin"
VALIDATION_PREFIX = "validation/model-suite-v1.0.1-gru-unrolled-fp32-origin"
OM_MANIFEST = "OM_SHA256SUMS.txt"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def target_path(relative: Path) -> str | None:
    if relative.as_posix() == "SHA256SUMS.txt":
        return None
    if relative.parts[:1] == ("models",):
        return relative.name
    if relative.parts[:2] == ("validation", "full-10000"):
        return f"{VALIDATION_PREFIX}/{relative.relative_to('validation').as_posix()}"
    return relative.as_posix()


def main() -> None:
    load_dotenv(ROOT.parent.parent / ".env")
    token = os.environ["HF_TOKEN"]
    api = HfApi(token=token)
    repo_files = set(api.list_repo_files(REPO_ID, repo_type="model"))
    local_files = sorted(path for path in BUNDLE.rglob("*") if path.is_file())
    old_paths = {
        f"{OLD_PREFIX}/{path.relative_to(BUNDLE).as_posix()}" for path in local_files
    }
    current_old_paths = {path for path in repo_files if path.startswith(f"{OLD_PREFIX}/")}
    if current_old_paths != old_paths:
        missing = sorted(old_paths - current_old_paths)
        extra = sorted(current_old_paths - old_paths)
        raise RuntimeError(f"Unexpected old release paths; missing={missing}, extra={extra}")

    operations = []
    mapped_targets: set[str] = set()
    for local_path in local_files:
        relative = local_path.relative_to(BUNDLE)
        destination = target_path(relative)
        if destination is None:
            continue
        if destination in mapped_targets:
            raise RuntimeError(f"Duplicate destination: {destination}")
        mapped_targets.add(destination)
        if destination in repo_files and not destination.startswith(f"{OLD_PREFIX}/"):
            raise RuntimeError(f"Destination already exists: {destination}")
        operations.append(
            CommitOperationCopy(
                src_path_in_repo=f"{OLD_PREFIX}/{relative.as_posix()}",
                path_in_repo=destination,
            )
        )

    manifest_lines = []
    for local_path in sorted((BUNDLE / "models").glob("*.om")):
        manifest_lines.append(
            f"{sha256_file(local_path)}  {local_path.name}"
        )
    manifest = ("\n".join(manifest_lines) + "\n").encode("utf-8")
    if OM_MANIFEST in repo_files:
        raise RuntimeError(f"Destination already exists: {OM_MANIFEST}")
    operations.append(
        CommitOperationAdd(
            path_in_repo=OM_MANIFEST,
            path_or_fileobj=BytesIO(manifest),
        )
    )
    operations.extend(
        CommitOperationDelete(path_in_repo=path) for path in sorted(old_paths)
    )
    commit = api.create_commit(
        repo_id=REPO_ID,
        repo_type="model",
        operations=operations,
        commit_message="Place validated Ascend310B4 OM artifacts beside ONNX models",
    )
    print({"commit_url": commit.commit_url, "operations": len(operations)})


if __name__ == "__main__":
    main()
