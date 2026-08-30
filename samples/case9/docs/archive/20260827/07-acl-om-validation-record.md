# ACL/OM LLM 验证记录

## 记录说明

这是板端实验记录模板。未填写的字段表示未执行，不表示通过。每个结果必须标注为
环境检查、工件完整性、ONNX 契约、ATC 转换、ACL smoke、NPU 证据、API 协议或性能观察；
禁止用其中一类结果替代另一类结果。

## 2026-08-21 实测状态

本轮状态为 **`blocked`**。环境门和固定工件完整性门已经通过，但 ONNX 契约门拒绝了该
通用 Transformers.js 图，因而严格停止在契约门；没有运行 ATC、OM 加载、ACL 推理或
8080 服务，也没有切换到 CPU、云端、Torch、MindSpore 或其他模型。

| 门 | 实测结果 | 板端证据 |
| --- | --- | --- |
| 独立 Python 环境 | 通过；`case9-acl-om`，Python 3.9.25 | `/home/HwHiAiUser/case9-local-chat/reports/acl-om/20260821T030423Z-install-runtime.log` |
| 锁定运行时 | 通过；`numpy 1.26.4`、`onnx 1.16.2`、`tokenizers 0.19.1`，仅直接 wheel、`--no-deps` | 同上 |
| ACL 与禁止包 | 通过；`acl` 可导入，`torch`、`torch_npu`、`torchaudio`、`mindtorch`、`torchvision`、`xformers` 均为 `absent` | `/home/HwHiAiUser/case9-local-chat/reports/acl-om/20260821T041937Z-acl-module-check.log` |
| CANN/设备 | 通过环境门；toolkit 目录 `8.0.0`，组件 `Version=7.6.0.1.220`，`ASCEND_CUSTOM_OPP_PATH` 为空，识别 `310B4` | `20260821T050008Z-cann-version.log`、`20260821T050008Z-npu-smi.log` |
| 可用磁盘 | 通过；`/home` 可用约 `196G`，脚本要求至少 `2G` | `/home/HwHiAiUser/case9-local-chat/reports/acl-om/20260821T050008Z-disk.log` |
| `npu-smi` 健康 | `Alarm`；按项目规则记录但不单独阻断 | `20260821T050008Z-npu-smi.log` |
| ACL/OM 纯协议与契约测试 | 通过；板端 `17/17`，未加载模型、未执行 NPU | `/home/HwHiAiUser/case9-local-chat/reports/acl-om/20260821T043230Z-acl-om-tests.log` |
| 固定 ONNX/tokenizer 下载 | 内容完整性通过；Hugging Face 直连重试 4 次为 `curl_exit=28`，随后使用 ModelScope 对象存储取得相同字节，大小和 SHA-256 均匹配。该传输来源不是候选的 canonical HF URL，已单独记录，不能替代 revision/哈希审计 | HF 失败：`20260821T035739Z-acl_om_llm-download.log`；ModelScope：`20260821T044204Z-modelscope-download.log`、`20260821T044512Z-modelscope-tokenizer-download.log`；板端锁：`acl-om-artifacts.lock.json` |
| ONNX 契约 | 失败；图含 51 个输入（含 48 个 `past_key_values`）、49 个输出（含 48 个 `present`），实际 logits 为 float32，存在动态/符号维度，且操作审计发现 `ai.onnx:Sigmoid` | `qwen1.5-0.5b-onnx-inspection.json`；`qwen1.5-0.5b-acl-contract.json` |
| ATC、OM、ACL、NPU 推理、OpenAI API | 未执行，受 ONNX 契约门阻断 | 不运行不满足契约的图；无伪造结果 |
| XiaoZhi | 未安装、未启动 | 保持第二阶段暂停 |

最终门禁快照（无服务启动、无 OM、无禁止模块、`contract_admitted=false`）已保存为
`/home/HwHiAiUser/case9-local-chat/reports/acl-om/20260821T045200Z-final-gate-status.log`；
其 SHA-256 为 `b5119a62091c5c30afc8771ab4ff2a87626a6d22e54c9a3f2907adcdd764c013`。

