# case9 双板调试记录

## 范围

case9 的发布目标明确为 **Ascend310B1 / 20T**；本轮在 20T 上启用已验证的 `case9-ms240-cann80` 环境。8T 不是该版本的发布目标，本轮没有把 20T 结果混入 8T，也没有冒充实测。

## 运行

- 20T 部署：`/home/HwHiAiUser/ascend-edge-chat-v0.1.0`
- Conda：`case9-ms240-cann80`
- 模型：`/home/HwHiAiUser/case9-mindspore-chat/artifacts/models/qwen1.5-0.5b-chat`
- 数据目录：`/home/HwHiAiUser/case9-mindspore-chat/artifacts`
- 进程：MindSpore worker `:8090`、gateway `:7861`、UI `:7865`

## 结果

| 项目 | 20T |
| --- | --- |
| release preflight | 通过；MindSpore 2.4.10、MindNLP 0.4.1、ACL 导入成功 |
| worker | 启动并监听 `127.0.0.1:8090` |
| gateway | 启动并监听 `127.0.0.1:7861` |
| UI | HTTP 200，监听 `0.0.0.0:7865` |
| 真实 chat completion | 通过；返回 `finish_reason=length`，16 completion tokens |
| UI 截图 | 通过，1440x900 |

请求使用固定 Qwen1.5-0.5B Chat 模型和低 token 预算；返回内容是生成结果，不代表准确率或长上下文能力。Ui 未启用登录，仅用于可信局域网。

## 截图

- [20T Ascend Edge Chat](board-20t.png)

## 证据

- [worker 日志](worker.log)
- [gateway 日志](gateway.log)
- [UI 日志](ui.log)
- [chat completion 原始响应](chat-response.json)
- [Uvicorn/截图原始结果](capture.json)
