#!/usr/bin/env python3
"""Bounded, read-only acceptance checks for one MindSpore chat profile.

The candidate service must already be running. This module uses only the
Python standard library: it does not install packages, launch a worker, or
manage another process. The shell wrapper defaults to ``--dry-run``.

For a new dual-board campaign, select the board explicitly and associate both
G0/G1 reports, for example::

    python scripts/mindspore_chat_acceptance.py \
        --profile qwen1.5-0.5b-mindspore --board 8t --soc Ascend310B4 \
        --environment-report reports/environment.json \
        --artifact-report reports/artifact.json --execute

Legacy invocations may omit these options; they retain the primary-board
health checks but do not claim an evidence gate that was not requested.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import http.client
import json
import math
import os
from pathlib import Path
import platform
import re
import socket
import stat
import subprocess
import sys
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
REPORT_ROOT = REPO_DIR / "reports" / "mindspore-chat"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8090
DEFAULT_MODEL = "case9-active"
MAX_CONTEXT_TOKENS = 1024
MAX_GENERATION_TOKENS = 64
MAX_REQUEST_BYTES = 256 * 1024
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_TIMEOUT_SECONDS = 600.0
MAX_LOOPS = 100
MAX_PROBES = 20
MAX_PROMPT_CHARS = 4000
OVER_CONTEXT_TERM_COUNT = 2048
ABORT_HEALTH_WAIT_SECONDS = 8.0
# The abort probe must observe that the HTTP handler accepted the request
# before it closes the socket.  Keep the optional body read short: response
# headers are sufficient evidence of an accepted SSE stream, while a first
# event is useful additional evidence when generation starts quickly.
ABORT_EVENT_GRACE_SECONDS = 0.5
ABORT_HEADER_BYTES = 32 * 1024
ABORT_OBSERVATION_BYTES = 64 * 1024
PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,63}$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{2,63}$")
SOC_RE = re.compile(r"^(?:ascend)?310b[0-9a-z]+$", re.IGNORECASE)
ACTIVATABLE_STATUSES = frozenset({"experimental_dirty_base", "admitted"})
MAX_EVIDENCE_BYTES = 4 * 1024 * 1024

# These aliases mirror the two board records in the checked-in registry.  An
# alias is only accepted after it is matched against the selected Profile's
# declared ``board_targets``; it can never redirect a campaign to an
# undeclared device.
BOARD_ALIASES: Mapping[str, Mapping[str, str]] = {
    # 192.168.1.90 is the current address of the existing B4/8T board.
    # The .11.14 and .178 addresses are retained only as selectors for
    # archived evidence; all normalized output uses the current host.
    "8t": {"host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
    "board8t": {"host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
    "192.168.1.90": {"host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
    "192.168.11.14": {"host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
    "192.168.8.178": {"host": "192.168.1.90", "soc": "Ascend310B4", "tier": "8T"},
    "20t": {"host": "192.168.1.95", "soc": "Ascend310B1", "tier": "20T"},
    "board20t": {"host": "192.168.1.95", "soc": "Ascend310B1", "tier": "20T"},
}

REQUIRED_MACHINE_GATES: Tuple[str, ...] = (
    "health",
    "models",
    "json",
    "sse",
    "long_output",
    "stability",
    "performance",
    "errors",
    "protocol",
)

DEFAULT_PROBES: Tuple[Dict[str, str], ...] = (
    {"id": "identity", "language": "zh", "prompt": "\u4f60\u662f\u8c01\uff1f\u8bf7\u7528\u4e00\u53e5\u8bdd\u56de\u7b54\u3002"},
    {"id": "addition", "language": "zh", "prompt": "\u8bf7\u8ba1\u7b97 12 \u52a0 30 \u7b49\u4e8e\u591a\u5c11\u3002"},
    {"id": "summary", "language": "zh", "prompt": "\u8bf7\u7528\u4e00\u53e5\u8bdd\u89e3\u91ca\u4ec0\u4e48\u662f\u4eba\u5de5\u667a\u80fd\u3002"},
    {"id": "steps", "language": "zh", "prompt": "\u8bf7\u5217\u51fa\u6ce1\u4e00\u676f\u8336\u7684\u4e09\u4e2a\u6b65\u9aa4\u3002"},
    {"id": "safety", "language": "zh", "prompt": "\u5199\u4e00\u53e5\u63d0\u9192\u7528\u6237\u4fdd\u62a4\u5bc6\u7801\u5b89\u5168\u7684\u8bdd\u3002"},
    {"id": "translation", "language": "en", "prompt": "Translate 'hello' into Chinese and return only the translation."},
    {"id": "comparison", "language": "en", "prompt": "In one sentence, explain the difference between a CPU and an NPU."},
    {"id": "weather", "language": "en", "prompt": "How should you answer honestly when you do not know the real-time weather?"},
    {"id": "short", "language": "en", "prompt": "Describe spring in no more than ten words."},
    {"id": "instruction", "language": "en", "prompt": "Reply with 'received' and keep it concise."},
)


class AcceptanceError(ValueError):
    """Raised when an option or observed response violates the contract."""


class Options:
    """Validated immutable-ish options container.

    This intentionally avoids ``dataclasses.dataclass`` so the helper can be
    loaded by lightweight ``spec_from_file_location`` test harnesses that do
    not pre-register the module in ``sys.modules``.
    """

    __slots__ = (
        "profile", "host", "port", "output", "timeout", "long_budgets",
        "stability_loops", "stability_max_tokens", "perf_warmup", "perf_loops",
        "perf_max_tokens", "probe_max_tokens", "probe_file", "run_id", "execute",
        "quality", "snapshots", "abort_max_tokens", "abort_health_wait_seconds",
        "registry", "board", "soc", "tier", "target_explicit",
        "environment_report", "artifact_report", "require_evidence",
    )

    def __init__(
        self, profile: str, host: str, port: int, output: Path, timeout: float,
        long_budgets: Tuple[int, ...], stability_loops: int, stability_max_tokens: int,
        perf_warmup: int, perf_loops: int, perf_max_tokens: int, probe_max_tokens: int,
        probe_file: Optional[Path], run_id: str, execute: bool, quality: bool,
        snapshots: bool, abort_max_tokens: int = 1,
        abort_health_wait_seconds: float = ABORT_HEALTH_WAIT_SECONDS,
        registry: Optional[Path] = None,
        board: str = "",
        soc: str = "",
        tier: str = "",
        target_explicit: bool = False,
        environment_report: Optional[Path] = None,
        artifact_report: Optional[Path] = None,
        require_evidence: bool = False,
    ) -> None:
        self.profile = profile
        self.host = host
        self.port = port
        self.output = output
        self.timeout = timeout
        self.long_budgets = long_budgets
        self.stability_loops = stability_loops
        self.stability_max_tokens = stability_max_tokens
        self.perf_warmup = perf_warmup
        self.perf_loops = perf_loops
        self.perf_max_tokens = perf_max_tokens
        self.probe_max_tokens = probe_max_tokens
        self.probe_file = probe_file
        self.run_id = run_id
        self.execute = execute
        self.quality = quality
        self.snapshots = snapshots
        self.abort_max_tokens = abort_max_tokens
        self.abort_health_wait_seconds = abort_health_wait_seconds
        self.registry = registry
        self.board = board
        self.soc = soc
        self.tier = tier
        self.target_explicit = bool(target_explicit)
        self.environment_report = environment_report
        self.artifact_report = artifact_report
        self.require_evidence = bool(require_evidence)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def percentile(values: Iterable[float], fraction: float) -> Optional[float]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    index = min(len(ordered) - 1, max(0, int(math.floor(float(fraction) * len(ordered)))))
    return round(ordered[index], 3)


def summarize(values: Iterable[float], *, unit: str = "ms") -> Dict[str, Optional[float]]:
    """Summarize a bounded timing/rate series with explicit unit names."""

    suffix = str(unit).strip() or "value"
    keys = ("min_" + suffix, "p50_" + suffix, "p95_" + suffix, "max_" + suffix)
    ordered = [float(value) for value in values]
    if not ordered:
        return {"count": 0, **{key: None for key in keys}}
    return {
        "count": len(ordered),
        keys[0]: round(min(ordered), 3),
        keys[1]: percentile(ordered, 0.50),
        keys[2]: percentile(ordered, 0.95),
        keys[3]: round(max(ordered), 3),
    }


def _is_loopback_host(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1"}


def _validate_port(port: Any) -> int:
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise AcceptanceError("port must be an integer between 1 and 65535")
    return int(port)


def _reject_symlink_components(path: Path) -> None:
    """Reject existing symlink components before writing a report."""

    current = Path(path.anchor) if path.anchor else Path()
    try:
        parts = path.relative_to(Path(path.anchor)).parts if path.anchor else path.parts
    except ValueError as exc:
        raise AcceptanceError("invalid report path") from exc
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise AcceptanceError("report path contains a symlink: %s" % current)


def safe_report_path(value: Path, root: Path = REPORT_ROOT) -> Path:
    """Resolve a report path and require it to stay below the report root."""

    root = root.expanduser().resolve(strict=False)
    candidate = value.expanduser()
    if not candidate.is_absolute():
        candidate = REPO_DIR / candidate
    # Inspect the lexical path before resolving it. Otherwise a symlink inside
    # the report root could resolve to a second in-root location and evade the
    # component check below.
    _reject_symlink_components(candidate.absolute())
    candidate = candidate.resolve(strict=False)
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise AcceptanceError("output must be below %s" % root) from exc
    if not relative.parts:
        raise AcceptanceError("output must name a report directory below %s" % root)
    _reject_symlink_components(candidate)
    if candidate.exists() and candidate.is_symlink():
        raise AcceptanceError("output directory must not be a symlink")
    return candidate


def _registry_path(value: Optional[Any] = None) -> Path:
    """Resolve the registry selected for this campaign.

    ``CASE9_MODEL_PROFILES`` is retained for board launch wrappers, while the
    explicit CLI option takes precedence.  The profile loader performs the
    final regular-file and symlink checks; keeping this helper deterministic
    ensures every metadata lookup in one campaign uses the same file.
    """

    raw = value
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raw = os.environ.get("CASE9_MODEL_PROFILES")
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        raw = REPO_DIR / "configs" / "chat_model_profiles.json"
    try:
        path = Path(raw).expanduser()
    except (TypeError, ValueError) as exc:
        raise AcceptanceError("registry must be a valid path") from exc
    if not path.is_absolute():
        path = REPO_DIR / path
    # Do not resolve away a symlink before the profile loader sees it.  This
    # also gives a useful acceptance error for a mistyped/unsafe board path.
    try:
        _reject_symlink_components(path.absolute())
    except AcceptanceError:
        raise
    return path


def load_profile_metadata(profile_id: str, registry_path: Optional[Any] = None) -> Dict[str, Any]:
    """Load one validated registry profile without importing ML runtimes."""

    if not isinstance(profile_id, str) or not PROFILE_ID_RE.fullmatch(profile_id):
        raise AcceptanceError("invalid profile id")
    if str(REPO_DIR) not in sys.path:
        sys.path.insert(0, str(REPO_DIR))
    try:
        from case9_model_profiles import get_profile, profile_to_public_dict

        profile = get_profile(profile_id, path=_registry_path(registry_path))
        return profile_to_public_dict(profile)
    except Exception as exc:
        raise AcceptanceError("could not load profile %s: %s" % (profile_id, exc)) from exc


def _soc_key(value: Any) -> str:
    """Return a case-insensitive lookup key for an Ascend 310B SoC."""

    raw = str(value or "").strip().lower()
    if raw.startswith("ascend"):
        raw = raw[len("ascend"):]
    match = re.search(r"310b[0-9a-z]+", raw)
    return match.group(0) if match else raw


def _canonical_soc(value: Any) -> str:
    """Normalize common ``310B4``/``Ascend310B4`` spellings."""

    raw = str(value or "").strip()
    if not raw or not SOC_RE.fullmatch(raw):
        return ""
    key = _soc_key(raw)
    return "Ascend" + key.upper()


def _canonical_board_host(value: Any) -> str:
    """Normalize known historical addresses to the board's current host."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    alias = BOARD_ALIASES.get(raw.lower())
    if alias is not None:
        return str(alias["host"])
    return raw


