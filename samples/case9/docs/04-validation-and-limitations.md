# Validation and Limitations

## Observed Release Baseline

The following measurements were recorded on `2026-10-16` for the 20T reference board before the release restructuring. The revalidation procedure in [20T Deployment](01-20t-deployment.md) must be run again against the packaged release before a tag or public release is created.

| Evidence item | Observed value | Evidence type |
| --- | ---: | --- |
| SoC | Ascend310B1 / 20T | Observed-pass environment identity |
| CANN | 8.0.0 | Observed-pass environment identity |
| Python | 3.9.25 | Observed-pass environment identity |
| MindSpore / MindNLP | 2.4.10 / 0.4.1 | Observed-pass import and worker load |
| Model revision | `4d14e384a4b037942bb3f3016665157c8bcb70ea` | Locked artifact identity |
| JSON and SSE | 11/11 machine checks | Observed-pass protocol checks |
| Short stability loop | 10/10 requests | Observed-pass machine check |
| First-token p50 / p95 | 671.868 ms / 723.280 ms | Historical performance measurement |
| Completion p50 / p95 | 1310.003 ms / 1401.746 ms | Historical performance measurement |
| Throughput p50 / p95 | 1.531 / 1.642 token/s | Historical performance measurement |

The complete historical evidence remains in `docs/archive/legacy-pre-release/`. The table is a boundary statement, not a benchmark promise for another board, CANN release, ambient load, prompt, or model revision.

## 2026-10-18 20T Acceptance

The active board service was validated after the code changes, with the
`qwen2-0.5b-instruct-mindspore` teaching profile. The browser path was
`:7865 -> :7861 -> :8090`; the board was `Ascend310B1 / 20T`, CANN `8.0.0`,
driver `25.2.0`, and Python `3.9.2`. The dated raw board report remains under
the deployment directory as ignored evidence and is not committed to Git.

| Evidence item | 2026-10-18 result |
| --- | --- |
| Gateway contract | `/v1/models`, invalid bearer rejection, and unknown-model rejection passed |
| UI contract | `/api/config` and a real `/api/chat` response passed |
| JSON completion | HTTP 200, non-empty response, `finish_reason=stop` in the direct check |
| SSE completion | HTTP 200, terminal `[DONE]`-equivalent completion, `finish_reason=stop` |
| Output-budget checks | 8/16/32 tokens returned `length`; 64-token request returned `stop` after 46 completion tokens |
| Stability | 10/10 short requests returned “测试通过” |
| Chinese probes | 10/10 requests completed the machine path; semantic quality is pending |
| One performance sample | First token 723.051 ms; completion 1370.604 ms; 1.459 token/s |
| Test-time worker state | Worker remained healthy after the run |
| Test-time NPU snapshot | 19,540 / 23,673 MB used; `npu-smi` still reported the 310B1 |
| UI screenshot | 1440×900 current 20T UI evidence image |

The first complete harness run had no inter-request delay and failed in the
stability loop with:

```text
HTTP 429: Too many request bodies are being read
```

That is a request-admission result, not an NPU or model failure. The subsequent
run used one-second spacing for JSON requests and two-second spacing for
streaming requests, and passed. The service is intentionally serial and bounded;
the test harness must not burst submissions.

This run does not establish p50/p95 performance because only one performance
sample was taken. It also does not establish Chinese semantic quality. A direct
Chinese prompt received an English apology; that is a quality observation for
that answer, while the machine path remained `observed-pass`. Use the
ten-prompt, fresh-session, human-review protocol below before changing
`quality_status`.

## 2026-10-18 Output-Budget Recheck

The earlier 64-token UI/worker ceiling was a service policy, not an observed
memory limit. It made a normal long answer appear to fail when its decoder did
not emit EOS before the limit. The runtime now defaults to 128 output tokens
and has a configured 256-token ceiling, while retaining the 1024-token context
contract and each profile's separate maximum.

On the 20T board, `qwen2-1.5b-instruct-mindspore` was retested after the
change. A two-paragraph Chinese prompt with `max_tokens=128` used 44 prompt
tokens and 69 completion tokens, returned `finish_reason=stop`, and completed
in 108,956 ms. A separate sequence-generation request with `max_tokens=96`
returned `finish_reason=length` after reaching the requested output budget. In
both runs the worker remained healthy; neither run is memory-exhaustion or
runtime-crash evidence. The raw board artifacts are retained under the
deployment's ignored `run/quality-recheck-20261018T122702Z/` and
`run/token-budget-96-20261018T123030Z/` directories.

