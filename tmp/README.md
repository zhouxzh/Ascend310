# Temporary workspace

`tmp/` is the repository's only temporary working directory. It exists so build output, downloads, test runs, and packaging stages remain visible to the repository owner instead of being hidden in an external or system temporary directory.

## Layout

Use a descriptive subdirectory for each task:

```text
tmp/
├── presentation-build/   # generated Marp staging
├── presentation-output/  # short-lived Marp HTML before publishing
├── downloads/            # downloaded models or packages
├── test-runs/            # local test output
└── package-staging/      # release-package staging
```

Do not put files directly in `tmp/`.

## Rules

- Only this `README.md` is tracked; all other contents are ignored by Git.
- Do not create `.tmp/`, `scratch/`, or another root-level temporary directory.
- Do not store durable models, datasets, credentials, personal data, or completed evidence packages here.
- Remove stale task directories when they are no longer useful.
- Git worktrees are not temporary data. Keep them outside this checkout at `D:\Github\Ascend310-worktrees\<branch-slug>\`.
