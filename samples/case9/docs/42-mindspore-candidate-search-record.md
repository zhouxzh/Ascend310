# MindSpore candidate search record

_Metadata-only inventory supporting the strict 8T campaign; reviewed 2026-09-05. No model file was downloaded or loaded from this list._

---

## 🔍 Selection method

Candidates were selected from official Hugging Face model pages and the existing
MindNLP/Orange Pi loader observations. A model card, a Safetensors file, or a visible
MindSpore tag is not hardware evidence. Before any candidate can become executable, the
board must pass a loader preflight, artifact size/SHA-256 lock, Ascend placement check,
and the full Case9 API and quality gates.

The six entries below are therefore registered as `candidate_kind=conditional` with
`runtime.provider=unsupported`. Their metadata is useful for a future, separately
approved experiment; the current downloader, worker and model switcher must refuse
them.

## 📋 Candidate metadata

| Profile | Official source | Pinned revision | License | Expected format | Current decision |
| --- | --- | --- | --- | --- | --- |
| `qwen2-0.5b-instruct-mindspore` | [Qwen2 0.5B Instruct][qwen2-05] | `befdd363d82258f10fa0cbf9f76a4210347dd432` | Apache-2.0 | HF Safetensors | `not-run`; loader and hashes pending |
| `qwen2-1.5b-instruct-mindspore` | [Qwen2 1.5B Instruct][qwen2-15] | `b139ca2b8d7ba579ed727ffcd7060075666a808b` | Apache-2.0 | HF Safetensors | `not-run`; memory and loader gates pending |
| `qwen2-math-1.5b-instruct-mindspore` | [Qwen2 Math 1.5B][qwen2-math] | `ce87e31b88f4dde584314cda301a6b11badf2ea7` | Apache-2.0 | HF Safetensors | `blocked`; math-specialized, no general-chat route |
| `qwen2.5-coder-0.5b-instruct-mindspore` | [Qwen2.5 Coder 0.5B][qwen25-coder] | `ea99d3edbfc6669b8b24cbaa6a98ec0e857f0155` | Apache-2.0 | HF Safetensors | `not-run`; loader and hashes pending |
| `qwen2.5-math-1.5b-instruct-mindspore` | [Qwen2.5 Math 1.5B][qwen25-math] | `f903dd76e2a9741d582f2a31248f1f5d0ac0e2bf` | Apache-2.0 | HF Safetensors | `blocked`; math-specialized, no general-chat route |
| `llama3.2-1b-instruct-mindspore` | [Llama 3.2 1B Instruct][llama32] | `main` (mutable) | Llama-3.2-Community | HF Safetensors | `blocked`; gated access and no immutable artifact lock |

The revisions for the five Qwen entries are immutable commit identifiers recorded in
the registry. The Llama repository is gated and its revision is intentionally left
mutable until access is separately approved; this is not a download authorization.
Artifact byte counts and SHA-256 values are `null` by design and must remain so until a
verified `.part -> size -> hash -> atomic rename` download occurs.

## 🛡️ Compatibility and license boundaries

The current board environment is a shared MindSpore/MindNLP `base` environment. The
candidate policy forbids installing Torch, Torch-NPU, Transformers, vLLM, MindIE, a new
CANN release, or custom OPP merely to make a candidate load. If the current loader does
not recognize an architecture, the candidate remains `blocked`; no CPU or cloud
fallback is permitted.

Qwen2 Math and Qwen2.5 Math are task-specialized checkpoints. A successful arithmetic
sample would not establish general conversational quality, so any future test must
report math and general-chat probes separately. Llama 3.2 additionally requires
acceptance of Meta's community license and gated access before artifacts may be
retrieved.

## 🧪 Future preflight checklist

1. Confirm the canonical board endpoint is `192.168.1.90` and identify `Ascend310B4`.
2. Freeze Python, MindSpore, MindNLP and CANN versions without changing the environment.
3. Resolve a concrete revision and license file; reject mutable or inaccessible files.
4. Download only an explicit allowlist with size and SHA-256 verification.
5. Run tokenizer/model import and a single NPU token before starting any HTTP service.
6. Continue through the strict gates in [the 8T plan](40-8t-mindspore-llm-strict-test-plan.md).

Until all six steps are approved, the six registry rows are informational only. Their
presence does not increase the count of models that can currently be used by Case9.

## 🔗 Sources

[qwen2-05]: https://huggingface.co/Qwen/Qwen2-0.5B-Instruct
[qwen2-15]: https://huggingface.co/Qwen/Qwen2-1.5B-Instruct
[qwen2-math]: https://huggingface.co/Qwen/Qwen2-Math-1.5B-Instruct
[qwen25-coder]: https://huggingface.co/Qwen/Qwen2.5-Coder-0.5B-Instruct
[qwen25-math]: https://huggingface.co/Qwen/Qwen2.5-Math-1.5B-Instruct
[llama32]: https://huggingface.co/meta-llama/Llama-3.2-1B-Instruct