The first response declined to describe Ascend 310B, so it is a factuality
observation for one completed answer. It is not a complete Chinese-quality
assessment. A semantic-quality result must use the protocol below.

### Qwen2 1.5B Output-Budget Boundary On 20T

On `2026-10-18`, `qwen2-1.5b-instruct-mindspore` was measured through the
same browser SSE route used by students: `:7865 -> :7861 -> :8090`. The board
was `Ascend310B1 / 20T`, CANN `8.0.0`, MindSpore `2.4.10`, MindNLP `0.4.1`,
and the profile revision was `b139ca2b8d7ba579ed727ffcd7060075666a808b`.
The original run used a Qwen2 tokenizer directory containing only
`config.json`, `model.safetensors`, and `tokenizer.json`: it has no
`tokenizer_config.json` or `generation_config.json`. MindNLP 0.4.1 therefore
had no tokenizer template. The generic provider fell back to a Qwen1.5-shaped
prompt, which explained the visible `<|user|>` / `<|assistant|>` continuation.
This is a prompt-contract error, not an NPU-memory conclusion.

The profile now carries the reviewed Qwen2 ChatML text template. The board-side
contract checker rendered `<|im_start|>user ... <|im_end|>` followed by
`<|im_start|>assistant`, and the adapter rejects a Qwen2/Qwen2.5 profile that
lacks that template instead of falling back. Forced long-output prompts were
then issued through the same browser SSE route used by students. Every
successful row reached the requested budget with `finish_reason=length`; the
worker returned to `healthy=true`, `busy=false`, and `cache_cleanup=idle`.

| Requested output budget | Observed completion tokens | Terminal state | Wall time | Text integrity | Interpretation |
| ---: | ---: | --- | ---: | --- | --- |
| 64 | 64 | `length`, worker healthy | 80.029 s | no `U+FFFD` or role marker | Observed-pass after template correction |
| 128 | 128 | `length`, worker healthy | 158.946 s | no `U+FFFD` or role marker | Observed-pass after template correction |
| 192 | 192 | `length`, worker healthy | 238.133 s | no `U+FFFD` or role marker | Observed-pass after template correction |
| 256 | partial SSE, worker exited before terminal event | a long partial response was streamed | not a valid latency metric | no terminal response retained | Observed-fail after template correction |

The corrected 256-token request again did **not** show an out-of-memory
condition: NPU memory fell to about `10,269/23,673 MB` after the worker
exited. The worker log instead records
`TBE Subprocess[task_distribute] ... main process disappeared` and semaphore
cleanup warnings. This is a fail-closed runtime result for the tested
combination, not evidence that all 256-token requests or all 310B devices are
unsupported. It is, however, enough evidence to retain the profile's
conservative 192-token UI ceiling.

The browser catalog and worker profile now cap this specific profile at `192`
tokens. The service-wide maximum remains 256 for profiles that have their own
successful evidence. Raw ignored board evidence is retained at
`artifacts/template-contract-20261018/` below the board data directory. The
directory contains the rendered prompt contract, raw 64/128/192 SSE streams,
the 256-token partial stream, NPU snapshots, and worker logs. They remain
ignored board evidence rather than source-controlled artifacts.

To reproduce this gate on a board, start with the active profile's small
budget, increase one option at a time, and retain the generated ignored JSON:

```bash
python scripts/verify/output_budget_probe.py \
  --profile qwen2-1.5b-instruct-mindspore \
  --budgets 128,192 \
  --timeout 330 \
  --output run/output-budget-$(date -u +%Y%m%dT%H%M%SZ).json
```

The client uses the browser's SSE route, records an unavailable worker as an
observation rather than deleting the run, and must not be used to claim a
higher budget before the worker is healthy again.

The tested MindSpore loader did not expose parameter-level device placement. Its NPU evidence is therefore `context_verified`: `mindspore.set_context(device_target="Ascend")` succeeded and `npu-smi` reported the expected 310B1 device. This is stronger than an import check, but it is not equivalent to a parameter-placement trace; release records must preserve that distinction.

## Quality State

The default Qwen1.5 profile passed automated completion-integrity checks for a
ten-prompt Chinese probe. Human Chinese-quality review is **pending**. This
limits the release claim, but it does not make the model unavailable to a
student testing the NPU path.

The teaching catalog intentionally includes the following technical/quality
separations:

