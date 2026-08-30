#!/usr/bin/env python3
"""Compare a Qwen2.5 static-KV ONNX graph with a CPU Transformers reference.

This is a controller-only numerical gate.  It intentionally refuses to import
ACL/CANN/Torch-NPU and never runs on the board.  The graph is executed one
token at a time with a fresh fixed-capacity cache; the reference uses the
Transformers ``StaticCache`` with the same cache position and mask.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import sys
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


MODEL_ID = "qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om"
SEQUENCE_LENGTH = 1024
VOCABULARY_SIZE = 151936
NUM_LAYERS = 24
NUM_KV_HEADS = 2
HEAD_DIM = 64
TOLERANCE_DEFAULT = 0.5
COSINE_THRESHOLD_DEFAULT = 0.999
TOPK_OVERLAP_DEFAULT = 0.8


class ComparisonError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_contract(path: Path) -> Mapping[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ComparisonError(f"cannot read contract: {path}") from exc
    if not isinstance(document, Mapping):
        raise ComparisonError("contract must be a JSON object")
    section = document.get("acl_om", document)
    if not isinstance(section, Mapping):
        raise ComparisonError("contract ACL section must be an object")
    if section.get("execution_mode") not in {"static_kv_token_fp32", "static_kv_token"}:
        raise ComparisonError("contract is not a static-KV graph")
    if int(section.get("static_sequence_length", -1)) != SEQUENCE_LENGTH:
        raise ComparisonError("contract sequence length is not 1024")
    inputs = section.get("inputs")
    outputs = section.get("outputs")
    if not isinstance(inputs, list) or not isinstance(outputs, list):
        raise ComparisonError("contract is missing descriptor inputs/outputs")
    return section


def _descriptor_items(section: Mapping[str, Any], role: str, *, inputs: bool) -> List[Mapping[str, Any]]:
    key = "inputs" if inputs else "outputs"
    values = section.get(key, [])
    result = [item for item in values if isinstance(item, Mapping) and item.get("role") == role]
    result.sort(key=lambda item: int(item.get("cache_index", 0)))
    return result


def _require_external_environment() -> None:
    forbidden = ("acl", "torch_npu", "mindspore", "mindtorch", "vllm", "mindie")
    present = [name for name in forbidden if importlib.util.find_spec(name) is not None]
    if present:
        raise ComparisonError("controller environment contains board packages: " + ", ".join(present))


def _topk(values: Any, k: int = 5) -> List[int]:
    import numpy as np

    array = np.asarray(values).reshape(-1)
    if array.size == 0 or k <= 0 or not bool(np.isfinite(array).all()):
        raise ComparisonError("logits must be a non-empty finite vector")
    k = min(int(k), int(array.size))
    return [int(value) for value in np.argsort(array)[-k:][::-1]]


def _cosine(left: Any, right: Any) -> float:
    import numpy as np

    a = np.asarray(left, dtype=np.float64).reshape(-1)
    b = np.asarray(right, dtype=np.float64).reshape(-1)
    if a.shape != b.shape or a.size == 0 or not bool(np.isfinite(a).all()) or not bool(np.isfinite(b).all()):
        raise ComparisonError("cosine inputs must be same-shaped finite vectors")
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator == 0.0:
        return 1.0 if np.array_equal(a, b) else 0.0
    return float(np.dot(a, b) / denominator)


def _messages(prompt: str) -> List[Dict[str, str]]:
    if not isinstance(prompt, str) or not prompt.strip():
        raise ComparisonError("prompt must be non-empty")
    return [{"role": "user", "content": prompt}]


def compare(
    model_dir: Path,
    onnx_path: Path,
    tokenizer_path: Path,
    tokenizer_config: Path,
    contract_path: Path,
    prompts: Sequence[str],
    *,
    max_steps: int = 4,
    tolerance: float = TOLERANCE_DEFAULT,
    cosine_threshold: float = COSINE_THRESHOLD_DEFAULT,
    topk_overlap_threshold: float = TOPK_OVERLAP_DEFAULT,
) -> Dict[str, Any]:
    _require_external_environment()
    if isinstance(max_steps, bool) or not isinstance(max_steps, int) or not 1 <= max_steps <= SEQUENCE_LENGTH:
        raise ComparisonError(f"max_steps must be between 1 and {SEQUENCE_LENGTH}")
    if isinstance(tolerance, bool) or not isinstance(tolerance, (int, float)) or not math.isfinite(float(tolerance)) or not float(tolerance) >= 0.0:
        raise ComparisonError("tolerance must be a non-negative finite number")
    if isinstance(cosine_threshold, bool) or not isinstance(cosine_threshold, (int, float)) or not math.isfinite(float(cosine_threshold)) or not 0.0 <= float(cosine_threshold) <= 1.0:
        raise ComparisonError("cosine_threshold must be between 0 and 1")
    if isinstance(topk_overlap_threshold, bool) or not isinstance(topk_overlap_threshold, (int, float)) or not math.isfinite(float(topk_overlap_threshold)) or not 0.0 <= float(topk_overlap_threshold) <= 1.0:
        raise ComparisonError("topk_overlap_threshold must be between 0 and 1")
    if not prompts:
        raise ComparisonError("at least one prompt is required")
    if not onnx_path.is_file() or not tokenizer_path.is_file() or not model_dir.is_dir():
        raise ComparisonError("model, ONNX, tokenizer and contract paths must exist")
    section = _load_contract(contract_path)
    cache_inputs = _descriptor_items(section, "kv_cache", inputs=True)
    cache_outputs = _descriptor_items(section, "kv_cache", inputs=False)
    logits_candidates = [item for item in section["outputs"] if item.get("role") == "logits"]
    if len(cache_inputs) != NUM_LAYERS * 2 or len(cache_outputs) != NUM_LAYERS * 2 or len(logits_candidates) != 1:
        raise ComparisonError("contract must contain 48 cache inputs, 48 cache outputs and one logits output")
    if any(tuple(item.get("shape", ())) != (1, 2, SEQUENCE_LENGTH, HEAD_DIM) for item in cache_inputs):
        raise ComparisonError("cache input shape is not [1,2,1024,64]")
    if any(tuple(item.get("shape", ())) != (1, 1, NUM_KV_HEADS, HEAD_DIM) for item in cache_outputs):
        raise ComparisonError("cache output shape is not [1,1,2,64]")

    try:
        import numpy as np
        import onnxruntime as ort
        import torch
        from transformers import AutoModelForCausalLM
        from transformers.cache_utils import StaticCache
        from qwen25_kv_tokenizer import Qwen25Tokenizer
    except ImportError as exc:
        raise ComparisonError("sci-agent requires numpy, onnxruntime, torch, transformers and tokenizers") from exc

    # The admitted single-file export stores model weights in FP16 to remain
    # below protobuf's 2 GiB limit. Match that precision in the CPU reference;
    # public graph inputs/outputs and the resident KV contract remain FP32.
    model = AutoModelForCausalLM.from_pretrained(
        str(model_dir), local_files_only=True, trust_remote_code=False, attn_implementation="eager"
    ).eval().half()
    model.config.use_cache = True
    tokenizer = Qwen25Tokenizer(tokenizer_path, tokenizer_config)
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    session_inputs = {item.name for item in session.get_inputs()}
    session_outputs = [item.name for item in session.get_outputs()]
    expected_inputs = ["input_ids", "attention_mask", "position_ids"] + [str(item["name"]) for item in cache_inputs]
    missing = [name for name in expected_inputs if name not in session_inputs]
    if missing:
        raise ComparisonError("ONNX session is missing contract inputs: " + ", ".join(missing[:5]))
    logits_name = str(logits_candidates[0]["name"])
    if logits_name not in session_outputs:
        raise ComparisonError("ONNX session is missing the contract logits output")
    output_names = [str(item["name"]) for item in cache_outputs]
    if any(name not in session_outputs for name in output_names):
        raise ComparisonError("ONNX session is missing a contract cache output")

    rows: List[Dict[str, Any]] = []
    for prompt in prompts:
        token_ids = [int(value) for value in tokenizer.encode_messages(_messages(prompt))]
        if len(token_ids) + max_steps > SEQUENCE_LENGTH:
            raise ComparisonError("prompt plus comparison steps exceeds 1024 tokens")
        cache = {str(item["name"]): np.zeros(tuple(int(x) for x in item["shape"]), dtype=np.float32) for item in cache_inputs}
        reference_cache = StaticCache(config=model.config, batch_size=1, max_cache_len=SEQUENCE_LENGTH, device="cpu", dtype=torch.float16)
        prompt_rows: List[Dict[str, Any]] = []
        next_token = None
        for step, token_id in enumerate(token_ids[:max_steps]):
            feed: Dict[str, Any] = {
                "input_ids": np.asarray([[token_id]], dtype=np.int64),
                "attention_mask": np.concatenate(
                    (np.ones((1, step + 1), dtype=np.int64), np.zeros((1, SEQUENCE_LENGTH - step - 1), dtype=np.int64)), axis=1
                ),
                "position_ids": np.asarray([[step]], dtype=np.int64),
            }
            feed.update(cache)
            observed = session.run([logits_name, *output_names], feed)
            observed_logits = np.asarray(observed[0], dtype=np.float32).reshape(1, -1)
            with torch.inference_mode():
                reference = model(
                    input_ids=torch.tensor([[token_id]], dtype=torch.long),
                    attention_mask=torch.tensor(feed["attention_mask"], dtype=torch.long),
                    position_ids=torch.tensor([[step]], dtype=torch.long),
                    cache_position=torch.tensor([step], dtype=torch.long),
                    past_key_values=reference_cache,
                    use_cache=True,
                    return_dict=True,
                    num_logits_to_keep=1,
                )
            reference_logits = reference.logits.detach().cpu().numpy().astype(np.float32).reshape(1, -1)
            if observed_logits.shape != reference_logits.shape or not bool(np.isfinite(observed_logits).all()) or not bool(np.isfinite(reference_logits).all()):
                raise ComparisonError("reference and ONNX logits must be finite vectors with equal shape")
            max_abs = float(np.max(np.abs(observed_logits - reference_logits)))
            observed_top = _topk(observed_logits)
            reference_top = _topk(reference_logits)
            topk_overlap = len(set(observed_top) & set(reference_top)) / float(len(reference_top))
            cosine = _cosine(observed_logits, reference_logits)
            reference_next_token = int(np.argmax(reference_logits))
            for index, output_item in enumerate(cache_outputs):
                input_item = cache_inputs[index]
                target = cache[str(input_item["name"])]
                token_cache = np.asarray(observed[index + 1], dtype=np.float32)
                if token_cache.shape != (1, 1, NUM_KV_HEADS, HEAD_DIM):
                    raise ComparisonError("ONNX token cache output has an unexpected shape")
                # The graph exposes [B,1,H,D], while its resident input cache
                # uses the native Qwen [B,H,S,D] layout.
                target[:, :, step : step + 1, :] = np.transpose(token_cache, (0, 2, 1, 3))
            next_token = int(np.argmax(observed_logits))
            prompt_rows.append({"step": step, "token_id": token_id, "max_abs_logit_diff": max_abs, "top5_equal": observed_top == reference_top, "top5_overlap": topk_overlap, "cosine": cosine, "top1_equal": observed_top[0] == reference_top[0], "onnx_top5": observed_top, "reference_top5": reference_top, "next_token": next_token, "reference_next_token": reference_next_token, "next_token_equal": next_token == reference_next_token})
        rows.append({"prompt": prompt, "prompt_tokens": len(token_ids), "steps": prompt_rows})

    failures = [
        row
        for prompt in rows
        for row in prompt["steps"]
        if row["max_abs_logit_diff"] > tolerance
        or not row["next_token_equal"]
        or row["cosine"] < cosine_threshold
        or row["top5_overlap"] < topk_overlap_threshold
    ]
    return {
        "schema_version": 1,
        "status": "passed" if not failures else "failed",
        "model_id": MODEL_ID,
        "sequence_length": SEQUENCE_LENGTH,
        "tolerance": tolerance,
        "cosine_threshold": cosine_threshold,
        "topk_overlap_threshold": topk_overlap_threshold,
        "reference_weights_precision": "fp16",
        "onnx": {"path": str(onnx_path), "bytes": onnx_path.stat().st_size, "sha256": _sha256(onnx_path)},
        "contract": {"path": str(contract_path), "sha256": _sha256(contract_path)},
        "prompts": rows,
        "failure_count": len(failures),
    }


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--onnx", required=True, type=Path)
    parser.add_argument("--tokenizer", required=True, type=Path)
    parser.add_argument("--tokenizer-config", required=True, type=Path)
    parser.add_argument("--contract", required=True, type=Path)
    parser.add_argument("--prompt", action="append", required=True)
    parser.add_argument("--max-steps", type=int, default=4)
    parser.add_argument("--tolerance", type=float, default=TOLERANCE_DEFAULT)
    parser.add_argument("--cosine-threshold", type=float, default=COSINE_THRESHOLD_DEFAULT)
    parser.add_argument("--topk-overlap-threshold", type=float, default=TOPK_OVERLAP_DEFAULT)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    try:
        result = compare(args.model, args.onnx, args.tokenizer, args.tokenizer_config, args.contract, args.prompt, max_steps=args.max_steps, tolerance=args.tolerance, cosine_threshold=args.cosine_threshold, topk_overlap_threshold=args.topk_overlap_threshold)
    except (ComparisonError, OSError, ValueError) as exc:
        print(f"static-KV CPU comparison failed: {exc}", file=sys.stderr)
        return 1
    data = json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.report.with_name(args.report.name + ".tmp")
        temporary.write_text(data + "\n", encoding="utf-8")
        temporary.replace(args.report)
    print(data)
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
