---
marp: true
theme: ascend310
paginate: true
size: 16:9
author: "周贤中"
email: "zhouxzh@gdut.edu.cn"
header: "《昇腾310B实战》教材配套演示"
footer: "附录 6：工具箱与 FAQ"
---

<!-- _class: cover -->

# 附录 6：工具箱与 FAQ

<div class="byline">周贤中 · zhouxzh@gdut.edu.cn</div>

## 从环境自检到常见故障定位


对应教材：`src/appendix/appendix6.md`

<!--
授课提示：用一句话说明本讲要解决的工程问题，再核对教材和样例路径。
-->

---

<!-- _class: center -->

## 本讲目标与路线

- 用报错速查表定位常见问题
- 套用模型转换参数模板
- 执行性能与质量 Checklist
- 按 FAQ 与版本路线回落

> 本讲主线：报错 → 模板 → Checklist → FAQ → 版本

<div class="source">教材：<a href="../appendix/appendix6.md"><code>src/appendix/appendix6.md</code></a></div>

<!--
先给学生路线图；后续每页都对应这里的一项，最后回到小结。
-->
---

<!-- _class: section -->

# 01

## 报错与转换模板
---

<!-- _class: center -->

## 内容概览

- 本附录聚焦“查得快、用得稳”：常见报错速查、转换参数模板、性能/质量 Checklist、术语字典、推荐资源与社区贡献规范。可作为日常开发随手翻阅的工具章节。
- 本节聚焦：内容概览 的输入、操作与证据边界。
- 先把输入、版本与命令固定下来，再判断结果是否可复现。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix6.md"><code>src/appendix/appendix6.md</code></a> · 内容概览</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 常见报错速查

- 分类：报错/现象，先判断属于环境、模型还是运行层。
- ATC：E19001 Op Not Supported，先核对目标 SoC 与算子清单。
- ATC：Shape 推断失败，核对输入形状、动态维度和模型合同。
- 运行：OM 加载失败时先查版本、平台与文件完整性。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix6.md"><code>src/appendix/appendix6.md</code></a> · 常见报错速查</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 模型转换参数模板合集

- 子主题：分类模型 (ResNet)
- 子主题：YOLO 动态分辨率
- 子主题：INT8 量化（示例）

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix6.md"><code>src/appendix/appendix6.md</code></a> · 模型转换参数模板合集</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 性能与质量 Checklist（执行勾项）

- [ ] Profiling 无明显 Idle gap > 10%
- [ ] H2D + D2H 占比 < 25%
- [ ] Postprocess 占比 < 20%
- [ ] 性能记录输入尺寸、精度、warmup、loops 与 repeats
- [ ] 质量验证输出对齐、精度回退与失败边界

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix6.md"><code>src/appendix/appendix6.md</code></a> · 性能与质量 Checklist（执行勾项）</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: section -->

# 02

## 质量、FAQ 与版本
---

<!-- _class: center -->

## 术语表（扩展）

- OM：Ascend 离线模型二进制格式
- ACL：Ascend 计算语言 API 层
- ATC：模型转换/编译工具

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix6.md"><code>src/appendix/appendix6.md</code></a> · 术语表（扩展）</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## FAQ

- 模型转换慢怎么办？：使用 SSD，关闭调试日志，检查不必要动态 shape。
- 精度下降如何定位？：离线脚本层级 Dump 比对，逐层二分。
- 如何减少内存占用？：启用内存池 + 减少中间冗余张量 + 固定 batch。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix6.md"><code>src/appendix/appendix6.md</code></a> · FAQ</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 实践任务

- 为你的项目添加 1 条本地常见错误记录（含根因与解决）。
- 复制分类 ATC 模板并改写为检测模型版本（含动态尺寸）。
- 在术语表补充 3 个任务相关术语（并验证唯一性）。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix6.md"><code>src/appendix/appendix6.md</code></a> · 实践任务</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 本章小结

- 先定位错误层，再套模板
- Checklist 必须逐项有证据
- 版本路线决定回退与迁移顺序
- 未验证项必须标为 untested 或 blocked。

> 下一讲：进入 CANN 升级与版本配套。

<div class="source">教材：<a href="../appendix/appendix6.md"><code>src/appendix/appendix6.md</code></a></div>

<!--
用三句话收束；提醒学生把未验证项写成 untested 或 blocked。
-->
