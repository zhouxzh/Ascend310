# Qwen2.5 静态 ONNX/ACL 验证记录

本记录对应 2026-08-23 在 `192.168.1.90`（Ascend 310B4/8T）进行的 Qwen2.5-0.5B
无 Torch 移植批次。它是独立于 TinyLlama 历史批次的新证据集；TinyLlama 的结果不会
被复制到本记录。

## 结论

Qwen2.5 的外部 CPU 导出、静态 ONNX 检查、板端 ATC、OM descriptor、真实 ACL 单 token
生成、隔离 loopback OpenAI 接口、独立网关转发和文字网页闭环均已通过。当前 ACL 服务
使用 `127.0.0.1:8082`，临时网关使用 `127.0.0.1:7864`/`7866`，文字界面使用
`0.0.0.0:7865`，没有修改或覆盖现有 TinyLlama `8081`、正式网关 `7861`。

这不是完整的中文聊天验收：网关切换、连续稳定性、中文质量、多 token 性能和音频闭环
尚未通过。随后生成的 last-logits 优化候选及其对照结果独立记录在
[`docs/16`](16-qwen25-optimization-research-and-last-logits-validation.md)，不改写本
记录中的 full-context 工件。因此模型当前状态是 `api_verified_experimental`，不能称为
产品可用。

## 设备和运行环境

| 项目 | 实测值 |
| --- | --- |
| 目标 | `HwHiAiUser@192.168.1.90` |
| 芯片/规格 | `Ascend310B4` / `8T` |
| CANN | `8.0.0` |
| Python | `3.10.12`（板端默认）；ACL 服务使用隔离 `case9-acl-om` 环境 |
| ACL | `import acl` 通过 |
| 禁止包 | `torch`、`torch_npu`、`torchaudio`、`transformers`、`onnxruntime`、`mindspore`、`mindtorch`、`vllm`、`mindie` 未导入 |
| 诊断 | `npu-smi` 显示 `Health: Alarm`；这是已知诊断状态，不单独否决本批次，实际 ACL/ATC 结果单独判定 |
| 板端工作目录 | `/home/HwHiAiUser/case9-qwen25` |

启动服务时显式执行了 `source /usr/local/Ascend/ascend-toolkit/set_env.sh`、激活
`case9-acl-om` 并设置 `PYTHONNOUSERSITE=1`。没有安装 Torch、TorchNPU、Torchaudio、
Transformers、ONNX Runtime、MindSpore、vLLM、MindIE 或自定义 OPP。

本批次同步到板端的关键源码 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| `qwen25_acl_contract.py` | `262efcce2e8d0cdfbb34d41d3145ca022cc37de547ebd0b676722ae53e94b974` |
| `qwen25_tokenizer.py` | `5d84f645ef34006a5688b131b0f5d1fed39f0642b53dec770b49bfafc5717619` |
| `qwen25_acl_runtime.py` | `a3ccbee84be2e41be7025dc4daaa9a51e3ca7af1223a596122709a9ebcb5f609` |
| `qwen25_acl_service.py` | `8a5a9587e3ebcdf0e0ab29e5977fc646e7b0bdda05a8603a5d7f0fd3133b47da` |
| `scripts/serve_qwen25_acl.py` | `a6022a6127ad60410598da7576da3fbf12490100ae4a89edab94fe0a5fcf2cba` |
| `scripts/run_qwen25_acl_service.sh` | `0602d40c6ea667e33d006d095af2c9e1c8bded2de4f5e91f0e89363ed0a1f339` |

## 外部模型和导出

控制机使用 `C:\Users\zhoux\anaconda3\envs\sci-agent`，仅运行 CPU 导出/检查；该环境
没有 CUDA。模型通过 ModelScope 对象存储取得，因 Hugging Face 直连超时，本批次不能把
ModelScope 文件下载 revision 冒充成已经验证的 Hugging Face commit。模型文件和 tokenizer
均留在控制机的 `C:\Users\zhoux\case9-qwen25-build\model`，没有提交 Git。

| 工件 | 大小 | SHA-256 |
| --- | ---: | --- |
| `model.safetensors` | `988,097,824` | `fdf756fa7fcbe7404d5c60e26bff1a0c8b8aa1f72ced49e7dd0210fe288fb7fe` |
| `tokenizer.json` | `7,031,645` | `c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539` |
| `tokenizer_config.json` | `7,305` | `5b5d4f65d0acd3b2d56a35b56d374a36cbc1c8fa5cf3b3febbbfabf22f359583` |
| `config.json` | `659` | `18e18afcaccafade98daf13a54092927904649e1dd4eba8299ab717d5d94ff45` |

首次尝试的 FP32 导出生成了超过 2.5 GB 的 external-data 图，ONNX checker 因 sidecar
完整性无法通过；该路线被记录为失败，没有把不完整的 FP32 图传到开发板。最终采用
`use_cache=False`、固定 `[1,2048]` 输入、单文件 FP16 的 full-context 图。

## ONNX contract

