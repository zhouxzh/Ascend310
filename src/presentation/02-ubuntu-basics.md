---
marp: true
size: 16:9
theme: ascend310
paginate: true
header: "《昇腾310B实战》教材配套演示"
footer: "附录 2：基于昇腾 310B 的 Ubuntu 教程"
---
<!-- _class: cover -->

# 附录 2：基于昇腾 310B 的 Ubuntu 教程

昇腾 310B 教材配套演示

3 课时 × 45 分钟

---

## 三课时学习地图

| 课时 | 45 分钟主题 | 核心能力 | 课堂产物 |
| --- | --- | --- | --- |
| 第 1 课时 | Ubuntu/Linux 基础 | 文件系统、路径、用户、权限、sudo、常用命令、nano/vim | 命令与权限练习记录 |
| 第 2 课时 | 网络与远程运维 | 静态 IP、SSH、VNC、NFS、WiFi/热点、Docker、自启动 | 远程访问与挂载验收记录 |
| 第 3 课时 | 310B 运行与证据 | CANN、PyACL、NPU、ATC/OM、设备、服务、日志、测试边界 | 板端诊断与证据报告 |

学习顺序不能倒置：先能在 Ubuntu 中安全定位文件和进程，再操作网络和服务，最后才进入 CANN/NPU。

---

## 本课标识与安全边界

- `【开发机/客户端】`：控制电脑、Ubuntu 桌面或 SSH 客户端，不具备 310B 运行时。
- `【板端】`：运行 Ubuntu 的昇腾 310B 主机。
- `【板端独占】`：CANN、PyACL、`npu-smi`、ATC/OM、`/dev/davinci*`、真实 NPU 推理、真实设备服务。
- 占位符 `<board-ip>`、`<user>`、`<container>`、`<path>` 必须替换为已确认值。
- 证据标签：`documented`、`inferred`、`observed-pass`、`observed-fail`、`untested`。

禁止把 `chmod -R 777`、`rm -rf` 未验证路径、`pkill python`、`killall` 当作通用教程命令。命令输出不等于验收结论。

---

## 第 1 课时（45 分钟）：Ubuntu/Linux 基础

本课时目标：

- 理解 Linux 内核、Shell、Ubuntu 发行版与文件系统之间的关系。
- 能区分绝对路径、相对路径、用户主目录和当前目录。
- 能解释 `root`、普通用户、用户组、`sudo` 与权限位。
- 能使用常见文件、目录、检索和编辑命令，并说明每条命令的影响范围。
- 能安全完成一次配置文件编辑和脚本授权。

45 分钟建议：5 分钟概念，15 分钟路径与权限，15 分钟常用命令与编辑器，10 分钟课堂练习。

---

## Linux、Shell 与 Ubuntu 的关系

| 层次 | 作用 | 本课关注点 |
| --- | --- | --- |
| 内核 | 管理进程、内存、文件、设备和网络 | 不是日常直接操作对象 |
| Shell | 把命令解释为系统调用 | `bash`、当前用户、当前目录 |
| 文件系统 | 用目录和文件组织系统资源 | 路径、权限、挂载点 |
| Ubuntu | 基于 Linux 内核的发行版 | 软件包、网络工具、桌面与服务 |
| 昇腾软件栈 | 在 Ubuntu 上增加 CANN、驱动与 NPU 运行时 | 只能在实际板端验证 |

Shell 提示符中的 `$` 通常表示普通用户，`#` 通常表示 `root`；提示符不是权限证明，仍要用 `whoami` 和 `id` 核对。

---

## Ubuntu 文件结构：系统入口

| 目录 | 典型内容 | 操作提醒 |
| --- | --- | --- |
| `/bin` | 重要二进制应用程序 | 通常由系统包管理，不手工替换 |
| `/boot` | 启动配置与内核文件 | 修改可能影响启动 |
| `/dev` | 设备文件，如串口、摄像头、NPU 设备节点 | 设备名与实际板端驱动相关 |
| `/etc` | 系统与服务配置文件 | 修改前备份、修改后验证 |
| `/home` | 本地用户主目录 | 项目、报告、个人配置通常放在这里 |

先理解“系统目录”和“用户工作目录”的边界，再决定是否使用 `sudo`。

---

## Ubuntu 文件结构：软件、挂载与本地安装

| 目录 | 典型内容 | 操作提醒 |
| --- | --- | --- |
| `/lib` | 系统库与内核模块 | 不要手工覆盖库文件 |
| `/mnt` | 临时挂载文件系统 | NFS、移动介质常用挂载点 |
| `/opt` | 可选应用程序安装目录 | 第三方软件常用位置 |
| `/usr/local` | 用户自行安装的软件 | CANN 等工具链常出现于 `/usr/local/Ascend` |
| `/root` | `root` 用户主目录 | 不要把普通项目放在这里 |

不要混淆 `/` 根目录、`/home` 用户主目录、`.` 当前目录和 `..` 上级目录。

---

## 路径：命令影响范围的第一证据

```bash
cd ~/Documents/Ascend310
pwd
realpath .
ls -la
readlink -f models/example.om
```

命令解释：

- `cd`：切换目录；`~` 展开为当前用户主目录。
- `pwd`：打印当前工作目录，确认命令将在哪里执行。
- `realpath .`：把当前相对路径解析为绝对路径。
- `ls -la`：列出隐藏文件、权限、所有者和大小。
- `readlink -f`：解析符号链接，删除、同步或启动服务前尤其重要。

绝对路径从 `/` 开始，如 `/home/user/project`；相对路径依赖当前目录。

---

## 用户、root 与 sudo

```bash
whoami
id
sudo -i
exit
sudo apt update
```

命令解释：

- `whoami`、`id`：确认当前用户、UID、GID 和附加组。
- `sudo -i`：进入具有登录环境的 `root` Shell；退出使用 `exit` 或 `Ctrl+D`。
- `sudo <command>`：只对单条命令临时提升权限，优先使用这种最小权限形式。
- 源教程还提到 `sudo su`；它容易保留混淆的环境变量，本课不把它作为默认写法。
- 普通用户目录中的文件通常不需要 `sudo`；只有系统路径和系统服务才需要管理员授权。

---

## 权限：用户、组、其他用户

Linux 每个文件有三组权限：

