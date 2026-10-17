#!/usr/bin/env python3
"""Bounded board acceptance client for Ascend Edge Chat release candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import statistics
import time
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


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


def main() -> int:
    parser = argparse.ArgumentParser(description="run bounded Ascend Edge Chat API acceptance")
    parser.add_argument("--gateway", default="http://127.0.0.1:7861/v1")
    parser.add_argument("--ui", default="http://127.0.0.1:7865")
    parser.add_argument("--token", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=330.0)
    parser.add_argument("--performance-samples", type=int, default=30)
    args = parser.parse_args()
    if not 1 <= args.performance_samples <= 30:
        parser.error("--performance-samples must be between 1 and 30")

    url = args.gateway.rstrip("/") + "/chat/completions"
    report: dict[str, Any] = {
        "model": "ascend-edge-chat",
        "gateway": args.gateway,
        "started_at_epoch": time.time(),
        "length_checks": [],
        "stability": [],
        "chinese_probe": [],
        "performance": [],
    }
    report["gateway_contract"] = _check_gateway_contract(args.gateway, args.token, args.timeout)
    report["ui_contract"] = _check_ui(args.ui, args.timeout)
    for length in (8, 16, 32, 64):
        report["length_checks"].append(_completion(url, args.token, "请用中文回答：什么是边缘推理？", length, args.timeout))
    report["sse"] = _stream(url, args.token, args.timeout)
    for _ in range(10):
        report["stability"].append(_completion(url, args.token, "请回答：测试通过。", 2, args.timeout))
    for prompt in PROBES:
        row = _completion(url, args.token, prompt, 16, args.timeout)
        row["prompt"] = prompt
        report["chinese_probe"].append(row)
    for _ in range(2):
        _stream(url, args.token, args.timeout, prompt="请回答：预热。", max_tokens=2)
    for _ in range(args.performance_samples):
        report["performance"].append(
            _stream(url, args.token, args.timeout, prompt="请回答：性能测量。", max_tokens=2)
        )
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
