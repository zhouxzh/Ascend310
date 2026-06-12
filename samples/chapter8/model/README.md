# Chapter 8 Model Directory

第 8 章使用的模型来自 Hugging Face 仓库 `zhouxzh/resnet18_tiny_imagenet`。进入 `samples/chapter8` 后，可以直接下载到本目录：

```bash
python3 tools/download_model.py
```

常用文件名：

- `resnet18_tiny_imagenet.onnx`
- `resnet18_tiny_imagenet.om`
- `resnet18_tiny_imagenet_fp16.om`
- `resnet18_tiny_imagenet_int8.om`
- `resnet18_tiny_imagenet_int8_deploy.onnx`

其中 FP16/INT8 模型由本章 `tools/` 下的转换脚本生成或由课程资料提前提供。`resnet18_tiny_imagenet_int8_deploy.onnx` 是 AMCT 生成、交给 ATC 转换 INT8 OM 的中间模型。

当前 Hugging Face 仓库已经提供基线 OM、ONNX 和 AIPP 相关文件；FP16/INT8 OM 如果还没有上传，请在 Ascend 310B 上通过本章转换脚本生成。
