# Changelog

All notable changes to Ascend Edge Chat are recorded here.

## v0.2.0 - 2026-10-25

- Added a second formal release backend, `acl-static-kv`, for Ascend310B4 / 8T.
- Locked the Qwen2.5-0.5B-Instruct static-KV ONNX, tokenizer, contracts, B4 OM, license, and public Hugging Face revision.
- Added the tracked 51-input/49-output ACL runtime and public artifact verifier.
- Added fixed `:8084 -> :7861 -> :7865` lifecycle and acceptance entry points for the 8T backend.
- Kept the MindSpore static-KV requirement boundary unchanged: the main MindSpore route does not require OM conversion.
- The 8T backend exposes only `ascend-edge-chat`; its normal UI offers 64/80 output tokens, defaults to 64, and caps at 80. The 20T MindSpore profile defaults to 128 and caps at 256.
- The public-source 8T acceptance passed with nine locked HF artifacts, 10/10 stability requests, length checks, JSON/SSE/UI contracts, and a 30-sample two-token performance set.
- Documented the application-level watchdog policy and context formula. A watchdog-disabled test completed 256 tokens on 8T and 853 tokens on 20T; these are capacity evidence, not release defaults.
- Added a fixed-revision lock and static screening tool for 12 unique public quantized Qwen ONNX candidates. All hit unsupported quantization or `com.microsoft` operators; six representative ATC runs across B4/B1 produced no OM.

## v0.1.0 - 2026-10-17

- Introduced the portable `ascend_edge_chat` package and `ascend-edge-chat` CLI.
- Released one locked MindSpore profile: Qwen1.5-0.5B-Chat for Ascend 310B1 / 20T.
- Added verified Hugging Face mirror download and offline import with byte-size and SHA-256 checks.
- Added loopback-only worker and gateway, server-side Bearer authentication, and a trusted-LAN browser UI warning.
- Archived experimental model, audio, conversion, and device-protocol routes outside the release surface.

## Versioning Policy

The project follows semantic versioning. A model revision, hardware target, runtime tuple, or public API incompatibility needs a recorded validation update before it is advertised as a supported release capability.
