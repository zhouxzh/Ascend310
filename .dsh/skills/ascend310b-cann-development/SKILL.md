---
name: ascend310b-cann-development
description: 面向 Ascend 310B1/310B4（8T/20T）的 CANN 程序开发工作流。用于新写或改造 PyACL/ACL、ATC/OM、DVPP/VENC、摄像头 V4L2、torch_npu、MindSpore/MindIR/MindSpore Lite 程序，以及需要设计 310B 工程目录、接口契约、板端验证和性能/精度证据时。案例发布复现必须从 GitHub 固定 commit 获取源码、从 Hugging Face 固定 revision 获取模型，不能把作者本地模型复制到板端。不得把 Ascend 910/950 的工作流、编译参数或性能结论直接套到 310B；先用 ascend_probe 确认真实板和软件组合，再按本 skill 的分层门推进。
---

# 昇腾 310B CANN 程序开发

这个 skill 负责**从程序结构到板端证据**的 310B 开发流程。它不替代调试 skill：开发时用它建立工程和验收门，失败后再用 `ascend310b-debug` 定位失败层。案例发布测试先经过 `ascend310b-case-testing` 的 GitHub commit / Hugging Face revision 来源门，不能把作者本地模型复制到板端后当作复现结果。

## 1. 范围和硬件身份

只适用于：

- Ascend 310B1 / 20T
- Ascend 310B4 / 8T

不适用于 Ascend 310P、Ascend 910、Ascend 950 或其他 NPU。`310B` 不是 `310P`，也不是云端 910 的缩小版。

8T 与 20T 是两个独立证据集：不能合并结果、不能互相替代性能数字，也不能用一个档位的算子行为推导另一个档位。板卡型号、SoC 和档位始终以 `ascend_probe` 的板端输出为准，不以商品名、旧文档或 IP 为准。

## 2. 开发前的强制预检

第一步使用 `ascend_probe`（或对已有原始输出使用 `ascend_check`），确认：

| 事实 | 用途 |
| --- | --- |
| `scope.applicable` | 只有 `true` 才继续 310B 流程；`false` 改用目标芯片自己的规范 |
| `board.soc` / `board.tier` | 确认 310B1-20T 或 310B4-8T，建立独立证据集 |
| `cannVersion` | 记录 Toolkit 实际运行版本，不凭文件名猜测 |
| `driverVersion` | 与 CANN、内核、设备节点一起判断环境 |
| `aclImport` / `aclImportSourced` | 区分裸 SSH 与 source CANN 后的结果 |
| `atcOnPath` / `setEnvPresent` | 判断转换链是否可达 |
| `deviceNodes` / `kernelModules` | 先确认设备层存在，再写业务代码 |
| `pythonExecutable` / `pythonVersion` | 记录系统解释器和包来源 |
| `ascendEnvPresent` / `ascendEnvVersion` / `ascendEnvAbi` | 统一案例环境必须存在，Python 固定为 `3.9.25` / `cpython-39` |
| `ascendEnvIsolated` | 必须禁用 user-site，案例不得依赖 `~/.local` |
| `ascendEnvAclImport` | `ascend` 环境 source CANN 后必须可导入 PyACL |
| `ascendEnvAclruntime` | `aclruntime` 是 case5 等 OM 推理路径的附加环境门 |
| `ascendEnvMindsporeContext` | 必须实际设置 `device_target=Ascend`；仅有 MindSpore 包不等于与 CANN ABI 匹配 |
| `ascendEnvPackages` | 统一环境必须覆盖所有案例依赖，不用 base/user-site 补洞 |
| `ipAddress` / `hostname` / MAC | 作为当前网络信息，不作为硬件身份 |

当前 310B 默认探测**不采集固件版本**：板端没有为非特权 SSH 用户提供可信、可读的版本来源。不要把驱动号伪装成固件号，也不要把“未采集”写成“版本不匹配”。固件只有在用户提供独立外部证据时才记录。

如果 `scope.applicable` 为 `unknown`，先补探针，不写兼容性结论；如果是其他 SoC，停止本 skill，不要复制它的 ATC 参数、tiling 或 perf 结论。

## 3. 工程开发分层

按顺序推进，前面的门失败时不要用后面的任务“覆盖”它。

### M0：环境门

