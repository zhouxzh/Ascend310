# MindSpore LLM 候选模型验收记录

_记录版本：1.6｜建立日期：2026-09-02｜更新日期：2026-09-04｜状态：Qwen2.5-0.5B 已完成 8T 重启后短诊断但严格准入阻断；Qwen2.5-1.5B 重启后的正确 CANN 诊断在生成阶段发生内存分配失败，后续服务门禁仍阻断；其余新增候选按逐板证据保留_

---

本文是可持续追加的实测账本。8T 当前连接入口是 `192.168.1.90`；
`192.168.11.14` 和 `192.168.8.178` 是同一物理板的历史别名。文中保留历史运行地址
以便逐字核对原始报告，但 IP 变化不产生新的硬件或性能批次。`not-run` 表示本轮尚未执行，`blocked` 表示已有明确
前置阻断或历史失败；空白指标不能解释为通过。每条状态都必须能回到板端原始日志、
响应、进程记录和 `npu-smi` 快照。

截至 2026-09-02（历史快照），本轮新增候选的板端 G0-G8 尚未开始：控制机对两个当前地址执行
只读 SSH 连通性检查均超时（每个目标 `ConnectTimeout=5`，退出码 1）：

```text
ssh -o BatchMode=yes -o ConnectTimeout=5 -o ConnectionAttempts=1 HwHiAiUser@192.168.1.90 ...
ssh: connect to host 192.168.1.90 port 22: Connection timed out

ssh -o BatchMode=yes -o ConnectTimeout=5 -o ConnectionAttempts=1 HwHiAiUser@192.168.1.95 ...
ssh: connect to host 192.168.1.95 port 22: Connection timed out
```

因此本轮没有执行板端命令、模型下载、包安装、服务启动或 NPU 推理；注册表中的
`not-run`/`blocked` 状态保持原值，不能把网络阻断解释为模型兼容性结论。恢复路由和
SSH 后，应从 G0 环境快照开始，按 [双板验收计划](32-mindspore-llm-candidate-validation-plan.md)
逐板追加原始证据。

在本记录生成后的二次连通性复核中，两个目标仍无法建立 TCP/22 连接：

```text
ssh -o BatchMode=yes -o ConnectTimeout=8 -o ConnectionAttempts=1 HwHiAiUser@192.168.1.90 ...
ssh: connect to host 192.168.1.90 port 22: Connection timed out

ssh -o BatchMode=yes -o ConnectTimeout=8 -o ConnectionAttempts=1 HwHiAiUser@192.168.1.95 ...
ssh: connect to host 192.168.1.95 port 22: Connection timed out

Test-NetConnection 192.168.1.90 -Port 22: False
Test-NetConnection 192.168.1.95 -Port 22: False
```

本次没有因超时执行重试安装、端口修改或远程清理；正式入口和候选入口均未被触碰。

## 0. 🧪 控制机验证证据

以下检查在 Windows 控制机的 `sci-agent` 环境完成，只覆盖纯 Python、注册表、脚本边界和
前端构建，不替代任何板端 G0-G8 门：

| 检查 | 实际结果 | 证据边界 |
| --- | --- | --- |
| Schema v2 解析 | 23 个 Profile；8 个 `native_mindspore`、15 个 `conditional` | 注册表结构，不是模型加载结果 |
| Python 回归 | `382 passed, 11 skipped` | Windows 平台限制导致的跳过项不代表板端通过 |
| Python 编译 | 相关 `.py` 全部通过 | 语法检查，不是运行时检查 |
| Shell 语法 | `bash -n` 全部通过 | 脚本语法，不执行 SSH/进程操作 |
| 候选矩阵 dry-run | `status=dry-run`，`formal_ports_untouched=true` | 不扫描报告、不连接服务 |
| 源码 allowlist | 132 条，缺失 0 条（`src/` 路径按仓库根解析） | 复制清单完整性 |
| 前端 | `npm test` 4/4；`npm run build` 通过 | 控制机静态资源构建 |
| 工作区差异 | `git diff --check` 通过 | 空白/补丁格式检查 |

Windows 上跳过的测试包括 POSIX 定时器、原生进程表、符号链接和 WSL fake-board 集成；
它们必须在相应 Linux/开发板环境重新执行。上述结果不改变任何 Profile 的逐板状态，
也不产生模型、tokenizer、NPU、ACL、MindSpore 或性能证据。

候选矩阵默认只扫描仓库内 `reports/mindspore-chat/` 的 canonical 报告根目录；本记录
引用的历史报告位于 `repro/` 复现包或旧编号文档，因此不会因默认扫描为空而被重新判定。
复核已复制的历史报告时，必须显式传入
`scripts/mindspore_candidate_matrix.py --reports-root <已核验目录>`，并保留报告的
Profile、SoC、工件哈希和端口身份对应关系。

## 0.1 🧭 2026-09-03 历史地址 G0 复核

该次记录中的 `192.168.11.14` 不是新硬件，而是原 Ascend310B4/8T 板的历史地址。
当前连接入口已回到 `192.168.1.90`；更早的 `192.168.8.178` 同样只是历史报告
provenance。IP 变化本身不产生新的性能批次。控制机当时通过免密 SSH 完成只读 G0 复核：

| 检查 | 实测结果 | 证据边界 |
| --- | --- | --- |
| SSH/系统身份 | `orangepiaipro`，Linux `5.10.0+`，`aarch64`，用户 `HwHiAiUser` | 当前地址连通性和系统身份，不是模型门 |
| NPU | `npu-smi 25.2.0`；`Ascend310B4`；`Health: Alarm` | Health Alarm 仅作诊断记录，不单独阻断 |
| CANN/ATC | `/usr/local/Ascend/ascend-toolkit`，工具链版本 `8.0.0` | 环境指纹，不代表 ATC 或推理通过 |
| Python/ACL | base Python `3.9.2`；`acl` 可导入；MindSpore `2.4.10`、MindNLP `0.4.1` 通过现有 user-site 导入 | dirty-base 环境检查；不安装或升级包 |
| 既有污染包 | `torch`、`torch_npu`、`torchaudio`（及 ONNX Runtime）已存在，未删除 | 仅记录环境污染，适配代码不导入 |
| 20T 对照 | `192.168.1.95` 本次仍无法建立 SSH/TCP/22 | 不对 20T 做可用性推断 |

