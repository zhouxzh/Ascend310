---
title: "附录 2：基于昇腾 310B 的 Ubuntu 教程"
author: [周贤中]
subject: "Markdown"
keywords: [昇腾310B, Ubuntu, Linux, SSH, VNC, NFS, CANN, PyACL]
lang: zh-cn
---

# 附录 2：基于昇腾 310B 的 Ubuntu 教程

本附录把 Ubuntu 基础操作、远程访问、网络配置和昇腾 310B 板端命令整理为一条可复用的学习路径。它面向三类位置：负责编辑代码和文档的**开发机**、负责连接网络和发起远程操作的 **Ubuntu 主机**、真正运行 CANN、PyACL、OM 推理和摄像头程序的 **310B 板端**。同一个命令在不同位置执行，得到的结果和证明范围不同，因此每一步都要先确认命令在哪台机器、哪个用户、哪个 shell 和哪个软件环境中运行。

本附录的命令来自 Ubuntu 基础教程中的文件结构、终端编辑器、SSH、VNC、NFS、Docker、热点与 WiFi、开机自启动、静态 IP 和无屏幕联网资料，并结合现有 310B 材料重新编排。外部资料中的 ROS 小车、树莓派、RDK X5、Orin 等名称只作为操作背景出现于命令示例的原始路径中，不能据此推断 310B 板的实际设备名、IP 地址、接口编号或运行结果。

## 1. 教程定位与证据边界

### 1.1 三类操作位置

| 位置 | 用途 | 能做什么 | 不能据此推断什么 |
| --- | --- | --- | --- |
| 开发机 | 编辑正文、样例代码和配置文件 | 文本编辑、Python 语法检查、纯 Python 测试、前端构建、静态文件检查 | 不能推断板端 CANN、PyACL、OM、摄像头、音频和 NPU 结果 |
| Ubuntu 主机 | 作为 SSH、VNC、NFS、SCP 和 rsync 的客户端 | 发起远程连接、挂载共享目录、传输文件、运行普通 Ubuntu 命令 | 不能因为客户端命令成功就认定板端推理或硬件测试成功 |
| 310B 板端 | 运行目标 Ubuntu 系统与昇腾运行时 | 加载 CANN、执行 ATC、导入 acl、运行 OM、检查设备、服务和日志 | 不能把一次导入成功写成完整模型精度或端到端性能 |

开发机和 Ubuntu 主机可以共享普通 Linux 操作，例如查看路径、编辑文本、管理网络和传输文件。板端操作则需要额外的证据控制：`source /usr/local/Ascend/ascend-toolkit/set_env.sh`、`npu-smi info`、`atc`、`python -c 'import acl'`、OM 加载、V4L2 摄像头访问和真实 NPU 推理都应在 310B 板上执行。

### 1.2 板端专属操作

以下操作默认只能在已经安装好驱动、CANN 和目标 Python 环境的 310B 板端执行：

~~~bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
command -v atc
python -c 'import acl; print("PyACL import: ok")'
npu-smi info
ls -l /dev/video*
v4l2-ctl --device=/dev/video0 --list-formats-ext
~~~

`command -v atc` 检查实际调用的 ATC 路径，`import acl` 检查当前 Python 是否能够加载 PyACL，`npu-smi info` 读取 NPU 设备摘要。三者属于不同的证据层：ATC 存在不代表转换成功，PyACL 可导入不代表模型能运行，`npu-smi` 能输出也不代表某个 OM 已经通过数值和性能验证。

### 1.3 证据状态

记录结果时，使用下面五类标签，避免把先验知识写成已经观测到的事实：

| 标签 | 含义 |
| --- | --- |
| `documented` | 官方文档、厂商文档或教程中明确写出的能力 |
| `inferred` | 根据已有资料推断出的可能路径，尚未在目标板上验证 |
| `observed-pass` | 在当前板端、当前环境和当前命令下实际通过 |
| `observed-fail` | 在当前组合下实际失败，并保留了完整错误和命令 |
| `untested` | 尚未执行，不能归入支持或不支持 |

一条命令的通过只能证明它对应的最小验证门。语法检查、HTTP 健康检查、ATC 转换、ACL 数值烟测、任务精度、性能测试和界面烟测必须分别记录。没有执行的板端测试不能写成“已验证”，没有采集到的日志不能补写。

### 1.4 执行命令前的四个问题

执行任何会改变文件、网络、用户、软件包或设备状态的命令前，先回答：

1. 命令在哪台机器、哪个 shell 和哪个 conda 环境中运行？
2. 输入文件、输出目录、日志和远端路径是否已经解析为明确的绝对路径？
3. 这一步证明的是语法、路径、网络、转换、数值一致性、性能，还是硬件现象？
4. 命令是否安装软件、修改权限、覆盖文件、删除数据或暴露网络服务？

四个问题没有答案时，先执行只读检查，不要直接执行写入命令。

## 2. Ubuntu 与 Linux 基础

### 2.1 终端、用户和提示符

Ubuntu 的图形界面之外，终端是与 Linux 交互的主要入口。提示符 `$` 通常表示普通用户，`#` 通常表示 root 用户。进入 root 状态以后，所有命令的权限都会提高，误操作的后果也会放大。

~~~bash
whoami
id
hostname
pwd
printf 'shell=%s\n' "$SHELL"
~~~

`whoami` 显示当前用户名，`id` 同时显示用户、组和补充组，`hostname` 显示主机名，`pwd` 显示当前目录，`$SHELL` 显示当前 shell 路径。远程登录后先确认这些信息，避免把命令发到另一台同名设备。

退出临时 root 状态或当前 shell 可使用：

~~~bash
exit
~~~

也可以按 `Ctrl+D` 发送文件结束符。不要为了省事长期保留 root 终端，更不要在 root 终端中编辑项目文件。

### 2.2 第一条环境检查命令

~~~bash
uname -a
cat /etc/os-release
date --iso-8601=seconds
ip addr
ip route
~~~

`uname -a` 查看内核和架构，`/etc/os-release` 查看 Ubuntu 版本，`date` 生成带时区含义的时间戳，`ip addr` 查看接口地址，`ip route` 查看默认路由。把这几条命令的输出和后续测试放在同一份记录中，才能说明测试环境。

### 2.3 可审计 shell

脚本或排障会话可以使用严格模式：

~~~bash
set -euo pipefail
pwd
whoami
hostname
printf 'shell=%s\n' "$SHELL"
~~~

`set -e` 在未处理的命令失败后停止脚本，`-u` 让未定义变量直接报错，`pipefail` 保留管道前段的失败状态。某个命令允许失败时，应显式处理返回值，不能为了继续执行而全局关闭严格模式。

交互式排查时可以先只运行环境检查命令。脚本中如果必须临时关闭 `-u` 来加载厂商环境脚本，应在加载完成后恢复原状态：

~~~bash
load_cann() {
  if [[ $- == *u* ]]; then
    set +u
    source /usr/local/Ascend/ascend-toolkit/set_env.sh
    set -u
  else
    source /usr/local/Ascend/ascend-toolkit/set_env.sh
  fi
}
~~~

### 2.4 命令权限与执行位置

同一句命令在开发机、Ubuntu 客户端和板端的结果可能不同。使用 `command -v` 查看实际可执行文件：

~~~bash
command -v python
command -v python3
command -v atc || true
command -v npu-smi || true
~~~

`|| true` 只适合让检查流程继续，不代表后面的命令可以跳过环境问题。若 `atc` 或 `npu-smi` 不存在，应记录当前机器和当前环境，而不是修改代码来掩盖缺失的板端依赖。