| 项目 | 实测值 |
| --- | --- |
| 文件 | `qwen25-static-2048.onnx` |
| 控制机路径 | `C:\Users\zhoux\case9-qwen25-build\onnx-full\qwen25-static-2048.onnx` |
| 大小 | `1,269,330,329` bytes |
| SHA-256 | `9887d67ad36179ef8d451a1226adc35011ad7093e65b1b49cf2ab1888163c43f` |
| opset | `17` |
| 输入 | `input_ids`, `attention_mask`, `position_ids`: `int64 [1,2048]` |
| 输出 | `logits`: `float16 [1,2048,151936]` |
| 图检查 | 无动态维度、无 external initializer、无未放行算子 |
| contract 报告 | `C:\Users\zhoux\case9-qwen25-build\reports\qwen25-full-contract-2048.json` |

板端同步后重新计算的 ONNX SHA 与控制机一致。加入输入/输出 byte-size 后，当前
full-context contract JSON 的 SHA-256 为
`7223fb5997a88aa5a7521192c2c7073cd92f23dce61f0587a87f02be7018ae82`。

## ATC 和 OM

ATC 使用现有 CANN 8.0 工具链，命令为：

```bash
atc --framework=5 \
  --model=artifacts/qwen25-static-2048.onnx \
  --output=artifacts/qwen25-static-2048 \
  --input_format=ND \
  --input_shape="input_ids:1,2048;attention_mask:1,2048;position_ids:1,2048" \
  --soc_version=Ascend310B4 \
  --precision_mode=must_keep_origin_dtype
```

第一次执行仅因 `case9-acl-om` 的 `PYTHONNOUSERSITE=1` 环境缺少 ATC 自带的
`decorator` 模块而失败；没有安装包。检查发现系统已有兼容文件
`/usr/local/miniconda3/lib/python3.9/site-packages/decorator.py`，重试时仅通过
`PYTHONPATH` 显式加入该既有目录，ATC 成功。

| 项目 | 实测值 |
| --- | --- |
| 日志 | `~/case9-qwen25/logs/atc-qwen25-static-2048-retry2.log` |
| OM | `~/case9-qwen25/artifacts/qwen25-static-2048.om` |
| OM 大小 | `1,407,111,161` bytes |
| OM SHA-256 | `dc17b153ee1e76b3d31e971617d1cfdc7c56f366226212a61377a2c85e1d92b8` |

## ACL descriptor 和单 token smoke

原生 ACL 成功完成 `acl.init`、设备/上下文建立、模型加载和 descriptor 读取。实际
descriptor 为：

```text
input_ids          int64    (1, 2048)       16384 bytes
attention_mask     int64    (1, 2048)       16384 bytes
position_ids       int64    (1, 2048)       16384 bytes
/Cast:0:logits     float16  (1, 2048, 151936) 622329856 bytes
```

ATC 将唯一输出名重写为 `/Cast:0:logits`。运行时只在唯一输出的 dtype、shape 和 byte
size 完全匹配时接受这个名称重写，不按名称盲猜输出索引。descriptor 日志为
`~/case9-qwen25/logs/acl-descriptor-2048-rerun2.log`。

中文 prompt `你好，请用一句话介绍你自己。` 的真实 `acl.mdl.execute` smoke 结果：

```text
GenerationResult(text='我是', prompt_tokens=36, completion_tokens=1,
                 finish_reason='length')
elapsed: approximately 21 seconds
```

原始日志为 `~/case9-qwen25/logs/acl-smoke-2048-rerun.log`；推理前后设备快照为
`~/case9-qwen25/logs/npu-before-acl-smoke-rerun.txt` 和
`~/case9-qwen25/logs/npu-after-acl-smoke-rerun.txt`；文字 UI 后快照为
`~/case9-qwen25/logs/npu-post-text-ui-20260823.txt`（实测约 `6788/15610 MB`，HugePages
`1110/1110`，Health `Alarm`）。这是 NPU 生成/诊断证据，不是中文质量或稳定性结论。

## 隔离 OpenAI API

通过 `scripts/serve_qwen25_acl.py` 在板端启动：

```text
127.0.0.1:8082
model = qwen2.5-0.5b-instruct-static-fp16-acl-om
PID = 27022（本记录最后一次重启的实测 PID；重启后以新的 PID 为准）
```

实测结果：

| 请求 | 结果 |
| --- | --- |
| `GET /health` | HTTP 200，`ready=true`，descriptor 已验证，输出名 `/Cast:0:logits` |
| `GET /v1/models` | HTTP 200，返回固定模型 ID |
| JSON `POST /v1/chat/completions`, 中文 prompt, `max_tokens=1` | HTTP 200，内容 `我是`，`prompt_tokens=36`，约 12.4 s |
| SSE `POST /v1/chat/completions`, 中文 prompt, `max_tokens=2` | HTTP 200，角色 chunk、增量 `你好！`、结束 chunk 和 `data: [DONE]` 均出现，约 21.8 s |

隔离网关进程最后一次实测 PID 为 `5847`，监听 `127.0.0.1:7864`；它只用于本批次
协议验证，停止或重启时不得误杀正式网关进程。

