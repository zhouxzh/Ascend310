# 本地中文聊天验证记录

## 验收口径

本记录将源代码检查、资产完整性、音频 I/O、ASR、LLM NPU、TTS 与端到端体验分开。
任何一项通过都不替代另一项，尤其是 CANN 编译通过不代表 Ascend 310B4 已完成真实
NPU 推理。

| 阶段 | 要求 | 当前状态（2026-08-21） |
| --- | --- | --- |
| Windows Python | `py_compile` 与单元测试 | 未执行：控制器的 `python` 是 WindowsApps stub；未用系统 Python 或其他环境替代 |
| Windows 前端 | `npm ci`、`npm test`、`npm run build` | 通过：4 个测试，生产 bundle 已构建 |
| 板端隔离环境 | Python 3.9、`sherpa_onnx` 导入、无用户 site-package 依赖 | 通过：Python 3.9.25、sherpa-onnx 1.13.6、41 个 Python 测试；`pip check` 无断裂依赖 |
| ASR/TTS 工件 | 文件字节数、SHA-256、模型目录 | 通过：锁定并解压；见下方哈希 |
| 板端音频 I/O | 1 秒 C922 采样 RMS、USB 喇叭管道 | 通过：16,000 样本，RMS 41.5；静音播放通过 |
| llama.cpp CANN（历史候选） | 固定源码构建、CANN 初始化、NPU 观测 | 失败：CANN 8.0 缺少 ACLNN 头文件；保留证据，不回退 |
| Qwen GGUF 工件 | 固定 revision、字节数、SHA-256 | 通过：428,730,208 字节；见下方哈希 |
| ACL/OM Qwen 候选 | 固定 ONNX revision、ONNX 契约、ATC、OM、ACL、NPU | 阻断：工件内容完整性通过，但图含动态 KV-cache，ONNX 契约拒绝；见 `docs/07-acl-om-validation-record.md` |
| 网关真实上游 | `/v1/models` 与流式 `/v1/chat/completions` | 阻塞：ACL/OM LLM 未通过 |
| 语音闭环 | 10 条中文 PTT 短句，人工可理解至少 8/10 | 待执行 |
| 小智服务端 | 无 Torch/Torchaudio 的依赖、配置加载和启动 | 暂缓：上游完整依赖包含 Torch/Torchaudio，禁止继续安装或启动 |
| 小智真机 | ESP32 ASR -> LLM -> TTS | 未执行，设备暂不可用 |

## 已执行的本地检查

在 Windows 开发控制器已运行前端和静态检查：

```text
npm ci
npm test
npm run build
git diff --check
```

控制器的默认 `python` 是 WindowsApps stub，因此没有用其他 Python 解释器替代执行
Python 测试。前端 4 个测试通过，生产 bundle 已构建，且源代码不包含浏览器音频采集 API。

在板端的 `case9-local-chat` Python 3.9.25 环境运行了：

```text
python -m py_compile app.py config.py retrieval.py upstream.py local_app.py local_session.py audio_io.py
python -m unittest discover -s tests -v
```

结果：41 个 Python 测试通过，覆盖 WebSocket 文本/PTT、断开时采集释放、会话上限、
SSE 解析、句子分割、音频单操作和网关限额。前端和 Python 检查均不代表开发板音频、
模型或 NPU 通过。

## 已完成的板端证据

以下结果来自 `HwHiAiUser@192.168.8.178`，工件保存在板端
`$HOME/case9-local-chat`，不写入 Git：

| 工件/检查 | 实测结果 |
| --- | --- |
| ASR archive | `74,004,050` bytes，SHA-256 `2cbd71b640d9c37d3784f29367333a4577b0398b62e9deeed418170b081cba8b` |
| TTS archive | `67,255,926` bytes，SHA-256 `dbdfec42b91d9cee31cce9ff4b3e9c305eb6fbf60546d071f7e46273554cce6b` |
| Qwen Q4_0 | `428,730,208` bytes，固定 revision `12145bd1d629190a4d44254073650877954d02c9`，SHA-256 `7671c0c304e6ce5a7fc577bcb12aba01e2c155cc2efd29b2213c95b18edaf6ed` |
| sherpa runtime | `sherpa-onnx 1.13.6`，Python `3.9.25`；Huayan 合成 `22,050` Hz PCM 后播放通过；Zipformer int8 静音推理返回空文本 |
| PulseAudio | C922 源 16 kHz/单声道读取 16,000 样本；USB sink 22.05 kHz 静音管道返回成功；原始 PCM 未保存 |
| llama.cpp source | `d9b6be07d0864ab09417b17ba36f9788087dd22c`，归档导入后 CMake 识别 `Ascend310B4` |
| CANN build | 失败于 `aclnnop/aclnn_recurrent_gated_delta_rule.h: No such file or directory`（板端 CANN `8.0.0`）；因此没有 `/v1/models` 或真实 NPU completion 结果 |

