# Qwen2.5 静态 KV OM 本地副本记录

## 记录范围

本文记录从 Ascend 310B4 开发板 `192.168.1.90` 同步回 Windows 控制机的
Qwen2.5 静态 KV 1024 OM 工件及其最小 provenance 文件。同步目标是保留可审计的
本地副本，不改变板端正式服务，也不把 OM 声明为可在 Windows CPU 上直接运行的模型。

同步完成时间：2026-08-25（Asia/Shanghai）。

## 工件身份

| 项目 | 板端来源 | 本地副本 | bytes | SHA-256 |
| --- | --- | --- | ---: | --- |
| OM | `/home/HwHiAiUser/case9-qwen25-kv1024/artifacts/qwen25-static-kv-1024-v2.om` | `artifacts/qwen25-kv-1024/board/qwen25-static-kv-1024-v2.om` | 1,266,010,586 | `f6650e52ff3908288763ef7957832ade606b0e554fa8fde986932f1ca1140eb8` |
| OM lock | `.../artifacts/qwen25-static-kv-1024-v2.om.lock.json` | `artifacts/qwen25-kv-1024/board/qwen25-static-kv-1024-v2.om.lock.json` | 1,421 | `7d6b35de8261ce3e5f077a6d6d3b6e19df43a2fba2470a2ef30c0d217a8c2770` |
| OM descriptor contract | `.../contracts/qwen25-static-kv-1024-v2-om-contract.json` | `contracts/qwen25-kv-1024/board/qwen25-static-kv-1024-v2-om-contract.json` | 32,172 | `923255c717165a2529e15563f2acd5cb1e31b67769e2a4afa4d3233053e310b6` |
| ATC log | `.../logs/atc-retry1-20260823T110028Z.log` | `reports/qwen25-kv-1024/board/atc-retry1-20260823T110028Z.log` | 22,289 | `e100b16935e0ca9438b08968a832388bd4d02302047867ad832a4917189c286a` |
| ACL smoke | `.../reports/20260823T111040Z-acl-smoke.txt` | `reports/qwen25-kv-1024/board/20260823T111040Z-acl-smoke.txt` | 334 | `e38a8fbae47fb1999cbd90112f7fa254b65e907bbc514baa578c57bafb9eeaee` |

板端和本地 OM 的 SHA-256、字节数均一致。OM 的模型 ID 为
`qwen2.5-0.5b-instruct-static-kv-1024-fp32-acl-om`。

## 来源和转换契约

OM 是板端使用 CANN `8.0.0`、`atc --soc_version=Ascend310B4` 从控制机生成的
单文件 ONNX 转换得到的，不是 Qwen 官方发布的 OM：

```text
source ONNX bytes: 1,261,082,122
source ONNX SHA-256: b4870df5da9c8cbef4163ceb65d4dc13433f2fd8ed5d2083ef3223d07d1a3c0e
ATC precision: must_keep_origin_dtype
execution mode: static_kv_token_fp32
cache layout: split
static sequence length: 1024
cache tensors: 48 x float32 [1,2,1024,64]
logits output: float32 [1,1,151936]
token cache outputs: 48 x float32 [1,1,2,64]
```

完整 ATC 参数、源 ONNX 哈希和 contract 哈希保存在本地 OM lock 文件中；descriptor
顺序必须以 contract 为准，不能自行猜测输入或输出索引。

## 传输和完整性检查

采用 WSL 中的 `rsync`，命令使用 `--partial --append-verify`，并通过 SSH
`ServerAliveInterval`/`ServerAliveCountMax` 保持连接。第一次传输因板端网络短暂中断
在约 58% 处断开，保留了本地部分文件；网络恢复后从断点继续完成。续传完成后：

1. 本地文件大小为 `1,266,010,586` bytes；
2. Windows `Get-FileHash -Algorithm SHA256` 得到
   `f6650e52ff3908288763ef7957832ade606b0e554fa8fde986932f1ca1140eb8`；
3. 板端重新读取同一源文件得到相同 SHA-256；
4. lock、contract、ATC 日志和 ACL smoke 文件逐一重新哈希并与板端一致。

## Git 和运行边界

OM、contract、报告和 board provenance 目录已加入 `.gitignore`，不会提交到仓库。
本地 OM 副本只用于离线归档、哈希复核和后续明确授权的控制机检查；Windows 环境没有
Ascend ACL runtime，不能直接执行该 OM。板端正式入口仍使用板端路径，当前服务链路和
PID 不因同步改变：`7865 -> 7861 -> 8080`。

本地副本完整性通过不等于以下门通过：

* Windows/CPU 推理；
* 重新 ATC 或跨 CANN 版本兼容性；
* XiaoZhi 音频闭环；
* 新板卡或不同 Ascend 型号上的 ACL 运行。

后续若复制 OM 到其他开发板，必须重新记录板卡型号、CANN/驱动版本、ATC/ACL
descriptor、NPU smoke 和完整 SHA-256，不能只复用本记录。

本次用户要求的完整可重复数据包不止包含 OM，已整理到被 Git 忽略的
`repro/qwen25-kv1024-20260825/`，入口文档为
[`docs/20-qwen25-kv1024-reproducibility-bundle.md`](20-qwen25-kv1024-reproducibility-bundle.md)。
