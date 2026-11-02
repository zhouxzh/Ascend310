# Case 9 审查报告：小智 OpenAI 兼容 RAG 网关

## 审查范围与方法

审查基准为当前工作树。审查只读检查了 `src/experiment/case9.md`、
`samples/case9/`、生成的 `latex/cases/case9.tex` 和 `latex/book.log`；没有在
开发机运行 CANN、ATC、ACL/OM、本地音频或小智设备链路。

## 总体结论

Case 9 的网关部分已经建立了较清楚的边界：小智服务端负责设备协议，网关负责 Bearer 鉴权、词法检索和 OpenAI/SSE 转发，并明确不把未完成的 NPU LLM 当作已验证功能。主要问题在于教材正文与样例 README 的范围不一致、上游模型示例不一致，以及样例中的本地音频服务默认以无鉴权地址监听。PDF 还把 Mermaid 检索图原样输出为代码块。若不先澄清范围和安全前提，读者容易把实验性的本地聊天路径误认为本案例已完成的主链路。

## 严重性定义

| 等级 | 含义 |
| --- | --- |
| P1 | 可能造成错误部署、未授权设备控制或阻断正式发布 |
| P2 | 教材范围、证据链或出版排版不一致 |
| P3 | 后续可改进的表达或维护问题 |

## 已核实问题

### P1-1 教材正文没有覆盖样例中的本地聊天和 ACL/OM 子系统

**证据**

- src/experiment/case9.md 第 1--20 行把案例限定为小智服务端调用的无状态 OpenAI 兼容文本网关；第 201--213 行的源代码树只列 app.py、config.py、retrieval.py、upstream.py、knowledge、脚本和测试。
- samples/case9/README.md 第 3--5 行声明样例包含两个独立服务；第 18--71 行详细介绍 local_app.py、浏览器 PTT、音频设备、sherpa-onnx 和板端 ACL/OM LLM。
- 样例还包含 local_app.py、local_session.py、audio_io.py、acl_om_service.py、frontend/ 和多份本地聊天部署文档，这些内容没有在正文的范围、架构图或源码结构中出现。

**影响**

读者从教材链接进入 samples/case9 后，会遇到一条未在正文解释的音频和本地生成式 LLM 路径；这会混淆“本案例已实现的网关”与“尚待板端门禁的实验子系统”，也会使验证结果的边界失真。

**修复建议**

1. 若本章只讲网关，在 README 开头明确本地聊天是独立、未纳入本章验收的附加实验，并链接到独立附录/工程文档。
2. 若本章要覆盖两套服务，新增小节说明进程、端口、数据流、鉴权、板端门禁和失败边界，不能只在 README 中给出命令。
3. 让源代码树与实际样例结构一致，至少标明哪些文件属于网关、哪些文件属于实验性本地聊天。

**验证要求**

在 clean clone 中按正文路径只启动网关，按 README 路径启动本地聊天；确认两者互不隐式依赖，并在测试报告中分别记录网关协议、音频闭环和 ACL/OM 推理证据。

### P1-2 本地聊天默认暴露无浏览器鉴权的音频控制面

**证据**

- samples/case9/local_app.py 第 158--164 行将 host 默认设为 0.0.0.0、端口设为 7862。
- 第 211--220 行从 LOCAL_CHAT_HOST 读取配置，默认仍为 0.0.0.0；只校验网关 URL 为 loopback，没有对本地聊天监听地址施加同等限制。
- samples/case9/README.md 第 28--34 行明确说明没有浏览器 API 鉴权，同一 LAN 主机可控制开发板麦克风和 USB 喇叭。

**影响**

一旦用户按常规方式把服务放入共享网络，任何可达客户端都可能调用 WebSocket 控制消息，触发录音、播放或会话操作。README 的文字警告不能替代安全默认值；这与教材对“安全网关”的整体印象不一致。

**修复建议**

1. 默认绑定 127.0.0.1，只有显式选择可信实验网络时才允许 0.0.0.0。
2. 为浏览器入口增加短期会话令牌或反向代理认证，并限制 WebSocket 来源、Origin 和消息频率。
3. 在正文明确区分网关 Bearer token 与本地聊天控制面的认证，不要暗示前者保护后者。

