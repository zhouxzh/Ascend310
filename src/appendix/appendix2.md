---
title: "附录 2：昇腾 310B Linux 操作与命令教程"
author: [周贤中]
subject: "Markdown"
keywords: [昇腾310B, Linux, Ubuntu, CANN, PyACL, ATC, 边缘计算]
lang: zh-cn
---

# 附录 2：昇腾 310B Linux 操作与命令教程

## 教程定位与证据边界

本附录把本书九个实践案例中反复出现的 Linux 命令整理为一条可复用的操作路径。它的对象是运行 Ubuntu 的昇腾 310B 开发板，以及负责准备代码和发起远程操作的开发机。命令不是脱离上下文的速查表：每条命令都应在确认执行位置、当前用户、工作目录和数据范围后使用。

本附录中的“开发机”表示不具备昇腾运行时的控制机；“板端”表示已经安装 CANN、驱动和目标 Python 环境的 310B 主机；“控制机”表示通过 SSH 或 rsync 操作板端的开发机。开发机可以进行纯 Python、前端和静态模型检查，但不能据此推断板端的 ACL、OM、摄像头或音频结果。ATC、PyACL、OM 推理、NPU 性能、V4L2、ALSA 和真实传感器验证都必须在板端完成。

命令来自当前工作树中 samples/case1/ 至 samples/case9/ 的 README、脚本、测试和 docs/，并按当前主线流程重新编排。历史归档脚本、Windows PowerShell 片段、停用的模型链和上游项目中的其他平台命令不属于默认部署步骤；需要保留时会明确标注为“历史”或“可选”。示例中的 〈板端IP〉、〈用户名〉、〈模型ID〉和〈路径〉必须替换为本地已确认的值，不应把示例占位符直接粘贴到 shell。

| 案例 | 主要 Linux 操作 | 默认执行位置 | 证据边界 |
| --- | --- | --- | --- |
| Case 1 人脸考勤 | Python 环境、FastAPI 服务、上传和健康检查 | 开发机与板端 | HTTP 路由检查不等于真实人脸推理 |
| Case 2 目标跟踪 | 模型下载、视频传输、检测/跟踪、摄像头 | 控制机与板端 | CPU 仅作离线基线，不能替代 NPU 结果 |
| Case 3 智能电子琴 | WebUI、ONNX/OM 准备、MIDI、ALSA/PulseAudio | 开发机与板端 | 音频设备和实时延迟必须在板端测量 |
| Case 4 掌纹工作台 | 资产清单、服务、摄像头、ACL 生命周期 | 开发机与板端 | 模型准入、ACL 烟测和识别指标是不同验证门 |
| Case 5 数据采集仪 | USB 仪器、sigrok、RTL-SDR、实时仪表盘 | 板端 | 音调演示和 CPU FFT 不能作为实物采集证据 |
| Case 6 智能小车 | 场景模型、服务、摄像头和运动控制入口 | 板端 | --share 或公网隧道不适合生产部署 |
| Case 7 智能相册 | 相册服务、索引、触控 kiosk、模型准备 | 控制机与板端 | 合成照片测试不代表真实数据集准确率 |
| Case 8 手势识别 | YOLO 导出、ATC、OM 摄像头和 WebRTC | 开发机与板端 | ONNX、OM、WebRTC 性能应分别记录 |
| Case 9 RAG 网关 | ACL/OM 服务、网关、文件同步和 HTTP | 控制机与板端 | B4 与 B1 工件、不同模型链不可混用 |

### 命令的四个审查问题

执行任何命令前，先回答四个问题：

1. 命令运行在哪台机器、哪个 shell 和哪个 conda 环境中？
2. 输入文件、输出目录和日志是否在预期的绝对路径内？
3. 这一步证明的是语法、路由、转换、数值一致性、性能，还是硬件现象？
4. 命令是否会安装软件、修改设备状态、覆盖文件或删除数据？

只有在四个问题都有答案时，命令输出才适合作为教学或实验记录。尤其不能把 npu-smi info 的设备摘要、curl 的 HTTP 状态码或一次模型加载成功误写成任务精度或端到端性能结论。

## Shell、路径与文件安全

### 启动一个可审计的 shell

在板端脚本或手工会话中，可先使用严格模式；交互式排查时也可以只执行其中的环境变量和路径检查：

~~~bash
set -euo pipefail
pwd
whoami
hostname
printf 'shell=%s\n' "$SHELL"
~~~

set -e 会在未处理的失败后停止脚本，-u 会暴露未定义变量，pipefail 会保留管道前段命令的失败状态。需要允许某个命令失败时，应显式写出原因并检查返回值，而不是全局关闭严格模式。

### 目录与路径

~~~bash
cd ~/Documents/Ascend310/samples/case1
pwd
realpath .
readlink -f frontend/dist/index.html
ls -la
find . -maxdepth 2 -type f -print | sort
~~~

pwd 和 realpath 用于确认当前路径；readlink -f 可解析符号链接，适合在删除、同步或启动服务前确认实际目标。find 的 -maxdepth 先限制扫描范围，避免误把整个家目录或挂载盘纳入操作。

常用的容量和文件属性检查如下：

~~~bash
stat -c '%A %U:%G %s %n' models/example.om
file models/example.om
du -sh models data reports
df -h .
~~~

stat 能同时看到权限、所有者和字节数；file 只能作格式线索，不能证明模型可由 ACL 加载。du 关注目录占用，df 关注文件系统剩余空间，两者含义不同。

### 创建、复制与权限

~~~bash
run_date="$(date --iso-8601=date)"
mkdir -p "reports/board/$run_date"
cp -a config.example.yaml config.yaml
mv reports/old.json reports/archive/old.json
chmod u+x scripts/run_service.sh
test -r models/example.om && echo 'model is readable'
command -v python
command -v atc || true
~~~

cp -a 尽量保留元数据；在覆盖前应先用 test -e 或 ls 检查目标。chmod u+x 只给文件所有者增加执行权限，不要对整棵家目录使用宽泛的 chmod -R 777。command -v 检查实际使用的可执行文件，能够发现 PATH 中混入了错误的 Python、ATC 或脚本版本。

### 文本、日志和归档

