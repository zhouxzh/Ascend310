# Model Artifacts

All runtime OM files are stored directly in one directory. They are not split
by board, precision, or test run:

```text
models/
|-- om/                     # 22 DDSP-VST OMs, 4 MIDI-DDSP OMs, and reverb IR
|-- conversion_logs/
|   |-- ddsp_vst/           # Ascend 8T ATC logs, summaries, and source hashes
|   `-- midi_ddsp/          # Ascend 8T ATC logs and summaries
|-- ddsp_vst/               # DDSP-VST TFLite, ONNX, and metadata
|-- midi_ddsp/              # MIDI-DDSP weights, ONNX, and references
`-- manifests/
    `-- SHA256SUMS.txt       # Runtime OM hashes relative to models/
```

The canonical DDSP-VST OMs were generated on `ascend8t` for Ascend310B4 with
CANN 8.3.RC1. The same OMs were verified on the 20T board, so no second copy is
kept for that board. Historical compatibility and failed-conversion evidence
is retained under `reports/`, not in the runtime model directory.

Both FP16 and `mixed_float16` files remain because benchmark and comparison
tasks use both precision modes. The original ONNX, TFLite, weights, and
reference files are not duplicated in `models/om/`.

`models/om/midi_ddsp_reverb_ir.npz` contains the 20 checkpoint-derived,
48,000-sample MIDI-DDSP impulse responses. Its SHA256 is
`ecbc733bc9a17516dc00897e64eaae70114aa79ed97e2bbc59dedb334f356058`;
the Web service rejects playback if this runtime asset changes.

On Linux, verify all runtime models with:

```bash
sha256sum -c manifests/SHA256SUMS.txt
```
