# 验证与证据边界

本章源码覆盖设备初始化、ONNX/OM 推理、分类、检测、ATC 和评估外壳，但各层证据不是同一种结论。当前仓库没有把模型 revision、CANN、驱动、固件、SoC 规格、源码提交和原始日志绑定成一个可复现实验 tuple。

## 验证门槛

| 门槛 | 入口/产物 | 当前状态 |
|---|---|---|
| Python 语法 | `py_compile`/解释器 | 本次任务执行静态检查，见最终交付记录 |
| PyACL 导入 | `python -c "import acl"` | 本次未在板端执行，`untested` |
| 设备初始化 | `check_ascend_device.py` | 本次未执行，`untested` |
| ONNX 接口 | ONNX Runtime 加载和 shape 检查 | 本次未执行，`untested` |
| ATC 转换 | `SSDLite/scripts/convert_onnx_to_om.py` | 本次只审阅代码，未执行，`untested` |
| OM 加载 | PyACL `load_from_file` | 本次未执行，`untested` |
| 数值一致性 | CPU ONNX 与 NPU 输出逐样本比较 | 仓库无统一脚本，`untested` |
| 分类准确率 | ResNet18 Tiny-ImageNet Top-1 | 仓库无新报告，`untested` |
| 检测任务质量 | SSD/SSDLite COCO 指标 | 只有 SSDLite 历史 CSV，未复验 |
| 性能 | 带 warmup/重复/percentile 的计时 | 仓库脚本没有该协议，`untested` |
| 服务/API/camera | 服务、HTTP、设备 | 本章没有这些入口 |

## ResNet18 artifact

本地 ONNX 文件的字节数与 SHA-256 已核对：

```text
bytes  45223270
sha256 e461aecee8bfafe95cd4528d3e836783c7546a0bb201c98e25109cde51b3bd6e
```

本地 OM 文件为 `22,821,283` 字节：

```text
sha256 0b9e112751181aab82b43963f1b975adf4abbcdeec7c919ee93e496c0df8d46d
```

Hugging Face 仓库 `zhouxzh/resnet18_tiny_imagenet` 的固定 revision
`dfcf5885dcf7beab3a5d6e2143c73d7973bead36` 对 ONNX 和 OM 分别登记上述
SHA-256；本地两个 artifact 均与发布元数据一致。下载脚本已固定 revision 并在
下载/复用文件时校验哈希。

这只证明 artifact 完整性。是否由当前 ONNX、指定 ATC 和 CANN 在本板重新生成，
仍需独立转换日志；不能用哈希一致替代 ATC/ACL 证据。

## bbox 坐标转换

CPU 与 NPU 验证路径现在都调用 [bbox_utils.py](../SSD/bbox_utils.py)，统一把 HF
`objects.bbox` 解释为 `[xmin,ymin,xmax,ymax]`，再缩放为 300×300 XYXY 或 COCO
XYWH。共享单元测试覆盖坐标顺序、尺度和非法框：

```text
python -m pytest -q tests/test_bbox_utils.py
3 passed
```

该测试证明转换合同一致，不证明 SSD300 模型、COCO mAP 或 NPU 推理已通过。

## SSDLite 历史报告

仓库中有两份 2026-06-03 的 15 行批量报告：

- [CPU 批量报告](../SSDLite/reports/cpu_all_eval_20260603_074100.csv)
- [NPU 批量报告](../SSDLite/reports/npu_all_eval_20260603_171439.csv)

两份报告都覆盖 15 个 backbone，provider 字段分别为 `cpu` 和 `npu`。以默认 `mobilenetv4_conv_small` 行为例：

| provider | mAP | fps_total | fps_inference |
|---|---:|---:|---:|
| CPU，历史 CSV | 0.0537438678 | 6.4678 | 6.9990 |
| NPU，历史 CSV | 0.0536818576 | 24.2964 | 32.9767 |

这些数值可在对应 CSV 中逐字核对，本次没有重跑。报告没有记录：

- SoC 型号和 8T/20T 规格。
- CANN、驱动、固件和 Python/包版本。
- 源码 commit、ONNX/OM revision、字节数或 SHA-256。
- 预热、重复次数、百分位方法和板端资源监控。
- 完整命令、stderr、ATC 日志和数据集 revision。

因此它们属于 `documented historical artifact`，不是本次任务的 `observed-pass`。CPU 与 NPU 的 mAP 很接近可以作为后续交叉检查的线索，但不能单独证明数值一致性、OM 来源或当前代码可复现。

`SSDLite/outputs/val_results/` 下还有 65 个历史 prediction JSON，覆盖多个运行时间。它们用于追溯旧报告，未被本次任务重新评分；JSON 中缺少同目录一起提交的环境 manifest，不能用来选择“最佳”模型。

## SSDLite artifact 清单

当前样例目录包含 15 个 ONNX 和 15 个 OM 文件，总字节数分别约为 562.7 MB 和 393.1 MB。文件存在不等于已验证：

- 下载脚本没有固定 Hugging Face revision。
- 下载脚本没有计算 SHA-256。
- 转换脚本没有保存 ATC 日志和版本。
- 模型权重许可证没有与每个 artifact 绑定。
- 现有 15 行报告没有引用上述哈希。

发布模型结果前，需要为每个 backbone 建立 artifact manifest，至少包含 repo、revision、文件路径、字节数、SHA-256、转换命令、CANN/ATC 版本和转换日志。

## SSD300 证据缺口

当前 `SSD/models/` 没有提交 ONNX/OM，也没有 SSD 专项运行报告。因此不能执行或声称以下结果：

- SSD300 ATC 成功。
- OM 在 310B 上加载成功。
- CPU/CUDA/NPU 输出数值一致。
- SSD300 COCO mAP。
- SSD300 310B latency/FPS。

随目录的 [NVIDIA README](../SSD/README.md)明确记录的是 A100/V100 数据，不属于 310B 证据集。它的 mAP 和吞吐不能复制到本章的 Ascend 结论。

SSD 代码还存在 bbox 字段解释不一致：CPU 与 NPU 对同一 `zhouxzh/coco-val` 数据分别按 XYXY 和 XYWH 处理。解决前，SSD mAP 状态是 `blocked`，需要先做标注字段合同测试和 CPU/NPU 同输入数值比较。

## 未执行的板端检查

本次文档任务没有执行：

- `npu-smi`、CANN 版本读取或设备健康检查。
- `check_ascend_device.py`。
- ATC。
- OM 加载或 PyACL 推理。
- ONNX Runtime 全量验证。
- COCO 指标计算。
- 性能、内存、稳定性、温升或功耗测试。
- 摄像头、服务、API 和 UI 测试。

不能把“文档写明了命令”当作“命令已经通过”。在真实板卡上重跑时，应分别保存 device、model-load、contract、task-quality 和 performance 证据。
