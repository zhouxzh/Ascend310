# Qwen2.5 Full-Context 2048 Rebuild: Dual-Board Record

## Scope

This record covers a fresh controller-side export of the fixed-length Qwen2.5
0.5B full-context ONNX graph and the first 8T/20T board checks. It is not a
promotion record. Audio, the gateway, browser UI, XiaoZhi, Chinese quality,
and long-generation stability are outside this run.

The two boards were tested separately:

| Board | Address at test time | SoC / tier | Result scope |
| --- | --- | --- | --- |
| 8T | `192.168.1.135` | Ascend 310B4 / 8T | ONNX integrity and ATC memory boundary |
| 20T | `192.168.1.95` (earlier), `192.168.1.101` (latest) | Ascend 310B1 / 20T | ONNX integrity, ATC, ACL descriptor, JSON/SSE smoke |

`Health: Alarm` was present in `npu-smi` snapshots on both boards and was
recorded as diagnostics only.

The endpoint address must not be used to infer board tier. A fresh, isolated
SSH check at `192.168.1.101` reported hostname `orangepiaipro-20t`, device
`Ascend310B1`, and 23,673 MB device memory. It is therefore the 20T board's
new address, not a replacement address for the unavailable 8T board. The SSH
host key was checked in a temporary known-hosts file; no global SSH trust
record was changed.

## Rebuilt Artifacts

The controller used `Qwen/Qwen2.5-0.5B-Instruct` revision
`7ae557604adf67be50417f59c2c2f167def9a775`. The export is FP16, batch 1,
with static `input_ids`, `attention_mask`, and `position_ids`, each
`int64 [1,2048]`.

| Artifact | Bytes | SHA-256 | Static output |
| --- | ---: | --- | --- |
| Full logits ONNX | 1,269,330,318 | `ed6e7ef5bcc6124e95be317f20b04e813d16dd797453f0217dbfe78ad11eb939` | `float16 [1,2048,151936]` |
| Last-logits ONNX | 1,269,331,189 | `00bc256de3ee22daf86048134d46ff101fca002697e14026b1592d303ad6cce8` | `float16 [1,1,151936]` |
| Source `model.safetensors` | 988,097,824 | `fdf756fa7fcbe7404d5c60e26bff1a0c8b8aa1f72ced49e7dd0210fe288fb7fe` | controller export input |
| `tokenizer.json` | 7,031,645 | `c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539` | Qwen tokenizer |

The local lock is `repro/qwen25-full2048-20260914/artifact-lock.json`.
The ONNX checker/contract inspection passed before transfer: no dynamic
dimensions, no external initializers, opset 17, and no unapproved operators.

## Network After Board Reboot

Before the reboot, large transfers degraded to tens of KiB/s. After the
reboot, both boards used the same `Zhong` Wi-Fi AP via Realtek `rtl8821cu`,
2.4 GHz channel 6, reported 130 Mbit/s, and had 40%/42% signal. `eth0` was
down on both boards.

| Measurement | Observed throughput | Interpretation |
| --- | ---: | --- |
| 20T -> 8T `iperf3` receiver | 12.9 Mbit/s | observed-pass, usable but weak Wi-Fi |
| 8T -> 20T `iperf3` receiver | 15.2 Mbit/s | observed-pass, usable but weak Wi-Fi |
| Controller -> 8T raw TCP, 16 MiB | 2.31 MiB/s | observed-pass |
| Controller -> 20T raw TCP, 16 MiB | 2.28 MiB/s | observed-pass |
| WSL `rsync` controller -> 8T ONNX resume | 4.12 MB/s average for final 596 MB | observed-pass |
| WSL `rsync` controller -> 20T ONNX resume | 2.86 MB/s average for final 910 MB | observed-pass |

The WSL command explicitly used a private-permission copy of the existing
Windows `id_rsa`, because `/mnt/c/.../id_rsa` appears as mode `0777` to WSL
OpenSSH and is rejected. No new SSH key was generated.

## 8T ATC Boundary

The full ONNX passed bytes/SHA admission on the 8T board. ATC was run with
`--soc_version=Ascend310B4`, CANN 8.0, and the exact 2048 input contract.
The default run first failed because the CANN TBE process could not import
NumPy; loading the board `base` conda environment before CANN corrected that
environment issue.

The corrected default ATC run was killed by the Linux global OOM killer.
The kernel recorded `atc.bin` at approximately 1.95 GiB RSS. A second run
used only process-scoped controls found in the installed CANN source:
`TBE_PARALLEL_COMPILER=0` and `TE_PARALLEL_COMPILER=1`. It ran longer but was
also killed by the OOM killer while the 7.4 GiB board had no swap. No OM was
generated. This is an observed failure for this exact ONNX/CANN/8T tuple, not
a general unsupported claim about Qwen or Ascend 310B4.

Logs are retained on the board under
`~/case9-qwen25-full2048-20260914/logs/atc-qwen25-static-2048-b4*.log`.

## 20T B1 ATC and ACL Smoke

On the 20T board, CANN 8.0 ATC with `--soc_version=Ascend310B1` completed.

