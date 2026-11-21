# 技术图工具链

## Graphviz

- 源文件：`.dot`
- 渲染：`dot -Tsvg source.dot -o source.svg`
- 检查：`dot -Tpng -Gdpi=160 source.dot -o out/source.png`
- 源码语法与退出码必须为 0；生成 SVG 后检查文字、箭头和裁切。

## Mermaid

- 源文件：`.mmd`
- 渲染可用 `mmdc -i source.mmd -o source.svg`，或由目标渲染器生成。
- 发布目录不只手放截图；同时保留 `.mmd` 和渲染命令。
- 若目标系统不支持 Mermaid，按最终平台导出 SVG/PNG，并记录工具版本。

## TikZ

- 源文件：`.tex`
- 神经网络/编码器框图同时维护 `<source>.layout.json`。
- 运行 `ascend310b_writing_neural_diagram_audit` 检查箭头和模块框碰撞。
- 编译 XeLaTeX/PDF 后在最终显示尺寸检查字体、箭头和标号。

## draw.io

- 保留 `.drawio` 或可编辑导出源。
- 导出 SVG 作为交付图；PNG 仅在必须固定像素时使用。
- 不把浏览器截图当作唯一源文件。

## 验收

1. 源文件、导出文件和构建命令齐全。
2. 同一图只表达一个主要关系。
3. 每个箭头都有数据、控制或物理含义。
4. 箭头和标签不压节点或其他文字。
5. 黑白或受限配色符合目标材料。
6. 图号、图注和正文引用一致。
7. 最终载体尺寸下文字可读。
8. 专利图再接受 `patent-disclosure` 的领域规则检查。
