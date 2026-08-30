# Qwen2.5 20T 性能对比记录

## 实测范围与结论

本记录对应独立开发板 `192.168.8.210`，主机名为
`orangepiaipro-20t`。实测芯片为 `Ascend310B1`（20T），CANN `8.0.0`，
`npu-smi 25.2.0`，aarch64，Python `3.9.2`。`npu-smi` 的 `Health: Alarm`
在本板出现，但按项目规则只作为诊断字段；ACL 初始化、OM 加载、NPU 执行和
HTTP 请求均有独立证据。

同一静态 KV ONNX 在 20T 上以 `--soc_version=Ascend310B1` 重新生成了 B1
专用 OM。ACL smoke、JSON、SSE 和固定协议测速全部通过。JSON 1 次预热加 5
次测量的 p50 为 `7751.579 ms`，与原 8T/310B4 同协议历史 p50
`11139.7 ms` 相比，20T 约为 `1.437x`，耗时下降 `30.415%`。这是两个不同
SoC 的单批次实验对照，不是 TOPS 理论值，也不代表中文质量或长期稳定性。

板端使用的是用户明确授权的 `base` Python。系统 site-packages 为 root 所有，
直接写入失败，因此没有修改系统环境；只把需要的 `numpy 1.26.4` 和
`tokenizers 0.19.1` wheel 安装到用户目录下的显式 overlay，并通过
`PYTHONPATH` 供 base Python 使用。base 中原有的 `torch`、`torch_npu`、
`torchaudio`、`mindspore` 仍可被发现，测试使用了显式 dirty-base override，
所以本结果不能标记为干净环境生产验收，也没有安装这些禁止包。

## 固定协议

20T 与归档 8T 报告使用完全相同的输入和请求参数：

| 项目 | 固定值 |
| --- | --- |
| 提示词 | `你好，请用一句话介绍你自己。` |
| 请求 | `POST /v1/chat/completions`，JSON，`stream=false` |
| 解码 | `max_tokens=2`、`temperature=0`、`top_p=1`、batch 1 |
| 预热/测量 | 1 次预热、5 次测量；只统计测量轮次 |
| 百分位 | lower-index：`floor((n-1)*q)`，零基索引 |
| 服务 | 候选 `127.0.0.1:8084`；测试后已停止。PID 未单独持久化，不能从报告反推 |

20T JSON 原始测量耗时为 `7741.169, 7778.458, 7725.153, 7751.579,
7751.770 ms`，每轮均返回 `我是Q`、2 completion tokens、HTTP 200。原始报告
SHA-256 为 `2e5ef763e9956bb343614c7a5d019a5a56c4636411858f743bdaf44803cb13db`。

| 指标 | 8T/310B4 历史静态 KV | 20T/310B1 本次 | 变化 |
| --- | ---: | ---: | ---: |
| min (ms) | 11132.0 | 7725.153 | -30.60% |
| mean (ms) | 11148.24 | 7749.626 | -30.48% |
| p50 (ms) | 11139.7 | 7751.579 | -30.415% |
| p95 (ms) | 11164.9 | 7751.770 | -30.570% |
| max (ms) | 11166.2 | 7778.458 | -30.29% |
| p50 speedup | 1.000x | 1.437x vs 8T |  |

8T 原始报告为复现包中的
`repro/qwen25-kv1024-20260825/reports/board/20260823T-candidate-benchmark-1warmup-5.json`；20T 原始
报告为 `repro/qwen25-kv1024-20260825/reports/board20t/20260827T045500Z/reports/benchmark-json-1warmup-5.json`。
样本只有 5 轮，p95 只作协议化参考，不能替代更大样本的统计。

### SSE 结果

SSE 同样 1 次预热加 5 次测量，5/5 成功。总耗时 p50/p95 为
`7789.273/7792.435 ms`，首 data event p50/p95 为
`7661.332/7668.468 ms`。原始 SSE 报告 SHA-256 为
`01ccf59362888d877b5c1700c337519bedb3bb154d3239fc7235cdcb1026c907`。服务当前 SSE 事件没有携带 usage 字段，故该报告的
completion token/s 不计算；SSE 首事件不与 JSON 总耗时混合排名。

## 工件和环境证据

| 项目 | 实测值 |
| --- | --- |
| ONNX | `1261082122` bytes，SHA-256 `b4870df5da9c8cbef4163ceb65d4dc13433f2fd8ed5d2083ef3223d07d1a3c0e` |
| 控制机契约 | SHA-256 `fcc423f24fdf36c5d364ad7bd535943fb0c8545ab546a5724ab0261330639498` |
| B1 OM | `1266009438` bytes，SHA-256 `6bca884fbce746efdb02f8c9294cad5b2faa6c8b96cac9ec8c83730126298609` |
| OM 输入/输出 | 51 inputs / 49 outputs；48 split KV pairs；cache `[1,2,1024,64]` float32；token cache `[1,1,2,64]` |
| logits | float32 `[1,1,151936]` |
| tokenizer | `numpy 1.26.4` overlay、`tokenizers 0.19.1`；tokenizer JSON SHA-256 `c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539` |
| ATC | `--framework=5`、静态契约派生 shape、`--soc_version=Ascend310B1`、`--precision_mode=must_keep_origin_dtype` |

