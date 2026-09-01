# 教学演示

本目录存放基于 Marp 的仓库导览和 8 周教学 PPT 源文件。每周围一个 Markdown 文件，每周 3 课时，每课时 45 分钟。`00-repository-map.md` 是总览入口，先把图示、案例和样例代码的来源串起来。

课件内容直接来自本仓库的教材和案例：

| 周次 | 文件 | 主题 | 主要来源 |
|---|---|---|---|
| 导览 | [00-repository-map.md](./00-repository-map.html) | 仓库内容地图 | `src/book`、`src/appendix`、`src/experiment`、`samples` |
| 第1周 | [01-hardware-basics.md](./01-hardware-basics.html) | 昇腾310B硬件基础 | 附录1、理论第1章、第2章，samples/chapter3-4 |
| 第2周 | [02-linux-commands.md](./02-linux-commands.html) | Linux命令基础 | 附录2、理论第2章、附录5，samples/chapter5 |
| 第3周 | [03-python-basics.md](./03-python-basics.html) | Python编程基础 | 附录3，samples/case1/fusion_result.json |
| 第4周 | [04-vibe-coding.md](./04-vibe-coding.html) | Vibe Coding基础 | 附录4，samples/case1/fusion_result.json |
| 第5周 | [05-face-recognition.md](./05-face-recognition.html) | 人脸识别 | 案例1，samples/case1 |
| 第6周 | [06-object-tracking.md](./06-object-tracking.html) | 目标跟踪 | 案例2，samples/case2 |
| 第7周 | [07-smart-piano.md](./07-smart-piano.html) | 智能电子琴 | 案例3，samples/case3 |
| 第8周 | [08-chatbot.md](./08-chatbot.html) | 聊天机器人 | 案例9，samples/case9 |

## 图示与代码来源

课件页内的图片均引用仓库中的原始 PNG/JPG，并在图片下方标注相对路径。主要图示入口如下：

| 内容 | 原始图示 | 配套代码 |
|---|---|---|
| 板卡、串口、网络 | [`src/appendix/img1/`](../appendix/img1/) | [`src/appendix/appendix1.md`](../appendix/appendix1.md) |
| CANN 与 ATC | [`src/book/img2/`](../book/img2/) | [`src/book/chapter2.md`](../book/chapter2.md) |
| 人脸考勤流程 | [`src/experiment/img1/`](../experiment/img1/) | [`samples/case1/`](../../samples/case1/) |
| 检测与跟踪流程 | [`src/experiment/img2/`](../experiment/img2/) | [`samples/case2/`](../../samples/case2/) |
| DDSP 模型链 | [`src/experiment/img3/`](../experiment/img3/) | [`samples/case3/`](../../samples/case3/) |
| Palmprint UI 证据 | [`src/experiment/img4/`](../experiment/img4/) | [`samples/case4/`](../../samples/case4/) |
| SDR/RTL 案例 | [`src/experiment/img5/`](../experiment/img5/) | [`samples/case5/`](../../samples/case5/) |
| 局域网相册网络 | [`src/experiment/img7/`](../experiment/img7/) | [`samples/case7/`](../../samples/case7/) |
| YOLO/聊天服务架构 | [`src/experiment/img8/`](../experiment/img8/) | [`samples/case8/`](../../samples/case8/)、[`samples/case9/`](../../samples/case9/) |

构建脚本 [`scripts/build_presentation.mjs`](../../scripts/build_presentation.mjs) 会在生成 HTML 前解析每个本地图片引用：文件不存在时直接失败，并把图片复制到生成目录的 `assets/`，避免发布后出现空白图。

## 本地预览

安装依赖后运行：

```bash
pnpm run docs:slides
```

生成的 HTML 位于：

[打开 00 · 仓库内容地图](./00-repository-map.html)（源文件：`00-repository-map.md`）。

专题课件：

`01` [硬件基础](./01-hardware-basics.html) ·
`02` [Linux 命令](./02-linux-commands.html) ·
`03` [Python 基础](./03-python-basics.html) ·
`04` [Vibe Coding](./04-vibe-coding.html) ·
`05` [人脸识别](./05-face-recognition.html) ·
`06` [目标跟踪](./06-object-tracking.html) ·
`07` [智能电子琴](./07-smart-piano.html) ·
`08` [聊天机器人](./08-chatbot.html)

也可以使用 VS Code 的 Marp 扩展直接打开 `00-repository-map.md` 或任一周课件预览。

## GitHub Pages

仓库构建时会自动把 `src/presentation/*.md` 转换为 Marp HTML。部署后可在以下地址查看：

```text
/Ascend310/presentation/00-repository-map.html
/Ascend310/presentation/00-repository-map.html?view=presenter
```

不带文件名的 `/presentation/` 是普通目录页；带 `.html` 的地址是放映页。打开 `.html` 后按 `f` 可全屏，按 `p` 可打开演讲者视图。

源文件保留在 `src/presentation/`，生成 HTML 不提交到仓库。