该复核只更新当前连接地址和环境身份；Qwen1.5、TinyLlama、DeepSeek 的历史指标仍以
原报告地址和 SHA-256 为准，新候选的 G1-G8 必须在本地址建立独立时间戳报告。

## 0.2 🧪 Qwen2.5-0.5B 8T 诊断批次

在同一块 Ascend310B4/8T 板的历史地址 `192.168.11.14` 上，对
`qwen2.5-0.5b-mindspore` 做了最小、可回滚的 loader 诊断。该地址与旧的
`192.168.1.90`/`192.168.8.178` 只是 IP 别名，不产生新的硬件性能批次。完整记录见
[Qwen2.5 loader smoke](34-qwen25-0.5b-mindspore-8t-loader-smoke-20260903.md)。

| 项目 | 记录 |
| --- | --- |
| 固定 revision | `7ae557604adf67be50417f59c2c2f167def9a775` |
| 权重 | `988097824` bytes，SHA-256 `fdf756fa7fcbe7404d5c60e26bff1a0c8b8aa1f72ced49e7dd0210fe288fb7fe` |
| tokenizer | `7031645` bytes，SHA-256 `c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539` |
| config | `659` bytes，SHA-256 `18e18afcaccafade98daf13a54092927904649e1dd4eba8299ab717d5d94ff45` |
| loader 环境 | Python `3.9.2`、MindSpore `2.4.10`、MindNLP `0.4.1`、CANN `8.0.0`、`device_target=Ascend` |
| 诊断耗时 | 模型加载 `50.293 s`；放宽 placement 的生成 `12.009 s`；输出 `我是来自`（4 token） |
| 严格 placement | **阻断**：模型/参数没有可枚举的显式 Ascend placement；默认严格 worker 拒绝加载 |
| API/候选链 | 未启动 `8090`、`7867` 或 `7868`；正式 `8080 -> 7861 -> 7865` 未触碰 |
| 原始旁证 | 本批次 stdout/stderr 与 `npu-smi` 原始文件尚未同步，见记录中的 `evidence_path=not-recorded`；不可把这次诊断当作正式 NPU/API 通过 |

该诊断没有安装、升级或删除任何包。历史批次的 `CASE9_REQUIRE_PLACEMENT_EVIDENCE=0` 仅用于观察
是否能完成一次 context-only 生成；当前 memory gate 默认严格为 `1`，只有显式
`--context-only` 才能复现该诊断模式。它不能成为服务默认值，也不能绕过模型准入。板端进程
退出时还观察到既有 `DRV_LPM_FAULT 0x80E3A203`/资源释放诊断，故 G2-G8 不自动展开。

## 1. 🧾 板卡和环境指纹

| Board key | 当前地址 | SoC/算力 | 环境 | 备注 |
| --- | --- | --- | --- | --- |
| `board8t` | `192.168.1.90` | Ascend310B4 / 8T | 现有 `base`，MindSpore/MindNLP 版本以每批快照为准 | `.11.14` 与 `.178` 是同一块板的历史 IP；IP 变化不自动产生新性能结果 |
| `board20t` | `192.168.1.95` | Ascend310B1 / 20T | 现有 `base`，dirty-base | `.210` 是历史请求地址；不得把旧地址当作当前连接入口 |

执行前必须保存：

```text
environment/<board>/<run-id>/python.txt
environment/<board>/<run-id>/mindspore.txt
environment/<board>/<run-id>/mindnlp.txt
environment/<board>/<run-id>/cann.txt
environment/<board>/<run-id>/npu-smi-before.txt
environment/<board>/<run-id>/packages.txt
```