板端系统快照：`orangepiaipro`，`aarch64`，Linux `5.10.0+`，根分区可用约 `198G`；
报告目录为 `/home/HwHiAiUser/case9-local-chat/reports/acl-om/`。工件已留在板端并通过
大小和 SHA-256 校验；后续不能跳过 ONNX 契约门，也不能因为工件可下载就直接运行 ATC。
若重新部署，优先使用 canonical HF URL；ModelScope 传输只作为本次内容一致性证据，不能
被脚本当作任意镜像或新的模型来源。

本轮还完成了代码级门禁加固：ACL 使用显式 stream/异步执行；超时或异常先执行无信号
stream 同步，失败时保留 dataset/buffer 句柄并标记服务需重启，不在设备可能仍运行时 free；
contract 绑定工件 bytes/SHA/revision 和固定输入顺序；检查器执行 ONNX checker、opset
范围和标准操作集审计；下载、磁盘、OM 锁和禁止包检查均写入日志。由于没有 OM，以上 stream、
deadline、ACL 内存释放和 NPU 生成仍属于代码已具备但硬件未执行的门，不能写成通过。

## 实验身份

| 字段 | 记录 |
| --- | --- |
| 记录日期（UTC） | `2026-08-21T03:24:00Z` |
| 最后更新（UTC） | `2026-08-21T05:00:08Z` |
| 操作员 | `<不写入密钥>` |
| 板端主机 | `192.168.8.178` |
| NPU 型号/计算档位 | `Ascend 310B4 / 8T` |
| `npu-smi` Health | `Alarm`（不是单独阻断条件） |
| `uname -a` | `Linux orangepiaipro 5.10.0+ #32 SMP Thu Sep 25 17:54:23 CST 2025 aarch64`；`20260821T032500Z-system-check.log` |
| Python/conda 环境 | `case9-acl-om`, Python `3.9.25` |
| CANN/ATC 版本 | toolkit 目录 `8.0.0`；组件 `Version=7.6.0.1.220`；ATC `/usr/local/Ascend/ascend-toolkit/latest/bin/atc` |
| CANN env 脚本 | `/usr/local/Ascend/ascend-toolkit/set_env.sh` |
| 报告目录 | `/home/HwHiAiUser/case9-local-chat/reports/acl-om/` |

## 无 Torch 环境门

| 检查 | 结果 | 证据路径 |
| --- | --- | --- |
| `import acl` | `通过` | `20260821T041937Z-acl-module-check.log` |
| `torch` 不存在 | `通过` | `20260821T041937Z-acl-module-check.log` |
| `torch_npu` 不存在 | `通过` | `20260821T041937Z-acl-module-check.log` |
| `torchaudio` 不存在 | `通过` | `20260821T041937Z-acl-module-check.log` |
| `mindtorch` 不存在 | `通过` | `20260821T041937Z-acl-module-check.log` |
| `ASCEND_CUSTOM_OPP_PATH` 未设置 | `通过` | `20260821T041937Z-acl-module-check.log` |
| `npu-smi info` 识别 310B4 | `通过` | `20260821T041937Z-acl-module-check.log` |

## 工件完整性门

| 工件 | 固定 revision | 实测 bytes | 预期 SHA-256 | 实测 SHA-256 | 结果 |
| --- | --- | ---: | --- | --- | --- |
| `onnx/model_fp16.onnx` | `6d413dd9a252749e0760902c93331e3e4e65b73c` | `928499243` | `1397b07c02c5821316ca20cb64f45af87b87932eddd13c743d988d5a7c826262` | `1397b07c02c5821316ca20cb64f45af87b87932eddd13c743d988d5a7c826262` | `通过（内容完整性）` |
| `tokenizer.json` | `6d413dd9a252749e0760902c93331e3e4e65b73c` | `11418266` | `bcfe42da0a4497e8b2b172c1f9f4ec423a46dc12907f4349c55025f670422ba9` | `bcfe42da0a4497e8b2b172c1f9f4ec423a46dc12907f4349c55025f670422ba9` | `通过（内容完整性）` |
| `config.json` | same | `N/A` | `未纳入首轮固定工件` | `N/A` | `本轮不下载` |
| `tokenizer_config.json` | same | `N/A` | `N/A` | `N/A` | `本轮不下载` |

