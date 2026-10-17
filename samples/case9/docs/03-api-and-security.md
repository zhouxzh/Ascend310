# API and Security

## Network Boundaries

| Service | Bind address | Purpose | Authentication |
| --- | --- | --- | --- |
| Text UI | `0.0.0.0:7865` | Browser chat on a trusted LAN | None |
| Gateway | `127.0.0.1:7861` | Public OpenAI-compatible contract | Bearer token |
| Worker | `127.0.0.1:8090` | Internal MindSpore inference | Not network reachable |

The UI is intentionally unauthenticated. This is a deployment constraint, not an access-control feature: hosts on the same reachable network can submit messages. Do not port-forward the UI, put it behind a public reverse proxy, or use it on an untrusted network.

## Gateway Authentication

Set a server-side secret in `.env`:

```dotenv
ASCEND_EDGE_CHAT_GATEWAY_API_KEY=replace-with-a-random-long-token
ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL=http://127.0.0.1:8090/v1
ASCEND_EDGE_CHAT_UPSTREAM_MODEL=ascend-edge-chat-worker
```

The gateway reads only `ASCEND_EDGE_CHAT_` variables. Legacy unprefixed variables do not configure the release. The key must be an ASCII token of at least 24 characters. The UI reads the key only from its server-side process environment; it never returns it in HTML, JSON, or browser JavaScript.

## OpenAI-Compatible Endpoints

```text
GET  /health
GET  /v1/models
POST /v1/chat/completions
```

`GET /v1/models` and `POST /v1/chat/completions` require:

```http
Authorization: Bearer <ASCEND_EDGE_CHAT_GATEWAY_API_KEY>
```

Example non-streaming request:

```bash
curl http://127.0.0.1:7861/v1/chat/completions \
  -H "Authorization: Bearer $ASCEND_EDGE_CHAT_GATEWAY_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "ascend-edge-chat",
    "messages": [{"role":"user","content":"你好，请介绍自己。"}],
    "temperature": 0,
    "top_p": 1,
    "max_tokens": 32
  }'
```

The release accepts `system`, `user`, and `assistant` roles. It accepts only greedy decoding (`temperature=0`, `top_p=1`), one request at a time, context at most 1024 tokens, and `max_tokens` at most 64. It rejects model changes, non-loopback upstreams, arbitrary paths, and arbitrary expressions.

For `stream:true`, the gateway emits standard `data:` SSE chunks and one terminal `data: [DONE]`. It rewrites the internal worker model identifier to `ascend-edge-chat` before sending the result to the client.

## Resource and Failure Limits

- request body: 256 KiB maximum;
- browser message history: at most four messages and 4,000 characters in server memory;
- gateway and worker: one in-flight request;
- worker watchdog: 300 seconds by default;
- browser write timeout: 30 seconds;
- worker error or timeout: fail closed instead of falling back to CPU, cloud, or another model.

Conversation memory is process-local. Clearing the browser conversation, restarting the UI, or stopping the UI discards it. The service does not create a message database.
