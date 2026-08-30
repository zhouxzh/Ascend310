# XiaoZhi Phase 2 Board Scaffold (Deferred)

## Scope

This document defines a paused deployment scaffold for a future real XiaoZhi
device test. It does not claim an ASR-to-LLM-to-TTS device loop, OTA
registration, or WebSocket session has been tested. The device is currently
unavailable, and the operator has prohibited the upstream Torch/Torchaudio
dependency path on this Ascend 310B4.

The local browser chat service and the XiaoZhi service are separate processes.
They must not run concurrent board-microphone or board-speaker tests.

| Component | Board-local location or endpoint |
| --- | --- |
| XiaoZhi source | `$HOME/case9-xiaozhi/xiaozhi-esp32-server` |
| XiaoZhi conda env | `case9-xiaozhi` with Python 3.10 |
| Case9 gateway | `http://127.0.0.1:7861/v1` |
| Device WebSocket | `ws://192.168.8.178:8000/xiaozhi/v1/` |
| OTA endpoint | `http://192.168.8.178:8003/xiaozhi/ota/` |

## Pinned Source And Configuration

The server source is
`xinnan-tech/xiaozhi-esp32-server` at
`e1876f1ce19cad6e7bfd7c80e41dc56b2e858dd5`. The board provisioning script
checks out that exact detached commit and rejects a dirty source tree rather
than overwriting it.

If the board cannot clone from GitHub, use the explicit archive fallback
instead of retrying an unpinned download. On a trusted controller, download
the GitHub archive for the exact revision, record its SHA-256 there, and copy
that one archive to the board. The board command requires both the archive
path and the controller-recorded checksum; it verifies the checksum and the
expected GitHub top-level directory before extracting. It refuses to replace
an existing source directory or an existing archive provenance record.

```bash
# The archive and this SHA-256 must already have been transferred and recorded.
export CASE9_DIR="${CASE9_DIR:-$HOME/case9-review-20260822}"
if [[ -d "$CASE9_DIR/src/scripts" ]]; then
  export CASE9_SOURCE_DIR="${CASE9_SOURCE_DIR:-$CASE9_DIR/src}"
  export CASE9_TINYLLAMA_HOME="${CASE9_TINYLLAMA_HOME:-$CASE9_DIR}"
else
  export CASE9_SOURCE_DIR="${CASE9_SOURCE_DIR:-$CASE9_DIR}"
fi
cd "$CASE9_SOURCE_DIR"
bash scripts/provision_xiaozhi_board.sh \
  --archive-source "$HOME/xiaozhi-esp32-server-e1876f1ce19cad6e7bfd7c80e41dc56b2e858dd5.tar.gz" \
  --archive-sha256 '<controller-recorded-sha256>' \
  --create-env
```

Do not combine `--archive-source` with `--clone-source`. The archive route
writes a board-local, mode-`0600` provenance record under
`$HOME/case9-xiaozhi`; later `--check` accepts either that validated archive
record or the detached Git revision. It does not generate a gateway token.

The checked-in partial configuration template is
`configs/xiaozhi-case9-override.template.yaml`. It selects the OpenAI provider
below, disables intent/function calling, and keeps no key in Git:

```yaml
selected_module:
  LLM: Case9RagLLM
  Intent: nointent

LLM:
  Case9RagLLM:
    type: openai
    base_url: http://127.0.0.1:7861/v1
    model_name: case9-rag
    api_key: <board-local internal gateway token>
```

`scripts/provision_xiaozhi_board.sh --render-config` obtains the token only
from `CASE9_GATEWAY_API_KEY`, writes the rendered file to
`data/.config.yaml` with mode `0600`, and never prints it. The rendered file,
server source, Python environment, upstream models, audio, logs, and reports
stay on the board and are not repository artifacts.

The renderer refuses to overwrite an existing XiaoZhi configuration. Review
and back up that board-local file first; only then set
`CASE9_ALLOW_CONFIG_OVERWRITE=1` for an intentional replacement.

## Board Provisioning

Run on the board as `HwHiAiUser`; do not use the base environment for the
server and do not add conda initialization to a shell startup file:

```bash
export CASE9_DIR="${CASE9_DIR:-$HOME/case9-review-20260822}"
if [[ -d "$CASE9_DIR/src/scripts" ]]; then
  export CASE9_SOURCE_DIR="${CASE9_SOURCE_DIR:-$CASE9_DIR/src}"
  export CASE9_TINYLLAMA_HOME="${CASE9_TINYLLAMA_HOME:-$CASE9_DIR}"
else
  export CASE9_SOURCE_DIR="${CASE9_SOURCE_DIR:-$CASE9_DIR}"
fi
cd "$CASE9_SOURCE_DIR"
bash scripts/provision_xiaozhi_board.sh --clone-source --create-env
```

`--install-dependencies` deliberately exits without changing the board. The
fixed upstream `requirements.txt` pins `torch==2.2.2` and `torchaudio==2.2.2`
for its default FunASR/Silero paths, and that path is prohibited for this case.
No reviewed no-Torch dependency profile exists yet, so do not render, start,
or accept the XiaoZhi server from this scaffold. A future phase must select and
review a no-Torch ASR, VAD, and TTS integration before the install command is
reintroduced.

Do not start the server from this scaffold. Do not start the local browser chat
service concurrently with a future XiaoZhi device test that uses the same board
audio hardware.

## Current Acceptance Boundary

Before a real device is available and a no-Torch profile is approved, retain
only these pieces of evidence:

1. The detached source revision and an isolated Python 3.10 environment may be
   created, but are not an accepted XiaoZhi installation.
2. Do not install the upstream requirements, render a service configuration,
   start the XiaoZhi server, or contact its OTA/WebSocket ports.
3. A later approved no-Torch profile must independently demonstrate the Case9
   gateway request and an unauthenticated protocol-shape simulator before any
   real-device test. Neither check is a device voice-loop result.

When the ESP32 device becomes available, the final acceptance is a separate
real-device ASR -> Case9 gateway -> local LLM -> XiaoZhi TTS run. Function
calling and MCP remain disabled for that first run.
