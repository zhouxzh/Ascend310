# Weights

This directory is for model-preparation assets.

The `.pt` weights are not part of the Ascend 310B runtime code. Export them to
ONNX on a PC or GPU workstation with:

```bash
python weights/export_yolo_to_onnx.py \
  --weights weights/YOLOv10n_gestures.pt \
  --output-dir models
```

This folder can later move to a separate model repository, for example on
Hugging Face.

