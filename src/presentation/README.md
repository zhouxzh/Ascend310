---
editLink: false
---

# 教材配套演示

本目录提供与《昇腾310B实战》正文一一对应的教学课件。理论教程 10 章、实践案例 9 个、附录 7 份各自拥有独立 Marp 放映文件。

课件的编号、冻结标题、源文件、样例路径、制作状态和构建输出只在一处维护：[catalog.mjs](./catalog.mjs)。侧栏、构建脚本和检查脚本都读取这份清单。

## 课件目录

<!-- BEGIN GENERATED PRESENTATION INDEX -->
### 课程总览

| 单元 | 状态 | 放映版 |
|---|---|---|
| 《昇腾310B实战》课程导览 | 可用 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/00-course-overview.html) |

### 理论章节课件

| 单元 | 状态 | 放映版 |
|---|---|---|
| 第1章：昇腾310B边缘计算基础 | 可用 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/10-chapter1.html) |
| 第2章：CANN软件栈核心组件解析 | 可用 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/11-chapter2.html) |
| 第3章：昇腾PyTorch扩展迁移基础 | 可用 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/12-chapter3.html) |
| 第4章：PyACL应用开发基础 | 可用 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/13-chapter4.html) |
| 第5章：MindSpore开发基础 | 可用 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/14-chapter5.html) |
| 第6章：DVPP视频处理基础 | 可用 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/15-chapter6.html) |
| 第7章：算子开发实战 | 可用 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/16-chapter7.html) |
| 第8章：性能分析与优化 | 可用 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/17-chapter8.html) |
| 第9章：模型量化案例教学 | 可用 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/18-chapter9.html) |
| 第10章：AI项目交付闭环 | 可用 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/19-chapter10.html) |

### 案例课件

| 单元 | 状态 | 放映版 |
|---|---|---|
| 案例 1：智能考勤机 | 可用 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/20-case1.html) |
| 案例 2：目标跟踪检测 | 可用 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/21-case2.html) |
| 案例 3：智能电子琴 | 可用 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/22-case3.html) |
| 案例 4：掌纹识别 | 可用 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/23-case4.html) |
| 案例 5：智能数据采集分析仪 | 可用 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/24-case5.html) |
| 案例 6：智能小车 | 制作中 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/25-case6.html) |
| 案例 7：智能相册 | 可用 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/26-case7.html) |
| 案例 8：实时手势识别 | 可用 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/27-case8.html) |
| 案例 9：智能聊天机器人 | 制作中 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/28-case9.html) |

### 附录课件

| 单元 | 状态 | 放映版 |
|---|---|---|
| 附录 1：开发板环境 | 制作中 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/30-appendix1.html) |
| 附录 2：Ubuntu 教程 | 制作中 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/31-appendix2.html) |
| 附录 3：Python 基础 | 制作中 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/32-appendix3.html) |
| 附录 4：Vibe Coding | 可用 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/33-appendix4.html) |
| 附录 5：ROS2 基础 | 制作中 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/34-appendix5.html) |
| 附录 6：工具箱与 FAQ | 制作中 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/35-appendix6.html) |
| 附录 7：CANN 升级 | 制作中 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/36-appendix7.html) |
<!-- END GENERATED PRESENTATION INDEX -->

## 当前制作状态

- `ready`：已完成并通过本目录检查的导览、章节、案例或附录课件。
- `scaffold`：已有封面、教学主线、来源页和讲师备注，正文正在逐份替换。

制作状态以 `catalog.mjs` 的 `status` 字段为准。课件正文完成后，把对应条目从 `scaffold` 改为 `ready`，再移除源文件中的 `presentation-scaffold` 标记。

## 目录约定

```text
src/presentation/
├─ catalog.mjs                        课件单一清单
├─ theme.css                          Marp 主题
├─ 00-course-overview.md              课程总览
├─ 10-chapter1.md ... 19-chapter10.md 理论章节课件
├─ 20-case1.md ... 28-case9.md        案例课件
├─ 30-appendix1.md ... 36-appendix7.md 附录课件
└─ screenshots/
   ├─ capture-plan.json               截图任务
   └─ capture-manifest.json           截图文件、哈希与证据边界
```

## 制作与检查

生成缺失的课件骨架，不覆盖已有文件：

```bash
pnpm run docs:slides:scaffold
pnpm run docs:slides:scaffold:write
```

修改 `catalog.mjs` 后，同步静态侧栏和 README 索引：

```bash
pnpm run docs:slides:index:write
```

检查目录覆盖、冻结标题、frontmatter、截图清单、侧栏与索引一致性：

```bash
pnpm run docs:slides:check
```

构建全部放映页，并在浏览器中核对 1280×720 裁切与图片加载：

```bash
pnpm run docs:slides
pnpm run docs:slides:render-check
```

只重建正在修改的一份课件：

```bash
node scripts/build_presentation.mjs --only=10-chapter1
```

运行站点与放映页：

```bash
pnpm run docs:dev
```

本地入口为 `http://localhost:8080/Ascend310/presentation/`。放映页中按 `f` 全屏，按 `p` 打开讲师视图。

## 前端截图

截图任务使用两块开发板的独立证据集：

- `board-8t`：Ascend 310B4 / 8T
- `board-20t`：Ascend 310B1 / 20T

采集前把页面、操作步骤、视口、隐私场景和证据边界写入 `screenshots/capture-plan.json`。URL 通过 `BOARD_8T_ORIGIN` 和 `BOARD_20T_ORIGIN` 注入，不把板卡地址和凭据写进课件。

先预览任务，再执行写入：

```bash
node scripts/capture_presentation_ui.mjs
node scripts/capture_presentation_ui.mjs --id=case4-palmprint-live-idle --write
```

原生 OpenCV 或 Qt 窗口先在板端桌面截图，再把 PNG 登记到同一清单：

```bash
node scripts/register_presentation_screenshot.mjs \
  --id=case2-tracking-window \
  --file=<本地PNG路径> \
  --write
```

截图只允许合成数据或匿名数据。采集后 `capture-manifest.json` 记录开发板档位、页面、视口、时间、截图与 JS/CSS/HTML 的 SHA-256、应用资产组合摘要、隐私分类和“能证明什么”的边界说明。原生窗口登记时会计算 `applicationFiles` 的源码哈希。8T 与 20T 的界面和性能证据不能合并成一组。

## 课件规范

- 每页正文汉字不超过 320 字，代码不超过 22 行，表格不超过 6 列 × 10 行，列表不超过 8 条。
- 正文与代码字号不低于主题规定值，不用缩小字号解决溢出，改用分页。
- 每个代码页标明 `samples/` 来源，每张图给出原图路径，每个结论说明证据边界。
- 每页使用 Marp 演讲者备注说明授课重点、误区和提问点。
- 理论课件按章节推进，案例课件必须包含真实前端截图或明确的待采集说明。

详细写作规范由 `ascend310b-writing` 技能和 `tools/dsh-plugin-ascend310b-writing` 审计器执行；本目录的目录检查不替代教材写作审计。