def _profile_targets(metadata: Mapping[str, Any]) -> List[Dict[str, str]]:
    """Return normalized board targets, retaining the primary-board fallback."""

    raw = metadata.get("board_targets")
    if isinstance(raw, Mapping):
        raw = [raw]
    targets: List[Dict[str, str]] = []
    if isinstance(raw, (list, tuple)):
        for item in raw:
            if not isinstance(item, Mapping):
                continue
            host = str(item.get("host", "")).strip()
            soc = _canonical_soc(item.get("soc"))
            tier = str(item.get("tier", "")).strip()
            if host and soc and tier:
                targets.append({"host": host, "soc": soc, "tier": tier})
    if targets:
        return targets
    host = str(metadata.get("board_host", "")).strip()
    soc = _canonical_soc(metadata.get("board_soc"))
    tier = str(metadata.get("board_tier", "")).strip()
    if host and soc and tier:
        return [{"host": host, "soc": soc, "tier": tier}]
    return []


def _resolve_target(
    metadata: Mapping[str, Any],
    board: Optional[Any] = None,
    soc: Optional[Any] = None,
) -> Dict[str, Any]:
    """Resolve a board/SoC selection against the Profile's declared targets.

    Existing callers may omit both options; in that case the Profile's
    primary target is selected and ``explicit`` remains false.  Supplying
    either selector turns on strict per-board validation and rejects a target
    which is not declared by the selected Profile.
    """

    targets = _profile_targets(metadata)
    if not targets:
        raise AcceptanceError("profile does not declare a usable board target")
    board_raw = str(board or "").strip()
    soc_raw = str(soc or "").strip()
    explicit = bool(board_raw or soc_raw)
    if soc_raw and not SOC_RE.fullmatch(soc_raw):
        raise AcceptanceError("--soc must be Ascend310B4/Ascend310B1 (or 310B4/310B1)")
    requested_soc = _canonical_soc(soc_raw) if soc_raw else ""

    alias: Optional[Mapping[str, str]] = None
    if board_raw:
        alias = BOARD_ALIASES.get(board_raw.lower())
    board_candidates = targets
    if board_raw and alias is None:
        # A literal board host, SoC, or tier is accepted only if the matching
        # target is already declared in the registry.
        board_candidates = [
            item for item in targets
            if item["host"].lower() == board_raw.lower()
            or _soc_key(item["soc"]) == _soc_key(board_raw)
            or item["tier"].lower() == board_raw.lower()
        ]
        if not board_candidates:
            raise AcceptanceError("--board %s is not declared by profile" % board_raw)
    elif alias is not None:
        board_candidates = [
            item for item in targets
            if _soc_key(item["soc"]) == _soc_key(alias["soc"])
            and item["tier"].lower() == alias["tier"].lower()
        ]
        if not board_candidates:
            raise AcceptanceError("--board %s does not match a profile board target" % board_raw)

    if requested_soc:
        board_candidates = [
            item for item in board_candidates
            if _soc_key(item["soc"]) == _soc_key(requested_soc)
        ]
        if not board_candidates:
            raise AcceptanceError("--soc %s is not a target for profile %s" % (soc_raw, metadata.get("id", "")))

    # A valid registry cannot contain duplicate SoCs, but choose the first
    # deterministic target if a legacy fixture happens to repeat one.
    target = dict(board_candidates[0] if board_candidates else targets[0])
    # The registry may retain an archived address in a historical validation
    # row.  Normalize only known B4 aliases so a current campaign cannot
    # accidentally report the old IP as its execution target.
    target["host"] = _canonical_board_host(target.get("host"))
    if _soc_key(target.get("soc")) == _soc_key("Ascend310B4"):
        target["host"] = str(BOARD_ALIASES["board8t"]["host"])
    if alias is not None and board_raw.lower() in BOARD_ALIASES:
        # Keep the alias visible for provenance, while the normalized host
        # above remains the current board address.
        target["alias"] = board_raw.lower()
    target["explicit"] = explicit
    target["selection"] = "explicit" if explicit else "profile_primary"
    return target


def _validation_for_target(metadata: Mapping[str, Any], soc: str) -> Optional[Mapping[str, Any]]:
    validation = metadata.get("validation")
    if not isinstance(validation, Mapping):
        return None
    expected = _soc_key(soc)
    for key, item in validation.items():
        if _soc_key(key) == expected and isinstance(item, Mapping):
            return item
    return None


def _safe_evidence_path(value: Any, name: str) -> Path:
    """Validate a report input without following symlink components."""

    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise AcceptanceError("%s must be a non-empty path" % name)
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = REPO_DIR / path
    lexical = path.absolute()
    _reject_symlink_components(lexical)
    if path.exists() and (path.is_symlink() or not path.is_file()):
        raise AcceptanceError("%s must be a regular file" % name)
    return path


def _report_soc(document: Mapping[str, Any]) -> str:
    """Extract a SoC from known environment/artifact report locations."""

    candidates: List[Any] = []
    for key in ("board_soc", "npu_model", "target_soc", "soc"):
        candidates.append(document.get(key))
    board = document.get("board")
    if isinstance(board, Mapping):
        candidates.extend(board.get(key) for key in ("soc", "board_soc", "npu_model"))
    checks = document.get("checks")
    if isinstance(checks, Mapping):
        profile_check = checks.get("profile")
        if isinstance(profile_check, Mapping):
            candidates.extend(profile_check.get(key) for key in ("board_soc", "soc", "npu_model"))
        npu_check = checks.get("npu")
        if isinstance(npu_check, Mapping):
            candidates.extend(npu_check.get(key) for key in ("chip", "soc", "npu_model"))
    normalized_values: List[str] = []
    for value in candidates:
        normalized = _canonical_soc(value)
        if normalized:
            normalized_values.append(normalized)
            continue
        # npu-smi commonly reports a compound value such as ``310B4 / 8T``.
        match = re.search(r"(?:Ascend)?(310B[0-9A-Za-z]+)", str(value or ""), re.IGNORECASE)
        if match:
            parsed = _canonical_soc(match.group(1))
            if parsed:
                normalized_values.append(parsed)
    unique = set(normalized_values)
    # A report containing B4 and B1 claims is ambiguous; do not let field
    # ordering decide which board receives the evidence.
    return next(iter(unique)) if len(unique) == 1 else ""


def _report_tier(document: Mapping[str, Any]) -> str:
    """Extract an optional compute-tier label from known report locations."""

    candidates: List[Any] = [document.get("board_tier"), document.get("tier")]
    board = document.get("board")
    if isinstance(board, Mapping):
        candidates.append(board.get("tier"))
    checks = document.get("checks")
    if isinstance(checks, Mapping):
        profile_check = checks.get("profile")
        if isinstance(profile_check, Mapping):
            candidates.append(profile_check.get("board_tier"))
            candidates.append(profile_check.get("tier"))
    normalized_values: List[str] = []
    for value in candidates:
        text = str(value or "").strip()
        if re.fullmatch(r"[0-9]+T", text, re.IGNORECASE):
            normalized_values.append(text.upper())
    unique = set(normalized_values)
    return next(iter(unique)) if len(unique) == 1 else ""


def _report_host(document: Mapping[str, Any]) -> str:
    """Extract and normalize a board host from an evidence report."""

    candidates: List[Any] = []
    for key in ("board_host", "target_host", "host"):
        candidates.append(document.get(key))
    board = document.get("board")
    if isinstance(board, Mapping):
        candidates.extend(board.get(key) for key in ("host", "board_host", "target_host"))
    checks = document.get("checks")
    if isinstance(checks, Mapping):
        profile_check = checks.get("profile")
        if isinstance(profile_check, Mapping):
            candidates.extend(profile_check.get(key) for key in ("host", "board_host", "target_host"))
        for section_name in ("environment", "device", "hardware"):
            section = checks.get(section_name)
            if isinstance(section, Mapping):
                candidates.extend(section.get(key) for key in ("host", "board_host", "target_host"))
    normalized_values: List[str] = []
    for value in candidates:
        raw = str(value or "").strip()
        if not raw:
            continue
        # Accept a URL or a compact diagnostic string, but only normalize
        # known board aliases.  Unknown hosts remain visible and fail the
        # strict equality check below.
        match = re.search(r"(?<![0-9])(?:\d{1,3}\.){3}\d{1,3}(?![0-9])", raw)
        normalized_values.append(_canonical_board_host(match.group(0) if match else raw))
    unique = {item for item in normalized_values if item}
    # Old addresses for the same physical B4 board normalize to .90 and are
    # therefore compatible; different explicit hosts are not.
    return next(iter(unique)) if len(unique) == 1 else ""


def _report_profile_id(document: Mapping[str, Any]) -> str:
    for key in ("profile_id", "profile"):
        value = document.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, Mapping):
            nested = value.get("id") or value.get("profile_id")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    checks = document.get("checks")
    if isinstance(checks, Mapping):
        profile = checks.get("profile")
        if isinstance(profile, Mapping):
            value = profile.get("id") or profile.get("profile_id")
            if isinstance(value, str):
                return value.strip()
    return ""


