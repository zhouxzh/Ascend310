# case6 双板调试记录

## 运行

- 8T/20T：`/home/HwHiAiUser/Ascend310/cases/case6`
- 启动：`python app.py --port 7860`
- 页面：Gradio，`http://<board>:7860/`

## 结果

| 项目 | 8T | 20T |
| --- | --- | --- |
| HTTP `/` | 200 | 200 |
| Gradio | 启动成功 | 启动成功 |
| ResNet18 CPU 权重 | 启动时下载成功 | 启动时下载成功 |
| 页面截图 | 通过 | 通过 |

本次只验证 Gradio 初始页面和启动链路；没有上传道路图像，因此车道线检测和场景分类推理均记为 `untested`。

## 截图

- [8T 页面](board-8t.png)
- [20T 页面](board-20t.png)

## 证据

- [8T 原始日志](board-8t.log)
- [20T 原始日志](board-20t.log)
