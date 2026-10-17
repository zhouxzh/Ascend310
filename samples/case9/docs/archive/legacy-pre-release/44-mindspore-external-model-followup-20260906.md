# MindSpore 外部模型续测记录（2026-09-06）

本记录承接 [外部模型经验探针](43-mindspore-external-model-empirical-probe-20260906.md)，只描述当前 8T 板（`orangepiaipro`、`Ascend310B4`）在本轮的实测。模型、配置和注册表均未被提升为正式准入；正式入口和浏览器服务没有修改。

## 测试边界

| 项目 | 实测值 |
| --- | --- |
| 目标 | `192.168.1.90`（同一块 8T/B4 板） |
| 主机 | `orangepiaipro`，Linux `5.10.0+`，aarch64 |
| NPU | `Ascend310B4`，`npu-smi 25.2.0` |
| CANN | `/usr/local/Ascend/ascend-toolkit`，`8.0.0` |
| Python | `/usr/local/miniconda3/bin/python`，`3.9.2` |
| 运行时 | 用户 site `MindSpore 2.4.10`、`MindNLP 0.4.1` |
| 设备状态 | `Health: Alarm`；温度约 `79--85 C`；HugePages `15/15` |
| 既有环境 | `base` 为 dirty-base；已有 Torch 系列包，本适配器不导入、不安装、不删除 |

`Health: Alarm` 不是单独的失败条件。本轮同时观察到周期性的 `DRV_LPM_FAULT 0x80E3A203`、`os-memory` soft fault 和恢复日志，因此在单进程探针之外停止长输出、稳定性和性能压力测试。日志只能说明时间相关性，不能证明某个模型单独触发了该故障。

## 本轮模型探针

所有命令均先 source conda/CANN，取消 `PYTHONNOUSERSITE`，启用离线本地工件，并在独立进程中执行 `--context-only --max-tokens 1`。`context-only` 允许记录加载和最小生成，但不满足显式参数 placement，也不允许启动正式服务。

| Profile | 工件完整性 | 加载 | 单 token 生成 | 结论 |
| --- | --- | --- | --- | --- |
| `qwen1.5-0.5b-mindspore` | 7/7 | 约 `52.260 s`（同一板端探针） | 约 `12.670 s`，输出 `我是` | `observed-pass`，`context_only`；非准入 |
| `qwen2.5-0.5b-mindspore` | 6/6 | `49.606327 s` | `14.840558 s`，输出 `我是` | `diagnostic_passed`，`context_only`；非准入 |
| `tinyllama-1.1b-mindspore` | 7/7 | `53.391025 s` | 失败：`Alloc failed, size:132163072` | `observed-fail`（生成期设备内存） |
| `deepseek-r1-qwen-1.5b-mindspore`（canonical 131072） | 5/5 | 失败于配置解析；规范化重试在加载期 OOM | 未执行 | `blocked`（配置/资源） |
| `deepseek-r1-qwen-1.5b-mindspore`（临时 context1024） | 5/5 | `58.292903 s` | `14.023559 s`，输出 `您好` | `diagnostic_passed`，`context-only`；非准入 |

Qwen2.5 报告文件为 `/home/HwHiAiUser/case9-mindspore-chat/run/mindspore-chat/exploratory/qwen25-05b-20260906.json`。该命令当时继承了旧的 `CASE9_BOARD_IP=192.168.11.14`，而 SSH 目标实际为 `192.168.1.90`；报告中的 `npu-smi` 仍明确识别 `Ascend310B4`，但 `board_ip` 字段不能作为当前地址 provenance。重跑时必须显式传入 `--board-ip 192.168.1.90`，不能直接把该字段写入正式清单。

TinyLlama 报告为 `/home/HwHiAiUser/case9-mindspore-chat/run/mindspore-chat/exploratory/tinyllama-20260906.json`。该进程退出后设备内存从约 `15.0 GB` 恢复到约 `2.8 GB`；这说明本次没有留下常驻 Python worker，但不替代同一 worker 的稳定性测试。失败发生在生成分配，不是文件哈希或 tokenizer 缺失。