| Gate | Observed result |
| --- | --- |
| ONNX SHA-256 | passed |
| ATC | passed |
| B1 OM | 1,406,879,335 bytes; SHA-256 `530906aea22d8a4145489aee8e060593acd294f29def87112022408c61d5fe14` |
| ACL load/descriptor/cleanup | passed |
| ACL inputs | three `int64 [1,2048]` tensors, each 16,384 bytes |
| ACL output | `/Cast:0:logits`, `float16 [1,2048,151936]`, 622,329,856 bytes |
| JSON API, one token | HTTP 200; 8.702 s |
| SSE API, one token | HTTP 200; 8.585 s; one text delta and `[DONE]` |

An additional strict loopback API run used three serial JSON requests and one
SSE request, all with `max_tokens=1`, `temperature=0`, and `top_p=1`.
All four requests passed without UTF-8 replacement characters. The JSON
latencies were 8.706 s, 8.556 s, and 8.528 s; the SSE latency was 8.555 s.
The returned text remained `" Moh"`, so this is an API/ACL stability result,
not a Chinese-quality result.

The candidate service bound only to `127.0.0.1:18084` and was stopped after
the smoke. It used an explicit dirty-base test override; existing Torch and
MindSpore packages remained present but were not imported by the ACL service.
The one-token output was `" Moh"`. It is protocol evidence only and does not
establish Chinese quality, practical latency, or candidate admission.

Raw reports remain on the 20T board under
`~/case9-qwen25-full2048-20260914/reports/`, including the OM descriptor and
OpenAI JSON/SSE probe. Models, ONNX files, OM files, transfer logs, and board
reports remain ignored by Git.

## 20T Eight-Token Follow-up

The 20T board was rechecked at `192.168.1.101` with the same verified B1 OM,
the same CANN 8.0/base environment, and a loopback-only candidate service on
`127.0.0.1:18084`. This was a bounded follow-up, not a formal performance
campaign: one JSON request and one SSE request each used greedy decoding,
`max_tokens=8`, and the Chinese prompt `你好，请用中文回答一句话。`.

| Gate | Observed result |
| --- | --- |
| Service startup and `/health` | passed |
| JSON transport | HTTP 200; 8 completion tokens; 59.343 s |
| SSE transport | HTTP 200; 8 completion tokens and `[DONE]`; 59.180 s |
| UTF-8 acceptance | failed: both responses contain `U+FFFD` |
| Generated text | ` Holden 纲纲 radiation�azzi Holden` |
| Approximate end-to-end generation rate | 0.135 token/s from JSON elapsed time |
| Candidate process cleanup | passed: port `18084` was not listening after the run |

The strict test correctly marks this run `failed`: the HTTP protocol completed,
but the decoded text contains a replacement character and is not a usable
Chinese response. The evidence does not establish the root cause; it is scoped
to this full-context ONNX, B1 OM, ACL runtime, tokenizer, mask/template, and
generation path. In particular, it must not be reported as a successful
chat-quality or performance result.

The device-memory snapshots were 3,295 MB before and 3,480 MB after service
cleanup. That 185 MB difference is a diagnostic observation from one run, not
a leak conclusion. The exact API report, NPU snapshots, and service log were
copied to `models/qwen25-full2048/reports/board20t/` and verified with matching
SHA-256 values between board and controller.

## Local Model Mirror and Cross-SoC Status

The user requested a local mirror. The verified source checkpoint, tokenizer,
configuration, full and last-logits ONNX artifacts, contracts, and the 20T B1
OM are now available under `models/qwen25-full2048/`. The B1 OM was copied
back from the 20T board and rechecked locally at 1,406,879,335 bytes and
SHA-256 `530906aea22d8a4145489aee8e060593acd294f29def87112022408c61d5fe14`.
The copied 20T ATC, descriptor, API, service, and NPU reports are stored below
`models/qwen25-full2048/reports/board20t/`. This local model mirror is ignored
by Git.

The intended test of this B1 OM on the 8T B4 board has not run. Before the
verified local OM could be uploaded to 8T, `192.168.1.135` became unreachable:
ICMP reported `DestinationHostUnreachable` and no ARP entry was present. The
state is therefore `not-run` due to board network unavailability, not a
cross-SoC incompatibility result. If it is later tested, it must be labelled a
compatibility experiment rather than a native B4 result.

## Status

| Board / artifact | Status | Next gate |
| --- | --- | --- |
| 8T B4 full-context 2048 native conversion | `blocked` | Reduce graph/ATC peak memory through a separately approved export or conversion strategy; do not claim a native B4 OM exists. |
| 8T B4 using 20T B1 OM | `not-run` | Restore board network, copy the locally verified B1 OM, then run descriptor and ACL/API compatibility gates. |
| 20T B1 full-context 2048 | `single_token_api_passed; multi_token_failed` | Isolate tokenizer/template/cache/logits semantics before any quality or performance campaign. |
| Last-logits ONNX | `artifact_verified` | Separate B4/B1 ATC campaign; it has not been converted in this run. |
