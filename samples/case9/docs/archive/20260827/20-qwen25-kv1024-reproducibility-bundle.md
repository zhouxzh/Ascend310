# Qwen2.5 静态 KV 1024 复现包与更换开发板流程

## 记录目的

本文定义如何把当前 Qwen2.5 静态 KV 文字聊天结果迁移到另一块
Ascend 310B4 开发板。目标不是复制某次运行的 PID，而是保存足够的模型输入、
转换契约、源码、依赖版本和原始证据，使新板可以从同一工件重新执行检查、ACL
smoke、OpenAI API 和文字页面验收。

完整复现包位于控制机的被 Git 忽略目录：

```text
repro/qwen25-kv1024-20260825/
```

包内 `README.md` 是可直接执行的操作手册，`bundle-manifest.json` 是核心文件
的字节数/SHA-256 清单，`SHA256SUMS.txt` 覆盖包内全部文件。模型、ONNX、OM、
checkpoint 和历史报告不应提交 Git。

## 当前已归档工件

| 工件 | 包内路径 | bytes | SHA-256 |
| --- | --- | ---: | --- |
| Static-KV ONNX | `artifacts/qwen25-static-kv-1024-v2.onnx` | 1,261,082,122 | `b4870df5da9c8cbef4163ceb65d4dc13433f2fd8ed5d2083ef3223d07d1a3c0e` |
| 310B4 OM | `artifacts/qwen25-static-kv-1024-v2.om` | 1,266,010,586 | `f6650e52ff3908288763ef7957832ade606b0e554fa8fde986932f1ca1140eb8` |
| OM lock | `artifacts/qwen25-static-kv-1024-v2.om.lock.json` | 1,421 | `7d6b35de8261ce3e5f077a6d6d3b6e19df43a2fba2470a2ef30c0d217a8c2770` |
| tokenizer | `artifacts/tokenizer.json` | 7,031,645 | `c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539` |
| tokenizer config | `artifacts/tokenizer_config.json` | 7,305 | `5b5d4f65d0acd3b2d56a35b56d374a36cbc1c8fa5cf3b3febbbfabf22f359583` |
| source checkpoint | `source-model/model.safetensors` | 988,097,824 | `fdf756fa7fcbe7404d5c60e26bff1a0c8b8aa1f72ced49e7dd0210fe288fb7fe` |

模型身份为 `qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om`，源 checkpoint
revision 为 `modelscope:13448952dbdab7a1627d0680ecd207535d889a23`。ONNX 是单文件，
检查结果为 51 inputs、49 outputs、opset 17、0 个动态维度、0 个 external initializer。
静态契约是 3 个基础 `int64` 输入、48 个 `float32 [1,2,1024,64]` cache 输入、
`float32 [1,1,151936]` logits 和 48 个 `float32 [1,1,2,64]` token cache 输出。

OM 是在原板 `Ascend310B4`、CANN `8.0.0` 上通过 `atc --soc_version=Ascend310B4`
生成的。OM hash 只绑定这份 ONNX、ATC 参数、CANN 和 SoC；它不是跨版本可盲拷贝的
通用格式。新板仍必须执行 descriptor 和 ACL smoke。若 CANN 不同，必须用包内 ONNX
在新板重新 ATC 到新的输出前缀，并保留旧 OM。

## 已同步的运行链路

包内保存了两套实际运行源码的快照：

* `src-board/`：ACL runtime、contract/tokenizer、ACL service、provision 脚本和板端启动器；
* `src-gateway-ui/`：网关、FastAPI 文字 UI、会话模块、依赖和脚本；
* `controller/`：Windows `sci-agent` 的导出、静态检查、CPU 对照工具、测试、ONNX contract/report 和前端源码/dist。

网关/UI 快照不包含填充的 `.env`。`config/repro.env.example` 只给出 loopback 地址和占位 token；
真实 `GATEWAY_API_KEY` 必须只存在新板当前 shell 或板端私有文件，不能同步进包，也不能发送给浏览器。

## 环境证据与外部前置条件

原板快照记录：aarch64、Ubuntu 22.04.5、Linux 5.10.0+、Ascend310B4/8T、CANN
8.0.0、Python 3.9.16；`case9-acl-om` 可导入 `acl`，且没有可导入的
`torch`、`torch_npu`、`torchaudio`、`transformers`、`onnxruntime`、MindSpore、
vLLM 或 MindIE。`case9-local-chat` 记录了 FastAPI 0.115.12、HTTPX 0.28.1、
Uvicorn 0.34.3 等实际版本。完整锁文件在：