DeepSeek 报告为 `/home/HwHiAiUser/case9-mindspore-chat/run/mindspore-chat/exploratory/deepseek-20260906.json`。其 `config.json` 的 `ms_dtype` 为 `mindspore.float16`。MindNLP 0.4.1 的配置解析会再次拼接 `mindspore`，最终查找不存在的 `mindspore.mindspore.float16`，因此在模型加载前停止。该结论只针对这个 revision、这个 MindNLP 版本和当前配置写法。

## DeepSeek `context1024` 探索性变体

为区分元数据问题和显存/上下文预算问题，在同一块 B4 板上建立了一个临时注册表和独立实验目录。该变体复用现有模型权重的硬链接，只复制并修改配置文件：`max_position_embeddings=1024`、`ms_dtype=float16`。它不是对已登记 Profile 的原地修改，也没有改动正式 `configs/chat_model_profiles.json`。

| 项目 | 实测值 |
| --- | --- |
| 目标 | 明确传入 `--board-ip 192.168.1.90`；实际 `npu-smi` 为 `Ascend310B4` |
| 目录 | `/home/HwHiAiUser/case9-mindspore-chat/run/mindspore-chat/exploratory/deepseek-context1024-exploratory-20260906T/` |
| 命令边界 | `--context-only --max-tokens 1 --generation-timeout 180`；独立进程、离线工件 |
| 工件 | 5/5 文件校验通过；CANN `acl`、`te`、`hccl` 导入通过 |
| 模型加载 | `58.292903 s` |
| 单 token 生成 | `14.023559 s`；prompt `12`、completion `1`；文本 `您好`；`finish_reason=length` |
| 运行状态 | exit `3`、`status=diagnostic_passed`、`after_npu_gate=passed`、`placement=context-only` |
| NPU 证据 | 约 `4993 -> 14973/15610 MB`，清理后约 `2243 MB`；未留下常驻 worker |
| 报告哈希 | `report.json` `3d20d659eaaa0ba6ff3583f3bfebf0aaca0b98e6dc0d0fba6eee5fee5688fd33`；`run.log` `014b6b29925b9e298f56048ed3b9ec45a53e897a9a5d119d41aeaba139e0be38`；临时 `registry.json` `37783150d45a41bb8cc4ebc51a253c59ff977435728d4f5ff5b7cafb77b60706` |

这里的临时 registry 仍带有原 Profile 的 B1/20T primary 元数据；这不是硬件观测值，硬件身份以本次命令的 `npu-smi` 和报告中的 `actual_npu_model=Ascend310B4` 为准。结果只证明“在 B4 上，1024 上下文配置可以完成 context-only 加载和一 token NPU 生成”。它不证明显式参数 placement、完整模型服务、JSON/SSE、长输出、稳定性、中文质量、性能或模型准入。原始 131072 上下文配置仍因 Linux OOM（exit `137`，见上一轮 `deepseek-msdtype-normalized-20260906T/run.log`）保持 `blocked`。

## 服务启动尝试

用临时探索注册表把 Qwen1.5 的 B4 状态改为 `experimental_dirty_base`，原始 `configs/chat_model_profiles.json` 未改动。服务完成模型加载后，按设计在 `MindSporeChatService._require_ready()` 因 `placement_status=context_only` fail-closed：

```text
MindSpore chat model is not ready;
profile admission experimental_dirty_base;
model placement is not verified
```

因此本轮没有监听 `127.0.0.1:8090`，也没有 JSON/SSE、网关、UI 或小智链路结果。不能把模型加载成功写成 API 通过。

本轮还修正了两个影响换板复现的启动问题：

