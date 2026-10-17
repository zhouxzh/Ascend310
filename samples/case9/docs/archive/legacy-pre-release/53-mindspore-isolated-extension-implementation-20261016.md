# MindSpore 隔离环境与中文模型扩展实施记录

## 目标和证据边界

本记录对应 2026-10-16 的扩展批次。正式 `8080 -> 7861 -> 7865` ACL 链路保持不变；新增路线只在候选链 `7868 -> 7867 -> 8090` 使用 MindSpore/MindNLP。20T `192.168.1.101`（Ascend310B1）先验收，轻量模型再回归 8T `192.168.1.90`（Ascend310B4）。

当前不升级 CANN 8.0，不安装 Torch、Torch-NPU、vLLM、MindIE 或 CPU/cloud fallback。Qwen3、MindSpore Lite 和 XiaoZhi 不属于本批次；它们需要独立的版本/固件门控。

证据标签固定为：

| 标签 | 含义 |
| --- | --- |
| `documented` | 官方文档或模型卡描述，未证明本板运行 |
| `inferred` | 由架构、loader 或工件静态信息提出的可检验假设 |
| `observed-pass` | 目标板实际通过对应门 |
| `observed-fail` | 目标板实际执行后失败 |
| `blocked` | 前置条件未满足或明确阻断，未伪造成功 |
| `not-run` | 本批次有意未执行 |

## 隔离环境

板端环境 ID 为 `case9-ms240-cann80`，固定 Python 3.9、MindSpore 2.4.10、MindNLP 0.4.1，启动时显式：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate case9-ms240-cann80
source /usr/local/Ascend/ascend-toolkit/set_env.sh
export PYTHONNOUSERSITE=1
```

环境创建脚本为 `scripts/provision_mindspore_isolated.sh`，只使用板端 wheelhouse 和哈希锁；不修改 `base`、CANN、驱动、kernel 或 OPP。每次启动前保存 `scripts/snapshot_mindspore_environment.py` 的环境指纹、模块来源和 `npu-smi` 快照。

此前 20T 隔离环境已确认：Python 从 `case9-ms240-cann80` 运行，MindSpore `2.4.10` 与 MindNLP `0.4.1` 均来自隔离环境；`PYTHONNOUSERSITE=1` 下没有复用 user-site。Qwen1.5-0.5B 是隔离回归基线，完整机器门通过后仍因非生产环境标为 `experimental_isolated`，人工质量审核独立进行。

## 模型扩展批次

| Profile | 来源与状态 | 下一步 |
| --- | --- | --- |
| `qwen1.5-0.5b-mindspore` | 20T 隔离短回归 `observed-pass`；8T 需独立回归 | 完整性能沿用既有证据，质量仍单独审核 |
| `qwen1.5-1.8b-mindspore` | 20T 隔离环境已完成完整机器门；中英文机器探测各 `5/5` | `experimental_isolated`；8T 仍须独立回归 |
| `qwen2-0.5b-instruct-mindspore` | 20T 重测完成 64-token、稳定性和性能；中文探测 `U+FFFD`，B1 `blocked` | 调查 tokenizer/解码文本完整性后再重测 |
| `qwen2-1.5b-instruct-mindspore` | 20T 完整权重和非质量机器门均通过；中文探测 `4/5`，含 `U+FFFD` | `blocked` 于文本完整性；8T 不执行回归 |
| `internlm2-1.8b-chat-mindspore` | 20T config/tokenizer `blocked`：MindNLP 0.4.1 无 `internlm2` native mapping | 不使用 `trust_remote_code`；等待框架版本门控 |
| `yi-1.5-1.5b-chat-conditional` | `blocked`；`01-ai/Yi-1.5-1.5B-Chat` 在 HF mirror 不存在 | 不下载；等待真实 1.5B Chat 来源 |
| TinyLlama、DeepSeek、Qwen2.5、Qwen3、MiniCPM3 | 保留历史状态 | 不因新候选批次自动解除原阻断 |

InternLM2 的 tokenizer 模板来自固定 revision 的 `tokenizer_config.json`，但仓库中的 Transformers Python 文件仅作为元数据；Case9 禁止 `trust_remote_code`。Yi 条目故意保留为 conditional，防止把不存在的仓库误写成可复现模型；`Yi-Coder-1.5B-Chat` 是另一类代码模型，不能冒充通用中文聊天模型。

## 执行命令

先在 20T：

```bash
export CASE9_MINDSPORE_CONDA_ENV=case9-ms240-cann80
export CASE9_PYTHONNOUSERSITE=1
bash scripts/run_mindspore_board_campaign.sh \
  --board 20t \
  --profile qwen1.5-1.8b-mindspore \
  --model-root "$PWD" \
  --run-id qwen15-18b-20t-<utc>
```

通过 loader、工件和单 token 后，再运行 `qwen2-0.5b-instruct-mindspore`、`qwen2-1.5b-instruct-mindspore` 和 `internlm2-1.8b-chat-mindspore`。模型之间必须停止并确认上一个 worker 的 PID、端口和 NPU 内存已释放。

20T 通过后回归 8T：

```bash
bash scripts/run_mindspore_board_campaign.sh \
  --board 8t \
  --profile qwen1.5-0.5b-mindspore \
  --model-root "$PWD" \
  --run-id qwen15-05b-8t-<utc>
