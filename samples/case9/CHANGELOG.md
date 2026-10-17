# Changelog

All notable changes to Ascend Edge Chat are recorded here.

## v0.1.0 - 2026-10-17

- Introduced the portable `ascend_edge_chat` package and `ascend-edge-chat` CLI.
- Released one locked MindSpore profile: Qwen1.5-0.5B-Chat for Ascend 310B1 / 20T.
- Added verified Hugging Face mirror download and offline import with byte-size and SHA-256 checks.
- Added loopback-only worker and gateway, server-side Bearer authentication, and a trusted-LAN browser UI warning.
- Archived experimental model, audio, conversion, and device-protocol routes outside the release surface.

## Versioning Policy

The project follows semantic versioning. A model revision, hardware target, runtime tuple, or public API incompatibility needs a recorded validation update before it is advertised as a supported release capability.