| 权限对象 | 含义 | 示例 |
| --- | --- | --- |
| user | 文件所有者 | `u` |
| group | 文件所属组 | `g` |
| other | 其他所有用户 | `o` |

每组包含 `r` 读、`w` 写、`x` 执行三种权限：

```text
-rwxr-xr-x
 |  |  |  |
 |  |  +-- other: r-x
 |  +----- group: r-x
 +-------- user: rwx
```

Python 脚本需要执行权限时，通常给所有者增加 `x`，而不是给所有用户开放写权限。

---

## 查看和修改权限

```bash
ls -l scripts/run_service.sh
stat -c '%A %U:%G %s %n' scripts/run_service.sh
groups
chmod u+x scripts/run_service.sh
sudo chown <user>:<group> <path>
```

命令解释：

- `ls -l`：查看权限位、链接数、所有者、组和大小。
- `stat`：以稳定格式输出权限、所有者、组、字节数和文件名。
- `groups`：查看当前用户所在组，如摄像头可能需要 `video` 组。
- `chmod u+x`：只给文件所有者增加执行权限。
- `chown`：改变所有者或组；必须确认目标路径和预期属主。

源资料中的 `chmod 777` 只说明“如何放开权限”，不是推荐做法。优先使用最小权限。

---

## 常用命令：目录与文件操作

```bash
pwd
cd ..
cd ~/Documents
mkdir -p work/reports
touch notes.txt
cp source.txt backup.txt
cp -r project project-copy
mv old.txt archive/old.txt
rm file.txt
rm -r temp-directory
```

命令解释：

- `pwd` 显示当前位置；`cd ..` 返回上一级，`cd` 无参数通常返回主目录。
- `mkdir -p` 创建多级目录且已存在时不报错。
- `touch` 创建空文件或更新时间戳。
- `cp` 复制文件，`cp -r` 复制目录。
- `mv` 移动或重命名；执行前确认目标目录。
- `rm` 删除文件，`rm -r` 删除目录；Linux 默认没有回收站。

---

## 常用命令：查看、检索与空间

```bash
cat /etc/os-release
head -n 20 log.txt
tail -n 80 log.txt
grep -n "ERROR\|WARN" log.txt
find . -maxdepth 2 -type f -print
file model.om
du -sh reports
df -h .
command -v python
```

命令解释：

- `cat` 适合小文件；长日志优先使用 `head`、`tail`。
- `grep` 过滤文本，`-n` 显示行号。
- `find -maxdepth` 限定扫描深度，避免误扫整个家目录。
- `file` 只给格式线索，不能证明模型可被 ACL 加载。
- `du` 看目录占用，`df` 看文件系统剩余空间。
- `command -v` 确认实际找到的可执行文件，避免 PATH 混入错误版本。

---

## nano：直接编辑、保存与退出

```bash
nano notes.txt
sudo nano /etc/exports
```

编辑流程：

1. 进入 nano 后可直接修改文本。
2. 按 `Ctrl+O` 写入文件。
3. 保持文件名不变时按 `Enter` 确认。
4. 按 `Ctrl+X` 退出。
5. 如果提示保存，确认文件名与内容后再选择 `Y`。

命令解释：

- `nano notes.txt` 编辑用户文件，通常不需要 `sudo`。
- `sudo nano /etc/exports` 编辑系统配置，必须明确修改内容和回滚方法。
- 保存前先确认当前路径、目标和权限；不要用 nano 静默覆盖未备份的系统文件。

---

## vim：模式化编辑与退出

```bash
vim notes.txt
```

基本流程：

1. 进入 vim 后默认是普通模式，不能直接输入文本。
2. 按 `i` 进入插入模式，右下角通常显示 `INSERT`。
3. 按 `Esc` 返回普通模式。
4. 输入 `:wq` 保存并退出。
5. 输入 `:q` 未修改时退出。
6. 输入 `:q!` 放弃修改退出。
7. 编辑受保护文件时可使用 `:wq!`，但必须先确认权限风险。

命令解释：使用 vim 时先确认当前模式，再执行编辑或退出命令；按键记忆可以在练习中逐步完成。

---

## 编辑器安全：先备份，再验证

```bash
sudo cp /etc/exports /etc/exports.bak-$(date +%Y%m%d)
sudoedit /etc/exports
diff -u /etc/exports.bak-$(date +%Y%m%d) /etc/exports
```

命令解释：

- `cp`：在修改系统配置前保留同目录备份，文件名带日期。
- `sudoedit`：以受控方式编辑需要管理员权限的文件，编辑后检查变更。
- `diff -u`：比较修改前后差异，确认没有误删配置行。

nano 适合初学者完成简单配置；vim 适合远程终端和熟练用户。两者共同要求是：知道文件是什么、改什么、如何回滚、如何验证。

---

## 第 1 课时课堂任务

1. 在自己项目目录中依次执行 `pwd`、`realpath .`、`ls -la`、`stat`，记录当前用户、绝对路径和权限。
2. 创建 `practice/` 目录，使用 `touch`、`cp`、`mv` 完成一次文件创建、复制、移动和重命名。
3. 用 `head`、`tail`、`grep` 从一份日志中找出首行、末行和错误行。
4. 编辑一个测试脚本，用 `chmod u+x` 只给所有者增加执行权限，并解释为什么不使用 `chmod 777`。
5. 对每条命令写出：执行位置、影响路径、是否改变系统状态、产生什么证据。

---

## 第 1 课时交付物与验收

交付物：

- `ubuntu-basics/lesson1-command-log.md`
- `ubuntu-basics/lesson1-permissions.txt`
- `ubuntu-basics/lesson1-editor-notes.md`

验收标准：

- 能说出绝对路径和相对路径的区别。
- 能解释 `$` 与 `#`、`root` 与普通用户、`sudo` 与 `su` 的基本边界。
- 能读懂 `rwxr-xr-x`，并只给自己需要的权限。
- 能完成 nano 保存退出和 vim `i`、`Esc`、`:wq` 流程。
- 执行删除、权限和系统配置修改前，能先确认绝对路径与回滚方案。

---

## 第 2 课时（45 分钟）：网络与远程运维

本课时目标：