| Profile | Technical evidence | Quality observation | Interpretation |
| --- | --- | --- | --- |
| Qwen1.5 0.5B | 20T machine gates passed | Pending | Baseline is runnable; quality still needs human labels |
| Qwen2 0.5B / 1.5B | 20T API, long-output and stability evidence | Historical probes included `U+FFFD`; semantic review pending | Runtime works; tokenizer/decode output needs investigation before a quality conclusion |
| Qwen2.5 0.5B | Corrected-timeout 20T gates passed | Pending | Earlier 30-second client timeout was a test-harness error, not a model verdict |
| Qwen2.5 1.5B | Low-occupancy API path passed; separate high-occupancy run had `Alloc failed` | Historical probes included `U+FFFD`; semantic review pending | Selectable as a degraded experiment with a memory warning |
| TinyLlama 1.1B | Short smoke passed on both tiers | English-oriented; long output had `U+FFFD`/TBE failure | Short test remains useful; long-output limitation is documented |
| DeepSeek-R1 Distill 1.5B | NPU/API gates recorded on both tiers | `<think>` prefix and factual errors | Slow or poor answers are model behavior, not proof of no NPU execution |

The browser selector follows the technical column. A failed or pending quality
entry remains selectable; a missing loader or a model that was never run is
shown as `blocked`/`not-run` and cannot be started until its prerequisite test
is completed. This is a teaching boundary, not a quality endorsement.

`U+FFFD`, malformed text, a `length` terminal reason, and factual weakness are
different observations. The first two are text-integrity evidence; `length`
means the answer is incomplete under that particular output budget; only a
completed response can enter semantic scoring. For Chinese quality, run the
same ten prompts in fresh sessions at `max_tokens=256` or the profile's lower
ceiling, retain raw JSON/SSE plus token counts and terminal reason, then have
two human reviewers independently score relevance, factuality, fluency and
completion on a 0/1/2 scale. Only this review may set a profile's
`quality_status` to `passed` or `failed`.

## Revalidation Gates

Before publishing a release tag, verify the package on the target board with the locked environment and artifact set:

1. environment fingerprint, CANN setup, NPU visibility, and model hashes;
2. worker NPU initialization and one-token generation;
3. gateway authentication, `/v1/models`, JSON completion, and SSE completion;
4. `8`, `16`, `32`, `64`, `128`, and `256` token outputs, including UTF-8, terminal reason and truncation checks;
5. ten serial requests with worker PID, RSS, file descriptors, and NPU snapshots;
6. browser UI chat through the gateway;
7. recorded latency and token/s parameters, including warmup and percentile method;
8. explicit human review of the Chinese probe set.

## 2026-10-23 Unified Environment Acceptance

The formal release acceptance was repeated on the same 20T board from the
unified `ascend` environment: Python `3.9.25`, MindSpore `2.4.10`, MindNLP
`0.4.1`, CANN `8.0.0`, and driver `25.2.0`.

| Gate | Result |
| --- | --- |
| Gateway and UI contracts | passed |
| JSON output budgets | 4/4 completed |
| SSE | completed with `finish_reason=stop` |
| Stability | 10/10 |
| Chinese probes | 10/10 machine-complete |
| Performance samples | 30/30 |
| Invalid UTF-8 / `U+FFFD` | not detected |
| First-token p50 / p95 | 705.746 / 747.193 ms |
| Completion p50 / p95 | 1336.014 / 1412.012 ms |
| Throughput p50 / p95 | 1.497 / 1.569 token/s |

The paced client used one-second JSON and two-second SSE intervals. A first
attempt without pacing reproduced the documented `HTTP 429: Too many request
bodies are being read`; the paced run then passed. These are now explicit
`release_acceptance.py` controls: `--json-interval` and `--sse-interval` accept
`0..10` seconds and are recorded in the report.

The MindSpore backend also accepts optional
`ASCEND_EDGE_CHAT_MAX_DEVICE_MEMORY`; when set, it is forwarded to
`mindspore.set_context(max_device_memory=...)`. This only changes the context
memory cap. It does not prove parameter placement, model fit, or generation
success, so the selected value and any allocation failure must be recorded
separately. The raw report is retained
on the board at
`run/reports/release-20261023T013704Z/acceptance.json`, 12,266 bytes, SHA-256
`4a0c19f11af4b39f84a030a0a669c753753fa72f6c7a5ae0d4d68bd244c068e0`.
Human Chinese semantic review remains pending.

