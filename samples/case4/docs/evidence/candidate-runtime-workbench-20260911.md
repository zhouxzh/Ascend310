# Candidate Runtime Workbench

## Scope

The workbench now exposes a separate candidate experiment channel for the OM
assets that have a verified Ascend 310B4 contract. This channel is for manual
research testing only. It does not modify `models/registry.json`, does not add
models to `/api/bootstrap`, and does not create template namespaces.

The current runtime manifest is `candidate_runtime_manifest.json`. It contains
six board-ready candidates:

- Holzweber ResNet18 Tongji classifier
- Holzweber MobileNetV2 Tongji classifier
- Holzweber ROI-LANet regressor
- Lin-Dxin paired ResNet18 comparator
- PPNet Tongji feature extractor
- Kenan palmar-vein CNN classifier

Each entry is checked by byte count and SHA-256 before loading. The adapter
uses the declared input contract and returns a task-specific output summary:
logits, ROI parameters, feature dimensions/cosine similarity, or vein logits.
No candidate output is treated as a production 512-dimensional palmprint
embedding unless a separate model-specific validation approves that use.

## HTTP surface

`GET /api/candidate-runtimes` returns the candidate contracts and whether the
local OM is available. `POST /api/candidate-runs` accepts multipart fields
`candidate_id`, `image`, and, for the paired model, `reference_image`.

Example manual request on the board:

```bash
curl --fail -F candidate_id=ppnet -F image=@/path/to/anonymous-roi.png \
  http://127.0.0.1:7860/api/candidate-runs
```

For `lin_dxin_resnet18_pair`, add a second `reference_image` field. The
response records the candidate ID, backend (`npu`), precision (`mixed_fp16`),
OM hash, actual output shapes, finite-output check, and measured inference
time. Candidate requests are serialized with the workbench execution lock.

The front end exposes this route under the model-status page as “候选模型实验”.
It intentionally keeps these experiments separate from live recognition,
registration, template lookup, and deletion. Production remains CCNet by
default, with the existing CompNet models available only in their existing
manual-test path.

Because this is an internal testing release, the uploaded primary image is
also archived by the existing `CaptureStore` under `data/captures` and the
response includes its `capture_id`. Continuous camera preview frames and the
optional paired reference image are not archived by this route.

## Evidence boundary

An available OM and a successful one-image ACL run are conversion and runtime
smoke evidence only. They are not recognition accuracy, dataset ranking,
latency qualification, or production admission. Those gates remain in the
candidate campaign reports and must be completed independently for each task.

The six OM files are ignored runtime assets. Do not commit candidate images,
templates, logs, or reports; keep them on the board under the release data and
reports directories.
