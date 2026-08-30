# Board Gateway Acceptance Record

## Scope And Environment

This record covers the OpenAI-compatible gateway on the replacement board on
2026-08-20. It is a protocol acceptance run, not an LLM quality, XiaoZhi voice,
ATC, ACL, OM, or NPU performance result.

| Item | Observed value |
| --- | --- |
| Board host | `orangepiaipro` |
| Architecture | `aarch64` |
| NPU device reported by `npu-smi` | Ascend 310B4 / 8T |
| Active project Python | `3.9.2` from the board `base` conda environment |
| FastAPI / HTTPX / Pydantic / Uvicorn | `0.115.2` / `0.27.2` / `2.5.3` / `0.32.0` |
| Gateway workspace | `/home/HwHiAiUser/case9-xiaozhi-gateway` |

The CANN environment and the board `base` conda environment were activated in
the same shell for every board Python command. No board package was installed,
upgraded, removed, or configured for this run.

## Source And Runtime Checks

- The controlled upstream helper `tests/board_upstream_stub.py` matched the
  controller SHA-256: `ccc1be226a192043fa1270d2d8515a3568083c9263fceed6b2403a5caceaafe7`.
- `app.py`, `config.py`, `retrieval.py`, `upstream.py`, the test modules, and
  the controlled upstream helper passed `python -m py_compile` on the board.
- Constructing `ChatCompletionRequest` with the board's Pydantic 2.5.3 passed.
  This specifically verifies the Python 3.9-compatible `Optional` and `Union`
  annotations used in runtime Pydantic models.
- `python -m unittest discover -s tests -v` passed all 21 tests on the board.
- The dependency ranges in `requirements.txt` admit the installed gateway
  packages. `python-dotenv` is absent on the board, and the explicit
  environment-variable configuration fallback was exercised successfully.

`python -m pip check` exits nonzero because the preinstalled CANN package
`op-compile-tool 0.1.0` declares standard-library modules (`getopt`, `inspect`,
and `multiprocessing`) as installable dependencies. This is pre-existing CANN
metadata, not a case9 package conflict; no attempt was made to modify it.

## Controlled Upstream Contract

A deterministic, standard-library test upstream bound only to
`127.0.0.1:18080`. The gateway used a separate loopback listener on
`127.0.0.1:7861`. The upstream does not run a model and returns only synthetic
JSON and SSE responses.

| Check | Result |
| --- | --- |
| `GET /health` | Pass: service status and public model returned |
| `GET /v1/models` with bearer token | Pass: advertised `case9-rag` |
| `/v1/models` without bearer token | Pass: HTTP 401 / `invalid_api_key` |
| Unknown model request | Pass: HTTP 404 / `model_not_found` without upstream call |
| JSON chat completion | Pass: request model was replaced by configured upstream model `board-stub-model` |
| SSE chat completion | Pass: two `delta.content` chunks forwarded and terminal `data: [DONE]` preserved |

## LAN Reachability Check

For a short separate acceptance run, the same synthetic upstream remained
loopback-only while the gateway temporarily listened on `0.0.0.0:7861`. A
Windows controller made authenticated requests to the board's private-network
address and passed health, token rejection, model listing, JSON forwarding, and
SSE forwarding checks. The SSE payload was parsed by event and its two content
chunks reassembled to `board upstream SSE acceptance response`.

Both temporary runs checked that ports `18080` and `7861` were unused before
starting. After the run, the exact test PIDs were terminated, the listeners
were verified absent, and only the test-created temporary logs were removed.
No existing service was stopped or modified.

## Unverified Boundaries

- No `xiaozhi-esp32-server` installation, device registration, OTA, WebSocket,
  Opus, ASR, TTS, or one-device voice loop was present on the board, so none of
  those checks ran.
- No real OpenAI-compatible LLM upstream or generation-quality evaluation ran.
- No ATC, ACL, OM inference, embedding acceleration, retrieval-quality metric,
  or NPU performance measurement ran. The board's NPU visibility is not
  evidence that those gates pass.

The next acceptance gate is the board-local no-Torch Qwen ACL/OM campaign, not a
XiaoZhi device test. First retain the fixed ONNX/tokenizer hashes, ONNX contract,
ATC/OM logs, ACL smoke output, and `npu-smi` snapshots. Only after the real ACL
service passes JSON/SSE and case9 gateway forwarding may the local browser text/audio
loop resume. XiaoZhi remains deferred until a no-Torch ASR/VAD/TTS profile and a
real test device are separately approved.