板端 shell 前缀固定为：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
# MindNLP 0.4.1 is installed in the board user's site-packages for this run.
# Keep it visible; use PYTHONNOUSERSITE=1 only for a separately labelled
# isolation diagnostic.
unset PYTHONNOUSERSITE
```

若隔离变量改变了既有服务的 import 结果，应记录为诊断，不安装新包或替换环境。

## 2. 📋 当前 Profile 状态

### 2.1 原生 MindSpore Profile

| Profile | `board8t` 当前状态 | `board20t` 当前状态 | 已知证据 | 质量/准入 |
| --- | --- | --- | --- | --- |
| `qwen1.5-0.5b-mindspore` | `blocked`；历史报表 9/9，按历史身份字段缺失记 8/9；当前 G0 已补齐但严格 placement 仍失败 | `experimental_dirty_base`；已有缺口机器批次 9/9 | Qwen1.5 `.90` 与 `.95` 的 JSON/SSE、长输出、稳定性和性能报告见 [docs/24](24-mindspore-chat-validation-record.md) 与复现包；当前 `.90` 对应的 G0 报告（历史地址 `.11.14`）和严格 placement 诊断已同步，B4 仍不可切换 | 中文人工复核和正式准入待完成 |
| `tinyllama-1.1b-mindspore` | `blocked`；历史机器门 8/9 | `blocked`；缺口机器门 8/9 | 32/48 token 长输出出现 `U+FFFD`；完整报告见 [docs/24](24-mindspore-chat-validation-record.md) | 中文质量不通过；英文结果单独保留 |
| `deepseek-r1-qwen-1.5b-mindspore` | `experimental_dirty_base`（机器门批次） | `blocked`（隔离机器门已记录） | 固定 FP16 revision、API、稳定性和性能报告见 [docs/26](26-deepseek-20t-validation-20260830.md) | 中文质量和正式准入未完成 |

以上是已有证据的摘要，不替代原始报告。`experimental_dirty_base` 只表示逐板技术门
已完成且可以继续实验，不能表示生产可用；身份门未完成的板卡必须是 `blocked`。

### 2.2 新增候选当前状态

| Profile | 8T 初始状态 | 20T 初始状态 | 首个动作 |
| --- | --- | --- | --- |
| `qwen2.5-0.5b-mindspore` | `blocked`；固定工件已核验，context-only 诊断因严格 placement 证据缺失被拒 | `not-run` | 先补齐可审计 placement/NPU 旁证；不能用诊断输出启动服务 |
| `qwen2.5-1.5b-mindspore` | `blocked`；七个文件已同步并通过 SHA-256，正确 CANN v2/v3b/v4 已加载并生成短文本；v4 加载 `53.068432 s`、连续生成 2 token `23.169714 s`；placement/API/长输出/稳定性/质量/性能未完成 | `not-run` | 首轮 SIGSEGV 是 PYTHONPATH 污染的历史诊断；详见 [8T 内存门记录](37-qwen25-1.5b-8t-memory-gate-20260904.md)，配置级预检见 [docs/36](36-qwen25-1.5b-8t-preflight-20260904.md) |
| `qwen3-0.6b-mindspore` | `blocked`；当前 MindNLP `0.4.1` 没有 Qwen3 loader | `not-run` | 不升级环境；待批准的兼容性分支另行记录 |
| `qwen3-1.7b-mindspore` | `blocked`；当前 MindNLP `0.4.1` 没有 Qwen3 loader | `not-run` | 不升级环境；待批准的兼容性分支另行记录 |
| `minicpm3-4b-mindspore` | `not-run`（不执行） | `not-run` | 仅用官方 FP16 Modelers 权重在 20T 预检 |

“不执行”仍必须保留为 `not-run`，不能写成“8T 不支持”而没有实际内存或 loader 证据。
Qwen3 的 `blocked` 是已执行的当前 loader 能力检查结论，不是硬件性能结论；Qwen2.5
0.5B 的 `blocked` 则同时保留了固定工件和诊断生成数据，但严格 placement/API 门仍未通过。
注册表同时为 MiniCPM3、MiniCPM4 和 MiniCPM5 保留 B4 行，是为了让双板矩阵/UI
保持完整；这些 B4 `not-run`/`blocked` 行不构成 8T 运行结果，也不授予下载、加载或
切换权限。MiniCPM3 的后续预检只在 B1/20T 进行，MiniCPM4/5 因 `conditional` 状态
在 B4、B1 均不可启动。

## 3. 🛡️ 条件候选状态

| Profile | 状态 | 阻断原因 | 是否允许下载/启动 |
| --- | --- | --- | --- |
| `openpangu-embedded-1b-conditional` | `blocked` | Torch/Torch-NPU、CANN 8.1、MindIE 路线 | 否 |
| `ee-model-1.5b-conditional` | `not-run` | 缺少当前 310B 可复核 loader 和部署证据 | 否 |
| `telechat-1b-conditional` | `blocked` | Transformers/PyTorch/custom code | 否 |
| `bitcpm-cann-1b-conditional` | `blocked` | Torch-NPU/MindSpeed 和专用量化运行时 | 否 |
| `minicpm5-1b-ascend-conditional` | `blocked` | vLLM、Torch-NPU、CANN 8.5/Python 3.11 | 否 |
| `minicpm4-0.5b-conditional` | `not-run` | 无当前 MindSpore 310B 证据 | 否 |
| `minimind3-64m-conditional` | `not-run` | Transformers/PyTorch，仅有 910B 证据 | 否 |
| `haidass-143m-conditional` | `not-run` | 原始预训练模型，不是已验证聊天模型 | 否 |
| `bloomz-560m-research-only` | `not-run` | 仅外部研究对照 | 否 |

条件条目可以显示在候选 UI，但 `case9-modelctl` 和 worker 必须拒绝加载。

## 4. 📝 每批次记录模板

新增候选执行时，复制以下表格到对应 `run-id` 小节，不覆盖历史记录：

| 字段 | 值 |
| --- | --- |
| Profile / board / SoC |  |
| run-id / UTC 时间 |  |
| 模型 revision / tokenizer revision |  |
| 权重、配置和 tokenizer SHA-256 |  |
| Python / MindSpore / MindNLP / CANN |  |
| worker PID / 服务端口 |  |
| G0 环境 | `passed` / `blocked` / `not-run` |
| G1 工件 | `passed` / `blocked` / `not-run` |
| G2 单 token NPU load | `passed` / `blocked` / `not-run` |
| G3 JSON/SSE/API | `passed` / `blocked` / `not-run` |
| G4 长输出 8/16/32/64 |  |
| G5 10 轮稳定性 |  |
| G6 中文/英文质量 |  |
| G7 性能 p50/p95/token/s |  |
| G8 网关/UI/切换回滚 |  |
| 原始报告目录 |  |
| `npu-smi` before/during/after |  |
| 失败原因或回滚结果 |  |
| 最终状态 |  |

推荐证据目录：

```text
repro/mindspore-chat-candidates/<run-id>/
  artifacts/<profile>/
  environment/<board>/
  reports/<board>/<profile>/
  logs/<board>/<profile>/
  bundle-manifest.json
  SHA256SUMS.txt
```

上面 `reports/<board>/<profile>/` 是板端产生报告时的源目录；同步到本地复现包后，
报告会统一落在 `profile-reports/<profile>/<soc>/` 命名空间（完整路径见下节），不应
把源目录路径直接当作 bundle 内路径。

### 矩阵扫描边界

候选矩阵工具默认使用仓库内的 `reports/mindspore-chat` 作为
`--reports-root`，只把该 canonical 根目录下的 `acceptance.json` 和邻近旁证纳入
当前扫描。`repro/` 下的历史包、板端同步包以及注册表中的 `report_paths` 不会被默认
递归发现，因此不能把历史报告自动理解为本轮通过。`--dry-run` 更不会扫描任何报告。
如需复核历史包，必须显式指定 `--reports-root <历史目录>`，并在输出和记录中标明
“历史批次”；历史结果不得与当前 canonical 矩阵合并或覆盖逐板状态。

```bash
python scripts/mindspore_candidate_matrix.py \
  --reports-root reports/mindspore-chat \
  --board both \
  --output reports/mindspore-chat/candidate-matrix.json
```

### 复现包同步边界

模型和报告同步由 `scripts/sync_mindspore_chat_repro_bundle.sh` 从
`configs/chat_model_profiles.json` 的 schema v2 动态生成**显式** allowlist。默认只
选择 `candidate_kind=native_mindspore` 的 8 个 Profile，并为每个 Profile 逐文件列出
权重、tokenizer、配置及注册表中已有的板卡报告路径；`conditional` 条目不会被下载或
复制。脚本不递归浏览 board home，也不传递 `rsync --delete`。

```bash
# 两块板、全部 8 个 native Profile（执行前先 dry-run 审阅路径）
bash scripts/sync_mindspore_chat_repro_bundle.sh --dry-run --board both
bash scripts/sync_mindspore_chat_repro_bundle.sh --board both

