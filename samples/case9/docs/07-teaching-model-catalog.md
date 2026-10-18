# Teaching Model Catalog

## Why Technical Running and Quality Are Separate

This project is a test tutorial. A model that loads, executes on the Ascend
device, and answers through the API remains useful for teaching even when the
answer is slow, English-only, truncated, or factually weak. The catalog
therefore keeps two independent conclusions:

- `technical_status`: whether the tested runtime path can be reproduced;
- `quality_status`: what the fixed Chinese/English probes actually produced.

`quality_status=failed` does not make a technically tested profile disappear.
It tells the student what to investigate next: tokenizer files, chat template,
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
| `qwen2-0.5b-instruct-mindspore` | 20T API, 64-token, stability and performance pass | Failed | `U+FFFD` in Chinese arithmetic probe points to tokenizer/decode behavior, not NPU absence |
| `qwen2-1.5b-instruct-mindspore` | 20T machine gates observed pass | Failed | `U+FFFD` in 4/5 Chinese probes; still useful for reproducing a text-integrity failure |
| `qwen2.5-0.5b-mindspore` | 20T corrected-timeout machine gates pass | Pending | The earlier context-only/30-second timeout report is a debugging lesson, not the final technical result |
| `qwen2.5-1.5b-mindspore` | 20T low-occupancy path observed degraded | Failed | Low-occupancy API path ran; a separate high-occupancy run produced `Alloc failed` |
| `tinyllama-1.1b-mindspore` | Short smoke on both tiers; long output degraded | Failed | English-oriented model; long output showed `U+FFFD`/TBE failure, so the short cap is intentional |
| `deepseek-r1-qwen-1.5b-mindspore` | Both tiers have NPU/API evidence; slow | Failed | `<think>` prefix and factual errors are model-output behavior; latency is a separate performance result |
| `qwen3-0.6b-mindspore` | Blocked before loading | Not run | MindNLP 0.4.1 has no loader; no claim is made about the hardware itself |
| `minicpm3-4b-mindspore` | Not run | Not run | Dedicated loader, artifact and memory gates remain future work |

The table is intentionally not a leaderboard. 8T and 20T results are separate
evidence sets, and a quality label does not replace the raw response files.

## Starting a Teaching Profile

The worker remains single-model and single-request. This avoids loading several
large checkpoints into the same 310B device. The page selector shows the
catalog, but selecting a different option displays the exact restart command
instead of silently changing a live NPU process.

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

After the worker is ready, start the gateway and UI as usual. The UI sends the
selected profile ID as a consistency check. A profile different from the
active worker returns `409 model_switch_required`, with the restart command in
the message. This makes a classroom mistake visible instead of sending a
request to the wrong model.

## Interpreting a Failed Quality Probe

Use the following order when a selectable model produces a poor answer:

1. Save the complete JSON/SSE response and the exact profile revision.
2. Check `U+FFFD`, token count, `finish_reason`, and whether the output was a
   model stop or a context/max-token cut-off.
3. Compare tokenizer and chat-template files with the profile revision.
4. Check `npu-smi`, worker health, and the provider placement fields. A quality
   failure must not be relabeled as an NPU failure without a device/runtime
   error.
5. Record the result under the profile's `quality_detail`; keep the technical
   status unchanged unless the runtime itself fails.

For TinyLlama, Chinese quality is expected to be weak because its training
target is primarily English. For DeepSeek-R1 Distill, a visible thinking prefix
can consume a short token budget before the final answer. For Qwen2/Qwen2.5,
the observed replacement characters are a text-integrity issue worth debugging
even though the NPU/API gates passed.

## What Is Not Selectable Yet

Qwen3 is visible but not selectable because the installed MindNLP 0.4.1 did not
provide its loader. MiniCPM3 is visible but not selectable because its dedicated
loader, 4B artifact and memory gate were not run. These are `blocked` or
`not-run` prerequisites, not statements that Ascend 310B can never run those
models. A future experiment may move an entry to `short-smoke` or
`observed-pass` after collecting the same environment, artifact, NPU, API and
performance evidence.
