# Case 1–9 图片规范审计

审计日期：2026-10-23。范围为 `src/experiment/img1/` 至 `img9/` 的源图，以及对应 VuePress 正文页面的实际渲染宽度。构建产物 `dist/` 和课件派生图不重复计数。

## 审计标准

| 检查项 | 目标 | Warning | Error |
| --- | ---: | ---: | ---: |
| 教材图自然尺寸 | 不超过 `1600×1000` | 宽度 >1600 或高度 >1000 | 宽度 >2400 或高度 >1500 |
| 单图文件大小 | 不超过 `1 MB` | >1 MB | >1.5 MB |
| 桌面实际缩放 | 自然宽度/显示宽度 ≤2.5 | >2.5× | >3× |
| 移动端实际缩放 | 自然宽度/显示宽度 ≤2.5 | >2.5× | 不升级为硬失败 |
| Alt | 每张图都有 | 缺失 | - |

1440 px 视口下多数正文图片实际显示宽度约 `684 px`，所以 `1600 px` 原图约放大到 2.34× 的像素密度，仍属可接受；`2400 px` 原图已接近 3.5× 缩放。

历史欠账记录在 [`image-audit-baseline.json`](image-audit-baseline.json)：只冻结本报告列出的 42 个 error，降级为 warning。未登记的新图/DOT 超限仍会使审计失败；图片修复后必须删除对应基线项，审计会报告 `image-baseline-stale`。

## 逐案结果

| Case | 源图 | >1600×1000 | >2400×1500 | >1 MB | 浏览器 >3× | 结论 |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| case1 | 4 | 2 | 2 | 0 | 2 | fail |
| case2 | 2 | 2 | 1 | 0 | 2 | fail |
| case3 | 24 | 22 | 4 | 2 | 0 | fail |
| case4 | 6 | 6 | 0 | 0 | 未加载 | warning |
| case5 | 18 | 15 | 5 | 0 | 0 | fail |
| case6 | 2 | 0 | 0 | 0 | 0 | pass |
| case7 | 4 | 2 | 2 | 0 | 2 | fail |
| case8 | 9 | 1 | 0 | 0 | 0 | warning |
| case9 | 4 | 0 | 0 | 0 | 0 | pass |

合计：

```text
73 张 case 源图
14 个尺寸硬错误
38 条尺寸/文件大小 warning
2 张图片超过 1 MB
```

## 逐案审核结论

除尺寸外，审核同时检查了 Markdown/HTML 引用、重复文件、DOT 画布和按实际正文宽度缩小后的可读性。

| Case | 主要复核结果 | 最终结论 |
| --- | --- | --- |
| case1 | 2 张流程图尺寸和缩放失败；2 张 DOT 均缺少 `size`；8T/20T 两张首页截图字节完全相同 | fail |
| case2 | 两张纵向流程图缩小 3.49×/3.54×；两张 DOT 均缺少 `size`；文字在实际 30%–40% 显示宽度下过小 | fail |
| case3 | 4 张纵向架构图超过高度硬上限；22 张超过目标尺寸；2 张超过 1 MB；时序图缩到 342 px 后不可读 | fail |
| case4 | 6 张整屏截图均为 1920×1080，文字在正文宽度下过密；当前 case 页面未引用这些图，仅 1 张在课件中使用 | warning |
| case5 | 5 张纵向流程超过高度硬上限；10 条 warning；3 个 DOT 显式画布超限，另有 9 个 DOT 缺少 `size` | fail |
| case6 | 两张 1440×900 页面截图尺寸和可读性通过，但 8T/20T 文件字节完全相同 | warning |
| case7 | 两张核心图缩小 5.77×/6.39×；两张 DOT 缺少 `size`；两张板端截图字节完全相同 | fail |
| case8 | case 页面两张 1440×900 截图通过；课件三张流程图尺寸通过；但 7 个 DOT 全缺少 `size`，4 张 YOLOv10 组件图未被任何正文引用 | fail |
| case9 | 4 张源图均低于目标尺寸，引用完整，实际缩放 1.66×–2.11×，3 个 DOT 都声明了合规画布 | pass |

自动审计的完整 findings：

| Case | Error | Warning | 主要规则 |
| --- | ---: | ---: | --- |
| case1 | 2 | 3 | 尺寸、DOT 缺画布、重复截图 |
| case2 | 1 | 3 | 尺寸、DOT 缺画布 |
| case3 | 4 | 20 | 尺寸、文件 >1 MB |
| case4 | 0 | 6 | 截图宽度 |
| case5 | 7 | 20 | 尺寸、DOT 缺画布、DOT 画布超限 |
| case6 | 0 | 1 | 重复截图 |
| case7 | 2 | 3 | 尺寸、DOT 缺画布、重复截图 |
| case8 | 0 | 8 | DOT 缺画布、未引用组件图、高度 warning |
| case9 | 0 | 0 | 全部通过 |

因此：

```text
通过：case9
warning：case4、case6
fail：case1、case2、case3、case5、case7、case8
```

## DOT 画布与来源

- case1：`case1_flow.dot`、`case1_flow_simple.dot` 没有 `size`。
- case2：`流程图.dot`、`主线图.dot` 没有 `size`。
- case5：
  - `case5-hantek-acquisition-flow.dot`：`7.4,9.0!`、180 dpi，约 1332×1620。
  - `case5-hantek-om-flow.dot`：`7.4,9.3!`、180 dpi，约 1332×1674。
  - `case5-hardware-connections.dot`：`12.8,8!`、150 dpi，约 1920×1200。
  - 其余 9 个 DOT 没有 `size`。