- `npu-smi` 能看到正确设备。
- `source /usr/local/Ascend/ascend-toolkit/set_env.sh` 后 `import acl` 成功。
- 所有案例使用统一 conda 环境 `ascend`，Python 固定为 `3.9.25`，不退回 `base`。
- `ascend` 进程设置 `PYTHONNOUSERSITE=1`，不得依赖 `~/.local`。
- `ascend` 内 source CANN 后 `import acl` 成功，需要 `aclruntime` 的案例也能导入它。
- MindSpore 案例必须实际设置 `device_target=Ascend`；只通过 `find_spec` 不能证明 MindSpore/CANN ABI 兼容。
- `atc` 在 PATH，版本与当前 CANN 同一条发布线。
- `/dev/davinci*` 和驱动模块存在。
- 需要音频/摄像头时，已读取并核对声卡、aplay/arecord、USB 和 V4L2 列表。
- Python、conda 与用户级 site-packages 来源已记录。

停止条件：设备不存在、CANN 变量缺失、统一环境缺失/回退到 base/user-site，或 `import acl` 失败时，先修环境；不要用 CPU 或云端推理掩盖 NPU 门失败。

### M0.5：网络不可见时的串口入口

如果 SSH 和 `ascend_discover` 找不到开发板，但串口线已连接：

- 使用 `ascend_serial_probe` 登录串口，读取主机名、接口、IPv4 和默认路由；
- 使用 `ascend_serial_exec` 执行单条只读诊断，例如 `hostname -I`；
- 串口凭据只从 profile 的 `serial` 配置读取，不写入仓库；
- 取得 IP 后，再执行 `ascend_probe target=<用户>@<IPv4>` 进入正常 SSH 流程。

串口工具不做网络配置修改；它只解决“板已开机但没有已知 IP”的恢复路径。

### M0.7：源码和模型获取门

发布/案例测试不能用作者电脑上的本地副本冒充可复现资产。先执行 `ascend310b-case-testing` 或按它的来源合同完成：

- **程序**：从公开 GitHub 仓库在板端 `git clone`/`git fetch`，检出完整 commit SHA；记录仓库 URL、commit 和干净的 `git status --short`。禁止把本地未提交源码用 `scp`/`rsync` 复制后当作公开来源。
- **模型**：从 Hugging Face 仓库按固定 revision 下载，默认使用 `HF_ENDPOINT=https://hf-mirror.com`；记录 repo、revision、文件清单、字节数和 SHA-256。优先使用 `ascend_model_download source=hf`，或板端 `hf download`。禁止从本地模型目录复制 `.safetensors`、ONNX、OM、tokenizer 或数据集来跳过下载。
- **离线例外**：只有用户明确要求离线包且带完整来源/哈希时才能使用；报告标为 `offline-bundle`，不能宣称公开下载成功，也不能作为默认发布路径。
- **失败处理**：GitHub/HF 不可达、commit/revision 不固定、哈希不符或工作树不干净时，测试标为 `blocked` 或 `untested`，不要改用作者本地缓存继续并写成 observed-pass。

### M1：工程门

- 固定 Python/C++ 入口、配置、模型、输入和输出目录。
- 外部模型、OM、数据集和运行报告与源码分离，并从既定 GitHub/HF 来源重新获取。
- 明确 8T 或 20T，写入 README 和运行参数。
- 不把板卡 IP、SSH 凭据、本地模型绝对路径或本机 checkout 路径写入源码。
- 生成物（ONNX、OM、日志、截图）有明确目录和清理策略；OM 由板端从已校验的源模型生成，不把本地 OM 当作发布输入。

### M2：契约门

先把输入输出合同写清楚，再写算子或模型调用：

- 输入名、shape、layout、dtype、动态维度和取值范围。
- 输出名、shape、dtype、后处理规则。
- 预处理/后处理是否在 NPU、CPU 或 DVPP 上执行。
- 图像格式、颜色空间、归一化方式和摄像头分辨率。
- 多输入模型不能强行套单输入模板。

没有合同就没有“推理正确”结论，只能算 API 跑通。

### M3：实现门

- PyACL：明确 device、context、stream、内存分配、同步和释放。
- ATC：固定模型哈希、CANN 版本、SoC 版本、输入 shape、精度模式和完整命令。
- DVPP/VENC：明确输入格式、对齐、帧率、通道和 buffer 生命周期。
- 摄像头：先枚举实际设备，再选 MIPI/USB 路径，不硬编码 `/dev/videoN`。
- 服务：启动进程必须完成 CANN source，健康检查不能只看 HTTP 200。

### M4：加载门

用**最小真实 OM/MindIR**加载一次，记录：

