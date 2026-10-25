# Ascend310B4 Static-KV Release

This document defines the formal Ascend310B4 / 8T text-chat release path. It is
separate from the Ascend310B1 / 20T MindSpore path. The two targets have
different runtimes, model artifacts, output budgets, and acceptance evidence.

## Release identity

| Item | Fixed value |
| --- | --- |
| Target | Ascend310B4 / 8T |
| CANN | `8.0.0` |
| Python | `3.9.25`, `cpython-39-aarch64-linux-gnu` |
| Runtime | Native PyACL, no CPU or cloud fallback |
| Public model artifact repository | `zhouxzh/ascend310b-llm-om-zoo` |
| Artifact revision | `0f516070e1c0566e3c652d43c488cd3cfbad89a3` |
| Base model | `Qwen/Qwen2.5-0.5B-Instruct` |
| Base model revision | `7ae557604adf67be50417f59c2c2f167def9a775` |
| License | Apache-2.0 |
| Context | 1024 tokens |
| Default output budget | 64 tokens |
| Browser output choices | 64 or 80 tokens |
| Maximum output budget | 80 tokens |
| Public model name | `ascend-edge-chat` |
| Internal ACL model id | `qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om` |

The immutable byte counts, SHA-256 values, and source paths are in
[`release-8t-static-kv.lock.json`](../configs/release-8t-static-kv.lock.json).
The service derives schema-2 runtime locks inside the data directory after all
public artifacts have passed verification.

## Required artifacts

The release downloads nine files:

- static-KV ONNX graph;
- tokenizer and tokenizer configuration;
- controller/export contract;
- B4 `CANN 8.0` OM;
- B4 descriptor contract;
- public tokenizer lock and schema-1 OM provenance lock;
- upstream Apache-2.0 license.

The downloader accepts no URL or destination from the caller. It uses only
`https://hf-mirror.com`, forbids path traversal and symlinks, writes through
`.part` files, and verifies both byte count and SHA-256 before rename.

## Install dependencies

All processes use the unified `ascend` environment. Install the hash-locked
8T closure into a reviewed local wheelhouse:

```bash
cd /opt/ascend-edge-chat
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate ascend
export PYTHONNOUSERSITE=1
python -m pip install --no-deps .

export ASCEND_EDGE_CHAT_WHEELHOUSE=/opt/ascend-edge-chat-wheelhouse
export ASCEND_EDGE_CHAT_REQUIREMENTS_FILE=/opt/ascend-edge-chat/configs/release-8t-service-requirements.txt
bash scripts/verify/install-service-dependencies.sh
```

Do not run case programs from `base` or `~/.local`. CANN and the NPU driver
remain board-owned system components.

## Configure

```bash
cp configs/release-8t.env.example .env
chmod 600 .env
```

Replace only the placeholder gateway token. The important fixed values are:

```text
ASCEND_EDGE_CHAT_BACKEND=acl-static-kv
ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL=http://127.0.0.1:8084/v1
ASCEND_EDGE_CHAT_UPSTREAM_MODEL=qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om
ASCEND_EDGE_CHAT_UI_WORKER_MODE=external
ASCEND_EDGE_CHAT_UI_WORKER_HEALTH_URL=http://127.0.0.1:8084/health
```

The gateway rejects an ACL backend pointed at `8090`, a MindSpore backend
pointed at `8084`, or a mismatched internal model id. The browser still sees
only `ascend-edge-chat`.

## Acquire and verify

```bash
export ASCEND_EDGE_CHAT_ENV_FILE=/opt/ascend-edge-chat/.env
bash scripts/import/download-static-release.sh
bash scripts/verify/release-8t-preflight.sh
```

`release-8t-preflight.sh` performs these gates before any service starts:

1. activate `ascend` and source CANN;
2. require Python `3.9.25` and disabled user-site imports;
3. import PyACL, NumPy, tokenizers, FastAPI, HTTPX, Pydantic, and Uvicorn;
4. identify `Ascend310B4` from `npu-smi`;
5. verify all nine public artifacts and derive runtime locks;
6. validate gateway and UI backend contracts.

## Start

Run the foreground launcher:

```bash
bash scripts/run/run-release-8t.sh
```

It starts:

```text
ACL static-KV :8084
Gateway       :7861
UI            :7865
```

