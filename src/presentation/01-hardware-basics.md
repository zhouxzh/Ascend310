---
marp: true
size: 16:9
theme: default
paginate: true
header: "昇腾310B 8周教学"
footer: "第1周：昇腾310B硬件基础"
---

# 第1周：昇腾310B硬件基础

昇腾310B 8周教学

每周 3 课时，每课时 45 分钟

| 课时 | 主题 | 完成后的能力 |
| --- | --- | --- |
| 第1课时 | 硬件理解 | 能识别310B模块、开发板接口与配件边界 |
| 第2课时 | 系统安装与启动 | 能校验、刷写镜像并完成首次启动 |
| 第3课时 | 网络与环境验证 | 能SSH登录并验证conda、CANN与NPU |

---

## 课程目标：建立一条可复现的验证路线

本课程的重点是建立从硬件、系统、网络到NPU环境的可复现路径。学完本周内容后，学生应能回答附录2给出的四个审查问题：命令运行在哪台机器和哪个环境中、输入输出路径是什么、这一步验证的是语法、路由、转换、数值、性能还是硬件现象、命令是否会安装软件或修改系统状态。这四个问题会贯穿后续章节和案例。

---

## 第1课时（45分钟）：310B在边缘计算中的位置

边缘计算把计算、存储和网络部署在靠近数据源的位置，在本地或近端处理数据，从而降低时延、节省带宽并保留数据主权。昇腾310是华为昇腾AI计算产品线的关键入门级芯片，2018年发布时面向边缘和端侧场景，设计目标是在极低功耗下提供边缘实时AI推理算力；昇腾310B是昇腾310的优化和增强版本。

本课程的实验板是OrangePi AIpro（8T），搭载昇腾310B处理器，支持8 TOPS INT8的AI算力。它把CPU、AI处理器、DVPP数字视觉预处理等集成在一起，适合视频、图像等非结构化数据的本地处理。

---

## 昇腾310B 8T模块规格

第1章给出了310B系列模块的技术规格。昇腾310B系列有20T和8T两个算力等级，核心架构、接口和外形高度统一，主要差异在性能配置；本项目按Ascend 310B4 / 8T记录结果。

| 规格项 | 昇腾310B（8T） |
| --- | --- |
| AI算力 | 8 TOPS INT8，4 TFLOPS FP16 |
| 内存 | LPDDR4X 4GB，总带宽25.6GB/s，支持ECC |
| CPU | 4核 × 1.0GHz |
| 视频解码 | H.264/H.265硬件解码：20路1080P 30FPS，2路4K 75FPS |
| 视频编码 | H.264/H.265硬件编码：12路1080P 30FPS，2路4K 50FPS |
| JPEG | 解码1080P 512FPS，编码1080P 256FPS，最大分辨率16384×16384 |
| 低速接口 | UART×5，I2C×4，SPI×2，CAN×4 |
| 典型功耗 | 21W |
| 工作温度 | -20℃ ~ +103℃ |
| 结构尺寸 | MXM 82mm × 60mm × 7mm |

---

## 开发板形态：OrangePi AIpro（8T）

OrangePi AIpro（8T）由香橙派联合华为打造，采用昇腾AI技术路线，搭载昇腾310B处理器（4核64位处理器 + AI处理器），集成图形处理器，支持8TOPS INT8的AI算力。板级规格来自附录1：

- 板载LPDDR4X内存：8GB / 16GB
- 支持外接32GB至256GB的eMMC模块
- 支持双4K高清输出
- 软件支持Ubuntu和openEuler，本教程基于Ubuntu

需要区分资料层级：附录1中的8GB/16GB是开发板内存规格，第1章表格中的LPDDR4X 4GB是310B 8T模块规格。课堂讨论时先说清说的是模块还是整板，避免把规格混在一起。

---

## 开发板接口

接口直接决定外设接入方式和实验边界。OrangePi AIpro（8T）的接口来自附录1：

| 类别 | 接口 |
| --- | --- |
| 显示 | 两个HDMI输出、MIPI屏幕接口 |
| 存储 | TF插槽、支持SATA/NVMe SSD 2280的M.2、预留eMMC接口 |
| 网络 | 千兆网口、板载Wi-Fi模块 |
| USB | 两个USB3.0、一个USB Type-C 3.0 |
| 调试 | Micro USB（CH343P芯片，UART串口打印） |
| 摄像头 | 两个MIPI摄像头接口 |
| 电源/扩展 | Type-C电源接口、GPIO接口、电池接口 |

