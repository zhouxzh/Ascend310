# 20T Deployment

## Preconditions

Use this procedure only on the validated 20T hardware class. It does not install a driver, firmware, CANN toolkit, OPP package, MindSpore, or MindNLP. The process must use the repository-wide Conda environment `ascend`; it must not fall back to `base` or user-site packages.

```bash
cd /opt/ascend-edge-chat
export ASCEND_EDGE_CHAT_CONDA_ENV='ascend'
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate "$ASCEND_EDGE_CHAT_CONDA_ENV"
python -m pip install --no-deps .
export ASCEND_EDGE_CHAT_DATA_DIR=/opt/ascend-edge-chat-data
cp configs/release.env.example .env
chmod 600 .env
```

Edit `.env` and replace `ASCEND_EDGE_CHAT_GATEWAY_API_KEY` with a random ASCII token of at least 24 characters. Do not put the token in a browser, a client-side script, a commit, or a shell history capture.

The gateway, worker, and UI launch scripts automatically source `./.env` when it is readable. Set `ASCEND_EDGE_CHAT_ENV_FILE` to use a different private environment file. The file is sourced before conda/CANN activation and must remain mode `0600`; explicit assignments in the file take precedence over same-named shell variables, so review it before starting a service. The browser UI has no authentication: reachable LAN clients can submit chat and, when voice mode is enabled, read sanitized voice status or request allowlisted board-audio controls. Keep port `7865` on a trusted LAN.

## Service Dependency Closure

The worker must run without user-site packages. If preflight reports that FastAPI, Pydantic, Uvicorn, or dotenv is absent, download the reviewed wheels on a connected machine or the board into a regular directory, verify them against `configs/release-service-requirements.txt`, then install only that local wheelhouse:

```bash
export ASCEND_EDGE_CHAT_WHEELHOUSE=/opt/ascend-edge-chat-wheelhouse
bash scripts/verify/install-service-dependencies.sh
```

The installer uses `--no-index --no-deps --require-hashes`; it neither upgrades CANN/MindSpore nor installs a model or another inference framework. Record this service-layer installation in the deployment evidence.

## Environment Check

```bash
bash scripts/verify/release-preflight.sh
```

The preflight fail-closes unless the active process is the unified `ascend` environment with Python `3.9.25`, ABI `cpython-39-aarch64-linux-gnu`, `PYTHONNOUSERSITE=1`, PyACL, the locked MindSpore/MindNLP and CANN versions. It then records the active import origins, shows `npu-smi info`, validates every locked model artifact, and parses gateway configuration. Stop here if it fails. A successful import is not a generation result; the next steps perform API verification.

## Model Installation

For online acquisition through the configured Hugging Face mirror:

```bash
bash scripts/import/download-model.sh
export ASCEND_EDGE_CHAT_MODEL_SOURCE=hf
```

For an offline source directory containing exactly the locked file names:

```bash
bash scripts/import/import-model.sh /mnt/offline/qwen1.5-0.5b
export ASCEND_EDGE_CHAT_MODEL_SOURCE=offline-bundle
```

Both methods write a hidden `.part` file, check the declared byte size and SHA-256, then atomically rename it. An incomplete or mismatched file is removed; a verified existing file is retained. An offline source must additionally contain `offline-bundle.manifest.json` declaring `offline-bundle`, repository, revision, license, and every artifact's bytes/SHA-256. Offline provenance remains labelled `offline-bundle`; it is not evidence of an online Hugging Face download.

## Start and Stop

Open three board terminals in the deployment directory:

```bash
# Terminal 1
bash scripts/run/start-worker.sh

# Terminal 2
bash scripts/run/start-gateway.sh

# Terminal 3
bash scripts/run/start-ui.sh
```

All three commands intentionally run in the foreground. Stop the identified process with `Ctrl+C` in its own terminal. Do not kill unrelated NPU processes and do not use a broad process-name match.

The worker and gateway bind only loopback. The UI binds `0.0.0.0:7865` to allow trusted-LAN access. The tutorial UI intentionally has no browser login: a reachable LAN client can submit chat and, when the voice feature is enabled, read sanitized voice status or request allowlisted board-audio mode/device changes. Use a firewall or VLAN to limit access to port `7865`.

## Health Checks

```bash
curl http://127.0.0.1:8090/health
curl http://127.0.0.1:7861/health
curl -H "Authorization: Bearer $ASCEND_EDGE_CHAT_GATEWAY_API_KEY" \
  http://127.0.0.1:7861/v1/models
curl -N -H "Authorization: Bearer $ASCEND_EDGE_CHAT_GATEWAY_API_KEY" \
  -H 'Content-Type: application/json' \
  http://127.0.0.1:7861/v1/chat/completions \
  -d '{"model":"ascend-edge-chat","stream":true,"messages":[{"role":"user","content":"请用一句中文介绍你自己。"}],"temperature":0,"top_p":1,"max_tokens":16}'
```

Then browse to `http://<board-ip>:7865/` and submit a short prompt. The UI health payload includes `worker.reachable`, `worker.ready`, and `worker.healthy`; HTTP reachability of `:7865` or `:7861` alone is not model readiness. The model runs one request at a time; a second request receives a bounded busy response rather than queueing unbounded work. Before loading a model, the worker also checks tier-specific `npu-smi` HBM accounting. That preflight fails early on known high-occupancy boards; passing it is a load precondition, not a guarantee that the allocator will fit every profile.

## 2026-10-18 Service Validation

The running 20T service was validated from the browser route with the active
teaching profile `qwen2-0.5b-instruct-mindspore`. The UI `/api/config` and
`/api/chat` returned HTTP 200, the gateway accepted only the configured bearer
token and public model, and the service completed JSON, SSE, 8/16/32/64-token
length checks, ten short stability requests, and ten Chinese probes. The dated
raw acceptance report is retained on the board as ignored deployment evidence.

The first unspaced run hit `HTTP 429 Too many request bodies are being read`.
That is an admission-boundary observation caused by the test client's burst,
not an NPU error. Add a delay between requests and wait for streaming cleanup
when reproducing the acceptance run. The 32/64 budget labels are tokenizer
tokens, not Chinese-character counts. Human Chinese semantic review remains
pending.

## Output Token and Timeout Validation

The release profile `qwen1.5-0.5b-chat` defaults to 128 output tokens and caps
at 256. On `2026-10-25`, a repeat-pattern probe with 171 prompt tokens generated
all 256 output tokens in `143.984 s`, returned `finish_reason=length`, and left
the worker healthy.

A temporary test checkout raised the profile and gateway ceilings to exercise
the 1024-token context boundary. It generated 850 tokens in `507.982 s` with
NPU peak `14,444 / 23,673 MB`, then 853 tokens in `483.612 s` with
`total_tokens=1024`. The latter is the exact ceiling for that prompt:

```text
171 prompt tokens + 853 completion tokens = 1024 context tokens
```

The temporary test then stopped and the tracked files were restored. These
larger values are capacity evidence, not release defaults.

MindSpore uses a separate 300-second default watchdog, configurable up to 600
seconds. It is independent of the 8T ACL runtime's 240-second limit. Do not copy
the 8T static-KV timeout or OM limits into the 20T deployment.

## Rollback

This release does not replace a prior service automatically. To roll back, stop only the three foreground release processes, keep model files and logs for diagnosis, and start the previously validated deployment from its own directory. Do not delete shared Conda caches, CANN installation directories, or unknown board workloads.
