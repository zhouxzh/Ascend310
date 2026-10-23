# 第8章样例工程文档

本目录补充 `samples/chapter8` 的运行合同和证据边界。根目录 `README.md` 是快速命令入口；这些文档说明每个脚本实际计时什么、需要哪些资源，以及哪些结果只能在 Ascend 310B 上成立。

本次整理只检查源码和既有报告，没有执行 ATC、PyACL、DVPP、AIPP、`msprof` 或 `npu-smi`。因此所有板端路径均标记为 `untested`，既有报告只按历史记录处理。

## 文档索引

| 文档 | 内容 |
|---|---|
| [01-runtime-model-and-io-contracts.md](01-runtime-model-and-io-contracts.md) | PyACL runner、OM 模型、预处理、AIPP 和确定性输入的合同 |
| [02-benchmark-and-profiling-runbook.md](02-benchmark-and-profiling-runbook.md) | 六个编号脚本、Queue Pipeline、`msprof` 和指标汇总的执行方式 |
| [03-failure-semantics-and-evidence.md](03-failure-semantics-and-evidence.md) | 本地与板端边界、失败处理、测试缺口、历史报告的来源约束 |

## 入口分类

| 入口 | 执行位置 | 目的 |
|---|---|---|
| `03_cpu_preprocess_benchmark.py` | 本地或板端 CPU | 比较 NumPy 预处理与工作区复用 |
| `05_pipeline_queue_demo.py --simulate` | 本地或板端 | 用固定 sleep 模拟串行和三段队列流水 |
| `01_baseline_resnet_sync.py` | Ascend 310B | 同步 PyACL ResNet18 基线 |
| `02_buffer_reuse_benchmark.py` | Ascend 310B | 比较逐帧分配与复用 ACL Buffer/Dataset |
| `04_dvpp_resize_vs_cpu.py` | Ascend 310B | 比较 DVPP VPC 与 OpenCV resize |
| `05_pipeline_queue_demo.py` 无 `--simulate` | Ascend 310B | 用真实 PyACL 推理比较串行和线程流水 |
| `06_aipp_preprocess_compare.py` | Ascend 310B | 比较 CPU 预处理与静态 AIPP OM |

## 模型与报告位置

- 默认普通 OM：`model/resnet18_tiny_imagenet.om`
- 默认 AIPP OM：`model/resnet18_tiny_imagenet_aipp.om`
- AIPP 配置：`model/resnet18_rgb_static_aipp.cfg`
- 默认指标目录：`outputs/`
- `msprof` 原始目录：`outputs/msprof/`

`model/` 和 `outputs/` 中的大型或生成文件由 `.gitignore` 排除。清理工作区或在另一台机器复现时，必须重新下载/转换模型并重新生成报告，不能假定这些文件随 Git 提供。

## 证据状态

现有历史 `outputs/*.json` 可以解析为 JSON，并包含主机、平台、Python、命令、预热和运行次数等字段；但它们没有完整的 SoC 计算档位、CANN、driver、firmware 和 Git revision，且多数命令仍自报旧的 `samples/chapter7` 路径。不得把这些历史报告直接改名为当前 chapter8 的 `observed-pass`。

2026-10-23 已在在线 `Ascend310B1 / 20T` 上完成新的最小验证。固定 OM revision 为 `dfcf5885dcf7beab3a5d6e2143c73d7973bead36`，OM SHA-256 为 `0b9e112751181aab82b43963f1b975adf4abbcdeec7c919ee93e496c0df8d46d`。

| 门禁 | 本次结果 | 证据 |
| --- | --- | --- |
| PyACL 导入与设备初始化 | `observed-pass` | `import acl`；CANN 8.0.0，driver 25.2.0 |
| OM 加载与推理 | `observed-pass` | 5 次 measured，2 次 warmup；execute mean 3.7784 ms |
| 端到端计时 | `observed-pass` | mean 4.8171 ms，p95 4.9282 ms |
| `msprof` 采集 | `observed-pass` | 3 次推理；timeline 已导出 |
| Tiny-ImageNet 准确率 | `untested` | 本任务没有带标签验证集报告 |

板端报告位于：

```text
/tmp/ascend310-validation-20261023/reports/chapter8-resnet18-om-smoke.json
/tmp/ascend310-validation-20261023/reports/chapter8-msprof-inner.json
/tmp/ascend310-validation-20261023/msprof/resnet18-smoke-20261023-171255/
```

两个 JSON 的 SHA-256：

```text
1927e230b56182bfbc981198521b0b6f274d7b811477cc410b44ee6485be15c4  chapter8-resnet18-om-smoke.json
2638acf3098fcd94a0088bf27b0952f490f9178551a2ba762ba3dbe24c517445  chapter8-msprof-inner.json
```

这些数字只属于当前 20T、CANN 8.0.0、driver 25.2.0 和上述 OM，不能外推到 8T 或其他系统组合。