其中Type-C接口支持USB 3.0协议但不兼容USB 2.0，需要连接USB 3.0外设时使用转接线。串口调试只需要Micro USB数据线，不占用HDMI和键盘鼠标。

---

## 配件准备

课堂应在开始刷写前检查配件，避免启动时才缺电源或显示线。附录1的配件清单如下：

| 类别 | 项目 | 要求 |
| --- | --- | --- |
| 必备 | TF卡 | 建议64GB及以上、Class10及以上，推荐SanDisk品牌 |
| 必备 | 读卡器 | 建议USB 3.0速率，减少刷写等待时间 |
| 必备 | HDMI线 | 标准HDMI或按显示器准备转接线 |
| 必备 | 电源适配器 | 支持PD协议20V输出的65W Type-C电源 |
| 必备 | USB鼠标、键盘 | 本地桌面操作 |
| 建议 | 金属外壳 | 防止意外短路或物理损伤 |
| 建议 | 散热风扇及鳍片 | 开发板提供2pin风扇接口（12V），支持PWM调速 |
| 可选 | 网线 | 需要更稳定网络时使用千兆网口 |
| 可选 | M.2 SSD、eMMC、MIPI摄像头等 | 按实验需求扩展 |

---

## 散热、供电与存储边界

硬件边界决定了哪些操作可以长期运行、哪些只是临时方案。本章给出的边界是：

- 昇腾310B处理器在高负载下发热量较大，强烈建议安装主动散热设备。
- 电源必须使用支持PD协议20V输出的65W Type-C适配器，不能随意用低功率电源替代。
- 开发板支持从TF卡、eMMC或M.2 SSD启动；板载无eMMC，eMMC需购买专用模块，M.2 SSD兼容性有限，初学者不推荐直接以SSD作为系统盘。
- 如果设备以TF卡作为主要存储，不建议在TF卡上频繁使用Swap。Swap涉及大量高频读写，会加速闪存磨损，缩短TF卡寿命。

---

## 硬件与命令安全边界

这一页给出后续每次操作都要遵守的边界：

- 不使用来源不明的系统镜像，只使用官方技术支持页面提供的镜像。
- 不拔掉正在写入的TF卡，不在刷写过程中断电。
- 不把开发板当作普通台式机随意安装系统级软件；软件包操作会改变系统状态。
- 执行命令前先确认机器、路径、验证目标和副作用，尤其不能执行未经路径确认的`rm -rf`、`sudo pip install`或带`--delete`的同步命令。
- 出现Health状态为Alarm时，先保存诊断背景，再根据ACL初始化、ATC退出码、段错误等具体现象判断是否失败，不能只凭摘要下结论。

---

## 第2课时（45分钟）：系统镜像选择与下载

开发板板载无预装系统，需要先在电脑上把系统镜像刷写到TF卡。官方提供了预装昇腾NPU应用环境及软件的Ubuntu和openEuler镜像，极大方便了开发者快速上手；本教程使用Ubuntu。

Ubuntu镜像中，带`Desktop`字样的版本包含GUI图形化界面，适合初学者；`Minimal`版本仅包含命令行界面，适合高级用户或服务器用途。下载目录中`.xz`后缀文件是镜像压缩包，`.sha`后缀文件是MD5校验码，用于验证下载文件完整性。

---

## 校验镜像MD5

解压前先校验，避免把损坏镜像刷入TF卡后无法启动。Windows、Ubuntu和macOS的命令分别如下：

```powershell
certutil -hashfile opiaipro_ubuntu22.04_desktop_aarch64_20241128.img.xz md5
```

```bash
md5sum <filename>
```

```bash
md5 <filename>
```

将输出值与同目录下`.sha`文件中的内容比对。若一致，说明文件完整，可以解压得到`.img`镜像；若不一致，请重新下载，不要继续刷写。

---

## 格式化TF卡并烧录镜像

刷写工具是GUI操作，但每一步都有明确目的：先格式化确认存储卡状态，再用balenaEtcher把`.img`写入TF卡。

1. 将TF卡插入读卡器并连接电脑，打开SD Card Formatter，确认选中正确盘符后点击Format。格式化会清除TF卡上所有数据，必须确认盘符无误。
2. 打开balenaEtcher，选择“Flash from file”，选择解压后的`.img`文件，再选择TF卡为目标，点击“Flash!”开始烧录。建议使用1.19.25及以下版本，避免兼容性问题。
3. 烧录完成后软件会自动校验，等待校验通过后再安全弹出TF卡。

