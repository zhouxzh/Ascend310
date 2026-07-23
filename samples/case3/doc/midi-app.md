# 软件说明 — midi.py

> 本文档中的命令默认从 `case3` 仓库根目录执行。[返回文档索引](README.md)。

## 功能概览

[midi.py](../midi.py) 是一个功能完善的 MIDI 键盘应用程序，基于 `pygame` 和 `pygame.midi` 实现。支持三种运行模式：

| 模式 | 参数 | 说明 |
| :--- | :--- | :--- |
| 输出模式（演奏） | `--output` | 绘制两八度钢琴键盘（F3 起共 24 个音符），支持鼠标点击和键盘按键演奏 |
| 输入模式（监听） | `--input` | 监听 MIDI 输入设备，将收到的 MIDI 事件打印到控制台 |
| 设备列表 | `--list` | 枚举并打印系统上所有可用的 MIDI 设备 |

## 核心功能

**输出模式（钢琴键盘演奏）**：

- 两八度键盘渲染，包含 14 个白键和 10 个黑键
- 鼠标点击演奏：垂直位置决定力度（42 ~ 127）
- 计算机键盘映射：Tab ~ Backslash 对应白键，1 ~ Backspace 对应黑键
- 键间阴影状态机，模拟真实钢琴键的视觉反馈
- 默认使用教堂风琴音色（乐器编号 19）

**键状态机**：

`Key` 类维护一个三比特状态机（自身按下 / 右侧白键按下 / 右侧黑键按下），最多 8 种状态组合。工厂函数 `key_class()` 根据键类型（黑键、右侧无黑键的白键、右侧有黑键的白键）动态生成 `Key` 子类，为每种状态组合分配对应的子图像矩形，实现键与键之间的阴影变化。

**Keyboard 布局算法**：

`_add_keys()` 方法按钢琴标准布局水平放置键：
- 白键宽 42 像素，黑键宽 22 像素
- 黑键在相邻白键上居中偏移 11 像素
- 根据前一个键的类型自动选择键类（`WhiteKey` / `WhiteKeyLeft` / `WhiteKeyCenter` / `WhiteKeyRight`），确保阴影关系正确

## 使用方法

**列出 MIDI 设备**：

```bash
python3 midi.py --list
```

**演奏模式**（需系统安装 MIDI 合成器，如 TiMidity++）：

```bash
python3 midi.py --output
# 或指定设备 ID
python3 midi.py --output 0
```

**监听模式**：

```bash
python3 midi.py --input
# 或指定设备 ID
python3 midi.py --input 1
```

## 依赖安装

```bash
pip install pygame
```

**Linux 下安装 MIDI 合成器**（可选，用于输出模式）：

```bash
sudo apt install timidity timidity-interfaces-extra
```