服务日志为 `~/case9-qwen25/logs/qwen25-service-8082.log`；本次原始响应分别保存在
`~/case9-qwen25/reports/api-qwen25-final-8082-json.txt` 和
`~/case9-qwen25/reports/api-qwen25-final-8082-sse.txt`。SSE 的增量必须是相对前一
次累计文本的差值；不能把累计全文重复发送。服务仅监听 loopback，不提供浏览器鉴权，
因此目前不应直接暴露到局域网。

## 隔离网关转发

为避免改变现有部署，使用已部署的 case9 gateway 源码在 `127.0.0.1:7864` 临时启动，
环境变量只在该进程 shell 中存在：

```text
UPSTREAM_BASE_URL=http://127.0.0.1:8082/v1
UPSTREAM_MODEL=qwen2.5-0.5b-instruct-static-fp16-acl-om
PUBLIC_MODEL_ID=case9-qwen25
RAG_ENABLED=false
```

临时 Bearer token 仅用于本次板端探测，没有写入仓库或正式 `.env`。无 token 的
`GET /v1/models` 返回 HTTP 401；带 token 的 `/v1/models`、JSON completion 和 SSE
均成功，响应内容分别为 `你好` 和 `你好`/`！`。原始报告为
`~/case9-qwen25/reports/gateway-qwen25-final-7864-json.txt`、
`~/case9-qwen25/reports/gateway-qwen25-final-7864-sse.txt`，启动日志为
`~/case9-qwen25/logs/gateway-qwen25-7864.log`。这证明鉴权和转发协议，不代表已经
批准把正式 `7861` 切换到 Qwen2.5。

## 隔离文字界面

为让浏览器直接测试，使用已有的无音频 `text_chat_app.py` 在板端另开 `7865`，其后端
网关为 `127.0.0.1:7866`（公开模型名 `case9-rag`，上游仍为 8082）。最后一次进程为：

```text
text_chat_app.py PID = 6693
gateway 7866 PID = 6644
浏览器地址 = http://192.168.1.90:7865/
```

从 Windows 控制机访问 `/` 和 `/health` 均返回 HTTP 200。`POST /api/chat` 的真实 SSE
闭环返回了 `你好！很高兴为您服务。有什么我可以`，随后发送 `done` 事件；原始报告为
`~/case9-qwen25/reports/text-ui-qwen25-final-7865.txt`。这是未鉴权的同网段实验页面，
页面明确显示警告；它只验证文字路径，不启用麦克风、ASR、TTS 或 XiaoZhi。

## 尚未完成的门

| 门 | 状态 | 说明 |
| --- | --- | --- |
| 工件完整性 | `passed` | 文件大小、SHA、来源和板端同步一致 |
| 静态 ONNX contract | `passed` | 固定三输入、单 logits 输出，无动态维度 |
| ATC/OM | `passed` | Ascend310B4/CANN8.0 生成并校验 OM |
| ACL descriptor | `passed` | 原生 ACL 读取并严格校验 |
| NPU 生成 | `passed-limited` | 已有单 token smoke；full-context 每 token 重算，速度很慢 |
| JSON/SSE API | `passed-isolated` | `127.0.0.1:8082` 原生 JSON/SSE 通过；随后由 7864 隔离网关转发复测 |
| case9 网关转发 | `passed-isolated` | `7864` 临时端口的鉴权、JSON、SSE 转发通过；正式 `7861` 未切换 |
| 中文质量 | `pending` | TinyLlama 的失败结论不能迁移；需独立中文探测集和人工判断 |
| 稳定性/性能 | `pending` | 未完成 10 轮 RSS/FD/NPU 内存和 p50/p95 统计 |
| 文字 UI | `passed-isolated` | `7865 -> 7866 -> 8082`，Windows 可访问，SSE `delta/done` 通过；无浏览器鉴权 |
| local_app/音频/XiaoZhi | `text-only passed; audio/XiaoZhi paused` | 文字 UI 已独立通过；音频仍需 ASR/TTS 方案和设备门，不安装 Torch/TorchNPU/Torchaudio |

失败时只停止识别出的 Qwen2.5 服务 PID，并保留 `~/case9-qwen25` 下日志、哈希和
`npu-smi` 快照。不得删除系统 CANN、共享 conda 缓存、TinyLlama 工件或现有网关。

## 控制机回归

以下检查在 Windows 控制机完成，使用 `C:\Users\zhoux\anaconda3\envs\sci-agent`，不
调用 ACL、ATC 或板端硬件：

| 检查 | 结果 |
| --- | --- |
| Qwen2.5 静态导出/ACL 专项 Python 测试 | `15 passed`（含 last-logits 优化契约/运行时测试） |
| Qwen2.5 Python `py_compile` | `passed` |
| 启动 shell `bash -n`、`git diff --check` | `passed` |
| React/Vite `npm test` | `4 passed` |
| React/Vite `npm run build` | `passed` |
| 全仓库 Python discovery | `92` tests；`3` 个模块因 sci-agent 没有 FastAPI 导入失败，`1` 个音频测试因控制机没有 `paplay` 失败，另有 `1` skip。这些是既有环境边界，不是 Qwen2.5 ACL 回归结论。 |
