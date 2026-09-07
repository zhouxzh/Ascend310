# MindSpore Lite 2.5.0 Ascend MobileNetV2 构建烟测记录

本记录只覆盖 8T 开发板上的隔离 MindSpore Lite 运行时探针，不改变 Case9
正式 ACL 基线、MindSpore LLM Profile 或网关入口。模型和日志仍留在板端，
不提交 Git。

## 结论摘要

在同一块 `Ascend310B4 / 8T` 开发板（当前地址 `192.168.1.90`）上，创建了
独立 conda 环境 `case9-mslite250`，安装并校验了官方 Linux-aarch64、Python 3.9、
CPU/Ascend 云侧推理 wheel。以下两层通过：

| 层 | 结果 | 证据标签 |
| --- | --- | --- |
| `import mindspore_lite` | 版本 `2.5.0`，导入路径属于新环境 | `observed-pass` |
| `Context()`、`target=['ascend']`、`device_id=0` | 配置成功；Lite 2.5.0 API 内部会自动追加 CPU backup | `observed-pass` |

随后使用 MindSpore 官方 MobileNetV2 MindIR 调用 `Model.build_from_file`。在
`target=['ascend']` 的独立进程中，模型构建阶段以 `SIGSEGV`（退出码 `139`）
结束，未进入 `predict`。同一个 MindIR 在 Lite CPU context 中可构建并完成一次
预测。因此本轮的结论是：

| 项目 | 状态 |
| --- | --- |
| 新环境和 wheel 安装 | `passed` |
| Lite Python 绑定导入 | `observed-pass` |
| Ascend Context 配置 | `observed-pass` |
| Lite 2.2.11 对同一 AddNet MindIR 的构建 | `observed-fail`；明确提示只支持 MindIR version `1`，当前图为 `2` |
| Lite 2.5.0 对官方 `.ms` 文件的构建 | `observed-fail`；返回 `Fail to support` |
| Ascend `build_from_file` | `observed-fail`，exit `139` |
| 最小 AddNet MindIR 的 Ascend `build_from_file` | `observed-fail`，exit `139` |
| Ascend `predict` 数值输出 | `not-run` |
| 显式 NPU placement | `not-established`；API 会保留 CPU backup |
| 性能、稳定性和准确率 | `not-run` |
| 当前 Lite/Ascend 路线 | `blocked`，等待兼容组合或更小的受支持 MindIR 复核 |

这个失败只适用于本次 wheel、CANN/驱动、设备、MindIR、输入和命令组合，不能
写成 MindSpore Lite、Ascend 310B 或所有 MindIR 的全局 `unsupported`。

## 环境和工件身份

| 项目 | 实测值 |
| --- | --- |
| 主机/地址 | `orangepiaipro` / `192.168.1.90` |
| SoC/算力 | `Ascend310B4` / `8T` |
| 架构/内核 | `aarch64` / `Linux 5.10.0+` |
| `npu-smi` | `25.2.0` |
| CANN toolkit | `/usr/local/Ascend/ascend-toolkit/latest`，目录版本 `8.0.0` |
| CANN 内部版本 | `Version=7.6.0.1.220`，`version_dir=8.0.0` |
| Lite 环境 | `/home/HwHiAiUser/.conda/envs/case9-mslite250` |
| Python | `3.9.25` |
| NumPy | `2.0.2`，来自 Lite 环境 |
| Lite wheel | `mindspore_lite-2.5.0-cp39-cp39-linux_aarch64.whl` |
| wheel bytes/SHA-256 | `138554014` / `410583d8e44f0945858411d8fd4db96feea7cc100c64ede49af47ed0100eaecb` |
| 原用户 site Lite wheel（恢复件） | `mindspore_lite-2.2.11-cp39-cp39-linux_aarch64.whl`，`111373274` bytes，SHA-256 `2731c8c7e27fafcc2c5a88443b101276963f8b64feba8e5313c81bf88538514d` |
| MindIR | `mobilenetv2.mindir`，`14325172` bytes |
| MindIR SHA-256 | `000748951cf1835b941b26fb96390dd63ef02fcf9b93d23cc746f0462640822d` |
| 输入文件 | `input.bin`，`602112` bytes，SHA-256 `3162fcaf6cf524414e943c833ff23f3f7dc2cd66cc87517880b8e0d98e1d3b5a` |
| 最小图 MindIR | `add_fp32.mindir`，`731` bytes，SHA-256 `2549ede10ca3799a0c1eb947b1051e49d7c99180807e37012ea6cda9a59abbb5` |