- case7：`network-architecture.dot`、`program-flow.dot` 没有 `size`。
- case8：7 个 DOT 全部没有 `size`。
- case9：3 个 DOT 均声明 `7.2,4.2` 或更小画布，符合规范。
- case3 是 AI/绘图工具生成的 PNG，没有 DOT 源；case4、case6 是截图，不需要 DOT。

## 引用与重复

- case1：`case1-board-8t-home.png` 与 `case1-board-20t-home.png` SHA-256 相同。
- case4：5 张 `palmprint-ui-react-*` 图没有被 Markdown/HTML 引用；只有 evaluation 图在课件中使用。
- case5：`case5-hantek-dashboard.png` 未被引用，正文使用的是 `case5-hantek-dashboard-1920x1080.png`。
- case6：`case6-board-8t-ui.png` 与 `case6-board-20t-ui.png` SHA-256 相同。
- case7：`case7-board-8t-ui.png` 与 `case7-board-20t-ui.png` SHA-256 相同。
- case8：`yolov10_architecture/backbone/neck/overview` 四张 PNG 及其 DOT 未被任何正文引用。

相同字节不代表图片尺寸错误，但当文件名声称来自 8T 和 20T 时，它不能作为两次独立采集的证据。

## 硬错误

### case1

- `case1_flow_simple.png`：`3452×3571`，网页显示约 550 px，缩放 6.28×。
- `case1_flow.png`：`3927×2474`，网页显示 764 px，缩放 5.14×。

### case2

- `流程图.png`：`1067×1900`，高度超过硬上限；网页显示约 306 px，缩放 3.49×。
- `主线图.png`：`812×1112`，超过目标高度；网页显示约 229 px，缩放 3.54×。

### case3

高度超过硬上限：

- `case3-backend-architecture.png`：`1536×2048`
- `case3-ddsp-vst-sequence.png`：`1536×2048`，另为 `1.31 MB`
- `case3-midi-ddsp-architecture.png`：`1536×2048`
- `case3-model-deployment-pipeline.png`：`1536×2048`

其余 18 张为 `2048×1280` 或类似尺寸的 warning。`case3-ddsp-vst-architecture.png` 约 `1.13 MB`。

### case5

高度超过硬上限：

- `case5-computation-boundary.png`：`1030×2031`
- `case5-hantek-acquisition-flow.png`：`868×1620`
- `case5-hantek-om-flow.png`：`893×1675`
- `case5-model-admission-flow.png`：`1024×1642`
- `case5-yolo-detection-decode-flow.png`：`1403×1901`

另有 10 条 warning，主要是 `1920×1080` 仪表盘截图和横向图。

### case7

- `network-architecture.png`：`4492×2303`，网页显示约 703 px，缩放 6.39×。
- `program-flow.png`：`3436×3495`，网页显示约 596 px，缩放 5.77×。

## Warning

- case3：22 张超过 `1600×1000`，包括 7 张 `2048×1280` 架构图；两张文件超过 1 MB。
- case4：6 张 `1920×1080` 交互界面截图超过目标宽度。文件本身只有 0.09–0.16 MB，问题主要是像素尺寸。
- case5：15 张超过 `1600×1000`，包括仪表盘、硬件连接和信号处理图。
- case8：`yolov10_architecture.png` 为 `971×1412`，横向较窄但高度超过目标。
- case2：`主线图.png` 同时触发高度和浏览器缩放 warning。

## 处置顺序

1. case7：重画两张超大总览图，拆成总览和局部子图。
2. case1：重画两张流程图，目标 `1296×756` 或 `1600×1000`。
3. case3：拆四张 `1536×2048` 纵向图；把 `2048×1280` 架构图统一重生成到 `1600×1000`；压缩两张超过 1 MB 的 PNG。
4. case5：拆五张纵向流程；仪表盘截图统一生成 `1600×900` 预览版。
5. case2：拆分两张纵向图，或改成横向总览 + 细节图。
6. case4：为正文生成 `1600×900` 展示版；原始 `1920×1080` 截图只留作证据，不直接作为正文大图。
7. case8：为 7 个 DOT 显式声明画布；把四张未引用的 YOLOv10 组件图接入正文/课件，或从发布资产中移除。
8. case1、case6、case7：8T/20T 同字节截图重新采集，或明确标注为同一界面证据的复制。
9. case4、case5：删除或归档未引用的源图，避免把历史文件误当成当前发布图。

case9 当前通过全部图片规范；case6 只有尺寸通过，但重复证据使其仍为 warning。

## samples/caseN 文档图片

`samples/case1`–`case9` 的工程文档中还另有 13 张发布图片（排除 `_upstream`、模型、数据、构建产物和运行报告）：

| Case | 文档图片 | 超过 1600×1000 | 超过 2400×1500 | 结论 |
| --- | ---: | ---: | ---: | --- |
| case3 | 12 | 12 | 0 | warning |
| case5 | 1 | 1 | 0 | warning |
| 其他 case | 0 | 0 | 0 | pass |

这 13 张主要是 `1920×1080` 的 WebUI/仪表盘截图，文件只有 0.13–0.18 MB，因此没有文件大小或硬尺寸错误；但按正文目标仍应生成 `1600×900` 展示版。

## 复现命令

```bash
node --input-type=module -e "...collectFigureFindings..."
node scripts/check_page_layout.mjs \
  --url http://127.0.0.1:8082/Ascend310/experiment/case1.html \
  --viewports 1440x1000,390x844
```

机械审计通过不等于视觉可读。修复后仍需在桌面和移动宽度人工检查文字、箭头、细线和图注。