- 理解 IP、子网掩码、网关、DNS 与同一网段检查。
- 能在 Ubuntu 中查看网络状态并规划静态 IP。
- 能使用 SSH 远程登录，处理 known_hosts 冲突并排查同一网段问题。
- 能使用 VNC/Remmina/MobaXterm 查看远程桌面并理解无显示器限制。
- 能配置 NFS、WiFi/热点、Docker 和程序自启动，并识别板端独占步骤。

45 分钟建议：10 分钟网络与静态 IP，10 分钟 SSH/VNC，10 分钟 NFS/WiFi，10 分钟 Docker/自启动，5 分钟总结。

---

## 网络模型：先分清四个量

| 概念 | 作用 | 例子 |
| --- | --- | --- |
| IP 地址 | 标识本机在网段中的地址 | `192.168.0.120` |
| 子网掩码 | 划分网络位与主机位 | `255.255.255.0` |
| 网关 | 离开本网段时的下一跳 | `192.168.0.1` |
| DNS | 把域名解析为 IP | `223.5.5.5` |

在同一个 `/24` 网段中，`192.168.0.100` 和 `192.168.0.120` 可以直接通信；如果掩码或网络号不同，可能必须经过网关。

设置静态 IP 时，要保证地址唯一、与目标设备同网段，并避开 DHCP 地址池。

---

## 网络状态：先观察，再修改

```bash
ip a
ip route
nmcli connection show
ss -ltnp
ping -c 4 <board-ip>
```

命令解释：

- `ip a`：查看网卡名称、状态、IPv4/IPv6 地址和掩码。
- `ip route`：查看默认网关和路由选择。
- `nmcli connection show`：列出 NetworkManager 连接配置。
- `ss -ltnp`：查看 TCP 监听端口和对应进程。
- `ping -c 4`：发送 4 个 ICMP 包，只证明基础可达性，不证明 SSH、VNC 或服务可用。

源教程中仍可能使用 `ifconfig`；新脚本优先使用 `ip`。

---

## 静态 IP：规划、设置与验证

源教程给出的静态 IP 示例是 `192.168.0.120`，网络配置选 `Manual`，子网掩码 `255.255.255.0`，并避免使用目标设备已有的 `192.168.0.100`。

```bash
ip -4 addr show
ip route
ping -c 4 <board-ip>
```

设置前确认：

- 目标 IP 未被其他设备占用。
- 目标 IP 与板端或网关位于同一网段。
- 网关、DNS、接口名称与当前网络匹配。
- 保留一条可恢复路径，例如串口、本地桌面或其他网络接口。
- 修改后记录旧配置、新配置、时间、接口和验证命令。

`【板端】` 修改 310B 的网络配置可能立即断开当前 SSH/VNC，必须准备物理或串口恢复路径。

---

## 同一网段：网络位比较

判断步骤：

1. 读取两个 IP 和子网掩码。
2. 把 IP 与掩码做按位 AND。
3. 如果网络地址相同，通常属于同一网段。
4. 再检查链路、VLAN、路由、防火墙和接口状态。

示例：`192.168.1.10` 与 `192.168.1.20`，掩码 `255.255.255.0`，网络地址都是 `192.168.1.0/24`。

```bash
ip -4 addr show
ip route
ip neigh
ping -c 4 <peer-ip>
```

命令解释：

- `ip -4 addr` 看本机 IPv4 与掩码；`ip route` 看是否走出正确接口。
- `ip neigh` 查看本网段邻居缓存。
- `ping` 不通时先检查网段、接口和 IP 冲突，不要直接归因于 SSH。

---

## NetworkManager：静态 IP 与连接配置

```bash
nm-connection-editor
nmcli connection show
nmcli connection up "<connection-name>"
```

命令解释：

- `nm-connection-editor`：打开图形化连接编辑器；选择对应有线或无线连接，在 IPv4 中选择 Manual，填写地址、掩码、网关和 DNS。
- `nmcli connection show`：命令行确认连接名称和状态；脚本不要依赖带空格的显示名称。
- `nmcli connection up`：重新启用指定连接，使配置生效。

`【板端】` 在 310B 上修改连接前，先确认该连接就是当前管理网卡，并保留串口或本地终端。源资料的静态 IP 步骤原本面向图形桌面，命令仍需按实际 Ubuntu 版本核对。

---

## WiFi 与热点：两种互斥模式

- 连接 WiFi：板端作为 Station，加入外部路由器或手机热点。
- 创建热点：板端作为 Soft AP，向手机或电脑发出 WiFi。
- 同一块无线网卡通常不能同时稳定承担热点和外部 WiFi 两种模式。
- 需要同时连接外网和提供服务时，应明确网卡数量、路由和 IP 规划。

源教程中的热点参数包括：

- SSID：对外可见的 WiFi 名称。
- Mode：`Hotspot`。
- Security：`WPA & WPA2 Personal` 与密码。
- Band：`Automatic`、`2.4 GHz` 或 `5 GHz`。
- Device：多网卡时必须明确选择哪一块。

---

## 创建热点、切换 WiFi 与优先级

源教程的图形步骤：

1. 右键顶部网络图标，选择 `Edit Connections` 或运行 `nm-connection-editor`。
2. 新建 WiFi 连接，设置 SSID、`Hotspot` 模式、密码和 IPv4 静态地址。
3. 从热点切换到 WiFi 时，先 `Disconnect` 或 `Turn Off Hotspot`，再选择外部 WiFi。
4. 从 WiFi 切回热点时，选择对应的热点连接并启用。

源教程还提到，在网络配置的 `General` 页中设置 `Connect automatically with priority`。不同版本对数值方向的说明不一致，必须以当前界面和实测结果为准。

`【板端】` 切换 WiFi 会中断当前远程会话；优先通过热点、串口或本地桌面保留恢复通道。

---

## 无屏幕连接 WiFi 的可复现路径

源教程的通用流程是：电脑连接板端默认热点，通过 SSH 登录板端，在板端切换网络，再从新的 IP 重新连接。

```bash
nmcli -t -f NAME connection show
sudo nmcli connection modify "Wi-Fi connection 1" connection.id "Wi-Fi_connection_1"
nmcli connection up "<wifi-connection>"
```

命令解释：

