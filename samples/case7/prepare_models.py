#!/usr/bin/env python3
"""Auditable checkpoint -> ONNX -> Ascend OM model pipeline for case7."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import subprocess
import sys
import tarfile
import fnmatch
import re
import urllib.parse
import time
from copy import deepcopy
from pathlib import Path
from typing import Optional
from urllib.request import urlopen

import numpy as np

from embedding_backend import (
    ACL_DTYPE_CODES,
    AclModel,
    AscendResource,
    NUMPY_DTYPES,
    l2_normalize,
)
from model_registry import (
    DEFAULT_CANDIDATE_MANIFEST,
    DEFAULT_REGISTRY,
    ROOT,
    RegistryError,
    load_candidates,
    model_dict,
    sha256_file,
)


MODEL_DIR = ROOT / "models"
SOURCE_DIR = MODEL_DIR / "sources"
CHECKPOINT_DIR = MODEL_DIR / "checkpoints"
TOKENIZER_DIR = MODEL_DIR / "tokenizers"
ONNX_DIR = MODEL_DIR / "onnx"
OM_DIR = MODEL_DIR / "om"
REPORT_DIR = ROOT / "reports" / "model_pipeline"
REFERENCE_DIR = REPORT_DIR / "references"
DEFAULT_HF_ENDPOINT = "https://hf-mirror.com"
MOBILECLIP_ID = "mobileclip_s0__npu__mixed_fp16"
CHINESE_CLIP_ID = "chinese_clip_rn50__npu__mixed_fp16"
RESNET50_ID = "resnet50_feature__npu__mixed_fp16"
NUMERICAL_THRESHOLD = 0.995
SERIAL_ATC_ENV = {
    # CANN/TBE compilation is intentionally serialized on the 7.4 GiB board.
    "MAX_COMPILE_CORE_NUMBER": "1",
    # CANN 8.x defaults model conversion to multi-thread mode.  Zero is the
    # documented single-thread setting and is required in addition to the
    # one-process operator compiler limit below.
    "MULTI_THREAD_COMPILE": "0",
    "TBE_PARALLEL_COMPILER": "0",
    # One is the minimum valid operator compiler process count; values greater
    # than one enable parallel operator builds.
    "TE_PARALLEL_COMPILER": "1",
    "ASCENDC_PAR_COMPILE_JOB": "0",
    "TILINGKEY_PAR_COMPILE": "0",
    "OMP_NUM_THREADS": "1",
    "OPENBLAS_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
    "CMAKE_BUILD_PARALLEL_LEVEL": "1",
    "MAKEFLAGS": "-j1",
    "VECLIB_MAXIMUM_THREADS": "1",
    "BLIS_NUM_THREADS": "1",
    "GOMP_NUM_THREADS": "1",
}


class PipelineError(RuntimeError):
    pass


def _serial_compile_environment():
    """Return a fresh environment that cannot opt into compiler parallelism."""
    environment = os.environ.copy()
    environment.update(SERIAL_ATC_ENV)
    return environment


def _host_memory_snapshot():
    values = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="ascii").splitlines():
            key, value = line.split(":", 1)
            values[key] = int(value.strip().split()[0]) * 1024
    except (OSError, ValueError):
        return None
    return {
        "mem_total_bytes": values.get("MemTotal"),
        "mem_available_bytes": values.get("MemAvailable"),
        "swap_total_bytes": values.get("SwapTotal", 0),
        "swap_free_bytes": values.get("SwapFree", 0),
    }


def _detect_cann_version() -> str:
    """Read the toolkit version from the active board installation.

    Registry evidence must describe the runtime that actually produced the
    OM.  A fixed version string would silently mislabel a board upgraded from
    the original deployment plan, so the active toolkit is always inspected.
    """
    candidates = []
    for variable in ("ASCEND_TOOLKIT_HOME", "ASCEND_HOME_PATH"):
        value = os.environ.get(variable)
        if value:
            root = Path(value)
            candidates.extend((root / "version.cfg", root / "latest" / "version.cfg"))
    candidates.extend(
        (
            Path("/usr/local/Ascend/ascend-toolkit/latest/version.cfg"),
            Path("/usr/local/Ascend/ascend-toolkit/version.cfg"),
        )
    )
    for path in candidates:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            if "toolkit_running_version" not in line and "compiler_running_version" not in line:
                continue
            match = re.search(r"\[([^\]]+)\]", line)
            if match:
                return match.group(1)
    return "unknown"


@contextlib.contextmanager
def _atc_lock(lock_root=None):
    """Serialize ATC invocations across shells sharing this release."""
    lock_path = Path(lock_root or REPORT_DIR) / ".atc.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="ascii")
    fcntl = None
    try:
        try:
            import fcntl
        except ImportError as exc:
            raise PipelineError("ATC locking requires a POSIX board runtime") from exc
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


# All ATC entry points in this project share one lock.  Candidate reports are
# intentionally isolated, but using a lock inside each candidate directory
# would still allow two independent sweeps (or a manual conversion) to
# compile concurrently on the memory-constrained board.
ATC_LOCK_ROOT = REPORT_DIR


def _run(command, cwd=None, env=None, log_path=None):
    printable = " ".join(str(item) for item in command)
    print(f"[run] {printable}")
    completed = subprocess.run(
        [str(item) for item in command],
        cwd=str(cwd) if cwd else None,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    if log_path:
        Path(log_path).parent.mkdir(parents=True, exist_ok=True)
        Path(log_path).write_text(
            f"command: {printable}\nexit_code: {completed.returncode}\n\n{completed.stdout}",
            encoding="utf-8",
        )
    print(completed.stdout)
    if completed.returncode != 0:
        raise PipelineError(f"command failed with exit code {completed.returncode}: {printable}")
    return completed


def _atc_parallel_option(enable_graph_parallel: int = 0) -> tuple[Optional[str], str]:
    """Select the serial ATC flag supported by the installed CANN release.

    CANN releases expose different names for the graph-parallel switch.
    Whichever supported option is found is explicitly set to zero; omitting
    the option is safe because the ATC default is disabled and the process
    environment is serialized.
    """
    if enable_graph_parallel != 0:
        raise PipelineError("parallel ATC graph compilation is disabled for this low-memory board")
    atc = shutil.which("atc")
    if not atc:
        raise PipelineError("atc is unavailable; run convert on the Ascend board after sourcing CANN")
    try:
        probe = subprocess.run(
            [atc, "--help"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=20,
        )
        help_text = probe.stdout or ""
    except (OSError, subprocess.SubprocessError) as exc:
        return None, f"probe-error:{exc.__class__.__name__}"
    if "--enable_graph_parallel" in help_text:
        return "--enable_graph_parallel=0", "enable_graph_parallel=0"
    if "--ac_parallel_enable" in help_text:
        return "--ac_parallel_enable=0", "ac_parallel_enable=0"
    return None, "default-disabled"


def _selected(value):
    records = model_dict(load_candidates())
    if value == "all":
        return list(records.values())
    requested = [item.strip() for item in value.split(",") if item.strip()]
    unknown = sorted(set(requested) - set(records))
    if unknown:
        raise PipelineError(f"unknown model id(s): {', '.join(unknown)}")
    return [records[item] for item in requested]


def _candidate_payload():
    return json.loads(DEFAULT_CANDIDATE_MANIFEST.read_text(encoding="utf-8"))


def _raw_candidate(model_id):
    for value in _candidate_payload()["models"]:
        if value["model_id"] == model_id:
            return value
    raise PipelineError(f"candidate metadata is missing for {model_id}")


def _ensure_dirs():
    for directory in (
        SOURCE_DIR,
        CHECKPOINT_DIR,
        TOKENIZER_DIR,
        ONNX_DIR,
        OM_DIR,
        REPORT_DIR,
        REFERENCE_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def _display_path(path: Path) -> str:
    """Render paths correctly when a release uses shared-asset symlinks."""
    path = Path(path).resolve()
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        try:
            return os.path.relpath(path, ROOT)
        except ValueError:
            # Windows cannot make a relative path across drive letters.  Keep
            # candidate evidence usable for controller-side tests instead of
            # hiding the actual external path.
            return str(path)


def _resolve_user_path(value, default: Path) -> Path:
    """Resolve a CLI path without depending on the caller's current directory.

    Model manifests are rooted at ``ROOT``.  Keeping the same convention for
    candidate output and report paths makes a sweep reproducible whether it is
    launched from the case directory or through an absolute board path.
    """
    if value is None:
        return Path(default).resolve()
    path = Path(value)
    if not path.is_absolute():
        path = ROOT / path
    return path.resolve()


def _canonical_component_path(component) -> Path:
    return Path(component.om_path).resolve()


def _candidate_om_path(component, output_om_dir=None) -> Path:
    """Return the requested OM destination for a conversion component."""
    if output_om_dir is None:
        return _canonical_component_path(component)
    return (_resolve_user_path(output_om_dir, OM_DIR) / Path(component.om_path).name).resolve()


def _assert_candidate_output_safe(component, destination: Path, explicit: bool) -> None:
    """Prevent an explicitly requested candidate conversion from touching production OM.

    The default conversion intentionally targets the registered production OM
    for backwards compatibility.  Once a caller opts into a candidate output
    directory, an exact canonical path (including a symlink resolving to it) is
    rejected before ATC is invoked.
    """
    canonical = _canonical_component_path(component)
    if explicit and destination == canonical:
        raise PipelineError(
            f"candidate output resolves to canonical OM and is refused: {canonical}"
        )


def _report_root_for_conversion(output_om_dir=None, report_dir=None) -> Path:
    """Choose an isolated report directory for candidate conversions.

    Legacy conversions keep writing ``REPORT_DIR``.  An explicit candidate
    output gets a sibling ``reports`` directory when no report directory is
    supplied, so a sweep cannot silently overwrite production evidence.
    """
    if report_dir is not None:
        return _resolve_user_path(report_dir, REPORT_DIR)
    if output_om_dir is None:
        return REPORT_DIR.resolve()
    output_dir = _resolve_user_path(output_om_dir, OM_DIR)
    return (output_dir.parent / "reports").resolve()


def _assert_candidate_report_safe(report_root: Path, explicit_output: bool) -> None:
    if explicit_output:
        try:
            report_root.relative_to(REPORT_DIR.resolve())
        except ValueError:
            return
        raise PipelineError(
            "candidate conversion cannot write inside the production model report directory; "
            "pass an isolated --report-dir"
        )


def _conversion_components(records, component_kind=None):
    """Yield the exact record/component pairs selected for one ATC invocation."""
    for record in records:
        if component_kind is None:
            for kind, component in record.components.items():
                yield record, kind, component
        else:
            yield record, component_kind, record.components[component_kind]


def _git_source(name, url, revision):
    target = SOURCE_DIR / name
    if not (target / ".git").is_dir():
        marker = target / ".source_revision"
        if marker.is_file() and marker.read_text(encoding="ascii").strip() == revision:
            return target
        archive_url = url.rstrip("/") + f"/archive/{revision}.tar.gz"
        archive_path = SOURCE_DIR / f"{name}-{revision}.tar.gz"
        print(f"[download] source archive: {archive_url}")
        try:
            with urlopen(archive_url, timeout=180) as response, archive_path.open("wb") as handle:
                shutil.copyfileobj(response, handle)
            with tarfile.open(archive_path, "r:gz") as archive:
                members = archive.getmembers()
                for member in members:
                    member_path = Path(member.name)
                    if member_path.is_absolute() or ".." in member_path.parts:
                        raise PipelineError(f"unsafe source archive member: {member.name}")
                top_levels = {member.name.split("/", 1)[0] for member in members if member.name}
                if len(top_levels) != 1:
                    raise PipelineError(f"unexpected source archive layout for {name}")
                archive.extractall(SOURCE_DIR)
                extracted = SOURCE_DIR / next(iter(top_levels))
            if target.exists():
                shutil.rmtree(target)
            extracted.rename(target)
            marker.write_text(revision, encoding="ascii")
        except Exception as exc:
            raise PipelineError(f"source archive download failed for {name}: {exc}") from exc
        finally:
            archive_path.unlink(missing_ok=True)
        return target
    actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=target, text=True).strip()
    if actual != revision:
        _run(["git", "fetch", "--depth", "1", "origin", revision], cwd=target)
        _run(["git", "checkout", "--detach", revision], cwd=target)
        actual = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=target, text=True).strip()
    if actual != revision:
        raise PipelineError(f"source revision mismatch for {name}: {actual}")
    return target


def _snapshot(repo_id, target, allow_patterns=None, endpoint=None, insecure_tls=False):
    snapshot_download = None
    if not insecure_tls:
        try:
            from huggingface_hub import snapshot_download as hub_snapshot_download
            snapshot_download = hub_snapshot_download
        except ImportError as exc:
            raise PipelineError("download requires huggingface-hub") from exc
    target.mkdir(parents=True, exist_ok=True)
    endpoint = (endpoint or os.environ.get("HF_ENDPOINT") or DEFAULT_HF_ENDPOINT).rstrip("/")
    print(f"[download] huggingface endpoint: {endpoint}")
    try:
        if snapshot_download is None:
            raise RuntimeError("insecure HF TLS mode uses the mirror streaming client")
        snapshot_download(
                repo_id=repo_id,
                local_dir=str(target),
                allow_patterns=allow_patterns,
                endpoint=endpoint,
            )
    except Exception as exc:
        # Some mirror deployments redirect the Hub API to huggingface.co.  The
        # hub client rejects that host before following the file redirect, so
        # keep the request anchored at the configured mirror and stream files
        # from its resolve endpoint instead.
        if not endpoint or "hf-mirror.com" not in endpoint:
            raise
        try:
            import requests

            if insecure_tls:
                requests.packages.urllib3.disable_warnings()
                print("[download] WARNING: HF mirror TLS certificate verification is disabled")

            api_url = f"{endpoint}/api/models/{repo_id}"
            response = requests.get(api_url, timeout=30, verify=not insecure_tls)
            response.raise_for_status()
            siblings = response.json().get("siblings", [])
            names = [item.get("rfilename", "") for item in siblings]
            if allow_patterns:
                names = [
                    name for name in names
                    if any(fnmatch.fnmatch(name, pattern) for pattern in allow_patterns)
                ]
            if not names:
                raise PipelineError(f"mirror returned no files for {repo_id}")
            for name in names:
                if not name or Path(name).is_absolute() or ".." in Path(name).parts:
                    raise PipelineError(f"unsafe mirror file name: {name!r}")
                destination = target / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                file_url = (
                    f"{endpoint}/{repo_id}/resolve/main/"
                    f"{urllib.parse.quote(name, safe='/')}"
                )
                with requests.get(
                    file_url, stream=True, timeout=60, verify=not insecure_tls
                ) as download_response:
                    download_response.raise_for_status()
                    temporary = destination.with_suffix(destination.suffix + ".part")
                    with temporary.open("wb") as handle:
                        for chunk in download_response.iter_content(chunk_size=1024 * 1024):
                            if chunk:
                                handle.write(chunk)
                    temporary.replace(destination)
                    print(f"[download] mirror file: {repo_id}/{name}")
        except Exception as mirror_exc:
            raise PipelineError(
                f"Hugging Face mirror download failed for {repo_id}: {mirror_exc}"
            ) from exc
    return target


def _copy_tokenizer(snapshot: Path, target: Path, fallback_paths=()):
    # MobileCLIP ships a tokenizer.json, while Chinese-CLIP's pinned source
    # ships the BERT vocabulary as vocab.txt.  Preserve the model-specific
    # filename instead of assuming every text encoder uses one format.
    candidates = []
    for pattern in ("tokenizer.json", "vocab.txt"):
        candidates.extend(sorted(snapshot.rglob(pattern)))
    if not candidates:
        candidates = [Path(value) for value in fallback_paths if Path(value).is_file()]
    if not candidates:
        raise PipelineError(f"tokenizer asset was not found under {snapshot}")
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy2(candidates[0], target / candidates[0].name)


def download(records, hf_endpoint=None, insecure_hf_tls=False):
    _ensure_dirs()
    for record in records:
        raw = _raw_candidate(record.model_id)
        if record.model_id == MOBILECLIP_ID:
            _git_source("ml-mobileclip", raw["source"], raw["revision"])
            _snapshot(
                raw["checkpoint_repository"],
                CHECKPOINT_DIR / "mobileclip_s0",
                endpoint=hf_endpoint,
                insecure_tls=insecure_hf_tls,
            )
            tokenizer_snapshot = _snapshot(
                "openai/clip-vit-base-patch32",
                CHECKPOINT_DIR / "openai_clip_tokenizer",
                allow_patterns=["tokenizer.json", "vocab.json", "merges.txt", "special_tokens_map.json"],
                endpoint=hf_endpoint,
                insecure_tls=insecure_hf_tls,
            )
            _copy_tokenizer(tokenizer_snapshot, TOKENIZER_DIR / "mobileclip_s0")
        elif record.model_id == CHINESE_CLIP_ID:
            _git_source("Chinese-CLIP", raw["source"], raw["revision"])
            snapshot = _snapshot(
                raw["checkpoint_repository"],
                CHECKPOINT_DIR / "chinese_clip_rn50",
                endpoint=hf_endpoint,
                insecure_tls=insecure_hf_tls,
            )
            _copy_tokenizer(
                snapshot,
                TOKENIZER_DIR / "chinese_clip_rn50",
                fallback_paths=[SOURCE_DIR / "Chinese-CLIP" / "cn_clip" / "clip" / "vocab.txt"],
            )
        elif record.model_id == RESNET50_ID:
            print("[download] ResNet50 weights are resolved by torchvision during export")


def _checkpoint(directory: Path):
    candidates = []
    for pattern in ("*.pt", "*.pth", "*.bin"):
        candidates.extend(directory.rglob(pattern))
    candidates = [path for path in candidates if path.is_file()]
    if not candidates:
        raise PipelineError(f"checkpoint was not found under {directory}")
    return max(candidates, key=lambda path: path.stat().st_size)


def _export_resnet(record):
    import torch
    import torchvision.models as models

    model = models.resnet50(weights=models.ResNet50_Weights.IMAGENET1K_V1)
    model.fc = torch.nn.Identity()
    model.eval()
    contract = record.components["image"]
    contract.onnx_path.parent.mkdir(parents=True, exist_ok=True)
    dummy = torch.randn(*contract.input_shape)
    torch.onnx.export(
        model,
        dummy,
        str(contract.onnx_path),
        input_names=[contract.input_name],
        output_names=["embedding"],
        opset_version=13,
        dynamic_axes=None,
    )


def _staticize_mobileclip_image_graph(source_path: Path, target_path: Path) -> dict:
    """Create a diagnostic MHSA shape-folded graph; never use it in production.

    The pinned MobileCLIP image encoder is exported with a fixed 256x256
    input, but the two final MHSA blocks still materialize ``[1, 512, 64]``
    through ``Shape -> Slice -> Concat``.  Some toolkit releases can infer
    that chain incorrectly and subsequently reject or miscompile the image
    graph.
    The rewrite is useful for isolating ATC shape behavior, but it is not
    assumed numerically equivalent to the original graph.  Production export
    therefore keeps the raw ONNX graph and callers must record this helper as
    diagnostic-only evidence.
    """
    try:
        import onnx
        from onnx import numpy_helper
    except ImportError as exc:
        raise PipelineError("MobileCLIP static graph folding requires onnx") from exc

    graph = onnx.load(str(source_path))
    replacements = []
    remove_nodes = set()
    nodes = list(graph.graph.node)
    producer_by_output = {
        output: node for node in nodes for output in node.output if output
    }
    block_suffixes = (
        "/image_encoder/model/network.7/network.7.0/token_mixer/Reshape",
        "/image_encoder/model/network.7/network.7.1/token_mixer/Reshape",
    )
    for node in nodes:
        if node.op_type != "Reshape" or not node.name.endswith(block_suffixes):
            continue
        if len(node.input) != 2:
            raise PipelineError(f"unexpected MobileCLIP MHSA Reshape inputs: {node.name}")
        old_shape = node.input[1]
        shape_producer = producer_by_output.get(old_shape)
        if shape_producer is None or shape_producer.op_type != "Concat":
            raise PipelineError(f"missing dynamic shape producer for {node.name}")

        # Walk only the shape-producing subgraph.  Constants that are still
        # referenced by the attention computation must remain in the graph.
        stack = [shape_producer]
        visited = set()
        while stack:
            current = stack.pop()
            if id(current) in visited:
                continue
            visited.add(id(current))
            remove_nodes.add(id(current))
            # The Shape node consumes the feature tensor.  Its input is the
            # actual network branch and must never be traversed as part of the
            # disposable shape-producing subgraph.
            if current.op_type == "Shape":
                continue
            for input_name in current.input:
                parent = producer_by_output.get(input_name)
                if parent is not None:
                    stack.append(parent)

        initializer_name = (
            node.name.replace("/", "_").replace(".", "_")
            + "_static_shape_1_512_64"
        )
        graph.graph.initializer.append(
            numpy_helper.from_array(
                np.asarray([1, 512, 64], dtype=np.int64),
                name=initializer_name,
            )
        )
        node.input[1] = initializer_name
        replacements.append({"node": node.name, "shape": [1, 512, 64]})

    if len(replacements) != 2:
        raise PipelineError(
            f"expected two MobileCLIP MHSA reshape chains, found {len(replacements)}"
        )
    kept_nodes = [node for node in nodes if id(node) not in remove_nodes]
    graph.graph.ClearField("node")
    graph.graph.node.extend(kept_nodes)
    onnx.checker.check_model(graph)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    onnx.save(graph, str(target_path))
    return {
        "source": str(source_path),
        "target": str(target_path),
        "replacements": replacements,
        "removed_shape_nodes": len(remove_nodes),
        "source_nodes": len(nodes),
        "target_nodes": len(graph.graph.node),
    }


def _export_mobileclip(record):
    import torch

    source = SOURCE_DIR / "ml-mobileclip"
    sys.path.insert(0, str(source))
    try:
        import mobileclip
    except ImportError as exc:
        raise PipelineError("MobileCLIP source dependencies are not installed") from exc
    checkpoint = _checkpoint(CHECKPOINT_DIR / "mobileclip_s0")
    model, _, _ = mobileclip.create_model_and_transforms(
        "mobileclip_s0", pretrained=str(checkpoint)
    )
    # The pinned MobileCLIP loader already builds inference-compatible blocks.
    # Calling the repository's reparameterizer a second time can visit a block
    # after it has been fused and raise ``RepMixer has no mixer``.
    model = model.eval()

    class ImageEncoder(torch.nn.Module):
        def __init__(self, clip_model):
            super().__init__()
            self.clip_model = clip_model

        def forward(self, image):
            return self.clip_model.encode_image(image)

    class TextEncoder(torch.nn.Module):
        def __init__(self, clip_model):
            super().__init__()
            self.clip_model = clip_model

        def forward(self, text):
            return self.clip_model.encode_text(text)

    image_contract = record.components["image"]
    text_contract = record.components["text"]
    # Keep the raw graph as the production contract.  A prior static MHSA
    # rewrite appeared checker-valid but changed the ONNX reference output;
    # it remains a diagnostic helper only and must never replace this file.
    raw_image_path = image_contract.onnx_path.with_name(
        image_contract.onnx_path.stem + ".dynamic.onnx"
    )
    torch.onnx.export(
        ImageEncoder(model).eval(),
        torch.randn(*image_contract.input_shape),
        str(raw_image_path),
        input_names=[image_contract.input_name],
        output_names=["embedding"],
        opset_version=17,
        dynamic_axes=None,
    )
    shutil.copy2(raw_image_path, image_contract.onnx_path)
    (REPORT_DIR / "mobileclip_image_export.json").write_text(
        json.dumps(
            {
                "production_graph": "raw_dynamic_shape_graph",
                "raw": str(raw_image_path),
                "raw_sha256": sha256_file(raw_image_path),
                "production_sha256": sha256_file(image_contract.onnx_path),
                "staticizer_status": "diagnostic_only_not_equivalent",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    torch.onnx.export(
        TextEncoder(model).eval(),
        torch.zeros(*text_contract.input_shape, dtype=torch.long),
        str(text_contract.onnx_path),
        input_names=[text_contract.input_name],
        output_names=["embedding"],
        opset_version=17,
        dynamic_axes=None,
    )


def _export_chinese_clip(record):
    source = SOURCE_DIR / "Chinese-CLIP"
    checkpoint = _checkpoint(CHECKPOINT_DIR / "chinese_clip_rn50")
    prefix = ONNX_DIR / "chinese_clip_rn50"
    env = os.environ.copy()
    env["PYTHONPATH"] = str(source / "cn_clip") + os.pathsep + env.get("PYTHONPATH", "")
    # The pinned exporter uses opset 13, while PyTorch 2.1 emits
    # scaled_dot_product_attention there only from opset 14 onward. Keep the
    # upstream source untouched and run an auditable temporary opset-17 copy.
    upstream_script = source / "cn_clip" / "deploy" / "pytorch_to_onnx.py"
    patched_script = REPORT_DIR / "chinese_clip_pytorch_to_onnx_opset17.py"
    patched_script.parent.mkdir(parents=True, exist_ok=True)
    patched_script.write_text(
        upstream_script.read_text(encoding="utf-8").replace("opset_version=13", "opset_version=17"),
        encoding="utf-8",
    )
    _run(
        [
            sys.executable,
            patched_script,
            "--model-arch",
            "RN50",
            "--pytorch-ckpt-path",
            checkpoint,
            "--save-onnx-path",
            prefix,
            "--convert-text",
            "--convert-vision",
            "--context-length",
            "52",
        ],
        cwd=source,
        env=env,
        log_path=REPORT_DIR / "chinese_clip_export.log",
    )
    generated = {
        "image": Path(str(prefix) + ".img.fp32.onnx"),
        "text": Path(str(prefix) + ".txt.fp32.onnx"),
    }
    for kind, path in generated.items():
        if path.resolve() != record.components[kind].onnx_path.resolve():
            shutil.copy2(path, record.components[kind].onnx_path)


def export(records):
    _ensure_dirs()
    for record in records:
        for component in record.components.values():
            component.onnx_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"[export] {record.model_id}")
        if record.model_id == MOBILECLIP_ID:
            _export_mobileclip(record)
        elif record.model_id == CHINESE_CLIP_ID:
            _export_chinese_clip(record)
        elif record.model_id == RESNET50_ID:
            _export_resnet(record)
        else:
            raise PipelineError(f"no exporter for {record.model_id}")


def _reference_input(record, kind, component):
    rng = np.random.default_rng(310)
    dtype = NUMPY_DTYPES[component.input_dtype]
    if kind == "image":
        return rng.normal(0, 0.25, component.input_shape).astype(dtype)
    if record.model_id == MOBILECLIP_ID:
        high = 49408
    elif record.model_id == CHINESE_CLIP_ID:
        high = 21128
    else:
        high = 1000
    return rng.integers(0, high, component.input_shape, dtype=dtype)


def check(records):
    _ensure_dirs()
    try:
        import onnx
    except ImportError as exc:
        raise PipelineError("check requires onnx") from exc
    try:
        import onnxruntime as ort
    except ImportError:
        # ONNX Runtime is optional on the aarch64 board image.  The ONNX
        # reference evaluator is slower, but it is sufficient for the
        # offline numerical reference gate and is never used by production
        # inference (which is ACL/OM only).
        ort = None
    report = {"generated_at": time.time(), "models": {}}
    for record in records:
        model_report = {}
        for kind, component in record.components.items():
            if not component.onnx_path.is_file():
                raise PipelineError(f"missing ONNX: {component.onnx_path}")
            graph = onnx.load(str(component.onnx_path))
            onnx.checker.check_model(graph)
            reference_backend = "onnxruntime" if ort is not None else "onnx.reference.ReferenceEvaluator"
            if ort is not None:
                try:
                    session = ort.InferenceSession(
                        str(component.onnx_path), providers=["CPUExecutionProvider"]
                    )
                    input_meta = session.get_inputs()[0]
                    if input_meta.name != component.input_name:
                        raise PipelineError(
                            f"{record.model_id}/{kind} input name is {input_meta.name}, expected {component.input_name}"
                        )
                except Exception as exc:
                    # Some board ORT builds omit kernels such as ArgMax(13).
                    # Fall through to the transparent CPU reference evaluator.
                    try:
                        from onnx.reference import ReferenceEvaluator
                    except ImportError as fallback_exc:
                        raise PipelineError(
                            f"ONNX Runtime cannot execute {record.model_id}/{kind}: {exc}"
                        ) from fallback_exc
                    session = ReferenceEvaluator(graph)
                    input_meta = graph.graph.input[0]
                    if input_meta.name != component.input_name:
                        raise PipelineError(
                            f"{record.model_id}/{kind} input name is {input_meta.name}, expected {component.input_name}"
                        )
                    reference_backend = "onnx.reference.ReferenceEvaluator"
            else:
                try:
                    from onnx.reference import ReferenceEvaluator
                except ImportError as exc:
                    raise PipelineError(
                        "check requires onnxruntime or the ONNX reference evaluator"
                    ) from exc
                session = ReferenceEvaluator(graph)
                input_meta = graph.graph.input[0]
                if input_meta.name != component.input_name:
                    raise PipelineError(
                        f"{record.model_id}/{kind} input name is {input_meta.name}, expected {component.input_name}"
                    )
            value = _reference_input(record, kind, component)
            output = np.asarray(session.run(None, {component.input_name: value})[0], dtype=np.float32)
            output = l2_normalize(output)
            if output.size != record.embedding_dim:
                raise PipelineError(
                    f"{record.model_id}/{kind} output dim is {output.size}, expected {record.embedding_dim}"
                )
            reference = REFERENCE_DIR / f"{record.model_id}__{kind}.npz"
            np.savez_compressed(reference, input=value, output=output)
            model_report[kind] = {
                "onnx": _display_path(component.onnx_path),
                "onnx_sha256": sha256_file(component.onnx_path),
                "input_name": component.input_name,
                "input_shape": list(value.shape),
                "input_dtype": str(value.dtype),
                "output_shape": list(output.shape),
                "reference": _display_path(reference),
                "reference_backend": reference_backend,
            }
        report["models"][record.model_id] = model_report
    target = REPORT_DIR / "onnx_check.json"
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[check] wrote {target}")


def convert(
    records,
    soc_version,
    enable_graph_parallel=0,
    component_kind=None,
    allow_low_memory_single_thread=False,
    op_select_implmode="high_precision",
    precision_mode=None,
    use_keep_dtype=True,
    output_om_dir=None,
    keep_dtype_file=None,
    report_dir=None,
    onnx_path=None,
):
    _ensure_dirs()
    explicit_output = output_om_dir is not None
    output_dir = _resolve_user_path(output_om_dir, OM_DIR) if explicit_output else None
    report_root = _report_root_for_conversion(output_om_dir, report_dir)
    _assert_candidate_report_safe(report_root, explicit_output)
    report_root.mkdir(parents=True, exist_ok=True)
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)
    explicit_keep_dtype = (
        _resolve_user_path(keep_dtype_file, ROOT) if keep_dtype_file is not None else None
    )
    if explicit_keep_dtype is not None and not explicit_keep_dtype.is_file():
        raise PipelineError(f"missing keep-dtype configuration: {explicit_keep_dtype}")
    if component_kind not in (None, "image", "text"):
        raise PipelineError("component must be image or text")
    if component_kind is not None and len(records) != 1:
        raise PipelineError("component-scoped conversion requires exactly one model")
    alternate_onnx = None
    if onnx_path is not None:
        if component_kind is None or len(records) != 1:
            raise PipelineError("--onnx-path requires one model and --component")
        alternate_onnx = _resolve_user_path(onnx_path, ROOT)
        if not alternate_onnx.is_file():
            raise PipelineError(f"missing alternate ONNX: {alternate_onnx}")
    # Resolve and reject unsafe destinations before probing ATC or checking
    # board memory.  A malformed candidate command must have no compiler-side
    # effects and must never be able to overwrite the production OM.
    for _selected_record, _selected_kind, selected_component in _conversion_components(
        records, component_kind
    ):
        destination = _candidate_om_path(selected_component, output_dir)
        _assert_candidate_output_safe(selected_component, destination, explicit_output)
    if shutil.which("atc") is None:
        raise PipelineError("atc is unavailable; run convert on the Ascend board after sourcing CANN")
    parallel_option, parallel_policy = _atc_parallel_option(enable_graph_parallel)
    if op_select_implmode not in (
        "high_precision",
        "high_performance",
        "high_precision_for_all",
        "high_performance_for_all",
    ):
        raise PipelineError("unsupported ATC operator implementation policy")
    if precision_mode is not None and precision_mode not in (
        "allow_fp32_to_fp16",
        "allow_mix_precision",
        "force_fp32",
        "force_fp16",
        "must_keep_origin_dtype",
    ):
        raise PipelineError("unsupported ATC precision mode")
    compile_env = _serial_compile_environment()
    memory = _host_memory_snapshot()
    low_memory = bool(
        memory
        and memory["swap_total_bytes"] == 0
        and memory["mem_total_bytes"] < 10 * 1024**3
    )
    if low_memory and not allow_low_memory_single_thread:
        report = {
            "generated_at": time.time(),
            "soc_version": soc_version,
            "compile_environment": SERIAL_ATC_ENV,
            "status": "blocked_low_memory_no_swap",
            "component_kind": component_kind,
            "host_memory": memory,
            "models": {},
        }
        target = report_root / "atc_conversion.json"
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        raise PipelineError(
            "ATC blocked: host has less than 10 GiB RAM and no swap; "
            "use --allow-low-memory-single-thread for one explicit component, "
            "or use a larger board before production conversion"
        )
    if low_memory and allow_low_memory_single_thread and (component_kind is None or len(records) != 1):
        raise PipelineError(
            "low-memory override requires one model and --component image or text"
        )
    report = {
        "generated_at": time.time(),
        "soc_version": soc_version,
        "compile_environment": SERIAL_ATC_ENV,
        "host_memory": memory,
        "component_kind": component_kind,
        "low_memory_override": bool(low_memory and allow_low_memory_single_thread),
        "op_select_implmode": op_select_implmode,
        "precision_mode": precision_mode,
        "alternate_onnx": _display_path(alternate_onnx) if alternate_onnx else None,
        "parallel_option": parallel_option,
        "parallel_policy": parallel_policy,
        "output_om_dir": _display_path(output_dir) if output_dir is not None else _display_path(OM_DIR),
        "report_dir": _display_path(report_root),
        "models": {},
    }
    target = report_root / "atc_conversion.json"
    with _atc_lock(ATC_LOCK_ROOT):
        for record in records:
            model_report = report["models"].setdefault(record.model_id, {"components": {}})
            selected_components = _conversion_components((record,), component_kind)
            for _selected_record, kind, component in selected_components:
                source_onnx = alternate_onnx if alternate_onnx is not None else component.onnx_path
                if not source_onnx.is_file():
                    raise PipelineError(f"missing ONNX: {source_onnx}")
                destination = _candidate_om_path(component, output_dir)
                _assert_candidate_output_safe(component, destination, explicit_output)
                output_prefix = destination.with_suffix("")
                output_prefix.parent.mkdir(parents=True, exist_ok=True)
                shape = ",".join(str(value) for value in component.input_shape)
                effective_precision = record.effective_precision_mode(component, precision_mode)
                command = [
                    "atc",
                    f"--model={source_onnx}",
                    "--framework=5",
                    f"--output={output_prefix}",
                    f"--soc_version={soc_version}",
                    f"--input_shape={component.input_name}:{shape}",
                    f"--precision_mode={effective_precision}",
                    f"--op_select_implmode={op_select_implmode}",
                    "--op_compiler_cache_mode=disable",
                ]
                if parallel_option:
                    command.insert(-2, parallel_option)
                keep_dtype = (
                    explicit_keep_dtype or component.atc_keep_dtype
                    if use_keep_dtype and effective_precision not in {
                        "must_keep_origin_dtype",
                        "force_fp32",
                        "force_fp16",
                    }
                    else None
                )
                if keep_dtype is not None:
                    if not keep_dtype.is_file():
                        raise PipelineError(
                            f"missing keep-dtype configuration: {keep_dtype}"
                        )
                    command.append(f"--keep_dtype={keep_dtype}")
                log_path = report_root / "atc" / f"{record.model_id}__{kind}.log"
                component_report = {
                    "command": command,
                    "log": _display_path(log_path),
                    "output_om": _display_path(destination),
                    "onnx": _display_path(source_onnx),
                    "onnx_sha256": sha256_file(source_onnx),
                    "parallel_option": parallel_option,
                    "parallel_policy": parallel_policy,
                    "precision_mode": effective_precision,
                    "status": "failed",
                }
                if keep_dtype is not None:
                    component_report["keep_dtype"] = _display_path(keep_dtype)
                    component_report["keep_dtype_sha256"] = sha256_file(keep_dtype)
                model_report["components"][kind] = component_report
                try:
                    _run(command, env=compile_env, log_path=log_path)
                except PipelineError as exc:
                    component_report["error"] = str(exc)
                    target.write_text(
                        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    raise
                if not destination.is_file():
                    error = f"ATC returned success but OM is missing: {destination}"
                    component_report["error"] = error
                    target.write_text(
                        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8",
                    )
                    raise PipelineError(error)
                component_report.update(
                    {
                        "status": "passed",
                        "om": _display_path(destination),
                        "om_sha256": sha256_file(destination),
                        "om_size": destination.stat().st_size,
                    }
                )
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[convert] wrote {target}")


def _cosine(left, right):
    left = l2_normalize(left)
    right = l2_normalize(right)
    return float(np.dot(left, right))


def validate(records, admit=False):
    _ensure_dirs()
    resource = AscendResource()
    results = {"generated_at": time.time(), "threshold": NUMERICAL_THRESHOLD, "models": {}}
    try:
        for record in records:
            model_result = {"passed": True, "components": {}}
            for kind, component in record.components.items():
                reference_path = REFERENCE_DIR / f"{record.model_id}__{kind}.npz"
                if not reference_path.is_file():
                    raise PipelineError(f"missing numerical reference: {reference_path}")
                if not component.om_path.is_file():
                    raise PipelineError(f"missing OM: {component.om_path}")
                reference = np.load(reference_path)
                model = AclModel(resource, component.om_path)
                try:
                    output_contracts = model.output_contracts()
                    if len(output_contracts) != 1:
                        raise PipelineError(
                            f"{record.model_id}/{kind} returned {len(output_contracts)} OM outputs, expected 1"
                        )
                    actual_contract = output_contracts[0]
                    expected_dtype_code = ACL_DTYPE_CODES[component.output_dtype]
                    expected_bytes = record.embedding_dim * np.dtype(
                        NUMPY_DTYPES[component.output_dtype]
                    ).itemsize
                    if actual_contract["acl_dtype"] != expected_dtype_code:
                        raise PipelineError(
                            f"{record.model_id}/{kind} OM dtype code is {actual_contract['acl_dtype']}, "
                            f"expected {expected_dtype_code} for {component.output_dtype}"
                        )
                    if actual_contract["size"] != expected_bytes:
                        raise PipelineError(
                            f"{record.model_id}/{kind} OM output is {actual_contract['size']} bytes, "
                            f"expected {expected_bytes}"
                        )
                    raw = model.execute([reference["input"]])[0]
                finally:
                    model.release()
                dtype = NUMPY_DTYPES[component.output_dtype]
                output = np.frombuffer(raw, dtype=dtype).astype(np.float32)
                if output.size != record.embedding_dim:
                    raise PipelineError(
                        f"{record.model_id}/{kind} returned {output.size} values, expected {record.embedding_dim}"
                    )
                cosine = _cosine(output, reference["output"])
                passed = np.isfinite(output).all() and cosine >= NUMERICAL_THRESHOLD
                model_result["passed"] = bool(model_result["passed"] and passed)
                model_result["components"][kind] = {
                    "passed": bool(passed),
                    "cosine_similarity": cosine,
                    "om_sha256": sha256_file(component.om_path),
                    "om_size": component.om_path.stat().st_size,
                    "om_output_bytes": actual_contract["size"],
                    "om_output_acl_dtype": actual_contract["acl_dtype"],
                }
            results["models"][record.model_id] = model_result
    finally:
        resource.release()
    target = REPORT_DIR / "acl_numerical_validation.json"
    target.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    failed = [model_id for model_id, value in results["models"].items() if not value["passed"]]
    if failed:
        raise PipelineError(f"numerical gate failed: {', '.join(failed)}")
    if admit:
        _write_registry(records, results)
    print(f"[validate] wrote {target}")


def _candidate_reference_paths(model_id: str, component_kind: str, reference_dir) -> list[Path]:
    """Resolve one or more immutable NPZ references for a candidate check."""
    root = _resolve_user_path(reference_dir, REFERENCE_DIR)
    if root.is_file():
        return [root]
    exact = root / f"{model_id}__{component_kind}.npz"
    if exact.is_file():
        # Keep the canonical reference first, then include optional fixed
        # fixtures such as ``__image__sample-0001.npz``.
        matches = sorted(root.glob(f"{model_id}__{component_kind}*.npz"))
        return [exact] + [path for path in matches if path != exact]
    return sorted(root.glob(f"{model_id}__{component_kind}*.npz"))


def validate_candidate(
    records,
    component_kind="image",
    om_path=None,
    report_path=None,
    reference_dir=None,
):
    """Run ACL numerical validation against an arbitrary candidate OM.

    This deliberately does not call ``_write_registry`` and never changes the
    model manifest.  It is intended for board-side precision sweeps where each
    candidate lives outside ``models/om`` and has its own evidence report.
    """
    if component_kind not in ("image", "text"):
        raise PipelineError("candidate component must be image or text")
    if not records or len(records) != 1:
        raise PipelineError("candidate validation requires exactly one model")
    if om_path is None:
        raise PipelineError("candidate validation requires --om")
    record = records[0]
    component = record.components.get(component_kind)
    if component is None:
        raise PipelineError(f"{record.model_id} does not support {component_kind} encoding")
    candidate_om = _resolve_user_path(om_path, component.om_path)
    if not candidate_om.is_file():
        raise PipelineError(f"missing candidate OM: {candidate_om}")
    references = _candidate_reference_paths(
        record.model_id,
        component_kind,
        reference_dir if reference_dir is not None else REFERENCE_DIR,
    )
    if not references:
        raise PipelineError(
            f"no numerical references for {record.model_id}/{component_kind} under "
            f"{reference_dir or REFERENCE_DIR}"
        )
    target = (
        _resolve_user_path(report_path, REPORT_DIR / "candidate_validation.json")
        if report_path is not None
        else REPORT_DIR / "candidate_validation.json"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    results = {
        "generated_at": time.time(),
        "threshold": NUMERICAL_THRESHOLD,
        "model_id": record.model_id,
        "component": component_kind,
        "candidate_om": _display_path(candidate_om),
        "candidate_om_sha256": sha256_file(candidate_om),
        "candidate_om_size": candidate_om.stat().st_size,
        "references": [],
        "passed": False,
    }
    resource = None
    model = None
    try:
        resource = AscendResource()
        model = AclModel(resource, candidate_om)
        output_contracts = model.output_contracts()
        if len(output_contracts) != 1:
            raise PipelineError(
                f"{record.model_id}/{component_kind} returned {len(output_contracts)} OM outputs, expected 1"
            )
        actual_contract = output_contracts[0]
        expected_dtype_code = ACL_DTYPE_CODES[component.output_dtype]
        expected_bytes = record.embedding_dim * np.dtype(
            NUMPY_DTYPES[component.output_dtype]
        ).itemsize
        contract_error = None
        if actual_contract["acl_dtype"] != expected_dtype_code:
            contract_error = (
                f"OM dtype code is {actual_contract['acl_dtype']}, expected {expected_dtype_code}"
            )
        elif actual_contract["size"] != expected_bytes:
            contract_error = (
                f"OM output is {actual_contract['size']} bytes, expected {expected_bytes}"
            )
        results["om_output"] = {
            "size": actual_contract["size"],
            "acl_dtype": actual_contract["acl_dtype"],
            "expected_size": expected_bytes,
            "expected_acl_dtype": expected_dtype_code,
        }
        if contract_error:
            raise PipelineError(contract_error)
        dtype = NUMPY_DTYPES[component.output_dtype]
        for reference_path in references:
            sample = {"reference": _display_path(reference_path), "passed": False}
            try:
                with np.load(reference_path) as reference:
                    if "input" not in reference or "output" not in reference:
                        raise PipelineError("reference NPZ must contain input and output arrays")
                    input_value = np.asarray(reference["input"], dtype=NUMPY_DTYPES[component.input_dtype])
                    expected_output = np.asarray(reference["output"], dtype=np.float32).reshape(-1)
                if tuple(input_value.shape) != tuple(component.input_shape):
                    raise PipelineError(
                        f"reference input shape {input_value.shape} does not match {component.input_shape}"
                    )
                if expected_output.size != record.embedding_dim:
                    raise PipelineError(
                        f"reference output has {expected_output.size} values, expected {record.embedding_dim}"
                    )
                raw = model.execute([input_value])[0]
                output = np.frombuffer(raw, dtype=dtype).astype(np.float32)
                finite = bool(np.isfinite(output).all())
                cosine = _cosine(output, expected_output) if finite else float("nan")
                sample.update(
                    {
                        "input_shape": list(input_value.shape),
                        "output_dim": int(output.size),
                        "finite": finite,
                        "cosine_similarity": cosine,
                        "passed": bool(finite and output.size == record.embedding_dim and cosine >= NUMERICAL_THRESHOLD),
                    }
                )
            except Exception as exc:
                sample["error"] = str(exc)
            results["references"].append(sample)
    except Exception as exc:
        results["error"] = str(exc)
    finally:
        if model is not None:
            model.release()
        if resource is not None:
            resource.release()
    results["passed"] = bool(
        "error" not in results
        and results["references"]
        and all(item.get("passed", False) for item in results["references"])
    )
    target.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"[validate-candidate] wrote {target}")
    if not results["passed"]:
        raise PipelineError(f"candidate numerical gate failed: {record.model_id}/{component_kind}")
    return results


def _write_registry(records, validation):
    candidates = {value["model_id"]: value for value in _candidate_payload()["models"]}
    admitted = []
    for record in records:
        value = deepcopy(candidates[record.model_id])
        value["status"] = "admitted"
        for kind, component in record.components.items():
            value["components"][kind]["onnx_sha256"] = sha256_file(component.onnx_path)
            value["components"][kind]["om_sha256"] = validation["models"][record.model_id]["components"][kind]["om_sha256"]
        admitted.append(value)
    existing = {value["model_id"]: value for value in []}
    if DEFAULT_REGISTRY.is_file():
        payload = json.loads(DEFAULT_REGISTRY.read_text(encoding="utf-8"))
        existing = {value["model_id"]: value for value in payload.get("models", [])}
    for value in admitted:
        existing[value["model_id"]] = value
    payload = {
        "schema_version": 1,
        "generated_at": time.time(),
        "hardware": {"soc_version": "Ascend310B4", "cann": _detect_cann_version()},
        "models": list(existing.values()),
    }
    DEFAULT_REGISTRY.parent.mkdir(parents=True, exist_ok=True)
    DEFAULT_REGISTRY.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[admit] wrote {DEFAULT_REGISTRY}")


def parser():
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--model",
        default="all",
        help="all, one model id, or comma-separated model ids",
    )
    common.add_argument(
        "--hf-endpoint",
        default=os.environ.get("HF_ENDPOINT", DEFAULT_HF_ENDPOINT),
        help="Hugging Face endpoint used by download (default: hf-mirror.com)",
    )
    common.add_argument(
        "--insecure-hf-tls",
        action="store_true",
        help="disable TLS certificate verification for HF mirror requests only",
    )
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("download", parents=[common], help="pin sources and download checkpoints/tokenizers")
    commands.add_parser("export", parents=[common], help="export fixed-shape FP32 ONNX models")
    commands.add_parser("check", parents=[common], help="validate ONNX contracts and write numerical references")
    conversion = commands.add_parser("convert", parents=[common], help="convert ONNX to OM with board ATC")
    conversion.add_argument("--soc-version", default="Ascend310B4")
    conversion.add_argument(
        "--enable-graph-parallel",
        type=int,
        choices=(0,),
        default=0,
        help="ATC graph parallelism (fixed at 0 on low-memory 310B boards)",
    )
    conversion.add_argument(
        "--without-keep-dtype",
        action="store_true",
        help="omit the candidate keep-dtype exception for a controlled diagnostic",
    )
    conversion.add_argument(
        "--output-om-dir",
        help=(
            "isolated directory for candidate OM files; explicit paths resolving to "
            "a production OM are refused"
        ),
    )
    conversion.add_argument(
        "--keep-dtype-file",
        help="explicit keep-dtype whitelist for a candidate conversion",
    )
    conversion.add_argument(
        "--report-dir",
        help="isolated report directory for candidate ATC logs and atc_conversion.json",
    )
    conversion.add_argument(
        "--onnx-path",
        help=(
            "explicit alternate ONNX for a component-scoped diagnostic; the file is "
            "never copied into the production model directory"
        ),
    )
    conversion.add_argument(
        "--precision-mode",
        choices=(
            "allow_fp32_to_fp16",
            "allow_mix_precision",
            "force_fp32",
            "force_fp16",
            "must_keep_origin_dtype",
        ),
        help="override the candidate precision mode for a controlled board diagnostic",
    )
    conversion.add_argument(
        "--op-select-implmode",
        choices=(
            "high_precision",
            "high_performance",
            "high_precision_for_all",
            "high_performance_for_all",
        ),
        default="high_precision",
        help="ATC operator implementation policy; high_precision is the production default",
    )
    conversion.add_argument(
        "--component",
        choices=("image", "text"),
        help="convert one component only; required with the low-memory override",
    )
    conversion.add_argument(
        "--allow-low-memory-single-thread",
        action="store_true",
        help="explicitly allow one component on a low-memory, swap-free board; never converts in parallel",
    )
    validation = commands.add_parser("validate", parents=[common], help="run ACL numerical validation")
    validation.add_argument("--admit", action="store_true", help="write passing models to production registry")
    candidate_validation = commands.add_parser(
        "validate-candidate",
        parents=[common],
        help="run ACL numerical validation for one non-production candidate OM",
    )
    candidate_validation.add_argument("--component", required=True, choices=("image", "text"))
    candidate_validation.add_argument("--om", required=True, help="candidate OM path; never promotes the registry")
    candidate_validation.add_argument(
        "--report",
        required=True,
        help="candidate JSON report path",
    )
    candidate_validation.add_argument(
        "--reference-dir",
        default=str(REFERENCE_DIR),
        help="directory containing fixed NPZ input/output references",
    )
    return root


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        records = _selected(args.model)
        if args.command == "download":
            download(
                records,
                hf_endpoint=args.hf_endpoint,
                insecure_hf_tls=args.insecure_hf_tls,
            )
        elif args.command == "export":
            export(records)
        elif args.command == "check":
            check(records)
        elif args.command == "convert":
            convert(
                records,
                args.soc_version,
                args.enable_graph_parallel,
                component_kind=args.component,
                allow_low_memory_single_thread=args.allow_low_memory_single_thread,
                op_select_implmode=args.op_select_implmode,
                precision_mode=args.precision_mode,
                use_keep_dtype=not args.without_keep_dtype,
                output_om_dir=args.output_om_dir,
                keep_dtype_file=args.keep_dtype_file,
                report_dir=args.report_dir,
                onnx_path=args.onnx_path,
            )
        elif args.command == "validate":
            validate(records, admit=args.admit)
        elif args.command == "validate-candidate":
            validate_candidate(
                records,
                component_kind=args.component,
                om_path=args.om,
                report_path=args.report,
                reference_dir=args.reference_dir,
            )
    except (PipelineError, RegistryError) as exc:
        print(f"[error] {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