ATC 原始日志为 `repro/qwen25-kv1024-20260825/reports/board20t/20260827T045500Z/logs/atc-20260827T053058Z.log`，
SHA-256 为 `d1945c78aad1f1290d0539f801b4b959ddecdaefe6c17a5420c54cf4e984ae87`；
OM lock 为 `repro/qwen25-kv1024-20260825/reports/board20t/20260827T045500Z/artifacts/qwen25-static-kv-1024-b1.om.lock.json`，
SHA-256 为 `47a4030202342132d5794bea1e1e5ae19316f6c132b2fa00895df87b4b9d3272`。
文件名中的 `20260827T053058Z` 是 UTC 命名时间，而 ATC 行内时间显示板端本地
`+08:00`，两者不能当作同一时间戳。
日志错误扫描没有发现 `ERROR`、`FATAL` 或失败标记；脚本第一次写 lock 时发生
缩进错误，随后使用同一 OM、同一 ATC 日志和同一哈希补写 lock，没有重复转换或
覆盖 OM。ATC 仍有大量 `Expand`/`ScatterElements` 未命中 high-priority operator
library 的警告，应视为性能风险，而不是错误。

## ACL、API 与 NPU 证据

ACL smoke 报告 `repro/qwen25-kv1024-20260825/reports/board20t/20260827T045500Z/reports/20260827T054614Z-acl-smoke.txt`（SHA-256
`7b527e39f42597855ed09dd0411103db8165bdc617f348f31d30b2a2f0e0b763`）返回：

```text
status=passed
text=你好！
prompt_tokens=30
completion_tokens=2
```

候选服务的 `/health` 报告 `descriptor_validated=true`、
`request_buffer_reuse=true`、`device_cache_update=true`，并声明
`restart_required=false`、`cleanup_failed=false`。`/v1/models` 返回模型名
`qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om`。JSON 与 SSE 均为 HTTP 200，
随后只停止已识别的候选服务，确认 `127.0.0.1:8084` 已无监听；没有切换正式入口
`8080 -> 7861 -> 7865`，也没有启动网关、网页、音频或 XiaoZhi。

测速期间保存了 JSON/SSE 各自的 `npu-smi info` before、during、after 快照：

```text
repro/qwen25-kv1024-20260825/reports/board20t/20260827T045500Z/reports/benchmark-json-before.txt
repro/qwen25-kv1024-20260825/reports/board20t/20260827T045500Z/reports/benchmark-json-during.txt
repro/qwen25-kv1024-20260825/reports/board20t/20260827T045500Z/reports/benchmark-json-after.txt
repro/qwen25-kv1024-20260825/reports/board20t/20260827T045500Z/reports/benchmark-sse-before.txt
repro/qwen25-kv1024-20260825/reports/board20t/20260827T045500Z/reports/benchmark-sse-during.txt
repro/qwen25-kv1024-20260825/reports/board20t/20260827T045500Z/reports/benchmark-sse-after.txt
```

快照持续报告 `310B1`、`Health: Alarm`、`npu-smi Hugepages-Usage 647/647`；测量前后设备内存
约为 `7383/23673 MB` 与 `7421/23673 MB`。`during` 快照的 AICore 采样为 0%。
`/proc/meminfo` 的 HugePages 计数器另行记录，因此不能声称采样器捕获了非零 AICore；
ACL execute 成功和内存变化是本次可支持的 NPU 证据。没有执行 10 轮 RSS/FD/NPU
内存泄漏门。

## 可重复命令边界

复现实验必须使用独立目录、B1 专用 OM 和明确的 base overlay。wheel 清单位于
`repro/qwen25-kv1024-20260825/reports/board20t/20260827T045500Z/artifacts/wheels/wheel-manifest.json`；
板端 overlay 预检报告为
`repro/qwen25-kv1024-20260827-20t/run/replacement/192.168.8.210/20260827T045500Z/reports/base-overlay-check.txt`，
服务停止复核为
`repro/qwen25-kv1024-20260827-20t/run/replacement/192.168.8.210/20260827T045500Z/reports/service-postcheck.txt`。
命令中的 `ROOT` 必须解析为 `/home/HwHiAiUser/case9-qwen25-kv1024-20260827-20t`：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
export PYTHONNOUSERSITE=1
export CASE9_QWEN25_KV_SOC_VERSION=Ascend310B1
export CASE9_QWEN25_KV_ALLOW_DIRTY_BASE=1
ROOT=/home/HwHiAiUser/case9-qwen25-kv1024-20260827-20t
export PYTHONPATH="$ROOT/base-overlay:$ROOT/src-board"
```

不得把归档 `Ascend310B4` OM 静默替换 B1 正式工件；如需验证跨 SoC 复用，只能在
独立 `cross-om-test` 目录中显式执行并记录兼容性例外，详见
[`docs/22-qwen25-cross-board-om-validation.md`](22-qwen25-cross-board-om-validation.md)。
本报告的 B1 专用 OM 结果仍是 20T 的规范实验路径。TinyLlama、音频闭环和 XiaoZhi
仍是独立验收项；本报告只证明 Qwen2.5 静态 KV 在 20T 上的实验性文本/NPU/API
性能结果。
