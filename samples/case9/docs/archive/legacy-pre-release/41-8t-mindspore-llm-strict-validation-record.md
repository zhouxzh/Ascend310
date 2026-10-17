# 8T MindSpore LLM strict validation record

_Case9 evidence ledger for the canonical `192.168.1.90` endpoint; updated 2026-09-06. The resumed run records bounded board observations and keeps the strict campaign fail-closed where the device is unstable._

---

## 📍 Board identity and address history

| Board key | Current endpoint | Historical aliases | Hardware |
| --- | --- | --- | --- |
| `board8t` | `192.168.1.90` | `192.168.11.14`, `192.168.8.178` | Ascend310B4 / 8T |

The three addresses identify the same physical board. Reports collected under an alias
retain that address in their raw provenance; they are not rewritten and do not count as
a new performance run. The current endpoint is the same physical board; an IP change does
not create a new performance batch.

## 🧪 Evidence inherited from earlier batches

The registry and previous records contain the following **historical** observations.
They remain useful as context but do not silently complete a missing strict gate.

| Profile | Historical state relevant to 8T | Current strict interpretation |
| --- | --- | --- |
| `qwen1.5-0.5b-mindspore` | A prior machine batch produced output; identity/placement evidence was incomplete | `blocked`; rerun missing gates on `.90` only after G0 |
| `tinyllama-1.1b-mindspore` | Long-output and Chinese-quality failures were recorded | `blocked`; English evidence cannot promote Chinese chat |
| `deepseek-r1-qwen-1.5b-mindspore` | A historical 8T gap batch passed machine gates in shared base | `experimental_dirty_base` in registry; human approval and fresh G0 remain separate |
| `qwen2.5-0.5b-mindspore` | Context-only diagnostic generated a short response; strict placement failed | `blocked`; no API or performance claim |
| `qwen2.5-1.5b-mindspore` | Correct-CANN diagnostic hit a runtime allocation failure near board memory limit | `blocked`; do not retry heavy load until a clean device baseline |
| `qwen3-0.6b-mindspore` / `qwen3-1.7b-mindspore` | Current MindNLP 0.4.1 loader probe found no Qwen3 loader | `blocked`/`not-run`; no package upgrade permitted |
| `minicpm3-4b-mindspore` | Official example targets a 20T-class board | `not-run` on 8T; no 4B download |

Detailed historical reports remain linked from [the candidate inventory](31-mindspore-llm-candidate-inventory.md),
[the prior validation ledger](33-mindspore-llm-candidate-validation-record.md), and the
numbered Qwen2.5 records. Their report paths and hashes are unchanged.

## 📋 Strict gate status for the canonical endpoint

| Gate | `.90` status in this record | Required next evidence |
| --- | --- | --- |
| G0 environment | `blocked` | Identity/runtime observed, but strict profile gate and version-probe warnings remain |
| G1 artifacts | `artifact_verified` for four listed profiles | Per-profile lock file, Content-Length and SHA-256; no execution implied |
| G2 NPU load | `diagnostic-only` / strict `not-run` | One bounded Qwen1.5 token; strict placement evidence is still missing |
| G3 API | `not-run` | JSON/SSE/error contract on candidate port only |
| G4 long output | `not-run` | 8/16/32/64-token UTF-8 responses |
| G5 stability | `not-run` | Ten requests plus RSS/FD/NPU before/after snapshots |
| G6 quality | `not-run` | Ten Chinese prompts and separate English labels |
| G7 performance | `not-run` | Two warmups, 30 samples, p50/p95 and token/s |
| G8 candidate chain | `not-run` | `7868 -> 7867 -> 8090`, switch/rollback and formal-port check |

`not-run` here means that the corresponding strict gate has no complete `.90` result. The
artifact rows are the explicit read-only exception documented below; they do not imply
model execution or admission. Historical `.11.14`/`.178` reports are not silently promoted.
If a concrete device fault reappears, mark the affected profile `blocked`, preserve the
diagnostics, and stop the batch.

## 🔐 Environment and no-change boundary

The strict batch must run in the existing board `base` environment and record its dirty
state. The command prefix is:

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
export PYTHONNOUSERSITE=1
```

No package installation, CANN/driver/OPP replacement, model download, worker start,
formal port change or audio/XiaoZhi action is part of this record. Conditional profiles
listed in the registry are metadata-only and must be rejected by the downloader and
model switcher.

## 🗂️ Evidence layout and reproducibility

Use one UTC run directory per profile and gate:

```text
~/case9-mindspore-chat/
  environment/Ascend310B4/<run-id>/
  reports/Ascend310B4/<profile>/<run-id>/
  logs/<profile>/<run-id>/
