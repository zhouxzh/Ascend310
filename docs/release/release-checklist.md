# Release checklist

Use this checklist for a tutorial or website release. Detailed case procedures remain in `samples/caseN/docs/`; this page records only the cross-project release gates.

## Content ownership

- `src/` contains the learner-facing manuscript and presentation sources.
- `samples/` contains runnable code and case-specific engineering documentation.
- `tools/` contains plugin source, plugin documentation, and audit data.
- Root `docs/` contains only governance, release policy, compatibility status, and dated cross-project audits.

## Required checks

```bash
pnpm skills:check
pnpm plugins:check
pnpm docs:slides:check
pnpm docs:slides
pnpm docs:slides:render-check
pnpm docs:build
git diff --check
```

For LaTeX/PDF changes, also rebuild the generated book and inspect the render log.

## Review gates

- Author preview: the site, navigation, target pages, and mobile width were reviewed by the user.
- Link integrity: Markdown and generated-site references resolve.
- Asset integrity: model files, datasets, logs, and screenshots remain outside Git unless explicitly retained as small fixtures.
- Evidence status: `observed-pass`, `observed-fail`, `blocked`, and `untested` are not presented as interchangeable.
- Board scope: 8T and 20T results remain separate.
- Publication: commit and push only after the required checks and user preview approval.

## Rollback

Record the commit before release. Revert the release commit rather than editing generated `latex/` or `dist/` output by hand.
