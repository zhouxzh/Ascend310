---
editLink: false
---

# 教材配套演示

本目录提供《昇腾310B实战》全书导览和配套专题教学演示。`00` 是整本教材的全书导览页，随后按单元进入专题演示。主页只展示放映入口；图片、流程图、网络结构图和代码引用说明均放在对应的课件页内。

## 课件目录

| 单元 | 放映版（GitHub Pages） | 主题 | 内容来源 |
|---|---|---|---|
| 全书导览 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/00-repository-map.html) | 昇腾310B实战——从入门到精通边缘计算与人工智能 | `src/book`、`src/appendix`、`src/experiment`、`samples` |
| 附录 1 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/01-hardware-basics.html) | 开发板与基础环境 | 附录1、理论第1章、第2章、`samples/chapter3-4` |
| 附录 2 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/02-linux-commands.html) | 昇腾 310B Linux 操作与命令教程 | 附录2、理论第2章、附录5、`samples/chapter5` |
| 附录 3 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/03-python-basics.html) | Python 编程基础 | 附录3、`samples/case1/fusion_result.json` |
| 附录 4 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/04-vibe-coding.html) | Vibe Coding 基础 | 附录4、`samples/case1/fusion_result.json` |
| 案例 1 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/05-face-recognition.html) | 边缘人脸考勤 | 案例1、`samples/case1` |
| 案例 2 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/06-object-tracking.html) | 目标跟踪检测 | 案例2、`samples/case2` |
| 案例 3 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/07-smart-piano.html) | Ascend 310B DDSP 智能电子琴 | 案例3、`samples/case3` |
| 案例 9 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/08-chatbot.html) | 在昇腾 310B 上复现中文文本聊天 | 案例9、`samples/case9` |

## 本地预览

在仓库根目录运行：

```bash
pnpm install
pnpm run docs:slides
pnpm run docs:dev
```

本地放映入口：

```text
http://localhost:8080/Ascend310/presentation/00-repository-map.html
http://localhost:8080/Ascend310/presentation/01-hardware-basics.html
```

## GitHub Pages

`/Ascend310/presentation/` 是普通目录页；表格中的 `.html` 地址是 Marp 放映页。打开放映页后按 `f` 全屏，按 `p` 打开演讲者视图。

生成的 HTML 不提交到仓库，Pages 会在每次构建时自动生成最新课件。