- 模型字节数和 SHA-256。
- 实际加载设备、context/stream 和错误码。
- 输入/输出描述是否与 M2 合同一致。
- 是否有算子、dtype 或动态 shape 在加载阶段才暴露的问题。

### M5：数值门

用一组固定、可重复的输入比较 golden 与板端输出：

- 记录 golden 的生成方式、输入文件和随机种子（如适用）。
- 记录 dtype、容差、最大误差和失败样本。
- 不把“进程 exit 0”当作数值正确。
- 不把一次样例通过当作模型精度或业务指标通过。

### M6：任务门

按程序类型追加：

- 分类/检测/跟踪：数据集、预处理、阈值、mAP/准确率、失败样本。
- 摄像头/视频：实际分辨率、格式、帧率、首帧延迟、丢帧和释放设备。
- 服务：启动、健康、真实推理请求、错误响应和资源回收。
- 前端：HTTP 可访问不等于推理可用；需要 UI 截图时使用 `ascend_ui_capture`。

### M7：性能门

记录可复现的：

```text
模型/哈希、8T 或 20T、CANN/驱动、输入 shape/dtype、
warmup、loops、repeats、线程数、计时边界、P50/P95、报告路径
```

不能把 910 的吞吐、Cube 参数或理论峰值迁移到 310B；也不能用一次单帧时延代表服务闭环时延。

## 4. 不同开发路径的关键约束

### PyACL / ACL 程序

1. 先 `aclInit`，再 device/context/stream；每一步记录返回码。
2. 所有输入输出 buffer 有明确字节数、对齐和生命周期。
3. 异步推理后必须同步或正确等待 stream，不能读取未完成结果。
4. 释放顺序与创建顺序相反，服务退出时回收 device/context。
5. 裸 shell 能 import 不等于服务进程能找到 CANN；检查启动脚本的 `source`。

### ATC 与 OM

- ATC 在板端执行，输入模型哈希和 `--soc_version` 必须与目标档位一致。
- 不复制 910/950 的 soc、tiling、fp8/bf16 或高性能选项。
- 保存完整 ATC 日志；失败时先判断 conversion，不要直接判设备不支持。
- OM 生成成功后仍需 ACL 加载、数值和任务门。
- 输入 shape 变化要有独立转换和验证记录，不假设动态 shape 自动工作。

### PyTorch / torch_npu

- 先记录 `pythonExecutable`、`torch`、`torch_npu` 来源和版本。
- 用一个最小 NPU tensor 操作确认 device/context，再加载模型。
- 不因一个自定义算子失败就判定整个模型或硬件不支持。
- CPU fallback 只能用于离线对照，不能静默成为生产路径。

### MindSpore / MindIR / MindSpore Lite

- 分开记录 MindSpore 与 MindSpore Lite 版本；它们是不同运行时。
- 在全新进程检查导入顺序和符号冲突。
- MindIR 版本、权重格式、tokenizer/后处理都要与运行时匹配。
- 运行失败时先判断框架/加载器层，再判断算子或硬件层。

### DVPP、VENC 和摄像头

- 每次先在目标板枚举 `/dev/video*` 和 V4L2 格式，不把其他板的设备号写死。
- MIPI/CSI 与 USB 摄像头是不同路径；设备能打开不代表能读到有效帧。
- 记录实际分辨率、像素格式、帧率、buffer 数、首帧和释放结果。
- 当前仓库实测中，8T 的 MIPI `/dev/video0` 可打开但读取失败，USB `/dev/video2` 可出帧；20T 的 USB C922 `/dev/video0` 可出帧。设备号会变化，以上只是探测线索，不是固定配置。
- 视频路径失败时，先区分设备、格式、DVPP、VENC、网络/显示和业务逻辑，不把所有视频问题归为 NPU。

### HTTP 服务

- 健康接口通过不代表模型已加载或 NPU 真正执行。
- 检查服务启动进程是否 source CANN，是否加载正确 Python 环境。
- 保存进程命令、PID、端口、启动日志和一次真实推理响应。
- 摄像头和服务需要串行占用设备，避免多个请求同时打开同一 V4L2 节点。

## 5. 310B 常见误区

- **直接把 910 的 ATC 参数或性能模型搬来。** 310B 是边缘 SoC，设备、内存、算力和软件栈都不同。
- **把 310B1 与 310B4 合并。** 8T 和 20T 必须分开记录，不能互相替代。
- **把 `Health: Alarm` 单独当作不可用。** 记录健康状态，但阻塞判断必须基于真实失败、错误码或设备缺失，不能只凭 Alarm。
- **把驱动版本当固件版本。** 当前 310B 默认探测不提供可信固件号。
- **把 HTTP 200 当作推理成功。** 真实输入、输出和数值门必须单独通过。
- **把 `/dev/videoN` 当作稳定身份。** 摄像头节点会随 USB/MIPI 顺序变化，必须现场枚举。
- **把未测组合写成 unsupported。** 缺证据写 `untested` 或 `blocked`；只有明确的不兼容或可复现失败才写 `unsupported`。