## 3. 文件系统、路径与常用命令

### 3.1 Ubuntu 目录结构

| 目录 | 主要用途 |
| --- | --- |
| `/bin` | 重要的二进制应用程序 |
| `/boot` | 启动配置文件和内核相关文件 |
| `/dev` | 设备文件，以文件形式表示设备和接口 |
| `/etc` | 系统配置文件、启动脚本和服务配置 |
| `/home` | 普通用户主目录 |
| `/lib` | 系统库文件 |
| `/mnt` | 临时挂载文件系统的位置 |
| `/opt` | 可选应用程序安装目录 |
| `/usr/local` | 用户自行安装的软件和运行环境 |

`/dev` 中的文件不是普通文档。`/dev/video0`、`/dev/snd` 和 `/dev/bus/usb` 对应真实设备，访问它们会改变设备状态或与硬件交互。`/etc` 下的文件通常需要 root 权限，修改前应备份并确认目标主机。

### 3.2 绝对路径、相对路径和工作目录

绝对路径从 `/` 开始，例如 `/home/<user>/Documents/ascend310`。相对路径从当前工作目录开始，例如 `models/example.om`。使用 `..` 返回上一层：

~~~bash
cd ~/Documents
pwd
cd ..
pwd
cd -
pwd
~~~

`cd` 不带参数时返回用户主目录，`cd -` 返回上一个工作目录。进入服务、转换或同步命令之前，先执行 `pwd`，确认没有在错误目录中运行。

解析实际路径：

~~~bash
realpath .
realpath models/example.om
readlink -f frontend/dist/index.html
~~~

`realpath` 和 `readlink -f` 适合在复制、删除、挂载和部署前确认符号链接真实指向。路径中包含空格时，变量和参数应使用双引号：

~~~bash
target_dir="$HOME/Documents/board assets"
mkdir -p "$target_dir"
cp -a config.example.yaml "$target_dir/"
~~~

### 3.3 创建、查看和复制

~~~bash
mkdir -p reports/board/$(date --iso-8601=date)
touch notes.txt
ls -la
ls -lh models
cp -a config.example.yaml config.yaml
cp -r templates templates-backup
mv old-name.txt new-name.txt
~~~

`mkdir -p` 创建多级目录，`cp -a` 尽量保留文件属性，`cp -r` 复制目录，`mv` 同时用于移动和重命名。覆盖已有文件之前，先用 `ls`、`test -e` 或 `stat` 检查目标：

~~~bash
test -e config.yaml && echo 'target exists'
stat -c '%A %U:%G %s %n' config.yaml
file config.yaml
~~~

`stat` 显示权限、所有者、字节数和名称，`file` 只根据文件内容给出格式线索，不能证明模型可以由 ACL 加载。

### 3.4 查看文本和日志

~~~bash
head -n 40 service.log
tail -n 80 service.log
tail -f service.log
wc -l service.log
sed -n '1,80p' service.log
awk '{print NR ":" $0}' service.log | tail -n 20
grep -n 'ERROR\|WARN' service.log
~~~

`tail -f` 持续跟踪日志，适合前台排障；批量检查应使用 `head`、`tail`、`wc`、`sed` 和 `grep`，并把输出保存到报告。仓库内搜索可使用 `rg`：

~~~bash
rg -n 'atc|acl|sha256|127\.0\.0\.1|0\.0\.0\.0' README.md docs scripts
~~~

### 3.5 查找、容量和文件类型

~~~bash
find . -maxdepth 2 -type f -print | sort
du -sh models data reports
df -h .
lsblk
~~~

`find` 先用 `-maxdepth` 限制范围，避免扫描整个家目录或挂载盘；`du` 查看目录占用，`df` 查看文件系统剩余空间，两者含义不同。`lsblk` 只用于查看块设备树，不能未经确认就挂载或格式化设备。

### 3.6 归档和校验

~~~bash
run_date="$(date --iso-8601=date)"
tar -czf "reports/board/smoke-$run_date.tar.gz" reports/board/smoke.log
tar -tzf "reports/board/smoke-$run_date.tar.gz" | head
unzip -l artifact.zip
sha256sum models/example.om
~~~

压缩前先确认内容。不要把照片、生物特征模板、数据库、模型二进制、密钥和 token 放入公开归档。`sha256sum` 只能证明字节一致，不能证明模型正确、可加载或数值正确。

## 4. 用户、组、权限、sudo 与安全删除

### 4.1 Linux 用户和组

Ubuntu 安装时会创建一个普通用户，同时保留具有更高权限的 root 用户。普通用户在自己的主目录中通常不需要 `sudo`，访问 `/etc`、安装软件包或修改系统服务时才需要提权。

每个文件都有三组权限：

| 权限组 | 适用范围 |
| --- | --- |
| user | 文件所有者 |
| group | 文件所属组 |
| other | 其他用户 |

每组权限包含读 `r`、写 `w`、执行 `x`：

~~~bash
ls -l script.sh
stat -c '%A %a %U:%G %n' script.sh
id
groups
~~~

`ls -l` 的权限字符串分为三段。`rwxr-xr-x` 表示所有者可读写执行，组和其他用户可读可执行。`stat` 的 `%a` 给出八进制权限，`%U:%G` 给出所有者和组。

### 4.2 修改权限

给脚本增加所有者执行权限：

~~~bash
chmod u+x scripts/run_service.sh
bash -n scripts/run_service.sh
./scripts/run_service.sh --help
~~~

`chmod u+x` 只给文件所有者增加执行权限。若脚本需要其他用户执行，应先确认运行用户和目录权限，再选择精确的组权限或 ACL。不要对整个家目录、模型目录或系统目录执行 `chmod -R 777`。

修改所有者和组需要管理员权限：

~~~bash
sudo chown "$USER":"$USER" path/to/file
sudo chgrp developers path/to/directory
~~~

`chown -R` 和 `chgrp -R` 会递归改变整棵树，执行前必须用 `find` 预览目标，并确认没有系统文件和共享资产混在其中。

### 4.3 sudo

`sudo` 表示以超级用户权限执行单条命令：

~~~bash
sudo apt-get update
sudo systemctl status ssh --no-pager
sudo -i
~~~

`sudo -i` 进入 root 环境，`sudo su` 也可能加载 root 环境或保留部分当前环境。两者都不是日常编辑项目文件的默认方式。退出 root 状态使用：

~~~bash
exit
~~~

频繁使用系统管理命令时，应通过明确的 sudo 策略和组权限管理，而不是把普通用户长期改成 root。

### 4.4 设备组权限

摄像头和音频设备常通过 `video`、`audio` 等组授权：

~~~bash
groups
ls -l /dev/video* /dev/snd
sudo usermod -aG video "$USER"
~~~

执行 `usermod` 后需要重新登录，组变更才会进入新会话。不要用 `sudo python` 启动整个服务来绕开设备权限，因为这会改变服务的数据目录所有者、环境变量和日志归属。

### 4.5 安全删除

`rm` 不提供回收站。删除测试数据时，先把目标解析为绝对路径并验证：

~~~bash
target_dir="$(realpath -- "$PALMPRINT_ROOT/data/captures")"
case "$target_dir" in
  "$PALMPRINT_ROOT"/data/captures) ;;
  *) printf 'refuse to remove: %s\n' "$target_dir" >&2; exit 1 ;;