1. `scripts/run_mindspore_chat_service.sh` 的 `setsid` 重启现在显式调用 `bash`，不再依赖 checkout 是否保留执行位；
2. artifact verifier 现在显式接收 `--registry "${registry}"`，不会在临时部署或复制目录时误读控制机默认注册表。

修复只改变启动边界，不放宽 profile、placement、CPU fallback 或正式端口门禁。随后通过 IPv6 管理地址完成了 `context1024` 独立诊断；由于其 `placement=context-only` 且设备仍有 LPM fault，修复后的完整 worker/API 回归仍未执行。

## 门禁状态

| 门 | 本轮状态 | 证据/原因 |
| --- | --- | --- |
| G0 环境 | `blocked`/诊断 | B4、CANN、ACL、MindSpore/MindNLP 可见；实际运行环境与 `PYTHONNOUSERSITE=1` 隔离环境不同；存在 LPM/os-memory 诊断 |
| G1 工件 | `artifact_verified`（逐模型） | Qwen1.5 7/7、Qwen2.5 6/6、Tiny 7/7、DeepSeek 5/5 |
| G2 加载/单 token | 分模型记录 | Qwen1.5/Qwen2.5 context-only 通过；Tiny 生成分配失败；DeepSeek canonical 阻断，context1024 临时变体 diagnostic passed（仍 context-only） |
| G3 JSON/SSE | `not-run` | 无健康、placement-verified worker |
| G4 长输出 | `not-run` | 受设备故障和资源风险停止 |
| G5 稳定性 | `not-run` | 未运行 10 轮同 worker |
| G6 质量 | `not-run` | 本轮没有新的固定探测集人工审核 |
| G7 性能 | `not-run` | 没有新的 2+30 测量 |
| G8 候选链 | `not-run` | 未启动 `8090/7867/7868` |

历史 8T/20T acceptance 和性能数据仍保留在 `docs/12-case9-evidence-index.md` 及其复现目录中；它们不因本轮短探针自动继承为当前正式结果。

## 本地验证

在控制机 `sci-agent` 环境完成：

```text
408 passed, 11 skipped
bash -n scripts/run_mindspore_chat_service.sh: passed
git diff --check: passed
py_compile（Python 文件）: passed
```

新增的 DeepSeek 配置测试确认：规范化只在内存中构造 `AutoConfig`，原始 `config.json` 字节不改变。

## 恢复后的最小重试顺序

板端重新可达后，先采集新的 `npu-smi info` 和增量 `dmesg`，确认没有新增故障，再按以下顺序执行独立进程：

1. Qwen1.5，显式 `--board-ip 192.168.1.90`，复核 1 token；
2. Qwen2.5-0.5B，显式地址，复核 1 token；
3. 如需继续 DeepSeek，只使用单独命名的 `context1024` 实验目录和临时 registry，先复核资源与 placement 证据；不要重试 canonical 131072 配置；
4. TinyLlama 仅在设备内存恢复且无新增故障时重试；
5. 只有 placement、资源和 worker 健康证据齐全，才考虑临时 API 探针。

不得在恢复前运行 10 轮、长输出或 2+30 性能，不得修改正式注册表，不得安装/升级 Torch、MindSpore、MindNLP、CANN 或其他推理框架。

## 参考资料

- [Atlas npu-smi 产品映射（310B1=20T、310B4=8T）](https://www.hiascend.com/document/detail/zh/Atlas%20200I%20A2/23.0.0/re/npu/npusmi_009.html)
- [Orange Pi MindSpore 概览](https://www.mindspore.cn/tutorials/zh-CN/master/orange_pi/overview.html)
- [Orange Pi Qwen1.5 在线示例](https://github.com/candle-org/orange-pi-mindspore/blob/dev/applications/online/inference/14_qwen1_5_0_5b/qwen1_5_0_5b.py)
- [MindSpore Lite FAQ（Ascend310 推理由 Lite 发布包维护）](https://www.mindspore.cn/docs/zh-CN/r2.2/faq/inference.html)
