# 20T Deployment

## Preconditions

Use this procedure only on the validated 20T hardware class. It does not install a driver, firmware, CANN toolkit, OPP package, MindSpore, or MindNLP. First choose the existing Conda environment that contains the tested runtime.

```bash
cd /opt/ascend-edge-chat
export ASCEND_EDGE_CHAT_CONDA_ENV='<validated-conda-environment>'
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate "$ASCEND_EDGE_CHAT_CONDA_ENV"
python -m pip install --no-deps .
export ASCEND_EDGE_CHAT_DATA_DIR=/opt/ascend-edge-chat-data
cp configs/release.env.example .env
chmod 600 .env
```

Edit `.env` and replace `ASCEND_EDGE_CHAT_GATEWAY_API_KEY` with a random ASCII token of at least 24 characters. Do not put the token in a browser, a client-side script, a commit, or a shell history capture.

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

The preflight records the active Python executable, imports `acl`, MindSpore, and MindNLP, shows `npu-smi info`, validates every locked model artifact, and parses gateway configuration. Stop here if it fails. A successful import is not a generation result; the next steps perform API verification.

## Model Installation

For online acquisition through the configured Hugging Face mirror:

```bash
bash scripts/import/download-model.sh
```

For an offline source directory containing exactly the locked file names:

```bash
bash scripts/import/import-model.sh /mnt/offline/qwen1.5-0.5b
```

Both methods write a hidden `.part` file, check the declared byte size and SHA-256, then atomically rename it. An incomplete or mismatched file is removed; a verified existing file is retained.

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

The worker and gateway bind only loopback. The UI binds `0.0.0.0:7865` to allow trusted-LAN access. Use a firewall or VLAN to limit access to port `7865`.

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

Then browse to `http://<board-ip>:7865/` and submit a short prompt. The model runs one request at a time; a second request receives a bounded busy response rather than queueing unbounded work.

## Rollback

This release does not replace a prior service automatically. To roll back, stop only the three foreground release processes, keep model files and logs for diagnosis, and start the previously validated deployment from its own directory. Do not delete shared Conda caches, CANN installation directories, or unknown board workloads.
