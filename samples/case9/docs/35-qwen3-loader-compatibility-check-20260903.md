# Qwen3 MindNLP Loader 兼容性检查

_检查日期：2026-09-03｜目标：原 Ascend310B4/8T 板，当前地址 `192.168.11.14`｜批次：`qwen3-loader-check-20260903`_

## 结论

在现有板端 `base` 环境中，MindSpore `2.4.10`、MindNLP `0.4.1` 可以导入
Qwen2 和 MiniCPM3，但没有 Qwen3 模块或 `Qwen3ForCausalLM` 类。因此
`qwen3-0.6b-mindspore` 和 `qwen3-1.7b-mindspore` 的 8T 状态登记为
`blocked`。这是当前 loader 能力和依赖边界的阻断，不是“Qwen3 在 310B4 上必然不能
运行”的硬件结论。

本检查没有下载权重、启动 worker、改变候选/正式端口，亦没有安装、升级或删除任何包。
不允许通过把 Qwen3 当作 Qwen2 加载来绕过架构检查。

## 环境和命令

检查在同一块物理板的新地址执行；旧地址 `192.168.1.90` 和 `192.168.8.178` 只是
历史 IP。命令前缀如下：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
python -c '... importlib.util.find_spec(...); from mindnlp import transformers; ...'
npu-smi info
```

观测结果：

| 项目 | 值 |
| --- | --- |
| Python | `3.9.2` |
| MindSpore / MindNLP | `2.4.10` / `0.4.1` |
| CANN / npu-smi | `8.0.0` / `25.2.0` |
| SoC | `Ascend310B4`，8T |
| `mindnlp.transformers.models.qwen2` | 存在 |
| `mindnlp.transformers.models.qwen2_5` | 不存在 |
| `mindnlp.transformers.models.qwen3` | 不存在 |
| `mindnlp.transformers.models.minicpm3` | 存在 |
| `Qwen2ForCausalLM` | 存在 |
| `Qwen3ForCausalLM` | 不存在 |
| 权重下载/模型加载 | 未执行 |

Qwen3 模型卡的 `model_type=qwen3`、`Qwen3ForCausalLM` 与现有 Qwen2 loader 不同；
即使模型规模较小，也必须先有版本匹配的 MindNLP adapter，再执行工件、内存和 NPU
门禁。当前计划明确禁止升级 MindSpore/MindNLP 或引入 Torch、Torch-NPU、vLLM、
MindIE 作为此检查的回退。

参考：[MindSpore-Lab/Qwen3-0.6B](https://huggingface.co/MindSpore-Lab/Qwen3-0.6B)、
[MindSpore-Lab/Qwen3-1.7B](https://huggingface.co/MindSpore-Lab/Qwen3-1.7B)。

## 当前地址复核（2026-09-04）

同一块板切换到 `192.168.11.14` 后，以仓库工具重新执行了只读 loader 探针；结果与本
记录一致，未下载或加载 Qwen3 权重。原始快照和 SHA-256 清单见
[MindNLP loader 能力记录](38-mindnlp-loader-capability-20260904.md)。这次复核只更新
provenance，不重新计为模型运行或性能结果。

## 后续边界

只有在单独批准并冻结兼容的 MindNLP/CANN 版本、权重 revision 和哈希后，才可另立
Qwen3 兼容性实验。届时仍须在 8T 与 20T 分别执行加载、协议、长输出、稳定性、中文
质量和性能门；本记录不能替代这些门，也不能把当前 `blocked` 自动改成可切换状态。
