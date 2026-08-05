# Case3 Report Artifacts

`reports/` stores generated evidence from local and Ascend 310B validation:
ATC logs, model hashes, precision reports, WebUI screenshots, rendered WAVs,
playback metrics, and library index snapshots.

The directory remains ignored because these files can be large, device-specific,
or contain local MIDI-derived material. Existing evidence must be preserved.
Do not clean this directory as part of model-download, frontend deployment, or
source-tree simplification work.

New output belongs under a stable subject directory, for example
`reports/webui/`, `reports/midi_ddsp/`, `reports/piano-ddsp/`, or a board name
such as `reports/ascend8t/`. Each report should retain the model/release
revision, SHA256 values, command or configuration, timestamp, and relevant
raw conversion or runtime logs. SQLite under `reports/webui/` is rebuildable
from filesystem task metadata and artifacts; it is not the only source of data.

The detailed WebUI test procedure, acceptance thresholds, and the 2026-08-04
Ascend 310B results are documented in
[`doc/webui-acceptance.md`](../doc/webui-acceptance.md). The JSON files and
screenshots under `reports/webui/` are the raw evidence for that narrative.
