#!/usr/bin/env python3
"""Publish the reviewed B1 static-KV candidate and pin its HF commit.

The script intentionally uploads only the nine paths in ASSETS.  It reads a
write token from HF_TOKEN, creates one atomic Hugging Face commit, then writes
the returned 40-character commit SHA into the source-download lock.  It never
prints the token and it never copies files to a board.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from huggingface_hub import CommitOperationAdd, HfApi


ROOT = Path(__file__).resolve().parents[2]
REPOSITORY = "zhouxzh/ascend310b-llm-om-zoo"
BASE = "models/qwen2.5-0.5b-instruct-static-kv-1024"
ASSETS = (
    (
        "onnx",
        ROOT / "repro/qwen25-kv1024-dual-board-20260827/artifacts/common/qwen25-static-kv-1024-v2.onnx",
        BASE + "/common/qwen25-static-kv-1024-v2.onnx",
    ),
    (
        "tokenizer",
        ROOT / "repro/qwen25-kv1024-dual-board-20260827/artifacts/common/tokenizer.json",
        BASE + "/common/tokenizer.json",
    ),
    (
        "tokenizer-config",
        ROOT / "repro/qwen25-kv1024-dual-board-20260827/artifacts/common/tokenizer_config.json",
        BASE + "/common/tokenizer_config.json",
    ),
    (
        "license",
        ROOT / "repro/qwen25-kv1024-dual-board-20260827/source-model/LICENSE",
        "licenses/qwen/LICENSE",
    ),
    (
        "controller-contract",
        ROOT / "repro/qwen25-kv1024-dual-board-20260827/contracts/Ascend310B1/qwen25-static-kv-1024-fp32-contract.json",
        BASE + "/common/qwen25-static-kv-1024-fp32-contract.json",
    ),
    (
        "b1-om",
        ROOT / "repro/qwen25-kv1024-dual-board-20260827/artifacts/om/Ascend310B1/qwen25-static-kv-1024-b1.om",
        BASE + "/ascend310b1-cann8.0/qwen25-static-kv-1024-b1.om",
    ),
    (
        "b1-descriptor-contract",
        ROOT / "repro/qwen25-kv1024-dual-board-20260827/contracts/Ascend310B1/qwen25-static-kv-1024-v2-om-contract.json",
        BASE + "/ascend310b1-cann8.0/qwen25-static-kv-1024-b1-om-contract.json",
    ),
    (
        "tokenizer-lock",
        ROOT / "repro/qwen25-kv1024-dual-board-20260827/artifacts/common/tokenizer.json.lock.json",
        BASE + "/common/tokenizer.json.lock.json",
    ),
    (
        "b1-om-upstream-lock",
        ROOT / "repro/qwen25-kv1024-dual-board-20260827/artifacts/om/Ascend310B1/qwen25-static-kv-1024-b1.om.lock.json",
        BASE + "/ascend310b1-cann8.0/qwen25-static-kv-1024-b1.om.lock.json",
    ),
)
LOCK_PATH = ROOT / "configs/20t-acl-b1-download.lock.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def inventory() -> list[dict[str, Any]]:
    rows = []
    for name, source, destination in ASSETS:
        if not source.is_file() or source.is_symlink():
            raise RuntimeError("required asset is unavailable: %s" % source)
        rows.append(
            {
                "name": name,
                "source_path": destination,
                "destination": {
                    "onnx": "common/qwen25-static-kv-1024-v2.onnx",
                    "tokenizer": "common/tokenizer.json",
                    "tokenizer-config": "common/tokenizer_config.json",
                    "license": "licenses/qwen-LICENSE",
                    "controller-contract": "contracts/qwen25-static-kv-1024-fp32-contract.json",
                    "b1-om": "om/qwen25-static-kv-1024-b1.om",
                    "b1-descriptor-contract": "contracts/qwen25-static-kv-1024-v2-om-contract.json",
                    "tokenizer-lock": "common/tokenizer.json.lock.json",
                    "b1-om-upstream-lock": "upstream/Ascend310B1/qwen25-static-kv-1024-b1.om.lock.json",
                }[name],
                "bytes": source.stat().st_size,
                "sha256": sha256(source),
            }
        )
    return rows


def write_lock(revision: str, artifacts: list[dict[str, Any]]) -> None:
    if len(revision) != 40 or any(char not in "0123456789abcdef" for char in revision.lower()):
        raise RuntimeError("Hugging Face did not return a full commit SHA")
    document = {
        "schema_version": 1,
        "candidate_id": "qwen2.5-0.5b-instruct-static-kv-1024-b1-offline",
        "release_status": "experimental-machine",
        "source": {
            "repository": REPOSITORY,
            "revision": revision,
            "mirror": "https://hf-mirror.com",
        },
        "base_model": {
            "repository": "Qwen/Qwen2.5-0.5B-Instruct",
            "revision": "7ae557604adf67be50417f59c2c2f167def9a775",
        },
        "target": {"soc": "Ascend310B1", "tier": "20T", "cann": "8.0.0", "python": "3.9.25"},
        "artifacts": artifacts,
    }
    temporary = LOCK_PATH.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(LOCK_PATH)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--endpoint", default="https://huggingface.co")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    token = os.environ.get("HF_TOKEN", "").strip()
    if not token:
        raise SystemExit("HF_TOKEN must be set")
    artifacts = inventory()
    if args.dry_run:
        print(json.dumps({"repository": REPOSITORY, "assets": artifacts}, ensure_ascii=False, indent=2))
        return 0
    api = HfApi(endpoint=args.endpoint, token=token)
    operations = [CommitOperationAdd(path_in_repo=destination, path_or_fileobj=source) for _, source, destination in ASSETS]
    commit = api.create_commit(
        repo_id=REPOSITORY,
        repo_type="model",
        operations=operations,
        commit_message="Publish Ascend310B1 static-KV offline experimental candidate",
    )
    write_lock(commit.oid, artifacts)
    print(json.dumps({"repository": REPOSITORY, "revision": commit.oid, "lock": str(LOCK_PATH)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
