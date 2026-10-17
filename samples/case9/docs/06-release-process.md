# Release Process

## Version Policy

Ascend Edge Chat uses semantic versioning. A patch release fixes implementation defects without changing the public contract or locked model identity. A minor release may add a validated capability. A major release may change public API behavior, security boundaries, or artifact compatibility.

## Release Checklist

### Source and Artifact Review

- Confirm the package version and `CHANGELOG.md` entry.
- Confirm only `ASCEND_EDGE_CHAT_` environment variables appear in current code and documents.
- Confirm `configs/release-model.json` contains one pinned profile.
- Verify the release lock, artifact sizes, SHA-256 values, target SoC, and runtime fingerprint.
- Confirm model weights, caches, reports, and logs remain ignored by Git.
- Confirm historical experiments remain under `docs/archive/` and are not advertised by the UI or README.

### Local Verification

```bash
python -m compileall -q ascend_edge_chat
python -m pytest -q tests
python -m ascend_edge_chat --version
python -m ascend_edge_chat verify --data-dir <verified-model-directory>
git diff --check
```

Run a Markdown-link check and inspect rendered Mermaid diagrams. Build the repository documentation with `pnpm run docs:build` after updating the textbook source.

### Board Verification

On the target 20T board, record the exact date, host identity, SoC, CANN version, driver/firmware data, Conda environment, Python executable, MindSpore, MindNLP, artifact hashes, and `npu-smi` snapshots. Complete the gates in [Validation and Limitations](04-validation-and-limitations.md) without CPU or cloud fallback. The release acceptance client uses two warmups and 30 SSE measurements with a fixed two-token greedy request; it records first-token and total-latency p50/p95 plus token/s p50/p95 from the terminal usage event.

Keep raw logs and measurements out of Git. Commit only a concise, evidence-linked validation record with exact protocol parameters and paths to retained board evidence.

### Human Review

Start a local documentation preview:

```bash
pnpm run docs:dev
```

Review the rendered deployment instructions, UI security warning, limitation statements, and case tutorial. A successful static build does not replace this review. Do not create a tag, push a release branch, or publish a GitHub release until the reviewer confirms the local preview.

## Rollback Principles

Release rollback stops only identified release PIDs and restores the prior verified deployment from its own directory. Never overwrite CANN, driver, firmware, OPP, a shared Conda environment, another model directory, or an unknown NPU process to make a release pass.
