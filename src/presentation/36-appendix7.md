---
marp: true
theme: ascend310
paginate: true
size: 16:9
author: "周贤中"
email: "zhouxzh@gdut.edu.cn"
header: "《昇腾310B实战》教材配套演示"
footer: "附录 7：CANN 升级"
---

<!-- _class: cover -->

# 附录 7：CANN 升级

<div class="byline">周贤中 · zhouxzh@gdut.edu.cn</div>

## 从版本核对到可回退的升级流程


对应教材：`src/appendix/appendix7.md`

<!--
授课提示：用一句话说明本讲要解决的工程问题，再核对教材和样例路径。
-->

---

<!-- _class: center -->

## 本讲目标与路线

- 查清固件、驱动、CANN 的版本层次
- 使用官方配套查询确认组合
- 按门禁准备并执行升级
- 用统一验收与回退清单收尾

> 本讲主线：版本层次 → 配套查询 → 门禁 → 升级 → 验收

<div class="source">教材：<a href="../appendix/appendix7.md"><code>src/appendix/appendix7.md</code></a></div>

<!--
先给学生路线图；后续每页都对应这里的一项，最后回到小结。
-->
---

<!-- _class: section -->

# 01

## 版本层次与配套查询
---

<!-- _class: center -->

## 本附录解决的问题

- 本附录按教程顺序组织两条独立升级路线：
- 两条路线共用版本采集、安装包校验、回滚材料和验收原则，但硬件、内核、驱动、固件、CANN 包及测试结果不可混用。Atlas 的 HDMI/DRM 未恢复时，升级不算完成。
- 本附录不是“看到新版本就升级”的建议。若现有案例已经通过了模型转换、ACL 烟测和长稳测试，升级应被视为一次新的变更实验；只有在回归证据完整后，才可以把新环境写入案例的默认配置。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix7.md"><code>src/appendix/appendix7.md</code></a> · 本附录解决的问题</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 教程路线与已验证组合

- 先选择路线，再执行后文的共用准备、专项步骤和统一验收。不要把香橙派的 B309/5.10.0+ 条件与 Atlas 的 7.8.0.5.216/6.6 内核条件拼接。
- 路线：已验证基线
- A：香橙派 AI Pro 8T：Ubuntu 22.04.3，20241128，310B4，驱动 23.0，B309，CANN 7.0

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix7.md"><code>src/appendix/appendix7.md</code></a> · 教程路线与已验证组合</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 软件栈中的版本层次

- 目标硬件的配套页面列出该驱动、固件和 CANN 组合；
- 当前操作系统、架构和 Python 环境满足安装要求；
- 代表性模型或 API 在板端通过了运行时和任务级验证。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix7.md"><code>src/appendix/appendix7.md</code></a> · 软件栈中的版本层次</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 官方配套查询的使用方法

- 本节聚焦：官方配套查询的使用方法 的输入、操作与证据边界。
- 先把输入、版本与命令固定下来，再判断结果是否可复现。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix7.md"><code>src/appendix/appendix7.md</code></a> · 官方配套查询的使用方法</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 升级前的准备与门禁

- 升级前写下以下信息，并把它们与本次报告绑定：
- 以下命令只读，不会修改系统；应在板端目标环境中执行：
- 至少保存：当前版本输出、安装包文件名和摘要、CANN 安装配置、服务启动参数、模型清单、环境变量、测试输入、原始日志和回滚顺序。模型、用户数据和个人信息不要复制到公开报告。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix7.md"><code>src/appendix/appendix7.md</code></a> · 升级前的准备与门禁</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: section -->

# 02

## 升级、验收与回退
---

<!-- _class: center -->

## 共用安装与升级操作

- 覆盖升级是指现有驱动和固件仍在系统中，准备换到新的配套版本。官方升级指南要求同时升级时遵循：
- 先升级固件，再升级驱动，最后升级 CANN 软件。
- 驱动和固件已经在配套状态时，可以单独升级 CANN。对 .run 包，常见的检查顺序是“增加执行权限—校验安装包—升级”：

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix7.md"><code>src/appendix/appendix7.md</code></a> · 共用安装与升级操作</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 升级后的统一验收顺序

- 升级验收分成五个门，不应把一个门的通过写成全链路通过。
- 记录设备数量、型号、驱动显示的版本和异常日志。Health: Alarm 只能作为诊断背景；是否阻断升级要由设备缺失、驱动加载失败、复位循环或可复现的任务错误决定。
- 对于支持该工具的 310B 驱动包，可进一步读取设备侧系统版本。命令的实际路径和参数应以当前驱动版本的升级指南为准：

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix7.md"><code>src/appendix/appendix7.md</code></a> · 升级后的统一验收顺序</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 回退与故障处理

- 子主题：回退原则
- 子主题：失败时的最小记录
- 本节聚焦：回退与故障处理 的输入、操作与证据边界。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix7.md"><code>src/appendix/appendix7.md</code></a> · 回退与故障处理</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 面向本仓库案例的升级清单

- 变更前：固定板卡、SoC、CANN/HDK 组合和 Python 环境
- 资产前：保存 ONNX/OM 清单、输入合同和旧报告
- 设备层：固件、驱动升级并重启，确认 npu-smi

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix7.md"><code>src/appendix/appendix7.md</code></a> · 面向本仓库案例的升级清单</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 版本记录模板

- 本节聚焦：版本记录模板 的输入、操作与证据边界。
- 先把输入、版本与命令固定下来，再判断结果是否可复现。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix7.md"><code>src/appendix/appendix7.md</code></a> · 版本记录模板</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 本章小结

- 升级顺序不能颠倒
- 官方配套表是依据，实际板卡是证据
- 回退方案必须在升级前准备好
- 未验证项必须标为 untested 或 blocked。

> 下一讲：回到案例，按新版本重新验证关键链路。

<div class="source">教材：<a href="../appendix/appendix7.md"><code>src/appendix/appendix7.md</code></a></div>

<!--
用三句话收束；提醒学生把未验证项写成 untested 或 blocked。
-->
