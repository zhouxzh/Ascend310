---
name: ascend310b-writing-dot
description: Ascend310 教材 Graphviz DOT 图写作与画布适配。用于系统架构图、数据流图、程序流程图、模块依赖图和生成 PNG/SVG；要求按网页、PDF、Presentation 16:9 的尺寸限制画布，保留 .dot 源、正确引用生成图、检查 Graphviz 语法与版面，不手改生成图片。
---

# Graphviz DOT 图写作

## 范围

用于教材、案例和代码 docs 中的系统架构图、数据流图、程序流程图和模块关系图。DOT 源文件是图的可编辑真源，PNG/SVG 是生成物。

优先使用 Graphviz `dot` 布局表达方向明确的流程图；只有确实需要力导向布局时才考虑其他布局引擎。

## 教材画布契约

DOT 不是越大越详细。先选目标载体，再限制画布：

| 载体 | Graph `size` | DPI | 目标可见尺寸 | 建议 |
| --- | --- | ---: | --- | --- |
| 教材网页/PDF | `7.2,4.2` | 160–180 | ≤ 1296×756 px | 默认；保留 PDF 可缩放性 |
| Presentation 16:9 | `8.5,4.8` | 144 | ≤ 1224×691 px | 使用 SVG；不要超过单页内容区 |
| 细节图 | 拆成多张 | 160 | 每张 ≤ 8 个主节点 | 不要靠缩小字体塞入 |

全局属性至少包含：

```dot
graph [
  rankdir=LR,
  fontname="Noto Sans CJK SC",
  fontsize=12,
  size="8.5,4.8",
  ratio="fill",
  dpi=144,
  pad="0.08",
  nodesep="0.28",
  ranksep="0.48",
  splines="polyline",
  bgcolor="transparent"
];
node [
  fontname="Noto Sans CJK SC",
  fontsize=12,
  margin="0.10,0.06",
  height="0.42",
  shape="box",
  style="rounded,filled",
  fillcolor="#F5F7FA",
  color="#607D8B"
];
edge [
  fontname="Noto Sans CJK SC",
  fontsize=10,
  color="#546E7A",
  arrowsize="0.7"
];
```

`size` 是上限，不是建议值。Presentation 使用 `ratio="fill"` 强制得到 16:9 画布；教材局部细节图可用 `ratio="compress"`，但要确认输出不是一条过宽或过窄的带子。课件和网页优先用 SVG，PNG 只用于需要固定像素或外部工具的场合。

## 字体和可读性

字体不是“看起来能出字”就够了。所有 `graph`、`node`、`edge` 必须使用同一个明确的中文字体族，不能依赖 Graphviz 的默认 Times/serif 回退：

```dot
fontname="Noto Sans CJK SC"
```

- 只写一个实际存在的字体族；`Noto Sans CJK SC`、`Noto Sans SC`、`Microsoft YaHei` 不是同一个名字。
- 先在构建机确认字体解析：
  ```bash
  fc-match "Noto Sans CJK SC"
  dot -Tsvg -v diagram.dot -o diagram.svg
  ```
  Graphviz 应显示解析到的真实字体；如果回退或告警，先安装字体或改用本机确实存在的字体，再生成图片。
- 教材节点正文 `fontsize` 不小于 16，边标签不小于 13；不要用 12 号字再加宽画布。
- 节点要有足够 `margin` 和 `height`，中文标签优先两行，不要依赖自动换行。
- 生成后按教材实际显示宽度检查，不要只看原图：内容列缩放后，正文仍应清楚可读。
- 图片一旦提交，字体已栅格化；换字体后必须重新生成 PNG/SVG，不能只改 DOT。

## 系统架构图规则

系统架构图回答“边界和职责”，不是罗列每个类：