Lite wheel 的官方地址为：

`https://ms-release.obs.cn-north-4.myhuaweicloud.com/2.5.0/MindSpore/lite/release/linux/aarch64/cloud_fusion/python39/mindspore_lite-2.5.0-cp39-cp39-linux_aarch64.whl`

板端工件和原始日志目录：

```text
/home/HwHiAiUser/case9-mindspore-lite-2.5.0/artifacts/
/home/HwHiAiUser/case9-mindspore-lite-2.5.0/reports/board8t/
```

## 安装过程

安装没有有意修改 `base` 中的包、系统 CANN、驱动或 OPP。首次尝试固定
`python=3.9.2` 和 `numpy=1.22.4` 时，板端 conda channel 没有这组精确包，
属于环境求解失败；随后改用同一 Python 主版本的可用包完成环境创建。安装命令为：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda create --yes --no-default-packages -n case9-mslite250 python=3.9 pip numpy
conda activate case9-mslite250
export PYTHONNOUSERSITE=1
pip install --no-deps --no-index \
  /home/HwHiAiUser/case9-mindspore-lite-2.5.0/artifacts/mindspore_lite-2.5.0-cp39-cp39-linux_aarch64.whl
```

首次安装命令未先设置 `PYTHONNOUSERSITE`，pip 曾看见用户 site 并卸载了原有
`mindspore-lite 2.2.11`；这不是新 conda 环境所需的变更。已从官方 2.2.11
wheel 重新下载、核对上述字节数和 SHA-256，并使用 base 解释器的
`--user --ignore-installed --no-deps` 恢复到
`/home/HwHiAiUser/.local/lib/python3.9/site-packages`。复核结果为版本
`2.2.11`；新环境在 `PYTHONNOUSERSITE=1` 下仍报告版本 `2.5.0`。
传输过程中产生的 33086800-byte 不完整文件改名为
`mindspore_lite-2.2.11-cp39-cp39-linux_aarch64.whl.failed-transfer-33086800`，
仅作为失败传输记录，未被安装或加载。

恢复后的 base 用户 site 中，单独启动 Python 进程分别导入完整 MindSpore
`2.4.10` 和 Lite `2.2.11` 均成功；在同一个进程按
`import mindspore; import mindspore_lite` 混用时出现
`libmindspore_converter.so: undefined symbol ... PoolGrad`。这属于两套版本
运行库的 ABI 混用边界，不能解释为硬件不支持；Lite 验证应继续使用独立环境和
`PYTHONNOUSERSITE=1`，不要把两个运行时放进同一进程。

后续安装或测试必须先设置 `PYTHONNOUSERSITE=1`，避免用户 site 参与依赖解析：

```bash
export PYTHONNOUSERSITE=1
```

每次运行前使用同一 shell 设置 CANN，并隔离用户 site：

```bash
conda activate case9-mslite250
export ASCEND_HOME_PATH=/usr/local/Ascend/ascend-toolkit/latest
source /usr/local/Ascend/ascend-toolkit/set_env.sh
export PYTHONNOUSERSITE=1
```

在新环境中，`mindspore_lite` 可见；`mindspore`、`torch`、`torch_npu`、
`torchaudio`、`onnxruntime` 和 `transformers` 均不可见。这里的“不可见”是
该进程的模块搜索结果，不代表它们从原有 `base` 环境被删除。

## 运行证据

### 绑定和上下文

以下检查在独立 Python 进程中通过：

```python
import mindspore_lite as mslite