# 只同步一个 Profile 或一块板；可重复指定 --profile
bash scripts/sync_mindspore_chat_repro_bundle.sh --dry-run --board 8t \
  --profile qwen2.5-0.5b-mindspore
```

每个远端文件都经过 `.part -> Content-Length -> SHA-256 -> 原子改名`；本地已核验的
目标文件会复用，不会重复传输。板端尚未安装的模型或缺失的报告是可预期的部分状态：
脚本跳过该精确路径并在 `bundle-manifest.json` 的 `missing` 数组中记录板卡、SoC、
Profile、远端候选路径和目标路径；对应 `sources.<board>.status` 为 `partial`，不会被
误标为已同步。`--board 8t`/`--board 20t` 未选中的板卡会写成 `not-selected`，而
`--skip-board20` 写成 `skipped`。因此一次局部同步不会覆盖另一块板的 B4/B1 证据。

报告目标路径由注册表路径确定且与实际命中的远端候选解耦。每条报告在
`profile_plan.reports`（以及缺失报告的 `missing` 条目）中同时保留三个字段：
`registry_path` 是注册表原始路径，`remote_candidates` 是脚本允许尝试的精确远端
相对路径数组，`destination` 是最终 bundle 内的 canonical 位置。目标固定为：

```text
<board>/profile-reports/<profile>/<soc>/<normalized-report-rel>
```

其中 `normalized-report-rel` 去掉 `repro/<bundle>/` 和对应的 `board8t`/`board20t`
包装，但保留剩余的 `reports/`、`run/` 层级和文件名。例如
`repro/mindspore-chat-20260829/reports/board8t/qwen-full/acceptance.json` 会写入
`board8t/profile-reports/<profile>/Ascend310B4/qwen-full/acceptance.json`。脚本在生成
计划时检查规范化后的目标碰撞，并拒绝指向另一块 SoC 的报告路径；不会因为某个候选
路径先命中就改变 canonical 目标。

同步后先检查：

```bash
jq '.registry.selected_native_profiles, .profile_plan.artifact_count, .scope.missing_optional_entries, .sources' \
  repro/mindspore-chat-20260829/bundle-manifest.json
```

`missing` 只说明该批次没有找到对应文件，不是模型兼容性结论；恢复板卡或补齐工件后，
应使用新的 `--sync-run-id` 重跑并保留旧 manifest。

## 5. 🖥️ 状态和候选 UI 规则

候选 UI 必须列出所有 Profile，包括失败和未执行条目。展示字段只包含显示名、语言、
目标板、运行时、状态、质量标签和阻断原因。

- `artifact_verified` 到 `performance_recorded` 的技术门全部通过，并且使用共享 `base`：
  设置 `experimental_dirty_base`，允许显式实验切换。
- `quality_reviewed` 独立于技术切换；中文质量未完成显示“质量待审核”。
- `blocked`、`not-run` 和 `conditional` 不得启动 worker。
- `admitted` 只能由人工批准，不能由测试脚本或 UI 自动设置。代码层还会拒绝缺少
  `quality.reviewed=true`、明确 `human_review=approved`、8/10 人工通过计数或中文
  `passed` 标签的记录；机器质量计数不能冒充人工批准。当前注册表没有任何 Profile
  满足这条规则，因此正式 `admitted` 数量仍为 0。

正式入口保持 Qwen2.5 ACL：`8080 -> 7861 -> 7865`。候选入口为
`7868 -> 7867 -> 8090`，两者互斥。

## 6. ✅ 复核清单

每次追加结果前检查：

- [ ] 原始命令、PID、时间和板卡地址已保存；
- [ ] 模型 revision、文件大小和 SHA-256 已核对；
- [ ] SoC、CANN、MindSpore/MindNLP 与环境指纹已记录；
- [ ] JSON、SSE、长输出、稳定性、质量和性能未混写；
- [ ] 失败门保留为 `blocked`/`not-run`，没有 CPU 或云端回退；
- [ ] 8T/20T 结果没有合并排名；
- [ ] 正式端口和正式 Profile 未被候选测试改变；
- [ ] 文档状态与 `configs/chat_model_profiles.json` 一致。

## 7.1 2026-09-04 历史地址 G0 与工件复核

确认记录中的 `192.168.11.14` 是原来的 Ascend310B4/8T 板，不是新硬件；当前入口为
`.90`，而 `.11.14` 和 `.178` 只作为历史地址保留。通过正确的 conda `base` + CANN 环境、未设置 `PYTHONNOUSERSITE`
执行只读检查，得到 MindSpore `2.4.10`、MindNLP `0.4.1`、CANN `8.0.0`、Python
`3.9.2`、`npu-smi` 芯片 `310B4`。`base` 明确为 dirty-base，现有 Torch、Torch-NPU、
MindIE、vLLM、ONNX Runtime 等包未修改；适配器源码没有这些推理框架 import。

G0 原始 JSON（板端）和同步后的本地副本：

```text
/home/HwHiAiUser/case9-mindspore-chat/reports/mindspore-chat/ip-20260904/qwen1.5-environment.json
repro/mindspore-chat-20260904/reports/board8t/environment/qwen1.5-environment-20260904.json
```

环境检查的 `ok=false` 原因是所选 `qwen1.5-0.5b-mindspore` 在注册表中仍为
`blocked`，不是 Python/CANN/ACL 缺失；报告内的 `npu.ok=true`、MindSpore/MindNLP
导入和 `adapter_import_audit.ok=true` 均通过。对应 Qwen1.5 的 7 个模型/tokenizer/
配置文件在当前地址全部通过字节数和 SHA-256，报告为：

```text
/home/HwHiAiUser/case9-mindspore-chat/reports/mindspore-chat/ip-20260904/qwen1.5-artifacts.json
repro/mindspore-chat-20260904/reports/board8t/artifacts/qwen1.5-artifacts-20260904.json
```

该复核只补齐历史地址的环境与工件 provenance，不创建新的性能批次，也不解除
Qwen1.5 的 `blocked` 状态；严格身份、人工质量和准入仍按本账本的逐板门禁处理。

## 7.2 2026-09-04 Qwen1.5 严格 placement 诊断

为确认历史地址记录中的 `blocked` 原因，在同一块 Ascend310B4/8T 板上使用保留 CANN
`PYTHONPATH` 的 shell 执行了单 token 严格诊断：

```text
profile: qwen1.5-0.5b-mindspore
board: 192.168.11.14 / Ascend310B4
placement_gate_requested: 1
error: ProviderUnavailable: MindSpore model exposes no explicit Ascend placement evidence
exit-code: 1
generation: not-run (strict gate在加载后 fail-closed)
npu-after: Ascend310B4, 11817/15610 MB（进程内快照）；外部清理快照以文件为准
```

模型和七个工件已经完成校验，CANN `acl/te/hccl` 也实际导入；失败点是模型对象没有
提供可审计的 placement 属性，而不是 NPU 不可见或 tokenizer 缺失。provider 在
失败后报告 `ready=false`、`placement_status=unknown` 并完成清理，故不能以该诊断
生成聊天输出。原始证据已同步到：

```text
repro/mindspore-chat-20260904/reports/board8t/qwen1.5-strict-placement-20260904-rerun/
  report.json
  run.log
  exit-code.txt
  npu-after.txt
  SHA256SUMS.txt
