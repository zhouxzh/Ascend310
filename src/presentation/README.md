# 教学演示

本目录存放基于 Marp 的 8 周教学 PPT 源文件。每周围一个 Markdown 文件，每周 3 课时，每课时 45 分钟。

课件内容直接来自本仓库的教材和案例：

| 周次 | 文件 | 主题 | 主要来源 |
|---|---|---|---|
| 第1周 | [week01.md](./week01.md) | 昇腾310B硬件基础 | 附录1、理论第1章、第2章，samples/chapter3-4 |
| 第2周 | [week02.md](./week02.md) | Linux命令基础 | 附录2、理论第2章、附录5，samples/chapter5 |
| 第3周 | [week03.md](./week03.md) | Python编程基础 | 附录3，samples/case1/fusion_result.json |
| 第4周 | [week04.md](./week04.md) | Vibe Coding基础 | 附录4，samples/case1/fusion_result.json |
| 第5周 | [week05.md](./week05.md) | 人脸识别 | 案例1，samples/case1 |
| 第6周 | [week06.md](./week06.md) | 目标跟踪 | 案例2，samples/case2 |
| 第7周 | [week07.md](./week07.md) | 智能电子琴 | 案例3，samples/case3 |
| 第8周 | [week08.md](./week08.md) | 聊天机器人 | 案例9，samples/case9 |

## 本地预览

安装依赖后运行：

```bash
pnpm run docs:slides
```

生成的 HTML 位于：

```text
src/.vuepress/public/presentation/week01.html
```

也可以使用 VS Code 的 Marp 扩展直接打开 `week01.md` 预览。

## GitHub Pages

仓库构建时会自动把 `src/presentation/*.md` 转换为 Marp HTML。部署后可在以下地址查看：

```text
/Ascend310/presentation/week01.html
```

源文件保留在 `src/presentation/`，生成 HTML 不提交到仓库。
