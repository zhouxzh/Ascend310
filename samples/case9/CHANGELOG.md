# 变更日志

此处记录Ascend Edge Chat的所有重要变更。

## v0.2.0 - 2026-10-25

- 为Ascend310B4 / 8T新增第二个正式发布后端`acl-static-kv`。
- 锁定Qwen2.5-0.5B-Instruct static-KV ONNX、tokenizer、契约、B4 OM、许可证和公开Hugging Face revision。
- 新增纳入版本跟踪的51输入/49输出ACL运行时和公开产物验证器。
- 为8T后端新增固定的`:8084 -> :7861 -> :7865`生命周期和验收入口。
- MindSpore static-KV的必须条件边界保持不变：主MindSpore路径不需要OM转换。
- 8T后端只公开`ascend-edge-chat`；其常规UI提供64/80输出token选项，默认64，上限80。20T MindSpore配置默认128，上限256。
- 基于公开源码的8T验收通过，覆盖9个锁定HF产物、10/10次稳定性请求、长度检查、JSON/SSE/UI契约和30个样本的双token性能集。
- 记录了应用级watchdog策略和上下文公式。禁用watchdog的测试在8T上完成256 token，在20T上完成853 token；这些是容量证据，不是发布默认值。
- 为12个唯一公开量化Qwen ONNX候选新增固定revision锁和静态筛选工具。所有候选均命中不受支持的量化或`com.microsoft`算子；B4/B1上6次代表性ATC运行均未生成OM。

## v0.1.0 - 2026-10-17

- 引入可移植的`ascend_edge_chat`包和`ascend-edge-chat` CLI。
- 发布一个锁定MindSpore配置：面向Ascend 310B1 / 20T的Qwen1.5-0.5B-Chat。
- 新增经过验证的Hugging Face镜像下载和离线导入，并进行字节数与SHA-256检查。
- 新增仅限回环的工作进程与网关、服务器端Bearer鉴权，以及可信局域网浏览器UI警告。
- 将实验性模型、音频、转换和设备协议路径归档到发布范围之外。

## 版本策略

项目遵循语义化版本控制。模型revision、硬件目标、运行时元组或公开API不兼容时，必须先记录验证更新，之后才能将其作为受支持的发布能力对外宣传。
