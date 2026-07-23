# MIDI-DDSP Studio Web 界面

MIDI-DDSP Studio 是运行在 Ascend 310B 开发板上的音乐工作台。界面可以在板载
触摸屏或同一局域网内的电脑浏览器中打开，提供实时演奏、MIDI-DDSP 播放与渲染、
OM 实验和设备检查四个工作区。

## 技术方案

- 浏览器端：React、TypeScript、Vite、Lucide 和 Recharts。
- 板端服务：FastAPI、Uvicorn 和 WebSocket。
- 推理与音频：现有 PyACL/OM、PortAudio、Mido 和 RtMidi 代码。
- 生产部署：开发电脑编译 `webui/dist/`，开发板只运行 Python 服务和静态文件。

Flask 与 FastAPI 都适合提供 HTTP 服务，但它们不是完整的前端框架。Gradio 适合快速
搭建模型演示，难以精确控制低延迟钢琴事件、复杂工作区、任务状态和触摸屏布局。
因此本项目使用 React + FastAPI；不需要额外安装 Flask 或 Gradio。

## 板端手动安装

以下命令仅供用户在 Ascend 开发板上手动执行。同步和启动脚本不会安装、升级或删除
任何板端软件。

先进入项目使用的 Anaconda `base` 环境：

```bash
source /usr/local/miniconda3/etc/profile.d/conda.sh
conda activate base
cd /home/HwHiAiUser/Documents/case3
```

检查 PortAudio 动态库：

```bash
ldconfig -p | grep libportaudio.so.2
```

如果没有输出，需要手动安装系统运行库：

```bash
sudo apt install libportaudio2
```

安装 Web 服务、MIDI 和音频 Python 依赖：

```bash
python -m pip install -r requirements-webui.txt
```

`requirements-webui.txt` 是 Web UI 的完整 Python 依赖入口，包含 NumPy、Mido、
RtMidi 和 SoundDevice。Web UI 只使用 OM 模型，不安装或扫描 ONNX 模型。仓库中的
ONNX/TFLite 导出工具仍使用独立的 `requirements-onnx.txt`。PyACL 必须由开发板现有
CANN 环境提供，`ais_bench` 由已有 Ascend 基准测试环境提供。

## 本地构建与同步

在 Windows 开发电脑的 `case3` 根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File tools/deploy_midi_ddsp_webui.ps1
```

脚本在本地执行 `npm ci` 和生产构建，然后通过 SSH 别名 `ascend8t` 将前端产物、
FastAPI 服务和启动脚本同步到 `/home/HwHiAiUser/Documents/case3`。它不会远程安装
依赖，也不会修改 shell 启动文件或系统服务。可通过参数修改目标：

```powershell
tools/deploy_midi_ddsp_webui.ps1 `
  -SshTarget ascend8t `
  -RemoteRoot /home/HwHiAiUser/Documents/case3
```

## 启动

首次安装或修改环境后执行一次只读检查：

```bash
cd /home/HwHiAiUser/Documents/case3
python check_webui_env.py
```

检查器只验证当前 `base`、CANN 环境变量、Python 包和前端产物，不设置环境变量，也不
安装或修改软件。检查通过后，日常在开发板终端直接启动：

```bash
cd /home/HwHiAiUser/Documents/case3
python run_webui.py
```

`run_webui.py` 只启动 Uvicorn 并监听 `0.0.0.0:8765`。它不设置或检查环境；程序和
模型运行错误会直接显示在当前终端。

打开地址：

- 板载浏览器：`http://127.0.0.1:8765`
- 局域网电脑：`http://<开发板 IP>:8765`

服务没有登录功能，只应运行在可信局域网中。API 只接受目录扫描生成的模型 ID、MIDI
ID 和设备 ID，不接受浏览器提交的任意文件路径或 shell 命令。

## 工作区

### 演奏

选择动态扫描到的 OM 模型、音频输出和实体 MIDI 输入。触控钢琴、电脑键盘和
实体 MIDI 会进入同一实时引擎。窗口失焦、触摸取消、WebSocket 断开或停止会触发
all-notes-off，避免持续音符。默认优先选择 Violin Mixed OM。

### MIDI-DDSP

可选择仓库 MIDI 或上传不超过 10 MiB 的 `.mid`/`.midi` 文件，随后播放或离线渲染
WAV。播放任务支持暂停、继续和停止；渲染完成后显示波形、音频控件和报告指标。

### 实验

只提供白名单内的一次 OM 运行验证和短基准测试。任务日志、状态和报告保存在
`reports/webui/jobs/<job-id>/`，界面可下载受控的 WAV、JSON 和文本产物。

### 设备

显示 NPU、CANN、PyACL、Python 依赖、模型、音频输出和 MIDI 端口状态。已知的
`npu-smi` `Health: Alarm` 只显示警告；实际 OM 推理成功时不会阻断操作。

蓝牙配对仍建议使用显示器和触摸屏上的系统图形界面。不同耳机或喇叭的命令行配对及
A2DP/HFP 配置可能不同，纯命令行流程对初学者较复杂。Web 界面只选择系统已经连接
并暴露出来的音频设备，不负责蓝牙配对。

## 开发与测试

本地启动 FastAPI 服务：

```powershell
python -m uvicorn midi_ddsp_webui.app:app --host 127.0.0.1 --port 8765
```

前端开发服务器：

```powershell
cd webui
npm ci
npm run dev
```

测试与构建：

```powershell
python -m unittest discover -s tests -v
cd webui
npm run test
npm run build
npm run test:e2e
```

本地测试不会执行 PyACL、ATC、OM 推理或 `npu-smi`。触控发声、USB/蓝牙输出、实体
MIDI、OM 验证和基准测试必须在真实 Ascend 310B 开发板上完成。

## 常见问题

- 页面显示“板端功能不可用”：本地开发环境会主动禁用 OM 播放和板端测试，这是预期行为。
- 启动时报 `missing existing dependencies`：按错误清单手动安装依赖后重新启动。
- 找不到蓝牙声卡：先在系统图形界面完成配对和音频模式选择，再刷新设备页。
- 返回 `409 busy`：NPU 或声卡正被另一个实时、播放或测试任务占用，停止该任务后重试。
- 更换或新增模型后列表不更新：点击界面刷新按钮，目录扫描不使用浏览器传入的路径。
