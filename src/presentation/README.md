---
editLink: false
---

# 教学演示

本目录提供《昇腾310B实战》全书导览和配套专题教学演示。`00` 是整本教材的全书导览页，随后按单元进入专题演示。主页只展示放映入口；图片、流程图、网络结构图和代码引用说明均放在对应的课件页内。

## 课件目录

| 单元 | 放映版（GitHub Pages） | 主题 | 内容来源 |
|---|---|---|---|
| 00 全书导览 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/00-repository-map.html) | 昇腾310B实战——从入门到精通边缘计算与人工智能 | `src/book`、`src/appendix`、`src/experiment`、`samples` |
| 第1周 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/01-hardware-basics.html) | 昇腾310B硬件基础 | 附录1、理论第1章、第2章、`samples/chapter3-4` |
| 第2周 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/02-linux-commands.html) | Linux命令基础 | 附录2、理论第2章、附录5、`samples/chapter5` |
| 第3周 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/03-python-basics.html) | Python编程基础 | 附录3、`samples/case1/fusion_result.json` |
| 第4周 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/04-vibe-coding.html) | Vibe Coding基础 | 附录4、`samples/case1/fusion_result.json` |
| 第5周 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/05-face-recognition.html) | 人脸识别 | 案例1、`samples/case1` |
| 第6周 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/06-object-tracking.html) | 目标跟踪 | 案例2、`samples/case2` |
| 第7周 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/07-smart-piano.html) | 智能电子琴 | 案例3、`samples/case3` |
| 第8周 | [打开放映版](https://zhouxzh.github.io/Ascend310/presentation/08-chatbot.html) | 聊天机器人 | 案例9、`samples/case9` |

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
