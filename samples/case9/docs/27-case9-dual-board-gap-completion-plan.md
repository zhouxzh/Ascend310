# Case9 双板模型数据补齐计划

_版本：1.1｜日期：2026-08-30｜状态：已完成（缺口证据已归档；保留 `blocked`/`not-run` 边界）_

本文定义 Case9 在两块 Ascend 310B 板上的缺口补测。当前批次已经完成证据闭环：
教程中的每个“模型 × 板卡 × 运行路线”都有真实报告，或者有带原始错误和工件缺失
证据的 `blocked`/`not-run` 记录。这里的“已完成”表示盘点和证据归档完成，不表示
所有模型或所有机器门通过；人工质量审查、dirty-base 清理和正式准入仍未完成。

## 1. 固定范围和边界

| 板卡 | 当前地址 | SoC/算力 | 运行环境 |
| --- | --- | --- | --- |
| 8T | `192.168.1.90` | `Ascend310B4 / 8T` | `case9-acl-om`（ONNX→OM）；`base` + MindSpore/MindNLP（MindNLP 路线） |
| 20T | `192.168.1.95` | `Ascend310B1 / 20T` | `case9-acl-om` 待建立/核验；现有 `base` + MindSpore/MindNLP（MindNLP 路线） |

`192.168.8.178` 是 8T 的历史采集地址，`192.168.8.210` 是 20T 的历史请求地址。
它们不作为本批次连接入口，也不产生新的性能结论。两块板的结果分别保存和报告，
不能合并成一个“310B”平均值。

本轮只覆盖两条文本 LLM 路线：

1. **ONNX→OM→原生 ACL**：Qwen2.5-0.5B-Instruct Static-KV 1024；
2. **MindNLP/MindSpore**：Qwen1.5-0.5B-Chat、TinyLlama-1.1B、
   DeepSeek-R1-Distill-Qwen-1.5B。

不测试音频、ASR/TTS、XiaoZhi、正式网关切换，也不安装或升级 Torch、Torch-NPU、
Torchaudio、MindSpore、CANN、vLLM、MindIE 或 OPP。已有 `base` 的包污染只记录，
不删除。

## 2. 需要补齐的组合

| 路线/模型 | 8T `.90` | 20T `.95` | 本批次闭环 |
| --- | --- | --- | --- |
| Qwen2.5 Static-KV / native B4/B1 | 当前 B4 身份只读复核 `passed`；历史 ACL/API/性能批次保留 | 当前 B1 身份只读复核 `blocked`（无当前 ONNX、OM、contract、lock） | `.90`/`.95` 身份报告已归档；不重跑历史性能，不把 `.210` 历史工件当作 `.95` 当前证据 |
| Qwen1.5 MindNLP | 已有完整 8T 批次 | 缺口批次 `passed`，9/9 机器门 | 已归档 JSON/SSE、8/16/24/32/48/64 长输出、10 轮稳定性和 2+30 性能 |
| TinyLlama MindNLP | 已测，长输出/中文质量失败 | 缺口批次 `failed`，8/9 机器门 | 已归档完整失败输出；32/48 token UTF-8 失败，继续 `blocked` |
| DeepSeek MindNLP | 缺口批次 `passed`，9/9 机器门 | 已有完整隔离 API 批次 | 已归档 `.90` JSON/SSE、8/16/24/32/48/64 长输出、稳定性、质量机器门和性能 |
| Qwen2.5 B4 OM ↔ B1、B1 OM ↔ B4 | `not-run` | `not-run` | 跨 SoC 仅保留历史 compatibility 说明，不作为本批次 native 结论 |

缺口批次的原始 acceptance、性能、长输出和环境快照位于
`repro/case9-dual-board-gap-20260830/`。Qwen2.5 `.95` 的 `blocked` 是当前工件缺失
证据，不是推测的推理失败；跨 SoC 的 `not-run` 是有意保留的边界。不得用历史 `.210`
报告继承当前 `.95` 状态，也不得填写没有报告支持的速度。

## 3. 批次目录和证据规则

每次补测使用唯一 UTC `run_id`，目录只放在 Git 忽略的 `repro/` 下：

```text
repro/case9-dual-board-gap-<UTC>/
├── artifacts/
├── boards/board8t/
├── boards/board20t/
├── campaign-registries/
├── reports/
├── source/
├── bundle-manifest.json
└── SHA256SUMS.txt
```

每个报告至少保存：

- `command.json`、开始/结束 UTC、主机名、IP、SoC 和算力等级；
- Python/conda、MindSpore/MindNLP、CANN、驱动和 `npu-smi` 快照；
- 模型 revision、相对路径、Content-Length、字节数和 SHA-256；
- worker PID、启动命令、服务日志和退出状态；
- `npu-smi` before/during/after、RSS、FD、HugePages 和 NPU 内存；
- 原始 JSON/SSE 响应、长输出、稳定性、质量和性能汇总。

大文件同步严格执行 `.part -> 远端长度 -> SHA-256 -> 原子改名`。只复制显式
allowlist，不使用 `--delete`，不递归复制 home 目录。

## 4. 统一测试协议

所有模型均使用 batch 1、单进程、单请求串行、greedy 解码：

- `temperature=0`、`top_p=1`；
- context 上限 1024，`max_tokens` 取 `8/16/32/64`；
- 性能批次为 2 次预热 + 30 次测量，记录实际 prompt/completion token 数；
- 记录模型加载时间、首事件/首 token、总耗时、p50/p95 和 token/s；
- 10 轮短请求记录成功率、worker PID、RSS、FD、HugePages、NPU 内存；
- 10 条中文和 5 条英文固定探测保存完整响应；中文质量与协议门分开判定；
- 检查 UTF-8、`U+FFFD`、EOS、`finish_reason` 和 SSE 前缀差量；
- 验证错误模型、错误角色、非法采样参数、超上下文、超大请求和客户端中断。

