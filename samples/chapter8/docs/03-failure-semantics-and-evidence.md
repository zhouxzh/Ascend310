# 失败语义、测试缺口与证据边界

## 本地与板端边界

| 能力 | 本地允许 | Ascend 310B 必测 |
|---|---|---|
| Python 语法检查 | 是 | 可选 |
| NumPy/CPU 预处理基准 | 是 | 可用于板端 CPU 对照 |
| `--simulate` Queue Pipeline | 是 | 可选 |
| OM 加载与 `acl.mdl.execute` | 否 | 是 |
| DVPP VPC/AIPP | 否 | 是 |
| ATC 转换 | 否 | 是 |
| `msprof`、`npu-smi` | 否 | 是 |

本地缺少 `acl`、`cv2` 或 profiler 不能判定代码失败。反过来，本地 CPU 或模拟脚本通过也不能证明板端路径通过。

## 可观察的失败行为

### 模型或脚本缺失

`download_model.py` 对每类文件分别记录失败；只要选定文件有失败，退出码为 2。查询仓库列表失败时仍会尝试直接下载，因此“文件已写出”还需要结合远端状态和模型哈希判断。

`06_aipp_preprocess_compare.py` 在普通 OM 或 AIPP OM 缺失时给出明确补救命令并以 1 退出。其他 runner 在 OM 缺失时抛出 `FileNotFoundError`，不会创建替代模型。

### ACL 调用失败

`check_ret()` 检查 ACL 返回值并尽量附加最近错误。`AclSession.__enter__()` 允许平台已初始化的 `100002`，其他非零值中止。异常退出时 context/device 的清理代码可能因底层状态异常而再次失败；应分别记录首个错误和清理错误。

### 输入合同错误

`BaseResNetRunner` 在输入字节数与 OM 不一致时抛 `ValueError`。它不检查 shape/dtype，因此发布前应有独立的模型描述检查。AIPP OM 使用 `uint8` 输入；普通 OM 使用 `float32`，把两者混用不能得到有效对照。

### 对照路径部分失败

VENC 之外的本章基准没有把所有分辨率都设计成逐档容错。`04`、`05` 或 `06` 的底层 API 异常通常会终止进程；只有顶层 `06` 明确捕获 `FileNotFoundError`、`RuntimeError`、`ValueError` 并转成退出码 1。异常的 Python 导入错误仍可能直接显示 traceback。

`msprof` 包装器在命令缺失、没有应用命令或 profiler 失败时由 `set -euo pipefail` 终止。采集失败不能生成“性能通过”结论。

## 自动化测试现状

`samples/chapter8` 当前没有 `test/` 目录、pytest 用例或 CI 专用板端 harness。可执行验证只有：

```bash
python -m py_compile *.py tools/*.py
python 03_cpu_preprocess_benchmark.py --runs 10
python 05_pipeline_queue_demo.py --simulate --frames 30
```

以及 README 中列出的板端命令。缺少自动测试意味着至少需要人工核对：

- 报告 JSON 可解析；
- 每个变体 count 等于请求的运行/帧数；
- 缺失模块返回明确非零退出；
- AIPP 与普通 OM 输出类数一致；
- profiler 目录有对应命令和完成标记；
- 模型、输入、设备和报告来自同一轮环境。

本次文档任务没有运行任何上述 AI 板端命令，因此相关项为 `untested`。

## 既有报告的事实

`outputs/` 中保留多组历史 JSON，部分名称含 `smoke` 或计时规模标签。结构化解析显示它们通常是合法 JSON，且包含 host、platform、Python、generated_at、command、warmup/runs 等字段。

同时存在两个重要来源问题：

- 多数 `command` 仍自报为 `samples/chapter7/...`，不是当前 `samples/chapter8/...`；
- 部分报告的模型路径指向旧的 `samples/chapter4/...` 或 `samples/chapter7/...`。

这说明报告可能来自目录重命名或迁移前的工作树。它们可以作为历史实现线索，但不能未经重跑就改名为当前 chapter8 的板端结果。

报告还缺少 SoC 计算档位、CANN、driver、firmware 和 Git revision，无法独立重建完整验收组合。

## 证据分级

| 证据 | 等级与用途 |
|---|---|
| 源码和命令路径存在 | `documented`，证明入口存在 |
| `py_compile`、CPU 预处理、模拟 queue | 可单独记录为本地 `observed-pass`，不能外推到 NPU |
| 既有 JSON 与 `msprof` 目录 | 历史 `documented`/待复核证据，不是本轮 `observed-pass` |
| 2026-10-23 的 20T ResNet18 ACL/OM 推理 | `observed-pass`；5 次 measured，execute mean 3.7784 ms |
| 2026-10-23 的 20T `msprof` 采集 | `observed-pass`；timeline 已导出 |
| Tiny-ImageNet 准确率 | `untested`；未运行带标签完整验证集 |
| 8T 或其他 CANN/driver 组合 | `untested`；不能继承本次 20T 数字 |
| 缺少 CANN 或模型导致的终止 | `blocked` 或 `observed-fail`，范围限于该组合，不能写成 Ascend 310B 不支持 |

## 发布前最低证据包

一次可引用的板端性能结论应同时保留：

- Git commit、模型来源 revision、OM 字节数和 SHA-256；
- Ascend 310B4/8T 或 20T 的明确计算档位，以及 CANN/driver/firmware；
- 精确命令、输入来源、预热数、计时数、重复数和统计方法；
- 原始 JSON、终端日志和必要的 profiler 目录；
- 失败或跳过的档位；
- 是对照 CPU、比较 DVPP/CANN，还是完整应用端到端。