Open `http://<board-ip>:7865/` only on a trusted LAN. The UI has no browser
login. The gateway key remains server-side. `Ctrl+C` stops all three processes
with bounded TERM/KILL cleanup.

The UI reports readiness only when the ACL health response satisfies:

```text
ready=true
healthy=true
model=qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om
backend=acl_om
sequence_length=1024
descriptor_validated=true
```

Model switching is disabled for this backend.

## API smoke

```bash
source .env
curl -H "Authorization: Bearer $ASCEND_EDGE_CHAT_GATEWAY_API_KEY" \
  http://127.0.0.1:7861/v1/models

curl -H "Authorization: Bearer $ASCEND_EDGE_CHAT_GATEWAY_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model":"ascend-edge-chat","messages":[{"role":"user","content":"你好"}],"max_tokens":4}' \
  http://127.0.0.1:7861/v1/chat/completions
```

The response must use `model=ascend-edge-chat`; the internal ACL model id must
not cross the gateway boundary.

## Machine acceptance

Run from a clean checkout of the public source commit:

```bash
ASCEND_EDGE_CHAT_MODEL_SOURCE=hf \
  bash scripts/verify/run-release-8t-acceptance.sh
```

The report includes:

- source repository and current public `main` commit;
- clean Git status;
- public HF repository and fixed revision;
- every downloaded artifact's bytes and SHA-256;
- unified environment and `npu-smi` output;
- gateway authentication and model-name boundaries;
- JSON and SSE completions;
- descriptor and KV-cache lock evidence;
- Chinese probes, length checks, stability requests, and measured latency.

The static graph executes one token per ACL invocation. Its latency is expected
to be much higher than the 20T MindSpore path and must be recorded separately.

The public source commit
`c11c96eb625403a2e160792c6428f2eaa341018b` returned
`machine_status=passed`. The report verified clean public provenance, nine HF
artifacts, gateway/UI, JSON/SSE, output budgets 8/16/32/64, 10/10 stability
requests, and 10 Chinese machine probes. Thirty two-token SSE samples measured
first-token p50/p95 `10623.506/10656.206 ms`, total p50/p95
`10755.880/10786.937 ms`, and `0.186 token/s` at both percentiles. Human
Chinese semantic review remains pending; the low static-graph throughput is not
a network or API-proxy measurement.

A separate output-budget probe asked the fixed question
`请简单介绍一下昇腾310B` with `max_tokens=80`. It used 38 prompt tokens, returned
41 completion tokens and 54 characters, and finished with
`finish_reason=stop`. This demonstrates that the configured ceiling can produce
a complete paragraph rather than being a nominal limit only. It does not prove
semantic quality: the small model repeated part of the sentence. The new
64-token default would not truncate this measured 41-token response, but longer
answers can still end with `finish_reason=length`.

The 240-second execution watchdog is an application fail-closed budget, not a
B4 memory specification. With a temporary test worker at `max_tokens=1024` and
`execution_timeout=3600`, 128 and 256 completion tokens completed while preserving
`watchdog_triggered=false`; 256 tokens took about 1984 seconds and kept NPU
usage near `5.9 GB`. The release remains capped at 80 because 80 tokens already
take about 225 seconds under the current product watchdog. For a prompt of 55
tokens, the 1024-token context ceiling is 969 output tokens, but that full
generation has not been completed and is not advertised as a release limit.

## Failure handling

| Failure | Stop condition |
| --- | --- |
| Source tree is dirty or not at public `main` | Do not claim public-release reproduction |
| HF artifact byte/SHA mismatch | Do not download, convert, load, or serve |
| `npu-smi` does not report Ascend310B4 | Stop; do not use a B1/20T OM |
| Schema-1 upstream OM lock is used directly | Stop; use the derived schema-2 runtime lock |
| Descriptor is not 51 inputs / 49 outputs | Stop before ACL execution |
| UI external health is degraded | Do not send chat; fix the ACL worker first |
| Any attempt to switch models | Reject; the external ACL worker is fixed |
| Model source is local or uncommitted | Label `blocked`, not `observed-pass` |

## Limits

This release does not claim continuous production availability, simultaneous
multi-model loading, voice support, retrieval, or human-approved Chinese answer
quality. The machine release can pass while semantic quality remains pending.
Performance and quality measurements from 20T never apply to this 8T target.
