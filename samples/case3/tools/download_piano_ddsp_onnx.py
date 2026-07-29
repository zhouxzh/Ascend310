"""Download the pinned Piano-DDSP ONNX release without third-party packages."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Iterable
import urllib.error
import urllib.parse
import urllib.request


REPOSITORY = "zhouxzh/piano-ddsp-ascend310"
RELEASE = "model-suite-v1.0.0"
EXPECTED_COMMIT = "2199df0a55953a0d2469d59ab2f23a8bef8eb314"
EXPECTED_SHA256SUMS = "cf055707b8dc43a86b6444d9b411f38d5f8c5fdbf6167e5eb1ee99910f2b12b9"
SOURCE_COMMIT = "1f7cf65ff9c58968bc3b605ee571db928d1ac37a"
SOURCE_REPOSITORY = "/home/zhong/Documents/piano-ddsp-pytorch"
ACE2_RELEASE = (
    "/home/zhong/Documents/piano-ddsp-pytorch/artifacts/hf-upload/"
    "model-suite-v1.0.0"
)
DEFAULT_TARGET = Path("models/piano_ddsp/model-suite-v1.0.0")
REQUIRED_FILES = (
    "model-suite.json",
    "SHA256SUMS",
    "README.md",
    "VALIDATION.md",
    "LICENSE",
    "LICENSES/Apache-2.0.txt",
    "LICENSES/CC-BY-NC-SA-4.0.txt",
    "THIRD_PARTY_NOTICES.md",
    "ddsp_piano_paper_ir.onnx",
    "ddsp_piano_paper_ir.json",
    "ddsp_piano_film_fdn.onnx",
    "ddsp_piano_film_fdn.json",
    "ddsp_piano_calibrated_ir.onnx",
    "ddsp_piano_calibrated_ir.json",
    "ddsp_piano_calibrated_film_ir.onnx",
    "ddsp_piano_calibrated_film_ir.json",
)
PINNED_HASHES = {
    "SHA256SUMS": EXPECTED_SHA256SUMS,
    "model-suite.json": "d8c03fd8ae3ee29088f71683d05dc0f3770a61d4ab4efd510503cf8286c3c455",
    "README.md": "4bb39437737cbe514ff3ad6087bb20cc0a33a07a2d956969e0ee71709cb07962",
    "VALIDATION.md": "42ed34773dd560c864d596d9182b7e4d750bf554aca442ab0dfdab1a45a763f5",
    "LICENSE": "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4",
    "LICENSES/Apache-2.0.txt": "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4",
    "LICENSES/CC-BY-NC-SA-4.0.txt": "7d41f85a2d305eff78241dd95a41262ee0c62ccc5e7627fa220abe91265587ae",
    "THIRD_PARTY_NOTICES.md": "3ac2fd930872bd390332f491beacb3036267629e371faf6cd653d5952348b11b",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_sha256s(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        digest, name = line.split(maxsplit=1)
        name = name.lstrip("*")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError(f"Invalid SHA256 entry: {line!r}")
        result[name] = digest
    return result


def build_opener(proxy: str | None) -> urllib.request.OpenerDirector:
    handlers: list[urllib.request.BaseHandler] = []
    if proxy:
        handlers.append(urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
    return urllib.request.build_opener(*handlers)


def request_headers(token: str | None) -> dict[str, str]:
    headers = {"User-Agent": "case3-piano-ddsp-downloader/1.0"}
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
    repository_path = urllib.parse.quote(repository, safe="/")
    revision_path = urllib.parse.quote(revision, safe="")
    url = f"https://huggingface.co/api/models/{repository_path}/revision/{revision_path}"
    request = urllib.request.Request(url, headers=request_headers(token))
    with opener.open(request, timeout=timeout) as response:
        payload = json.load(response)
    commit = str(payload.get("sha", ""))
    if repository == REPOSITORY and revision == RELEASE and commit != EXPECTED_COMMIT:
        raise RuntimeError(
            f"HF tag {RELEASE} resolved to {commit or 'no commit'}, expected {EXPECTED_COMMIT}"
        )
    if len(commit) != 40:
        raise RuntimeError(f"HF revision returned an invalid commit: {commit!r}")
    return commit


def hf_url(repository: str, commit: str, name: str) -> str:
    return (
        f"https://huggingface.co/{urllib.parse.quote(repository, safe='/')}/resolve/"
        f"{urllib.parse.quote(commit, safe='')}/{urllib.parse.quote(name, safe='/')}"
    )


def download_http(
    opener: urllib.request.OpenerDirector,
    url: str,
    target: Path,
    token: str | None,
    timeout: float,
) -> None:
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


def download_ace2(host: str, remote_root: str, name: str, target: Path) -> str:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    partial.unlink(missing_ok=True)
    remote = f"{host}:{remote_root.rstrip('/')}/{name}"
    result = subprocess.run(["scp", remote, str(partial)], check=False)
    provenance = f"{remote_root.rstrip('/')}/{name}"
    if result.returncode != 0 and name.startswith("LICENSES/"):
        git_path = name.replace("'", "'\\''")
        with partial.open("wb") as output:
            result = subprocess.run(
                [
                    "ssh",
                    host,
                    f"git -C '{SOURCE_REPOSITORY}' show '{SOURCE_COMMIT}:{git_path}'",
                ],
                stdout=output,
                check=False,
            )
        provenance = f"git:{SOURCE_REPOSITORY}@{SOURCE_COMMIT}:{name}"
    if result.returncode != 0:
        partial.unlink(missing_ok=True)
        raise RuntimeError(f"scp failed for {remote} with exit code {result.returncode}")
    os.replace(partial, target)
    return provenance


def verified(path: Path, expected: str | None) -> bool:
    return bool(path.is_file() and expected and sha256_file(path) == expected)


def expected_sizes(manifest: dict[str, object]) -> dict[str, int]:
    result: dict[str, int] = {}
    models = manifest.get("models", {})
    if not isinstance(models, dict):
        return result
    for model in models.values():
        if not isinstance(model, dict):
            continue
        assets = model.get("assets", {})
        if not isinstance(assets, dict):
            continue
        for name, raw in assets.items():
            if isinstance(raw, dict) and isinstance(raw.get("bytes"), int):
                result[str(name)] = int(raw["bytes"])
    return result


def validate_release(
    target: Path,
    hashes: dict[str, str],
    names: Iterable[str],
) -> None:
    manifest = json.loads((target / "model-suite.json").read_text(encoding="utf-8"))
    if manifest.get("release") != RELEASE:
        raise ValueError(f"Unexpected release in model-suite.json: {manifest.get('release')!r}")
    sizes = expected_sizes(manifest)
    for name in names:
        path = target / name
        if not path.is_file():
            raise FileNotFoundError(path)
        expected_hash = PINNED_HASHES.get(name, hashes.get(name))
        if expected_hash is None:
            raise ValueError(f"SHA256SUMS does not contain {name}")
        actual = sha256_file(path)
        if actual != expected_hash:
            raise ValueError(f"SHA256 mismatch for {path}: {actual} != {expected_hash}")
        if name in sizes and path.stat().st_size != sizes[name]:
            raise ValueError(
                f"Size mismatch for {path}: {path.stat().st_size} != {sizes[name]}"
            )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=REPOSITORY)
    parser.add_argument("--revision", default=RELEASE)
    parser.add_argument("--target-dir", type=Path, default=DEFAULT_TARGET)
    parser.add_argument("--source", choices=("hf", "ace2"), default="hf")
    parser.add_argument("--ace2-host", default="ace2")
    parser.add_argument("--ace2-root", default=ACE2_RELEASE)
    parser.add_argument("--proxy")
    parser.add_argument("--token", default=os.environ.get("HF_TOKEN"))
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=2)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.timeout <= 0 or args.retries < 0:
        raise ValueError("timeout must be positive and retries must be non-negative")
    target = args.target_dir.resolve()
    target.mkdir(parents=True, exist_ok=True)
    opener = build_opener(args.proxy)
    commit = EXPECTED_COMMIT
    if args.source == "hf":
        commit = resolve_revision(
            opener, args.repository, args.revision, args.token, args.timeout
        )

    transfer_sources: dict[str, str] = {}

    def transfer(name: str) -> None:
        destination = target / name
        if args.source == "hf":
            download_http(
                opener,
                hf_url(args.repository, commit, name),
                destination,
                args.token,
                args.timeout,
            )
            transfer_sources[name] = f"hf:{args.repository}@{commit}/{name}"
        else:
            transfer_sources[name] = download_ace2(
                args.ace2_host, args.ace2_root, name, destination
            )

    for bootstrap in ("SHA256SUMS", "model-suite.json"):
        if not verified(target / bootstrap, PINNED_HASHES[bootstrap]):
            transfer(bootstrap)
    hashes = parse_sha256s((target / "SHA256SUMS").read_text(encoding="utf-8"))

    diagnostics: list[dict[str, object]] = []
    for name in REQUIRED_FILES:
        destination = target / name
        expected = PINNED_HASHES.get(name, hashes.get(name))
        if verified(destination, expected):
            diagnostics.append(
                {"file": name, "status": "verified-skip", "source": "existing"}
            )
            continue
        last_error: BaseException | None = None
        for attempt in range(args.retries + 1):
            destination.unlink(missing_ok=True)
            try:
                transfer(name)
                if not verified(destination, expected):
                    actual = sha256_file(destination) if destination.is_file() else "missing"
                    raise ValueError(f"checksum {actual}, expected {expected}")
                diagnostics.append(
                    {
                        "file": name,
                        "status": "downloaded",
                        "attempt": attempt + 1,
                        "source": transfer_sources[name],
                    }
                )
                last_error = None
                break
            except (OSError, RuntimeError, ValueError, urllib.error.URLError) as exc:
                last_error = exc
                diagnostics.append(
                    {"file": name, "status": "retry", "attempt": attempt + 1, "error": str(exc)}
                )
                if attempt < args.retries:
                    time.sleep(min(2**attempt, 4))
        if last_error is not None:
            raise RuntimeError(f"Failed to download {name}: {last_error}") from last_error

    validate_release(target, hashes, REQUIRED_FILES)
    report = {
        "schema": "piano-ddsp-download/v1",
        "source": args.source,
        "repository": args.repository if args.source == "hf" else None,
        "revision": args.revision if args.source == "hf" else None,
        "resolved_commit": commit if args.source == "hf" else None,
        "ace2_root": args.ace2_root if args.source == "ace2" else None,
        "files": diagnostics,
    }
    report_path = target / "download-report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Verified {len(REQUIRED_FILES)} Piano-DDSP release files in {target}")


if __name__ == "__main__":
    main()
