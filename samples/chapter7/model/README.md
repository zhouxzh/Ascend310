# Chapter 7 Model Directory

Download the ResNet18-TinyImageNet model files from Hugging Face after entering `samples/chapter7`:

```bash
python3 tools/download_model.py
```

The default command downloads `resnet18_tiny_imagenet.om` from `zhouxzh/resnet18_tiny_imagenet`.
Use `--all` to download:

- `resnet18_tiny_imagenet.om`
- `resnet18_tiny_imagenet.onnx`
- `resnet18_tiny_imagenet_aipp.om`
- `resnet18_rgb_static_aipp.cfg`

Use `--aipp` if you only need the AIPP OM and AIPP config.
