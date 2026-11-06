# Repository Documentation

This directory contains maintainer-facing repository governance, release policy, environment compatibility, and cross-project audit summaries. It is not part of the learner-facing tutorial and is not included in the VuePress site.

## Canonical content locations

| Content | Location |
| --- | --- |
| Theory, case tutorials, and appendices | [`src/`](../src/) |
| Runnable code and case-specific engineering docs | [`samples/`](../samples/) |
| Plugin source, plugin docs, and audit data | [`tools/`](../tools/) |
| Generated LaTeX and PDF | [`latex/`](../latex/) |

Root `docs/` may link to those locations, but it does not duplicate their content, commands, logs, screenshots, or model artifacts.

## Documentation map

| Document | Purpose |
| --- | --- |
| [`governance/repository-structure.md`](governance/repository-structure.md) | Repository layout and ownership boundaries |
| [`governance/documentation-ownership.md`](governance/documentation-ownership.md) | Rules for choosing the canonical documentation location |
| [`release/release-checklist.md`](release/release-checklist.md) | Publication and verification gates |
| [`release/environment-matrix.md`](release/environment-matrix.md) | Supported board, CANN, driver, Python, and case status |
| [`audits/20260907-documentation.md`](audits/20260907-documentation.md) | Historical documentation audit |
| [`audits/20260907-case-reviews.md`](audits/20260907-case-reviews.md) | Cross-case review index; reports live with their cases |
| [`audits/20261023-case-images.md`](audits/20261023-case-images.md) | Case image-size and scaling audit |

## Maintenance rules

- Keep living governance and release documents at stable paths.
- Put only dated cross-project audit summaries in `audits/`; do not add raw evidence.
- Put a case review in the corresponding `samples/caseN/docs/` directory.
- Put plugin details and machine-readable audit baselines under `tools/`.
- Keep raw logs, screenshots, datasets, models, and temporary outputs outside root `docs/`.