```

`SHA256SUMS.txt` 中的关键摘要为：`report.json`
`4069213f7461a9ff33aae94608d167e09a8bb386f4e269db60f2bac2677bcca6`，
`run.log` `4efcda11fa7b69843c3427caac7d95f20cb38e1683e3bc343466cac7ee5fb8f3`，
`exit-code.txt` `4355a46b19d348dc2f57c046f8ef63d4538ebb936000f3c9ee954a27460dd865`。

该批次补充了阻断原因，但不创建性能、质量或 API 通过门；Qwen1.5 的 B4 行继续为
`blocked`，IP 从旧 `.90/.178` 变为 `.11.14` 只改变连接地址。

## 7.3 2026-09-04 Qwen2.5-0.5B 严格 placement 复测（历史地址）

在同一块 B4/8T 板的历史地址 `192.168.11.14` 上，对已锁定的 Qwen2.5-0.5B
工件执行了严格诊断（未传 `--context-only`）。现有 `base`、CANN `8.0.0` 和
MindSpore `2.4.10`/MindNLP `0.4.1` 保持不变，进程级 watchdog 设置为 420 秒、
`max_tokens=2`；未启动 8090、候选网关或正式端口。

```text
run_id: qwen2.5-0.5b-strict-placement-20260904-v2
exit-code: 1
status: failed
placement_gate_requested: 1
error: ProviderUnavailable: MindSpore model exposes no explicit Ascend placement evidence
generation: not-run
http_service_started: false
expected/actual SoC: Ascend310B4 / Ascend310B4
```

`acl`、`te`、`hccl` 均实际导入，六个声明工件通过大小和 SHA-256。`npu-smi` 运行中
记录了设备内存 `5705 -> 12520/15610 MB`，但这只是活动旁证，不能替代参数级
placement 证据。报告、日志、前后快照和远端 SHA256 清单已同步至：

```text
repro/mindspore-chat-20260904/reports/board8t/
  qwen2.5-0.5b-strict-placement-20260904-v2/
```

`report.json` SHA-256：`9f9792838fe149d4100f252a8a6ccee21ca7ab41beb1fe59aeb25b7d1ce359c9`。
该证据将 Qwen2.5-0.5B 的当前 B4 行保持为 `blocked`；API、长输出、稳定性、质量和
性能门均为 `not-run`。

## 7.4 2026-09-04 历史地址 TinyLlama/DeepSeek G0 与 G1 复核

为把 IP 变更与已有模型结果对应起来，在同一块 Ascend310B4/8T 板的历史地址
`192.168.11.14` 上，对已有 TinyLlama 和 DeepSeek Profile 只执行了环境读取与工件
哈希校验。没有加载模型、创建服务、启动候选网关，也没有重复性能或质量测试；旧 `.90`
和 `.178` 报告仍是同一块板的历史采集 provenance。

### TinyLlama

固定 revision `fe8a4ea1ffedaf415f4da2f062534de366a451e6` 的 7 个声明文件全部通过
字节数和 SHA-256（`artifact_verified=true`，`verified=7/7`）。当前地址报告：

```text
repro/mindspore-chat-20260904/reports/board8t/artifacts/
  tinyllama-g0g1-20260904T065034Z-artifacts.json
repro/mindspore-chat-20260904/reports/board8t/environment/
  tinyllama-g0g1-20260904T065034Z-environment.json
```

工件报告 SHA-256 为
`a3d3938c092c28ff46088c82dabf5f63da244f9ef8bdf2331ccab520512b454a`；环境报告 SHA-256 为
`d26b7f2ca6b36791c5941f5cc15d10b974fa433decf1e37ff3828d2c65718fc5`。环境 JSON 的
`ok=false` 仅因为 Profile 既有状态为 `blocked`；其中 `npu.chip=310B4`、CANN
`8.0.0`、MindSpore `2.4.10`、MindNLP `0.4.1`、Python `3.9.2` 和适配器导入审计均
有记录。既有 TinyLlama 长输出/中文质量失败仍是阻断原因，本复核不改变状态。

### DeepSeek

固定 revision `0a28897fe71fdd30de350b667ae588601a85990f` 的 5 个声明文件全部通过
字节数和 SHA-256（`artifact_verified=true`，`verified=5/5`）。首次报告因 Profile 的
主目标板是 20T/B1，元数据会把板卡写成 `.95`；该文件不作为 B4 证据。随后仅在板端
临时 registry 中把观测目标覆盖为 `192.168.11.14`/`Ascend310B4`/`8T`，正式注册表
没有修改，重跑得到以下报告。模型文件使用板端既有 hard-link 复现路径
`/home/HwHiAiUser/case9-deepseek-8t-experiment/fp16/model`（未复制或改写权重）：

```text
repro/mindspore-chat-20260904/reports/board8t/artifacts/
  deepseek-g0g1-b4-20260904T065856Z-artifacts.json
repro/mindspore-chat-20260904/reports/board8t/environment/
  deepseek-g0g1-b4-20260904T065856Z-environment.json
repro/mindspore-chat-20260904/reports/board8t/environment/
  deepseek-g0g1-b4-20260904T065856Z-registry.json
