# Chapter 8 Model Directory

Download the ResNet18-TinyImageNet model files from Hugging Face after entering `samples/chapter8`:

```bash
python tools/download_model.py
```

The downloader is pinned to repository `zhouxzh/resnet18_tiny_imagenet`, revision:

```text
dfcf5885dcf7beab3a5d6e2143c73d7973bead36
```

Verified SHA-256 values:

| File | SHA-256 |
| --- | --- |
| `resnet18_tiny_imagenet.om` | `0b9e112751181aab82b43963f1b975adf4abbcdeec7c919ee93e496c0df8d46d` |
| `resnet18_tiny_imagenet.onnx` | `e461aecee8bfafe95cd4528d3e836783c7546a0bb201c98e25109cde51b3bd6e` |
| `resnet18_tiny_imagenet_aipp.om` | `d1e864ae220533442c813029b58ab4fdc91adfe2a9406be8620d0c060b6a6307` |

The script verifies these hashes when files are downloaded or reused. `resnet18_rgb_static_aipp.cfg` is pinned by revision but is not an LFS model tensor, so it has no model-LFS SHA-256 entry.

Use `--all` to download the OM, ONNX, AIPP OM and AIPP config. Use `--aipp` for only the AIPP pair.
