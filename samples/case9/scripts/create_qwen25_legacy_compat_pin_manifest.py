#!/usr/bin/env python3
"""Create a byte/SHA-256 pin manifest for a fixed historical Qwen2.5 B4 OM.

The generated JSON is a campaign-local input for
``serve_qwen25_legacy_compat.py``.  It has no authority to change a profile:
all generated digests must match the fixed historical identities compiled into
``qwen25_legacy_compat_service.py``.  The file is written through ``.part``
and an atomic rename after its own content is constructed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Mapping, Optional, Sequence


_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from qwen25_legacy_compat_service import (
    ArtifactPaths,
    CompatibilityError,
    MANIFEST_SCHEMA_VERSION,
    _sha256_file,
    profile_for,
)


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        required=True,
        choices=("qwen25-full2048-b4-compat", "qwen25-lastlogits2048-b4-compat"),
    )
    parser.add_argument("--om", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--tokenizer", type=Path, required=True)
    parser.add_argument("--tokenizer-config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-output-sha256", default=None)
    return parser.parse_args(argv)


def build_manifest(profile_key: str, paths: ArtifactPaths) -> Mapping[str, object]:
    profile = profile_for(profile_key)
    artifacts = {}
    for name, path in paths.items().items():
        if not path.is_file():
            raise CompatibilityError(f"required {name} artifact does not exist: {path}")
        size, digest = _sha256_file(path)
        known = profile.artifacts[name]
        if digest != known.sha256:
            raise CompatibilityError(f"{name} SHA-256 does not match fixed historical identity")
        if known.bytes is not None and size != known.bytes:
            raise CompatibilityError(f"{name} byte count does not match fixed historical identity")
        artifacts[name] = {"bytes": size, "sha256": digest}
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "profile": profile.key,
        "source_soc": profile.source_soc,
        "board_soc": profile.target_soc,
        "artifacts": artifacts,
    }


def _validate_sha256(value: str) -> str:
    normalized = value.strip().lower()
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise CompatibilityError("expected output SHA-256 must be 64 lowercase hexadecimal characters")
    return normalized


def write_manifest(document: Mapping[str, object], output: Path, expected_output_sha256: Optional[str] = None) -> str:
    target = output.expanduser()
    if not target.parent.is_dir():
        raise CompatibilityError(f"output parent does not exist: {target.parent}")
    if target.exists():
        raise CompatibilityError(f"refusing to overwrite existing pin manifest: {target}")
    data = (json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    temporary = target.with_name(target.name + ".part")
    temporary.write_bytes(data)
    _, digest = _sha256_file(temporary)
    if expected_output_sha256 is not None and digest != _validate_sha256(expected_output_sha256):
        temporary.unlink(missing_ok=True)
        raise CompatibilityError("generated pin manifest SHA-256 does not match --expected-output-sha256")
    temporary.replace(target)
    return digest


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    paths = ArtifactPaths(
        om=args.om.expanduser(),
        contract=args.contract.expanduser(),
        tokenizer=args.tokenizer.expanduser(),
        tokenizer_config=args.tokenizer_config.expanduser(),
    )
    try:
        document = build_manifest(args.profile, paths)
        digest = write_manifest(document, args.output, args.expected_output_sha256)
    except CompatibilityError as exc:
        raise SystemExit(f"historical OM pin manifest refused: {exc}") from exc
    print(json.dumps({"status": "created", "output": str(args.output.expanduser()), "sha256": digest}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