```

工件报告 SHA-256 为
`2f0dc0d59e7e7e92689ea8967ac0fb7b2364db4abc5c66b71fe5166b797db625`；环境报告 SHA-256 为
`1019436924d606c5f288ebaebf803face0e8a2e1c9517694a8c7495480f8f2ee`；临时 registry
SHA-256 为 `8503186384ad9292b0ce2c23bb0dc24b7ed7e300649ae987688b4d4d9e672933`。环境
`ok=true`，并记录实际 `npu.chip=310B4`、CANN `8.0.0`、MindSpore `2.4.10`、MindNLP
`0.4.1`、Python `3.9.2` 及 dirty-base 禁止包清单。该 G0/G1 复核只补充当前 IP 的
provenance；DeepSeek 的历史 8T 机器门、性能和 `experimental_dirty_base` 状态不因它
改变，G2-G8 仍按原批次记录。

## 7.5 2026-09-04 历史地址 MindNLP loader 能力复核

为继续清理未完成的候选移植，在同一块 B4/8T 板的历史地址 `192.168.11.14` 上执行了只读
MindNLP 类导出探针。探针没有下载或加载权重，也没有启动服务；完整输出、`npu-smi`
和 CANN 版本快照见 [当前 loader 能力记录](38-mindnlp-loader-capability-20260904.md)，
本地报告目录为：

```text
repro/mindspore-chat-20260904/reports/board8t/environment/
  loader-capability-20260904T072000Z/
```

观察到 `Qwen2Config/Qwen2ForCausalLM`、`MiniCPM3Config/MiniCPM3ForCausalLM/MiniCPM3Tokenizer`
和 `DeepseekV2*` 类；没有 `qwen3` 模块或 `Qwen3ForCausalLM`。因此 Qwen3 两个 Profile
的 B4 状态仍为 `blocked`，MiniCPM3 只能记为 loader 可见、但工件/20T 运行未执行的
`not-run`。类导出不构成 NPU placement、API、质量或性能证据，不能改变正式入口。

## 7.6 2026-09-04 Qwen2.5-0.5B context-only 短生成复核

在上述严格 placement 复测后，为区分“loader 能够分配并执行”与“Case9 可以准入”，
使用同一块 B4/8T 板、同一组固定工件和同一 CANN 环境显式运行了
`--context-only` 诊断。该模式只允许一次两 token 生成，绝不绕过服务准入。

```text
run_id: qwen25-05-context-only-clean-20260904T073649Z
board: 192.168.11.14 / Ascend310B4 / 8T
MindSpore/MindNLP: 2.4.10 / 0.4.1
load: 48.649923 s
generation: 2 tokens / 15.225400 s / finish_reason=length
text: 我是Q
npu-smi: Ascend310B4 before and after; after_npu_gate=passed
exit-code: 3 (diagnostic_passed; not a service pass)
```

原始目录已按 allowlist 同步到：

```text
repro/mindspore-chat-20260904/reports/board8t/
  qwen2.5-0.5b-mindspore/
  qwen25-05-context-only-clean-20260904T073649Z/
```

`report.json` SHA-256 为
`16e5bf950a25f92766ea6d8d7f28062ba0fba507373b9b3d25ea9bbf25a0afbd`，`run.log` SHA-256
为 `39d2b0cb04eef52d63c6d084eee877ebb2e2cf1571c6f109ef213104fe4ae2a7`。报告同时记录
设备内存约从 `7113 MB` 增至 `14900 MB`，但没有参数级 placement metadata。故该结果
只能补充 NPU 活动和资源观察，不能把 Profile 从 `blocked` 改为可切换，也不能开始
JSON/SSE、长输出、稳定性、中文质量或性能门。详细说明见
[Qwen2.5 0.5B context-only 记录](39-qwen25-05b-context-only-20260904.md)。

## 7.7 2026-09-04 历史地址 8T 设备健康诊断与暂停边界

在完成上述只读复核后，对同一块 `Ascend310B4/8T`（历史地址
`192.168.11.14`）再次执行了只读设备快照。快照显示当时没有 Case9 worker、候选网关或
正式入口监听；`npu-smi` 仍识别 `310B4`，但设备内存约为 `13598/15610 MB`，健康状态为
`Alarm`。`dmesg` 在约五分钟间隔持续出现 `DRV_LPM_FAULT`，事件号为
`0x80E3A203`（最近一条为 UTC `2026-09-04T08:18:02Z`）。

原始快照只读保存于：

```text
repro/mindspore-chat-20260904/reports/board8t/environment/
  lpm-fault-snapshot-20260904T081808Z/board-status.txt
```

文件大小为 `6476` bytes，SHA-256 为
`0396b563b203895cdff19cd4de7b8cdd7ab9618e949c58d185c1e94a772c1b10`；同目录的
`SHA256SUMS.txt` 是该快照的校验旁证。`npu-smi proc`/`proc-mem` 在此镜像中不支持，
因此不能把设备内存归因到某个进程，也不能仅凭这次快照判断故障根因。

这不是把 `Health: Alarm` 单独当作模型失败门，而是当前设备存在可重复的 LPM 故障和
未回落的 NPU 内存占用，继续加载 1.5B 模型或执行长压测会增加设备重置/崩溃风险。因而
本轮暂不启动服务、不追加重模型加载；Qwen2.5-0.5B/1.5B 的 placement、API、长输出、
稳定性、质量和性能门继续保持 `blocked`/`not-run`。待设备由用户重启或确认恢复后，才可
在新 UTC 批次中重新执行受控 smoke；不得用本快照伪造成功结果，也不得终止系统守护进程。

## 7.8 2026-09-04 历史地址 MindNLP loader 能力复核

为继续整理尚未移植的候选模型，在同一块 B4/8T 板的历史地址
`192.168.11.14` 上执行了只读 `probe_mindnlp_loaders.py`。命令显式激活
`base` 和 CANN 8.0.0，并取消 `PYTHONNOUSERSITE`；脚本只导入
`mindnlp.transformers`，不下载权重、不实例化模型、不创建服务，也不改变环境。

结果如下：

| 项目 | 观测 |
| --- | --- |
| Python | `/usr/local/miniconda3/bin/python`，3.9.2 |
| MindSpore | 2.4.10 |
| MindNLP | 包版本字段为空，但模块导入成功 |
| 可见架构 | Qwen2/Qwen2Moe、Llama、DeepseekV2、MiniCPM3 |
| Qwen3 | 没有 `Qwen3*` loader 或配置映射 |
| 退出码 | 0（脚本结果有效） |

原始文件已从板端按单文件 SHA-256 校验同步到：

```text
repro/mindspore-chat-20260904/reports/board8t/
  loader-capability-20260904T082903Z/
