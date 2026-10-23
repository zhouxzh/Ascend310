# 模型转换与 NPU 后端

## 模型文件

```text
models/resnet18_scene.pth     PyTorch state_dict
models/resnet18_scene.onnx    ONNX 模型
models/resnet18_scene.om      Ascend 离线模型
```

路径由 `config.py` 生成，均位于 `samples/case6/models/`。模型和生成物默认不提交到 Git。

## prepare_models.py

```bash
# 只导出 PTH/ONNX，不要求板端 ATC
python3 prepare_models.py --onnx-only

# 完整流程；在配置好 CANN/ATC 的板端执行
python3 prepare_models.py

# 覆盖已有模型
python3 prepare_models.py --force
```

脚本行为：

- 无 PTH 时创建 torchvision ResNet18，替换成 5 类 head 并保存；
- 导出输入 `input:1,3,224,224`，输出 `output`，opset 11；
- 加载 ONNX 后执行 `onnx.checker.check_model`；
- 非 `--onnx-only` 时调用 `atc --framework=5`；
- `atc` 不存在时只提示 ONNX 已准备好，不声称 OM 已生成。

### 重要限制

`save_pretrained_resnet18()` 保存的是 ImageNet backbone + **随机初始化 5 类 head**。在缺少训练权重时，CPU 页面仍能返回分类，但预测没有质量意义。运行日志会打印 warning，文档和验收不得把随机分类器写成已训练模型。

`get_soc_version()` 从 `npu-smi` 文本中寻找 `310B` 或 `910`，找不到时回退为 `Ascend310B4`。因此：

- 不要把默认字符串当成真实探测结果；
- 在 310B1 / 20T 上必须显式确认 SoC；
- 不要把 `--onnx-only` 的结果当作 ATC 或 NPU 通过。

## NPU 运行

`SceneClassifier._init_backend()` 只有在以下条件同时满足时使用 NPU：

1. `models/resnet18_scene.om` 存在；
2. `import acl` 成功；
3. `acl.init`、`set_device`、`create_context`、`create_stream` 成功；
4. OM 加载和输入输出 buffer 创建成功。

任何一步失败都会打印错误并回退到 CPU PyTorch。页面会显示实际 `use_npu` 状态；必须把“页面可用”和“NPU 推理通过”分开记录。

`AscendResource.release()` 和 `AscendModel.release()` 负责 stream、context、dataset、buffer 和 model 的释放。异常路径的释放仍需独立板端验证。