- `nmcli -t -f NAME connection show`：以脚本友好格式列出连接名称。
- `connection modify ... connection.id`：把连接名称改为无空格标识，避免脚本参数被拆分。
- `nmcli connection up`：启用目标 WiFi；具体属性需要在当前 Ubuntu 版本中用 `nmcli connection show "<name>"` 核对。

`【板端独占】` 源教程中的切换脚本带有其他产品型号的固定路径和固定设备名，不能直接复制到 310B。

---

## 切换后获取新 IP 并重连

```bash
hostname -I
ip -4 addr show
ping -c 4 <new-board-ip>
ssh -Y <user>@<new-board-ip>
```

命令解释：

- `hostname -I` 快速列出本机 IPv4 地址，适合本地终端查看。
- `ip -4 addr show` 给出接口、地址和掩码，是更完整的证据。
- `ping` 确认客户端与板端基础可达。
- `ssh -Y` 在可信实验网络中登录新地址；不使用时可用普通 `ssh <user>@<ip>`。

`【板端】` 扫描 WiFi 或切换模式时，热点可能短暂断开。客户端需要重新连接原热点，但原 SSH 会话在部分源教程中可能保持，不能假设一定不断线。

---

## SSH：客户端与服务端

| 角色 | 运行位置 | 职责 |
| --- | --- | --- |
| SSH 客户端 | 开发机、控制电脑 | 发起连接、输入命令、显示输出 |
| SSH 服务端 | 310B 板端或其他 Ubuntu 主机 | 监听连接、验证用户、提供 Shell |

源教程的典型场景：板端作为服务端，控制电脑作为客户端，登录后客户端输入的命令实际在板端执行。

使用 SSH 前必须确认用户名、目标 IP、端口、认证方式和网络路径。SSH 成功登录只证明远程 Shell 可用，不证明 NPU、摄像头或项目服务已通过验收。

---

## SSH 服务端：安装、启动与开机自启

```bash
sudo service ssh status
sudo apt update
sudo apt install openssh-server
sudo service ssh start
sudo systemctl enable ssh
```

命令解释：

- `sudo service ssh status`：查看 SSH 服务是否已安装并运行。
- `sudo apt update`：更新软件包索引。
- `sudo apt install openssh-server`：在板端安装 SSH 服务端。
- `sudo service ssh start`：立即启动服务。
- `sudo systemctl enable ssh`：设置开机自启，不替代本次启动。

客户端侧，Ubuntu 通常自带 SSH 客户端；源教程给出的安装命令是：

```bash
sudo apt-get install openssh-client
```

`【板端】` 安装和启用服务会改变系统状态，应先记录 Ubuntu 版本、服务状态和监听端口。

---

## SSH 首次连接：ping 后再登录

源教程的登录流程：

```bash
ping 192.168.0.100
ssh -Y wheeltec@192.168.0.100
```

命令解释：

- `ping 192.168.0.100`：确认客户端与目标 IP 基础可达。
- `ssh -Y wheeltec@192.168.0.100`：以 `wheeltec` 用户登录 `192.168.0.100`，并启用可信 X11 转发。
- 密码输入时不显示字符，这是正常行为；不要因为终端没有回显而重复输入。
- 第一次连接会显示主机指纹，核对设备信息后再接受。

`-Y` 会扩大客户端 X11 信任范围，只应在受信任实验网络中使用。普通命令行任务优先使用 `ssh <user>@<ip>`。

---

## known_hosts 冲突：先确认，再删除旧记录

源教程给出的处理命令是：

```bash
ssh-keygen -f "/home/wheeltec-client/.ssh/known_hosts" -R "192.168.0.100"
ssh -Y wheeltec@192.168.0.100
```

命令解释：

- `ssh-keygen -R`：从当前用户的 `known_hosts` 中删除指定主机的旧记录。
- 再次执行 `ssh -Y`：重新建立连接并接受新的主机指纹。

只有在确认目标设备确实重装、换机或重置密钥后才删除旧记录。若 IP 只是被另一台设备占用，应先排查 IP 冲突，而不是直接信任新指纹。

---

## SSH 同一网段故障排查

按顺序执行：

```bash
ip -4 addr show
ip route
ping -c 4 <board-ip>
sudo service ssh status
ss -ltnp | grep ':22'
```

排查逻辑：

1. 客户端和板端是否都有正确 IPv4 与掩码。
2. 两边是否在同一网段，或路由是否能到达对方。
3. 接口是否启用，WiFi 是否连接到正确 SSID。
4. 板端 SSH 服务是否运行，是否监听 22 端口。
5. 是否有防火墙或安全策略阻断。
6. IP 是否冲突；必要时回到串口或本地终端修正。

`【板端】` 最后两项检查修改系统状态，必须在确认当前连接不会因操作而永久丢失时执行。

---

## VNC：远程桌面适合什么任务

VNC 让客户端看到板端桌面，适合：

- 需要图形窗口的 OpenCV 预览。
- 图形化网络、服务和文件管理器配置。
- 不方便使用纯命令行完成的可视化调试。

常见客户端：

- Ubuntu：Remmina。
- Windows：MobaXterm 或其他 VNC 客户端。
- 板端：预配置的 VNC 服务端。

源教程提到，部分功能使用 `ssh -Y` 转发图像窗口时可能卡顿，改用 VNC 远程桌面后可以正常显示。SSH 与 VNC 解决的是不同问题，不应互相替代。

---

## VNC 连接：工具、目标 IP 与密码

Remmina 的源教程安装命令：

```bash
sudo apt-add-repository ppa:remmina-ppa-team/remmina-next
sudo apt update
sudo apt install remmina remmina-plugin-rdp remmina-plugin-secret
```

命令解释：

- `apt-add-repository` 添加 Remmina PPA；使用第三方源前确认系统版本和源可信度。
- `apt update` 刷新软件包索引。
- `apt install` 安装 Remmina 及插件。
- 源教程还写了 `sudo killall remmina`；共享环境中不要批量终止进程，先正常退出，必要时按已确认 PID 使用 `kill -TERM`。

连接步骤：

1. 客户端先连接板端所在 WiFi 或网段。
2. Remmina 选择 VNC，MobaXterm 选择 VNC Session。
3. 填写目标 IP，例如源教程默认示例 `192.168.0.100`，但必须以实际板端为准。
4. 输入 VNC 或系统登录密码，确认远程桌面出现。

---