```

其中 `probe.json` 为 `5589` bytes，SHA-256 为
`8cda8f579ea03381574d755840c310e36989412ab6d9fbb74872b7cc6acbb0e5`；
同目录 `SHA256SUMS.txt`（SHA-256
`5a5aec7f2a5df35b54a9a21cd7f03bbfa9049fb64aa426f289cc208733a36cef`）记录完整
旁证。该结果只说明当前 loader 能力：Qwen3 继续 `blocked`，MiniCPM3 仍需
20T/B1 的工件、内存和真实 NPU 验收；不能把类导出当作模型加载或性能通过。

## 7.9 2026-09-04 Qwen2.5-1.5B 活动目录工件补齐

在不启动模型的前提下，先补齐当前 Case9 活动根目录中的 Qwen2.5-1.5B 权重。源文件
来自板端已经完成校验的隔离目录，目标文件使用 `rsync --partial --append-verify
--checksum` 写入；其余 tokenizer、配置和生成元数据未覆盖。板端随后执行
`verify_mindspore_profile_artifacts.py`，逐项核对 7 个文件，结果为 `artifact_verified=true`：

| 文件 | 大小（bytes） | SHA-256 |
| --- | ---: | --- |
| `model.safetensors` | 3,087,467,144 | `dd924a11b4c220f385b51ffa522daea7c9f3d850e31b162bb5661df483c6d3ee` |
| 其余 6 个 tokenizer/config 文件 | 与注册表一致 | 见板端报告 |

板端报告路径为：

```text
/home/HwHiAiUser/case9-mindspore-chat/reports/mindspore-chat/ip-20260904/
  qwen25-15-artifact-verify/report.json
```

该报告已同步到本地复现目录
`repro/mindspore-chat-20260904/reports/board8t/qwen25-15-artifact-verify/report.json`，
大小 `3943` bytes，SHA-256 为
`a934b4974d5af27da52006963f89fd8f3611644b72f01e9f72d921261bc9017e`。脚本因 Profile
当前状态为 `blocked` 返回进程码 `1`，这是预期的准入拒绝；报告内部的 7/7 工件结果为
通过，且没有模型加载、NPU 推理或 API 证据。placement、长输出、稳定性、质量和性能
门仍保持未完成。

## 7.10 2026-09-04 历史地址 8T 健康快照（继续暂停重负载）

为确认地址变更后的设备状态，在同一块原 `Ascend310B4/8T` 板（当时使用历史地址
`192.168.11.14`）再次执行了只读快照。该快照不是新硬件结果，也没有启动 Case9 或
模型服务。`npu-smi` 仍报告 `Health: Alarm`，设备内存约为 `14954/15610 MB`；快照期间
没有 Case9 进程、候选/正式端口或已识别的聊天 worker。`dmesg` 尾部继续出现
`DRV_LPM_FAULT`，事件号 `0x80E3A203`，最近记录延续到本地时间约 `16:48`
（UTC `2026-09-04T08:48Z`）。

本次原始证据保存在：

```text
repro/mindspore-chat-20260904/reports/board8t/environment/
  lpm-fault-snapshot-20260904T085113Z/
```

关键文件和哈希如下：

| 文件 | SHA-256 |
| --- | --- |
| `npu-smi.txt` | `bf860c8a538c60d4763d365f1548db277cd98773d205d08e31e345c5dcf375f7` |
| `dmesg-tail.txt` | `76a11549fd9d71050985db550e1e90aebc976f4d4407d557f4a981dd7b1217c8` |
| `processes-ports.txt` | `ccaa4181f84570623b3ce73d8950e4657a65354a0b6c5f6aefdc279b2e2ace7a` |
| `SHA256SUMS.txt` | `8bcc30917ec0970842fbe02697083997af3045d80fd56140a1d4c4419999e7f7` |

`npu-smi proc` 和 `proc-mem` 在当前镜像中仍不支持，不能据此将设备内存归因到某个
进程。由于 LPM 故障和高水位内存均可重复观察，本轮继续暂停 1.5B/4B 及其他重负载
模型的加载、转换和压测；不得终止系统守护进程。待用户重启或确认设备恢复后，才重新
采集基线并开启新的受控测试批次。该快照不改变任何 Profile 的 `blocked`、`not-run` 或
`experimental_dirty_base` 状态。

## 7.11 2026-09-04 重启后的受控复测与 1.5B 内存失败

用户重启的是原来的同一块 `Ascend310B4/8T` 板；该历史批次使用地址 `192.168.11.14`，
当前连接入口为 `192.168.1.90`，不是新硬件批次。重启时间为本地 `2026-09-04 17:52:25`。重启后先执行了只读基线检查，随后
只进行了两个 `context-only`、单请求诊断，没有启动候选 worker、网关或正式入口，也没有
安装、升级或删除任何包。

### Qwen2.5-0.5B：短诊断可执行，但仍不可准入

批次 `20260904T095732Z-qwen25-05-reboot` 在正确 CANN 环境中加载固定的七个工件，完成
一次单 token 生成：

| 项目 | 实测值 |
| --- | --- |
| 模型加载 | `48.507482 s` |
| 生成 | `13.207156 s`，1 token |
| prompt / 文本 | `36` tokens / `我是` |
| finish reason | `length` |
| NPU | 约 `4322 -> 13178 / 15610 MB`（进程内快照） |
| 诊断状态 | `diagnostic_passed`，不是服务验收 |

原始报告保存在
`repro/mindspore-chat-20260904/reports/board8t/qwen2.5-0.5b-mindspore/20260904T095732Z-qwen25-05-reboot/report.json`，
大小 `37015` bytes，SHA-256 为
`2ea5ad7a51fdb9b19bd488c0ca8e1a86bbae9f1bd9d67fed80896361c480538b`。该目录只有诊断报告，
没有 HTTP、JSON/SSE、长输出、稳定性、中文质量或性能证据；Profile 仍为 `blocked`。

### Qwen2.5-1.5B：模型加载通过，首次生成发生真实分配失败

批次 `20260904T100745Z-qwen25-15-reboot` 保留了 `set_env.sh` 注入的 CANN 路径，且
`acl`、`te`、`hccl` 均实际导入，SoC 仍为 `Ascend310B4`，七个模型工件全部校验通过。
模型加载阶段报告 `57.870395 s` 并返回 `model_load.status=passed`，但第一次生成在
分配 `481482240` bytes 时失败：

```text
ProviderUnavailable: MindSpore generation failed: Alloc failed, size:481482240
mindspore/ops/kernel/ascend/pyboost/aclnn_utils.h:184 MemBlock
```

NPU 记账从约 `7703/15610 MB` 增至 `14993/15610 MB`，进程 RSS 峰值约
`9583710208` bytes；报告结束时 `mem_available` 约 `1933279232` bytes，退出码为 `1`。
报告的 `placement_status=context_only`、`promotion_eligible=false`，没有启动 HTTP 服务。
这次是保留正确 CANN 路径后的真实运行期内存分配失败，不再归因于先前的 `PYTHONPATH`
污染；因此 1.5B 在当前 8T 运行配置下继续 `blocked`，不能由 v2/v3b/v4 的极短诊断
提升为聊天服务。

原始文件和校验清单如下：

```text
repro/mindspore-chat-20260904/reports/board8t/qwen2.5-1.5b-mindspore/
  20260904T100745Z-qwen25-15-reboot/report.json
  20260904T100745Z-qwen25-15-reboot/run.log
  20260904T100745Z-qwen25-15-reboot/exit-code.txt