~~~bash
printf '%s\n' 'starting board smoke' | tee reports/board/smoke.log
grep -n 'ERROR\|WARN' reports/board/smoke.log
rg -n 'atc|acl|sha256|0\.0\.0\.0' samples/case*/README.md samples/case*/docs
head -n 40 reports/board/smoke.log
tail -n 80 reports/board/smoke.log
wc -l reports/board/smoke.log
awk '{print NR ":" $0}' reports/board/smoke.log | tail -n 20
sed -n '1,80p' reports/board/smoke.log
run_date="$(date --iso-8601=date)"
tar -czf "reports/board/smoke-$run_date.tar.gz" reports/board/smoke.log
unzip -l artifact.zip
~~~

rg 适合在仓库内检索命令和接口，grep 适合对单份日志做过滤。归档前先确认内容和路径；压缩包不应包含照片、掌纹模板、数据库、模型二进制或密钥。

### 删除操作的边界

rm 不提供回收站。对案例数据只能在已经解析为绝对路径、且明确属于隔离测试目录时使用：

~~~bash
target_dir="$(realpath -- "$PALMPRINT_ROOT/data/captures")"
case "$target_dir" in
  "$PALMPRINT_ROOT"/data/captures) ;;
  *) printf 'refuse to remove: %s\n' "$target_dir" >&2; exit 1 ;;
