# XiaoZhi Gateway Architecture And Acceptance

## Deployment Boundary

`case9` is an OpenAI-compatible text gateway, not a XiaoZhi device server. The
deployment needs three independently operated services:

| Service | Network role | Required evidence before production use |
| --- | --- | --- |
| XiaoZhi server | Device-facing WebSocket/MQTT, ASR, TTS and sessions | Device authentication, WSS/TLS, one-device voice loop |
| case9 gateway | Internal text RAG and LLM proxy | Token check, JSON completion, SSE completion, request bounds |
| LLM upstream | Text generation | Current candidate: no-Torch ACL/OM Qwen service; model/provider acceptance, latency and failure behavior |

The gateway must be reachable only from the XiaoZhi server or a controlled
reverse proxy. It has no browser UI and no endpoint for changing upstream
configuration at runtime.

## Request Contract

The gateway requires `Authorization: Bearer <GATEWAY_API_KEY>` for `/v1/*`.
The only advertised model is `PUBLIC_MODEL_ID`, normally `case9-rag`. A caller
can select generation parameters but cannot select the real upstream model: the
gateway replaces the request model with `UPSTREAM_MODEL` before forwarding.

Supported request fields are `model`, `messages`, `stream`, `max_tokens`,
`temperature`, `top_p`, `frequency_penalty`, `presence_penalty`, `stop`, and
`user`. Message roles are limited to `system`, `user`, and `assistant`.

When `stream` is true, the upstream must use OpenAI-style Server-Sent Events.
The gateway opens the upstream request before returning its own response so an
upstream HTTP failure remains an OpenAI-shaped JSON error instead of a partial
SSE response. It requires a 2xx `text/event-stream` response before streaming;
once streaming starts, it forwards data with configured total-duration and
total-byte limits.

The ASGI layer counts chunks before parsing JSON and enforces a total request
body deadline, so a missing or chunked `Content-Length` cannot force an
unbounded in-memory body buffer or hold a worker waiting for a slow upload. The
process also has a direct-peer rate limit and a non-queuing upstream concurrency cap.
Deployments with multiple workers or replicas still need matching limits at a
trusted reverse proxy because process-local counters are not cluster-wide.

## Retrieval Contract

The current local retriever reads small UTF-8 Markdown or text files from
`RAG_DOCUMENTS_DIR`. It rejects paths that resolve outside the configured
directory, ignores non-text files and ignores files larger than 1 MB. The
retrieval result is not returned to the device; it is placed in an explicit
system reference block for the upstream model.

This is a lexical baseline, not a claim of semantic embedding quality or NPU
acceleration. The board-side model campaign must introduce a separate provider
with explicit artifact, conversion, ACL, numerical, quality and performance
evidence before it replaces the baseline.

The current board-side LLM decision is documented in
[`05-llm-backend-research-and-decision.md`](05-llm-backend-research-and-decision.md).
The implementation and fail-closed gates are in
[`06-acl-om-llm-deployment-plan.md`](06-acl-om-llm-deployment-plan.md), and the blank
evidence form is in [`07-acl-om-validation-record.md`](07-acl-om-validation-record.md).
The former llama.cpp CANN attempt remains historical evidence; it is not an accepted
upstream until a separate board run passes all ACL/NPU gates.

## XiaoZhi Provider Configuration

Use the OpenAI provider in `xinnan-tech/xiaozhi-esp32-server` and disable tools
until tool-call streaming is implemented:

```yaml
selected_module:
  LLM: Case9RagLLM
  Intent: nointent

LLM:
  Case9RagLLM:
    type: openai
    base_url: http://case9-rag-gateway:7861/v1
    model_name: case9-rag
    api_key: replace-with-the-internal-gateway-token
```

Do not point the XiaoZhi device at this URL. Its device-facing endpoint remains
the XiaoZhi server's WebSocket or MQTT endpoint.

## Acceptance Sequence

1. Run local syntax and unit tests. They use an in-memory upstream double and
   make no NPU, ATC, ACL, device, audio, or network calls.
2. On the Ascend board, pass the no-Torch environment, artifact, ONNX contract,
   ATC, OM, ACL smoke, and NPU evidence gates in
   [`06-acl-om-llm-deployment-plan.md`](06-acl-om-llm-deployment-plan.md).
3. Start the ACL/OM service on loopback and verify `/v1/models`, JSON completion,
   and SSE completion; then verify case9 gateway `/health`, authenticated model
   listing, and upstream forwarding.
4. Resume local browser text chat, then perform the separately measured ASR/TTS
   audio loop. Audio success cannot substitute for an LLM/NPU gate.
5. Keep XiaoZhi uninstalled and stopped until its no-Torch ASR/VAD/TTS profile and
   a real device are separately approved. Only then add device authentication,
   TLS/WSS, rate limits, secret rotation, and a one-device voice loop.
