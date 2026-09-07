# MindSpore 外部模型实测证据（2026-09-06）

本记录用于回答一个可证伪的问题：当前 Orange Pi 8T（Ascend310B4）上的
MindSpore 是否只能运行系统示例中附带的模型。测试不修改 conda、CANN、驱动或
固件，也不把探索性结果提升为 Case9 正式准入。

## 假设与证据分级

- 假设 H1：MindSpore/MindNLP 的模型来源不是系统目录白名单；只要架构、权重格式、
  tokenizer 和算子与当前 loader/版本相容，外部 Hugging Face 或 Modelers 模型可以
  在 Ascend context 中运行。
- `documented`：昇腾官方 `npu-smi` 文档把 310B1 定义为 20T、310B4 定义为 8T，
  并将其列在 Atlas 200I A2 产品范围内。
- `observed-pass`：本记录中的板端命令和输出。
- `context_only`：只有 Ascend context、设备可见性和最小生成证据；没有参数级
  placement 元数据，因此不等价于正式 NPU-only admission。

## 测试对象与板端环境

| 项目 | 实测值 |
| --- | --- |
| 主机 | `orangepiaipro`，`192.168.1.90` |
| 芯片 | `Ascend310B4`，8T |
| 驱动工具 | `npu-smi 25.2.0` |
| CANN toolkit | `8.0.0`（`/usr/local/Ascend/ascend-toolkit/latest`） |
| Python | `/usr/local/miniconda3/bin/python`，3.9.2 |
| MindSpore（用户 site 可见时） | 2.4.10，`/home/HwHiAiUser/.local/.../mindspore` |
| MindNLP | 0.4.1，`/home/HwHiAiUser/.local/.../mindnlp` |
| MindSpore Lite | 2.2.11，用户 site 可见；本次 LLM smoke 未使用 Lite |
| NPU 健康 | `Alarm`，温度约 80--81 C；作为诊断记录，不单独判失败 |
| 现有污染包 | `torch`、`torch_npu`、`torchaudio` 等已存在于 dirty-base；本次适配器未导入或安装它们 |

同一解释器设置 `PYTHONNOUSERSITE=1` 时看到的是 conda 内的 MindSpore 2.4.0，
MindNLP 和 `mindspore_lite` 显示为缺失。取消该变量后，用户 site 中的已安装运行时
恢复可见。这个差异是环境选择结果，不是模型兼容性结论。

Lite 的补充探针可以导入 `mindspore_lite 2.2.11`，并接受
`Context.target=['ascend']`；这只是 Lite API 和目标配置层的 `observed-pass`。本次板上
没有可用的 MindIR/`.ms` LLM 工件，且 Lite 在某些图上可能配置 CPU backup，因此没有
把该探针写成全图 NPU 推理通过。

另外做了两个独立进程的 ABI 顺序探针，发现当前用户 site 的 Lite 2.2.11 与
MindSpore 2.4.10 不能在同一进程可靠共存：先导入 Lite 再导入 MindSpore 报
`libmindspore_ops.so: undefined symbol ...ContainsValueAny`；先导入 MindSpore 再导入
Lite 报 `libmindspore_converter.so: undefined symbol ...PoolGrad`。这是版本/ABI
组合问题，不是 310B 不支持 Lite 的证据。Lite 路线应在隔离目录使用与 MindSpore
和 CANN 匹配的版本后再做端到端测试，本轮没有覆盖系统安装。

## Loader 探针

在取消 `PYTHONNOUSERSITE`、显式 source CANN 后，`mindnlp.transformers` 的只读探针
结果如下：

| Loader | 可见性 |
| --- | --- |
| `AutoTokenizer` | `True` |
| `AutoModelForCausalLM` | `True` |
| `Qwen2ForCausalLM` | `True` |
| `LlamaForCausalLM` | `True` |
| `DeepseekV2ForCausalLM` | `True` |
| `MiniCPM3ForCausalLM` | `True` |
| `Qwen3ForCausalLM` | `False` |

因此当前版本的真实边界是“loader/架构覆盖有限”，而不是“只允许系统自带模型”。
Qwen3 的 `False` 是该 `MindNLP 0.4.1` 组合的具体观察，应限定在这个版本和架构。

## 外部 Qwen1.5 单 token smoke

测试使用板上已经存在并经过哈希登记的外部工件：