---

## 设置启动模式

开发板支持从TF卡、eMMC或M.2 SSD启动。当连接多种存储设备时，需要通过背面的BOOT拨码开关指定启动设备。拨码开关ON方向为1/右，相反为0/左，具体以板上丝印为准。

| Boot1 | Boot2 | 启动设备 |
| :---: | :---: | :---: |
| 左 | 左 | 未使用 |
| 右 | 右 | TF卡 |
| 左 | 右 | eMMC |
| 右 | 左 | M.2 SSD |

切换拨码开关后必须完全断电（拔掉电源线）再重新上电，新的启动配置才会生效；仅按RESET键重启无效。

---

## 图形化界面启动

图形化启动适合课堂演示，也方便学生第一次看到登录界面和桌面环境。连接顺序来自附录1：

1. 将刷写好的TF卡插入开发板插槽。
2. 确认BOOT开关均拨至右侧，即TF卡启动模式。
3. 将HDMI线连接到HDMI0接口，即靠近USB 3.0接口的那个。
4. 连接鼠标和键盘，最后接入Type-C电源。
5. 上电后风扇会全速旋转，随后声音变小，屏幕显示启动画面，稍候进入登录界面。

默认账户：

| 用户名 | 密码 |
| :---: | :---: |
| HwHiAiUser | Mind@123 |
| root | Mind@123 |

若无法登录，请检查密码的大小写和符号是否正确。

---

## 串口登录

没有显示器、键盘和鼠标时，可以使用开发板自带的Micro USB接口进行串口登录，只需要一根Micro USB数据线。步骤如下：

1. 使用Micro USB数据线连接开发板和电脑，此时不要给开发板上电。
2. 打开电脑的设备管理器，在端口下查找开发板对应的串口端口号。
3. 打开PUTTY，将Connection Type选择为Serial，Serial Line填写设备管理器中查到的端口号，将Speed从9600修改为115200，点击Open。
4. 给开发板上电，等待出现类似`Ubuntu 22.04.3 LTS orangepiaipro ttyAM0`的提示，输入用户名`HwHiAiUser`并回车，再输入密码`Mind@123`并回车。

串口输入密码时屏幕不会显示任何内容，这是正常现象，不要因此重复输入。

---

## 启动观察与故障记录

每次启动都应记录可观察过程，而不是只等最终界面：

- 上电后风扇是否先全速旋转再变小；屏幕是否出现启动画面；是否进入登录界面或串口提示。
- 若HDMI无画面，先检查HDMI0接口、BOOT开关和电源适配器，再检查TF卡是否已刷写。
- 若串口无输出，先确认Micro USB在供电前连接、串口速度和端口号正确。
- 将异常现象、完整命令输出和时间记录到课堂文档，作为后续环境验证的证据。

---

## 第3课时（45分钟）：WiFi无线网络连接

开发板板载WiFi模块，可以通过`nmcli`命令行连接无线网络。先扫描WiFi，再连接，最后查看连接状态：

```bash
nmcli dev wifi list
nmcli dev wifi connect <SSID> password <PASSWORD>
nmcli connection show
```

`nmcli dev wifi list`用于列出附近WiFi；`nmcli connection show`用于查看已保存和已激活的连接。连接后可以用`ip addr`确认接口是否拿到IP地址，通常无线接口名为`wlan0`。

---

## nmtui与有线网络

不熟悉命令行参数时，可以使用NetworkManager的终端图形界面：

```bash
sudo nmtui
```

在`Activate a connection`中选择需要连接的WiFi，输入密码并等待状态变为已连接，然后返回并退出。确认网络状态时执行：

```bash
nmcli connection show
ip addr
```

有线网络更简单：直接插入网线，然后使用`ip addr`查看IP地址；部分系统仍可使用`ifconfig`，但新脚本不应只依赖它。有线接口通常为`eth0`，在SSH前应确认实际接口名。

---

## SSH远程登录

为了在开发机上操作板端，需要先获取开发板IP地址，再使用SSH客户端连接。获取IP使用`ip addr`，地址通常在`wlan0`或`eth0`接口下。

```bash
ssh HwHiAiUser@<开发板IP地址>
```

例如：

```bash
ssh HwHiAiUser@192.168.1.100
```

Windows可以使用CMD、PowerShell或PUTTY，Mac/Linux使用Terminal。默认密码为`Mind@123`。SSH登录成功后，当前shell就在板端，之后加载conda和CANN必须保持在同一个shell中完成。

