# 验证与限制

## 本地可执行检查

在开发机上至少执行：

```bash
cd samples/case6
python3 -m py_compile app.py config.py lane_detector.py scene_classifier.py prepare_models.py
python3 prepare_models.py --onnx-only
```

如果本机缺少 `torch`、`torchvision`、`onnx`、OpenCV 或 Gradio，记录为本地环境限制，不要把它写成板端失败。

## 板端验收顺序

1. 记录板卡型号、8T/20T、CANN、驱动、Python 和 `npu-smi`。
2. 激活正确的 conda/CANN 环境，`import acl` 通过。
3. 确认 `models/resnet18_scene.om` 的生成记录、大小和 SHA-256。
4. 启动 `python3 app.py`，记录实际后端。
5. 上传合成/匿名道路图，记录标注图、Top-1/Top-3、耗时和后端。
6. 记录一次 NPU 成功样本；如果没有成功，保留回退 CPU 日志。
7. 停止服务，确认端口释放和 ACL 资源释放。

## 证据分层

| 层 | 能证明什么 |
| --- | --- |
| `py_compile` | Python 语法可解析 |
| `--onnx-only` | PTH/ONNX 导出成功 |
| ATC | 指定 SoC 上 ONNX→OM 成功 |
| ACL smoke | OM 能在目标板加载并执行 |
| 页面 | Gradio 路由和静态展示可用 |
| 任务质量 | 需要经过训练和独立验证集，当前文档未建立 |
| 性能 | 需要固定输入、warmup、重复次数和统计方法 |

## 当前限制

- 缺少训练数据与质量评估协议；无训练权重时 CPU head 是随机分类器。
- `setup.sh` 使用 `sudo apt` 和 `pip3 install`，尚未统一到仓库当前的 `ascend` conda 环境规范。
- `app.py` 固定监听 `0.0.0.0`，`--share` 无认证。
- ROI 实际为全宽矩形，不是透视梯形。
- NPU 失败会自动 CPU 回退；页面显示后端，但文档不能把回退写成 NPU 通过。
- 当前没有独立测试目录；所有任务质量和摄像头结论都标为 `untested`，除非提供新报告。
