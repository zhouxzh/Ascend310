---
name: ascend310b-debug
description: 昇腾 310B（Ascend 310B1/310B4）程序调试工作流。当用户要调试 Ascend310 教材样例、案例或自写程序，需要确认开发板型号、CANN/驱动版本配套，定位 import acl、ATC 转换、OM 加载、DVPP/VENC、摄像头 V4L2、torch_npu/MindSpore 运行时、服务健康但推理失败、性能不达标等问题，或需要把症状映射到对应样例入口与只读板端命令时使用。触发后先 ascend_probe 采集真实板端事实，再 ascend_debug_plan 生成分层计划，按停止条件逐层定位；缺证据时写 untested，不写成不支持。
---

# 昇腾 310B 程序调试

这个工作流只做一件事：**把失败定位到具体的一层，并且只用板端真实读到的证据下结论。**

它只适用于**昇腾 310B1 / 310B4**。`ascend_probe` 的 `scope.applicable` 为 `false` 时，说明探测到的是其他芯片：不要套用本工作流的调试程序、样例索引或版本配套表，直接改用该芯片自己的工具链与文档。`scope.applicable` 为 `unknown` 时先补探测，不要猜。

开发新程序或整理样例时，同时遵守仓库中的 `tools/dsh-plugin-ascend310b-debug/docs/ascend310b-development-standard.md`。该规范覆盖工程目录、ACL/ATC 合同、资源生命周期、文档结构、证据等级和 M0-M8 验收门；调试工作流负责定位失败，不能替代开发规范中的交付条件。

## 三条硬规则

1. **版本必须来自板端。** 板卡型号、SoC、CANN、驱动和 npu-smi 版本一律由 `ascend_probe` 或 `ascend_check` 从板端读取。当前 310B 的非特权探测不采集固件版本，不能把驱动号当作固件号；任何"应该是 8.0"的推断都不能写进结论。
2. **证据分层标注。** 只能用 `documented`、`inferred`、`observed-pass`、`observed-fail`、`untested`。文档没写某个型号，是 `unknown`/`untested`，不是 `unsupported`。
3. **先只读，后显式执行。** `ascend_board_exec` 只执行白名单内的只读命令；下载、ATC、Python、pytest、msame 和教程脚本使用 `ascend_board_run`，并显式设置 `allowStateChange: true`。硬件复位、`npu-smi set/upgrade`、关机和破坏性操作仍由用户手工执行。
4. **离线目标要独立标记。** 已配置但未开机、未接入当前网段或主机名失效的板，返回 `offline/unreachable`；这说明网络/电源状态，不是 310B `unsupported`，也不能拿另一块在线板的结果替代。

## 标准顺序

1. `ascend_probe`（或 `ascend_check` 带 `rawText`）——先拿设备、版本、环境的事实。
2. 看返回的 `pairing.verdict`：
   - `observed-match`：这套组合仓库里记录过，可以直接进入最小门。
   - `documented-pairing` / `documented-unpaired`：按发布线判断，配套性仍需实际验证。
   - `undocumented-cann`：用昇腾版本配套查询助手核对，不要猜。
   - `incomplete`：先把缺失项采齐，不要继续往下判。
3. `ascend_debug_plan`——用症状或领域生成分层计划，从最早的门开始，按每步的停止条件推进。
4. `ascend_sample_index`——定位可参照的样例程序；先看样例目录的 README，再执行命令。
5. 需要探针没覆盖的信息时，用 `ascend_board_exec` 打一条只读命令；不确定就先 `run: false` 看等价调用。
6. 多板或 IP 会变化的实验室：用 `ascend_discover` 做局域网扫描；它按 MAC 建本地资产表，并记录驱动/CANN/系统重装变化。**同一 hostname 不等于同一块板**，多块出厂镜像可能都叫 `orangepiaipro`；不同 MAC 必须保留为不同 target。网络找不到板时，用 `ascend_serial_probe` 做串口发现：`serial.port: auto` 自动枚举 CH340/CH343 等 USB 串口，读取 IPv4/MAC 后再尝试 SSH 探测并写入设备列表。需要单条诊断时用 `ascend_serial_exec`。串口工具只使用 `HwHiAiUser`，不做 root 串口探测。
7. 案例发布复现：先从公开 GitHub 仓库在板端检出固定 commit（记录 commit 和干净工作树）；模型必须从 HF 固定 revision 下载，默认 `HF_ENDPOINT=https://hf-mirror.com`，再用 `ascend_model_download source=hf` 或板端 `hf download`。禁止从作者电脑用 `scp`/`rsync` 复制模型、ONNX、OM 或数据集后当作下载证据；完整门见 `ascend310b-case-testing`。
8. 下载单个教程资产：`ascend_model_download`。华为云 OBS 直接下载；HF 自动改写到 `hf-mirror.com`；GitHub 自动使用网页代理。先 `dryRun: true`，确认来源/目标后，再显式 `allowStateChange: true`。
9. 运行章节程序：`ascend_board_run`。先 `dryRun: true` 核对 cwd 和命令；默认 `environment: "ascend"` 会加载统一 ascend 环境、CANN，并禁用 user-site，再执行 `atc`、`python`、`pytest`、`msame` 或测试脚本，并保存退出码、耗时和输出。`base` 只用于显式系统诊断。
10. 前端页面可用后使用 `ascend_ui_capture` 保存 PNG；截图输出必须在工作区内，并记录 URL、viewport、字节数和耗时。截图是 UI 证据，不能替代 API/真实推理检查。

## 分层与常见落点

| 层 | 先看什么 | 典型症状 |
| --- | --- | --- |
| device | npu-smi、`/dev/davinci*`、lsmod、aplay/arecord、lsusb、V4L2 | 设备不存在、声卡/摄像头缺失、`err_ret=-512`、复位 |
| cann | `import acl`、`atc --version`、`version.cfg` | `No module named acl`、ATC 找不到算子 |
| conversion | ATC 日志、输入契约 | `EZ3002`、`EZ1001`、exit 137（OOM） |
| load | OM 加载、模型哈希、MindIR 版本 | 加载失败、exit 139、MindIR 版本高于 Lite 支持 |
| contract | 输入名/形状/数据类型 | 推理结果全错但不报错 |
| service | 启动脚本、`/api/health`、真实推理请求 | 端口通但推理失败 |
| performance | P50/P95、msprof、闭环时延 | 单次快但闭环慢 |

`Health: Alarm` 只是诊断字段，不能单独作为阻塞条件；要报的是具体失败。

## 报告纪律

- 结论必须带完整组合：SoC 档位（8T/20T）、系统日期、驱动、CANN/Toolkit/ops/NNAL、Python 与框架插件；当前 310B 默认探测不提供固件版本。
- 分开写四类证据：转换证据、数值一致性、任务精度、性能。数值烟测通过不等于精度达标。
- 8T 与 20T 的结果是两个证据集，不合并、不排名。
- 板端跑不出来的项目写清楚是"未在本板验证"，不要用本机结果替代。
- 把每一次失败限定在"被测试的组合"上（模型版本、加载器版本、CANN/驱动、SoC、输入、命令），不要升级成对整个硬件或框架的判断。