```

旧的 `scripts/run_mindspore_20t_campaign.sh` 仍可用，默认等价于 `--board 20t`；新批次使用通用 wrapper，避免把 B1 检查误用于 B4。

## 验收门和停止条件

每个板卡、每个 Profile 使用独立 UTC 目录，保存命令、PID、环境/工件哈希、`npu-smi` before/during/after、完整 JSON/SSE 响应、8/16/32/64-token 长输出、10 轮稳定性、10 条中文/5 条英文探测及 2 次预热加 30 次性能测量。性能只按 SoC 分开报告：首 token、总耗时、p50/p95、token/s。

loader 缺失、权重来源不可验证、NPU placement 缺失、TBE/ACL 崩溃、超时、内存不足或服务无法 fail-closed 时，停止该 Profile 并记录 `blocked`/`observed-fail`。不升级框架、不切换 CPU、不把另一块板的结果复制过来。

## 交付状态

- 已完成：隔离环境创建脚本、锁文件生成工具、环境快照工具、loader/tokenizer 只读探针、InternLM2 provider 和双板 runner。
- 已完成：注册表 schema v2 扩展，InternLM2 固定 revision 和 Yi 源不可用事实记录。
- 已完成：控制机单元回归 `86 passed, 1 skipped`；runner shell 语法检查通过。
- 待板端：20T 已通过模型的 8T 独立回归；InternLM2 已在 loader 层阻断，不下载权重。

Qwen1.5-0.5B 在本批次以 `qwen15-05b-20t-isolated-20261016` 完成短回归，健康、模型列表、JSON、SSE、长输出、稳定性、协议和单次性能探针均通过；环境指纹仍为 `3bc4c988fbbe7182042080476e790b644851e8b5f6739bcba18c03a9979adb02`。完整 30 轮性能数据沿用既有隔离报告，当前状态继续是 `experimental_isolated`，不是正式准入。

### 20T 首轮实测补充

`qwen2-0.5b-instruct-mindspore` 在 20T 隔离环境已完成一次完整机器批次：固定 revision 权重 `988097824` bytes，SHA-256 `130282af0dfa9fe5840737cc49a0d339d06075f83c5a315c3372c9a0740d0b96`；环境指纹 `3bc4c988fbbe7182042080476e790b644851e8b5f6739bcba18c03a9979adb02`。健康、模型列表、JSON 和 SSE 均通过，且 `/health` 记录 `device_target=Ascend`、`npu_model=Ascend310B1`、`placement_status=context_verified`。

原批次的 30 秒客户端超时会主动触发服务的 fail-closed 清理，因而其后的 TBE
`main process disappeared` 不再作为模型崩溃根因。修正超时后，Qwen2-0.5B 完成了
64-token、10 轮稳定性和 30 次性能，但中文加法探测返回 `12 加 30 �`，故保持
`blocked`，原因是可复现 UTF-8 质量失败而非已确认 TBE 崩溃。完整对照见
[54](54-mindspore-watchdog-timeout-analysis-20261016.md) 和
[56](56-mindspore-20t-isolated-rerun-20261016.md)。

旧批次在客户端断开后记录的 TBE 子进程日志保留为 `timeout-confounded` 历史证据，不能用它
替代新的长输出和稳定性结果。Qwen2 目前的真实阻断是已观察到的 UTF-8 损坏；该结论只适用
当前模型 revision、隔离环境和 Ascend310B1 组合，后续修复必须使用新的 run ID 重走全部门禁。

### Qwen2-1.5B 20T 扩展复测

`qwen2-15b-20t-retry2-20261016` 使用正确的 Case9 根目录，因而没有重复首次错误地把
`artifacts/` 子目录当作 `--model-root` 的调用错误。固定 revision
`b139ca2b8d7ba579ed727ffcd7060075666a808b` 的 3,087,467,144-byte 权重及 tokenizer、
config 都完成 SHA-256 校验。Ascend context、健康、模型列表、JSON、SSE、
`2/8/16/32/64` token、10 轮稳定性和 2 次预热后的 30 次性能均通过；首事件 p50/p95 为
`1343.582/1379.287 ms`，总耗时 p50/p95 为 `2577.378/2628.939 ms`，吞吐 p50/p95 为
`0.776/0.796 token/s`。

中文探测中“请计算 12 加 30 等于多少。”返回 `12 加 30 �`，中文机器完整性为 `4/5`、
英文为 `5/5`。因此注册表记录为 `blocked`：这不是内存不足、30 秒客户端断开或 TBE
watchdog 的归因，而是当前软件/模型组合中已经复现的文本完整性故障。完整原始报告保留在
忽略目录 `repro/mindspore-model-mirror-20261016/board20t/qwen2-15b-20t-retry2-20261016/`。

模型权重、缓存、运行日志和原始报告只保留在板端或 Git 忽略的 `artifacts/`、`reports/`、`run/`、`repro/` 中，不提交仓库。
