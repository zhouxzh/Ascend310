# 当前 8T 板 MindNLP Loader 能力探针

_检查日期：2026-09-04｜板卡：原 Ascend310B4 / 8T｜当前地址：`192.168.11.14`｜范围：只读兼容性检查_

## 检查边界

本记录只回答“当前板端已安装的 MindNLP 是否导出了目标模型的 loader 类”。它不代表
权重完整、模型成功加载、NPU 生成、OpenAI API、中文质量或性能通过。探针没有下载或
实例化任何模型，也没有启动 Case9 服务、修改端口或改变 Python/CANN 环境。

这是此前 `192.168.1.90`/`192.168.8.178` 所指同一块物理 8T 板的新 IP；不是新硬件，
因此历史模型性能数据不重复计为一组新的硬件结果。

## 固定环境

命令使用与其他板端检查相同的显式前缀：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
unset PYTHONNOUSERSITE
export PYTHONPATH=/home/HwHiAiUser/case9-mindspore-chat
python scripts/probe_mindnlp_loaders.py
```

实测环境为 Python `3.9.2`、MindSpore `2.4.10`、MindNLP `0.4.1`、CANN `8.0.0`、
`npu-smi 25.2.0`，`npu-smi` 识别 `Ascend310B4`。共享 `base` 中已有 Torch 系列及
其他污染包仍保留，故任何后续成功也只能标为 `experimental_dirty_base`，不能直接作为
生产准入。

## Loader 观察

| 目标 | 当前观察 | 结论 |
| --- | --- | --- |
| Qwen1.5/Qwen2.5 | `Qwen2Config`、`Qwen2ForCausalLM` 和通用 `AutoModelForCausalLM` 存在 | 仅说明 Qwen2 架构可被识别；仍需各自工件、placement 和 NPU 门禁 |
| Qwen3-0.6B/1.7B | 没有 `qwen3` 模块，也没有 `Qwen3ForCausalLM` | 两个 Profile 在 8T 保持 `blocked`；不能把 Qwen3 当 Qwen2 加载 |
| MiniCPM3-4B | `MiniCPM3Config`、`MiniCPM3ForCausalLM`、`MiniCPM3Tokenizer` 存在 | loader 可见，但当前没有锁定工件或 20T 实际运行；Profile 保持 `not-run` |
| DeepSeek | `DeepseekV2*` 与 tokenizer 类存在 | 类存在不替代固定 DeepSeek 工件和已有逐板运行报告 |

## 可复现证据

控制机复现目录：

```text
repro/mindspore-chat-20260904/reports/board8t/environment/
  loader-capability-20260904T072000Z/loader-probe.json
  loader-capability-20260904T072000Z/loader-probe.stderr.log
  loader-capability-20260904T072000Z/npu-smi.txt
  loader-capability-20260904T072000Z/python.txt
  loader-capability-20260904T072000Z/cann-version.txt
  loader-capability-20260904T072000Z/SHA256SUMS.txt
```

`loader-probe.json` SHA-256 为
`8cda8f579ea03381574d755840c310e36989412ab6d9fbb74872b7cc6acbb0e5`；
`npu-smi.txt` SHA-256 为
`249154e0f46e7821c356e9999bd9235367c6f87c3f32e34b00816d131c13b06f`；
`cann-version.txt` 记录 runtime/compiler/OPP 均为 `8.0.0`。原始报告也保留在板端：

```text
/home/HwHiAiUser/case9-mindspore-chat/reports/mindspore-chat/ip-20260904/
  loader-capability-20260904T072000Z/
```

仓库工具为 [`scripts/probe_mindnlp_loaders.py`](../scripts/probe_mindnlp_loaders.py)，
只使用 Python 标准库，便于换板后重复执行。

### 当前地址复核批次 `loader-capability-20260904T082903Z`

在同一地址重新执行了上述只读探针，作为 IP 变更后的最新 provenance。结果仍为
MindSpore `2.4.10`、Qwen2/Llama/DeepseekV2/MiniCPM3 类可见，且没有 Qwen3 类；
退出码为 `0`。本次原始目录为：

```text
repro/mindspore-chat-20260904/reports/board8t/
  loader-capability-20260904T082903Z/
```

`probe.json` 大小 `5589` bytes，SHA-256 为
`8cda8f579ea03381574d755840c310e36989412ab6d9fbb74872b7cc6acbb0e5`；完整旁证见
同目录 `SHA256SUMS.txt`。该批次没有下载权重、创建 NPU 模型实例或启动服务，不能
替代后续 G2-G8 门禁。

## 后续门禁

- Qwen3：只有在单独批准并冻结包含 Qwen3 loader 的 MindNLP/CANN 版本后，才可下载
  权重并另立兼容性实验；本轮不升级包。
- MiniCPM3：需要 20T/B1 可达、固定 Modelers revision、逐文件大小/SHA-256 和内存
  预检，然后才允许模型加载、API、长输出、稳定性、中文质量和性能测试。
- 任何 loader 能力探针都不能解除严格 placement、无 CPU fallback、逐 SoC 证据和人工
  质量门。正式 `8080 -> 7861 -> 7865` 链保持不变。
