#!/usr/bin/env python3
"""Bounded board acceptance client for Ascend Edge Chat release candidates."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys
import time
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


EXPECTED_SOURCE_REPOSITORY = "https://github.com/zhouxzh/Ascend310.git"

PROBES = (
    "请用一句话介绍你自己。",
    "用中文解释什么是边缘计算。",
    "给出三个保护本地服务密钥的做法。",
    "用一句话说明串行推理的取舍。",
    "请写一条简短的部署前检查清单。",
    "什么是 SHA-256 校验？",
    "用中文回答：为什么模型版本需要固定？",
    "请简要解释流式输出。",
    "给出一个局域网安全提醒。",
    "请用一句话总结本次对话。",
)


class AcceptanceError(RuntimeError):
    pass


def _post(url: str, token: str, payload: dict[str, Any], timeout: float) -> tuple[int, bytes]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read()
    except HTTPError as exc:
        return int(exc.code), exc.read()
    except URLError as exc:
        raise AcceptanceError("gateway request failed: %s" % exc) from exc


def _get(url: str, token: str, timeout: float) -> tuple[int, bytes]:
    request = Request(
        url,
        method="GET",
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            return int(response.status), response.read()
    except HTTPError as exc:
        return int(exc.code), exc.read()
    except URLError as exc:
        raise AcceptanceError("gateway request failed: %s" % exc) from exc


def _json_response(status: int, body: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AcceptanceError("%s returned non-JSON UTF-8 response" % label) from exc
    if not isinstance(value, dict):
        raise AcceptanceError("%s returned a non-object JSON response" % label)
    return value


def _check_gateway_contract(base_url: str, token: str, timeout: float) -> dict[str, Any]:
    models_url = base_url.rstrip("/") + "/models"
    status, body = _get(models_url, token, timeout)
    value = _json_response(status, body, "models")
    if status != 200:
        raise AcceptanceError("models returned HTTP %d: %s" % (status, value))
    models = value.get("data")
    if not isinstance(models, list) or len(models) != 1:
        raise AcceptanceError("models endpoint did not expose exactly one release model")
    if not isinstance(models[0], dict) or models[0].get("id") != "ascend-edge-chat":
        raise AcceptanceError("models endpoint leaked or changed the public model id")

    denied_status, denied_body = _get(models_url, "invalid-release-token-000000", timeout)
    denied = _json_response(denied_status, denied_body, "unauthenticated models")
    if denied_status != 401 or not isinstance(denied.get("error"), dict):
        raise AcceptanceError("models endpoint did not reject an invalid Bearer token")

    bad_status, bad_body = _post(
        base_url.rstrip("/") + "/chat/completions",
        token,
        {
            "model": "unavailable-model",
            "messages": [{"role": "user", "content": "contract check"}],
            "temperature": 0,
            "top_p": 1,
            "max_tokens": 2,
        },
        timeout,
    )
    bad = _json_response(bad_status, bad_body, "invalid model")
    if bad_status != 404 or not isinstance(bad.get("error"), dict):
        raise AcceptanceError("gateway did not reject an unavailable public model")
    return {"models": "passed", "bearer_rejection": "passed", "model_rejection": "passed"}


def _check_ui(ui_url: str, timeout: float) -> dict[str, Any]:
    config_url = ui_url.rstrip("/") + "/api/config"
    request = Request(config_url, method="GET", headers={"Accept": "application/json"})
    try:
        with urlopen(request, timeout=timeout) as response:
            status, body = int(response.status), response.read()
    except (HTTPError, URLError) as exc:
        raise AcceptanceError("UI config request failed: %s" % exc) from exc
    value = _json_response(status, body, "UI config")
    if status != 200 or value.get("model") != "ascend-edge-chat" or not value.get("warning"):
        raise AcceptanceError("UI config does not expose the fixed release boundary")

    request = Request(
        ui_url.rstrip("/") + "/api/chat",
        data=json.dumps({"message": "请用一句中文回复：界面链路已验证。", "stream": False}, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            status, body = int(response.status), response.read()
    except HTTPError as exc:
        status, body = int(exc.code), exc.read()
    except URLError as exc:
        raise AcceptanceError("UI chat request failed: %s" % exc) from exc
    reply = _json_response(status, body, "UI chat")
    if (
        status != 200
        or reply.get("model") != "ascend-edge-chat"
        or not isinstance(reply.get("message"), str)
        or not reply["message"].strip()
        or "\ufffd" in reply["message"]
    ):
        raise AcceptanceError("UI chat did not complete through the release gateway")
    return {"config": "passed", "chat": "passed"}


def _completion(url: str, token: str, prompt: str, max_tokens: int, timeout: float) -> dict[str, Any]:
    started = time.monotonic()
    status, body = _post(
        url,
        token,
        {
            "model": "ascend-edge-chat",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "top_p": 1,
            "max_tokens": max_tokens,
        },
        timeout,
    )
    elapsed_ms = (time.monotonic() - started) * 1000.0
    value = _json_response(status, body, "completion")
    if status != 200:
        raise AcceptanceError("completion returned HTTP %d: %s" % (status, value))
    if value.get("model") != "ascend-edge-chat":
        raise AcceptanceError("gateway leaked or changed the public model id")
    choices = value.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        raise AcceptanceError("completion choices do not match the release contract")
    message = choices[0].get("message")
    text = message.get("content") if isinstance(message, dict) else None
    if not isinstance(text, str) or not text.strip() or "\ufffd" in text:
        raise AcceptanceError("completion has empty or invalid UTF-8 text")
    finish = choices[0].get("finish_reason")
    if finish not in {"stop", "length"}:
        raise AcceptanceError("unexpected completion finish reason: %r" % finish)
    usage = value.get("usage") if isinstance(value.get("usage"), dict) else {}
    completion_tokens = usage.get("completion_tokens")
    if not isinstance(completion_tokens, int) or not 1 <= completion_tokens <= max_tokens:
        raise AcceptanceError("completion token count is outside the requested bound")
    return {
        "max_tokens": max_tokens,
        "elapsed_ms": round(elapsed_ms, 3),
        "finish_reason": finish,
        "completion_tokens": completion_tokens,
        "text": text,
    }


def _stream(
    url: str,
    token: str,
    timeout: float,
    *,
    prompt: str = "请用中文回复一个简短问候。",
    max_tokens: int = 16,
) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(
            {
                "model": "ascend-edge-chat",
                "stream": True,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "top_p": 1,
                "max_tokens": max_tokens,
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": "Bearer " + token,
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
    )
    started = time.monotonic()
    first_ms: float | None = None
    deltas: list[str] = []
    terminal = False
    terminal_usage: dict[str, Any] | None = None
    finish_reason: str | None = None
    try:
        with urlopen(request, timeout=timeout) as response:
            if int(response.status) != 200:
                raise AcceptanceError("SSE request returned HTTP %d" % int(response.status))
            for raw_line in response:
                line = raw_line.decode("utf-8").strip()
                if not line.startswith("data:"):
                    continue
                encoded = line[5:].strip()
                if encoded == "[DONE]":
                    terminal = True
                    break
                event = json.loads(encoded)
                if not isinstance(event, dict):
                    raise AcceptanceError("SSE event is not a JSON object")
                if event.get("model") != "ascend-edge-chat":
                    raise AcceptanceError("SSE leaked or changed the public model id")
                if isinstance(event.get("error"), dict):
                    raise AcceptanceError("SSE returned an error event")
                choices = event.get("choices", [])
                if not choices:
                    continue
                if not isinstance(choices[0], dict):
                    raise AcceptanceError("SSE choice is malformed")
                delta = choices[0].get("delta", {})
                text = delta.get("content") if isinstance(delta, dict) else None
                if isinstance(text, str) and text:
                    if "\ufffd" in text:
                        raise AcceptanceError("SSE contains invalid UTF-8 text")
                    if first_ms is None:
                        first_ms = (time.monotonic() - started) * 1000.0
                    deltas.append(text)
                reason = choices[0].get("finish_reason")
                if reason is not None:
                    if reason not in {"stop", "length"}:
                        raise AcceptanceError("unexpected SSE finish reason: %r" % reason)
                    finish_reason = reason
                    usage = event.get("usage")
                    if isinstance(usage, dict):
                        terminal_usage = usage
    except (HTTPError, URLError) as exc:
        raise AcceptanceError("SSE request failed: %s" % exc) from exc
    if not terminal or not deltas:
        raise AcceptanceError("SSE stream ended without text and terminal marker")
    if finish_reason is None or terminal_usage is None:
        raise AcceptanceError("SSE stream ended without terminal usage")
    completion_tokens = terminal_usage.get("completion_tokens")
    if not isinstance(completion_tokens, int) or not 1 <= completion_tokens <= max_tokens:
        raise AcceptanceError("SSE completion token count is outside the requested bound")
    return {
        "max_tokens": max_tokens,
        "first_token_ms": round(first_ms or 0.0, 3),
        "total_ms": round((time.monotonic() - started) * 1000.0, 3),
        "completion_tokens": completion_tokens,
        "finish_reason": finish_reason,
        "text": "".join(deltas),
    }


def _percentile(values: Iterable[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * fraction)))
    return round(ordered[index], 3)


def _run_command(argv: list[str], *, cwd: Path | None = None) -> dict[str, Any]:
    """Capture a provenance command without hiding its exit status."""

    try:
        completed = subprocess.run(
            argv,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            check=False,
            timeout=15,
        )
        return {"argv": argv, "returncode": completed.returncode, "output": completed.stdout}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"argv": argv, "returncode": None, "output": str(exc)}


def _source_provenance(root: Path) -> dict[str, Any]:
    remote = _run_command(["git", "config", "--get", "remote.origin.url"], cwd=root)
    commit = _run_command(["git", "rev-parse", "HEAD"], cwd=root)
    status = _run_command(["git", "status", "--short"], cwd=root)
    public_head = _run_command(["git", "ls-remote", "origin", "refs/heads/main"], cwd=root)
    remote_url = str(remote.get("output", "")).strip()
    # Never persist credentials embedded in an author-local Git remote.
    remote_url = re.sub(r"//[^/@]+@", "//", remote_url)
    if remote_url == "git@github.com:zhouxzh/Ascend310.git":
        remote_url = EXPECTED_SOURCE_REPOSITORY
    commit_value = str(commit.get("output", "")).strip()
    status_value = str(status.get("output", ""))
    public_match = re.match(
        r"([0-9a-fA-F]{40})\s+refs/heads/main(?:\s|$)",
        str(public_head.get("output", "")).strip(),
    )
    public_commit = public_match.group(1).lower() if public_match else None
    return {
        "repository": remote_url or None,
        "commit": commit_value if re.fullmatch(r"[0-9a-fA-F]{40}", commit_value) else None,
        "public_main_commit": public_commit,
        "working_tree_clean": not bool(status_value.strip()) and status.get("returncode") == 0,
        "status": status_value,
        "commands": [remote, commit, status, public_head],
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _static_kv_model_provenance(root: Path, source_kind: str) -> dict[str, Any]:
    """Collect the public HF artifact tuple for the 8T static-KV backend."""

    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from ascend_edge_chat.artifacts.static_release_store import (  # noqa: PLC0415
        DEFAULT_STATIC_RELEASE_LOCK,
        StaticReleaseError,
        verify_static_release_artifacts,
    )

    data_dir_raw = os.environ.get("ASCEND_EDGE_CHAT_DATA_DIR", "").strip()
    if not data_dir_raw:
        raise ValueError("ASCEND_EDGE_CHAT_DATA_DIR is required for acl-static-kv")
    data_dir = Path(data_dir_raw).expanduser()
    try:
        verified = verify_static_release_artifacts(data_dir, lock_path=DEFAULT_STATIC_RELEASE_LOCK)
    except StaticReleaseError as exc:
        raise ValueError(str(exc)) from exc
    if source_kind != "hf":
        raise ValueError("the 8T formal static-KV release accepts source_kind=hf only")
    return {
        "source_kind": source_kind,
        "repository": verified["source_repository"],
        "revision": verified["source_revision"],
        "base_model_repository": "Qwen/Qwen2.5-0.5B-Instruct",
        "base_model_revision": "7ae557604adf67be50417f59c2c2f167def9a775",
        "license": "Apache-2.0",
        "mirror": "https://hf-mirror.com",
        "target": verified["target"],
        "profile_status": "formal",
        "admission_eligible": True,
        "admission_reason": "Public artifacts, target SoC, and runtime descriptor are locked; human language-quality review remains independent.",
        "artifacts": [
            {
                **item,
                "expected_bytes": item["bytes"],
                "observed_bytes": item["bytes"],
                "observed_sha256": item["sha256"],
                "verified_by": "ascend-edge-chat acl verify",
            }
            for item in verified["artifacts"]
        ],
        "runtime_locks": verified.get("runtime_locks", {}),
        "data_directory": str(data_dir),
    }


def _model_provenance(root: Path, source_kind: str) -> dict[str, Any]:
    if os.environ.get("ASCEND_EDGE_CHAT_BACKEND") == "acl-static-kv":
        return _static_kv_model_provenance(root, source_kind)
    config_path = root / "configs" / "release-model.json"
    document = json.loads(config_path.read_text(encoding="utf-8"))
    profiles = document.get("profiles") if isinstance(document, dict) else None
    if not isinstance(profiles, list) or len(profiles) != 1 or not isinstance(profiles[0], dict):
        raise ValueError("release model config must contain exactly one profile")
    profile = profiles[0]
    revision = profile.get("revision")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
        raise ValueError("release model revision must be a full 40-hex commit")
    if source_kind not in {"hf", "offline-bundle"}:
        raise ValueError("model source must be hf or offline-bundle")
    data_dir_raw = os.environ.get("ASCEND_EDGE_CHAT_DATA_DIR", "").strip()
    data_dir = Path(data_dir_raw).expanduser() if data_dir_raw else None
    cache_dir = profile.get("cache_dir")
    artifacts: list[dict[str, Any]] = []
    for artifact in profile.get("artifacts", []):
        if not isinstance(artifact, dict):
            continue
        filename = artifact.get("filename")
        path = data_dir / cache_dir / filename if data_dir and isinstance(cache_dir, str) and isinstance(filename, str) else None
        artifacts.append(
            {
                "name": artifact.get("name"),
                "filename": filename,
                "expected_bytes": artifact.get("expected_bytes"),
                "sha256": artifact.get("sha256"),
                "observed_bytes": path.stat().st_size if path and path.is_file() else None,
                "observed_sha256": _sha256_file(path) if path and path.is_file() else None,
                "verified_by": "ascend-edge-chat verify",
            }
        )
    return {
        "source_kind": source_kind,
        "repository": profile.get("repository"),
        "revision": revision,
        "tokenizer_revision": profile.get("tokenizer_revision"),
        "license": profile.get("license"),
        "mirror": profile.get("mirror"),
        "profile_status": profile.get("status"),
        "admission_eligible": (
            profile.get("admission", {}).get("eligible")
            if isinstance(profile.get("admission"), dict)
            else None
        ),
        "admission_reason": (
            profile.get("admission", {}).get("reason")
            if isinstance(profile.get("admission"), dict)
            else None
        ),
        "artifacts": artifacts,
        "data_directory": str(data_dir) if data_dir else None,
    }


def _environment_provenance() -> dict[str, Any]:
    packages: dict[str, str | None] = {}
    try:
        import importlib.metadata

        package_names = (
            ("numpy", "tokenizers", "fastapi", "httpx", "pydantic", "uvicorn")
            if os.environ.get("ASCEND_EDGE_CHAT_BACKEND") == "acl-static-kv"
            else ("mindspore", "mindnlp", "fastapi", "httpx", "pydantic", "uvicorn")
        )
        for name in package_names:
            try:
                packages[name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                packages[name] = None
    except ImportError:
        packages = {}
    return {
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "python_abi": getattr(sys.implementation, "cache_tag", None),
        "machine": platform.machine(),
        "conda_env": os.environ.get("CONDA_DEFAULT_ENV"),
        "python_no_user_site": os.environ.get("PYTHONNOUSERSITE"),
        "packages": packages,
        "npu_smi": _run_command(["npu-smi", "info"]),
    }


def _provenance(root: Path, source_kind: str) -> dict[str, Any]:
    source = _source_provenance(root)
    model = _model_provenance(root, source_kind)
    environment = _environment_provenance()
    status = "passed"
    reasons: list[str] = []
    if source.get("repository") != EXPECTED_SOURCE_REPOSITORY:
        status = "blocked"
        reasons.append("source repository is not the public locked GitHub repository")
    if not source.get("commit"):
        status = "blocked"
        reasons.append("source commit is not a full 40-hex SHA")
    if source.get("public_main_commit") != source.get("commit"):
        status = "blocked"
        reasons.append("source commit is not the current public main commit")
    if not source.get("working_tree_clean"):
        status = "blocked"
        reasons.append("source working tree is not clean")
    if not model.get("revision"):
        status = "blocked"
        reasons.append("model revision is missing")
    # This repository is a tutorial. A technical-preview profile may complete
    # machine acceptance while Chinese semantic review remains pending. Record
    # that scope explicitly instead of using admission as a startup or report
    # gate; source and artifact identity remain mandatory for reproducibility.
    if model.get("profile_status") == "admitted" and model.get("admission_eligible") is True:
        model["evidence_label"] = "formal-admitted"
    elif model.get("profile_status") == "formal":
        model["evidence_label"] = "formal-machine-release"
    else:
        model["evidence_label"] = "technical-preview"
    for artifact in model.get("artifacts", []):
        if artifact.get("observed_bytes") != artifact.get("expected_bytes"):
            status = "blocked"
            reasons.append("artifact byte count mismatch: %s" % artifact.get("name"))
        if artifact.get("observed_sha256") != artifact.get("sha256"):
            status = "blocked"
            reasons.append("artifact SHA-256 mismatch: %s" % artifact.get("name"))
    if model.get("source_kind") == "offline-bundle":
        model["evidence_label"] = "offline-bundle"
    return {
        "status": status,
        "reasons": reasons,
        "source": source,
        "model": model,
        "environment": environment,
        "commands": source.get("commands", []) + [environment["npu_smi"]],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="run bounded Ascend Edge Chat API acceptance")
    parser.add_argument("--gateway", default="http://127.0.0.1:7861/v1")
    parser.add_argument("--ui", default="http://127.0.0.1:7865")
    parser.add_argument("--token", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=330.0)
    parser.add_argument("--performance-samples", type=int, default=30)
    parser.add_argument("--json-interval", type=float, default=1.0)
    parser.add_argument("--sse-interval", type=float, default=2.0)
    parser.add_argument(
        "--model-source",
        choices=("hf", "offline-bundle"),
        default=os.environ.get("ASCEND_EDGE_CHAT_MODEL_SOURCE", ""),
        help="how the locked model entered the board data directory",
    )
    args = parser.parse_args()
    if not args.model_source:
        parser.error("--model-source or ASCEND_EDGE_CHAT_MODEL_SOURCE is required")
    if not 1 <= args.performance_samples <= 30:
        parser.error("--performance-samples must be between 1 and 30")
    if not 0.0 <= args.json_interval <= 10.0:
        parser.error("--json-interval must be between 0 and 10 seconds")
    if not 0.0 <= args.sse_interval <= 10.0:
        parser.error("--sse-interval must be between 0 and 10 seconds")

    root = Path(__file__).resolve().parents[2]
    try:
        provenance = _provenance(root, args.model_source)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        parser.error("could not collect release provenance: %s" % exc)
    if provenance["status"] != "passed":
        parser.error("release provenance is blocked: %s" % "; ".join(provenance["reasons"]))

    url = args.gateway.rstrip("/") + "/chat/completions"
    report: dict[str, Any] = {
        "model": "ascend-edge-chat",
        "backend": os.environ.get("ASCEND_EDGE_CHAT_BACKEND", "mindspore"),
        "gateway": args.gateway,
        "provenance": provenance,
        "started_at_epoch": time.time(),
        "request_intervals": {
            "json_seconds": args.json_interval,
            "sse_seconds": args.sse_interval,
        },
        "length_checks": [],
        "stability": [],
        "chinese_probe": [],
        "performance": [],
    }
    report["gateway_contract"] = _check_gateway_contract(args.gateway, args.token, args.timeout)
    time.sleep(args.json_interval)
    report["ui_contract"] = _check_ui(args.ui, args.timeout)
    time.sleep(args.json_interval)
    for length in (8, 16, 32, 64):
        report["length_checks"].append(_completion(url, args.token, "请用中文回答：什么是边缘推理？", length, args.timeout))
        time.sleep(args.json_interval)
    report["sse"] = _stream(url, args.token, args.timeout)
    time.sleep(args.sse_interval)
    for _ in range(10):
        report["stability"].append(_completion(url, args.token, "请回答：测试通过。", 2, args.timeout))
        time.sleep(args.json_interval)
    for prompt in PROBES:
        row = _completion(url, args.token, prompt, 16, args.timeout)
        row["prompt"] = prompt
        report["chinese_probe"].append(row)
        time.sleep(args.json_interval)
    for _ in range(2):
        _stream(url, args.token, args.timeout, prompt="请回答：预热。", max_tokens=2)
        time.sleep(args.sse_interval)
    for _ in range(args.performance_samples):
        report["performance"].append(
            _stream(url, args.token, args.timeout, prompt="请回答：性能测量。", max_tokens=2)
        )
        time.sleep(args.sse_interval)
    first_timings = [float(item["first_token_ms"]) for item in report["performance"]]
    timings = [float(item["total_ms"]) for item in report["performance"]]
    tokens = [int(item["completion_tokens"]) for item in report["performance"]]
    report["performance_summary"] = {
        "samples": len(timings),
        "first_token_ms_p50": _percentile(first_timings, 0.50),
        "first_token_ms_p95": _percentile(first_timings, 0.95),
        "total_ms_p50": _percentile(timings, 0.50),
        "total_ms_p95": _percentile(timings, 0.95),
        "tokens_per_second_p50": round(statistics.median(token / (elapsed / 1000.0) for token, elapsed in zip(tokens, timings)), 3),
        "tokens_per_second_p95": _percentile(
            (token / (elapsed / 1000.0) for token, elapsed in zip(tokens, timings)), 0.95
        ),
    }
    report["machine_status"] = "passed"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
