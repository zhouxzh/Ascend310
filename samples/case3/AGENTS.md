# AGENTS.md - case3 Instructions

## Scope

These instructions apply to the entire `case3` directory.

## Runtime Boundary

- The local computer is the development and model-export environment.
- TensorFlow checkpoint to ONNX export must be completed on the local computer.
- Ascend-only operations such as ATC conversion, OM inference, PyACL runtime
  checks, and `npu-smi` inspection must run on the target Ascend 310B board.

## Remote Board Safety

- Never install, upgrade, downgrade, remove, or initialize software on any
  remote Ascend 310B board.
- Do not run package-management commands on a remote board, including
  `pip install`, `conda install`, `apt install`, or their uninstall/upgrade
  equivalents.
- Use only software and conda environments that already exist on the board.
- If a required board-side dependency is missing, stop that step, preserve the
  diagnostic output, and report the dependency as a blocker. Do not work around
  it by modifying system or environment packages.
- Do not edit remote shell startup files or system configuration.

## Change Discipline

- Do not modify any files in the sibling `case2` directory.
- Do not revert unrelated user changes.
- Keep model checksums and raw conversion logs with each conversion result.

## Test Assets

- Files under `midi/` and `midi_wav/` are local test assets for MIDI parsing,
  DDSP rendering, WebUI catalog checks, and audio playback validation. Do not
  delete them during cleanup or code reduction.
- These MIDI, MuseScore, and WAV files are intentionally excluded from the
  GitHub package by the repository `.gitignore`. Keep them in the local
  workspace and board staging area, but do not add them with `git add -f` or
  upload them to GitHub.
- Regenerate only the deterministic fixture when needed with
  `python tools/create_test_midi.py --output midi/ddsp-test.mid`; preserve the
  rest of the local test library.
