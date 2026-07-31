# 项目概览

> 命令默认从 `case3` 根目录执行。[返回文档索引](README.md)。

## 项目边界

本项目是在 Ascend 310B 上运行的 MIDI-DDSP 音乐工作台。输入来自触控钢琴、电脑
键盘、实体 MIDI 或 MIDI 文件；神经网络在 NPU 上预测 DDSP 控制参数；CPU 合成器
生成音频并输出到板载、USB 或已连接的蓝牙设备。

当前代码不包含摄像头或手势识别。案例名称中的“智能电子琴”指 MIDI 输入、神经音色
模型、实时音频和触控 Web 界面的组合，不应写成尚未实现的隔空手势系统。

## 核心模块

- [realtime_ddsp.py](../realtime_ddsp.py)：DDSP-VST OM 实时演奏与文件播放引擎。
- [midi_ddsp_realtime.py](../midi_ddsp_realtime.py)：MIDI-DDSP 版本化模型包、完整渲染缓存和播放会话。
- `pyacl_ddsp.py` / `pyacl_midi_ddsp.py`：两套模型的 PyACL 后端。
- `midi_ddsp_webui/`：FastAPI、任务调度、设备枚举和扬声器测试。
- `webui/`：React/TypeScript 触控工作台。
- `tools/`：ONNX 导出、ATC 转换、板端验证、部署和报告工具。
- `model3/`：FreeCAD、STEP 和 STL 电子琴结构件。

## 硬件

- Ascend 310B 开发板和现有 CANN/PyACL 环境；
- 显示器与触摸屏，或同一局域网内的浏览器设备；
- 可选的 USB MIDI 键盘；
- USB 喇叭、系统已连接的蓝牙音箱或板载音频输出；
- 可选的 3D 打印设备，用于制作 `model3/` 中的结构件。

本地模拟测试不需要 NPU。ATC、OM、PyACL、`ais_bench`
和 `npu-smi` 必须在真实 Ascend 310B 开发板上执行。

## 系统链路

```text
触控钢琴 / 电脑键盘 / 实体 MIDI
                |
                v
          统一实时演奏入口
                |
        +-------+--------+
        |                |
  Piano-DDSP       DDSP-VST 神经音色
  16声部 bundle      单音色状态化 OM
        |                |
        +-------+--------+
                |
                v
        板载 / USB / 蓝牙音频输出

MIDI 文件 -> MIDI-DDSP 会话 -> stateful v2 模型包 -> WAV / 音频输出
```

Web 工作台包含“实时演奏”“MIDI-DDSP”和“设备”三个工作区。“实时演奏”将
Piano-DDSP 钢琴与 DDSP-VST 神经音色放在同一个入口中，但后端模型和运行时保持独立；
扬声器测试位于设备页。所有 NPU/声卡任务共享资源锁。

## 模型和报告

`models/om/` 保存 DDSP-VST 与 legacy MIDI-DDSP OM；stateful v2 的 8 个组件统一位于
`models/midi_ddsp/bundles/<bundle-id>/`。模型
二进制、权重、ONNX、转换日志和运行报告默认不提交；`models/README.md` 记录目录约定，
模型 SHA256 清单保存在 `models/manifests/`。

Ascend 20T 已验证可以运行 8T 生成的同一批 OM，因此仓库不再保存按开发板重复的
运行时模型。历史实验和兼容性证据保留在 `reports/`。

## 使用顺序

1. 运行 `python scripts/check_webui_env.py` 检查现有板端环境。
2. 运行 `python scripts/run_webui.py`，使用程序打印的局域网地址打开界面。
3. 在“设备”页的扬声器测试中确认目标输出可以发声。
4. 在“实时演奏”中分别测试 Piano-DDSP 钢琴和 DDSP-VST 神经音色实时输入。
5. 在“MIDI-DDSP”页面测试 MIDI 文件播放和 WAV 渲染。
6. 在“设备”页面检查 OM、性能、NPU 和依赖状态。

书稿中的案例说明见
[`src/experiment/case3.md`](../../../src/experiment/case3.md)。
