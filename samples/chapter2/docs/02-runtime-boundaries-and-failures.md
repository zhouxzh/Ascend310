# 运行边界与故障定位

本章样例横跨通用 CPU、CANN 编译期和 310B 设备运行期。排障时应先确认失败发生在
哪一层，避免把 CPU 脚本错误、模型转换错误、ACL 初始化错误和数值错误混为一谈。

## CPU与NPU边界

| 阶段 | 执行单元 | 可本地执行 | 成功能证明什么 |
| --- | --- | --- | --- |
| JPEG 读取、缩放、裁剪、归一化、写 BIN | CPU | 是，需要 Pillow/NumPy | 输入文件符合脚本约定 |
| ONNX 到 OM 的 ATC 转换 | CANN 编译工具链 | 不在普通开发机执行 | 目标 SoC 的模型转换成功 |
| OM 加载、内存申请、设备执行 | 310B NPU/ACL Runtime | 否 | 该 OM 在当前 ACL/驱动组合上完成一次执行 |
| raw logits 到 Top-K | CPU | 是，需要 NumPy | BIN 能被给定后处理逻辑解释 |
| 精度、延迟、吞吐结论 | 板端测量 | 否 | 必须另有模型、数据、warmup 和计时协议 |

仓库根规范把本地工作站定义为开发控制器。本文档整理期间只做了 Python 语法解析、
fixture 尺寸/有限值检查以及 CPU 后处理，没有执行 ATC、`acl` 初始化、OM 推理或
`npu-smi`。

## 输入文件故障

### 找不到图片

`make_bin_resnet224_float32.py` 使用相对路径
`data/dog1_1024_683.jpg`。在仓库根目录直接运行会相对仓库根解析路径，可能得到
`FileNotFoundError`。应切到 `sample_resnet_quick_start/` 后运行。

### BIN大小不符

正确的 ResNet50 输入 fixture 为 602112 字节。常见原因是：

- 模型实际输入 shape 不是 `1 x 3 x 224 x 224`。
- 预处理输出仍为 HWC，或漏掉 batch 维。
- 输入被保存为 `float16`，但下游 OM 声明 `float32`。
- 传给 `msame` 的是 JPEG 或文本文件。

BIN 没有内嵌 shape 和 dtype，`msame` 无法仅凭文件内容可靠识别这些错误。

### 颜色或精度偏移

转换成功不代表预处理一致。需要同时核对 RGB/BGR、归一化参数、中心裁剪方式、
NCHW/NHWC 以及 OM 的 precision mode。建议在同一批图片上比较 ONNX 与 OM 的原始
logits，而不是只比较 Top-1。

## ATC转换故障

可能表现包括：

- ONNX 输入名与 `--input_shape` 名称不匹配。
- 目标算子或数据类型在当前 CANN 算子库中不可用。
- `--soc_version` 与实际芯片不匹配。
- 小内存板卡在并行编译阶段被系统终止，出现 broken pipe 或无日志退出。
- 输出路径不可写，或同名 OM 被锁占用。

定位时必须保存完整命令、环境变量、CANN 版本、ATC 退出码和日志。不要通过伪造
OM、替换输入 shape 或把 20T 结果标成 8T 来过门禁。

## `msame`与ACL故障

常见边界如下：

| 现象 | 优先核查 |
| --- | --- |
| `acl` 导入或初始化失败 | 当前 shell 是否 source 了匹配的 CANN `set_env.sh` |
| 找不到设备或权限不足 | `npu-smi`、设备节点、运行用户权限 |
| 模型加载失败 | OM 是否完整、是否由匹配 SoC/CANN 转换、SHA-256 是否正确 |
| 输入大小不符 | 实际模型输入名、shape、dtype、format 与 BIN 字节数 |
| 推理成功但输出异常 | 输入预处理、权重来源、精度模式与输出 dtype |
| Profiling 或 dump 失败 | `msame --help` 是否支持当前组合，两个选项是否互斥 |

预编译 `msame` 为 AArch64 ELF，不能在 x86_64 Windows 或 x86_64 Linux 上直接运行。
即使 AArch64 主机可以加载该 ELF，缺少匹配 ACL 动态库时仍会在启动阶段失败。

## 后处理故障

### 输出被解释成错误类别

`postprocess_resnet50.py` 假定 BIN 是 1000 个主机原生顺序的 `float32` logits。
若 OM 输出为 `float16`、包含多个输出、带 padding、经过 transpose，或文件由 TXT
格式误命名为 BIN，解码结果就没有语义。

### 标签文件缺失

标签路径被固定为当前工作目录下的 `src/imagenet_class_index.json`。从其他目录
启动会抛出：

```text
FileNotFoundError: 请先将imagenet_class_index.json下载到src目录。
```

该错误消息描述的是脚本期望的路径，不一定代表文件真的未下载。

### 概率看似正常但不可信

当前脚本没有检查元素数、有限值或 JSON 与输出维度是否一致。`numpy.fromfile` 还会
按 dtype 隐式解释任意字节。自动化流程应在后处理前校验输出 shape、dtype、字节数
与模型签名。

## 证据等级

当前可记录的结论：

- `observed-pass`：scoped Python 文件通过 AST 语法解析。
- `observed-pass`：输入 fixture 为 602112 字节，可解释为 150528 个有限
  `float32` 元素。
- `observed-pass`：后处理脚本用 CPU 读取 4000 字节输出 fixture，并给出 Top-5。
- `untested`：预处理脚本重新执行、ATC 转换、OM 推理、板端精度和性能。
- `unknown`：fixture 的生成时间线、生成 OM、CANN/driver/firmware 组合和
  `msame` 的精确上游版本。

若后续补齐板端记录，至少应分别记录 ATC 成功、ACL/OM 执行成功、ONNX 与 OM 数值
对齐、ImageNet 任务指标和性能数据。任一阶段通过都不能替代其他阶段。