LFS pointer 检查：通过；最终文件不是 pointer，也没有保留 `.part` 文件。固定 HF URL 的
初始 IPv4 探测和正式下载分别记录于 `20260821T032400Z-huggingface-connectivity.log`
和 `20260821T035739Z-acl_om_llm-download.log`，两者为 `curl_exit=28`。为继续做本地
契约审计，板端通过 ModelScope 的固定仓库路径取得文件；其响应的 `X-Linked-ETag` 与
manifest SHA 相同，整文件重算也相同。传输日志中的 URL、大小和 SHA 仅是 provenance，
不改变 canonical HF revision。

## ONNX 契约门

| 项目 | 实测值 | 结果 |
| --- | --- | --- |
| ONNX IR/opset | `opset 14` | `通过范围检查，但不代表契约通过` |
| graph 输入名称、dtype、shape | `51 个输入；前三个为 int64 [?,?]，另有 48 个 float32 past KV 输入` | `阻断` |
| graph 输出名称、dtype、shape | `49 个输出；logits 为 float32 [?, ?,151936]，另有 48 个 float32 present KV 输出` | `阻断` |
| 动态维度 | `存在动态或符号维度` | `阻断` |
| KV-cache 布局 | `past_key_values.* 与 present.* 均存在` | `阻断；首轮 runtime 不支持` |
| 外部数据文件 | `未发现 external initializer` | `通过检查；不代表契约通过` |
| 量化算子 | `未发现量化算子` | `观察项` |
| 操作审计 | `ai.onnx:Sigmoid` 未列入首轮 allowlist | `阻断` |
| ACL contract admitted | `false` | `阻断（inspect 退出码 2）` |
| 报告路径 | `/home/HwHiAiUser/case9-local-chat/reports/acl-om/qwen1.5-0.5b-onnx-inspection.json` | `已生成` |

只有契约 JSON 明确允许 batch 1、长度 2048、Qwen logits 输出时，才能进入 ATC。

## ATC/OM 门

```text
command: atc --model=<verified-onnx> --framework=5 --output=<prefix> \
  --input_format=ND \
  --input_shape='input_ids:1,2048;attention_mask:1,2048;position_ids:1,2048' \
  --soc_version=Ascend310B4 --output_type=FP16
exit_code: 未执行（ONNX 契约门阻断）
log: N/A
```

| 项目 | 记录 |
| --- | --- |
| OM 文件 | `N/A` |
| OM bytes | `N/A` |
| OM SHA-256 | `N/A` |
| ATC 结果 | `未执行（阻断）` |
| 失败原因 | `固定图不满足首轮静态 full-context contract；不是 ATC 失败` |

## ACL/NPU smoke 门

| 项目 | 记录 |
| --- | --- |
| 服务入口 | `acl_om_service.py` |
| 模型名 | `qwen1.5-0.5b-chat-acl-om` |
| 监听地址 | `127.0.0.1:8080` |
| prompt | `你好`（不记录用户隐私文本） |
| timeout | `300 s` |
| ACL 初始化 | `未执行（无 OM）` |
| stream/异步执行与 deadline | `代码已实现；未执行（无 OM）` |
| model/context/stream 释放 | `未执行（无 OM）` |
| 生成结果 | `N/A` |
| pre-smoke `npu-smi` | `N/A` |
| post-smoke `npu-smi` | `N/A` |
| smoke 日志 | `N/A` |
| 结果 | `未执行（阻断）` |

## OpenAI API 门

| 请求 | 结果 | 证据 |
| --- | --- | --- |
| `GET /v1/models` | `未执行（8080 未启动）` | `N/A` |
| JSON `POST /v1/chat/completions` | `未执行（8080 未启动）` | `N/A` |
| SSE `POST /v1/chat/completions` | `未执行（8080 未启动）` | `N/A` |
| case9 gateway `7861` 转发 | `未执行（避免上游假通过）` | `N/A` |
| `local_app` 文本请求 | `未执行（等待真实 LLM）` | `N/A` |

API 通过前不启动本地音频闭环，不配置或启动 XiaoZhi。

## 结果判定

最终状态只能填写：

- `passed`: 所有硬门完整通过，且附有原始日志、哈希和 NPU 证据；
- `blocked`: 任一硬门失败或尚未执行；
- `observed-only`: 只有协议/静态检查，未达到真实 NPU 推理门。

本轮最终状态：`blocked`。当前工件已完成完整性校验，但候选图不能进入 ATC。需要另行
审核一个不含动态 KV-cache、满足输入输出契约且通过操作审计的 310B4 候选；在此之前不
启动 8080、网关、本地聊天或 XiaoZhi。
