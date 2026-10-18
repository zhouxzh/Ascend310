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

## 从本地模型到可复核的对话服务


对应教材：`src/experiment/case9.md`

配套样例：`samples/case9`

<!--
授课提示：用一句话说明本讲要解决的工程问题，再核对教材和样例路径。
-->

---

<!-- _class: center -->

## 本讲目标与路线

- 理解本地模型服务的系统架构
- 用工件锁固定发布模型与版本
- 启动 OpenAI 兼容接口并完成调用
- 按验收方法记录回答质量与失败边界
- 实测限于 20T / Ascend310B1；8T / Ascend310B4 不继承本讲结论

> 本讲主线：架构 → 模型锁 → 服务 → 调用 → 验收

<div class="source">教材：<a href="../experiment/case9.md"><code>src/experiment/case9.md</code></a></div>

<!--
先给学生路线图；后续每页都对应这里的一项，最后回到小结。
-->
---

<!-- _class: section -->

# 01

## 架构与模型锁
---

<!-- _class: center -->

## 系统架构

- 三个端口承担不同角色：
- 网页没有浏览器登录功能。因此，同一可达网段中的主机都可以发送消息。不要做端口映射，不要将 7865 放到公网反向代理之后，也不要在不可信网络使用本页面。
- 7865：0.0.0.0

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../experiment/case9.md"><code>src/experiment/case9.md</code></a> · 2. 系统架构</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 发布模型与工件锁

- 下载或离线导入都必须执行相同的完整性流程：
- model.safetensors：模型权重
- tokenizer.json：tokenizer 数据

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../experiment/case9.md"><code>src/experiment/case9.md</code></a> · 3. 发布模型与工件锁</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 环境准备

- 本节聚焦：4. 环境准备 的输入、操作与证据边界。
- 先把输入、版本与命令固定下来，再判断结果是否可复现。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../experiment/case9.md"><code>src/experiment/case9.md</code></a> · 4. 环境准备</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 获取或导入模型

- 通过离线目录导入时：
- 两种方法都会先写入临时文件，再检查服务端声明长度、本地实际长度和 SHA-256。校验失败的临时文件会被删除，已验证的目标文件不会被覆盖。可在启动前再次核验：
- 本节聚焦：5. 获取或导入模型 的输入、操作与证据边界。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../experiment/case9.md"><code>src/experiment/case9.md</code></a> · 5. 获取或导入模型</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 启动服务

- 分别打开三个终端，在项目根目录执行：
- 本节聚焦：6. 启动服务 的输入、操作与证据边界。
- 先把输入、版本与命令固定下来，再判断结果是否可复现。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../experiment/case9.md"><code>src/experiment/case9.md</code></a> · 6. 启动服务</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: section -->

# 02

## 服务、调用与验收
---

<!-- _class: center -->

## 调用 OpenAI 兼容接口

- 网关公开模型名固定为 ascend-edge-chat。调用方必须携带 Bearer 密钥。
- 将 stream 设置为 true 可获得 Server-Sent Events。网关会将 worker 的内部模型名改写为公开模型名，再把增量返回给客户端。
- 仅接受 system、user、assistant 三种角色；

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../experiment/case9.md"><code>src/experiment/case9.md</code></a> · 7. 调用 OpenAI 兼容接口</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: visual -->

## 20T 对话界面证据

![20T 版 Ascend Edge Chat 前端页面](../experiment/img9/case9-board-20t-ui.png)

<div class="source">图片：<a href="../experiment/img9/case9-board-20t-ui.png"><code>src/experiment/img9/case9-board-20t-ui.png</code></a>；说明：<a href="../experiment/case9.md#9-教学模型的实测结果与原因"><code>src/experiment/case9.md</code></a></div>

<!--
界面截图只证明 20T 前端页面与本地服务的展示路径；回答质量、模型速度和长稳结论必须回到实测记录，不能从截图外推。
-->

---

## 验收方法

- 不能用“服务已启动”代替模型验收。每个目标板都应独立记录以下证据：
- SoC、CANN、Python、MindSpore、MindNLP、驱动和模型 SHA-256；
- worker NPU 初始化和单 token 生成；

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../experiment/case9.md"><code>src/experiment/case9.md</code></a> · 8. 验收方法</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 教学模型的实测结果与原因

- 下表是本案例已有板端证据的摘要。observed-pass 只说明机器和接口路径
- 通过；它不等同于中文回答优秀。相反，quality_status=failed 也不等同于
- 学生应先复制原始响应，再分析 tokenizer、chat template、finish_reason、

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../experiment/case9.md"><code>src/experiment/case9.md</code></a> · 9. 教学模型的实测结果与原因</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 二次开发

- 新模型不是通过任意请求参数注入路径或 Python 代码。教学模型通过受控 registry
- 和目录加入页面；正式发布模型仍需要独立锁文件。新模型需要实现
- BackendProvider 协议，并提供：

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../experiment/case9.md"><code>src/experiment/case9.md</code></a> · 10. 二次开发</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 本章小结

- 模型、服务和接口是三份独立合同
- 工件锁让部署可复现、可回退
- 教学模型的实测结果必须绑定运行条件
- 未验证项必须标为 untested 或 blocked。

> 下一讲：把对话服务接入更完整的应用链路。

<div class="source">教材：<a href="../experiment/case9.md"><code>src/experiment/case9.md</code></a></div>

<!--
用三句话收束；提醒学生把未验证项写成 untested 或 blocked。
-->
