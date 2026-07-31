# 触摸屏输入法配置

> 本文档面向 Ascend 310B 开发板的英文 XFCE 桌面。[返回文档索引](README.md)。

开发板的 WebUI 可以直接在板载触摸屏上使用。当前验证环境为 Ubuntu 22.04、
XFCE 4.16 和 X11；`QDtech MPI1001` 触摸设备已经由 XInput 识别。系统已有 IBus、
`ibus-pinyin` 和中英文输入引擎，但没有安装屏幕键盘。因此推荐使用 **Onboard + IBus
Pinyin**：Onboard 提供平板式软键盘，IBus 负责中英文切换和拼音候选词。

本文中的安装和配置命令只供用户在开发板上手动执行。部署脚本不会安装、升级或删除
板端软件，也不会修改桌面或系统配置。

## 1. 安装并首次启动 Onboard

在开发板终端中手动安装：

```bash
sudo apt install onboard
```

安装完成后打开 `Applications Menu`，搜索 `Onboard` 并启动。也可以在开发板的图形
终端中运行：

```bash
onboard
```

Onboard 启动后，从键盘窗口的菜单进入 `Preferences`。如果菜单不便操作，可以运行：

```bash
onboard-settings
```

## 2. 配置自动弹出与窗口位置

不同 Ubuntu 构建中的少数字段名称略有差异。以下名称以 Onboard 1.4 英文界面为准，
括号中给出可能出现的同义名称。

### General

启用：

```text
Show floating icon when Onboard is hidden
```

自动弹出失败时，可以点击悬浮图标，再选择 `Show Onboard` 手动显示键盘。

### Auto-show

启用：

```text
Automatically show Onboard when editing text
```

部分构建显示为 `Show when editing text`。不要启用 `Only auto-show in tablet mode`；
当前开发板没有上报平板模式硬件开关，启用后可能导致键盘不再自动显示。

进入 `Auto-show` 下的 `External Keyboards`。如果设备列表中出现
`Jieli Technology UACDemoV1.0`，将其设为忽略，或关闭连接外部键盘时隐藏 Onboard 的
选项。该音频设备同时暴露了 HID 键盘接口，可能被 Onboard 错误识别为物理键盘。

### Window

启用底部停靠：

```text
Dock to screen edge
Edge: Bottom
```

将键盘高度调整为屏幕高度的约 30% 至 35%。底部停靠比浮动窗口更适合 WebUI，可减少
软键盘遮挡按钮和输入框的情况。

## 3. 启用 GTK 辅助功能

Onboard 需要 GTK accessibility 才能识别 Firefox 等应用中的文本输入框。在开发板
图形终端中执行：

```bash
gsettings set org.gnome.desktop.interface toolkit-accessibility true
dconf write /org/onboard/auto-show/enabled true
```

然后打开 `Applications Menu -> Log Out -> Log Out`，退出当前 XFCE 会话并重新登录。
只有关闭终端或刷新网页不足以使辅助功能设置完整生效。

重新登录后可检查配置：

```bash
gsettings get org.gnome.desktop.interface toolkit-accessibility
dconf read /org/onboard/auto-show/enabled
```

两个命令都应输出 `true`。

## 4. 配置 IBus 拼音

系统已经安装并预载 `Pinyin` 和 `English (US)`，通常只需检查现有配置：

```bash
ibus-setup
```

在英文设置窗口中依次操作：

1. 打开 `Input Method`。
2. 点击 `Add`。
3. 选择 `Chinese`。
4. 选择 `Pinyin`。
5. 点击 `Add`。
6. 确认列表中同时存在 `Pinyin` 和 `English (US)`。
7. 打开 `General`，确认切换快捷键包含 `Control+space`。

使用触摸屏时，可以点击 XFCE 面板中的 IBus 图标切换输入法，也可以在 Onboard 上依次
点击 `Ctrl` 和空格。开发板图形终端还可以直接切换引擎：

```bash
ibus engine pinyin
ibus engine xkb:us::eng
```

第一条切换到拼音，第二条切换回英文。如果通过 SSH 操作桌面会话，需要显式连接显示器
和当前用户的 D-Bus：

```bash
DISPLAY=:0 \
DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus \
ibus engine pinyin
```

## 5. 设置登录后自动启动

先检查用户自动启动目录的所有者：

```bash
ls -ld /home/HwHiAiUser/.config/autostart
```

如果所有者显示为 `root root`，由用户手动修复该目录的所有权：

```bash
sudo chown HwHiAiUser:HwHiAiUser /home/HwHiAiUser/.config/autostart
```

不要对整个用户主目录执行递归 `chown`。然后在 XFCE 中打开：

```text
Applications Menu
-> Settings
-> Session and Startup
-> Application Autostart
-> Add
```

填写以下内容：

```text
Name: Onboard
Description: Touch screen keyboard
Command: onboard
```

点击 `OK` 并重新登录。下一次进入桌面后，Onboard 应随用户会话启动。

## 6. 验证 WebUI 输入

1. 在 Firefox 中打开 `http://127.0.0.1:8765`。
2. 点击 WebUI 中的文本输入框，确认 Onboard 从屏幕底部出现。
3. 切换到 `Pinyin`，输入拼音并确认候选词窗口能够选字。
4. 点击文本框外部，确认键盘能够自动隐藏。
5. 再次点击输入框；如果没有自动弹出，使用 Onboard 悬浮图标手动显示。

Onboard 提供传统屏幕键盘和中文候选词输入，不等同于 Android 或 iPadOS 的滑行输入、
手写识别和完整移动端表情键盘。

## 7. 故障排查

### 键盘完全不显示

在图形终端中运行 `onboard`。如果手动启动能够显示，问题位于自动启动或自动弹出设置，
不是触摸屏驱动。重新检查 GTK accessibility、`Auto-show` 和悬浮图标设置。

### 点击 Firefox 输入框不自动弹出

确认辅助功能和自动弹出值均为 `true`，然后重新登录桌面。还应在
`Auto-show -> External Keyboards` 中忽略 `Jieli Technology UACDemoV1.0`。Firefox
自动弹出仍不稳定时，保留悬浮图标作为可靠的手动入口。

### 可以输入英文，但没有中文候选词

打开 `ibus-setup -> Input Method`，确认 `Pinyin` 已加入列表，然后运行：

```bash
ibus engine pinyin
```

切换引擎后重新点击 Firefox 输入框。如果该命令在 SSH 中提示无法连接 IBus，使用第 4
节包含 `DISPLAY` 和 `DBUS_SESSION_BUS_ADDRESS` 的命令。

### 键盘遮挡 WebUI

退出 Firefox 全屏模式，在 `Onboard Preferences -> Window` 中启用底部停靠，并将键盘
高度控制在屏幕高度的 30% 至 35%。WebUI 本身支持触摸屏布局，但浏览器全屏窗口不一定
会在软键盘出现时自动调整可用区域。

## 参考资料

- [Ubuntu 22.04 Onboard 软件包](https://packages.ubuntu.com/jammy/onboard)
- [Onboard 1.4 自动显示和外部键盘说明](https://launchpad.net/onboard/1.4/1.4.0)
- [Ubuntu Onboard 悬浮图标说明](https://wiki.ubuntu.com/Nexus7/UsingTheDevice)
- [IBus 社区文档](https://help.ubuntu.com/community/ibus)
