# Source and attribution

- Upstream repository: <https://github.com/Yuan1z0825/nature-skills>
- Upstream path: `skills/nature-polishing`
- Commit: `233ed09`
- Retrieved: 2026-10-20
- License: Apache-2.0; see [LICENSE](LICENSE)
- Local changes:
  - corrected four skill-local relative references so they resolve inside the vendored directory;
  - kept the upstream `../nature-shared` layout by vendoring the shared corpus beside this skill;
  - no polishing rules, journal guidance, or examples were rewritten.

This skill keeps its upstream `../nature-shared` references. The shared corpus is vendored beside it as a support directory and is not exposed as a third standalone skill.