def _read_evidence_report(
    path: Optional[Path],
    *,
    kind: str,
    profile: str,
    soc: str,
    required: bool,
    tier: str = "",
    host: str = "",
) -> Dict[str, Any]:
    """Read and validate one environment/artifact evidence report."""

    if path is not None and not isinstance(path, Path):
        path = Path(path)
    result: Dict[str, Any] = {
        "kind": kind,
        "path": str(path) if path is not None else None,
        "required": bool(required),
        "status": "not_requested" if path is None and not required else "failed",
        "ok": False if required else True,
        "checks": {},
    }
    if path is None:
        result["reason"] = "report path was not supplied"
        return result
    try:
        lexical = path.absolute()
        _reject_symlink_components(lexical)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            raise AcceptanceError("report is not a regular file")
        if info.st_size > MAX_EVIDENCE_BYTES:
            raise AcceptanceError("report exceeds 4 MiB")
        text = path.read_text(encoding="utf-8")
        document = json.loads(text)
        if not isinstance(document, Mapping):
            raise AcceptanceError("report root must be an object")
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError, AcceptanceError) as exc:
        result["reason"] = "%s: %s" % (type(exc).__name__, exc)
        return result

    report_profile = _report_profile_id(document)
    report_soc = _report_soc(document)
    report_tier = _report_tier(document)
    report_host = _report_host(document)
    if kind == "environment":
        success = document.get("ok") is True or document.get("status") in {"passed", "verified"}
        failures = document.get("failures")
        no_failures = not failures if isinstance(failures, list) else failures in (None, [])
    else:
        success = document.get("artifact_verified") is True and document.get("status") in {"passed", "verified"}
        errors = document.get("errors")
        no_failures = not errors if isinstance(errors, list) else errors in (None, [])
    checks = {
        "report_success": bool(success),
        "report_has_no_failures": bool(no_failures),
        "profile_matches": report_profile == profile,
        "soc_present": bool(report_soc),
        "soc_matches": bool(report_soc) and _soc_key(report_soc) == _soc_key(soc),
        # Host identity is mandatory whenever a strict target is selected.
        # Historical aliases normalize to the current address before compare.
        "host_present": bool(report_host) if host else True,
        "host_matches": (
            not host
            or (bool(report_host) and _canonical_board_host(report_host) == _canonical_board_host(host))
        ),
        "tier_matches_when_present": not report_tier or report_tier == str(tier or "").upper(),
    }
    result["checks"] = checks
    result["report_profile"] = report_profile or None
    result["report_soc"] = report_soc or None
    result["report_host"] = report_host or None
    result["report_tier"] = report_tier or None
    result["ok"] = all(checks.values())
    result["status"] = "verified" if result["ok"] else "failed"
    if not result["ok"]:
        result["reason"] = "report identity or success checks failed"
    return result


def _evidence_reports(
    options: "Options", metadata: Mapping[str, Any]
) -> Tuple[bool, Dict[str, Any]]:
    # Direct unit callers may construct Options with the legacy positional
    # signature, leaving board/soc empty.  Resolve the same deterministic
    # primary target used by validate_options before associating reports.
    try:
        target = _resolve_target(
            metadata,
            options.board if options.board else None,
            options.soc if options.soc else None,
        )
        target_host = str(target.get("host") or "")
        target_soc = str(target["soc"])
        target_tier = str(target.get("tier") or "")
    except AcceptanceError:
        target_host = _canonical_board_host(options.board or "")
        target_soc = str(options.soc or "")
        target_tier = str(options.tier or "")
    required = bool(options.require_evidence or options.target_explicit)
    environment = _read_evidence_report(
        options.environment_report,
        kind="environment",
        profile=options.profile,
        soc=target_soc,
        tier=target_tier,
        host=target_host,
        required=required,
    )
    artifact = _read_evidence_report(
        options.artifact_report,
        kind="artifact",
        profile=options.profile,
        soc=target_soc,
        tier=target_tier,
        host=target_host,
        required=required,
    )
    # If either path was supplied, both reports are required.  This prevents a
    # single successful artifact hash from disguising a missing environment
    # preflight (or vice versa).
    if options.environment_report is not None or options.artifact_report is not None:
        required = True
        environment["required"] = True
        artifact["required"] = True
        for item in (environment, artifact):
            if item.get("path") is None:
                item["status"] = "failed"
                item["ok"] = False
                item["reason"] = "required report path was not supplied"
    passed = (not required) or (environment.get("ok") is True and artifact.get("ok") is True)
    return passed, {
        "required": required,
        "status": ("passed" if passed else "failed") if required else "not_requested",
        "target": {"profile": options.profile, "host": target_host, "soc": target_soc, "tier": target_tier},
        "environment": environment,
        "artifact": artifact,
    }


def _parse_budgets(value: Any) -> Tuple[int, ...]:
    if isinstance(value, (list, tuple)):
        raw_values = list(value)
    else:
        raw_values = [part.strip() for part in str(value).split(",") if part.strip()]
    if not raw_values:
        raise AcceptanceError("long-budgets must contain at least one value")
    result: List[int] = []
    for raw in raw_values:
        if isinstance(raw, bool):
            raise AcceptanceError("long-budgets values must be integers")
        try:
            number = int(raw)
        except (TypeError, ValueError) as exc:
            raise AcceptanceError("long-budgets must be comma-separated integers") from exc
        if not 1 <= number <= MAX_GENERATION_TOKENS:
            raise AcceptanceError("long-budgets values must be between 1 and %d" % MAX_GENERATION_TOKENS)
        result.append(number)
    return tuple(dict.fromkeys(result))


def validate_options(args: argparse.Namespace) -> Options:
    """Validate all bounds before a network request or filesystem write."""

    profile = str(getattr(args, "profile", ""))
    registry = _registry_path(getattr(args, "registry", None))
    metadata = load_profile_metadata(profile, registry)
    board_selector = getattr(args, "board", None)
    soc_selector = getattr(args, "soc", None)
    target = _resolve_target(metadata, board_selector, soc_selector)
    target_soc = str(target["soc"])
    target_validation = _validation_for_target(metadata, target_soc)
    candidate_kind = str(metadata.get("candidate_kind", "native_mindspore")).strip().lower()
    if bool(metadata.get("conditional")) or candidate_kind == "conditional":
        raise AcceptanceError("profile %s is conditional; acceptance is not run" % profile)
    # A selector explicitly requests per-board evidence.  A missing validation
    # entry is therefore a hard stop instead of silently falling back to the
    # aggregate profile status.  Legacy calls without --board/--soc retain
    # their historical primary-board fallback.
    if target["explicit"] and target_validation is None:
        raise AcceptanceError("profile %s has no validation entry for %s" % (profile, target_soc))
    target_status = str(
        target_validation.get("status") if isinstance(target_validation, Mapping) else metadata.get("status", "")
    ).strip().lower()
    if target_status in {"blocked", "not-run"}:
        raise AcceptanceError(
            "profile %s is %s for %s; acceptance is not run" % (profile, target_status, target_soc)
        )
    if target_status not in ACTIVATABLE_STATUSES:
        raise AcceptanceError(
            "profile %s is not activatable for %s (status=%s)" % (profile, target_soc, target_status or "missing")
        )
    # Preserve the legacy aggregate behavior when no board selector is given:
    # a globally blocked/not-run Profile remains unavailable.  An explicit
    # selector may opt into a separately validated SoC entry (for example a
    # profile blocked on B1 but experimentally usable on B4).  Without this
    # guard, a schema-v2 profile with aggregate ``not-run`` and a stale or
    # prematurely populated primary-board validation row could be accepted by
    # a legacy invocation that omits ``--board``/``--soc``.
    aggregate_status = str(metadata.get("status", "")).strip().lower()
    if aggregate_status in {"blocked", "not-run"} and (
        not target["explicit"] or target_status not in ACTIVATABLE_STATUSES
    ):
        raise AcceptanceError(
            "profile %s is %s; acceptance is not run" % (profile, aggregate_status)
        )
    host = str(getattr(args, "host", DEFAULT_HOST))
    if not _is_loopback_host(host):
        raise AcceptanceError("acceptance target must be loopback-only")
    port = _validate_port(getattr(args, "port", DEFAULT_PORT))
    timeout = float(getattr(args, "timeout", 30.0))
    if not math.isfinite(timeout) or not 0.001 <= timeout <= MAX_TIMEOUT_SECONDS:
        raise AcceptanceError("timeout must be between 0.001 and 600 seconds")

    def bounded(name: str, minimum: int, maximum: int, default: Optional[int] = None) -> int:
        value = getattr(args, name, default)
        if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
            raise AcceptanceError("%s must be an integer between %d and %d" % (name, minimum, maximum))
        return int(value)

    stability_loops = bounded("stability_loops", 1, MAX_LOOPS)
    stability_max_tokens = bounded("stability_max_tokens", 1, MAX_GENERATION_TOKENS)
    perf_warmup = bounded("perf_warmup", 0, MAX_LOOPS)
    perf_loops = bounded("perf_loops", 1, MAX_LOOPS)
    perf_max_tokens = bounded("perf_max_tokens", 1, MAX_GENERATION_TOKENS)
    probe_max_tokens = bounded("probe_max_tokens", 1, MAX_GENERATION_TOKENS)
    abort_max_tokens = bounded("abort_max_tokens", 1, MAX_GENERATION_TOKENS, 1)
    abort_health_wait_seconds = float(
        getattr(args, "abort_health_wait_seconds", ABORT_HEALTH_WAIT_SECONDS)
    )
    if not math.isfinite(abort_health_wait_seconds) or not 0.0 <= abort_health_wait_seconds <= 120.0:
        raise AcceptanceError("abort_health_wait_seconds must be between 0 and 120 seconds")
    budgets = _parse_budgets(getattr(args, "long_budgets", "8,16,32,64"))
    run_id = str(getattr(args, "run_id", "") or utc_run_id())
    if not RUN_ID_RE.fullmatch(run_id):
        raise AcceptanceError("run-id contains unsafe characters")

    output_arg = getattr(args, "output", None)
    output = Path(output_arg) if output_arg else REPORT_ROOT / profile / run_id
    output = safe_report_path(output)

    environment_report_raw = getattr(args, "environment_report", None)
    artifact_report_raw = getattr(args, "artifact_report", None)
    environment_report = (
        _safe_evidence_path(environment_report_raw, "--environment-report")
        if environment_report_raw is not None else None
    )
    artifact_report = (
        _safe_evidence_path(artifact_report_raw, "--artifact-report")
        if artifact_report_raw is not None else None
    )
    require_evidence = bool(getattr(args, "require_evidence", False))
    if environment_report is not None or artifact_report is not None:
        require_evidence = True

    probe_file = getattr(args, "probe_file", None)
    if probe_file is not None:
        probe_file = Path(probe_file).expanduser()
        if not probe_file.is_file() or probe_file.is_symlink():
            raise AcceptanceError("probe-file must be a regular file")
        try:
            _reject_symlink_components(probe_file.absolute())
            probe_file.resolve(strict=True).relative_to(REPO_DIR.resolve(strict=True))
        except ValueError as exc:
            raise AcceptanceError("probe-file must be inside the repository") from exc

    return Options(
        profile=profile,
        host=host,
        port=port,
        output=output,
        timeout=timeout,
        long_budgets=budgets,
        stability_loops=stability_loops,
        stability_max_tokens=stability_max_tokens,
        perf_warmup=perf_warmup,
        perf_loops=perf_loops,
        perf_max_tokens=perf_max_tokens,
        probe_max_tokens=probe_max_tokens,
        probe_file=probe_file,
        run_id=run_id,
        execute=bool(getattr(args, "execute", False)),
        quality=not bool(getattr(args, "skip_quality", False)),
        snapshots=not bool(getattr(args, "skip_snapshots", False)),
        abort_max_tokens=abort_max_tokens,
        abort_health_wait_seconds=abort_health_wait_seconds,
        registry=registry,
        board=str(target["host"]),
        soc=target_soc,
        tier=str(target["tier"]),
        target_explicit=bool(target["explicit"]),
        environment_report=environment_report,
        artifact_report=artifact_report,
        require_evidence=require_evidence,
    )