## VNC 无显示器与分辨率调整

```bash
xrandr --fb 1024x768
```

命令解释：

- `xrandr --fb 1024x768`：请求把当前 X 桌面的帧缓冲改为 1024 × 768。
- 该命令只在图形会话和正确 `DISPLAY` 下有效。
- 如果分辨率没有变化，先确认显示器、虚拟显示或远程桌面后端支持该尺寸。
- 源教程说明，部分主板没有连接显示器时需要接屏幕或显示欺骗器，VNC 才有可用的屏幕会话。

`【板端独占】` 该命令改变板端图形会话。执行前记录原分辨率，并准备本地终端或串口恢复路径。VNC 中 `Ctrl+Alt+T` 可能无效时，可尝试桌面右键打开终端。

---

## VNC 共享开关与 SSH/VNC 对比

VNC 常见限制：

- 接入或拔掉网线、修改 IP 后，共享开关可能被关闭。
- 需要重新打开 `Sharing -> Screen sharing`，或启用 `Remote Desktop` 的兼容协议。
- 共享服务关闭时，即使 IP 可达，VNC 仍可能连接失败。

| 对比项 | SSH | VNC |
| --- | --- | --- |
| 主要用途 | 命令行、脚本、文件操作 | 完整桌面与图形窗口 |
| 带宽 | 通常较低 | 通常较高 |
| 图形窗口 | 依赖 X11 转发 | 原生远程桌面 |
| 故障定位 | 服务、端口、认证 | 服务、共享开关、屏幕会话、分辨率 |
| 证据边界 | Shell 可用 | 桌面可交互 |

---

## NFS：服务端与客户端模型

NFS 把一台 Ubuntu 主机上的目录映射到另一台主机，适合在开发机上直接编辑板端工作空间。

源教程中的角色：

- 板端或 ROS 主机作为 NFS 服务端，对外共享工作目录。
- Ubuntu 开发机作为 NFS 客户端，把共享目录挂载到 `/mnt` 下的明确目录。

```bash
sudo apt-get install nfs-kernel-server
sudo mkdir -p /srv/nfs/<export-name>
sudo nano /etc/exports
sudo exportfs -ra
sudo systemctl restart nfs-kernel-server
```

命令解释：

- `apt-get install nfs-kernel-server`：安装服务端。
- `mkdir -p`：创建明确的共享目录。
- `nano /etc/exports`：编辑共享策略。
- `exportfs -ra`：重新导出配置。
- `systemctl restart`：重启服务使配置和依赖重新加载。

`【板端】` NFS 服务端配置属于系统级操作，必须限制客户端范围并记录回滚。

---

## NFS 导出配置与最小权限

源教程给出的导出行格式是：

```text
/home/<user>/<workspace> <client-subnet>(rw,sync,no_subtree_check)
```

说明：

- 第一段是服务端共享目录。
- 第二段限定允许访问的客户端或子网，不要使用无边界的 `*` 作为默认教学配置。
- `rw` 允许读写，`sync` 要求同步写入，`no_subtree_check` 是常见稳定选项。
- 源教程还使用了 `no_root_squash`；它会保留客户端 root 权限，安全风险较高，只在隔离实验网络中评估。

源资料曾出现 `chmod -R 777` 和把目录改为错误属主的写法，本课不采用。优先用最小共享目录、最小客户端范围和明确属主。

---

## NFS 客户端：挂载、验证与卸载

```bash
sudo apt-get install nfs-common
sudo mkdir -p /mnt/mount_nfs
sudo umount -t nfs 192.168.0.100:/home/wheeltec/wheeltec_robot /mnt/mount_nfs
sudo mount -t nfs -o nolock 192.168.0.100:/home/wheeltec/wheeltec_robot /mnt/mount_nfs
mount | grep nfs
```

命令解释：

- `nfs-common`：客户端基础包；源教程中的 `portmap` 属于旧版方案。
- `mkdir -p`：创建独立挂载点，避免覆盖已有目录。
- `umount`：挂载前先卸载旧 NFS，源教程用于避免服务端断电后的残留状态。
- `mount -t nfs -o nolock`：挂载远端目录；`nolock` 来自源教程，现代环境应根据 NFS 版本选择参数。
- `mount | grep nfs`：确认实际挂载源、挂载点和选项。

先 `ping` 目标 IP，再挂载；能 ping 不代表导出路径和权限一定正确。

---

## NFS 开机自动挂载与故障排查

源教程给出过 `rc.local` 方案：编写 `nfs.sh`，在 `/etc/rc.local` 末尾调用脚本并重启。现代 Ubuntu 更推荐用 `systemd` 挂载单元或谨慎编辑 `/etc/fstab`：

```bash
sudo nano /etc/fstab
sudo systemctl daemon-reload
sudo mount -a
findmnt /mnt/mount_nfs
```

命令解释：

- `/etc/fstab` 中应使用真实导出路径和独立挂载点，并考虑 `nofail` 避免服务端离线时阻塞启动。
- `systemctl daemon-reload` 重新加载 systemd 单元。
- `mount -a` 按 fstab 尝试挂载，但必须在本地终端执行，以免配置错误造成启动受阻。
- `findmnt` 比 `mount | grep` 更结构化地确认挂载状态。

故障顺序：`ping` → `showmount -e <server-ip>` → 导出路径 → 客户端挂载点 → 权限 → 服务端关机后的残留挂载。

---

## Docker：轻量容器与 Ubuntu 的关系

Docker 在同一个 Ubuntu 内核上隔离文件系统和进程环境。

关键概念：

- Image：只读的软件模板。
- Container：镜像启动后的运行实例。
- Volume/Bind mount：把宿主目录或持久化数据映射给容器。
- Network：为容器提供端口和网络隔离。
- 宿主机：运行 Docker Engine 的 Ubuntu 板端或开发机。

容器适合固定依赖和快速部署，但容器内的设备、网络、图形显示和 NPU 访问仍依赖宿主机配置。`【板端】` 能用 Docker 不等于容器内能自动访问 NPU。

---

## Docker 生命周期命令

```bash
docker --version
docker info
docker ps -a
docker start <container>
docker exec -it <container> bash
docker stop <container>
docker restart <container>
docker logs --tail 100 <container>
```

命令解释：