The deterministic board voice path also completed under the unified
environment: bundled Chinese WAV → sherpa-onnx ASR → gateway/worker LLM →
eSpeak NG 22,050 Hz mono WAV → ALSA playback. It reported
`PIPELINE_PLAYBACK_COMPLETE`. The Jieli USB speaker was initially absent from
the USB/ALSA inventory, so a temporary board `hw:2,0` run completed without a
confirmed audible source. After reconnecting Jieli, the same pipeline selected
`hw:1,0` and the operator confirmed hearing the Chinese reply. This is
`observed-pass` for the current 20T deterministic board voice chain; speech
quality and ten-turn timing remain separate gates. The UGREEN USB microphone
remained visible as capture `hw:0,0`.

## Planned Voice Mode Evidence

Two voice modes are implemented separately from this text release. Board mode
uses board-local USB/3.5mm devices, automatic VAD silence segmentation, ASR, the
existing loopback LLM path, TTS, and board-local playback. Browser mode uses a
remote browser's microphone/speaker through an HTTPS or localhost UI proxy to
the loopback voice agent; browser mode must never open board ALSA or receive
board PCM.

On `2026-10-21`, a Chromium synthetic microphone completed the two-second
browser recording and playback self-test through the `127.0.0.1` UI tunnel on
the 20T board. The generated in-memory WAV was 16 kHz, mono, signed 16-bit,
57,388 bytes, RMS `2880.5`, and peak `32767`; the page started one browser
playback and reported completion. This is `observed-pass` for the browser
capture/encoding/playback data path only.

The same board then ran the shared Python 3.9 `ascend` environment with
`sherpa-onnx 1.13.6` and the Apache-2.0
`sherpa-onnx-streaming-zipformer-zh-14M-2023-02-23` int8 model. The archive
SHA-256 is `2cbd71b640d9c37d3784f29367333a4577b0398b62e9deeed418170b081cba8b`;
the encoder, decoder, joiner, and tokens SHA-256 values are recorded in the
[board voice plan](08-board-voice-terminal-plan.md). A bundled 16 kHz Chinese
sample transcribed offline, then a Chromium synthetic-microphone turn produced
HTTP 200 from `/api/voice/browser/transcribe`, sent the recognized text through
the existing gateway, and displayed an assistant text reply. This is
`observed-pass` for one ASR-to-text technical sample in browser mode.

Board-side TTS is now available through system-level eSpeak NG and the unified
`ascend` environment. The deterministic ASR→LLM→TTS→ALSA path reached
`PIPELINE_PLAYBACK_COMPLETE` on the board output. On `2026-10-22`, the UI added
a browser-native TTS fallback using `SpeechSynthesisUtterance` and
`speechSynthesis` with `zh-CN` preference. A Chromium synthetic voice-input turn
completed recording, ASR, the LLM response, and the speech-synthesis call; the
automated probe recorded the full assistant text passed to the browser and the
UI reached “浏览器语音播报完成.” This is `observed-pass` for the browser
speech-synthesis call path, not proof of audible speaker output or voice
quality. Human listening review, USB/Jieli audibility on the current date, and
the browser/OS voice inventory remain separate gates.

The operator subsequently confirmed that a real browser microphone was
recognized through the same page. This is `observed-pass` for the
physical-browser microphone to ASR-to-text technical path, but not for a fixed
reference prompt set, word/character error rate, board-local microphone, speaker
audibility, USB/3.5mm routing, VAD, board TTS, hostile-client audio privacy, or a
fully human-reviewed spoken voice turn. The synthetic and manual samples are not
substitutes for those gates. The remaining hardware and speech gates stay
`untested` until collected.

## Known Limits

- The worker serializes all requests; latency increases for concurrent users.
- The public UI has no browser login and is suitable only for a restricted LAN.
- Context is capped at 1024 tokens. The browser defaults to 128 output tokens and the service ceiling is 256, subject to each profile's declared ceiling. Qwen2 1.5B is currently capped at 192 by its measured 20T boundary.
- Retrieval augmentation is not part of this release; keeping it outside the gateway preserves the fixed prompt budget.
- The test baseline uses a shared hardware image; it is not a clean-room production certification.
- The release does not verify Ascend310B4 / 8T. Run the full gate independently before describing that hardware as supported.
- The `SN0002 1080P USB Camera` (`2bdf:0289`) can be used for UVC video, but its UAC microphone path is incompatible with the tested Orange Pi AI Pro 20T and 8T system images; capture stops with `Input/output error` and it must not be selected as a voice input. Details and separate per-board evidence are in [Voice Modes and Board Terminal Plan](08-board-voice-terminal-plan.md#known-camera-microphone-incompatibility).
- Historical alternative models, conversion paths, speech experiments, and device integrations are archived evidence only.
