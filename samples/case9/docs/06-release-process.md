# Release Process

## Version Policy

Ascend Edge Chat uses semantic versioning. A patch release fixes implementation defects without changing the public contract or locked model identity. A minor release may add a validated capability. A major release may change public API behavior, security boundaries, or artifact compatibility.

## Release Checklist

### Source and Artifact Review

- Confirm the package version and `CHANGELOG.md` entry.
- Confirm only `ASCEND_EDGE_CHAT_` environment variables appear in current code and documents.
- Confirm the 20T `configs/release-model.json` contains one pinned profile.
- Confirm the 8T `configs/release-8t-static-kv.lock.json` contains the nine pinned public artifacts, base-model revision, target SoC, and Apache-2.0 license.
- Verify the target-specific release lock, artifact sizes, SHA-256 values, and runtime fingerprint.
- Confirm model weights, caches, reports, and logs remain ignored by Git.
- Confirm historical experiments remain under `docs/archive/` and are not advertised by the UI or README.

### Local Verification

```bash
python -m compileall -q ascend_edge_chat
python -m pytest -q tests
python -m ascend_edge_chat --version
python -m ascend_edge_chat verify --data-dir <verified-20t-model-directory>
python -m ascend_edge_chat acl verify --data-dir <verified-8t-data-directory>
git diff --check
```

Run a Markdown-link check and inspect rendered Mermaid diagrams. Build the repository documentation with `pnpm run docs:build` after updating the textbook source.

### Board Verification

Run each target independently. The acceptance client requires the current public `main` commit, a clean source tree, fixed model revision, verified artifact bytes/SHA-256, and an explicit acquisition label. It writes those provenance facts with the report. Chinese semantic quality remains separate from the machine gate.

- 20T: run `scripts/verify/run-release-acceptance.sh`; record MindSpore/MindNLP, target HBM, model hashes, gateway/UI JSON and SSE, length checks, stability, and performance.
- 8T: run `scripts/verify/run-release-8t-acceptance.sh`; record the nine public HF artifacts, derived schema-2 locks, 51/49 descriptor, ACL health, gateway/UI JSON and SSE, fixed-model switch rejection, length checks, stability, and performance.

The tutorial UI has no authentication, so the validation LAN must be trusted: reachable clients can submit chat and, when voice mode is enabled, read sanitized voice status or request allowlisted board-audio controls.

Keep raw logs and measurements out of Git. Commit only a concise, evidence-linked validation record with exact protocol parameters and paths to retained board evidence.

### Human Review

Start a local documentation preview:

```bash
pnpm run docs:dev
```

Review the rendered deployment instructions, UI security warning, limitation statements, and case tutorial. A successful static build does not replace this review. Do not create a tag, push a release branch, or publish a GitHub release until the reviewer confirms the local preview.

## Rollback Principles

Release rollback stops only identified release PIDs and restores the prior verified deployment from its own directory. Never overwrite CANN, driver, firmware, OPP, a shared Conda environment, another model directory, or an unknown NPU process to make a release pass.
