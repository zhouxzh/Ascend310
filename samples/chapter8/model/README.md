# Chapter 8 Model Directory

第 8 章使用的模型来自 Hugging Face 仓库 `zhouxzh/resnet18_tiny_imagenet`。进入 `samples/chapter8` 后，可以直接下载到本目录：

```bash
python tools/download_model.py
```

常用文件名：

- `resnet18_tiny_imagenet.onnx`
- `resnet18_tiny_imagenet_fp32.om`
- `resnet18_tiny_imagenet_fp16.om`
- `resnet18_tiny_imagenet_int8.om`
- `resnet18_tiny_imagenet_int8_deploy.onnx`

其中 FP32/FP16/INT8 OM 都由本章 ATC 命令从 ONNX 转换得到。
`resnet18_tiny_imagenet_int8_deploy.onnx` 是 AMCT 生成、交给 ATC 转换
INT8 OM 的中间模型。

当前脚本只直接下载原始 ONNX；OM 需要在 Ascend 310B 上按本章 README 或
`src/book/chapter8.md` 中的 ATC 命令生成。
