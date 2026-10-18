---
marp: true
theme: ascend310
paginate: true
size: 16:9
header: "《昇腾310B实战》教材配套演示"
footer: "附录归档：旧版专题课件"
---

<!-- _class: cover -->

# 附录归档：旧版专题课件

旧版 10 份专题保留原文和放映版本，但不再作为新版章节与案例的教学主线。

<div class="source">归档索引：`src/presentation/89-legacy-archive.md`；课件清单：`src/presentation/catalog.mjs`</div>

<!--
授课提示：这里说明归档不等于删除；重点是避免旧口径混入新版课堂。
-->

---

## 为什么单列旧版归档

旧课件最初承担的是写作技术路线验证，不是最终课程结构：

- 单元编号与当前 10 章、9 个案例、7 个附录不一致
- 部分页面把附录主题、理论章节和案例内容混在一起
- 端口、模型状态、界面和部署路径可能已经过时
- 旧页面的教学目标不能直接承接当前教材的章节产出

新版课件只引用已经核对过的教材正文和样例。

<div class="source">新版目录：`src/presentation/README.md`；冻结名称：`AGENTS.md`</div>

<!--
授课提示：如果学生手里还保存旧链接，先让他们确认页面顶部是否标有“旧版课件”。
-->

---

## 基础专题旧版

| 旧版放映 | 原主题 | 新版对应位置 |
|---|---|---|
| [旧版全书导览](https://zhouxzh.github.io/Ascend310/presentation/legacy-90-repository-map.html) | 全书结构 | 课程总览 |
| [旧版硬件基础](https://zhouxzh.github.io/Ascend310/presentation/legacy-91-hardware-basics.html) | 开发板与基础环境 | 第1章、第2章、附录1 |
| [旧版Ubuntu](https://zhouxzh.github.io/Ascend310/presentation/legacy-92-ubuntu-basics.html) | Ubuntu与板端操作 | 附录2 |
| [旧版Python](https://zhouxzh.github.io/Ascend310/presentation/legacy-93-python-basics.html) | Python基础 | 附录3 |
| [旧版Vibe Coding](https://zhouxzh.github.io/Ascend310/presentation/legacy-94-vibe-coding.html) | DSH与代码生成 | 附录4 |
| [旧版ROS2](https://zhouxzh.github.io/Ascend310/presentation/legacy-99-ros-basics.html) | ROS2基础 | 附录5 |

<div class="source">这些页面只保留历史教学组织方式，不作为新版章节进度或验收依据。</div>

<!--
授课提示：带读“新版对应位置”一列，说明内容没有丢，只是重新归属。
-->

---

## 案例专题旧版

| 旧版放映 | 原主题 | 新版对应位置 |
|---|---|---|
| [旧版智能考勤机](https://zhouxzh.github.io/Ascend310/presentation/legacy-95-face-recognition.html) | 案例1 | 新版案例1课件 |
| [旧版目标跟踪检测](https://zhouxzh.github.io/Ascend310/presentation/legacy-96-object-tracking.html) | 案例2 | 新版案例2课件 |
| [旧版智能电子琴](https://zhouxzh.github.io/Ascend310/presentation/legacy-97-smart-piano.html) | 案例3 | 新版案例3课件 |
| [旧版智能聊天机器人](https://zhouxzh.github.io/Ascend310/presentation/legacy-98-chatbot.html) | 案例9 | 新版案例9课件 |

案例4至案例8原先没有独立课件，新版统一补齐为九个案例各一份。

<div class="source">新版案例范围：`src/experiment/case1.md` 至 `src/experiment/case9.md`</div>

<!--
授课提示：说明旧版只覆盖部分案例，不能用旧课件数量推断课程范围。
-->

---

## 使用旧版页面的三条规则

1. 只在追溯旧教学组织或查看历史截图时使用。
2. 命令、端口、模型状态和板端结果必须回到当前教材正文重新核对。
3. 任何旧版结论都不能替代新版课件的实测证据。

当新版课件完成并验证后，旧版链接继续保留，但不再出现在章节或案例的教学路径中。

<div class="source">证据分级：`AGENTS.md`；版本配套：`src/appendix/appendix7.md`</div>

<!--
授课提示：最后明确课堂入口只走新版目录，旧版归档用于追溯。
-->