esac
find "$target_dir" -maxdepth 1 -type f -print
rm -f -- "$target_dir"/*.jpg
~~~

先运行 `find` 检查文件列表，再由操作者确认后执行删除。`rm -rf` 只应在脚本创建的隔离临时目录中使用，并且变量必须非空、路径必须已经解析。不要把变量为空、路径未验证的命令变成 `rm -rf "$HOME"`、`rm -rf .` 或对整个部署目录的递归删除。

## 5. nano 与 vim 编辑器

### 5.1 nano

打开文件：

~~~bash
nano config.yaml
sudo nano /etc/exports
~~~

nano 进入后可以直接编辑文本。保存时按 `Ctrl+O`，屏幕会显示文件名；不修改文件名就按回车。退出按 `Ctrl+X`。如果文件有未保存修改，nano 会询问是否保存，按屏幕提示选择。

编辑系统文件前先备份：

~~~bash
sudo cp /etc/apt/sources.list /etc/apt/sources.list.bak
sudo nano /etc/apt/sources.list
~~~

### 5.2 vim

打开文件：

~~~bash
vim config.yaml
sudo vim ~/.config/autostart/myprogram.desktop
~~~

vim 初始处于普通模式。按 `i` 或 `a` 进入插入模式，按 `Esc` 回到普通模式。常用移动和编辑键：

| 按键 | 作用 |
| --- | --- |
| `k` / `j` | 光标上移 / 下移 |
| `h` / `l` | 光标左移 / 右移 |
| `i` / `I` | 当前光标处插入 / 行首插入 |
| `a` / `A` | 光标后插入 / 行末插入 |
| `o` / `O` | 当前行后新建一行 / 当前行前新建一行 |
| `x` / `X` | 删除当前字符 / 删除前一个字符 |
| `dd` | 剪切整行，也可作为删除 |
| `dw` | 删除一个单词 |
| `d^` | 删除到行首 |
| `dG` / `d1G` | 删除到文档末尾 / 删除到文档开头 |
| `gg` / `Shift+g` | 移到第一行 / 最后一行 |
| `yy` / `p` / `P` | 复制整行 / 粘贴到光标后 / 粘贴到光标前 |

保存和退出前先按 `Esc` 回到普通模式，再输入冒号命令：

| 命令 | 作用 |
| --- | --- |
| `:q` | 退出 |
| `:q!` | 强制退出，不保存 |
| `:w` | 保存 |
| `:wq` | 保存并退出 |
| `:wq!` | 强制保存并退出 |
| `:w <文件路径>` | 另存为 |

编辑器中的 `:q!` 会丢失未保存内容，只在确认不需要修改时使用。需要编辑 root 文件时使用 `sudo nano` 或 `sudo vim`，不要把来源不明的 `tee` 保存技巧直接粘贴到终端。保存后使用 `head`、`grep` 或 `git diff` 检查文件内容。

## 6. APT、换源、Conda、pip 与 Python 路径

### 6.1 APT 软件包管理

APT 操作会改变系统状态。执行前记录 Ubuntu 版本和计划安装的包：

~~~bash
cat /etc/os-release
sudo apt-get update
sudo apt-get install -y package-name
sudo apt-get install -f
~~~

`apt-get update` 获取软件包列表，`install` 安装软件包，`install -f` 修复依赖。安装完成后记录命令、版本和输出：

~~~bash
dpkg -s python3-dev python3-pip 2>/dev/null | grep -E '^(Package|Status|Version):'
apt-cache policy package-name
~~~

不要为了通过文档示例而重复安装或升级 CANN 相关包。板端已经验证过的 CANN、驱动和 Python 环境应保持原样，变更前先建立回滚方案。

### 6.2 换源

Ubuntu 的软件源配置通常位于 `/etc/apt/sources.list`，部分新版本还会使用 `/etc/apt/sources.list.d/` 下的 `.sources` 文件。先备份，再根据 Ubuntu 版本替换为合适的国内镜像：

~~~bash
sudo cp -a /etc/apt/sources.list /etc/apt/sources.list.bak
sudo nano /etc/apt/sources.list
sudo apt-get update
~~~

换源时确认发行版代号与镜像仓库匹配。不要混用不同 Ubuntu 版本的仓库，不要把第三方 PPA 当作系统基础源。更新失败时先恢复备份并检查错误，不要把错误源继续保留。

### 6.3 Conda 环境

先加载 Conda shell 函数，再激活环境：

~~~bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
python --version
python -c 'import sys; print(sys.executable)'
~~~

若脚本开启了 `set -u`，而 Conda 或 CANN 环境脚本读取未定义变量，可以使用第 2 节的 `load_cann` 模式，只在加载厂商脚本时临时关闭 `-u`，加载完成后恢复。

创建独立环境：

~~~bash
conda create -n case9-acl-om python=3.9
conda activate case9-acl-om
python -c 'import sys; print(sys.executable)'
~~~

环境名称和 Python 版本必须来自项目的版本要求。不要用其他版本的同名环境替换已经验证的板端环境。

### 6.4 pip 与 Python 解释器

优先使用 `python -m pip`，让 pip 对应当前解释器：

~~~bash
python -m pip --version
python -m pip install -r requirements.txt
python -m pip install -r requirements-board.txt
python -m pip list
~~~

不要使用 `sudo pip install` 或 `sudo python`。前者会把包安装到系统目录并绕过 Conda，后者会改变脚本的解释器和文件所有权。安装失败时先记录 `python -m pip --version`、`which python`、`sys.executable` 和完整错误，再决定是否变更环境。

### 6.5 Python 路径和动态库路径

~~~bash
export PYTHONNOUSERSITE=1
export PYTHONPATH="$PWD:${PYTHONPATH:-}"
export LD_LIBRARY_PATH="/usr/local/Ascend/ascend-toolkit/latest/lib64:${LD_LIBRARY_PATH:-}"
python -c 'import sys; print("\n".join(sys.path))'
python -c 'import os; print(os.environ.get("LD_LIBRARY_PATH", ""))'
~~~

`PYTHONNOUSERSITE=1` 用于避免用户目录中的包遮蔽板端环境，但可能让实际启动环境中存在的用户站点包不可见。判断导入问题时，应比较隔离环境和真实启动环境的 `sys.executable` 与 `sys.path`。`PYTHONPATH` 和 `LD_LIBRARY_PATH` 只加入已经确认的项目目录或 CANN 目录。持久化修改应经过部署评审，不能临时写入 `/etc/profile` 后让所有用户和所有服务继承。

## 7. 网络、静态 IP、WiFi、热点与同网段检查

### 7.1 网络状态

~~~bash
ip addr
ip route
nmcli connection show
nmcli -t -f NAME connection show
ping -c 4 <target-ip>
~~~

`ip addr` 查看接口地址，`ip route` 查看默认路由和网段，`nmcli connection show` 查看 NetworkManager 连接配置。`ping` 成功只说明网络层可达，不代表 SSH、VNC、NFS 或 NPU 服务已经可用。

### 7.2 一般静态 IP 概念

Ubuntu 图形桌面可以通过 NetworkManager 配置静态 IP。打开连接编辑器：

~~~bash
nm-connection-editor
~~~

在对应连接中进入 `IPv4 Settings`，将方法设为 `Manual`，再填写地址、子网掩码、网关和 DNS。静态地址留在目标设备所在网段内，且不能与其他设备冲突。修改后重新启用该连接，再验证：

~~~bash
ip -4 addr show
ip route
ping -c 4 <同网段设备IP>
~~~

静态 IP 不等于端口已经开放。SSH 服务、VNC 共享开关、NFS 导出和防火墙都需要单独检查。不要在不确定目标网段和网关的情况下直接覆盖原配置，先在连接编辑器中记录原值，或保存一份配置备份。

### 7.3 同网段判断

IPv4 地址由网络地址和主机地址组成，子网掩码决定两部分的分界。以 `255.255.255.0` 为例，前 24 位是网络地址，后 8 位是主机地址。`192.168.1.10` 和 `192.168.1.20` 与同一子网掩码进行按位与运算后，网络地址都是 `192.168.1.0`，因此属于同一网段。`192.168.0.100` 与 `192.168.1.100` 在同一掩码下不属于同一网段。

排障时检查客户端和板端是否拿到同一网段的地址、是否连接到同一个热点或有线网络、网关是否一致。若客户端和板端存在多个网卡，应使用 `ip route get <目标IP>` 确认实际出口接口，不要只看某一个网卡的地址。

### 7.4 创建 WiFi 热点

图形界面通常从右上角网络图标进入 `Edit Connections`，点击加号，选择 `WiFi`，再点击 `Create`。命令行方式可以打开连接编辑器：

~~~bash
nm-connection-editor
~~~

在 `Wi-Fi` 选项卡中设置：

- `SSID`：热点名称，和连接配置名称 `connection name` 不是一回事。
- `Mode`：选择 `Hotspot`。
- `Band`：可选自动、2.4 GHz 或 5 GHz。频段拥挤时固定频段可能更稳定。
- `Device`：多网卡主机需要指定实际使用的无线网卡。
- `Wi-Fi Security`：选择 `WPA&WPA2 Personal` 并设置密码。
- `IPv4 Settings`：按需填写静态地址、掩码和网关。
- `IPv6`：不需要时可选择忽略。

一个无线网卡通常不能同时作为热点又连接外部 WiFi。需要同时提供热点和连接上游网络时，应使用两个独立网卡，并分别确认设备名和驱动状态。

### 7.5 热点与 WiFi 模式切换

图形界面中先断开当前热点或 WiFi，再选择要连接的网络。重新开启热点时，从网络设置菜单选择热点配置并启用。切换后检查：

~~~bash
ip addr
iw dev
nmcli device status
nmcli connection show --active
~~~

`iw dev` 是否可用取决于系统是否安装相关工具；即使命令不存在，也应记录 `nmcli device status` 和界面状态，而不是直接推断网卡损坏。切换过程中，已经连接的 SSH 会话可能短暂断开。使用无屏幕板端时，先准备好重新发现的 IP 的方法，再修改网络模式。

### 7.6 自动连接优先级

热点和 WiFi 配置可以在 `General` 选项卡中设置 `Connect automatically with priority`。不同版本的教程对数值方向描述不一致：有的资料写“数值越大优先级越高”，有的资料写“数值越小优先级越高”。不要只照抄数字，应查看当前 NetworkManager 的实际配置并做一次重启验证：

~~~bash
nmcli -f connection.id,connection.autoconnect,connection.autoconnect-priority connection show
~~~

记录哪一个连接在重启后实际被选中，再据此调整优先级。修改网络配置后，若板端没有屏幕，先确认仍能通过已有热点或串口获取 IP，防止失去远程入口。

### 7.7 无屏幕联网的基本流程

无屏幕板端的基本顺序是：默认热点可用，客户端连接热点，通过 SSH 登录，扫描可用 WiFi，选择目标 SSID 并输入密码，获取新的 IP，然后让客户端连接到同一 WiFi。切换时可能短暂断开当前热点，需要准备重新连接或通过串口查看 IP。

~~~bash
ssh -Y <user>@<board-ip>
nmcli device wifi list
nmcli connection show
ip addr
~~~

具体热点名称、默认 IP 和脚本路径必须来自当前板端的实际配置。不要把其他产品的示例热点名或脚本路径当作 310B 板的默认值。成功连接 WiFi 后，IP 可能改变；应记录 DHCP 分配的地址或为板端设置不冲突的静态地址。

## 8. SSH 远程命令行控制

### 8.1 客户端与服务端

SSH 使用客户端和服务端模型。310B 板端作为服务端运行 `sshd`，Ubuntu 主机作为客户端发起连接。客户端连接后看到的 shell 就是板端 shell，因此文件路径、权限、设备和运行时环境都以板端为准。

### 8.2 安装和启动服务端

在板端确认 SSH 状态：

~~~bash
sudo service ssh status
~~~

未安装时执行：

~~~bash
sudo apt update
sudo apt install openssh-server
sudo service ssh start
sudo systemctl enable ssh
~~~

再次检查：

~~~bash
systemctl status ssh --no-pager
ss -ltnp | grep ':22'
~~~

Ubuntu 主机通常自带 SSH 客户端。需要单独安装时执行：

~~~bash
sudo apt-get install openssh-client
ssh -V
~~~

### 8.3 网络连通性检查

先连接板端 WiFi 或有线网络，再检查同网段和可达性：

~~~bash
ip addr
ip route
ping -c 4 192.168.0.100
~~~

若无法 ping 通，依次检查：两台设备是否在同一网段、客户端是否连接了错误的网络、板端 IP 是否已经变化、路由出口是否正确、无线热点是否仍处于开启状态。`ping` 不通时不要直接反复执行 `ssh`，先把网络层问题定位清楚。

### 8.4 登录

源文档中的典型命令是：

~~~bash
ssh -Y wheeltec@192.168.0.100
~~~

其中 `-Y` 启用受信任的 X11 转发，`wheeltec` 是服务端用户名，`192.168.0.100` 是服务端地址。实际使用时替换为已经确认的值，不要复制示例密码。仅需要命令行时可以使用普通 SSH：

~~~bash
ssh <user>@<board-ip>
ssh <user>@<board-ip> 'hostname; uname -a; npu-smi info'
~~~

登录后提示符中的用户名和主机名会变化。若用户名仍是本地用户，说明没有进入远端 shell。需要执行单条板端命令时，把命令放在引号中，并确认引号内的变量是在远端还是本地展开。

### 8.5 known_hosts 冲突

第一次连接某台设备或设备身份发生变化时，可能出现主机密钥冲突。源文档给出的处理命令是：

~~~bash
ssh-keygen -f "/home/wheeltec-client/.ssh/known_hosts" -R "192.168.0.100"
ssh -Y wheeltec@192.168.0.100
~~~

第一条命令只删除指定主机在 `known_hosts` 中的记录，第二条重新连接。执行删除前先确认 IP 确实属于目标板端，并核对新的主机密钥指纹。不要为了省事直接删除整个 `known_hosts`，也不要在未确认设备身份时盲目确认新密钥。

### 8.6 同一网段排障

如果 SSH 连接失败，检查客户端和板端的 IPv4 地址与子网掩码：

~~~bash
ip -4 addr show
ip route
ping -c 4 <board-ip>
~~~

静态 IP 必须位于同一网段，且不能与板端地址或其他设备冲突。若修改的是客户端地址，保留原配置；若修改的是板端地址，先确认自己有第二条访问通道。路由器、热点、交换机和防火墙都可能影响可达性。

### 8.7 SSH 安全注意事项

- 使用强密码或 SSH 密钥，优先使用密钥登录。
- 只允许可信网络访问 22 端口，不要把 SSH 直接暴露到公网。
- 需要远程管理时，先确认服务端主机密钥指纹，再建立连接。
- `ssh -Y` 会转发 X11 连接，信任边界较宽，只在受信网络中使用。
- 多人共用板端时，为每个用户分配账号，不要共享 root 密码。
- 修改防火墙或 SSH 配置后，保留一个已登录会话，再从新会话验证，避免把自己锁在系统外。

## 9. VNC 远程桌面控制

### 9.1 客户端与服务端

VNC 也采用客户端和服务端模型。板端提供桌面服务，Ubuntu 主机或 Windows 主机运行 VNC 客户端。远程桌面适合查看图形窗口、图像预览和需要鼠标交互的程序；纯命令行任务优先使用 SSH。

### 9.2 Ubuntu 客户端安装 Remmina

源文档给出的 Remmina 安装命令是：

~~~bash
sudo apt-add-repository ppa:remmina-ppa-team/remmina-next
sudo apt update
sudo apt install remmina remmina-plugin-rdp remmina-plugin-secret
~~~

PPA 是否适合当前 Ubuntu 版本需要先确认。若系统仓库已经提供合适版本，也可以只安装 `remmina`。安装完成后从应用菜单启动，选择 VNC 协议，填写板端 IP 地址，再输入 VNC 密码。源文档示例 IP 为 `192.168.0.100`，实际使用时必须替换。

### 9.3 Windows 客户端使用 MobaXterm

在 Windows 主机上启动 MobaXterm，点击左上角 `Session`，选择 `VNC`，填写板端 IP 地址，确认客户端已经连接到板端所在网络，然后点击 `OK` 并输入 VNC 密码。不同版本的菜单名称可能略有变化，但客户端、目标 IP、密码和网络可达性这几项不变。

### 9.4 分辨率和画面质量

板端没有连接显示器时，VNC 分辨率可能较低。源文档给出的调整命令是：

~~~bash
xrandr --fb 1024x768
~~~

`xrandr --fb` 设置当前 X 显示的帧缓冲尺寸。执行前先运行 `xrandr` 查看可用输出和模式，确认目标分辨率不会超出显示能力。若画面卡顿，可以在 Remmina 中降低画质、颜色深度或更新频率。图像质量和实时性需要权衡，不要把降低画质后的流畅度当作原始编码性能。

### 9.5 共享开关

部分 Ubuntu 桌面镜像使用共享设置提供 VNC。修改网线、IP 或网络模式后，VNC 可能断开，需要检查桌面设置中的 `Sharing` 和 `Screen Sharing` 是否仍为活动状态。带有远程桌面协议的版本可能还需要启用兼容 VNC 协议的选项。共享开关关闭时，SSH 可能仍正常，但 VNC 无法连接。

### 9.6 屏幕和显示要求

部分板端镜像在没有连接显示器时不会生成可用的桌面帧缓冲。VNC 连接前可能需要连接真实屏幕或使用显示欺骗器。无屏幕场景应先确认 VNC 服务、共享开关、显示输出和分辨率，再判断是否为客户端问题。

### 9.7 VNC 中打开终端

大部分 VNC 客户端无法让 `Ctrl+Alt+T` 正确传递到远端桌面。源文档建议使用鼠标右键菜单中的终端入口。打开终端后，先用 `whoami`、`hostname` 和 `pwd` 确认会话确实运行在板端。

### 9.8 SSH 与 VNC 的对比

| 对比项 | SSH | VNC |
| --- | --- | --- |
| 传输内容 | 终端文本和可选 X11 转发 | 桌面图像、鼠标和键盘事件 |
| 带宽占用 | 通常较低 | 通常较高，受分辨率和画质影响 |
| 适合任务 | 命令、日志、脚本、服务管理 | 图形程序、图像窗口、桌面交互 |
| 无屏幕要求 | 不要求桌面帧缓冲 | 部分镜像要求显示器或显示欺骗器 |
| 安全边界 | 可使用密钥和严格访问控制 | 需要保护 VNC 密码和端口 |
| 断线影响 | 前台命令可能随会话结束 | 前台图形程序可能随会话结束 |

VNC 服务不要直接暴露到不可信网络。需要通过 SSH 隧道访问时，可以把本地端口转发到板端 VNC 端口，但应确认 VNC 监听地址和端口，并使用唯一强密码。

## 10. NFS、SCP 与 rsync

### 10.1 NFS 的角色

NFS 把服务端目录映射到客户端挂载点。典型的板端作为服务端，Ubuntu 主机作为客户端。挂载后，客户端编辑文件就像编辑本地目录，但底层文件仍属于远端文件系统，断网、服务端关机或网络切换会造成挂载失效。

### 10.2 服务端安装和导出

板端安装服务端：

~~~bash
sudo apt-get install nfs-kernel-server
~~~

编辑导出配置：

~~~bash
sudo nano /etc/exports
~~~

文件内容示例：

~~~text
/srv/ascend310-share *(rw,sync,no_root_squash)
~~~

第一列是共享目录，第二列是允许访问的客户端范围。生产或多人环境中应把 `*` 收窄为具体网段或主机地址，并根据需要改为 `ro`。`no_root_squash` 会让客户端 root 保留 root 权限，安全风险较高；只有在隔离实验网络中经过评估后才使用。

源文档还出现过下面的旧示例：

~~~text
/home/wheeltec/wheeltec_ros2 *(rw,sync,no_root_squash)
~~~

该路径只是示例，不是 310B 板的默认路径。使用时必须替换为实际共享目录，并确认目录存在、内容范围和客户端权限。

启动服务：

~~~bash
sudo /etc/init.d/nfs-kernel-server start
sudo /etc/init.d/nfs-kernel-server restart
systemctl status nfs-kernel-server --no-pager
exportfs -v
~~~

`exportfs -v` 查看当前导出，`systemctl status` 查看服务状态。修改 `/etc/exports` 后通常需要重新加载或重启服务。

### 10.3 客户端安装和挂载

Ubuntu 客户端安装：

~~~bash
sudo apt-get install nfs-common
~~~

创建挂载点：

~~~bash
sudo mkdir -p /mnt/mount_nfs
~~~

先确认可达，再挂载：

~~~bash
ping -c 4 <server-ip>
sudo mount -t nfs <server-ip>:/srv/ascend310-share /mnt/mount_nfs
mount | grep /mnt/mount_nfs
~~~

需要指定 NFS 版本或锁选项时，使用明确的参数：

~~~bash
sudo mount -t nfs -o nolock <server-ip>:/srv/ascend310-share /mnt/mount_nfs
~~~

卸载：

~~~bash
sudo umount -t nfs <server-ip>:/srv/ascend310-share /mnt/mount_nfs
~~~

若服务端曾经关机，挂载可能变成失效状态，重新挂载前先卸载。不要对正在写入的共享目录执行 `umount -f`，除非已经确认没有进程使用并且接受数据风险。

### 10.4 NFS 权限和安全

源文档中的旧配置使用了宽泛权限。下面的命令只作为说明，不建议直接照抄：

~~~text
sudo chmod -R 777 /path/to/share
sudo chown -R 777 nobody /path/to/share
~~~

`chmod -R 777` 会向所有用户开放读写执行；`chown -R 777 nobody` 不是通用安全的用户和组写法。更合理的做法是创建专用组、设置 `u+rwX` 或组权限、把客户端限制在同一网段，并在不需要写入时使用只读导出。模型、数据、模板和报告目录应按实际所有者设置权限，不要用一次宽泛递归命令处理整个家目录。

### 10.5 SCP 定向传输

SCP 使用 SSH 传输文件：

~~~bash
board_user="replace-with-board-user"
board_ip="replace-with-board-ip"
ssh_target="$board_user@$board_ip"
scp demo/vtest.avi "$ssh_target:Documents/ascend310/demo/"
scp "$ssh_target:Documents/ascend310/reports/result.json" .
~~~

下载和上传前都先检查目标目录和磁盘空间：

~~~bash
ssh "$ssh_target" 'df -h "$HOME"; test -d "$HOME/Documents/ascend310/demo"'
~~~

### 10.6 rsync 同步

rsync 适合目录同步和断点续传。先预览：

~~~bash
rsync -av --dry-run samples/case8/ \
  "$ssh_target:Documents/ascend310/samples/case8/"
~~~

确认输出后再执行：

~~~bash
rsync -av --protect-args \
  samples/case8/ \
  "$ssh_target:Documents/ascend310/samples/case8/"
~~~

默认不要使用 `--delete`。它会删除远端已有文件，可能误删模型、数据或报告。需要排除大资产时：

~~~bash
rsync -av --protect-args \
  --exclude 'data/' --exclude 'models/' --exclude '*.om' \
  samples/case8/ \
  "$ssh_target:Documents/ascend310/samples/case8/"
~~~

同步后分别在本地和远端执行 `wc -c`、`sha256sum` 或清单检查。模型、OM、数据集和真实运行报告不应因为一次同步就进入 Git。

## 11. Docker 基础与 ROS2 容器命令

### 11.1 Docker 基本概念

Docker 使用镜像和容器组织应用。镜像包含文件系统和运行环境，容器是镜像的一次隔离运行实例。容器与宿主系统共享内核，但可以使用独立的文件系统、网络和进程空间。不要把容器当作可以随意获得所有硬件权限的沙箱。

检查 Docker 是否安装和运行：

~~~bash
docker --version
systemctl status docker --no-pager
sudo systemctl start docker
sudo systemctl enable docker
~~~

查看容器和镜像：

~~~bash
docker ps
docker ps -a
docker images
~~~

`docker ps` 只显示运行中的容器，`docker ps -a` 显示包括已停止的容器，`docker images` 显示本地镜像。

### 11.2 通用容器生命周期命令

~~~bash
docker start <container-name>
docker exec -it <container-name> bash
exit
docker stop <container-name>
docker restart <container-name>
docker update --restart=always <container-name>
docker inspect <container-name>
~~~

`docker exec -it` 进入正在运行的容器，`exit` 退出容器 shell 返回宿主。`docker update --restart=always` 会让容器随 Docker 服务自动启动，设置前应确认应用具备断电恢复和安全停机策略。

### 11.3 教程中的 ROS2 容器命令

部分镜像提供了封装脚本，树莓派 5 的 ROS2 示例命令名称为：

~~~bash
ros2_start
ros2
ros2_stop
ros2_restart
~~~

这些名称是镜像提供的帮助命令或别名，不是 Docker 自带子命令。执行 `ros2_start` 启动容器，`ros2` 进入容器环境，`ros2_stop` 停止容器，`ros2_restart` 重启容器。使用前先检查它们是脚本、别名还是函数：

~~~bash
type ros2_start
type ros2
type ros2_stop
type ros2_restart
~~~

进入容器后看到的用户提示符可能变化。容器状态异常时先查看 `docker ps -a`、`docker logs <container-name>` 和 `docker inspect`，不要直接删除容器或镜像。ROS2 节点、话题、launch 和工作空间编译属于 ROS2 内容，应放在附录 5 中讲解。

### 11.4 源码目录、挂载和编译

容器可以把宿主目录挂载到容器路径。源文档提示容器内的源码可能已经映射到宿主目录，因此可以在宿主或容器中修改文件，但编译必须在安装了对应构建系统的容器中完成。使用前确认挂载关系：

~~~bash
docker inspect -f '{{json .Mounts}}' <container-name>
docker exec -it <container-name> pwd
docker exec -it <container-name> ls -la
~~~

不要把宿主 `/`、`/etc`、Docker socket 或整个家目录挂载到不可信容器。需要摄像头、串口、USB 或 NPU 设备时，逐项确认设备节点和权限，不要直接使用 `--privileged`。

### 11.5 图形窗口和 DISPLAY

容器中的图形程序需要显示服务器。源文档中的常见传递方式是：

~~~bash
DISPLAY=${DISPLAY:-unix:0}
docker exec -it -e DISPLAY=$DISPLAY <container-id> bash
~~~

`DISPLAY` 是图形显示目标，`-e` 把它传给容器。使用 X11 转发时还要确认宿主端的 `xhost` 和认证设置。源教程使用：

~~~text
xhost +
~~~

该命令允许所有客户端连接显示服务器，范围过宽。受信多用户环境中应改为只允许当前本地用户，并在使用后恢复限制。SSH 会话的 `DISPLAY` 可能是 `localhost:10.0` 等转发地址，容器启动后新建的 SSH 会话不一定继承原有显示权限。

## 12. 程序开机自启动：桌面项、启动应用和 systemd

### 12.1 桌面自启动项

桌面环境可以读取 `~/.config/autostart/*.desktop` 自动启动程序：

~~~bash
mkdir -p ~/.config/autostart
vim ~/.config/autostart/myprogram.desktop
~~~

文件内容：

~~~ini
[Desktop Entry]
Encoding=UTF-8
Type=Application
Name=myprogram
Exec=/home/wheeltec/script.sh
Terminal=true
~~~

`Exec` 必须指向已经确认的脚本或程序，`Terminal=true` 表示需要打开终端。实际使用时把 `/home/wheeltec/script.sh` 替换为当前用户可执行的路径，并设置文件权限：

~~~bash
chmod a+r ~/.config/autostart/myprogram.desktop
~~~

用户级桌面项只适用于图形登录用户。服务账号、无桌面会话和系统级程序应使用 systemd，而不是依赖桌面自启动。

### 12.2 启动脚本

一个最小脚本应明确 shebang、工作目录和环境：

~~~bash
#!/bin/bash
set -euo pipefail
cd /home/<user>/Documents/ascend310
exec /home/<user>/Documents/ascend310/scripts/run_service.sh
~~~

赋予所有者执行权限即可：

~~~bash
chmod u+x script.sh
bash -n script.sh
./script.sh
~~~

源教程中的桌面脚本会调用终端模拟器并加载应用程序环境。若脚本只做桌面显示，`Terminal=true` 可以保留终端；若脚本管理后台服务，使用 systemd 并在服务单元中显式设置环境变量。

### 12.3 Startup Applications

部分 Ubuntu 桌面提供 `Startup Applications` 图形工具。搜索 `startup` 打开该工具，点击 `Add`，填写名称和启动命令，保存后重启验证。它本质上仍是用户会话级自启动，不等价于系统服务。

### 12.4 systemd 服务

无桌面或需要自动重启的程序使用 systemd。创建单元文件：

~~~bash
sudo vim /etc/systemd/system/ascend310-demo.service
~~~

示例内容：

~~~ini
[Unit]
Description=Ascend 310B demo service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=<board-user>
WorkingDirectory=/home/<board-user>/Documents/ascend310
Environment=PYTHONNOUSERSITE=1
ExecStart=/home/<board-user>/Documents/ascend310/scripts/run_service.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
~~~

加载并启用：

~~~bash
sudo systemctl daemon-reload
sudo systemctl enable --now ascend310-demo.service
systemctl status ascend310-demo.service --no-pager
journalctl -u ascend310-demo.service -b --no-pager | tail -n 80
~~~

systemd 不会自动继承交互式 shell 中临时 `source` 的环境。CANN、Conda、`PYTHONPATH` 和 `LD_LIBRARY_PATH` 应由包装脚本或服务单元显式设置。修改单元后重新执行 `daemon-reload`，停止或重启前先确认服务 PID 和影响范围。

### 12.5 旧式 rc.local

部分旧教程使用 `/etc/rc.local`：

~~~bash
sudo nano /etc/rc.local
~~~

在文件末尾添加脚本路径，例如：

~~~text
/home/wheeltec/nfs.sh
~~~

然后重启：

~~~bash
reboot
~~~

新版本 Ubuntu 默认不一定启用 `rc.local`，而且它不提供服务依赖、重启策略和日志单位。除非目标系统已有明确要求，优先使用 systemd。

## 13. 昇腾 310B 的 CANN、PyACL、NPU、ATC 与 OM

### 13.1 在同一 shell 加载 Conda 和 CANN

~~~bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
source /usr/local/Ascend/ascend-toolkit/set_env.sh
python --version
python -c 'import sys; print(sys.executable)'
command -v atc
python -c 'import acl; print("PyACL import: ok")'
~~~

`set_env.sh` 设置 ACL、运行时和工具链相关路径，`import acl` 验证当前解释器能否加载 PyACL。另一个终端中执行 `source` 不会影响当前服务进程。启动 ATC 或 NPU 服务时，Conda 和 CANN 必须在同一个 shell 或同一个包装脚本中加载。

### 13.2 NPU 设备状态

~~~bash
npu-smi info
~~~

`npu-smi info` 输出设备、芯片和健康状态。310B4 / 8T 与 310B1 / 20T 属于不同算力层级，结果不能混合排名。已知板端出现 `Health: Alarm` 时，应把它作为诊断背景保存，但不能仅凭告警判定 ATC、ACL、精度或性能失败；相反，应继续检查 `acl` 导入失败、设备不存在、ATC 非零退出、OM 缺失、推理不一致、段错误和资源泄漏等具体现象。

### 13.3 ATC 转换

ATC 把 ONNX 等前端模型转换为 OM。下面是最小示例：

~~~bash
atc \
  --model=models/resnet18_scene.onnx \
  --framework=5 \
  --output=models/resnet18_scene \
  --soc_version=Ascend310B4 \
  --input_format=NCHW \
  --input_shape=input:1,3,224,224
~~~

`--framework=5` 表示 ONNX，`--soc_version=Ascend310B4` 必须与目标板匹配，输入布局和 `--input_shape` 必须来自该模型的输入合同。不要从另一个模型复制形状、算子参数或 `--soc_version`。转换前先阅读项目脚本的 `--help`：

~~~bash
python scripts/convert_onnx_to_om.py --help
SOC_VERSION=Ascend310B4 bash scripts/atc_convert.sh
~~~

脚本可能写入日志、manifest 和中间目录。执行前确认输出路径，执行后保存完整 ATC 命令、CANN 版本、模型输入合同、退出码和日志。

### 13.4 OM 工件检查

转换完成后检查文件存在、字节数和摘要：

~~~bash
stat -c '%s %n' models/*.om
sha256sum models/example.om
~~~

若复现包提供了 `SHA256SUMS.txt`，只能在生成该清单的根目录执行：

~~~bash
bundle_root=/path/to/repro-bundle
cd "$bundle_root"
sha256sum -c SHA256SUMS.txt
~~~

摘要一致只证明文件没有发生变化。OM 是否能够由 PyACL 加载、输入输出是否与合同一致、数值是否可接受，需要分别执行 ACL 烟测和数值比较。不同 CANN 版本、不同 SoC、不同精度和不同模型 ID 的 OM 不能混用。

### 13.5 PyACL 与 OM 推理

PyACL 是 ACL 的 Python 接口。服务启动前执行最小导入检查：

~~~bash
python -c 'import acl; print("PyACL import: ok")'
~~~

推荐使用项目包装脚本，因为包装脚本可以在启动前检查 `import acl`：

~~~bash
bash scripts/run_palmprint_service.sh --host 127.0.0.1 --port 7860
~~~

若 `import acl` 失败，记录 Python 路径、CANN 路径和完整错误，修复环境后重新启动服务。不要添加 CPU 推理回退，也不要用随机输出或模拟结果让健康检查通过。服务健康接口可以证明 HTTP 进程存活，不能证明 NPU 推理链已加载。

### 13.6 环境变量边界

~~~bash
export PYTHONNOUSERSITE=1
export PYTHONPATH="$PWD:${PYTHONPATH:-}"
export LD_LIBRARY_PATH="/usr/local/Ascend/ascend-toolkit/latest/lib64:${LD_LIBRARY_PATH:-}"
~~~

这些变量只针对当前 shell 和子进程。需要长期一致的环境应写入项目包装脚本或 systemd 单元，并经过部署评审。不要全局修改 `/etc/profile` 来让所有用户继承未经验证的 CANN 和 Python 路径。

## 14. 板端设备、服务、日志、测试与证据边界

### 14.1 系统状态报告

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
npu-smi info
~~~

这份状态报告用于记录测试环境，不是性能测试。把它和后续 ATC、ACL、精度和性能命令放在同一报告目录中，便于追溯。

### 14.2 摄像头和 V4L2

~~~bash
ls -l /dev/video*
groups
v4l2-ctl --device=/dev/video0 --list-formats-ext
v4l2-ctl --device=/dev/video0 --all
~~~

`/dev/video0` 不一定是目标摄像头，设备编号可能随枚举顺序变化。先记录实际节点、格式、分辨率和帧率，再运行采集程序。`--source 0` 的含义由 V4L2 枚举顺序决定，不能仅凭一次运行推断为固定硬件。

无权限时检查组：

~~~bash
groups
sudo usermod -aG video "$USER"
~~~

重新登录后再验证。不要用 root 启动整个图形程序或 Python 服务来绕开设备权限。

### 14.3 USB 和音频设备

~~~bash
lsusb
lsusb -t
cat /proc/asound/cards
aplay -l
arecord -l
ls -l /dev/snd
~~~

音频设备编号和名称必须以当前板端输出为准。`aplay -l` 只列出播放设备，`arecord -l` 列出采集设备。播放测试会改变音量或产生声音，执行前确认扬声器、耳机和实验环境。

USB 采集设备通常需要独占。确认设备节点后查看占用进程：

~~~bash
lsusb
usb_node="/dev/bus/usb/001/002"
test -e "$usb_node"
fuser -v "$usb_node"
~~~

`fuser -v` 只用于定位占用者，确认进程属于哪个程序后再处理。不要按模糊名称批量结束进程。

### 14.4 服务和端口

~~~bash
systemctl status ssh --no-pager
systemctl --failed
systemctl list-units --type=service --state=running
ss -ltnp
ss -lunp
~~~

`systemctl --failed` 显示失败单元，`ss -ltnp` 显示 TCP 监听及进程，`ss -lunp` 显示 UDP 监听。服务绑定到 `127.0.0.1` 时只接受本机连接，绑定到 `0.0.0.0` 则对所有网卡开放。只有可信实验网络中的明确需求才使用 `0.0.0.0`。

### 14.5 进程和日志

~~~bash
pgrep -af 'uvicorn|python|docker|npu'
ps -ef | grep -E 'python|uvicorn|docker' | grep -v grep
journalctl -b -p err --no-pager | tail -n 100
journalctl -u ascend310-demo.service -b --no-pager | tail -n 80
sudo dmesg -T | tail -n 200
~~~

`journalctl -b` 查看本次启动，`-u` 查看指定 systemd 单元，`-p err` 过滤错误级别。`dmesg` 查看内核消息，涉及 USB、摄像头、网络和驱动问题时尤其有用。公开报告只保留与故障相关的行，并删除用户名、IP、token、真实图像路径等隐私信息。

### 14.6 安全停止服务

只停止已经确认的 PID：

~~~bash
service_pid=12345
case "$service_pid" in
  ''|*[!0-9]*) printf 'invalid PID\n' >&2; exit 2 ;;
esac
test "$service_pid" -gt 1
ps -fp "$service_pid"
readlink -f "/proc/$service_pid/cwd"
kill -TERM "$service_pid"
for _ in $(seq 1 20); do
  kill -0 "$service_pid" 2>/dev/null || break
  sleep 1
done
~~~

先确认 PID 对应的命令、工作目录和用户，再发送 `TERM`。不要执行 `pkill python`、`killall python` 或按模糊名称批量结束进程，板端可能同时运行多个实验。

### 14.7 本地测试

开发机可以执行语法检查和纯 Python 测试：

~~~bash
python -m py_compile app.py
python -m compileall -q package_or_directory
python -m pytest -q
python -m unittest discover -s tests -v
git diff --check
~~~

前端项目按锁文件执行：

~~~bash
npm ci
npm test
npm run build
npm run test:e2e
~~~

真实板端硬件测试必须有显式开关，且只在板端执行。例如项目定义了硬件测试变量时：

~~~bash
BOARD_HARDWARE_TESTS=1 python -m pytest -q tests/test_board_hardware.py
~~~

变量名应以项目实际脚本为准。普通单元测试不应隐式访问 NPU、摄像头、串口、音频设备或真实个人数据。

### 14.8 证据门

| 证据门 | 能证明 | 不能证明 |
| --- | --- | --- |
| Python 语法检查 | 文件可解析 | 运行时依赖、板端设备、模型正确性 |
| HTTP 200 | 路由和服务进程可达 | NPU 推理成功、精度和性能 |
| `npu-smi info` | 设备可被管理工具读取 | 某个 OM、模型或应用程序已经验证 |
| `import acl` | PyACL 可在当前解释器导入 | ACL context、具体模型和算子链通过 |
| ATC 退出码为 0 | 转换流程返回成功 | OM 数值和任务精度正确 |
| OM 摘要一致 | 文件字节未变化 | 模型可加载或输入输出合同正确 |
| ACL 数值烟测 | 指定输入的数值关系在容差内 | 数据集精度、延迟和吞吐 |
| 任务精度测试 | 指定数据集和协议上的指标 | 其他数据集和硬件层级的结果 |
| 性能测试 | 指定参数下的延迟或吞吐 | 精度和端到端业务正确性 |
| UI 烟测 | 页面或控件可以交互 | 后端真实硬件推理已经完成 |

每份报告至少记录硬件型号和算力层级、CANN 版本、模型 ID 和精度、输入合同、预热次数、循环次数、重复次数、百分位方法、输入来源、输出路径和原始日志。

## 15. 故障排查与禁止命令模式

### 15.1 常见故障

| 现象 | 先执行 | 判断重点 |
| --- | --- | --- |
| SSH 无法连接 | `ping`、`ip addr`、`ip route`、`systemctl status ssh` | 是否同网段、服务是否启动、22 端口是否监听 |
| known_hosts 冲突 | `ssh-keygen -R <host>` | 只删除目标主机记录，并核对新指纹 |
| VNC 无法连接 | 检查共享开关、板端 IP、VNC 服务和显示输出 | VNC 与 SSH 的端口和权限边界不同 |
| VNC 分辨率低 | `xrandr`、`xrandr --fb 1024x768` | 是否超过显示能力，是否影响画面质量 |
| WiFi 切换后失联 | 检查热点、网卡、连接优先级和新 IP | 一个网卡通常不能同时做热点和连接上游 WiFi |
| 无法 ping 通板端 | `ip addr`、`ip route`、`ping` | 同网段、路由、热点和地址冲突 |
| NFS 挂载失败 | `ping`、`showmount`、`exportfs -v`、检查路径 | 导出范围、目录路径、客户端权限和失效挂载 |
| NFS 服务端关机 | 先 `umount` 再重新 `mount` | 失效挂载不能直接覆盖 |
| Docker 容器不在运行 | `docker ps -a`、`docker logs` | 容器状态、退出码和重启策略 |
| 容器中无图形窗口 | 检查 `DISPLAY`、X11 认证、容器环境变量 | 图形显示与业务推理是两个问题 |
| `import acl` 失败 | `command -v python`、`source set_env.sh`、重新导入 | 解释器和 CANN 是否来自同一环境 |
| ATC 找不到算子 | `atc --version`、检查 framework、soc_version、输入形状和日志 | 保存失败命令和模型合同，不生成伪 OM |
| `npu-smi` 显示 Alarm | 保存状态报告并检查具体错误 | 告警是诊断背景，不能单独作为测试结论 |
| 摄像头无图像 | `/dev/video*`、`v4l2-ctl`、`groups` | 设备节点、格式、权限和实际尺寸 |
| USB 仪器被占用 | `lsusb`、`fuser -v <device>` | 确认占用者，不用宽泛 kill |
| 音频无声音 | `aplay -l`、`pactl list short sinks` | 设备、默认 sink 和配置 profile |
| 端口被占用 | `ss -ltnp`、`ps -fp <pid>` | 只停止确认属于目标服务的 PID |
| 磁盘不足 | `df -h`、`du -sh <dir>` | 先归档报告，再清理隔离临时目录 |

### 15.2 禁止或必须审查的命令模式

以下命令模式不应未经确认直接执行：

- `rm -rf` 指向家目录、仓库根目录、变量未验证的路径或正在运行的部署目录。
- `rsync --delete` 同步到未确认的远端目录，它可能删除模型、数据和报告。
- `pkill python`、`killall` 或模糊 `kill`，它们可能终止其他案例和系统服务。
- `sudo pip install`、`sudo python`，它们会绕过 Conda 并改变系统包所有权。
- `chmod -R 777` 和未核实所有者的递归 `chown`，它们会放宽整个目录树的权限。
- 在开发机运行 CANN、ATC、PyACL、OM、摄像头或 NPU 命令，并把失败归因于代码。
- 在一个终端加载 CANN 或 Conda，却在另一个未加载环境的终端启动服务。
- 启动 `0.0.0.0`、公网隧道、未鉴权网关或宽泛 X11 授权后再处理个人数据。
- 使用 `xhost +` 允许所有客户端访问显示服务器，而不是限制到当前用户。
- 直接使用 `--privileged` 给容器开放全部宿主能力，而不是逐项授权设备。
- 把 `npu-smi` 摘要、HTTP 200、软件 tone、CPU 回退或界面可达写成完整硬件验收。
- 未确认路径就复制真实照片、生物特征模板、数据库、模型、密钥或 token 到 Git、截图和公开报告。

安全的操作顺序是：确认机器和环境，解析绝对路径，预览输入与目标，执行最小范围命令，保存原始输出，再把经过审查的结论写入报告。这样才能让 Ubuntu 命令成为可复现的操作步骤，而不是无法追溯的部署记录。
