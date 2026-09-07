# 文档审计记录（2026-09-07）

_本记录说明本次仓库文档审计发现的问题、整理决定和后续维护边界。它不是某个案例的运行手册。_

---

## 📋 审计范围

本次检查了仓库根 README、`src/book/`、`src/experiment/`、`src/appendix/`、`samples/` 下各案例 README 与工程文档，以及 `docs/case-reviews/`。生成的 `latex/`、VuePress 缓存和 `src/.vuepress/dist/` 仅作为“不可编辑的生成物”核对，没有修改。

当前仓库同时存在三种读者需求：学习教材、运行案例、审计板端证据。此前这些需求没有统一入口，读者容易把理论源稿、历史实验记录和当前启动命令混在一起。

## 🔎 发现的问题

| 问题 | 影响 | 处理 |
| --- | --- | --- |
| 根目录没有完整文档地图 | 不知道理论、案例、附录和审查报告的关系 | 新增 [`docs/README.md`](README.md) |
| Case7 有 16 份编号文档且入口重复 | 读者不知道先看部署、上传、设备还是模型文档 | 新增 [`samples/case7/docs/README.md`](../samples/case7/docs/README.md)，README 改为指向该入口 |
| 当前操作与历史证据混在同一列表 | 旧 IP、旧固件或历史性能数字可能被误执行 | 索引按“当前操作 / 历史证据 / 理论教程”分组，原文件不搬移 |
| 根 README 的附录数量仍写成 1~3 | 与当前 `appendix1` 至 `appendix5` 不一致 | 修正为 1~5，并加入文档总索引 |
| Case7 理论教程把 Gradio 作为无上下文的实现行 | 读者可能误以为当前服务仍依赖 Gradio | 改为“早期 Gradio Demo（历史参考）”，明确当前运行时为 FastAPI |
| `samples/case3/doc/`、Case9 多段编号等历史布局 | 全仓库无法强制使用单一目录名而不破坏现有链接 | 在仓库级地图声明兼容例外，不做无计划批量迁移 |
| 生成目录包含大量 Markdown/HTML 派生内容 | 搜索结果噪声大、误改生成物 | 文档地图明确 `latex/` 与 `.vuepress/dist/` 只读，搜索示例排除生成目录 |

## ✅ 整理后的唯一入口

```text
仓库根目录
├── README.md                         # 项目和构建入口
├── docs/README.md                    # 全仓库文档地图
├── docs/case-reviews/00-index.md     # 全案例审查入口
├── src/book/README.md                # 理论教程入口
├── src/experiment/README.md          # 实践教程入口
├── src/appendix/README.md            # 附录入口
└── samples/
    ├── README.md                     # 可运行案例总览
    └── case7/
        ├── README.md                 # Case7 最短运行手册
        └── docs/README.md             # Case7 工程文档地图
```

## 🧭 推荐阅读顺序

### 教材读者

1. 从 [`src/README.md`](../src/README.md) 进入站点。
2. 先读 [`src/book/README.md`](../src/book/README.md)，再按兴趣进入理论章节。
3. 需要动手时进入 [`src/experiment/README.md`](../src/experiment/README.md)，从对应案例的 README 开始。

### Case7 操作者

1. 读 [`samples/case7/README.md`](../samples/case7/README.md) 启动 310B 服务并上传照片。
2. 触摸屏问题读 [`docs/07-touchscreen-ui-and-operations.md`](../samples/case7/docs/07-touchscreen-ui-and-operations.md)。
3. 手机 API 或 ESP32 配对读 [`docs/03-album-server-api-and-esp32-protocol.md`](../samples/case7/docs/03-album-server-api-and-esp32-protocol.md) 和 [`docs/14-wake-and-discover-esp32-photoframes.md`](../samples/case7/docs/14-wake-and-discover-esp32-photoframes.md)。
4. 模型转换读 [`docs/08-model-pipeline-and-npu-admission.md`](../samples/case7/docs/08-model-pipeline-and-npu-admission.md)；更换 8T/20T 板卡再读 [`docs/12-mobileclip-cross-board-compatibility.md`](../samples/case7/docs/12-mobileclip-cross-board-compatibility.md)。
5. 理解架构、模型和 NPU 迁移读 [`src/experiment/case7.md`](../src/experiment/case7.md)。

### 审计或维护者

1. 先读本页和 [`docs/case-reviews/00-index.md`](case-reviews/00-index.md)。
2. 只把标为 `observed-pass` 的证据用于验收结论。
3. 发现代码、报告和文档不一致时，先更新源文件和证据，再更新入口摘要。

## ⚠️ 未在本次整理中解决的事项

- 本次没有重新执行任何 CANN、ATC、ACL、NPU、触摸屏或电子纸硬件测试。
- 本次没有重新生成 LaTeX/PDF 或 VuePress 派生文件。
- 各案例已有的 P0/P1 审查问题仍以 `docs/case-reviews/` 的对应报告为准。
- Case7 的 PhotoPainter/E6 实屏刷新、模型板端准入和性能数字仍需以各自报告中的真实证据为准。

## 🔗 相关规范

- [`AGENTS.md`](../AGENTS.md)：仓库协作、证据和生成物边界
- [`samples/CASE_LAYOUT.md`](../samples/CASE_LAYOUT.md)：案例目录稳定路径
- [`README.md`](../README.md)：构建、VuePress 和 PDF 操作