```text
environment/board-snapshot-20260825.txt
environment/case9-acl-om-pip-freeze.txt
environment/case9-acl-om-conda-explicit.txt
environment/case9-local-chat-pip-freeze.txt
environment/case9-local-chat-conda-explicit.txt
```

CANN、驱动、固件、内核和 ACL 系统安装不属于应用数据，未打包，也不应由复现脚本
自动覆盖。新板必须由板卡维护流程安装并核对这些前置条件；本项目不安装 Torch、
Torch-NPU、Torchaudio、MindSpore、vLLM、MindIE 或自定义系统 OPP。

## 新板复现顺序

建议先把整个包复制到新板用户目录，再在新板执行：

```bash
cd ~/case9-qwen25-kv1024-20260825
chmod +x scripts/*.sh src-board/*.sh src-board/scripts/*.sh src-gateway-ui/scripts/*.sh
bash scripts/verify_all_hashes.sh

source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate case9-acl-om
source /usr/local/Ascend/ascend-toolkit/set_env.sh
export PYTHONNOUSERSITE=1
export CASE9_QWEN25_KV_ROOT="$PWD"
export PYTHONPATH="$PWD/src-board${PYTHONPATH:+:$PYTHONPATH}"

bash src-board/provision_qwen25_kv102_board.sh check
bash src-board/provision_qwen25_kv102_board.sh inspect
bash src-board/provision_qwen25_kv102_board.sh smoke
```

通过上述门后，再在同一可信实验网络中设置私有 token 并启动三进程链路：

```bash
export GATEWAY_API_KEY='use-a-new-private-token-at-least-24-characters'
bash scripts/run_repro_chain.sh
```

浏览器访问 `http://<新板 IP>:7865/`。停止时只使用包内 PID 文件：

```bash
bash scripts/stop_repro_chain.sh
```

`run_repro_chain.sh` 固定 `127.0.0.1:8080` ACL、`127.0.0.1:7861` 网关和
`0.0.0.0:7865` 文字 UI，拒绝占用中的端口，不创建 systemd/cron/autostart，
也不停止包外进程。开发板重启后不能假设服务自动恢复，应重复 hash/preflight/start 流程。

## 192.168.8.178 替换板实测记录

2026-08-27 已将完整复现包同步到：

```text
/home/HwHiAiUser/case9-qwen25-kv1024-20260825/
```

同步脚本使用 `rsync --checksum`，不删除远端文件；板端
`sha256sum --check SHA256SUMS.txt` 对 47 个受管文件全部通过。校验表使用 LF
行尾，以避免 Linux 将 Windows 的 `\r` 当作文件名的一部分。

## 192.168.8.210 20T 性能对比记录

2026-08-27 在另一块开发板 `192.168.8.210` 上完成了独立的 20T 实验批次。该板
由 `npu-smi` 确认为 `Ascend310B1`（20T），不能加载本包中按 `Ascend310B4`
生成的原 OM，因此使用同一 ONNX/契约在板端以
`--soc_version=Ascend310B1` 重新生成了 B1 OM。B1 OM 为
`1,266,009,438` bytes，SHA-256 为
`6bca884fbce746efdb02f8c9294cad5b2faa6c8b96cac9ec8c83730126298609`。

用户明确要求直接使用该板 `base` 环境。由于 base 的 system site-packages 为
root 所有，不能直接写入；实际采用 base Python `3.9.2` 加用户目录 overlay，
只安装 `numpy 1.26.4` 与 `tokenizers 0.19.1`，没有安装 Torch、Torch-NPU、
Torchaudio 或其他推理框架。base 中原有的相关包仍存在，所以该批次使用了显式
dirty-base 测试开关，不是干净环境生产验收。

JSON 固定协议（1 次预热、5 次测量、`max_tokens=2`）p50/p95 为
`7751.579/7751.770 ms`；归档 8T/310B4 同协议为
`11139.7/11164.9 ms`，p50 耗时下降 `30.415%`。ACL smoke、8084 JSON/SSE
均通过，候选服务测试后已停止，正式 `8080 -> 7861 -> 7865` 未切换。完整原始
报告、npu-smi 快照、ATC 日志和限制见
[`docs/21`](21-qwen25-20t-performance-comparison.md) 及本地忽略目录
`repro/qwen25-kv1024-20260825/reports/board20t/`；20T OM 本身仍只保留在
板端独立目录，不写入 B4 复现包的正式工件清单。