- `--version`、`info`：确认客户端、服务端和存储状态。
- `ps -a`：列出运行和停止的容器。
- `start`：启动已存在容器；`exec -it` 进入其中的 Shell。
- `stop`、`restart`：按容器名执行生命周期操作。
- `logs --tail 100`：读取最近日志，不进入容器。

源教程还使用项目专用包装命令；包装命令只在对应镜像中成立，不能把其他产品的容器名照搬到 310B。

---

## Docker 挂载、自启动与图形显示

```bash
docker update --restart=always <container>
docker ps -qf "name=<container>"
docker inspect <container>
docker exec -it -e DISPLAY=$DISPLAY <container> bash
```

命令解释：

- `docker update --restart=always`：设置容器开机和异常退出后的重启策略。
- `docker ps -qf "name=<container>"`：按名称过滤并只输出容器 ID。
- `docker inspect`：查看宿主目录、环境变量、设备和网络映射。
- `-e DISPLAY=$DISPLAY`：把宿主图形显示变量传入容器；它只对允许 X11 的受信任任务有效。
- 源教程使用 bind mount 在两个环境间同步源码；修改源码前必须确认挂载方向，避免覆盖板端目录。

`【板端】` 涉及 `/dev/davinci*`、摄像头或特权设备的容器映射必须单独验证，不把开发机容器结果当成 NPU 证据。

---

## 程序自启动：先选机制，再写脚本

| 机制 | 适合场景 | 优点 | 风险 |
| --- | --- | --- | --- |
| 桌面 autostart | 登录图形桌面后启动 GUI 程序 | 配置直观 | 依赖桌面会话和 `DISPLAY` |
| systemd service | 板端后台服务、网络服务、设备服务 | 开机自启、日志、依赖和状态清晰 | 需要正确用户、环境和工作目录 |
| `rc.local` | 旧镜像和历史脚本 | 传统、简单 | 启动时序和环境不稳定，逐渐淘汰 |

选择顺序：后台服务优先 systemd；必须等用户登录并显示 GUI 的程序才用桌面 autostart。

自启动脚本必须使用绝对路径，并显式加载 ROS 环境、CANN 环境或 conda 环境；仅在一个终端 `source` 过环境，不会自动传给开机服务。

---

## 桌面 autostart：脚本与 .desktop 文件

```bash
mkdir -p ~/.config/autostart
vim ~/.config/autostart/myprogram.desktop
```

文件示例：

```ini
[Desktop Entry]
Type=Application
Name=myprogram
Exec=/home/<user>/startup.sh
Terminal=true
```

命令解释：

- `mkdir -p ~/.config/autostart`：创建当前用户的桌面自启动目录。
- `vim`：编辑 `.desktop` 文件；该文件属于当前用户，通常不需要 `sudo`。源教程使用 `sudo`，会导致属主不一致，本课不采用。
- `Exec`：必须是脚本的绝对路径。
- `Terminal=true`：需要交互窗口时保留终端。

启用前先手工运行 `chmod u+x startup.sh` 和 `./startup.sh`，确认脚本本身可运行。

---

## systemd：后台服务开机自启

```bash
sudo systemctl daemon-reload
sudo systemctl enable <service>.service
sudo systemctl start <service>.service
sudo systemctl status <service>.service --no-pager
journalctl -u <service>.service -b --no-pager | tail -n 80
```

命令解释：

- `daemon-reload`：让 systemd 重新读取单元文件，适用于新建或修改服务。
- `enable`：建立开机自启链接。
- `start`：立即启动；`status` 查看状态、主 PID 和最近日志。
- `journalctl -u ... -b`：查看本次启动的服务日志。

`【板端独占】` CANN、NPU、摄像头和真实设备服务必须验证启动用户、环境变量、设备权限和依赖顺序。状态为 `active` 只说明进程运行，不证明推理成功。

---

## 第 2 课时课堂任务

在隔离实验网络中完成：

1. 用 `ip a`、`ip route`、`nmcli connection show` 记录客户端和板端网络状态。
2. 规划一个同网段且不冲突的静态 IP，写出地址、掩码、网关、DNS 和回滚方式。
3. 从客户端 `ping` 板端，执行 `ssh -Y <user>@<board-ip>`，记录主机名和 `uname -a`。
4. 在一台有图形桌面的 Ubuntu 上安装或配置 VNC 客户端，记录 IP、密码来源、分辨率和共享开关状态。
5. 在临时挂载点完成一次 NFS 挂载、`findmnt` 验证和卸载；不修改生产共享目录。
6. 用一个测试容器练习 `start`、`logs`、`exec`、`stop`，并说明重启策略。
7. 写一个只读自启动脚本，先手工运行，再选择桌面 autostart 或 systemd 进行验证。

---

## 第 2 课时交付物与验收

交付物：

- `ubuntu-networking/static-ip-plan.md`
- `ubuntu-networking/ssh-vnc-evidence.md`
- `ubuntu-networking/nfs-mount-report.md`
- `ubuntu-networking/docker-autostart.md`

验收标准：

- 能判断两个 IP 是否在同一网段，并能解释掩码的作用。
- 能在不丢失恢复通道的前提下规划静态 IP。
- 能使用 SSH 和 VNC，并能区分两者的用途与证据。
- 能挂载和卸载 NFS，能定位 IP、路径、权限和服务端离线问题。
- 能说明容器与宿主机的边界，不把容器启动当作 NPU 验收。
- 能解释桌面 autostart 与 systemd 的启动时机差异。

---

## 第 3 课时（45 分钟）：310B CANN 与运行证据

本课时目标：

- 明确开发机与 310B 板端的职责边界。
- 能在板端同一 Shell 中加载 CANN，并验证 PyACL 导入。
- 能读取 `npu-smi`、检查设备节点和记录 `soc_version` 判断过程。
- 能理解 ATC、OM、PyACL 加载和数值烟测的区别。
- 能查看服务、端口、日志并有序停止目标 PID。
- 能按证据门组织测试，不伪造板端结果。

45 分钟建议：10 分钟 CANN/PyACL/NPU，15 分钟 ATC/OM，10 分钟服务与日志，10 分钟测试与证据。

---

## 板端独占：本课时最重要的边界

以下操作 `【板端独占】`，不能在开发机上代替：