---

## 环境验证前的审查与严格模式

在板端执行命令前，先回答附录2的四个问题：在哪台机器和哪个shell、输入输出路径是什么、这一步验证什么、是否修改系统状态。可审计的shell示例：

```bash
set -euo pipefail
pwd
whoami
hostname
printf 'shell=%s\n' "$SHELL"
```

`set -e`会在未处理的失败后停止脚本，`-u`暴露未定义变量，`pipefail`保留管道前段命令的失败状态。交互式排查时也可以只执行其中的环境变量和路径检查。

---

## 板端诊断快照

环境验证前先保存一份板端快照，后续ATL、ATC、推理和性能结果都应有同一份基础环境作为背景：

```bash
uname -a
cat /etc/os-release
hostname
date --iso-8601=seconds
free -h
df -h
uptime
npu-smi info
```

这些命令记录操作系统、内存、磁盘、运行时长和NPU状态。快照中的`Health: Alarm`要作为诊断背景保存，但不能据此自动判定ATC、ACL、性能或精度失败；具体失败仍要由独立现象确认。

---

## Conda与Python环境

板端镜像通常预装Miniconda和`pip3`。加载Conda后，再激活目标环境并检查解释器：

```bash
load_conda() {
  if [[ $- == *u* ]]; then
    set +u
    source /usr/local/miniconda3/etc/profile.d/conda.sh
    set -u
  else
    source /usr/local/miniconda3/etc/profile.d/conda.sh
  fi
}
load_conda
conda activate base
python --version
python -c 'import sys; print(sys.executable)'
```

Conda加载、环境激活、CANN加载和后续启动服务必须发生在同一个shell中。`python -m pip`能保证pip对应当前解释器，比直接使用`pip`或`pip3`更可审计。

---

## CANN环境验证

CANN（Compute Architecture for Neural Networks）是面向昇腾处理器的全栈软件体系，覆盖模型表示、转换编译、图优化、调度执行和可观测分析。官方镜像通常预装CANN，可用`npu-smi info`确认驱动和固件，再检查环境变量和编译器：

```bash
echo $ASCEND_HOME_PATH
c++ --version
```

加载CANN环境：

```bash
load_cann() {
  export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
  source /usr/local/Ascend/ascend-toolkit/set_env.sh
}
load_cann
command -v atc
python -c 'import acl; print("PyACL import: ok")'
```

`set_env.sh`只影响当前shell；在另一个终端执行它，不能保证启动服务的shell继承环境。若`import acl`失败，应记录解释器、CANN路径和完整错误，再修复环境，不添加CPU回退。

---

## 查看NPU状态

查看NPU状态的标准命令：

```bash
npu-smi info
```

附录1的示例输出可以整理为以下字段进行解释：

| 字段 | 示例值 | 含义 |
| --- | --- | --- |
| npu-smi | 23.0.0 | 工具版本 |
| NPU Name | 310B4 | 设备型号 |
| Health | OK | 健康状态 |
| Power | 12.8W | 当前功耗 |
| Temp | 45C | 当前温度 |
| AICore | 0% | 当前AI Core利用率 |
| Memory-Usage | 2433 / 7564 MB | 当前/总内存使用 |

查询结果中的Name若为`310B4`，后续ATC转换配置`--soc_version=Ascend310B4`，即在Name前加`Ascend`前缀。

---

## 最小ACL设备验证

第4章样例提供了一段最简的ACL验证程序，流程是ACL初始化、查询设备数量、去初始化：

```python
import acl

ret = acl.init("")
if ret != 0:
    print(f"ACL init failed, ret={ret}")
    exit(1)

count, ret = acl.rt.get_device_count()
if ret == 0:
    print(f"Found {count} Ascend devices.")

acl.finalize()
```

运行：

```bash
python samples/chapter4/check_ascend_device/check_ascend_device.py
```

这段代码不加载模型、不做推理，只验证ACL运行库能否初始化并枚举设备，适合作为板端环境的第一道门禁。

---

## 后续章节样例的运行前提

第3章和第4章样例的运行前提也应在本周验证。第3章README要求先加载CANN并确认`torch_npu`可用：

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
python -c "import torch; import torch_npu; print(torch.npu.is_available())"
```

第4章README要求先加载CANN并确认`acl`模块可导入：

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
python -c "import acl; print('PyACL OK')"
```

这两个检查分别对应第3章的训练/迁移环境和第4章的PyACL推理环境，必须在板端执行。

