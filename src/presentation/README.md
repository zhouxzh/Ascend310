# 教学演示

本目录包含仓库导览和 8 周教学课件。这里有两类入口，请不要混用：

- `*.md` 是可审阅、可修改的 Marp 源文件，源文件在 GitHub 中维护。
- `*.html` 是构建生成的放映文件，只在本地构建产物和 GitHub Pages 中提供。

课件内容直接来自本仓库的教材、附录、实践案例和 `samples/` 代码。`00-repository-map.md` 只负责串起这些来源，不会移动或替换原有教材。

## 课件目录

| 周次 | 源文件（GitHub） | 放映版（GitHub Pages） | 主题 | 主要来源 |
|---|---|---|---|---|
| 导览 | [00-repository-map.md](https://github.com/zhouxzh/Ascend310/blob/main/src/presentation/00-repository-map.md) | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/00-repository-map.html) | 仓库内容地图 | `src/book`、`src/appendix`、`src/experiment`、`samples` |
| 第1周 | [01-hardware-basics.md](https://github.com/zhouxzh/Ascend310/blob/main/src/presentation/01-hardware-basics.md) | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/01-hardware-basics.html) | 昇腾310B硬件基础 | 附录1、理论第1章、第2章，`samples/chapter3-4` |
| 第2周 | [02-linux-commands.md](https://github.com/zhouxzh/Ascend310/blob/main/src/presentation/02-linux-commands.md) | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/02-linux-commands.html) | Linux命令基础 | 附录2、理论第2章、附录5，`samples/chapter5` |
| 第3周 | [03-python-basics.md](https://github.com/zhouxzh/Ascend310/blob/main/src/presentation/03-python-basics.md) | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/03-python-basics.html) | Python编程基础 | 附录3，`samples/case1/fusion_result.json` |
| 第4周 | [04-vibe-coding.md](https://github.com/zhouxzh/Ascend310/blob/main/src/presentation/04-vibe-coding.md) | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/04-vibe-coding.html) | Vibe Coding基础 | 附录4，`samples/case1/fusion_result.json` |
| 第5周 | [05-face-recognition.md](https://github.com/zhouxzh/Ascend310/blob/main/src/presentation/05-face-recognition.md) | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/05-face-recognition.html) | 人脸识别 | 案例1，`samples/case1` |
| 第6周 | [06-object-tracking.md](https://github.com/zhouxzh/Ascend310/blob/main/src/presentation/06-object-tracking.md) | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/06-object-tracking.html) | 目标跟踪 | 案例2，`samples/case2` |
| 第7周 | [07-smart-piano.md](https://github.com/zhouxzh/Ascend310/blob/main/src/presentation/07-smart-piano.md) | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/07-smart-piano.html) | 智能电子琴 | 案例3，`samples/case3` |
| 第8周 | [08-chatbot.md](https://github.com/zhouxzh/Ascend310/blob/main/src/presentation/08-chatbot.md) | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/08-chatbot.html) | 聊天机器人 | 案例9，`samples/case9` |

## 图示与代码来源

课件页内的图片引用仓库中的原始 PNG/JPG；下面的链接全部指向 GitHub 源文件或目录，因此在 GitHub README 和 Pages 页面中都不会变成站点的错误路由。

| 内容 | 原始图示 | 配套代码 |
|---|---|---|
| 板卡、串口、网络 | [src/appendix/img1/](https://github.com/zhouxzh/Ascend310/tree/main/src/appendix/img1) | [src/appendix/appendix1.md](https://github.com/zhouxzh/Ascend310/blob/main/src/appendix/appendix1.md) |
| CANN 与 ATC | [src/book/img2/](https://github.com/zhouxzh/Ascend310/tree/main/src/book/img2) | [src/book/chapter2.md](https://github.com/zhouxzh/Ascend310/blob/main/src/book/chapter2.md) |
| 人脸考勤流程 | [src/experiment/img1/](https://github.com/zhouxzh/Ascend310/tree/main/src/experiment/img1) | [samples/case1/](https://github.com/zhouxzh/Ascend310/tree/main/samples/case1) |
| 检测与跟踪流程 | [src/experiment/img2/](https://github.com/zhouxzh/Ascend310/tree/main/src/experiment/img2) | [samples/case2/](https://github.com/zhouxzh/Ascend310/tree/main/samples/case2) |
| DDSP 模型链 | [src/experiment/img3/](https://github.com/zhouxzh/Ascend310/tree/main/src/experiment/img3) | [samples/case3/](https://github.com/zhouxzh/Ascend310/tree/main/samples/case3) |
| Palmprint UI 证据 | [src/experiment/img4/](https://github.com/zhouxzh/Ascend310/tree/main/src/experiment/img4) | [samples/case4/](https://github.com/zhouxzh/Ascend310/tree/main/samples/case4) |
| SDR/RTL 案例 | [src/experiment/img5/](https://github.com/zhouxzh/Ascend310/tree/main/src/experiment/img5) | [samples/case5/](https://github.com/zhouxzh/Ascend310/tree/main/samples/case5) |
| 局域网相册网络 | [src/experiment/img7/](https://github.com/zhouxzh/Ascend310/tree/main/src/experiment/img7) | [samples/case7/](https://github.com/zhouxzh/Ascend310/tree/main/samples/case7) |
| YOLO/聊天服务架构 | [src/experiment/img8/](https://github.com/zhouxzh/Ascend310/tree/main/src/experiment/img8) | [samples/case8/](https://github.com/zhouxzh/Ascend310/tree/main/samples/case8)、[samples/case9/](https://github.com/zhouxzh/Ascend310/tree/main/samples/case9) |

构建脚本 [scripts/build_presentation.mjs](https://github.com/zhouxzh/Ascend310/blob/main/scripts/build_presentation.mjs) 会在生成 HTML 前检查每个本地图片引用，复制图片到生成目录的 `assets/`，并把放映页中的源码链接指向 GitHub；图片缺失时构建会直接失败。

## 本地预览

在仓库根目录运行：

```bash
pnpm install
pnpm run docs:slides
pnpm run docs:dev
```

生成的放映文件位于 `src/.vuepress/public/presentation/`，本地站点地址为：

```text
http://localhost:8080/Ascend310/presentation/00-repository-map.html
http://localhost:8080/Ascend310/presentation/01-hardware-basics.html
```

也可以使用 VS Code 的 Marp 扩展直接打开任一 `src/presentation/*.md` 源文件预览。

## GitHub Pages

`/Ascend310/presentation/` 是普通的 VuePress 目录页；带 `.html` 的地址是 Marp 放映页。当前放映入口：

```text
https://zhouxzh.github.io/Ascend310/presentation/00-repository-map.html
https://zhouxzh.github.io/Ascend310/presentation/01-hardware-basics.html
```

打开放映页后按 `f` 全屏，按 `p` 打开演讲者视图；浏览器刷新或直接复制上述 URL 仍然会回到同一张课件。生成的 HTML 不提交到仓库，源码始终保留在 `src/presentation/`。
