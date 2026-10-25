# API and Security

## Network Boundaries

| Service | Bind address | Purpose | Authentication |
| --- | --- | --- | --- |
| Text UI | `0.0.0.0:7865` | Browser chat on a trusted LAN | None |
| Gateway | `127.0.0.1:7861` | Public OpenAI-compatible contract | Bearer token |
| MindSpore worker | `127.0.0.1:8090` | Internal 20T inference | Not network reachable |
| ACL static-KV worker | `127.0.0.1:8084` | Internal 8T inference | Not network reachable; fixed model |

The UI is intentionally unauthenticated. This is a deployment constraint, not an access-control feature: hosts on the same reachable network can submit messages, read the sanitized voice status, and request allowlisted board-audio mode/device changes through the UI proxy when voice mode is enabled. The voice agent's own HTTP listener remains loopback-only, and arbitrary device paths or commands are rejected. Do not port-forward the UI, put it behind a public reverse proxy, or use it on an untrusted network.

## Gateway Authentication

Set a server-side secret in `.env`:

For the 20T MindSpore backend:

```dotenv
ASCEND_EDGE_CHAT_GATEWAY_API_KEY=replace-with-a-random-long-token
ASCEND_EDGE_CHAT_BACKEND=mindspore
ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL=http://127.0.0.1:8090/v1
ASCEND_EDGE_CHAT_UPSTREAM_MODEL=ascend-edge-chat-worker
```

For the fixed 8T ACL backend:

```dotenv
ASCEND_EDGE_CHAT_GATEWAY_API_KEY=replace-with-a-random-long-token
ASCEND_EDGE_CHAT_BACKEND=acl-static-kv
ASCEND_EDGE_CHAT_UPSTREAM_BASE_URL=http://127.0.0.1:8084/v1
ASCEND_EDGE_CHAT_UPSTREAM_MODEL=qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om
```

The gateway reads only `ASCEND_EDGE_CHAT_` variables. It rejects an ACL backend on port `8090`, a MindSpore backend on `8084`, and any mismatched internal model id. Legacy unprefixed variables do not configure the release. The key must be an ASCII token of at least 24 characters. The UI reads the key only from its server-side process environment; it never returns it in HTML, JSON, or browser JavaScript.

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

The release accepts `system`, `user`, and `assistant` roles. It accepts only greedy decoding (`temperature=0`, `top_p=1`) with one request at a time and a 1024-token context. The 20T MindSpore path defaults to 128 output tokens and caps at 256, subject to the selected profile. The 8T ACL path defaults to 16 and caps at 80. It rejects backend/model/port cross-wiring, non-loopback upstreams, arbitrary paths, and arbitrary expressions. The 8T UI disables model switching.

For `stream:true`, the gateway emits standard `data:` SSE chunks and one terminal `data: [DONE]`. It rewrites the internal worker model identifier to `ascend-edge-chat` before sending the result to the client.

## Resource and Failure Limits

- request body: 256 KiB maximum;
- browser message history: at most four messages and 4,000 characters in server memory;
- gateway and worker: one in-flight request;
- MindSpore worker watchdog: 300 seconds by default;
- ACL execution watchdog: 240 seconds maximum;
- browser write timeout: 30 seconds;
- worker error or timeout: fail closed instead of falling back to CPU, cloud, or another model.

Conversation memory is process-local. Clearing the browser conversation, restarting the UI, or stopping the UI discards it. The service does not create a message database.
