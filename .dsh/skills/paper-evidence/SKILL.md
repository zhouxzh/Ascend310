---
name: paper-evidence
description: 论文中的数字、表格与结论必须绑定到可追溯证据。当要写或改 Results/Discussion/Abstract、新增 claim、把实验结果写进正文、核对表格与正文数字、判断某个结论能否写、或处理 negative/failed 结果时使用；也用于区分 confirmatory、exploratory 与 validation-only 证据。
license: MIT
compatibility: 规则本身不需要额外依赖；配套的 paper_audit 工具用于机械核对 locator 是否存在。
metadata:
  version: "1.0"
  skill-author: local
---

# Paper Evidence

本 skill 是本地论文工作区的证据主门。完成证据冻结后，英文稿的起草或完整审阅交给 `scientific-writing`，纯语言润色交给 `nature-polishing`；后两者不得绕过本证据门新增数字或结论。

论文里每一个数字都必须能回答三个问题：**来自哪个证据文件、属于哪个协议、适用范围到哪里为止。** 回答不了，就不要写进正文。

## 何时使用

- 写或修改 Abstract、Results、Discussion、Conclusion 中的任何数字或比较结论。
- 新增、修改或判定一条 claim。
- 把远程实验结果搬进论文仓库。
- 处理 negative、failed、interrupted、coverage-gate-failed 的结果。
- 读者（或自己）问“这个数能不能写”。

## 硬规则

1. **数字 → 文件 → 协议**。正文数字必须指向 `evidence/` 下的紧凑表（或 claim map 中登记的仓库相对路径），并注明协议名（例如 `canonical_v2`、`local-cluster`、`ocra_v1`）。
2. **不同协议不混池**。不得把两个协议的结果平均、拼接或互相“补足”。同一论文内出现多个协议时，分表报告并写明各自边界。
3. **日志不是证据**。终端输出、训练日志、临时脚本打印都不能作为论文数字来源；必须来自保存下来的结果表或 manifest。
4. **validation-only 不等于 held-out**。验证集上选出的结果只能写成筛选或描述，不能写成确认性结论。
5. **禁止 best-horizon 择报**。预先声明的所有 horizon 都要报；不能只挑最好的那个。
6. **negative 结果必须保留**。失败、退化、覆盖门未过的坐标要写进正文或附录，不得删除，也不得用“整体平均”掩盖。
7. **claim 状态要诚实**。`pending` 只有在证据满足预先声明的阈值后才能改为 `supported`；证据不足写 `pending`，被证伪写 `invalidated`。
8. **不做等价性推断**。不显著不等于等价；非劣性必须来自预先声明的上界。
9. **不编造**。不虚构 DOI、数字、分母、随机种子、作者或声明。缺什么就标记缺什么。

## 工作流

1. 先列 claim：一句话、可证伪、指明适用对象与范围。
2. 为每条 claim 找证据文件；找不到就先补证据，不要先写正文。
3. 在 `audit/claim_evidence_map.md` 登记 locator；本地路径用 `evidence/...`，远程路径写 `host:path`。
4. 写正文，数字与 claim 一一对应。
5. 草稿阶段运行 `paper_audit mode=draft`；投稿前必须运行 `paper_audit mode=submission`，此时缺失 register、`pending`、`invalidated` 和无法离线核验的远程 locator 都阻断。
6. 人工打开证据文件核对数字本身——工具只证明“文件在”，不证明“数字对”。

## 输出格式

给用户的 claim 摘要用表：

| Claim | 证据 locator | 协议/范围 | 状态 |
| --- | --- | --- | --- |
| <一句话> | `evidence/<file>` | <协议，适用对象> | pending / supported / invalidated |

## 与工具的分工

- `paper_audit mode=draft|submission` 检查：claim map 文件、远程 locator 状态、pending/invalidated、正文引用与 `.bib` 是否闭合、PDF 是否过期；提交模式把未闭合证据设为 error。
- 本 skill 负责：这个数字**该不该**写、写在哪个范围里、状态该标什么。
- 工具不能替代人工核验；它只能把“文件不见了”这类错误提前抓出来。

## 禁止清单

- 用未登记来源的数字。
- 把探索性筛选写成确认性结果。
- 用测试集调阈值后再报告同一测试集。
- 删除不利 seed、origin 或坐标。
- 把“平均 RMSE 更低”当成跨机制成功的证据。
