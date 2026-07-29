# Model Artifacts

All runtime OM files are stored directly in one directory. They are not split
by board, precision, or test run:

```text
models/
|-- om/                     # 22 DDSP-VST OMs and the MIDI-DDSP reverb IR
|-- conversion_logs/
|   |-- ddsp_vst/           # Ascend 8T ATC logs, summaries, and source hashes
|-- ddsp_vst/               # DDSP-VST TFLite, ONNX, and metadata
|-- midi_ddsp/              # MIDI-DDSP weights, ONNX, and references
|-- piano_ddsp/
|   |-- model-suite-v1.0.0/ # Pinned FP32 ONNX release, metadata, licenses
|   |-- references/         # Deterministic 10,000-frame NPZ/WAV fixtures
|   |-- bundles/            # Immutable Ascend310B4 FP32 OM bundles
|   `-- active-bundle.json  # Atomic rollback pointer
`-- manifests/
    `-- SHA256SUMS.txt       # Runtime OM hashes relative to models/
```

The canonical DDSP-VST OMs were generated on `ascend8t` for Ascend310B4 with
CANN 8.3.RC1. The same OMs were verified on the 20T board, so no second copy is
kept for that board. Historical compatibility and failed-conversion evidence
is retained under `reports/`, not in the runtime model directory.

Both DDSP-VST FP16 and `mixed_float16` files remain because DDSP-VST benchmark
and comparison tasks use both precision modes. MIDI-DDSP runtime components are
stored in origin stateful bundles under `models/midi_ddsp/bundles/`. The original
ONNX, TFLite, weights, and reference files are not duplicated in `models/om/`.

`models/om/midi_ddsp_reverb_ir.npz` contains the 20 checkpoint-derived,
48,000-sample MIDI-DDSP impulse responses. Its SHA256 is
`ecbc733bc9a17516dc00897e64eaae70114aa79ed97e2bbc59dedb334f356058`;
the Web service rejects playback if this runtime asset changes.

Piano-DDSP artifacts are separate from both legacy layouts. The downloader pins
Hugging Face commit `2199df0a55953a0d2469d59ab2f23a8bef8eb314` and excludes
checkpoints and `.pt` files. Each OM bundle preserves source/OM hashes, raw ATC
logs, the exact command, environment evidence, and the PyACL-validated I/O
contract. Completed bundles are immutable; rollback atomically changes only
`models/piano_ddsp/active-bundle.json`.

Piano-DDSP FP32 bundles must record `precision_mode_v2=origin`. A model is not
runtime-eligible until its hashed validation report passes at least 10,000
continuous frames. The CANN 8.0.0 native `DynamicGRUV2` path only accepts FP16,
so the FP32 baseline uses the separately verified `gru-unrolled` ONNX variant;
failed native-conversion logs remain as provenance rather than being relabeled.

On Linux, verify all runtime models with:

```bash
sha256sum -c manifests/SHA256SUMS.txt
```