```

Store the exact command, PID, model/tokenizer revisions, file sizes and hashes, raw API
responses, and `npu-smi` before/during/after snapshots. Keep all model and run artifacts
outside Git. The formal Qwen2.5 ACL chain (`8080 -> 7861 -> 7865`) remains unchanged
until a separate approval reviews a complete candidate report.

## 📏 2026-09-05 resumed board evidence

The board was reached at `192.168.1.90` after the address-only change. The read-only
identity snapshot reported hostname `orangepiaipro`, Linux `5.10.0+`, `aarch64`,
`Ascend310B4`, CANN `8.0.0`, and `npu-smi 25.2.0` with `Health: Alarm`. The active conda
environment used Python `3.9.2`, MindSpore `2.4.10`, MindNLP `0.4.1`, and an importable
`acl`; the system Python was `3.10.12`. The existing shared `base` is dirty: `torch`,
`torch_npu`, `torchaudio`, and checker-visible `mindie`, `mindtorch`, `onnxruntime`,
`transformers`, and `vllm` were already present. No package was installed, removed, or
upgraded, and the Case9 adapter did not use those packages.

The environment checker wrote
`/home/HwHiAiUser/case9-mindspore-chat/reports/mindspore-chat/strict-g0/environment-20260905/qwen1.5-environment.json`.
It observed the expected SoC/CANN/runtime imports, but returned `ok=false` because the
selected registry row is intentionally `blocked`; its stderr also reported that `te` and
`hccl` were not discoverable through the version probe. Therefore G0 is **blocked**, not
passed.

The read-only artifact verifier completed these files on the same board (the reports are
kept on the board and were not committed to Git):

| Profile | Verified files | Board report |
| --- | ---: | --- |
| `qwen1.5-0.5b-mindspore` | 7/7 | `/home/HwHiAiUser/case9-mindspore-chat/reports/mindspore-chat/strict-g0/artifacts-20260905/qwen1.5-0.5b-mindspore.json` |
| `qwen2.5-0.5b-mindspore` | 6/6 | `/home/HwHiAiUser/case9-mindspore-chat/reports/mindspore-chat/strict-g0/artifacts-20260905/qwen2.5-0.5b-mindspore.json` |
| `tinyllama-1.1b-mindspore` | 7/7 | `/home/HwHiAiUser/case9-mindspore-chat/reports/mindspore-chat/strict-g0/artifacts-20260905/tinyllama-1.1b-mindspore.json` |
| `deepseek-r1-qwen-1.5b-mindspore` | 5/5 | `/home/HwHiAiUser/case9-mindspore-chat/reports/mindspore-chat/strict-g0/artifacts-20260905/deepseek-r1-qwen-1.5b-mindspore.json` |

These are G1 artifact observations only. The verifier exits nonzero when the profile is
blocked, even when every declared file hash matches; this is expected fail-closed
behavior and is not an inference test.

A bounded Qwen1.5 diagnostic was run before the device became unstable. In
`/home/HwHiAiUser/case9-mindspore-chat/reports/mindspore-chat/strict-g0/qwen1.5-smoke-20260905T124123Z/`,
the tokenizer took `0.595 s`, model load `30.546 s`, and one-token generation `11.894 s`;
the observed text ended with `我是`. `npu-smi` memory changed from `2376/15610 MB` to
`5110/15610 MB`. This is context/diagnostic evidence only: it has no valid during snapshot,
does not satisfy strict placement, and did not start an HTTP worker.

The subsequent Qwen2.5 context-only attempt lost the SSH connection during model load
(`Timeout, server 192.168.1.90 not responding`). No completion, API, or service result is
claimed. Kernel logs also repeated `DRV_LPM_FAULT 0x80E3A203`/core-reset messages. This is
a concrete device fault, not merely the `Health: Alarm` label; retain the diagnosis and
pause heavy loads. See the [sample issue](https://gitee.com/ascend/samples/issues/IBVPYX)
and [Ascend forum report](https://www.hiascend.com/dev/forum/thread-02190216356225473002-1-1.html)
for the same LPM error family.

| Gate | Resumed `.90` state | Evidence boundary |
| --- | --- | --- |
| G0 environment | `blocked` | Identity/runtime observed, but strict profile gate and version-probe warnings remain |
| G1 artifacts | `artifact_verified` per listed profile | Hash/size checks only; admission remains blocked |
| G2 NPU load | `diagnostic-only` / strict `not-run` | One bounded Qwen1.5 token; no strict placement or during sample |
| G3 API | `not-run` | No candidate worker started |
| G4 long output | `not-run` | Heavy generation paused |
| G5 stability | `not-run` | No 10-round run under this resumed batch |
| G6 quality | `not-run` | No new human or machine quality batch |
| G7 performance | `not-run` | No valid 2+30 timing batch |
| G8 candidate chain | `not-run` | Ports `8084`, `7867`, `7868`, and formal `8080`, `7861`, `7865` were untouched |

## 📷 2026-09-05 15:14 UTC read-only recheck

After the address-only restart, a second read-only check reached the same board at
`192.168.1.90`. The board reported hostname `orangepiaipro`, uptime `3:02`, load averages
`17.00/17.05/17.08`, and no Case9, MindSpore, Qwen, candidate, or formal service process.
No listener was found on the Case9 candidate or formal ports. `npu-smi` still reported
`Ascend310B4`, `Health: Alarm`, temperature `79 C`, AICore `0%`, device memory
`11736/15610 MB`, and hugepages `15/15`. Kernel logs continued to report
`DRV_LPM_FAULT 0x80E3A203` followed by `reset_core_mk`.

The raw snapshot is retained on the board at
`/home/HwHiAiUser/case9-mindspore-chat/reports/mindspore-chat/strict-g0/resume-readonly-20260905T151439Z/`.
Its `SHA256SUMS.txt` has SHA-256
`44e5dd196a3460047d6fd748a4665f3938270618cfefc2cb8481391ffd9f87fa`. Because the
device fault and high allocation remain present, no model worker was started and no
G2-G8 gate was attempted in this recheck.

## 📷 2026-09-06 01:15 UTC reboot recheck

After the user rebooted the same physical 8T board, a short read-only SSH probe reached
`192.168.1.90` and returned at `2026-09-06 01:15:14 UTC`. It reported hostname
`orangepiaipro`, Linux `aarch64`, uptime `1:18`, and load averages
`17.02/17.05/17.01`. No Case9, MindSpore, Qwen, candidate, or formal service was
running; the only `pgrep` lines were the probe shell itself, and no listener appeared
on `8080`, `8082`, `8083`, `8084`, `7861`, `7865`, `7867`, `7868`, or `8090`.

`npu-smi 25.2.0` again identified `Ascend310B4` with `Health: Alarm`, temperature
`80 C`, AICore `0%`, device memory `2348/15610 MB`, and hugepages `15/15`. The tail
of the kernel log contained a `reset_core_mk` at `01:09:07 UTC`, followed by
`DRV_LPM_FAULT 0x80E3A203` and another `reset_core_mk` at `01:14:08 UTC`. This is
concrete device-fault evidence and is independent of the informational `Health: Alarm`
label.

An attempt to persist a new multi-file report directory over the same SSH session
returned exit code `1` without a remote path, so its creation is **not** claimed. A
subsequent one-command hostname check succeeded once, then three bounded probes timed
out while the board became unreachable again. The observed values above are therefore
recorded as controller-side command output, not as a hash-verified report bundle. No
model was loaded, no package or startup file was changed, and no candidate or formal
port was touched.

### Persisted G0 environment check

During a later short connectivity window, the required command prefix was executed
again with `PYTHONNOUSERSITE=1` and its output was persisted on the board at
`/home/HwHiAiUser/case9-mindspore-chat/reports/mindspore-chat/strict-g0/resume-g0-20260906T012557Z/`.
The checker exited with code `2` (fail-closed). The SHA-256 values recorded in the
remote `SHA256SUMS.txt` are:

| File | SHA-256 | Observation |
| --- | --- | --- |
| `environment.json` | `05448e0f2fb89a4a7e25ce74dd1a365056795570dc68b6d62a2b3f5fd2093013` | `Ascend310B4` visible; CANN root reports `8.0.0`; strict profile is blocked; MindNLP import failed |
| `exit-code.txt` | `53c234e5e8472b6ac51c1ae1cab3fe06fad053beb8ebfd8977b010655bfdd3c3` | `2` |

The report identifies the active interpreter as `/usr/local/miniconda3/bin/python`
(`Python 3.9.2`) and MindSpore as `2.4.0`; `mindnlp` is absent when the user site is
disabled. MindSpore also warns that its `2.4.0` build does not match the detected
Ascend software package `7.6` (the toolkit root separately reports `8.0.0`). The base
environment remains dirty with pre-existing Torch, Torch-NPU, Torchaudio, Transformers,
ONNX Runtime, vLLM, MindIE and MindTorch packages. No package was changed. This fresh
G0 result blocks all MindSpore model loading on the current board until an approved,
matching environment is made available; it does not invalidate the historical reports
that used the earlier user-site environment.

The strict state remains unchanged: G0 is `blocked` by the device/runtime condition,
G1 remains artifact-only, and G2-G8 are `not-run` for this reboot check. Do not infer a
fresh performance or availability result from the brief SSH window; wait for a stable
board and collect a hash-verified report before retrying a model.

## ✅ Completion rule

This record becomes a measured validation result only when a board run fills G0-G8 with
raw evidence and the registry row is updated for `Ascend310B4`. A profile with technical
passes in the shared `base` environment remains `experimental_dirty_base`; `admitted`
requires explicit human quality approval. The IP-only transition to `.90` is complete as
an address bookkeeping change, but the strict test campaign itself is still pending.
