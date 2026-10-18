---
marp: true
theme: ascend310
paginate: true
size: 16:9
author: "周贤中"
email: "zhouxzh@gdut.edu.cn"
header: "《昇腾310B实战》教材配套演示"
footer: "附录 1：开发板环境"
---

<!-- _class: cover -->

# 附录 1：开发板环境

<div class="byline">周贤中 · zhouxzh@gdut.edu.cn</div>

## 从开箱检查到可复现的运行环境


对应教材：`src/appendix/appendix1.md`

<!--
授课提示：用一句话说明本讲要解决的工程问题，再核对教材和样例路径。
-->

---

<!-- _class: center -->

## 本讲目标与路线

- 识别开发板接口与配件边界
- 完成系统镜像校验、刷写与启动
- 配置网络与 SSH 远程连接
- 用最小命令验证开发环境

> 本讲主线：硬件 → 系统 → 网络 → 远程 → 验证

<div class="source">教材：<a href="../appendix/appendix1.md"><code>src/appendix/appendix1.md</code></a></div>

<!--
先给学生路线图；后续每页都对应这里的一项，最后回到小结。
-->
---

<!-- _class: section -->

# 01

## 硬件与系统安装
---

<!-- _class: center -->

## 昇腾310B开发板介绍

- 子主题：开发板详细视图
- 子主题：开发板硬件规格
- 本节聚焦：昇腾310B开发板介绍 的输入、操作与证据边界。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix1.md"><code>src/appendix/appendix1.md</code></a> · 昇腾310B开发板介绍</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 配件准备

- 为了顺利进行开发，请准备以下配件：
- 建议使用容量64GB及以上、速率为Class10级以上的闪迪（SanDisk）品牌TF卡。虽然最小支持32GB，但为了避免开发过程中出现磁盘空间不足的问题，推荐使用更大容量。
- 用于在电脑上读写TF卡以刷写系统镜像。建议选择USB 3.0速率的读卡器，以减少系统刷写的等待时间。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix1.md"><code>src/appendix/appendix1.md</code></a> · 配件准备</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 操作系统安装

- 首先，访问香橙派官网的技术支持页面。
- 在页面下方找到“官方镜像”区域。官方提供了预装昇腾NPU应用环境及软件的Ubuntu和openEuler镜像，极大地方便了开发者快速上手。
- #### Ubuntu

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix1.md"><code>src/appendix/appendix1.md</code></a> · 操作系统安装</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 启动开发板

- 将刷写好的TF卡插入开发板插槽。
- 确认拨码开关均拨至右侧（TF卡启动模式）。
- 将HDMI线连接至HDMI0接口（靠近USB 3.0接口的那个）。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix1.md"><code>src/appendix/appendix1.md</code></a> · 启动开发板</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: section -->

# 02

## 网络、远程与验证
---

<!-- _class: center -->

## 网络连接

- 开发板板载了WiFi模块，可以通过命令行工具nmcli轻松连接无线网络。
- 将替换为你的WiFi名称，替换为密码。
- 如果不熟悉命令参数，也可以使用nmtui提供的终端图形界面连接WiFi：

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix1.md"><code>src/appendix/appendix1.md</code></a> · 网络连接</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 远程连接 (SSH)

- 为了方便开发，通常我们会使用个人电脑通过SSH远程连接到开发板。
- 使用上述ip addr命令获取开发板的IP地址（通常在wlan0或eth0接口下）。
- 使用SSH客户端连接

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix1.md"><code>src/appendix/appendix1.md</code></a> · 远程连接 (SSH)</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 验证开发环境

- 系统启动并连接网络后，我们需要验证昇腾AI处理器的状态以及开发环境是否正常。
- 使用npu-smi工具查看NPU的详细信息，包括温度、功耗、算力利用率等。
- 如果能看到类似以下的输出，说明NPU工作正常：

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix1.md"><code>src/appendix/appendix1.md</code></a> · 验证开发环境</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 本章小结

- 先确认硬件与配件，再安装系统
- 网络与 SSH 是后续所有实验的入口
- 环境验证必须保留命令输出
- 未验证项必须标为 untested 或 blocked。

> 下一讲：进入 Ubuntu 与 Linux 基础操作。

<div class="source">教材：<a href="../appendix/appendix1.md"><code>src/appendix/appendix1.md</code></a></div>

<!--
用三句话收束；提醒学生把未验证项写成 untested 或 blocked。
-->