该 llama.cpp 结果是历史候选失败证据，不是当前方案。当前首选的 Qwen1.5-0.5B
通用 ONNX 候选固定为 revision `6d413dd9a252749e0760902c93331e3e4e65b73c` 的
`onnx/model_fp16.onnx`，预期 `928,499,243` bytes，SHA-256
`1397b07c02c5821316ca20cb64f45af87b87932eddd13c743d988d5a7c826262`；tokenizer 同一
revision，`11,418,266` bytes，SHA-256
`bcfe42da0a4497e8b2b172c1f9f4ec423a46dc12907f4349c55025f670422ba9`。截至本记录日期，
该候选随后通过受控 ModelScope 对象存储取得，整文件大小和 SHA-256 与固定 HF 工件一致；
但图检查发现 51 个输入（含 48 个 `past_key_values`）、49 个输出（含 48 个 `present`），
实际 logits 为 float32 且输入输出含动态/符号维度，并有未放行的 `ai.onnx:Sigmoid`，因此
ONNX 契约门失败。尚未执行 ATC、OM 加载或 ACL 推理，不能记录为通过。

2026-08-21 板端 ACL/OM 环境证据：`case9-acl-om` Python `3.9.25`，`acl` 导入通过，
`torch`、`torch_npu`、`torchaudio`、`mindtorch`、`torchvision`、`xformers` 均不存在；
`npu-smi` 识别 `Ascend 310B4`（Health `Alarm`）。固定 Hugging Face URL 的 IPv4 探测
和下载返回 `curl_exit=28`；之后 ModelScope 传输的整文件 SHA-256 校验通过，但不能绕过
ONNX 契约门，也不使用替代后端。完整日志路径和版本信息见
`docs/07-acl-om-validation-record.md`。

ASR/TTS 的发布包没有上游 SHA，板端
`artifacts/manifest.lock.json` 保存了首次 TLS 下载的字节数、SHA 和传输 URL；后续
脚本会拒绝不匹配的包。历史 Qwen GGUF 的 SHA 与固定 revision 在控制器下载后、同步前
以及板端同步后均重新计算一致。当前 ACL/OM ONNX 候选已完成内容完整性校验，但契约
检查失败；其固定元数据仍记录在 `local_model_manifest.json`，不能继续 ATC。

## 板端证据记录格式

在每次模型构建或性能批次前，先运行：

```bash
bash scripts/collect_system_status.sh
```

若该辅助脚本不在案例目录，至少保留 `uname -a`、Python 可执行路径、CANN 版本、
`npu-smi info`、`pactl list short sources`、`pactl list short sinks` 和模型锁文件的
输出。当前板为 Ascend 310B4 / 8T；`npu-smi` 的 `Health: Alarm` 是已知诊断状态，
不能单独阻止测试，也不能用于伪造通过。

每次端到端运行仅记录聚合时延，不记录音频或文本内容：

| 指标 | 定义 | 记录方式 |
| --- | --- | --- |
| ASR 完成 | `ptt_stop` 到转写完成 | 毫秒，p50/p95 |
| LLM 首 token | 发送网关请求到第一 `delta` | 毫秒，p50/p95 |
| LLM 完成 | 发送网关请求到 SSE `[DONE]` | 毫秒，p50/p95 |
| TTS 首音频 | 首个可播放句子开始合成到首次提交 `paplay` | 毫秒，p50/p95 |
| 总时长 | PTT 停止到最后播放完成 | 毫秒，p50/p95 |

`GET /api/metrics` 仅提供有限容量的进程内聚合统计，进程重启后丢失。完成 10 条
中文短句后，人工只记录“可理解/不可理解”计数，不在仓库或报告中写入实际转写文本。

同时观察连续 10 轮的进程存活、文件描述符和内存变化。资源稳定性目前是观察项，
不是首轮硬门槛；任何崩溃、设备未释放或异常退出都必须保留命令输出和时间点。

## 未通过时的处理

1. ASR/TTS 文件校验失败：停止解压和加载，重新下载到板端临时路径并比较 SHA-256。
2. 麦克风或扬声器失败：保存 PulseAudio 设备枚举与返回码，不写入原始 PCM。
3. ONNX 契约、ATC、OM 加载、ACL 初始化或推理失败：保存固定 revision、检查/ATC/ACL
   日志和 `npu-smi` 快照；本地 LLM 验收标为失败，不改用 CPU、云端、Torch 或替代模型。
4. 历史 llama.cpp CANN 构建失败：保存固定 revision、CMake 日志和缺失头文件证据；不
   重新把 GGUF 路径标为当前首选。
5. 网关 SSE 不符合 OpenAI 流式契约：保留 HTTP 状态、Content-Type 与去敏后的错误码，
   先修复网关，不将普通 JSON 当作流成功。
6. 真机不可用：只记录小智服务的配置加载和模拟结果，绝不称为真实设备语音闭环。
7. 小智上游完整依赖包含 Torch/Torchaudio：停止安装和服务启动；只有审核完成无 Torch
   的 ASR、VAD、TTS 配置后，才能恢复第二阶段。
