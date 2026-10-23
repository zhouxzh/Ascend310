# Weights

This directory is for model-preparation assets.

The `.pt` weights are not part of the Ascend 310B runtime code. They must be
published in a Hugging Face model repository with a fixed revision and verified
by the board downloader before they can be used as release evidence. The local
export command is for development only:

```bash
python weights/export_yolo_to_onnx.py \
  --weights weights/YOLOv10n_gestures.pt \
  --output-dir models
```

Before publishing this case, record the HF repository, revision, file names,
byte sizes, SHA-256 values, and license here. A local copy of `.pt`, ONNX, or OM
is not a substitute for a public model source.