def _json_request(host: str, port: int, path: str, payload: Optional[Mapping[str, Any]], timeout: float) -> Dict[str, Any]:
    """Issue one bounded HTTP request and return a serializable observation."""

    started = time.monotonic()
    connection: Optional[http.client.HTTPConnection] = None
    try:
        connection = http.client.HTTPConnection(host, port, timeout=timeout)
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            if len(body) > MAX_REQUEST_BYTES:
                raise AcceptanceError("request body exceeds 256 KiB")
            headers.update({"Content-Type": "application/json", "Content-Length": str(len(body))})
        connection.request("POST" if payload is not None else "GET", path, body=body, headers=headers)
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        too_large = len(raw) > MAX_RESPONSE_BYTES
        if too_large:
            raw = raw[:MAX_RESPONSE_BYTES]
        record: Dict[str, Any] = {
            "status": "ok" if response.status == 200 and not too_large else "error",
            "http_status": int(response.status),
            "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
            "content_type": response.getheader("Content-Type", ""),
        }
        if too_large:
            record["error"] = "response exceeded bounded report size"
            return record
        try:
            text = raw.decode("utf-8")
            record["utf8_valid"] = True
        except UnicodeDecodeError:
            text = raw.decode("utf-8", errors="replace")
            record["utf8_valid"] = False
        try:
            record["body"] = json.loads(text) if text else None
        except json.JSONDecodeError:
            record["body_text"] = text[:8192]
            record["error"] = "response was not JSON"
            record["status"] = "error"
        return record
    except (OSError, http.client.HTTPException, TimeoutError, AcceptanceError) as exc:
        return {
            "status": "error",
            "http_status": None,
            "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
            "error": "%s: %s" % (type(exc).__name__, exc),
        }
    finally:
        if connection is not None:
            connection.close()


def _completion_payload(model: str, prompt: str, max_tokens: int, stream: bool) -> Dict[str, Any]:
    return {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": int(max_tokens),
        "temperature": 0,
        "top_p": 1,
        "stream": bool(stream),
    }


def _extract_json_completion(record: Mapping[str, Any]) -> Dict[str, Any]:
    result = dict(record)
    body = record.get("body")
    choices = body.get("choices") if isinstance(body, Mapping) else None
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], Mapping) else {}
    message = choice.get("message") if isinstance(choice, Mapping) else {}
    text = message.get("content") if isinstance(message, Mapping) else None
    usage = body.get("usage") if isinstance(body, Mapping) else {}
    usage = usage if isinstance(usage, Mapping) else {}
    result["text"] = text if isinstance(text, str) else ""
    result["finish_reason"] = choice.get("finish_reason") if isinstance(choice, Mapping) else None
    result["prompt_tokens"] = usage.get("prompt_tokens")
    result["completion_tokens"] = usage.get("completion_tokens")
    result["utf8_valid"] = isinstance(text, str) and "\ufffd" not in text
    result["machine_valid"] = (
        result.get("status") == "ok"
        and isinstance(text, str)
        and bool(text.strip())
        and result["utf8_valid"]
        and result.get("finish_reason") in {"stop", "length"}
    )
    return result


def parse_sse_lines(lines: Iterable[str], started: Optional[float] = None) -> Dict[str, Any]:
    """Parse OpenAI SSE and flag repeated output fragments.

    Suffix deltas are the case9 contract. Cumulative prefix snapshots are
    normalized to their unseen suffix; a repeated prefix is flagged.
    """

    started = time.monotonic() if started is None else started
    assembled = ""
    deltas: List[str] = []
    finish_reason: Optional[str] = None
    usage: Mapping[str, Any] = {}
    done = False
    malformed = False
    duplicate = False
    cumulative_mode = False
    first_event_ms: Optional[float] = None
    event_buffer: List[str] = []

    def consume(data: str) -> None:
        nonlocal assembled, finish_reason, usage, done, malformed, duplicate, first_event_ms, cumulative_mode
        if data == "[DONE]":
            done = True
            return
        try:
            payload = json.loads(data)
        except (TypeError, ValueError):
            malformed = True
            return
        if not isinstance(payload, Mapping):
            malformed = True
            return
        choices = payload.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], Mapping) else {}
        delta = choice.get("delta") if isinstance(choice, Mapping) else {}
        content = delta.get("content") if isinstance(delta, Mapping) else None
        if isinstance(content, str) and content:
            if first_event_ms is None:
                first_event_ms = round((time.monotonic() - started) * 1000.0, 3)
            if assembled and content.startswith(assembled):
                # A cumulative snapshot is accepted once and reduced to its
                # unseen suffix. Mixing it with later suffix fragments makes
                # duplicate detection ambiguous, so fail closed thereafter.
                if len(content) > len(assembled):
                    cumulative_mode = True
                content = content[len(assembled):]
            elif assembled and cumulative_mode:
                duplicate = True
                content = ""
            elif assembled and content == assembled:
                duplicate = True
                content = ""
            assembled += content
            if content:
                deltas.append(content)
        reason = choice.get("finish_reason") if isinstance(choice, Mapping) else None
        if reason is not None:
            finish_reason = reason
        candidate_usage = payload.get("usage")
        if isinstance(candidate_usage, Mapping):
            usage = candidate_usage

    for raw_line in lines:
        decoded = raw_line.decode("utf-8", errors="replace") if isinstance(raw_line, bytes) else str(raw_line)
        # ``HTTPResponse.readline`` yields one physical line, while tests and
        # simple clients often provide a chunk containing several lines.
        physical_lines = decoded.splitlines(keepends=True) or [decoded]
        for physical in physical_lines:
            line = physical.rstrip("\r\n")
            if not line:
                if event_buffer:
                    consume("\n".join(event_buffer))
                    event_buffer = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                event_buffer.append(line[5:].lstrip())
            else:
                malformed = True
    if event_buffer:
        consume("\n".join(event_buffer))

    result: Dict[str, Any] = {
        "status": "ok" if done and not malformed else "error",
        "text": assembled,
        "deltas": deltas,
        "delta_count": len(deltas),
        "duplicate_delta": duplicate,
        "malformed": malformed,
        "done": done,
        "finish_reason": finish_reason,
        "completion_tokens": usage.get("completion_tokens") if isinstance(usage, Mapping) else None,
        "prompt_tokens": usage.get("prompt_tokens") if isinstance(usage, Mapping) else None,
        "first_event_ms": first_event_ms,
        "utf8_valid": "\ufffd" not in assembled,
    }
    result["machine_valid"] = (
        result["status"] == "ok"
        and bool(assembled.strip())
        and result["utf8_valid"]
        and not duplicate
        and finish_reason in {"stop", "length"}
    )
    return result


def _sse_request(host: str, port: int, payload: Mapping[str, Any], timeout: float) -> Dict[str, Any]:
    started = time.monotonic()
    connection: Optional[http.client.HTTPConnection] = None
    try:
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(raw) > MAX_REQUEST_BYTES:
            raise AcceptanceError("request body exceeds 256 KiB")
        connection = http.client.HTTPConnection(host, port, timeout=timeout)
        connection.request(
            "POST",
            "/v1/chat/completions",
            body=raw,
            headers={"Content-Type": "application/json", "Accept": "text/event-stream", "Content-Length": str(len(raw))},
        )
        response = connection.getresponse()
        if response.status != 200:
            body = response.read(min(MAX_RESPONSE_BYTES, 8192))
            return {
                "status": "error",
                "http_status": int(response.status),
                "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
                "error_body": body.decode("utf-8", errors="replace"),
            }
        parsed = parse_sse_lines(iter(response.readline, b""), started=started)
        parsed["http_status"] = int(response.status)
        parsed["elapsed_ms"] = round((time.monotonic() - started) * 1000.0, 3)
        return parsed
    except (OSError, http.client.HTTPException, TimeoutError, AcceptanceError) as exc:
        return {
            "status": "error",
            "http_status": None,
            "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
            "error": "%s: %s" % (type(exc).__name__, exc),
        }
    finally:
        if connection is not None:
            connection.close()


def _error_fields(record: Mapping[str, Any]) -> Dict[str, Any]:
    """Extract the structured OpenAI error fields from one HTTP observation."""

    body = record.get("body")
    error = body.get("error") if isinstance(body, Mapping) else None
    if not isinstance(error, Mapping):
        return {"code": None, "type": None, "message": None}
    return {
        "code": error.get("code"),
        "type": error.get("type"),
        "message": error.get("message"),
    }


def _mark_expected_error(
    record: Mapping[str, Any],
    *,
    case: str,
    expected_status: int,
    expected_code: str,
) -> Dict[str, Any]:
    """Annotate a deliberately invalid request with its contract result.

    Invalid-request probes are successful acceptance observations only when
    both the HTTP status and the structured OpenAI error code match.  A plain
    4xx response, HTML body, or missing code is recorded as a failure rather
    than silently accepted.
    """

    result = dict(record)
    observed = _error_fields(record)
    result["case"] = str(case)
    result["expected_http_status"] = int(expected_status)
    result["expected_error_code"] = str(expected_code)
    result["observed_error"] = observed
    result["contract_status"] = (
        "passed"
        if record.get("status") == "error"
        and record.get("http_status") == int(expected_status)
        and observed.get("code") == str(expected_code)
        else "failed"
    )
    return result


def _over_context_payload(model: str) -> Dict[str, Any]:
    """Build a deterministic token-heavy request below the body-size limit."""

    # Whitespace-separated terms keep the payload comfortably below 256 KiB
    # while producing more than the 1024-token context on the board tokenizer.
    terms = " ".join("token%04d" % number for number in range(OVER_CONTEXT_TERM_COUNT))
    return _completion_payload(model, terms, 1, False)