## 6. 失败路由与停止条件

| 层 | 先查 | 停止条件 |
| --- | --- | --- |
| device | `npu-smi`、设备节点、驱动模块 | 设备不可见或驱动未加载时不继续 |
| cann | `source` 后的 `import acl`、Python 来源、`set_env.sh` | 同一进程无法 import 时先修环境 |
| conversion | ATC 命令、模型哈希、输入合同、日志 | 转换失败时保留日志，不混淆为加载失败 |
| load | OM/MindIR 路径、版本、dtype、shape | 加载失败时先查模型/运行时合同 |
| contract | 输入名、layout、预处理、输出解码 | 合同不一致时停止数值判断 |
| numerical | golden、容差、固定输入 | 未比较前不能写 observed-pass |
| camera | V4L2 枚举、格式、分辨率、buffer | 设备能开但读帧失败时继续查格式/路径 |
| service | 启动命令、环境、真实请求 | HTTP 正常但推理失败时查进程环境 |
| performance | 模型、8T/20T、warmup、重复次数 | 没有协议和原始报告时不能报性能结论 |

## 7. 使用插件和仓库样例

- `ascend_probe` / `ascend_check`：获取板端事实。
- `ascend_debug_plan`：失败时生成分层计划和停止条件。
- `ascend_sample_index`：先查现有章节/案例，优先复用契约和入口。
- `ascend_board_exec`：只读诊断命令，先 `run: false` 核对。
- `ascend_board_run`：下载、ATC、Python、pytest、msame 或脚本，必须显式 `allowStateChange: true`。
- `ascend_model_download`：先 `dryRun: true`，再显式允许写入。
- `ascend_ui_capture`：有真实前端时保存 UI 证据，不能代替推理证据。

仓库中的 `docs/ascend310b-development-standard.md` 是工程目录、代码合同、文档和 M0-M8 验收门的规范真源。`samples/` 是运行入口；先读对应 `README.md`，不要从旧文档复制一个与当前模型不匹配的命令。

## 8. 外部 CANNBot Skills 的使用边界

`https://gitcode.com/cann/cannbot-skills` 是通用 CANN Skills 仓库，不是 310B 专用。它的 `npu-arch` 可以作为架构映射参考，`ascendc-env-check`、`ascendc-runtime-debug`、`ascendc-crash-debug`、`ascendc-precision-debug`、`ascendc-sync-audit` 等可以提供通用检查思路，但：

- 先核对它的平台声明、CANN 版本和适用 SoC。
- 不要把 `A2`/`A3`/`A5` 的 ATC、tiling、perf 或评测结论移植到 310B。
- 不要默认使用明确只支持 Ascend 950 的 `npu-check` 或 regbase/blaze 技能。
- 不要复制整个外部仓库；需要借用规则时记录来源、版本、许可证和板端验证结果。
- 任何外部 skill 的结论都必须回到本板重新验证。

## 9. 交付证据模板

```text
目标：Ascend 310B1/310B4（8T/20T）
板端事实：hostname、MAC、IP（仅当前网络）
软件：CANN、driver、Python、框架/插件、ATC
模型：路径、字节数、SHA-256
合同：输入/输出 name、shape、layout、dtype、预处理
环境：source CANN、import acl、设备节点
转换：ATC 命令、日志、结果、OM SHA-256
加载：OM/MindIR 路径、错误码
数值：golden、输入、容差、误差
任务：数据集/指标或服务请求
性能：warmup、loops、repeats、P50/P95、原始报告
结论：observed-pass / observed-fail / untested / blocked
```

只有当每一层证据都存在且可复现时，才能写“310B 程序通过”。缺少证据时明确写“未在本板验证”，不要用 910/950 的结果替代。

## 10. 维护

- 修改本 skill 后，在仓库根目录运行 `npm run skills:sync` 和 `npm run skills:check`。
- 改动插件源码后运行 `npm test`，并在真实 310B 板上重跑对应探针和最小门。
- 如果板卡、CANN、驱动或样例发生变化，更新事实和日期；不要把旧版本结论静默沿用。