- `source /usr/local/Ascend/ascend-toolkit/set_env.sh`
- `npu-smi info`
- `python -c "import acl"`
- `atc` 执行 ONNX 到 OM 转换
- PyACL 加载 OM 并执行推理
- `/dev/davinci*`、摄像头、串口、GPIO 等真实设备访问
- CANN/NPU 服务启动、日志与性能测量

开发机可以做：语法检查、静态文件检查、文档编辑、客户端 SSH/VNC。开发机通过检查不等于板端通过检查。

---

## CANN 与 PyACL 软件层次

| 层次 | 代表能力 | 验证方式 |
| --- | --- | --- |
| 驱动与固件 | 让操作系统识别 NPU | `npu-smi info` |
| CANN Runtime | 设备、上下文、流和内存 | ACL/PyACL 初始化 |
| CANN Toolkit | ATC、算子库、运行时库 | `command -v atc`、版本文件 |
| PyACL | Python 绑定 AscendCL | `import acl`、设备查询 |
| 模型链 | ONNX → ATC → OM → 推理 | 转换日志、OM 摘要、数值烟测 |

每一层都有自己的失败证据。`import acl` 成功不证明设备可用，`npu-smi` 可见不证明 OM 可加载，ATC 成功也不证明精度或性能达标。

---

## 在同一 Shell 中加载 CANN 与 PyACL

```bash
source /usr/local/Ascend/ascend-toolkit/set_env.sh
command -v atc
python -c 'import acl; print("PyACL import: ok")'
python -c 'import acl; print(acl.rt.get_device_count())'
```

命令解释：

- `source set_env.sh`：在当前 Shell 设置 CANN、ACL、运行时和工具链路径。
- `command -v atc`：确认当前 PATH 找到的 ATC 路径。
- `import acl`：加载 PyACL Python 模块，验证 Python 包与动态库能否解析。
- `get_device_count()`：查询 ACL 可见设备数量；实际返回值必须来自板端。

必须在启动服务的同一个 Shell 中完成环境加载。另一个终端执行过 `source`，不会自动改变当前服务环境。

---

## NPU 状态与板端设备节点

```bash
uname -a
cat /etc/os-release
date --iso-8601=seconds
npu-smi info
ls -l /dev/davinci* /dev/davinci_manager /dev/hisi_hdc /dev/devmm_svm 2>/dev/null
```

命令解释：

- `uname`、`os-release`、`date`：记录系统、内核和采集时间。
- `npu-smi info`：读取 NPU 名称、健康、温度、功耗、内存和版本字段。
- `ls -l /dev/...`：确认驱动是否创建设备节点；设备节点名称可能随驱动版本变化，不存在时原样记录。
- 若 `npu-smi` 的名称类似 `310B4`，ATC 的 `--soc_version` 通常写 `Ascend310B4`，但必须以实际板端和 CANN 文档为准。

本页不预设任何 310B 健康、温度、内存或设备数量结果。所有数字都要现场采集并绑定时间、主机和环境。

---

## ATC：在板端把 ONNX 转换为 OM

```bash
export TE_PARALLEL_COMPILER=1
export MAX_COMPILE_CORE_NUMBER=1
atc \
  --model=models/resnet18_scene.onnx \
  --framework=5 \
  --output=models/resnet18_scene \
  --soc_version=Ascend310B4 \
  --input_format=NCHW \
  --input_shape=input:1,3,224,224
```

命令解释：

- `TE_PARALLEL_COMPILER=1`、`MAX_COMPILE_CORE_NUMBER=1`：限制编译并行度，降低内存压力。
- `--framework=5`：输入为 ONNX。
- `--model`、`--output`：源模型与输出 OM 基名。
- `--soc_version=Ascend310B4`：目标芯片版本，必须与板端匹配。
- `--input_format`、`--input_shape`：必须来自该模型的输入合同，不能从其他模型复制。

`【板端独占】` ATC 会消耗 CPU、内存并调用 CANN 编译链。

---

## ATC 关键参数与失败边界

| 参数 | 作用 | 核对点 |
| --- | --- | --- |
| `--framework` | 指定原始模型框架 | 5 = ONNX |
| `--input_shape` | 固定输入形状 | 名称、维度、dtype 必须与导出一致 |
| `--input_format` | 声明输入排布 | NCHW/NHWC 与模型合同一致 |
| `--soc_version` | 选择目标芯片 | 由板端设备信息确认 |
| `--precision_mode` | 混合精度策略 | 不能只追求速度而跳过精度对比 |
| `--op_select_implmode` | 算子实现优先级 | 记录实际选择与日志 |
| `--insert_op_conf` | 下沉 AIPP 或自定义算子 | JSON/YAML 必须纳入版本控制 |

ATC 输出 `ATC run success` 只表示转换流程完成。若出现算子不支持、非零退出或 BrokenPipe，应保存命令、模型合同、CANN 版本和完整日志；不生成伪 OM，不擅自替换模型。

---

## OM 文件：摘要验证只是第一道门

```bash
stat -c '%s %n' models/*.om
sha256sum models/example.om
bundle_root=/path/to/repro-bundle
cd "$bundle_root"
sha256sum -c SHA256SUMS.txt
```

命令解释：

- `stat -c '%s %n'`：查看 OM 文件字节数和文件名，确认输出存在。
- `sha256sum`：计算单个文件摘要，用于比较两份工件是否一致。
- `sha256sum -c SHA256SUMS.txt`：只在生成该清单的复现包根目录执行，逐项校验。
- 摘要一致只证明文件字节一致，不证明 OM 能在目标设备加载。

模型、ONNX、OM、数据集和真实报告默认不提交到 Git。`310B4 / 8T` 与 `310B1 / 20T` 的工件和结果不得混用。

---

## 服务与端口：确认进程和监听范围

```bash
systemctl status <service>.service --no-pager
pgrep -af "python|uvicorn|<service-keyword>"
ss -ltnp | grep -E ':<port1>|:<port2>'
ps -fp <pid>
```

命令解释：

- `systemctl status`：查看服务状态、主 PID 和最近日志。
- `pgrep -af`：按关键词列出进程和完整参数，不直接终止。
- `ss -ltnp`：查看 TCP 监听地址、端口和进程。
- `ps -fp`：查看已确认 PID 的命令行和启动时间。