def _over_context_request(host: str, port: int, model: str, timeout: float) -> Dict[str, Any]:
    if not _is_loopback_host(host):
        raise AcceptanceError("protocol probes must remain loopback-only")
    port = _validate_port(port)
    payload = _over_context_payload(model)
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    result = _json_request(host, port, "/v1/chat/completions", payload, timeout)
    result["case"] = "over_context"
    result["request_bytes"] = len(body)
    result["declared_prompt_terms"] = OVER_CONTEXT_TERM_COUNT
    return _mark_expected_error(
        result,
        case="over_context",
        expected_status=400,
        expected_code="invalid_request_error",
    )


def _oversized_content_length_request(host: str, port: int, timeout: float) -> Dict[str, Any]:
    """Exercise the body-size guard without transmitting a large payload.

    The candidate service checks ``Content-Length`` before reading the body.
    Declaring one byte over the limit therefore tests the guard while keeping
    this read-only campaign from allocating or sending a 256 KiB request.
    """

    if not _is_loopback_host(host):
        raise AcceptanceError("protocol probes must remain loopback-only")
    port = _validate_port(port)
    declared_length = MAX_REQUEST_BYTES + 1
    connection: Optional[http.client.HTTPConnection] = None
    started = time.monotonic()
    try:
        connection = http.client.HTTPConnection(host, port, timeout=timeout)
        connection.putrequest(
            "POST",
            "/v1/chat/completions",
            skip_host=True,
            skip_accept_encoding=True,
        )
        connection.putheader("Host", host)
        connection.putheader("Content-Type", "application/json")
        connection.putheader("Content-Length", str(declared_length))
        connection.putheader("Connection", "close")
        connection.endheaders()
        response = connection.getresponse()
        raw = response.read(min(MAX_RESPONSE_BYTES, 64 * 1024))
        result: Dict[str, Any] = {
            "status": "ok" if response.status < 400 else "error",
            "http_status": int(response.status),
            "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
            "declared_content_length": declared_length,
            "transmitted_body_bytes": 0,
        }
        try:
            text = raw.decode("utf-8")
            result["utf8_valid"] = True
            result["body"] = json.loads(text) if text else None
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            result["utf8_valid"] = False
            result["body_text"] = raw.decode("utf-8", errors="replace")[:8192]
        return _mark_expected_error(
            result,
            case="oversized_request",
            expected_status=413,
            expected_code="request_too_large",
        )
    except (OSError, http.client.HTTPException, TimeoutError) as exc:
        return _mark_expected_error(
            {
                "status": "error",
                "http_status": None,
                "elapsed_ms": round((time.monotonic() - started) * 1000.0, 3),
                "declared_content_length": declared_length,
                "transmitted_body_bytes": 0,
                "error": "%s: %s" % (type(exc).__name__, exc),
            },
            case="oversized_request",
            expected_status=413,
            expected_code="request_too_large",
        )
    finally:
        if connection is not None:
            connection.close()


