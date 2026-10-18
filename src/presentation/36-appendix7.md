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

<!-- presentation-scaffold: appendix-07 -->

<!-- _class: cover -->

# 附录 7：CANN 升级

<div class="byline">周贤中 · zhouxzh@gdut.edu.cn</div>

## 从版本核对到可回退的升级流程


对应教材：`src/appendix/appendix7.md`

<!--
授课提示：用一句话说明本讲要解决的工程问题，再核对教材和样例路径。
-->

---

## 本讲教学主线

1. 驱动、固件、HDK与CANN的边界
2. 升级前核查与升级顺序
3. 升级后五道验收门
4. 回退边界和版本证据记录

课堂结束时，学生应能沿着这 4 个节点复述设计、运行入口和证据边界。

<div class="source">教材：`src/appendix/appendix7.md`；样例：`无独立样例`</div>

<!--
授课提示：逐项带读，不逐字念稿；每讲完一个节点就指出对应教材小节。
-->


---

<!-- _class: visual -->

## 本章运行证据

> 本页预留本章最关键的板端运行界面、日志或结果图。没有实测证据时保留待采集说明，不把示意图写成通过结论。

<div class="source">来源：`src/appendix/appendix7.md`；运行入口：`无独立样例`</div>

<!--
授课提示：先指出这是什么证据，再解释它能证明什么、不能证明什么。
-->

---

## 配套源码与课堂产出

| 项目 | 本讲入口 |
|---|---|
| 教材正文 | `src/appendix/appendix7.md` |
| 可运行样例 | `无独立样例` |
| 截图清单 | `src/presentation/screenshots/capture-manifest.json` |
| 课件清单 | `src/presentation/catalog.mjs` |

课堂产出必须包含实际运行的命令、界面或日志，以及失败时停在哪一层。

<div class="source">课件制作中：appendix-07；完成正文、截图和讲师备注后移除 scaffold 标记。</div>

<!--
授课提示：最后留下一个可检查的课堂任务；学生提交运行入口、结果和未完成边界。
-->
