---
marp: true
theme: ascend310
paginate: true
size: 16:9
author: "周贤中"
email: "zhouxzh@gdut.edu.cn"
header: "《昇腾310B实战》教材配套演示"
footer: "附录 4：Vibe Coding"
---

<!-- _class: cover -->

# 附录 4：Vibe Coding

<div class="byline">周贤中 · zhouxzh@gdut.edu.cn</div>

## DSH 桌面版、写作插件与调试插件

对应教材：`src/appendix/appendix4.md`

<!--
授课提示：先说明本讲不再讲 npx 安装，学生只需要一个桌面安装包；后面再解释插件和 Skill 的分工。
-->

---

## 本讲目标与路线

- 装好 DSH 桌面版，能确认版本、模型和工作区
- 分清插件（工具）与 Skill（操作规程）
- 用写作插件检查教材，用调试插件复核板端证据
- 留下可复现的命令、截图和失败边界

<div class="source">教材：`src/appendix/appendix4.md` §3</div>

<!--
授课提示：四个目标对应后面四组页面；提醒学生每一步都要有可检查的证据。
-->

---

## 为什么统一用桌面版

| 方式 | 学生要准备 | 常见问题 |
| --- | --- | --- |
| **DSH 桌面版** | 一个安装包 | 几乎没有环境依赖 |
| `npx` / `npm` | Node、npm、代理、PATH | 命令找不到、版本冲突 |
| 源码运行 | Git、Node、pnpm | 步骤多，出错点分散 |

课堂只采用桌面版；旧教程里的 `npx` 命令直接跳过。

<div class="source">教材：`src/appendix/appendix4.md` §3.1</div>

<!--
授课提示：这里要明确说"不要用 npx"，并解释这能消除大部分环境问题。
-->

---

## 安装与首次启动

1. 运行课程提供的 Windows x64 安装包
2. 默认选项安装，从开始菜单启动
3. 打开 `设置 → 关于`，确认版本
4. 本教材验证版本：`0.2.1-rc.2`

<div class="source">教材：`src/appendix/appendix4.md` §3.2</div>

<!--
授课提示：让学生举手确认自己能打开"关于"页；版本不同不影响操作，但工具名以实际为准。
-->

---

<!-- _class: visual -->

## DSH 桌面版主界面

![DSH 桌面版主界面](../appendix/img4/dsh-webui-main.png)

<div class="source">图片：`src/appendix/img4/dsh-webui-main.png`</div>

<!--
授课提示：指出左侧工作区、底部输入框和设置入口；本页只认界面，不展开操作。
-->

---

## 模型与工作区

- 在 `设置 → 模型` 填入 DeepSeek API Key
- 工作区选择本仓库根目录：
  - Windows：`D:\Github\Ascend310`
  - 板端：`~/Documents/Ascend310`
- 新建会话后先问：

```text
请告诉我当前工作区路径，以及你加载了哪些 skill。
```

<div class="source">教材：`src/appendix/appendix4.md` §3.3</div>

<!--
授课提示：工作区选错是最高频的失败；让学生把这句话保存成自己的开场检查。
-->

---

<!-- _class: visual -->

## 插件与 Skill 的协作关系

![DSH 插件与 Skill 的协作关系](../appendix/img4/dsh-plugin-architecture.png)

<div class="source">图片：`src/appendix/img4/dsh-plugin-architecture.png`</div>

<!--
授课提示：先让学生看清"桌面版—Skill—两个插件—仓库—产物"这条链，再进入分工表。
-->

---

## 两个插件如何分工

| 插件 | 管什么 | 典型工具 |
| --- | --- | --- |
| 写作插件 | 教材是否写对 | `audit` / `render` / `push_check` |
| 调试插件 | 板端为什么不对 | `ascend_probe` / `ascend_debug_plan` |

<div class="source">图片：`src/appendix/img4/dsh-plugin-architecture.png`；源码：`tools/dsh-plugin-ascend310b-*`</div>

<!--
授课提示：强调两条产物流不同——写作插件产出可交付教材，调试插件产出可复核的板端证据。
-->

---

## 写作插件能做什么

| 工具 | 检查内容 |
| --- | --- |
| `audit` | 命名、锚点、链接、图片、排版、PDF 章节表、网页产物 |
| `render` | 缺字形、未定义引用、版面溢出、产物过期 |
| `push_check` | 提交内链接、产物新鲜度、预览确认 |
| `style` | 营销腔、空话、元话语、对话残留 |

写作插件的结论只覆盖"教材"，不替代板端实测。

<div class="source">教材：`src/appendix/appendix4.md` §3.4</div>

<!--
授课提示：现场跑一次 audit，让学生看到工具输出，而不是只听介绍。
-->

---

## 调试插件能做什么

| 工具 | 用途 |
| --- | --- |
| `ascend_probe` | 只读采集 SoC、CANN、驱动、固件 |
| `ascend_check` | 判断版本配套与验收门 |
| `ascend_debug_plan` | 按 ACL、ATC、OM、DVPP、摄像头分层排查 |
| `ascend_board_exec` | 在板端执行只读诊断 |

调试插件先采事实，再给计划；没有证据的结论只能写 `untested`。

<div class="source">教材：`src/appendix/appendix4.md` §3.4</div>

<!--
授课提示：提醒学生不要在本地冒充板端结果；所有板端结论必须有设备输出。
-->

---

## Skill 的优先级

| 优先级 | 位置 | 适用 |
| --- | --- | --- |
| 100 | `<仓库>/.dsh/skills` | 本仓库，新克隆即生效 |
| 200 | `<仓库>/.agents/skills` | 其他 Agent 约定目录 |
| 之后 | `~/.dsh/skills` | 用户级，跨仓库 |

本仓库已有：

```text
.dsh/skills/ascend310b-writing/SKILL.md
.dsh/skills/ascend310b-debug/SKILL.md
```

<div class="source">教材：`src/appendix/appendix4.md` §3.5</div>

<!--
授课提示：项目级优先于用户级，所以仓库里的规则不会被个人旧版本覆盖。
-->

---

## Skill 怎么生效

- Skill 只是一份 `SKILL.md`，告诉模型何时调用哪个工具
- DSH 在**会话启动时**加载，更新后要新建会话
- 改完插件里的 Skill 后执行：

```bash
pnpm run skills:sync
pnpm run skills:check
```

自检问题：`你现在能用哪些 skill？写作任务先调用哪个工具？`

<div class="source">教材：`src/appendix/appendix4.md` §3.5；脚本：`scripts/sync-repo-skills.mjs`</div>

<!--
授课提示：让学生当场问一次，确认 skill 真的加载；这比记命令更重要。
-->

---

## 安全边界

1. 先看计划，再批准执行
2. 只修改工作区里的指定文件
3. 不执行删除、格式化、系统级安装
4. API Key 不写进代码、文档或 GitHub
5. 结论必须能从命令输出或文件内容验证

<div class="source">教材：`src/appendix/appendix4.md` §3.7</div>

<!--
授课提示：用一次"选错工作区"的演示说明为什么要先问路径；不要用真实 Key 做演示。
-->

---

## 课堂任务与小结

**任务**：用 DSH 桌面版完成一次小改动，并留下证据链。

- 工作区路径截图
- 一次 `ascend310b_writing_audit` 的输出
- 一条失败边界：哪些没有验证、为什么
- 一句话总结：插件和 Skill 各自解决了什么问题

<div class="source">教材：`src/appendix/appendix4.md` §4–§7</div>

<!--
授课提示：任务按"证据链"评分，不按界面截图是否好看评分；未验证项必须明确写出。
-->