Qwen2.5 的历史性能使用 ACL Static-KV 协议；MindNLP 模型使用其独立服务协议。
只有同一模型、同一 payload、同一批次口径的数值才可直接比较；不同模型或不同
服务栈的 token/s 不得直接排名。

## 5. 执行顺序

### 5.1 只读环境和工件门

在每块板的同一 shell 中执行：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate case9-acl-om   # ONNX→OM 检查
source /usr/local/Ascend/ascend-toolkit/set_env.sh
```

MindNLP 测试切换到现有 `base` 后重新记录完整环境指纹。检查 SoC、CANN、Python、
磁盘、内存、HugePages、`acl`/MindSpore 导入和禁止包状态。环境检查失败即停止，
不安装替代包。

### 5.2 ONNX→OM 复核

本批次已完成当前身份核对（不重跑历史性能）：

- `.90`：`repro/case9-dual-board-gap-20260830/identity-input/board8t-qwen25-current-identity.json`，
  `status=passed`，只读确认四个工件、B4 descriptor、CANN/ACL 和 `npu-smi`；报告 SHA-256
  为 `738e2788b1ff52f2d623c3462175baef49cce16739c8f40eea5dfc2c4a0d2474`。
- `.95`：`repro/case9-dual-board-gap-20260830/identity-input/board20t-qwen25-current-identity.json`，
  `status=blocked`；明确记录当前板找不到 Qwen2.5 ONNX、OM、contract 或 lock，报告
  SHA-256 为 `2f556e06760333288bf76b459db413ad6dd563b5609ee32c567de1077cb751eb`。

历史 descriptor、ACL smoke 和性能报告继续作为历史证据；当前 `.95` 未执行 ACL load 或
推理，因为工件门先阻断。

跨 SoC 互载组合本批次 `not-run`，不以 compatibility 说明替代 native OM 证据。

### 5.3 MindNLP 缺口

1. `.95` 的 Qwen1.5 缺口批次 `qwen20-gap-20260830` 已完成，9/9 机器门通过；
2. `.95` 的 TinyLlama 缺口批次 `tiny20-gap-20260830` 已完成，8/9 机器门，32/48
   token 长输出出现 UTF-8 替换字符，状态保持 `blocked`；
3. `.90` 的 DeepSeek 缺口批次 `deepseek-8t-gap-20260830b` 已完成，9/9 机器门通过；
4. 每个报告都保留临时 worker、原始响应、资源快照和失败输出；没有因机器门通过而设置
   `admitted`，共享 `base` 仍标为 `experimental_dirty_base`；
5. 人工中文质量审查仍为待审，不能把机器 `10/10` 或 `7/10` 直接写成正式质量结论。

## 6. 验收门和状态

| 门 | 内容 | 通过条件 |
| --- | --- | --- |
| G0 | SSH、SoC、CANN、Python、依赖污染 | 身份和环境快照完整 |
| G1 | 权重、tokenizer、配置、OM/ONNX | revision、长度、SHA-256 全部一致 |
| G2 | descriptor 或 MindSpore/Ascend load | 单 token/单请求真实执行 |
| G3 | JSON、SSE、错误边界 | 状态码、schema、delta 和中断符合契约 |
| G4 | 长输出 | 8/16/32/64，UTF-8、EOS、finish reason 可解释 |
| G5 | 稳定性 | 10/10 或如实记录失败，附资源快照 |
| G6 | 性能 | 2+30，首 token、总耗时、p50/p95、token/s |
| G7 | 质量 | 中文/英文原始响应和人工/机器判定分栏 |
| G8 | 复现和教程 | 报告、哈希清单、索引和教程相互可追溯 |

允许的状态仅为：`artifact_verified`、`environment_verified`、`load_passed`、
`json_passed`、`sse_passed`、`stability_passed`、`quality_reviewed`、
`performance_recorded`、`experimental_dirty_base`、`admitted`、`blocked`、
`not-run`。机器门通过不等于质量门通过；共享 `base` 的结果不能直接称为生产准入。

## 7. 失败、回滚和完成条件

失败时只停止本批次中已记录且命令行匹配的 PID；保留所有日志、哈希、响应和 NPU
快照。不得删除系统 CANN、conda 缓存、其他模型或正式服务，不得自动改用 CPU、云端
或其他推理框架。正式 `8080 -> 7861 -> 7865` 在所有缺口完成并人工批准前保持不变。

本计划完成判定（已满足）：

1. Qwen1.5/20T、TinyLlama/20T、DeepSeek/8T 均有真实 acceptance；Qwen2.5 `.90` 有
   当前身份 `passed`，`.95` 有工件缺失 `blocked`；跨 SoC 组合明确 `not-run`；
2. 两条路线的模型矩阵、速度、64-token 长输出和状态可由报告路径复核；
3. B4/B1 native、历史 provenance 与跨 SoC compatibility 已明确分栏；
4. 教程不把机器门写成中文质量，也不把旧 IP 写成当前连接地址；
5. 复现包 manifest 结构、缺口报告条目和报告哈希已核对，未提交模型权重或运行日志；
   源码快照已重新同步，`SHA256SUMS.txt` 与当前 `bundle-manifest.json` 的 `197/197`
   项一致，复现哈希门通过。

完成后的未决事项：人工中文质量签字、dirty-base 清理/准入、Qwen2.5 `.95` 工件恢复和
跨 SoC 兼容性测试仍需另行批准；这些事项不回写为本计划已通过。
