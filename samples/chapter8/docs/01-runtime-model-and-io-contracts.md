# 运行时、模型与输入输出合同

## PyACL 会话与 runner

`acl_resnet_runner.py` 提供两层抽象：

- `AclSession(device_id)`：执行 `acl.init()`、设置设备、创建 context，并在退出时 destroy context、reset device、finalize。
- `BaseResNetRunner`：加载 OM、读取模型描述、分配输入输出并复制结果。

`NaiveResNetRunner` 每帧重新创建输入/输出设备内存、Dataset 和 DataBuffer；`ReuseResNetRunner` 在加载模型后创建一次并复用。两者都返回 `(list[np.ndarray], timings)`。

`AclSession` 把 ACL 的“已初始化”返回码 `100002` 视为可继续，但不把其他非零返回视为成功。runner 的错误检查会附带 `acl.get_recent_err_msg()`（若可用）。

所有走 PyACL 的入口都需要：

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate ascend
export PYTHONNOUSERSITE=1
```

## 默认 ResNet18 OM 合同

`perf_utils.py` 和 runner 假定默认模型来自 Hugging Face 仓库 `zhouxzh/resnet18_tiny_imagenet`：

```text
model/resnet18_tiny_imagenet.om
```

下游脚本的输入合同是：

| 项目 | 合同 |
|---|---|
| 输入张量 | 单 batch，NCHW，`1x3x64x64` |
| 输入 dtype | 普通 OM 使用 `float32` |
| 预处理 | nearest resize 到 64x64，RGB，除以 255，按 ImageNet mean/std 归一化 |
| 输出 | 第一个输出用于 `argmax` Top-1 |
| 输出 dtype | runner 按 `float32` 读取 |

`BaseResNetRunner` 从 OM 读取输入字节数和输出数量/大小，但 `_prepare_input()` 只校验字节数，不校验形状、dtype 或布局。调用者必须保证传入数组满足模型合同。形状错误但字节数恰好相同的输入不会被这一层拒绝。

AIPP 路径的输入合同不同：`06_aipp_preprocess_compare.py` 以 `input_dtype=np.uint8` 运行 AIPP OM，直接传入 HWC RGB 帧。`model/resnet18_rgb_static_aipp.cfg` 声明 `RGB888_U8`、`64x64`、关闭 crop 和 CSC，并在 AIPP 内完成通道均值/缩放。不能把 AIPP 输入当作普通 float32 NCHW。

## 模型来源与完整性

`tools/download_model.py` 默认从 `https://hf-mirror.com` 查询 `zhouxzh/resnet18_tiny_imagenet`，失败时回退到 `https://huggingface.co`。普通模式只下载 OM；`--onnx`、`--aipp`、`--all` 选择其他资产。

下载流程先写 `.tmp` 再原子替换目标，并避免覆盖已有文件，除非传入 `--force`。默认 revision 固定为：

```text
dfcf5885dcf7beab3a5d6e2143c73d7973bead36
```

脚本对 OM、ONNX 和 AIPP OM 验证模型 LFS 的 SHA-256；OM 的值为 `0b9e112751181aab82b43963f1b975adf4abbcdeec7c919ee93e496c0df8d46d`。哈希不一致时临时文件会被删除，不能用错误资产继续转换。

脚本仍不检查 OM 内部 SoC 或输入形状元数据，因此下载完整性通过和 ACL/OM 合同通过仍是两个独立门禁。`06_aipp_preprocess_compare.py` 只加载已有 AIPP OM，不调用 ATC；需要重建时使用 `tools/convert_aipp_resnet18.sh`。

## 输入的确定性

`perf_utils.py` 提供两类合成输入：

- `deterministic_rgb_frame()`：按帧序号生成 RGB HWC 渐变/周期图案；
- `deterministic_nv12_frame()`：按帧序号生成 NV12，Y 平面含移动条，UV 固定为 128。

它们用于可重复的计时和流程演示，不代表 Tiny-ImageNet 数据分布，也不包含真实标签。任何准确率结论都必须来自带标签的数据集，不能由这些合成帧推断。

CPU 预处理的两种实现对比如下：

| 变体 | 行为 |
|---|---|
| `naive_numpy` | 每帧重新生成 resize 索引和中间数组 |
| `workspace_reuse` | 复用 resize 索引、RGB 工作区和 CHW batch 数组 |

两者都使用 nearest-neighbor 采样，而不是 OpenCV 或 AIPP 的插值算法。跨实现做数值比较时必须先统一 resize、颜色空间、归一化顺序和 dtype。

## 报告合同

`perf_utils.write_report()` 自动补入：

- `generated_at`：本地时区 ISO 时间；
- `host`：hostname、platform、Python 版本；
- `command`：`sys.argv` 拼接的命令行。

典型报告还包含 `case`、模型路径、`device`、`warmup`、`runs`、阶段统计和 `summary_rows`。`summarize_ms()` 给出 count、mean、p50、p95、min、max 和由均值推导的 FPS。

这些字段足以做基本来源追踪，但不等同于完整板端环境清单。至少还要补充 SoC/计算档位、CANN、driver、firmware、模型哈希和 Git commit。