esac
find "$target_dir" -maxdepth 1 -type f -print
rm -f -- "$target_dir"/*.jpg
~~~

上述示例仍需由操作者确认文件列表。仓库中出现的 rm -rf 只用于脚本创建的临时或隔离目录；绝不能把变量为空、路径未解析的命令改成 rm -rf "$HOME"、rm -rf . 或对整个部署目录执行。删除前优先保留报告、移动到明确的归档目录，或让部署脚本在独立临时目录中自动清理。

## Ubuntu 软件包与 Python 环境

### APT 软件包

Case 5 的 sigrok/RTL-SDR 桥接和 Case 6 的示例安装涉及系统软件包。软件包操作改变系统状态，应在维护窗口中由管理员执行，并记录 Ubuntu 版本和安装结果：

~~~bash
sudo apt-get update
sudo apt-get install -y libsigrok-dev sigrok-cli gcc pkg-config libfftw3-single3 rtl-sdr
~~~

Case 6 的旧安装脚本还会尝试安装 python3-dev 和 python3-pip。在已经准备好的板端环境中，优先确认包是否存在；不要为了通过文档示例而重复安装或升级 CANN 相关包：

~~~bash
dpkg -s python3-dev python3-pip 2>/dev/null | grep -E '^(Package|Status):'
~~~

触摸屏的 xdotool、onboard 属于可选维护工具，不是案例运行时依赖。只有在隔离的桌面维护窗口中才按需安装；常规板端部署不应执行图形桌面安装。

### Conda 与解释器

先加载 Conda shell 函数，再激活目标环境；两步必须在启动 ATC 或服务的同一个 shell 中完成。为兼容前文的严格模式，定义一个可重复使用的加载函数：

~~~bash
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
~~~

若项目明确要求独立环境，可在开发机或板端维护窗口创建。Case 9 当前板端门禁要求 Python 3.9；不要用其他版本的同名环境替代它：

~~~bash
conda create -n case9-acl-om python=3.9
conda activate case9-acl-om
~~~

当前板端主线通常复用已验证的环境，不应无计划地创建新环境。python -m pip 能保证 pip 对应当前解释器；pip、pip3 是历史命令，在教程中保留时必须先用 command -v 核对。

~~~bash
python -m pip install -r requirements.txt
python -m pip install -r requirements-board.txt
python -m pip list
~~~

Case 2 的旧 README 曾出现 requirementstxt 拼写错误；实际文件名是 requirements.txt，应使用上面的命令。

### Python 路径变量

~~~bash
export PYTHONNOUSERSITE=1
export PYTHONPATH="$PWD:${PYTHONPATH:-}"
export LD_LIBRARY_PATH="/usr/local/Ascend/ascend-toolkit/latest/lib64:${LD_LIBRARY_PATH:-}"
python -c 'import sys; print("\n".join(sys.path))'
~~~

PYTHONNOUSERSITE=1 用于避免用户目录中的包遮蔽板端环境；PYTHONPATH 和 LD_LIBRARY_PATH 应只加入已经确认的项目或 CANN 目录。若变量尚未设置，可先用 export LD_LIBRARY_PATH=/usr/local/Ascend/ascend-toolkit/latest/lib64:；持久化修改应经过部署评审。

## CANN 环境、NPU 诊断与模型工件

### 加载 CANN

~~~bash
load_cann() {
  # CANN environment scripts may read variables that an interactive shell has not set.
  export LD_LIBRARY_PATH="${LD_LIBRARY_PATH:-}"
  if [[ $- == *u* ]]; then
    set +u
    source /usr/local/Ascend/ascend-toolkit/set_env.sh
    set -u
  else
    source /usr/local/Ascend/ascend-toolkit/set_env.sh
  fi
}
load_cann
command -v atc
python -c 'import acl; print("PyACL import: ok")'
~~~

set_env.sh 设置 ACL、运行时和工具链的库路径；只在另一个终端执行它，不能保证启动服务的 shell 继承环境。若此前开启了 nounset，示例函数只在加载 CANN 时暂时关闭它，再恢复原状态，以避免环境脚本读取未定义变量而失败。若 import acl 失败，应记录解释器、CANN 路径和完整错误，使用项目的启动脚本修复环境，不添加 CPU 或随机输出回退。

### 板端基本诊断

~~~bash
uname -a
cat /etc/os-release
hostname
date --iso-8601=seconds
free -h
swapon --show
df -h
uptime
ps -p 1 -o pid,comm,args
ps aux | head -n 20
npu-smi info
~~~

这些命令记录的是操作系统、内存、进程和设备状态。310B4 / 8T 板上出现 Health: Alarm 时，应把它作为诊断背景保存，但不能据此自动判定 ATC、ACL、性能或精度失败；相反，ACL 初始化失败、设备不存在、ATC 非零退出、段错误、资源泄漏和可重复的任务失败都必须单独报告。

可把诊断和被测命令绑定为一份报告：

~~~bash
case4_root="$HOME/Documents/palmprint-recognition"
test -d "$case4_root"
cd "$case4_root"
model_id="admitted-model-id"
synthetic_roi="path/to/synthetic-roi.png"
test -r "$synthetic_roi"
python -m tools.board.collect_npu_trace \
  --label case4-acl-smoke \
  --interval 1 \
  --output-dir reports/system \
  -- python -m tools.board.acl_lifecycle_probe \
      --model "$model_id" \
      --image "$synthetic_roi" \
      --cycles 10 --threads 1
~~~

### ATC 与 OM

ATC 只在板端执行 ONNX 到 OM 的转换。下面的示例使用 ONNX（`--framework=5`）；`--soc_version`、输入布局和形状必须来自该模型的合同，不应从另一个模型复制：

~~~bash
atc \
  --model=models/resnet18_scene.onnx \
  --framework=5 \
  --output=models/resnet18_scene \
  --soc_version=Ascend310B4 \
  --input_format=NCHW \
  --input_shape=input:1,3,224,224
~~~

各案例的实际转换入口如下；脚本可能还会写入日志、manifest 或中间目录，应先阅读 --help：

| 案例 | 入口 |
| --- | --- |
| Case 1 | python scripts/prepare_models.py，按 download-only、convert-only 或 force 选择阶段 |
| Case 2 | python scripts/convert_onnx_to_om.py --soc-version Ascend310B4 |
| Case 3 | `convert_onnx_to_om.sh`；批量流程由 README 中的编排脚本执行 |
| Case 4 | python -m tools.export.prepare_models check --model all；候选模型先经过 manifest 准入 |
| Case 5 | 模型准备与验证模块（完整模块命令见下文）；RTL-IQ 使用对应的准备/验证入口 |
| Case 6 | python3 prepare_models.py，等价 ATC 参数见上例 |
| Case 7 | `prepare_models.py` 的 download/export/check 阶段；板端候选转换和准入按准入文档执行 |
| Case 8 | SOC_VERSION=Ascend310B4 bash scripts/atc_convert.sh |
| Case 9 | bash scripts/provision_qwen25_kv102_board.sh check，当前主线使用已经核验的 ACL/OM 工件 |

转换完成后至少检查文件存在、字节数和摘要；摘要检查不能替代 OM 加载和数值烟测：

~~~bash
stat -c '%s %n' models/*.om
sha256sum models/example.om
bundle_root=/path/to/repro-bundle
cd "$bundle_root"
sha256sum -c SHA256SUMS.txt
~~~

SHA256SUMS.txt 只在生成该清单的 bundle 根目录中执行；各案例根目录没有这个文件时，不要凭空创建或照抄该命令。模型、ONNX、OM、数据集、模板和真实运行报告默认不提交到 Git。B4 与 B1 的工件、不同 NPU 算力等级的结果以及不同模型 ID 不得混合比较。

## 摄像头、USB 仪器与视频设备

### 摄像头枚举与 V4L2

~~~bash
ls -l /dev/video*
v4l2-ctl --device=/dev/video0 --list-formats-ext
groups
~~~

若当前用户没有视频设备权限，可在确认设备归属后由管理员执行：

~~~bash
sudo usermod -aG video "$USER"
~~~

重新登录后再验证组成员关系。不要用 sudo 启动整个 Python 服务来绕过权限，否则会改变数据目录所有权和环境解析。

Case 2 的检测和跟踪入口可使用摄像头或视频文件：

~~~bash
python scripts/detection_app.py --device npu --source 0
python scripts/tracking_app.py --device npu --source demo/vtest.avi \
  --track-classes person,bus --no-display --save
~~~

--source 0 的含义由 V4L2 枚举顺序决定；在板端记录实际设备节点、分辨率、帧率和是否使用 MJPEG。CPU 模式只能作为离线基线，不能冒充 NPU 结果。

### USB 采集仪器

Case 5 的 sigrok、RTL-SDR 和 USB 桥接程序要求设备独占：启动前关闭 PulseView、sigrok、GQRX、GNU Radio、SDR++ 等可能占用同一接口的程序。

~~~bash
lsusb
command -v rtl_sdr
rtl_test -t
python -m time_frequency_dashboard.acquisition.usb_diagnostics
usb_node="/dev/bus/usb/001/002"
test -e "$usb_node"
fuser -v "$usb_node"
bash scripts/build_sigrok_capture_bridge.sh
~~~

rtl_test -t 只检查设备能否打开；run_rtl_sdr_npu_demo.sh --source tone 是软件演示，不能当作真实天线采集证据。真实运行应保存采集来源、采样率、批次、模型和报告路径。
完成模型准备和验证后，再按 Case 5 的命令启动仪表盘；采集设备诊断本身不等于 NPU 推理验收。

## ALSA、PulseAudio、MIDI 与蓝牙

### ALSA 设备

~~~bash
cat /proc/asound/cards
cat /proc/asound/seq/clients
aplay -l
aplay -L
arecord -l
ls -l /dev/snd
amixer
speaker-test -t sine -f 440
~~~

aplay 播放、arecord 录音，amixer 查询或修改 ALSA 混音器；设备编号和名称必须以当前板端输出为准。播放测试会改变音量或产生声音，开始前确认扬声器、耳机和实验环境。

Case 3 的实时链还会调用 PulseAudio 的 paplay 播放 WAV/PCM。它与直接访问 ALSA 的 aplay 不是同一层：

~~~bash
paplay --list-sinks
sink_name="replace-with-pulse-sink-name"
test -n "$sink_name"
paplay --device="$sink_name" output.wav
~~~

pactl 管理 PulseAudio 的 sink、source 和 profile：

~~~bash
pactl list short cards
pactl list short sinks
pactl list short sources
pactl get-default-sink
pactl get-default-source
sink_name="replace-with-pulse-sink-name"
card_name="replace-with-pulse-card-name"
pactl set-default-sink "$sink_name"
pactl set-card-profile "$card_name" a2dp_sink
~~~

不要把 Monitor of ... source 当作独立麦克风输入；它是扬声器回采，可能形成反馈环路。实时演奏应使用独立单音声源，验证输入帧、输出帧、有效 F0、特征/控制延迟和总延迟。蓝牙音箱和蓝牙耳机可以使用，但蓝牙编码与缓冲通常增加音频链路延迟；实时效果变差时，首先检查音频链路和 profile，而不是把它归因于 NPU 推理能力。

录放音示例：

~~~bash
arecord -D pulse -f S16_LE -r 48000 -c 2 -d 5 capture.wav
aplay -D pulse capture.wav
~~~

### MIDI

~~~bash
python realtime_ddsp.py --list-midi
python realtime_ddsp.py --list-audio
python tools/create_test_midi.py --output midi/ddsp-test.mid
python realtime_ddsp.py --play-midi midi/ddsp-test.mid \
  --device-id 0 --audio-device 0 --sample-rate 48000 \
  --prebuffer 6 --max-voices 1
~~~

MIDI 设备编号和音频设备编号不一定相同；每次测试都应保存枚举结果。--demo 或软件合成声源可用于链路调试，但不能证明外部键盘、摄像头或蓝牙设备已经满足实时验收。

### 桌面与触控辅助

触摸 kiosk 的历史步骤使用 xdotool、firefox --kiosk、onboard 或输入法工具。这些命令只在带桌面的板端维护会话中使用，并不属于无头服务的必需依赖：

~~~bash
command -v xdotool
xdotool --version
board_user="replace-with-board-user"
window_title="replace-with-window-title"
kiosk_port=7860
DISPLAY=:0 XAUTHORITY="/home/$board_user/.Xauthority" xdotool search --name "$window_title"
firefox --kiosk "http://127.0.0.1:$kiosk_port/"
~~~

不要在没有确认 DISPLAY、XAUTHORITY 和窗口目标时执行点击脚本；避免把固定 IP 或真实用户照片写入自动化脚本。

## 网络、SSH、文件传输与 HTTP

### 网络状态

~~~bash
ip addr
ip route
ss -ltnp
nmcli connection show
~~~

ip addr 查看接口地址，ip route 查看路由，ss -ltnp 显示 TCP 监听进程。nmcli connection show 适合检查 NetworkManager 配置；ifconfig 在部分系统仍可用，但不应作为新脚本的唯一依赖。

### SSH 与定向同步

控制机连接板端时使用已配置的密钥和明确的用户、地址：

~~~bash
board_user="replace-with-board-user"
board_ip="replace-with-board-ip"
ssh_target="$board_user@$board_ip"
ssh "$ssh_target"
ssh "$ssh_target" 'hostname; uname -a; npu-smi info'
scp demo/vtest.avi "$ssh_target:Documents/Ascend310/samples/case2/demo/"
~~~

部署前先在远端创建带日期的隔离目录，再用定向 rsync：

~~~bash
release_id="case9-repro-$(date +%Y%m%d)"
ssh "$ssh_target" "mkdir -p \"\$HOME/Documents/releases/$release_id\""
rsync -a --protect-args \
  --exclude 'data/' --exclude 'models/' --exclude '*.om' \
  samples/case9/ \
  "$ssh_target:Documents/releases/$release_id/case9/"
~~~

默认不使用 --delete，这样不会删除远端已有模型、数据或报告。同步完成后在两端分别执行 wc -c、sha256sum 或 manifest 检查。若脚本提供 --remote-dir，只传入已经 realpath 验证过的部署目录；不要把家目录作为目标。

### HTTP 检查

curl 的成功状态只证明 HTTP 路由返回了预期状态，不能证明 NPU 推理成功。常见检查：

~~~bash
curl --fail --silent --show-error http://127.0.0.1:5000/api/health
curl --fail --silent http://127.0.0.1:7860/api/bootstrap
curl --fail --silent http://127.0.0.1:7860/api/candidates
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/video_feed
~~~

上传接口应使用项目定义的 multipart 字段，并只使用合成或获授权的测试图像：

~~~bash
curl --fail -F 'name=example' \
  -F 'image=@synthetic-face.jpg' \
  http://127.0.0.1:5000/api/users
curl --fail -F 'image=@synthetic-face.jpg' \
  http://127.0.0.1:5000/api/camera/capture
~~~

Case 9 的网关验收还会查询 /v1/models 和 chat completions；只有在服务启用鉴权后才传递 API token：

~~~bash
export GATEWAY_API_KEY="$(openssl rand -hex 24)"
gateway_port=7867
curl --fail -H "Authorization: Bearer $GATEWAY_API_KEY" \
  "http://127.0.0.1:$gateway_port/v1/models"
~~~

不要把 token、Cookie、真实 URL 或完整响应中的个人信息写入 Git、截图或教材。

## 服务进程、日志与有序停止

### 启动服务

示例服务都应绑定到明确的本地地址和端口。默认优先监听回环地址；只有在可信实验网络中才由操作者显式指定 0.0.0.0：

~~~bash
python app.py --host 127.0.0.1 --port 5000
python -m palmprint_workbench.api --host 127.0.0.1 --port 7860
bash scripts/run_smart_album_service.sh --root "$PWD" --host 127.0.0.1
~~~

0.0.0.0 会把服务暴露到所有网卡；--share 还可能创建公网隧道，只适合短时、无敏感数据的演示，不应作为生产启动参数。Case 6 旧脚本中的 --share 和 Case 9 归档脚本中的固定端口均属于历史示例，使用前必须核对当前脚本。Case 9 的 ACL 服务、网关和文本界面具有不同的环境边界，统一放在本附录的 Case 9 小节说明。

### 进程、端口和日志

~~~bash
pgrep -af 'uvicorn|fastapi|case9|smart_album'
ps -ef | grep -E 'python|uvicorn' | grep -v grep
ss -ltnp | grep -E ':5000|:7860|:8080'
tail -f logs/service.log
~~~

停止服务时只终止已经确认的 PID，并等待端口释放：

~~~bash
service_pid=12345
case "$service_pid" in
  ''|*[!0-9]*) printf 'invalid PID\n' >&2; exit 2 ;;
esac
test "$service_pid" -gt 1
ps -fp "$service_pid"
readlink -f "/proc/$service_pid/cwd"
# 确认上两行均属于目标服务后，才执行下一行。
kill -TERM "$service_pid"
for _ in $(seq 1 20); do
  kill -0 "$service_pid" 2>/dev/null || break
  sleep 1
done
~~~

不要使用 pkill python、killall python 或按模糊名称批量终止进程；板端可能同时运行多个实验。涉及 systemd 的系统诊断可用：

~~~bash
systemctl status ssh sshd systemd-logind --no-pager
journalctl -b -u ssh --no-pager | tail -n 80
dmesg -T | tail -n 200
~~~

若命令只用于定位故障，应保存输出和时间戳，而不是把系统日志全文复制到公开仓库。音频或 USB 故障可进一步检查：

~~~bash
lsmod | grep -E 'snd|usb_audio'
dmesg | grep -iE 'usb|snd|audio|alsa|edifier' | tail -n 80
~~~

## 各案例的最小 Linux 命令链

下面的链条是从各案例当前主线脚本提取的代表性最小顺序，并非逐字穷举。完整参数以对应 README、测试和脚本的 --help 为准；命令链只说明操作关系，不构成板端成功证据。板端代码块沿用前文已经定义的 load_cann 函数；若新开终端，先在同一终端重新定义该函数。

### Case 1：人脸考勤

开发机先做纯 Python 和前端检查：

~~~bash
cd samples/case1
python -m unittest discover -s tests -p 'test_layout.py'
python -m py_compile app.py face_attendance/*.py
cd frontend
npm ci
npm test
npm run build
cd ..
~~~

板端在同一 shell 加载 CANN 后准备模型和运行时：

~~~bash
case_root="$HOME/Documents/Ascend310/samples/case1"
test -d "$case_root"
cd "$case_root"
load_conda
conda activate base
load_cann
python -m pip install -r requirements.txt
python scripts/prepare_models.py
python scripts/check_onnx.py
python scripts/check_onnx_out.py
python -c 'import acl, cv2, fastapi, uvicorn; print("runtime imports: ok")'
~~~

终端 A 启动 FastAPI 服务：

~~~bash
case_root="$HOME/Documents/Ascend310/samples/case1"
test -d "$case_root"
cd "$case_root"
python app.py --host 127.0.0.1 --port 5000
~~~

终端 B 检查 /api/health、/api/users、/api/camera/capture 和 /video_feed。模型不可用时健康状态应为降级，推理接口返回明确的 503，不得生成随机特征。未知人脸自动登记是本案例保留的教学演示策略，不应在教材或部署脚本中表述为生产级身份认证。

~~~bash
curl --fail http://127.0.0.1:5000/api/health
curl --fail http://127.0.0.1:5000/api/users
curl --fail -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5000/video_feed
~~~

### Case 2：检测与跟踪

控制机下载演示视频并定向传输：

~~~bash
wget https://raw.githubusercontent.com/opencv/opencv/master/samples/data/vtest.avi \
  -O demo/vtest.avi
board_user="replace-with-board-user"
board_ip="replace-with-board-ip"
ssh_target="$board_user@$board_ip"
scp demo/vtest.avi "$ssh_target:Documents/Ascend310/samples/case2/demo/"
~~~

板端流程：

~~~bash
case_root="$HOME/Documents/Ascend310/samples/case2"
test -d "$case_root"
cd "$case_root"
load_conda
conda activate npu
load_cann
python scripts/download_models.py
python scripts/convert_onnx_to_om.py --soc-version Ascend310B4
~~~

摄像头运行时把 --source 换成已经枚举过的设备编号，并记录实际分辨率和帧率。20--26 FPS 等未经报告支持的数字不能直接写入实验结论。

转换完成后，检测和跟踪分别在前台终端运行：

~~~bash
case_root="$HOME/Documents/Ascend310/samples/case2"
test -d "$case_root"
cd "$case_root"
python scripts/detection_app.py --device npu --source demo/vtest.avi
~~~

~~~bash
case_root="$HOME/Documents/Ascend310/samples/case2"
test -d "$case_root"
cd "$case_root"
python scripts/tracking_app.py --device npu --source demo/vtest.avi \
  --track-classes person --no-display --save
~~~

### Case 3：智能电子琴

控制机执行 WebUI 单测、构建和固定版本下载：

~~~bash
case_root="$PWD/samples/case3"
cd "$case_root"
python -m pytest -q
cd webui
npm ci
npm run test
npm run build
npm run test:e2e
cd ..
python tools/download_model_release.py
~~~

板端在已有 CANN/base 环境中准备 Piano-DDSP 并检查运行时：

~~~bash
case_root="$HOME/Documents/case3"
test -d "$case_root"
cd "$case_root"
load_conda
conda activate base
load_cann
python -m pip install -r requirements.txt
python prepare_piano_ddsp_models.py --variant gru-unrolled --models gru_ir_96_64
python tools/check_webui_env.py
~~~

WebUI 是前台服务，应在终端 A 启动：

~~~bash
case_root="$HOME/Documents/case3"
test -d "$case_root"
cd "$case_root"
python scripts/run_webui.py
~~~

终端 B 再枚举音频/MIDI，并运行固定 MIDI 片段。以下 realtime_ddsp.py 命令是当前仓库保留的实时链路诊断入口；一次播放成功仍不能替代长稳或延迟报告：

~~~bash
case_root="$HOME/Documents/case3"
test -d "$case_root"
cd "$case_root"
python tools/create_test_midi.py --output midi/ddsp-test.mid
python realtime_ddsp.py --list-audio
python realtime_ddsp.py --list-midi
python realtime_ddsp.py --play-midi midi/ddsp-test.mid --device-id 0
~~~

需要板端转换或验证时使用项目脚本和已经核对的输入合同；不得把 npu-smi 或一次播放成功当作实时延迟证明。paplay、pactl 和蓝牙 profile 的检查见本附录前文。

### Case 4：掌纹识别工作台

开发机执行语法、合同、前端和离线基线检查：

~~~bash
cd samples/case4
python -m compileall -q app.py palmprint_workbench tools
python -m pytest -ra tests
cd frontend
npm ci
npm test
npm run build
cd ..
python -m tools.board.verify_frontend_assets --dist frontend/dist --strict
python -m palmprint_workbench.tools.verify_assets --strict
~~~

板端启动前验证 CANN、registry 和资源：

~~~bash
case_root="$HOME/Documents/palmprint-recognition"
test -d "$case_root"
cd "$case_root"
load_conda
conda activate base
load_cann
python -c 'import sys, acl; print(sys.executable)'
python -m palmprint_workbench.tools.verify_assets --strict
~~~

服务在终端 A 启动，终端 B 再做 HTTP 检查：

~~~bash
case_root="$HOME/Documents/palmprint-recognition"
test -d "$case_root"
cd "$case_root"
python -m palmprint_workbench.api --host 127.0.0.1 --port 7860
~~~

~~~bash
curl --fail http://127.0.0.1:7860/api/health
curl --fail http://127.0.0.1:7860/api/bootstrap
curl --fail http://127.0.0.1:7860/api/candidates
~~~

同步发布目录时使用显式 rsync 且不带 --delete。掌纹图像和模板只留在隔离板端目录，find 预览后才能清理；不要把 rm -rf 示例当作常规部署步骤。

### Case 5：数据采集仪

在板端安装并检查 USB 采集依赖，然后构建桥接程序：

~~~bash
case_root="$HOME/Documents/case5"
test -d "$case_root"
cd "$case_root"
sudo apt-get update
sudo apt-get install -y libsigrok-dev sigrok-cli gcc pkg-config libfftw3-single3 rtl-sdr
load_conda
conda activate base
load_cann
python -m pip install -r requirements-board.txt
python -m time_frequency_dashboard.acquisition.usb_diagnostics
bash scripts/build_sigrok_capture_bridge.sh
~~~

模型和验证链：

~~~bash
case_root="$HOME/Documents/case5"
test -d "$case_root"
cd "$case_root"
load_conda
conda activate base
load_cann
python -m time_frequency_dashboard.model.prepare_models
python -m time_frequency_dashboard.model.verify_npu_model
python -m time_frequency_dashboard.model.prepare_rtl_iq_model
python -m time_frequency_dashboard.model.verify_rtl_iq_model
bash scripts/run_rtl_sdr_npu_demo.sh --source tone --batches 2
~~~

仪表盘是前台 Qt 程序，应在准备完成后另开终端启动：

~~~bash
case_root="$HOME/Documents/case5"
cd "$case_root"
bash scripts/run_dashboard.sh --sigrok-bridge build/sigrok_capture_bridge
~~~

真实 RTL-SDR 推理也应单独占用一个终端，并在结束后复核同一次运行报告：

~~~bash
case_root="$HOME/Documents/case5"
cd "$case_root"
manifest="models/generated/inference/candidates/replace-with-accepted-manifest.json"
test -r "$manifest"
bash scripts/run_rtl_sdr_npu_inference.sh \
  --source rtl \
  --manifest "$manifest" \
  --duration-seconds 10
run_dir="data/rtl_sdr_npu_inference/replace-with-run"
test -r "$run_dir/inference.jsonl"
python -m time_frequency_dashboard.rtl_sdr_run_report \
  --inference-jsonl "$run_dir/inference.jsonl" \
  --output "$run_dir/qc_summary.json"
~~~

真实硬件测试显式打开后才运行：

~~~bash
case_root="$HOME/Documents/case5"
test -d "$case_root"
cd "$case_root"
CASE5_RUN_HARDWARE_TESTS=1 python -m pytest -q tests/test_hardware_capture_and_inference.py
~~~

纯 Python 检查可用以下命令：

~~~bash
python -m pytest -q
python -m compileall -q time_frequency_dashboard
~~~

演示音调、CPU FFT、rtl_test -t 和接口连通性不能替代真实采集、NPU 推理和吞吐报告。上述 replace-with-... 变量必须替换为实际且已核验的 manifest 或运行目录；若文件不存在，test -r 会让流程安全停止。

### Case 6：智能小车

setup.sh 会尝试执行系统包安装、pip 安装和模型准备；使用前审阅脚本，在控制机先导出 ONNX：

~~~bash
cd samples/case6
python3 prepare_models.py --onnx-only
~~~

板端只执行已有 CANN 环境中的转换和服务：

~~~bash
case_root="$HOME/Documents/Ascend310/samples/case6"
test -d "$case_root"
cd "$case_root"
load_conda
conda activate base
load_cann
python3 prepare_models.py
~~~

Case 6 的 app.py 是 Gradio 程序，当前实现没有 /health 路由，且内部固定以 0.0.0.0 监听；服务启动后应在另一个终端检查根页面：

~~~bash
case_root="$HOME/Documents/Ascend310/samples/case6"
cd "$case_root"
python3 app.py --port 8080
~~~

~~~bash
curl --fail -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/
~~~

--share 会建立外部访问通道，只能在无敏感数据的临时演示中显式使用。当前程序在没有可用 OM 时可能使用 CPU 分类回退；网页可达不等于 NPU 推理、GPIO、串口或急停策略已经验收。运动控制输出必须在确认硬件安全边界后单独测试。

### Case 7：智能相册

开发机先做 Python、JavaScript 和布局检查：

~~~bash
cd samples/case7
python -m unittest discover -s tests -v
python -m py_compile app.py server_config.py photo_index.py smart_selector.py
node --check web/app.js
git diff --check
~~~

控制机先做发布范围预览，再应用发布：

~~~bash
board_user="replace-with-board-user"
board_ip="replace-with-board-ip"
ssh_target="$board_user@$board_ip"
bash scripts/deploy_ascend8t.sh --ssh-target "$ssh_target"
# 阅读上一步输出，确认 release、源文件和远端目录后再执行：
bash scripts/deploy_ascend8t.sh --ssh-target "$ssh_target" --apply
~~~

模型下载、导出和检查仍在控制机的 Case 7 根目录完成：

~~~bash
cd samples/case7
export HF_ENDPOINT=https://hf-mirror.com
python prepare_models.py download --model all --hf-endpoint "$HF_ENDPOINT"
python prepare_models.py export --model all
python prepare_models.py check --model all
~~~

板端另开 SSH 会话启动当前发布版本：

~~~bash
case_root="$HOME/Documents/ai-album/current"
test -d "$case_root"
cd "$case_root"
load_conda
conda activate base
load_cann
bash setup.sh board
bash scripts/run_smart_album_service.sh --root "$case_root" --host 127.0.0.1 --port 7860
~~~

服务保持运行时，在第二个终端检查接口：

~~~bash
curl --fail http://127.0.0.1:7860/api/health
curl --fail http://127.0.0.1:7860/api/models
curl --fail http://127.0.0.1:7860/api/index/stats
~~~

触控相框脚本应指定显式照片来源和设备 URL；不得扫描整个局域网。模型下载、导出、COCO 评测和候选转换要分别保存 manifest 与报告；Recall 或延迟只有在对应数据集和板端报告存在时才可引用。

### Case 8：手势识别

控制机导出并检查 ONNX，再定向同步模型伴随文件：

~~~bash
case_root="$PWD/samples/case8"
cd "$case_root"
weights="$case_root/weights/YOLOv10n_gestures.pt"
onnx_file="$case_root/models/YOLOv10n_gestures.onnx"
labels_file="$case_root/models/YOLOv10n_gestures_labels.txt"
metadata_file="$case_root/models/YOLOv10n_gestures_metadata.json"
test -r "$weights"
python weights/export_yolo_to_onnx.py \
  --weights "$weights" --output-dir models \
  --imgsz 640 --batch 1 --opset 13 --device cpu
test -r "$onnx_file"
test -r "$labels_file"
test -r "$metadata_file"
board_user="replace-with-board-user"
board_ip="replace-with-board-ip"
ssh_target="$board_user@$board_ip"
rsync -av "$onnx_file" "$labels_file" "$metadata_file" \
  "$ssh_target:Documents/Ascend310/samples/case8/models/"
~~~

板端转换只在已准备好的 CANN 和 npu 环境中执行：

~~~bash
case_root="$HOME/Documents/Ascend310/samples/case8"
test -d "$case_root"
cd "$case_root"
load_conda
conda activate npu
load_cann
python -m pip install -r requirements.txt
SOC_VERSION=Ascend310B4 bash scripts/atc_convert.sh
~~~

先用 ls /dev/video* 和 v4l2-ctl --list-formats-ext 确认摄像头，再逐项运行前台推理程序：

~~~bash
case_root="$HOME/Documents/Ascend310/samples/case8"
test -d "$case_root"
cd "$case_root"
python scripts/infer_om_camera.py \
  --model models/YOLOv10n_gestures.om \
  --benchmark-runs 20 --print-model-info
~~~

~~~bash
case_root="$HOME/Documents/Ascend310/samples/case8"
test -d "$case_root"
cd "$case_root"
python scripts/infer_om_camera.py \
  --model models/YOLOv10n_gestures.om \
  --source /dev/video0 --no-window --max-frames 60
~~~

WebRTC 服务另开终端启动。使用 `--strict-port` 固定端口，避免端口被占用时脚本自动改用其他端口；保持运行时再从第二个终端访问健康路由：

~~~bash
case_root="$HOME/Documents/Ascend310/samples/case8"
test -d "$case_root"
cd "$case_root"
python scripts/webrtc_om_app.py \
  --model models/YOLOv10n_gestures.om \
  --source /dev/video0 --host 0.0.0.0 --port 8081 --strict-port
~~~

~~~bash
curl --fail http://127.0.0.1:8081/health
~~~

WebRTC 的网络、编码和 OM 推理耗时应分开记录；不能用 CPU 编码结果替代板端端到端证据。0.0.0.0 会向所有网卡开放服务，只有在可信实验网络中才使用；不需要远程浏览器时应改为 127.0.0.1。

### Case 9：小智 RAG 网关

当前候选部署根由 Case 9 的发布包决定。下面的默认值对应板端验收目录；迁移到其他目录时只需设置 `CASE9_ROOT`，并在同一终端保持该变量一致。ACL 服务、网关和文字界面必须分终端启动；其中 ACL 使用 case9-acl-om，网关和文字界面由各自包装器激活 case9-local-chat。

~~~bash
case9_root="${CASE9_ROOT:-$HOME/case9-qwen25-dual-acceptance-20260827}"
test -d "$case9_root"
cd "$case9_root"
load_conda
conda activate case9-acl-om
load_cann
export PYTHONNOUSERSITE=1
export CASE9_QWEN25_KV_ROOT="$case9_root"
export CASE9_QWEN25_KV_BOARD_ID="$(hostname)"
export CASE9_QWEN25_KV_SOC_VERSION="Ascend310B4"
export CASE9_QWEN25_KV_OUTPUT_ROOT="$case9_root/reports/$(date -u +%Y%m%dT%H%M%SZ)"
export PYTHONPATH="$case9_root${PYTHONPATH:+:$PYTHONPATH}"
bash scripts/provision_qwen25_kv102_board.sh check
bash scripts/provision_qwen25_kv102_board.sh inspect
bash scripts/provision_qwen25_kv102_board.sh smoke
~~~

门禁成功后，在终端 A 启动候选 ACL 服务。服务是前台进程，因此不要把后续启动命令接在同一代码块中：

~~~bash
case9_root="${CASE9_ROOT:-$HOME/case9-qwen25-dual-acceptance-20260827}"
cd "$case9_root"
export QWEN25_ROOT="$case9_root"
export QWEN25_KV_OM="$case9_root/artifacts/qwen25-static-kv-1024-v2.om"
export QWEN25_KV_CONTRACT="$case9_root/contracts/qwen25-static-kv-1024-v2-om-contract.json"
export QWEN25_KV_TOKENIZER="$case9_root/artifacts/tokenizer.json"
export QWEN25_KV_TOKENIZER_CONFIG="$case9_root/artifacts/tokenizer_config.json"
export QWEN25_KV_LOCK="$QWEN25_KV_OM.lock.json"
export QWEN25_KV_TOKENIZER_LOCK="$QWEN25_KV_TOKENIZER.lock.json"
export QWEN25_KV_MAX_TOKENS=80
bash scripts/run_qwen25_kv_acl_service.sh
~~~

终端 B 查询候选 ACL 服务：

~~~bash
curl --fail http://127.0.0.1:8084/health
curl --fail http://127.0.0.1:8084/v1/models
~~~

ACL、JSON/SSE、长输出和资源门通过后，在终端 C 生成临时 token 并启动网关。包装器会使用 case9-local-chat：

~~~bash
case9_root="${CASE9_ROOT:-$HOME/case9-qwen25-dual-acceptance-20260827}"
cd "$case9_root"
export GATEWAY_API_KEY="$(openssl rand -hex 24)"
export CASE9_GATEWAY_CONDA_ENV=case9-local-chat
bash scripts/run_qwen25_kv102_gateway.sh
~~~

终端 D 使用同一 token 启动文字界面；该界面固定暴露 0.0.0.0:7868，只能在可信实验网络中使用：

~~~bash
case9_root="${CASE9_ROOT:-$HOME/case9-qwen25-dual-acceptance-20260827}"
cd "$case9_root"
gateway_token="replace-with-token-from-terminal-C"
export GATEWAY_API_KEY="$gateway_token"
export TEXT_CHAT_CONDA_ENV=case9-local-chat
bash scripts/run_qwen25_kv102_text_chat.sh
~~~

网关保持运行时，在另一个终端使用 Bearer token 验证模型和一次非流式请求：

~~~bash
gateway_port=7867
gateway_token="replace-with-token-from-terminal-C"
export GATEWAY_API_KEY="$gateway_token"
curl --fail -H "Authorization: Bearer $GATEWAY_API_KEY" \
  "http://127.0.0.1:$gateway_port/v1/models"
curl --fail -H "Authorization: Bearer $GATEWAY_API_KEY" \
  -H 'Content-Type: application/json' \
  --data '{"model":"case9-rag","messages":[{"role":"user","content":"请用一句话说明检索网关已启动。"}],"stream":false}' \
  "http://127.0.0.1:$gateway_port/v1/chat/completions"
~~~

复现包同步脚本使用 ssh、rsync -aL --partial --append-verify、字节数和 sha256sum 核对；sha256sum -c SHA256SUMS.txt 只在生成该清单的复现包根目录执行。归档中的 TinyLlama、本地聊天和旧端口脚本只作历史参考，不应与当前 Qwen/ACL/OM 链混用。

## 测试、证据与教材构建

### Python 与前端检查

~~~bash
python -m py_compile app.py
python -m compileall -q package_or_directory
python -m pytest -q
python -m unittest discover -s tests -v
git diff --check
~~~

前端项目按各案例的锁文件执行：

~~~bash
npm ci
npm test
npm run build
npm run test:e2e
~~~

npm run test:e2e 的真实板端用例必须显式设置项目要求的环境变量；普通 E2E 应使用 fake API，不应在开发机隐式访问摄像头、NPU 或真实个人数据。

### 结果记录

每份报告至少应包含：硬件型号和算力层级、CANN 版本、模型 ID/精度、输入合同、预热次数、循环次数、重复次数、百分位计算方式、输入来源、输出路径和原始日志。语法通过、HTTP 200、模型摘要一致、ACL 数值烟测、任务精度、性能和 UI 烟测分别记录，不能互相替代。

### VuePress 与 PDF

在仓库根目录执行站点构建时使用锁文件：

~~~bash
pnpm install --frozen-lockfile
pnpm docs:build
~~~

检查构建产物中的内部链接、图片引用、sitemap、robots 和控制台错误。用户明确要求重新生成书稿时，再运行：

~~~bash
./convert-vuepress.sh
pdfinfo latex/book.pdf
pdftotext latex/book.pdf /tmp/ascend310-book.txt
rg -n 'LaTeX Error|Undefined control sequence|Fatal error|Missing character|Overfull \hbox' latex/book.log
~~~

转换脚本会从 src/appendix/appendix[0-9]*.md 按数字顺序收集附录；不要手改 latex/appendices/*.tex。PDF 页面还应渲染为 PNG 检查图片清晰度、代码块分页、表格宽度、中文字体和页眉页脚；发现问题时修改 Markdown 或模板后重新生成。

## 常见故障的诊断顺序

| 现象 | 先执行 | 再判断 |
| --- | --- | --- |
| import acl 失败 | command -v python；source set_env.sh；python -c "import acl" | 解释器和 CANN 是否来自同一环境；不要加 CPU 回退 |
| ATC 找不到算子 | atc --version；检查 --framework、--soc_version、输入形状和日志 | 保存失败命令和模型合同，不生成伪 OM |
| HTTP 健康正常但推理失败 | curl /api/health；查看服务日志；执行 ACL smoke | HTTP 路由与 NPU 推理是两个验证门 |
| 摄像头无图像 | ls /dev/video*；v4l2-ctl --list-formats-ext；groups | 设备节点、格式、权限和实际尺寸是否匹配 |
| USB 仪器被占用 | lsusb；确认设备节点后执行 fuser -v /dev/bus/usb/... | 关闭占用程序，不用宽泛 kill |
| 无声音或延迟大 | aplay -l；pactl list short sinks；pactl get-default-sink | ALSA/PulseAudio profile、蓝牙缓冲和实际 sink |
| 端口仍被占用 | ss -ltnp；ps -fp PID | 只停止确认的服务 PID |
| 磁盘不足 | df -h；du -sh target-dir | 先归档报告，再清理隔离临时目录 |
| 前端空白 | test -f frontend/dist/index.html；find frontend/dist -maxdepth 2 -type f | 构建产物、静态路径和浏览器缓存 |
| 摘要不一致 | sha256sum；sha256sum -c；stat | 停止部署，重新取得并核验资产 |

必要时收集更完整的系统信息：

~~~bash
uname -a
date --iso-8601=seconds
npu-smi info
dmesg -T | tail -n 200
~~~

报告中只保留与故障相关的行，并删除用户名、IP、token、真实图像路径等隐私信息。

## 不应直接执行的命令模式

以下模式在案例文档或网络帖子中可能出现，但不应未经审查直接执行：

- rm -rf 指向家目录、仓库根目录、变量未验证的路径或正在运行的部署目录。
- rsync --delete 同步到未确认的远端目录；它可能删除模型、数据和报告。
- pkill python、killall 或模糊 kill；它们可能终止其他案例和系统服务。
- sudo pip install、sudo python；它们会绕过 conda 并改变系统包所有权。
- 在开发机运行 CANN、ATC、PyACL、OM、摄像头或 NPU 命令，并把失败归因于代码。
- 启动 0.0.0.0、--share、未鉴权网关或固定公网地址后再处理个人数据。
- 把扬声器 monitor、软件 tone、CPU FFT、HTTP 200 或 npu-smi 摘要写成完整硬件验收。
- 未确认路径就复制真实照片、掌纹模板、数据库、模型或 API token 到 Git、截图或公开报告。

安全的操作顺序是：确认机器和环境，解析绝对路径，预览输入与目标，执行最小范围命令，保存原始输出，最后再把经过审查的结论写入案例报告。这样才能让 Linux 命令成为可复现的实验步骤，而不是无法追溯的部署记账。