- 模型：`Qwen/Qwen1.5-0.5B-Chat`
- revision：`4d14e384a4b037942bb3f3016665157c8bcb70ea`
- 本地目录：`/home/HwHiAiUser/case9-mindspore-chat/artifacts/models/qwen1.5-0.5b-chat`
- 权重格式：Hugging Face `model.safetensors` + `config.json` + `tokenizer.json`
- 运行策略：`mindspore.set_context(device_target='Ascend', device_id=0)`、greedy、1 token、
  `CASE9_REQUIRE_PLACEMENT_EVIDENCE=0`（明确的 context-only 探索开关）

结果：

| 指标 | 结果 |
| --- | ---: |
| 模型加载耗时 | 52.260 s |
| prompt token 数 | 25 |
| 生成耗时（1 token） | 12.670 s |
| 输出 | `我是` |
| `device_target` | `Ascend` |
| `npu-smi` 识别 | `Ascend310B4` |
| provider 状态 | `ready=true`、`healthy=true`、`placement_status=context_only` |
| 资源清理 | `provider_close=ok` |
| 进程退出 | 0 |

这条结果是外部模型从本地 Safetensors 读入并完成生成的直接证据，故 H1 在此
模型/版本/板卡组合上得到 `observed-pass`。它不证明所有外部模型都能运行，也不
证明每个参数都有显式 Ascend placement；完整 API、长输出、稳定性、质量和性能仍
需按模型分别测试。

## 为什么此前会显示 blocked

本次同一板端环境检查在用户 site 可见时的唯一 profile 失败项是：
`selected profile is blocked for 310B4`。MindSpore、MindNLP、CANN、Ascend context、
310B4 识别和磁盘检查均通过。也就是说，当前 Case9 的 `blocked` 是注册表/准入门，
不是“外部模型不能加载”。此外，严格脚本主动设置 `PYTHONNOUSERSITE=1` 会隐藏板上
MindNLP 0.4.1，造成另一类环境假失败；后续应把“干净环境检查”和“实际运行环境”
分开记录。

## 可复现实验命令

以下命令只使用已有文件，不下载或安装包：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
unset PYTHONNOUSERSITE
export PYTHONPATH=/home/HwHiAiUser/case9-mindspore-chat:$PYTHONPATH
export CASE9_MODEL_ROOT=/home/HwHiAiUser/case9-mindspore-chat
export CASE9_REQUIRE_PLACEMENT_EVIDENCE=0
```

然后用 `case9_model_profiles.load_profiles()` 取得
`qwen1.5-0.5b-mindspore`，调用 `create_provider(profile).load()` 和
`provider.complete([{'role':'user','content':'请用一句话介绍你自己。'}], 1)`；
结束时调用 `provider.close()`。正式 worker 仍默认 fail-closed，不能用这个探索开关
绕过准入、启动正式端口或宣称生产可用。

## 当前结论与下一步

1. “MindSpore 只能支持系统自带模型”这一全局判断被板端实测否定。
2. 当前更准确的说法是：`MindSpore + MindNLP` 能加载外部模型，但必须逐模型验证
   架构 loader、权重/tokenizer、算子、内存、Ascend placement 和端到端生成。
3. Qwen1.5 已有一条 context-only 外部模型通过记录；Qwen2.5、TinyLlama、DeepSeek
   可在同一 user-site 环境按相同方法逐个复测。Qwen3 先记为当前 MindNLP 版本的
   loader 未见，不能外推到 MindSpore 全部版本。
4. 不安装 Torch/MindSpore 新版、不替换 CANN、不切 CPU 或云端 fallback；每次实验
   保存 `npu-smi` 前中后快照和完整错误，成功只提升对应模型/版本/SoC 组合的状态。

## 参考资料

- [Atlas 200I A2 npu-smi：310B1=20T、310B4=8T](https://www.hiascend.com/document/detail/zh/Atlas%20200I%20A2/23.0.0/re/npu/npusmi_009.html)
- [Orange Pi MindSpore 在线模型示例](https://raw.githubusercontent.com/candle-org/orange-pi-mindspore/dev/applications/online/README.md)
- [MindSpore Lite 云侧快速入门](https://www.mindspore.cn/lite/cloud_docs/zh-CN/master/quick_start/one_hour_introduction.html)
- [MindNLP 0.4 发布说明](https://github.com/candle-org/mindnlp/releases)