**验证要求**

分别从本机、同网段未授权客户端和带有效令牌的客户端测试 health、静态页面、WebSocket、ptt_start/ptt_stop 和 clear；确认默认监听地址、拒绝状态码和音频设备状态符合安全合同。

### P1-3 正文与样例的上游模型配置不一致

**证据**

- src/experiment/case9.md 第 121--130 行的 .env 示例使用 UPSTREAM_MODEL=Qwen2.5-3B-Instruct。
- samples/case9/.env.example 第 5--11 行和 README.md 第 89--109 行固定使用 qwen1.5-0.5b-chat-acl-om，并把它描述为板端 no-Torch ACL/OM 服务的模型。
- README 第 184--187 行还说明该 Qwen1.5 资产只有候选 ONNX，尚无 Ascend 310B4 的预生成 OM/ATC 证明。

**影响**

照正文复制配置后，请求可能被转发给不存在或不匹配的上游模型；同时读者无法判断示例是云端 OpenAI 兼容服务、板端候选服务，还是已验收的生产模型。配置错误会在启动或首次请求时才暴露。

**修复建议**

1. 选择一个与本章目标一致的默认值，并让正文、.env.example、README、测试 stub 和部署脚本共用同一模型 ID。
2. 若正文只演示通用 OpenAI 上游，使用明确的占位模型名并注明需要按上游服务替换；不要把板端候选模型写成默认生产配置。
3. 把模型候选、ONNX/ATC/OM 状态和网关协议验收分开记录。

**验证要求**

执行 app.py --check-config、/v1/models、普通 JSON completion 和 SSE completion；验证配置中的模型名会被固定映射，客户端不能覆盖上游 URL、模型名或密钥。

### P2-1 检索 Mermaid 在 PDF 中没有转换为图示

**证据**

- src/experiment/case9.md 第 76--94 行是 mermaid flowchart。
- 当前 latex/cases/case9.tex 第 133 行仍出现 flowchart 文本，没有对应的 includegraphics 静态图。
- latex/book.log 的当前构建还报告 Case 9 相关 overfull hbox；长 URL 和代码行需要重新检查换行。

**影响**

电子书读者会看到 Mermaid 源码而不是“小智请求—检索—上下文注入—SSE”关系图，图示信息与在线页面不一致；长 URL 溢出还可能突破正文版心。

**修复建议**

生成白底、高对比、可缩放的确定性静态图，并在章节中只保留最终 PNG/矢量图引用；对参考文献 URL 使用可断行链接或短标题。不要手改 latex/cases/case9.tex。

**验证要求**

重新运行转换脚本，检查 case9.tex 不再包含 flowchart/sequenceDiagram 原文；在 PDF 中渲染对应页，检查图题、箭头、代码和参考文献换行。

## 验证要求与证据边界/未验证项

- 正文第 181--199 行明确声明本案例没有执行 CANN、ATC、ACL、OM、摄像头或设备测试；这条边界应保留，不能因网关协议验收通过而推导出本地 NPU LLM 或小智语音闭环通过。
- samples/case9/docs/01-board-gateway-acceptance.md 只应作为网关 HTTP/SSE 协议证据；samples/case9/docs/07-acl-om-validation-record.md 已记录下载阻塞时，应继续保持 blocked，而不是回填性能或质量数字。
- 本次审查没有运行 XiaoZhi server、ASR、TTS、音频设备或板端模型。任何声学质量、端到端时延和 NPU 生成性能都仍待目标板端的独立报告。

## 发布前最小验收顺序

1. 先明确教材只覆盖网关还是同时覆盖本地聊天，并同步 README、源码树和导航。
2. 统一上游模型配置，给候选 ACL/OM 路径标注证据状态。
3. 收紧 local_app 默认监听和 WebSocket 认证，再做跨主机安全测试。
4. 替换 PDF Mermaid、修复 URL 溢出，并重新检查生成日志。
