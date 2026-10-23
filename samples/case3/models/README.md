# Model Artifacts

All runtime OM files are stored directly in one directory. They are not split
by board, precision, or test run:

```text
models/
|-- om/                     # 22 Control OMs, one Feature OM, and the MIDI-DDSP reverb IR
|-- conversion_logs/
|   |-- ddsp_vst/           # Ascend 8T ATC logs, summaries, and source hashes
|-- ddsp_vst/               # Published DDSP-VST ONNX/OM metadata
|-- midi_ddsp/              # Published MIDI-DDSP ONNX/OM bundles and references
|-- piano_ddsp/
|   |-- model-suite-v1.0.1/ # Pinned FP32 ONNX release, metadata, licenses
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

The DDSP-VST Effect Feature runtime is
`om/ddsp_vst_feature_mixed_float16.om`, SHA256
`a1973830eca98111642dcb331e0a1a163f7a664d871e6d15f40fdc70f9b98db4`.
It was generated separately on `ascend8t` with CANN 8.0.0 for Ascend310B4.
The 1,000-frame board comparison passed with Feature p95 `10.207 ms`, combined
Feature+Control p95 `11.321 ms`, and maximum `f0_hz` error `0.141 Hz`.
The `force_fp16` candidate was rejected for large power-feature errors; only the
verified `mixed_float16` OM is listed in the runtime manifest.

Both DDSP-VST FP16 and `mixed_float16` files remain because DDSP-VST benchmark
and comparison tasks use both precision modes. MIDI-DDSP runtime components are
stored in origin stateful bundles under `models/midi_ddsp/bundles/`. The original
ONNX, TFLite, weights, and reference files are not duplicated in `models/om/`.

`models/om/midi_ddsp_reverb_ir.npz` contains the 20 checkpoint-derived,
48,000-sample MIDI-DDSP impulse responses. Its SHA256 is
`ecbc733bc9a17516dc00897e64eaae70114aa79ed97e2bbc59dedb334f356058`;
the Web service rejects playback if this runtime asset changes.

Published release assets are downloaded from `zhouxzh/piano-ddsp-ascend310` with
`tools/download_model_release.py`. The downloader requires a fixed revision,
downloads `SHA256SUMS` first, resumes partial files, and records the resolved
commit. It does not create or delete TFLite files, old ONNX files, checkpoints,
or upstream material. Piano-DDSP pins source commit
`c41911aa7de454aeacf0b3edbb2d06a0801fb3ff` directly. Each OM bundle preserves
source/OM hashes, raw ATC logs, the exact command, environment evidence, and the
PyACL-validated I/O contract. Completed bundles are immutable; rollback
atomically changes only `models/piano_ddsp/active-bundle.json`.

The Hugging Face repository intentionally keeps only one moving branch (`main`)
and one immutable release tag (`case3-runtime-v1.0.1`). The historical
`model-suite-v1.0.0` and `model-suite-v1.0.1` Git tags were retired from the
revision selector. `model-suite-v1.0.1` remains the internal model-release
identifier and source commit pin, not a second branch or active HF tag.

The complete Ascend 310B runtime overlay is published as:

```text
repository: zhouxzh/piano-ddsp-ascend310
tag: case3-runtime-v1.0.1
commit: bd04b503920860896fbbf504993046ea099b45f8
prefix: ascend310b4-runtime-v1.0.1/
files: 166
SHA256SUMS: e178f3db214437e45a00660f509b4b19611b3a1d44cb554f37c732292a347660
```

It contains the Piano-DDSP FP32 bundle, MIDI-DDSP stateful bundle, DDSP-VST
Feature and 22 Control OMs, metadata, active-bundle pointer, ATC provenance,
validation evidence, licenses, and runtime checksum manifest. Source ONNX,
TFLite, training checkpoints, datasets, MIDI inputs, and rendered WAV files are
not duplicated in this runtime overlay.

```bash
hf download zhouxzh/piano-ddsp-ascend310 \
  --repo-type model \
  --revision case3-runtime-v1.0.1 \
  --include "ascend310b4-runtime-v1.0.1/*" \
  --local-dir artifacts/case3-runtime

cd artifacts/case3-runtime/ascend310b4-runtime-v1.0.1
sha256sum -c SHA256SUMS
```

A public cold-download verification from `huggingface.co` passed all 166 checks
in GitHub Actions run `39926902169`. The earlier `case3-runtime-v1.0.0` staging
tag was invalidated and deleted before release.

The DDSP-VST Effect release is included in the fixed runtime overlay above,
with its license, source/ONNX/OM hashes, ATC log, and validation reports. Its
standalone source remains staged locally under
`models/ddsp_vst_effect/release-v1.0.0/`; consumers should use the immutable
runtime tag rather than a moving branch.

Piano-DDSP FP32 bundles must record `precision_mode_v2=origin`. A model is not
runtime-eligible until its hashed validation report passes at least 10,000
continuous frames. The CANN 8.0.0 native `DynamicGRUV2` path only accepts FP16,
so the FP32 baseline uses the separately verified `gru-unrolled` ONNX variant;
failed native-conversion logs remain as provenance rather than being relabeled.

On Linux, verify all runtime models with:

```bash
sha256sum -c manifests/SHA256SUMS.txt
```
