# Qwen2.5 0.5B 当前地址 context-only 诊断

_测试日期：2026-09-04｜板卡：Ascend310B4 / 8T｜地址：192.168.11.14｜结论：诊断完成，严格准入仍阻断_

## 测试边界

本记录补充同一块 8T 开发板在地址变更后的 Qwen2.5-0.5B 诊断。`192.168.11.14`
是当前地址，旧的 `192.168.1.90` 和 `192.168.8.178` 是同一硬件的历史地址；本批次
不是新的硬件性能结果。

只使用板端现有 `base` 环境、MindSpore 2.4.10、MindNLP 0.4.1 和 CANN 8.0.0。
没有安装、升级或删除包，没有启动 `8090`、候选网关、正式网关、浏览器、音频或
XiaoZhi。命令显式使用 `--context-only`，因此只允许观察模型加载和短生成，不能
作为服务准入或生产可用性证明。

## 固定工件

| 项目 | 值 |
| --- | --- |
| 模型 | `Qwen/Qwen2.5-0.5B-Instruct` |
| revision | `7ae557604adf67be50417f59c2c2f167def9a775` |
| 权重 | `988097824` bytes；`fdf756fa7fcbe7404d5c60e26bff1a0c8b8aa1f72ced49e7dd0210fe288fb7fe` |
| tokenizer | `7031645` bytes；`c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539` |
| 配置 | `659` bytes；`18e18afcaccafade98daf13a54092927904649e1dd4eba8299ab717d5d94ff45` |

其余 `tokenizer_config.json`、`generation_config.json` 和 `merges.txt` 也由板端验证器
核对，具体逐文件结果保存在诊断报告中。

## 环境与命令

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
unset PYTHONNOUSERSITE
export PYTHONPATH="$ROOT:$PYTHONPATH"
python scripts/qwen25_15_memory_gate.py \
  --root "$ROOT" \
  --registry "$ROOT/configs/chat_model_profiles.json" \
  --profile qwen2.5-0.5b-mindspore \
  --board-ip 192.168.11.14 \
  --prompt '你好，请用一句话介绍你自己。' \
  --max-tokens 2 --context-only --generation-timeout 300 \
  --output "$OUT/report.json"
```

实际运行目录为：

```text
repro/mindspore-chat-20260904/reports/board8t/
  qwen2.5-0.5b-mindspore/
  qwen25-05-context-only-clean-20260904T073649Z/
```

板端原始目录为：

```text
/home/HwHiAiUser/case9-mindspore-chat/reports/mindspore-chat/
  ip-20260904/qwen25-05-context-only-clean-20260904T073649Z/
```

## 实测结果

| 阶段 | 实测结果 | 证据边界 |
| --- | --- | --- |
| CANN Python 模块 | `acl`、`te`、`hccl` 均可见并导入 | 环境检查 |
| SoC | before/after 均为 `Ascend310B4` | 板卡身份 |
| 模型加载 | `48.649923 s` | loader 诊断 |
| prompt | `36` tokens | tokenizer/输入统计 |
| 生成 | `2` tokens，`15.225400 s`，文本为 `我是Q` | context-only 短生成 |
| 设备内存 | `7113 MB`（before）到约 `14900 MB`（after） | 资源旁证，不是 placement 证明 |
| NPU 活动 | 采样期间设备内存持续分配；`npu-smi` 可识别 B4 | 运行旁证；无参数级 placement metadata |
| 清理 | report `after_npu_gate=passed`，进程退出 | 单次清理观察 |

原始 `report.json` 的 SHA-256 为
`16e5bf950a25f92766ea6d8d7f28062ba0fba507373b9b3d25ea9bbf25a0afbd`，`run.log` 为
`39d2b0cb04eef52d63c6d084eee877eb2e2cf1571c6f109ef213104fe4ae2a7`。目录中的
`SHA256SUMS.txt` 列出全部五个原始文件的哈希。

## 门禁判定

这次显式 context-only 运行成功，但 provider 的 placement 状态仍是
`context_only`，模型对象没有提供可枚举的 Ascend 参数/输出 placement 证据。严格
运行仍会在生成前返回：

```text
ProviderUnavailable: MindSpore model exposes no explicit Ascend placement evidence
```

因此 Profile 继续保持：

```text
Ascend310B4: blocked
API/JSON/SSE: not-run
长输出/稳定性/质量/性能: not-run
```

设备活动不能替代严格 placement 门，也不能证明没有 CPU fallback。不得通过修改环境
变量、临时注册表或直接启动 worker 来绕过该门禁。正式 `8080 -> 7861 -> 7865` 和
候选 `7868 -> 7867 -> 8090` 均未改变。

## 后续边界

若要继续该 Profile，下一步应先获得可审计的参数或输出 placement 证据，并在同一
工件和环境下重新通过严格 G2；之后才允许执行 API、长输出、稳定性、中文质量和性能
门。当前地址只是原 8T 板的连接地址变化，不需要把历史性能结果重算成新板批次。
