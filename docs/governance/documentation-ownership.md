# Documentation ownership

`AGENTS.md` remains the canonical repository policy. This page explains where each documentation type belongs.

## Ownership rules

| Documentation type | Canonical owner |
| --- | --- |
| Theory chapters and appendices | `src/book/` and `src/appendix/` |
| Practical case tutorials | `src/experiment/` |
| Presentation sources | `src/presentation/` |
| Runnable case code and tests | `samples/caseN/` |
| Case deployment, API, validation, reviews, and troubleshooting | `samples/caseN/docs/` |
| Plugin implementation and plugin-specific supporting docs | `tools/` |
| Repository governance and release policy | `docs/` |
| Generated site, slides, LaTeX, and PDF | generated output directories |

## Root docs boundaries

Root `docs/` must not become a second book or a general evidence dump. It must not contain:

- learner-facing lessons;
- sample-run commands;
- case-specific architecture or troubleshooting;
- raw terminal output, screenshots, or result tables;
- model files, datasets, or machine-readable run artifacts;
- copies of another directory's content.

Cross-project documents may link to the canonical owner. Case-specific audit results belong with the case; plugin-specific audit data belongs with the plugin.
