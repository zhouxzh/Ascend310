# 案例 N 测试问题记录

> 本文件是案例 N 的唯一测试问题台账。每个在本地、板端、服务、模型或 UI 测试中出现的问题，都必须在这里登记，并在对应的部署/验证文档中链接本条目，而不是把失败原因散落在多个文件里。

## 使用约定

- 按该 case 的 docs 编号规范使用 `samples/caseN/docs/NN-troubleshooting.md`；没有编号约定时用 `docs/troubleshooting.md`。创建后在 `docs/README.md` 或该 case 文档索引登记。
- 问题 ID 固定为 `CASE<N>-ISS-YYYYMMDD-NN`，同一天从 `01` 递增。
- 状态只能是 `open`、`mitigated`、`resolved`、`unverified`、`superseded`。
- 严重级别只能是 `blocker`、`major`、`minor`、`observation`。
- 先记录原始失败，再写分析；分析和结论不能覆盖原始输出。
- 8T 与 20T 的问题分开记录，不合并证据，不把一块板的结果外推到另一块板。
- 设备离线、未开机、网络不可达是环境问题，不是 310B `unsupported`。
- 根因未确认时写 `unknown` 或 `hypothesis`，不要写成“已修复”。
- 问题修复后保留条目，更新状态、修复和回归结果；禁止删除失败历史。
- 原始日志、截图和报告放忽略目录或外部证据位置，文档记录路径、大小和 SHA-256；不要把大文件提交到 Git。

## 测试环境快照

| 字段 | 值 |
| --- | --- |
| 测试日期 | YYYY-MM-DD |
| 开发板 | `<board label / hostname / IP>` |
| SoC / 档位 | `<Ascend310B1 / 20T>` 或 `<Ascend310B4 / 8T>` |
| CANN / 驱动 | `<version / version>` |
| Python / 框架 | `<version / version>` |
| 模型 / revision | `<model / revision>` |
| 代码提交 | `<git commit>` |
| 输入与命令 | `<prompt / script / args>` |
| 原始证据 | `<path / size / sha256>` |

## 问题索引

| ID | 状态 | 级别 | 首次发现 | 问题摘要 | 回归结果 |
| --- | --- | --- | --- | --- | --- |
| `CASE<N>-ISS-YYYYMMDD-01` | `open` | `major` | YYYY-MM-DD | `<一句话描述>` | `<未回归 / 通过 / 失败>` |

## CASE<N>-ISS-YYYYMMDD-01：<问题标题>

### 状态与影响

- 状态：`open`
- 级别：`major`
- 影响对象：`<device / CANN / ATC / model-load / input-contract / service / UI / performance / test-harness>`
- 影响范围：`<只影响此输入 / 此模型 / 此档位 / 此服务>`
- 是否阻塞发布：`<是 / 否 / 待确认>`

### 预期行为

`<测试本来应该看到什么，写可观察结果。>`

### 实际症状

`<实际观察到的短描述，以及最早出现时间。>`

### 复现步骤

1. `<cwd、环境激活、前置条件>`
2. `<精确命令或 UI 操作>`
3. `<输入、参数、超时>`
4. `<观察点>`

### 原始证据

```text
<保留原始错误、HTTP 状态、退出码、关键日志；不要只写“失败了”。>
```

落盘证据：

- 路径：`<ignored run path>`
- 大小：`<bytes>`
- SHA-256：`<digest>`
- 相关截图/报告：`<path>`

### 判定层级

`<device / driver / CANN / ATC / model-load / contract / task-quality / performance / UI / test-harness>`

为什么是这个层级：`<用原始证据说明，不靠猜测。>`

### 根因状态

- 根因结论：`unknown` / `hypothesis` / `confirmed`
- 结论说明：`<为什么；如果是假设，明确写尚未证实。>`

### 临时处理与修复

- 临时处理：`<限流、加等待、重启单个服务、保持 fail-closed 等>`
- 代码/配置修复：`<文件、提交或配置项>`
- 没有采用的方案及原因：`<避免把未验证方案写成结论>`

### 回归验证

| 检查 | 修复前 | 修复后 | 证据 |
| --- | --- | --- | --- |
| `<复现命令>` | `<fail>` | `<pass / fail>` | `<path>` |
| `<相关服务/数值/质量>` | `<result>` | `<result>` | `<path>` |

回归结论：`<resolved / mitigated / still failing / unverified>`

### 剩余边界

`<仍未验证的 8T、并发、长时间稳定性、模型质量、性能统计等。>`

### 关联文档

- 部署：`<link>`
- 验证与限制：`<link>`
- API/服务：`<link>`
- 原始报告：`<path>`
