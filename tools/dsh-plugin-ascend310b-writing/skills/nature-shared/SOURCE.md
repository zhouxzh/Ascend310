# Shared support corpus

This directory is vendored from:

- Repository: <https://github.com/Yuan1z0825/nature-skills>
- Upstream path: `skills/nature-shared`
- Commit: `233ed09`
- Retrieved: 2026-10-20
- License: Apache-2.0; see [LICENSE](LICENSE)

Only the files needed by `nature-polishing` are included (`core/`, `journal-formats/`, `scripts/`, and `manifest.yaml`). The upstream `SKILL.md` and standalone metadata are intentionally omitted so DSH does not list this dependency as a third skill.

Local change: the optional `nature-response` cross-reference was rewritten as optional prose because that separate skill is not bundled here.

`nature-polishing` resolves these files through its upstream-relative `../nature-shared/` paths.
