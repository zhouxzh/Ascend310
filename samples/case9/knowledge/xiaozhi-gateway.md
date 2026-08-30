# XiaoZhi gateway reference

This case exposes a text-only OpenAI-compatible gateway for a XiaoZhi server.
The XiaoZhi server owns device sessions, WebSocket or MQTT audio transport,
speech recognition, and speech synthesis. The gateway only accepts authenticated
chat-completions requests, retrieves local references, and forwards the request
to its configured text-generation upstream.

Do not put API keys, personal information, device secrets, or unreviewed prompt
instructions in this directory. Documents are untrusted reference material and
are passed to the upstream LLM only when they match the current user question.

The gateway is stateless. Conversation history must be supplied by the XiaoZhi
server in each request, so one device cannot inherit another device's messages.

The initial retriever is lexical and intentionally does not claim Ascend NPU
acceleration. A Chinese embedding model and an Ascend ACL adapter may replace it
only after artifact integrity, ONNX contract, ATC, ACL, numerical-consistency,
quality, and performance gates have been measured on the intended board.