本次替换板证据根目录为：

```text
run/replacement/192.168.8.178/20260827T030758Z/
```

同一 OM 在 8T 与 20T 的隔离交叉验证另见
[`docs/22-qwen25-cross-board-om-validation.md`](22-qwen25-cross-board-om-validation.md)。
该验证使用独立 `cross-om-test` 目录和短请求，只证明本次两个具体 OM 的实测兼容性，
不改变按目标 SoC 生成 OM 的正式复现流程。

实测环境和门禁如下：

| 门 | 实测记录 | 状态 |
| --- | --- | --- |
| 硬件/运行时 | `orangepiaipro`、aarch64、Ascend310B4/8T、CANN 8.0.0、Python 3.9.25、`acl` 可导入 | 通过 |
| 禁止包 | `PYTHONNOUSERSITE=1` 下 `torch`、`torch_npu`、`torchaudio`、`transformers`、`onnxruntime`、MindSpore、vLLM、MindIE 均不可导入；用户目录仍有旧 MindSpore 元数据但未删除 | 通过 |
| ONNX/契约 | ONNX SHA-256 `b4870df5...d1a3c0e`；51 inputs、49 outputs、48 KV 对、静态长度 1024 | 通过 |
| ACL/NPU smoke | 同一 Python 进程、单 ACL 生命周期；文本 `你好！`，`prompt_tokens=30`，`completion_tokens=2` | 通过 |
| 服务/API/UI | 候选 ACL 服务 `127.0.0.1:8084` 的 `/health`、`/v1/models`、JSON 和 SSE 已通过；网关/UI 尚未启动，未宣称正式 API 或 UI 门通过 | 候选通过 / 正式未执行 |

原始报告均保留在上述目录：`npu-before-20260827T030828Z.txt`、
`system-20260827T030828Z.txt`、`20260827T031013Z-acl-smoke.txt`、
`20260827T031013Z-smoke-{before,during,after}.txt` 和生成的
`contracts/qwen25-static-kv-1024-v2-om-contract.json`。smoke 前快照为
`9403/15610 MB`，完成后为 `9439/15610 MB`；采样期间记录了模型加载和释放过程。
`npu-smi` 报告中的 `Health: Alarm` 是该板已知诊断状态，不单独阻断本次 ACL 结果。

候选 API 原始响应也保存在 `reports/`：`20260827T034015Z-health.json`、
`20260827T034015Z-models.json`、`20260827T034015Z-json-completion.json` 和
`20260827T034015Z-sse-completion.txt`；对应服务日志为
`logs/api-candidate-20260827T034015Z.log`。这些文件只证明 loopback 候选端口
`8084` 的协议行为，不证明正式 `8080 -> 7861 -> 7865` 链路已升级。

首次替换板 smoke 曾在旧脚本的重复 `acl.init()` 路径返回 `100002`；该失败报告
保留在 `run/replacement/192.168.8.178/20260827T024643Z/`。脚本已改为 descriptor
校验和生成共用一个 runtime，随后在同一板、同一 OM 上通过 smoke。不要在同一
Python 进程中执行 `acl.finalize()` 后再次初始化 ACL。

## 本次同步边界和当前状态

截至 2026-08-25，原板在归档期间再次重启；`22:40:25` 快照中模型目录仍在，但
8080/7861/7865 没有监听。因此本地复现包是完整的数据与证据归档，不表示当前原板
服务仍在线。原板此前的 ACL smoke、NPU 生成、JSON/SSE、网关鉴权、文字 UI、中文
探测 8/10 和相对 2048 基线 p50/p95 改善 48.79%/48.70% 仍以 `docs/18` 的原始
报告为准；新板必须重新执行对应门禁，不能把复制的报告当作新硬件实测。

音频/ASR/TTS 和 XiaoZhi 服务端仍暂停，故本包没有把它们作为 Qwen 文字链路的
运行依赖。TinyLlama、Qwen1.5、旧 full-context 和 last-logits 工件保留在历史
文档/板端证据中，不会被新板启动脚本隐式切换。
