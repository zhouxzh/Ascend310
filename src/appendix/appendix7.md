---
title: "附录 7：CANN 升级与版本配套"
author: [周贤中]
subject: "CANN、驱动与固件的版本管理"
keywords: [CANN, Ascend HDK, PyTorch, 驱动, 固件, 版本配套, 升级]
lang: zh-cn
---

# 附录 7：CANN 升级与版本配套

## 本附录解决的问题

昇腾软件环境经常被简单地称为“CANN 版本”。这种说法对于初次安装可以帮助理解，但不足以支撑一次可复现的升级。一个能够运行的 310B 环境至少包含操作系统与内核、NPU 驱动、NPU 固件、CANN 用户态软件、Python 运行时以及可能存在的框架适配层。它们由不同安装包提供，也有不同的生命周期。只替换其中一个组件，可能使 `npu-smi` 仍能显示设备，却让 ATC、PyACL 或已有 OM 模型在运行时失败。

本附录以昇腾官方版本配套查询助手的 310B 系列查询结果为当前版本配套参考，并以 CANN 9.0.0 和用户提供的 CANN 9.0.1 补丁说明补充历史发布线的升级信息，建立一套面向昇腾 310B 的版本审查和升级方法。文中沿用本书的证据状态：`documented` 表示厂商版本说明或安装指南已明确给出；`inferred` 表示由配套关系推得、尚未在目标板验证；`observed-pass` 和 `observed-fail` 分别表示在记录的硬件、版本和输入条件下实测通过或失败；`untested` 表示尚未执行。命令示例属于项目操作建议，不等于厂商对所有设备的兼容性承诺。

本附录不是“看到新版本就升级”的建议。若现有案例已经通过了模型转换、ACL 烟测和长稳测试，升级应被视为一次新的变更实验；只有在回归证据完整后，才可以把新环境写入案例的默认配置。

## 软件栈中的版本层次

### 驱动、固件、CANN 和框架适配层

| 层次 | 主要职责 | 常见查询或目录 | 升级时的判断 |
| --- | --- | --- | --- |
| 操作系统与内核 | 提供用户态、内核模块、设备节点和系统调用 | 系统信息与发行版文件 | 内核、发行版、架构必须在硬件和驱动支持范围内 |
| NPU 驱动 | 让操作系统和用户进程访问 NPU，管理设备、内存和运行时接口 | `npu-smi` 与驱动目录 | 驱动包必须匹配目标 SoC、内核和固件；它不是 CANN Toolkit |
| NPU 固件 | 运行在设备侧的底层固件，负责设备启动及硬件协同 | 固件目录与厂商升级工具 | 覆盖升级时通常先固件后驱动；升级过程中不能断电或复位 |
| CANN | 用户态编译器、Runtime、AscendCL、PyACL、算子和工具链 | `set_env.sh`、`atc` 与 Toolkit 目录 | 必须满足 CANN 与 Ascend HDK 的配套关系 |
| 框架适配层 | 例如 `torch_npu`、MindSpore 插件和第三方库 | Python 导入检查 | 需单独核对框架、Python、CANN 和驱动的兼容矩阵 |

这里的“固件版本”和“驱动版本”不是同义词。固件主要在设备侧运行，驱动主要在主机操作系统中运行；二者常由同一套 Ascend HDK 下载页面提供，但安装包、升级命令和生效方式不同。版本说明中的 **Ascend HDK** 是厂商用于表达硬件开发套件配套关系的版本标识，不能把它直接当作板上某一个可执行文件的版本号。实际安装时仍应以目标硬件页面列出的驱动包和固件包全名为准。

CANN 也不是一个单一二进制。CANN 9.0.0 的发布说明把组合包分为 Toolkit、算子包（ops）和加速库（NNAL）；Toolkit 中还包含 Runtime、AscendCL 扩展、编译器和 PyACL 等子包。因而“升级 CANN”可能指升级完整 Toolkit，也可能只升级一个允许独立升级的子包。两种操作的风险和验收范围不同。后续命令块给出默认目录的检查方式；表中使用目录类别，是为了避免把默认路径误读为唯一安装路径。