context = mslite.Context()
context.target = ["ascend"]
context.ascend.device_id = 0
```

实测输出包含 `mindspore_lite version 2.5.0` 和 `context.target ['ascend']`。
进一步读取内部设备列表得到 Ascend 与 CPU 两个目标；这是 Lite 2.5.0 Python API
的既定 backup 行为。因此这一步只证明绑定能解析 Ascend 配置，不证明严格 NPU-only、
图编译或算子执行成功。

### Ascend MindIR 构建

测试使用：

```python
model = mslite.Model()
model.build_from_file(
    "/home/HwHiAiUser/case9-mindspore-lite-2.5.0/artifacts/mobilenetv2.mindir",
    mslite.ModelType.MINDIR,
    context,
)
```

进程在 `model_ready` 之后、`build_ready` 之前收到 `SIGSEGV`，退出码为 `139`。
板端 `dmesg` 显示该进程曾绑定 Ascend 设备并创建设备内存池，退出时释放了进程
上下文；没有把这次退出伪装成成功，也没有切换 CPU 继续回答。

完整原始文件：

| 文件 | SHA-256 |
| --- | --- |
| `mobilenetv2-lite-ascend-smoke-20260907.log`（缺少 `ASCEND_HOME_PATH` 的首轮） | `6152086475bebfed5d2f34e0e1cc35e6ddcfedc87ce52a35c47ac36b724a02fb` |
| `mobilenetv2-lite-ascend-smoke-20260907-retry.log`（正确 CANN 路径） | `5763570d95af6870270b81b1d233944b5c8a77dfdab37c8ea78c1ed7c81b7eb9` |
| `addnet-lite-2.5-ascend-20260907.log`（最小 AddNet，正确 CANN 路径） | `58e2651f738bb937285d608c48c8d854a930184f84ce373507427ecda1e74f1a` |

首轮日志中的 `ASCEND_HOME_PATH` 缺失导致 Lite 环境检查器异常，不能当作模型
失败；重试已设置实际存在的 toolkit 路径，仍在 Ascend `build_from_file` 阶段
复现 `SIGSEGV`。

为排除 MobileNetV2 图本身的复杂算子影响，又使用板端 MindSpore 2.4.10
导出的最小 `AddNet(left, right)` MindIR 做相同的 Ascend 构建探针。该图只有
两个 `[1,4]` 的 `float32` 输入和一个加法输出，仍在
`Model.build_from_file` 返回前以退出码 `139`（`SIGSEGV`）结束。这个结果把
当前失败边界进一步收窄到 Lite 2.5.0 Ascend 构建/插件初始化组合；它仍不是
对所有模型或所有 CANN 版本的全局不支持结论。

同一官方 MobileNetV2 的 `.ms` 文件在 Lite 2.5.0 中用
`ModelType.MINDIR_LITE` 进行探针，返回 `Fail to support`（未崩溃）；日志为
`mobilenetv2-ms-lite-2.5-ascend-20260907.log`，SHA-256
`706421b3e7daae6b2ca11ea03e374532559bfeaaf93a6e09c1310880bb1ba6a3`。这只是
本 Python/Ascend API 对该文件的格式探针，不代表所有 `.ms` 文件都不支持。

### Lite 2.2.11 版本对照

为区分 Lite 版本和 CANN 后端问题，另建临时隔离环境
`case9-mslite2211`，安装已恢复的官方 `2.2.11` aarch64 wheel。该环境的
导入和 Ascend Context 配置通过；对同一个 `add_fp32.mindir` 的
`build_from_file` 没有崩溃，而是返回明确错误：
`This software can only support the maximum mind ir version: 1 ... mind ir version: 2`。
因此 2.2.11 这次没有进入 Ascend 图编译，不能拿它作为 NPU 成功或失败的证据；
它说明 Lite 版本必须和 MindIR 版本匹配。对应日志为
`addnet-lite-2.2-ascend-20260907.log`，SHA-256
`d40feee423998d0c98f6327297c2487a387ccdcdd925e1ee846de69a62cff088`。
该 `case9-mslite2211` 仅用于本次对照，测试结束后已删除；板端保留其日志和
已校验的恢复 wheel，不把临时环境当作运行依赖。

### CPU 对照

为区分工件损坏和 Ascend 后端问题，在同一新环境、同一 MindIR 和输入上运行了
CPU context：

| 指标 | 结果 |
| --- | ---: |
| `build_from_file` | 约 `3.001174 s` |
| `predict` | 约 `0.041089 s` |
| 输出 | `shape1`，`[1, 1000]`，`float32`，argmax `446` |

这个 CPU 对照仅用于故障定位，不是 Case9 的生产 fallback，也不构成 310B NPU
验收。

### 设备状态

Ascend 探针前后 `npu-smi` 均识别 `310B4`，健康字段为 `Alarm`；内存约从
`3595/15610 MB` 变为 `3844/15610 MB`。`Health: Alarm` 只作为诊断记录，
不单独判定 Lite 成功或失败。此次实际失败证据是进程 `SIGSEGV`，而不是健康字段。

## 官方边界和版本解释

官方 [MindSpore Lite 2.5.0 下载页](https://www.mindspore.cn/lite/docs/zh-CN/r2.5.0/use/downloads.html)
将该 wheel 标记为 Linux-aarch64、Python 3.9、CPU/Ascend 云侧推理包；这说明
平台标签匹配当前板的架构，不保证每个 Ascend SKU、驱动、CANN 组合或每个 MindIR
都能成功编译。

官方 [Python 云侧推理流程](https://www.mindspore.cn/lite/docs/zh-CN/r2.5.0/mindir/runtime_python.html)
给出的 Ascend 路径也是设置 `Context.target=['ascend']`、指定 `device_id`，
再调用 `build_from_file` 和 `predict`。Lite Python Context 还说明 Ascend 目标会
自动保留 CPU backup；本次停在构建阶段，因此不能把文档中的
MobileNetV2 示例写成已在本板完整通过。

官方 [版本矩阵](https://www.mindspore.cn/versions/) 对 MindSpore 2.5.0 使用的
Ascend 软件包标为 CANN 8.0.0.beta1。本板 toolkit 目录显示 `8.0.0`，而
`compiler/version.info` 显示内部软件包版本 `7.6.0.1.220`；这两个字段都已
记录，不能简单把它们当成完全相同的发行组合。Lite 源码检查器本身会读取后者，
但实际图构建仍需在目标板上验证。

官方 [310 推理 FAQ](https://www.mindspore.cn/docs/zh-CN/r2.2/faq/inference.html)
说明 Ascend 310 推理使用匹配的 MindSpore Lite 发布包。这里已经选择了云侧
Ascend wheel，而不是只含 CPU 的端侧 aarch64 包；仍不能由安装成功推断 LLM
或任意 Hugging Face 权重可以直接加载。

### 第二轮分层探针（2026-09-07）

为排除 Python 绑定导入、设备初始化和模型图本身相互混淆，增加了两个板端脚本：

- `scripts/probe_acl_board.py` 只调用原生 ACL。当前板上 `acl.init`、
  `acl.rt.set_device(0)`、创建/销毁 context、`reset_device` 和 `acl.finalize`
  均返回 `0`（`observed-pass`）。这证明驱动、CANN runtime、ACL 和设备上下文
  可以正常初始化；它不等于 Lite 图编译通过。
- `scripts/probe_mindspore_lite_board.py` 同一份脚本支持 `--target cpu` 和
  `--target ascend`，把 `import`、`context`、`build`、`predict` 分成独立事件，
  并记录输入输出 shape/dtype。脚本只导入 `mindspore_lite`，不会引入完整
  MindSpore、Torch 或 Transformers。

Lite 2.5.0 在同一隔离环境中的最小 AddNet MindIR 对照结果：

| 路径 | build | predict | 结论 |
| --- | --- | --- | --- |
| `--target cpu` | 0.095683 s，`observed-pass` | 0.000303 s，`observed-pass` | MindIR、Lite Python API 和 CPU runtime 有效 |
| `--target ascend` | 进程退出 `139/SIGSEGV`，仅打印 import/context | 未执行 | 失败边界在 Ascend `build_from_file` |

官方 MobileNetV2 MindIR 在 `--target cpu --input-bin input.bin` 下也完成了
`build + predict`，输出 `[1,1000] float32`。因此两次 CPU 对照都不是因为文件损坏；
Ascend 侧仍然在构建阶段崩溃，且最小 AddNet 已复现，不能归因于 MobileNet 的卷积
算子复杂度。

为继续验证版本因素，已从官方 2.4.10 下载页获取 Linux aarch64 Python 3.9
CPU/Ascend cloud wheel。文件在控制机计算后再传输，板端大小为
`132901167` bytes，SHA-256 为
`96a1da9be455e03caf32cdfee5206c4599d4c5aa9dee25dd4b48e1361d5d1fba`。新建
`case9-mslite2410` 隔离环境，环境中只有 Python 3.9.25、NumPy 2.0.2 和
`mindspore_lite==2.4.10`；`mindspore`、Torch、Torch-NPU、Transformers、
ONNX Runtime 均不存在。环境报告为
`lite-2.4.10-environment-20260907.log`，SHA-256
`ad80a7e6dcb79255928da0bb06541840ea2aece48713843e1edf605d5042ab16`。

Lite 2.4.10 的实际结果：

| 路径 | 最小 AddNet | 官方 MobileNetV2 | 结论 |
| --- | --- | --- | --- |
| CPU | `build + predict` 通过 | 已在 Lite 2.5.0 做过同样 CPU 通过 | MindIR 与 CPU runtime 有效 |
| Ascend | `SIGSEGV(139)`，日志 SHA-256 `94094221eba789ff7055b38adb09d846e44bdf83dcca0cd7d55bdee524c6f606` | `SIGSEGV(139)`，日志 SHA-256 `a70c9b5eb23e1e4b25cb3d0eba870a62e627e77eca9ddee3e0e25cc1f46540f2` | 与 2.5.0 相同，失败点为 `build_from_file` |

2.4.10 Ascend 两次运行均只打印 `import` 和 `context`，没有进入
`build_passed` 或 `predict`；`npu-smi` 的 AICore 保持 0，设备没有 reset。
这使当前证据从“某个 Lite 版本故障”收窄为“Lite 2.4.10/2.5.0 Python cloud
runtime 在本板现有 CANN/GE 组合的 Ascend 图构建边界失败”。

### 崩溃栈和设备日志

板端为两次 2.4.10/2.5.0 Ascend 崩溃生成了只读 coredump 摘要：

```text
/var/log/npu/coredump/stackcore.python.4790.11.1788759342
/var/log/npu/coredump/stackcore.python.5018.11.1788759462
```

两份栈都经过 `libaicpu_extend_kernels.so`、CANN
`plugin/opskernel/libfe.so`、`libgraph_base.so` 和 `libge_compiler.so`；这与
Lite Python 层只打印 `context` 后崩溃的时间点一致。`ldd` 在显式加入 Lite
自带 `lib` 目录后对 `libmslite_shared_lib.so`、`libascend_ge_plugin.so` 和
Python wrapper 均显示 `all-resolved`，所以当前证据不支持“普通动态库缺失”
这一解释。相关 slog 记录了 AICPU 包加载和 GE 初始化，未记录用户模型的
`predict` 任务执行。

同时，内核/设备日志在测试期间出现 Ascend LPM 异常码 `0xA6193215`，该状态
在重启前后周期性出现；它是板卡诊断证据，不能单独归因于 Lite，也不能用作
Lite 失败的唯一原因。实际 Lite 门禁失败仍是可复现的用户进程 `SIGSEGV(139)`。

### 完整 MindSpore Ascend 对照

为验证 CANN/GE 并非对所有 Python NPU 框架都失效，在同一板卡、同一 toolkit
环境中运行了一个不加载模型的完整 MindSpore 2.4.10 Add 算子：

```text
target=Ascend, device_id=0
left=[1,1,1,1], right=[2,2,2,2]
output=[3.0,3.0,3.0,3.0], shape=[1,4], dtype=Float32
exit=0
```

完整日志 `mindspore-full-add-20260907.log` 的 SHA-256 为
`450d559e89cadf5f8ccf249f1254ded361c0fb942d788650fe8eee22e87fbd9a`。
该结果是 `observed-pass`：当前 CANN/驱动/GE 可以执行完整 MindSpore 的
Ascend 算子，而 Lite 2.4.10/2.5.0 在相同环境对 MindIR 进行 Ascend
`build_from_file` 时崩溃。因此失败范围进一步限定为 MindSpore Lite 的
Ascend graph build/plugin 组合，不是 310B、ACL 或 CANN 完全不可用。

官方 2.4.10 C++ cloud tarball（`245146878` bytes，SHA-256
`e9d0ebc1adc1b3e750284be4864de0a5e69b5f96acc5f6c271a65f46b5a2d001`）曾尝试
传输；由于板端当时持续传输速度降至约 15 KB/s，为保护板端资源已停止，
因此 C++ benchmark 路径状态为 `not-run`，不能把它写成失败。

截至本轮记录，8T 板在测试期间出现过约 17 的系统负载和短暂 SSH/ICMP 不可达。
随后开发板重启，重启后用低负载重新完成了 2.4.10 验证；没有执行清理用户进程
或修改系统 CANN。资源异常本身只记为硬件运行条件，不当作 Lite 兼容性结论。

## 对 Case9 LLM 的影响

这次实验不改变以下状态：

- Qwen2.5 静态 KV ACL 仍是现有正式文本基线；
- MindSpore/MindNLP 候选仍按各自板卡、模型和 placement 证据单独记录；
- 不把 HF Safetensors、ONNX 或 OM 自动转换成 Lite 可加载的 MindIR；
- 不在 Lite 环境安装 Torch、Torch-NPU、Transformers、vLLM 或 MindIE；
- 不修改正式 `8080 -> 7861 -> 7865` 入口，也不启动 Lite 聊天服务。

MindSpore Lite 需要一个与其 converter/runtime 契约相符的 MindIR（或明确支持的
Lite 模型格式）。当前板上没有已经验证的 LLM MindIR；因此这次 MobileNetV2
Ascend 构建失败后，不能继续声称 Lite 已具备 LLM 推理能力。后续若继续，应该
单独选择与本板 CANN/驱动匹配的 Lite 版本或官方已验证的最小 MindIR，先复现
`build_from_file + predict`，再评估 LLM 转换和 tokenizer/API 适配。

## 复现和停止条件

重跑前先确认没有遗留服务占用设备，然后执行：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate case9-mslite250
export ASCEND_HOME_PATH=/usr/local/Ascend/ascend-toolkit/latest
source /usr/local/Ascend/ascend-toolkit/set_env.sh
export PYTHONNOUSERSITE=1
npu-smi info
```

若再次出现 `SIGSEGV`、ACL 初始化错误、设备重置或 `build_from_file` 非零返回，
停止 Lite LLM 移植，保留板端日志和 `npu-smi` 快照；不自动换 CPU、云端、Torch、
MindSpore 新版或其他模型。只允许停止本次探针产生的 PID，不删除共享 conda
缓存、系统 CANN、既有模型或历史报告。
