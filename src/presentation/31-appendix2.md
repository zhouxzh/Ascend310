---
marp: true
theme: ascend310
paginate: true
size: 16:9
author: "周贤中"
email: "zhouxzh@gdut.edu.cn"
header: "《昇腾310B实战》教材配套演示"
footer: "附录 2：Ubuntu 教程"
---

<!-- _class: cover -->

# 附录 2：Ubuntu 教程

<div class="byline">周贤中 · zhouxzh@gdut.edu.cn</div>

## 从终端操作到可复现的远程开发环境


对应教材：`src/appendix/appendix2.md`

<!--
授课提示：用一句话说明本讲要解决的工程问题，再核对教材和样例路径。
-->

---

<!-- _class: center -->

## 本讲目标与路线

- 掌握文件系统、路径与常用命令
- 理解用户、权限与安全删除
- 配置 APT、Conda、网络与 SSH
- 用日志和故障命令定位板端问题

> 本讲主线：命令 → 权限 → 网络 → 远程 → 排障

<div class="source">教材：<a href="../appendix/appendix2.md"><code>src/appendix/appendix2.md</code></a></div>

<!--
先给学生路线图；后续每页都对应这里的一项，最后回到小结。
-->
---

<!-- _class: section -->

# 01

## 命令、权限与软件源
---

<!-- _class: center -->

## Ubuntu 与 Linux 基础

- 退出临时 root 状态或当前 shell 可使用：
- 也可以按 Ctrl+D 发送文件结束符。不要为了省事长期保留 root 终端，更不要在 root 终端中编辑项目文件。
- 脚本或排障会话可以使用严格模式：

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix2.md"><code>src/appendix/appendix2.md</code></a> · 2. Ubuntu 与 Linux 基础</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 文件系统、路径与常用命令

- 绝对路径从 / 开始，例如 /home//Documents/ascend310。相对路径从当前工作目录开始，例如 models/example.om。使用 .. 返回上一层：
- cd 不带参数时返回用户主目录，cd - 返回上一个工作目录。进入服务、转换或同步命令之前，先执行 pwd，确认没有在错误目录中运行。
- realpath 和 readlink -f 适合在复制、删除、挂载和部署前确认符号链接真实指向。路径中包含空格时，变量和参数应使用双引号：

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix2.md"><code>src/appendix/appendix2.md</code></a> · 3. 文件系统、路径与常用命令</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 用户、组、权限、sudo 与安全删除

- Ubuntu 安装时会创建一个普通用户，同时保留具有更高权限的 root 用户。普通用户在自己的主目录中通常不需要 sudo，访问 /etc、安装软件包或修改系统服务时才需要提权。
- 每个文件都有三组权限：
- 每组权限包含读 r、写 w、执行 x：

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix2.md"><code>src/appendix/appendix2.md</code></a> · 4. 用户、组、权限、sudo 与安全删除</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## APT、换源、Conda、pip 与 Python 路径

- APT 操作会改变系统状态。执行前记录 Ubuntu 版本和计划安装的包：
- apt-get update 获取软件包列表，install 安装软件包，install -f 修复依赖。安装完成后记录命令、版本和输出：
- 不要为了通过文档示例而重复安装或升级 CANN 相关包。板端已经验证过的 CANN、驱动和 Python 环境应保持原样，变更前先建立回滚方案。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix2.md"><code>src/appendix/appendix2.md</code></a> · 6. APT、换源、Conda、pip 与 Python 路径</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 网络、静态 IP、WiFi、热点与同网段检查

- Ubuntu 图形桌面可以通过 NetworkManager 配置静态 IP。打开连接编辑器：
- 图形界面通常从右上角网络图标进入 Edit Connections，点击加号，选择 WiFi，再点击 Create。命令行方式可以打开连接编辑器：
- 在 Wi-Fi 选项卡中设置：

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix2.md"><code>src/appendix/appendix2.md</code></a> · 7. 网络、静态 IP、WiFi、热点与同网段检查</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: section -->

# 02

## 网络、远程与排障
---

<!-- _class: center -->

## SSH 远程命令行控制

- 在板端确认 SSH 状态：
- Ubuntu 主机通常自带 SSH 客户端。需要单独安装时执行：
- 先连接板端 WiFi 或有线网络，再检查同网段和可达性：

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix2.md"><code>src/appendix/appendix2.md</code></a> · 8. SSH 远程命令行控制</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## NFS、SCP 与 rsync

- 源文档还出现过下面的旧示例：
- 该路径只是示例，不是 310B 板的默认路径。使用时必须替换为实际共享目录，并确认目录存在、内容范围和客户端权限。
- exportfs -v 查看当前导出，systemctl status 查看服务状态。修改 /etc/exports 后通常需要重新加载或重启服务。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix2.md"><code>src/appendix/appendix2.md</code></a> · 10. NFS、SCP 与 rsync</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## CANN、PyACL、NPU、ATC 与 OM

- ATC 把 ONNX 等前端模型转换为 OM。下面是最小示例：
- 脚本可能写入日志、manifest 和中间目录。执行前确认输出路径，执行后保存完整 ATC 命令、CANN 版本、模型输入合同、退出码和日志。
- 转换完成后检查文件存在、字节数和摘要：

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix2.md"><code>src/appendix/appendix2.md</code></a> · 13. CANN、PyACL、NPU、ATC 与 OM</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 板端设备、服务、日志、测试与证据边界

- 这份状态报告用于记录测试环境，不是性能测试。把它和后续 ATC、ACL、精度和性能命令放在同一报告目录中，便于追溯。
- 重新登录后再验证。不要用 root 启动整个图形程序或 Python 服务来绕开设备权限。
- 音频设备编号和名称必须以当前板端输出为准。aplay -l 只列出播放设备，arecord -l 列出采集设备。播放测试会改变音量或产生声音，执行前确认扬声器、耳机和实验环境。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix2.md"><code>src/appendix/appendix2.md</code></a> · 14. 板端设备、服务、日志、测试与证据边界</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 故障排查与禁止命令模式

- 以下命令模式不应未经确认直接执行：
- rm -rf 指向家目录、仓库根目录、变量未验证的路径或正在运行的部署目录。
- rsync --delete 同步到未确认的远端目录，它可能删除模型、数据和报告。

> 本页结论要能回到教材原文、命令输出或板端记录。

<div class="source">教材：<a href="../appendix/appendix2.md"><code>src/appendix/appendix2.md</code></a> · 15. 故障排查与禁止命令模式</div>

<!--
授课提示：按本页要点带读；关键数字和通过结论以教材正文与板端记录为准。
-->
---

<!-- _class: center -->

## 本章小结

- Linux 命令要绑定具体路径和用户
- 权限与删除操作必须先确认目标
- CANN 与设备检查放在最后统一验证
- 未验证项必须标为 untested 或 blocked。

> 下一讲：进入 Python 编程基础。

<div class="source">教材：<a href="../appendix/appendix2.md"><code>src/appendix/appendix2.md</code></a></div>

<!--
用三句话收束；提醒学生把未验证项写成 untested 或 blocked。
-->
