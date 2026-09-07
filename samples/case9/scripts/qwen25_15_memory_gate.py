#!/usr/bin/env python3
"""Run a bounded, non-service Qwen2.5-1.5B MindSpore load gate.

This command is deliberately a diagnostic boundary.  It loads exactly one
profile in the selected board environment, optionally performs one greedy
token, and writes measurements to a report.  It never starts an HTTP server,
changes the registry, installs packages, or falls back to another backend.
The shell caller should wrap it in a process-level watchdog (for example
``timeout --kill-after=10``), because a device call may not be interruptible
from Python.  A successful diagnostic returns exit code ``3``: it is evidence
for a later review, not a promotion or production-readiness result.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time
import re
from typing import Any, Dict, Mapping, Optional, Sequence

try:  # ``resource`` is available on Linux boards but not on Windows.
    import resource
except ImportError:  # pragma: no cover - exercised only on Windows
    resource = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _command(command: Sequence[str], timeout: float = 10.0) -> Dict[str, Any]:
    try:
        completed = subprocess.run(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"command": list(command), "returncode": None, "output": str(exc)}
    return {
        "command": list(command),
        "returncode": int(completed.returncode),
        "output": completed.stdout[-16_384:],
    }


def _extract_soc(snapshot: Mapping[str, Any]) -> Optional[str]:
    """Extract an Ascend 310B model label from a captured npu-smi result."""

    output = str(snapshot.get("output", ""))
    match = re.search(r"(?:Ascend\s*)?(310B[0-9A-Za-z]+)", output, re.IGNORECASE)
    return ("Ascend" + match.group(1).upper()) if match else None


def _memory_snapshot() -> Dict[str, Any]:
    # ``ru_maxrss`` is a process peak, not the resident set at snapshot time.
    # Keep both values so a crash report cannot be mistaken for a leak trend.
    current_rss_bytes = None
    if sys.platform.startswith("linux"):
        try:
            fields = Path("/proc/self/statm").read_text(encoding="ascii").split()
            if len(fields) >= 2:
                current_rss_bytes = int(fields[1]) * int(os.sysconf("SC_PAGE_SIZE"))
        except (OSError, ValueError, TypeError):
            current_rss_bytes = None
    max_rss_bytes = None
    if resource is not None:
        # Linux reports ru_maxrss in KiB; keep the report unit explicit.
        raw_max_rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
        max_rss_bytes = raw_max_rss * 1024 if sys.platform.startswith("linux") else raw_max_rss
    values: Dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii", errors="replace").splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[1].isdigit():
                values[fields[0].rstrip(":")] = int(fields[1]) * 1024
    except OSError as exc:
        return {
            "error": str(exc),
            "rss_bytes": current_rss_bytes,
            "current_rss_bytes": current_rss_bytes,
            "max_rss_bytes": max_rss_bytes,
        }
    return {
        "mem_total_bytes": values.get("MemTotal"),
        "mem_available_bytes": values.get("MemAvailable"),
        "swap_total_bytes": values.get("SwapTotal"),
        "swap_free_bytes": values.get("SwapFree"),
        # Compatibility key: it now means the resident set at this snapshot.
        "rss_bytes": current_rss_bytes,
        "current_rss_bytes": current_rss_bytes,
        "max_rss_bytes": max_rss_bytes,
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
    descriptor, temporary = tempfile.mkstemp(prefix=".%s." % path.name, suffix=".part", dir=str(path.parent))
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _file_digest(path: Path) -> Dict[str, Any]:
    """Return immutable source evidence without failing the diagnostic run."""

    try:
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                size += len(block)
                digest.update(block)
        return {"path": str(path), "bytes": size, "sha256": digest.hexdigest()}
    except OSError as exc:
        return {"path": str(path), "error": str(exc)}


def _cann_module_probe() -> Dict[str, Any]:
    """Capture CANN Python module visibility before loading the model.

    A caller that overwrites ``PYTHONPATH`` after ``set_env.sh`` can make a
    healthy board appear to lack CANN.  Keep this preflight explicit and fail
    closed before any model allocation when the expected modules are hidden.
    """

    modules: Dict[str, Any] = {}
    for name in ("acl", "te", "hccl"):
        entry: Dict[str, Any]
        try:
            spec = importlib.util.find_spec(name)
        except Exception as exc:  # pragma: no cover - platform-specific
            modules[name] = {"available": False, "origin": None, "error": str(exc)}
            continue
        entry = {
            "available": spec is not None,
            "origin": str(spec.origin) if spec is not None and spec.origin else None,
            "imported": False,
        }
        if spec is not None:
            try:
                imported = importlib.import_module(name)
                entry["imported"] = True
                entry["origin"] = str(getattr(imported, "__file__", entry["origin"]))
            except Exception as exc:  # pragma: no cover - board/runtime-specific
                entry["import_error"] = str(exc)
        modules[name] = entry
    modules["pythonpath"] = os.environ.get("PYTHONPATH", "")
    expected = ("acl", "te", "hccl")
    modules["status"] = "passed" if all(
        modules[name].get("available") and modules[name].get("imported") for name in expected
    ) else "failed"
    return modules


def _validate_after_npu(report: Dict[str, Any]) -> None:
    """Fail the diagnostic when the device is not healthy after cleanup.

    A successful model call alone is insufficient evidence: ``npu-smi`` can
    fail after an ACL teardown or report a different device if the process was
    reset/rebound.  Keep this check in the report-producing process so callers
    cannot accidentally treat a partial cleanup as a passing gate.
    """

    measurements = report.setdefault("measurements", {})
    after = measurements.get("npu_after")
    failures: list[str] = []
    if not isinstance(after, Mapping) or after.get("returncode") != 0:
        returncode = after.get("returncode") if isinstance(after, Mapping) else None
        failures.append(
            "npu-smi info failed after model cleanup (returncode=%s)" % returncode
        )

    expected_soc = measurements.get("expected_soc") or measurements.get("npu_before_soc")
    actual_soc = measurements.get("npu_after_soc")
    if expected_soc:
        if not actual_soc:
            failures.append("npu-smi output did not identify a SoC after model cleanup")
        elif str(actual_soc) != str(expected_soc):
            failures.append(
                "npu-smi SoC %s after cleanup does not match target %s"
                % (actual_soc, expected_soc)
            )

    report["after_npu_gate"] = "failed" if failures else "passed"
    if failures:
        report.setdefault("errors", []).extend(failures)
        if report.get("status") == "diagnostic_passed":
            report["status"] = "failed"


def _exit_code_for_status(status: Any) -> int:
    """Map a report status to a shell result without conflating diagnostics and promotion."""

    if status == "passed":
        return 0
    if status == "diagnostic_passed":
        # Distinct from argparse's 2 and ordinary failure's 1.  A caller can
        # preserve the report while refusing to promote the profile.
        return 3
    return 1


def _placement_gate_value(context_only: bool) -> str:
    """Return the placement policy for this invocation.

    Strict placement is the default.  The diagnostic escape hatch is an
    explicit command-line choice so an inherited environment variable cannot
    silently weaken a normal gate run.
    """

    return "0" if bool(context_only) else "1"


class _NpuSampler:
    """Collect bounded ``npu-smi`` snapshots while a diagnostic call runs."""

    def __init__(self, interval: float = 2.0) -> None:
        self.interval = max(0.5, float(interval))
        self.samples: list[Dict[str, Any]] = []
        self._stop = threading.Event()
        self._started = False
        self._thread = threading.Thread(target=self._run, name="case9-npu-sampler", daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            self.samples.append({"at": _now(), **_command(["npu-smi", "info"], timeout=5.0)})
            self._stop.wait(self.interval)

    def start(self) -> None:
        self._thread.start()
        self._started = True

    def stop(self) -> list[Dict[str, Any]]:
        if not self._started:
            return list(self.samples)
        self._stop.set()
        self._thread.join(timeout=6.0)
        return list(self.samples)


def run(args: argparse.Namespace) -> Dict[str, Any]:
    root = args.root.expanduser().resolve()
    registry_path = args.registry.expanduser().resolve()
    report: Dict[str, Any] = {
        "schema_version": 1,
        "started_at": _now(),
        "pid": os.getpid(),
        "profile": args.profile,
        "registry": str(registry_path),
        "model_root": str(root),
        "host": socket.gethostname(),
        "board_ip": args.board_ip,
        "argv": list(sys.argv),
        "diagnostic_only": True,
        "promotion_eligible": False,
        "placement_gate": "not-verified",
        "offline_mode": True,
        "http_service_started": False,
        "requested_ms_dtype": "float16",
        "stages": {},
        "measurements": {"before": _memory_snapshot()},
        "status": "failed",
        "errors": [],
    }
    report["source_files"] = {
        "registry": _file_digest(registry_path),
        "gate_script": _file_digest(Path(__file__).resolve()),
        "profile_module": _file_digest(root / "case9_model_profiles.py"),
        "provider_module": _file_digest(root / "mindspore_chat_providers.py"),
    }
    provider: Any = None
    sampler: Optional[_NpuSampler] = None
    try:
        sys.path.insert(0, str(root))
        from case9_model_profiles import load_profiles
        from mindspore_chat_providers import create_provider
        from scripts.verify_mindspore_profile_artifacts import verify_profile_artifacts

        registry = load_profiles(registry_path)
        profile = registry.get(args.profile)
        report["profile_metadata"] = {
            "id": profile.id,
            "model_id": profile.model_id,
            "revision": profile.revision,
            "board_soc": profile.board_soc,
            "board_tier": profile.board_tier,
            "cache_dir": profile.cache_dir,
            "weight_format": profile.weight_format,
            # ``board`` is a registry primary/default field.  Keep all
            # per-SoC targets visible so a B4 run is not mislabeled as the
            # profile's historical B1 primary target.
            "declared_board_targets": [dict(item) for item in profile.board_targets],
        }

        artifact_report = verify_profile_artifacts(profile, root)
        report["stages"]["artifact_verification"] = artifact_report
        if not all(item.get("status") == "verified" for item in artifact_report.get("artifacts", [])):
            raise RuntimeError("all declared artifacts must be verified before the load gate")

        before_npu = _command(["npu-smi", "info"])
        report["measurements"]["npu_before"] = before_npu
        report["measurements"]["npu_before_soc"] = _extract_soc(before_npu)
        if before_npu.get("returncode") != 0:
            raise RuntimeError("npu-smi info failed before model load")
        actual_soc = report["measurements"].get("npu_before_soc")
        expected_soc = None
        for target in profile.board_targets:
            if str(target.get("host")) == str(args.board_ip):
                expected_soc = str(target.get("soc"))
                break
        expected_soc = expected_soc or str(profile.board_soc or "")
        report["measurements"]["expected_soc"] = expected_soc
        if not actual_soc:
            raise RuntimeError("npu-smi output did not identify an Ascend 310B SoC")
        if expected_soc and actual_soc != expected_soc:
            raise RuntimeError(
                "npu-smi SoC %s does not match target %s" % (actual_soc, expected_soc)
            )
        # Do not inherit a path or device selection from an active deployment.
        # This process is intentionally pinned to the selected isolated root.
        os.environ["CASE9_MODEL_ROOT"] = str(root)
        os.environ["CASE9_DEVICE_TARGET"] = "Ascend"
        os.environ["CASE9_DEVICE_ID"] = "0"
        # Refuse an implicit Hub fallback if any local artifact is missing or
        # a provider rejects the synchronized checkpoint.
        os.environ["HF_HUB_OFFLINE"] = "1"
        os.environ["TRANSFORMERS_OFFLINE"] = "1"
        # Placement is strict by default.  Only the explicit --context-only
        # diagnostic switch may weaken it; this prevents a stale inherited
        # environment variable from changing the meaning of a gate run.
        context_only = bool(getattr(args, "context_only", False))
        os.environ["CASE9_REQUIRE_PLACEMENT_EVIDENCE"] = _placement_gate_value(context_only)
        report["context_only_requested"] = context_only
        report["placement_gate_requested"] = os.environ.get(
            "CASE9_REQUIRE_PLACEMENT_EVIDENCE"
        )

        cann_probe = _cann_module_probe()
        report["stages"]["cann_python_modules"] = cann_probe
        if cann_probe.get("status") != "passed":
            raise RuntimeError(
                "CANN Python modules are not visible; preserve PYTHONPATH from set_env.sh"
            )

        sampler = _NpuSampler()
        sampler.start()
        load_started = time.monotonic()
        provider = create_provider(profile, generation_timeout=float(args.generation_timeout))
        provider.load()
        report["stages"]["model_load"] = {
            "status": "passed",
            "seconds": round(time.monotonic() - load_started, 6),
            "provider_status": provider.status(),
        }

        messages = [{"role": "user", "content": args.prompt}]
        generation_started = time.monotonic()
        result = provider.complete(messages, int(args.max_tokens))
        report["stages"]["single_generation"] = {
            "status": "passed",
            "seconds": round(time.monotonic() - generation_started, 6),
            "text": result.text,
            "prompt_tokens": result.prompt_tokens,
            "completion_tokens": result.completion_tokens,
            "finish_reason": result.finish_reason,
        }
        # A successful load/generation is useful diagnostic evidence, but this
        # command never promotes a profile.  Keep the strict/context-only
        # policy and registry promotion status separate.
        report["status"] = "diagnostic_passed"
        report["placement_gate"] = "context-only" if context_only else "strict"
        report["measurements"]["npu_during"] = sampler.stop()
    except BaseException as exc:
        report["errors"].append("%s: %s" % (type(exc).__name__, exc))
        report["exception_type"] = type(exc).__name__
        if provider is not None:
            try:
                report["provider_status_on_error"] = provider.status()
            except Exception as status_exc:
                report["errors"].append("provider.status: %s" % status_exc)
    finally:
        if sampler is not None:
            report["measurements"].setdefault("npu_during", sampler.stop())
        # Capture the model-resident state before closing, then a second
        # snapshot after cleanup so memory retention is observable.
        report["measurements"]["before_cleanup"] = _memory_snapshot()
        report["measurements"]["npu_before_cleanup"] = _command(["npu-smi", "info"])
        if provider is not None:
            try:
                provider.close()
            except Exception as exc:
                report["errors"].append("provider.close: %s" % exc)
        report["measurements"]["after"] = _memory_snapshot()
        report["measurements"]["npu_after"] = _command(["npu-smi", "info"])
        report["measurements"]["npu_after_soc"] = _extract_soc(report["measurements"]["npu_after"])
        _validate_after_npu(report)
        if "npu_during" not in report["measurements"]:
            # The sampler may not have started if artifact or environment
            # validation failed; retain an explicit empty diagnostic field.
            report["measurements"]["npu_during"] = []
        report["finished_at"] = _now()
    return report


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="qwen2.5-1.5b-mindspore")
    parser.add_argument("--registry", type=Path, default=ROOT / "configs" / "chat_model_profiles.json")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--prompt", default="你好，请用一句话介绍你自己。")
    parser.add_argument("--max-tokens", type=int, default=1)
    parser.add_argument("--generation-timeout", type=float, default=180.0)
    parser.add_argument("--board-ip", default=os.environ.get("CASE9_BOARD_IP", "192.168.1.90"))
    parser.add_argument(
        "--context-only",
        action="store_true",
        help="diagnostic-only load that disables strict placement evidence (never promotes a profile)",
    )
    args = parser.parse_args(argv)
    if args.max_tokens < 1 or args.max_tokens > 2:
        parser.error("the diagnostic gate accepts max_tokens 1 or 2")
    report = run(args)
    if args.output:
        _write_json(args.output.expanduser().resolve(), report)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return _exit_code_for_status(report.get("status"))


if __name__ == "__main__":
    raise SystemExit(main())