def _client_abort_sse(
    host: str,
    port: int,
    model: str,
    timeout: float,
    max_tokens: int,
) -> Dict[str, Any]:
    """Start an SSE request, observe acceptance, then close the client.

    An immediate close can report success even when the server rejected the
    request or never accepted the connection.  This probe therefore reads a
    bounded HTTP response header first.  A valid 200 SSE header, a first SSE
    event, or an explicit busy response is evidence that the request reached
    the handler; only then is the socket closed.  No response body is retained
    beyond the small bounded observation buffer.
    """

    if not _is_loopback_host(host):
        raise AcceptanceError("client-abort probe must remain loopback-only")
    port = _validate_port(port)
    if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= MAX_GENERATION_TOKENS:
        raise AcceptanceError("abort max_tokens must be between 1 and %d" % MAX_GENERATION_TOKENS)
    try:
        timeout_value = float(timeout)
    except (TypeError, ValueError) as exc:
        raise AcceptanceError("abort timeout must be a finite positive number") from exc
    if not math.isfinite(timeout_value) or timeout_value <= 0.0:
        raise AcceptanceError("abort timeout must be a finite positive number")
    body = json.dumps(
        _completion_payload(model, "Return one short greeting.", max_tokens, True),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    sock: Optional[socket.socket] = None
    observed = bytearray()
    result: Dict[str, Any] = {
        "status": "failed",
        "request_bytes": len(body),
        "http_status": None,
        "headers_received": False,
        "first_event_received": False,
        "busy_observed": False,
        "started_observed": False,
    }
    try:
        deadline = time.monotonic() + timeout_value
        sock = socket.create_connection((host, int(port)), timeout=timeout_value)
        if ":" in host and not host.startswith("["):
            host_header = "[%s]" % host
        else:
            host_header = host
        if int(port) != 80:
            host_header = "%s:%d" % (host_header, int(port))
        request = (
            "POST /v1/chat/completions HTTP/1.1\r\n"
            "Host: %s\r\n" % host_header
            + "Content-Type: application/json\r\n"
            + "Accept: text/event-stream\r\n"
            + "Content-Length: %d\r\n" % len(body)
            + "Connection: close\r\n\r\n"
        ).encode("ascii")
        sock.sendall(request + body)
        # Read through the response header delimiter, with a strict bound on
        # both time and bytes.  This is enough to distinguish an accepted SSE
        # handler from a connection that was merely opened.
        while b"\r\n\r\n" not in observed and len(observed) < ABORT_HEADER_BYTES:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise TimeoutError("timed out waiting for SSE response headers")
            sock.settimeout(remaining)
            chunk = sock.recv(min(4096, ABORT_HEADER_BYTES - len(observed)))
            if not chunk:
                break
            observed.extend(chunk)
        marker = b"\r\n\r\n"
        if marker not in observed:
            raise TimeoutError("SSE response headers were not received")
        header_bytes, _, body_bytes = bytes(observed).partition(marker)
        header_lines = header_bytes.split(b"\r\n")
        status_line = header_lines[0].decode("iso-8859-1", errors="replace") if header_lines else ""
        status_parts = status_line.split(None, 2)
        if len(status_parts) < 2 or not status_parts[1].isdigit():
            raise ValueError("SSE response status line is malformed")
        http_status = int(status_parts[1])
        result["http_status"] = http_status
        result["headers_received"] = True
        headers: Dict[str, str] = {}
        for line in header_lines[1:]:
            if b":" not in line:
                continue
            key, value = line.split(b":", 1)
            headers[key.decode("iso-8859-1", errors="replace").strip().lower()] = value.decode(
                "iso-8859-1", errors="replace"
            ).strip()
        content_type = headers.get("content-type", "")
        result["content_type"] = content_type

        def has_event(data: bytes) -> bool:
            # Require a complete SSE event delimiter, not merely a header or
            # an arbitrary occurrence of the word ``data``.
            return b"\n\n" in data and any(
                line.lstrip().startswith(b"data:") for line in data.splitlines()
            )

        body_observed = bytearray(body_bytes[:ABORT_OBSERVATION_BYTES])
        first_event = has_event(bytes(body_observed))

        def read_grace() -> None:
            """Read a tiny bounded body prefix for event/busy evidence."""

            nonlocal first_event
            grace_deadline = min(deadline, time.monotonic() + ABORT_EVENT_GRACE_SECONDS)
            while (
                not first_event
                and time.monotonic() < grace_deadline
                and len(body_observed) < ABORT_OBSERVATION_BYTES
            ):
                remaining = grace_deadline - time.monotonic()
                if remaining <= 0.0:
                    break
                sock.settimeout(remaining)
                try:
                    chunk = sock.recv(min(4096, ABORT_OBSERVATION_BYTES - len(body_observed)))
                except (socket.timeout, TimeoutError):
                    break
                if not chunk:
                    break
                body_observed.extend(chunk)
                first_event = has_event(bytes(body_observed))

        # Give a quickly starting model a small chance to emit its first event
        # for stronger evidence.  A valid SSE response header remains enough
        # to prove handler admission, so a slow first token is not a false
        # failure by itself.  For an error response, the same bounded read lets
        # us identify an explicit ``busy`` admission signal when its body is
        # sent after the headers.
        if http_status == 200 and "text/event-stream" in content_type.lower():
            read_grace()
        elif http_status in {409, 429, 503} and b"busy" not in bytes(body_observed).lower():
            read_grace()
        result["observation_bytes"] = len(body_observed)
        result["first_event_received"] = bool(first_event)

        # A busy response is an explicit admission signal even though it is
        # not a successful stream.  Keep only a bounded, lower-case marker
        # check; the full response is intentionally discarded.
        if http_status in {409, 429, 503}:
            result["busy_observed"] = b"busy" in bytes(body_observed).lower()
        valid_sse = http_status == 200 and "text/event-stream" in content_type.lower()
        # A complete first event is independently sufficient evidence that a
        # 200 handler started, even when a proxy stripped Content-Type.  A
        # valid SSE header with no event is also accepted because generation
        # may not have produced its first token within the short grace window.
        accepted = (
            http_status == 200 and (valid_sse or first_event)
        ) or bool(result["busy_observed"])
        result["started_observed"] = bool(accepted)
        result["observation"] = (
            "first_event" if first_event else "response_headers" if valid_sse else "busy_response" if result["busy_observed"] else "none"
        )
        result["status"] = "sent_and_closed" if accepted else "failed"
        if not accepted:
            result["error"] = "unexpected SSE response status or content type"
        result["expected"] = "response acceptance observed before client close"
        return result
    except (OSError, ValueError, TimeoutError) as exc:
        result["status"] = "failed"
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
        return result
    finally:
        if sock is not None:
            # Closing after the observation makes the server-side cancellation
            # path observable while preserving the evidence collected above.
            shutdown = getattr(sock, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            close = getattr(sock, "close", None)
            if callable(close):
                close()


def _health_after_abort(
    host: str,
    port: int,
    timeout: float,
    wait_seconds: float,
    profile: Optional[str] = None,
    registry_path: Optional[Any] = None,
    board: Optional[str] = None,
    soc: Optional[str] = None,
    tier: Optional[str] = None,
    strict_target: Optional[bool] = None,
) -> Dict[str, Any]:
    """Accept a live ready service or an explicit 503 fail-closed state."""

    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    attempts: List[Dict[str, Any]] = []
    while True:
        record = _json_request(host, port, "/health", None, min(timeout, 5.0))
        body = record.get("body")
        attempt = {
            "http_status": record.get("http_status"),
            "status": record.get("status"),
            "ready": body.get("ready") if isinstance(body, Mapping) else None,
            "healthy": body.get("healthy") if isinstance(body, Mapping) else None,
        }
        if profile is not None:
            identity_ok, identity_checks = _health_ok(
                record,
                profile,
                registry_path,
                board=board,
                soc=soc,
                tier=tier,
                strict_target=strict_target,
            )
            attempt["identity_checks"] = identity_checks
        else:
            identity_ok = True
        attempts.append(attempt)
        if (
            record.get("status") == "ok"
            and record.get("http_status") == 200
            and isinstance(body, Mapping)
            and body.get("ready") is True
            and body.get("healthy") is True
            and identity_ok
        ):
            return {"status": "healthy", "contract_status": "passed", "attempts": attempts}
        if (
            record.get("http_status") == 503
            and isinstance(body, Mapping)
            and body.get("ready") is False
        ):
            return {"status": "fail_closed", "contract_status": "passed", "attempts": attempts}
        if time.monotonic() >= deadline:
            return {"status": "unavailable", "contract_status": "failed", "attempts": attempts}
        time.sleep(min(0.25, max(0.01, deadline - time.monotonic())))


def _valid_json(record: Mapping[str, Any], budget: int) -> bool:
    completion = record.get("completion_tokens")
    return bool(
        record.get("machine_valid")
        and isinstance(completion, int)
        and not isinstance(completion, bool)
        and 1 <= completion <= budget
    )


def _valid_sse(record: Mapping[str, Any], budget: int) -> bool:
    completion = record.get("completion_tokens")
    return bool(
        record.get("machine_valid")
        and isinstance(completion, int)
        and not isinstance(completion, bool)
        and 1 <= completion <= budget
    )


def _required_machine_gates(options: Options, *, evidence_required: Optional[bool] = None) -> Tuple[str, ...]:
    """Return the gates that determine campaign status.

    Evidence is conditional for backwards compatibility with the original
    single-board helper.  New per-board/explicit-evidence campaigns include it
    as a hard gate, so a missing environment or artifact report cannot yield
    a misleading ``passed`` status.
    """

    required = bool(
        options.require_evidence
        or options.target_explicit
        or options.environment_report is not None
        or options.artifact_report is not None
        or (evidence_required is True)
    )
    return REQUIRED_MACHINE_GATES + (("evidence",) if required else ())


def _quality_summary(probe_results: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Return legacy totals plus machine-valid counts partitioned by language."""

    by_language: Dict[str, Dict[str, Any]] = {}
    languages = sorted({str(item.get("language", "zh")) for item in probe_results})
    for language in languages:
        items = [item for item in probe_results if str(item.get("language", "zh")) == language]
        valid_count = sum(1 for item in items if item.get("machine_valid"))
        by_language[language] = {
            "probe_count": len(items),
            "machine_valid_count": valid_count,
            "machine_valid_rate": round(valid_count / len(items), 3) if items else None,
            "human_review": "pending",
        }
    return {
        "probes": list(probe_results),
        "machine_valid_count": sum(1 for item in probe_results if item.get("machine_valid")),
        "by_language": by_language,
        "human_review": "pending",
    }


def _load_probes(path: Optional[Path]) -> List[Dict[str, str]]:
    if path is None:
        return [dict(item) for item in DEFAULT_PROBES]
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise AcceptanceError("could not read probe-file: %s" % exc) from exc
    if isinstance(document, Mapping):
        document = document.get("probes")
    if not isinstance(document, list) or not document or len(document) > MAX_PROBES:
        raise AcceptanceError("probe-file must contain 1-%d probes" % MAX_PROBES)
    probes: List[Dict[str, str]] = []
    for index, item in enumerate(document):
        if not isinstance(item, Mapping) or not isinstance(item.get("prompt"), str):
            raise AcceptanceError("probe %d is invalid" % index)
        prompt = str(item["prompt"])
        if not prompt or len(prompt) > MAX_PROMPT_CHARS:
            raise AcceptanceError("probe %d has an invalid prompt length" % index)
        probe_id = str(item.get("id", "probe-%02d" % (index + 1)))
        if not RUN_ID_RE.fullmatch(probe_id):
            raise AcceptanceError("probe %d has an unsafe id" % index)
        language = str(item.get("language", "zh")).strip().lower()
        if language not in {"zh", "en"}:
            raise AcceptanceError("probe %d has an unsupported language (expected zh or en)" % index)
        probes.append({"id": probe_id, "language": language, "prompt": prompt})
    return probes


def _command_output(command: Sequence[str], timeout: float = 5.0) -> Dict[str, Any]:
    try:
        result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=timeout, check=False)
        return {"status": "ok", "returncode": result.returncode, "output": result.stdout[-16000:]}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "unavailable", "error": "%s: %s" % (type(exc).__name__, exc)}


def _worker_snapshot(pid: Any, profile: str) -> Dict[str, Any]:
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        return {"status": "unavailable", "reason": "health did not provide a worker_pid"}
    proc = Path("/proc") / str(pid)
    if not proc.is_dir():
        return {"status": "unavailable", "reason": "proc entry is not available"}
    result: Dict[str, Any] = {"status": "ok", "pid": pid}
    try:
        status_text = (proc / "status").read_text(encoding="utf-8", errors="replace")
        for line in status_text.splitlines():
            if line.startswith("VmRSS:"):
                result["rss_kb"] = int(line.split()[1])
                break
        fd_dir = proc / "fd"
        result["fd_count"] = len(list(fd_dir.iterdir())) if fd_dir.is_dir() else None
        cmdline = (proc / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", errors="replace").strip()
        result["cmdline"] = cmdline[:1000]
        result["profile_in_cmdline"] = profile in cmdline
    except (OSError, ValueError) as exc:
        result["status"] = "unavailable"
        result["error"] = "%s: %s" % (type(exc).__name__, exc)
    return result


def system_snapshot(label: str, health: Optional[Mapping[str, Any]] = None, profile: str = "") -> Dict[str, Any]:
    snapshot: Dict[str, Any] = {
        "label": label,
        "recorded_at_utc": utc_now(),
        "hostname": platform.node(),
        "platform": platform.platform(),
        "python": sys.version.replace("\n", " "),
        "machine": platform.machine(),
    }
    if health is not None and isinstance(health.get("body"), Mapping):
        body = health["body"]
        snapshot["worker"] = _worker_snapshot(body.get("worker_pid"), profile)
        snapshot["npu_model"] = body.get("npu_model")
        snapshot["environment_fingerprint"] = body.get("environment_fingerprint")
    snapshot["npu_smi"] = _command_output(["npu-smi", "info"], timeout=5)
    return snapshot


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def _write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".part")
    temporary.write_text(value, encoding="utf-8")
    temporary.replace(path)


def _health_ok(
    record: Mapping[str, Any],
    profile: str,
    registry_path: Optional[Any] = None,
    board: Optional[str] = None,
    soc: Optional[str] = None,
    tier: Optional[str] = None,
    strict_target: Optional[bool] = None,
) -> Tuple[bool, Dict[str, Any]]:
    """Validate service health and its board-specific admission identity.

    ``board``/``soc`` are optional for compatibility with historical calls.
    Once either is supplied, all per-SoC admission and candidate-kind fields
    become mandatory.  This keeps old reports readable while ensuring a new
    dual-board campaign cannot pass on a healthy service running the wrong
    profile or SoC.
    """

    body = record.get("body")
    metadata: Dict[str, Any] = {}
    try:
        metadata = load_profile_metadata(profile, registry_path)
        target = _resolve_target(metadata, board, soc)
        expected_npu_model = str(target.get("soc") or "")
        expected_tier = str(target.get("tier") or "")
        validation = _validation_for_target(metadata, expected_npu_model)
        expected_admission = str(
            validation.get("status") if isinstance(validation, Mapping) else metadata.get("status", "")
        ).strip().lower()
        expected_kind = str(metadata.get("candidate_kind", "native_mindspore")).strip().lower()
        target_supported = True
    except AcceptanceError:
        expected_npu_model = ""
        expected_tier = ""
        expected_admission = ""
        expected_kind = ""
        target_supported = False
    strict = bool(strict_target) if strict_target is not None else bool(board or soc)
    observed_npu_model = None
    fingerprint = None
    if isinstance(body, Mapping):
        observed_npu_model = (
            body.get("npu_model")
            or body.get("board_soc")
            or body.get("target_soc")
            or body.get("soc")
        )
        fingerprint = body.get("environment_fingerprint")
        environment = body.get("environment")
        if fingerprint is None and isinstance(environment, Mapping):
            fingerprint = environment.get("fingerprint") or environment.get("environment_fingerprint")
    observed_admission = body.get("admission_status") if isinstance(body, Mapping) else None
    if not observed_admission and isinstance(body, Mapping):
        observed_admission = body.get("admission")
    observed_admission_soc = body.get("admission_soc") if isinstance(body, Mapping) else None
    if observed_admission_soc is None and isinstance(body, Mapping):
        observed_admission_soc = body.get("target_soc") or body.get("board_soc")
    observed_kind = body.get("candidate_kind") if isinstance(body, Mapping) else None
    if observed_kind is None and isinstance(body, Mapping):
        observed_kind = body.get("profile_candidate_kind")
    observed_conditional = body.get("conditional") if isinstance(body, Mapping) else None
    observed_tier = body.get("board_tier") if isinstance(body, Mapping) else None
    observed_host = body.get("board_host") if isinstance(body, Mapping) else None

    def optional_or(check: bool, value: Any) -> bool:
        """Treat omitted new identity fields as legacy-compatible only."""

        return bool(check) if strict or value is not None else True

    checks: Dict[str, bool] = {
        "http_200": record.get("http_status") == 200,
        "ready": isinstance(body, Mapping) and body.get("ready") is True,
        "healthy": isinstance(body, Mapping) and body.get("healthy") is True,
        "not_busy": isinstance(body, Mapping) and body.get("busy") is not True,
        "cache_cleared": isinstance(body, Mapping) and body.get("cache_cleared") is True,
        "busy_field": isinstance(body, Mapping) and body.get("busy") is False,
        # A healthy HTTP response alone does not prove that the candidate is
        # using the intended Ascend device.  Require the runtime identity
        # fields emitted after MindSpore context initialization.
        "npu_model_present": isinstance(body, Mapping)
        and isinstance(observed_npu_model, str)
        and bool(observed_npu_model),
        "npu_model_matches_profile": bool(expected_npu_model)
        and _soc_key(observed_npu_model) == _soc_key(expected_npu_model),
        "target_supported": target_supported,
        "requested_tier_matches_target": (
            not tier or (bool(expected_tier) and str(tier).strip() == expected_tier)
        ),
        "device_target_ascend": isinstance(body, Mapping)
        and str(body.get("device_target", "")).lower() == "ascend",
        "worker_pid": isinstance(body, Mapping)
        and isinstance(body.get("worker_pid"), int)
        and not isinstance(body.get("worker_pid"), bool)
        and body.get("worker_pid", 0) > 0,
        "environment_fingerprint": isinstance(body, Mapping)
        and isinstance(fingerprint, str)
        and bool(re.fullmatch(r"[0-9a-fA-F]{64}", fingerprint)),
    }
    checks["admission_status_present"] = optional_or(
        isinstance(observed_admission, str) and bool(str(observed_admission).strip()), observed_admission
    )
    checks["admission_status_matches_profile"] = optional_or(
        bool(expected_admission)
        and isinstance(observed_admission, str)
        and str(observed_admission).strip().lower() == expected_admission,
        observed_admission,
    )
    checks["admission_status_activatable"] = optional_or(
        isinstance(observed_admission, str)
        and str(observed_admission).strip().lower() in ACTIVATABLE_STATUSES,
        observed_admission,
    )
    checks["admission_allowed"] = optional_or(
        isinstance(body, Mapping) and body.get("admission_allowed") is True,
        body.get("admission_allowed") if isinstance(body, Mapping) else None,
    )
    checks["admission_soc_matches_target"] = optional_or(
        bool(expected_npu_model)
        and _soc_key(observed_admission_soc) == _soc_key(expected_npu_model),
        observed_admission_soc,
    )
    checks["candidate_kind_native"] = optional_or(
        expected_kind == "native_mindspore"
        and isinstance(observed_kind, str)
        and observed_kind.strip().lower() == "native_mindspore",
        observed_kind,
    )
    checks["candidate_not_conditional"] = optional_or(
        observed_conditional is False,
        observed_conditional,
    )
    # Health payloads from older workers do not expose host/tier.  Validate
    # those fields whenever present, but do not make them a prerequisite for
    # the SoC/admission identity gate (the NPU model and admission_soc are the
    # authoritative runtime identity fields).
    checks["board_tier_matches_target"] = (
        observed_tier is None
        or (bool(expected_tier) and str(observed_tier).strip() == expected_tier)
    )
    checks["board_host_matches_target"] = (
        observed_host is None
        or (bool(board) and isinstance(observed_host, str) and observed_host.strip() == str(board).strip())
    )
    # Stable aliases make the machine-readable report easier for external
    # acceptance tooling to consume without coupling it to one internal key
    # spelling.
    checks["soc_matches_target"] = checks["npu_model_matches_profile"]
    checks["admission_matches_target"] = checks["admission_status_matches_profile"]
    checks["candidate_kind_matches"] = checks["candidate_kind_native"]
    if isinstance(body, Mapping):
        observed_profile = body.get("profile") or body.get("profile_id")
        checks["profile_matches"] = observed_profile == profile
        checks["model_id_present"] = (body.get("model_id") or body.get("model")) == DEFAULT_MODEL
    else:
        checks["profile_matches"] = False
        checks["model_id_present"] = False
    return all(checks.values()), checks


def _models_ok(record: Mapping[str, Any], model: str = DEFAULT_MODEL) -> Tuple[bool, Dict[str, Any]]:
    body = record.get("body")
    data = body.get("data") if isinstance(body, Mapping) else None
    ids = [item.get("id") for item in data if isinstance(item, Mapping)] if isinstance(data, list) else []
    checks = {"http_200": record.get("http_status") == 200, "list": isinstance(data, list), "model_present": model in ids}
    return all(checks.values()), {"checks": checks, "model_ids": ids}


def run_campaign(options: Options) -> Dict[str, Any]:
    """Run one campaign against an already-running candidate service."""

    metadata = load_profile_metadata(options.profile, options.registry)
    target = _resolve_target(
        metadata,
        options.board if options.board else None,
        options.soc if options.soc else None,
    )
    evidence_pass, evidence = _evidence_reports(options, metadata)
    strict_target = bool(options.target_explicit or options.require_evidence or evidence.get("required"))
    endpoint = "http://%s:%d" % (options.host, options.port)
    report: Dict[str, Any] = {
        "schema_version": 1,
        "recorded_at_utc": utc_now(),
        "run_id": options.run_id,
        "execution": {
            "pid": os.getpid(),
            "argv": list(sys.argv),
            "service_prestarted": True,
            "process_management": "none",
        },
        "profile": metadata,
        "service": {"base_url": endpoint, "model": DEFAULT_MODEL, "requires_prestarted_service": True},
        "target": {
            "board": target.get("host"),
            "soc": target.get("soc"),
            "tier": target.get("tier"),
            "selection": target.get("selection"),
            "explicit": bool(target.get("explicit")),
        },
        "registry": str(options.registry) if options.registry is not None else None,
        "evidence": evidence,
        "policy": {
            "timeout_seconds": options.timeout,
            "long_budgets": list(options.long_budgets),
            "stability_loops": options.stability_loops,
            "performance_warmup": options.perf_warmup,
            "performance_loops": options.perf_loops,
            "max_generation_tokens": MAX_GENERATION_TOKENS,
            "evidence_required": bool(evidence.get("required")),
        },
        "gates": {},
        "notes": [
            "This campaign never starts or manages a service process.",
            "The quality gate is machine-validity only; human review must be recorded separately.",
            "npu-smi Health: Alarm, when present, is retained as evidence and is not a standalone gate.",
        ],
    }

    health = _json_request(options.host, options.port, "/health", None, options.timeout)
    models = _json_request(options.host, options.port, "/v1/models", None, options.timeout)
    report["api"] = {"health": health, "models": models}
    health_pass, health_checks = _health_ok(
        health,
        options.profile,
        options.registry,
        board=str(target.get("host") or "") if strict_target else None,
        soc=str(target.get("soc") or "") if strict_target else None,
        tier=str(target.get("tier") or "") if strict_target else None,
        strict_target=strict_target,
    )
    models_pass, models_details = _models_ok(models)
    report["api"]["health_checks"] = health_checks
    report["api"]["models_checks"] = models_details
    report["gates"]["health"] = health_pass
    report["gates"]["models"] = models_pass
    report["gates"]["evidence"] = evidence_pass if evidence.get("required") else None

    if options.snapshots:
        report.setdefault("snapshots", {})["before"] = system_snapshot("before", health, options.profile)

    # Do not spend a full performance campaign against an endpoint that is
    # absent, unhealthy, or serving a different profile.
    # Missing or mismatched provenance is a hard campaign failure.  Stop
    # before expensive model requests rather than producing a large report
    # whose passing API probes could obscure the absent G0 evidence.
    if not evidence_pass or not (health_pass and models_pass):
        for gate in ("json", "sse", "long_output", "stability", "quality_machine", "performance", "errors", "protocol"):
            report["gates"][gate] = False if gate != "quality_machine" else None
        report["status"] = "failed"
        required = _required_machine_gates(options, evidence_required=bool(evidence.get("required")))
        report["machine_gate_count"] = sum(1 for name in required if report["gates"].get(name) is True)
        report["machine_gate_total"] = len(required)
        return report

    smoke_prompt = "\u4f60\u597d\uff0c\u8bf7\u7528\u4e00\u53e5\u8bdd\u4ecb\u7ecd\u4f60\u81ea\u5df1\u3002"
    json_smoke = _extract_json_completion(
        _json_request(
            options.host, options.port, "/v1/chat/completions",
            _completion_payload(DEFAULT_MODEL, smoke_prompt, 2, False), options.timeout,
        )
    )
    sse_smoke = _sse_request(
        options.host, options.port,
        _completion_payload(DEFAULT_MODEL, smoke_prompt, 2, True), options.timeout,
    )
    report["smoke"] = {"json": json_smoke, "sse": sse_smoke}
    report["gates"]["json"] = _valid_json(json_smoke, 2)
    report["gates"]["sse"] = _valid_sse(sse_smoke, 2) and not bool(sse_smoke.get("duplicate_delta"))

    long_results: List[Dict[str, Any]] = []
    for budget in options.long_budgets:
        item = _extract_json_completion(
            _json_request(
                options.host, options.port, "/v1/chat/completions",
                _completion_payload(DEFAULT_MODEL, "\u8bf7\u7528\u4e2d\u6587\u56de\u7b54\uff1a\u9759\u6001 KV cache \u7684\u4f5c\u7528\u662f\u4ec0\u4e48\uff1f", budget, False), options.timeout,
            )
        )
        item["max_tokens"] = budget
        item["valid_for_budget"] = _valid_json(item, budget) and (
            item.get("finish_reason") != "length" or item.get("completion_tokens") == budget
        )
        long_results.append(item)
    report["long_output"] = long_results
    report["gates"]["long_output"] = bool(long_results) and all(item.get("valid_for_budget") for item in long_results)

    stability: List[Dict[str, Any]] = []
    for number in range(1, options.stability_loops + 1):
        item = _extract_json_completion(
            _json_request(
                options.host, options.port, "/v1/chat/completions",
                _completion_payload(DEFAULT_MODEL, "\u8bf7\u56de\u7b54\uff1a1+1 \u7b49\u4e8e\u51e0\uff1f", options.stability_max_tokens, False), options.timeout,
            )
        )
        item["round"] = number
        stability.append(item)
    report["stability"] = stability
    report["gates"]["stability"] = len(stability) == options.stability_loops and all(
        _valid_json(item, options.stability_max_tokens) for item in stability
    )

    if options.quality:
        probes = _load_probes(options.probe_file)
        probe_results: List[Dict[str, Any]] = []
        for probe in probes:
            item = _extract_json_completion(
                _json_request(
                    options.host, options.port, "/v1/chat/completions",
                    _completion_payload(DEFAULT_MODEL, probe["prompt"], options.probe_max_tokens, False), options.timeout,
                )
            )
            probe_results.append({"id": probe["id"], "language": probe["language"], "prompt": probe["prompt"], **item, "human_understandable": None})
        report["quality"] = _quality_summary(probe_results)
        report["gates"]["quality_machine"] = bool(probe_results) and all(item.get("machine_valid") for item in probe_results)
    else:
        report["quality"] = {"status": "skipped", "human_review": "not-run"}
        report["gates"]["quality_machine"] = None

    perf: List[Dict[str, Any]] = []
    for _ in range(options.perf_warmup):
        _sse_request(options.host, options.port, _completion_payload(DEFAULT_MODEL, "\u4f60\u597d", options.perf_max_tokens, True), options.timeout)
    for number in range(1, options.perf_loops + 1):
        item = _sse_request(
            options.host, options.port,
            _completion_payload(DEFAULT_MODEL, "\u4f60\u597d", options.perf_max_tokens, True), options.timeout,
        )
        item["round"] = number
        tokens = item.get("completion_tokens")
        if item.get("status") == "ok" and isinstance(tokens, int) and tokens > 0:
            item["tokens_per_second"] = round(tokens / max(0.001, float(item.get("elapsed_ms", 0)) / 1000.0), 6)
        perf.append(item)
    report["performance"] = {
        "warmup": options.perf_warmup,
        "loops": options.perf_loops,
        "results": perf,
        "elapsed_ms": summarize(item["elapsed_ms"] for item in perf if item.get("status") == "ok"),
        "first_event_ms": summarize(item["first_event_ms"] for item in perf if item.get("status") == "ok" and item.get("first_event_ms") is not None),
        "tokens_per_second": summarize(
            (item["tokens_per_second"] for item in perf if item.get("tokens_per_second") is not None),
            unit="tokens_per_second",
        ),
    }
    report["gates"]["performance"] = len(perf) == options.perf_loops and all(
        _valid_sse(item, options.perf_max_tokens) for item in perf
    )

    # G5/G9 protocol boundaries: every deliberately invalid request must
    # return the documented HTTP status and structured OpenAI error code.  No
    # invalid case is sent to the model provider after the service preflight.
    invalid_cases: Tuple[Tuple[str, Dict[str, Any], int, str], ...] = (
        (
            "wrong_model",
            _completion_payload("not-admitted", "x", 1, False),
            404,
            "model_not_found",
        ),
        (
            "wrong_role",
            {
                **_completion_payload(DEFAULT_MODEL, "x", 1, False),
                "messages": [{"role": "developer", "content": "x"}],
            },
            400,
            "invalid_request_error",
        ),
        (
            "temperature_nonzero",
            {
                **_completion_payload(DEFAULT_MODEL, "x", 1, False),
                "temperature": 0.1,
            },
            400,
            "invalid_request_error",
        ),
        (
            "top_p_non_greedy",
            {
                **_completion_payload(DEFAULT_MODEL, "x", 1, False),
                "top_p": 0.9,
            },
            400,
            "invalid_request_error",
        ),
        (
            "max_tokens_over_limit",
            _completion_payload(DEFAULT_MODEL, "x", MAX_GENERATION_TOKENS + 1, False),
            400,
            "invalid_request_error",
        ),
        (
            "unsupported_field",
            {
                **_completion_payload(DEFAULT_MODEL, "x", 1, False),
                "unknown": True,
            },
            400,
            "invalid_request_error",
        ),
    )
    error_results: List[Dict[str, Any]] = []
    for case, payload, expected_status, expected_code in invalid_cases:
        item = _json_request(
            options.host,
            options.port,
            "/v1/chat/completions",
            payload,
            options.timeout,
        )
        error_results.append(
            _mark_expected_error(
                item,
                case=case,
                expected_status=expected_status,
                expected_code=expected_code,
            )
        )
    report["errors"] = {
        "cases": error_results,
        "passed": sum(item.get("contract_status") == "passed" for item in error_results),
        "total": len(error_results),
    }
    report["gates"]["errors"] = bool(error_results) and all(
        item.get("contract_status") == "passed" for item in error_results
    )

    over_context = _over_context_request(
        options.host, options.port, DEFAULT_MODEL, options.timeout
    )
    oversized = _oversized_content_length_request(
        options.host, options.port, options.timeout
    )
    abort_request = _client_abort_sse(
        options.host,
        options.port,
        DEFAULT_MODEL,
        options.timeout,
        options.abort_max_tokens,
    )
    health_after_abort = _health_after_abort(
        options.host,
        options.port,
        options.timeout,
        options.abort_health_wait_seconds,
        options.profile,
        options.registry,
        board=str(target.get("host") or "") if strict_target else None,
        soc=str(target.get("soc") or "") if strict_target else None,
        tier=str(target.get("tier") or "") if strict_target else None,
        strict_target=strict_target,
    )
    abort_passed = (
        abort_request.get("status") == "sent_and_closed"
        and abort_request.get("started_observed") is True
        and health_after_abort.get("contract_status") == "passed"
    )
    abort_request["contract_status"] = "passed" if abort_passed else "failed"
    report["protocol"] = {
        "over_context": over_context,
        "oversized_request": oversized,
        "client_abort": abort_request,
        "health_after_abort": health_after_abort,
    }
    report["gates"]["protocol"] = all(
        item.get("contract_status") == "passed"
        for item in (over_context, oversized, abort_request, health_after_abort)
    )

    if options.snapshots:
        # Re-read health after the abort probe so the after snapshot reflects
        # the worker's post-cancellation state rather than the initial sample.
        after_health = _json_request(options.host, options.port, "/health", None, options.timeout)
        report.setdefault("snapshots", {})["after"] = system_snapshot("after", after_health, options.profile)
    required = _required_machine_gates(options, evidence_required=bool(evidence.get("required")))
    report["status"] = "passed" if all(report["gates"].get(name) is True for name in required) else "failed"
    report["machine_gate_count"] = sum(1 for name in required if report["gates"].get(name) is True)
    report["machine_gate_total"] = len(required)
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument(
        "--board", "--board-ip",
        dest="board",
        default=None,
        help="target board host or declared alias (8t/20t); optional for legacy primary-board calls",
    )
    parser.add_argument(
        "--soc", "--soc-version",
        dest="soc",
        default=None,
        help="target Ascend SoC (Ascend310B4/Ascend310B1); optional for legacy calls",
    )
    parser.add_argument(
        "--registry",
        type=Path,
        default=None,
        help="profile registry JSON (defaults to CASE9_MODEL_PROFILES or the checked-in registry)",
    )
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--long-budgets", default="8,16,32,64")
    parser.add_argument("--stability-loops", type=int, default=10)
    parser.add_argument("--stability-max-tokens", type=int, default=2)
    parser.add_argument("--perf-warmup", type=int, default=2)
    parser.add_argument("--perf-loops", type=int, default=30)
    parser.add_argument("--perf-max-tokens", type=int, default=2)
    parser.add_argument("--probe-max-tokens", type=int, default=8)
    parser.add_argument("--probe-file", type=Path, default=None)
    parser.add_argument(
        "--environment-report", "--environment-evidence",
        dest="environment_report",
        type=Path,
        default=None,
        help="G0 environment/preflight JSON to associate with this board campaign",
    )
    parser.add_argument(
        "--artifact-report", "--artifact-evidence",
        dest="artifact_report",
        type=Path,
        default=None,
        help="G1 artifact-verification JSON to associate with this board campaign",
    )
    parser.add_argument(
        "--require-evidence",
        action="store_true",
        help="require and validate both environment and artifact reports",
    )
    parser.add_argument(
        "--abort-max-tokens",
        type=int,
        default=1,
        help="token budget for the deliberately aborted SSE request (1-64)",
    )
    parser.add_argument(
        "--abort-health-wait-seconds",
        type=float,
        default=ABORT_HEALTH_WAIT_SECONDS,
        help="maximum time to poll health after the client-abort probe",
    )
    parser.add_argument("--execute", "--run", action="store_true", help="perform requests against an already-running service")
    parser.add_argument("--dry-run", action="store_true", help="print the plan without requests or report writes")
    parser.add_argument("--skip-quality", action="store_true")
    parser.add_argument("--skip-snapshots", action="store_true")
    return parser


def _dry_run_payload(options: Options) -> Dict[str, Any]:
    return {
        "status": "dry-run",
        "profile": options.profile,
        "registry": str(options.registry) if options.registry is not None else None,
        "target": "http://%s:%d" % (options.host, options.port),
        "board": options.board or None,
        "soc": options.soc or None,
        "tier": options.tier or None,
        "target_explicit": options.target_explicit,
        "evidence": {
            "required": bool(options.require_evidence or options.target_explicit),
            "environment_report": str(options.environment_report) if options.environment_report else None,
            "artifact_report": str(options.artifact_report) if options.artifact_report else None,
        },
        "output": str(options.output),
        "checks": [
            "health", "models", "JSON", "SSE", "long-output", "stability",
            "performance", "structured-errors", "protocol-boundaries",
        ],
        "service_must_already_be_running": True,
        "writes_reports": False,
        "installs_packages": False,
        "manages_processes": False,
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        options = validate_options(args)
    except AcceptanceError as exc:
        parser.error(str(exc))
    if not options.execute or bool(getattr(args, "dry_run", False)):
        print(json.dumps(_dry_run_payload(options), ensure_ascii=False, sort_keys=True))
        return 0

    try:
        report = run_campaign(options)
    except Exception as exc:
        # Keep an aborted campaign machine-readable.  Consumers use the gate
        # keys to distinguish an execution failure from a skipped check; an
        # empty mapping made a failed report look structurally incomplete.
        try:
            profile_metadata = load_profile_metadata(options.profile, options.registry)
        except Exception as metadata_exc:
            profile_metadata = {
                "id": options.profile,
                "metadata_error": "%s: %s" % (type(metadata_exc).__name__, metadata_exc),
            }
        try:
            fallback_target = _resolve_target(
                profile_metadata,
                options.board if options.board else None,
                options.soc if options.soc else None,
            )
            fallback_target = {
                "board": fallback_target.get("host"),
                "soc": fallback_target.get("soc"),
                "tier": fallback_target.get("tier"),
                "selection": fallback_target.get("selection"),
                "explicit": bool(fallback_target.get("explicit")),
            }
        except Exception:
            fallback_target = {
                "board": options.board or None,
                "soc": options.soc or None,
                "tier": options.tier or None,
                "selection": "unresolved",
                "explicit": bool(options.target_explicit),
            }
        required = _required_machine_gates(options)
        report = {
            "schema_version": 1,
            "recorded_at_utc": utc_now(),
            "run_id": options.run_id,
            "execution": {"pid": os.getpid(), "argv": list(sys.argv), "service_prestarted": True, "process_management": "none"},
            "profile": profile_metadata,
            "target": fallback_target,
            "evidence": {
                "required": bool(options.require_evidence or options.target_explicit),
                "status": "failed",
                "reason": "campaign raised before evidence validation",
            },
            "status": "error",
            "error": "%s: %s" % (type(exc).__name__, exc),
            "gates": {name: False for name in required},
        }
    options.output.mkdir(parents=True, exist_ok=True)
    _write_json(options.output / "acceptance.json", report)
    _write_json(options.output / "metadata.json", {"profile": report.get("profile"), "target": report.get("target"), "evidence": report.get("evidence"), "service": report.get("service"), "policy": report.get("policy"), "execution": report.get("execution"), "status": report.get("status"), "recorded_at_utc": report.get("recorded_at_utc")})
    _write_json(options.output / "command.json", report.get("execution", {}))
    _write_json(options.output / "health.json", report.get("api", {}).get("health", {}))
    _write_json(options.output / "models.json", report.get("api", {}).get("models", {}))
    _write_json(options.output / "json-smoke.json", report.get("smoke", {}).get("json", {}))
    _write_json(options.output / "sse-smoke.json", report.get("smoke", {}).get("sse", {}))
    _write_json(options.output / "long-output.json", report.get("long_output", []))
    _write_json(options.output / "stability.json", report.get("stability", []))
    _write_json(options.output / "performance.json", report.get("performance", {}))
    _write_json(options.output / "errors.json", report.get("errors", {}))
    _write_json(options.output / "protocol.json", report.get("protocol", {}))
    _write_json(options.output / "quality.json", report.get("quality", {}))
    if "snapshots" in report:
        _write_json(options.output / "snapshots.json", report["snapshots"])
    _write_text(options.output / "README.txt", "Generated by the read-only MindSpore chat acceptance tool.\nThe service had to be running before the campaign.\n")
    print(json.dumps({"status": report.get("status"), "output": str(options.output), "gates": report.get("gates", {})}, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
