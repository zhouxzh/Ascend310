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

The tested MindSpore loader did not expose parameter-level device placement. Its NPU evidence is therefore `context_verified`: `mindspore.set_context(device_target="Ascend")` succeeded and `npu-smi` reported the expected 310B1 device. This is stronger than an import check, but it is not equivalent to a parameter-placement trace; release records must preserve that distinction.

## Quality State

The fixed model passed automated completion integrity checks for a ten-prompt Chinese probe. Human Chinese-quality review is **pending**. Therefore this release is a technical preview and must not claim that its answers are reliable, complete, safe, or suitable for high-stakes decisions.

## Revalidation Gates

Before publishing a release tag, verify the package on the target board with the locked environment and artifact set:

1. environment fingerprint, CANN setup, NPU visibility, and model hashes;
2. worker NPU initialization and one-token generation;
3. gateway authentication, `/v1/models`, JSON completion, and SSE completion;
4. `8`, `16`, `32`, and `64` token outputs, including UTF-8 and terminal reason checks;
5. ten serial requests with worker PID, RSS, file descriptors, and NPU snapshots;
6. browser UI chat through the gateway;
7. recorded latency and token/s parameters, including warmup and percentile method;
8. explicit human review of the Chinese probe set.

## Known Limits

- The worker serializes all requests; latency increases for concurrent users.
- The public UI has no browser login and is suitable only for a restricted LAN.
- Context is capped at 1024 tokens and output is capped at 64 tokens.
- Retrieval augmentation is not part of this release; keeping it outside the gateway preserves the fixed prompt budget.
- The test baseline uses a shared hardware image; it is not a clean-room production certification.
- The release does not verify Ascend310B4 / 8T. Run the full gate independently before describing that hardware as supported.
- Historical alternative models, conversion paths, speech experiments, and device integrations are archived evidence only.