### 310B 的产品边界

本仓库的主要目标板是 Ascend 310B4 / 8T。CANN 9.0.0 发布说明中出现的 Ascend 950PR、Atlas A2 或 Atlas A3 特性，首先证明这些特性在相应产品族上被发布；它们不自动证明 310B 支持。对 310B 的结论必须同时满足：

1. 目标硬件的配套页面列出该驱动、固件和 CANN 组合；
2. 当前操作系统、架构和 Python 环境满足安装要求；
3. 代表性模型或 API 在板端通过了运行时和任务级验证。

如果只满足前两项，结论应写成“配套关系已记录，板端功能未验证”，而不是“已支持”。

## 310B 官方版本配套查询快照

证据状态：`documented`。下表来自昇腾[版本配套查询助手](https://www.hiascend.com/developer/download/compatibility)，于 2026-10-03 查询时选择“310B 系列产品”得到的版本配套结果。发布日期由新到旧排列；2026/06/30 一列在官网标记为“推荐”。

| 产品 | 2026/06/30（推荐） | 2026/03/30 | 2025/12/30 | 2025/09/30 | 2025/06/30 | 2025/03/30 |
| --- | --- | --- | --- | --- | --- | --- |
| PyTorch（第三方） | 26.1.1 | 26.0.0 | 7.3.0 | 7.2.0 | 7.1.0 | 7.0.0 |
| CANN | 9.1.1 | 9.0.1 | 8.5.0 | 8.3.RC1 | 8.2.RC1 | 8.1.RC1 |
| Ascend HDK | 26.1.1（A3/A2/910/310P/310B 系列）；25.7.RC1.10（350 加速卡）；25.6.RC1（850E 超节点/650E 服务器）；25.1.RC1（950 超节点） | 26.0.RC1 | 25.5.1 | 25.3.RC1 | 25.2.0 | 25.0.RC1 |

官网将 `N/A` 定义为“该产品不支持或无此系列版本”；本次 310B 查询结果中的上述三行没有 `N/A`。表内“PyTorch（第三方）”沿用官网产品项的名称和版本号，不能直接把它等同于本地某个 `torch` 或 `torch_npu` Python 包已经安装或已经可用。

Ascend HDK 的最新列同时显示多个带产品范围的版本。只有 `26.1.1` 的括号范围明确包含 310B；其余条目分别标注 350 加速卡、850E/650E 或 950。因此，不能仅因它们同处一列就把它们记为 310B 的直接配套证据。实际选包时仍应以目标 310B 型号、操作系统、CPU 架构和下载页面给出的驱动、固件、Toolkit 与 ops 包为准。

这是一份版本选择快照，不是板端验收结果。将 CANN 9.1.1 或任何表中组合用于本仓库前，仍需在目标板完成设备初始化、`import acl`、代表性模型的 ACL 烟测和任务级回归；在这些检查前状态应保持为 `untested` 或 `inferred`。

## 历史版本说明中的 CANN 9.0.0 与 9.0.1 配套信息

### CANN 与 Ascend HDK

下面的关系来自 CANN 9.0.0 官方版本说明以及用户提供的 9.0.1 补丁说明，用于理解历史发布线。当前版本选择优先使用上一节的官方查询结果；本表不替代下载页面针对具体 310B 产品的筛选结果。

| CANN | 版本说明中列出的 Ascend HDK |
| --- | --- |
| CANN 9.0.1 | 25.7.RC1、26.0.RC3、26.0.RC1、25.5.X、25.3.X、25.2.X、25.0.X |
| CANN 9.0.0 | 25.7.RC1、26.0.RC1、25.5.X、25.3.X、25.2.X、25.0.X |
| CANN 8.5.2 | 26.0.RC1、25.5.X、25.3.X、25.2.X、25.0.X |

表中的 HDK 是版本说明使用的配套标识，不是一个可以代替驱动包或固件包名称的独立版本号。下载中心会根据产品、操作系统和 CPU 架构列出具体的驱动与固件安装包；正式记录应同时保存包名、实际版本和摘要。“列在表中”只表示版本说明声明了配套关系；仍需确认该关系是否覆盖目标板的具体 SoC、操作系统和架构。特别是 9.0.1 新增的 HDK 26.0.RC3 不能被倒推为当前板端已经具备该驱动或固件。

### 组合包与独立子包

CANN 9.0.0/9.0.1 的组合包可按下表理解：

| 组合包 | 代表内容 | 独立升级边界 |
| --- | --- | --- |
| Toolkit | Runtime、AscendCL 扩展、编译器、工具、PyACL 等 | 先确认基础 Toolkit 已安装；子包路径必须与组合包一致 |
| ops | DVPP、通用算子、数学算子、Transformer 算子等 | 版本说明列出的若干 ops 子包支持独立升级 |
| NNAL | `atb`、`sip` 等加速库 | 仅在业务确实使用时纳入变更范围 |

用户提供的 9.0.1 说明还列出了 ops 与 Toolkit 的交叉配套：9.0.1 ops 可与 9.0.1、9.0.0 和 8.5.2 Toolkit 组合，9.0.0 ops 也列出了与 9.0.1、9.0.0 和 8.5.2 Toolkit 的组合。该表不能替代完整兼容性检查：框架插件、模型算子、驱动固件和操作系统仍需单独验证。对于本仓库的案例，优先采用同一发布线的 Toolkit 与 ops，只有在有明确需求和回归报告时才使用跨版本组合。

### 版本特性与 310B 结论的关系

CANN 9.0.0 说明包含低比特量化、确定性计算、通信合并、SIMD/SIMT、Runtime 查询接口等大量变化，也列出了若干废弃接口和已知问题。这些条目适合用来制定迁移检查表，但不能直接写成“本项目已获得性能提升”。本仓库不把发布说明中的理论加速倍数、算子覆盖或修复列表当作案例测量结果。

升级前至少搜索代码和配置中是否使用了下列废弃方向：

* `aclnnGroupedMatmul` 的旧版本接口，迁移目标为版本说明列出的 `aclnnGroupedMatmulV5`；
* `aclnnPromptFlashAttention`、`aclnnIncreFlashAttention` 等旧版本接口；
* 模型压缩工具中被标记为废弃的非均匀量化、自动混合精度、近似校准、INT4 量化感知训练和 `amct_mindspore` 特性。

这只是静态迁移筛查。每一项都还要在目标板上完成编译、加载、数值一致性和任务级回归。

## 升级前的准备与门禁

### 固定目标与维护窗口

升级前写下以下信息，并把它们与本次报告绑定：

| 项目 | 需要记录的内容 |
| --- | --- |
| 设备 | 产品型号、SoC、算力等级、序列号或经脱敏的设备标识 |
| 系统 | 发行版、版本、内核、`aarch64` 或 `x86_64` |
| 软件 | 驱动、固件、CANN Toolkit、ops/NNAL、Python、框架插件 |
| 业务 | 正在运行的服务、端口、模型和数据目录 |
| 证据 | 当前健康检查、代表性 ACL 烟测、性能基线和回滚包位置 |

升级会造成业务中断。应先停止案例服务和自动启动单元，通知使用者，确保串口或本地控制台可用，并确认设备有稳定供电。安装或升级过程中不要对主机或设备执行断电、复位、热插拔或并行维护操作。

### 采集当前版本

以下命令只读，不会修改系统；应在板端目标环境中执行：

~~~bash
uname -a
uname -m
cat /etc/os-release
hostname
date --iso-8601=seconds
npu-smi info
command -v atc || true
python --version
python -c 'import sys; print(sys.executable)'
python -c 'import acl; print("PyACL import: ok")'
readlink -f /usr/local/Ascend/ascend-toolkit/latest 2>/dev/null || true
readlink -f /usr/local/Ascend/driver 2>/dev/null || true
readlink -f /usr/local/Ascend/firmware 2>/dev/null || true
df -h /usr/local/Ascend
~~~

`npu-smi info` 能显示设备并不等于 CANN、PyACL 和已有模型全部可用；它只构成设备层检查。`import acl`、`atc --version`、模型加载和任务输出应分别记录。若 `import acl` 来自用户目录或另一个 Conda 环境，必须把解释器路径和 `sys.path` 一并保存，避免把环境混用误判为版本问题。

### 保存可回退的材料

至少保存：当前版本输出、安装包文件名和摘要、CANN 安装配置、服务启动参数、模型清单、环境变量、测试输入、原始日志和回滚顺序。模型、用户数据和个人信息不要复制到公开报告。

~~~bash
report_root="$HOME/upgrade-reports/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$report_root"
npu-smi info > "$report_root/npu-smi-before.txt"
uname -a > "$report_root/uname-before.txt"
python -m pip list > "$report_root/pip-before.txt"
env | sort | sed -E 's/(TOKEN|KEY|PASSWORD|SECRET)=.*/\1=[redacted]/I' \
  > "$report_root/env-before.txt"
~~~

如果需要保存 `/usr/local/Ascend` 下的配置，只复制经审查的文本配置，不要把整个安装目录打包进 Git。报告目录应位于隔离位置，并明确其保留期限。

### 检查安装权限、空间和架构

驱动和固件通常需要 root 权限；CANN `.run` 包可以按安装指南由安装用户执行。安装用户、运行用户和驱动固件用户组的关系必须符合目标版本指南。升级前检查：

~~~bash
id
groups
df -h /usr/local/Ascend /tmp
du -sh /usr/local/Ascend/ascend-toolkit \
       /usr/local/Ascend/driver \
       /usr/local/Ascend/firmware 2>/dev/null || true
mount | sort
~~~

空间要求以当前下载页面和安装指南为准，并为解包、日志和回滚包预留余量。

### 核对安装包而不是文件名

下载页同时提供不同产品和架构的包；文件名相似不代表可以互换。先记录所选安装包的本地摘要，再以厂商发布的对应摘要清单和签名材料完成验证：

~~~bash
package_dir="$HOME/packages/cann-upgrade"; cd "$package_dir" || exit 1
sha256sum ./selected-*.run > package-sha256.txt || exit 1
~~~

将 `selected-*.run` 替换为实际选择的文件名；`.deb` 或 `.rpm` 包应按其包管理器和版本指南核验。若厂商提供的 `SHA256SUMS` 包含这些精确文件，应提取对应条目形成专用校验清单，再执行 `sha256sum -c hashlist`；摘要不匹配或文件缺失即停止。签名校验必须使用该版本发布说明指定的材料和工具，不能把“下载页提供签名”写成“签名已验证”。必要时再用 `file -- *.run` 核对安装包类型。不要使用未核验的镜像链接、旧板卡包或其他 SoC 的包。若下载页没有明确列出目标 310B 的配套关系，状态应记为 `unknown`，先做小范围兼容性核对，不得直接覆盖生产环境。

## 首次安装、覆盖升级和 CANN 升级

### 首次安装：驱动后固件

首次安装是指设备尚未安装驱动和固件，或两者都已卸载。官方安装指南的顺序是先驱动、后固件；安装完成后按提示重启，再安装 CANN。先将下载页筛选出的真实文件名写入变量，示例不预设 310B 的固定包名：

~~~bash
cd "$package_dir" || exit 1
driver_package="./selected-driver.run"; firmware_package="./selected-firmware.run"
chmod +x "$driver_package" "$firmware_package" || exit 1
sudo "$driver_package" --full --install-for-all || exit 1
sudo "$firmware_package" --full || exit 1
sudo reboot
~~~

安装方式、`--full` 参数和 `--install-for-all` 是否适用取决于包类型和版本指南；不要把上面的示例当作任意设备的固定命令。每一步均在失败时退出；只有驱动和固件安装都成功后才重启。重启后先执行 `npu-smi info`，确认设备层正常，再安装或加载 CANN。

### 覆盖升级：固件、驱动、CANN

覆盖升级是指现有驱动和固件仍在系统中，准备换到新的配套版本。官方升级指南要求同时升级时遵循：

**先升级固件，再升级驱动，最后升级 CANN 软件。**

命令结构如下：

~~~bash
cd "$package_dir" || exit 1
driver_package="./selected-driver.run"; firmware_package="./selected-firmware.run"
chmod +x "$firmware_package" "$driver_package" || exit 1
"$firmware_package" --check || exit 1
"$driver_package" --check || exit 1
sudo "$firmware_package" --upgrade || exit 1
sudo "$driver_package" --upgrade || exit 1
sudo reboot
~~~

如果安装路径不是默认路径，必须把路径参数替换为当前安装路径。升级过程中不要同时启动服务、运行 ATC 或访问摄像头。升级成功消息只是安装器层面的结果，重启后还要完成设备、运行时和任务级验收。

### 升级 CANN Toolkit

驱动和固件已经在配套状态时，可以单独升级 CANN。对 `.run` 包，常见的检查顺序是“增加执行权限—校验安装包—升级”：

~~~bash
cd "$package_dir" || exit 1
toolkit_package="./selected-toolkit.run"
chmod +x "$toolkit_package" || exit 1
"$toolkit_package" --check || exit 1
"$toolkit_package" --upgrade || exit 1
~~~

升级命令通常从安装配置中读取原有路径；如果使用了自定义安装路径，应按安装指南显式指定。升级后检查 `latest` 软链接指向的版本，并在同一 shell 加载环境：

~~~bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
readlink -f /usr/local/Ascend/ascend-toolkit/latest
command -v atc
atc --version
python -c 'import acl; print("PyACL import: ok")'
~~~

### 子包独立升级

CANN 9.0.0/9.0.1 说明允许若干子包独立升级。独立升级不是“随便替换一个 `.so`”：

1. 组合包 `Ascend-cann-toolkit` 和 `Ascend-cann-ops` 必须已经安装；
2. 子包版本必须在当前 CANN 版本说明的配套表中；
3. 子包的安装路径必须与组合包一致；
4. 升级后要重新运行使用该子包的案例和模型回归。

以 `cann-hixl` 为例，变量中应填写下载页提供的实际文件名，例如带有版本号和架构标识的 `.run` 包。默认路径时省略 `--install-path`；自定义路径时使用下例：

~~~bash
hixl_package="./selected-cann-hixl.run"
chmod +x "$hixl_package" || exit 1
"$hixl_package" --upgrade --install-path="/home/custom_path" || exit 1
~~~

上例中的包名和版本号只是命令形状。执行前必须先查看该包的 `--help` 或对应安装指南；如果安装器提供独立的校验选项，应先完成校验，失败时停止，不要用 `--upgrade` 强行覆盖。

## 升级后的验收顺序

升级验收分成五个门，不应把一个门的通过写成全链路通过。

### 门 1：设备和驱动

~~~bash
npu-smi info
lsmod | grep -E 'drv|ascend|hisi' || true
ls -l /dev/davinci* /dev/devmm* 2>/dev/null || true
dmesg -T | tail -n 120
~~~

记录设备数量、型号、驱动显示的版本和异常日志。`Health: Alarm` 只能作为诊断背景；是否阻断升级要由设备缺失、驱动加载失败、复位循环或可复现的任务错误决定。

对于支持该工具的 310B 驱动包，可进一步读取设备侧系统版本。命令的实际路径和参数应以当前驱动版本的升级指南为准：

~~~bash
upgrade_tool="/usr/local/Ascend/driver/tools/upgrade-tool"
if [ -x "$upgrade_tool" ]; then
  "$upgrade_tool" --device_index -1 --system_version
else
  echo "upgrade-tool is not available; record the version from the board's release files"
fi
~~~

`npu-smi` 和 `upgrade-tool` 的输出分别属于设备状态查询和版本查询；二者都不能替代 CANN、框架插件和代表性模型的验证。

### 门 2：CANN 和 PyACL

~~~bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
command -v atc
atc --version
python -c 'import acl, sys; print(sys.executable); print("acl: ok")'
python -c 'import numpy; print(numpy.__version__)'
~~~

如果只在当前交互 shell 中成功，不能说明 systemd、Conda 或案例启动脚本也能成功。应使用与服务完全相同的启动入口再次执行。

### 门 3：代表性模型

选择每个业务最小但有代表性的模型，记录输入合同、模型摘要、SoC、精度模式和输出。对 ONNX 到 OM 的案例，至少执行一次模型检查和一次 ACL 加载；对已经固定的 OM，不要因为 `npu-smi` 正常就跳过加载。

~~~bash
model_path="models/accepted-model.om"
test -r "$model_path"
stat -c '%s %n' "$model_path"
sha256sum "$model_path"
~~~

模型加载通过仍然只是数值烟测。图像识别、音频合成、时频分析和 RAG 生成必须分别使用各自的输入协议与验收脚本。

### 门 4：服务和前端

~~~bash
ss -ltnp
systemctl --type=service --state=running
service_port=7860  # 按案例实际服务端口修改
curl --fail "http://127.0.0.1:${service_port}/health"
~~~

把 `service_port` 替换为实际服务端口；对没有 `/health` 的案例，应使用其 README 中规定的根页面或 API 路由。服务返回 HTTP 200 不能代替 NPU 推理和任务级结果。

### 门 5：回归与性能

升级后重新运行与旧版本相同的输入、预热次数、循环次数、线程数和输出检查。只有当结果与旧版本在预先定义的容差内一致，且没有新的资源泄漏、崩溃、超时、溢出或延迟回归，才可以更新案例报告。未经重新测量的旧数字不得标为新版本结果。

## 已知变化与迁移检查

### 废弃接口

CANN 9.0.0 说明把若干 Transformer 相关接口标记为废弃，并给出替代接口；说明中还指出部分特性将在后续版本移除。升级项目应先用静态搜索找出旧接口，再按实际调用路径迁移。不要因为编译仍然通过就认为废弃接口可以长期保留。

~~~bash
rg -n "aclnn(GroupedMat[Mm]ul|PromptFlashAttention|IncreFlashAttention|FusedInferAttentionScore)" \
  src samples
~~~

搜索结果需要由熟悉该模型和算子合同的人审阅。若接口来自第三方二进制或生成代码，必须在运行时日志和版本清单中标记为待验证。

### 版本说明中的已知问题

用户提供的 CANN 9.0.0 说明记录了通信域中 `int64` 集合通信算子可能影响断链后的快速恢复，也记录了跨超场景 `sendrecv` 偶发卡住的问题。它们属于版本说明中的风险提示，不是本仓库 310B 已观察到的故障。若案例使用相关通信路径，应在目标设备和实际拓扑上设计单独的故障恢复测试；不使用该路径时，在报告中写明“不适用”。

### 9.0.1 补丁的使用方式

用户提供的 9.0.1 文本列出了配套关系更新和若干修复，但没有替代目标产品下载页对 310B 的筛选。采用 9.0.1 时应保留：

* 9.0.0 到 9.0.1 的包名、架构和摘要；
* 驱动、固件、Toolkit、ops/NNAL 的完整组合；
* 旧版本基线报告和新版本回归报告；
* 任何因接口废弃、模型算子或框架插件变化产生的适配提交。

不要只升级 Toolkit 而把驱动、固件和 `torch_npu` 的旧版本写成“9.0.1 环境”。准确写法应列出每个组件的实际版本和证据。

## 回退与故障处理

### 回退原则

官方升级说明指出，从高版本回退到低版本通常需要卸载高版本后重新安装目标低版本，不能假设 `--upgrade` 会自动完成降级。因此升级前要保存旧安装包和配置，并准备与当前业务隔离的回退窗口。回退命令必须按目标版本安装指南执行，不能凭经验组合卸载脚本。

### 失败时的最小记录

~~~bash
failure_root="$HOME/upgrade-reports/failed-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$failure_root"
npu-smi info > "$failure_root/npu-smi.txt" 2>&1 || true
dmesg -T | tail -n 200 > "$failure_root/dmesg-tail.txt" 2>&1 || true
journalctl -b --no-pager -n 200 > "$failure_root/journal-tail.txt" 2>&1 || true
ps auxww > "$failure_root/processes.txt"
ss -ltnp > "$failure_root/listeners.txt" 2>&1 || true
~~~

保存日志后，先停止受影响的服务，确认设备状态和安装目录，再决定回退或提交支持工单。不要在失败后连续尝试不同版本包，也不要删除原始日志。任何 `rm -rf`、卸载脚本或覆盖安装都必须先解析绝对路径并获得维护授权。

## 面向本仓库案例的升级清单

| 阶段 | 必做事项 | 对应案例影响 |
| --- | --- | --- |
| 变更前 | 固定板卡、SoC、CANN/HDK 组合和 Python 环境 | 所有案例 |
| 资产前 | 保存 ONNX/OM 清单、输入合同和旧报告 | 案例 2、3、4、5、7、8、9 尤其重要 |
| 设备层 | 固件、驱动升级并重启，确认 `npu-smi` | 所有使用 NPU 的案例 |
| 软件层 | 加载 CANN，确认 ATC、PyACL 和框架插件 | 案例 1、3、4、5、7、9 |
| 模型层 | 重新检查或转换模型，执行 ACL 数值烟测 | 所有模型案例 |
| 外设层 | 重新枚举摄像头、USB、ALSA、MIDI 和网络端口 | 案例 2、3、5、8、9 |
| 任务层 | 使用固定输入重跑功能、性能和长稳测试 | 对应案例的验收报告 |
| 文档层 | 更新版本清单和证据路径，不覆盖旧报告 | 教材、README、工程文档 |

升级完成前，案例文档中的“已验证”不应自动继承。最小可接受结论是：设备层、运行时层、模型层和任务层分别有可定位的输出；缺少其中任一层时，结论写为 `untested` 或 `unknown`。

## 版本记录模板

~~~text
设备：
SoC/算力等级：
操作系统/内核/架构：
升级前驱动：
升级前固件：
升级前 CANN Toolkit/ops/NNAL：
升级前 Python/框架插件：
目标驱动：
目标固件：
目标 CANN Toolkit/ops/NNAL：
安装包摘要与签名位置：
升级顺序：首次安装 / 覆盖升级 / 仅 CANN / 子包独立升级
维护窗口：
回滚包与配置位置：
设备门结果：
运行时门结果：
模型门结果：
服务门结果：
任务和性能门结果：
结论：observed-pass / observed-fail / untested / unknown
原始报告目录：
~~~

## 资料来源与使用边界

* [昇腾版本配套查询助手](https://www.hiascend.com/developer/download/compatibility)：310B 系列产品的当前软件版本配套查询。本文表格于 2026-10-03 查询，官网将 2026/06/30 标为推荐。
* [CANN 9.0.0 版本说明](https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/900/releasenote/release-notes.md)：版本配套、组合包、特性变化、废弃项和已知问题。
* [昇腾资源下载中心](https://www.hiascend.com/developer/download/ascend-driver)：按产品、操作系统、架构筛选驱动、固件和 CANN 包。
* [升级前必读](https://www.hiascend.com/document/detail/zh/CANNCommunityEdition/83RC1/softwareinst/instg/instg_0028.html)：升级影响、回退限制和固件—驱动—CANN 的升级顺序。具体版本应以对应版本的安装指南为准。
* [安装 NPU 驱动和固件](https://www.hiascend.com/document/detail/en/CANNCommunityEdition/900/softwareinst/instg/instg_0005.html?InstallType=local&Mode=PmIns&OS=Debian)：安装包形式、权限、默认路径和用户组注意事项。
* [CANN 下载中心](https://www.hiascend.com/cann/download)：按具体产品筛选驱动、固件、Toolkit 和配套版本。
* [Ascend 310B 驱动升级指南](https://www.hiascend.com/document/detail/zh/Atlas%20200I%20A2/24.1.RC1/EP/upgradeguide/upgrad_006.html)：使用 `upgrade-tool` 查询设备侧系统版本的示例。

本附录中的当前版本矩阵来自上述官方查询页；9.0.1 配套表和补丁摘要来自用户提供的版本说明文本。正式升级前，应在昇腾下载中心重新确认页面、包名、签名、适用产品和当前版本的安装指南；本附录不替代厂商发布的安装手册，也不构成对未测试硬件组合的兼容性承诺。
