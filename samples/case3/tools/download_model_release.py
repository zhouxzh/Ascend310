"""Download a verified ONNX/OM model release from Hugging Face.

The downloader always resolves a user-supplied immutable revision, fetches the
release ``SHA256SUMS`` manifest first, then resumes and verifies every selected
asset.  It deliberately has no TensorFlow, TFLite, or model-export dependency.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import time
from typing import Iterable
import urllib.error
import urllib.parse
import urllib.request


REPOSITORY = "zhouxzh/piano-ddsp-ascend310"
DEFAULT_PIANO_REVISION = "model-suite-v1.0.1"
DEFAULT_PIANO_COMMIT = "c41911aa7de454aeacf0b3edbb2d06a0801fb3ff"
DEFAULT_PIANO_MANIFEST_SHA256 = (
    "1a4a2500ae357577a4a6f7378c28d54235f543663b9b69cc3cf5938929c458d7"
)
DEFAULT_PIANO_TARGET = Path("models/piano_ddsp/model-suite-v1.0.1")
DEFAULT_PIANO_FILES = (
    "model-suite.json",
    "README.md",
    "VALIDATION.md",
    "LICENSE",
    "LICENSES/Apache-2.0.txt",
    "LICENSES/CC-BY-NC-SA-4.0.txt",
    "THIRD_PARTY_NOTICES.md",
    "ddsp_piano_gru_ir_96_64.onnx",
    "ddsp_piano_gru_ir_96_64.json",
    "ddsp_piano_film_fdn_128_96.onnx",
    "ddsp_piano_film_fdn_128_96.json",
    "ddsp_piano_gru_ir_fullwet_96_64.onnx",
    "ddsp_piano_gru_ir_fullwet_96_64.json",
    "ddsp_piano_film_ir_fullwet_96_64.onnx",
    "ddsp_piano_film_ir_fullwet_96_64.json",
)
SOURCE_ONLY_SUFFIXES = {".ckpt", ".pb", ".pt", ".pth", ".tflite"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_release_path(name: str) -> str:
    path = PurePosixPath(name)
    if not name or path.is_absolute() or ".." in path.parts or path == PurePosixPath("."):
        raise ValueError(f"Unsafe release path: {name!r}")
    return path.as_posix()


def parse_sha256s(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            raise ValueError(f"Invalid SHA256 entry: {raw_line!r}")
        digest, name = parts
        name = safe_release_path(name.lstrip("*"))
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError(f"Invalid SHA256 entry: {raw_line!r}")
        if name in result:
            raise ValueError(f"Duplicate SHA256 entry: {name}")
        result[name] = digest
    if not result:
        raise ValueError("SHA256SUMS contains no file entries")
    return result


def build_opener(proxy: str | None) -> urllib.request.OpenerDirector:
    handlers: list[urllib.request.BaseHandler] = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


def request_headers(token: str | None) -> dict[str, str]:
    headers = {"User-Agent": "case3-model-release-downloader/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def resolve_revision(
    opener: urllib.request.OpenerDirector,
    repository: str,
    revision: str,
    token: str | None,
    timeout: float,
) -> str:
    if not revision:
        raise ValueError("--revision is required and must identify a fixed release")
    repository_path = urllib.parse.quote(repository, safe="/")
    revision_path = urllib.parse.quote(revision, safe="")
    url = f"https://huggingface.co/api/models/{repository_path}/revision/{revision_path}"
    request = urllib.request.Request(url, headers=request_headers(token))
    with opener.open(request, timeout=timeout) as response:
        payload = json.load(response)
    commit = str(payload.get("sha", ""))
    if len(commit) != 40 or any(char not in "0123456789abcdef" for char in commit):
        raise RuntimeError(f"HF revision returned an invalid commit: {commit!r}")
    return commit


def validate_requested_revision(revision: str) -> None:
    if not revision or revision.lower() in {"head", "main", "master"}:
        raise ValueError("--revision must be a fixed release tag or commit, not a moving branch")


def validate_pinned_revision(repository: str, revision: str, commit: str) -> None:
    """Reject a moved default release tag before fetching model payloads."""
    if (
        repository == REPOSITORY
        and revision == DEFAULT_PIANO_REVISION
        and commit != DEFAULT_PIANO_COMMIT
    ):
        raise RuntimeError(
            f"HF tag {DEFAULT_PIANO_REVISION} resolved to {commit}, "
            f"expected {DEFAULT_PIANO_COMMIT}"
        )


def hf_url(repository: str, commit: str, name: str) -> str:
    return (
        f"https://huggingface.co/{urllib.parse.quote(repository, safe='/')}/resolve/"
        f"{urllib.parse.quote(commit, safe='')}/"
        f"{urllib.parse.quote(safe_release_path(name), safe='/')}"
    )


def download_http(
    opener: urllib.request.OpenerDirector,
    url: str,
    target: Path,
    token: str | None,
    timeout: float,
) -> None:
    """Resume ``target.part`` when the server supports ranges, then atomically replace."""
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    offset = partial.stat().st_size if partial.is_file() else 0
    headers = request_headers(token)
    if offset:
        headers["Range"] = f"bytes={offset}-"
    request = urllib.request.Request(url, headers=headers)
    with opener.open(request, timeout=timeout) as response:
        status = getattr(response, "status", response.getcode())
        if offset and status != 206:
            partial.unlink(missing_ok=True)
            return download_http(opener, url, target, token, timeout)
        mode = "ab" if offset else "wb"
        with partial.open(mode) as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
    os.replace(partial, target)


def verified(path: Path, expected: str | None) -> bool:
    return bool(path.is_file() and expected and sha256_file(path) == expected)


def validate_release(target: Path, hashes: dict[str, str], names: Iterable[str]) -> None:
    for name in names:
        safe_name = safe_release_path(name)
        path = target / safe_name
        if not path.is_file():
            raise FileNotFoundError(path)
        expected_hash = hashes.get(safe_name)
        if expected_hash is None:
            raise ValueError(f"SHA256SUMS does not contain {safe_name}")
        actual = sha256_file(path)
        if actual != expected_hash:
            raise ValueError(f"SHA256 mismatch for {path}: {actual} != {expected_hash}")


def release_path(release_dir: str, name: str) -> str:
    normalized_name = safe_release_path(name)
    if not release_dir:
        return normalized_name
    return f"{safe_release_path(release_dir).rstrip('/')}/{normalized_name}"


def default_release_files(hashes: dict[str, str]) -> tuple[str, ...]:
    """Select deployable release assets, not source checkpoints or TFLite files."""
    return tuple(
        name for name in hashes if Path(name).suffix.lower() not in SOURCE_ONLY_SUFFIXES
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=REPOSITORY)
    parser.add_argument(
        "--revision",
        default=DEFAULT_PIANO_REVISION,
        help="Immutable HF tag or commit; it is resolved to and recorded as a commit SHA.",
    )
    parser.add_argument(
        "--release-dir",
        default="",
        help="Directory below the repository containing SHA256SUMS and release assets.",
    )
    parser.add_argument("--target-dir", type=Path, default=DEFAULT_PIANO_TARGET)
    parser.add_argument("--manifest", default="SHA256SUMS")
    parser.add_argument(
        "--manifest-sha256",
        default=DEFAULT_PIANO_MANIFEST_SHA256,
        help="Expected SHA256SUMS digest. Pass an explicit release digest for non-default releases.",
    )
    parser.add_argument(
        "--files",
        nargs="*",
        help="Optional manifest-relative asset names; defaults to every SHA256SUMS entry.",
    )
    parser.add_argument("--proxy")
    parser.add_argument("--token", default=os.environ.get("HF_TOKEN"))
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=2)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.timeout <= 0 or args.retries < 0:
        raise ValueError("timeout must be positive and retries must be non-negative")
    if len(args.manifest_sha256) != 64 or any(
        char not in "0123456789abcdef" for char in args.manifest_sha256
    ):
        raise ValueError("--manifest-sha256 must be a lowercase 64-character SHA256")
    validate_requested_revision(args.revision)

    target = args.target_dir.resolve()
    target.mkdir(parents=True, exist_ok=True)
    opener = build_opener(args.proxy)
    commit = resolve_revision(opener, args.repository, args.revision, args.token, args.timeout)
    validate_pinned_revision(args.repository, args.revision, commit)

    manifest_name = safe_release_path(args.manifest)
    manifest_path = target / manifest_name
    manifest_remote_path = release_path(args.release_dir, manifest_name)
    if not verified(manifest_path, args.manifest_sha256):
        download_http(
            opener,
            hf_url(args.repository, commit, manifest_remote_path),
            manifest_path,
            args.token,
            args.timeout,
        )
    actual_manifest_hash = sha256_file(manifest_path)
    if actual_manifest_hash != args.manifest_sha256:
        raise ValueError(
            f"SHA256 mismatch for manifest: {actual_manifest_hash} != {args.manifest_sha256}"
        )
    hashes = parse_sha256s(manifest_path.read_text(encoding="utf-8"))
    selected = tuple(args.files) if args.files else default_release_files(hashes)
    if manifest_name not in selected and manifest_name in hashes:
        selected = (manifest_name, *selected)

    diagnostics: list[dict[str, object]] = []
    for name in selected:
        safe_name = safe_release_path(name)
        expected = hashes.get(safe_name)
        if expected is None:
            raise ValueError(f"SHA256SUMS does not contain {safe_name}")
        destination = target / safe_name
        if verified(destination, expected):
            diagnostics.append({"file": safe_name, "status": "verified-skip"})
            continue

        last_error: BaseException | None = None
        for attempt in range(args.retries + 1):
            try:
                download_http(
                    opener,
                    hf_url(args.repository, commit, release_path(args.release_dir, safe_name)),
                    destination,
                    args.token,
                    args.timeout,
                )
                validate_release(target, hashes, (safe_name,))
                diagnostics.append(
                    {"file": safe_name, "status": "downloaded", "attempt": attempt + 1}
                )
                last_error = None
                break
            except (OSError, ValueError, urllib.error.URLError) as exc:
                last_error = exc
                diagnostics.append(
                    {"file": safe_name, "status": "retry", "attempt": attempt + 1, "error": str(exc)}
                )
                if attempt < args.retries:
                    time.sleep(min(2**attempt, 4))
        if last_error is not None:
            raise RuntimeError(f"Failed to download {safe_name}: {last_error}") from last_error

    validate_release(target, hashes, selected)
    report = {
        "schema": "case3-model-release-download/v1",
        "repository": args.repository,
        "revision": args.revision,
        "resolved_commit": commit,
        "release_dir": args.release_dir,
        "manifest": manifest_name,
        "manifest_sha256": actual_manifest_hash,
        "files": diagnostics,
    }
    (target / "download-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Verified {len(selected)} release files in {target}")


if __name__ == "__main__":
    main()