---

## ATC转换入口示例

第4章样例展示了ONNX到OM的ATC转换命令。ATC只在板端执行，`--soc_version`、输入布局和形状必须来自模型合同：

```bash
atc --model=samples/chapter4/resnet18/model/resnet18_tiny_imagenet.onnx \
    --framework=5 --output=samples/chapter4/resnet18/model/resnet18_tiny_imagenet \
    --soc_version=Ascend310B4
```

`--framework=5`表示ONNX格式；`--soc_version=Ascend310B4`来自`npu-smi info`查询结果。转换完成后还要检查文件存在、字节数和摘要，但摘要检查不能替代OM加载和数值烟测。

---

## CANN安装命令（仅在镜像未预装时）

`npu-smi info`能正常显示设备信息，说明NPU驱动和固件已正确安装；昇腾310B开发板通常预装驱动、固件、Miniconda和`pip3`，无需额外配置。若确实需要手动安装，第2章给出的示例以root执行：

```bash
su -
chmod +x Ascend-cann-toolkit_8.3.RC1_linux-aarch64.run
./Ascend-cann-toolkit_8.3.RC1_linux-aarch64.run --install
source /usr/local/Ascend/ascend-toolkit/set_env.sh
chmod +x Ascend-cann-kernels-310b_8.3.RC1_linux-aarch64.run
./Ascend-cann-kernels-310b_8.3.RC1_linux-aarch64.run --install
```

文件名中的版本号必须替换为实际下载版本，Toolkit与Kernels版本号需一致。安装后建议把CANN环境变量追加到`~/.bashrc`；官方镜像通常已经预置这些配置。

---

## 验证结果的证据边界

本周收集的证据必须区分层级：`npu-smi info`是设备摘要，`import acl`成功是运行库可用，模型加载成功不代表任务精度，HTTP状态码不代表NPU推理成功。记录时应保存原始输出、执行机器、shell、环境、路径和时间戳。

若出现`import acl`失败，先检查`command -v python`和是否在同一shell中执行了`source set_env.sh`；若ATC找不到算子，保存失败命令、模型合同和日志，不生成伪OM；若出现`Health: Alarm`，记录为诊断背景，不自动判定失败。

---

## 课堂任务

1. 完成TF卡格式化、镜像校验与烧录，使用TF卡启动模式进入图形界面或串口登录。
2. 连接WiFi或有线网络，通过`ip addr`获取IP，并从开发机使用SSH登录板端。
3. 在板端同一个shell中加载conda和CANN，执行`python --version`、`python -c 'import sys; print(sys.executable)'`、`command -v atc`和`python -c 'import acl; print("PyACL import: ok")'`。
4. 执行`npu-smi info`，保存输出并说明设备型号、健康状态、功耗、温度、AI Core利用率和内存使用。
5. 运行第3章样例前提检查`python -c "import torch; import torch_npu; print(torch.npu.is_available())"`和第4章`check_ascend_device.py`，把结果写入实验记录。
6. 编写安全笔记，列出3个未经确认不应执行的系统级或删除命令模式。

---

## 交付物

| 交付物 | 内容 |
| --- | --- |
| `linux/week01/board-snapshot.txt` | `uname -a`、`/etc/os-release`、`free -h`、`df -h`、`npu-smi info`等原始输出 |
| `linux/week01/network-ssh.txt` | WiFi或有线连接方式、IP地址、SSH登录使用的用户名和结果摘要 |
| `linux/week01/env-validation.txt` | conda、Python、CANN、PyACL、torch_npu和ACL设备检查的原始输出 |
| `linux/week01/safety-notes.md` | 硬件安全边界、命令审查清单和不执行命令模式 |

报告中应删除用户名、IP、token、真实图像路径等隐私信息；IP地址可保留课堂实验网络中的必要记录。

---

## 验收标准

- 能说明310B 8T模块和OrangePi AIpro（8T）开发板的规格层级，以及主要接口、配件和运行边界。
- 能复现MD5校验、TF卡格式化、镜像烧录、BOOT开关设置、图形化或串口启动流程。
- 能解释`nmcli`、`ip addr`、`ssh`、`npu-smi info`、conda和CANN命令的作用，并说明它们分别验证什么。
- 板端环境验证命令全部有原始输出；若某项失败，记录完整错误、执行环境和修复过程，不伪造通过结果。
- 能写出3个未经确认不应直接执行的命令模式，并说明原因。