优先绑定 `127.0.0.1`；只有可信实验网络中才显式使用 `0.0.0.0`。HTTP 健康检查通过只证明路由可用，不证明 NPU 推理成功。

---

## 日志：定位而非粘贴全文

```bash
journalctl -b -u ssh --no-pager | tail -n 80
journalctl -b -u <service>.service --no-pager | tail -n 80
dmesg -T | tail -n 200
tail -f logs/service.log
```

命令解释：

- `journalctl -b -u ssh`：查看本次启动的 SSH 服务日志。
- `journalctl -u <service>`：查看指定服务日志。
- `dmesg -T`：查看内核日志并显示可读时间，适合设备、USB、驱动问题。
- `tail -f`：实时跟踪应用日志；定位结束后按 `Ctrl+C` 退出。

`【板端】` 报告中只保留与故障相关的行，并删除用户名、IP、token、真实设备序列号和私人路径。不要公开完整系统日志。

---

## 有序停止服务：确认 PID 再终止

```bash
service_pid=<pid>
case "$service_pid" in
  ''|*[!0-9]*) printf 'invalid PID\n' >&2; exit 2 ;;
esac
ps -fp "$service_pid"
readlink -f "/proc/$service_pid/cwd"
kill -TERM "$service_pid"
```

命令解释：

- `case`：确认 PID 只包含数字，防止空值或注入。
- `ps -fp`：确认该 PID 的命令行。
- `readlink -f /proc/<pid>/cwd`：确认进程工作目录，避免杀错同名进程。
- `kill -TERM`：先发送正常终止信号，让服务保存状态和释放资源。

停止后等待端口释放，再执行 `ss -ltnp` 复查。不要使用 `pkill python`、`killall` 或模糊 `kill` 批量终止进程。

---

## 测试与证据：每道门只证明一件事

| 证据门 | 证明内容 | 不能替代 |
| --- | --- | --- |
| 语法/静态检查 | 文件可解析、引用完整 | 板端运行 |
| CANN/PyACL 导入 | Python 与库路径可解析 | 设备初始化和推理 |
| `npu-smi` | 设备摘要可见 | OM 加载与性能 |
| ATC 日志 | ONNX 转换流程结束 | OM 可加载和数值正确 |
| ACL 烟测 | 一次模型加载和执行路径 | 精度、性能和稳定性 |
| 数值对比 | 输出误差在门限内 | 任务精度和实时性 |
| 性能测量 | 指定协议的延迟/吞吐 | 识别准确率 |
| UI 烟测 | 页面或接口可操作 | NPU 端到端验收 |

把每道门的输入、命令、环境、输出路径和结论分别记录，不能用一个较长流程的最终成功覆盖中间证据。

---

## 310B 证据报告与失败记录

每份板端报告至少记录：

- 板卡型号与算力层级、主机名、IP、时间。
- Ubuntu、内核、CANN、驱动与固件版本。
- Python 解释器路径、conda 环境、`PYTHONPATH`、`LD_LIBRARY_PATH`。
- 模型 ID、精度、ONNX/OM 路径、SHA-256 和输入合同。
- 原始命令、退出码、关键日志、报告路径。
- 预热次数、循环次数、重复次数和百分位方法。

常见失败边界：

| 现象 | 先检查 | 处理原则 |
| --- | --- | --- |
| `import acl` 失败 | 解释器、CANN 环境、库路径 | 修复同一 Shell，不加 CPU 回退 |
| ATC 算子不支持 | CANN 版本、输入合同、日志 | 保存失败证据，不生成伪 OM |
| OM 加载失败 | 文件摘要、权限、soc_version | 先核对工件与目标芯片 |
| HTTP 正常但推理失败 | 服务日志、ACL 初始化 | 路由状态与 NPU 状态分开报告 |
| 设备节点缺失 | 驱动、`npu-smi`、内核日志 | 记录为设备问题，不推断模型不支持 |

---

## 第 3 课时课堂任务

1. 在板端同一 Shell 中执行 `source set_env.sh`、`command -v atc` 和 `import acl`，保存命令与退出码。
2. 执行 `npu-smi info`，记录设备名称、健康、内存、CANN/驱动字段和采集时间；不照抄示例值。
3. 检查 `/dev/davinci*` 等设备节点，并解释设备节点、ACL 设备数量和 `npu-smi` 的关系。
4. 选择一个已批准的小模型，按输入合同执行一次 ATC；若失败，保存完整失败日志。
5. 对 OM 计算 `stat` 与 `sha256sum`，再执行一次 ACL 加载烟测。
6. 启动一个测试服务，检查 PID、监听端口、日志和 HTTP 状态，然后按 PID 有序停止。
7. 写一张证据表，把语法、转换、加载、数值、性能、UI 六类结果分开。

---

## 第 3 课时交付物与验收

交付物：

- `ascend310b/cann-acl-check.md`
- `ascend310b/npu-device-status.txt`
- `ascend310b/atc-om-evidence.md`
- `ascend310b/service-log-stop.md`
- `ascend310b/evidence-boundary.md`

验收标准：

- 能指出哪些命令是 `【板端独占】`，哪些可以在开发机完成。
- 能在同一 Shell 中加载 CANN，并区分 `import acl`、设备数量和 OM 加载。
- 能读取 `npu-smi` 并说明 `soc_version` 的判断依据。
- 能执行或审查 ATC 命令，记录输入合同、CANN 版本和失败边界。
- 能按 PID 查看进程、端口、工作目录和日志，并有序列停止服务。
- 能明确说明测试只证明了哪一道证据门，不夸大结果是识别精度或端到端性能。

---

## 总结：从 Ubuntu 操作到 310B 证据

三课时形成一条完整路径：

1. 第 1 课时：能安全地定位文件、理解用户与权限、编辑配置和审查命令。
2. 第 2 课时：能连接、访问、挂载和启动 Ubuntu 服务，并保留恢复通道。
3. 第 3 课时：能在真实 310B 板端加载 CANN/PyACL，处理 ATC/OM、设备、服务和日志证据。

最终原则：先确认机器和环境，再解析绝对路径；先观察，再修改；先保留原始输出，再写结论；开发机结果不冒充板端结果，路由成功不冒充 NPU 推理，转换成功不冒充精度与性能。
