---
marp: true
theme: ascend310
paginate: true
size: 16:9
author: "周贤中"
email: "zhouxzh@gdut.edu.cn"
header: "《昇腾310B实战》教材配套演示"
footer: "案例 9：智能聊天机器人"
---

<!-- _class: cover -->

# 案例 9：智能聊天机器人

<div class="byline">周贤中 · zhouxzh@gdut.edu.cn</div>

## 把本地模型变成可验收的对话服务

对应教材：[GitHub Pages · 案例 9](https://310b.cn/experiment/case9.html)

配套样例：[GitHub · samples/case9](https://github.com/zhouxzh/Ascend310/tree/main/samples/case9)

<!--
授课提示：先说明本讲不是“跑通一次生成”就结束，而是把模型、服务链和证据边界一起交付。
-->

---

<!-- _class: center -->

## 本讲目标与路线

本讲按这条路线走：先把职责和工件锁说清，再启动服务、验收 JSON/SSE，最后用 20T 实测把机器通过和质量分开。

![案例 9 本讲路线：架构、模型锁、环境、服务、API、20T 验收、二次开发](../experiment/img9/case9_lecture_route.png)

<div class="source">教材：<a href="https://310b.cn/experiment/case9.html"><code>src/experiment/case9.md</code></a></div>

<!--
授课提示：强调 8T/20T 独立证据，本讲的实测页只讲 20T。
-->

---

<!-- _class: section -->

# 01

## 架构与模型锁

---

## 三个进程，三种网络边界

| 进程 | 监听 | 职责 | 安全边界 |
| --- | --- | --- | --- |
| 浏览器 UI | `0.0.0.0:7865` | 对话、模型选择、SSE 展示 | 仅可信局域网，无登录 |
| 网关 | `127.0.0.1:7861` | Bearer 鉴权、协议适配、限流 | 不直接暴露 |
| MindSpore worker | `127.0.0.1:8090` | 单模型加载、串行生成 | 不直接暴露 |

浏览器不知道网关密钥；密钥由 UI 服务端持有。教学模型切换会重启目标 worker，而不是在请求中接受任意路径或 Python 代码。

<div class="source">教材：<a href="https://310b.cn/experiment/case9.html"><code>src/experiment/case9.md</code></a> · 系统架构</div>

<!--
授课提示：先让学生指出哪些端口可被局域网访问，再解释为什么不能把 7865 暴露到公网。
-->

---

## 模型锁不是模型名称

发布工件锁固定：

- `model.safetensors` 与 tokenizer/词表/配置
- revision、文件大小和 SHA-256
- 目标 SoC 与运行时边界

```text
固定 revision -> .part 临时文件 -> 大小核验 -> SHA-256 -> 原子改名 -> worker 加载
```

正式发布默认模型仍是 Qwen1.5-0.5B；本轮 20T 教学验收使用 `qwen2-0.5b-instruct-mindspore`，两者不能混写。

<div class="source">教材：<a href="https://310b.cn/experiment/case9.html"><code>src/experiment/case9.md</code></a> · 发布模型与工件锁</div>

<!--
授课提示：问学生“模型名相同但 revision 不同，能否复现？”引到工件锁。
-->

---

<!-- _class: section -->

# 02

## 环境、服务与调用

---

## 启动前必须证明环境门

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate <validated-env>
source /usr/local/Ascend/ascend-toolkit/set_env.sh
bash scripts/verify/release-preflight.sh
```

预检要看到：

- Python 解释器与 `PYTHONNOUSERSITE`
- `import acl`、MindSpore、MindNLP
- 设备节点、`npu-smi` 与模型哈希
- 网关配置

<div class="source">教材：<a href="https://310b.cn/experiment/case9.html"><code>src/experiment/case9.md</code></a> · 环境准备</div>

<!--
授课提示：裸 SSH 能 import 不等于服务进程能 import；启动进程必须自己 source CANN。
-->

---

## 服务入口与一次最小调用

三个进程分别启动：

```bash
bash scripts/run/start-worker.sh
bash scripts/run/start-gateway.sh
bash scripts/run/start-ui.sh
```

公开模型名固定为 `ascend-edge-chat`，OpenAI 兼容入口：

```text
GET  /v1/models
POST /v1/chat/completions
```

网关只允许 greedy 解码、单请求串行和受限输出预算；超时或 worker 不可用时 fail-closed，不自动切 CPU 或云端。

<div class="source">教材：<a href="https://310b.cn/experiment/case9.html"><code>src/experiment/case9.md</code></a> · 启动服务与接口</div>

<!--
授课提示：提醒 Bearer 密钥只在服务器端；示例命令不要写入截图或 git。
-->

---

## API 验收必须同时覆盖 JSON 与 SSE

| 检查 | 预期 |
| --- | --- |
| `/v1/models` | 只暴露 `ascend-edge-chat` |
| 错误 Bearer | 401，不泄露上游细节 |
| 未知模型 | 拒绝 |
| JSON completion | 非空文本、合法 finish reason |
| SSE completion | start、delta、terminal、完整文本 |
| 长度门 | `stop` 或明确 `length`，不能静默丢弃 |

网页、网关和 worker 都要能独立定位；端口 200 只说明有进程在监听，不说明模型已经生成。

<div class="source">教材：<a href="https://310b.cn/experiment/case9.html"><code>src/experiment/case9.md</code></a> · 验收方法</div>

<!--
授课提示：把“HTTP 200”和“数值/文本验收”明确分开。
-->

---

<!-- _class: section -->

# 03

## 20T 实测与质量边界

---

## 2026-10-18 的 20T 验收记录

| 项目 | 实测 |
| --- | --- |
| 硬件 | Ascend310B1 / 20T |
| 运行时 | CANN 8.0.0，Python 3.9.2 |
| 教学 profile | `qwen2-0.5b-instruct-mindspore` |
| UI / 网关 / worker | 200 / 健康 / 健康 |
| JSON + SSE | 均完成 |
| 长度门 | 8/16/32 `length`；64 `stop`（46 token） |
| 稳定性 | 10/10 短请求通过 |
| 单性能样本 | 首 token 723.051 ms；完成 1370.604 ms；1.459 token/s |

这些数字只属于这次 20T、这个模型 revision、这组提示和当前环境。

<div class="source">教材：<a href="https://310b.cn/experiment/case9.html"><code>src/experiment/case9.md</code></a> · 板端实测记录</div>

<!--
授课提示：强调只有单性能样本，不能把 723 ms 写成 p50，也不能外推到 8T。
-->

---

## 429、截断和质量是三种不同问题

- 第一次无间隔验收在稳定性段收到 HTTP 429：请求 body 突发，属于服务 admission
- 加请求间隔和流式收尾等待后，同一服务完成验收
- `finish_reason=length` 表示预算耗尽，不是 NPU 崩溃
- 直接中文提问曾返回英文道歉句：机器路径完成，中文语义质量不通过该次期望
- 质量状态必须经过固定提示、完整响应和人工评审，不能由“页面有回复”替代

> 结论：技术门 `observed-pass`；中文质量、长稳 p50/p95、并发和 8T 仍是 `untested` 或待复核。

<div class="source">教材：<a href="https://310b.cn/experiment/case9.html"><code>src/experiment/case9.md</code></a> · 长度、完整性与质量</div>

<!--
授课提示：让学生复述“429 是测试客户端突发，不是模型失败”；同时不要反过来掩盖质量失败。
-->

---

<!-- _class: visual -->

## 20T 浏览器界面证据

![20T 版 Ascend Edge Chat 前端页面](../experiment/img9/case9-board-20t-ui.png)

<div class="source">图片：<a href="../experiment/img9/case9-board-20t-ui.png"><code>src/experiment/img9/case9-board-20t-ui.png</code></a>；接口证据见 <a href="https://310b.cn/experiment/case9.html"><code>src/experiment/case9.md</code></a></div>

<!--
授课提示：截图只证明页面和本地服务展示路径，不证明回答质量、长上下文或性能。
-->

---

## 二次开发不是改一个模型名

新模型要经过：

1. 固定 revision、工件大小和 SHA-256
2. tokenizer、chat template 和目标运行时
3. 板端 loader、NPU 初始化和单 token 生成
4. JSON/SSE、长度、稳定性、性能和人工质量门
5. 只在证据满足时更新教学目录或正式发布配置

页面只接受已注册的 profile ID，不接受任意路径、shell 命令或 Python 表达式。

<div class="source">教材：<a href="https://310b.cn/experiment/case9.html"><code>src/experiment/case9.md</code></a> · 二次开发</div>

<!--
授课提示：把“可运行模型”和“正式发布模型”分开；教学目录不是质量背书。
-->

---

## 本章小结

- 架构边界：UI 面向局域网，网关和 worker 保持回环
- 工件边界：模型 revision、文件和哈希必须锁定
- 服务边界：JSON/SSE、串行、限流、fail-closed
- 证据边界：机器通过、长度、稳定性、性能、质量分开记录
- 20T 本轮机器验收通过；8T、并发、p50/p95 和中文语义质量未继承

> 下一讲：把已验证的对话服务接入完整案例应用链路。

<div class="source">教材：<a href="https://310b.cn/experiment/case9.html"><code>src/experiment/case9.md</code></a> · 小结</div>

<!--
授课提示：收束时让学生说出一个 observed-pass 和一个仍为 untested 的项。
-->
