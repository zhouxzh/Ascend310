# 16GB 8T MindSpore模型矩阵

## 测试边界

本矩阵中的所有结果均产自同一块Ascend310B4 / 8T板卡；该板NPU上报内存为15,610 MB，CANN为8.0.0，驱动为25.2.0，并使用用户自有的`ascend`环境：Python 3.9.25、MindSpore 2.4.10、MindNLP 0.4.1和`PYTHONNOUSERSITE=1`。每个模型都通过同一个Case9 MindSpore worker加载。当板上旧的MindNLP序列化实现将BF16报告为错误的NumPy宽度时，worker的BF16兼容分支处于启用状态。

板卡的`Health: Alarm`、周期性LPM消息和MATA RAS事件是独立的设备风险证据。它们不会被用作笼统的模型失败依据。以下每个结果都记录实际的模型加载和API结果。

## 已验证模型

| 模型 | 固定revision | 加载 | API生成 | 受测输出预算 | 状态 |
| --- | --- | --- | --- | ---: | --- |
| Qwen1.5 0.5B Chat | `4d14e384a4b037942bb3f3016665157c8bcb70ea` | passed | JSON和SSE通过 | 256 | observed-pass |
| Qwen1.5 1.8B Chat | `e482ee3f73c375a627a16fdf66fd0c8279743ca6` | passed | 首次8 Token生成失败：Ascend `Alloc failed`，632186368字节 | 0 | observed-fail |
| Qwen2 0.5B Instruct | `befdd363d82258f10fa0cbf9f76a4210347dd432` | passed | JSON通过；8/64 Token回复中无U+FFFD | 64 | observed-pass |
| Qwen2 1.5B Instruct | `b139ca2b8d7ba579ed727ffcd7060075666a808b` | passed | JSON通过；8/32 Token回复中无U+FFFD | 32 | observed-pass |
| Qwen2.5 0.5B Instruct | `7ae557604adf67be50417f59c2c2f167def9a775` | passed | JSON通过 | 256 | observed-pass |
| Qwen2.5 1.5B Instruct | `989aa7980e4cf806f80c7fef2b1adb7bc71aa306` | passed | 中文32/64/96无异常；中文128反复出现U+FFFD；英文32/64/96/128无异常 | 128 | observed-degraded (Chinese long output) |
| TinyLlama 1.1B Chat | `fe8a4ea1ffedaf415f4da2f062534de366a451e6` | passed | 英文8/32/64 Token JSON通过，无U+FFFD或角色标记 | 64 | observed-pass (English) |

受测预算不是硬性上下文上限。它记录在该模型已记录的提示和测试路径下经验证的最高实际完成量。正常的模型EOS可以在达到请求预算前结束输出，而不使请求失败。语言是测试契约的一部分：TinyLlama按英文优先模型评估，而Qwen系列模型分别记录中文和英文文本完整性观察结果。

## 失败候选

| 模型 | 固定revision | 失败层 | 证据 | 状态 |
| --- | --- | --- | --- | --- |
| DeepSeek-R1 Distill Qwen 1.5B | `0a28897fe71fdd30de350b667ae588601a85990f` | MindSpore/TBE模型加载 | TBE `task_distribute`报告主进程消失；30条semaphore cleanup警告；没有health/API成功 | observed-fail |

DeepSeek失败的范围限于这个B4/CANN/MindSpore/MindNLP/runtime/模型组合。它不是硬件普遍不受支持或其他模型无法加载的证据。

## 因资产缺失未运行

板卡目前不包含这些profile所需的固定revision文件。它们仍为`untested`，而不是失败：


Qwen3 0.6B另因当前MindNLP加载器前置条件而受阻。它没有在本次测试活动中下载或加载。

## 与PyACL的对照

独立的20T Ascend310B1测试活动验证了B1专用的Qwen2.5-0.5B static-KV OM。该B1 OM不能用于这块B4板卡。反过来，本矩阵中的MindSpore结果也不构成PyACL/OM转换、数值一致性或性能证据。

## 板端证据

原始板端报告保留在仓库外部：

```text
/home/HwHiAiUser/Documents/ascend-edge-chat-ms-8t/reports/
```

8T串口与清理调查记录在Case9问题台账中。
