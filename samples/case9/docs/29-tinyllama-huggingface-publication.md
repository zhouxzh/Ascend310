# TinyLlama Hugging Face 发布记录

_记录日期：2026-09-02｜目的：发布可复核的 TinyLlama 实验工件，并明确其已知问题；不代表生产准入。_

---

## 发布结论

TinyLlama 工件发布到 [`zhouxzh/ascend310b-llm-om-zoo`](https://huggingface.co/zhouxzh/ascend310b-llm-om-zoo) 时，按“历史实验工件”单独归档。模型卡明确写出：它曾在 Ascend 310B4 上完成 ACL/API 试验，但没有当前 CANN 8.0 的完整转换证据，也没有通过中文质量和长输出门禁。发布不等于推荐使用。

本次发布由多个可追溯提交组成：元数据和 tokenizer 为 [`6bc3b897`](https://huggingface.co/zhouxzh/ascend310b-llm-om-zoo/commit/6bc3b897b4a24c1a400fd006a10a9d5c02d98b90)，OM 为 [`51818dc0`](https://huggingface.co/zhouxzh/ascend310b-llm-om-zoo/commit/51818dc0c7e8a7d7b501bd34bcb51746818cb128)，锁文件为 [`5e454113`](https://huggingface.co/zhouxzh/ascend310b-llm-om-zoo/commit/5e454113fa5857c8ea4c16960e2a331108e073cd)，源权重为 [`3174d5c4`](https://huggingface.co/zhouxzh/ascend310b-llm-om-zoo/commit/3174d5c47a2ffff91afb72ec82a111f4f586cd06)，源权重锁为 [`0f516070`](https://huggingface.co/zhouxzh/ascend310b-llm-om-zoo/commit/0f516070e1c0566e3c652d43c488cd3cfbad89a3)。远端 LFS 元数据再次报告的 OM、tokenizer 和源权重 SHA-256 与下表一致。

```mermaid
flowchart LR
    accTitle: TinyLlama artifact publication flow
    accDescr: A historical community OM and matching tokenizer are published with provenance and failure evidence; the artifact remains blocked for Chinese production use.
    source[社区 OM 与 tokenizer] --> verify[字节与 SHA 校验]
    verify --> publish[实验工件发布]
    publish --> limits[模型卡记录限制]
    limits --> blocked[中文生产准入阻断]
```

## 工件清单

来源为 [`wan-zutao/tiny-llama-manual-reset`](https://gitee.com/wan-zutao/tiny-llama-manual-reset)，固定源码 commit `114a158718411d8b0a252806ca14144c01a7e3db`。OBS 对象的 multipart ETag 不是 SHA-256；下表使用本地完整下载后计算的 SHA-256。

| 工件 | 发布路径 | 字节数 | SHA-256 | 说明 |
| --- | --- | ---: | --- | --- |
| 预编译 OM | `models/tinyllama-1.1b-chat/ascend310b4-cann8.0/tiny-llama.om` | `1,493,077,371` | `604e47c5b6e1239abcc012d7e8d4be8398465657a142ad59280d2c1917eda967` | 社区预编译、仅作 B4/实验证据 |
| tokenizer 压缩包 | `models/tinyllama-1.1b-chat/common/tokenizer.zip` | `709,459` | `d785e2532e65d83fd34870e762cc3c65326991ddcc97179796860ab9893f6917` | 与历史 OM 配套 |
| `tokenizer.json` | `models/tinyllama-1.1b-chat/common/tokenizer.json` | `1,842,767` | `bcd04f0eadf90287bd26e1a183ac487d8a141b09b06aecb7725bbdd343640f2e` | 解压后校验 |
| `tokenizer.model` | `models/tinyllama-1.1b-chat/common/tokenizer.model` | `499,723` | `9e556afd44213b6bd1be2b850ebbbd98f5481437a8021afaf58ee7fb1818d347` | 解压后校验 |
| `special_tokens_map.json` | `models/tinyllama-1.1b-chat/common/special_tokens_map.json` | `414` | `6fa06efa2785e450051989a6f8fb4416b10149ded485ddd3f127a40734f5cfd0` | 解压后校验 |
| `tokenizer_config.json` | `models/tinyllama-1.1b-chat/common/tokenizer_config.json` | `932` | `bcdc6f267b05e1afd27fa622a62fea649bcb941e6dc705d2835883a0746192da` | 解压后校验 |
| MindSpore 源权重 | `source/tinyllama-1.1b-chat/model.safetensors` | `2,200,119,864` | `6e6001da2106d4757498752a021df6c2bdc332c650aae4bae6b0c004dcf14933` | 与 OM 分开；不是 OM 转换证明 |

同时发布 `config.json`、`generation_config.json`、模型卡来源说明和逐文件 manifest。历史 `tiny-llama.onnx` 没有本地 SHA-256，也不是首轮运行依赖，因此不作为已验证工件发布。

## 已确认的问题

### OM 与运行环境

- OM 来自社区历史对象，缺少在本板当前 CANN 8.0 上重新 ATC 的完整日志、可复核的官方支持声明和当前板 `npu-smi` 证据。它只能标记为 `experimental`，不能写成 Ascend 官方模型。
- OM 是面向 `Ascend310B4` 的预编译工件。其他 SoC、CANN 或驱动组合必须重新读取 descriptor、执行 ACL smoke 和记录哈希，不能按文件名推断兼容。
- `model.safetensors` 是 MindSpore/Transformers 源权重，和预编译 OM 是两个不同工件；上传源权重不会证明它能在 ACL/NPU 上转换或运行。

### 中文和长输出

- TinyLlama 官方模型主要面向英文；Case9 的固定中文探测中，机器质量为 `7/10`，不能作为中文聊天能力通过。
- 8T 与 20T 的长输出批次在 `max_tokens=32/48` 观察到 `U+FFFD` 替换字符，UTF-8 完整性门失败；因此 Profile `tinyllama-1.1b-mindspore` 保持 `blocked`。
- 历史 10 轮观察还记录过 NPU/HugePages 增长风险。该现象需要新的受控批次复核，不能被一次短 smoke 覆盖。

### 准入边界

当前 MindSpore 记录中，实际曾在 NPU 上启动并产生输出的聊天模型有 3 个（Qwen1.5、TinyLlama、DeepSeek）；按历史报表的完整 9/9 机器门计为 2 个（Qwen1.5、DeepSeek），但 Qwen1.5 的 8T 严格身份门仍为 8/9 待补证，正式 `admitted` 为 0 个。TinyLlama 的 ACL/API 试验通过不改变它的中文质量和长输出失败状态，也不允许接入正式 `8080 -> 7861 -> 7865` 或 XiaoZhi。

## 复核命令

下载后必须先校验字节数和 SHA-256，再解压 tokenizer。下面的命令只检查文件，不安装推理框架：

```bash
sha256sum tiny-llama.om tokenizer.zip tokenizer.json tokenizer.model \
  special_tokens_map.json tokenizer_config.json
```

板端 ACL 运行仍需显式加载 CANN 环境、读取 OM descriptor，并使用与目标 SoC 匹配的 runtime。遇到 descriptor、ACL、长输出或资源错误时，应保留日志并停止候选服务，不切换 CPU、云端、Torch、MindSpore 之外的后端或未经审核 OPP。

## 证据位置

- Case9 TinyLlama 验收账本：[`docs/24-mindspore-chat-validation-record.md`](24-mindspore-chat-validation-record.md)
- 双板缺口记录：[`docs/28-case9-dual-board-gap-validation-record.md`](28-case9-dual-board-gap-validation-record.md)
- 当前模型清单：[`local_model_manifest.json`](../local_model_manifest.json)
- 上游实现：[`wan-zutao/tiny-llama-manual-reset`](https://gitee.com/wan-zutao/tiny-llama-manual-reset)
- 上游模型卡：[`TinyLlama/TinyLlama-1.1B-Chat-v1.0`](https://huggingface.co/TinyLlama/TinyLlama-1.1B-Chat-v1.0)
- 原始 OM 对象：[OBS tiny-llama.om](https://obs-9be7.obs.cn-east-2.myhuaweicloud.com/models/tiny_llama/tiny-llama.om)
- 原始 tokenizer 对象：[OBS tokenizer.zip](https://obs-9be7.obs.cn-east-2.myhuaweicloud.com/wanzutao/tiny-llama/tokenizer.zip)

本记录只描述已实际下载、校验或已保存的证据；它不把模型卡、一次生成或协议成功扩大为中文质量、生产稳定性或设备语音闭环结论。
