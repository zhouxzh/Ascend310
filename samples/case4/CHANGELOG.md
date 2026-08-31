# Changelog

All notable changes to the palmprint workbench are recorded here. This file
describes source releases; board-side model and benchmark assets remain
separate and are identified by `release_manifest.json`.

## Unreleased - manual deployment migration

- Completed the working-tree migration of production imports from the
  historical flat modules into `palmprint_workbench`; the release allowlist
  keeps only `app.py` as a thin process entry point and puts export, board
  diagnostics, and offline baselines into `tools/` namespaces.
- Defining a source package and a separate board asset package. OM binaries are
  not source-package contents; the planned public asset repository is
  `zhouxzh/ascend310-palmprint`, with a pinned commit and `om_manifest.json`
  required before any download is treated as reproducible.
- Documenting manual conda/CANN activation, dependency installation, explicit
  SSH/rsync staging, service launch, verification, and rollback. No shell
  installation, launch, deployment, or extraction wrapper is part of the
  release workflow.
- Keeping the production API NPU-only with `mixed_fp16` precision. CCNet
  remains the default and rollback model; `PALMPRINT_PROFILE=manual_test`
  exposes the five CompNet variants with `manual_test_pending=true` for the
  frozen human-validation phase.
- Public documentation now contains generic device/runtime troubleshooting
  only. Detailed board incident logs remain outside the source release.
- Retaining the distinction between conversion, numerical consistency,
  recognition metrics, performance, UI smoke, and hardware diagnostics.

The `1.0.0` release tag is not valid until the clean-clone, local, board, asset
hash, license, and rollback gates in `docs/04-release-checklist.md` are all
recorded in a signed `release_manifest.json`.

## 1.0.0 - planned manual deployment profile

Reserved for the first verified source/asset release. Do not use this heading
for a build whose manifest still has `template`, `draft`, or null hash fields.

## 0.1.0

- Initial React/FastAPI workbench source and candidate audit records.