1. 先画 3–5 个边界，例如客户端、服务、网关、worker、设备；
2. 每个边界最多 3 个主要节点，标签 2 行以内；
3. 数据方向统一，通常 `rankdir=LR`；反馈边可用 `constraint=false`；
4. 用 `subgraph cluster_*` 表示安全边界、进程边界或硬件边界；
5. 端口、密钥、loopback 等关键约束写进节点或边标签；
6. 超过 8 个主节点就拆成“总览图 + 细节图”，不要缩成不可读的大图；
7. 图内不放未验证的性能数字、兼容性结论或本机路径。

程序流程图使用 `rankdir=TB`，每个判断节点只表达一个分支条件；长流程拆成主流程和异常流程。

## 可复用模板

- [`templates/architecture-textbook.dot`](templates/architecture-textbook.dot)：教材正文两列纵向架构图；
- [`templates/architecture-slide.dot`](templates/architecture-slide.dot)：16:9 系统架构图；
- [`templates/flow-textbook.dot`](templates/flow-textbook.dot)：教材纵向流程图。

复制模板后替换节点标签和边，不要删除画布、字体和间距属性。系统架构优先使用两列/分层布局；如果横向节点超过 4 个，先重新组织边界，不要直接扩大画布。

## DOT 基本规则

- 有方向的流程使用 `digraph` 和 `->`。
- 先声明全局属性，例如：

```dot
digraph workflow {
  rankdir=LR;
  graph [fontname="Noto Sans CJK SC", bgcolor="transparent"];
  node [shape=box, style="rounded,filled", fillcolor="#f5f7fa"];
  edge [color="#546e7a"];
  start [label="输入"];
  process [label="ATC/ACL"];
  start -> process;
}
```

- 节点 ID 稳定，标签写给人看；
- 同一张图只用一种主方向，避免边交叉；
- 用 `subgraph cluster_*` 表示硬件、进程或阶段边界；
- 中文字体必须实际存在或使用站点/PDF 可用字体，生成后检查；
- 不把超大模型图塞进一页，按子系统拆图；
- 不把 DOT 源和生成图混在一个目录层级中，保持可追溯。

## 生成与引用

先生成到源图同目录或明确的 `imgN/` 目录。网页/PDF 优先 SVG，固定像素场景再生成 PNG：

```bash
dot -V
dot -Tsvg -Gdpi=144 diagram.dot -o diagram.svg
dot -Tpng -Gdpi=144 diagram.dot -o diagram.png
file diagram.png
```

教材和课件正文只引用生成图，并写面向读者的 alt：

```markdown
![昇腾 310B 数据流](img1/pipeline.png)
```

不要在读者可见正文里写 `源图：`、`.dot` 路径、字体名、pt、dpi 或画布自检。`.dot` 必须与 PNG/SVG 同目录保留，供作者改图和审计追溯，但不进入教程叙事。不要引用一个没有正文说明的图片，也不要只提交 `.dot` 而忘记生成图。

## 验收

1. `dot -V` 确认 Graphviz 可用。
2. 用 SVG 和/或 PNG 生成，检查退出码和 stderr。
3. 用 `file` 或图片元数据确认像素尺寸：
   - 教材图不超过约 `1600×1000`；
   - Presentation 图不超过 `1280×720` 内容区；
   - 超过就拆图或减少节点，不靠缩小字体。
4. 在目标网页和 1280×720 课件中打开，检查节点文字、箭头、cluster 边界和中文字体；图片不得需要横向滚动。
5. 运行 `ascend310b_writing_audit`，检查 dot/png/svg 的孤儿和引用。
6. 进入 PDF/站点后重新构建，不直接编辑生成图片。

## 停止条件

- DOT 语法错误或 Graphviz 返回非零。
- 图片超过教材/课件画布，或必须靠缩小字号才能放下。
- 教材或课件正文出现 `.dot` 路径、字体名、pt/dpi 或画布自检。
- 生成图没有正文引用，或仓库里缺少对应 `.dot` 源。
- 文字溢出、箭头方向错误、中文缺字形、图无法在目标页阅读。
- 只改了 PNG/SVG 而没有 DOT 源。
- 图把未经验证的硬件/性能结论画成既成事实。
