# Teaching Model Catalog

## Why Technical Running and Quality Are Separate

This project is a test tutorial. A model that loads, executes on the Ascend
device, and answers through the API remains useful for teaching even when the
answer is slow, English-only, truncated, or factually weak. The catalog
therefore keeps two independent conclusions:

- `technical_status`: whether the tested runtime path can be reproduced;
- `text_integrity_status`: whether the decoded UTF-8 text was complete and free
  of replacement characters under the recorded generation budget;
- `quality_status`: an independent human semantic-quality conclusion from
  complete responses with recorded prompts, token budgets and model revision.

A text-integrity issue and a max-token cut-off do not make a technically tested
profile disappear, and neither is by itself a Chinese semantic-quality result.
They tell the student what to investigate next: tokenizer files, chat template,
decode behavior, model training language, context budget, or output limits.

## Current Teaching Entries

The browser reads `configs/tutorial-model-catalog.json`. Entries marked
`selectable=true` have an observed technical path and can be started with the
tutorial registry. Entries marked `blocked` or `not-run` stay visible so the
student can distinguish an untested prerequisite from a measured failure.

| Profile | Technical result | Quality result | Why the result matters |
| --- | --- | --- | --- |
| `qwen1.5-0.5b-chat` | 20T observed pass; 8T degraded evidence | Pending | Default baseline and the reference for the service protocol |
| `qwen1.5-1.8b-mindspore` | 20T machine gates observed pass | Pending | Larger Qwen1.5 comparison; 8T must be measured independently |
| `qwen2-0.5b-instruct-mindspore` | 20T API, 64-token, stability and performance pass | Pending | Historical `U+FFFD` is a tokenizer/decode observation, not an NPU or semantic-quality verdict |
| `qwen2-1.5b-instruct-mindspore` | 20T machine gates observed pass; corrected ChatML template reaches 64/128/192 token length gates | Pending | A missing tokenizer template once caused Qwen1.5-style role markers; the profile template fixes that path, while 256 tokens still fail closed |
| `qwen2.5-0.5b-mindspore` | 20T corrected-timeout machine gates pass | Pending | The earlier context-only/30-second timeout report is a debugging lesson, not the final technical result |
| `qwen2.5-1.5b-mindspore` | 20T low-occupancy path observed degraded | Pending | Low-occupancy API path ran; a separate high-occupancy run produced `Alloc failed` |
| `tinyllama-1.1b-mindspore` | Short smoke on both tiers; long output degraded | Pending | English-oriented model; long output showed a text-integrity/TBE boundary that needs a separate quality run |
| `deepseek-r1-qwen-1.5b-mindspore` | Both tiers have NPU/API evidence; slow | Pending | `<think>` can consume a short budget before the final answer; it needs a completed-response review |
| `qwen3-0.6b-mindspore` | Blocked before loading | Not run | MindNLP 0.4.1 has no loader; no claim is made about the hardware itself |
| `minicpm3-4b-mindspore` | Not run | Not run | Dedicated loader, artifact and memory gates remain future work |

The table is intentionally not a leaderboard. 8T and 20T results are separate
evidence sets, and a quality label does not replace the raw response files.

## Starting a Teaching Profile

The worker remains single-model and single-request. This avoids loading several
large checkpoints into the same 310B device. The page selector can switch a
model, but it does so as a controlled restart: it validates the selected
catalog/registry/cache entry, stops the reported worker PID, launches the fixed
profile through the CANN-aware launcher, waits for `/health`, and clears browser
sessions. It rolls back to the prior profile if the new worker fails to start.
The selector never receives an arbitrary model path, shell command, or Python
expression.

```bash
export ASCEND_EDGE_CHAT_PROFILE=qwen2-1.5b-instruct-mindspore
ascend-edge-chat worker \
  --profile "$ASCEND_EDGE_CHAT_PROFILE" \
  --registry configs/tutorial-models.json
```

Before starting, set `ASCEND_EDGE_CHAT_DATA_DIR` to the directory containing
the selected profile's verified local cache. The launcher checks `config.json`,
the tokenizer, and at least one weight/checkpoint file. It refuses a missing or
partial cache and never falls back to downloading an unpinned model during
startup.

After the worker is ready, start the gateway and UI as usual. On the teaching
page, select an available profile and wait for the status to return to “就绪”
before sending a prompt. The selector clears the current session because a
conversation built with one chat template must not be carried into another
model. If the model cache is incomplete or the worker fails to become healthy,
the UI shows the failure and restores the prior worker when possible.
The selector is disabled during generation; it becomes available after the
terminal SSE event and a short server-side cleanup window.

## Interpreting a Failed Quality Probe

Use the following order when a selectable model produces a poor answer:

1. Save the complete JSON/SSE response and the exact profile revision.
2. Record the requested `max_tokens`, actual completion-token count,
   `finish_reason`, and whether the output was a model stop or a
   context/max-token cut-off. A `length` finish is a length result, not a
   semantic failure.
3. Compare tokenizer and chat-template files with the profile revision.
4. Check `npu-smi`, worker health, and the provider placement fields. A quality
   failure must not be relabeled as an NPU failure without a device/runtime
   error.
5. Record the result under the profile's `quality_detail`; keep the technical
   status unchanged unless the runtime itself fails.

### Human Chinese-Quality Protocol

Run each profile with the same 10 Chinese prompts, a fresh conversation, greedy
decoding and `max_tokens=256` (or the profile's documented ceiling). Preserve
the raw JSON/SSE response, profile revision, prompt-token count, completion-token
count and `finish_reason`. A response ending with `length` is marked
**incomplete** and is not scored as a failed answer. First record UTF-8/text
integrity separately. Then have at least two reviewers score relevance,
factuality, fluency and completion on a 0/1/2 rubric; retain the per-reviewer
scores and disagreements. Only this review may change `quality_status` to
`passed` or `failed`.

For TinyLlama, Chinese quality remains a hypothesis because its training target
is primarily English. For DeepSeek-R1 Distill, a visible thinking prefix can
consume a short budget before the final answer. For Qwen2/Qwen2.5, replacement
characters remain a text-integrity issue worth debugging even though the NPU/API
gates passed.

## What Is Not Selectable Yet

Qwen3 is visible but not selectable because the installed MindNLP 0.4.1 did not
provide its loader. MiniCPM3 is visible but not selectable because its dedicated
loader, 4B artifact and memory gate were not run. These are `blocked` or
`not-run` prerequisites, not statements that Ascend 310B can never run those
models. A future experiment may move an entry to `short-smoke` or
`observed-pass` after collecting the same environment, artifact, NPU, API and
performance evidence.
