# Repository content scripts

This directory contains scripts that build and verify repository content. DSH plugin installation and skill synchronization live under [`tools/`](../tools/), not here.

## Presentation and site

| Script | Purpose |
| --- | --- |
| `build_presentation.mjs` | Build Marp sources into standalone presentation HTML |
| `check_presentation_catalog.mjs` | Validate presentation coverage and metadata |
| `check_presentation_render.mjs` | Check every rendered slide for clipping and missing images |
| `capture_presentation_ui.mjs` | Capture presentation UI screenshots |
| `register_presentation_screenshot.mjs` | Register screenshot metadata and hashes |
| `scaffold-presentations.mjs` | Create new presentation sources from the repository template |
| `split-decks.mjs` | Split overfull Marp slides |
| `sync_presentation_readme.mjs` | Regenerate the presentation index in `src/presentation/README.md` |
| `sync_presentation_sidebar.mjs` | Regenerate the presentation sidebar block |
| `check_page_layout.mjs` | Check VuePress pages at desktop and mobile widths |

## Evidence handling

| Script | Purpose |
| --- | --- |
| `redact_historical_ui.py` | Create privacy-safe derivatives of historical UI screenshots |

Plugin and agent infrastructure belongs in [`tools/`](../tools/) instead.