```

| 文件 | bytes | SHA-256 |
| --- | ---: | --- |
| `report.json` | `38145` | `03d16bb1903288bbdc206c99255fdf0f1cd795186a52898acabae0805a0bc9e7` |
| `run.log` | `36785` | `bf3123a48b7f494919ade38722c1510da8e40a3e922fe8a31475e49d11fb0d4b` |
| `exit-code.txt` | `2` | `4355a46b19d348dc2f57c046f8ef63d4538ebb936000f3c9ee954a27460dd865` |

这里的 `exit-code.txt` 内容为 `1`；文件大小为 2 bytes（含换行）。远端 shell 的返回
值不是模型结果，验收以报告内的 `status=failed` 和进程退出码为准。

### 重启后设备状态和暂停边界

失败进程退出后没有 Case9 进程或 `8080/8090/7861/7867/7865` 监听。包含完整进程、端口、
资源、dmesg 和输入工件旁证的只读跟进快照位于：

```text
repro/mindspore-chat-20260904/reports/board8t/environment/
  post-qwen25-15-reboot-20260904T101948Z/
```

该快照的 `npu-smi-info.txt` 为 `3005/15610 MB`、`Health: Alarm`，文件 SHA-256 为
`2053610fed18fcd02a7be89a508db70bddd2c1483cccfd0d5e6e93dc781b02d3`。同一快照的
`dmesg-lpm-fault-tail.txt` SHA-256 为
`0618666c423440c503570a9c2804392245fd74e3757f6133eb11dadc60627687`，仍能看到周期性
`DRV_LPM_FAULT 0x80E3A203`；这不是单独的模型失败门，但与本次内存分配失败一起构成
继续重负载测试的风险证据。后续若要测试 1.5B、Qwen3 或 MiniCPM3，必须先重新采集
干净基线并另开时间戳批次；本批次不再重复加载重模型。20T `.95` 仍不可达，正式链路
`8080 -> 7861 -> 7865` 保持不变。

快照目录内的 `snapshot-final-manifest.txt` SHA-256 为
`1e721fa082f5b743bfbd7e3ace61d14142c78446d6b3d07cd7f8cb8af013dc65`，用于复核快照文件
最终 stat 和哈希；它不改变失败报告的状态或门禁结论。

## 7.12 2026-09-05 canonical endpoint bookkeeping

用户确认当前仍是同一块 Ascend310B4/8T 开发板，连接入口改回
`192.168.1.90`。`192.168.11.14` 和 `192.168.8.178` 现在只作为历史别名；它们对应的
环境、工件、故障和性能报告继续按原采集地址引用。此次更新没有连接开发板、启动服务、
下载模型或执行 NPU/性能测试，因此不创建新的 `run_id`，也不改变任何 Profile 的
`blocked`、`not-run` 或 `experimental_dirty_base` 状态。

后续严格 8T 测试必须以 `.90` 为目标，并从 G0 环境快照开始；执行顺序、停止条件和
证据格式见 [8T 严格测试计划](40-8t-mindspore-llm-strict-test-plan.md) 与
[当前验证账本](41-8t-mindspore-llm-strict-validation-record.md)。

## 7.13 2026-09-06 reboot G0 recheck

同一块 Ascend310B4/8T 板在地址 `192.168.1.90` 重启后，按严格命令前缀重新执行了
环境检查。检查报告保存在板端：

```text
/home/HwHiAiUser/case9-mindspore-chat/reports/mindspore-chat/strict-g0/
  resume-g0-20260906T012557Z/environment.json
```

`environment.json` SHA-256 为
`05448e0f2fb89a4a7e25ce74dd1a365056795570dc68b6d62a2b3f5fd2093013`，检查退出码为
`2`。`npu-smi` 仍识别 `Ascend310B4`，但 `PYTHONNOUSERSITE=1` 下实际 conda Python
为 `3.9.2`、MindSpore 为 `2.4.0`，`mindnlp` 不可导入；MindSpore 同时报告与 Ascend
软件包 `7.6` 不匹配（工具包根目录报告 `8.0.0`）。共享 `base` 中原有 Torch、Torch-NPU、
Torchaudio、Transformers、ONNX Runtime、vLLM、MindIE 和 MindTorch 只作为污染项记录，
没有安装、升级或删除任何包。

因此本次 G0 为 `blocked`，不是模型加载失败也不是质量结论；Qwen1.5、TinyLlama、
DeepSeek 以及新候选均不得在当前严格环境中启动。历史用户 site 环境产生的报告仍保留，
但不能与本次严格边界混用。只有在获得明确批准并准备好匹配的 MindSpore/CANN 环境后，
才可重新执行 G0，再进入单 token、API、长输出、稳定性、质量和性能门。
